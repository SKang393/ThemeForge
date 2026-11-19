import csv
from dataclasses import asdict, replace
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from themeforge.analysis_types import AnalysisResult, AnalysisSettings, Theme, ThemeQuote, TranscriptDocument
from themeforge.app import ThemeForgeApp
from themeforge.audit import create_audit_event, export_audit_events_csv
from themeforge.project_io import ProjectState, load_project, save_project


class HiddenApp(ThemeForgeApp):
    def _build_layout(self):
        self.withdraw()
        super()._build_layout()


class QuoteReassignmentTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.app = HiddenApp()
        self.addCleanup(self.app.destroy)
        doc = TranscriptDocument("studio.txt", "Ren: Wait for the bell.\nJo: Wait for the bell.\nThen enter.")
        passage = "Wait for the bell."
        self.original = ThemeQuote("Q2", "Jo", passage + "\nThen enter.", 0.82, 2, doc.name,
            "Selected for the bell signal.", doc.text.rindex(passage), len(doc.text), "Earlier memo.")
        self.other = replace(self.original, quote_id="Q1", speaker="Ren", text=passage, source_line=1,
            source_start=doc.text.index(passage), source_end=doc.text.index(passage) + len(passage), relevance=0.37)
        self.source = Theme("T1", "Bell signal", "#123456", [], 1, 0.82, [self.original])
        self.destination = Theme("T2", "Permission", "#654321", [], 1, 0.37, [self.other])
        result = AnalysisResult("test", 1, 2, [self.source, self.destination], [])
        self.state = ProjectState((), None, (doc,), AnalysisSettings(), result, "Editor B")
        self.first = self.folder / "original.tfproj"
        save_project(self.first, self.state)
        self.app._restore_project(load_project(self.first), None)  # Work on an unsaved copy.

    def move(self, reason):
        with patch("themeforge.app.simpledialog.askstring", return_value=reason) as ask:
            self.app.move_current_quote()
        return ask

    def export(self, suffix):
        path = self.folder / ("after" + suffix)
        with patch("themeforge.app.filedialog.asksaveasfilename", return_value=str(path)):
            self.app.save_report()
        with path.open(encoding="utf-8", newline="") as stream:
            return stream.read()

    def test_destination_is_unscored_and_history_survives_reopen(self):
        original = self.first.read_bytes()
        self.move("The second speaker is waiting for permission.").assert_called_once()
        target = self.app._current_theme()
        self.assertEqual(self.app._current_quote_match().quote.quote_id, "Q2")
        moved = target.quotes[-1]
        self.assertEqual((moved.quote_id, moved.speaker, moved.source_name, moved.source_start, moved.source_end,
                          moved.source_line, moved.text, moved.memo),
                         ("Q2", "Jo", self.original.source_name, self.original.source_start, self.original.source_end,
                          2, self.original.text, "Earlier memo."))
        self.assertEqual(target.quotes[0], self.other)
        self.assertIn("Editor B", self.app.quote_reason.get())
        self.assertIn("Selected for the bell signal.", self.app.quote_reason.get())
        revised = self.folder / "revised.tfproj"
        with patch("themeforge.app.filedialog.asksaveasfilename", return_value=str(revised)):
            self.assertTrue(self.app.save_project())
        with patch("themeforge.app.filedialog.askopenfilename", return_value=str(revised)):
            self.app.open_project()
        event, = [e for e in self.app.audit_events if e.action == "quote_reassigned"]
        history = json.loads(event.details)
        self.assertEqual((event.actor, event.target_id, history["original_quote"]), ("Editor B", "Q2", asdict(self.original)))
        self.assertEqual((history["from_theme"]["name"], history["to_theme"]["name"]), ("Bell signal", "Permission"))
        self.assertEqual(history["reason"], "The second speaker is waiting for permission.")
        audit = next(r for r in csv.DictReader(io.StringIO(export_audit_events_csv(self.app.audit_events)))
                     if r["action"] == "quote_reassigned")
        self.assertEqual(json.loads(audit["details"])["original_quote"], asdict(self.original))
        rows = list(csv.DictReader(io.StringIO(self.export(".csv"))))
        self.assertEqual({r["quote_id"]: r["relevance"] for r in rows}, {"Q1": "0.370", "Q2": ""})
        self.assertEqual(next(r["quote"] for r in rows if r["quote_id"] == "Q2"), self.original.text)
        report = json.loads(self.export(".json"))
        self.assertIsNone(report["themes"][1]["quotes"][-1]["relevance"])
        self.assertIsNone(report["themes"][1]["score"])
        self.assertIsNone(report["themes"][0]["score"])
        md = self.export(".md")
        self.assertIn("Relevance: Not scored", md)
        self.assertIn("The second speaker is waiting for permission.", md)
        self.assertEqual(self.first.read_bytes(), original)

    def test_cancel_missing_editor_or_duplicate_does_not_change_either_assignment(self):
        self.move(None)
        self.move("   ")
        self.app.researcher_name.set("")
        self.move("reason").assert_not_called()
        self.assertEqual(self.app.result, self.state.result)
        self.assertFalse(self.app.audit_events)
        for overlap in (self.original, replace(self.original, quote_id="Different")):
            bound = replace(self.destination, quotes=[self.other, overlap], quote_count=2)
            duplicate = replace(self.state.result, themes=[self.source, bound])
            self.app._restore_project(replace(self.state, result=duplicate), None)
            self.move("reason").assert_not_called()
            self.assertEqual(self.app.result, duplicate)
            self.assertIn("already", self.app.status_text.get())
            self.assertFalse(self.app.audit_events)

    def test_stale_anchor_refuses_and_undo_redo_restore_relation_without_guessing_from_audit(self):
        stale = replace(self.original, source_start=self.original.source_start - 1)
        changed = replace(self.state.result, themes=[replace(self.source, quotes=[stale]), self.destination])
        self.app._restore_project(replace(self.state, result=changed), None)
        self.move("reason").assert_not_called()
        self.assertIn("anchor", self.app.status_text.get())
        self.assertEqual(self.app.result, changed)
        self.app._restore_project(self.state, None)
        self.move("A different interpretation.")
        transferred = self.app.result
        self.app.undo_manual_edit()
        self.assertEqual(self.app.result, self.state.result)
        self.assertEqual(json.loads(self.export(".json"))["themes"][0]["quotes"][0]["relevance"], 0.82)
        self.app.redo_manual_edit()
        self.assertEqual(self.app.result, transferred)
        self.assertIsNone(json.loads(self.export(".json"))["themes"][1]["quotes"][-1]["relevance"])

    def test_old_move_event_does_not_reclassify_a_current_score(self):
        event = create_audit_event(actor="Earlier editor", action="quote_moved", target_type="quote",
                                   target_id="Q2", details="from_theme=T2; to_theme=T1")
        self.app._restore_project(replace(self.state, audit_events=(event,)), None)
        report = json.loads(self.export(".json"))
        self.assertEqual(report["themes"][0]["quotes"][0]["relevance"], 0.82)
        self.assertEqual(self.app.audit_events, [event])

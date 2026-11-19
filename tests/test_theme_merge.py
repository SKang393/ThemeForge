import csv
from dataclasses import asdict, replace
from hashlib import sha256
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from themeforge.analysis_types import AnalysisResult, AnalysisSettings, Theme, ThemeQuote, TranscriptDocument
from themeforge.app import ThemeForgeApp
from themeforge.audit import create_audit_event
from themeforge.project_io import ProjectState, load_project, save_project
from themeforge.rigor_dialog import RigorDialog


class HiddenApp(ThemeForgeApp):
    def _build_layout(self):
        self.withdraw()
        super()._build_layout()


class ThemeMergeTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.app = HiddenApp()
        self.addCleanup(self.app.destroy)
        self.doc = TranscriptDocument("Workshop", "Lee: Wait.\nRen: Wait.")
        q = ThemeQuote("D1", "Lee", "Wait.", .37, 1, self.doc.name, "Destination rationale.", 5, 10, "First memo.")
        shared = replace(q, quote_id="S1", rationale="Distinct source rationale.", relevance=.91, memo="Second memo.\nKeep it.")
        later = replace(shared, quote_id="S2", speaker="Ren", source_line=2, source_start=16, source_end=21)
        self.destination = Theme("D", "Permission", "#123456", [], 1, .67, [q], memo="Destination group memo.")
        self.source = Theme("S", "Timing", "#654321", [], 2, .82, [shared, later],
                            validation={"review_status": "Provisional"}, memo="Source group memo.")
        self.prior = create_audit_event(actor="Coder A", action="quote_memo_updated", target_type="quote",
                                        target_id="S1", details="Earlier coding decision.")
        self.state = ProjectState((), None, (self.doc,), AnalysisSettings(),
            AnalysisResult("test", 1, 2, [self.source, self.destination], []), "Editor B", (self.prior,))
        self.path = self.folder / "before.tfproj"
        save_project(self.path, self.state)
        self.app._restore_project(load_project(self.path), self.path)

    def merge(self, reason):
        with patch("themeforge.app.simpledialog.askstring", return_value=reason) as ask:
            self.app.merge_current_theme()
        return ask

    def export(self, suffix):
        path = self.folder / ("report" + suffix)
        with patch("themeforge.app.filedialog.asksaveasfilename", return_value=str(path)):
            self.app.save_report()
        with path.open(encoding="utf-8", newline="") as stream:
            return stream.read()

    def test_shared_and_transferred_assignments_keep_two_histories_but_count_each_exact_span_once(self):
        original = self.path.read_bytes()
        reason = "Group both waits under permission."
        self.merge(reason).assert_called_once()
        active = self.app.result
        group, = active.themes
        self.assertEqual((group.id, group.quote_count, group.score), ("D", 2, None))
        shared, moved = group.quotes
        self.assertEqual(shared, self.destination.quotes[0])
        self.assertEqual((moved.quote_id, moved.relevance, moved.memo), ("S2", None, self.source.quotes[1].memo))
        self.assertIn("Editor B", moved.rationale)
        self.assertIn(reason, moved.rationale)
        self.assertEqual(self.path.read_bytes(), original)
        self.app.undo_manual_edit()
        self.assertEqual(self.app.result, self.state.result)
        self.app.redo_manual_edit()
        self.assertEqual(self.app.result, active)
        copy = self.folder / "after.tfproj"
        with patch("themeforge.app.filedialog.asksaveasfilename", return_value=""):
            self.assertFalse(self.app.save_project())
        with patch("themeforge.app.filedialog.asksaveasfilename", return_value=str(copy)):
            self.assertTrue(self.app.save_project())
        with patch("themeforge.app.filedialog.askopenfilename", return_value=str(copy)):
            self.app.open_project()
        self.assertEqual(self.app.result, active)
        self.assertIn(self.prior, self.app.audit_events)
        self.assertEqual(self.path.read_bytes(), original)
        report = json.loads(self.export(".json"))
        self.assertEqual([q["relevance"] for q in report["themes"][0]["quotes"]], [.37, None])
        self.assertIn("original coding records", self.export(".md"))
        rows = list(csv.DictReader(io.StringIO(self.export(".csv"))))
        self.assertEqual([(r["quote_id"], r["relevance"]) for r in rows], [("D1", "0.370"), ("S2", "")])
        audit = self.folder / "audit.csv"
        dialog = RigorDialog(self.app, self.app._rigor_context())
        dialog.withdraw()
        with patch("themeforge.rigor_dialog.filedialog.asksaveasfilename", return_value=str(audit)):
            dialog._export_audit()
        with audit.open(encoding="utf-8", newline="") as stream:
            event, = [r for r in csv.DictReader(stream) if r["action"] == "theme_merge_recorded"]
        self.assertEqual((event["actor"], event["target_id"]), ("Editor B", "D"))
        details = json.loads(event["details"])
        self.assertEqual((details["source_before"], details["destination_before"], details["reason"]),
                         (asdict(self.source), asdict(self.destination), reason))
        self.assertEqual(details["source_sha256"], {"Workshop": sha256(self.doc.text.encode()).hexdigest()})
        self.assertEqual([(r["source_quote_id"], r["destination_quote_id"]) for r in details["passage_links"]],
                         [("S1", "D1"), ("S2", "S2")])

    def test_same_id_same_span_allowed_but_conflicts_or_cancel_never_discard_records(self):
        original = self.path.read_bytes()
        self.merge(None)
        self.merge(" ")
        self.assertEqual(self.app.result, self.state.result)
        for q in (replace(self.source.quotes[1], quote_id="D1"), replace(self.source.quotes[0], source_end=9)):
            source = replace(self.source, quotes=[q], quote_count=1)
            result = replace(self.state.result, themes=[source, self.destination])
            self.app._restore_project(replace(self.state, result=result), None)
            self.merge("Combine.")
            self.assertEqual(self.app.result, result)
            self.assertEqual(self.app.audit_events, [self.prior])
        duplicate = replace(self.source.quotes[0], quote_id="D1")
        source = replace(self.source, quotes=[duplicate], quote_count=1)
        self.app._restore_project(replace(self.state, result=replace(self.state.result,
                                  themes=[source, self.destination])), None)
        self.merge("Keep both decisions.")
        self.assertEqual(self.app.result.themes[0].quotes, self.destination.quotes)
        details = json.loads(self.app.audit_events[-1].details)
        self.assertEqual(details["source_before"]["quotes"], [asdict(duplicate)])
        self.assertEqual(self.path.read_bytes(), original)

import csv
from copy import deepcopy
from dataclasses import replace
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from themeforge.analysis_types import AnalysisSettings, CodebookEntry, TranscriptDocument
from themeforge.app import ThemeForgeApp
from themeforge.project_io import ProjectState, load_project
from themeforge.rigor_dialog import RigorDialog


class HiddenApp(ThemeForgeApp):
    def _build_layout(self):
        self.withdraw()
        super()._build_layout()


class MatchReviewTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.app = HiddenApp()
        self.addCleanup(self.app.destroy)
        self.doc = TranscriptDocument("Workshop", "Jo: I never submitted the daily log.\nRen: I submitted the daily log myself.")
        settings = AnalysisSettings(codebook_entries=(
            CodebookEntry("Self submission", "submitted the daily log"),
            CodebookEntry("Talk about the log", "daily log"),))
        self.app._restore_project(ProjectState((), None, (self.doc,), settings, researcher_name="Reader A"), None)
        self.app.analyze()
        self.first = deepcopy(self.app.result)
        self.select(0, "Jo")

    def select(self, theme_index, speaker):
        self.app._select_theme(theme_index)
        for _ in self.app.current_quote_matches:
            if self.app._current_quote_match().quote.speaker == speaker:
                return
            self.app.next_quote()
        self.fail("Expected fictional speaker in the selected group")

    def mark(self, status, reason):
        with patch("themeforge.app.MatchReviewReason") as prompt:
            prompt.return_value.result = reason
            self.app.review_current_match(status)

    def export(self, suffix):
        path = self.folder / ("report." + suffix)
        with patch("themeforge.app.filedialog.asksaveasfilename", return_value=str(path)):
            self.app.save_report()
        return path.read_text(encoding="utf-8")

    def test_returning_to_by_source_refreshes_reviews_recorded_while_it_was_hidden(self):
        app = self.app
        app.deiconify()
        app.update()
        self.mark("deferred", "Check who sent the log.")
        app.open_rigor_tools()
        dialog, = [child for child in app.winfo_children() if isinstance(child, RigorDialog)]
        dialog.geometry("900x600+0+0")
        app.update()
        tree = dialog.source_matrix_tree

        def follow(item):
            self.assertTrue(tree.winfo_viewable())
            tree.see(item)
            tree.focus_force()
            tree.focus(item)
            tree.selection_set(item)
            app.update()
            tree.event_generate("<Return>")
            app.update()
            self.assertEqual(dialog.state(), "withdrawn")

        parent, = [row for row in tree.get_children() if tree.set(row, "reviews") == "1 deferred"]
        item, = tree.get_children(parent)
        follow(item)

        with patch.object(dialog, "_render_current_state", wraps=dialog._render_current_state) as render:
            self.mark("declined", "Jo says the log was never submitted.")
            app.update()
            self.select(0, "Ren")
            self.mark("supported", "Ren describes submitting it.")
            app.update()
            render.assert_not_called()
            app.open_rigor_tools()
            app.update()
            render.assert_called_once()
        reviewed, = [row for row in tree.get_children() if tree.set(row, "reviews") == "1 supported, 1 declined"]
        self.assertEqual(len(dialog.audit_tree.get_children()), len(app.audit_events))
        saved_state = app._project_state()
        expected = [(1, 4, 36, "declined", "Jo says the log was never submitted."),
                    (2, 42, 75, "supported", "Ren describes submitting it.")]
        for item, (line, start, end, status, reason) in zip(tree.get_children(reviewed), expected, strict=True):
            self.assertEqual(tree.item(item, "text"), f"Line {line} [{start}, {end})")
            self.assertEqual(tree.set(item, "reviews"), status)
            follow(item)
            self.assertEqual(app._current_document_name(), self.doc.name)
            text = app.transcript_widgets[self.doc.name]
            self.assertEqual(len(text.get("1.0", "selected_quote.first")), start)
            self.assertEqual(text.get("selected_quote.first", "selected_quote.last"), self.doc.text[start:end])
            self.assertIn(f"{status} by Reader A", app.match_review_text.get())
            self.assertIn(reason, app.match_review_text.get())
            app.open_rigor_tools()
            app.update()
        self.assertEqual(app._project_state(), saved_state)

    def test_reviews_survive_reopen_and_export_but_never_change_coding_or_transfer_to_new_run(self):
        app = self.app
        q = app._current_quote_match().quote
        self.mark("deferred", "Read the preceding exchange.")
        app.researcher_name.set("Reader B")
        self.mark("declined", "Jo explicitly says never; this is not self-submission.")
        self.assertIn("Reader B", app.match_review_text.get())
        self.select(1, "Jo")
        self.assertNotIn("Reader B", app.match_review_text.get())  # Same quote in another group.
        self.select(0, "Ren")
        self.mark("deferred", "Check who supplied the log.")
        self.assertEqual(app.result, self.first)
        project = self.folder / "review.tfproj"
        with patch("themeforge.app.filedialog.asksaveasfilename", return_value=str(project)):
            self.assertTrue(app.save_project())
        with patch("themeforge.app.filedialog.askopenfilename", return_value=str(project)):
            app.open_project()
        self.select(0, "Jo")
        self.assertIn("declined", app.match_review_text.get())
        loaded = load_project(project)
        self.assertEqual(loaded.result, self.first)
        self.assertEqual(loaded.documents, (self.doc,))
        events = [e for e in loaded.audit_events if e.action == "match_review_note"]
        self.assertEqual([e.actor for e in events], ["Reader A", "Reader B", "Reader B"])
        detail = json.loads(events[1].details)
        self.assertEqual((detail["match"]["quote"]["text"], detail["match"]["quote"]["relevance"]), (q.text, q.relevance))
        self.assertEqual(self.doc.text[q.source_start:q.source_end], q.text)
        report = json.loads(self.export("json"))
        rows = { (t["id"], r["speaker"]): r for t in report["themes"] for r in t["quotes"] }
        current = rows[(self.first.themes[0].id, "Jo")]["latest_match_review"]
        self.assertEqual((current["disposition"], current["actor"], current["reason"]),
                         ("declined", "Reader B", detail["reason"]))
        self.assertIsNone(rows[(self.first.themes[1].id, "Jo")]["latest_match_review"])
        for fmt in ("md", "csv"):
            report_text = self.export(fmt)
            self.assertIn("declined", report_text)
            self.assertIn(detail["reason"], report_text)
        rows = list(csv.DictReader(io.StringIO(self.export("csv"))))
        self.assertEqual([r["match_review_disposition"] for r in rows].count("declined"), 1)
        original = project.read_bytes()
        # A reader joining the study can also reset other readers' current decisions.
        app.researcher_name.set("Reader C")
        before = app._project_state()
        with patch("themeforge.app.messagebox.askokcancel", return_value=False) as confirm:
            app.analyze()
        confirm.assert_called_once()
        self.assertEqual(confirm.call_args.kwargs["default"], "cancel")
        self.assertEqual(app._project_state(), before)
        self.assertEqual(project.read_bytes(), original)
        with patch("themeforge.app.messagebox.askokcancel", return_value=True):
            app.analyze()
        self.assertEqual(app.result, self.first)
        self.assertFalse(app.match_reviews.has_reviews_since_analysis)
        self.assertNotIn("Reader B", app.match_review_text.get())
        self.assertTrue(all(r["latest_match_review"] is None for t in json.loads(self.export("json"))["themes"] for r in t["quotes"]))
        self.assertEqual([e for e in load_project(project).audit_events if e.action == "match_review_note"], events)
        audit = self.folder / "history.csv"
        dialog = RigorDialog(app, app._rigor_context())
        dialog.withdraw()
        with patch("themeforge.rigor_dialog.filedialog.asksaveasfilename", return_value=str(audit)):
            dialog._export_audit()
        history = [r for r in csv.DictReader(io.StringIO(audit.read_text(encoding="utf-8"))) if r["action"] == "match_review_note"]
        self.assertEqual([(r["actor"], r["details"]) for r in history], [(e.actor, e.details) for e in events])

    def test_cancel_missing_actor_source_change_and_pending_memo(self):
        app = self.app
        baseline = app.audit_events.copy()
        self.mark("declined", None)
        self.mark("deferred", "   ")
        app.researcher_name.set("")
        self.mark("declined", "Reason.")
        self.assertEqual(app.audit_events, baseline)
        app.researcher_name.set("Reader A")
        app.quote_memo_text.insert("1.0", "Unsaved memo.")
        self.mark("deferred", "Verify surrounding context.")
        self.assertEqual(app.quote_memo_text.get("1.0", "end-1c"), "Unsaved memo.")
        state = app._project_state()
        # Identical quote and offsets in a revised source do not inherit a note.
        changed = replace(self.doc, text=self.doc.text + "\nJo: A later clarification.")
        app._restore_project(replace(state, documents=(changed,)), None)
        self.select(0, "Jo")
        self.assertNotIn("Reader A", app.match_review_text.get())
        # An unresolved exact location cannot acquire a new note.
        app._restore_project(replace(state, documents=(replace(self.doc, text="Jo: Something else."),)), None)
        self.mark("declined", "Invalid link.")
        self.assertEqual(app.audit_events, list(state.audit_events))

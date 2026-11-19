"""Changing studies must not redirect the next save back into the old study."""
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from themeforge.analysis_types import AnalysisResult, AnalysisSettings, CodebookEntry, TranscriptDocument
from themeforge.app import ThemeForgeApp
from themeforge.audit import create_audit_event
from themeforge.project_io import ProjectState, load_project, save_project


class TranscriptHandoffTests(unittest.TestCase):
    def setUp(self):
        class HiddenApp(ThemeForgeApp):
            def _build_layout(self):
                self.withdraw()
                super()._build_layout()
        self.app = HiddenApp()
        self.addCleanup(self.app.destroy)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.first = self.folder / "first.tfproj"
        self.new_source = self.folder / "second.txt"
        self.new_text = "Lee: Studio access requires a key at the side door."
        self.new_source.write_text(self.new_text, encoding="utf-8")
        self.entry = CodebookEntry("Studio access", "How people enter.", ("Use the side door.",))
        self.state = ProjectState(
            (self.folder / "first.txt",), None,
            (TranscriptDocument("first.txt", "Sam: Bring a spare key.", "Outgoing document memo."),),
            AnalysisSettings(central_theme="First question", codebook_entries=(self.entry,)),
            AnalysisResult("test", 1, 1, [], ["Outgoing analysis note."]),
            "First reviewer", (create_audit_event(actor="First reviewer", action="reviewed",
                target_type="quote", target_id="Q1", details="Outgoing audit."),),
        )
        save_project(self.first, self.state)
        self.app._restore_project(load_project(self.first), self.first)

    def choose(self, decisions, source=None):
        with patch("themeforge.app.filedialog.askopenfilenames", return_value=[str(source or self.new_source)]), \
                patch("themeforge.app.messagebox.askyesnocancel", side_effect=decisions):
            self.app.open_transcript()

    def test_replacement_starts_separate_project_and_saves_only_new_study(self):
        original = self.first.read_bytes()
        self.choose([False, True])  # Leave without saving; explicitly reuse the definitions.
        second = self.folder / "second.tfproj"
        with patch("themeforge.app.filedialog.asksaveasfilename", return_value=str(second)):
            self.app.save_project()
        self.assertEqual(self.first.read_bytes(), original)
        opened = load_project(second)
        self.assertEqual(opened.transcript_paths, (self.new_source,))
        self.assertEqual(opened.documents, (TranscriptDocument(self.new_source.name, self.new_text),))
        self.assertIsNone(opened.result)
        self.assertEqual(opened.audit_events, ())
        self.assertEqual(opened.researcher_name, "")
        self.assertEqual(opened.settings.central_theme, "")
        self.assertEqual(opened.settings.codebook_entries, (self.entry,))
        self.assertIsNone(opened.codebook_path)
        self.assertEqual(load_project(self.first), self.state)
        with patch("themeforge.app.filedialog.askopenfilename", return_value=str(second)):
            self.app.open_project()
        with patch("themeforge.app.messagebox.showerror", side_effect=AssertionError):
            self.app.analyze()
        report_path = self.folder / "second.json"
        with patch("themeforge.app.filedialog.asksaveasfilename", return_value=str(report_path)):
            self.app.save_report()
        report_text = report_path.read_text(encoding="utf-8")
        quotes = [q for t in json.loads(report_text)["themes"] for q in t["quotes"]]
        self.assertTrue(quotes)
        self.assertEqual({(q["source_name"], q["text"]) for q in quotes},
                         {(self.new_source.name, self.new_text.split(": ", 1)[1])})
        self.assertNotIn("Outgoing analysis note.", report_text)
        self.assertEqual(self.first.read_bytes(), original)

    def test_cancel_failed_read_and_failed_save_preserve_active_study(self):
        before = self.app._project_state()
        original = self.first.read_bytes()
        self.choose([None])
        self.assertEqual(self.app._project_state(), before)
        self.choose([False, None])
        self.assertEqual(self.app._project_state(), before)
        with patch("themeforge.app.messagebox.showerror"):
            self.choose([], self.folder / "missing.txt")
            self.assertEqual(self.app._project_state(), before)
            with patch("themeforge.app.save_project", side_effect=OSError("disk full")):
                self.choose([True, False])
        self.assertEqual(self.app._project_state(), before)
        self.assertEqual(self.app.project_path, self.first)
        self.assertEqual(self.first.read_bytes(), original)
        self.app.project_path = None
        with patch("themeforge.app.filedialog.asksaveasfilename", return_value=""):
            self.choose([True, False])
        self.assertEqual(self.app._project_state(), before)
        self.assertIsNone(self.app.project_path)

    def test_save_before_remove_keeps_applied_edit_and_stops_for_unapplied_text(self):
        self.app.document_memo_text.insert("end", " Not applied yet.")
        original = self.first.read_bytes()
        with patch("themeforge.app.messagebox.askyesnocancel", side_effect=[True]):
            self.app.remove_transcripts()
        self.assertEqual(self.app.project_path, self.first)
        self.assertEqual(self.first.read_bytes(), original)
        self.assertIn("Not applied yet.", self.app.document_memo_text.get("1.0", "end-1c"))
        self.app.documents[0] = replace(self.app.documents[0], memo="Revised outgoing memo.")
        self.app._refresh_document_memo()
        with patch("themeforge.app.messagebox.askyesnocancel", side_effect=[True, False]):
            self.app.remove_transcripts()
        self.assertEqual(load_project(self.first).documents[0].memo, "Revised outgoing memo.")
        self.assertIsNone(self.app.project_path)
        self.assertFalse(self.app.documents)
        self.assertFalse(self.app.audit_events)
        self.assertFalse(self.app.codebook_entries)

    def test_initial_import_keeps_setup_but_detaches_saved_template(self):
        template = replace(self.state, transcript_paths=(), documents=(), audit_events=(), result=None)
        save_project(self.first, template)
        self.app._restore_project(load_project(self.first), self.first)
        self.choose([])  # No preceding study to leave.
        self.assertIsNone(self.app.project_path)
        self.assertEqual(self.app.central_theme.get(), "First question")
        self.assertEqual(self.app.codebook_entries, (self.entry,))
        self.assertEqual(load_project(self.first), template)

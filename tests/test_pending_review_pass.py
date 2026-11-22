from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from themeforge.analysis_types import AnalysisResult, AnalysisSettings, TranscriptDocument
from themeforge.app import ThemeForgeApp
from themeforge.project_io import PendingVisit, ProjectState, load_project, save_project


class PendingReviewPassTests(unittest.TestCase):
    def test_only_a_confirmed_successful_pass_promotes_the_saved_snapshots(self):
        with tempfile.TemporaryDirectory() as folder:
            first, second = (Path(folder) / name for name in ("desk.txt", "hall.txt"))
            source = TranscriptDocument(first.name, "Guest: The bell was silent.", case_label="North")
            pending = PendingVisit(second, "Guest: Two notes sounded from the hall.")
            prior = ProjectState((first,), None, (source,), AnalysisSettings(), AnalysisResult("1", 1, 0, [], []))
            active = Path(folder) / "collected.tfproj"
            save_project(active, replace(prior, pending_visits=(pending,)), new_only=True)
            original = active.read_bytes()
            app = ThemeForgeApp()
            self.addCleanup(app.destroy)
            app._restore_project(load_project(active), active)
            with patch("themeforge.app.messagebox.askokcancel", return_value=False) as confirm, \
                 patch("themeforge.app.analyze_documents_with_units") as engine:
                app.analyze()
                self.assertIn("pending", confirm.call_args.args[1])
                self.assertEqual(confirm.call_args.kwargs["default"], "cancel")
                engine.assert_not_called()
            with patch("themeforge.app.messagebox.askokcancel", return_value=True), \
                 patch("themeforge.app.analyze_documents_with_units", side_effect=ValueError("Unavailable")), \
                 patch("themeforge.app.messagebox.showerror") as error:
                app.analyze()
                error.assert_called_once()
            self.assertEqual(app.documents, [source])
            self.assertEqual(app.pending_visits, (pending,))
            self.assertEqual(app.audit_events, [])
            self.assertEqual(active.read_bytes(), original)

            generated = AnalysisResult("1", 2, 0, [], [])
            with patch("themeforge.app.messagebox.askokcancel", return_value=True), \
                 patch("themeforge.app.analyze_documents_with_units", return_value=(generated, [])) as engine:
                app.analyze()
            documents = (source, TranscriptDocument(second.name, pending.text))
            self.assertEqual(tuple(engine.call_args.args[0]), documents)
            saved = load_project(active)
            self.assertEqual(saved.documents, documents)
            self.assertEqual(saved.transcript_paths, (first, second))
            self.assertEqual(saved.pending_visits, ())
            self.assertTrue(saved.is_case_master)
            self.assertEqual(saved.result, generated)
            self.assertEqual([event.action for event in saved.audit_events], ["analysis_run"])

    def test_append_refuses_a_changed_project_before_choosing_a_destination(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "master.tfproj"
            source = TranscriptDocument("visit.txt", "An open window.", case_label="North")
            state = ProjectState((Path(folder) / source.name,), None, (source,), AnalysisSettings())
            save_project(path, state)
            app = ThemeForgeApp()
            self.addCleanup(app.destroy)
            app._restore_project(load_project(path), path)
            save_project(path, replace(load_project(path), researcher_name="Other reviewer"))
            latest = path.read_bytes()
            with patch("themeforge.app.choose_transcript_files", return_value=[Path(folder) / "next.txt"]), \
                 patch("themeforge.app.filedialog.asksaveasfilename") as destination, \
                 patch("themeforge.app.messagebox.showerror") as error:
                app.append_visit()
                destination.assert_not_called()
                self.assertIn("changed", error.call_args.args[1])
            self.assertEqual(path.read_bytes(), latest)
            self.assertEqual(app.pending_visits, ())
            self.assertEqual(app.project_path, path)

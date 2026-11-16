from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

from themeforge.analysis_types import AnalysisSettings
from themeforge.project_io import ProjectState, save_project, load_project
from themeforge.app import ThemeForgeApp


class ProjectSaveSafetyTests(unittest.TestCase):
    def test_failed_archive_write_preserves_existing_project(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "work.tfproj"
            state = ProjectState((), None, (), AnalysisSettings())
            save_project(path, state)
            original = path.read_bytes()
            with patch("themeforge.project_io.zipfile.ZipFile.writestr", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    save_project(path, state)
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(list(Path(folder).iterdir()), [path])
            self.assertEqual(load_project(path), state)

    def test_failed_replace_preserves_old_project_and_cleans_sibling(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "work.tfproj"
            state = ProjectState((), None, (), AnalysisSettings())
            save_project(path, state)
            original = path.read_bytes()
            with patch("themeforge.project_io.os.replace", side_effect=PermissionError("locked")):
                with self.assertRaises(PermissionError):
                    save_project(path, state)
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(list(Path(folder).iterdir()), [path])

    def test_successful_replacement_reopens_new_state(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "work.tfproj"
            before = ProjectState((), None, (), AnalysisSettings(), researcher_name="Before")
            after = ProjectState((), None, (), AnalysisSettings(), researcher_name="After")
            save_project(path, before)
            save_project(path, after)
            self.assertEqual(load_project(path), after)
            self.assertEqual(list(Path(folder).iterdir()), [path])

    def app(self):
        return SimpleNamespace(input_paths=[Path("old.txt")], documents=["old"],
            project_path=Path("original.tfproj"), _load_documents=Mock(return_value=["new"]),
            _reset_analysis_view=Mock(), file_summary=Mock(), _render_transcript_tabs=Mock(), status_text=Mock())

    def test_replacing_transcripts_detaches_previous_project(self):
        app = self.app()
        with patch("themeforge.app.filedialog.askopenfilenames", return_value=("new.txt",)):
            ThemeForgeApp.open_transcript(app)
        self.assertIsNone(app.project_path)
        self.assertEqual(app.input_paths, [Path("new.txt")])
        with patch("themeforge.app.save_project") as save:
            self.assertIsNone(ThemeForgeApp._autosave_project(app))
            save.assert_not_called()

    def test_failed_import_keeps_old_paths_documents_and_project(self):
        app = self.app()
        app._load_documents.side_effect = OSError("cannot read")
        with patch("themeforge.app.filedialog.askopenfilenames", return_value=("new.txt",)), patch("themeforge.app.messagebox.showerror"):
            ThemeForgeApp.open_transcript(app)
        self.assertEqual(app.input_paths, [Path("old.txt")])
        self.assertEqual(app.documents, ["old"])
        self.assertEqual(app.project_path, Path("original.tfproj"))
        app._reset_analysis_view.assert_not_called()

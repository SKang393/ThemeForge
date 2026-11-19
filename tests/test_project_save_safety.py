from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from themeforge.analysis_types import AnalysisSettings
from themeforge.project_io import ProjectState, save_project, load_project


class ProjectSaveSafetyTests(unittest.TestCase):
    def test_previous_copy_cannot_replace_a_file_created_during_serialization(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "prior.tfproj"
            def competing_save(fd):
                with path.open("xb") as stream:
                    stream.write(b"already claimed by a different save")
            state = ProjectState((), None, (), AnalysisSettings())
            with patch("themeforge.project_io.os.fsync", side_effect=competing_save), self.assertRaises(FileExistsError):
                save_project(path, state, new_only=True)
            self.assertEqual(path.read_bytes(), b"already claimed by a different save")
            self.assertEqual(list(Path(folder).iterdir()), [path])

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

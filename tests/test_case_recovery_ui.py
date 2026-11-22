from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from themeforge.analysis_types import AnalysisSettings, TranscriptDocument
from themeforge.app import ThemeForgeApp
from themeforge.case_recovery import older_working_copy
from themeforge.case_tab import CaseTab
from themeforge.project_io import ProjectState, load_project, save_project


class CaseRecoveryDialogTests(unittest.TestCase):
    def test_recovery_rechecks_both_files_after_the_output_picker(self):
        for changed in ("master", "returned"):
            with self.subTest(changed=changed), tempfile.TemporaryDirectory() as tmp:
                master = Path(tmp) / "master.tfproj"
                returned = Path(tmp) / "returned.tfproj"
                output = Path(tmp) / "recovered.tfproj"
                doc = TranscriptDocument("Visit", "A fictional observation.", case_label="Lantern")
                save_project(master, ProjectState((), None, (doc,), AnalysisSettings()))
                original = load_project(master)
                revision = save_project(returned, older_working_copy(original))
                panel = Mock()
                panel._active_file.return_value = (returned, revision)

                def choose_output(**_options):
                    if changed == "master":
                        new_doc = replace(doc, case_label="Kite")
                        save_project(master, replace(original, documents=(new_doc,)))
                    else:
                        save_project(returned, replace(load_project(returned), researcher_name="Second reader"))
                    return str(output)

                with patch("themeforge.case_tab.filedialog.askopenfilename", return_value=str(master)), \
                     patch("themeforge.case_tab.CaseRecoveryPreview") as preview, \
                     patch("themeforge.case_tab.filedialog.asksaveasfilename", side_effect=choose_output):
                    preview.return_value.result = True
                    CaseTab._copy_project(panel, recover=True)
                preview.assert_called_once_with(panel.winfo_toplevel(), master, original.documents)
                self.assertFalse(output.exists())
                self.assertIn("changed", panel.status.set.call_args.args[0])

    def test_copy_stops_before_pickers_for_unsaved_work_or_an_unapplied_case_label(self):
        app = Mock()
        app.project_path = Path("study.tfproj")
        app._saved_state.file_revision = b"saved"
        app._has_unsaved_work.return_value = True
        with self.assertRaisesRegex(ValueError, "pending edits"):
            ThemeForgeApp._saved_case_file(app)
        panel = Mock()
        panel.source_box.current.return_value = 0
        panel._documents = (TranscriptDocument("Visit", "A fictional observation.", case_label="Kite"),)
        panel.label_box.get.return_value = "Lantern"
        with self.assertRaisesRegex(ValueError, "Apply the case label"):
            CaseTab._active_file(panel)
        panel._saved_file.assert_not_called()

"""Recipient checks reuse a window without carrying success into a different file selection."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from test_evidence_handoff import press
from themeforge.app import ThemeForgeApp, main
from themeforge.handoff_dialog import HandoffDialog


class HandoffDialogTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "win32", "Windows recipient dialog")
    def test_toolbar_and_menu_open_the_same_owned_verifier(self):
        with patch("themeforge.windows_handoff.mount_handoff_button") as mount:
            app = ThemeForgeApp()
        self.addCleanup(app.destroy)
        mount.assert_called_once()
        toolbar_command = mount.call_args.args[1]
        app.update()
        menu = app.nametowidget(app.cget("menu"))
        handoff = app.nametowidget(menu.entrycget("Handoff", "menu"))
        with patch("themeforge.windows_handoff.show_windows_handoff") as show:
            toolbar_command()
            handoff.invoke("Verify handoff…")
            self.assertEqual(show.call_count, 2)
            owner = int(app.wm_frame(), 0)
            self.assertTrue(owner)
            self.assertEqual([args for args, _ in show.call_args_list], [(owner,), (owner,)])

    @unittest.skipUnless(sys.platform == "win32", "Windows recipient dialog")
    def test_direct_recipient_launch_uses_no_project_window(self):
        with patch.object(sys, "argv", ["ThemeForge.exe", "--verify-handoff"]), \
                patch("themeforge.windows_handoff.show_windows_handoff") as show, \
                patch("themeforge.app.ThemeForgeApp") as workspace:
            self.assertEqual(main(), 0)
            show.assert_called_once_with(0)
            workspace.assert_not_called()

    def test_empty_project_can_check_files_and_replacing_a_file_invalidates_the_result(self):
        app = ThemeForgeApp()
        self.addCleanup(app.destroy)
        app.withdraw()
        before = app._project_state()
        dialog = HandoffDialog(app)

        paths = ["evidence.csv", "interview.txt", "codes.csv", "corrected.txt"]
        with patch("themeforge.handoff_dialog.filedialog.askopenfilename", side_effect=paths):
            for label in ("Evidence CSV…", "Transcript…", "Codebook CSV…"):
                press(dialog, label)
            with patch("themeforge.handoff_dialog.verify_handoff", return_value="Checked the selected files.") as verify:
                press(dialog, "Verify files")
                verify.assert_called_once_with(*(Path(name) for name in paths[:3]))
            self.assertIn("Checked the selected files.", dialog.report.get("1.0", "end"))
            press(dialog, "Transcript…")
            self.assertNotIn("Checked the selected files.", dialog.report.get("1.0", "end"))
            with patch("themeforge.handoff_dialog.verify_handoff", side_effect=ValueError("Transcript differs.")):
                press(dialog, "Verify files")
            self.assertIn("Verification failed.\n\nTranscript differs.", dialog.report.get("1.0", "end"))

        self.assertEqual(before, app._project_state())

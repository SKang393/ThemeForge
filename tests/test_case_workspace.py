import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_evidence_handoff import press
from themeforge.app import ThemeForgeApp
from themeforge.rigor_dialog import RigorDialog


class CaseWorkspaceTests(unittest.TestCase):
    def test_assigning_before_analysis_keeps_a_memo_draft_and_detects_a_stale_assignment(self):
        app = ThemeForgeApp()
        self.addCleanup(app.destroy)
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "visit.txt"
            source.write_text("Guest: I counted the blue pebbles twice.", encoding="utf-8")
            with patch("themeforge.app.filedialog.askopenfilenames", return_value=[str(source)]):
                press(app, "Choose files")

        app.document_memo_text.insert("1.0", "A draft still being written.")
        with patch("themeforge.app.messagebox.showinfo") as info:
            press(app, "Supported CSV…")
            info.assert_called_once()
        press(app, "Rigor tools")
        dialog, = [w for w in app.winfo_children() if isinstance(w, RigorDialog)]
        tab = dialog.case_tab
        notebook = tab.master
        self.assertEqual(notebook.select(), str(tab))
        self.assertTrue(all(str(notebook.tab(i, "state")) == "disabled" for i in range(4)))
        tab.label_box.set("First group")
        press(tab, "Assign case")
        self.assertEqual(app.documents[0].case_label, "First group")
        self.assertEqual(app.document_memo_text.get("1.0", "end-1c"), "A draft still being written.")
        self.assertEqual(app.documents[0].memo, "")
        self.assertEqual(app.audit_events, [])
        self.assertIsNone(app.result)

        # Another assignment arrives while this view still has the previous one selected.
        app._assign_document_case(app.documents[0], "Second group")
        tab.label_box.set("Stale edit")
        press(tab, "Assign case")
        self.assertEqual(app.documents[0].case_label, "Second group")
        self.assertIn("changed", tab.status.get())
        self.assertEqual(tab.label_box.get(), "Second group")
        press(tab, "Clear assignment")
        self.assertEqual(app.documents[0].case_label, "")
        self.assertEqual(app.audit_events, [])

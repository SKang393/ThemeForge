"""Revisit earlier readings after narrowing one code in a fictional two-source study."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from test_evidence_handoff import export, press
from themeforge.app import ThemeForgeApp
from themeforge.match_reviews import REVIEW_LABELS
from themeforge.rigor_dialog import RigorDialog


SOLO = "I drew the dial draft alone before the workshop opened."
JOINT = "We drew the dial draft together and both signed the sheet."
LABEL = "I tied a return label to the spindle before packing it."
OLD = "A maker prepares a dial draft."
NEW = "Two makers prepare the dial draft together and both agree."
READINGS = {
    ("Dial draft", "bench.txt", 1): (7, 62, SOLO, "Indigo", "deferred", "Check when the drawing was made."),
    ("Dial draft", "bench.txt", 2): (70, 125, SOLO, "Ochre", "supported", "The maker drew it before opening."),
    ("Dial draft", "tower.txt", 1): (7, 65, JOINT, "Ochre", "supported", "Both makers signed their drawing."),
    ("Return label", "tower.txt", 2): (73, 128, LABEL, "Ochre", "supported", "The label was attached for return."),
}


class CodebookReconsiderationTests(unittest.TestCase):
    def test_revisit_only_this_readers_earlier_judgments_and_keep_unchanged_support(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        app = ThemeForgeApp()
        self.addCleanup(app.destroy)
        app.minsize(1, 1)
        app.geometry("1024x705+0+0")
        app.update()
        files = []
        for name, lines in (("bench.txt", (SOLO, SOLO)), ("tower.txt", (JOINT, LABEL))):
            path = root / name
            path.write_text("\n".join(f"Maker: {line}" for line in lines), encoding="utf-8")
            files.append(str(path))
        with patch("themeforge.app.filedialog.askopenfilenames", return_value=files):
            press(app, "Choose files")

        def import_book(definition):
            path = root / "codes.csv"
            path.write_text(f"code,definition\nDial draft,{definition}\nReturn label,A return label for the spindle.\n",
                            encoding="utf-8")
            with patch("themeforge.app.filedialog.askopenfilename", return_value=str(path)):
                press(app, "Choose codebook")

        import_book(OLD)
        press(app, "Analyze")
        seen = set()
        for index, theme in enumerate(app.result.themes):
            app._select_theme(index)
            for _ in app.current_quote_matches:
                quote = app._current_quote_match().quote
                key = theme.name, quote.source_name, quote.source_line
                start, end, words, actor, status, reason = READINGS[key]
                self.assertEqual((quote.source_start, quote.source_end, quote.text), (start, end, words))
                app.researcher_name.set(actor)
                with patch("themeforge.app.MatchReviewReason") as prompt:
                    prompt.return_value.result = reason
                    press(app, REVIEW_LABELS[status])
                    prompt.assert_called_once()
                seen.add(key)
                press(app, "Next quote")
        self.assertEqual(seen, set(READINGS))
        app.researcher_name.set("Ochre")

        def rigor():
            press(app, "Rigor tools")
            app.update()
            dialog, = [w for w in app.winfo_children() if isinstance(w, RigorDialog)]
            return dialog

        def exports():
            dialog = rigor()
            tab = dialog.audit_tree.master
            tab.master.select(tab)
            app.update()
            dialog.supported_sources.selection_set(0, "end")
            evidence, history = root / "evidence.csv", root / "history.csv"
            rows = export(tab, "Export supported matches CSV", evidence)
            export(tab, "Export audit CSV", history)
            dialog.destroy()
            return rows, evidence.read_bytes(), history.read_bytes()

        original, _, _ = exports()
        self.assertEqual(len(original), 3)
        original_events = tuple(e for e in app.audit_events if e.action == "match_review_note")
        self.assertEqual(len(original_events), 4)
        import_book(NEW)
        project = root / "workshop.tfproj"
        with patch("themeforge.app.filedialog.asksaveasfilename", return_value=str(project)):
            press(app, "Save project")
        saved = project.read_bytes()
        with patch("themeforge.app.filedialog.askopenfilename", return_value=str(project)):
            press(app, "Open project")
        before = exports()
        self.assertEqual(before[0], [r for r in original if r["quote"] == LABEL])
        self.assertEqual(len(before[0]), 1)
        self.assertEqual(tuple(e for e in app.audit_events if e.action == "match_review_note"), original_events)
        state = app._project_state()

        def open_reading(code, name, line, parent_status, child_status):
            dialog = rigor()
            tree = dialog.source_matrix_tree
            parent, = [i for i in tree.get_children() if tree.item(i, "text").startswith(code + " (")
                       and tree.set(i, "source") == name]
            self.assertEqual(tree.set(parent, "reviews"), parent_status)
            item, = tree.get_children(parent)
            start, end, words, actor, status, reason = READINGS[code, name, line]
            self.assertEqual(tree.item(item, "text"), f"Line {line} [{start}, {end})")
            self.assertEqual(tree.set(item, "reviews"), child_status)
            tree.focus_force()
            tree.see(item)
            tree.selection_set(item)
            tree.focus(item)
            app.update()
            tree.event_generate("<Return>")
            app.update()
            self.assertEqual(dialog.state(), "withdrawn")
            self.assertTrue(app.quote_review_page.winfo_viewable())
            self.assertEqual(app._current_document_name(), name)
            widget = app.transcript_widgets[name]
            self.assertEqual(widget.get("selected_quote.first", "selected_quote.last"), words)
            self.assertEqual(len(widget.get("1.0", "selected_quote.first")), start)
            self.assertIn(f"{status} by {actor}", app.match_review_text.get())
            self.assertIn(reason, app.match_review_text.get())
            if child_status.startswith("Historical"):
                self.assertIn("Historical codebook match", app.match_review_text.get())
                with patch("themeforge.app.MatchReviewReason") as prompt:
                    press(app, "Supports this theme")
                    prompt.assert_not_called()

        open_reading("Dial draft", "bench.txt", 2, "1 historical review", "Historical: supported")
        open_reading("Dial draft", "tower.txt", 1, "1 historical review", "Historical: supported")
        open_reading("Return label", "tower.txt", 2, "1 supported", "supported")
        self.assertEqual(app._project_state(), state)
        app.researcher_name.set("Indigo")
        open_reading("Dial draft", "bench.txt", 1, "1 historical review", "Historical: deferred")
        dialog = rigor()
        tree = dialog.source_matrix_tree
        for item in tree.get_children():
            if tree.set(item, "source") == "tower.txt":
                self.assertEqual(tree.set(item, "reviews"), "No current reviews")
                self.assertFalse(tree.get_children(item))
        dialog.destroy()
        app.researcher_name.set("Ochre")
        self.assertEqual(exports(), before)
        self.assertEqual(app._project_state(), state)
        self.assertEqual(project.read_bytes(), saved)

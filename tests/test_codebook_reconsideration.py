"""Revisit earlier readings after narrowing one code in a fictional two-source study."""
from pathlib import Path
from tempfile import TemporaryDirectory
from tkinter import ttk
import unittest
from unittest.mock import patch

from test_evidence_handoff import descendants, export, press
from themeforge.app import ThemeForgeApp
from themeforge.match_reviews import REVIEW_LABELS
from themeforge.rigor_dialog import ReviewHistoryScope, RigorDialog


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
        screen = app.winfo_screenwidth(), app.winfo_screenheight()
        if app.tk.call("tk", "windowingsystem") == "win32" and screen == (1024, 768):
            app.state("zoomed")
        else:
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
            dialog.geometry("900x600+0+0")
            app.update()
            self.assertEqual((dialog.winfo_width(), dialog.winfo_height()), (900, 600))
            return dialog

        def exports(stage):
            folder = root / stage
            folder.mkdir()
            for suffix in ("md", "csv", "json"):
                with patch("themeforge.app.filedialog.asksaveasfilename", return_value=str(folder / f"quotes.{suffix}")):
                    press(app, "Export report")
            dialog = rigor()
            for axis, tree in (("source", dialog.source_matrix_tree), ("speaker", dialog.speaker_matrix_tree)):
                tree.master.master.select(tree.master)
                export(tree.master, "Export matrix CSV", folder / f"{axis}.csv")
            tab = dialog.audit_tree.master
            tab.master.select(tab)
            app.update()
            dialog.supported_sources.selection_set(0, "end")
            rows = export(tab, "Export supported matches CSV", folder / "evidence.csv")
            export(tab, "Export audit CSV", folder / "audit.csv")
            scope = dialog.supported_sources
            scope.selection_clear(0, "end")
            scope.selection_set(scope.get(0, "end").index("bench.txt"))

            def choose_decisions(history, _window):
                history.choices.selection_set(0, "end")
                history.show_preview()
                history.ok()

            with patch.object(ReviewHistoryScope, "wait_window", autospec=True, side_effect=choose_decisions):
                chosen, = export(tab, "Choose review history…", folder / "selected-history.csv")
            self.assertEqual(chosen["source_start"], "70")
            dialog.destroy()
            outputs = {path.name: path.read_bytes() for path in folder.iterdir()}
            self.assertEqual(set(outputs), {"quotes.md", "quotes.csv", "quotes.json", "source.csv", "speaker.csv",
                                            "evidence.csv", "audit.csv", "selected-history.csv"})
            return rows, outputs

        original, _ = exports("original")
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
        before = exports("before-navigation")
        self.assertEqual(before[0], [r for r in original if r["quote"] == LABEL])
        self.assertEqual(len(before[0]), 1)
        self.assertEqual(tuple(e for e in app.audit_events if e.action == "match_review_note"), original_events)
        state = app._project_state()

        def open_reading(code, name, line, parent_status, child_status):
            dialog = rigor()
            tree = dialog.source_matrix_tree
            self.assertIn("excluded from current support", dialog.source_review_hint.get())
            parent, = [i for i in tree.get_children() if tree.item(i, "text").startswith(code + " (")
                       and tree.set(i, "source") == name]
            self.assertEqual(tree.set(parent, "reviews"), parent_status)
            item, = tree.get_children(parent)
            start, end, words, actor, status, reason = READINGS[code, name, line]
            self.assertEqual(tree.item(item, "text"), f"Line {line} [{start}, {end})")
            self.assertEqual(tree.set(item, "reviews"), child_status)
            tree.focus_force()
            app.update()
            tree.focus(parent)
            tree.selection_set(parent)
            tree.event_generate("<Right>")
            app.update()
            self.assertTrue(tree.item(parent, "open"))
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
            label, = [w for w in descendants(app.quote_review_page) if isinstance(w, ttk.Label)
                      and str(w.cget("textvariable")) == str(app.match_review_text)]
            x, y = label.winfo_rootx(), label.winfo_rooty()
            self.assertEqual(app.winfo_containing(x + label.winfo_reqwidth() - 1,
                                                  y + label.winfo_reqheight() - 1), label)
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
        self.assertEqual(exports("after-navigation"), before)
        self.assertEqual(app._project_state(), state)
        self.assertEqual(project.read_bytes(), saved)
        # Analyze autosaves. Exercise a new pass on a copy of the revised study.
        copy = root / "new-pass.tfproj"
        copy.write_bytes(saved)
        with patch("themeforge.app.filedialog.askopenfilename", return_value=str(copy)):
            press(app, "Open project")
        with patch("themeforge.app.messagebox.askokcancel", return_value=True):
            press(app, "Analyze")
        dialog = rigor()
        tree = dialog.source_matrix_tree
        self.assertTrue(tree.get_children())
        self.assertTrue(all(tree.set(row, "reviews") == "No current reviews" for row in tree.get_children()))
        self.assertTrue(all(not tree.get_children(row) for row in tree.get_children()))
        dialog.destroy()
        self.assertEqual(tuple(e for e in app.audit_events if e.action == "match_review_note"), original_events)
        self.assertEqual(project.read_bytes(), saved)
        print(f"Historical navigation on {screen[0]}x{screen[1]} in a 900x600 By source dialog. "
              f"Verified Ochre at [70, 125) and Indigo at [7, 62). "
              f"Preserved project bytes and {len(before[1])} export files.")

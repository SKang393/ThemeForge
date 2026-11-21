"""Read one fictional market finding across interviews through the source matrix."""
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from tkinter import ttk
import unittest
from unittest.mock import patch

from test_evidence_handoff import descendants, export, press
from themeforge.app import ThemeForgeApp
from themeforge.match_reviews import REVIEW_LABELS
from themeforge.rigor_dialog import RigorDialog

# Document-relative character offsets in the imported texts, with the end excluded.
CASES = {
    "cove.txt": (
        (8, 81, "The bell rang at noon and I closed the market gate as soon as I heard it.", "supported",
         "The keeper directly links hearing the bell to closing the gate."),
        (90, 151, "I painted a bell and a market gate on the sign for our stall.", "declined",
         "A painted sign describes neither a closure nor an exception."),
    ),
    "dune.txt": (
        (8, 73, "The bell rang at noon but the market gate stayed open until dusk.", "declined",
         "Counterexample: the bell rang, yet the gate stayed open until dusk."),
        (82, 150, "I found the bell rope tangled near the market gate the next morning.", "deferred",
         "The rope may be relevant but we do not know when it tangled."),
    ),
    "fen.txt": (
        (8, 71, "I could see the market gate from the bell tower while I waited.", None, None),
        (80, 142, "We walked past the market gate to return the bell to its hook.", None, None),
    ),
}


class CrossCaseReviewTests(unittest.TestCase):
    def test_trace_supported_declined_and_deferred_passages_without_deciding_for_a_source(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        for name, rows in CASES.items():
            text = "\n".join("Keeper: " + row[2] for row in rows)
            for start, end, words, _, _ in rows:
                self.assertEqual(text[start:end], words)
            (root / name).write_text(text, encoding="utf-8")
        book = root / "signals.csv"
        book.write_text("code,definition\nBell closes the market gate,The keeper closes the market gate "
                        "in response to the bell.\n", encoding="utf-8")
        app = ThemeForgeApp()
        self.addCleanup(app.destroy)
        screen = app.winfo_screenwidth(), app.winfo_screenheight()
        if app.tk.call("tk", "windowingsystem") == "win32" and screen == (1024, 768):
            app.state("zoomed")
        else:
            app.minsize(1, 1)
            app.geometry("1024x705+0+0")
        app.update()
        with patch("themeforge.app.filedialog.askopenfilenames", return_value=[str(root / name) for name in CASES]):
            press(app, "Choose files")
        with patch("themeforge.app.filedialog.askopenfilename", return_value=str(book)):
            press(app, "Choose codebook")
        app.researcher_name.set("Flax")
        press(app, "Analyze")
        self.assertEqual(len(app.current_quote_matches), 6)

        def record(status, reason):
            with patch("themeforge.app.MatchReviewReason") as prompt:
                prompt.return_value.result = reason
                press(app, REVIEW_LABELS[status])
                prompt.assert_called_once()

        for _ in app.current_quote_matches:
            quote = app._current_quote_match().quote
            start, end, words, status, reason = CASES[quote.source_name][quote.source_line - 1]
            self.assertEqual((quote.source_start, quote.source_end, quote.text), (start, end, words))
            if status == "supported":
                app.researcher_name.set("Reed")
                record("deferred", "I want to check the timing.")
                app.researcher_name.set("Flax")
                record("deferred", "I will return to the keeper's explanation.")
            if status:
                record(status, reason)
            press(app, "Next quote")

        path = root / "market.tfproj"
        with patch("themeforge.app.filedialog.asksaveasfilename", return_value=str(path)):
            press(app, "Save project")
        saved_bytes = path.read_bytes()
        with patch("themeforge.app.filedialog.askopenfilename", return_value=str(path)):
            press(app, "Open project")
        saved_state = app._project_state()
        memo_tab = app.quote_memo_text.master
        memo_tab.master.select(memo_tab)
        press(app, "Rigor tools")
        dialog, = [w for w in app.winfo_children() if isinstance(w, RigorDialog)]
        dialog.geometry("900x600+0+0")
        app.update()
        tree = dialog.source_matrix_tree
        def export_tables(stage):
            tabs = tree.master.master
            tabs.select(tree.master)
            app.update()
            matrix, audit, evidence = (root / f"{stage}-{name}.csv" for name in ("matrix", "audit", "evidence"))
            rows = export(tree.master, "Export matrix CSV", matrix)
            self.assertEqual([(r["source"], r["quote_count"]) for r in rows], [(name, "2") for name in CASES])
            tabs.select(dialog.audit_tree.master)
            app.update()
            export(dialog.audit_tree.master, "Export audit CSV", audit)
            scope = dialog.supported_sources
            scope.selection_clear(0, "end")
            scope.selection_set(scope.get(0, "end").index("cove.txt"))
            row, = export(dialog.audit_tree.master, "Export supported matches CSV", evidence)
            self.assertEqual((row["source_file"], row["match_review_actor"], row["match_review_reason"]),
                             ("cove.txt", "Flax", CASES["cove.txt"][0][4]))
            tabs.select(tree.master)
            app.update()
            return {p: p.read_bytes() for p in (matrix, audit, evidence)}

        original_exports = export_tables("before")
        self.assertIn("by Flax", dialog.source_review_hint.get())
        parents = {tree.set(i, "source"): i for i in tree.get_children()}
        self.assertEqual(set(parents), set(CASES))
        for name, summary in (("cove.txt", "1 supported, 1 declined"), ("dune.txt", "1 declined, 1 deferred"),
                              ("fen.txt", "No current reviews")):
            parent = parents[name]
            self.assertEqual((tree.set(parent, "quotes"), tree.set(parent, "reviews")), ("2", summary))
            expected = [row for row in CASES[name] if row[3]]
            children = tree.get_children(parent)
            self.assertEqual(len(children), len(expected))
            if not children:
                continue
            tree.focus_force()
            app.update()
            tree.focus(parent)
            tree.selection_set(parent)
            tree.event_generate("<Right>")
            app.update()
            self.assertTrue(tree.item(parent, "open"))
            for line, (item, (start, end, words, status, reason)) in enumerate(zip(children, expected, strict=True), 1):
                self.assertEqual(tree.item(item, "text"), f"Line {line} [{start}, {end})")
                self.assertEqual(tree.set(item, "reviews"), status)
                tree.see(item)
                tree.focus(item)
                tree.selection_set(item)
                if name == "dune.txt" and status == "declined":
                    app.update_idletasks()
                    x, y, width, height = tree.bbox(item, "reviews")
                    for _ in range(2):
                        for click in ("<ButtonPress-1>", "<ButtonRelease-1>"):
                            tree.event_generate(click, x=x + width // 2, y=y + height // 2)
                else:
                    tree.event_generate("<Return>")
                app.update()
                self.assertEqual(dialog.state(), "withdrawn")
                self.assertEqual(app._current_document_name(), name)
                widget = app.transcript_widgets[name]
                self.assertEqual(widget.get("selected_quote.first", "selected_quote.last"), words)
                self.assertEqual(len(widget.get("1.0", "selected_quote.first")), start)
                self.assertTrue(app.quote_review_page.winfo_viewable())
                self.assertIn(f"{status} by Flax", app.match_review_text.get())
                self.assertIn(reason, app.match_review_text.get())
                if name == "cove.txt" and status == "declined":
                    self.assertNotIn("counterexample", app.match_review_text.get().lower())
                label, = [w for w in descendants(app.quote_review_page) if isinstance(w, ttk.Label)
                          and str(w.cget("textvariable")) == str(app.match_review_text)]
                self.assertTrue(label.winfo_viewable())
                self.assertGreaterEqual(label.winfo_height(), label.winfo_reqheight())
                x, y = label.winfo_rootx(), label.winfo_rooty()
                self.assertEqual(app.winfo_containing(x + label.winfo_reqwidth() - 1,
                                                      y + label.winfo_reqheight() - 1), label)
                self.assertEqual(app._project_state(), saved_state)
                press(app, "Rigor tools")
                app.update()
                self.assertTrue(tree.winfo_viewable())
                self.assertEqual(tree.selection(), (item,))
                self.assertTrue(tree.item(parent, "open"))
        self.assertEqual(path.read_bytes(), saved_bytes)

        # Return to the main window through the selected review, then switch readers in the Coder name entry.
        def open_focused_passage():
            tree.focus_force()
            app.update()
            tree.event_generate("<Return>")
            app.update()
            self.assertEqual(dialog.state(), "withdrawn")

        def switch_reader(name):
            entry, = [w for w in descendants(app) if isinstance(w, ttk.Entry)
                      and str(w.cget("textvariable")) == str(app.researcher_name)]
            entry.delete(0, "end")
            entry.insert(0, name)
            press(app, "Rigor tools")
            app.update()
            self.assertTrue(tree.winfo_viewable())

        open_focused_passage()
        switch_reader("Reed")
        reed_rows = {tree.set(i, "source"): tree.set(i, "reviews") for i in tree.get_children()}
        self.assertEqual(reed_rows, {"cove.txt": "1 deferred", "dune.txt": "No current reviews",
                                     "fen.txt": "No current reviews"})
        after_exports = export_tables("after")
        self.assertEqual(list(original_exports.values()), list(after_exports.values()))
        for original, data in original_exports.items():
            self.assertEqual(original.read_bytes(), data)
        cove = next(i for i in tree.get_children() if tree.set(i, "source") == "cove.txt")
        reed_note, = tree.get_children(cove)
        tree.see(reed_note)
        tree.selection_set(reed_note)
        tree.focus(reed_note)
        open_focused_passage()
        self.assertIn("deferred by Reed", app.match_review_text.get())
        self.assertIn("I want to check the timing.", app.match_review_text.get())
        self.assertEqual(app._project_state(), replace(saved_state, researcher_name="Reed"))
        switch_reader("")
        self.assertTrue(all(not tree.get_children(i) for i in tree.get_children()))
        self.assertIn("Enter a coder name", dialog.source_review_hint.get())
        self.assertEqual(path.read_bytes(), saved_bytes)
        print(f"Cross-case reviews on {screen[0]}x{screen[1]}, matrix {dialog.winfo_width()}x{dialog.winfo_height()}. "
              "Verified four Flax passages and Reed’s independent deferral. "
              "Project, source matrix, audit and scoped evidence bytes preserved.")

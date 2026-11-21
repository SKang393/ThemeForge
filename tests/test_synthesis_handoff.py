"""Hand off a revised finding from a saved fictional three-interview study."""
from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from test_cross_case_reviews import CASES
from test_evidence_handoff import export, open_audit, press
from themeforge.app import ThemeForgeApp
from themeforge.match_reviews import REVIEW_LABELS


CLAIM = "The bell prompted closure at one visit, while another gate stayed open."
ANNOTATION = "Follow up about the dusk shift."
CONTRARY = "Counterexample to automatic closure: the gate stayed open after the bell."


class SynthesisHandoffTests(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        for name, turns in CASES.items():
            (root / name).write_text("\n".join("Keeper: " + row[2] for row in turns), encoding="utf-8")
        book = root / "codes.csv"
        book.write_text("code,definition\nBell response,How work at the market gate changed or continued after the bell.\n"
                        "Stall artwork,Painting the sign at the stall.\n", encoding="utf-8")
        with patch.object(ThemeForgeApp, "maxsize", return_value=(1024, 705)):
            app = ThemeForgeApp()
        self.addCleanup(app.destroy)
        with patch("themeforge.app.filedialog.askopenfilenames", return_value=[str(root / n) for n in CASES]):
            press(app, "Choose files")
        with patch("themeforge.app.filedialog.askopenfilename", return_value=str(book)):
            press(app, "Choose codebook")
        app.researcher_name.set("Flax")
        press(app, "Analyze")
        bell, art = app.result.themes
        self.assertTrue(any(q.source_name == "fen.txt" for q in bell.quotes))  # Deliberately unreviewed.

        def review(theme, source, line, disposition, reason):
            app._select_theme_by_id(theme.id)
            quote, = [q for q in theme.quotes if (q.source_name, q.source_line) == (source, line)]
            app._focus_quote_by_id(quote.quote_id)
            with patch("themeforge.app.MatchReviewReason") as prompt:
                prompt.return_value.result = reason
                press(app, REVIEW_LABELS[disposition])
                prompt.assert_called_once()

        app.theme_memo_text.insert("1.0", "The bell always closes the market gate.")
        press(app, "Save theme memo")
        review(bell, "cove.txt", 1, "supported", CASES["cove.txt"][0][4])
        review(bell, "cove.txt", 2, "declined", CASES["cove.txt"][1][4])
        review(bell, "dune.txt", 1, "declined", CASES["dune.txt"][0][4])
        app.theme_memo_text.delete("1.0", "end")
        app.theme_memo_text.insert("1.0", CLAIM)
        press(app, "Save theme memo")
        review(bell, "dune.txt", 1, "supported", CONTRARY)
        app.quote_memo_text.insert("1.0", ANNOTATION)
        press(app, "Save quote memo")
        review(art, "cove.txt", 2, "supported", "The keeper painted a sign for the stall.")
        project = root / "market.tfproj"
        with patch("themeforge.app.filedialog.asksaveasfilename", return_value=str(project)):
            press(app, "Save project")

        self.root, self.app, self.project = root, app, project

    def test_one_theme_traces_support_and_a_reviewed_exception(self):
        root, app, project = self.root, self.app, self.project
        saved = project.read_bytes()
        dialog = open_audit(app, project)
        bell, art = app.result.themes
        scope = dialog.supported_theme
        self.assertEqual((scope.get(), dialog.supported_sources.curselection()), ("All themes", ()))
        self.assertTrue(scope.winfo_viewable())
        for x, y in ((0, 0), (scope.winfo_width() - 1, scope.winfo_height() - 1)):
            self.assertEqual(dialog.winfo_containing(scope.winfo_rootx() + x, scope.winfo_rooty() + y), scope)
        press(dialog, "Select all sources")
        button = "Export supported matches CSV"
        all_rows = export(dialog, button, root / "all.csv")
        self.assertCountEqual([r["theme_id"] for r in all_rows], [bell.id, bell.id, art.id])
        sign, = [r for r in all_rows if r["theme_id"] == art.id]
        self.assertEqual((sign["source_file"], sign["quote"]), ("cove.txt", CASES["cove.txt"][1][2]))
        label = f"{bell.name} ({bell.id})"
        scope.current(scope["values"].index(label))
        rows = export(dialog, button, root / "finding.csv")
        self.assertEqual(rows, [r for r in all_rows if r["theme_id"] == bell.id])
        self.assertCountEqual([(r["source_file"], r["match_review_reason"]) for r in rows],
                              [("cove.txt", CASES["cove.txt"][0][4]), ("dune.txt", CONTRARY)])
        output = (root / "finding.csv").read_text(encoding="utf-8")
        for omitted in (art.name, CASES["cove.txt"][1][2], "fen.txt", ANNOTATION, CLAIM):
            self.assertNotIn(omitted, output)

        tree = dialog.source_matrix_tree
        for row in rows:
            source = row["source_file"]
            text = (root / source).read_text(encoding="utf-8")
            start, end = int(row["source_start"]), int(row["source_end"])
            self.assertEqual(text[start:end], row["quote"])
            self.assertEqual(sha256(text.encode()).hexdigest(), row["source_sha256"])
            events = [e for e in app.audit_events if e.target_id == row["match_review_target_id"]]
            self.assertEqual((events[-1].actor, events[-1].timestamp_utc),
                             (row["match_review_actor"], row["match_review_time_utc"]))
            decisions = [json.loads(e.details) for e in events]
            expected = [("declined", CASES["dune.txt"][0][4])] if source == "dune.txt" else []
            self.assertEqual([(d["disposition"], d["reason"]) for d in decisions],
                             [*expected, ("supported", row["match_review_reason"])])
            tree.master.master.select(tree.master)
            app.update()
            parent, = [i for i in tree.get_children() if tree.item(i, "text") == label and tree.set(i, "source") == source]
            location = f"Line {row['source_line']} [{start}, {end})"
            child, = [i for i in tree.get_children(parent) if tree.item(i, "text") == location]
            tree.see(child)
            tree.focus_force()
            tree.focus(child)
            tree.selection_set(child)
            app.update()
            tree.event_generate("<Return>")
            app.update()
            self.assertEqual(dialog.state(), "withdrawn")
            self.assertEqual(app._current_document_name(), source)
            widget = app.transcript_widgets[source]
            self.assertEqual(widget.get("selected_quote.first", "selected_quote.last"), row["quote"])
            self.assertEqual(len(widget.get("1.0", "selected_quote.first")), start)
            self.assertIn(row["match_review_reason"], app.match_review_text.get())
            self.assertEqual(app._current_theme().memo, CLAIM)
            press(app, "Rigor tools")

        tree.master.master.select(dialog.audit_tree.master)
        app.update()
        self.assertEqual(scope.get(), label)
        scope.current(0)
        export(dialog, button, root / "again.csv")
        self.assertEqual((root / "all.csv").read_bytes(), (root / "again.csv").read_bytes())
        scope.current(scope["values"].index(label))
        sources = dialog.supported_sources
        sources.selection_clear(0, "end")
        sources.selection_set(sources.get(0, "end").index("cove.txt"))
        self.assertEqual(export(dialog, button, root / "cove.csv"), [r for r in rows if r["source_file"] == "cove.txt"])

        # The main export shortcut takes the displayed theme into the same chooser.
        notebook = dialog.source_matrix_tree.master.master
        notebook.select(dialog.source_matrix_tree.master)
        app.theme_list.selection_clear(0, "end")
        app.theme_list.selection_set(1)
        app.theme_list.event_generate("<<ListboxSelect>>")
        app.update()
        position = (app.current_theme_index, app.current_quote_index)
        self.assertEqual(app._current_theme(), art)
        press(app, "Supported CSV…")
        self.assertEqual(notebook.select(), str(dialog.audit_tree.master))
        self.assertEqual(scope.get(), f"{art.name} ({art.id})")
        self.assertEqual(sources.curselection(), ())
        with patch("themeforge.rigor_dialog.filedialog.asksaveasfilename") as choose:
            press(dialog, button)
            choose.assert_not_called()
        sources.selection_set(sources.get(0, "end").index("cove.txt"))
        self.assertEqual(export(dialog, button, root / "sign.csv"), [sign])
        self.assertEqual((app.current_theme_index, app.current_quote_index), position)
        scope.current(scope["values"].index(label))

        # Another theme now has the same name as the removed selection, but a different ID.
        context = dialog.context
        changed = replace(context, result=replace(context.result, themes=[replace(art, name=bell.name)]))
        with patch.object(dialog, "_context_source", return_value=changed), \
                patch("themeforge.rigor_dialog.filedialog.asksaveasfilename") as choose:
            press(dialog, button)
            choose.assert_not_called()
        self.assertEqual(scope.get(), "")
        self.assertEqual(project.read_bytes(), saved)

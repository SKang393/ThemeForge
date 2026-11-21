"""Browse a missed passage in a fictional supply room and recover its manual assignment."""
from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from test_evidence_handoff import export, open_audit, press
from themeforge.app import ThemeForgeApp
from themeforge.project_io import load_project, save_project
from themeforge.rigor_dialog import RigorDialog


class SuggestionCoverageTests(unittest.TestCase):
    def compact_layout(self, app):
        screen = app.winfo_screenwidth(), app.winfo_screenheight()
        if app.tk.call("tk", "windowingsystem") == "win32" and screen == (1024, 768):
            app.state("zoomed")  # Use the actual desktop work area, including its title bar and taskbar.
        else:
            app.minsize(1, 1)
            app.geometry("1024x705+0+0")

    def assert_footer_fits(self, app):
        app.update_idletasks()
        summary, = app.preview_button.master.grid_slaves(row=0, column=0)
        self.assertLessEqual(summary.winfo_reqwidth(), summary.winfo_width())
        for button in (app.preview_button, app.unsuggested_button):
            self.assertTrue(button.winfo_viewable())
            x, y = button.winfo_rootx(), button.winfo_rooty()
            right, bottom = x + button.winfo_width() - 1, y + button.winfo_height() - 1
            for px, py in ((x, y), (right, bottom)):
                self.assertTrue(0 <= px < 1024 and 0 <= py < 768)
                self.assertEqual(app.winfo_containing(px, py), button)

    def scoped_evidence(self, dialog, source, destination):
        scope = dialog.supported_sources
        scope.selection_clear(0, "end")
        scope.selection_set(scope.get(0, "end").index(source.name))
        return export(dialog, "Export supported matches CSV", destination)

    def test_browse_without_deciding_then_apply_and_reopen(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        source = root / "supply-room.txt"
        unknown = "The tile drawer was empty. Somebody mentioned a roof leak but nobody knew where the tiles went."
        omitted = "We had six brass stencils yesterday and every one had vanished by dawn."
        text = f"Maker: {unknown}\nMaker: {omitted}"
        source.write_text(text, encoding="utf-8")
        rain = root / "rain-room.txt"
        observed = "We saw water enter through the roof leak and collect inside the crate."
        rain.write_text(f"Keeper: {observed}", encoding="utf-8")
        packing = root / "packing-room.txt"
        wrapping = "I wound a length of brown twine around the handle."
        packing.write_text(f"Packer: {wrapping}", encoding="utf-8")
        book = root / "supplies.csv"
        book.write_text("code,definition\nMaterial loss,Previously available supplies became unavailable.\n"
                        "Roof leak,Water entered through the roof.\n", encoding="utf-8")

        self.app = app = ThemeForgeApp()
        self.addCleanup(lambda: self.app.destroy())
        self.compact_layout(app)
        with patch("themeforge.app.filedialog.askopenfilenames", return_value=(str(source), str(rain), str(packing))):
            press(app, "Choose files")
        with patch("themeforge.app.filedialog.askopenfilename", return_value=str(book)):
            press(app, "Choose codebook")
        app.researcher_name.set("Supply reader")
        press(app, "Analyze")
        app.update()
        self.assertIn("2/4 eligible passages", app.status_text.get())
        self.assertIn("may need other codes", app.status_text.get())
        # Both lines describe loss, but only the possible cause on line one has a suggestion.
        theme, = app.result.themes
        self.assertEqual((theme.name, [q.text for q in theme.quotes]), ("Roof leak", [observed, unknown]))
        self.assert_footer_fits(app)
        with patch("themeforge.app.MatchReviewReason") as reason:
            reason.return_value.result = "The keeper saw the water enter."
            press(app, "Supports this theme")
            press(app, "Next quote")
            reason.return_value.result = "The cause was unknown."
            press(app, "Not this match")

        def choose_scope(app, name):
            box = app.review_source_box
            box.current(box["values"].index(name))
            box.event_generate("<<ComboboxSelected>>")
            app.update()

        before = app._project_state()
        choose_scope(app, source.name)
        self.assertIn(f"Last Analyze · {source.name} · 1 eligible passage without a suggestion", app.status_text.get())
        press(app, "Next without suggestion")
        selection = app._selected_transcript_text()
        start, end = text.index(omitted), len(text)
        self.assertEqual((selection.source_name, selection.source_line, selection.source_start,
                          selection.source_end, selection.text), (source.name, 2, start, end, omitted))
        self.assertEqual(app.quote_status.get(), f"Passage 1 of 1 · {source.name}")
        self.assertIsNone(app._visible_review_match())
        self.assertEqual(app._project_state(), before)

        app.definition_box.current(0)

        def confirm(dialog):
            self.assertEqual(dialog.passage.get("evidence.first", "evidence.last"), omitted)
            press(dialog, "Apply selected definition")

        with patch.object(app, "wait_window", side_effect=confirm):
            press(app, "Apply codebook entry…")
        # Save a changed display limit without starting a new review pass.
        app.quotes_per_theme.set(1)
        path = root / "supplies.tfproj"
        with patch("themeforge.app.filedialog.asksaveasfilename", return_value=str(path)):
            press(app, "Save project")

        saved = load_project(path)
        original = path.read_bytes()
        app.request_close()
        self.app = fresh = ThemeForgeApp()
        self.compact_layout(fresh)
        # The project is portable. The external originals now disagree or no longer exist.
        rain.unlink()
        source.write_text("Maker: The revised document mentions only a roof leak.", encoding="utf-8")
        audit = open_audit(fresh, path)
        self.assertEqual(self.scoped_evidence(audit, source, root / "supply-only.csv"), [])
        row, = self.scoped_evidence(audit, rain, root / "before.csv")
        self.assertEqual((row["source_file"], row["source_start"], row["source_end"], row["quote"],
                          row["match_review_actor"], row["match_review_reason"]),
                         (rain.name, "8", "78", observed, "Supply reader", "The keeper saw the water enter."))
        manual, = export(audit, "Export this project's manual codebook assignments", root / "manual.csv")
        self.assertEqual((manual["code_name"], manual["assigned_by"], manual["passage"], manual["source"],
                          manual["source_start"], manual["source_end"]),
                         ("Material loss", "Supply reader", omitted, source.name, str(start), str(end)))
        audit.destroy()
        fresh.update()

        self.assertTrue(fresh.preview_button.winfo_viewable())
        with patch("themeforge.app.analyze_documents_with_units") as analyze, patch("themeforge.app.save_project") as save:
            press(fresh, "Preview coverage")
            summary = fresh.status_text.get()
            self.assertIn("Preview on project text with current settings", summary)
            self.assertIn("1/4 eligible passages", summary)
            self.assert_footer_fits(fresh)

            def check_next(name, passage, offset, position):
                press(fresh, "Next without suggestion")
                selected = fresh._selected_transcript_text()
                self.assertEqual((selected.source_name, selected.source_start, selected.source_end, selected.text),
                                 (name, offset, offset + len(passage), passage))
                self.assertEqual(fresh.quote_status.get(), position)
                self.assertIn("current settings", fresh.quote_reason.get())
                self.assertIn("Review decisions are separate", fresh.quote_reason.get())
                self.assertIn("may still need another", fresh.quote_reason.get())
                self.assertIsNone(fresh._visible_review_match())

            with patch("themeforge.app.suggestion_inputs") as inputs, patch("themeforge.app.build_codebook_themes") as rank:
                choose_scope(fresh, source.name)
                self.assertIn(f"Preview (current settings) · {source.name} · 2 eligible passages", fresh.status_text.get())
                for i, passage in enumerate((unknown, omitted, unknown)):
                    check_next(source.name, passage, text.index(passage), f"Passage {1 + i % 2} of 2 · {source.name}")
                choose_scope(fresh, rain.name)
                self.assertIsNone(fresh._selected_transcript_text())
                press(fresh, "Next without suggestion")
                self.assertIn(f"{rain.name} · 0 eligible passages without a suggestion", fresh.status_text.get())
                self.assertIsNone(fresh._selected_transcript_text())
                choose_scope(fresh, "All sources")
                self.assertIn("All sources · 3 eligible passages", fresh.status_text.get())
                for i, (name, passage, offset) in enumerate((
                    (source.name, unknown, text.index(unknown)), (source.name, omitted, text.index(omitted)),
                    (packing.name, wrapping, len("Packer: ")), (source.name, unknown, text.index(unknown)),
                )):
                    check_next(name, passage, offset, f"Passage {1 + i % 3} of 3 · All sources")
                choose_scope(fresh, source.name)
                check_next(source.name, unknown, text.index(unknown), f"Passage 1 of 2 · {source.name}")
                inputs.assert_not_called()
                rank.assert_not_called()
            analyze.assert_not_called()
            save.assert_not_called()
        self.assertEqual(fresh._project_state(), saved)
        notes = [json.loads(e.details) for e in fresh.audit_events if e.action == "match_review_note"]
        self.assertEqual([(n["disposition"], n["reason"]) for n in notes],
                         [("supported", "The keeper saw the water enter."), ("declined", "The cause was unknown.")])
        press(fresh, "Rigor tools")
        audit, = [w for w in fresh.winfo_children() if isinstance(w, RigorDialog)]
        self.assertEqual(self.scoped_evidence(audit, rain, root / "after.csv"), [row])
        self.assertEqual((root / "before.csv").read_bytes(), (root / "after.csv").read_bytes())
        self.assertEqual(export(audit, "Export this project's manual codebook assignments", root / "manual-after.csv"),
                         [manual])
        audit.destroy()
        self.assertEqual(path.read_bytes(), original)

        fresh.quotes_per_theme.set(0)
        self.assertFalse(fresh.unsuggested_button.grid_info())
        self.assertIsNone(fresh._selected_transcript_text())
        choose_scope(fresh, source.name)
        press(fresh, "Preview coverage")
        self.assertIn(f"Preview (current settings) · {source.name} · 1 eligible passage without a suggestion",
                      fresh.status_text.get())
        choose_scope(fresh, "All sources")
        self.assertIn("All sources · 2 eligible passages without a suggestion", fresh.status_text.get())
        fresh.quotes_per_theme.set(1)
        # Same reviewed quote and offsets, changed text elsewhere in the embedded source.
        damaged = replace(saved.documents[0], text=text + " An additional line.")
        for name, docs, warning in (("changed", (damaged, *saved.documents[1:]), "differs from a saved review"),
                                    ("missing", saved.documents[1:], "transcript text is missing")):
            copy = root / f"{name}.tfproj"
            save_project(copy, replace(saved, documents=docs))
            archived = copy.read_bytes()
            with patch("themeforge.app.filedialog.askopenfilename", return_value=str(copy)):
                press(fresh, "Open project")
            press(fresh, "Preview coverage")
            self.assertIn("Coverage preview unavailable", fresh.status_text.get())
            self.assertIn(warning, fresh.status_text.get())
            self.assertFalse(fresh.unsuggested_button.grid_info())
            self.assertEqual(copy.read_bytes(), archived)
        self.assertEqual(path.read_bytes(), original)

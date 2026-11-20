"""Browse a missed passage in a fictional supply room and recover its manual assignment."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from test_evidence_handoff import export, open_audit, press
from themeforge.app import ThemeForgeApp


class SuggestionCoverageTests(unittest.TestCase):
    def test_browse_without_deciding_then_apply_and_reopen(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        source = root / "supply-room.txt"
        unknown = "The tile drawer was empty. Somebody mentioned a roof leak but nobody knew where the tiles went."
        omitted = "We had six brass stencils yesterday and every one had vanished by dawn."
        text = f"Maker: {unknown}\nMaker: {omitted}"
        source.write_text(text, encoding="utf-8")
        book = root / "supplies.csv"
        book.write_text("code,definition\nMaterial loss,Previously available supplies became unavailable.\n"
                        "Roof leak,Water entered through the roof.\n", encoding="utf-8")

        app = ThemeForgeApp()
        self.addCleanup(app.destroy)
        screen = app.winfo_screenwidth(), app.winfo_screenheight()
        if app.tk.call("tk", "windowingsystem") == "win32" and screen == (1024, 768):
            app.state("zoomed")  # Use the actual desktop work area, including its title bar and taskbar.
        else:
            app.minsize(1, 1)
            app.geometry("1024x705+0+0")
        with patch("themeforge.app.filedialog.askopenfilenames", return_value=(str(source),)):
            press(app, "Choose files")
        with patch("themeforge.app.filedialog.askopenfilename", return_value=str(book)):
            press(app, "Choose codebook")
        app.researcher_name.set("Supply reader")
        press(app, "Analyze")
        app.update()
        self.assertIn("1/2 eligible passages", app.status_text.get())
        self.assertIn("may need other codes", app.status_text.get())
        # Both lines describe loss, but only the possible cause on line one has a suggestion.
        theme, = app.result.themes
        quote, = theme.quotes
        self.assertEqual((theme.name, quote.text), ("Roof leak", unknown))
        button = app.unsuggested_button
        self.assertTrue(button.winfo_viewable())
        summary, = button.master.grid_slaves(row=0, column=0)
        self.assertLessEqual(summary.winfo_reqwidth(), summary.winfo_width())
        x, y = button.winfo_rootx(), button.winfo_rooty()
        right, bottom = x + button.winfo_width() - 1, y + button.winfo_height() - 1
        for px, py in ((x, y), (right, bottom)):
            self.assertTrue(0 <= px < min(screen[0], 1024) and 0 <= py < min(screen[1], 768))
            self.assertEqual(app.winfo_containing(px, py), button)
        with patch("themeforge.app.CodebookMatchReason") as reason:
            reason.return_value.result = "The cause was unknown."
            press(app, "Not this match")

        before = app._project_state()
        press(app, "Next without suggestion")
        selection = app._selected_transcript_text()
        start, end = text.index(omitted), len(text)
        self.assertEqual((selection.source_name, selection.source_line, selection.source_start,
                          selection.source_end, selection.text), (source.name, 2, start, end, omitted))
        self.assertEqual(app.quote_status.get(), "Passage 1 of 1")
        self.assertIsNone(app._visible_review_match())
        self.assertEqual(app._project_state(), before)

        app.definition_box.current(0)

        def confirm(dialog):
            self.assertEqual(dialog.passage.get("evidence.first", "evidence.last"), omitted)
            press(dialog, "Apply selected definition")

        with patch.object(app, "wait_window", side_effect=confirm):
            press(app, "Apply codebook entry…")
        path = root / "supplies.tfproj"
        with patch("themeforge.app.filedialog.asksaveasfilename", return_value=str(path)):
            press(app, "Save project")

        fresh = ThemeForgeApp()
        self.addCleanup(fresh.destroy)
        audit = open_audit(fresh, path)
        press(audit, "Select all sources")
        self.assertEqual(export(audit, "Export supported matches CSV", root / "support.csv"), [])
        row, = export(audit, "Export this project's manual codebook assignments", root / "manual.csv")
        self.assertEqual((row["code_name"], row["assigned_by"], row["passage"], row["source"],
                          row["source_start"], row["source_end"]),
                         ("Material loss", "Supply reader", omitted, source.name, str(start), str(end)))
        note, = [e for e in fresh.audit_events if e.action == "match_review_note"]
        decision = json.loads(note.details)
        self.assertEqual((note.actor, decision["disposition"], decision["match"]["quote"]["text"]),
                         ("Supply reader", "declined", unknown))
        print(f"Desktop {screen[0]}x{screen[1]}, coverage button ({x},{y}) to ({right},{bottom}). "
              f"Browsing preserved project state. Reopened one manual span [{start},{end}) and one decline.")

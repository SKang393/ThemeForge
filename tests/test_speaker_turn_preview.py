"""Speaker eligibility for a call sign and an editorial heading in fictional transcripts."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from test_evidence_handoff import export, press
from themeforge.analysis_types import SpeakerOverride
from themeforge.app import ThemeForgeApp
from themeforge.quote_units import suggestion_inputs
from themeforge.rigor_dialog import RigorDialog

BELL = "The bell rang beside the storeroom door."
COUNT = "I counted the blue spools beside the clock."
PAUSE = "The recording paused while the cart turned."
ROLE = (f"Lead: Note is the call sign sewn on your apron.\nNote: {BELL}\n"
        f"Lead: What did you count next?\nNote: {COUNT}")
EDITED = (f"Clerk: The transcript editor used headings to mark pauses.\nNote: {BELL}\n"
          f"Clerk: I waited for the spool cart.\nNote: {PAUSE}\n"
          f"{COUNT}\nClerk: I stored the blue spools on the top shelf.")


class SpeakerTurnPreviewTests(unittest.TestCase):
    def test_identical_labels_in_different_files_show_the_full_affected_turn(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        paths = [root / "role.txt", root / "edited.txt"]
        for path, text in zip(paths, (ROLE, EDITED), strict=True):
            path.write_text(text, encoding="utf-8")
        book = root / "codes.csv"
        book.write_text("code,definition\nStock count,Counting blue spools beside the clock.\n", encoding="utf-8")
        app = ThemeForgeApp()
        self.addCleanup(app.destroy)
        with patch("themeforge.app.filedialog.askopenfilenames", return_value=list(map(str, paths))):
            press(app, "Choose files")
        with patch("themeforge.app.filedialog.askopenfilename", return_value=str(book)):
            press(app, "Choose codebook")
        app.researcher_name.set("Stock reader")
        pristine = app._project_state()

        def select(dialog, name):
            item = str(dialog.keys.index((name, "Note")))
            dialog.table.selection_set(item)
            dialog.update()
            return item

        def inspect(dialog):
            dialog.geometry("480x360+20+20")
            items = []
            for name, intro, second in (("role.txt", "call sign", COUNT), ("edited.txt", "headings", PAUSE + "\n" + COUNT)):
                item = select(dialog, name)
                items.append(item)
                self.assertEqual(dialog.table.set(item, "turns"), "2")
                button = dialog.next_turn
                self.assertIs(dialog.winfo_containing(button.winfo_rootx() + button.winfo_width() // 2,
                                                      button.winfo_rooty() + button.winfo_height() // 2), button)
                self.assertIn("Turn 1 of 2", dialog.preview.get())
                self.assertIn(intro, dialog.context.get("1.0", "turn.first"))
                self.assertEqual(dialog.context.get("turn.first", "turn.last"), BELL)
                press(dialog, "Next turn")
                self.assertIn(f"{name} · Note · Line 4 · Turn 2 of 2", dialog.preview.get())
                self.assertEqual(dialog.context.get("turn.first", "turn.last"), second)
                self.assertIsNotNone(dialog.context.bbox("turn.first"))
                self.assertEqual(dialog.context.cget("state"), "disabled")
                if name == "edited.txt":
                    self.assertTrue(dialog.context.get("1.0", "turn.first").endswith("Note: "))
                    self.assertIn("Clerk: I stored", dialog.context.get("turn.last", "end"))
                press(dialog, "Next turn")
                self.assertEqual(dialog.context.get("turn.first", "turn.last"), BELL)
            dialog.table.selection_set(*items)
            dialog.update()
            self.assertTrue(dialog.next_turn.instate(["disabled"]))
            self.assertEqual(dialog.context.get("1.0", "end-1c"), "")
            item = select(dialog, "edited.txt")
            press(dialog, "Include" if dialog.table.set(item, "choice") == "Exclude" else "Exclude")
            press(dialog, "Cancel")

        def choose(action):
            with patch.object(app, "wait_window", side_effect=action) as window:
                app.review_suggestion_speakers()
                window.assert_called_once()

        choose(inspect)
        self.assertEqual(app._project_state(), pristine)

        def exclude(dialog, name):
            select(dialog, name)
            press(dialog, "Exclude")
            press(dialog, "Apply")

        def note_spans():
            _, units = suggestion_inputs(app.documents, app._analysis_settings())
            return [(u.source_name, u.source_line, u.source_start, u.source_end) for u in units if u.speaker == "Note"]

        role_spans = [("role.txt", 2, 54, 94), ("role.txt", 4, 132, 175)]
        self.assertEqual(note_spans(), role_spans + [("edited.txt", 2, 65, 105), ("edited.txt", 4, 148, 235)])
        choose(lambda dialog: exclude(dialog, "edited.txt"))
        self.assertEqual(app.speaker_overrides, (SpeakerOverride("edited.txt", "Note", False),))
        self.assertEqual(note_spans(), role_spans)
        self.assertEqual(EDITED[148:235], PAUSE + "\n" + COUNT)
        self.assertEqual(EDITED[192:235], COUNT)
        press(app, "Analyze")
        theme, = app.result.themes
        for _ in theme.quotes:
            q = app._current_quote_match().quote
            if (q.source_name, q.source_line, q.source_start, q.source_end) == role_spans[1]:
                break
            press(app, "Next quote")
        self.assertEqual((q.speaker, q.source_name, q.source_start, q.source_end, q.text),
                         ("Note", "role.txt", 132, 175, COUNT))
        reason = "The speaker describes counting the stock."
        with patch("themeforge.app.MatchReviewReason") as prompt:
            prompt.return_value.result = reason
            press(app, "Supports this theme")
            prompt.assert_called_once()
        project = root / "inventory.tfproj"
        with patch("themeforge.app.filedialog.asksaveasfilename", return_value=str(project)):
            press(app, "Save project")
        original = project.read_bytes()

        def evidence(destination):
            press(app, "Rigor tools")
            audit, = [w for w in app.winfo_children() if isinstance(w, RigorDialog)]
            audit.audit_tree.master.master.select(audit.audit_tree.master)
            scope = audit.supported_sources
            scope.selection_set(scope.get(0, "end").index("role.txt"))
            row, = export(audit, "Export supported matches CSV", destination)
            self.assertEqual((row["speaker"], row["quote"], row["source_start"], row["source_end"],
                              row["match_review_actor"], row["match_review_reason"]),
                             ("Note", COUNT, "132", "175", "Stock reader", reason))
            audit.destroy()
            return destination.read_bytes()

        before = evidence(root / "before.csv")
        with patch("themeforge.app.filedialog.askopenfilename", return_value=str(project)):
            press(app, "Open project")
        saved = app._project_state()
        choose(inspect)
        self.assertEqual(app._project_state(), saved)
        self.assertEqual(evidence(root / "after.csv"), before)
        choose(lambda dialog: exclude(dialog, "role.txt"))
        self.assertEqual(note_spans(), [])
        self.assertEqual(tuple(app.audit_events[:-1]), saved.audit_events)
        self.assertEqual(app.audit_events[-1].action, "suggestion_speakers_changed")
        self.assertEqual(app.result, saved.result)
        self.assertEqual(evidence(root / "changed-filter.csv"), before)

        # Import the known correction separately. The editorial label can now be excluded on its own.
        corrected = root / "copy.txt"
        corrected.write_text(EDITED.replace("\n" + COUNT, "\nClerk: " + COUNT), encoding="utf-8")
        copy = ThemeForgeApp()
        self.addCleanup(copy.destroy)
        with patch("themeforge.app.filedialog.askopenfilenames", return_value=[str(corrected)]):
            press(copy, "Choose files")
        with patch("themeforge.app.filedialog.askopenfilename", return_value=str(book)):
            press(copy, "Choose codebook")
        with patch.object(copy, "wait_window", side_effect=lambda dialog: exclude(dialog, corrected.name)):
            copy.review_suggestion_speakers()
        press(copy, "Analyze")
        recovered, = [q for t in copy.result.themes for q in t.quotes if q.text == COUNT]
        self.assertEqual((recovered.speaker, recovered.source_line, recovered.source_start, recovered.source_end),
                         ("Clerk", 5, 199, 242))
        self.assertFalse(any(q.speaker == "Note" for t in copy.result.themes for q in t.quotes))
        self.assertEqual(app.documents[1].text, EDITED)
        self.assertEqual(project.read_bytes(), original)

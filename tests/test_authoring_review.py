"""Verify the Windows coding view delegates decisions and exports the displayed scope."""
import csv
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
import sys
import unittest
from tkinter import ttk
from unittest.mock import patch

from themeforge.app import ThemeForgeApp
from themeforge.authoring_review import CodingReview


class AuthoringReviewTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        self.book = root / "codes.csv"
        self.book.write_text("code,definition\nBell,An audible bell sounding at the bench.\n", encoding="utf-8")
        self.destination = root / "supported.csv"
        passage = "I heard the bell sounding at the bench."
        paths = []
        for name, text in (("bench.txt", f"Reed: {passage}\nLoam: {passage}"), ("other.txt", f"Ibis: {passage}")):
            path = root / name
            path.write_text(text, encoding="utf-8")
            paths.append(str(path))
        self.app = ThemeForgeApp()
        self.addCleanup(self.app.destroy)
        self.review = CodingReview(self.app)
        with patch("themeforge.app.filedialog.askopenfilenames", return_value=paths):
            self.review.perform("import_source")
        with patch("themeforge.app.filedialog.askopenfilename", return_value=str(self.book)):
            self.review.perform("import_codebook")
        self.review.perform("set_analyst", analyst="Mica")
        self.review.perform("analyze")
        self.select("bench.txt", 2)

    def select(self, source, line):
        match, = [m for m in self.app.current_quote_matches if (m.source_name, m.quote.source_line) == (source, line)]
        self.app._focus_quote_by_id(match.quote.quote_id)
        return self.review.snapshot()

    def test_decision_is_bound_to_displayed_match_and_analyst(self):
        first = self.review.snapshot()
        self.assertIn("bench.txt, line 2", first["source"])
        self.assertEqual(first["speaker"], "Loam")
        self.assertIn("An audible bell", first["definition"])
        self.app.next_quote()
        with self.assertRaisesRegex(ValueError, "exact source passage"):
            self.review.perform("support", analyst="Mica", reason="Stale reason")
        self.assertFalse(any(e.action == "match_review_note" for e in self.app.audit_events))
        self.select("bench.txt", 2)
        with self.assertRaisesRegex(ValueError, "analyst changed"):
            self.review.perform("decline", analyst="Another reader", reason="Wrong actor")
        self.assertTrue(self.review.perform("support", analyst="Mica", reason="Sound was heard."))
        self.assertIn("Sound was heard.", self.review.snapshot()["review"])
        self.review.perform("set_analyst", analyst="Flint")
        self.assertNotIn("Sound was heard.", self.review.snapshot()["review"])
        self.assertTrue(self.review.perform("decline", analyst="Flint", reason="A different reading."))
        self.review.perform("set_analyst", analyst="Mica")
        self.assertIn("Sound was heard.", self.review.snapshot()["review"])
        self.assertEqual(len([e for e in self.app.audit_events if e.action == "match_review_note"]), 2)
        with self.assertRaisesRegex(ValueError, "decision reason"):
            self.review.perform("next_passage", reason="Unfinished reason")
        self.assertEqual(first["target"], self.review.snapshot()["target"])

    def test_linked_export_is_scoped_exclusive_and_rechecks_both_choosers(self):
        self.assertTrue(self.review.perform("support", analyst="Mica", reason="Scope includes this line."))
        self.select("other.txt", 1)
        self.assertTrue(self.review.perform("support", analyst="Mica", reason="Withhold this line."))
        self.select("bench.txt", 2)
        with patch("themeforge.authoring_review.filedialog.askopenfilename", return_value=str(self.book)), \
             patch("themeforge.authoring_review.filedialog.asksaveasfilename", return_value=str(self.destination)):
            self.review.perform("export")
            original = self.destination.read_bytes()
            with self.assertRaisesRegex(ValueError, "existing file"):
                self.review.perform("export")
            self.assertEqual(self.destination.read_bytes(), original)
        with self.destination.open(encoding="utf-8", newline="") as stream:
            row, = csv.DictReader(stream)
        self.assertEqual((row["source_file"], row["source_line"], row["speaker"]), ("bench.txt", "2", "Loam"))
        self.assertEqual(row["shared_codebook_file_sha256"], sha256(self.book.read_bytes()).hexdigest())
        self.assertNotIn("Withhold this line.", original.decode())

        def change_selection(**_):
            self.app.next_quote()
            return str(self.book)

        with patch("themeforge.authoring_review.filedialog.askopenfilename", side_effect=change_selection), \
             patch("themeforge.authoring_review.filedialog.asksaveasfilename") as save:
            with self.assertRaisesRegex(ValueError, "exact source passage"):
                self.review.perform("export")
            save.assert_not_called()
        self.select("bench.txt", 2)
        fresh = self.destination.with_name("fresh.csv")

        def change_book_revision(**_):
            self.book.write_bytes(b"\xef\xbb\xbf" + self.book.read_bytes())
            return str(fresh)

        with patch("themeforge.authoring_review.filedialog.askopenfilename", return_value=str(self.book)), \
             patch("themeforge.authoring_review.filedialog.asksaveasfilename", side_effect=change_book_revision):
            with self.assertRaisesRegex(ValueError, "shared codebook changed"):
                self.review.perform("export")
            self.assertFalse(fresh.exists())

@unittest.skipUnless(sys.platform == "win32", "Native Windows workspace launcher")
class CodingLauncherLayoutTests(unittest.TestCase):
    def test_launcher_and_other_input_controls_have_separate_hit_areas(self):
        from themeforge.windows_handoff import mount_workspace_button

        with patch("themeforge.windows_handoff.mount_workspace_button", wraps=mount_workspace_button) as mount:
            app = ThemeForgeApp()
        self.addCleanup(app.destroy)
        launcher, = [call.args[0] for call in mount.call_args_list if call.args[2] == "Coding review…"]
        for width, height in [(1440, 900), (1180, 760)]:
            with self.subTest(width=width, height=height):
                app.geometry(f"{width}x{height}")
                app.update()
                self.assertTrue(launcher.winfo_viewable())
                left, top = launcher.winfo_x(), launcher.winfo_y()
                right, bottom = left + launcher.winfo_width(), top + launcher.winfo_height()
                self.assertLessEqual(right, launcher.master.winfo_width())
                self.assertLessEqual(bottom, launcher.master.winfo_height())
                for control in launcher.master.winfo_children():
                    if isinstance(control, (ttk.Button, ttk.Label)) and control.winfo_viewable():
                        x, y = control.winfo_x(), control.winfo_y()
                        self.assertTrue(right <= x or x + control.winfo_width() <= left
                                        or bottom <= y or y + control.winfo_height() <= top, control.cget("text"))

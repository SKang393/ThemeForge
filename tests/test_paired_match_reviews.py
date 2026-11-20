"""Walk two independent copies of one synthetic study through the desktop review workflow."""
import csv
from dataclasses import replace
import json
from pathlib import Path
from shutil import copyfile
import tempfile
from tkinter import ttk
import unittest
from unittest.mock import patch

from themeforge.app import ThemeForgeApp
from themeforge.match_reviews import REVIEW_LABELS
from themeforge.project_io import load_project, save_project
from themeforge.rigor_dialog import RigorDialog


# The intended question is whether the speaker checked a closed door. The two scripts
# differ on an audible click. Identical accounts in different shifts are distinct evidence.
CHECK = "I tested the door latch after closing it."
SHIFTS = {
    "early.txt": [CHECK, "I heard the door latch click behind me.", "I left the door latch alone.",
                  "I pulled the door handle twice before leaving."],
    "late.txt": [CHECK, "I might test the door latch tomorrow.", "I waited beside the door latch.",
                 "We tugged the door handle before walking away."],
}
AMBER = {
    ("early.txt", 1): ("supported", "An explicit test after closing."),
    ("early.txt", 2): ("supported", "The audible click was a check for closure."),
    ("early.txt", 3): ("declined", "No attempt to check."),
    ("early.txt", 4): ("supported", "The speaker tested the handle."),
    ("late.txt", 3): ("deferred", "Unclear why the speaker waited."),
    ("late.txt", 4): ("deferred", "Check who tugged the door."),
}
BLUE = {
    ("early.txt", 1): ("supported", "The speaker describes testing the latch."),
    ("early.txt", 2): ("declined", "A click alone does not indicate a deliberate check."),
    ("early.txt", 3): ("declined", "The speaker explicitly left it alone."),
    ("late.txt", 1): ("supported", "Tested the latch after closure."),
    ("late.txt", 3): ("declined", "Waiting describes no check."),
    ("late.txt", 4): ("deferred", "Need the preceding exchange."),
}


def press(widget, label):
    for child in widget.winfo_children():
        if isinstance(child, ttk.Button) and child.cget("text") == label:
            child.invoke()
            return True
        if press(child, label):
            return True
    return False


class PairedMatchReviewTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.app = ThemeForgeApp()
        self.app.withdraw()
        self.addCleanup(self.app.destroy)
        self.spans = {}
        paths = []
        for source, passages in SHIFTS.items():
            path = self.folder / source
            lines = [f"Finch: {passage}" for passage in passages]
            path.write_text("\n".join(lines), encoding="utf-8")
            paths.append(str(path))
            offset = 0
            for line, passage in enumerate(passages, 1):
                start = offset + len("Finch: ")
                self.spans[source, line] = start, start + len(passage), passage
                offset += len(lines[line - 1]) + 1
        with patch("themeforge.app.filedialog.askopenfilenames", return_value=paths):
            self.app.open_transcript()
        self.app.theme_count.set(1)
        self.app.quotes_per_theme.set(0)
        self.app.analyze()
        self.app.theme_name.set("Checking a closed door")
        self.app.save_theme_edits()
        theme, = self.app.result.themes
        self.assertEqual({(q.source_name, q.source_line): (q.source_start, q.source_end, q.text)
                          for q in theme.quotes}, self.spans)
        seed = self.folder / "unreviewed.tfproj"
        with patch("themeforge.app.filedialog.asksaveasfilename", return_value=str(seed)):
            self.assertTrue(self.app.save_project())
        self.paths = [self.folder / f"{actor}.tfproj" for actor in ("Amber", "Blue")]
        self.provenance = {}
        for path, decisions in zip(self.paths, (AMBER, BLUE), strict=True):
            copyfile(seed, path)
            self.open(path)
            self.app.researcher_name.set(path.stem)
            for (source, line), (disposition, reason) in decisions.items():
                quote = next(q for q in self.app.result.themes[0].quotes if (q.source_name, q.source_line) == (source, line))
                self.app._focus_quote_by_id(quote.quote_id)
                view = self.app.transcript_widgets[source]
                self.assertEqual(view.get("selected_quote.first", "selected_quote.last"), self.spans[source, line][2])
                with patch("themeforge.app.simpledialog.askstring", return_value=reason):
                    self.assertTrue(press(self.app, REVIEW_LABELS[disposition]))
            self.assertTrue(self.app.save_project())
            self.open(path)
            self.assertEqual(self.app.researcher_name.get(), path.stem)
            notes = {}
            for event in self.app.audit_events:
                if event.action == "match_review_note":
                    self.assertEqual(event.actor, path.stem)
                    details = json.loads(event.details)
                    q = details["match"]["quote"]
                    key = q["source_name"], q["source_line"]
                    notes[key] = details["disposition"], details["reason"]
                    self.provenance[path.stem, key] = event.target_id, event.timestamp_utc
            self.assertEqual(notes, decisions)
        self.open(self.paths[0])
        self.open_comparison(self.paths[1])

    def open_comparison(self, path):
        self.app.open_rigor_tools()
        self.dialog = next(w for w in self.app.winfo_children() if isinstance(w, RigorDialog))
        self.choose(path)

    def choose(self, path):
        with patch("themeforge.rigor_dialog.filedialog.askopenfilename", return_value=str(path)):
            self.dialog._choose_comparison()

    def open(self, path):
        with patch("themeforge.app.filedialog.askopenfilename", return_value=str(path)):
            self.app.open_project()

    def export(self, action, filename):
        path = self.folder / filename
        with patch("themeforge.rigor_dialog.filedialog.asksaveasfilename", return_value=str(path)):
            action()
        with path.open(encoding="utf-8", newline="") as stream:
            return list(csv.DictReader(stream))

    def paired_rows(self, filename="reviews.csv"):
        def export():
            self.assertTrue(press(self.dialog, "Export paired match reviews"), "Missing paired review comparison")
        return self.export(export, filename)

    def test_independent_reviews_survive_reopen_and_comparison(self):
        coverage, = self.export(self.dialog._export_reliability, "presence.csv")
        self.assertEqual(coverage["percent_agreement"], "100.00")
        self.assertIn("coverage by transcript unit", coverage["comparison_basis"])
        self.assertEqual(self.export(self.dialog._export_disagreements, "presence-differences.csv"), [])
        rows = self.paired_rows()
        outcomes = ["both_supported", "disagreement", "both_declined", "missing_comparison_review",
                    "missing_current_review", "neither_reviewed", "deferred", "deferred"]
        self.assertEqual(len(rows), len(self.spans))
        self.assertEqual({(r["source_file"], int(r["source_line"])): r["review_comparison"] for r in rows},
                         dict(zip(self.spans, outcomes, strict=True)))
        for row in rows:
            key = row["source_file"], int(row["source_line"])
            self.assertEqual((int(row["source_start"]), int(row["source_end"]), row["quote"]), self.spans[key])
            for role, actor, decisions in (("current", "Amber", AMBER), ("comparison", "Blue", BLUE)):
                self.assertEqual(row[f"{role}_reviewer"], actor)
                self.assertEqual((row[f"{role}_disposition"], row[f"{role}_reason"]),
                                 decisions.get(key, ("", "")))
                if key in decisions:
                    self.assertEqual((row["match_review_target_id"], row[f"{role}_time_utc"]),
                                     self.provenance[actor, key])
                else:
                    self.assertEqual(row[f"{role}_time_utc"], "")
        self.assertEqual(len({r["match_review_target_id"] for r in rows}), 8)

    def test_copied_history_and_a_later_reader_do_not_supply_another_readers_vote(self):
        copied = self.folder / "copied.tfproj"
        copyfile(self.paths[0], copied)
        self.open(copied)
        quote = next(q for q in self.app.result.themes[0].quotes if (q.source_name, q.source_line) == ("early.txt", 2))
        self.app._focus_quote_by_id(quote.quote_id)
        for actor, disposition, reason in (("Blue", "deferred", "Consider the context."),
                                          ("Blue", "declined", "=An audible click alone is insufficient."),
                                          ("Amber", "supported", "A later note by a different reader.")):
            self.app.researcher_name.set(actor)
            with patch("themeforge.app.simpledialog.askstring", return_value=reason):
                self.app.review_current_match(disposition)
        self.app.researcher_name.set("Coordinator")
        self.assertTrue(self.app.save_project())
        self.open(self.paths[0])
        self.open_comparison(copied)
        self.assertEqual(set(self.dialog.comparison_reviewer["values"]), {"Amber", "Blue", "Coordinator"})
        self.assertEqual(self.dialog.comparison_reviewer.get(), "Coordinator")
        self.assertTrue(all(r["comparison_disposition"] == "" for r in self.paired_rows("coordinator.csv")))
        self.dialog.comparison_reviewer.set("Blue")
        rows = self.paired_rows()
        reviewed, = [row for row in rows if row["comparison_disposition"]]
        self.assertEqual((reviewed["review_comparison"], reviewed["comparison_reviewer"], reviewed["comparison_reason"]),
                         ("disagreement", "Blue", "'=An audible click alone is insufficient."))
        self.assertFalse(any(row["review_comparison"] == "both_supported" for row in rows))
        self.dialog.comparison_reviewer.set("Amber")
        with patch("themeforge.rigor_dialog.filedialog.asksaveasfilename", side_effect=AssertionError):
            self.dialog._export_paired_reviews()
        self.assertIn("distinct named reviewers", self.dialog.status.get())

    def test_changed_matches_are_excluded_instead_of_counted_as_missing_reviews(self):
        state = load_project(self.paths[1])
        docs = tuple(replace(d, text=d.text + "\nFinch: One further detail.") if d.name == "early.txt" else d
                     for d in state.documents)
        theme, = state.result.themes
        quotes = [replace(q, source_start=q.source_start + 1) if (q.source_name, q.source_line) == ("late.txt", 4)
                  else q for q in theme.quotes]
        revised = self.folder / "changed.tfproj"
        save_project(revised, replace(state, documents=docs, result=replace(state.result, themes=[replace(theme, quotes=quotes)])))
        self.choose(revised)
        rows = self.paired_rows()
        self.assertEqual([(r["source_file"], r["source_line"]) for r in rows], [("late.txt", str(i)) for i in (1, 2, 3)])
        self.assertEqual({(r["current_quote_rows_excluded"], r["comparison_quote_rows_excluded"]) for r in rows}, {("5", "5")})

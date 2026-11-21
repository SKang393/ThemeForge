"""Resume a partly reviewed, three-interview study from the desktop."""
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

COLD = "I logged the cooler temperature before opening."
INTERVIEWS = {
    "market.txt": ("Aster", [COLD, "My handover sheet listed two crates for the next shift.",
                             "I unlocked the visitor gate for a delivery."]),
    "depot.txt": ("Birch", ["I never measured the cooler temperature on that shift.",
                            "The handover sheet was blank when I arrived.",
                            "I wrote the visitor gate number on the handover sheet."]),
    "stall.txt": ("Cedar", [COLD, "We discussed a handover sheet for next summer.",
                            "I waited at the visitor gate until someone came."]),
}
BOOK = ("code,description\nCold checks,cooler temperature\n"
        "Shift transfer,handover sheet\nSite access,visitor gate\n")
DECISIONS = {
    ("Cold checks", "market.txt", 1): ("supported", "A measurement was logged."),
    ("Cold checks", "depot.txt", 1): ("declined", "The speaker explicitly denies measuring."),
    ("Cold checks", "stall.txt", 1): ("supported", "The morning check is explicit."),
    ("Shift transfer", "market.txt", 2): ("supported", "The outgoing shift left written instructions."),
    ("Shift transfer", "depot.txt", 2): ("deferred", "Check whether the blank sheet was used."),
    ("Site access", "stall.txt", 3): ("deferred", "Waiting alone does not establish entry."),
}
UNREVIEWED = {
    ("Shift transfer", "stall.txt", 2), ("Shift transfer", "depot.txt", 3),
    ("Shift transfer", "depot.txt", 1), ("Site access", "market.txt", 3), ("Site access", "depot.txt", 3),
}


def buttons(widget):
    for child in widget.winfo_children():
        if isinstance(child, ttk.Button):
            yield child.cget("text"), child
        yield from buttons(child)


class ResumeReviewTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.app = ThemeForgeApp()
        self.addCleanup(lambda: self.app.destroy())
        self.spans = {}
        files = []
        for name, (speaker, passages) in INTERVIEWS.items():
            source = self.root / name
            text = ""
            for line, passage in enumerate(passages, 1):
                text += f"{speaker}: "
                start = len(text)
                text += passage
                self.spans[name, line] = (start, len(text), passage)
                text += "\n"
            source.write_text(text, encoding="utf-8")
            files.append(str(source))
        codebook = self.root / "codes.csv"
        codebook.write_text(BOOK, encoding="utf-8")
        with patch("themeforge.app.filedialog.askopenfilenames", return_value=files):
            self.press("Choose files")
        with patch("themeforge.app.filedialog.askopenfilename", return_value=str(codebook)):
            self.press("Choose codebook")
        self.app.researcher_name.set("Elm")
        self.press("Analyze")

    def press(self, label):
        dict(buttons(self.app))[label].invoke()
        self.app.update()

    def current(self):
        match = self.app._current_quote_match()
        self.assertIsNotNone(match)
        q = match.quote
        self.assertEqual(self.app._current_document_name(), q.source_name)
        widget = self.app.transcript_widgets[q.source_name]
        self.assertIsNotNone(widget.bbox("selected_quote.first"), "The selected excerpt must be in view")
        self.assertEqual((q.source_start, q.source_end, widget.get("selected_quote.first", "selected_quote.last")),
                         self.spans[q.source_name, q.source_line])
        return self.app._current_theme().name, q.source_name, q.source_line

    def mark(self, decision, reason):
        with patch("themeforge.app.MatchReviewReason") as prompt:
            prompt.return_value.result = reason
            self.press(REVIEW_LABELS[decision])
        prompt.assert_called_once()

    def reopen_compact(self, path):
        self.app.request_close()
        with patch.object(ThemeForgeApp, "maxsize", return_value=(1024, 705)):
            self.app = ThemeForgeApp()
        with patch("themeforge.app.filedialog.askopenfilename", return_value=str(path)):
            self.press("Open project")
        self.assertEqual((self.app.winfo_width(), self.app.winfo_height()), (1024, 705))
        controls = dict(buttons(self.app))
        for label in (*REVIEW_LABELS.values(), "Next unreviewed", "Next deferred", "Apply definition to this quote…"):
            button = controls[label]
            self.assertTrue(button.winfo_viewable(), label)
            x = button.winfo_rootx() - self.app.winfo_rootx()
            y = button.winfo_rooty() - self.app.winfo_rooty()
            self.assertGreaterEqual(min(x, y), 0, label)
            self.assertLessEqual(x + button.winfo_width(), self.app.winfo_width(), label)
            self.assertLessEqual(y + button.winfo_height(), self.app.winfo_height(), label)

    def test_two_coders_reopen_independent_copies_at_compact_size(self):
        seed = self.root / "unreviewed.tfproj"
        with patch("themeforge.app.filedialog.asksaveasfilename", return_value=str(seed)):
            self.press("Save project")
        supported = ("Shift transfer", "market.txt", 2)
        all_matches = DECISIONS.keys() | UNREVIEWED
        # Identical cooler accounts in separate interviews require distinct decisions.
        sessions = (("Elm", "market.txt", "Check the market opening routine."),
                    ("Fern", "stall.txt", "Check the stall opening routine."))
        for actor, source, reason in sessions:
            path = self.root / f"{actor}.tfproj"
            copyfile(seed, path)
            self.reopen_compact(path)
            self.app.researcher_name.set(actor)
            if source == "stall.txt":
                self.press("Next quote")
            self.assertEqual(self.current(), ("Cold checks", source, 1))
            self.mark("deferred", reason)
            for _ in all_matches:
                self.press("Next unreviewed")
                if self.current() == supported:
                    break
            self.assertEqual(self.current(), supported)
            self.mark("supported", "The outgoing shift left written instructions.")
            self.press("Save project")

        for actor, source, reason in sessions:
            with self.subTest(coder=actor):
                self.reopen_compact(self.root / f"{actor}.tfproj")
                self.assertEqual(self.app.researcher_name.get(), actor)
                audit = list(self.app.audit_events)
                self.assertEqual([e.actor for e in audit if e.action == "match_review_note"], [actor, actor])
                self.press("Next deferred")
                deferred = ("Cold checks", source, 1)
                self.assertEqual(self.current(), deferred)
                self.assertIn(reason, self.app.match_review_text.get())
                expected = all_matches - {deferred, supported}
                reached = []
                for _ in expected:
                    self.press("Next unreviewed")
                    reached.append(self.current())
                    self.assertIn("No current match review", self.app.match_review_text.get())
                self.assertCountEqual(reached, expected)
                self.assertEqual(self.app.audit_events, audit)

    def test_next_buttons_follow_one_source_and_the_named_reviewer(self):
        app = self.app
        decisions = [
            ("Elm", "Cold checks", "market.txt", 1, "supported", "The reading was entered."),
            ("Elm", "Shift transfer", "market.txt", 2, "deferred", "Check the outgoing sheet."),
            ("Fern", "Cold checks", "market.txt", 1, "supported", "An opening reading was logged."),
            ("Fern", "Shift transfer", "market.txt", 2, "declined", "Receipt is unclear."),
            ("Fern", "Site access", "market.txt", 3, "supported", "The gate was unlocked."),
            ("Fern", "Cold checks", "stall.txt", 1, "deferred", "Check the stall record."),
        ]
        for actor, code, source, line, decision, reason in decisions:
            app.researcher_name.set(actor)
            theme, = [t for t in app.result.themes if t.name == code]
            quote, = [q for q in theme.quotes if (q.source_name, q.source_line) == (source, line)]
            app._select_theme_by_id(theme.id)
            app._focus_quote_by_id(quote.quote_id)
            self.mark(decision, reason)
        app.researcher_name.set("Elm")
        project = self.root / "shifts.tfproj"
        with patch("themeforge.app.filedialog.asksaveasfilename", return_value=str(project)):
            self.press("Save project")
        original = project.read_bytes()
        self.reopen_compact(project)
        saved = self.app._project_state()
        scope = self.app.review_source_box
        self.assertEqual(scope.get(), "All sources")
        scope.current(scope["values"].index("market.txt"))
        self.press("Next unreviewed")
        self.assertEqual(self.current(), ("Site access", "market.txt", 3))
        self.assertIn("No current match review for this coder", self.app.match_review_text.get())
        self.press("Next unreviewed")  # Wrap within the file even when another file has pending work.
        self.assertEqual(self.current(), ("Site access", "market.txt", 3))
        self.press("Next deferred")
        self.assertEqual(self.current(), ("Shift transfer", "market.txt", 2))
        self.press("Next supported")
        self.assertEqual(self.current(), ("Cold checks", "market.txt", 1))

        self.app.researcher_name.set("Fern")
        self.press("Next supported")
        self.assertEqual(self.current(), ("Site access", "market.txt", 3))
        self.assertIn("The gate was unlocked.", self.app.match_review_text.get())
        self.press("Next unreviewed")
        self.assertEqual(self.current(), ("Site access", "market.txt", 3))
        self.assertIn("No unreviewed scored matches with verified spans for Fern in market.txt", self.app.status_text.get())
        self.press("Next deferred")
        self.assertEqual(self.current(), ("Site access", "market.txt", 3))
        self.assertIn("No verified deferred matches for Fern in market.txt", self.app.status_text.get())
        scope.current(0)
        self.press("Next deferred")
        self.assertEqual(self.current(), ("Cold checks", "stall.txt", 1))
        self.app.researcher_name.set("Elm")
        self.assertEqual(self.app._project_state(), saved)
        self.assertEqual(project.read_bytes(), original)

    def test_resume_across_codes_and_revisit_own_deferrals(self):
        suggested = self.app.result
        seen = set()
        for index, theme in enumerate(suggested.themes):
            self.app.theme_list.selection_clear(0, "end")
            self.app.theme_list.selection_set(index)
            self.app.theme_list.event_generate("<<ListboxSelect>>")
            self.app.update()
            for _ in theme.quotes:
                key = self.current()
                seen.add(key)
                if key in DECISIONS:
                    self.mark(*DECISIONS[key])
                if key in {("Shift transfer", "depot.txt", 2), ("Shift transfer", "stall.txt", 2)}:
                    self.app.researcher_name.set("Fern")
                    self.mark("declined", "This account does not establish a completed handover.")
                    self.app.researcher_name.set("Elm")
                self.press("Next quote")
        self.assertEqual(seen, DECISIONS.keys() | UNREVIEWED)
        study = self.root / "market-study.tfproj"
        with patch("themeforge.app.filedialog.asksaveasfilename", return_value=str(study)):
            self.press("Save project")
        self.app.request_close()
        self.app = ThemeForgeApp()
        with patch("themeforge.app.filedialog.askopenfilename", return_value=str(study)):
            self.press("Open project")

        self.assertEqual(self.app.researcher_name.get(), "Elm")
        self.assertEqual(self.current(), ("Cold checks", "market.txt", 1))
        saved_events = list(self.app.audit_events)
        unresolved = []
        for _ in UNREVIEWED:
            self.press("Next unreviewed")
            unresolved.append(self.current())
            self.assertIn("No current match review", self.app.match_review_text.get())
        self.assertEqual(unresolved[0], ("Shift transfer", "stall.txt", 2))
        self.assertCountEqual(unresolved, UNREVIEWED)
        deferred = set()
        for _ in range(2):
            self.press("Next deferred")
            key = self.current()
            deferred.add(key)
            self.assertIn(DECISIONS[key][1], self.app.match_review_text.get())
        self.assertEqual(deferred, {key for key, (state, _) in DECISIONS.items() if state == "deferred"})
        self.assertEqual(self.app.audit_events, saved_events)
        self.assertEqual(self.app.result, suggested)

        # Resolve one deferral, then return directly to the other one.
        resolved = self.current()
        self.mark("declined", "The preceding exchange describes a plan only.")
        self.press("Next deferred")
        self.assertEqual({resolved, self.current()}, deferred)
        self.app.researcher_name.set("Fern")
        self.assertNotIn("deferred by Elm", self.app.match_review_text.get())
        self.press("Next deferred")
        self.assertIn("No verified deferred matches for Fern", self.app.status_text.get())
        fern_unseen = []
        for _ in range(9):
            self.press("Next unreviewed")
            fern_unseen.append(self.current())
        self.assertCountEqual(fern_unseen, seen - {("Shift transfer", "depot.txt", 2), ("Shift transfer", "stall.txt", 2)})

        self.app.researcher_name.set("Elm")
        self.press("Save project")
        with patch("themeforge.app.filedialog.askopenfilename", return_value=str(study)):
            self.press("Open project")
        self.press("Next deferred")
        self.assertEqual(self.current(), (deferred - {resolved}).pop())
        report = self.root / "review.json"
        with patch("themeforge.app.filedialog.asksaveasfilename", return_value=str(report)):
            self.press("Export report")
        exported = json.loads(report.read_text(encoding="utf-8"))
        rows = {(t["name"], q["source_name"], q["source_line"]): q for t in exported["themes"] for q in t["quotes"]}
        self.assertEqual(set(rows), seen)
        for (_, source, line), q in rows.items():
            self.assertEqual((q["source_start"], q["source_end"], q["text"]), self.spans[source, line])
        # The regular report still attributes the latest note across readers.
        note = rows["Shift transfer", "stall.txt", 2]["latest_match_review"]
        self.assertEqual((note["disposition"], note["actor"]), ("declined", "Fern"))
        self.assertEqual(rows[resolved]["latest_match_review"]["disposition"], "declined")

    def test_resume_skips_unscored_and_unverified_rows_and_preserves_pending_edits(self):
        state = self.app._project_state()
        theme = state.result.themes[0]
        a, b, c = theme.quotes
        # One scored, exact match, one conflicting span and one unscored assignment.
        theme = replace(theme, quotes=[a, replace(b, source_start=b.source_start + 1), replace(c, relevance=None)])
        self.app._restore_project(replace(state, result=replace(state.result, themes=[theme])), None)
        self.app.quote_memo_text.insert("1.0", "Draft interpretation.")
        self.press("Next unreviewed")
        self.assertIn("unfinished", self.app.status_text.get())
        self.assertEqual(self.app.quote_memo_text.get("1.0", "end-1c"), "Draft interpretation.")
        self.app.quote_memo_text.delete("1.0", "end")
        self.press("Next unreviewed")
        self.assertEqual(self.current(), (theme.name, a.source_name, a.source_line))
        self.mark("supported", "The log records a completed check.")
        self.press("Next unreviewed")
        self.assertIn("No unreviewed scored matches", self.app.status_text.get())

import csv
from dataclasses import replace
from hashlib import sha256
import io
import json
from pathlib import Path
import tempfile
from tkinter import ttk
import unittest
from unittest.mock import Mock, patch

from themeforge.app import ThemeForgeApp
from themeforge.definition_review import DefinitionReview
from themeforge.exports import export_quotes_csv
from themeforge.rigor_dialog import RigorDialog


# Fixture rule: a speaker reports a completed transfer. A denial, a plan and polishing
# the tag are counterexamples. Only one of the two identical positive accounts is reviewed.
DONE = "I handed the brass tag to the incoming keeper."
DENIAL = "I never handed the brass tag to the incoming keeper."
PLAN = "I planned to hand the brass tag to the incoming keeper."
OTHER = "I polished the brass tag before the shift."
SOURCES = {"east.txt": f"A: {DONE}\nB: {DENIAL}\nC: {PLAN}", "west.txt": f"D: {DONE}\nE: {OTHER}"}
EXPECTED_SPANS = {
    ("east.txt", 1): (3, 49, DONE), ("east.txt", 2): (53, 105, DENIAL), ("east.txt", 3): (109, 164, PLAN),
    ("west.txt", 1): (3, 49, DONE), ("west.txt", 2): (53, 95, OTHER),
}


def press(widget, label):
    children = list(widget.winfo_children())
    while children:
        child = children.pop()
        if isinstance(child, ttk.Button) and child.cget("text") == label:
            return child.invoke()
        children.extend(child.winfo_children())
    raise AssertionError(f"No button labelled {label}")


class SupportedMatchTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.app = ThemeForgeApp()
        self.app.withdraw()
        self.addCleanup(self.app.destroy)
        paths = []
        for name, text in SOURCES.items():
            path = self.root / name
            path.write_text(text, encoding="utf-8")
            paths.append(str(path))
        with patch("themeforge.app.filedialog.askopenfilenames", return_value=paths):
            self.app.open_transcript()
        self.app.theme_count.set(1)
        self.app.researcher_name.set("Fixture reviewer")

    def discover(self, max_quotes=0):
        self.app.quotes_per_theme.set(max_quotes)
        self.app.analyze()
        self.app.theme_name.set("Completed shift handover")
        self.app.save_theme_edits()
        self.theme, = self.app.result.themes

    def show(self, source, line):
        self.app._select_theme_by_id(self.theme.id)
        for _ in self.theme.quotes:
            q = self.app._current_quote_match().quote
            if (q.source_name, q.source_line) == (source, line):
                widget = self.app.transcript_widgets[source]
                self.assertEqual(widget.get("selected_quote.first", "selected_quote.last"), q.text)
                return q
            self.app.next_quote()
        self.fail(f"Missing candidate at {source} line {line}")

    def review(self, button, reason):
        with patch("themeforge.app.MatchReviewReason") as prompt:
            prompt.return_value.result = reason
            press(self.app, button)
        return prompt

    def export_csv(self, dialog, button, filename):
        path = self.root / filename
        with patch("themeforge.rigor_dialog.filedialog.asksaveasfilename", return_value=str(path)):
            press(dialog, button)
        with path.open(encoding="utf-8", newline="") as stream:
            return list(csv.DictReader(stream))

    def test_discover_review_assign_reopen_and_export(self):
        app = self.app
        self.discover()
        self.assertEqual({(q.source_name, q.source_line): (q.source_start, q.source_end, q.text)
                          for q in self.theme.quotes}, EXPECTED_SPANS)
        self.show("east.txt", 2)
        self.review("Not this match", "The speaker denies the transfer.")
        self.show("east.txt", 3)
        self.review("Revisit later", "Only a plan is described. Check for a later account.")
        target = self.show("west.txt", 1)
        reason = "The keeper received the tag in this account."
        prompt = self.review("Supports this theme", reason)
        self.assertIn("west.txt, line 1, characters [3, 49)", prompt.call_args.args[2])
        self.assertEqual(DONE, prompt.call_args.args[3])
        self.assertEqual(app.result.themes, [self.theme])
        self.assertIn("supported by Fixture reviewer", app.match_review_text.get())

        # Apply a codebook definition separately to the reviewed passage using the actual modal.
        book = self.root / "codebook.csv"
        book.write_text("code,description\nCompleted transfer,The speaker reports handing the tag to another person.\n",
                        encoding="utf-8")
        with patch("themeforge.app.filedialog.askopenfilename", return_value=str(book)):
            app.open_codebook()
        def apply_definition():
            dialog = next(w for w in app.winfo_children() if isinstance(w, DefinitionReview))
            dialog.choice.current(0)
            dialog.show_choice()
            dialog.apply_button.invoke()
        app.after_idle(apply_definition)
        press(app, "Apply definition to this quote…")
        assigned = app._current_quote_match().quote
        self.assertNotEqual(assigned.quote_id, target.quote_id)
        self.assertEqual((assigned.source_name, assigned.source_line, assigned.source_start, assigned.source_end,
                          assigned.text, assigned.relevance), ("west.txt", 1, 3, 49, DONE, None))
        project = self.root / "review.tfproj"
        with patch("themeforge.app.filedialog.asksaveasfilename", return_value=str(project)):
            self.assertTrue(app.save_project())
        with patch("themeforge.app.filedialog.askopenfilename", return_value=str(project)):
            app.open_project()
        self.show("east.txt", 1)
        self.assertIn("No current match review", app.match_review_text.get())
        self.assertEqual(self.show("west.txt", 1), target)
        self.assertIn(reason, app.match_review_text.get())
        app.open_rigor_tools()
        rigor = next(w for w in app.winfo_children() if isinstance(w, RigorDialog))
        press(rigor, "Select all sources")
        row, = self.export_csv(rigor, "Export supported matches CSV", "supported.csv")
        self.assertEqual((row["theme_name"], row["quote_id"], row["source_file"], row["source_line"],
                          row["source_start"], row["source_end"], row["quote"], row["source_location_status"]),
                         (self.theme.name, target.quote_id, "west.txt", "1", "3", "49", DONE, "exact_span"))
        self.assertEqual((row["match_review_disposition"], row["match_review_reason"], row["match_review_actor"],
                          row["relevance"]), ("supported", reason, "Fixture reviewer", f"{target.relevance:.3f}"))
        audit = self.export_csv(rigor, "Export audit CSV", "audit.csv")
        reviews = [r for r in audit if r["action"] == "match_review_note"]
        self.assertEqual([json.loads(r["details"])["disposition"] for r in reviews],
                         ["declined", "deferred", "supported"])
        event = reviews[-1]
        self.assertEqual(row["match_review_target_id"], event["target_id"])
        self.assertEqual((event["actor"], event["timestamp_utc"]),
                         (row["match_review_actor"], row["match_review_time_utc"]))
        snapshot = json.loads(event["details"])["match"]
        self.assertEqual(snapshot["source_sha256"], sha256(SOURCES["west.txt"].encode()).hexdigest())
        self.assertEqual(snapshot["theme_id"], self.theme.id)
        self.assertEqual(snapshot["quote"]["relevance"], target.relevance)
        self.assertEqual(snapshot["quote"]["rationale"], target.rationale)
        link, = [r for r in audit if r["action"] == "assignment_source_match"]
        self.assertEqual((link["target_id"], json.loads(link["details"])["match"]), (assigned.quote_id, snapshot))

        output = self.root / "all.json"
        with patch("themeforge.app.filedialog.asksaveasfilename", return_value=str(output)):
            app.save_report()
        groups = json.loads(output.read_text(encoding="utf-8"))["themes"]
        rows = {(r["source_name"], r["source_line"]): r["latest_match_review"] for r in groups[0]["quotes"]}
        self.assertEqual({k: v and v["disposition"] for k, v in rows.items()},
                         {("east.txt", 1): None, ("east.txt", 2): "declined", ("east.txt", 3): "deferred",
                          ("west.txt", 1): "supported", ("west.txt", 2): None})
        self.assertIsNone(groups[1]["quotes"][0]["latest_match_review"])

        # Even the same text at the same offsets in a revised source is a different review context.
        revised = [replace(d, text=d.text + "\nF: The account was revised.") for d in app.documents]
        filtered = export_quotes_csv(app.result, revised, app.audit_events, supported_only=True)
        self.assertEqual(list(csv.DictReader(io.StringIO(filtered))), [])

        self.review("Not this match", "Withdrawing support after checking the context.")
        self.assertEqual(self.export_csv(rigor, "Export supported matches CSV", "latest.csv"), [])
        self.review("Supports this theme", reason)
        with patch("themeforge.app.messagebox.askokcancel", return_value=True):
            app.analyze()
        self.assertEqual(self.export_csv(rigor, "Export supported matches CSV", "new-run.csv"), [])
        history = self.export_csv(rigor, "Export audit CSV", "history.csv")
        self.assertIn(event, history)

    def test_review_requires_the_same_visible_match_and_reviewer_throughout(self):
        app = self.app
        self.discover()
        self.show("west.txt", 1)
        baseline = list(app.audit_events)
        app.transcript_notebook.select(app.transcript_frames["east.txt"])
        self.review("Supports this theme", "Wrong document").assert_not_called()
        self.show("west.txt", 1)
        self.review("Supports this theme", None)
        app.researcher_name.set(" ")
        self.review("Supports this theme", "No named reviewer").assert_not_called()
        app.researcher_name.set("Fixture reviewer")
        def change_tab(*args, **kwargs):
            app.transcript_notebook.select(app.transcript_frames["east.txt"])
            return Mock(result="The visible passage changed.")
        with patch("themeforge.app.MatchReviewReason", side_effect=change_tab):
            press(app, "Not this match")
        self.assertEqual(app.audit_events, baseline)

    def test_reviewing_a_similar_passage_preview_cannot_mark_the_seed_quote(self):
        app = self.app
        self.discover(max_quotes=1)
        app.find_more_from_quote()
        seed = app._current_quote_match().quote
        index = next(i for i, p in enumerate(app.similar_passages)
                     if p.source_name == seed.source_name and p.text != seed.text)
        app.similar_list.selection_clear(0, "end")
        app.similar_list.selection_set(index)
        app.show_selected_similar_passage()
        self.assertEqual(app._current_document_name(), seed.source_name)
        before = list(app.audit_events)
        self.review("Not this match", "Wrong target").assert_not_called()
        self.assertEqual(app.audit_events, before)
        app.next_quote()
        self.assertIsNone(app._selected_similar_passage())
        self.assertFalse(app.transcript_widgets[seed.source_name].tag_ranges("similar_suggestion"))
        self.review("Supports this theme", "The seed describes a completed transfer.").assert_called_once()

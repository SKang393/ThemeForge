import csv
import json
from pathlib import Path
import tempfile
from tkinter import ttk
import unittest
from unittest.mock import patch

from themeforge.analysis_types import AnalysisResult, AnalysisSettings, Theme, ThemeQuote, TranscriptDocument
from themeforge.app import ThemeForgeApp
from themeforge.manual_editing import ManualSelection
from themeforge.project_io import ProjectState, load_project, save_project


class HiddenApp(ThemeForgeApp):
    def _build_layout(self):
        self.withdraw()
        super()._build_layout()


class FirstRunLayoutTests(unittest.TestCase):
    def test_launch_fits_available_area_and_exposes_first_code_and_review(self):
        # Supply a laptop-sized client area without resizing the window after launch.
        with patch.object(ThemeForgeApp, "maxsize", return_value=(1250, 705)):
            app = ThemeForgeApp()
        self.addCleanup(app.destroy)
        app.update()
        self.assertLessEqual(app.winfo_width(), 1250)
        self.assertLessEqual(app.winfo_height(), 705)

        def descendants(widget):
            for child in widget.winfo_children():
                yield child
                yield from descendants(child)

        notebook = app.definition_box.master.master
        self.assertEqual(notebook.index("current"), 0)
        self.assertEqual(notebook.tab(0, "text"), "Code passages")
        controls = {w.cget("text"): w for w in descendants(app) if isinstance(w, ttk.Button)}
        for label in ("Choose files", "New code from selection", "Apply codebook entry…",
                      "Supports this theme", "Save project", "Export report"):
            widget = controls[label]
            self.assertTrue(widget.winfo_ismapped(), label)
            parent = widget.master
            while parent is not None:
                x = widget.winfo_rootx() - parent.winfo_rootx()
                y = widget.winfo_rooty() - parent.winfo_rooty()
                self.assertGreaterEqual(min(x, y), 0, label)
                self.assertLessEqual(x + widget.winfo_width(), parent.winfo_width(), label)
                self.assertLessEqual(y + widget.winfo_height(), parent.winfo_height(), label)
                parent = parent.master

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, project, report = root / "studio.txt", root / "first.tfproj", root / "codes.csv"
            passage = "We moved the display."
            text = f"Pat: {passage}\nSam: {passage}"
            source.write_text(text, encoding="utf-8")
            with patch("themeforge.app.filedialog.askopenfilenames", return_value=(str(source),)):
                controls["Choose files"].invoke()
            app.researcher_name.set("Studio reviewer")
            app.theme_name.set("Display moved")
            widget = app.transcript_widgets[source.name]
            widget.tag_add("sel", "2.5", "2.end")
            controls["New code from selection"].invoke()
            with patch("themeforge.app.filedialog.asksaveasfilename", return_value=str(project)):
                controls["Save project"].invoke()
            self.assertTrue(project.is_file())
            app.withdraw()
            # Reopen in a new window with no in-memory assignments.
            fresh = ThemeForgeApp()
            self.addCleanup(fresh.destroy)
            with patch("themeforge.app.filedialog.askopenfilename", return_value=str(project)):
                fresh.open_project()
            with patch("themeforge.app.filedialog.asksaveasfilename", return_value=str(report)):
                fresh.save_report()
            with report.open(encoding="utf-8", newline="") as stream:
                row, = csv.DictReader(stream)
            self.assertEqual((row["theme_name"], row["source_file"], row["source_line"], row["quote"]),
                             ("Display moved", source.name, "2", passage))
            self.assertEqual((int(row["source_start"]), int(row["source_end"])),
                             (text.rindex(passage), len(text)))
            self.assertEqual((row["source_location_status"], row["relevance"]), ("exact_span", ""))
            self.assertIn("Studio reviewer", row["selection_rationale"])


class ImportedSelectionTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.app = HiddenApp()
        self.addCleanup(self.app.destroy)
        self.passage = "Check the blue latch, twice."
        self.text = f"Em: {self.passage}\nIra: 🧰 {self.passage} Leave the brass latch open."
        self.source = self.folder / "synthetic.txt"
        self.source.write_text(self.text, encoding="utf-8")
        with patch("themeforge.app.filedialog.askopenfilenames", return_value=[str(self.source)]):
            self.app.open_transcript()
        self.widget = self.app.transcript_widgets[self.source.name]

    def select_second_occurrence(self):
        start = self.widget.search(self.passage, "2.0", "2.end")
        self.widget.tag_add("sel", start, f"{start}+{len(self.passage)}c")

    def test_first_freehand_code_exports_the_selected_occurrence(self):
        self.app.researcher_name.set("Tester")
        self.app.theme_name.set("Double check")
        self.select_second_occurrence()
        with patch("themeforge.app.analyze_documents", side_effect=AssertionError):
            self.app.code_selection_to_new_theme()
        self.assertIsNotNone(self.app.result)
        theme, = self.app.result.themes
        quote, = theme.quotes
        start = self.text.rindex(self.passage)
        end = start + len(self.passage)
        self.assertEqual((theme.name, theme.score), ("Double check", None))
        self.assertEqual((quote.text, quote.source_line, quote.source_start, quote.source_end),
                         (self.passage, 2, start, end))
        self.assertEqual(self.widget.get("selected_quote.first", "selected_quote.last"), self.passage)
        self.assertEqual(self.app.result.quote_count, 0)
        event, = self.app.audit_events
        self.assertEqual((event.actor, event.action, event.target_id),
                         ("Tester", "selection_assignment_recorded", quote.quote_id))
        details = json.loads(event.details)
        self.assertEqual((details["text"], details["start"], details["end"]), (self.passage, start, end))
        for suffix in (".json", ".csv", ".md"):
            path = self.folder / ("report" + suffix)
            with patch("themeforge.app.filedialog.asksaveasfilename", return_value=str(path)):
                self.app.save_report()
        data = json.loads((self.folder / "report.json").read_text(encoding="utf-8"))
        row, = data["themes"][0]["quotes"]
        self.assertEqual((row["text"], row["source_name"], row["source_line"]), (self.passage, self.source.name, 2))
        self.assertEqual((row["source_start"], row["source_end"], row["relevance"], row["source_location_status"]),
                         (start, end, None, "exact_span"))
        with (self.folder / "report.csv").open(encoding="utf-8", newline="") as stream:
            row, = csv.DictReader(stream)
        self.assertEqual((row["quote"], row["source_file"], row["source_line"]), (self.passage, self.source.name, "2"))
        self.assertEqual((int(row["source_start"]), int(row["source_end"]), row["relevance"], row["source_location_status"]),
                         (start, end, "", "exact_span"))
        markdown = (self.folder / "report.md").read_text(encoding="utf-8")
        for value in (f"> {self.passage}\n", "No automated analysis has been run", "Relevance: Not scored"):
            self.assertIn(value, markdown)
        self.app.undo_manual_edit()
        self.assertEqual(self.app.result.themes, [])
        self.assertIsNone(self.app._current_quote_match())
        self.assertFalse(self.widget.tag_ranges("selected_quote"))
        self.app.redo_manual_edit()
        self.assertEqual(self.app._current_quote_match().quote, quote)

    def test_missing_selection_or_coder_leaves_import_unmodified(self):
        self.app.code_selection_to_new_theme()
        self.assertIn("Select transcript text", self.app.status_text.get())
        self.select_second_occurrence()
        self.app.code_selection_to_new_theme()
        self.assertIn("named coder", self.app.status_text.get())
        self.assertIsNone(self.app.result)
        self.assertFalse(self.app.audit_events)
        self.assertFalse(self.app.edit_history.undo_stack)


class SelectionDecisionTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.app = HiddenApp()
        self.addCleanup(self.app.destroy)
        seed = "We check the bell before opening the studio."
        self.doc = TranscriptDocument("Workshop", "Lee: " + seed +
            "\nRen: We check the bell again before closing the studio.\nJo: Paint the wooden sign.")
        self.scored = ThemeQuote("Q1", "Lee", seed, 1.0, 1, self.doc.name, "Original lexical match.", 5, 5 + len(seed))
        group = Theme("T01", "Bell checks", "#234567", ["bell"], 1, 1.0, [self.scored])
        self.state = ProjectState((), None, (self.doc,), AnalysisSettings(),
                                  AnalysisResult("test", 1, 1, [group], []), "Coder B")
        self.original = self.folder / "original.tfproj"
        save_project(self.original, self.state)
        self.app._restore_project(load_project(self.original), self.original)

    def select_sign(self):
        widget = self.app.transcript_widgets[self.doc.name]
        widget.tag_add("sel", "3.4", "3.end")

    def test_freehand_and_accepted_retrieval_remain_distinct_unscored_decisions(self):
        original = self.original.read_bytes()
        self.select_sign()
        self.app.theme_name.set("Studio signs")
        self.app.code_selection_to_new_theme()
        manual_group = self.app._current_theme()
        direct, = manual_group.quotes
        self.assertEqual((direct.text, direct.relevance, manual_group.score),
                         ("Paint the wooden sign.", None, None))
        self.assertEqual(manual_group.validation["score_scope"], "Not scored")
        self.app._select_theme_by_id("T01")
        self.app.find_more_from_quote()
        suggestion = self.app._selected_similar_passage()
        self.assertIsNotNone(suggestion)
        self.assertEqual(suggestion.source_line, 2)
        self.assertGreater(suggestion.similarity, 0)
        self.assertEqual(len(self.app.audit_events), 1)  # Retrieval did not assign it.
        self.app.code_selected_similar_passage()
        self.assertEqual(len(self.app.audit_events), 2)
        accepted = self.app._current_quote_match().quote
        self.assertEqual((accepted.text, accepted.speaker, accepted.reported_relevance), (suggestion.text, "Ren", None))
        direct_event, applied_event = self.app.audit_events
        query = json.loads(applied_event.details)["retrieval_query"]
        self.assertEqual((query["theme_id"], query["quote_id"], query["text"]), ("T01", "Q1", self.scored.text))
        self.assertIn("TF-IDF", query["method_note"])
        self.assertIsNone(json.loads(direct_event.details)["retrieval_query"])
        self.assertEqual([(e.actor, e.target_id) for e in self.app.audit_events],
                         [("Coder B", direct.quote_id), ("Coder B", accepted.quote_id)])
        for q, event, method, retrieval in ((direct, direct_event, "transcript selection", None),
                (accepted, applied_event, "applied similarity suggestion", suggestion.similarity)):
            details = json.loads(event.details)
            self.assertEqual((details["method"], details["retrieval_similarity"], details["assignment_score"]),
                             (method, retrieval, None))
            self.assertEqual(self.doc.text[details["start"]:details["end"]], q.text)
            self.assertIn("Coder B", q.rationale)
        self.assertEqual(self.original.read_bytes(), original)
        copy = self.folder / "decisions.tfproj"
        with patch("themeforge.app.filedialog.asksaveasfilename", return_value=str(copy)):
            self.assertTrue(self.app.save_project())
        with patch("themeforge.app.filedialog.askopenfilename", return_value=str(copy)):
            self.app.open_project()
        self.app.analyze()
        state = load_project(copy)
        self.assertEqual(tuple(state.audit_events[:2]), (direct_event, applied_event))
        for q in (direct, accepted, self.scored):
            self.assertIn(q, [row for group in state.result.themes for row in group.quotes])
        self.assertIsNone(next(t for t in state.result.themes if t.id == "T01").reported_score)
        for suffix in (".csv", ".json", ".md"):
            path = self.folder / ("report" + suffix)
            with patch("themeforge.app.filedialog.asksaveasfilename", return_value=str(path)):
                self.app.save_report()
        with (self.folder / "report.csv").open(encoding="utf-8", newline="") as stream:
            rows = {r["quote_id"]: r for r in csv.DictReader(stream)}
        self.assertEqual([rows[q.quote_id]["relevance"] for q in (direct, accepted, self.scored)], ["", "", "1.000"])
        report = json.loads((self.folder / "report.json").read_text(encoding="utf-8"))
        data = {row["quote_id"]: row for t in report["themes"] for row in t["quotes"]}
        self.assertEqual([data[q.quote_id]["relevance"] for q in (direct, accepted, self.scored)], [None, None, 1])
        markdown = (self.folder / "report.md").read_text(encoding="utf-8")
        self.assertIn("Selected directly from the transcript", markdown)
        self.assertIn("retrieval similarity", markdown)
        self.assertIn("Relevance: Not scored", markdown)
        self.assertNotIn("These themes and quote matches are machine-generated suggestions.", markdown)
        self.assertEqual(self.original.read_bytes(), original)

    def test_no_actor_or_mismatched_source_refuses_without_audit_or_file_write(self):
        original = self.original.read_bytes()
        self.app.researcher_name.set("")
        self.select_sign()
        self.app.code_selection_to_theme()
        self.assertIn("named coder", self.app.status_text.get())
        self.app.researcher_name.set("Coder B")
        self.app._apply_selection_coding(ManualSelection(self.doc.name, "Different text.", 1, 5, 20), "T01", "")
        self.assertIn("exact passage", self.app.status_text.get())
        self.assertEqual(self.app.result, self.state.result)
        self.assertFalse(self.app.audit_events)
        self.assertEqual(self.original.read_bytes(), original)

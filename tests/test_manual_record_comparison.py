"""Compare positive records from two separately saved fictional studies, not inferred negatives."""
import csv
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from themeforge.analysis_types import AnalysisSettings, CodebookEntry, TranscriptDocument
from themeforge.app import ThemeForgeApp
from themeforge.project_io import ProjectState, load_project, save_project
from themeforge.rigor_dialog import RigorDialog


class HiddenApp(ThemeForgeApp):
    def _build_layout(self):
        self.withdraw()
        super()._build_layout()


class ManualRecordComparisonTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.app = HiddenApp()
        self.addCleanup(self.app.destroy)
        self.doc = TranscriptDocument("studio.txt", "Jo: Wait.\nLee: Wait.\nJo: Or leave early.")
        untouched = TranscriptDocument("backstage.txt", "Ren: We wait for the rehearsal bell outside.")
        self.defs = (CodebookEntry("Signal", "Pause.", ("Wait.",)), CodebookEntry("Signal", "Pause.", ("Bell.",)))
        template = ProjectState((), None, (self.doc, untouched), AnalysisSettings(codebook_entries=self.defs))
        self.paths = []
        for index, actor in enumerate(("Amber", "Blue")):
            self.app._restore_project(template, None)
            self.app.researcher_name.set(actor)
            self.assign(0, "1.4", "1.end")
            self.assign(index, "2.5", "3.end")  # Same label and span; only the definition examples differ.
            if index:
                self.assign(0, "1.4", "1.end-1c")  # Same definition, different exact boundary.
                self.app.analyze()  # Lexical rows on this side are not manual decisions.
            self.app.researcher_name.set("Session label")  # Not the assignment author.
            path = self.folder / f"study{index}.tfproj"
            with patch("themeforge.app.filedialog.asksaveasfilename", return_value=str(path)):
                self.assertTrue(self.app.save_project())
            self.paths.append(path)
        with patch("themeforge.app.filedialog.askopenfilename", return_value=str(self.paths[0])):
            self.app.open_project()
        self.app.open_rigor_tools()
        self.dialog = next(w for w in self.app.winfo_children() if isinstance(w, RigorDialog))
        self.dialog.withdraw()

    def assign(self, index, start, end):
        widget = self.app.transcript_widgets[self.doc.name]
        widget.tag_remove("sel", "1.0", "end")
        widget.tag_add("sel", start, end)
        self.app.definition_box.current(index)
        with patch("themeforge.app.messagebox.askyesno", return_value=True):
            self.app.code_selection_to_definition()

    def choose(self, path):
        with patch("themeforge.rigor_dialog.filedialog.askopenfilename", return_value=str(path)):
            self.dialog._choose_comparison()

    def test_saved_projects_export_only_exact_positive_assignments(self):
        original = [p.read_bytes() for p in self.paths]
        self.choose(self.paths[1])
        self.assertIsNone(self.dialog.report)  # No agreement statistics.
        destination = self.folder / "recorded.csv"
        with patch("themeforge.rigor_dialog.filedialog.asksaveasfilename", return_value=str(destination)):
            self.dialog._export_manual_records()
        with destination.open(encoding="utf-8", newline="") as source:
            rows = list(csv.DictReader(source))
        self.assertEqual({r["recorded_by"] for r in rows}, {"both", "current only", "comparison only"})
        self.assertEqual(len(rows), 4)
        for row in rows:
            self.assertEqual((row["current_coder"], row["comparison_coder"]), ("Amber", "Blue"))
            start, end = int(row["source_start"]), int(row["source_end"])
            self.assertEqual(row["passage"], self.doc.text[start:end])
            self.assertIn("not a negative decision", row["scope"])
            self.assertEqual((row["matching_documents"], row["other_current_quote_rows_excluded"]), ("2", "0"))
            self.assertGreater(int(row["other_comparison_quote_rows_excluded"]), 0)
        self.assertEqual(sorted((r["recorded_by"], r["passage"], json.loads(r["definition_json"])["examples"][0]) for r in rows),
                         sorted([("both", "Wait.", "Wait."), ("comparison only", "Wait", "Wait."),
                                 ("current only", "Wait.\nJo: Or leave early.", "Wait."),
                                 ("comparison only", "Wait.\nJo: Or leave early.", "Bell.")]))
        with patch("themeforge.rigor_dialog.filedialog.asksaveasfilename", return_value=str(self.paths[1])):
            self.dialog._export_manual_records()
        self.assertIn("existing file was not changed", self.dialog.status.get())
        self.assertEqual([p.read_bytes() for p in self.paths], original)

    def test_unverifiable_comparisons_cannot_export(self):
        original = load_project(self.paths[1])
        theme = original.result.themes[0]
        quote = theme.quotes[0]
        edited = replace(quote, source_start=quote.source_start + 1)
        for state, reason in (
            (load_project(self.paths[0]), "shared assignment"),
            (replace(original, documents=(replace(self.doc, text=self.doc.text + "!"), original.documents[1])), "source"),
            (replace(original, documents=(self.doc,)), "source"),
            (replace(original, audit_events=()), "audit"),
            (replace(original, audit_events=tuple(replace(e, details="unmatched creation details")
                if e.action == "codebook_passage_assigned" else e for e in original.audit_events)), "original"),
            (replace(original, result=replace(original.result, themes=[replace(theme, quotes=[edited])])), "anchor"),
        ):
            with self.subTest(reason=reason):
                changed = self.folder / "alternative.tfproj"
                save_project(changed, state)
                self.choose(changed)
                with patch("themeforge.rigor_dialog.filedialog.asksaveasfilename", side_effect=AssertionError):
                    self.dialog._export_manual_records()
                self.assertIn(reason, self.dialog.status.get().lower())

    def test_same_and_mixed_recorded_coder_not_replaced_by_project_label(self):
        state = load_project(self.paths[1])
        for mixed, message in [(False, "same declared coder"), (True, "exactly one declared coder")]:
            with self.subTest(mixed=mixed):
                selected = {q.quote_id for t in state.result.themes for q in t.quotes if q.manual_definition_assignment}
                if mixed:
                    selected = {min(selected)}
                events = tuple(replace(e, actor="Amber") if e.target_id in selected else e for e in state.audit_events)
                themes = [replace(t, quotes=[replace(q, rationale=q.rationale.replace("by Blue at", "by Amber at"))
                                  if q.quote_id in selected else q for q in t.quotes]) for t in state.result.themes]
                changed = self.folder / "attribution.tfproj"
                save_project(changed, replace(state, audit_events=events, result=replace(state.result, themes=themes)))
                self.choose(changed)
                with patch("themeforge.rigor_dialog.filedialog.asksaveasfilename", side_effect=AssertionError):
                    self.dialog._export_manual_records()
                self.assertIn(message, self.dialog.status.get())

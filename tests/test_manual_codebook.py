"""A selected codebook entry is a manual decision, not a lexical match."""
import csv
from dataclasses import replace
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from themeforge.app import ThemeForgeApp
from themeforge.analysis_types import CodebookEntry
from themeforge.definition_review import DefinitionReview
from themeforge.project_io import NULLABLE_MEMBER, NULLABLE_PROFILE, load_project
from themeforge.rigor import CodedProject, ManualDefinitionComparisonError, intercoder_reliability
from themeforge.rigor_dialog import RigorDialog


class HiddenApp(ThemeForgeApp):
    def _build_layout(self):
        self.withdraw()
        super()._build_layout()


class ManualCodebookTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.app = HiddenApp()
        self.addCleanup(self.app.destroy)
        source = self.folder / "studio.txt"
        self.passage = "Wait for the lamp."
        self.text = f"Lee: {self.passage}\nJo: \U0001f4a1  {self.passage}  \nLee: The bell signals the end of rehearsal."
        source.write_text(self.text, encoding="utf-8")
        with patch("themeforge.app.filedialog.askopenfilenames", return_value=[str(source)]):
            self.app.open_transcript()
        book = self.folder / "codes.csv"
        book.write_text("code,description,example\nSignal,Ending the rehearsal,The bell sounds\n"
                        "Signal,Waiting for permission,Wait for the lamp\n", encoding="utf-8")
        with patch("themeforge.app.filedialog.askopenfilename", return_value=str(book)):
            self.app.open_codebook()
        self.app.researcher_name.set("Coder A")

    def select(self, index=1):
        widget = self.app.transcript_widgets["studio.txt"]
        start = widget.search(self.passage, "2.0", stopindex="2.end")
        widget.tag_add("sel", f"{start}-1c", "2.end")
        self.app.definition_box.current(index)

    def apply(self, confirm=True):
        with patch("themeforge.app.review_definition", return_value=self.app.definition_box.current() if confirm else None) as prompt:
            self.app.code_selection_to_definition()
        return prompt

    def report(self, suffix):
        path = self.folder / ("review" + suffix)
        with patch("themeforge.app.filedialog.asksaveasfilename", return_value=str(path)):
            self.app.save_report()
        return path.read_text(encoding="utf-8")

    def test_exact_duplicate_named_definition_and_source_survive_save_analyze_and_exports(self):
        self.assertIn("Ending the rehearsal", self.app.definition_box.cget("values")[0])
        self.select(0)  # Start on the wrong same-label entry.
        before = self.app._selected_transcript_text()
        def compare(dialog):
            self.assertFalse(dialog.winfo_viewable())
            self.assertEqual(dialog.indices, [0, 1])
            content = dialog.definitions.get("1.0", "end-1c")
            for value in ("Ending the rehearsal", "The bell sounds", "Waiting for permission", "Wait for the lamp"):
                self.assertIn(value, content)
            self.assertIn("Line 2", dialog.location.cget("text"))
            self.assertIn("Coder A", dialog.location.cget("text"))
            self.assertEqual(dialog.passage.get("evidence.first", "evidence.last"), self.passage)
            self.assertTrue(dialog.passage.get("1.0", "evidence.first").endswith("Jo: \U0001f4a1  "))
            dialog.choice.current(1)
            dialog.show_choice()
            self.assertIsNone(self.app.result)
            self.assertFalse(self.app.audit_events)
            self.assertEqual(self.app._selected_transcript_text(), before)

        self.review(compare, apply=False)
        self.assertIsNone(self.app.result)
        self.assertFalse(self.app.audit_events)
        self.assertEqual(self.app.definition_box.current(), 0)
        with patch("themeforge.app.analyze_documents_with_units", side_effect=AssertionError):
            self.review(compare, apply=True)
            self.assertEqual(self.app.definition_box.current(), 1)
            self.select()
            self.apply()
        self.assertEqual(len(self.app.result.themes), 1)
        theme, = self.app.result.themes
        quote, = theme.quotes
        start = self.text.rindex(self.passage)
        self.assertEqual((quote.source_name, quote.source_line, quote.source_start, quote.source_end, quote.text),
                         ("studio.txt", 2, start, start + len(self.passage), self.passage))
        self.assertEqual(self.app.result.quote_count, 0)  # No units analyzed.
        self.assertEqual(theme.validation["speaker_count"], 0)  # The selection did not assert a speaker.
        self.assertNotIn("central_theme_alignment", theme.validation)
        self.assertIn("unscored", self.app.theme_list.get(0))
        self.assertIn("Coder A", quote.rationale)
        self.assertIn("Waiting for permission", quote.rationale)
        self.assertIn("No automated analysis", self.report(".md"))
        self.assertIsNone(json.loads(self.report(".json"))["themes"][0]["quotes"][0]["relevance"])
        project = self.folder / "studio.tfproj"
        with patch("themeforge.app.filedialog.asksaveasfilename", return_value=str(project)):
            self.assertTrue(self.app.save_project())
        with zipfile.ZipFile(project) as archive:
            self.assertNotIn("project.json", archive.namelist())
            payload = json.loads(archive.read(NULLABLE_MEMBER))
            self.assertEqual(payload["format_version"], NULLABLE_PROFILE)
            self.assertIsNone(payload["result"]["themes"][0]["score"])
            self.assertIsNone(payload["result"]["themes"][0]["quotes"][0]["relevance"])
        with patch("themeforge.app.filedialog.askopenfilename", return_value=str(project)):
            self.app.open_project()
        self.assertEqual(self.app.result.themes, [theme])
        events = [e for e in self.app.audit_events if e.action == "codebook_passage_assigned"]
        self.assertEqual([(e.actor, e.target_id) for e in events], [("Coder A", quote.quote_id)])
        self.select(0)
        self.apply()
        self.assertEqual(len(self.app.result.themes), 2)
        self.app.analyze()
        saved = load_project(project)
        self.assertIn(theme, saved.result.themes)
        self.assertEqual(sum(t.id.startswith("M") for t in saved.result.themes), 2)
        rows = list(csv.DictReader(io.StringIO(self.report(".csv"))))
        manual = [row for row in rows if row["theme_id"].startswith("M")]
        self.assertEqual([row["relevance"] for row in manual], ["", ""])
        self.assertTrue(any("Waiting for permission" in row["selection_rationale"] for row in manual))
        self.assertTrue(any(row["relevance"] and row["theme_id"].startswith("C") for row in rows))
        coded = CodedProject(saved.documents, saved.result)
        with self.assertRaisesRegex(ManualDefinitionComparisonError, "matches names, not the definitions"):
            intercoder_reliability(coded, coded)

    def review(self, inspect, *, apply, from_match=False):
        # Exercise the actual modal without mapping a test window.
        failures = []
        visited = []
        def choose():
            dialog = next(w for w in self.app.winfo_children() if isinstance(w, DefinitionReview))
            visited.append(True)
            try:
                inspect(dialog)
                if apply:
                    dialog.apply_button.invoke()
            except Exception as exc:
                failures.append(exc)
            finally:
                if dialog.winfo_exists():
                    dialog.destroy()
        token = self.app.after_idle(choose)
        try:
            if from_match:
                self.app.code_match_to_definition()
            else:
                self.app.code_selection_to_definition()
        finally:
            self.app.after_cancel(token)
        if failures:
            raise failures[0]
        self.assertTrue(visited)

    def test_match_to_definition_uses_verified_quote_not_an_old_selection_and_keeps_both_records(self):
        app = self.app
        app.analyze()
        original = self.folder / "candidates.tfproj"
        with patch("themeforge.app.filedialog.asksaveasfilename", return_value=str(original)):
            self.assertTrue(app.save_project())
        before = original.read_bytes()
        original_state = load_project(original)
        candidates = original_state.result.themes
        theme = candidates[0]
        q = next(q for q in theme.quotes if q.source_line == 3)
        app._select_theme_by_id(theme.id)
        app._focus_quote_by_id(q.quote_id)
        self.select(1)  # A stale selection and preselected same-name entry must not drive this action.
        self.assertNotEqual(app._selected_transcript_text().text, q.text)

        def compare(dialog):
            self.assertEqual(dialog.choice.current(), -1)
            self.assertTrue(dialog.apply_button.instate(["disabled"]))
            self.assertIn(q.quote_id, dialog.location.cget("text"))
            self.assertEqual(dialog.passage.get("evidence.first", "evidence.last"), q.text)
            self.assertIn(self.passage, dialog.passage.get("1.0", "end-1c"))
            dialog.choice.current(0)
            dialog.show_choice()
            self.assertIn("Ending the rehearsal", dialog.definitions.get("1.0", "end-1c"))
            self.assertIn("Waiting for permission", dialog.definitions.get("1.0", "end-1c"))

        self.review(compare, apply=False, from_match=True)
        self.assertEqual(app.result, original_state.result)
        self.assertEqual(tuple(app.audit_events), original_state.audit_events)
        self.review(compare, apply=True, from_match=True)
        manual = app._current_theme()
        assigned, = manual.quotes
        self.assertNotEqual(assigned.quote_id, q.quote_id)
        self.assertEqual((assigned.source_start, assigned.source_end, assigned.text, assigned.relevance),
                         (q.source_start, q.source_end, q.text, None))
        self.assertEqual((manual.score, json.loads(manual.validation["manual_codebook_definition"])["description"]),
                         (None, "Ending the rehearsal"))
        self.assertIn("Coder A", assigned.rationale)
        self.assertEqual(original.read_bytes(), before)
        app._select_theme_by_id(theme.id)
        app._focus_quote_by_id(q.quote_id)
        n_events = len(app.audit_events)
        self.review(compare, apply=True, from_match=True)
        self.assertIn("already has this manual definition", app.status_text.get())
        self.assertEqual(len(app.audit_events), n_events)
        copy = self.folder / "assigned.tfproj"
        with patch("themeforge.app.filedialog.asksaveasfilename", return_value=str(copy)):
            self.assertTrue(app.save_project())
        with patch("themeforge.app.filedialog.askopenfilename", return_value=str(copy)):
            app.open_project()
        saved = load_project(copy)
        self.assertEqual(saved.result.themes, [*candidates, manual])
        link, = [e for e in saved.audit_events if e.action == "assignment_source_match"]
        info = json.loads(link.details)
        self.assertEqual((link.target_id, link.actor, info["match"]["theme_id"],
                          info["match"]["quote"]["quote_id"], info["match"]["quote"]["relevance"]),
                         (assigned.quote_id, "Coder A", theme.id, q.quote_id, q.relevance))
        self.assertEqual(info["applied_definition"], json.loads(manual.validation["manual_codebook_definition"]))
        audit = self.folder / "audit.csv"
        dialog = RigorDialog(app, app._rigor_context())
        dialog.withdraw()
        with patch("themeforge.rigor_dialog.filedialog.asksaveasfilename", return_value=str(audit)):
            dialog._export_audit()
        exported_link, = [r for r in csv.DictReader(io.StringIO(audit.read_text(encoding="utf-8")))
                          if r["action"] == "assignment_source_match"]
        self.assertEqual((exported_link["target_id"], json.loads(exported_link["details"])), (assigned.quote_id, info))
        with zipfile.ZipFile(copy) as archive:
            self.assertEqual(archive.namelist(), [NULLABLE_MEMBER])
            self.assertEqual(json.loads(archive.read(NULLABLE_MEMBER))["format_version"], NULLABLE_PROFILE)
        rows = list(csv.DictReader(io.StringIO(self.report(".csv"))))
        self.assertEqual(next(r for r in rows if r["quote_id"] == assigned.quote_id)["relevance"], "")
        self.assertEqual(next(r for r in rows if r["theme_id"] == theme.id and r["quote_id"] == q.quote_id)["relevance"],
                         f"{q.relevance:.3f}")
        self.assertIn("Original manual codebook assignment by Coder A", self.report(".md"))
        self.assertIsNone(json.loads(self.report(".json"))["themes"][-1]["quotes"][0]["relevance"])
        self.assertEqual(original.read_bytes(), before)

    def test_changed_match_or_unverified_anchor_does_not_create_a_manual_record(self):
        app = self.app
        app.codebook_entries += (CodebookEntry("End of rehearsal", "bell signals the end of rehearsal"),)
        app._refresh_definition_box()
        app.analyze()
        state = app._project_state()
        self.assertGreaterEqual(len(state.result.themes), 2)
        def change_group(*args, **kwargs):
            app._select_theme(1)
            return 0
        with patch("themeforge.app.review_definition", side_effect=change_group):
            app.code_match_to_definition()
        self.assertIn("Review again", app.status_text.get())
        self.assertEqual(app._project_state(), state)
        group = state.result.themes[0]
        q = next(q for q in group.quotes if q.source_line == 3)
        for stale in (replace(q, source_start=0, source_end=0), replace(q, source_line=1)):
            result = replace(state.result, themes=[replace(group, quotes=[stale], quote_count=1)])
            app._restore_project(replace(state, result=result), None)
            self.select(1)  # Even a valid text selection cannot stand in for a missing match anchor.
            with patch("themeforge.app.review_definition") as modal:
                app.code_match_to_definition()
                modal.assert_not_called()
            self.assertIn("source-verified quote", app.status_text.get())
            self.assertEqual(app.result, result)
            self.assertEqual(tuple(app.audit_events), state.audit_events)

    def test_examples_only_difference_and_multiline_passage_keep_exact_original_entry(self):
        self.app.codebook_entries = (
            CodebookEntry("Signal", "A cue.", ("Cue to finish.",)), CodebookEntry("Unrelated"),
            CodebookEntry("Signal", "A cue.", ("Cue to wait.",)))
        self.app._refresh_definition_box()
        self.app.definition_box.current(0)
        widget = self.app.transcript_widgets["studio.txt"]
        widget.tag_add("sel", "1.5", "2.end")
        selected = self.app._selected_transcript_text()
        def choose(dialog):
            self.assertEqual(dialog.indices, [0, 2])
            self.assertIn("Lines 1–2", dialog.location.cget("text"))
            self.assertEqual(dialog.passage.get("evidence.first", "evidence.last"), selected.text)
            for label in ("Entry 1", "Entry 3", "Cue to finish.", "Cue to wait."):
                self.assertIn(label, dialog.definitions.get("1.0", "end-1c"))
            self.assertNotIn("Unrelated", dialog.definitions.get("1.0", "end-1c"))
            dialog.choice.current(1)
        self.review(choose, apply=True)
        theme, = self.app.result.themes
        quote, = theme.quotes
        self.assertEqual(json.loads(theme.validation["manual_codebook_definition"])["examples"], ["Cue to wait."])
        self.assertEqual((quote.source_start, quote.source_end, quote.text),
                         (selected.source_start, selected.source_end, selected.text))
        self.assertEqual([e.target_id for e in self.app.audit_events], [quote.quote_id])

    def test_no_coder_or_cancel_does_not_create_workspace(self):
        self.select()
        before = tuple(self.app.audit_events)
        self.app.researcher_name.set("")
        self.apply().assert_not_called()
        self.app.researcher_name.set("Coder B")
        self.apply(False).assert_called_once()
        self.assertIsNone(self.app.result)
        self.assertEqual(tuple(self.app.audit_events), before)

    def test_second_passage_uses_same_definition_and_focuses_its_own_anchor(self):
        self.select()
        self.apply()
        widget = self.app.transcript_widgets["studio.txt"]
        widget.tag_remove("sel", "1.0", "end")
        widget.tag_add("sel", "1.0", "1.end")
        self.app.code_selection_to_theme()
        self.assertEqual(len(self.app.result.themes[0].quotes), 1)
        self.apply()
        only, = self.app.result.themes
        self.assertEqual(len(only.quotes), 2)
        selected = self.app._current_quote_match().quote
        self.assertEqual((selected.source_start, selected.text), (0, self.text.splitlines()[0]))

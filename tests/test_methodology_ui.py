from __future__ import annotations

import csv
from dataclasses import replace
from hashlib import sha256
import io
import json
from pathlib import Path
import tempfile
import tkinter as tk
import unittest

from themeforge.analysis_types import (
    AnalyticIntent, AnalysisResult, AnalysisSettings, CodebookEntry,
    QuoteUnit, Theme, ThemeQuote, TranscriptDocument,
)
from themeforge.app import AnalysisSetupDialog, EvidenceReviewDialog, ThemeForgeApp
from themeforge.exports import export_markdown, export_quotes_csv
from themeforge.manual_editing import preserve_manual_themes, update_theme_memo
from themeforge.project_io import ProjectState, load_project, save_project
from themeforge.rigor import IntercoderReliabilityReport, ThemeReliabilityRow
from themeforge.rigor_dialog import RigorDialog
from themeforge.rigor_exports import export_reliability_csv


def _result(**changes) -> AnalysisResult:
    return AnalysisResult(version="test", document_count=1, quote_count=0, themes=[], notes=[], **changes)


class MethodologyDesktopTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = ThemeForgeApp()
        self.app.withdraw()

    def tearDown(self) -> None:
        self.app.destroy()

    def test_setup_dialog_versions_changes_and_keeps_completed_run_intent(self) -> None:
        self.app.result = _result(analytic_intent=AnalyticIntent(methodology="codebook"))
        dialog = AnalysisSetupDialog(self.app)
        dialog.withdraw()
        dialog.methodology.set("Reflexive")
        dialog.logic.set("Hybrid")
        dialog.depth.set("Both")
        dialog.mode.set("Hybrid")
        dialog.questions.insert("1.0", "How is support experienced?\nWhat constrains choice?")
        dialog.lens.insert("1.0", "Relational autonomy")
        dialog.memo.insert("1.0", "Compare enabling and restrictive accounts.")
        dialog._save()

        saved = self.app._analysis_settings()
        self.assertEqual(saved.analytic_intent.methodology, "reflexive")
        self.assertEqual(saved.analytic_intent.research_questions, ("How is support experienced?", "What constrains choice?"))
        self.assertEqual(saved.analytic_intent.version, 2)
        self.assertEqual(saved.codebook_mode, "hybrid")
        self.assertEqual(self.app.result.analytic_intent.methodology, "codebook")
        self.assertEqual(self.app.audit_events[-1].action, "analytic_intent_updated")
        self.assertFalse(self.app._apply_analytic_settings(saved.analytic_intent, "hybrid"))
        self.assertEqual(self.app.analytic_intent.version, 2)

    def test_project_restore_preserves_unexposed_parameters_and_analytic_intent(self) -> None:
        intent = AnalyticIntent(methodology="coding_reliability", coding_logic="deductive", analytic_depth="semantic", research_questions=("Who receives assistance?",), version=4)
        entry = CodebookEntry("Assistance", code_id="support", version=3, inclusion_criteria="Received help", exclusion_criteria="Hypothetical help", negative_examples=("Nobody helped.",))
        settings = AnalysisSettings(min_quote_words=1, min_theme_size=2, agglomerative_limit=35, analytic_intent=intent, codebook_mode="legacy", codebook_entries=(entry,))
        state = ProjectState((), None, (TranscriptDocument("study", "P1: Support helped me decide."),), settings)
        with tempfile.TemporaryDirectory() as directory:
            first = Path(directory) / "first.tfproj"
            second = Path(directory) / "second.tfproj"
            save_project(first, state)
            self.app._restore_project(load_project(first), first)
            save_project(second, self.app._project_state())
            restored = load_project(second)
        self.assertEqual(restored.settings, settings)

    def test_excluded_reply_is_reviewable_and_selectable_with_exact_unicode_offsets(self) -> None:
        text = "Moderator: 🙂 Does that still happen?\nP1: No, not anymore.\nP2: My situation changed."
        target = "No, not anymore."
        start = text.index(target)
        unit = QuoteUnit("review-1", "P1", target, 2, 3, "focus-group", start, start + len(target), sha256(text.encode("utf-8")).hexdigest(), 0, len(text), text, "short_response")
        self.app.documents = [TranscriptDocument("focus-group", text)]
        self.app.result = _result(evidence_units=(unit,))
        self.app._render_transcript_tabs()
        dialog = EvidenceReviewDialog(self.app)
        dialog.withdraw()
        self.assertEqual(len(dialog.visible_units), 1)
        self.assertIn("short_response", dialog.detail.get("1.0", tk.END))
        self.assertIn("Moderator:", dialog.detail.get("1.0", tk.END))
        dialog._select_in_transcript()
        selection = self.app._selected_transcript_text()
        self.assertIsNotNone(selection)
        self.assertEqual(selection.text, target)
        self.assertEqual((selection.source_start, selection.source_end), (start, start + len(target)))
        self.app.theme_name.set("Changed experience")
        self.app.code_selection_to_new_theme()
        quote = self.app.result.themes[0].quotes[0]
        self.assertEqual(quote.text, target)
        self.assertEqual(text[quote.source_start:quote.source_end], target)

    def test_source_hash_mismatch_prevents_review_selection(self) -> None:
        text = "P1: I choose independently."
        target = "I choose independently."
        unit = QuoteUnit("review-2", "P1", target, 1, 3, "study", 4, len(text), "0" * 64)
        self.app.documents = [TranscriptDocument("study", text)]
        self.app._render_transcript_tabs()
        self.assertFalse(self.app._select_evidence_unit(unit))
        self.assertIn("source version", self.app.status_text.get())

    def test_manual_selection_trims_blank_lines_and_reports_physical_source_line(self) -> None:
        text = "Heading\n\n\n  A participant describes a change.  \n"
        target = "A participant describes a change."
        self.app.documents = [TranscriptDocument("study", text)]
        self.app._render_transcript_tabs()
        widget = self.app.transcript_widgets["study"]
        widget.tag_add(tk.SEL, "2.0", "5.0")
        selection = self.app._selected_transcript_text()
        self.assertIsNotNone(selection)
        self.assertEqual(selection.source_line, 4)
        self.assertEqual(selection.source_start, text.index(target))
        self.assertEqual(selection.source_end, text.index(target) + len(target))
        self.assertEqual(selection.text, target)
        self.assertEqual(selection.source_text, text)

    def test_reflexive_rigor_panel_explains_agreement_limit(self) -> None:
        self.app.documents = [TranscriptDocument("study", "P1: I choose independently.")]
        self.app.result = _result(analytic_intent=AnalyticIntent(methodology="reflexive"))
        self.app.open_rigor_tools()
        dialog = next(child for child in self.app.winfo_children() if isinstance(child, RigorDialog))
        self.assertIn("Kappa is not a validity grade", dialog.method_note.get())


class MethodologyExportTests(unittest.TestCase):
    def test_preserved_manual_code_exports_its_historical_definition(self) -> None:
        original = CodebookEntry("Assistance", "Version one: practical help", code_id="support", version=1, inclusion_criteria="Help already received", exclusion_criteria="Future plans", negative_examples=("I may ask next year.",))
        revised = replace(original, version=2, description="Version two: anticipated and actual help", inclusion_criteria="Help received or planned", exclusion_criteria="Unrelated activities", negative_examples=("I went swimming.",))
        quote = ThemeQuote("MQ01", "P1", "I received practical help.", 1.0, 1, "study", source_hash="a" * 64, memo="Check the earlier transcript version.")
        theme = Theme("C:support", "Assistance", "#000000", [], 1, 1.0, [quote], code_id="support", code_version=1, code_definition=original)
        previous = replace(_result(codebook_snapshot=(original,), source_hashes={"study": "a" * 64}), themes=[theme], quote_count=1)
        previous = update_theme_memo(previous, theme.id, "Keep the narrower interpretation.")
        current = _result(codebook_snapshot=(revised,), source_hashes={"study": "b" * 64})
        preserved = preserve_manual_themes(previous, current)

        row = next(csv.DictReader(io.StringIO(export_quotes_csv(preserved))))
        self.assertEqual(row["code_version"], "1")
        self.assertEqual(row["code_definition"], original.description)
        self.assertEqual(row["inclusion_criteria"], original.inclusion_criteria)
        self.assertEqual(row["exclusion_criteria"], original.exclusion_criteria)
        self.assertEqual(json.loads(row["negative_examples"]), list(original.negative_examples))
        self.assertIn("Researcher assignment marker", row["score_interpretation"])
        self.assertIn("code definition changed", row["recheck_reason"])
        self.assertIn("Historical source version differs", row["source_review_note"])
        self.assertEqual(row["quote_memo"], quote.memo)
        markdown = export_markdown(preserved)
        theme_section = markdown.split("## Theme 1:", 1)[1].split("## Color-Coded", 1)[0]
        self.assertIn("Assigned code version: 1", theme_section)
        self.assertIn(original.description, theme_section)
        self.assertNotIn(revised.description, theme_section)
        self.assertIn("Researcher assignment marker: 1.000", theme_section)
        self.assertIn("review again:", theme_section)

    def test_missing_historical_definition_does_not_substitute_current_definition(self) -> None:
        current = CodebookEntry("Assistance", "CURRENT DEFINITION", code_id="support", version=2)
        quote = ThemeQuote("Q1", "P1", "Help was available.", 0.5, 1)
        theme = Theme("C:support", "Assistance", "#000000", [], 1, 0.5, [quote], code_id="support", code_version=1)
        result = replace(_result(codebook_snapshot=(current,)), themes=[theme], quote_count=1)
        row = next(csv.DictReader(io.StringIO(export_quotes_csv(result))))
        self.assertEqual(row["code_definition"], "")
        self.assertEqual(row["code_version"], "1")
        self.assertIn("Historical definition unavailable", row["code_definition_status"])
        section = export_markdown(result).split("## Theme 1:", 1)[1]
        self.assertIn("Historical definition unavailable", section)
        self.assertNotIn("CURRENT DEFINITION", section)

    def test_markdown_marks_every_raw_source_line_as_a_blockquote(self) -> None:
        quote = ThemeQuote("Q1", "P1", "Evidence first line.\nEvidence second line.", 0.5, 1, context_text="Context first line.\nContext second line.")
        theme = Theme("T1", "Support", "#000000", [], 1, 0.5, [quote])
        result = replace(_result(), themes=[theme], quote_count=1)
        lines = export_markdown(result).splitlines()
        source_lines = [line for line in lines if "Evidence first line." in line or "Evidence second line." in line or "Context first line." in line or "Context second line." in line]
        self.assertEqual(len(source_lines), 6)
        self.assertTrue(all(line.lstrip().startswith("> ") for line in source_lines), source_lines)

    def test_reports_preserve_definitions_context_and_score_meaning(self) -> None:
        entry = CodebookEntry("Assistance", "Help received", code_id="support", version=3, inclusion_criteria="Actual help", exclusion_criteria="Imagined help", negative_examples=("I might ask someday.",))
        intent = AnalyticIntent(methodology="codebook", coding_logic="hybrid", analytic_depth="both", research_questions=("How does help affect choice?",), theoretical_lens="Relational autonomy", researcher_memo="Review contrasting cases.")
        quote = ThemeQuote("q1", "P1", "I received help.", 0.8, 2, "study", source_start=26, source_end=42, source_hash="a" * 64, context_start=0, context_end=42, context_text="Moderator: What happened?\nI received help.")
        theme = Theme("C1", "Assistance", "#000000", [], 1, 0.8, [quote], code_id="support", code_version=3)
        result = replace(_result(analytic_intent=intent, codebook_snapshot=(entry,), source_hashes={"study": "a" * 64}, run_id="run-1"), themes=[theme], quote_count=1)
        markdown = export_markdown(result)
        self.assertIn("Relational autonomy", markdown)
        self.assertIn("Imagined help", markdown)
        self.assertIn("I might ask someday.", markdown)
        self.assertIn("Context window", markdown)
        self.assertIn("not calibrated probabilities", markdown)
        self.assertNotIn("Cohesion score", markdown)
        row = next(csv.DictReader(io.StringIO(export_quotes_csv(result))))
        self.assertEqual(row["code_id"], "support")
        self.assertEqual(row["code_version"], "3")
        self.assertEqual(row["context"], quote.context_text)
        self.assertEqual(row["source_sha256"], "a" * 64)

    def test_reliability_export_keeps_method_and_interpretation_warning(self) -> None:
        row = ThemeReliabilityRow("choice", 1, 1, 0, 2, 0.75, 0.5, False, "")
        report = IntercoderReliabilityReport("A", "B", 4, (row,), ())
        output = export_reliability_csv(report, analytic_intent=AnalyticIntent(methodology="reflexive", theoretical_lens="=lens"), comparison_intent=AnalyticIntent(methodology="coding_reliability"), comparison_note="Codebook versions differ.")
        exported = next(csv.DictReader(io.StringIO(output)))
        self.assertEqual(exported["methodology"], "reflexive")
        self.assertEqual(exported["comparison_methodology"], "coding_reliability")
        self.assertIn("Kappa is not a validity grade", exported["interpretation_note"])
        self.assertIn("Codebook versions differ", exported["interpretation_note"])
        self.assertEqual(exported["theoretical_lens"], "'=lens")


if __name__ == "__main__":
    unittest.main()

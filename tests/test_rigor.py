from __future__ import annotations

import unittest
from collections.abc import Sequence

from themeforge.analysis_types import AnalysisResult, Theme, ThemeQuote, TranscriptDocument
from themeforge.quote_units import extract_quote_units
from themeforge.rigor import (
    CodedProject,
    CoderLabels,
    DocumentTextMismatchError,
    NoSharedDocumentsError,
    intercoder_reliability,
    matrix_queries,
)
from themeforge.transcript_parser import parse_transcript


def _quote(
    quote_id: str,
    source: str,
    speaker: str,
    text: str,
    start: int,
    end: int,
) -> ThemeQuote:
    return ThemeQuote(
        quote_id=quote_id,
        speaker=speaker,
        text=text,
        relevance=1.0,
        source_line=1,
        source_name=source,
        source_start=start,
        source_end=end,
    )


def _theme(name: str, quotes: Sequence[ThemeQuote]) -> Theme:
    return Theme(
        id=name[:1].upper(),
        name=name,
        color="#000000",
        keywords=[],
        quote_count=len(quotes),
        score=1.0,
        quotes=list(quotes),
    )


def _result(themes: Sequence[Theme]) -> AnalysisResult:
    return AnalysisResult(
        version="test",
        document_count=1,
        quote_count=sum(len(theme.quotes) for theme in themes),
        themes=list(themes),
        notes=[],
    )


def _unit_quotes(document: TranscriptDocument, indexes: set[int], theme: str) -> list[ThemeQuote]:
    units = extract_quote_units(parse_transcript(document.text, document.name), min_quote_words=1)
    return [
        _quote(f"{theme}-{index}", unit.source_name, unit.speaker, unit.text, unit.source_start, unit.source_end)
        for index, unit in enumerate(units)
        if index in indexes
    ]


class RigorMatrixTests(unittest.TestCase):
    def test_matrix_queries_deduplicate_overlap_and_include_zero_cells(self) -> None:
        doc_a = TranscriptDocument(
            name="A",
            text="Alpha: One two three four five.\nBeta: Six seven eight nine ten.",
        )
        doc_b = TranscriptDocument(name="B", text="Gamma: Eleven twelve thirteen fourteen.")
        first_start = doc_a.text.index("One")
        quotes = [
            _quote("Q1", "A", "Alpha", "One two three", first_start, first_start + 13),
            _quote("Q2", "A", "Alpha", "three four", first_start + 8, first_start + 18),
        ]

        report = matrix_queries([doc_a, doc_b], _result([_theme("Support", quotes), _theme("Barrier", [])]))

        support_a = report.source_cell("Support", "A")
        self.assertEqual(support_a.quote_count, 2)
        self.assertEqual(support_a.covered_chars, 18)
        self.assertEqual(support_a.denominator_chars, len(doc_a.text))

        self.assertEqual(report.source_cell("Support", "B").quote_count, 0)
        self.assertEqual(report.source_cell("Barrier", "A").covered_chars, 0)
        self.assertEqual(report.source_cell("Barrier", "B").denominator_chars, len(doc_b.text))

        support_alpha = report.speaker_cell("Support", "Alpha")
        self.assertEqual(support_alpha.quote_count, 2)
        self.assertEqual(support_alpha.covered_chars, 18)
        self.assertGreater(support_alpha.denominator_chars, support_alpha.covered_chars)
        self.assertEqual(report.speaker_cell("Support", "Beta").covered_chars, 0)

    def test_matrix_queries_keeps_speaker_coverage_separate_across_sources(self) -> None:
        documents = [
            TranscriptDocument(name="A", text="P1: Alpha beta gamma."),
            TranscriptDocument(name="B", text="P1: Delta zeta theta."),
        ]
        quotes = [
            _quote("Q1", "A", "P1", "Alpha", 4, 9),
            _quote("Q2", "B", "P1", "Delta", 4, 9),
        ]

        report = matrix_queries(documents, _result([_theme("Support", quotes)]))

        cell = report.speaker_cell("Support", "P1")
        self.assertEqual(cell.quote_count, 2)
        self.assertEqual(cell.covered_chars, 10)


class RigorReliabilityTests(unittest.TestCase):
    def test_intercoder_reliability_reports_perfect_theme_agreement(self) -> None:
        document = TranscriptDocument(
            name="Shared",
            text=(
                "P1: Group support helped me learn.\n"
                "P2: Teacher feedback improved my writing."
            ),
        )
        coder_a = _result([_theme(" Support ", _unit_quotes(document, {0}, "A"))])
        coder_b = _result([_theme("support", _unit_quotes(document, {0}, "B"))])

        report = intercoder_reliability(
            CodedProject([document], coder_a),
            CodedProject([document], coder_b),
            CoderLabels("Alice", "Bao"),
        )

        row = report.theme_row("support")
        self.assertEqual(report.coder_a_label, "Alice")
        self.assertEqual(report.coder_b_label, "Bao")
        self.assertEqual(report.unit_count, 2)
        self.assertEqual((row.both_present, row.a_only, row.b_only, row.neither), (1, 0, 0, 1))
        self.assertEqual(row.percent_agreement, 1.0)
        self.assertEqual(row.cohens_kappa, 1.0)
        self.assertFalse(row.kappa_undefined)
        self.assertEqual(report.disagreements, ())

    def test_intercoder_reliability_reports_partial_disagreement_counts_and_kappa(self) -> None:
        document = TranscriptDocument(
            name="Shared",
            text=(
                "P1: Support groups helped my confidence.\n"
                "P2: Clear deadlines reduced confusion.\n"
                "P3: Platform errors created stress.\n"
                "P4: Practice examples improved my writing."
            ),
        )
        coder_a = _result([_theme("Need clarity", _unit_quotes(document, {0, 1}, "A"))])
        coder_b = _result([_theme("need  CLARITY", _unit_quotes(document, {1, 2}, "B"))])

        report = intercoder_reliability(CodedProject([document], coder_a), CodedProject([document], coder_b))

        row = report.theme_row("need clarity")
        self.assertEqual((row.both_present, row.a_only, row.b_only, row.neither), (1, 1, 1, 1))
        self.assertEqual(row.percent_agreement, 0.5)
        self.assertEqual(row.cohens_kappa, 0.0)
        self.assertFalse(row.kappa_undefined)
        self.assertEqual(len(report.disagreements), 2)
        self.assertEqual({record.speaker for record in report.disagreements}, {"P1", "P3"})
        self.assertTrue(any(record.coder_a_present and not record.coder_b_present for record in report.disagreements))
        self.assertTrue(any(record.coder_b_present and not record.coder_a_present for record in report.disagreements))

    def test_intercoder_reliability_reports_undefined_kappa_when_prevalence_is_total(self) -> None:
        document = TranscriptDocument(
            name="Shared",
            text=(
                "P1: Support groups helped my confidence.\n"
                "P2: Clear deadlines reduced confusion."
            ),
        )
        coder_a = _result([_theme("Support", _unit_quotes(document, {0, 1}, "A"))])
        coder_b = _result([_theme("support", _unit_quotes(document, {0, 1}, "B"))])

        report = intercoder_reliability(CodedProject([document], coder_a), CodedProject([document], coder_b))

        row = report.theme_row("support")
        self.assertEqual((row.both_present, row.a_only, row.b_only, row.neither), (2, 0, 0, 0))
        self.assertIsNone(row.cohens_kappa)
        self.assertTrue(row.kappa_undefined)
        self.assertIn("prevalence", row.prevalence_warning)

    def test_intercoder_reliability_handles_empty_coding(self) -> None:
        document = TranscriptDocument(name="Shared", text="P1: Support groups helped my confidence.")

        report = intercoder_reliability(CodedProject([document], _result([])), CodedProject([document], _result([])))

        self.assertEqual(report.unit_count, 1)
        self.assertEqual(report.theme_rows, ())
        self.assertEqual(report.disagreements, ())

    def test_intercoder_reliability_rejects_same_name_changed_transcript_text(self) -> None:
        left = TranscriptDocument(name="Shared", text="P1: The original transcript text.")
        right = TranscriptDocument(name="Shared", text="P1: The edited transcript text.")

        with self.assertRaisesRegex(DocumentTextMismatchError, "same-name transcript text changed"):
            intercoder_reliability(CodedProject([left], _result([])), CodedProject([right], _result([])))

    def test_intercoder_reliability_rejects_projects_with_no_shared_documents(self) -> None:
        left = TranscriptDocument(name="Left", text="P1: One useful transcript line.")
        right = TranscriptDocument(name="Right", text="P1: Another useful transcript line.")

        with self.assertRaisesRegex(NoSharedDocumentsError, "at least one shared document"):
            intercoder_reliability(CodedProject([left], _result([])), CodedProject([right], _result([])))


if __name__ == "__main__":
    unittest.main()

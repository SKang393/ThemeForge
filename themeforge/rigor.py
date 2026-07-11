from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Final

from .analysis_types import AnalysisResult, QuoteUnit, ThemeQuote, TranscriptDocument, TranscriptSegment
from .quote_units import extract_quote_units
from .transcript_parser import parse_transcript


@dataclass(frozen=True, slots=True)
class DocumentTextMismatchError(Exception):
    name: str

    def __str__(self) -> str:
        return f"same-name transcript text changed for {self.name!r}"


@dataclass(frozen=True, slots=True)
class NoSharedDocumentsError(Exception):
    def __str__(self) -> str:
        return "intercoder reliability requires at least one shared document with matching name and text"


@dataclass(frozen=True, slots=True)
class MatrixCellNotFoundError(Exception):
    theme_name: str
    axis_value: str

    def __str__(self) -> str:
        return f"matrix cell not found for theme {self.theme_name!r} and value {self.axis_value!r}"


@dataclass(frozen=True, slots=True)
class MatrixCell:
    theme_name: str
    axis_value: str
    quote_count: int
    covered_chars: int
    denominator_chars: int

    @property
    def coverage_ratio(self) -> float:
        if self.denominator_chars == 0:
            return 0.0
        return self.covered_chars / self.denominator_chars


@dataclass(frozen=True, slots=True)
class MatrixQueryReport:
    source_rows: tuple[MatrixCell, ...]
    speaker_rows: tuple[MatrixCell, ...]

    def source_cell(self, theme_name: str, source_name: str) -> MatrixCell:
        return _find_cell(self.source_rows, theme_name, source_name)

    def speaker_cell(self, theme_name: str, speaker: str) -> MatrixCell:
        return _find_cell(self.speaker_rows, theme_name, speaker)


@dataclass(frozen=True, slots=True)
class CodedProject:
    documents: Sequence[TranscriptDocument]
    result: AnalysisResult


@dataclass(frozen=True, slots=True)
class CoderLabels:
    coder_a: str = "Coder A"
    coder_b: str = "Coder B"


@dataclass(frozen=True, slots=True)
class ThemeReliabilityRow:
    theme_name: str
    both_present: int
    a_only: int
    b_only: int
    neither: int
    percent_agreement: float
    cohens_kappa: float | None
    kappa_undefined: bool
    prevalence_warning: str


@dataclass(frozen=True, slots=True)
class DisagreementRecord:
    theme_name: str
    source_name: str
    speaker: str
    text: str
    coder_a_present: bool
    coder_b_present: bool


@dataclass(frozen=True, slots=True)
class IntercoderReliabilityReport:
    coder_a_label: str
    coder_b_label: str
    unit_count: int
    theme_rows: tuple[ThemeReliabilityRow, ...]
    disagreements: tuple[DisagreementRecord, ...]

    def theme_row(self, theme_name: str) -> ThemeReliabilityRow:
        normalized = _normalize_theme(theme_name)
        for row in self.theme_rows:
            if row.theme_name == normalized:
                return row
        raise MatrixCellNotFoundError(theme_name, "theme")


DEFAULT_LABELS: Final = CoderLabels()


def matrix_queries(documents: Sequence[TranscriptDocument], result: AnalysisResult) -> MatrixQueryReport:
    doc_lengths = {document.name: len(document.text) for document in documents}
    segments = [segment for document in documents for segment in parse_transcript(document.text, document.name)]
    speakers = sorted({segment.speaker for segment in segments})
    speaker_spans = _speaker_spans(segments)
    source_rows: list[MatrixCell] = []
    speaker_rows: list[MatrixCell] = []

    for theme in result.themes:
        for source_name, length in doc_lengths.items():
            quotes = [quote for quote in theme.quotes if quote.source_name == source_name]
            spans = [_clip_span(quote.source_start, quote.source_end, length) for quote in quotes]
            source_rows.append(MatrixCell(theme.name, source_name, len(quotes), _covered_chars(spans), length))
        for speaker in speakers:
            spans_by_source = speaker_spans[speaker]
            overlaps = _quote_speaker_overlaps(theme.quotes, spans_by_source, doc_lengths)
            speaker_rows.append(
                MatrixCell(
                    theme.name,
                    speaker,
                    _speaker_quote_count(theme.quotes, spans_by_source, doc_lengths),
                    sum(_covered_chars(spans) for spans in overlaps.values()),
                    sum(_covered_chars(spans) for spans in spans_by_source.values()),
                )
            )
    return MatrixQueryReport(tuple(source_rows), tuple(speaker_rows))


def intercoder_reliability(
    coder_a: CodedProject,
    coder_b: CodedProject,
    labels: CoderLabels = DEFAULT_LABELS,
) -> IntercoderReliabilityReport:
    shared_documents = _shared_documents(coder_a.documents, coder_b.documents)
    units = tuple(
        unit
        for document in shared_documents
        for unit in extract_quote_units(parse_transcript(document.text, document.name), min_quote_words=1)
    )
    a_quotes = _theme_quotes(coder_a.result, shared_documents)
    b_quotes = _theme_quotes(coder_b.result, shared_documents)
    theme_names = sorted(a_quotes.keys() | b_quotes.keys())
    rows: list[ThemeReliabilityRow] = []
    disagreements: list[DisagreementRecord] = []

    for theme_name in theme_names:
        counts = _theme_counts(units, a_quotes[theme_name], b_quotes[theme_name])
        rows.append(_reliability_row(theme_name, counts))
        disagreements.extend(_disagreements(theme_name, units, a_quotes[theme_name], b_quotes[theme_name]))

    return IntercoderReliabilityReport(
        labels.coder_a,
        labels.coder_b,
        len(units),
        tuple(rows),
        tuple(disagreements),
    )


def _find_cell(rows: Iterable[MatrixCell], theme_name: str, axis_value: str) -> MatrixCell:
    for row in rows:
        if row.theme_name == theme_name and row.axis_value == axis_value:
            return row
    raise MatrixCellNotFoundError(theme_name, axis_value)


def _shared_documents(
    documents_a: Sequence[TranscriptDocument],
    documents_b: Sequence[TranscriptDocument],
) -> tuple[TranscriptDocument, ...]:
    by_name_a = {document.name: document for document in documents_a}
    by_name_b = {document.name: document for document in documents_b}
    changed = sorted(name for name in by_name_a.keys() & by_name_b.keys() if by_name_a[name].text != by_name_b[name].text)
    if changed:
        raise DocumentTextMismatchError(changed[0])
    shared = tuple(by_name_a[name] for name in sorted(by_name_a.keys() & by_name_b.keys()))
    if not shared:
        raise NoSharedDocumentsError()
    return shared


def _speaker_spans(segments: Sequence[TranscriptSegment]) -> defaultdict[str, defaultdict[str, list[tuple[int, int]]]]:
    spans: defaultdict[str, defaultdict[str, list[tuple[int, int]]]] = defaultdict(lambda: defaultdict(list))
    for segment in segments:
        spans[segment.speaker][segment.source_name].append((segment.source_start, segment.source_end))
    return spans


def _clip_span(start: int, end: int, limit: int) -> tuple[int, int]:
    return max(0, min(start, limit)), max(0, min(end, limit))


def _covered_chars(spans: Iterable[tuple[int, int]]) -> int:
    merged: list[tuple[int, int]] = []
    for start, end in sorted((start, end) for start, end in spans if end > start):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return sum(end - start for start, end in merged)


def _quote_speaker_overlaps(
    quotes: Sequence[ThemeQuote],
    speaker_spans: defaultdict[str, list[tuple[int, int]]],
    doc_lengths: dict[str, int],
) -> defaultdict[str, list[tuple[int, int]]]:
    overlaps: defaultdict[str, list[tuple[int, int]]] = defaultdict(list)
    for quote in quotes:
        quote_start, quote_end = _clip_span(quote.source_start, quote.source_end, doc_lengths.get(quote.source_name, 0))
        quote_overlaps = [
            (max(quote_start, turn_start), min(quote_end, turn_end))
            for turn_start, turn_end in speaker_spans.get(quote.source_name, [])
            if min(quote_end, turn_end) > max(quote_start, turn_start)
        ]
        if quote_overlaps:
            overlaps[quote.source_name].extend(quote_overlaps)
    return overlaps


def _speaker_quote_count(
    quotes: Sequence[ThemeQuote],
    speaker_spans: defaultdict[str, list[tuple[int, int]]],
    doc_lengths: dict[str, int],
) -> int:
    return sum(1 for quote in quotes if _quote_speaker_overlaps([quote], speaker_spans, doc_lengths))


def _normalize_theme(name: str) -> str:
    return " ".join(name.casefold().split())


def _theme_quotes(result: AnalysisResult, documents: Sequence[TranscriptDocument]) -> defaultdict[str, list[ThemeQuote]]:
    source_names = {document.name for document in documents}
    quotes: defaultdict[str, list[ThemeQuote]] = defaultdict(list)
    for theme in result.themes:
        quotes[_normalize_theme(theme.name)].extend(quote for quote in theme.quotes if quote.source_name in source_names)
    return quotes


def _is_present(unit: QuoteUnit, quotes: Sequence[ThemeQuote]) -> bool:
    return any(
        unit.source_name == quote.source_name
        and min(unit.source_end, quote.source_end) > max(unit.source_start, quote.source_start)
        for quote in quotes
    )


def _theme_counts(units: Sequence[QuoteUnit], a_quotes: Sequence[ThemeQuote], b_quotes: Sequence[ThemeQuote]) -> tuple[int, int, int, int]:
    both = a_only = b_only = neither = 0
    for unit in units:
        a_present = _is_present(unit, a_quotes)
        b_present = _is_present(unit, b_quotes)
        if a_present and b_present:
            both += 1
        elif a_present:
            a_only += 1
        elif b_present:
            b_only += 1
        else:
            neither += 1
    return both, a_only, b_only, neither


def _reliability_row(theme_name: str, counts: tuple[int, int, int, int]) -> ThemeReliabilityRow:
    both, a_only, b_only, neither = counts
    total = both + a_only + b_only + neither
    observed = (both + neither) / total if total else 0.0
    a_yes = both + a_only
    b_yes = both + b_only
    expected = ((a_yes * b_yes) + ((total - a_yes) * (total - b_yes))) / (total * total) if total else 1.0
    undefined = expected == 1.0
    kappa = None if undefined else round((observed - expected) / (1.0 - expected), 6)
    sparse = a_yes == 0 or b_yes == 0 or a_yes == total or b_yes == total
    warning = "theme prevalence may make kappa unstable" if sparse else ""
    return ThemeReliabilityRow(theme_name, both, a_only, b_only, neither, round(observed, 6), kappa, undefined, warning)


def _disagreements(
    theme_name: str,
    units: Sequence[QuoteUnit],
    a_quotes: Sequence[ThemeQuote],
    b_quotes: Sequence[ThemeQuote],
) -> tuple[DisagreementRecord, ...]:
    records: list[DisagreementRecord] = []
    for unit in units:
        a_present = _is_present(unit, a_quotes)
        b_present = _is_present(unit, b_quotes)
        if a_present != b_present:
            records.append(DisagreementRecord(theme_name, unit.source_name, unit.speaker, unit.text, a_present, b_present))
    return tuple(records)

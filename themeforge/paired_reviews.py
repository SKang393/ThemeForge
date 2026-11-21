"""Compare named readers' current audit notes on identical, source-verified match snapshots."""
import csv
from dataclasses import dataclass
import io

from .analysis_types import Theme, ThemeQuote
from .audit import neutralize_csv_cell
from .match_reviews import MatchReviews, ReviewNote, match_id, match_snapshot, review_key
from .rigor import CodedProject, CoderLabels
from .source_location import TranscriptIndex


@dataclass(frozen=True)
class PairedMatch:
    theme: Theme
    quote: ThemeQuote
    key: str
    outcome: str
    current: ReviewNote | None
    comparison: ReviewNote | None
    shared_id: str


@dataclass(frozen=True)
class PairedReviewReport:
    matches: tuple[PairedMatch, ...]
    reviewers: CoderLabels
    current_excluded: int
    comparison_excluded: int

    @property
    def shared(self) -> int:
        return len(self.matches)

    @property
    def csv_text(self) -> str:
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(("review_comparison", "theme_id", "theme_name", "quote_id", "source_file", "source_line",
                         "source_start", "source_end", "quote", "match_review_target_id",
                         "current_reviewer", "current_disposition", "current_reason", "current_time_utc",
                         "comparison_reviewer", "comparison_disposition", "comparison_reason", "comparison_time_utc",
                         "current_quote_rows_excluded", "comparison_quote_rows_excluded",
                         "current_review_target_id", "comparison_review_target_id"))
        for row in self.matches:
            theme, quote = row.theme, row.quote
            values = (row.outcome, theme.id, theme.name, quote.quote_id, quote.source_name, quote.source_line,
                      quote.source_start, quote.source_end, quote.text, row.shared_id,
                      self.reviewers.coder_a, *_fields(row.current), self.reviewers.coder_b, *_fields(row.comparison),
                      self.current_excluded, self.comparison_excluded,
                      row.current.audit_target_id if row.current else "",
                      row.comparison.audit_target_id if row.comparison else "")
            writer.writerow(neutralize_csv_cell(value) if isinstance(value, str) else value for value in values)
        return output.getvalue()


def _matches(project: CodedProject, reviews: MatchReviews) -> tuple[dict[str, tuple[Theme, ThemeQuote, str]], int]:
    sources = TranscriptIndex(project.documents)
    matches = {}
    total = 0
    for theme in project.result.themes:
        total += len(theme.quotes)
        if not reviews.definition_is_current(theme):
            continue
        for quote in theme.quotes:
            if sources.locate(quote).status == "exact_span":
                snapshot = match_snapshot(theme, quote, sources)
                target_id = match_id(snapshot)
                matches[review_key(snapshot, target_id)] = theme, quote, target_id
    return matches, total


def _outcome(current: ReviewNote | None, comparison: ReviewNote | None) -> str:
    if current is None:
        return "neither_reviewed" if comparison is None else "missing_current_review"
    if comparison is None:
        return "missing_comparison_review"
    if "deferred" in (current.disposition, comparison.disposition):
        return "deferred"
    if current.disposition == comparison.disposition:
        return f"both_{current.disposition}"
    return "disagreement"


def _fields(note: ReviewNote | None) -> tuple[str, str, str]:
    if note is None:
        return "", "", ""
    return note.disposition, note.reason, note.timestamp_utc


def compare_match_reviews(current: CodedProject, comparison: CodedProject, reviewers: CoderLabels) -> PairedReviewReport:
    a, b = reviewers.coder_a.strip(), reviewers.coder_b.strip()
    if not a or not b or a.casefold() == b.casefold():
        raise ValueError("Select two distinct named reviewers, one from each project.")
    current_notes = MatchReviews(current.audit_events, reviewer=a, codebook_entries=current.codebook_entries)
    comparison_notes = MatchReviews(comparison.audit_events, reviewer=b, codebook_entries=comparison.codebook_entries)
    left, left_total = _matches(current, current_notes)
    right, right_total = _matches(comparison, comparison_notes)
    shared = left.keys() & right.keys()
    if not shared:
        raise ValueError(f"No identical, source-verified matches across {left_total} current and "
                         f"{right_total} comparison quote rows. Check the source text, saved themes and codebook entries.")
    excluded = left_total - len(shared), right_total - len(shared)

    def source_order(key: str) -> tuple:
        theme, quote, target_id = left[key]
        return quote.source_name, quote.source_start, quote.source_end, theme.name, target_id

    matches = []
    for key in sorted(shared, key=source_order):
        theme, quote, target_id = left[key]
        current_note = current_notes.by_key(key)
        comparison_note = comparison_notes.by_key(key)
        current_id = current_note.audit_target_id if current_note else ""
        comparison_id = comparison_note.audit_target_id if comparison_note else ""
        shared_id = current_id or comparison_id
        if not shared_id and target_id == right[key][2]:
            shared_id = target_id
        if current_id and comparison_id and current_id != comparison_id:
            shared_id = ""
        matches.append(PairedMatch(theme, quote, key, _outcome(current_note, comparison_note),
                                   current_note, comparison_note, shared_id))
    return PairedReviewReport(tuple(matches), CoderLabels(a, b), *excluded)

"""Compare current review notes and disclose definition differences at verified source spans."""
import csv
from collections import Counter
from dataclasses import dataclass
import io

from .analysis_types import Theme, ThemeQuote
from .audit import neutralize_csv_cell
from .codebook_match import comparison_entry_snapshot
from .match_reviews import MatchReviews, ReviewNote, match_id, match_snapshot, review_key
from .rigor import CodedProject, CoderLabels
from .source_location import TranscriptIndex
from .text_utils import normalize_space


@dataclass(frozen=True)
class PairedMatch:
    theme: Theme
    quote: ThemeQuote
    key: str
    outcome: str
    current: ReviewNote | None
    comparison: ReviewNote | None
    shared_id: str
    comparison_key: str


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
    def outcome_counts(self) -> Counter[str]:
        return Counter(row.outcome for row in self.matches)

    @property
    def counts_csv(self) -> str:
        counts = self.outcome_counts
        output = io.StringIO(newline="")
        csv.writer(output).writerows((
            ("Paired review outcome or scope", "Count"),
            ("Agreement (both supported)", counts["both_supported"]),
            ("Agreement (both declined)", counts["both_declined"]),
            ("Disagreement (support versus decline)", counts["disagreement"]),
            ("Deferral (both reviewed and at least one deferred)", counts["deferred"]),
            ("Missing exactly one review", counts["missing_current_review"] + counts["missing_comparison_review"]),
            ("Missing both reviews", counts["neither_reviewed"]),
            ("Comparable pairs (denominator for the six review outcomes)", self.shared - counts["different_definitions"]),
            ("Excluded pairs (different codebook definitions or examples)", counts["different_definitions"]),
            ("Total pairs across selected sources", self.shared),
            ("Unpaired quote rows in current project", self.current_excluded),
            ("Unpaired quote rows in comparison project", self.comparison_excluded),
        ))
        return output.getvalue()

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


@dataclass(frozen=True)
class _Candidate:
    theme: Theme
    quote: ThemeQuote
    note_key: str
    target_id: str
    definition: str | None
    entry_span: tuple | None
    match_without_position: str | None


def _matches(project: CodedProject, reviews: MatchReviews,
             source_names: frozenset[str] | None) -> tuple[dict[str, _Candidate], int]:
    sources = TranscriptIndex(doc for doc in project.documents if source_names is None or doc.name in source_names)
    entries_by_position = {f"C{i:02d}": entry for i, entry in enumerate(project.codebook_entries, start=1)}
    matches = {}
    total = 0
    for theme in project.result.themes:
        current_definition = reviews.definition_is_current(theme)
        # Only generated suggestions use the entry/span pairing rule across projects.
        entry = reviews.codebook_entry(theme) if "generated_from_codebook_entry" in theme.validation else None
        definition = comparison_entry_snapshot(entry) if entry is not None else None
        for quote in theme.quotes:
            if source_names is not None and quote.source_name not in source_names:
                continue
            total += 1
            if current_definition and sources.locate(quote).status == "exact_span":
                snapshot = match_snapshot(theme, quote, sources)
                target_id = match_id(snapshot)
                note_key = review_key(snapshot, target_id)
                pair_key = note_key
                entry_span = None
                match_without_position = None
                if entry is not None:
                    # Keep audit lookup exact even when only the definition's spacing differs.
                    comparison_snapshot = {**snapshot, "generated_from_codebook_entry": definition}
                    if definition != snapshot["generated_from_codebook_entry"]:
                        pair_key = review_key(comparison_snapshot)
                    if not theme.parent_theme_id and entries_by_position.get(theme.id) == entry:
                        # The saved codebook verifies that this group ID denotes a row position.
                        match_without_position = review_key({**comparison_snapshot, "theme_id": ""})
                    entry_span = (entry.name, normalize_space(theme.name), quote.source_name, snapshot["source_sha256"],
                                  quote.source_start, quote.source_end, quote.speaker)
                matches[pair_key] = _Candidate(theme, quote, note_key, target_id, definition, entry_span,
                                               match_without_position)
    return matches, total


def _unique_entry_spans(matches: dict[str, _Candidate]) -> dict[tuple, str | None]:
    by_span = {}
    for key, row in matches.items():
        if row.entry_span is not None:
            by_span[row.entry_span] = None if row.entry_span in by_span else key
    return by_span


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


def compare_match_reviews(current: CodedProject, comparison: CodedProject, reviewers: CoderLabels,
                          *, source_names: frozenset[str] | None = None) -> PairedReviewReport:
    a, b = reviewers.coder_a.strip(), reviewers.coder_b.strip()
    if not a or not b or a.casefold() == b.casefold():
        raise ValueError("Select two distinct named reviewers, one from each project.")
    if source_names is not None and not source_names:
        raise ValueError("Choose at least one source for the paired CSV.")
    current_notes = MatchReviews(current.audit_events, reviewer=a, codebook_entries=current.codebook_entries)
    comparison_notes = MatchReviews(comparison.audit_events, reviewer=b, codebook_entries=comparison.codebook_entries)
    left, left_total = _matches(current, current_notes, source_names)
    right, right_total = _matches(comparison, comparison_notes, source_names)
    pairs = {key: key for key in left.keys() & right.keys()}
    # At a unique source occurrence, a reordered entry may differ only in its position.
    # A changed definition may also change the match itself; align it only to report the exclusion.
    left_spans, right_spans = _unique_entry_spans(left), _unique_entry_spans(right)
    for span in left_spans.keys() & right_spans.keys():
        a_key, b_key = left_spans[span], right_spans[span]
        if a_key and b_key and a_key not in pairs:
            first, second = left[a_key], right[b_key]
            reordered_match = (first.match_without_position is not None
                               and first.match_without_position == second.match_without_position)
            if first.definition != second.definition or reordered_match:
                pairs[a_key] = b_key
    if source_names is not None and source_names != {left[key].quote.source_name for key in pairs}:
        raise ValueError("A selected source has no verified shared matches. Choose sources again.")
    if not pairs:
        raise ValueError(f"No identical, source-verified matches across {left_total} current and "
                         f"{right_total} comparison quote rows. Check the source text, saved themes and codebook entries.")
    excluded = left_total - len(pairs), right_total - len(pairs)

    def source_order(key: str) -> tuple:
        row = left[key]
        return row.quote.source_name, row.quote.source_start, row.quote.source_end, row.theme.name, row.target_id

    matches = []
    for key in sorted(pairs, key=source_order):
        first, second = left[key], right[pairs[key]]
        current_note = current_notes.by_key(first.note_key)
        comparison_note = comparison_notes.by_key(second.note_key)
        current_id = current_note.audit_target_id if current_note else first.target_id
        comparison_id = comparison_note.audit_target_id if comparison_note else second.target_id
        outcome = (_outcome(current_note, comparison_note) if first.definition == second.definition
                   else "different_definitions")
        shared_id = current_id if current_id == comparison_id else ""
        matches.append(PairedMatch(first.theme, first.quote, first.note_key, outcome,
                                   current_note, comparison_note, shared_id, second.note_key))
    return PairedReviewReport(tuple(matches), CoderLabels(a, b), *excluded)

"""Summarize stored theme passages by explicitly assigned case and current reader."""
from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from .analysis_types import AnalysisResult, CodebookEntry, Theme, ThemeQuote, TranscriptDocument
from .audit import AuditEvent
from .match_reviews import MatchReviews, ReviewNote
from .source_location import TranscriptIndex


@dataclass(frozen=True, slots=True)
class CasePassage:
    quote: ThemeQuote
    status: str
    note: ReviewNote | None = None

    @property
    def navigable(self) -> bool:
        return self.status not in ("historical", "unverified")


@dataclass(frozen=True, slots=True)
class CaseCell:
    # Case labels and individual source positions occupy separate namespaces.
    group_key: tuple[str, str | int]
    sources: tuple[str, ...]
    theme: Theme
    passages: tuple[CasePassage, ...]

    @property
    def case_label(self) -> str:
        return str(self.group_key[1]) if self.group_key[0] == "case" else ""

    @property
    def unresolved(self) -> bool:
        return self.group_key[0] == "unresolved"

    @property
    def counts(self) -> Counter[str]:
        return Counter(passage.status for passage in self.passages)


def case_review_cells(
    documents: Sequence[TranscriptDocument],
    result: AnalysisResult | None,
    audit_events: Iterable[AuditEvent],
    *,
    reviewer: str,
    codebook_entries: Iterable[CodebookEntry] = (),
) -> tuple[CaseCell, ...]:
    """One cell per case and theme, with visits in project order. Unassigned visits stay separate."""
    if result is None:
        return ()
    sources = TranscriptIndex(documents)
    reader = reviewer.strip()
    reviews = MatchReviews(audit_events, reviewer=reader, codebook_entries=codebook_entries)
    groups: dict[tuple[str, str | int], list[str]] = {}
    for index, document in enumerate(documents):
        label = document.case_label.strip()
        key = ("case", label) if label else ("source", index)
        groups.setdefault(key, []).append(document.name)

    cells = []
    for theme in result.themes:
        current = reviews.definition_is_current(theme)
        by_source: dict[str, list[CasePassage]] = defaultdict(list)
        unresolved: dict[str, list[CasePassage]] = defaultdict(list)
        for quote in theme.quotes:
            name = quote.source_name
            if not sources.has_unique_source(name):
                # Keep the stored occurrence visible without assigning it to a guessed visit.
                unresolved[name].append(CasePassage(quote, "unverified"))
            elif sources.locate(quote).status != "exact_span":
                by_source[name].append(CasePassage(quote, "unverified"))
            elif not current:
                note = reviews.for_navigation(theme, quote, sources, reviewer=reader)
                by_source[name].append(CasePassage(quote, "historical", note))
            else:
                note = reviews.latest(theme, quote, sources, reviewer=reader) if reader else None
                by_source[name].append(CasePassage(quote, note.disposition if note else "not_reviewed", note))
        for entries in by_source.values():
            entries.sort(key=lambda p: (p.quote.source_line, p.quote.source_start))
        for key, names in groups.items():
            passages = tuple(p for name in names for p in by_source.get(name, ()))
            cells.append(CaseCell(key, tuple(names), theme, passages))
        for name, entries in unresolved.items():
            cells.append(CaseCell(("unresolved", name), (name,), theme, tuple(entries)))
    return tuple(cells)

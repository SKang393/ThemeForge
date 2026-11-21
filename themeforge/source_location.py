from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from hashlib import sha256
import re
from typing import Literal

from .analysis_types import QuoteUnit, ThemeQuote, TranscriptDocument, TranscriptSegment
from .transcript_parser import parse_transcript


LOCATION_SCOPE = ("The location check compares the recorded excerpt and location with the supplied transcript snapshot. "
                  "It does not verify speaker identity or whether a code fits.")


@dataclass(frozen=True, slots=True)
class SourceLocation:
    status: Literal["exact_span", "text_only", "conflict", "unverifiable"]
    note: str
    start: int | None = None
    end: int | None = None


class TranscriptIndex:
    """Check recorded locations without reading external files or substituting other occurrences."""

    def __init__(self, documents: Iterable[TranscriptDocument]):
        self._digests: dict[str, str] = {}
        self._turn_ends: dict[str, list[int]] = {}
        self._sources: dict[str, tuple[str, list[int]] | None] = {}
        for doc in documents:
            if doc.name in self._sources:
                self._sources[doc.name] = None
            else:
                self._sources[doc.name] = (doc.text, [m.start() for m in re.finditer("\n", doc.text)])

    def matches_documents(self, documents: Sequence[TranscriptDocument]) -> bool:
        """Check the ordered source texts used to render the transcript tabs, ignoring memo edits."""
        return len(documents) == len(self._sources) and all(
            doc.name == name and entry is not None and doc.text == entry[0]
            for doc, (name, entry) in zip(documents, self._sources.items(), strict=True)
        )

    def has_unique_source(self, name: str) -> bool:
        return self._sources.get(name) is not None

    def digest(self, name: str) -> str | None:
        entry = self._sources.get(name)
        if entry is None:
            return None
        if name not in self._digests:
            self._digests[name] = sha256(entry[0].encode("utf-8")).hexdigest()
        return self._digests[name]

    def neighboring_turn_text(self, quote: ThemeQuote | TranscriptSegment) -> tuple[str, str]:
        """Raw text around a verified excerpt, through one neighboring parsed turn on each side."""
        entry = self._sources.get(quote.source_name)
        if entry is None or self.locate(quote).status != "exact_span":
            return "", ""
        source, _ = entry
        if quote.source_name not in self._turn_ends:
            self._turn_ends[quote.source_name] = [t.source_end for t in parse_transcript(source, quote.source_name)]
        ends = self._turn_ends[quote.source_name]
        first = bisect_right(ends, quote.source_start)
        last = bisect_left(ends, quote.source_end)
        # Start after the turn before the previous one, preserving speaker prefixes and line breaks.
        start = ends[first - 2] if first >= 2 else 0
        stop = ends[last + 1] if last + 1 < len(ends) else len(source)
        return source[start:quote.source_start].lstrip("\r\n"), source[quote.source_end:stop].rstrip("\r\n")

    def locate(self, q: ThemeQuote | QuoteUnit | TranscriptSegment) -> SourceLocation:
        entry = self._sources.get(q.source_name)
        if entry is None or not q.text:
            return SourceLocation("unverifiable", "No unique named source or nonempty recorded excerpt.")
        source, newlines = entry
        start, end = q.source_start, q.source_end
        legacy = start == end == 0
        if legacy:
            start = source.find(q.text)
            if start < 0 or source.find(q.text, start + 1) >= 0:
                return SourceLocation("unverifiable", "No stored offsets and no unique full-text match.")
            end = start + len(q.text)
        if not (0 <= start < end <= len(source) and end - start == len(q.text) and source[start:end] == q.text):
            return SourceLocation("conflict", "The recorded offsets do not match the full stored excerpt.")
        if q.source_line < 1:
            return SourceLocation("unverifiable", "No valid recorded line to verify the excerpt location.")
        if 1 + bisect_left(newlines, start) != q.source_line:
            return SourceLocation("conflict", "The recorded line does not match the full excerpt's location.")
        if legacy:
            return SourceLocation("text_only", "Text-only location: unique full excerpt on the recorded line; "
                                  "original offsets unavailable.", start, end)
        return SourceLocation("exact_span", "Recorded span matches the source text on the recorded line; "
                              "speaker identity and code meaning are not verified.", start, end)

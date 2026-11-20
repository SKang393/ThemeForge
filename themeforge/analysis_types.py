from __future__ import annotations

from dataclasses import dataclass, field

ValidationValue = str | int | float

@dataclass(frozen=True, slots=True)
class CodebookEntry:
    name: str
    description: str = ""
    examples: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True, order=True)
class SpeakerOverride:
    source_name: str
    speaker: str
    included: bool


@dataclass(frozen=True, slots=True)
class AnalysisSettings:
    theme_count: int = 8
    quotes_per_theme: int = 0
    min_theme_size: int = 1
    min_quote_words: int = 4
    agglomerative_limit: int = 120
    central_theme: str = ""
    semantic_backend: str = "tfidf"
    language_mode: str = "Auto"
    codebook_entries: tuple[CodebookEntry, ...] = ()
    speaker_overrides: tuple[SpeakerOverride, ...] = ()


@dataclass(frozen=True, slots=True)
class FocusProfile:
    central_theme: str
    direct_terms: tuple[str, ...]
    related_weights: dict[str, float]
    document_frequency: dict[str, int]
    document_count: int
    average_document_length: float


@dataclass(frozen=True, slots=True)
class TranscriptDocument:
    name: str
    text: str
    memo: str = ""


@dataclass(frozen=True, slots=True)
class TranscriptSegment:
    speaker: str
    text: str
    source_line: int
    source_name: str = "Transcript"
    source_start: int = 0
    source_end: int = 0


@dataclass(frozen=True, slots=True)
class QuoteUnit:
    id: str
    speaker: str
    text: str
    source_line: int
    word_count: int
    source_name: str = "Transcript"
    source_start: int = 0
    source_end: int = 0


@dataclass(frozen=True, slots=True)
class ThemeQuote:
    quote_id: str
    speaker: str
    text: str
    relevance: float | None
    source_line: int
    source_name: str = "Transcript"
    rationale: str = "Selected as a candidate quote for researcher review."
    source_start: int = 0
    source_end: int = 0
    memo: str = ""

    @property
    def manual_definition_assignment(self) -> bool:
        # This ID family is allocated only to explicitly applied codebook definitions.
        return self.quote_id.startswith("MQD")

    @property
    def reported_relevance(self) -> float | None:
        return None if self.manual_definition_assignment or self.relevance is None or self.relevance < 0 else self.relevance


@dataclass(frozen=True, slots=True)
class Theme:
    id: str
    name: str
    color: str
    keywords: list[str]
    quote_count: int
    score: float | None
    quotes: list[ThemeQuote] = field(default_factory=list)
    validation: dict[str, ValidationValue] = field(default_factory=dict)
    memo: str = ""
    parent_theme_id: str = ""

    @property
    def includes_manual_definition(self) -> bool:
        return "manual_codebook_definition" in self.validation or any(q.manual_definition_assignment for q in self.quotes)

    @property
    def reported_score(self) -> float | None:
        if self.score is None or self.score < 0 or self.includes_manual_definition or any(q.reported_relevance is None for q in self.quotes):
            return None
        return self.score


@dataclass(frozen=True, slots=True)
class AnalysisResult:
    version: str
    document_count: int
    quote_count: int
    themes: list[Theme]
    notes: list[str]
    validation_summary: list[str] = field(default_factory=list)



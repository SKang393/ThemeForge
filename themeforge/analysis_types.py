from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256

ValidationValue = str | int | float


@dataclass(frozen=True, slots=True)
class AnalyticIntent:
    methodology: str = "unspecified"
    coding_logic: str = "unspecified"
    analytic_depth: str = "unspecified"
    research_questions: tuple[str, ...] = ()
    theoretical_lens: str = ""
    researcher_memo: str = ""
    version: int = 1

    def __post_init__(self) -> None:
        choices = {
            "methodology": {"unspecified", "reflexive", "codebook", "coding_reliability"},
            "coding_logic": {"unspecified", "inductive", "deductive", "hybrid"},
            "analytic_depth": {"unspecified", "semantic", "latent", "both"},
        }
        for name, allowed in choices.items():
            if getattr(self, name) not in allowed:
                raise ValueError(f"Unsupported {name}: {getattr(self, name)}")
        if isinstance(self.version, bool) or not isinstance(self.version, int) or self.version < 1:
            raise ValueError("Analytic intent version must be a positive integer.")


@dataclass(frozen=True, slots=True)
class CodebookEntry:
    name: str
    description: str = ""
    examples: tuple[str, ...] = ()
    code_id: str = ""
    version: int = 1
    inclusion_criteria: str = ""
    exclusion_criteria: str = ""
    negative_examples: tuple[str, ...] = ()
    origin: str = "unspecified"

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("A code definition needs a name.")
        if not self.code_id:
            # A default ID is deterministic for legacy imports. Persist it when renaming.
            digest = sha256(self.name.strip().encode("utf-8")).hexdigest()[:20]
            object.__setattr__(self, "code_id", f"code_{digest}")
        if isinstance(self.version, bool) or not isinstance(self.version, int) or self.version < 1:
            raise ValueError("Code definition version must be a positive integer.")
        if self.origin not in {"unspecified", "inductive", "deductive", "hybrid"}:
            raise ValueError(f"Unsupported code origin: {self.origin}")


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
    analytic_intent: AnalyticIntent = field(default_factory=AnalyticIntent)
    codebook_mode: str = "codebook_only"
    discovery_mode: str = "auto"

    def __post_init__(self) -> None:
        if self.codebook_mode not in {"codebook_only", "hybrid", "legacy"}:
            raise ValueError(f"Unsupported codebook mode: {self.codebook_mode}")
        if self.discovery_mode not in {"auto", "open", "guided", "legacy"}:
            raise ValueError(f"Unsupported discovery mode: {self.discovery_mode}")
        if self.codebook_entries and self.discovery_mode in {"open", "guided"}:
            raise ValueError("Open/context-guided discovery takes transcripts without an imported codebook. Remove the codebook or use Auto for deductive assignment.")
        ids = [entry.code_id for entry in self.codebook_entries]
        if len(ids) != len(set(ids)):
            raise ValueError("Code IDs must be unique within a codebook; review duplicate rows explicitly.")


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
    source_text: str = ""
    source_hash: str = ""


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
    source_hash: str = ""
    context_start: int = 0
    context_end: int = 0
    context_text: str = ""
    exclusion_reason: str = ""


@dataclass(frozen=True, slots=True)
class ThemeQuote:
    quote_id: str
    speaker: str
    text: str
    relevance: float
    source_line: int
    source_name: str = "Transcript"
    rationale: str = "Selected as a candidate quote for researcher review."
    source_start: int = 0
    source_end: int = 0
    memo: str = ""
    source_hash: str = ""
    context_start: int = 0
    context_end: int = 0
    context_text: str = ""


@dataclass(frozen=True, slots=True)
class Theme:
    id: str
    name: str
    color: str
    keywords: list[str]
    quote_count: int
    score: float
    quotes: list[ThemeQuote] = field(default_factory=list)
    validation: dict[str, ValidationValue] = field(default_factory=dict)
    memo: str = ""
    parent_theme_id: str = ""
    code_id: str = ""
    code_version: int = 0
    code_definition: CodebookEntry | None = None


@dataclass(frozen=True, slots=True)
class AnalysisResult:
    version: str
    document_count: int
    quote_count: int
    themes: list[Theme]
    notes: list[str]
    validation_summary: list[str] = field(default_factory=list)
    analytic_intent: AnalyticIntent = field(default_factory=AnalyticIntent)
    codebook_mode: str = "codebook_only"
    codebook_snapshot: tuple[CodebookEntry, ...] = ()
    evidence_units: tuple[QuoteUnit, ...] = ()
    run_id: str = ""
    source_hashes: dict[str, str] = field(default_factory=dict)
    source_lengths: dict[str, int] = field(default_factory=dict)
    analysis_parameters: dict[str, ValidationValue] = field(default_factory=dict)
    discovery: dict = field(default_factory=dict)



from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from .analysis_types import AnalysisResult, AnalysisSettings, QuoteUnit, Theme, ThemeQuote, TranscriptDocument
from .analysis_vectors import cosine, tfidf_vectors
from .local_embeddings import local_embedding_vectors
from .quote_units import suggestion_inputs


@dataclass(frozen=True, slots=True)
class SimilarityQuery:
    theme_id: str
    quote_id: str
    text: str
    method_note: str


@dataclass(frozen=True, slots=True)
class SimilarPassage:
    source_name: str
    speaker: str
    text: str
    source_line: int
    source_start: int
    source_end: int
    similarity: float
    query: SimilarityQuery


@dataclass(frozen=True, slots=True)
class SimilarPassageResult:
    passages: tuple[SimilarPassage, ...]
    note: str
    method_label: str = ""


def find_similar_passages(
    documents: Sequence[TranscriptDocument],
    result: AnalysisResult,
    theme_id: str,
    settings: AnalysisSettings,
    quote_id: str = "",
    limit: int = 8,
    *,
    source_names: set[str] | None = None,
) -> SimilarPassageResult:
    theme = next((item for item in result.themes if item.id == theme_id), None)
    if theme is None:
        return SimilarPassageResult((), "Select a theme before finding similar passages.")

    query_text = _query_text(theme, quote_id)
    if not query_text:
        return SimilarPassageResult((), "The selected theme has no text to use for similarity search.")

    _, units = suggestion_inputs(documents, settings)
    other_quotes = [q for t in result.themes if t.id != theme_id for q in t.quotes]
    uncoded, stored_elsewhere = [], []
    for unit in units:
        if _is_already_coded(unit, theme.quotes):
            continue
        if _is_already_coded(unit, other_quotes):
            if not quote_id:
                stored_elsewhere.append(unit)
        else:
            uncoded.append(unit)
    candidates = uncoded + stored_elsewhere
    if not candidates:
        note = ("No uncoded quote-length passages remain in the open transcripts." if quote_id else
                "No eligible passages remain outside the selected theme.")
        return SimilarPassageResult((), note)

    texts = [query_text, *(candidate.text for candidate in candidates)]
    # Keep the original query and uncoded passages as the TF-IDF reference corpus.
    vectors, note, method_label = _retrieval_vectors(texts, settings, 1 + len(uncoded))
    if len(vectors) != len(texts):
        return SimilarPassageResult((), "Similarity vectors could not be created for the open transcripts.")

    query_vector = vectors[0]
    method_note = note if source_names is None else f"{note} Results limited to {', '.join(sorted(source_names))}."
    query = SimilarityQuery(theme.id, quote_id, query_text, method_note)
    # Keep similarity on the same corpus; apply the reviewer's source scope before the display limit.
    ranked = sorted(
        (
            SimilarPassage(
                source_name=candidate.source_name,
                speaker=candidate.speaker,
                text=candidate.text,
                source_line=candidate.source_line,
                source_start=candidate.source_start,
                source_end=candidate.source_end,
                similarity=cosine(query_vector, vector),
                query=query,
            )
            for candidate, vector in zip(candidates, vectors[1:], strict=True)
            if source_names is None or candidate.source_name in source_names
        ),
        key=lambda item: (-item.similarity, item.source_name.casefold(), item.source_start),
    )
    passages = tuple(item for item in ranked if item.similarity > 0.0)[: max(0, limit)]
    if not passages:
        return SimilarPassageResult((), f"{note} No related passages received a positive similarity score.", method_label)
    return SimilarPassageResult(passages, note, method_label)


def _query_text(theme: Theme, quote_id: str) -> str:
    if quote_id:
        quote = next((item for item in theme.quotes if item.quote_id == quote_id), None)
        if quote is not None:
            return quote.text
    evidence = " ".join(quote.text for quote in theme.quotes[:3])
    return " ".join((theme.name, *theme.keywords, evidence)).strip()


def _is_already_coded(candidate: QuoteUnit, coded_quotes: Sequence[ThemeQuote]) -> bool:
    for coded in coded_quotes:
        if coded.source_name != candidate.source_name:
            continue
        if coded.source_end > coded.source_start and candidate.source_end > candidate.source_start:
            if candidate.source_start < coded.source_end and coded.source_start < candidate.source_end:
                return True
        elif coded.text.strip() == candidate.text.strip():
            return True
    return False


def _retrieval_vectors(
    texts: list[str],
    settings: AnalysisSettings,
    reference_count: int,
) -> tuple[list[dict[str, float]], str, str]:
    if settings.semantic_backend == "local_embeddings":
        embedding_result = local_embedding_vectors(texts, settings.language_mode)
        if len(embedding_result.vectors) == len(texts):
            label = embedding_result.model_name.rsplit("/", 1)[-1] or "Local embeddings"
            return embedding_result.vectors, "Local embedding cosine similarity was used for these suggestions.", label
        note, label = f"{embedding_result.note} TF-IDF similarity was used for these suggestions.", "TF-IDF fallback"
    else:
        note, label = "TF-IDF cosine similarity was used for these suggestions.", "TF-IDF"
    vectors, _idf = tfidf_vectors(texts, reference_count=reference_count)
    return vectors, note, label

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from .analysis_types import AnalysisResult, AnalysisSettings, QuoteUnit, Theme, ThemeQuote, TranscriptDocument
from .analysis_vectors import cosine, tfidf_vectors
from .local_embeddings import local_embedding_vectors
from .quote_units import extract_quote_units
from .transcript_parser import parse_transcript


@dataclass(frozen=True, slots=True)
class SimilarPassage:
    source_name: str
    speaker: str
    text: str
    source_line: int
    source_start: int
    source_end: int
    similarity: float


@dataclass(frozen=True, slots=True)
class SimilarPassageResult:
    passages: tuple[SimilarPassage, ...]
    note: str


def find_similar_passages(
    documents: Sequence[TranscriptDocument],
    result: AnalysisResult,
    theme_id: str,
    settings: AnalysisSettings,
    quote_id: str = "",
    limit: int = 8,
) -> SimilarPassageResult:
    theme = next((item for item in result.themes if item.id == theme_id), None)
    if theme is None:
        return SimilarPassageResult((), "Select a theme before finding similar passages.")

    query_text = _query_text(theme, quote_id)
    if not query_text:
        return SimilarPassageResult((), "The selected theme has no text to use for similarity search.")

    candidates = _uncoded_quote_units(documents, result, settings.min_quote_words)
    if not candidates:
        return SimilarPassageResult((), "No uncoded quote-length passages remain in the open transcripts.")

    texts = [query_text, *(candidate.text for candidate in candidates)]
    vectors, note = _retrieval_vectors(texts, settings)
    if len(vectors) != len(texts):
        return SimilarPassageResult((), "Similarity vectors could not be created for the open transcripts.")

    query_vector = vectors[0]
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
            )
            for candidate, vector in zip(candidates, vectors[1:], strict=True)
        ),
        key=lambda item: (-item.similarity, item.source_name.casefold(), item.source_start),
    )
    passages = tuple(item for item in ranked if item.similarity > 0.0)[: max(0, limit)]
    if not passages:
        return SimilarPassageResult((), f"{note} No related uncoded passages received a positive similarity score.")
    return SimilarPassageResult(passages, note)


def _query_text(theme: Theme, quote_id: str) -> str:
    if quote_id:
        quote = next((item for item in theme.quotes if item.quote_id == quote_id), None)
        if quote is not None:
            return quote.text
    evidence = " ".join(quote.text for quote in theme.quotes[:3])
    return " ".join((theme.name, *theme.keywords, evidence)).strip()


def _uncoded_quote_units(
    documents: Sequence[TranscriptDocument],
    result: AnalysisResult,
    min_quote_words: int,
) -> list[QuoteUnit]:
    coded_quotes = [quote for theme in result.themes for quote in theme.quotes]
    candidates = [
        quote
        for document in documents
        if document.text.strip()
        for quote in extract_quote_units(
            parse_transcript(document.text, source_name=document.name or "Transcript"),
            min_quote_words=min_quote_words,
        )
    ]
    return [candidate for candidate in candidates if not _is_already_coded(candidate, coded_quotes)]


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
) -> tuple[list[dict[str, float]], str]:
    if settings.semantic_backend == "local_embeddings":
        embedding_result = local_embedding_vectors(texts, settings.language_mode)
        if len(embedding_result.vectors) == len(texts):
            return embedding_result.vectors, "Local embedding cosine similarity was used for these suggestions."
        vectors, _idf = tfidf_vectors(texts)
        return vectors, f"{embedding_result.note} TF-IDF similarity was used for these suggestions."
    vectors, _idf = tfidf_vectors(texts)
    return vectors, "TF-IDF cosine similarity was used for these suggestions."

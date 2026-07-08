from __future__ import annotations

from typing import Sequence

from . import __version__
from .analysis_focus import (
    build_focus_profile as _build_focus_profile,
    central_theme_alignment as _central_theme_alignment,
    focus_augmented_text as _focus_augmented_text,
    focus_terms as _focus_terms,
    weighted_bm25_score as _weighted_bm25_score,
)
from .analysis_labeling import (
    is_low_value_term as _is_low_value_term,
    ranked_keyword_terms as _ranked_keyword_terms,
)
from .analysis_theme_builder import (
    build_themes as _build_themes,
    _limit_theme_quotes,
    _matched_terms,
    sort_themes as _sort_themes,
    theme_color as _theme_color,
    validation_summary as _validation_summary,
)
from .analysis_types import (
    AnalysisResult,
    AnalysisSettings,
    CodebookEntry,
    FocusProfile,
    QuoteUnit,
    Theme,
    ThemeQuote,
    TranscriptDocument,
    TranscriptSegment,
    ValidationValue,
)
from .analysis_vectors import (
    cluster_quotes as _cluster_quotes,
    terms_for_vector as _terms_for_vector,
    tfidf_vectors as _tfidf_vectors,
)
from .local_embeddings import local_embedding_vectors as _local_embedding_vectors
from .quote_units import extract_quote_units, speaker_stopwords as build_speaker_stopwords
from .transcript_parser import parse_transcript


def analyze_transcript(text: str, settings: AnalysisSettings | None = None) -> AnalysisResult:
    return analyze_documents([TranscriptDocument(name="Transcript", text=text)], settings)


def analyze_documents(
    documents: Sequence[TranscriptDocument],
    settings: AnalysisSettings | None = None,
) -> AnalysisResult:
    settings = settings or AnalysisSettings()
    transcript_documents = [document for document in documents if document.text.strip()]
    segments = [
        segment
        for document in transcript_documents
        for segment in parse_transcript(document.text, source_name=document.name or "Transcript")
    ]
    quotes = extract_quote_units(segments, min_quote_words=settings.min_quote_words)
    notes = [
        "Researcher review required: generated themes are suggested analytic groupings, not final qualitative findings.",
        "Use exported quotes as evidence candidates and verify meaning against the full transcript context.",
    ]
    validation_summary = _validation_summary(settings)
    if settings.central_theme.strip():
        notes.append(
            f"Central theme priority used: {settings.central_theme.strip()}."
        )
    if settings.codebook_entries:
        notes.append(
            f"Imported codebook used for deductive quote matching: {len(settings.codebook_entries)} themes."
        )

    if not quotes:
        return AnalysisResult(
            version=__version__,
            document_count=len(transcript_documents),
            quote_count=0,
            themes=[],
            notes=notes + ["No quote-length text units were found."],
            validation_summary=validation_summary,
        )

    speaker_stopwords = build_speaker_stopwords(segments)
    if settings.codebook_entries:
        from .codebook_match import build_codebook_themes

        themes = build_codebook_themes(quotes, settings, speaker_stopwords)
        if themes:
            return AnalysisResult(
                version=__version__,
                document_count=len(transcript_documents),
                quote_count=len(quotes),
                themes=themes,
                notes=notes,
                validation_summary=validation_summary,
            )
        notes.append("No codebook entries matched quote-length text units; automatic theme discovery was used.")

    focus_profile = _build_focus_profile(quotes, settings.central_theme, speaker_stopwords)
    vector_texts = [_focus_augmented_text(quote.text, settings, focus_profile) for quote in quotes]
    vectors, idf = _tfidf_vectors(
        vector_texts,
        speaker_stopwords,
    )
    if settings.semantic_backend == "local_embeddings":
        embedding_result = _local_embedding_vectors(vector_texts, settings.language_mode)
        notes.append(embedding_result.note)
        if embedding_result.vectors:
            vectors = embedding_result.vectors
    clusters, clustering_note = _cluster_quotes(
        vectors,
        settings.theme_count,
        settings.min_theme_size,
        settings.agglomerative_limit,
    )
    if clustering_note:
        notes.append(clustering_note)
    themes = _build_themes(quotes, vectors, clusters, idf, settings, focus_profile, speaker_stopwords)

    return AnalysisResult(
        version=__version__,
        document_count=len(transcript_documents),
        quote_count=len(quotes),
        themes=_sort_themes(themes, settings),
        notes=notes,
        validation_summary=validation_summary,
    )

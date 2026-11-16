from __future__ import annotations

from typing import Sequence
from dataclasses import asdict, replace
from hashlib import sha256
import json
from uuid import uuid4

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
    AnalyticIntent,
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
from .quote_units import extract_evidence_units, extract_quote_units, speaker_stopwords as build_speaker_stopwords
from .transcript_parser import parse_transcript


def analyze_transcript(text: str, settings: AnalysisSettings | None = None) -> AnalysisResult:
    return analyze_documents([TranscriptDocument(name="Transcript", text=text)], settings)


def analyze_documents(
    documents: Sequence[TranscriptDocument],
    settings: AnalysisSettings | None = None,
) -> AnalysisResult:
    settings = settings or AnalysisSettings()
    transcript_documents = [document for document in documents if document.text.strip()]
    source_names = [document.name or "Transcript" for document in transcript_documents]
    if len(source_names) != len(set(source_names)):
        raise ValueError("Transcript source names must be unique; rename duplicate inputs before analysis.")
    if not settings.codebook_entries and settings.discovery_mode != "legacy":
        from .discovery import discover_documents
        return discover_documents(transcript_documents, settings)
    segments = [
        segment
        for document in transcript_documents
        for segment in parse_transcript(document.text, source_name=document.name or "Transcript")
    ]
    evidence_units = tuple(extract_evidence_units(segments, min_quote_words=settings.min_quote_words))
    quotes = [unit for unit in evidence_units if not unit.exclusion_reason]
    analyzed_quote_count = len(quotes)
    notes = [
        "Researcher review required: generated themes are suggested analytic groupings, not final qualitative findings.",
        "Use exported quotes as evidence candidates and verify meaning against the full transcript context.",
    ]
    validation_summary = _validation_summary(settings)
    excluded_count = len(evidence_units) - len(quotes)
    notes.append(f"Evidence review retains {excluded_count} units excluded from automatic suggestions with reasons and context.")
    notes.append("RQ, theoretical lens, depth, and coding rules are recorded for review; this run uses lexical matching or similarity grouping, not a trained interpretive classifier.")
    parameters = {
        key: value for key, value in asdict(settings).items()
        if isinstance(value, (str, int, float))
    }
    parameters["unitization"] = "exact_source_spans_v1"
    parameters["codebook_sha256"] = sha256(json.dumps(
        [asdict(entry) for entry in settings.codebook_entries], ensure_ascii=False, sort_keys=True,
    ).encode("utf-8")).hexdigest()
    source_hashes = {
        document.name or "Transcript": sha256(document.text.encode("utf-8")).hexdigest()
        for document in transcript_documents
    }

    def finish(themes: list[Theme]) -> AnalysisResult:
        return AnalysisResult(
            version=__version__, document_count=len(transcript_documents),
            quote_count=analyzed_quote_count, themes=themes, notes=notes,
            validation_summary=validation_summary, analytic_intent=settings.analytic_intent,
            codebook_mode=settings.codebook_mode, codebook_snapshot=settings.codebook_entries,
            evidence_units=evidence_units, run_id=uuid4().hex, source_hashes=source_hashes,
            source_lengths={document.name or "Transcript": len(document.text) for document in transcript_documents},
            analysis_parameters=parameters,
        )
    if settings.central_theme.strip():
        notes.append(
            f"Central theme priority used: {settings.central_theme.strip()}."
        )
    if settings.codebook_entries:
        notes.append(
            f"Imported codebook used for deductive quote matching: {len(settings.codebook_entries)} themes."
        )

    if not quotes:
        notes.append("No quote-length text units were found. Retained evidence is available for manual review.")
        return finish([])

    speaker_stopwords = build_speaker_stopwords(segments)
    codebook_themes: list[Theme] = []
    if settings.codebook_entries:
        from .codebook_match import build_codebook_themes

        complete_matches = build_codebook_themes(quotes, replace(settings, quotes_per_theme=0), speaker_stopwords)
        matched_ids = {quote.quote_id for theme in complete_matches for quote in theme.quotes}
        codebook_themes = [
            replace(theme, quotes=_limit_theme_quotes(theme.quotes, settings.quotes_per_theme))
            for theme in complete_matches
        ]
        notes.append("Codebook scores measure lexical relevance, not calibrated probabilities of correct coding. Only exact negative examples are excluded automatically; other criteria require review.")
        if settings.codebook_mode == "codebook_only":
            if not codebook_themes:
                notes.append("No codebook entries matched; codebook-only mode retained the no-match outcome.")
            return finish(codebook_themes)
        if settings.codebook_mode == "legacy":
            notes.append("Legacy codebook workflow retained from project settings.")
            if codebook_themes:
                return finish(codebook_themes)
            notes.append("No codebook entries matched quote-length text units; automatic theme discovery was used.")
        else:
            quotes = [quote for quote in quotes if quote.id not in matched_ids]
            notes.append(f"Hybrid review: {len(quotes)} unmatched units considered for separate provisional discovery suggestions. Lexical matches can still hide novel concepts; review the full evidence list.")
            if not quotes:
                return finish(codebook_themes)

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
    return finish([*codebook_themes, *_sort_themes(themes, settings)])

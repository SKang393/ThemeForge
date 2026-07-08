from __future__ import annotations

from collections import Counter
import math
from statistics import mean

from .analysis import (
    AnalysisSettings,
    CodebookEntry,
    FocusProfile,
    QuoteUnit,
    Theme,
    ThemeQuote,
    ValidationValue,
    _central_theme_alignment,
    _is_low_value_term,
    _limit_theme_quotes,
    _matched_terms,
    _ranked_keyword_terms,
    _terms_for_vector,
    _theme_color,
    _weighted_bm25_score,
)


def build_codebook_themes(
    quotes: list[QuoteUnit],
    settings: AnalysisSettings,
    speaker_stopwords: set[str],
) -> list[Theme]:
    term_documents = [_terms_for_vector(quote.text, speaker_stopwords) for quote in quotes]
    document_frequency: Counter[str] = Counter()
    for terms in term_documents:
        document_frequency.update(set(terms))
    profile = FocusProfile(
        central_theme=settings.central_theme,
        direct_terms=(),
        related_weights={},
        document_frequency=dict(document_frequency),
        document_count=len(term_documents),
        average_document_length=max(1.0, mean(len(terms) for terms in term_documents) if term_documents else 1.0),
    )
    themes: list[Theme] = []
    for index, entry in enumerate(settings.codebook_entries, start=1):
        query_terms = _terms_for_vector(" ".join([entry.name, entry.description, *entry.examples]), speaker_stopwords)
        query_weights = {term: 1.4 if "_" in term else 1.0 for term in query_terms if not _is_low_value_term(term)}
        ranked_quotes = _rank_codebook_quotes(quotes, term_documents, profile, query_weights, entry, settings)
        if not ranked_quotes:
            continue
        themes.append(
            Theme(
                id=f"C{index:02d}",
                name=entry.name,
                color=_theme_color(index),
                keywords=_codebook_keywords(entry, speaker_stopwords),
                quote_count=len(ranked_quotes),
                score=round(mean(quote.relevance for quote in ranked_quotes), 3),
                quotes=_limit_theme_quotes(ranked_quotes, settings.quotes_per_theme),
                validation=_codebook_validation(ranked_quotes),
            )
        )
    return themes


def _rank_codebook_quotes(
    quotes: list[QuoteUnit],
    term_documents: list[list[str]],
    profile: FocusProfile,
    query_weights: dict[str, float],
    entry: CodebookEntry,
    settings: AnalysisSettings,
) -> list[ThemeQuote]:
    ranked: list[ThemeQuote] = []
    for quote, terms in zip(quotes, term_documents, strict=True):
        raw_score = _weighted_bm25_score(Counter(terms), len(terms), profile, query_weights)
        relevance = round(min(1.0, 1.0 - math.exp(-raw_score / 1.8)), 3)
        if relevance <= 0:
            continue
        ranked.append(
            ThemeQuote(
                quote_id=quote.id,
                speaker=quote.speaker,
                text=quote.text,
                relevance=relevance,
                source_line=quote.source_line,
                source_name=quote.source_name,
                rationale=_codebook_rationale(entry, quote.text, relevance, settings.central_theme),
                source_start=quote.source_start,
                source_end=quote.source_end,
            )
        )
    return sorted(ranked, key=lambda quote: (-quote.relevance, quote.source_name, quote.source_line, quote.quote_id))


def _codebook_keywords(entry: CodebookEntry, speaker_stopwords: set[str]) -> list[str]:
    terms = Counter(_terms_for_vector(" ".join([entry.name, entry.description]), speaker_stopwords))
    return _ranked_keyword_terms(
        {term.replace("_", " "): count for term, count in terms.items() if not _is_low_value_term(term)},
        5,
    )


def _codebook_validation(quotes: list[ThemeQuote]) -> dict[str, ValidationValue]:
    sources = {quote.source_name for quote in quotes}
    speakers = {quote.speaker for quote in quotes}
    return {
        "evidence_count": len(quotes),
        "displayed_quote_count": len(quotes),
        "source_count": len(sources),
        "speaker_count": len(speakers),
        "central_theme_alignment": 0.0,
        "review_status": "Codebook match requires researcher review",
    }


def _codebook_rationale(
    entry: CodebookEntry,
    quote_text: str,
    relevance: float,
    central_theme: str,
) -> str:
    matched_terms = _matched_terms(quote_text, [entry.name, entry.description, *entry.examples])
    parts = [
        f"Codebook theme: {entry.name}.",
        f"BM25-style match score: {relevance:.3f}.",
    ]
    if matched_terms:
        parts.append(f"Quote matches codebook signals: {', '.join(matched_terms[:3])}.")
    if central_theme.strip():
        parts.append(f"Shared focus alignment: {round(_central_theme_alignment(quote_text, central_theme) * 100):.0f}%.")
    parts.append("Researcher review is required before treating this as final coding.")
    return " ".join(parts)

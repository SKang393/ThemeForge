from __future__ import annotations

from collections import Counter
from dataclasses import replace
import math
import re
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
from .text_utils import normalize_space


_LABEL = r"(include|exclude)[ \t]*:"
# Text codebook imports can join definition lines with spaces.
_DEFINITION_LABEL = re.compile(r"(?:^|(?<=[\s.;!?]))" + _LABEL, re.IGNORECASE)
# Examples can quote ordinary speech such as "we listed what to exclude: ...".
_EXAMPLE_LABEL = re.compile(r"(?:^|(?<=[\r\n.;!?]))[^\S\r\n]*" + _LABEL, re.IGNORECASE)


def _positive_text(text: str, label_pattern: re.Pattern[str]) -> str:
    sections = label_pattern.split(text)
    if len(sections) == 1:
        return text
    positive = [sections[0]]
    for label, body in zip(sections[1::2], sections[2::2], strict=True):
        if label.lower() == "include":
            positive.append(body)
    return " ".join(part.strip() for part in positive if part.strip())


def _search_entry(entry: CodebookEntry) -> CodebookEntry:
    examples = tuple(text for example in entry.examples if (text := _positive_text(example, _EXAMPLE_LABEL)))
    return replace(entry, description=_positive_text(entry.description, _DEFINITION_LABEL), examples=examples)


def comparison_entry_snapshot(entry: CodebookEntry) -> str:
    def sections(text: str, labels: re.Pattern[str]) -> str:
        # Split first: in examples a newline can turn literal words into an instruction.
        # Each normalized part is newline-free, so separators preserve every boundary.
        return "\n".join(normalize_space(part) for part in labels.split(text))

    return replace(entry, description=sections(entry.description, _DEFINITION_LABEL),
                   examples=tuple(sections(e, _EXAMPLE_LABEL) for e in entry.examples)).snapshot()


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
        search_entry = _search_entry(entry)
        query_terms = _terms_for_vector(
            " ".join([search_entry.name, search_entry.description, *search_entry.examples]), speaker_stopwords,
        )
        query_weights = {term: 1.4 if "_" in term else 1.0 for term in query_terms if not _is_low_value_term(term)}
        ranked_quotes = _rank_codebook_quotes(quotes, term_documents, profile, query_weights, search_entry, settings)
        if not ranked_quotes:
            continue
        themes.append(
            Theme(
                id=f"C{index:02d}",
                name=entry.name,
                color=_theme_color(index),
                keywords=_codebook_keywords(search_entry, speaker_stopwords),
                quote_count=len(ranked_quotes),
                score=round(mean(quote.relevance for quote in ranked_quotes), 3),
                quotes=_limit_theme_quotes(ranked_quotes, settings.quotes_per_theme),
                validation={**_codebook_validation(ranked_quotes),
                            "generated_from_codebook_entry": entry.snapshot()},
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

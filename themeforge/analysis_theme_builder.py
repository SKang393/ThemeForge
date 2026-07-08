from __future__ import annotations

from collections import defaultdict
from statistics import mean
from typing import Iterable, Sequence

from .analysis_constants import THEME_COLORS
from .analysis_focus import central_theme_alignment
from .analysis_labeling import (
    ctfidf_keywords_for_clusters,
    keywords_for_cluster,
    label_theme,
    prioritize_keywords,
)
from .analysis_types import (
    AnalysisSettings,
    FocusProfile,
    QuoteUnit,
    Theme,
    ThemeQuote,
    ValidationValue,
)
from .analysis_vectors import centroid, cosine
from .text_utils import normalize_for_match, tokenize


def build_themes(
    quotes: list[QuoteUnit],
    vectors: list[dict[str, float]],
    clusters: list[list[int]],
    idf: dict[str, float],
    settings: AnalysisSettings,
    focus_profile: FocusProfile,
    speaker_stopwords: set[str],
) -> list[Theme]:
    themes: list[Theme] = []
    ctfidf_keywords = ctfidf_keywords_for_clusters(quotes, clusters, speaker_stopwords, 5)

    for theme_number, cluster in enumerate(clusters, start=1):
        cluster_centroid = centroid(vectors[index] for index in cluster)
        cluster_text = " ".join(quotes[index].text for index in cluster)
        keywords = ctfidf_keywords[theme_number - 1]
        if not keywords:
            keywords = keywords_for_cluster(quotes, cluster, idf, 5, speaker_stopwords)
        keywords = prioritize_keywords(keywords, settings.central_theme, cluster_text)
        label = label_theme(keywords, settings.central_theme)
        ranked_quotes = sorted(
            (
                ThemeQuote(
                    quote_id=quotes[index].id,
                    speaker=quotes[index].speaker,
                    text=quotes[index].text,
                    relevance=round(cosine(vectors[index], cluster_centroid), 3),
                    source_line=quotes[index].source_line,
                    source_name=quotes[index].source_name,
                    rationale=quote_rationale(
                        label,
                        keywords,
                        quotes[index].text,
                        settings.central_theme,
                        round(cosine(vectors[index], cluster_centroid), 3),
                        focus_profile,
                    ),
                    source_start=quotes[index].source_start,
                    source_end=quotes[index].source_end,
                )
                for index in cluster
            ),
            key=lambda quote: (
                -quote_sort_score(quote, settings.central_theme, focus_profile),
                quote.source_name,
                quote.source_line,
                quote.quote_id,
            ),
        )
        ranked_quotes = _limit_theme_quotes(ranked_quotes, settings.quotes_per_theme)

        cohesion_scores = [cosine(vectors[index], cluster_centroid) for index in cluster]
        cohesion = mean(cohesion_scores) if cohesion_scores else 0.0
        themes.append(
            Theme(
                id=f"T{theme_number:02d}",
                name=label,
                color=theme_color(theme_number),
                keywords=keywords,
                quote_count=len(cluster),
                score=round(max(0.0, min(1.0, cohesion)), 3),
                quotes=ranked_quotes,
                validation=theme_validation(ranked_quotes, len(cluster), settings, focus_profile),
            )
        )

    return _merge_themes_by_name(themes, settings, focus_profile)


def validation_summary(settings: AnalysisSettings) -> list[str]:
    lines = [
        "Researcher review required: suggested codes and themes must be checked against full transcript context.",
        "Validation checks include evidence count, source coverage, speaker coverage, central-theme alignment, and an explicit review status.",
        "Research basis: thematic analysis is treated as an iterative researcher-led process with traceable quote evidence, not an automated finding generator.",
    ]
    if settings.central_theme.strip():
        lines.append(
            f"Central theme applied as a deductive priority: {settings.central_theme.strip()}."
        )
    return lines


def theme_validation(
    quotes: list[ThemeQuote],
    evidence_count: int,
    settings: AnalysisSettings,
    focus_profile: FocusProfile | None = None,
) -> dict[str, ValidationValue]:
    sources = {quote.source_name for quote in quotes}
    speakers = {quote.speaker for quote in quotes}
    return {
        "evidence_count": evidence_count,
        "displayed_quote_count": len(quotes),
        "source_count": len(sources),
        "speaker_count": len(speakers),
        "central_theme_alignment": round(
            central_theme_alignment(
                " ".join(quote.text for quote in quotes),
                settings.central_theme,
                focus_profile,
            ),
            3,
        ),
        "review_status": "Researcher review required",
    }


def sort_themes(themes: list[Theme], settings: AnalysisSettings) -> list[Theme]:
    if not settings.central_theme.strip():
        return sorted(themes, key=lambda theme: (-theme.quote_count, -theme.score, theme.name))
    return sorted(
        themes,
        key=lambda theme: (
            -_theme_focus_score(theme, settings.central_theme),
            -theme.quote_count,
            -theme.score,
            theme.name,
        ),
    )


def quote_sort_score(
    quote: ThemeQuote,
    central_theme: str,
    focus_profile: FocusProfile | None = None,
) -> float:
    return quote.relevance + central_theme_alignment(quote.text, central_theme, focus_profile)


def quote_rationale(
    theme_name: str,
    keywords: Sequence[str],
    quote_text: str,
    central_theme: str,
    relevance: float,
    focus_profile: FocusProfile | None = None,
) -> str:
    matched_keywords = _matched_terms(quote_text, keywords)
    focus_alignment = central_theme_alignment(quote_text, central_theme, focus_profile)
    parts = [
        f"Theme candidate: {theme_name}.",
        f"Similarity score: {relevance:.3f}.",
    ]
    if matched_keywords:
        parts.append(f"Quote shares theme signals: {', '.join(matched_keywords[:5])}.")
    else:
        parts.append("Quote was grouped by contextual similarity to other quotes in this theme.")
    if central_theme.strip():
        parts.append(f"Shared focus alignment: {round(focus_alignment * 100):.0f}%.")
    parts.append(
        "Use this as an audit-trail reason for review; final coding and interpretation remain researcher decisions."
    )
    return " ".join(parts)


def theme_color(theme_number: int) -> str:
    return THEME_COLORS[(theme_number - 1) % len(THEME_COLORS)]


def _merge_themes_by_name(
    themes: list[Theme],
    settings: AnalysisSettings,
    focus_profile: FocusProfile,
) -> list[Theme]:
    grouped: dict[str, list[Theme]] = defaultdict(list)
    for theme in themes:
        grouped[theme.name].append(theme)

    merged: list[Theme] = []
    for same_name_themes in grouped.values():
        if len(same_name_themes) == 1:
            merged.append(same_name_themes[0])
            continue

        primary = same_name_themes[0]
        evidence_count = sum(theme.quote_count for theme in same_name_themes)
        keywords = _unique_terms(keyword for theme in same_name_themes for keyword in theme.keywords)
        quotes = _unique_quotes(quote for theme in same_name_themes for quote in theme.quotes)
        quotes = sorted(
            quotes,
            key=lambda quote: (
                -quote_sort_score(quote, settings.central_theme, focus_profile),
                quote.source_name,
                quote.source_line,
                quote.quote_id,
            ),
        )
        quotes = _limit_theme_quotes(quotes, settings.quotes_per_theme)
        weighted_score = sum(theme.score * theme.quote_count for theme in same_name_themes) / max(1, evidence_count)
        merged.append(
            Theme(
                id=primary.id,
                name=primary.name,
                color=primary.color,
                keywords=keywords[:5],
                quote_count=evidence_count,
                score=round(weighted_score, 3),
                quotes=quotes,
                validation=theme_validation(quotes, evidence_count, settings, focus_profile),
            )
        )

    return sorted(merged, key=lambda theme: (-theme.quote_count, -theme.score, theme.name))


def _unique_terms(terms: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for term in terms:
        if term in seen:
            continue
        seen.add(term)
        unique.append(term)
    return unique


def _unique_quotes(quotes: Iterable[ThemeQuote]) -> list[ThemeQuote]:
    seen: set[tuple[str, str, int, str]] = set()
    unique: list[ThemeQuote] = []
    for quote in quotes:
        key = (quote.quote_id, quote.source_name, quote.source_line, quote.text)
        if key in seen:
            continue
        seen.add(key)
        unique.append(quote)
    return unique


def _limit_theme_quotes(quotes: list[ThemeQuote], quotes_per_theme: int) -> list[ThemeQuote]:
    if quotes_per_theme <= 0:
        return quotes
    return quotes[:quotes_per_theme]


def _theme_focus_score(theme: Theme, central_theme: str) -> float:
    if central_theme.strip() and "central_theme_alignment" in theme.validation:
        try:
            return float(theme.validation.get("central_theme_alignment", 0))
        except (TypeError, ValueError):
            return 0.0
    text = " ".join(
        [theme.name, *theme.keywords, *(quote.text for quote in theme.quotes)]
    )
    return central_theme_alignment(text, central_theme)


def _matched_terms(text: str, terms: Sequence[str]) -> list[str]:
    normalized_text = normalize_for_match(text)
    text_tokens = set(tokenize(text))
    matched: list[str] = []
    for term in terms:
        normalized_term = normalize_for_match(term)
        if not normalized_term:
            continue
        if normalized_term in normalized_text or normalized_term in text_tokens:
            matched.append(term)
    return matched

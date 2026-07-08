from __future__ import annotations

from collections import Counter, defaultdict
import math
from statistics import mean
from typing import Sequence

from .analysis_constants import GENERIC_THEME_TERMS, STOPWORDS
from .analysis_types import AnalysisSettings, FocusProfile, QuoteUnit
from .analysis_vectors import terms_for_vector
from .text_utils import normalize_for_match, tokenize


def build_focus_profile(
    quotes: list[QuoteUnit],
    central_theme: str,
    extra_stopwords: set[str] | None = None,
) -> FocusProfile:
    direct_terms = tuple(focus_terms(central_theme))
    term_documents = [terms_for_vector(quote.text, extra_stopwords) for quote in quotes]
    document_frequency: Counter[str] = Counter()
    for terms in term_documents:
        document_frequency.update(set(terms))

    document_count = len(term_documents)
    average_document_length = mean(len(terms) for terms in term_documents) if term_documents else 1.0
    if not direct_terms:
        return FocusProfile(
            central_theme=central_theme,
            direct_terms=(),
            related_weights={},
            document_frequency=dict(document_frequency),
            document_count=document_count,
            average_document_length=max(1.0, average_document_length),
        )

    focus_indexes = [
        index
        for index, quote in enumerate(quotes)
        if _direct_focus_match(quote.text, direct_terms)
    ]
    related_scores: defaultdict[str, float] = defaultdict(float)
    for index in focus_indexes:
        counts = Counter(term_documents[index])
        for term, count in counts.items():
            if term in direct_terms or _is_low_value_term(term):
                continue
            phrase_boost = 1.35 if "_" in term else 1.0
            related_scores[term] += math.sqrt(count) * _bm25_idf(
                document_frequency.get(term, 0),
                document_count,
            ) * phrase_boost

    ranked_tokens = [
        item
        for item in sorted(related_scores.items(), key=lambda item: (-item[1], item[0]))
        if "_" not in item[0]
    ][:14]
    ranked_phrases = [
        item
        for item in sorted(related_scores.items(), key=lambda item: (-item[1], item[0]))
        if "_" in item[0]
    ][:12]
    ranked_related = ranked_tokens + ranked_phrases
    max_score = max((score for _term, score in ranked_related), default=1.0)
    related_weights = {
        term: round(0.65 * (score / max_score), 4)
        for term, score in ranked_related
        if score > 0
    }
    return FocusProfile(
        central_theme=central_theme,
        direct_terms=direct_terms,
        related_weights=related_weights,
        document_frequency=dict(document_frequency),
        document_count=document_count,
        average_document_length=max(1.0, average_document_length),
    )


def _direct_focus_match(text: str, direct_terms: Sequence[str]) -> bool:
    normalized_text = normalize_for_match(text)
    text_terms = set(terms_for_vector(text))
    for term in direct_terms:
        normalized_term = normalize_for_match(term)
        if normalized_term and (normalized_term in normalized_text or term in text_terms):
            return True
    return False


def _contextual_focus_alignment(text: str, focus_profile: FocusProfile) -> float:
    terms = terms_for_vector(text)
    if not terms:
        return 0.0
    counts = Counter(terms)
    direct_weights = {term: 1.0 for term in focus_profile.direct_terms}
    direct_score = weighted_bm25_score(counts, len(terms), focus_profile, direct_weights)
    related_score = weighted_bm25_score(counts, len(terms), focus_profile, focus_profile.related_weights)
    direct_hits = sum(1 for term in focus_profile.direct_terms if counts.get(term, 0) > 0)
    direct_alignment = max(
        1.0 - math.exp(-direct_score),
        direct_hits / max(1, len(focus_profile.direct_terms)),
    )
    related_alignment = 1.0 - math.exp(-related_score / 0.65)
    return round(min(1.0, (direct_alignment * 0.72) + (related_alignment * 0.55)), 3)


def weighted_bm25_score(
    counts: Counter[str],
    document_length: int,
    focus_profile: FocusProfile,
    query_weights: dict[str, float],
) -> float:
    if not query_weights:
        return 0.0
    k1 = 1.2
    b = 0.75
    average_length = max(1.0, focus_profile.average_document_length)
    length_norm = k1 * (1 - b + b * (document_length / average_length))
    score = 0.0
    for term, weight in query_weights.items():
        frequency = counts.get(term, 0)
        if frequency <= 0:
            continue
        idf = _bm25_idf(
            focus_profile.document_frequency.get(term, 0),
            focus_profile.document_count,
        )
        score += weight * idf * ((frequency * (k1 + 1)) / (frequency + length_norm))
    return score


def _bm25_idf(document_frequency: int, document_count: int) -> float:
    return max(0.0, math.log(1 + ((document_count - document_frequency + 0.5) / (document_frequency + 0.5))))


def _is_low_value_term(term: str) -> bool:
    normalized = term.replace("_", " ").lower().strip()
    if not normalized or len(normalized) < 3:
        return True
    words = [word for word in normalized.split() if word]
    if not words:
        return True
    if len(words) > 1 and len(set(words)) == 1:
        return True
    if all(word in STOPWORDS or word in GENERIC_THEME_TERMS for word in words):
        return True
    return len(words) == 1 and words[0] in GENERIC_THEME_TERMS



def central_theme_alignment(
    text: str,
    central_theme: str,
    focus_profile: FocusProfile | None = None,
) -> float:
    terms = focus_terms(central_theme)
    if not terms:
        return 0.0

    if focus_profile is not None and focus_profile.direct_terms:
        return _contextual_focus_alignment(text, focus_profile)

    normalized_text = normalize_for_match(text)
    text_tokens = set(tokenize(text))
    score = 0.0
    for term in terms:
        normalized_term = normalize_for_match(term)
        if not normalized_term:
            continue
        if normalized_term in normalized_text:
            score += 1.0
        elif term in text_tokens:
            score += 0.7
    return min(1.0, score / max(1, len(terms)))


def focus_augmented_text(
    text: str,
    settings: AnalysisSettings,
    focus_profile: FocusProfile | None = None,
) -> str:
    central_theme = settings.central_theme.strip()
    if not central_theme:
        return text
    alignment = central_theme_alignment(text, central_theme, focus_profile)
    if alignment <= 0:
        return text
    return " ".join([text, central_theme, central_theme])


def focus_terms(central_theme: str) -> list[str]:
    return tokenize(central_theme)



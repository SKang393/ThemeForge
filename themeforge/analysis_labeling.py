from __future__ import annotations

from collections import Counter, defaultdict
import math

from .analysis_constants import (
    GENERIC_THEME_TERMS,
    STOPWORDS,
    STRONG_LABEL_HINTS,
    THEME_HINTS,
)
from .analysis_focus import central_theme_alignment, focus_terms
from .analysis_types import QuoteUnit
from .analysis_vectors import terms_for_vector
from .text_utils import tokenize


def keywords_for_cluster(
    quotes: list[QuoteUnit],
    cluster: list[int],
    idf: dict[str, float],
    limit: int,
    extra_stopwords: set[str] | None = None,
) -> list[str]:
    scores: defaultdict[str, float] = defaultdict(float)
    cluster_text = " ".join(quotes[index].text for index in cluster)
    tokens = tokenize(cluster_text, extra_stopwords=extra_stopwords)

    for token, count in Counter(tokens).items():
        if is_low_value_term(token):
            continue
        scores[token] += count * idf.get(token, 1.0)
        if token in THEME_HINTS:
            scores[token] += 4.0 * count

    for phrase in _candidate_phrases(tokens):
        if is_low_value_term(phrase):
            continue
        scores[phrase] += 1.75 * idf.get(phrase.replace(" ", "_"), 1.0)

    ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    keywords: list[str] = []
    for term, _score in ranked:
        display = term.replace("_", " ")
        if len(display) < 3 or display in keywords or is_low_value_term(display):
            continue
        keywords.append(display)
        if len(keywords) >= limit:
            break

    return keywords


def ctfidf_keywords_for_clusters(
    quotes: list[QuoteUnit],
    clusters: list[list[int]],
    extra_stopwords: set[str],
    limit: int,
) -> list[list[str]]:
    cluster_terms = [
        terms_for_vector(" ".join(quotes[index].text for index in cluster), extra_stopwords)
        for cluster in clusters
    ]
    document_frequency: Counter[str] = Counter()
    for terms in cluster_terms:
        document_frequency.update(set(terms))

    cluster_count = max(1, len(cluster_terms))
    ranked_keywords: list[list[str]] = []
    for terms in cluster_terms:
        counts = Counter(terms)
        total_terms = sum(counts.values()) or 1
        scores: dict[str, float] = {}
        for term, count in counts.items():
            display = term.replace("_", " ")
            if is_low_value_term(display):
                continue
            idf = math.log((1 + cluster_count) / (1 + document_frequency[term])) + 1
            phrase_boost = 1.8 if "_" in term else 1.0
            hint_boost = 1.4 if set(display.split()).intersection(STRONG_LABEL_HINTS) else 1.0
            scores[display] = (count / total_terms) * idf * phrase_boost * hint_boost
        ranked_keywords.append(ranked_keyword_terms(scores, limit))
    return ranked_keywords


def ranked_keyword_terms(scores: dict[str, float], limit: int) -> list[str]:
    ranked = sorted(
        scores.items(),
        key=lambda item: (-item[1], -item[0].count(" "), item[0]),
    )
    phrases = [term for term, _score in ranked if " " in term][:3]
    tokens = [term for term, _score in ranked if " " not in term]
    keywords = phrases + [term for term in tokens if term not in phrases]
    return keywords[:limit]


def prioritize_keywords(keywords: list[str], central_theme: str, text: str) -> list[str]:
    terms = focus_terms(central_theme)
    if not terms or central_theme_alignment(text, central_theme) <= 0:
        return keywords

    prioritized: list[str] = list(keywords)
    for term in terms:
        if term not in prioritized and not is_low_value_term(term):
            prioritized.append(term)

    return prioritized[:5]


def label_theme(keywords: list[str], central_theme: str = "") -> str:
    terms = set(focus_terms(central_theme))
    if terms:
        for keyword in keywords:
            words = [word for word in keyword.replace("_", " ").split() if word not in STOPWORDS]
            if len(words) >= 2 and terms.intersection(words) and not is_low_value_term(keyword):
                return " ".join(word.capitalize() for word in words[:5])

    if not keywords:
        return "Emerging Theme"

    for keyword in keywords:
        words = [word for word in keyword.replace("_", " ").split() if word not in STOPWORDS]
        if len(words) >= 3 and not is_low_value_term(keyword):
            return " ".join(word.capitalize() for word in words[:5])

    for keyword in keywords[:5]:
        if keyword in STRONG_LABEL_HINTS:
            return THEME_HINTS[keyword]

    for keyword in keywords:
        words = [word for word in keyword.replace("_", " ").split() if word not in STOPWORDS]
        if len(words) >= 2 and not is_low_value_term(keyword):
            return " ".join(word.capitalize() for word in words[:5])

    for keyword in keywords:
        for token in keyword.split():
            if token in THEME_HINTS and token in STRONG_LABEL_HINTS:
                return THEME_HINTS[token]

    title_words = []
    for word in keywords[0].replace("_", " ").split():
        if word not in STOPWORDS:
            title_words.append(word.capitalize())

    return " ".join(title_words[:5]) or "Emerging Theme"


def is_low_value_term(term: str) -> bool:
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


def _candidate_phrases(tokens: list[str]) -> list[str]:
    phrases = []
    for n in (2, 3):
        for index in range(0, max(0, len(tokens) - n + 1)):
            window = tokens[index : index + n]
            if any(token in STOPWORDS or is_low_value_term(token) for token in window):
                continue
            phrases.append(" ".join(window))
    return phrases

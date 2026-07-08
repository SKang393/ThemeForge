from __future__ import annotations

from collections import Counter, defaultdict
import math
from typing import Iterable

from .analysis_constants import STOPWORDS
from .text_utils import tokenize
def cluster_quotes(
    vectors: list[dict[str, float]],
    requested_count: int,
    min_theme_size: int,
    agglomerative_limit: int,
) -> tuple[list[list[int]], str | None]:
    if not vectors:
        return [], None

    target = max(1, min(requested_count, len(vectors)))
    if len(vectors) > max(1, target, agglomerative_limit):
        clusters = _bounded_seed_clusters(vectors, target)
        clusters = _merge_small_clusters(clusters, vectors, min_theme_size)
        clusters = sorted(clusters, key=lambda cluster: (-len(cluster), min(cluster)))
        return (
            clusters,
            (
                f"Large transcript bounded clustering used for {len(vectors)} quote units "
                f"with {target} target themes."
            ),
        )

    clusters = [[index] for index in range(len(vectors))]

    while len(clusters) > target:
        best_pair: tuple[int, int] | None = None
        best_score = -1.0
        for left_index in range(len(clusters)):
            left_centroid = centroid(vectors[index] for index in clusters[left_index])
            for right_index in range(left_index + 1, len(clusters)):
                right_centroid = centroid(vectors[index] for index in clusters[right_index])
                score = cosine(left_centroid, right_centroid)
                if score > best_score:
                    best_score = score
                    best_pair = (left_index, right_index)

        if best_pair is None:
            break

        left_index, right_index = best_pair
        clusters[left_index].extend(clusters[right_index])
        del clusters[right_index]

    clusters = _merge_small_clusters(clusters, vectors, min_theme_size)
    return sorted(clusters, key=lambda cluster: (-len(cluster), min(cluster))), None


def _bounded_seed_clusters(
    vectors: list[dict[str, float]],
    target: int,
) -> list[list[int]]:
    seed_indexes = _diverse_seed_indexes(vectors, target)
    clusters = [[index] for index in seed_indexes]
    seed_lookup = set(seed_indexes)

    for index, vector in enumerate(vectors):
        if index in seed_lookup:
            continue
        best_cluster = 0
        best_score = -1.0
        for cluster_index, cluster in enumerate(clusters):
            score = cosine(vector, centroid(vectors[item] for item in cluster))
            if score > best_score:
                best_score = score
                best_cluster = cluster_index
        clusters[best_cluster].append(index)

    return clusters


def _diverse_seed_indexes(vectors: list[dict[str, float]], target: int) -> list[int]:
    seed_indexes = [max(range(len(vectors)), key=lambda index: _vector_weight(vectors[index]))]

    while len(seed_indexes) < target:
        best_index = None
        best_distance = -1.0
        for index, vector in enumerate(vectors):
            if index in seed_indexes:
                continue
            nearest_similarity = max(cosine(vector, vectors[seed]) for seed in seed_indexes)
            distance = 1.0 - nearest_similarity
            weighted_distance = distance + min(0.25, _vector_weight(vector) / 20.0)
            if weighted_distance > best_distance:
                best_distance = weighted_distance
                best_index = index
        if best_index is None:
            break
        seed_indexes.append(best_index)

    return seed_indexes


def _vector_weight(vector: dict[str, float]) -> float:
    return sum(abs(value) for value in vector.values())


def _merge_small_clusters(
    clusters: list[list[int]],
    vectors: list[dict[str, float]],
    min_theme_size: int,
) -> list[list[int]]:
    if min_theme_size <= 1 or len(clusters) <= 1:
        return clusters

    changed = True
    while changed:
        changed = False
        small_indexes = [i for i, cluster in enumerate(clusters) if len(cluster) < min_theme_size]
        if not small_indexes or len(clusters) <= 1:
            break

        source_index = small_indexes[0]
        source_centroid = centroid(vectors[index] for index in clusters[source_index])
        best_target = None
        best_score = -1.0

        for target_index, cluster in enumerate(clusters):
            if target_index == source_index:
                continue
            score = cosine(source_centroid, centroid(vectors[index] for index in cluster))
            if score > best_score:
                best_score = score
                best_target = target_index

        if best_target is not None:
            clusters[best_target].extend(clusters[source_index])
            del clusters[source_index]
            changed = True

    return clusters


def tfidf_vectors(texts: list[str], extra_stopwords: set[str] | None = None) -> tuple[list[dict[str, float]], dict[str, float]]:
    documents = [terms_for_vector(text, extra_stopwords) for text in texts]
    document_frequency: Counter[str] = Counter()
    for document in documents:
        document_frequency.update(set(document))

    total = max(1, len(documents))
    idf = {
        term: math.log((1 + total) / (1 + frequency)) + 1
        for term, frequency in document_frequency.items()
    }

    vectors: list[dict[str, float]] = []
    for document in documents:
        counts = Counter(document)
        total_terms = sum(counts.values()) or 1
        vector = {
            term: (count / total_terms) * idf.get(term, 1.0)
            for term, count in counts.items()
        }
        vectors.append(normalize_vector(vector))

    return vectors, idf


def terms_for_vector(text: str, extra_stopwords: set[str] | None = None) -> list[str]:
    tokens = tokenize(text, extra_stopwords=extra_stopwords)
    phrases = []
    for n in (2, 3):
        phrases.extend(
            "_".join(tokens[index : index + n])
            for index in range(0, max(0, len(tokens) - n + 1))
            if not any(token in STOPWORDS for token in tokens[index : index + n])
        )
    return tokens + phrases



def centroid(vectors: Iterable[dict[str, float]]) -> dict[str, float]:
    totals: defaultdict[str, float] = defaultdict(float)
    count = 0
    for vector in vectors:
        count += 1
        for term, value in vector.items():
            totals[term] += value

    if count == 0:
        return {}

    return normalize_vector({term: value / count for term, value in totals.items()})


def normalize_vector(vector: dict[str, float]) -> dict[str, float]:
    magnitude = math.sqrt(sum(value * value for value in vector.values()))
    if magnitude == 0:
        return {}
    return {term: value / magnitude for term, value in vector.items()}


def cosine(left: dict[str, float], right: dict[str, float]) -> float:
    if not left or not right:
        return 0.0
    if len(left) > len(right):
        left, right = right, left
    return sum(value * right.get(term, 0.0) for term, value in left.items())



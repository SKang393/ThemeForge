from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
import math
import re
from statistics import mean
from typing import Iterable, Sequence

from . import __version__


STOPWORDS = {
    "a",
    "about",
    "above",
    "after",
    "again",
    "against",
    "all",
    "already",
    "also",
    "am",
    "an",
    "and",
    "any",
    "are",
    "as",
    "at",
    "be",
    "because",
    "been",
    "before",
    "being",
    "below",
    "between",
    "both",
    "but",
    "by",
    "can",
    "cannot",
    "could",
    "did",
    "do",
    "does",
    "doing",
    "down",
    "during",
    "each",
    "few",
    "fine",
    "for",
    "from",
    "further",
    "had",
    "has",
    "have",
    "having",
    "he",
    "her",
    "here",
    "hers",
    "herself",
    "him",
    "himself",
    "his",
    "he's",
    "how",
    "i",
    "i'm",
    "i've",
    "if",
    "in",
    "into",
    "is",
    "it",
    "its",
    "itself",
    "just",
    "kids",
    "kid",
    "kind",
    "know",
    "keep",
    "like",
    "me",
    "more",
    "most",
    "my",
    "myself",
    "name",
    "no",
    "nor",
    "not",
    "now",
    "of",
    "off",
    "on",
    "once",
    "one",
    "only",
    "or",
    "other",
    "our",
    "ours",
    "ourselves",
    "out",
    "over",
    "own",
    "same",
    "she",
    "should",
    "so",
    "some",
    "sort",
    "such",
    "than",
    "that",
    "the",
    "their",
    "theirs",
    "them",
    "themselves",
    "then",
    "there",
    "these",
    "they",
    "they're",
    "this",
    "those",
    "through",
    "to",
    "too",
    "under",
    "understand",
    "until",
    "up",
    "very",
    "was",
    "we",
    "were",
    "what",
    "when",
    "where",
    "which",
    "while",
    "who",
    "whom",
    "why",
    "will",
    "with",
    "you",
    "you've",
    "your",
    "yours",
    "yourself",
    "yourselves",
    "yeah",
    "yes",
    "think",
    "really",
    "maybe",
    "probably",
    "basically",
    "actually",
    "obviously",
    "get",
    "got",
    "go",
    "going",
    "gonna",
    "wanna",
    "want",
    "wants",
    "that's",
    "it's",
    "don't",
    "you're",
    "would",
    "true",
    "okay",
    "well",
    "guess",
    "much",
    "thing",
    "things",
    "another",
    "umm",
    "um",
    "uh",
    "interviewee",
    "interviewer",
    "researcher",
    "participant",
    "soi",
    "andi",
    "andthenit",
    "andthen",
}

KOREAN_STOPWORDS = {
    "그리고",
    "그래서",
    "하지만",
    "그러나",
    "저는",
    "제가",
    "우리",
    "때문에",
    "정말",
    "그냥",
    "많이",
    "조금",
    "있는",
    "없는",
    "했습니다",
    "있었습니다",
    "되었습니다",
}

STOPWORDS.update(KOREAN_STOPWORDS)

THEME_HINTS = {
    "peer": "Peer Collaboration",
    "peers": "Peer Collaboration",
    "classmate": "Peer Collaboration",
    "classmates": "Peer Collaboration",
    "community": "Belonging and Course Community",
    "group": "Peer Collaboration",
    "groups": "Peer Collaboration",
    "teacher": "Instructor Support",
    "teachers": "Instructor Support",
    "instructor": "Instructor Support",
    "instructors": "Instructor Support",
    "feedback": "Instructor Support",
    "support": "Instructor Support",
    "supported": "Instructor Support",
    "confidence": "Confidence and Self-Efficacy",
    "confident": "Confidence and Self-Efficacy",
    "isolated": "Isolation and Disconnection",
    "isolation": "Isolation and Disconnection",
    "deadline": "Course Structure and Clarity",
    "deadlines": "Course Structure and Clarity",
    "platform": "Technology and Access Barriers",
    "online": "Online Learning Experience",
    "autonomy": "Learner Autonomy",
    "independent": "Learner Autonomy",
    "motivation": "Motivation and Persistence",
}

ASD_PROFILE_TERMS = {
    "autism",
    "autistic",
    "asd",
    "spectrum",
}

FOUR_H_PROFILE_RE = re.compile(r"\b4[-\s]?h\b", re.IGNORECASE)

THEME_COLORS = (
    "#2563eb",
    "#dc2626",
    "#16a34a",
    "#9333ea",
    "#ea580c",
    "#0891b2",
    "#be123c",
    "#4f46e5",
    "#65a30d",
    "#c2410c",
)

TOKEN_RE = re.compile(r"[A-Za-z가-힣][A-Za-z0-9가-힣'-]*")
TOKEN_NOISE_RE = re.compile(r"p\d+", re.IGNORECASE)
SPEAKER_RE = re.compile(r"^\s*([A-Za-z가-힣][A-Za-z0-9가-힣 _.-]{0,60})\s*:\s+(.+?)\s*$")
SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True)
class AnalysisSettings:
    theme_count: int = 8
    quotes_per_theme: int = 0
    min_theme_size: int = 1
    min_quote_words: int = 4
    agglomerative_limit: int = 120
    central_theme: str = ""


@dataclass(frozen=True)
class TranscriptDocument:
    name: str
    text: str


@dataclass(frozen=True)
class CodeFrame:
    name: str
    terms: tuple[str, ...]
    phrases: tuple[str, ...]
    keywords: tuple[str, ...]


ASD_4H_CODE_FRAMES = (
    CodeFrame(
        name="ASD shapes 4-H participation",
        terms=("autism", "autistic", "asd", "spectrum", "4-h", "4h", "participation", "experience"),
        phrases=("autism spectrum", "on the spectrum", "4-h participation", "participation stressful"),
        keywords=("autism spectrum", "4-H participation", "experience", "stressful"),
    ),
    CodeFrame(
        name="Barriers to 4-H participation",
        terms=("barrier", "barriers", "noise", "crowds", "loud", "unpredictable", "waiting", "line", "harder"),
        phrases=("biggest barrier", "made 4-h participation stressful", "too loud", "talked at once"),
        keywords=("barriers", "participation", "noise", "unpredictability"),
    ),
    CodeFrame(
        name="Sensory and transition challenges",
        terms=("noise", "crowds", "loud", "unpredictable", "transitions", "waiting", "changed", "suddenly"),
        phrases=("noise and crowds", "too loud", "transitions waiting", "plans changed suddenly"),
        keywords=("sensory", "transitions", "unpredictable changes", "crowds"),
    ),
    CodeFrame(
        name="Leader training and accommodations",
        terms=("train", "training", "leaders", "visual", "schedules", "directions", "break", "space", "rules"),
        phrases=("train leaders", "visual schedules", "short directions", "calm break space", "rules ahead"),
        keywords=("leader training", "visual schedules", "break space", "advance rules"),
    ),
    CodeFrame(
        name="Useful hands-on 4-H activities",
        terms=("rabbit", "animals", "project", "hands-on", "cooking", "job", "show", "demonstration"),
        phrases=("rabbit project", "hands-on", "show animals", "cooking club", "short demonstration"),
        keywords=("hands-on activities", "animal projects", "defined jobs", "demonstrations"),
    ),
    CodeFrame(
        name="Smaller clubs support inclusion",
        terms=("small", "smaller", "club", "setting", "predictable", "routine", "fewer", "social", "demands"),
        phrases=("small special interest club", "smaller 4-h setting", "predictable routine", "fewer social demands"),
        keywords=("smaller clubs", "predictable routine", "reduced social demands", "inclusion"),
    ),
    CodeFrame(
        name="Gradual involvement builds confidence",
        terms=("involvement", "watched", "helped", "later", "demonstration", "proud", "teach", "engaged"),
        phrases=("involvement changed over time", "watched from the side", "helped set up", "gave a short demonstration", "feel proud"),
        keywords=("gradual involvement", "confidence", "demonstration", "teaching others"),
    ),
    CodeFrame(
        name="Peer and staff support strategies",
        terms=("peer", "buddies", "extension", "staff", "support", "checking", "quietly", "explaining", "breaks", "help"),
        phrases=("peer buddies", "extension staff", "checking in quietly", "explaining rules", "take breaks"),
        keywords=("peer buddies", "staff support", "quiet check-ins", "break support"),
    ),
)


@dataclass(frozen=True)
class TranscriptSegment:
    speaker: str
    text: str
    source_line: int
    source_name: str = "Transcript"


@dataclass(frozen=True)
class QuoteUnit:
    id: str
    speaker: str
    text: str
    source_line: int
    word_count: int
    source_name: str = "Transcript"


@dataclass(frozen=True)
class ThemeQuote:
    quote_id: str
    speaker: str
    text: str
    relevance: float
    source_line: int
    source_name: str = "Transcript"


@dataclass(frozen=True)
class Theme:
    id: str
    name: str
    color: str
    keywords: list[str]
    quote_count: int
    score: float
    quotes: list[ThemeQuote] = field(default_factory=list)
    validation: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class AnalysisResult:
    version: str
    document_count: int
    quote_count: int
    themes: list[Theme]
    notes: list[str]
    validation_summary: list[str] = field(default_factory=list)


def parse_transcript(text: str, source_name: str = "Transcript") -> list[TranscriptSegment]:
    """Parse a plain text transcript into speaker turns."""
    segments: list[TranscriptSegment] = []
    current_speaker = "Unknown"
    current_text: list[str] = []
    current_line = 1

    def flush() -> None:
        if current_text:
            merged = " ".join(part.strip() for part in current_text if part.strip())
            if merged:
                segments.append(
                    TranscriptSegment(
                        speaker=current_speaker.strip() or "Unknown",
                        text=_normalize_space(merged),
                        source_line=current_line,
                        source_name=source_name,
                    )
                )

    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            flush()
            current_text = []
            continue

        match = SPEAKER_RE.match(line)
        if match:
            flush()
            current_speaker = match.group(1).strip()
            current_text = [match.group(2).strip()]
            current_line = line_number
        elif current_text:
            current_text.append(line)
        else:
            current_speaker = "Unknown"
            current_text = [line]
            current_line = line_number

    flush()
    return segments


def extract_quote_units(
    segments: Iterable[TranscriptSegment],
    min_quote_words: int = 4,
) -> list[QuoteUnit]:
    quotes: list[QuoteUnit] = []
    next_id = 1

    for segment in segments:
        for sentence in _split_sentences(segment.text):
            word_count = len(_tokenize(sentence, keep_stopwords=True))
            if word_count < min_quote_words:
                continue
            quotes.append(
                QuoteUnit(
                    id=f"Q{next_id:04d}",
                    speaker=segment.speaker,
                    text=sentence,
                    source_line=segment.source_line,
                    word_count=word_count,
                    source_name=segment.source_name,
                )
            )
            next_id += 1

    return quotes


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

    if not quotes:
        return AnalysisResult(
            version=__version__,
            document_count=len(transcript_documents),
            quote_count=0,
            themes=[],
            notes=notes + ["No quote-length text units were found."],
            validation_summary=validation_summary,
        )

    codebook_themes = _build_contextual_codebook_themes(quotes, settings)
    if codebook_themes:
        return AnalysisResult(
            version=__version__,
            document_count=len(transcript_documents),
            quote_count=len(quotes),
            themes=_sort_themes(codebook_themes, settings),
            notes=notes
            + [
                "Contextual coding model used: labels combine transcript context with qualitative code frames.",
                "A quote can appear under more than one suggested theme when it supports multiple analytic codes.",
            ],
            validation_summary=validation_summary,
        )

    vectors, idf = _tfidf_vectors([_focus_augmented_text(quote.text, settings) for quote in quotes])
    clusters, clustering_note = _cluster_quotes(
        vectors,
        settings.theme_count,
        settings.min_theme_size,
        settings.agglomerative_limit,
    )
    if clustering_note:
        notes.append(clustering_note)
    themes = _build_themes(quotes, vectors, clusters, idf, settings)

    return AnalysisResult(
        version=__version__,
        document_count=len(transcript_documents),
        quote_count=len(quotes),
        themes=_sort_themes(themes, settings),
        notes=notes,
        validation_summary=validation_summary,
    )


def _build_themes(
    quotes: list[QuoteUnit],
    vectors: list[dict[str, float]],
    clusters: list[list[int]],
    idf: dict[str, float],
    settings: AnalysisSettings,
) -> list[Theme]:
    themes: list[Theme] = []

    for theme_number, cluster in enumerate(clusters, start=1):
        centroid = _centroid(vectors[index] for index in cluster)
        cluster_text = " ".join(quotes[index].text for index in cluster)
        keywords = _keywords_for_cluster(quotes, cluster, idf, limit=5)
        keywords = _prioritize_keywords(keywords, settings.central_theme, cluster_text)
        label = _label_theme(keywords)
        ranked_quotes = sorted(
            (
                ThemeQuote(
                    quote_id=quotes[index].id,
                    speaker=quotes[index].speaker,
                    text=quotes[index].text,
                    relevance=round(_cosine(vectors[index], centroid), 3),
                    source_line=quotes[index].source_line,
                    source_name=quotes[index].source_name,
                )
                for index in cluster
            ),
            key=lambda quote: (
                -_quote_sort_score(quote, settings.central_theme),
                quote.source_name,
                quote.source_line,
                quote.quote_id,
            ),
        )
        ranked_quotes = _limit_theme_quotes(ranked_quotes, settings.quotes_per_theme)

        cohesion_scores = [_cosine(vectors[index], centroid) for index in cluster]
        cohesion = mean(cohesion_scores) if cohesion_scores else 0.0
        validation = _theme_validation(ranked_quotes, len(cluster), settings)
        themes.append(
            Theme(
                id=f"T{theme_number:02d}",
                name=label,
                color=_theme_color(theme_number),
                keywords=keywords,
                quote_count=len(cluster),
                score=round(max(0.0, min(1.0, cohesion)), 3),
                quotes=ranked_quotes,
                validation=validation,
            )
        )

    return sorted(themes, key=lambda theme: (-theme.quote_count, -theme.score, theme.name))


def _build_contextual_codebook_themes(
    quotes: list[QuoteUnit],
    settings: AnalysisSettings,
) -> list[Theme]:
    corpus_text = " ".join(quote.text for quote in quotes).lower()
    if not _has_asd_4h_context(corpus_text):
        return []

    themes: list[Theme] = []
    for frame_index, frame in enumerate(ASD_4H_CODE_FRAMES, start=1):
        matches: list[ThemeQuote] = []
        for quote in quotes:
            score = _score_code_frame_quote(frame, quote.text)
            if score <= 0:
                continue
            matches.append(
                ThemeQuote(
                    quote_id=quote.id,
                    speaker=quote.speaker,
                    text=quote.text,
                    relevance=round(min(1.0, score / 6.0), 3),
                    source_line=quote.source_line,
                    source_name=quote.source_name,
                )
            )

        if not matches:
            continue

        ranked_quotes = sorted(
            matches,
            key=lambda quote: (
                -_quote_sort_score(quote, settings.central_theme),
                quote.source_name,
                quote.source_line,
                quote.quote_id,
            ),
        )
        limited_quotes = _limit_theme_quotes(ranked_quotes, settings.quotes_per_theme)
        themes.append(
            Theme(
                id=f"C{frame_index:02d}",
                name=frame.name,
                color=_theme_color(frame_index),
                keywords=list(frame.keywords),
                quote_count=len(ranked_quotes),
                score=round(mean(quote.relevance for quote in ranked_quotes), 3),
                quotes=limited_quotes,
                validation=_theme_validation(limited_quotes, len(ranked_quotes), settings),
            )
        )

    return themes[: max(1, settings.theme_count)]


def _has_asd_4h_context(corpus_text: str) -> bool:
    normalized = _normalize_for_match(corpus_text)
    has_asd_context = any(term in normalized for term in ASD_PROFILE_TERMS)
    has_4h_context = bool(FOUR_H_PROFILE_RE.search(corpus_text))
    return has_asd_context and has_4h_context


def _score_code_frame_quote(frame: CodeFrame, text: str) -> float:
    normalized = _normalize_for_match(text)
    tokens = set(_tokenize(text))
    score = 0.0

    for phrase in frame.phrases:
        if _normalize_for_match(phrase) in normalized:
            score += 2.5

    for term in frame.terms:
        normalized_term = _normalize_for_match(term)
        if " " in normalized_term or "-" in normalized_term:
            if normalized_term in normalized:
                score += 1.5
        elif normalized_term in tokens:
            score += 1.0

    return score


def _limit_theme_quotes(quotes: list[ThemeQuote], quotes_per_theme: int) -> list[ThemeQuote]:
    if quotes_per_theme <= 0:
        return quotes
    return quotes[:quotes_per_theme]


def _theme_color(theme_number: int) -> str:
    return THEME_COLORS[(theme_number - 1) % len(THEME_COLORS)]


def _cluster_quotes(
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
            left_centroid = _centroid(vectors[index] for index in clusters[left_index])
            for right_index in range(left_index + 1, len(clusters)):
                right_centroid = _centroid(vectors[index] for index in clusters[right_index])
                score = _cosine(left_centroid, right_centroid)
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
            score = _cosine(vector, _centroid(vectors[item] for item in cluster))
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
            nearest_similarity = max(_cosine(vector, vectors[seed]) for seed in seed_indexes)
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
        source_centroid = _centroid(vectors[index] for index in clusters[source_index])
        best_target = None
        best_score = -1.0

        for target_index, cluster in enumerate(clusters):
            if target_index == source_index:
                continue
            score = _cosine(source_centroid, _centroid(vectors[index] for index in cluster))
            if score > best_score:
                best_score = score
                best_target = target_index

        if best_target is not None:
            clusters[best_target].extend(clusters[source_index])
            del clusters[source_index]
            changed = True

    return clusters


def _tfidf_vectors(texts: list[str]) -> tuple[list[dict[str, float]], dict[str, float]]:
    documents = [_terms_for_vector(text) for text in texts]
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
        vectors.append(_normalize_vector(vector))

    return vectors, idf


def _terms_for_vector(text: str) -> list[str]:
    tokens = _tokenize(text)
    phrases = []
    for n in (2, 3):
        phrases.extend(
            "_".join(tokens[index : index + n])
            for index in range(0, max(0, len(tokens) - n + 1))
            if not any(token in STOPWORDS for token in tokens[index : index + n])
        )
    return tokens + phrases


def _keywords_for_cluster(
    quotes: list[QuoteUnit],
    cluster: list[int],
    idf: dict[str, float],
    limit: int,
) -> list[str]:
    scores: defaultdict[str, float] = defaultdict(float)
    cluster_text = " ".join(quotes[index].text for index in cluster)
    tokens = _tokenize(cluster_text)

    for token, count in Counter(tokens).items():
        scores[token] += count * idf.get(token, 1.0)
        if token in THEME_HINTS:
            scores[token] += 4.0 * count

    for phrase in _candidate_phrases(tokens):
        scores[phrase] += 1.75 * idf.get(phrase.replace(" ", "_"), 1.0)

    ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    keywords: list[str] = []
    for term, _ in ranked:
        display = term.replace("_", " ")
        if len(display) < 3 or display in keywords:
            continue
        keywords.append(display)
        if len(keywords) >= limit:
            break

    return keywords


def _prioritize_keywords(keywords: list[str], central_theme: str, text: str) -> list[str]:
    focus_terms = _focus_terms(central_theme)
    if not focus_terms or _central_theme_alignment(text, central_theme) <= 0:
        return keywords

    prioritized: list[str] = []
    for term in focus_terms:
        if term not in prioritized:
            prioritized.append(term)

    for keyword in keywords:
        if keyword not in prioritized:
            prioritized.append(keyword)

    return prioritized[:5]


def _candidate_phrases(tokens: list[str]) -> list[str]:
    phrases = []
    for n in (2, 3):
        for index in range(0, max(0, len(tokens) - n + 1)):
            window = tokens[index : index + n]
            if any(token in STOPWORDS for token in window):
                continue
            phrases.append(" ".join(window))
    return phrases


def _label_theme(keywords: list[str]) -> str:
    for keyword in keywords:
        for token in keyword.split():
            if token in THEME_HINTS:
                return THEME_HINTS[token]

    if not keywords:
        return "Emerging Theme"

    title_words = []
    for word in keywords[0].replace("_", " ").split():
        if word not in STOPWORDS:
            title_words.append(word.capitalize())

    return " ".join(title_words[:5]) or "Emerging Theme"


def _validation_summary(settings: AnalysisSettings) -> list[str]:
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


def _theme_validation(
    quotes: list[ThemeQuote],
    evidence_count: int,
    settings: AnalysisSettings,
) -> dict[str, object]:
    sources = {quote.source_name for quote in quotes}
    speakers = {quote.speaker for quote in quotes}
    return {
        "evidence_count": evidence_count,
        "displayed_quote_count": len(quotes),
        "source_count": len(sources),
        "speaker_count": len(speakers),
        "central_theme_alignment": round(
            _central_theme_alignment(
                " ".join(quote.text for quote in quotes),
                settings.central_theme,
            ),
            3,
        ),
        "review_status": "Researcher review required",
    }


def _sort_themes(themes: list[Theme], settings: AnalysisSettings) -> list[Theme]:
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


def _theme_focus_score(theme: Theme, central_theme: str) -> float:
    text = " ".join(
        [theme.name, *theme.keywords, *(quote.text for quote in theme.quotes)]
    )
    return _central_theme_alignment(text, central_theme)


def _quote_sort_score(quote: ThemeQuote, central_theme: str) -> float:
    return quote.relevance + _central_theme_alignment(quote.text, central_theme)


def _central_theme_alignment(text: str, central_theme: str) -> float:
    focus_terms = _focus_terms(central_theme)
    if not focus_terms:
        return 0.0

    normalized_text = _normalize_for_match(text)
    text_tokens = set(_tokenize(text))
    score = 0.0
    for term in focus_terms:
        normalized_term = _normalize_for_match(term)
        if not normalized_term:
            continue
        if normalized_term in normalized_text:
            score += 1.0
        elif term in text_tokens:
            score += 0.7
    return min(1.0, score / max(1, len(focus_terms)))


def _focus_augmented_text(text: str, settings: AnalysisSettings) -> str:
    central_theme = settings.central_theme.strip()
    if not central_theme:
        return text
    alignment = _central_theme_alignment(text, central_theme)
    if alignment <= 0:
        return text
    return " ".join([text, central_theme, central_theme])


def _focus_terms(central_theme: str) -> list[str]:
    return _tokenize(central_theme)


def _centroid(vectors: Iterable[dict[str, float]]) -> dict[str, float]:
    totals: defaultdict[str, float] = defaultdict(float)
    count = 0
    for vector in vectors:
        count += 1
        for term, value in vector.items():
            totals[term] += value

    if count == 0:
        return {}

    return _normalize_vector({term: value / count for term, value in totals.items()})


def _normalize_vector(vector: dict[str, float]) -> dict[str, float]:
    magnitude = math.sqrt(sum(value * value for value in vector.values()))
    if magnitude == 0:
        return {}
    return {term: value / magnitude for term, value in vector.items()}


def _cosine(left: dict[str, float], right: dict[str, float]) -> float:
    if not left or not right:
        return 0.0
    if len(left) > len(right):
        left, right = right, left
    return sum(value * right.get(term, 0.0) for term, value in left.items())


def _split_sentences(text: str) -> list[str]:
    pieces = [piece.strip() for piece in SENTENCE_RE.split(_normalize_space(text)) if piece.strip()]
    return [piece if piece.endswith((".", "!", "?")) else f"{piece}." for piece in pieces]


def _tokenize(text: str, keep_stopwords: bool = False) -> list[str]:
    tokens = [match.group(0).lower().strip("'") for match in TOKEN_RE.finditer(text)]
    tokens = [token for token in tokens if token and not TOKEN_NOISE_RE.fullmatch(token)]
    if keep_stopwords:
        return [token for token in tokens if token]
    return [token for token in tokens if token and token not in STOPWORDS and len(token) > 1]


def _normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _normalize_for_match(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^a-z0-9가-힣-]+", " ", text)
    return _normalize_space(text)

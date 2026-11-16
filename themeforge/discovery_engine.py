"""Offline extractive proposals, with no claim to automate thematic interpretation.

Similarity retrieves review neighborhoods. Every proposal is named by one contiguous
participant assertion, never by joining high-frequency words or interviewer prompts.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from hashlib import sha256
import math
import re

from .analysis_constants import BRACKETED_METADATA_RE, STOPWORDS
from .analysis_vectors import cosine, normalize_vector

_TOKEN = re.compile(r"[^\W_]+(?:['’][^\W_]+)?", re.UNICODE)
_NEGATIVE = {"not", "never", "no", "cannot", "can't", "don't", "doesn't", "didn't", "without", "wasn't", "isn't", "couldn't", "wouldn't"}
_MODAL = {"may", "might", "could", "would", "should", "must", "can"}
_CONDITION = {"if", "when", "unless", "until", "except", "sometimes", "only"}
_RELATION = _CONDITION | {"because", "therefore", "since", "although", "but", "however", "than", "whereas", "despite", "so"}
_FILLER = {"okay", "ok", "yes", "yeah", "uh", "um", "hmm", "well", "actually", "basically", "really", "able", "let's"}
_POSITIVE = {"help", "improve", "easy", "easier", "benefit", "safe", "safer", "trust", "useful", "effective", "successful"}
_ADVERSE = {"harm", "worsen", "difficult", "harder", "unsafe", "distrust", "useless", "ineffective", "fail", "stress"}
_ROLE = re.compile(r"\b(interviewer|moderator|facilitator|researcher)\b", re.I)
_MIN_SIMILARITY = 0.18
_AUXILIARY = {"am", "is", "are", "was", "were", "be", "been", "being", "has", "have", "had", "do", "does", "did", "will", "shall"} | _MODAL
_SUBJECTS = {"i", "we", "you", "he", "she", "it", "they"}
_PREPOSITIONS = {"about", "for", "during", "regarding", "concerning", "with", "without", "from", "on", "in", "at", "to", "by", "of"}
_PREDICATES = {"take", "takes", "took", "need", "needs", "require", "requires", "seem", "seems", "feel", "feels", "felt", "find", "finds", "found", "help", "helps", "make", "makes", "made", "work", "works", "fail", "fails", "prefer", "trust", "want", "use", "used", "save", "cost", "costs", "spend", "spent", "know", "think", "believe", "left", "went", "said", "got", "became", "stop", "start"}


def _words(text: str) -> list[str]:
    return [match.group().lower().replace("’", "'") for match in _TOKEN.finditer(text)]


def _stem(word: str) -> str:
    # Conservative morphology only; no domain-specific concept/synonym dictionary.
    if len(word) > 8 and word.endswith("ability"):
        return word[:-7] + "able"
    if len(word) > 8 and word.endswith("ibility"):
        return word[:-7] + "ible"
    if len(word) > 5 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 5 and word.endswith("ing"):
        base = word[:-3]
        return base[:-1] if len(base) > 2 and base[-1] == base[-2] else base
    if len(word) > 4 and word.endswith("ed"):
        return word[:-2]
    if len(word) > 4 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
        return word[:-1]
    return word


def _complete_assertion(text: str) -> bool:
    """Conservative surface grammar check, not a syntactic/semantic parser."""
    if re.match(r"^\s*(?:(?:what|why|how|where|when|which)\s+(?:do|does|did|can|could|would|will|is|are|was|were)|(?:do|does|did|can|could|would|will|is|are|was|were)\s+(?:you|we|they|the))\b", text, re.I):
        return False
    words = _words(text)
    if len(words) < 4 or words[-1] in {"because", "although", "unless", "and", "but", "if", "when", "to", "for", "of", "with"}:
        return False
    if re.search(r"\b(?:the|a|an|from|for|to|with)\s+(?:like|you\s+know)[.!\s]*$", text, re.I):
        return False
    while words and words[0] in _FILLER:
        words.pop(0)
    if not words:
        return False
    if words[0] in _PREPOSITIONS:
        # A relative/subordinate clause inside a prepositional phrase is not a
        # main assertion, even when it contains an auxiliary ("about when X is Y").
        comma = text.find(",")
        if comma >= 0 and _complete_assertion(text[comma + 1:]):
            return True
        if any(word in {"when", "where", "whether", "which", "how"} for word in words[:5]):
            return False
        if not any(word in _SUBJECTS for word in words[1:6]):
            return False
    finite = any(word in _AUXILIARY | _PREDICATES or "'" in word and word.endswith(("'m", "'re", "'s", "'ve", "'ll", "'d", "n't")) for word in words)
    personal = any(word in _SUBJECTS for word in words[:5])
    inflected = any(len(word) > 4 and word.endswith("ed") for word in words)
    third_person = any(index > 0 and index < 5 and len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "ness", "ous", "is")) for index, word in enumerate(words))
    # Do not apply English surface grammar to non-Latin scripts.
    non_latin = any(ord(character) > 0x024F and character.isalpha() for character in text)
    return finite or personal or inflected or third_person or non_latin


def _concise_label(text: str) -> tuple[str, int, int]:
    # Only independent semicolon clauses can be shortened. Keep every occurrence
    # of a negation/modality/condition/relation cue from the full anchor.
    scope = _NEGATIVE | _MODAL | _RELATION
    all_cues = [word for word in _words(text) if word in scope or word.endswith("n't")]
    candidates = [(text, 0, len(text))]
    for match in re.finditer(r"[^;]+", text):
        raw = match.group()
        clause = raw.strip()
        begin = match.start() + len(raw) - len(raw.lstrip())
        cues = [word for word in _words(clause) if word in scope or word.endswith("n't")]
        if _complete_assertion(clause) and cues == all_cues:
            candidates.append((clause, begin, begin + len(clause)))
    def priority(item):
        words = set(_words(item[0]))
        experiential = bool(words & {"i", "we", "my", "our", "i'm", "i've", "we're", "we've"})
        evaluative = bool(words & (_POSITIVE | _ADVERSE))
        return (-int(experiential) - int(evaluative), len(_words(item[0])), len(item[0]), item[1])
    return min(candidates, key=priority)


def _content(text: str) -> set[str]:
    return {_stem(word) for word in _words(text) if word not in STOPWORDS | _FILLER | _NEGATIVE | _MODAL | _RELATION and len(word) > 1}


def _signature(text: str) -> tuple[bool, frozenset[str], frozenset[str], int]:
    words = set(_words(text))
    stems = {_stem(word) for word in words}
    evaluation = int(bool(stems & _POSITIVE)) - int(bool(stems & _ADVERSE))
    return bool(words & _NEGATIVE) or any(word.endswith("n't") for word in words), frozenset(words & _MODAL), frozenset(words & _CONDITION), evaluation


def _vectors(texts: list[str]) -> list[dict[str, float]]:
    bags = []
    for text in texts:
        words = _words(text)
        content = _content(text)
        bag = Counter({term: 1.0 for term in sorted(content)})
        # Preserve local order and relation/modality cues without letting generic
        # structural features make two unrelated assertions look similar.
        for left, right in zip(words, words[1:]):
            if _stem(left) in content and _stem(right) in content:
                bag[f"pair:{_stem(left)}:{_stem(right)}"] += 0.35
        for word in sorted(set(words) & (_NEGATIVE | _MODAL | _RELATION)):
            bag[f"cue:{word}"] = 0.18
        bags.append(bag)
    frequencies = Counter(term for bag in bags for term in bag)
    return [normalize_vector({term: weight * (1 + math.log((1 + len(bags)) / (1 + frequencies[term]))) for term, weight in bag.items()}) for bag in bags]


def _anchors(unit: dict) -> list[dict]:
    text = str(unit.get("text", ""))
    start = unit.get("start", unit.get("source_start"))
    sentences = unit.get("sentences") or [{"text": text, "source_start": start, "source_end": unit.get("end", unit.get("source_end"))}]
    anchors = []
    for sentence in sentences:
        claim = str(sentence.get("text", ""))
        left, right = sentence.get("source_start"), sentence.get("source_end")
        words = _words(claim)
        stage_direction = re.match(r"^\s*[\[*]*(?:pause|silence|laughter|inaudible)\b", claim, re.I)
        if "?" in claim or "？" in claim or BRACKETED_METADATA_RE.search(claim) or stage_direction or len(words) < 4 or len(_content(claim)) < 2 or not _complete_assertion(claim):
            continue
        if not isinstance(left, int) or not isinstance(right, int) or right - left != len(claim):
            continue
        # Reject invented/discontinuous anchors, retaining the supplied source span.
        if claim not in text or (isinstance(start, int) and text[left - start:right - start] != claim):
            continue
        lexical = set(words)
        first_person = bool(lexical & {"i", "we", "my", "our", "i'm", "i've", "i'd", "we're", "we've", "we'd"})
        evaluated = bool(lexical & (_POSITIVE | _ADVERSE)) or bool(re.search(r"\b(?:i|we)\s+(?:think|feel|felt|found|prefer|noticed|tried|need|needed|want|wanted|struggle|struggled)\b", claim, re.I))
        # Prefer a readable, complete assertion over a sprawling transcript
        # sentence. Retain the full long claim for review, never truncate it.
        length_penalty = min(3.0, max(0, len(words) - 28) * 0.065)
        filler_penalty = min(0.5, sum(word in _FILLER for word in words) / len(words))
        quality = min(len(_content(claim)), 10) / 20 + 0.65 * bool(lexical & _RELATION) + 0.15 * bool(lexical & (_NEGATIVE | _MODAL)) + 0.5 * first_person + 0.4 * evaluated - length_penalty - filler_penalty
        label, label_left, label_right = _concise_label(claim)
        anchors.append({"text": claim, "start": left, "end": right, "quality": quality, "label": label, "label_start": left + label_left, "label_end": left + label_right})
    return sorted(anchors, key=lambda anchor: (-anchor["quality"], anchor["start"], anchor["text"]))


def _is_excluded(unit: dict) -> bool:
    flags = unit.get("flags", [])
    role_flags = {str(flag).lower() for flag in flags}
    return not unit.get("eligible", True) or bool(_ROLE.search(str(unit.get("speaker", "")))) or bool(role_flags & {"interviewer", "interviewer_question", "question_only", "moderator"})


def _possible_contrast(left: str, right: str) -> bool:
    a, b = _signature(left), _signature(right)
    return a[0] != b[0] or a[3] * b[3] < 0


def _possible_qualification(left: str, right: str) -> bool:
    a, b = _signature(left), _signature(right)
    return a[1] != b[1] or a[2] != b[2]


def _identifier(prefix: str, *parts: str) -> str:
    return prefix + sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:16]


def build_candidate_codebook(
    units: list[dict], *, research_questions: tuple[str, ...] = (),
    theoretical_lens: str = "", mode: str = "open", max_candidates: int = 12,
    vector_provider=None,
) -> dict:
    """Build deterministic, provisional codes from immutable meaning-unit records.

    ``max_candidates`` is a ceiling. Guided mode ranks assertion and bounded
    reference-question overlap with corpus-weighted context. It does not interpret a lens.
    An optional local vector provider accepts texts and returns sparse vectors.
    """
    if mode not in {"open", "guided", "context_guided", "context-guided"}:
        raise ValueError("mode must be open or context_guided")
    if not isinstance(max_candidates, int) or isinstance(max_candidates, bool) or max_candidates < 0:
        raise ValueError("max_candidates must be a nonnegative integer")
    ordered = sorted(units, key=lambda unit: str(unit["id"]))
    if len({str(unit["id"]) for unit in ordered}) != len(ordered):
        raise ValueError("meaning-unit ids must be unique")
    by_id = {str(unit["id"]): unit for unit in ordered}
    eligible = [unit for unit in ordered if not _is_excluded(unit)]
    claims = []
    for unit in eligible:
        # Multiple assertions in a meaning unit may seed overlapping codes.
        for anchor in _anchors(unit):
            claims.append({**anchor, "unit_id": str(unit["id"]), "terms": _content(anchor["text"])})
    texts = [claim["text"] for claim in claims]
    vectors = list(vector_provider(texts)) if vector_provider is not None and texts else _vectors(texts)
    if len(vectors) != len(claims):
        raise ValueError("vector_provider must return one sparse vector per assertion")
    vectors = [normalize_vector(dict(sorted(vector.items()))) for vector in vectors]
    guided = mode != "open"
    query = " ".join((*research_questions, theoretical_lens)) if guided else ""
    query_terms = _content(query)
    assertion_frequency = Counter(term for claim in claims for term in claim["terms"])
    document_terms = defaultdict(set)
    for claim in claims:
        document_terms[str(by_id[claim["unit_id"]].get("source_name", ""))].update(claim["terms"])
    document_frequency = Counter(term for terms in document_terms.values() for term in terms)
    query_weights = {}
    for term in sorted(query_terms):
        assertion_idf = math.log(1 + (len(claims) - assertion_frequency[term] + 0.5) / (assertion_frequency[term] + 0.5))
        document_idf = math.log(1 + (len(document_terms) - document_frequency[term] + 0.5) / (document_frequency[term] + 0.5))
        query_weights[term] = math.sqrt(assertion_idf * document_idf)
    query_total = sum(query_weights.values()) or 1.0
    for claim in claims:
        matched = claim["terms"] & query_terms
        direct = sum(query_weights[term] for term in sorted(matched)) / query_total
        # Reference-question terms affect relevance only. They never enter claim
        # vectors, anchors, labels, lexical evidence, or counterexample retrieval.
        reference = str(by_id[claim["unit_id"]].get("reference_context", ""))
        reference_matched = _content(reference[:2000]) & query_terms if guided else set()
        reference_score = sum(query_weights[term] for term in sorted(reference_matched)) / query_total
        claim["reference_relevance"] = 0.2 * reference_score * (1 - direct) if guided else 0.0
        claim["relevance"] = direct + claim["reference_relevance"] if guided else 0.0
        claim["matched"] = sorted(matched)
        claim["reference_matched"] = sorted(reference_matched)

    # Content postings avoid scoring every pair for portable lexical retrieval.
    # Dense optional vectors still require pairwise scoring, but the retained
    # neighborhoods are bounded rather than a corpus-sized similarity matrix.
    postings = defaultdict(list)
    for index, claim in enumerate(claims):
        for term in sorted(claim["terms"]):
            postings[term].append(index)
    neighborhoods, contrasts, qualifications = [], [], []
    for i, claim in enumerate(claims):
        neighbors, possible = [(i, 1.0)], []
        overlaps = Counter(j for term in sorted(claim["terms"]) for j in postings[term])
        candidates = range(len(claims)) if vector_provider is not None else sorted(j for j, count in overlaps.items() if count >= 2)
        for j in candidates:
            other = claims[j]
            if i == j or claim["unit_id"] == other["unit_id"]:
                continue
            score = cosine(vectors[i], vectors[j])
            overlap = len(claim["terms"] & other["terms"])
            if score < _MIN_SIMILARITY or (vector_provider is None and overlap < 2):
                continue
            if vector_provider is None and min(overlap / len(claim["terms"]), overlap / len(other["terms"])) < 0.25:
                continue
            target = possible if _possible_contrast(claim["text"], other["text"]) else neighbors
            target.append((j, score))
        # Neighborhood radius follows the strongest nonself match, bounded below
        # so isolated statements cannot acquire arbitrary nearest neighbors.
        strongest = max((score for j, score in neighbors[1:] + possible), default=0.0)
        threshold = max(_MIN_SIMILARITY, min(0.48, strongest * 0.5))
        key = lambda pair: (-pair[1], claims[pair[0]]["unit_id"], claims[pair[0]]["start"])
        neighborhoods.append(sorted((pair for pair in neighbors if pair[1] >= threshold), key=key)[:64])
        contrasts.append(sorted((pair for pair in possible if pair[1] >= threshold), key=key)[:32])
        qualifications.append({j for j, _ in neighborhoods[-1] if j != i and _possible_qualification(claim["text"], claims[j]["text"])})

    selected, represented, used_sources, used_speakers = [], set(), set(), set()
    remaining = {i for i, claim in enumerate(claims) if not guided or claim["relevance"] > 0}
    while remaining and len(selected) < max_candidates:
        def priority(index):
            claim = claims[index]
            unit = by_id[claim["unit_id"]]
            ids = {claims[j]["unit_id"] for j, _ in neighborhoods[index]}
            # Frequency is capped; complete rare assertions and source diversity
            # remain competitive with repeated statements from one interview.
            return (4 * claim["relevance"] + claim["quality"] + 0.15 * min(3, len(ids)) + 0.25 * (unit.get("source_name", "") not in used_sources) + 0.2 * ((unit.get("source_name", ""), unit.get("speaker", "")) not in used_speakers) + 0.35 * bool(ids - represented), claim["unit_id"], -claim["start"])
        index = max(remaining, key=priority)
        remaining.remove(index)
        claim = claims[index]
        evidence = {claims[j]["unit_id"] for j, _ in neighborhoods[index]}
        duplicate = False
        for previous in selected:
            previous_neighborhood = previous["evidence"] | previous["qualifications"]
            shared = evidence & previous_neighborhood
            union = evidence | previous_neighborhood
            if texts[index].casefold() == texts[previous["index"]].casefold() or (cosine(vectors[index], vectors[previous["index"]]) >= 0.65 and len(shared) / max(1, len(union)) >= 0.65 and not _possible_contrast(texts[index], texts[previous["index"]]) and not _possible_qualification(texts[index], texts[previous["index"]])):
                duplicate = True
                break
        if duplicate:
            continue
        # A unit containing both a qualifying and supporting assertion is treated
        # conservatively as a review case, never simultaneous unconditional support.
        counterexamples = {claims[j]["unit_id"] for j, _ in contrasts[index]} - {claim["unit_id"]}
        evidence -= counterexamples
        qualified_ids = {claims[j]["unit_id"] for j in qualifications[index]} & evidence - {claim["unit_id"]}
        evidence -= qualified_ids
        selected.append({"index": index, "evidence": evidence, "counterexamples": counterexamples, "qualifications": qualified_ids})
        represented.update(evidence)
        unit = by_id[claim["unit_id"]]
        used_sources.add(unit.get("source_name", ""))
        used_speakers.add((unit.get("source_name", ""), unit.get("speaker", "")))

    codes = []
    for item in selected:
        claim = claims[item["index"]]
        uncertainty = ["Extractive anchor proposed for researcher review; lexical or vector proximity does not establish shared meaning."]
        if len(item["evidence"]) == 1:
            uncertainty.append("Single-unit proposal retained as a possible rare case; recurrence is unestablished.")
        if item["counterexamples"]:
            uncertainty.append("Possible qualifying or contrasting passages require contextual review; negation or modality alone does not establish contradiction.")
        if item["qualifications"]:
            uncertainty.append("Some retrieved evidence has differing modality or conditions and is marked qualifying_assertion; it is not unconditional support or a contradiction.")
        matches = {claim["unit_id"]: {"unit_id": claim["unit_id"], "text": claim["text"], "source_start": claim["start"], "source_end": claim["end"], "similarity": 1.0, "role": "anchor"}}
        match_candidates = [(j, score, "qualifying_assertion" if claims[j]["unit_id"] in item["qualifications"] else "similar_assertion") for j, score in neighborhoods[item["index"]] if claims[j]["unit_id"] in item["evidence"] | item["qualifications"] and (claims[j]["unit_id"] not in item["qualifications"] or j in qualifications[item["index"]])]
        match_candidates.extend((j, score, "possible_contrast") for j, score in contrasts[item["index"]] if claims[j]["unit_id"] in item["counterexamples"])
        for j, score, role in sorted(match_candidates, key=lambda value: (-value[1], claims[value[0]]["unit_id"], claims[value[0]]["start"])):
            other = claims[j]
            matches.setdefault(other["unit_id"], {"unit_id": other["unit_id"], "text": other["text"], "source_start": other["start"], "source_end": other["end"], "similarity": round(score, 6), "role": role})
        codes.append({
            "id": _identifier("C", claim["unit_id"], str(claim["start"]), str(claim["end"])),
            "kind": "code", "label": claim["label"],
            "definition": "Extractive proposal anchored in this participant assertion: " + claim["text"],
            "inclusion_criteria": ["Review passages expressing the anchored assertion with compatible conditions, negation, modality, and referents.", "Other retrieved evidence is a provisional similarity suggestion, pending researcher confirmation."],
            "exclusion_criteria": ["Interviewer prompts and statements sharing vocabulary without the anchored assertion.", "Different conditions, qualifications, negation, or modality requiring separate review."],
            "origin": "local_extractive", "definition_kind": "extractive_anchor",
            "anchor_unit_id": claim["unit_id"], "anchor_text": claim["text"],
            "anchor_source_start": claim["start"], "anchor_source_end": claim["end"],
            "label_source_start": claim["label_start"], "label_source_end": claim["label_end"],
            "evidence_unit_ids": sorted(item["evidence"]), "counterexample_unit_ids": sorted(item["counterexamples"]),
            "qualification_unit_ids": sorted(item["qualifications"]),
            "evidence_status": {unit_id: "anchor" if unit_id == claim["unit_id"] else "qualifying_assertion" if unit_id in item["qualifications"] else "similar_assertion" for unit_id in sorted(item["evidence"] | item["qualifications"])},
            "matches": [matches[unit_id] for unit_id in sorted(matches)],
            "uncertainty": uncertainty, "scope_terms": sorted(claim["terms"]),
            "context_relevance": round(claim["relevance"], 6),
            "context_guidance": {"mode": "context_guided" if guided else "open", "matched_terms": claim["matched"], "reference_matched_terms": claim["reference_matched"], "reference_relevance": round(claim["reference_relevance"], 6), "basis": "corpus_weighted_assertion_and_bounded_reference_overlap" if guided else "context_ignored"},
        })
    categories, relations = _group_proposals(codes, selected, claims, vectors)
    all_ids = set(by_id)
    assigned = {unit_id for code in codes for unit_id in code["evidence_unit_ids"]}
    notes = [
        "These are offline extractive candidate codes, not validated codes or interpreted themes. All definitions retain a contiguous participant assertion.",
        "Overlapping similarity neighborhoods suggest evidence for review; no fixed cluster count or forced assignment is used.",
        "Portable lexical retrieval additionally requires at least two shared content terms covering at least one quarter of each assertion's content vocabulary. Low lexical coverage abstains rather than treating isolated shared words as semantic agreement.",
        "Categories are review groups of related proposals. Interpretive themes require researcher synthesis and remain empty.",
        "Possible contrast retrieval uses surface negation, modality, conditions, and evaluation cues; it does not establish logical contradiction.",
        "Differences in modality or conditions are qualified retrieval evidence with explicit evidence_status, not contradictions or unconditional support. Only possible polarity/evaluation contrasts enter counterexample_unit_ids.",
        "Lexical retrieval may miss paraphrases, irony, implicit referents, and languages without whitespace; optional vectors do not resolve interpretation.",
        "Assertion completeness uses conservative English surface-grammar checks, not syntactic parsing. Labels may select a complete contiguous semicolon clause while full anchors retain source scope.",
        "Generic -ability/-ibility nominal forms share an adjective form; broader derivations and conceptual synonyms are not inferred.",
    ]
    if guided:
        notes.append("Context-guided mode weights research-question/lens terms by assertion and source-document frequency, reducing ubiquitous background vocabulary. Reference-question overlap contributes at most 0.2 relevance and never supplies labels, assertions, evidence membership, or contrast. This is lexical context weighting, not theoretical interpretation; out-of-focus units remain accessible in unassigned_unit_ids.")
        if not query_terms:
            notes.append("No usable context terms were supplied; guided discovery abstained rather than silently switching to open mode.")
    else:
        notes.append("Open mode ignores supplied research questions and theoretical lens for discovery and ranking.")
    return {
        "codes": codes, "categories": categories, "interpretive_themes": [], "relations": relations,
        "unassigned_unit_ids": sorted(all_ids - assigned),
        "flagged_unit_ids": sorted(str(unit["id"]) for unit in ordered if unit.get("flags") or _is_excluded(unit)),
        "notes": notes,
        "diagnostics": {"input_units": len(ordered), "eligible_units": len(eligible), "assertion_count": len(claims), "candidate_count": len(codes), "candidate_cap": max_candidates, "minimum_similarity": _MIN_SIMILARITY, "adaptive_neighborhood_fraction": 0.5, "vector_backend": "provided_local_vectors" if vector_provider is not None else "portable_relation_aware_lexical", "mode": "context_guided" if guided else "open", "context_terms": sorted(query_terms), "context_term_weights": {term: round(weight, 6) for term, weight in query_weights.items()}, "reference_relevance_cap": 0.2, "out_of_focus_unit_ids": sorted({claim["unit_id"] for claim in claims} - {claim["unit_id"] for claim in claims if claim["relevance"]}) if guided else [], "excluded_or_no_anchor_unit_ids": sorted(all_ids - {claim["unit_id"] for claim in claims})},
    }


def _group_proposals(codes, selected, claims, vectors):
    adjacency = {i: set() for i in range(len(codes))}
    relations = []
    for i, left in enumerate(codes):
        for j in range(i + 1, len(codes)):
            right = codes[j]
            score = cosine(vectors[selected[i]["index"]], vectors[selected[j]["index"]])
            overlap = set(left["evidence_unit_ids"]) & set(right["evidence_unit_ids"])
            cross = (set(left["counterexample_unit_ids"]) & set(right["evidence_unit_ids"])) | (set(right["counterexample_unit_ids"]) & set(left["evidence_unit_ids"]))
            qualified = (set(left["qualification_unit_ids"]) & set(right["evidence_unit_ids"])) | (set(right["qualification_unit_ids"]) & set(left["evidence_unit_ids"]))
            if score < _MIN_SIMILARITY and not overlap:
                continue
            if overlap or score >= 0.32:
                adjacency[i].add(j)
                adjacency[j].add(i)
            if cross or qualified or overlap or score >= 0.45:
                kind = "possible_qualification" if cross or qualified else "possible_split" if overlap else "possible_merge"
                relations.append({"id": _identifier("R", left["id"], right["id"], kind), "kind": kind, "code_ids": [left["id"], right["id"]], "evidence_unit_ids": sorted(overlap | cross | qualified | {left["anchor_unit_id"], right["anchor_unit_id"]}), "rationale": "Review possible polarity/evaluation differences; contradiction is unestablished." if cross else "Related assertions differ in modality or conditions; review qualified applicability separately." if qualified else "Shared evidence may contain distinct assertions deserving separate coding." if overlap else "Similar assertion vocabulary suggests reviewing these proposals for possible consolidation.", "uncertainty": ["Similarity and surface qualifiers are retrieval signals, not a confirmed analytic relationship."]})
    categories, visited = [], set()
    for seed in adjacency:
        if seed in visited:
            continue
        group, pending = set(), [seed]
        while pending:
            index = pending.pop()
            if index in group:
                continue
            group.add(index)
            pending.extend(adjacency[index] - group)
        visited.update(group)
        if len(group) < 2:
            continue
        members = [codes[index] for index in sorted(group)]
        code_ids = [code["id"] for code in members]
        categories.append({"id": _identifier("G", *sorted(code_ids)), "kind": "category", "label": f"Related proposal review group {len(categories) + 1}", "code_ids": code_ids, "evidence_unit_ids": sorted({unit_id for code in members for unit_id in code["evidence_unit_ids"]}), "rationale": "Connected by overlapping evidence or above-threshold assertion similarity; review the individual anchored claims before interpreting a category.", "uncertainty": ["A retrieval review group is not an interpretive theme or an established common meaning."]})
    return categories, relations

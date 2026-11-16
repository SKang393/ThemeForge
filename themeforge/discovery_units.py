"""Source-preserving turns for local discovery, with reviewable heuristic flags.

Eligibility and relation cues are conservative English-oriented routing hints, not
interpretations or translations. Interviewer context is never appended to a turn's
assertion text. No lexical frequency threshold discards a rare account.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from hashlib import sha256
import re

from .analysis_types import TranscriptDocument, TranscriptSegment
from .transcript_parser import explicit_role_roster, parse_transcript


_INTERVIEWER = re.compile(
    r"\b(?:interviewer|moderator|mediator|facilitator|researcher|host|note\s*taker)\b|진행자|면담자|질문자",
    re.IGNORECASE,
)
_PARTICIPANT = re.compile(r"^(?:p\d+|participant(?:\s+\w+)?|interviewee(?:\s+\d+)?|참여자|응답자)$", re.IGNORECASE)
_PROMPT = re.compile(
    r"^(?:(?:can|could|would)\s+you\b|tell\s+me\b|what\b|how\b|why\b|when\b|where\b|do\s+you\b|did\s+you\b)",
    re.IGNORECASE,
)
_ADMINISTRATION = re.compile(
    r"\b(?:"
    r"(?:stop|start|pause|end|turn\s+(?:off|on))\s+(?:the\s+|this\s+)?(?:audio\s+|video\s+)?recording"
    r"|(?:stop|start|pause)\s+recording"
    r"|(?:end|conclude|finish)\s+(?:the|this|our)\s+(?:interview|session)"
    r"|(?:this|the|our)\s+interview\s+(?:is\s+(?:over|finished)|has\s+ended)"
    r"|thank\s+you\s+for\s+(?:participating|taking\s+part|joining\s+(?:the|this)\s+interview)"
    r"|(?:do\s+you|i)\s+(?:give\s+)?consent\s+to\s+(?:participat\w*|record\w*)"
    r"|(?:sign|signed|read|complete)\s+(?:the\s+|this\s+|your\s+)?consent\s+form"
    r"|(?:participation\s+is\s+voluntary|withdraw\s+at\s+any\s+time)"
    r"|(?:will|can|shall|going\s+to|i'll|we'll)\s+(?:be\s+)?(?:audio\s+|video\s+)?record(?:ed)?\s+(?:this|the|our|for\s+research)"
    r"|(?:email|e-mail|send)\s+(?:you\s+)?(?:a\s+|the\s+|your\s+)?(?:copy\s+of\s+(?:the\s+)?|interview\s+)?(?:transcript|consent\s+form|recording\s+link)"
    r"|we(?:['’]re|\s+are)\s+conducting\s+(?:this|the|our|a)\s+study"
    r"|during\s+(?:this|the|our)\s+interview\s+i\s+(?:will|shall|am\s+going\s+to)\s+ask"
    r"|(?:now|next)\s+i\s+(?:will|shall|am\s+going\s+to)\s+(?:name|introduce)"
    r"|(?:share|sharing)\s+my\s+screen"
    r"|(?:goal|purpose)\s+of\s+(?:this|the|our)\s+interview"
    r"|(?:can|could|are)\s+you\s+(?:able\s+to\s+)?(?:hear|see)\s+(?:me|my\s+screen|the\s+screen|it)"
    r")\b",
    re.IGNORECASE,
)
_BRIEF = re.compile(
    r"^(?:yes|no|yeah|yep|nope|okay|ok|sure|thanks|thank\s+you|mm\s*hm|mhm|uh\s+huh|hmm|"
    r"no\s+not\s+anymore|not\s+anymore|never|right|i\s+see)[.!?,\s]*$",
    re.IGNORECASE,
)
_CUES = {
    "negation": r"\b(?:no|not|never|neither|nor|without|cannot|\w+n['’]t)\b",
    "contrast": r"\b(?:but|however|although|whereas|instead|yet)\b",
    "condition": r"\b(?:if|unless|provided\s+that|only\s+when|as\s+long\s+as)\b",
    "cause": r"\b(?:because|therefore|due\s+to|as\s+a\s+result)\b",
    "modality": r"\b(?:may|might|could|would|should|must|can|perhaps|possibly|probably)\b",
    "temporal_change": r"\b(?:before|after|previously|anymore|no\s+longer|used\s+to|now)\b",
}
_PROTOCOL_CUE = re.compile(
    r"\b(?:before\s+we\s+(?:start|begin)|(?:my|the|our)\s+(?:next|first|follow[ -]?up)\s+question|"
    r"i\s+have\s+(?:some\s+)?follow[ -]?up\s+questions|"
    r"let\s+me\s+ask|i(?:['’]d|\s+would)\s+like\s+to\s+ask|"
    r"i(?:['’]m|\s+am)\s+(?:going\s+to|gonna)\s+(?:ask|provide\s+(?:a\s+)?(?:written\s+)?description|show\s+you)|"
    r"(?:post|posting)\s+(?:it|this|the\s+\w+)\s+(?:in|into)\s+(?:the\s+)?chat)\b",
    re.IGNORECASE,
)
_REQUEST = re.compile(
    r"\b(?:(?:can|could|would)\s+you\s+(?:tell|describe|explain|share|talk|say)\b|tell\s+me\s+about\b)",
    re.IGNORECASE,
)
_QUESTION_CLAUSE = re.compile(
    r"\b(?:(?:how|why|where|when|who)\s+(?:is|are|was|were|do|does|did|has|have|had|would|could|should|can|will)\b|"
    r"(?:what|which)(?!\s+(?:i|we|he|she|they)\b)\s+(?:[^\W\d_]+\s+){0,3}"
    r"(?:is|are|was|were|do|does|did|has|have|had|would|could|should|can|will)\b)",
    re.IGNORECASE,
)
_PAUSE = re.compile(r"\[\s*(?:\d+\s*[- ]\s*second\s+pause|pause|silence|laughs?|inaudible)\s*\]", re.IGNORECASE)


@dataclass
class _Turn:
    speaker: str
    start: int
    end: int
    header_start: int
    flags: list[str]


def build_meaning_units(documents: Sequence[TranscriptDocument], *, speaker_label_policy: str = "legacy") -> list[dict]:
    """Return full, exact source turns, including ineligible review material.

    Names must be unique because downstream provenance is keyed by source name.
    IDs and output order do not depend on the supplied document order. Offsets are
    Python Unicode character offsets into the original, unnormalized text.
    Unlabeled same-speaker paragraphs merge only across whitespace. Explicitly
    labeled turns remain separate so labels cannot become assertion wording;
    removed parser noise is never silently stitched out of a source span.
    ``explicit_roles`` opts into recorded-role-only label detection; no recorded
    participant-style label falls back to legacy parsing with a routing flag.
    Mixed named/role-style participants still require source review. Default output
    remains unchanged for frozen source/prompt compatibility.
    """
    if speaker_label_policy not in {"legacy", "explicit_roles"}:
        raise ValueError("speaker_label_policy must be legacy or explicit_roles")
    names = [document.name for document in documents]
    if len(names) != len(set(names)):
        raise ValueError("Discovery documents must have unique source names.")
    units: list[dict] = []
    for document in sorted(documents, key=lambda item: item.name):
        source = document.text
        source_hash = sha256(source.encode("utf-8")).hexdigest()
        role_labels = explicit_role_roster(source) if speaker_label_policy == "explicit_roles" else None
        segments = parse_transcript(source, document.name, speaker_label_policy=speaker_label_policy)
        turns = _contiguous_turns(source, segments)
        interviewers = ({segment.speaker for segment in segments if re.fullmatch(r"(?:interviewer|moderator)(?:\s*#?\s*\d+)?", segment.speaker, re.IGNORECASE)}
                        if role_labels else _interviewer_speakers(segments))
        prior_question: _Turn | None = None
        seen: set[tuple[int, int]] = set()
        for index, turn in enumerate(turns):
            span = (turn.start, turn.end)
            if span in seen:
                continue
            seen.add(span)
            text = source[turn.start:turn.end]
            flags = list(turn.flags)
            if speaker_label_policy == "explicit_roles":
                flags.append("speaker_label_policy_explicit_roles" if role_labels else "speaker_label_policy_legacy_fallback")
            interviewer = turn.speaker in interviewers
            unverified = turn.speaker.casefold() in {"unknown", "unknown speaker"}
            unattributed_prompt = unverified and _unattributed_prompt(text)
            if interviewer:
                flags.append("interviewer")
                if not role_labels and not _INTERVIEWER.search(turn.speaker):
                    flags.append("interviewer_inferred")
            if unverified:
                flags.append("speaker_unverified")
            if unattributed_prompt:
                flags.append("unattributed_prompt")
            if _ADMINISTRATION.search(text):
                flags.append("administration")
            if _brief_response(text):
                flags.append("brief_response")
            if _PAUSE.search(text) and _brief_response(text, allow_empty=True):
                flags.append("nonverbal_or_pause")
            if re.fullmatch(r"page\s*\d+", turn.speaker, re.IGNORECASE) and re.match(r"\s*[A-Za-z]\d+\b", text):
                flags.append("source_format_uncertain")
            if _possible_fragment(text):
                flags.append("possible_fragment")
            if re.search(r"\b(?:this|that|it|these|those|they|them)\b", text, re.IGNORECASE):
                flags.append("context_required")

            # A contiguous source window preserves all intervening words and labels.
            # The nearest question may be further away than one turn in a group.
            neighbors = turns[max(0, index - 1):index + 2]
            context_start = min(item.header_start for item in neighbors)
            if prior_question is not None:
                context_start = min(context_start, prior_question.header_start)
            context_end = max(item.end for item in neighbors)
            identity = f"{document.name}\0{source_hash}\0{turn.start}:{turn.end}"
            units.append({
                "id": f"MU-{sha256(identity.encode('utf-8')).hexdigest()[:20]}",
                "source_name": document.name,
                "speaker": turn.speaker,
                "source_start": turn.start,
                "source_end": turn.end,
                "source_hash": source_hash,
                "text": text,
                "context_start": context_start,
                "context_end": context_end,
                "context_text": source[context_start:context_end],
                "reference_context": (
                    source[prior_question.start:prior_question.end] if prior_question else ""
                ),
                "flags": flags,
                "eligible": not any(flag in flags for flag in (
                    "interviewer", "unattributed_prompt", "administration", "brief_response", "possible_fragment",
                    "nonverbal_or_pause", "source_format_uncertain",
                )),
                "relation_cues": [name for name, pattern in _CUES.items() if re.search(pattern, text, re.IGNORECASE)],
                "sentences": _sentences(text, turn.start),
            })
            if (interviewer or unattributed_prompt) and _question_or_request(text):
                prior_question = turn
    return units


def _contiguous_turns(source: str, segments: list[TranscriptSegment]) -> list[_Turn]:
    turns: list[_Turn] = []
    for segment in segments:
        if source[segment.source_start:segment.source_end] != segment.text:
            raise ValueError(f"Source range does not resolve in {segment.source_name}")
        line_start = max(source.rfind("\n", 0, segment.source_start), source.rfind("\r", 0, segment.source_start)) + 1
        # Include separate timestamp headers in context without changing assertion spans.
        prefix = source[:line_start].rstrip("\r\n")
        previous_line_start = max(prefix.rfind("\n"), prefix.rfind("\r")) + 1
        previous_line = prefix[previous_line_start:]
        timestamp_header = re.fullmatch(
            re.escape(segment.speaker) + r"\s+(?:\d+:)?\d{1,2}:\d{2}(?:\.\d+)?\s*", previous_line,
            re.IGNORECASE,
        )
        header_start = previous_line_start if timestamp_header else line_start
        turn = _Turn(segment.speaker, segment.source_start, segment.source_end, header_start, [])
        if turns:
            previous = turns[-1]
            gap = source[previous.end:turn.start]
            # Unknown paragraphs have no evidence of a shared actor. Preserve them
            # separately so an unattributed prompt cannot swallow a later account.
            same = previous.speaker.casefold() == turn.speaker.casefold() and turn.speaker != "Unknown"
            continuation = turn.speaker == "Unknown" and previous.speaker != "Unknown" and bool(gap) and gap.isspace()
            if (same and gap.isspace()) or continuation:
                previous.end = turn.end
                if continuation and "inferred_paragraph_continuation" not in previous.flags:
                    previous.flags.append("inferred_paragraph_continuation")
                continue
        turns.append(turn)
    return turns


def _interviewer_speakers(segments: list[TranscriptSegment]) -> set[str]:
    """Infer a protocol-leading role per source, never from one participant slip.

    Count original turns before paragraph merging. Initials and names have no
    special meaning: repeated questions plus protocol conduct are the evidence.
    These routing heuristics do not establish a speaker's actual identity.
    """
    speakers = {segment.speaker for segment in segments}
    excluded = {speaker for speaker in speakers if _INTERVIEWER.search(speaker)}
    for speaker in speakers - excluded:
        if _PARTICIPANT.fullmatch(speaker) or speaker.casefold().startswith("unknown"):
            continue
        texts = [segment.text for segment in segments if segment.speaker == speaker]
        questions = sum(_question_or_request(text) for text in texts)
        procedures = sum(bool(_ADMINISTRATION.search(text) or _PROTOCOL_CUE.search(text)) for text in texts)
        substantive_count = max(1, sum(not _brief_response(text) for text in texts))
        if questions >= 2 and (
            questions / substantive_count >= 0.5
            or (procedures >= 2 and questions / substantive_count >= 0.2)
        ):
            excluded.add(speaker)
    return excluded


def _question_or_request(text: str) -> bool:
    return "?" in text or "？" in text or bool(_REQUEST.search(text) or _QUESTION_CLAUSE.search(text))


def _possible_fragment(text: str) -> bool:
    normalized = text.strip()
    # Deliberately avoid declaring ordinary short or unpunctuated accounts invalid.
    return bool(
        re.search(r"(?:\.\.\.|…|[,;:]|\b(?:because|although|unless|and|but|if))\s*$", normalized, re.IGNORECASE)
        or re.fullmatch(r"(?:because|although|unless|and|but|if)[.!?]?", normalized, re.IGNORECASE)
    )


def _brief_response(text: str, allow_empty: bool = False) -> bool:
    # Classification may ignore punctuation/nonverbal annotations, but the unit
    # and all its offsets always retain the untouched source characters.
    content = re.sub(r"(?<=[.!?])\s+\d{1,4}(?=\s|$)", " ", text)
    content = re.sub(r"[.!?,;]+", " ", _PAUSE.sub(" ", content))
    content = " ".join(content.split())
    return bool(_BRIEF.fullmatch(content)) or (
        (bool(content) or allow_empty)
        and not re.sub(r"\b(?:hmm|hmm+|mmhm|mhm|mm\s+hm|uh\s+huh|um|uh)\b", "", content, flags=re.IGNORECASE).strip()
    )


def _unattributed_prompt(text: str) -> bool:
    sentences = _sentences(text, 0)
    direct_prompt = re.compile(
        r"^(?:(?:can|could|would)\s+you\s+(?:tell|describe|explain|share|talk|say)\b|tell\s+me\b)",
        re.IGNORECASE,
    )
    return bool(sentences) and (
        all(sentence["text"].rstrip().endswith(("?", "？")) for sentence in sentences)
        or any(direct_prompt.search(sentence["text"]) for sentence in sentences)
    )


def _sentences(text: str, source_start: int) -> list[dict]:
    sentences: list[dict] = []
    for match in re.finditer(r"\S[\s\S]*?(?:[.!?。！？](?=\s|$)|$)", text):
        start, end = match.span()
        while end > start and text[end - 1].isspace():
            end -= 1
        if end > start:
            sentences.append({"text": text[start:end], "source_start": source_start + start, "source_end": source_start + end})
    return sentences

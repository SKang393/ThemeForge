from __future__ import annotations

from collections import defaultdict
from bisect import bisect_right
import hashlib
from typing import Iterable
import re

from .analysis_constants import (
    BACKCHANNEL_RE,
    BRACKETED_PAUSE_RE,
    EXCLUDED_SPEAKER_RE,
    GENERIC_IMPORT_SPEAKER_RE,
    INTERVIEW_PROCEDURE_RE,
    INTERVIEW_PROMPT_RE,
    NAMED_INTERVIEW_PROMPT_RE,
    PARTICIPANT_SPEAKER_RE,
    SENTENCE_RE,
    URL_RE,
)
from .analysis_types import QuoteUnit, TranscriptSegment
from .text_utils import normalize_space, tokenize
def extract_quote_units(
    segments: Iterable[TranscriptSegment],
    min_quote_words: int = 4,
) -> list[QuoteUnit]:
    """Return only evidence eligible for the current lexical suggestion stage."""
    return [
        quote for quote in extract_evidence_units(segments, min_quote_words)
        if not quote.exclusion_reason
    ]


def extract_evidence_units(
    segments: Iterable[TranscriptSegment],
    min_quote_words: int = 4,
) -> list[QuoteUnit]:
    """Retain reviewable source spans, including passages excluded from clustering."""
    quotes: list[QuoteUnit] = []
    segment_list = list(segments)
    document_segments: defaultdict[tuple[str, str], list[TranscriptSegment]] = defaultdict(list)
    for segment in segment_list:
        document_segments[(segment.source_name, segment.source_hash)].append(segment)
    excluded_speakers = {
        key: _infer_excluded_speakers(turns)
        for key, turns in document_segments.items()
    }
    line_indexes: dict[tuple[str, str], list[int]] = {}

    for segment_index, segment in enumerate(segment_list):
        if segment.source_text and segment.source_text[segment.source_start:segment.source_end] != segment.text:
            raise ValueError(f"Source range does not resolve to the parsed turn in {segment.source_name}")
        document_key = (segment.source_name, segment.source_hash)
        spans = [
            (start, end, _sentence_exclusion(segment.text[start:end], segment.speaker, excluded_speakers[document_key]))
            for start, end in _sentence_spans(segment.text)
        ]
        if document_key not in line_indexes:
            line_indexes[document_key] = [0, *(match.end() for match in re.finditer(r"\r\n|\r|\n", segment.source_text))]
        line_starts = line_indexes[document_key]
        context_start, context_end, context_text = _context_window(segment_list, segment_index, line_starts)
        for unit_start, unit_end, reason in _meaning_unit_spans(spans):
            unit = segment.text[unit_start:unit_end]
            word_count = len(tokenize(unit, keep_stopwords=True))
            if not reason and len(tokenize(unit)) < 3:
                reason = "fewer_than_three_content_words"
            if not reason and word_count < min_quote_words:
                reason = "below_minimum_quote_words"
            source_start = segment.source_start + unit_start
            source_end = segment.source_start + unit_end
            identity = f"{segment.source_name}\0{segment.source_hash}\0{source_start}:{source_end}\0{unit}"
            source_line = (
                bisect_right(line_starts, source_start)
                if segment.source_text else segment.source_line + segment.text.count("\n", 0, unit_start)
            )
            quotes.append(
                QuoteUnit(
                    id=f"Q-{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:20]}",
                    speaker=segment.speaker,
                    text=unit,
                    source_line=source_line,
                    word_count=word_count,
                    source_name=segment.source_name,
                    source_start=source_start,
                    source_end=source_end,
                    source_hash=segment.source_hash,
                    context_start=context_start,
                    context_end=context_end,
                    context_text=context_text,
                    exclusion_reason=reason,
                )
            )

    return quotes


def _sentence_exclusion(text: str, speaker: str, excluded_speakers: set[str]) -> str:
    if speaker in excluded_speakers:
        return "excluded_speaker"
    text = BRACKETED_PAUSE_RE.sub(" ", text)
    if URL_RE.search(text):
        return "source_url"
    if _is_excluded_interview_sentence(text, speaker):
        return "interview_prompt_or_procedure"
    if _is_backchannel_text(text):
        return "brief_response_or_backchannel"
    return ""


def _sentence_spans(text: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    cursor = 0
    for boundary in [*SENTENCE_RE.finditer(text), None]:
        end = boundary.start() if boundary is not None else len(text)
        while cursor < end and text[cursor].isspace():
            cursor += 1
        while end > cursor and text[end - 1].isspace():
            end -= 1
        if end > cursor:
            spans.append((cursor, end))
        if boundary is not None:
            cursor = boundary.end()
    return spans


def _meaning_unit_spans(spans: list[tuple[int, int, str]]) -> list[tuple[int, int, str]]:
    """Pair adjacent sentences only when their eligibility agrees."""
    units: list[tuple[int, int, str]] = []
    index = 0
    while index < len(spans):
        start, end, reason = spans[index]
        if index + 1 < len(spans) and spans[index + 1][2] == reason:
            end = spans[index + 1][1]
            index += 1
        units.append((start, end, reason))
        index += 1
    return units


def _context_window(
    segments: list[TranscriptSegment], index: int, line_starts: list[int],
) -> tuple[int, int, str]:
    segment = segments[index]
    if not segment.source_text:
        return segment.source_start, segment.source_start + len(segment.text), segment.text
    neighbors = [segment]
    for neighbor_index in (index - 1, index + 1):
        if 0 <= neighbor_index < len(segments):
            neighbor = segments[neighbor_index]
            if neighbor.source_name == segment.source_name and neighbor.source_hash == segment.source_hash:
                neighbors.append(neighbor)
    earliest = min(neighbors, key=lambda item: item.source_start)
    # Include the first turn's speaker header, including a separate timestamp header.
    start = line_starts[min(max(earliest.source_line - 1, 0), len(line_starts) - 1)]
    end = max(neighbor.source_end for neighbor in neighbors)
    return start, end, segment.source_text[start:end]


def _is_excluded_speaker(speaker: str) -> bool:
    return bool(EXCLUDED_SPEAKER_RE.search(speaker))


def _is_interview_procedure_text(text: str) -> bool:
    return bool(INTERVIEW_PROCEDURE_RE.search(normalize_space(text)))


def _is_excluded_interview_sentence(text: str, speaker: str) -> bool:
    if _is_interview_procedure_text(text):
        return True
    if _looks_like_named_interview_prompt(text):
        return True
    if _is_generic_import_speaker(speaker) and _is_interview_prompt_text(text):
        return True
    return False


def _is_backchannel_text(text: str) -> bool:
    normalized = re.sub("[^a-z\uac00-\ud7a3']+", " ", text.lower()).strip()
    return bool(BACKCHANNEL_RE.match(normalized))


def _infer_excluded_speakers(segments: list[TranscriptSegment]) -> set[str]:
    speaker_segments: defaultdict[str, list[TranscriptSegment]] = defaultdict(list)
    for segment in segments:
        speaker_segments[segment.speaker].append(segment)

    excluded: set[str] = set()
    for speaker, speaker_turns in speaker_segments.items():
        if _is_excluded_speaker(speaker):
            excluded.add(speaker)
            continue
        if _is_generic_import_speaker(speaker):
            continue
        if any(_is_interview_procedure_text(turn.text) for turn in speaker_turns):
            excluded.add(speaker)
            continue
        if PARTICIPANT_SPEAKER_RE.search(speaker):
            continue

        question_turns = sum(_is_interview_prompt_text(turn.text) or "?" in turn.text for turn in speaker_turns)
        turn_count = len(speaker_turns)
        if turn_count >= 2 and question_turns >= 2 and question_turns / turn_count >= 0.5:
            excluded.add(speaker)

    return excluded


def _is_interview_prompt_text(text: str) -> bool:
    return bool(INTERVIEW_PROMPT_RE.search(normalize_space(text)))


def _is_generic_import_speaker(speaker: str) -> bool:
    return bool(GENERIC_IMPORT_SPEAKER_RE.match(normalize_space(speaker)))


def _looks_like_named_interview_prompt(text: str) -> bool:
    return bool(NAMED_INTERVIEW_PROMPT_RE.match(normalize_space(text)))


def speaker_stopwords(segments: Iterable[TranscriptSegment]) -> set[str]:
    stopwords: set[str] = set()
    for segment in segments:
        stopwords.update(tokenize(segment.speaker, keep_stopwords=True))
    return stopwords



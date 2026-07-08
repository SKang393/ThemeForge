from __future__ import annotations

from collections import defaultdict
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
    URL_RE,
)
from .analysis_types import QuoteUnit, TranscriptSegment
from .text_utils import normalize_space, split_sentences, tokenize
def extract_quote_units(
    segments: Iterable[TranscriptSegment],
    min_quote_words: int = 4,
) -> list[QuoteUnit]:
    quotes: list[QuoteUnit] = []
    next_id = 1
    segment_list = list(segments)
    excluded_speakers = _infer_excluded_speakers(segment_list)

    for segment in segment_list:
        if segment.speaker in excluded_speakers:
            continue
        usable_sentences = [
            sentence
            for sentence in split_sentences(segment.text)
            if _is_quote_sentence(sentence, segment.speaker)
        ]
        segment_text_offset = 0
        for unit in _meaning_units(usable_sentences):
            content_word_count = len(tokenize(unit))
            if content_word_count < 3:
                continue
            word_count = len(tokenize(unit, keep_stopwords=True))
            if word_count < min_quote_words:
                continue
            unit_start = segment.text.find(unit, segment_text_offset)
            if unit_start < 0:
                unit_start = segment.text.find(unit)
            if unit_start < 0:
                unit_start = 0
            unit_end = unit_start + len(unit)
            segment_text_offset = unit_end
            quotes.append(
                QuoteUnit(
                    id=f"Q{next_id:04d}",
                    speaker=segment.speaker,
                    text=unit,
                    source_line=segment.source_line,
                    word_count=word_count,
                    source_name=segment.source_name,
                    source_start=segment.source_start + unit_start,
                    source_end=segment.source_start + unit_end,
                )
            )
            next_id += 1

    return quotes


def _is_quote_sentence(text: str, speaker: str) -> bool:
    text = BRACKETED_PAUSE_RE.sub(" ", text)
    if URL_RE.search(text):
        return False
    if _is_excluded_interview_sentence(text, speaker):
        return False
    return not _is_backchannel_text(text)


def _meaning_units(sentences: list[str]) -> list[str]:
    return [
        normalize_space(" ".join(sentences[index : index + 2]))
        for index in range(0, len(sentences), 2)
    ]


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



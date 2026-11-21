from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator, Sequence
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
from .analysis_types import AnalysisSettings, QuoteUnit, SpeakerOverride, TranscriptDocument, TranscriptSegment
from .text_utils import normalize_space, split_sentences, tokenize
from .transcript_parser import document_segments


def automatic_speaker_selection(segments: list[TranscriptSegment]) -> dict[tuple[str, str], bool]:
    excluded = _infer_excluded_speakers(segments)
    return {(s.source_name, s.speaker): s.speaker not in excluded for s in segments}


def suggestion_inputs(documents: Sequence[TranscriptDocument],
                      settings: AnalysisSettings) -> tuple[list[TranscriptSegment], list[QuoteUnit]]:
    segments = document_segments(documents)
    if settings.speaker_overrides:
        names = Counter(d.name or "Transcript" for d in documents)
        available = {(s.source_name, s.speaker) for s in segments}
        for rule in settings.speaker_overrides:
            key = rule.source_name, rule.speaker
            if names[rule.source_name] != 1 or key not in available:
                raise ValueError(f"Expected one decision for an existing speaker in a unique transcript: {key!r}")
            available.remove(key)
    return segments, extract_quote_units(segments, settings.min_quote_words, settings.speaker_overrides)


def extract_quote_units(
    segments: Iterable[TranscriptSegment],
    min_quote_words: int = 4,
    speaker_overrides: Iterable[SpeakerOverride] = (),
) -> list[QuoteUnit]:
    quotes: list[QuoteUnit] = []
    next_id = 1
    segment_list = list(segments)
    included = automatic_speaker_selection(segment_list)
    included.update({(rule.source_name, rule.speaker): rule.included for rule in speaker_overrides})

    for segment in segment_list:
        if not included[segment.source_name, segment.speaker]:
            continue
        for unit_start, unit_end, source_line in _meaning_units(segment):
            unit = segment.text[unit_start:unit_end]
            content_word_count = len(tokenize(unit))
            if content_word_count < 3:
                continue
            word_count = len(tokenize(unit, keep_stopwords=True))
            if word_count < min_quote_words:
                continue
            quotes.append(
                QuoteUnit(
                    id=f"Q{next_id:04d}",
                    speaker=segment.speaker,
                    text=unit,
                    source_line=source_line,
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


def _meaning_units(segment: TranscriptSegment) -> Iterator[tuple[int, int, int]]:
    # Pair only adjacent usable sentences. Joining across an excluded sentence
    # would create an excerpt that never occurred in the transcript.
    pending: tuple[int, int, int] | None = None
    cursor = 0
    line = segment.source_line
    for sentence in split_sentences(segment.text):
        start = segment.text.index(sentence, cursor)
        line += segment.text.count("\n", cursor, start)
        cursor = start + len(sentence)
        if _is_quote_sentence(normalize_space(sentence), segment.speaker):
            if pending is None:
                pending = start, cursor, line
            else:
                yield pending[0], cursor, pending[2]
                pending = None
        elif pending is not None:
            yield pending
            pending = None
        line += sentence.count("\n")
    if pending is not None:
        yield pending


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



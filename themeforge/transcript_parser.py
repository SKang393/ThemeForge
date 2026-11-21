from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
import re

from .analysis_constants import (
    EMBEDDED_SPEAKER_LABEL_RE,
    EXCLUDED_SPEAKER_RE,
    GENERIC_IMPORT_SPEAKER_RE,
    INLINE_SPEAKER_TIMESTAMP_RE,
    OTTER_BOILERPLATE_RE,
    PDF_HEADER_RE,
    ROW_NUMBER_RE,
    SPEAKER_RE,
    SPEAKER_TIMESTAMP_RE,
    TIMESTAMP_RANGE_RE,
    TIMESTAMP_RE,
    URL_RE,
)
from .analysis_types import TranscriptDocument, TranscriptSegment
from .text_utils import normalize_space


def document_segments(documents: Iterable[TranscriptDocument]) -> list[TranscriptSegment]:
    return [turn for doc in documents for turn in parse_transcript(doc.text, doc.name or "Transcript")]


def parse_transcript(text: str, source_name: str = "Transcript") -> list[TranscriptSegment]:
    """Parse a plain text transcript into speaker turns."""
    segments: list[TranscriptSegment] = []
    current_speaker = "Unknown"
    turn_start: int | None = None
    turn_end = 0
    turn_line = 1
    current_has_speaker = False
    speaker_lookup: dict[str, str] = {}
    # Physical line headers can corroborate a multiword role with no capitalization cues.
    header_counts = Counter(normalize_space(header[1]).lower() for line in text.splitlines()
                            if (header := SPEAKER_RE.match(line)))
    cursor = 0
    cursor_line = 1

    def flush() -> None:
        nonlocal turn_start
        if turn_start is not None:
            segments.append(TranscriptSegment(current_speaker, text[turn_start:turn_end], turn_line,
                                              source_name, turn_start, turn_end))
            turn_start = None

    def speaker_name(raw_name: str) -> str:
        name = normalize_space(raw_name)
        key = name.lower()
        if key not in speaker_lookup:
            speaker_lookup[key] = name
        return speaker_lookup[key]

    def is_colon_label(raw_name: str) -> bool:
        name = normalize_space(raw_name)
        key = name.lower()
        if key in speaker_lookup or header_counts[key] >= 2:
            return True
        if EXCLUDED_SPEAKER_RE.fullmatch(name) or GENERIC_IMPORT_SPEAKER_RE.fullmatch(name):
            return True
        words = name.split()
        return len(words) == 1 or (len(words) == 2 and words[-1].isdecimal()) or all(w[0].isupper() for w in words)

    # Preparation inserts boundaries at labels and timestamps. Each nonempty
    # logical line is still a literal substring of the imported document.
    for raw_line in _prepare_transcript_text(text).splitlines():
        line = raw_line.strip()
        if not line:
            flush()
            current_has_speaker = False
            continue

        start = text.index(line, cursor)
        cursor_line += text.count("\n", cursor, start)
        cursor = start + len(line)
        timestamp_match = SPEAKER_TIMESTAMP_RE.match(line)
        if timestamp_match:
            flush()
            current_speaker = speaker_name(timestamp_match.group(1))
            current_has_speaker = True
            continue

        if _is_transcript_noise_line(line):
            flush()
            current_has_speaker = False
            continue

        speaker_match = SPEAKER_RE.match(line)
        if speaker_match and is_colon_label(speaker_match[1]):
            flush()
            current_speaker = speaker_name(speaker_match.group(1))
            current_has_speaker = True
            start += speaker_match.start(2)
        elif turn_start is None and not current_has_speaker:
            current_speaker = "Unknown"

        if start < cursor:
            if turn_start is None:
                turn_start, turn_line = start, cursor_line
            turn_end = cursor

    flush()
    return segments


def _prepare_transcript_text(text: str) -> str:
    prepared = EMBEDDED_SPEAKER_LABEL_RE.sub("\n", text)
    return INLINE_SPEAKER_TIMESTAMP_RE.sub(_split_inline_timestamp_header, prepared)


def _split_inline_timestamp_header(match: re.Match[str]) -> str:
    return f"{match.group('prefix')}\n{match.group('header')}\n"


def _is_transcript_noise_line(line: str) -> bool:
    normalized = normalize_space(line)
    lowered = normalized.lower()
    if lowered in {"timespan", "content"}:
        return True
    if PDF_HEADER_RE.match(normalized):
        return True
    if URL_RE.search(normalized):
        return True
    if OTTER_BOILERPLATE_RE.search(normalized):
        return True
    if TIMESTAMP_RE.match(normalized) or TIMESTAMP_RANGE_RE.match(normalized):
        return True
    if ROW_NUMBER_RE.match(normalized):
        return True
    return _looks_like_uppercase_table_header(normalized)


def _looks_like_uppercase_table_header(line: str) -> bool:
    if re.search(r"[.!?]", line):
        return False
    letters = [char for char in line if char.isalpha()]
    if len(letters) < 4:
        return False
    return sum(char.isupper() for char in letters) / len(letters) >= 0.8



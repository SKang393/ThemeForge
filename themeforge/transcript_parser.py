from __future__ import annotations

import re

from .analysis_constants import (
    EMBEDDED_SPEAKER_LABEL_RE,
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
from .analysis_types import TranscriptSegment
from .text_utils import normalize_space
def parse_transcript(text: str, source_name: str = "Transcript") -> list[TranscriptSegment]:
    """Parse a plain text transcript into speaker turns."""
    segments: list[TranscriptSegment] = []
    current_speaker = "Unknown"
    current_text: list[str] = []
    current_line = 1
    current_has_speaker = False
    speaker_lookup: dict[str, str] = {}
    search_offset = 0

    def flush() -> None:
        nonlocal search_offset
        if current_text:
            merged = " ".join(part.strip() for part in current_text if part.strip())
            if merged:
                source_start, source_end = _find_normalized_span(text, merged, search_offset)
                if source_end > source_start:
                    search_offset = source_end
                segments.append(
                    TranscriptSegment(
                        speaker=current_speaker.strip() or "Unknown",
                        text=normalize_space(merged),
                        source_line=current_line,
                        source_name=source_name,
                        source_start=source_start,
                        source_end=source_end,
                    )
                )

    def speaker_name(raw_name: str) -> str:
        name = normalize_space(raw_name)
        key = name.lower()
        if key not in speaker_lookup:
            speaker_lookup[key] = name
        return speaker_lookup[key]

    for line_number, raw_line in enumerate(_prepare_transcript_text(text).splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            flush()
            current_text = []
            current_has_speaker = False
            continue

        timestamp_match = SPEAKER_TIMESTAMP_RE.match(line)
        if timestamp_match:
            flush()
            current_speaker = speaker_name(timestamp_match.group(1))
            current_text = []
            current_line = line_number
            current_has_speaker = True
            continue

        if _is_transcript_noise_line(line):
            flush()
            current_text = []
            current_has_speaker = False
            continue

        speaker_match = SPEAKER_RE.match(line)
        if speaker_match:
            flush()
            current_speaker = speaker_name(speaker_match.group(1))
            current_text = [speaker_match.group(2).strip()]
            current_line = line_number
            current_has_speaker = True
        elif current_text or current_has_speaker:
            current_text.append(line)
        else:
            current_speaker = "Unknown"
            current_text = [line]
            current_line = line_number
            current_has_speaker = False

    flush()
    return segments


def _find_normalized_span(source: str, needle: str, start: int = 0) -> tuple[int, int]:
    normalized = normalize_space(needle)
    if not normalized:
        return 0, 0
    pattern = r"\s+".join(re.escape(part) for part in normalized.split())
    match = re.search(pattern, source[start:], flags=re.IGNORECASE)
    if match:
        return start + match.start(), start + match.end()
    match = re.search(pattern, source, flags=re.IGNORECASE)
    if match:
        return match.start(), match.end()
    return 0, 0


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



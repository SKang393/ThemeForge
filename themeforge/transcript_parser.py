from __future__ import annotations

import hashlib
import re
from bisect import bisect_right

from .analysis_constants import (
    EMBEDDED_SPEAKER_LABEL_RE,
    INLINE_SPEAKER_TIMESTAMP_RE,
    OTTER_BOILERPLATE_RE,
    PARTICIPANT_SPEAKER_RE,
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

_ROLE_NAME = r"(?:(?:participant|interviewee|interviewer|moderator)(?:\s*#?\s*\d+)?|p\d+)"
_ROLE_COLON = re.compile(r"^\s*(" + _ROLE_NAME + r")\s*:\s*(.+?)\s*$", re.IGNORECASE)
_ROLE_TIME = r"(?:(?:\d+:)?\d{1,2}:\d{2}(?:\.\d+)?)"
_ROLE_TIMESTAMP = re.compile(r"^\s*(" + _ROLE_NAME + r")\s+" + _ROLE_TIME + r"\s*$", re.IGNORECASE)
_ROLE_INLINE_TIMESTAMP = re.compile(
    r"(?P<prefix>^|[.!?]\s+|\n)(?P<header>" + _ROLE_NAME + r"\s+" + _ROLE_TIME + r")\s+", re.IGNORECASE | re.MULTILINE,
)
_ROLE_HEADER = re.compile(
    r"(?:^|(?<=[.!?])\s+)[ \t]*(?P<name>" + _ROLE_NAME + r")(?=\s*:\s*\S|\s+" + _ROLE_TIME + r"(?:\s|$))", re.IGNORECASE | re.MULTILINE,
)


def explicit_role_roster(text: str) -> frozenset[str]:
    """Return a roster only when participant-style labels actually occur.

    Interviewer-only labels cannot establish how participants are labeled: named
    participants must retain legacy parsing rather than become interviewer text.
    Mixed named/numbered participant styles still require explicit source review.
    """
    labels = frozenset(normalize_space(match["name"]).casefold() for match in _ROLE_HEADER.finditer(text))
    participant = r"(?:(?:participant|interviewee)(?:\s*#?\s*\d+)?|p\d+)"
    return labels if any(re.fullmatch(participant, label, re.IGNORECASE) for label in labels) else frozenset()


def parse_transcript(text: str, source_name: str = "Transcript", *, speaker_label_policy: str = "legacy") -> list[TranscriptSegment]:
    """Parse exact source turns; opt-in ``explicit_roles`` limits label detection.

    Explicit participant/interviewer/moderator/interviewee labels and P1-style
    labels form the recorded roster only if a participant-style label occurs.
    Without one, legacy parsing is retained even if interviewers are labeled.
    Ordinary colon phrases never create a role-aware speaker label. Sources
    mixing named and role-style participant labels require source review.
    """
    if speaker_label_policy not in {"legacy", "explicit_roles"}:
        raise ValueError("speaker_label_policy must be legacy or explicit_roles")
    role_labels = explicit_role_roster(text) if speaker_label_policy == "explicit_roles" else None
    role_labels = role_labels or None
    segments: list[TranscriptSegment] = []
    current_speaker = "Unknown"
    current_ranges: list[tuple[int, int]] = []
    current_line = 1
    current_has_speaker = False
    speaker_lookup: dict[str, str] = {}
    source_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    line_starts = [0, *(match.end() for match in re.finditer(r"\r\n|\r|\n", text))]

    def flush() -> None:
        if current_ranges:
            source_start, source_end = current_ranges[0][0], current_ranges[-1][1]
            segments.append(
                TranscriptSegment(
                    speaker=current_speaker.strip() or "Unknown",
                    text=text[source_start:source_end],
                    source_line=current_line,
                    source_name=source_name,
                    source_start=source_start,
                    source_end=source_end,
                    source_text=text,
                    source_hash=source_hash,
                )
            )

    def speaker_name(raw_name: str) -> str:
        name = normalize_space(raw_name)
        key = name.lower()
        if key not in speaker_lookup:
            speaker_lookup[key] = name
        return speaker_lookup[key]

    for raw_start, raw_end in _source_line_ranges(text, role_labels=role_labels):
        raw_line = text[raw_start:raw_end]
        left_trim = len(raw_line) - len(raw_line.lstrip())
        line_start = raw_start + left_trim
        line_end = raw_end - (len(raw_line) - len(raw_line.rstrip()))
        line = text[line_start:line_end]
        line_number = bisect_right(line_starts, line_start)
        if not line:
            flush()
            current_ranges = []
            current_has_speaker = False
            continue

        timestamp_match = (_ROLE_TIMESTAMP if role_labels is not None else SPEAKER_TIMESTAMP_RE).match(line)
        if timestamp_match:
            flush()
            current_speaker = speaker_name(timestamp_match.group(1))
            current_ranges = []
            current_line = line_number
            current_has_speaker = True
            continue

        if _is_transcript_noise_line(line):
            flush()
            current_ranges = []
            current_has_speaker = False
            continue

        speaker_match = (_ROLE_COLON if role_labels is not None else SPEAKER_RE).match(line)
        if speaker_match:
            flush()
            current_speaker = speaker_name(speaker_match.group(1))
            current_ranges = [(line_start + speaker_match.start(2), line_start + speaker_match.end(2))]
            current_line = line_number
            current_has_speaker = True
        elif current_ranges or current_has_speaker:
            current_ranges.append((line_start, line_end))
        else:
            current_speaker = "Unknown"
            current_ranges = [(line_start, line_end)]
            current_line = line_number
            current_has_speaker = False

    flush()
    return segments


def _source_line_ranges(text: str, *, role_labels: frozenset[str] | None = None) -> list[tuple[int, int]]:
    """Apply virtual speaker/line breaks without rewriting source characters."""
    breaks = [(match.start(), match.end()) for match in re.finditer(r"\r\n|\r|\n", text)]
    embedded = (re.compile(r"(?<=[.!?])\s+(?=" + _ROLE_NAME + r"\s*:\s*\S)", re.IGNORECASE)
                if role_labels is not None else EMBEDDED_SPEAKER_LABEL_RE)
    breaks.extend((match.start(), match.end()) for match in embedded.finditer(text))
    inline_timestamps = _ROLE_INLINE_TIMESTAMP if role_labels is not None else INLINE_SPEAKER_TIMESTAMP_RE
    for match in inline_timestamps.finditer(text):
        breaks.append((match.start("header"), match.start("header")))
        breaks.append((match.end("header"), match.end()))

    ranges: list[tuple[int, int]] = []
    cursor = 0
    for start, end in sorted(set(breaks)):
        if start < cursor:
            cursor = max(cursor, end)
            continue
        ranges.append((cursor, start))
        cursor = end
    ranges.append((cursor, len(text)))
    return ranges


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
    speaker_match = SPEAKER_RE.match(line)
    if speaker_match and PARTICIPANT_SPEAKER_RE.search(speaker_match.group(1)):
        return False
    if re.search(r"[.!?]", line):
        return False
    letters = [char for char in line if char.isalpha()]
    if len(letters) < 4:
        return False
    return sum(char.isupper() for char in letters) / len(letters) >= 0.8



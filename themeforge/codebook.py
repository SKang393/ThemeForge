from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from hashlib import sha256
from io import StringIO
from itertools import zip_longest
from pathlib import Path
from typing import TextIO

from .analysis import CodebookEntry
from .io import load_transcript_text


NAME_FIELDS = ("theme", "code", "name", "category")
DESCRIPTION_FIELDS = ("description", "definition", "memo")
EXAMPLE_FIELDS = ("example", "quote", "evidence")
CRITERIA_HEADER = re.compile(
    r"(?:include|exclude|inclusion|exclusion)(?:[\s_-]*(?:criteria|criterion|rules?|when|if))?", re.IGNORECASE
)
CSV_GUIDANCE = (
    "Use a theme, code, name, or category column with a nonblank code name. "
    "Optional definition columns are description, definition, or memo. "
    "Optional example columns are example, quote, or evidence. "
    "Use Include: and Exclude: in definitions or examples. Separate criteria and hierarchy columns are ignored."
)


def load_codebook_entries(path: str | Path) -> tuple[CodebookEntry, ...]:
    source = Path(path)
    suffix = source.suffix.lower()
    if suffix == ".csv":
        with source.open("r", encoding="utf-8-sig", newline="") as stream:
            return tuple(entry for entry, _ in _csv_codebook_rows(stream))
    if suffix not in {".txt", ".md", ".docx", ".rtf", ".pdf"}:
        raise ValueError("Choose a CSV, TXT, MD, DOCX, RTF, or text-based PDF codebook.")
    entries = _load_text_codebook(load_transcript_text(source))
    if not entries:
        raise ValueError("No named codebook entries found. Start with a code name or a line such as 'Theme: Access'.")
    return entries


@dataclass(frozen=True)
class SharedCodebook:
    file_sha256: str
    entry_numbers: dict[CodebookEntry, int | None]
    entries_with_separate_criteria: frozenset[int] = frozenset()

    @classmethod
    def from_csv(cls, path: Path) -> SharedCodebook:
        if path.suffix.lower() != ".csv":
            raise ValueError("Choose the CSV codebook your collaborator will use.")
        raw = path.read_bytes()
        rows = _csv_codebook_rows(StringIO(raw.decode("utf-8-sig"), newline=""))
        numbers = {}
        separate_criteria = set()
        for number, (entry, has_criteria) in enumerate(rows, start=1):
            numbers[entry] = None if entry in numbers else number
            if has_criteria:
                separate_criteria.add(number)
        return cls(sha256(raw).hexdigest(), numbers, frozenset(separate_criteria))


def _csv_codebook_rows(source: TextIO) -> list[tuple[CodebookEntry, bool]]:
    entries = []
    try:
        rows = csv.reader(source, strict=True)
        fields = next(rows, [])
        headers = {field.strip().lower() for field in fields}
        criteria_columns = [i for i, field in enumerate(fields) if CRITERIA_HEADER.fullmatch(field.strip())]
        if headers.isdisjoint(NAME_FIELDS):
            raise ValueError("Missing code name column. Evidence and manual-assignment exports are study reports.\n\n"
                             + CSV_GUIDANCE)
        for cells in rows:
            if len(cells) > len(fields):
                raise ValueError(f"The codebook row ending on line {rows.line_num} has more cells than the header. "
                                 "Put double quotes around fields containing commas, or remove extra separators.")
            row = dict(zip_longest(fields, cells, fillvalue=""))
            name = _first_value(row, NAME_FIELDS)
            if name:
                example = _first_value(row, EXAMPLE_FIELDS)
                entry = CodebookEntry(name=name, description=_first_value(row, DESCRIPTION_FIELDS),
                                      examples=(example,) if example else ())
                # Read by position so duplicate criterion headers cannot hide a populated cell.
                has_criteria = any(i < len(cells) and cells[i].strip() for i in criteria_columns)
                entries.append((entry, has_criteria))
    except csv.Error as exc:
        raise ValueError(f"Could not read the codebook CSV. {CSV_GUIDANCE}") from exc
    if not entries:
        raise ValueError("No named codebook entries found in the CSV rows.\n\n" + CSV_GUIDANCE)
    return entries


def _load_text_codebook(text: str) -> tuple[CodebookEntry, ...]:
    entries: list[CodebookEntry] = []
    current_name = ""
    current_description: list[str] = []
    current_examples: list[str] = []

    def flush() -> None:
        nonlocal current_name, current_description, current_examples
        if current_name:
            entries.append(
                CodebookEntry(
                    name=current_name,
                    description=" ".join(current_description),
                    examples=tuple(current_examples),
                )
            )
        current_name = ""
        current_description = []
        current_examples = []

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            flush()
            continue
        label, separator, value = line.partition(":")
        normalized_label = label.strip().lower()
        if separator and normalized_label in NAME_FIELDS:
            flush()
            current_name = value.strip()
        elif separator and normalized_label in DESCRIPTION_FIELDS:
            if value.strip():
                current_description.append(value.strip())
        elif separator and normalized_label in EXAMPLE_FIELDS:
            if value.strip():
                current_examples.append(value.strip())
        elif current_name:
            current_description.append(line)
        else:
            current_name = line
    flush()
    return tuple(entries)


def _first_value(row: dict[str, str], names: tuple[str, ...]) -> str:
    lowered = {key.strip().lower(): value.strip() for key, value in row.items() if key and value}
    for name in names:
        if lowered.get(name):
            return lowered[name]
    return ""

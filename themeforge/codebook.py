from __future__ import annotations

import csv
from pathlib import Path

from .analysis import CodebookEntry
from .io import load_transcript_text


NAME_FIELDS = ("theme", "code", "name", "category")
DESCRIPTION_FIELDS = ("description", "definition", "memo")
EXAMPLE_FIELDS = ("example", "quote", "evidence")


def load_codebook_entries(path: str | Path) -> tuple[CodebookEntry, ...]:
    source = Path(path)
    if source.suffix.lower() == ".csv":
        return _load_csv_codebook(source)
    return _load_text_codebook(load_transcript_text(source))


def _load_csv_codebook(path: Path) -> tuple[CodebookEntry, ...]:
    with path.open("r", encoding="utf-8-sig", newline="") as source:
        rows = list(csv.DictReader(source))
    entries: list[CodebookEntry] = []
    for row in rows:
        name = _first_value(row, NAME_FIELDS)
        if not name:
            continue
        entries.append(
            CodebookEntry(
                name=name,
                description=_first_value(row, DESCRIPTION_FIELDS),
                examples=tuple(value for value in [_first_value(row, EXAMPLE_FIELDS)] if value),
            )
        )
    return tuple(entries)


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
        label, _, value = line.partition(":")
        normalized_label = label.strip().lower()
        if normalized_label in NAME_FIELDS and value.strip():
            flush()
            current_name = value.strip()
        elif normalized_label in DESCRIPTION_FIELDS and value.strip():
            current_description.append(value.strip())
        elif normalized_label in EXAMPLE_FIELDS and value.strip():
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
        if name in lowered:
            return lowered[name]
    return ""

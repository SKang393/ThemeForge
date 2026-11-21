from __future__ import annotations

import csv
from pathlib import Path

from .analysis import CodebookEntry
from .io import load_transcript_text


NAME_FIELDS = ("theme", "code", "name", "category")
DESCRIPTION_FIELDS = ("description", "definition", "memo")
EXAMPLE_FIELDS = ("example", "quote", "evidence")
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
        try:
            return _load_csv_codebook(source)
        except csv.Error as exc:
            raise ValueError(f"Could not read the codebook CSV. {CSV_GUIDANCE}") from exc
    if suffix not in {".txt", ".md", ".docx", ".rtf", ".pdf"}:
        raise ValueError("Choose a CSV, TXT, MD, DOCX, RTF, or text-based PDF codebook.")
    entries = _load_text_codebook(load_transcript_text(source))
    if not entries:
        raise ValueError("No named codebook entries found. Start with a code name or a line such as 'Theme: Access'.")
    return entries


def _load_csv_codebook(path: Path) -> tuple[CodebookEntry, ...]:
    entries: list[CodebookEntry] = []
    with path.open("r", encoding="utf-8-sig", newline="") as source:
        rows = csv.DictReader(source, strict=True)
        headers = {field.strip().lower() for field in rows.fieldnames or ()}
        if headers.isdisjoint(NAME_FIELDS):
            raise ValueError("Missing code name column. Evidence and manual-assignment exports are study reports.\n\n"
                             + CSV_GUIDANCE)
        for row in rows:
            name = _first_value(row, NAME_FIELDS)
            if name:
                example = _first_value(row, EXAMPLE_FIELDS)
                entries.append(CodebookEntry(
                    name=name, description=_first_value(row, DESCRIPTION_FIELDS),
                    examples=(example,) if example else (),
                ))
    if not entries:
        raise ValueError("No named codebook entries found in the CSV rows.\n\n" + CSV_GUIDANCE)
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

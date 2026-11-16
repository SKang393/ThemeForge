from __future__ import annotations

import csv
from pathlib import Path

from .analysis_types import CodebookEntry
from .io import load_transcript_text


NAME_FIELDS = ("theme", "code", "name", "category")
DESCRIPTION_FIELDS = ("description", "definition", "memo")
EXAMPLE_FIELDS = ("examples", "example", "quote", "evidence", "positive_examples")
METADATA_FIELDS = {
    "code_id": ("code_id", "id"),
    "version": ("version", "code_version"),
    "inclusion_criteria": ("inclusion_criteria", "inclusion", "include"),
    "exclusion_criteria": ("exclusion_criteria", "exclusion", "exclude"),
    "negative_examples": ("negative_examples", "negative_example", "counterexamples"),
    "origin": ("origin",),
}


def load_codebook_entries(path: str | Path) -> tuple[CodebookEntry, ...]:
    source = Path(path)
    entries = (
        _load_csv_codebook(source)
        if source.suffix.lower() == ".csv"
        else _load_text_codebook(load_transcript_text(source))
    )
    ids = [entry.code_id for entry in entries]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate code IDs in codebook. Use one row per code or review distinct IDs explicitly.")
    return entries


def _load_csv_codebook(path: Path) -> tuple[CodebookEntry, ...]:
    with path.open("r", encoding="utf-8-sig", newline="") as source:
        rows = list(csv.DictReader(source))
    entries: list[CodebookEntry] = []
    for row in rows:
        name = _first_value(row, NAME_FIELDS)
        if not name:
            continue
        entries.append(
            _make_entry(name, _first_value(row, DESCRIPTION_FIELDS),
                        _examples(_first_value(row, EXAMPLE_FIELDS)),
                        {key: _first_value(row, aliases) for key, aliases in METADATA_FIELDS.items()})
        )
    return tuple(entries)


def _load_text_codebook(text: str) -> tuple[CodebookEntry, ...]:
    entries: list[CodebookEntry] = []
    current_name = ""
    current_description: list[str] = []
    current_examples: list[str] = []
    current_metadata: dict[str, str] = {}

    def flush() -> None:
        nonlocal current_name, current_description, current_examples, current_metadata
        if current_name:
            entries.append(
                _make_entry(current_name, " ".join(current_description),
                            tuple(current_examples), current_metadata)
            )
        current_name = ""
        current_description = []
        current_examples = []
        current_metadata = {}

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            flush()
            continue
        label, _, value = line.partition(":")
        normalized_label = _field_name(label)
        if normalized_label in NAME_FIELDS and value.strip():
            flush()
            current_name = value.strip()
        elif normalized_label in DESCRIPTION_FIELDS and value.strip():
            current_description.append(value.strip())
        elif normalized_label in EXAMPLE_FIELDS and value.strip():
            current_examples.extend(_examples(value))
        elif current_name and any(normalized_label in aliases for aliases in METADATA_FIELDS.values()):
            key = next(key for key, aliases in METADATA_FIELDS.items() if normalized_label in aliases)
            if key == "negative_examples" and key in current_metadata:
                current_metadata[key] += " || " + value.strip()
            else:
                current_metadata[key] = value.strip()
        elif current_name:
            current_description.append(line)
        else:
            current_name = line
    flush()
    return tuple(entries)


def _first_value(row: dict[str, str], names: tuple[str, ...]) -> str:
    lowered = {_field_name(key): value.strip() for key, value in row.items() if key and value}
    for name in names:
        if name in lowered:
            return lowered[name]
    return ""


def _field_name(value: str) -> str:
    return value.strip().lower().replace("-", "_").replace(" ", "_")


def _examples(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in value.split("||") if item.strip())


def _make_entry(name: str, description: str, examples: tuple[str, ...], metadata: dict[str, str]) -> CodebookEntry:
    try:
        version = int(metadata.get("version", "") or "1")
    except ValueError:
        raise ValueError(f"Code version for {name!r} must be a positive integer.") from None
    return CodebookEntry(
        name=name, description=description, examples=examples,
        code_id=metadata.get("code_id", ""), version=version,
        inclusion_criteria=metadata.get("inclusion_criteria", ""),
        exclusion_criteria=metadata.get("exclusion_criteria", ""),
        negative_examples=_examples(metadata.get("negative_examples", "")),
        origin=metadata.get("origin", "") or "unspecified",
    )

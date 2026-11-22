"""Check a single-source supported-evidence export against files selected by its recipient."""
from __future__ import annotations

import csv
from datetime import datetime, timedelta
from hashlib import sha256
from io import StringIO
from pathlib import Path
import re
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile

from .analysis_types import TranscriptDocument, TranscriptSegment
from .audit import neutralize_csv_cell
from .codebook import SharedCodebook
from .io import load_transcript_text
from .source_location import TranscriptIndex


_REQUIRED = {
    "theme_id", "theme_name", "quote_id", "speaker", "source_file", "source_line", "quote",
    "source_start", "source_end", "source_location_status", "match_review_disposition",
    "match_review_actor", "match_review_reason", "match_review_time_utc", "match_review_target_id",
    "match_review_theme_name", "source_sha256", "shared_codebook_entry_number", "shared_codebook_file_sha256",
}


def _read_linked_rows(path: Path) -> tuple[list[dict[str, str]], str]:
    raw = path.read_bytes()
    try:
        reader = csv.DictReader(StringIO(raw.decode("utf-8-sig"), newline=""), strict=True)
        fields = reader.fieldnames or []
        if len(fields) != len(set(fields)) or not _REQUIRED.issubset(fields):
            raise ValueError("Choose a supported CSV exported with codebook links. "
                             "The evidence needs source offsets, a recorded review, and file hashes.")
        rows = list(reader)
    except (UnicodeDecodeError, csv.Error) as exc:
        raise ValueError("Could not read the evidence as a UTF-8 CSV. Choose the original export.") from exc
    if not rows:
        raise ValueError("The evidence CSV has no supported rows to check.")
    for number, row in enumerate(rows, 1):
        if None in row or any(not row[field] or not row[field].strip() for field in _REQUIRED):
            raise ValueError(f"Evidence row {number} has missing fields or unexpected cells.")
    if len({row["source_file"] for row in rows}) != 1:
        raise ValueError("The CSV contains multiple recorded sources. Export one selected transcript "
                         "with codebook links to check it against one file.")
    return rows, sha256(raw).hexdigest()


def _check_review(row: dict[str, str], number: int) -> None:
    if row["match_review_disposition"] != "supported" or row["source_location_status"] != "exact_span":
        raise ValueError(f"Evidence row {number} lacks a supported review with an exact recorded span.")
    if not re.fullmatch(r"[0-9a-f]{64}", row["match_review_target_id"]):
        raise ValueError(f"Evidence row {number} has an invalid recorded review target.")
    try:
        stamp = datetime.fromisoformat(row["match_review_time_utc"].replace("Z", "+00:00"))
        if stamp.utcoffset() != timedelta(0):
            raise ValueError
    except ValueError as exc:
        raise ValueError(f"Evidence row {number} needs a valid UTC review time.") from exc
    # The current display label can differ from the reviewed one in word spacing.
    # The export spreadsheet-escapes the reviewed label, but leaves the current label literal.
    label = " ".join(neutralize_csv_cell(row["theme_name"]).split())
    if " ".join(row["match_review_theme_name"].split()) != label:
        raise ValueError(f"Evidence row {number} has inconsistent recorded theme labels.")


def verify_handoff(evidence_path: Path, transcript_path: Path, codebook_path: Path) -> str:
    """Return a read-only comparison. No filename from a CSV is opened or used to locate another source."""
    rows, evidence_digest = _read_linked_rows(evidence_path)
    recorded = rows[0]["source_file"]
    try:
        text = load_transcript_text(transcript_path)
    except (BadZipFile, KeyError, ParseError) as exc:
        raise ValueError("Could not extract the selected transcript. Choose a readable source revision.") from exc
    sources = TranscriptIndex([TranscriptDocument(recorded, text)])
    digest = sources.digest(recorded)
    if any(row["source_sha256"] != digest for row in rows):
        raise ValueError("The selected transcript's extracted text differs from the recorded source SHA-256. "
                         "Choose the revision used for review.")
    book = SharedCodebook.from_csv(codebook_path)
    if any(row["shared_codebook_file_sha256"] != book.file_sha256 for row in rows):
        raise ValueError("The selected codebook differs from the recorded file SHA-256. "
                         "Choose the exact shared CSV used for export.")
    entries = {n: entry for entry, n in book.entry_numbers.items() if n is not None}
    seen = set()
    linked_entries = {}
    details = []
    for number, row in enumerate(rows, 1):
        _check_review(row, number)
        try:
            line, start, end, entry_number = (int(row[k]) for k in (
                "source_line", "source_start", "source_end", "shared_codebook_entry_number"))
        except ValueError as exc:
            raise ValueError(f"Evidence row {number} has a non-integer citation or codebook entry number.") from exc
        passage = TranscriptSegment(row["speaker"], row["quote"], line, recorded, start, end)
        location = sources.locate(passage)
        if location.status != "exact_span":
            raise ValueError(f"Evidence row {number}: {location.note}")
        entry = entries.get(entry_number)
        if entry is None or entry_number in book.entries_with_separate_criteria:
            raise ValueError(f"Evidence row {number} needs a unique codebook entry with supported criteria.")
        target = row["match_review_target_id"]
        if target in seen:
            raise ValueError(f"Evidence row {number} repeats a recorded review target.")
        seen.add(target)
        linked_entries[entry_number] = entry
        details.extend([
            f"Row {number} · line {line} · characters [{start}, {end}) · entry {entry_number}: {entry.name}",
            row["quote"],
            f"Recorded theme: {row['match_review_theme_name']} ({row['theme_id']}) · quote {row['quote_id']}",
            f"Recorded speaker: {row['speaker']}",
            f"Recorded support: {row['match_review_actor']} · {row['match_review_time_utc']}",
            f"Recorded reason: {row['match_review_reason']}",
            f"Recorded review target: {target}", "",
        ])
    definitions = []
    for number, entry in sorted(linked_entries.items()):
        definitions.append(f"Shared entry {number}: {entry.name}")
        if entry.description:
            definitions.append(f"Definition: {entry.description}")
        definitions.extend(f"Example: {example}" for example in entry.examples)
        definitions.append("")
    return "\n".join([
        f"Checked {len(rows)} supported {'row' if len(rows) == 1 else 'rows'} against the selected files.",
        f"Recorded source: {recorded}", f"Selected transcript: {transcript_path}",
        f"Extracted text SHA-256: {digest}", f"Selected codebook: {codebook_path.name}",
        f"Codebook file SHA-256: {book.file_sha256}", f"Evidence CSV SHA-256: {evidence_digest}", "",
        "Compare the evidence CSV SHA-256 with a digest obtained independently from the sender. "
        "The source and codebook match the recorded hashes. Each quoted span and linked entry was located. "
        "The CSV alone cannot authenticate the reviewer, prove that decision fields are unaltered, "
        "or establish which entry was originally reviewed. The original target and complete history "
        "require separate records.", "",
        *definitions, *details,
    ]).rstrip()

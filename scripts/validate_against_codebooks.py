# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
# How to run:
#   python scripts/validate_against_codebooks.py --root validation

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
import re
import sys
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from themeforge.analysis import (
    AnalysisResult,
    AnalysisSettings,
    CodebookEntry,
    TranscriptDocument,
    analyze_documents,
    extract_quote_units,
    parse_transcript,
)
from themeforge.io import load_transcript_text


TOKEN_RE = re.compile(r"[A-Za-z가-힣][A-Za-z0-9가-힣'-]*")
TRANSCRIPT_BOILERPLATE_RE = re.compile(r"https?://|otter\.ai|\btranscribed\s+by\b", re.IGNORECASE)
BARE_TIMESTAMP_RE = re.compile(r"^(?:\d+:)?\d{1,2}:\d{2}(?:\.\d+)?$")
KNOWN_NAME_LEAKS = {
    "charissa",
    "david",
    "marie",
    "voorhis",
    "unknown speaker",
}


@dataclass(frozen=True, slots=True)
class DatasetSpec:
    name: str
    transcript_globs: tuple[str, ...]
    ground_truth_themes: tuple[str, ...]
    codebook_globs: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class LoadedDataset:
    documents: tuple[TranscriptDocument, ...]
    skipped_inputs: tuple[str, ...]


DATASETS = (
    DatasetSpec(
        name="Adult day service curriculum",
        transcript_globs=(
            "Adult day service curriculum/Interview Sessions/DSPs/*/*.txt",
            "Adult day service curriculum/Interview Sessions/ADMIN/*.docx",
            "Adult day service curriculum/Interview Sessions/ADMIN/*/*.docx",
        ),
        ground_truth_themes=(
            "Relevant Strengths of Wabash Center",
            "Training Needs of Stakeholders",
            "Curriculum Development",
            "Community Participation",
            "Implementation of the practice",
        ),
        codebook_globs=("Adult day service curriculum/Interview Sessions/Combined code book.docx",),
    ),
    DatasetSpec(
        name="4-H",
        transcript_globs=("4-H/FG_Extension ed_09-24-19_2_Ayna.docx",),
        ground_truth_themes=(
            "Enrollment process and barriers",
            "Educator perspectives",
            "Educator challenges",
            "Accommodations",
            "Active strategies",
            "Outreach",
            "New supports and resources",
            "New trainings",
        ),
        codebook_globs=("4-H/Mccormick 2022.pdf",),
    ),
    DatasetSpec(
        name="Autism and kindness",
        transcript_globs=("autism kindness/Anonymised Transcripts/*.pdf",),
        ground_truth_themes=(
            "Personal Experiential Themes",
            "General Experiential Themes",
            "Autism and kindness",
        ),
        codebook_globs=(
            "autism kindness/General Experiential Themes (GETs)/*.pdf",
            "autism kindness/Personal Experiential Themes (PETs)/*.pdf",
        ),
    ),
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run ThemeForge validation metrics against local qualitative-study datasets."
    )
    parser.add_argument("--root", default="validation", help="Validation data root directory.")
    parser.add_argument("--themes", type=int, default=8, help="Theme count for each dataset run.")
    args = parser.parse_args()

    root = Path(args.root)
    for spec in DATASETS:
        print_dataset_report(spec, root, max(1, args.themes))
    return 0


def print_dataset_report(spec: DatasetSpec, root: Path, theme_count: int) -> None:
    loaded = load_dataset(spec, root)
    print(f"Dataset: {spec.name}")
    print(f"  Inputs loaded: {len(loaded.documents)}")
    if loaded.skipped_inputs:
        print(f"  Inputs skipped: {len(loaded.skipped_inputs)}")
        for skipped in loaded.skipped_inputs[:5]:
            print(f"    - {skipped}")
    if not loaded.documents:
        print("  Status: no analyzable transcripts found")
        print()
        return

    settings = AnalysisSettings(theme_count=theme_count, quotes_per_theme=3, min_theme_size=1)
    result = analyze_documents(loaded.documents, settings)
    quote_units = [
        quote
        for document in loaded.documents
        for quote in extract_quote_units(parse_transcript(document.text, document.name))
    ]
    generated_text = generated_theme_text(result)
    leak_terms = sorted(term for term in KNOWN_NAME_LEAKS if term in generated_text.lower())
    artifact_count = sum(1 for quote in quote_units if has_transcript_artifact(quote.text))
    named_quotes = sum(1 for quote in quote_units if quote.speaker.lower() not in {"unknown", "unknown speaker"})
    named_percent = round((named_quotes / max(1, len(quote_units))) * 100, 1)

    print("  Parsing sanity:")
    print(f"    Quote units: {len(quote_units)}")
    print(f"    Named speaker quote units: {named_percent}%")
    print(f"    Transcript artifact quote units: {artifact_count}")
    print("  Name leakage:")
    print(f"    Known leak terms: {', '.join(leak_terms) if leak_terms else 'none'}")
    print("  Theme coverage:")
    for expected in spec.ground_truth_themes:
        best_name, best_score = best_theme_match(expected, result)
        print(f"    {expected}: {best_score:.3f} best={best_name}")
    codebook_entries = load_codebook_entries(spec, root)
    if codebook_entries:
        codebook_settings = AnalysisSettings(
            theme_count=theme_count,
            quotes_per_theme=3,
            min_theme_size=1,
            codebook_entries=codebook_entries,
        )
        codebook_result = analyze_documents(loaded.documents, codebook_settings)
        print("  Codebook-assisted coverage:")
        print(f"    Codebook entries: {len(codebook_entries)}")
        for expected in spec.ground_truth_themes:
            best_name, best_score = best_theme_match(expected, codebook_result)
            print(f"    {expected}: {best_score:.3f} best={best_name}")
        print("  Quote plausibility:")
        for entry in codebook_entries[:5]:
            theme = next((theme for theme in codebook_result.themes if theme.name == entry.name), None)
            status = "matched" if theme and theme.quotes else "missing"
            print(f"    {entry.name}: {status}")
        matched_examples, total_examples = example_quote_matches(codebook_entries, loaded.documents)
        print(f"    Example quote token matches: {matched_examples}/{total_examples}")
    print("  Generated themes:")
    for theme in result.themes:
        print(f"    {theme.id} {theme.name}: {', '.join(theme.keywords[:3])}")
    print()


def load_dataset(spec: DatasetSpec, root: Path) -> LoadedDataset:
    documents: list[TranscriptDocument] = []
    skipped: list[str] = []
    for relative_pattern in spec.transcript_globs:
        for path in sorted(root.glob(relative_pattern)):
            try:
                text = load_transcript_text(path)
            except ValueError as exc:
                skipped.append(f"{path}: {exc}")
                continue
            if text.strip():
                documents.append(TranscriptDocument(name=path.name, text=text))
    return LoadedDataset(documents=tuple(documents), skipped_inputs=tuple(skipped))


def load_codebook_entries(spec: DatasetSpec, root: Path) -> tuple[CodebookEntry, ...]:
    codebook_parts: list[str] = []
    for relative_pattern in spec.codebook_globs:
        for path in sorted(root.glob(relative_pattern)):
            try:
                codebook_parts.append(load_transcript_text(path))
            except (OSError, ValueError, zipfile.BadZipFile):
                continue
    codebook_text = "\n".join(codebook_parts)
    if not codebook_text.strip():
        return tuple(CodebookEntry(name=theme, description=theme) for theme in spec.ground_truth_themes)
    return tuple(
        CodebookEntry(
            name=theme,
            description=theme_section(codebook_text, theme, spec.ground_truth_themes),
        )
        for theme in spec.ground_truth_themes
    )


def theme_section(text: str, theme: str, themes: tuple[str, ...]) -> str:
    lowered = text.lower()
    start = lowered.find(theme.lower())
    if start < 0:
        return theme
    end = len(text)
    for other_theme in themes:
        if other_theme == theme:
            continue
        other_start = lowered.find(other_theme.lower(), start + len(theme))
        if other_start >= 0:
            end = min(end, other_start)
    return text[start:end].strip() or theme


def example_quote_matches(
    entries: tuple[CodebookEntry, ...],
    documents: tuple[TranscriptDocument, ...],
) -> tuple[int, int]:
    corpus_tokens = set(tokens(" ".join(document.text for document in documents)))
    examples = [
        line.split("Example:", 1)[1]
        for entry in entries
        for line in entry.description.splitlines()
        if line.strip().startswith("Example:")
    ]
    matched = 0
    for example in examples:
        example_tokens = tokens(example)[:18]
        if example_tokens and len(set(example_tokens) & corpus_tokens) >= min(5, len(set(example_tokens))):
            matched += 1
    return matched, len(examples)


def has_transcript_artifact(text: str) -> bool:
    normalized = " ".join(text.split())
    return bool(TRANSCRIPT_BOILERPLATE_RE.search(normalized) or BARE_TIMESTAMP_RE.match(normalized))


def generated_theme_text(result: AnalysisResult) -> str:
    return " ".join(
        [
            *(theme.name for theme in result.themes),
            *(keyword for theme in result.themes for keyword in theme.keywords),
            *(quote.text for theme in result.themes for quote in theme.quotes),
        ]
    )


def best_theme_match(expected_theme: str, result: AnalysisResult) -> tuple[str, float]:
    expected_tokens = set(tokens(expected_theme))
    best_name = "none"
    best_score = 0.0
    for theme in result.themes:
        candidate_tokens = set(tokens(" ".join([theme.name, *theme.keywords, *(quote.text for quote in theme.quotes)])))
        score = jaccard(expected_tokens, candidate_tokens)
        if score > best_score:
            best_name = theme.name
            best_score = score
    return best_name, best_score


def tokens(text: str) -> list[str]:
    return [match.group(0).lower() for match in TOKEN_RE.finditer(text) if len(match.group(0)) > 2]


def jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


if __name__ == "__main__":
    raise SystemExit(main())

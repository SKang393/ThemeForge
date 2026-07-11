# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
# How to run:
#   python scripts/validate_against_codebooks.py --root validation

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
import re
import sys
import tempfile
import zipfile
from xml.etree import ElementTree

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from themeforge.analysis import (
    AnalysisResult,
    AnalysisSettings,
    CodebookEntry,
    Theme,
    ThemeQuote,
    TranscriptDocument,
    analyze_documents,
    extract_quote_units,
    parse_transcript,
)
from themeforge.io import load_transcript_text
from themeforge.project_io import ProjectState, load_project, save_project
from themeforge.rigor import CodedProject, CoderLabels, IntercoderReliabilityReport, intercoder_reliability


TOKEN_RE = re.compile(r"[A-Za-z가-힣][A-Za-z0-9가-힣'-]*")
TRANSCRIPT_BOILERPLATE_RE = re.compile(r"https?://|otter\.ai|\btranscribed\s+by\b", re.IGNORECASE)
BARE_TIMESTAMP_RE = re.compile(r"^(?:\d+:)?\d{1,2}:\d{2}(?:\.\d+)?$")
MATCH_TOKEN_RE = re.compile(r"[A-Za-z0-9]+(?:['’][A-Za-z0-9]+)?")
WORD_NAMESPACE = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
WORD_NS = {"w": WORD_NAMESPACE}
WORD_ID = f"{{{WORD_NAMESPACE}}}id"
WORD_AUTHOR = f"{{{WORD_NAMESPACE}}}author"
KNOWN_NAME_LEAKS = {
    "charissa",
    "david",
    "drm",
    "marie",
    "md",
    "moderator",
    "samira",
    "voorhis",
    "unknown speaker",
}
NON_THEME_LABELS = {
    "(deleted code)",
    "[consensus]",
    "code definition needs expansion",
    "n/a",
    "needs second review",
    "no matching code",
    "retrieving data. wait a few seconds and try to cut or copy again.",
}


@dataclass(frozen=True, slots=True)
class DatasetSpec:
    name: str
    transcript_globs: tuple[str, ...]
    ground_truth_themes: tuple[str, ...]
    expected_inputs: int
    codebook_globs: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class LoadedDataset:
    documents: tuple[TranscriptDocument, ...]
    skipped_inputs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CodedSpan:
    theme_name: str
    source_name: str
    text: str
    source_start: int
    source_end: int
    author: str = ""

    @property
    def resolved(self) -> bool:
        return bool(self.source_name and self.source_start >= 0 and self.source_end > self.source_start)


@dataclass(frozen=True, slots=True)
class QuoteMetric:
    theme_name: str
    true_positive: int
    false_positive: int
    false_negative: int
    precision: float
    recall: float


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
        expected_inputs=10,
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
        expected_inputs=1,
        codebook_globs=("4-H/Mccormick 2022.pdf",),
    ),
    DatasetSpec(
        name="Familiarity and perceptions of preference assessment",
        transcript_globs=(
            "Familiarity and perceptions of preference assessment/De-Identified Interviews/De-Identified Interviews for Coding/P5/P5 De-Identified Interview (1).docx",
            "Familiarity and perceptions of preference assessment/De-Identified Interviews/De-Identified Interviews for Coding/P16/P16 Transcript (Original).docx",
            "Familiarity and perceptions of preference assessment/De-Identified Interviews/De-Identified Interviews for Coding/P18/Interview Transcript 18.docx",
            "Familiarity and perceptions of preference assessment/De-Identified Interviews/De-Identified Interviews for Coding/P19/P19 Transcript.docx",
            "Familiarity and perceptions of preference assessment/De-Identified Interviews/De-Identified Interviews for Coding/P20/P20 Transcript.docx",
        ),
        ground_truth_themes=(
            "General preference assessment",
            "Free operant preference assessment",
            "Multiple stimulus without replacement",
            "Paired stimulus preference assessment",
            "Single stimulus preference assessment",
        ),
        expected_inputs=5,
    ),
    DatasetSpec(
        name="Autism and kindness",
        transcript_globs=("autism kindness/Anonymised Transcripts/*.pdf",),
        ground_truth_themes=(
            "Personal Experiential Themes",
            "General Experiential Themes",
            "Autism and kindness",
        ),
        expected_inputs=10,
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
    failures: list[str] = []
    for spec in DATASETS:
        try:
            failures.extend(print_dataset_report(spec, root, max(1, args.themes)))
        except Exception as exc:  # noqa: BLE001 - validation boundary must return a failing gate, not a traceback.
            message = f"{spec.name}: validation crashed: {exc}"
            failures.append(message)
            print(f"  Status: {message}")
            print()
    try:
        failures.extend(print_reliability_validation(root))
    except Exception as exc:  # noqa: BLE001 - validation boundary must return a failing gate, not a traceback.
        failures.append(f"Intercoder reliability: validation crashed: {exc}")
    if failures:
        print("Validation failures:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    return 0


def print_dataset_report(spec: DatasetSpec, root: Path, theme_count: int) -> tuple[str, ...]:
    loaded = load_dataset(spec, root)
    failures: list[str] = []
    print(f"Dataset: {spec.name}")
    print(f"  Inputs loaded: {len(loaded.documents)}")
    print(f"  Inputs skipped: {len(loaded.skipped_inputs)}")
    for skipped in loaded.skipped_inputs[:5]:
        print(f"    - {skipped}")
    if len(loaded.documents) != spec.expected_inputs:
        failures.append(f"{spec.name}: expected {spec.expected_inputs} inputs, loaded {len(loaded.documents)}")
    if loaded.skipped_inputs:
        failures.append(f"{spec.name}: skipped {len(loaded.skipped_inputs)} inputs")
    if not loaded.documents:
        print("  Status: no analyzable transcripts found")
        print()
        failures.append(f"{spec.name}: no analyzable transcripts found")
        return tuple(failures)

    expected_themes = spec.ground_truth_themes
    if spec.name in {"4-H", "Autism and kindness"}:
        try:
            expected_themes = published_theme_names(spec, root)
        except (OSError, ValueError, IndexError) as exc:
            failures.append(f"{spec.name}: published theme extraction failed: {exc}")
            print(f"  Published theme extraction: failed ({exc})")

    settings = AnalysisSettings(theme_count=theme_count, quotes_per_theme=3, min_theme_size=1)
    result = analyze_documents(loaded.documents, settings)
    codebook_entries = load_codebook_entries(spec, root, expected_themes)
    codebook_result = (
        analyze_documents(
            loaded.documents,
            AnalysisSettings(
                theme_count=theme_count,
                quotes_per_theme=3,
                min_theme_size=1,
                codebook_entries=codebook_entries,
            ),
        )
        if codebook_entries
        else None
    )
    quote_units = [
        quote
        for document in loaded.documents
        for quote in extract_quote_units(parse_transcript(document.text, document.name))
    ]
    generated_text = generated_theme_text(result)
    if codebook_result is not None:
        generated_text += "\n" + generated_theme_text(codebook_result)
    leak_terms = find_name_leaks(generated_text)
    artifact_count = sum(1 for quote in quote_units if has_transcript_artifact(quote.text))
    named_quotes = sum(1 for quote in quote_units if quote.speaker.lower() not in {"unknown", "unknown speaker"})
    named_percent = round((named_quotes / max(1, len(quote_units))) * 100, 1)

    print("  Parsing sanity:")
    print(f"    Quote units: {len(quote_units)}")
    print(f"    Named speaker quote units: {named_percent}%")
    print(f"    Transcript artifact quote units: {artifact_count}")
    print("  Name leakage:")
    print(f"    Known leak terms: {', '.join(leak_terms) if leak_terms else 'none'}")
    if artifact_count:
        failures.append(f"{spec.name}: found {artifact_count} transcript artifact quote units")
    if leak_terms:
        failures.append(f"{spec.name}: known name leaks found: {', '.join(leak_terms)}")
    print("  Theme coverage:")
    if expected_themes != spec.ground_truth_themes:
        print(f"    Published PDF themes extracted: {len(expected_themes)}")
    for expected in expected_themes:
        best_name, best_score = best_theme_match(expected, result)
        print(f"    {expected}: {best_score:.3f} best={best_name}")
    if codebook_result is not None:
        print("  Codebook-assisted coverage:")
        print(f"    Codebook entries: {len(codebook_entries)}")
        for expected in expected_themes:
            best_name, best_score = best_theme_match(expected, codebook_result)
            print(f"    {expected}: {best_score:.3f} best={best_name}")
        print("  Quote plausibility:")
        for entry in codebook_entries[:5]:
            theme = next((theme for theme in codebook_result.themes if theme.name == entry.name), None)
            status = "matched" if theme and theme.quotes else "missing"
            print(f"    {entry.name}: {status}")
        matched_examples, total_examples = example_quote_matches(codebook_entries, loaded.documents)
        print(f"    Example quote token matches: {matched_examples}/{total_examples}")
    quote_validation = load_quote_validation(spec.name, root)
    if quote_validation is not None:
        quote_documents, ground_truth = quote_validation
        metrics = print_quote_metrics(quote_documents, ground_truth)
        unresolved = sum(not span.resolved for span in ground_truth)
        if not ground_truth:
            failures.append(f"{spec.name}: no quote-level ground truth loaded")
        if len(metrics) != len({_normalize_theme_name(span.theme_name) for span in ground_truth}):
            failures.append(f"{spec.name}: quote metrics missing ground-truth themes")
        if unresolved:
            failures.append(f"{spec.name}: {unresolved} quote-level passages unresolved")
    elif spec.name in {"Adult day service curriculum", "Familiarity and perceptions of preference assessment"}:
        failures.append(f"{spec.name}: quote-level ground truth missing")
    print("  Generated themes:")
    for theme in result.themes:
        print(f"    {theme.id} {theme.name}: {', '.join(theme.keywords[:3])}")
    print()
    return tuple(failures)


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


def load_table_ground_truth(path: Path, document: TranscriptDocument) -> tuple[CodedSpan, ...]:
    spans: list[CodedSpan] = []
    occurrences: Counter[str] = Counter()
    for rows in _docx_tables(path):
        if not rows:
            continue
        header = [cell.casefold() for cell in rows[0]]
        text_column = "text" if "text" in header else "comment scope" if "comment scope" in header else ""
        theme_column = "theme" if "theme" in header else "comment text" if "comment text" in header else ""
        if not text_column or not theme_column:
            continue
        text_index = header.index(text_column)
        theme_index = header.index(theme_column)
        author_index = header.index("author") if "author" in header else None
        for row in rows[1:]:
            if len(row) <= max(text_index, theme_index):
                continue
            quote_text = _normalize_space(row[text_index])
            author = row[author_index] if author_index is not None and len(row) > author_index else ""
            theme_names = tuple(
                label
                for line in row[theme_index].splitlines()
                if (label := _clean_coder_label(line, author))
            )
            if not quote_text or not theme_names:
                continue
            matches = _token_spans(document.text, quote_text)
            key = " ".join(_match_tokens(quote_text))
            occurrence = occurrences[key]
            occurrences[key] += 1
            if occurrence < len(matches):
                source_start, source_end = matches[occurrence]
                matched_text = document.text[source_start:source_end]
            else:
                anchored = _anchored_token_span(document.text, quote_text)
                if anchored is None:
                    source_start = source_end = -1
                    matched_text = quote_text
                else:
                    source_start, source_end = anchored
                    matched_text = document.text[source_start:source_end]
            for theme_name in theme_names:
                spans.append(
                    CodedSpan(
                        theme_name=theme_name,
                        source_name=document.name,
                        text=matched_text,
                        source_start=source_start,
                        source_end=source_end,
                        author=_normalize_space(author),
                    )
                )
    return tuple(spans)


def load_commented_ground_truth(
    path: Path,
    source_name: str,
    author: str | None = None,
) -> tuple[TranscriptDocument, tuple[CodedSpan, ...]]:
    text = load_transcript_text(path)
    with zipfile.ZipFile(path) as archive:
        document_root = ElementTree.fromstring(archive.read("word/document.xml"))
        comments_root = ElementTree.fromstring(archive.read("word/comments.xml"))

    comments: dict[str, tuple[str, tuple[str, ...]]] = {}
    for comment in comments_root.findall("w:comment", WORD_NS):
        comment_id = comment.attrib[WORD_ID]
        comment_author = _normalize_space(comment.attrib.get(WORD_AUTHOR, ""))
        labels = tuple(
            label
            for paragraph in comment.findall("w:p", WORD_NS)
            if (label := _clean_coder_label(_word_text(paragraph), comment_author))
        )
        comments[comment_id] = comment_author, labels

    selected_text: defaultdict[str, list[str]] = defaultdict(list)
    bounds: dict[str, list[int]] = {}
    active: set[str] = set()
    paragraphs: list[tuple[int, str]] = []
    for paragraph_index, paragraph in enumerate(document_root.findall(".//w:p", WORD_NS)):
        paragraph_parts: list[str] = []
        for node in paragraph.iter():
            tag = node.tag.rsplit("}", 1)[-1]
            if tag == "commentRangeStart":
                comment_id = node.attrib[WORD_ID]
                active.add(comment_id)
                bounds.setdefault(comment_id, [paragraph_index, paragraph_index])
            elif tag == "commentRangeEnd":
                comment_id = node.attrib[WORD_ID]
                bounds.setdefault(comment_id, [paragraph_index, paragraph_index])[1] = paragraph_index
                active.discard(comment_id)
            else:
                piece = _word_piece(tag, node.text)
                if not piece:
                    continue
                paragraph_parts.append(piece)
                for comment_id in active:
                    selected_text[comment_id].append(piece)
        paragraph_text = _normalize_space("".join(paragraph_parts))
        if paragraph_text:
            paragraphs.append((paragraph_index, paragraph_text))
        for comment_id in active:
            selected_text[comment_id].append(" ")

    starts: dict[int, int] = {}
    stops: dict[int, int] = {}
    cursor = 0
    for paragraph_index, paragraph_text in paragraphs:
        starts[paragraph_index] = cursor
        stops[paragraph_index] = cursor + len(paragraph_text)
        cursor += len(paragraph_text) + 1

    spans: list[CodedSpan] = []
    for comment_id, (comment_author, labels) in comments.items():
        if author is not None and comment_author.casefold() != author.casefold():
            continue
        raw_selection = _normalize_space("".join(selected_text.get(comment_id, [])))
        first_paragraph, last_paragraph = bounds.get(comment_id, [0, 0])
        search_start = min((offset for index, offset in starts.items() if index >= first_paragraph), default=0)
        search_end = max((offset for index, offset in stops.items() if index <= last_paragraph), default=len(text))
        matched_span = _whitespace_span(text, raw_selection, search_start, search_end)
        if matched_span is None:
            matched_span = next(
                (
                    span
                    for span in _token_spans(text, raw_selection)
                    if span[0] >= search_start and span[1] <= search_end
                ),
                None,
            )
        if matched_span is None:
            matched_span = _anchored_token_span(text, raw_selection)
        if matched_span is None:
            matched_span = _fuzzy_token_span(text, raw_selection, search_start, search_end)
        if matched_span is None:
            matched_span = _fuzzy_token_span(
                text,
                raw_selection,
                0,
                len(text),
                preferred_offset=(search_start + search_end) // 2,
            )
        source_start, source_end = matched_span if matched_span is not None else (-1, -1)
        matched_text = text[source_start:source_end] if matched_span is not None else raw_selection
        for label in labels:
            spans.append(
                CodedSpan(
                    theme_name=label,
                    source_name=source_name,
                    text=matched_text,
                    source_start=source_start,
                    source_end=source_end,
                    author=comment_author,
                )
            )
    return TranscriptDocument(name=source_name, text=text), tuple(spans)


def quote_metrics(ground_truth: tuple[CodedSpan, ...], result: AnalysisResult) -> tuple[QuoteMetric, ...]:
    truth_by_theme: defaultdict[str, list[CodedSpan]] = defaultdict(list)
    display_names: dict[str, str] = {}
    for span in ground_truth:
        key = _normalize_theme_name(span.theme_name)
        truth_by_theme[key].append(span)
        display_names.setdefault(key, span.theme_name)

    metrics: list[QuoteMetric] = []
    for key in sorted(truth_by_theme):
        truth = truth_by_theme[key]
        theme = _best_result_theme(display_names[key], result)
        predictions = theme.quotes if theme is not None else []
        true_positive = _maximum_overlap_matches(truth, predictions)
        false_positive = len(predictions) - true_positive
        false_negative = len(truth) - true_positive
        precision = true_positive / (true_positive + false_positive) if true_positive + false_positive else 0.0
        recall = true_positive / (true_positive + false_negative) if true_positive + false_negative else 0.0
        metrics.append(
            QuoteMetric(
                display_names[key],
                true_positive,
                false_positive,
                false_negative,
                round(precision, 6),
                round(recall, 6),
            )
        )
    return tuple(metrics)


def _best_result_theme(name: str, result: AnalysisResult) -> Theme | None:
    if not result.themes:
        return None
    normalized = _normalize_theme_name(name)
    exact = next((theme for theme in result.themes if _normalize_theme_name(theme.name) == normalized), None)
    if exact is not None:
        return exact
    expected_tokens = set(tokens(name))
    scored = [(jaccard(expected_tokens, set(tokens(theme.name))), theme) for theme in result.themes]
    score, theme = max(scored, key=lambda item: item[0])
    return theme if score > 0.0 else None


def _maximum_overlap_matches(truth: list[CodedSpan], predictions: list[ThemeQuote]) -> int:
    edges = [
        [truth_index for truth_index, span in enumerate(truth) if _span_overlaps_quote(span, quote)]
        for quote in predictions
    ]
    matched_truth: dict[int, int] = {}

    def augment(prediction_index: int, seen: set[int]) -> bool:
        for truth_index in edges[prediction_index]:
            if truth_index in seen:
                continue
            seen.add(truth_index)
            previous_prediction = matched_truth.get(truth_index)
            if previous_prediction is None or augment(previous_prediction, seen):
                matched_truth[truth_index] = prediction_index
                return True
        return False

    return sum(augment(prediction_index, set()) for prediction_index in range(len(predictions)))


def load_quote_validation(
    dataset_name: str,
    root: Path,
) -> tuple[tuple[TranscriptDocument, ...], tuple[CodedSpan, ...]] | None:
    if dataset_name == "Adult day service curriculum":
        reliability_root = root / "Adult day service curriculum" / "Interview Sessions" / "Reliability Coding"
        paths = sorted(reliability_root.glob("*.docx"))
        if not paths:
            return None
        documents: list[TranscriptDocument] = []
        all_spans: list[CodedSpan] = []
        for path in paths:
            document, document_spans = load_commented_ground_truth(path, path.stem)
            documents.append(document)
            all_spans.extend(document_spans)
        primary_author = _primary_author(tuple(all_spans))
        spans = tuple(span for span in all_spans if span.author == primary_author)
        return tuple(documents), spans
    if dataset_name == "Familiarity and perceptions of preference assessment":
        study_root = (
            root
            / "Familiarity and perceptions of preference assessment"
            / "De-Identified Interviews"
            / "De-Identified Interviews for Coding"
        )
        if not study_root.is_dir():
            return None
        pairs = (
            ("P5", "P5 De-Identified Interview (1).docx", "PT 5 Coding Text.docx"),
            ("P16", "P16 Transcript (Original).docx", "PT 16 Coding Table.docx"),
            ("P18", "Interview Transcript 18.docx", "PT 18 Coding Table.docx"),
            ("P19", "P19 Transcript.docx", "PT 19 Coding Table.docx"),
            ("P20", "P20 Transcript.docx", "Pt20 Coding Table.docx"),
        )
        documents = []
        spans = []
        for participant, transcript_name, table_name in pairs:
            transcript_path = study_root / participant / transcript_name
            table_path = study_root / participant / table_name
            document = TranscriptDocument(participant, load_transcript_text(transcript_path))
            documents.append(document)
            spans.extend(load_table_ground_truth(table_path, document))
        return tuple(documents), tuple(spans)
    return None


def print_quote_metrics(
    documents: tuple[TranscriptDocument, ...],
    ground_truth: tuple[CodedSpan, ...],
) -> tuple[QuoteMetric, ...]:
    entries = quote_validation_entries(ground_truth)
    result = analyze_documents(
        documents,
        AnalysisSettings(
            theme_count=max(1, len(entries)),
            quotes_per_theme=0,
            min_theme_size=1,
            min_quote_words=1,
            codebook_entries=entries,
        ),
    )
    metrics = quote_metrics(ground_truth, result)
    print("  Quote-level precision and recall:")
    print(f"    Ground-truth codes: {len(ground_truth)}")
    print(f"    Unresolved passages: {sum(not span.resolved for span in ground_truth)}")
    for metric in metrics:
        print(
            f"    {metric.theme_name}: precision={metric.precision:.3f} recall={metric.recall:.3f} "
            f"tp={metric.true_positive} fp={metric.false_positive} fn={metric.false_negative}"
        )
    return metrics


def quote_validation_entries(ground_truth: tuple[CodedSpan, ...]) -> tuple[CodebookEntry, ...]:
    display_names: dict[str, str] = {}
    for span in ground_truth:
        key = _normalize_theme_name(span.theme_name)
        display_names.setdefault(key, span.theme_name)
    return tuple(
        CodebookEntry(
            name=display_names[key],
            description=display_names[key],
        )
        for key in sorted(display_names)
    )


def _docx_tables(path: Path) -> tuple[tuple[tuple[str, ...], ...], ...]:
    with zipfile.ZipFile(path) as archive:
        root = ElementTree.fromstring(archive.read("word/document.xml"))
    tables: list[tuple[tuple[str, ...], ...]] = []
    for table in root.findall(".//w:tbl", WORD_NS):
        rows = tuple(
            tuple(_word_text(cell) for cell in row.findall("w:tc", WORD_NS))
            for row in table.findall("w:tr", WORD_NS)
        )
        tables.append(tuple(row for row in rows if any(row)))
    return tuple(tables)


def _word_text(node: ElementTree.Element) -> str:
    if node.tag.rsplit("}", 1)[-1] == "tc":
        return "\n".join(filter(None, (_word_text(paragraph) for paragraph in node.findall(".//w:p", WORD_NS))))
    return _normalize_space("".join(_word_piece(child.tag.rsplit("}", 1)[-1], child.text) for child in node.iter()))


def _word_piece(tag: str, text: str | None) -> str:
    if tag == "t":
        return text or ""
    if tag == "tab":
        return "\t"
    if tag == "br":
        return "\n"
    return ""


def _normalize_space(text: str) -> str:
    return " ".join(text.split())


def _clean_theme_label(text: str) -> str:
    label = _normalize_space(text)
    return "" if label.casefold() in NON_THEME_LABELS else label


def _clean_coder_label(text: str, author: str) -> str:
    label = _clean_theme_label(text)
    author_parts = re.findall(r"[A-Za-z]+", author)
    prefixes = (
        {
            author_parts[0].casefold(),
            "".join(part[0] for part in author_parts).casefold(),
            (author_parts[0][0] + author_parts[-1][0]).casefold(),
        }
        if author_parts
        else set()
    )
    head, separator, tail = label.partition(":")
    if separator and head.strip().casefold() in prefixes:
        return _clean_theme_label(tail)
    return label


def _match_tokens(text: str) -> list[str]:
    return [match.group(0).replace("’", "'").casefold() for match in MATCH_TOKEN_RE.finditer(text)]


def _token_spans(source: str, needle: str) -> tuple[tuple[int, int], ...]:
    source_matches = list(MATCH_TOKEN_RE.finditer(source))
    source_tokens = [match.group(0).replace("’", "'").casefold() for match in source_matches]
    wanted = _match_tokens(needle)
    if not wanted:
        return ()
    return tuple(
        (source_matches[index].start(), source_matches[index + len(wanted) - 1].end())
        for index in range(len(source_tokens) - len(wanted) + 1)
        if source_tokens[index : index + len(wanted)] == wanted
    )


def _anchored_token_span(source: str, needle: str) -> tuple[int, int] | None:
    source_matches = list(MATCH_TOKEN_RE.finditer(source))
    source_tokens = [match.group(0).replace("’", "'").casefold() for match in source_matches]
    wanted = _match_tokens(needle)
    if len(wanted) < 8:
        return None
    anchor_size = min(12, max(4, len(wanted) // 4))
    prefix = wanted[:anchor_size]
    suffix = wanted[-anchor_size:]
    prefix_positions = [
        index
        for index in range(len(source_tokens) - anchor_size + 1)
        if source_tokens[index : index + anchor_size] == prefix
    ]
    suffix_positions = [
        index
        for index in range(len(source_tokens) - anchor_size + 1)
        if source_tokens[index : index + anchor_size] == suffix
    ]
    candidates = [
        (abs((suffix_index + anchor_size - prefix_index) - len(wanted)), prefix_index, suffix_index)
        for prefix_index in prefix_positions
        for suffix_index in suffix_positions
        if suffix_index >= prefix_index
        and abs((suffix_index + anchor_size - prefix_index) - len(wanted)) <= max(20, len(wanted) // 3)
    ]
    if not candidates:
        return None
    _, prefix_index, suffix_index = min(candidates)
    return source_matches[prefix_index].start(), source_matches[suffix_index + anchor_size - 1].end()


def _whitespace_span(source: str, needle: str, start: int, end: int) -> tuple[int, int] | None:
    if not needle:
        return None
    pattern = r"\s+".join(re.escape(part) for part in needle.split())
    match = re.search(pattern, source[start:end], re.IGNORECASE)
    if match is None:
        return None
    return start + match.start(), start + match.end()


def _fuzzy_token_span(
    source: str,
    needle: str,
    start: int,
    end: int,
    preferred_offset: int | None = None,
) -> tuple[int, int] | None:
    source_matches = list(MATCH_TOKEN_RE.finditer(source, start, end))
    source_tokens = [match.group(0).replace("’", "'").casefold() for match in source_matches]
    wanted = _match_tokens(needle)
    if not source_tokens or not wanted:
        return None
    size_delta = max(2, len(wanted) // 4)
    sizes = range(max(1, len(wanted) - size_delta), len(wanted) + size_delta + 1)
    target = preferred_offset if preferred_offset is not None else (start + end) // 2
    best: tuple[float, int, int, int] | None = None
    for window_size in sizes:
        for index in range(len(source_tokens) - window_size + 1):
            score = SequenceMatcher(None, wanted, source_tokens[index : index + window_size], autojunk=False).ratio()
            window_center = (source_matches[index].start() + source_matches[index + window_size - 1].end()) // 2
            candidate = (score, -abs(window_center - target), index, window_size)
            if best is None or candidate > best:
                best = candidate
    if best is None:
        return None
    score, _, index, window_size = best
    threshold = 1.0 if len(wanted) < 3 else 0.7
    if score < threshold:
        return None
    return source_matches[index].start(), source_matches[index + window_size - 1].end()


def _normalize_theme_name(name: str) -> str:
    return " ".join(name.casefold().split())


def _span_overlaps_quote(span: CodedSpan, quote: ThemeQuote) -> bool:
    return (
        span.resolved
        and span.source_name == quote.source_name
        and min(span.source_end, quote.source_end) > max(span.source_start, quote.source_start)
    )


def result_from_spans(documents: tuple[TranscriptDocument, ...], spans: tuple[CodedSpan, ...]) -> AnalysisResult:
    grouped: defaultdict[str, list[CodedSpan]] = defaultdict(list)
    display_names: dict[str, str] = {}
    for span in spans:
        if not span.resolved:
            continue
        key = _normalize_theme_name(span.theme_name)
        grouped[key].append(span)
        display_names.setdefault(key, span.theme_name)
    themes: list[Theme] = []
    for index, key in enumerate(sorted(grouped), start=1):
        quotes = [
            ThemeQuote(
                quote_id=f"GT-{index:02d}-{quote_index:04d}",
                speaker="Ground truth",
                text=span.text,
                relevance=1.0,
                source_line=1,
                source_name=span.source_name,
                rationale="Imported human coding span.",
                source_start=span.source_start,
                source_end=span.source_end,
            )
            for quote_index, span in enumerate(grouped[key], start=1)
        ]
        themes.append(Theme(f"GT-{index:02d}", display_names[key], "#64748B", [], len(quotes), 1.0, quotes))
    return AnalysisResult("validation", len(documents), len(spans), themes, [])


def round_trip_coded_project(
    path: Path,
    documents: tuple[TranscriptDocument, ...],
    spans: tuple[CodedSpan, ...],
    coder_name: str,
) -> CodedProject:
    save_project(
        path,
        ProjectState(
            transcript_paths=(),
            codebook_path=None,
            documents=documents,
            settings=AnalysisSettings(min_quote_words=1),
            result=result_from_spans(documents, spans),
            researcher_name=coder_name,
        ),
    )
    loaded = load_project(path)
    if loaded.result is None:
        raise RuntimeError(f"round-tripped project has no result: {path.name}")
    return CodedProject(loaded.documents, loaded.result)


def print_reliability_validation(root: Path) -> tuple[str, ...]:
    failures: list[str] = []
    studies = (_adult_reliability_inputs(root), _preference_reliability_inputs(root))
    for label, study in zip(("Adult day service", "Preference assessment"), studies, strict=True):
        if study is None:
            failures.append(f"{label}: reliability inputs missing")
    available_studies = tuple(study for study in studies if study is not None)
    if not available_studies:
        return tuple(failures)
    print("Intercoder reliability (.tfproj round trip):")
    with tempfile.TemporaryDirectory(prefix="themeforge-validation-") as tmpdir:
        temporary_root = Path(tmpdir)
        for study_name, documents, coder_a, coder_b, agreement, labels in available_studies:
            unresolved_codes = sum(not span.resolved for span in (*coder_a, *coder_b, *agreement))
            if unresolved_codes:
                failures.append(f"{study_name}: {unresolved_codes} coded passages unresolved")
            project_a = round_trip_coded_project(temporary_root / f"{study_name}-a.tfproj", documents, coder_a, labels.coder_a)
            project_b = round_trip_coded_project(temporary_root / f"{study_name}-b.tfproj", documents, coder_b, labels.coder_b)
            report = intercoder_reliability(project_a, project_b, labels)
            defined = sum(row.cohens_kappa is not None for row in report.theme_rows)
            warnings = sum(bool(row.prevalence_warning) for row in report.theme_rows)
            print(f"  Study: {study_name}")
            print(f"    Units: {report.unit_count}")
            print(f"    Themes: {len(report.theme_rows)}")
            print(f"    Defined kappas: {defined}")
            print(f"    Prevalence warnings: {warnings}")
            print(f"    Disagreements: {len(report.disagreements)}")
            failures.extend(reliability_report_failures(study_name, report))
            for row in report.theme_rows:
                kappa = "undefined" if row.cohens_kappa is None else f"{row.cohens_kappa:.3f}"
                warning = row.prevalence_warning or "none"
                print(f"    {row.theme_name}: kappa={kappa} agreement={row.percent_agreement:.3f} warning={warning}")
            if agreement:
                direction = agreement_direction_counts(report, documents, agreement)
                print(
                    f"    Agreement direction: {labels.coder_a}={direction[0]} "
                    f"{labels.coder_b}={direction[1]} unresolved={direction[2]}"
                )
                if sum(direction) != len(report.disagreements):
                    failures.append(f"{study_name}: Agreement direction did not account for every disagreement")
                if direction[2]:
                    failures.append(f"{study_name}: {direction[2]} Agreement directions unresolved")
                if not direction[0] or not direction[1]:
                    failures.append(f"{study_name}: Agreement reconciliation did not select both coder directions")
    print()
    return tuple(failures)


def reliability_report_failures(
    study_name: str,
    report: IntercoderReliabilityReport,
) -> tuple[str, ...]:
    failures: list[str] = []
    defined_rows = tuple(row for row in report.theme_rows if row.cohens_kappa is not None)
    warning_rows = tuple(row for row in report.theme_rows if row.prevalence_warning)
    if not report.unit_count:
        failures.append(f"{study_name}: no shared meaning units")
    if not report.theme_rows:
        failures.append(f"{study_name}: no coded themes")
    if not defined_rows:
        failures.append(f"{study_name}: no defined kappa values")
    if not warning_rows:
        failures.append(f"{study_name}: no prevalence warnings")
    if not report.disagreements:
        failures.append(f"{study_name}: no disagreements")
    if any(not 0.0 <= row.percent_agreement <= 1.0 for row in report.theme_rows):
        failures.append(f"{study_name}: percent agreement outside [0, 1]")
    if any(not -1.0 <= row.cohens_kappa <= 1.0 for row in defined_rows):
        failures.append(f"{study_name}: Cohen's kappa outside [-1, 1]")
    return tuple(failures)


def agreement_direction_counts(
    report: IntercoderReliabilityReport,
    documents: tuple[TranscriptDocument, ...],
    agreement: tuple[CodedSpan, ...],
) -> tuple[int, int, int]:
    units = [
        unit
        for document in documents
        for unit in extract_quote_units(parse_transcript(document.text, document.name), min_quote_words=1)
    ]
    coder_a = coder_b = unresolved = 0
    for disagreement in report.disagreements:
        unit = next(
            (
                item
                for item in units
                if item.source_name == disagreement.source_name
                and item.speaker == disagreement.speaker
                and item.text == disagreement.text
            ),
            None,
        )
        if unit is None:
            unresolved += 1
            continue
        agreement_present = any(
            span.resolved
            and span.source_name == unit.source_name
            and _normalize_theme_name(span.theme_name) == disagreement.theme_name
            and min(span.source_end, unit.source_end) > max(span.source_start, unit.source_start)
            for span in agreement
        )
        if agreement_present == disagreement.coder_a_present:
            coder_a += 1
        elif agreement_present == disagreement.coder_b_present:
            coder_b += 1
        else:
            unresolved += 1
    return coder_a, coder_b, unresolved


def _adult_reliability_inputs(
    root: Path,
) -> tuple[str, tuple[TranscriptDocument, ...], tuple[CodedSpan, ...], tuple[CodedSpan, ...], tuple[CodedSpan, ...], CoderLabels] | None:
    paths = sorted((root / "Adult day service curriculum" / "Interview Sessions" / "Reliability Coding").glob("*.docx"))
    if not paths:
        return None
    documents: list[TranscriptDocument] = []
    all_spans: list[CodedSpan] = []
    for path in paths:
        document, spans = load_commented_ground_truth(path, path.stem)
        documents.append(document)
        all_spans.extend(spans)
    primary_author = _primary_author(tuple(all_spans))
    coder_a = tuple(span for span in all_spans if span.author == primary_author)
    coder_b = tuple(span for span in all_spans if span.author != primary_author)
    return "Adult day service", tuple(documents), coder_a, coder_b, (), CoderLabels()


def _preference_reliability_inputs(
    root: Path,
) -> tuple[str, tuple[TranscriptDocument, ...], tuple[CodedSpan, ...], tuple[CodedSpan, ...], tuple[CodedSpan, ...], CoderLabels] | None:
    study_root = (
        root
        / "Familiarity and perceptions of preference assessment"
        / "De-Identified Interviews"
        / "De-Identified Interviews for Coding"
    )
    participants = ("P16", "P19", "P20")
    if not all((study_root / participant).is_dir() for participant in participants):
        return None
    documents: list[TranscriptDocument] = []
    coder_a: list[CodedSpan] = []
    coder_b: list[CodedSpan] = []
    agreement: list[CodedSpan] = []
    for participant in participants:
        folder = study_root / participant
        commented_paths = tuple(path for path in sorted(folder.glob("*.docx")) if _has_word_comments(path))
        agreement_paths = tuple(path for path in commented_paths if "agreement" in path.stem.casefold())
        coder_paths = tuple(path for path in commented_paths if path not in agreement_paths)
        if len(coder_paths) != 2 or len(agreement_paths) != 1:
            raise RuntimeError(
                f"expected two coder files and one Agreement file for {participant}, "
                f"found {len(coder_paths)} coder and {len(agreement_paths)} Agreement"
            )
        coder_a_document, coder_a_spans = load_commented_ground_truth(coder_paths[0], participant)
        coder_b_document, coder_b_spans = load_commented_ground_truth(coder_paths[1], participant)
        agreement_path = agreement_paths[0]
        agreement_document, agreement_spans = load_commented_ground_truth(agreement_path, participant)
        if coder_a_document.text != coder_b_document.text or coder_a_document.text != agreement_document.text:
            raise RuntimeError(f"coder transcript text differs for {participant}")
        documents.append(coder_a_document)
        coder_a.extend(coder_a_spans)
        coder_b.extend(coder_b_spans)
        agreement.extend(agreement_spans)
    return "Preference assessment", tuple(documents), tuple(coder_a), tuple(coder_b), tuple(agreement), CoderLabels()


def _primary_author(spans: tuple[CodedSpan, ...]) -> str:
    counts = Counter(span.author for span in spans if span.author)
    if not counts:
        raise RuntimeError("coded documents contain no author metadata")
    return counts.most_common(1)[0][0]


def _has_word_comments(path: Path) -> bool:
    try:
        with zipfile.ZipFile(path) as archive:
            return "word/comments.xml" in archive.namelist()
    except zipfile.BadZipFile:
        return False


def published_theme_names(spec: DatasetSpec, root: Path) -> tuple[str, ...]:
    if spec.name == "4-H":
        path = root / "4-H" / "Mccormick 2022.pdf"
        if not path.is_file():
            raise ValueError(f"missing published paper: {path}")
        themes = extract_mccormick_themes(path)
        if len(themes) < 10:
            raise ValueError(f"Mccormick Table 2 extraction incomplete: found {len(themes)} labels")
        return themes
    if spec.name == "Autism and kindness":
        base = root / "autism kindness"
        get_paths = sorted((base / "General Experiential Themes (GETs)").glob("*.pdf"))
        pet_paths = sorted((base / "Personal Experiential Themes (PETs)").glob("*.pdf"))
        if len(get_paths) != 1 or len(pet_paths) != 10:
            raise ValueError(f"expected 1 GET and 10 PET PDFs, found {len(get_paths)} GET and {len(pet_paths)} PET")
        return extract_autism_themes(tuple((*get_paths, *pet_paths)))
    return ()


def extract_mccormick_themes(path: Path) -> tuple[str, ...]:
    from pypdf import PdfReader

    page = PdfReader(path).pages[4]
    top_level = extract_mccormick_themes_from_layout(page.extract_text(extraction_mode="layout"))
    chunks: list[tuple[float, str]] = []
    in_table = False

    def collect_table_text(text: str, _cm: list[float], tm: list[float], _font: object, size: float) -> None:
        nonlocal in_table
        label = _normalize_space(_normalize_ligatures(text))
        if label == "Theme Subtheme":
            in_table = True
            return
        if not in_table or not label or round(size, 1) != 7.0:
            return
        x = float(tm[4])
        if 40.0 <= x <= 230.0:
            chunks.append((x, label))
        if label == "Stakeholder Involvement":
            in_table = False

    page.extract_text(visitor_text=collect_table_text)
    return mccormick_table_labels(top_level, tuple(chunks))


def mccormick_table_labels(
    top_level: tuple[str, ...],
    chunks: tuple[tuple[float, str], ...],
) -> tuple[str, ...]:
    labels: list[str] = []
    for x, raw_label in chunks:
        label = _normalize_space(_normalize_ligatures(raw_label))
        if x < 100.0:
            theme = next((name for name in top_level if label.casefold().startswith(name.casefold())), None)
            if theme is None:
                continue
            labels.append(theme)
            first_subtheme = label[len(theme) :].strip()
            if first_subtheme:
                labels.append(first_subtheme)
        elif 180.0 <= x <= 230.0:
            labels.append(label)
    return tuple(dict.fromkeys(labels))


def extract_mccormick_themes_from_layout(text: str) -> tuple[str, ...]:
    lines = text.splitlines()
    in_table = False
    themes: list[str] = []
    for line in lines:
        if line.startswith("TABLE 2 |"):
            in_table = True
            continue
        if not in_table:
            continue
        left_cell = _normalize_ligatures(line[:50].strip())
        if left_cell and left_cell != "Theme":
            themes.append(left_cell)
        if themes and line.startswith(" " * 58 + "New Trainings"):
            break
    return tuple(dict.fromkeys(themes))


def extract_autism_themes(paths: tuple[Path, ...]) -> tuple[str, ...]:
    from pypdf import PdfReader

    themes: list[str] = []
    for path in paths:
        first_page = PdfReader(path).pages[0].extract_text()
        extracted = extract_summary_themes(first_page)
        if not extracted:
            raise ValueError(f"no summary themes extracted from {path.name}")
        themes.extend(extracted)
    return tuple(dict.fromkeys(themes))


def extract_summary_themes(text: str) -> tuple[str, ...]:
    normalized = _normalize_space(_normalize_ligatures(text))
    match = re.search(
        r"identified (a single|two|three|four|five|six|seven|eight|nine|ten|\d+) (?:GETs?|PETs?).*?: (.+?)\.",
        normalized,
        re.IGNORECASE,
    )
    if match is None:
        return ()
    count_label = match.group(1).casefold()
    value = match.group(2)
    if count_label == "a single" or count_label == "1":
        return (value.strip(" ;"),)
    parts = re.split(r",\s*(?:and\s+)?", value) if "," in value else value.rsplit(" and ", 1)
    return tuple(part.strip(" ;") for part in parts if part.strip(" ;"))


def _normalize_ligatures(text: str) -> str:
    return text.translate(str.maketrans({"ﬁ": "fi", "ﬂ": "fl", "ﬀ": "ff", "ﬃ": "ffi", "ﬄ": "ffl"}))


def load_codebook_entries(
    spec: DatasetSpec,
    root: Path,
    themes: tuple[str, ...] | None = None,
) -> tuple[CodebookEntry, ...]:
    themes = themes or spec.ground_truth_themes
    codebook_parts: list[str] = []
    for relative_pattern in spec.codebook_globs:
        for path in sorted(root.glob(relative_pattern)):
            try:
                codebook_parts.append(load_transcript_text(path))
            except (OSError, ValueError, zipfile.BadZipFile):
                continue
    codebook_text = "\n".join(codebook_parts)
    if not codebook_text.strip():
        return tuple(CodebookEntry(name=theme, description=theme) for theme in themes)
    return tuple(
        CodebookEntry(
            name=theme,
            description=theme_section(codebook_text, theme, themes),
        )
        for theme in themes
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
            *(quote.speaker for theme in result.themes for quote in theme.quotes),
            *(quote.text for theme in result.themes for quote in theme.quotes),
        ]
    )


def find_name_leaks(text: str) -> list[str]:
    return sorted(
        term
        for term in KNOWN_NAME_LEAKS
        if re.search(
            rf"(?<![A-Za-z0-9]){re.escape(term)}(?![A-Za-z0-9])",
            text,
            re.IGNORECASE,
        )
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

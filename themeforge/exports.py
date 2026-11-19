from __future__ import annotations

import csv
import io
import json
from collections.abc import Iterable
from dataclasses import asdict

from .analysis import AnalysisResult, ValidationValue
from .analysis_types import TranscriptDocument
from .manual_editing import MANUAL_ONLY_NOTE
from .source_location import LOCATION_SCOPE, TranscriptIndex


def export_markdown(result: AnalysisResult, documents: Iterable[TranscriptDocument] = ()) -> str:
    sources = TranscriptIndex(documents)
    manual_only = MANUAL_ONLY_NOTE in result.notes
    mixed = any(t.reported_score is None for t in result.themes)
    origin = "These themes and quote matches are machine-generated suggestions."
    if manual_only:
        origin = MANUAL_ONLY_NOTE
    elif mixed:
        origin = "Manual coding decisions and machine suggestions are separate. Unscored records have no current numeric score."
    lines = [
        "# Qualitative Thematic Coding Report",
        "",
        f"Generated with ThemeForge v{result.version}.",
        "",
        "## Researcher review required",
        "",
        (
            f"{origin} Researchers should verify each quote against the transcript and revise "
            "theme names before reporting findings."
        ),
        "",
        LOCATION_SCOPE + " Speaker labels, excerpts and counts are reported as recorded, including unresolved records.",
        "",
        "## Summary",
        "",
        f"- Documents in project: {result.document_count}" if manual_only else f"- Documents analyzed: {result.document_count}",
        f"- Quote units analyzed: {result.quote_count}",
        f"- Coding groups: {len(result.themes)}" if manual_only or mixed else f"- Suggested themes: {len(result.themes)}",
        "",
    ]

    lines.extend(["## Validation Framework", ""])
    if result.validation_summary:
        lines.extend(f"- {note}" for note in result.validation_summary)
    else:
        lines.append("- Researcher review required before using suggested themes as findings.")
    lines.append("")

    transcript_evidence = []
    for index, theme in enumerate(result.themes, start=1):
        lines.extend(
            [
                f"## Theme {index}: {theme.name}",
                "",
                f"- Theme ID: {theme.id}",
                f"- Theme color: {theme.color}",
                f"- Quote units in theme: {theme.quote_count}",
                "- Cohesion score: Not scored for this coding group" if theme.reported_score is None
                else f"- Cohesion score: {theme.score:.3f}",
                f"- Keywords: {', '.join(theme.keywords) if theme.keywords else 'None'}",
                f"- Validation: {_format_validation(theme.validation)}",
                "",
                "### Quote Evidence",
                "",
            ]
        )

        for quote in theme.quotes:
            loc = sources.locate(quote)
            citation = (f"[{theme.id} {theme.color}] Source: {quote.source_name} | Recorded line: {quote.source_line} | "
                        f"Recorded speaker: {quote.speaker} | Quote ID: {quote.quote_id} | Location: {loc.status}")
            excerpt = quote.text.split("\n")
            lines.extend(
                [
                    f"- {citation}. {loc.note}",
                    "",
                    *(f"> {line}" for line in excerpt),
                    "",
                    "- Relevance: " + ("Not scored" if quote.reported_relevance is None else f"{quote.relevance:.3f}"),
                    f"- Selection rationale: {quote.rationale}",
                    "",
                ]
            )
            transcript_evidence.extend([f"- {citation}. {loc.note}", *(f"  > {line}" for line in excerpt)])

    if result.themes:
        lines.extend(["## Color-Coded Transcript Evidence", ""])
        lines.extend(transcript_evidence)
        lines.append("")

    if result.notes:
        lines.extend(["## Notes", ""])
        lines.extend(f"- {note}" for note in result.notes)
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def export_json(result: AnalysisResult, documents: Iterable[TranscriptDocument] = ()) -> str:
    sources = TranscriptIndex(documents)
    payload = asdict(result)
    payload["source_location_scope"] = LOCATION_SCOPE
    for theme, item in zip(result.themes, payload["themes"], strict=True):
        item["score"] = theme.reported_score
        for quote, row in zip(theme.quotes, item["quotes"], strict=True):
            row["relevance"] = quote.reported_relevance
            loc = sources.locate(quote)
            row["source_location_status"] = loc.status
            row["source_location_note"] = loc.note
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"


def export_quotes_csv(result: AnalysisResult, documents: Iterable[TranscriptDocument] = ()) -> str:
    sources = TranscriptIndex(documents)
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(
        [
            "theme_id",
            "theme_name",
            "theme_keywords",
            "theme_color",
            "quote_id",
            "speaker",
            "source_file",
            "source_line",
            "relevance",
            "quote",
            "selection_rationale",
            "validation_review_status",
            "source_start",
            "source_end",
            "source_location_status",
            "source_location_note",
        ]
    )

    for theme in result.themes:
        keywords = "; ".join(theme.keywords)
        for quote in theme.quotes:
            loc = sources.locate(quote)
            writer.writerow(
                [
                    theme.id,
                    theme.name,
                    keywords,
                    theme.color,
                    quote.quote_id,
                    quote.speaker,
                    quote.source_name,
                    quote.source_line,
                    "" if quote.reported_relevance is None else f"{quote.relevance:.3f}",
                    quote.text,
                    quote.rationale,
                    theme.validation.get("review_status", "Researcher review required"),
                    quote.source_start,
                    quote.source_end,
                    loc.status,
                    loc.note,
                ]
            )

    return buffer.getvalue()


def _format_validation(validation: dict[str, ValidationValue]) -> str:
    if not validation:
        return "Researcher review required"
    unscored = validation.get("score_scope") == "Not scored" or "manual_codebook_definition" in validation
    alignment = "not scored" if unscored else validation.get("central_theme_alignment", 0)
    return (
        f"evidence={validation.get('evidence_count', 0)}, "
        f"sources={validation.get('source_count', 0)}, "
        f"speakers={validation.get('speaker_count', 0)}, "
        f"central alignment={alignment}, "
        f"status={validation.get('review_status', 'Researcher review required')}"
    )

from __future__ import annotations

import csv
import io
import json
from dataclasses import asdict

from .analysis import AnalysisResult


def export_markdown(result: AnalysisResult) -> str:
    lines = [
        "# Qualitative Thematic Coding Report",
        "",
        f"Generated with ThemeForge v{result.version}.",
        "",
        "## Researcher review required",
        "",
        (
            "These themes and quote matches are machine-generated suggestions. "
            "Researchers should verify each quote against the transcript and revise "
            "theme names before reporting findings."
        ),
        "",
        "## Summary",
        "",
        f"- Documents analyzed: {result.document_count}",
        f"- Quote units analyzed: {result.quote_count}",
        f"- Suggested themes: {len(result.themes)}",
        "",
    ]

    lines.extend(["## Validation Framework", ""])
    if result.validation_summary:
        lines.extend(f"- {note}" for note in result.validation_summary)
    else:
        lines.append("- Researcher review required before using suggested themes as findings.")
    lines.append("")

    for index, theme in enumerate(result.themes, start=1):
        lines.extend(
            [
                f"## Theme {index}: {theme.name}",
                "",
                f"- Theme ID: {theme.id}",
                f"- Theme color: {theme.color}",
                f"- Quote units in theme: {theme.quote_count}",
                f"- Cohesion score: {theme.score:.3f}",
                f"- Keywords: {', '.join(theme.keywords) if theme.keywords else 'None'}",
                f"- Validation: {_format_validation(theme.validation)}",
                "",
                "### Quote Evidence",
                "",
            ]
        )

        for quote in theme.quotes:
            lines.extend(
                [
                    f"> {quote.text}",
                    "",
                    (
                        f"- [{theme.id} {theme.color}] Speaker: {quote.speaker} | "
                        f"Quote ID: {quote.quote_id} | Source: {quote.source_name} | "
                        f"Line: {quote.source_line} | Relevance: {quote.relevance:.3f}"
                    ),
                    "",
                ]
            )

    if result.themes:
        lines.extend(["## Color-Coded Transcript Evidence", ""])
        for theme in result.themes:
            for quote in theme.quotes:
                lines.extend(
                    [
                        f"- [{theme.id} {theme.color}] Source: {quote.source_name} | "
                        f"Line: {quote.source_line} | Speaker: {quote.speaker} | "
                        f"Quote ID: {quote.quote_id}",
                        f"  > {quote.text}",
                    ]
                )
        lines.append("")

    if result.notes:
        lines.extend(["## Notes", ""])
        lines.extend(f"- {note}" for note in result.notes)
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def export_json(result: AnalysisResult) -> str:
    return json.dumps(asdict(result), indent=2, ensure_ascii=False) + "\n"


def export_quotes_csv(result: AnalysisResult) -> str:
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
            "validation_review_status",
        ]
    )

    for theme in result.themes:
        keywords = "; ".join(theme.keywords)
        for quote in theme.quotes:
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
                    f"{quote.relevance:.3f}",
                    quote.text,
                    theme.validation.get("review_status", "Researcher review required"),
                ]
            )

    return buffer.getvalue()


def _format_validation(validation: dict[str, object]) -> str:
    if not validation:
        return "Researcher review required"
    return (
        f"evidence={validation.get('evidence_count', 0)}, "
        f"sources={validation.get('source_count', 0)}, "
        f"speakers={validation.get('speaker_count', 0)}, "
        f"central alignment={validation.get('central_theme_alignment', 0)}, "
        f"status={validation.get('review_status', 'Researcher review required')}"
    )

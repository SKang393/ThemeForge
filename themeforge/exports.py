from __future__ import annotations

import csv
import io
import json
from collections.abc import Iterable
from dataclasses import asdict

from .analysis import AnalysisResult, ValidationValue
from .analysis_types import TranscriptDocument
from .audit import AuditEvent, neutralize_csv_cell
from .manual_editing import MANUAL_ONLY_NOTE
from .match_reviews import NO_CURRENT_REVIEW, REVIEW_SCOPE, MatchReviews
from .source_location import LOCATION_SCOPE, TranscriptIndex


def export_markdown(result: AnalysisResult, documents: Iterable[TranscriptDocument] = (),
                    audit_events: Iterable[AuditEvent] = ()) -> str:
    sources = TranscriptIndex(documents)
    reviews = MatchReviews(audit_events)
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
        REVIEW_SCOPE,
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
            review = reviews.latest(theme, quote, sources)
            review_text = " " + (review.describe() if review else NO_CURRENT_REVIEW)
            excerpt = quote.text.split("\n")
            lines.extend(
                [
                    f"- {citation}. {loc.note}{review_text}",
                    "",
                    *(f"> {line}" for line in excerpt),
                    "",
                    "- Relevance: " + ("Not scored" if quote.reported_relevance is None else f"{quote.relevance:.3f}"),
                    f"- Selection rationale: {quote.rationale}",
                    "",
                ]
            )
            transcript_evidence.extend([f"- {citation}. {loc.note}{review_text}", *(f"  > {line}" for line in excerpt)])

    if result.themes:
        lines.extend(["## Color-Coded Transcript Evidence", ""])
        lines.extend(transcript_evidence)
        lines.append("")

    if result.notes:
        lines.extend(["## Notes", ""])
        lines.extend(f"- {note}" for note in result.notes)
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def export_json(result: AnalysisResult, documents: Iterable[TranscriptDocument] = (),
                audit_events: Iterable[AuditEvent] = ()) -> str:
    sources = TranscriptIndex(documents)
    reviews = MatchReviews(audit_events)
    payload = asdict(result)
    payload["source_location_scope"] = LOCATION_SCOPE
    payload["match_review_scope"] = REVIEW_SCOPE
    for theme, item in zip(result.themes, payload["themes"], strict=True):
        item["score"] = theme.reported_score
        for quote, row in zip(theme.quotes, item["quotes"], strict=True):
            row["relevance"] = quote.reported_relevance
            loc = sources.locate(quote)
            row["source_location_status"] = loc.status
            row["source_location_note"] = loc.note
            review = reviews.latest(theme, quote, sources)
            row["latest_match_review"] = asdict(review) if review else None
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"


def export_quotes_csv(result: AnalysisResult, documents: Iterable[TranscriptDocument] = (),
                      audit_events: Iterable[AuditEvent] = (), *, supported_only: bool = False) -> str:
    sources = TranscriptIndex(documents)
    reviews = MatchReviews(audit_events)
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
            "match_review_disposition",
            "match_review_actor",
            "match_review_reason",
            "match_review_time_utc",
            "match_review_target_id",
        ]
    )

    for theme in result.themes:
        keywords = "; ".join(theme.keywords)
        for quote in theme.quotes:
            loc = sources.locate(quote)
            review = reviews.latest(theme, quote, sources)
            if supported_only and (loc.status != "exact_span" or review is None or review.disposition != "supported"):
                continue
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
                    review.disposition if review else "",
                    neutralize_csv_cell(review.actor) if review else "",
                    neutralize_csv_cell(review.reason) if review else "",
                    review.timestamp_utc if review else "",
                    review.audit_target_id if review else "",
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

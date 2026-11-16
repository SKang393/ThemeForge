from __future__ import annotations

import csv
import io
import json
from dataclasses import asdict

from .analysis import AnalysisResult, ValidationValue
from .analysis_types import AnalyticIntent, CodebookEntry, Theme, ThemeQuote
from .audit import neutralize_csv_cell


def method_guidance(intent: AnalyticIntent) -> str:
    if intent.methodology == "reflexive":
        return "Reflexive TA: review interpretations, alternatives, and reflexive memos. Kappa is not a validity grade for reflexive themes."
    if intent.methodology == "coding_reliability":
        return "Coding reliability TA: interpret agreement against a fixed codebook and shared units; independent coding and prevalence matter."
    if intent.methodology == "codebook":
        return "Codebook TA: review code definitions, revisions, and case comparisons. Agreement is optional and does not establish interpretive validity."
    return "Methodology unspecified: select an analytic approach before interpreting quality measures. Agreement does not establish interpretive validity."


def _label(value: str) -> str:
    return value.replace("_", " ").capitalize()


def _blockquote(value: str) -> str:
    return "\n".join(f"> {line}" for line in value.splitlines())


def _theme_definition(result: AnalysisResult, theme: Theme) -> tuple[CodebookEntry | None, str]:
    if not theme.code_id:
        return None, "No codebook definition associated with this theme"
    if theme.code_definition is not None:
        entry = theme.code_definition
        if (entry.code_id, entry.version) == (theme.code_id, theme.code_version):
            return entry, "Stored definition for this theme's code version"
        return None, "Stored definition does not match the theme's code ID/version; review required"
    entry = next((item for item in result.codebook_snapshot if (item.code_id, item.version) == (theme.code_id, theme.code_version)), None)
    if entry is not None:
        return entry, "Matched code ID and version in the run codebook"
    return None, f"Historical definition unavailable for {theme.code_id} version {theme.code_version}; review required"


def _quote_score_label(quote: ThemeQuote) -> str:
    return "Researcher assignment marker" if quote.quote_id.startswith("MQ") else "Recorded score"


def _quote_score_interpretation(theme: Theme, quote: ThemeQuote) -> str:
    if quote.quote_id.startswith("MQ"):
        return "Researcher assignment marker; not a similarity measurement or correctness probability"
    if theme.validation.get("score_kind") == "researcher_assigned":
        return "Score retained after researcher editing; not a newly computed similarity or validity measure"
    return "Similarity or lexical match; not a calibrated correctness probability"


def _source_review_note(result: AnalysisResult, quote: ThemeQuote) -> str:
    current_hash = result.source_hashes.get(quote.source_name, "")
    if not quote.source_hash or not current_hash:
        return "Source version not verified against the current run"
    if quote.source_hash != current_hash:
        return "Historical source version differs from the current run; verify retained evidence"
    return "Source hash matches the current run"


def export_markdown(result: AnalysisResult) -> str:
    if result.discovery:
        from .discovery_exports import export_discovery_markdown
        return export_discovery_markdown(result)
    lines = [
        "# Qualitative Thematic Coding Report",
        "",
        f"Generated with ThemeForge v{result.version}.",
        "",
        "## Researcher review required",
        "",
        (
            "This report may include machine suggestions and researcher-edited coding. "
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

    intent = result.analytic_intent
    lines.extend([
        "## Analytic intent", "",
        f"- Methodology: {_label(intent.methodology)}",
        f"- Coding logic: {_label(intent.coding_logic)}",
        f"- Analytic depth: {_label(intent.analytic_depth)}",
        f"- Analytic intent version: {intent.version}",
        f"- Codebook workflow: {_label(result.codebook_mode)}",
        f"- Analysis run: {result.run_id or 'Legacy or manually created result'}",
        f"- Theoretical lens: {intent.theoretical_lens or 'Not specified'}",
        f"- Researcher memo: {intent.researcher_memo or 'Not specified'}",
        "",
    ])
    lines.extend(f"- Research question {number}: {question}" for number, question in enumerate(intent.research_questions, 1))
    if not intent.research_questions:
        lines.append("- Research questions: Not specified")
    lines.extend(["", method_guidance(intent), ""])
    lines.append("Analytic depth, lens, and code criteria record researcher intent. Current lexical/embedding suggestions do not automatically operationalize those interpretations or criteria.")
    lines.extend(["", "Automatic scores describe similarity or lexical matching; they are not calibrated probabilities of correct coding. Researcher assignment markers and retained scores are identified separately.", ""])
    if result.codebook_snapshot:
        lines.extend(["## Codebook used for this run", ""])
        for entry in result.codebook_snapshot:
            lines.extend([
                f"### {entry.name}", "",
                f"- Code ID: {entry.code_id}",
                f"- Version: {entry.version}",
                f"- Origin: {entry.origin}",
                f"- Definition: {entry.description or 'Not specified'}",
                f"- Inclusion criteria: {entry.inclusion_criteria or 'Not specified'}",
                f"- Exclusion criteria: {entry.exclusion_criteria or 'Not specified'}",
            ])
            lines.extend(f"- Positive example: {example}" for example in entry.examples)
            lines.extend(f"- Negative example: {example}" for example in entry.negative_examples)
            lines.append("")
    if result.source_hashes:
        lines.extend(["## Source versions", ""])
        lines.extend(f"- {name}: SHA-256 `{digest}`" for name, digest in sorted(result.source_hashes.items()))
        lines.append("")
    excluded = [unit for unit in result.evidence_units if unit.exclusion_reason]
    if excluded:
        lines.extend(["## Evidence requiring separate review", "", f"{len(excluded)} unit(s) were excluded from automatic suggestions. Exact text and context remain available in Evidence review and the JSON export.", ""])
        lines.extend(f"- {unit.id}: {unit.source_name}, offsets {unit.source_start}:{unit.source_end}, reason: {unit.exclusion_reason}" for unit in excluded)
        lines.append("")
    lines.extend(["## Validation Framework", ""])
    if result.validation_summary:
        lines.extend(f"- {note}" for note in result.validation_summary)
    else:
        lines.append("- Researcher review required before using suggested themes as findings.")
    lines.append("")

    for index, theme in enumerate(result.themes, start=1):
        entry, definition_status = _theme_definition(result, theme)
        score_label = "Retained score (researcher-edited theme)" if theme.validation.get("score_kind") == "researcher_assigned" else "Suggestion score"
        lines.extend(
            [
                f"## Theme {index}: {theme.name}",
                "",
                f"- Theme ID: {theme.id}",
                f"- Theme color: {theme.color}",
                f"- Quote units in theme: {theme.quote_count}",
                f"- {score_label}: {theme.score:.3f}",
                f"- Keywords: {', '.join(theme.keywords) if theme.keywords else 'None'}",
                f"- Validation: {_format_validation(theme.validation)}",
                f"- Theme memo: {theme.memo or 'None'}",
                "",
            ]
        )
        if theme.code_id:
            lines.extend([
                f"- Assigned code ID: {theme.code_id}",
                f"- Assigned code version: {theme.code_version}",
                f"- Definition provenance: {definition_status}",
            ])
            if entry is not None:
                lines.extend([
                    f"- Assigned code definition: {entry.description or 'Not specified'}",
                    f"- Assigned inclusion criteria: {entry.inclusion_criteria or 'Not specified'}",
                    f"- Assigned exclusion criteria: {entry.exclusion_criteria or 'Not specified'}",
                ])
                lines.extend(f"- Assigned positive example: {example}" for example in entry.examples)
                lines.extend(f"- Assigned negative example: {example}" for example in entry.negative_examples)
            lines.append("")
        lines.extend(["### Quote Evidence", ""])

        for quote in theme.quotes:
            lines.extend(
                [
                    _blockquote(quote.text),
                    "",
                    (
                        f"- [{theme.id} {theme.color}] Speaker: {quote.speaker} | "
                        f"Quote ID: {quote.quote_id} | Source: {quote.source_name} | "
                        f"Line: {quote.source_line} | {_quote_score_label(quote)}: {quote.relevance:.3f}"
                    ),
                    f"- Score interpretation: {_quote_score_interpretation(theme, quote)}",
                    f"- Selection rationale: {quote.rationale}",
                    f"- Quote memo: {quote.memo or 'None'}",
                    f"- Exact source offsets: {quote.source_start}:{quote.source_end}",
                    f"- Source SHA-256: {quote.source_hash or 'Unavailable in legacy result'}",
                    f"- Source review: {_source_review_note(result, quote)}",
                    "",
                ]
            )
            if quote.context_text:
                lines.extend([f"Context window ({quote.context_start}:{quote.context_end}); may include other speakers:", "", _blockquote(quote.context_text), ""])

    if result.themes:
        lines.extend(["## Color-Coded Transcript Evidence", ""])
        for theme in result.themes:
            for quote in theme.quotes:
                lines.extend(
                    [
                        f"- [{theme.id} {theme.color}] Source: {quote.source_name} | "
                        f"Line: {quote.source_line} | Speaker: {quote.speaker} | "
                        f"Quote ID: {quote.quote_id}",
                        "\n".join(f"  {line}" for line in _blockquote(quote.text).splitlines()),
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
            "selection_rationale",
            "validation_review_status",
            "methodology", "coding_logic", "analytic_depth", "research_questions",
            "theoretical_lens", "researcher_memo", "intent_version", "codebook_mode", "run_id",
            "code_id", "code_version", "code_definition", "inclusion_criteria", "exclusion_criteria",
            "negative_examples", "source_start", "source_end", "source_sha256",
            "context_start", "context_end", "context", "score_interpretation",
            "code_definition_status", "positive_examples", "code_origin",
            "theme_memo", "quote_memo", "recheck_reason", "source_review_note", "analysis_notes",
        ]
    )

    for theme in result.themes:
        keywords = "; ".join(theme.keywords)
        entry, definition_status = _theme_definition(result, theme)
        for quote in theme.quotes:
            writer.writerow([neutralize_csv_cell(str(value)) for value in
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
                    quote.rationale,
                    theme.validation.get("review_status", "Researcher review required"),
                    result.analytic_intent.methodology,
                    result.analytic_intent.coding_logic,
                    result.analytic_intent.analytic_depth,
                    json.dumps(result.analytic_intent.research_questions, ensure_ascii=False),
                    result.analytic_intent.theoretical_lens,
                    result.analytic_intent.researcher_memo,
                    result.analytic_intent.version,
                    result.codebook_mode,
                    result.run_id,
                    theme.code_id,
                    theme.code_version if theme.code_id else "",
                    entry.description if entry else "",
                    entry.inclusion_criteria if entry else "",
                    entry.exclusion_criteria if entry else "",
                    json.dumps(entry.negative_examples, ensure_ascii=False) if entry else "",
                    quote.source_start, quote.source_end, quote.source_hash,
                    quote.context_start, quote.context_end, quote.context_text,
                    _quote_score_interpretation(theme, quote),
                    definition_status,
                    json.dumps(entry.examples, ensure_ascii=False) if entry else "",
                    entry.origin if entry else "",
                    theme.memo, quote.memo, theme.validation.get("recheck_reason", ""),
                    _source_review_note(result, quote), json.dumps(result.notes, ensure_ascii=False),
                ]
            ])

    return buffer.getvalue()


def _format_validation(validation: dict[str, ValidationValue]) -> str:
    if not validation:
        return "Researcher review required"
    return (
        f"evidence={validation.get('evidence_count', 0)}, "
        f"sources={validation.get('source_count', 0)}, "
        f"speakers={validation.get('speaker_count', 0)}, "
        f"central alignment={validation.get('central_theme_alignment', 0)}, "
        f"status={validation.get('review_status', 'Researcher review required')}"
        + (f"; review again: {validation['recheck_reason']}" if validation.get("recheck_reason") else "")
    )

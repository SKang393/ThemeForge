from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import assert_never

from . import __version__
from .analysis import AnalysisSettings, TranscriptDocument, analyze_documents
from .analysis_types import AnalyticIntent
from .codebook import load_codebook_entries
from .exports import export_json, export_markdown, export_quotes_csv
from .io import load_transcript_text


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="themeforge",
        description="Suggest qualitative themes and quote evidence from interview transcripts.",
    )
    parser.add_argument(
        "inputs",
        nargs="+",
        type=Path,
        help="Transcript file(s) (.txt, .md, .csv, .docx, .rtf, or text-based .pdf)",
    )
    parser.add_argument("--out", type=Path, default=Path("analysis_report.md"), help="Report output path")
    parser.add_argument("--format", choices=("markdown", "json", "csv", "html"), default="markdown")
    parser.add_argument("--themes", type=int, default=8, help="Candidate cap; legacy mode uses a requested cluster count")
    parser.add_argument("--discovery-mode", choices=("auto", "open", "guided", "legacy"), default="auto", help="Without a codebook: open candidate discovery, research-context-guided discovery, or legacy phrase clusters. Auto uses guidance when context is supplied.")
    parser.add_argument("--quotes", type=int, default=0, help="Quotes to list per theme; 0 lists all matches")
    parser.add_argument("--semantic-backend", choices=("tfidf", "local-embeddings"), default="tfidf")
    parser.add_argument("--language", choices=("Auto", "English", "Korean", "Multilingual"), default="Auto")
    parser.add_argument("--methodology", choices=("unspecified", "reflexive", "codebook", "coding_reliability"), default="unspecified")
    parser.add_argument("--coding-logic", choices=("unspecified", "inductive", "deductive", "hybrid"), default="unspecified")
    parser.add_argument("--analytic-depth", choices=("unspecified", "semantic", "latent", "both"), default="unspecified")
    parser.add_argument("--research-question", action="append", default=[], help="Research question; repeat for multiple questions")
    parser.add_argument("--theoretical-lens", default="", help="Theoretical framework or analytic lens")
    parser.add_argument("--researcher-memo", default="", help="Researcher's analytic or reflexive memo")
    parser.add_argument("--codebook-mode", choices=("codebook_only", "hybrid", "legacy"), default="codebook_only", help="With a codebook: keep fixed codes, propose additional groups, or preserve legacy fallback")
    parser.add_argument(
        "--central-theme",
        default="",
        help="Optional focus topic that should be prioritized during theme and quote ranking",
    )
    parser.add_argument(
        "--codebook",
        type=Path,
        help="Optional CSV, TXT, DOCX, RTF, or text-based PDF codebook with theme names and descriptions",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = parser.parse_args()
    if any(not question.strip() for question in args.research_question):
        parser.error("--research-question must contain text")

    try:
        codebook_entries = load_codebook_entries(args.codebook) if args.codebook else ()
    except (OSError, ValueError) as error:
        parser.error(str(error))

    documents: list[TranscriptDocument] = []
    for input_path in args.inputs:
        try:
            text = load_transcript_text(input_path)
        except (OSError, ValueError) as error:
            parser.error(str(error))
        documents.append(TranscriptDocument(name=input_path.name, text=text))
    try:
        settings = AnalysisSettings(
            theme_count=args.themes,
            quotes_per_theme=args.quotes,
            central_theme=args.central_theme,
            semantic_backend=args.semantic_backend.replace("-", "_"),
            language_mode=args.language,
            codebook_entries=codebook_entries,
            analytic_intent=AnalyticIntent(
                methodology=args.methodology,
                coding_logic=args.coding_logic,
                analytic_depth=args.analytic_depth,
                research_questions=tuple(question.strip() for question in args.research_question),
                theoretical_lens=args.theoretical_lens.strip(),
                researcher_memo=args.researcher_memo.strip(),
            ),
            codebook_mode=args.codebook_mode,
            discovery_mode=args.discovery_mode,
        )
        result = analyze_documents(documents, settings)
    except ValueError as error:
        parser.error(str(error))

    match args.format:
        case "markdown":
            output = export_markdown(result)
        case "json":
            output = export_json(result)
        case "csv":
            output = export_quotes_csv(result)
        case "html":
            if not result.discovery:
                parser.error("HTML candidate-codebook reports require open or guided discovery without an imported codebook.")
            from .discovery_exports import export_discovery_html
            output = export_discovery_html(result)
        case unreachable:
            assert_never(unreachable)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(output, encoding="utf-8")
    console_encoding = sys.stdout.encoding or "ascii"
    display_path = str(args.out).encode(console_encoding, errors="backslashreplace").decode(console_encoding)
    print(f"Wrote {display_path}")
    print(f"Documents analyzed: {result.document_count}")
    print(f"Suggested {'codes' if result.discovery else 'themes'}: {len(result.themes)}")
    print(f"Quote units analyzed: {result.quote_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

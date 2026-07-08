from __future__ import annotations

import argparse
from pathlib import Path
from typing import assert_never

from . import __version__
from .analysis import AnalysisSettings, TranscriptDocument, analyze_documents
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
    parser.add_argument("--format", choices=("markdown", "json", "csv"), default="markdown")
    parser.add_argument("--themes", type=int, default=8, help="Maximum number of suggested themes")
    parser.add_argument("--quotes", type=int, default=0, help="Quotes to list per theme; 0 lists all matches")
    parser.add_argument("--semantic-backend", choices=("tfidf", "local-embeddings"), default="tfidf")
    parser.add_argument("--language", choices=("Auto", "English", "Korean", "Multilingual"), default="Auto")
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
    result = analyze_documents(
        documents,
        AnalysisSettings(
            theme_count=args.themes,
            quotes_per_theme=args.quotes,
            central_theme=args.central_theme,
            semantic_backend=args.semantic_backend.replace("-", "_"),
            language_mode=args.language,
            codebook_entries=codebook_entries,
        ),
    )

    match args.format:
        case "markdown":
            output = export_markdown(result)
        case "json":
            output = export_json(result)
        case "csv":
            output = export_quotes_csv(result)
        case unreachable:
            assert_never(unreachable)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(output, encoding="utf-8")
    print(f"Wrote {args.out}")
    print(f"Documents analyzed: {result.document_count}")
    print(f"Suggested themes: {len(result.themes)}")
    print(f"Quote units analyzed: {result.quote_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

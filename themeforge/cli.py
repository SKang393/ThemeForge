from __future__ import annotations

import argparse
from pathlib import Path

from . import __version__
from .analysis import AnalysisSettings, TranscriptDocument, analyze_documents
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
        help="Transcript file(s) (.txt, .md, .csv, .docx, or .rtf)",
    )
    parser.add_argument("--out", type=Path, default=Path("analysis_report.md"), help="Report output path")
    parser.add_argument("--format", choices=("markdown", "json", "csv"), default="markdown")
    parser.add_argument("--themes", type=int, default=8, help="Maximum number of suggested themes")
    parser.add_argument("--quotes", type=int, default=0, help="Quotes to list per theme; 0 lists all matches")
    parser.add_argument(
        "--central-theme",
        default="",
        help="Optional focus topic that should be prioritized during theme and quote ranking",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = parser.parse_args()

    documents = [
        TranscriptDocument(name=input_path.name, text=load_transcript_text(input_path))
        for input_path in args.inputs
    ]
    result = analyze_documents(
        documents,
        AnalysisSettings(
            theme_count=args.themes,
            quotes_per_theme=args.quotes,
            central_theme=args.central_theme,
        ),
    )

    if args.format == "markdown":
        output = export_markdown(result)
    elif args.format == "json":
        output = export_json(result)
    else:
        output = export_quotes_csv(result)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(output, encoding="utf-8")
    print(f"Wrote {args.out}")
    print(f"Documents analyzed: {result.document_count}")
    print(f"Suggested themes: {len(result.themes)}")
    print(f"Quote units analyzed: {result.quote_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

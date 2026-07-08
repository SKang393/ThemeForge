from __future__ import annotations

from pathlib import Path
from typing import Mapping, Sequence

ValidationValue = str | int | float


APP_THEMES = {
    "Light": {
        "background": "#f7f8fa",
        "surface": "#ffffff",
        "surface_alt": "#f1f5f9",
        "border": "#d9dee7",
        "text": "#1f2937",
        "muted": "#667085",
        "accent": "#2563eb",
        "accent_hover": "#1d4ed8",
        "warning": "#92400e",
        "transcript_background": "#ffffff",
        "transcript_text": "#111827",
        "selected_quote": "#fef3c7",
        "highlight_foreground": "#111827",
    },
    "Dark": {
        "background": "#111827",
        "surface": "#1f2937",
        "surface_alt": "#374151",
        "border": "#4b5563",
        "text": "#f9fafb",
        "muted": "#cbd5e1",
        "accent": "#60a5fa",
        "accent_hover": "#93c5fd",
        "warning": "#fbbf24",
        "transcript_background": "#0f172a",
        "transcript_text": "#f8fafc",
        "selected_quote": "#854d0e",
        "highlight_foreground": "#f8fafc",
    },
}

PROFESSIONAL_THEME = APP_THEMES["Light"]

REPOSITORY_URL = "https://github.com/SKang393/ThemeForge"
ARCHIVE_DOI_URL = "https://doi.org/10.5281/zenodo.20653169"


def file_selection_summary(paths: Sequence[Path]) -> str:
    if not paths:
        return "No transcripts selected"
    if len(paths) == 1:
        return Path(paths[0]).name
    return f"{len(paths)} transcripts selected"


def codebook_selection_summary(path: Path | None, entry_count: int = 0) -> str:
    if path is None:
        return "No codebook selected"
    if entry_count > 0:
        return f"{path.name} | {entry_count} themes"
    return path.name


def analysis_status_text(document_count: int, quote_count: int, theme_count: int) -> str:
    return f"{document_count} documents | {quote_count} quote units | {theme_count} themes"


def theme_list_label(name: str, color: str, quote_count: int, focus_alignment: float) -> str:
    del color
    return f"{name}  |  {quote_count} quotes  |  {_percent(focus_alignment)} focus"


def format_validation_summary(validation: Mapping[str, ValidationValue]) -> str:
    if not validation:
        return "Researcher review required"
    return (
        f"Evidence {validation.get('evidence_count', 0)} | "
        f"Sources {validation.get('source_count', 0)} | "
        f"Speakers {validation.get('speaker_count', 0)} | "
        f"Focus {_percent(validation.get('central_theme_alignment', 0))} | "
        f"{validation.get('review_status', 'Researcher review required')}"
    )


def evidence_navigation_status(current_index: int, total_count: int) -> str:
    if total_count <= 0:
        return "No matching quote selected"
    bounded_index = max(0, min(current_index, total_count - 1))
    return f"Quote {bounded_index + 1} of {total_count}"


def transcript_tab_label(name: str, max_length: int = 28) -> str:
    if len(name) <= max_length:
        return name
    return f"{name[: max(1, max_length - 3)]}..."


def about_text(version: str) -> str:
    return "\n".join(
        [
            f"ThemeForge v{version}",
            "",
            "Practical qualitative coding and quote-evidence review for interview and focus group transcripts.",
            "",
            "Developer: SKang393",
            f"Repository: {REPOSITORY_URL}",
            f"Archive DOI: {ARCHIVE_DOI_URL}",
            "License: Apache License 2.0",
        ]
    )


def _percent(value: ValidationValue) -> str:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        numeric = 0.0
    return f"{round(max(0.0, min(1.0, numeric)) * 100):.0f}%"

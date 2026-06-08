from __future__ import annotations

from pathlib import Path
from typing import Mapping, Sequence


PROFESSIONAL_THEME = {
    "background": "#f7f8fa",
    "surface": "#ffffff",
    "surface_alt": "#f1f5f9",
    "border": "#d9dee7",
    "text": "#1f2937",
    "muted": "#667085",
    "accent": "#2563eb",
    "accent_hover": "#1d4ed8",
    "warning": "#92400e",
}


def file_selection_summary(paths: Sequence[Path]) -> str:
    if not paths:
        return "No transcripts selected"
    if len(paths) == 1:
        return Path(paths[0]).name
    return f"{len(paths)} transcripts selected"


def analysis_status_text(document_count: int, quote_count: int, theme_count: int) -> str:
    return f"{document_count} documents | {quote_count} quote units | {theme_count} themes"


def theme_list_label(name: str, color: str, quote_count: int, focus_alignment: float) -> str:
    del color
    return f"{name}  |  {quote_count} quotes  |  {_percent(focus_alignment)} focus"


def format_validation_summary(validation: Mapping[str, object]) -> str:
    if not validation:
        return "Researcher review required"
    return (
        f"Evidence {validation.get('evidence_count', 0)} | "
        f"Sources {validation.get('source_count', 0)} | "
        f"Speakers {validation.get('speaker_count', 0)} | "
        f"Focus {_percent(validation.get('central_theme_alignment', 0))} | "
        f"{validation.get('review_status', 'Researcher review required')}"
    )


def _percent(value: object) -> str:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        numeric = 0.0
    return f"{round(max(0.0, min(1.0, numeric)) * 100):.0f}%"

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
import zipfile

from .audit import AuditEvent
from .analysis_types import (
    AnalysisResult,
    AnalysisSettings,
    CodebookEntry,
    Theme,
    ThemeQuote,
    TranscriptDocument,
    ValidationValue,
)

PROJECT_FORMAT_VERSION = 2
PROJECT_JSON = "project.json"


@dataclass(frozen=True, slots=True)
class UnsupportedProjectFormatError(Exception):
    format_version: int | str
    supported_version: int

    def __str__(self) -> str:
        return f"project format {self.format_version} is unsupported; supported format is {self.supported_version}"


@dataclass(frozen=True, slots=True)
class ProjectState:
    transcript_paths: tuple[Path, ...]
    codebook_path: Path | None
    documents: tuple[TranscriptDocument, ...]
    settings: AnalysisSettings
    result: AnalysisResult | None = None
    researcher_name: str = ""
    audit_events: tuple[AuditEvent, ...] = ()


def save_project(path: Path, state: ProjectState) -> None:
    payload = {
        "format_version": PROJECT_FORMAT_VERSION,
        "transcript_paths": [str(item) for item in state.transcript_paths],
        "codebook_path": str(state.codebook_path) if state.codebook_path is not None else None,
        "documents": [asdict(document) for document in state.documents],
        "settings": _settings_to_json(state.settings),
        "result": _result_to_json(state.result),
        "researcher_name": state.researcher_name,
        "audit_events": [asdict(event) for event in state.audit_events],
    }
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(PROJECT_JSON, json.dumps(payload, ensure_ascii=False, indent=2))


def load_project(path: Path) -> ProjectState:
    with zipfile.ZipFile(path) as archive:
        payload = json.loads(archive.read(PROJECT_JSON).decode("utf-8"))

    raw_format_version = payload.get("format_version", 1)
    try:
        format_version = int(raw_format_version)
    except (TypeError, ValueError):
        raise UnsupportedProjectFormatError(
            format_version=str(raw_format_version),
            supported_version=PROJECT_FORMAT_VERSION,
        ) from None
    _reject_unsupported_format(format_version)
    return ProjectState(
        transcript_paths=tuple(Path(item) for item in payload.get("transcript_paths", [])),
        codebook_path=_optional_path(payload.get("codebook_path")),
        documents=tuple(_document_from_json(item) for item in payload.get("documents", []) if isinstance(item, dict)),
        settings=_settings_from_json(payload.get("settings", {})),
        result=_result_from_json(payload.get("result")),
        researcher_name=str(payload.get("researcher_name", "")),
        audit_events=tuple(_audit_event_from_json(item) for item in payload.get("audit_events", []) if isinstance(item, dict)),
    )


def _reject_unsupported_format(format_version: int) -> None:
    if format_version > PROJECT_FORMAT_VERSION:
        raise UnsupportedProjectFormatError(format_version=format_version, supported_version=PROJECT_FORMAT_VERSION)


def _optional_path(value: str | None) -> Path | None:
    if not value:
        return None
    return Path(value)


def _settings_to_json(settings: AnalysisSettings) -> dict[str, ValidationValue | list[dict[str, str | list[str]]]]:
    return {
        "theme_count": settings.theme_count,
        "quotes_per_theme": settings.quotes_per_theme,
        "min_theme_size": settings.min_theme_size,
        "min_quote_words": settings.min_quote_words,
        "agglomerative_limit": settings.agglomerative_limit,
        "central_theme": settings.central_theme,
        "semantic_backend": settings.semantic_backend,
        "language_mode": settings.language_mode,
        "codebook_entries": [
            {
                "name": entry.name,
                "description": entry.description,
                "examples": list(entry.examples),
            }
            for entry in settings.codebook_entries
        ],
    }


def _settings_from_json(payload: dict[str, ValidationValue | list[dict[str, str | list[str]]]]) -> AnalysisSettings:
    entries = payload.get("codebook_entries", [])
    return AnalysisSettings(
        theme_count=int(payload.get("theme_count", 8)),
        quotes_per_theme=int(payload.get("quotes_per_theme", 0)),
        min_theme_size=int(payload.get("min_theme_size", 1)),
        min_quote_words=int(payload.get("min_quote_words", 4)),
        agglomerative_limit=int(payload.get("agglomerative_limit", 120)),
        central_theme=str(payload.get("central_theme", "")),
        semantic_backend=str(payload.get("semantic_backend", "tfidf")),
        language_mode=str(payload.get("language_mode", "Auto")),
        codebook_entries=tuple(_codebook_entry_from_json(item) for item in entries if isinstance(item, dict)),
    )


def _codebook_entry_from_json(payload: dict[str, str | list[str]]) -> CodebookEntry:
    examples = payload.get("examples", [])
    return CodebookEntry(
        name=str(payload.get("name", "")),
        description=str(payload.get("description", "")),
        examples=tuple(str(item) for item in examples) if isinstance(examples, list) else (),
    )


def _document_from_json(payload: dict[str, object]) -> TranscriptDocument:  # noqa: OBJECT_OK - JSON boundary.
    return TranscriptDocument(
        name=str(payload.get("name", "")),
        text=str(payload.get("text", "")),
        memo=str(payload.get("memo", "")),
    )


def _audit_event_from_json(payload: dict[str, object]) -> AuditEvent:  # noqa: OBJECT_OK - JSON boundary.
    return AuditEvent(
        timestamp_utc=str(payload.get("timestamp_utc", "")),
        actor=str(payload.get("actor", "")),
        action=str(payload.get("action", "")),
        target_type=str(payload.get("target_type", "")),
        target_id=str(payload.get("target_id", "")),
        details=str(payload.get("details", "")),
    )


def _result_to_json(result: AnalysisResult | None) -> dict[str, object] | None:  # noqa: OBJECT_OK - JSON boundary.
    if result is None:
        return None
    return asdict(result)


def _result_from_json(payload: dict[str, object] | None) -> AnalysisResult | None:  # noqa: OBJECT_OK - JSON boundary.
    if payload is None:
        return None
    themes = [
        Theme(
            id=str(theme["id"]),
            name=str(theme["name"]),
            color=str(theme["color"]),
            keywords=[str(item) for item in theme.get("keywords", [])],
            quote_count=int(theme.get("quote_count", 0)),
            score=float(theme.get("score", 0.0)),
            quotes=[_quote_from_json(item) for item in theme.get("quotes", [])],
            validation=_validation_from_json(theme.get("validation", {})),
            memo=str(theme.get("memo", "")),
            parent_theme_id=str(theme.get("parent_theme_id", "")),
        )
        for theme in payload.get("themes", [])
        if isinstance(theme, dict)
    ]
    return AnalysisResult(
        version=str(payload.get("version", "")),
        document_count=int(payload.get("document_count", 0)),
        quote_count=int(payload.get("quote_count", 0)),
        themes=themes,
        notes=[str(item) for item in payload.get("notes", [])],
        validation_summary=[str(item) for item in payload.get("validation_summary", [])],
    )


def _quote_from_json(payload: dict[str, object]) -> ThemeQuote:  # noqa: OBJECT_OK - JSON boundary.
    return ThemeQuote(
        quote_id=str(payload.get("quote_id", "")),
        speaker=str(payload.get("speaker", "")),
        text=str(payload.get("text", "")),
        relevance=float(payload.get("relevance", 0.0)),
        source_line=int(payload.get("source_line", 0)),
        source_name=str(payload.get("source_name", "Transcript")),
        rationale=str(payload.get("rationale", "Selected as a candidate quote for researcher review.")),
        source_start=int(payload.get("source_start", 0)),
        source_end=int(payload.get("source_end", 0)),
        memo=str(payload.get("memo", "")),
    )


def _validation_from_json(payload: object) -> dict[str, ValidationValue]:  # noqa: OBJECT_OK - JSON boundary.
    if not isinstance(payload, dict):
        return {}
    return {
        str(key): value
        for key, value in payload.items()
        if isinstance(value, str | int | float)
    }

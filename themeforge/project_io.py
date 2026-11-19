from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import math
import os
from pathlib import Path
import tempfile
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
NULLABLE_PROFILE = "2-nullable-scores"
NULLABLE_MEMBER = "project-nullable-scores.json"


class ProjectCopyRequired(ValueError):
    """An older project must not be replaced with one its reader cannot open."""


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

    @property
    def needs_nullable_profile(self) -> bool:
        return (any(e.action in {"codebook_passage_assigned", "quote_reassigned"} for e in self.audit_events)
                or self.result is not None and any(t.reported_score is None for t in self.result.themes))


def _data_member(archive: zipfile.ZipFile) -> str:
    members = [n for n in archive.namelist() if n in (PROJECT_JSON, NULLABLE_MEMBER)]
    if len(members) != 1:
        raise ValueError("Expected one recognizable project data member; the archive is ambiguous or unsupported.")
    return members[0]


def save_project(path: Path, state: ProjectState, *, new_only: bool = False) -> None:
    path = Path(path)
    nullable = state.needs_nullable_profile
    if not new_only and path.exists():
        try:
            with zipfile.ZipFile(path) as existing:
                old_member = _data_member(existing)
        except zipfile.BadZipFile as exc:
            raise ValueError("The existing project is unreadable; choose a new file instead.") from exc
        if nullable and old_member != NULLABLE_MEMBER:
            raise ProjectCopyRequired("Older project unchanged. Use Save project to create a separate nullable-score copy.")
        nullable = nullable or old_member == NULLABLE_MEMBER
    elif nullable:
        new_only = True  # Never overwrite a competing file while creating the first copy.
    member = NULLABLE_MEMBER if nullable else PROJECT_JSON
    payload = {
        "format_version": NULLABLE_PROFILE if nullable else PROJECT_FORMAT_VERSION,
        "transcript_paths": [str(item) for item in state.transcript_paths],
        "codebook_path": str(state.codebook_path) if state.codebook_path is not None else None,
        "documents": [asdict(document) for document in state.documents],
        "settings": _settings_to_json(state.settings),
        "result": _result_to_json(state.result),
        "researcher_name": state.researcher_name,
        "audit_events": [asdict(event) for event in state.audit_events],
    }
    # Serialize before touching disk, then replace only a complete sibling archive.
    serialized = json.dumps(payload, ensure_ascii=False, indent=2)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w+b", prefix=f".{path.name}.", suffix=".tmp", dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr(member, serialized)
            stream.flush()
            os.fsync(stream.fileno())
        if not new_only:
            os.replace(temporary, path)
        elif os.name == "nt":
            os.rename(temporary, path)  # Windows fails atomically if the destination already exists.
        else:
            os.link(temporary, path)  # POSIX rename would replace an existing file.
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def load_project(path: Path) -> ProjectState:
    with zipfile.ZipFile(path) as archive:
        member = _data_member(archive)
        payload = json.loads(archive.read(member).decode("utf-8"))

    raw_format_version = payload.get("format_version", 1)
    nullable = member == NULLABLE_MEMBER
    if nullable:
        if raw_format_version != NULLABLE_PROFILE:
            raise UnsupportedProjectFormatError(str(raw_format_version), PROJECT_FORMAT_VERSION)
    elif not isinstance(raw_format_version, (int, str)) or str(raw_format_version) not in ("1", "2"):
        raise UnsupportedProjectFormatError(str(raw_format_version), PROJECT_FORMAT_VERSION)
    return ProjectState(
        transcript_paths=tuple(Path(item) for item in payload.get("transcript_paths", [])),
        codebook_path=_optional_path(payload.get("codebook_path")),
        documents=tuple(_document_from_json(item) for item in payload.get("documents", []) if isinstance(item, dict)),
        settings=_settings_from_json(payload.get("settings", {})),
        result=_result_from_json(payload.get("result"), nullable=nullable),
        researcher_name=str(payload.get("researcher_name", "")),
        audit_events=tuple(_audit_event_from_json(item) for item in payload.get("audit_events", []) if isinstance(item, dict)),
    )


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
    payload = asdict(result)
    for theme, item in zip(result.themes, payload["themes"], strict=True):
        _write_score(item, "score", theme.reported_score)
        for quote, row in zip(theme.quotes, item["quotes"], strict=True):
            _write_score(row, "relevance", quote.reported_relevance)
    return payload


def _write_score(item: dict, key: str, reported: float | None) -> None:
    # A mixed group can retain a pre-edit numeric value that is not its current score.
    if reported is None and item[key] is not None:
        item[f"unreported_stored_{key}"] = item[key]
    item[key] = reported


def _read_score(item: dict, key: str, nullable: bool, *, legacy_zero: bool, legacy_move: bool) -> float | None:
    if nullable and key not in item:
        raise ValueError(f"Missing required {key} in nullable-score project.")
    value = item.get(key)
    prior = f"unreported_stored_{key}"
    if nullable and prior in item:
        if value is not None or item[prior] is None:
            raise ValueError(f"Conflicting stored and current {key}.")
        value = item[prior]
    if value is None:
        return None
    if not nullable and isinstance(value, str):
        value = float(value)  # Legacy readers accepted numeric strings.
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"Invalid numeric {key}.")
    if not nullable and ((legacy_zero and value == 0) or (legacy_move and value == -1)):
        return None
    return float(value)


def _result_from_json(payload: dict[str, object] | None, *, nullable: bool) -> AnalysisResult | None:  # noqa: OBJECT_OK - JSON boundary.
    if payload is None:
        return None
    themes = []
    for theme in payload.get("themes", []):
        if not isinstance(theme, dict):
            continue
        validation = _validation_from_json(theme.get("validation", {}))
        decoded = Theme(
            id=str(theme["id"]),
            name=str(theme["name"]),
            color=str(theme["color"]),
            keywords=[str(item) for item in theme.get("keywords", [])],
            quote_count=int(theme.get("quote_count", 0)),
            score=_read_score(theme, "score", nullable, legacy_zero="manual_codebook_definition" in validation,
                              legacy_move=validation.get("score_scope") == "Not scored"),
            quotes=[_quote_from_json(item, nullable=nullable) for item in theme.get("quotes", [])],
            validation=validation,
            memo=str(theme.get("memo", "")),
            parent_theme_id=str(theme.get("parent_theme_id", "")),
        )
        if nullable and theme["score"] != decoded.reported_score:
            raise ValueError("Group score conflicts with its recorded score scope.")
        themes.append(decoded)
    return AnalysisResult(
        version=str(payload.get("version", "")),
        document_count=int(payload.get("document_count", 0)),
        quote_count=int(payload.get("quote_count", 0)),
        themes=themes,
        notes=[str(item) for item in payload.get("notes", [])],
        validation_summary=[str(item) for item in payload.get("validation_summary", [])],
    )


def _quote_from_json(payload: dict[str, object], *, nullable: bool) -> ThemeQuote:  # noqa: OBJECT_OK - JSON boundary.
    quote_id = str(payload.get("quote_id", ""))
    rationale = str(payload.get("rationale", "Selected as a candidate quote for researcher review."))
    decoded = ThemeQuote(
        quote_id=quote_id,
        speaker=str(payload.get("speaker", "")),
        text=str(payload.get("text", "")),
        relevance=_read_score(payload, "relevance", nullable, legacy_zero=quote_id.startswith("MQD"),
                              legacy_move=rationale.startswith("Manual move by ")),
        source_line=int(payload.get("source_line", 0)),
        source_name=str(payload.get("source_name", "Transcript")),
        rationale=rationale,
        source_start=int(payload.get("source_start", 0)),
        source_end=int(payload.get("source_end", 0)),
        memo=str(payload.get("memo", "")),
    )
    if nullable and payload["relevance"] != decoded.reported_relevance:
        raise ValueError("Quote relevance conflicts with its recorded score scope.")
    return decoded


def _validation_from_json(payload: object) -> dict[str, ValidationValue]:  # noqa: OBJECT_OK - JSON boundary.
    if not isinstance(payload, dict):
        return {}
    return {
        str(key): value
        for key, value in payload.items()
        if isinstance(value, str | int | float)
    }

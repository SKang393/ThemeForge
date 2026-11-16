from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import json
import os
import tempfile
from hashlib import sha256
from pathlib import Path
import zipfile

from .audit import AuditEvent
from .analysis_types import (
    AnalysisResult,
    AnalysisSettings,
    AnalyticIntent,
    CodebookEntry,
    QuoteUnit,
    Theme,
    ThemeQuote,
    TranscriptDocument,
    ValidationValue,
)

PROJECT_FORMAT_VERSION = 3
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
        "documents": [
            {**asdict(document), "source_hash": sha256(document.text.encode("utf-8")).hexdigest()}
            for document in state.documents
        ],
        "settings": _settings_to_json(state.settings),
        "result": _result_to_json(state.result),
        "researcher_name": state.researcher_name,
        "audit_events": [asdict(event) for event in state.audit_events],
    }
    # Serialize before touching disk, then replace only a complete sibling archive.
    serialized = json.dumps(payload, ensure_ascii=False, indent=2)
    path = Path(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w+b", prefix=f".{path.name}.", suffix=".tmp", dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr(PROJECT_JSON, serialized)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


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
    settings = _settings_from_json(payload.get("settings", {}), legacy=format_version < 3)
    result = _result_from_json(payload.get("result"))
    if result is not None and any(entry.code_id.startswith("legacy_row_") for entry in settings.codebook_entries):
        notice = "Legacy codebook rows with duplicate identities were preserved with distinct IDs; review these rows before merging or recoding."
        if notice not in result.notes:
            result = replace(result, notes=[*result.notes, notice])
    return ProjectState(
        transcript_paths=tuple(Path(item) for item in payload.get("transcript_paths", [])),
        codebook_path=_optional_path(payload.get("codebook_path")),
        documents=tuple(_document_from_json(item) for item in payload.get("documents", []) if isinstance(item, dict)),
        settings=settings,
        result=result,
        researcher_name=str(payload.get("researcher_name", "")),
        audit_events=tuple(_audit_event_from_json(item) for item in payload.get("audit_events", []) if isinstance(item, dict)),
    )


def _reject_unsupported_format(format_version: int) -> None:
    if format_version < 1 or format_version > PROJECT_FORMAT_VERSION:
        raise UnsupportedProjectFormatError(format_version=format_version, supported_version=PROJECT_FORMAT_VERSION)


def _optional_path(value: str | None) -> Path | None:
    if not value:
        return None
    return Path(value)


def _settings_to_json(settings: AnalysisSettings) -> dict[str, object]:
    return asdict(settings)


def _settings_from_json(payload: dict[str, object], *, legacy: bool = False) -> AnalysisSettings:
    entries = payload.get("codebook_entries", [])
    loaded_entries = [_codebook_entry_from_json(item) for item in entries if isinstance(item, dict)]
    if legacy:
        seen: set[str] = set()
        for index, entry in enumerate(loaded_entries):
            if entry.code_id in seen:
                entry = replace(entry, code_id=f"legacy_row_{index + 1}_{entry.code_id}")
                loaded_entries[index] = entry
            seen.add(entry.code_id)
    return AnalysisSettings(
        theme_count=int(payload.get("theme_count", 8)),
        quotes_per_theme=int(payload.get("quotes_per_theme", 0)),
        min_theme_size=int(payload.get("min_theme_size", 1)),
        min_quote_words=int(payload.get("min_quote_words", 4)),
        agglomerative_limit=int(payload.get("agglomerative_limit", 120)),
        central_theme=str(payload.get("central_theme", "")),
        semantic_backend=str(payload.get("semantic_backend", "tfidf")),
        language_mode=str(payload.get("language_mode", "Auto")),
        codebook_entries=tuple(loaded_entries),
        analytic_intent=_intent_from_json(payload.get("analytic_intent", {})),
        # Old projects keep their recorded workflow instead of silently adopting new semantics.
        codebook_mode=str(payload.get("codebook_mode", "legacy")),
        discovery_mode=str(payload.get("discovery_mode", "legacy")),
    )


def _intent_from_json(payload: object) -> AnalyticIntent:
    if not isinstance(payload, dict):
        raise ValueError("Analytic intent must be an object.")
    return AnalyticIntent(
        methodology=str(payload.get("methodology", "unspecified")),
        coding_logic=str(payload.get("coding_logic", "unspecified")),
        analytic_depth=str(payload.get("analytic_depth", "unspecified")),
        research_questions=_strings_from_json(payload.get("research_questions", [])),
        theoretical_lens=str(payload.get("theoretical_lens", "")),
        researcher_memo=str(payload.get("researcher_memo", "")),
        version=int(payload.get("version", 1)),
    )


def _strings_from_json(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValueError("Expected a JSON array of text values.")
    return tuple(str(item) for item in value)


def _codebook_entry_from_json(payload: dict[str, object]) -> CodebookEntry:
    return CodebookEntry(
        name=str(payload.get("name", "")),
        description=str(payload.get("description", "")),
        examples=_strings_from_json(payload.get("examples", [])),
        code_id=str(payload.get("code_id", "")),
        version=int(payload.get("version", 1)),
        inclusion_criteria=str(payload.get("inclusion_criteria", "")),
        exclusion_criteria=str(payload.get("exclusion_criteria", "")),
        negative_examples=_strings_from_json(payload.get("negative_examples", [])),
        origin=str(payload.get("origin", "unspecified")),
    )


def _document_from_json(payload: dict[str, object]) -> TranscriptDocument:  # noqa: OBJECT_OK - JSON boundary.
    text = str(payload.get("text", ""))
    expected_hash = payload.get("source_hash", "")
    if expected_hash and expected_hash != sha256(text.encode("utf-8")).hexdigest():
        raise ValueError(f"Cached transcript hash mismatch: {payload.get('name', '')}")
    return TranscriptDocument(
        name=str(payload.get("name", "")),
        text=text,
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
            code_id=str(theme.get("code_id", "")),
            code_version=int(theme.get("code_version", 0)),
            code_definition=_codebook_entry_from_json(theme["code_definition"]) if isinstance(theme.get("code_definition"), dict) else None,
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
        analytic_intent=_intent_from_json(payload.get("analytic_intent", {})),
        codebook_mode=str(payload.get("codebook_mode", "legacy")),
        codebook_snapshot=tuple(_codebook_entry_from_json(item) for item in payload.get("codebook_snapshot", [])),
        evidence_units=tuple(_evidence_from_json(item) for item in payload.get("evidence_units", [])),
        run_id=str(payload.get("run_id", "")),
        source_hashes={str(key): str(value) for key, value in payload.get("source_hashes", {}).items()},
        source_lengths={str(key): int(value) for key, value in payload.get("source_lengths", {}).items()},
        analysis_parameters=_validation_from_json(payload.get("analysis_parameters", {})),
        discovery=dict(payload.get("discovery", {})),
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
        source_hash=str(payload.get("source_hash", "")),
        context_start=int(payload.get("context_start", 0)),
        context_end=int(payload.get("context_end", 0)),
        context_text=str(payload.get("context_text", "")),
    )


def _evidence_from_json(payload: dict[str, object]) -> QuoteUnit:
    return QuoteUnit(
        id=str(payload.get("id", "")),
        speaker=str(payload.get("speaker", "")),
        text=str(payload.get("text", "")),
        source_line=int(payload.get("source_line", 0)),
        word_count=int(payload.get("word_count", 0)),
        source_name=str(payload.get("source_name", "Transcript")),
        source_start=int(payload.get("source_start", 0)),
        source_end=int(payload.get("source_end", 0)),
        source_hash=str(payload.get("source_hash", "")),
        context_start=int(payload.get("context_start", 0)),
        context_end=int(payload.get("context_end", 0)),
        context_text=str(payload.get("context_text", "")),
        exclusion_reason=str(payload.get("exclusion_reason", "")),
    )


def _validation_from_json(payload: object) -> dict[str, ValidationValue]:  # noqa: OBJECT_OK - JSON boundary.
    if not isinstance(payload, dict):
        return {}
    return {
        str(key): value
        for key, value in payload.items()
        if isinstance(value, str | int | float)
    }

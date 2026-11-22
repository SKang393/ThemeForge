from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from hashlib import sha256
import json
import math
import os
from pathlib import Path, PureWindowsPath
import tempfile
from typing import BinaryIO
import zipfile

from .audit import AuditEvent
from .analysis_types import (
    AnalysisResult,
    AnalysisSettings,
    CodebookEntry,
    SpeakerOverride,
    Theme,
    ThemeQuote,
    TranscriptDocument,
    ValidationValue,
)

PROJECT_FORMAT_VERSION = 2
PROJECT_JSON = "project.json"
NULLABLE_PROFILE = "2-nullable-scores"
NULLABLE_MEMBER = "project-nullable-scores.json"
CASE_MEMBER = "project-case-master.json"
CASE_PROFILE = "3-case-master"
CASE_NULLABLE_PROFILE = "3-case-master-nullable-scores"
STAGED_PROFILE = "4-case-master-pending"
STAGED_NULLABLE_PROFILE = "4-case-master-pending-nullable-scores"


@dataclass(frozen=True, slots=True)
class PendingVisit:
    path: Path
    text: str

    @property
    def name(self) -> str:
        # Preserve the original filename when opening a Windows study on another OS.
        return self.path.name if self.path.is_absolute() else PureWindowsPath(self.path).name


class ProjectCopyRequired(ValueError):
    """An older project must not be replaced with one its reader cannot open."""

    def __init__(self, message: str, *, suffix: str = "-unscored",
                 title: str = "Save a separate nullable-score project (original stays unchanged)"):
        super().__init__(message)
        self.suffix = suffix
        self.title = title


class ProjectChanged(ValueError):
    def __init__(self):
        super().__init__("The saved project changed or was removed. Your changes remain open. "
                         "Use Save as… with a new filename to keep them, or reopen the project on disk.")


@dataclass(frozen=True, slots=True)
class UnsupportedProjectFormatError(ValueError):
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
    # Identifies the disk snapshot. It is neither study content nor part of the project format.
    file_revision: bytes | None = field(default=None, compare=False, repr=False)
    is_case_master: bool = field(default=False, compare=False)
    pending_visits: tuple[PendingVisit, ...] = ()

    @property
    def needs_case_master(self) -> bool:
        return self.is_case_master or bool(self.pending_visits) or any(doc.case_label.strip() for doc in self.documents)

    def collection_sources(self) -> tuple[PendingVisit, ...]:
        """Validate the embedded source identities without consulting the original files."""
        if not self.documents or len(self.transcript_paths) != len(self.documents):
            raise ValueError("Every earlier transcript needs its recorded source path before appending visits.")
        previous = tuple(PendingVisit(path, doc.text) for path, doc in zip(self.transcript_paths, self.documents))
        if any(source.name != doc.name for source, doc in zip(previous, self.documents)):
            raise ValueError("A recorded path and transcript name differ or a filename is ambiguous.")
        sources = previous + self.pending_visits
        if any(not (v.path.is_absolute() or PureWindowsPath(v.path).is_absolute()) or not v.name.strip()
               or not v.text.strip() for v in sources):
            raise ValueError("Each visit needs an absolute source path, filename and nonempty transcript.")
        if len({v.name.casefold() for v in sources}) != len(sources):
            raise ValueError("A transcript filename is already present or ambiguous. Choose a distinct visit.")
        if len({v.text for v in sources}) != len(sources):
            raise ValueError("Two sources have identical transcript text. Choose a distinct visit.")
        pending_names = {v.name for v in self.pending_visits}
        if self.result and any(q.source_name in pending_names for t in self.result.themes for q in t.quotes):
            raise ValueError("A pending visit appears in the earlier analysis.")
        return sources

    @property
    def needs_nullable_profile(self) -> bool:
        return (any(e.action in {"codebook_passage_assigned", "quote_reassigned", "theme_merge_recorded",
                                 "selection_assignment_recorded"}
                    for e in self.audit_events)
                or self.result is not None and any(t.reported_score is None for t in self.result.themes))


def _data_member(archive: zipfile.ZipFile) -> str:
    members = [n for n in archive.namelist() if n in (PROJECT_JSON, NULLABLE_MEMBER, CASE_MEMBER)]
    if len(members) != 1:
        raise ValueError("Expected one recognizable project data member; the archive is ambiguous or unsupported.")
    return members[0]


def _project_profile(member: str, payload: dict) -> tuple[bool, bool]:
    """Return whether this is a case master and whether it stores nullable scores."""
    if not isinstance(payload, dict):
        raise ValueError("Expected a project data object.")
    version = payload.get("format_version", 1)
    if member == CASE_MEMBER and version in (STAGED_PROFILE, STAGED_NULLABLE_PROFILE):
        return True, version == STAGED_NULLABLE_PROFILE
    if "pending_visits" in payload:
        raise ValueError("Pending visits require a staged case master.")
    if member == CASE_MEMBER and version in (CASE_PROFILE, CASE_NULLABLE_PROFILE):
        return True, version == CASE_NULLABLE_PROFILE
    if member == NULLABLE_MEMBER and version == NULLABLE_PROFILE:
        return False, True
    if member == PROJECT_JSON and isinstance(version, (int, str)) and str(version) in ("1", "2"):
        return False, False
    raise UnsupportedProjectFormatError(str(version), PROJECT_FORMAT_VERSION)


def _revision(stream: BinaryIO) -> bytes:
    stream.seek(0)
    digest = sha256()
    for block in iter(lambda: stream.read(256 * 1024), b""):
        digest.update(block)
    return digest.digest()


def file_revision(path: Path) -> bytes | None:
    try:
        with path.open("rb") as stream:
            return _revision(stream)
    except FileNotFoundError:
        return None


def ensure_project_revision(path: Path, expected_bytes: bytes) -> None:
    """Require the same complete archive bytes that were checked when the project was opened."""
    if expected_bytes is None or file_revision(Path(path)) != expected_bytes:
        raise ProjectChanged()


def _check_input_revisions(revisions: tuple[tuple[Path, bytes], ...], transcripts: set[Path]) -> None:
    for source, revision in revisions:
        if source in transcripts:
            if file_revision(source) != revision:
                raise ValueError(f"The transcript changed or disappeared before saving: {source.name}")
        else:
            ensure_project_revision(source, revision)


@contextmanager
def _publish_guard(path: Path):
    # Hold a kernel-released guard only for the final destination check and install.
    if os.name == "nt":
        marker = path.with_name(f".{path.name}.saving")
        try:
            fd = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_TEMPORARY, 0o600)
        except FileExistsError:
            raise ValueError("Another save is finishing. Retry Save project or use Save as… with a new filename.") from None
    else:
        import fcntl
        # The directory stays at the same inode when the project is atomically replaced.
        fd = os.open(path.parent, os.O_RDONLY)
    try:
        if os.name != "nt":
            fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def save_project(path: Path, state: ProjectState, *, new_only: bool = False,
                 expected_revision: bytes | None = None, input_revisions: tuple[tuple[Path, bytes], ...] = (),
                 transcript_inputs: tuple[Path, ...] = ()) -> bytes:
    path = Path(path)
    case_master = state.needs_case_master
    nullable = state.needs_nullable_profile
    staged_paths = {v.path for v in state.collection_sources()} if state.pending_visits else set()
    staged_paths.update(transcript_inputs)
    previous = None
    if not new_only:
        try:
            with path.open("rb") as stream:
                previous = _revision(stream)
                if expected_revision is not None and previous != expected_revision:
                    raise ProjectChanged()
                with zipfile.ZipFile(stream) as existing:
                    old_member = _data_member(existing)
                    old_payload = json.loads(existing.read(old_member))
                    old_case, old_nullable = _project_profile(old_member, old_payload)
                    old_pending = _pending_from_json(old_payload)
                if state.pending_visits != old_pending:
                    if state.pending_visits:
                        raise ProjectCopyRequired("Save the appended visits in a new project file.",
                                                  suffix="-visits", title="Save a new staged project")
                    paths = tuple(Path(p) for p in old_payload["transcript_paths"]) + tuple(v.path for v in old_pending)
                    promoted = tuple(TranscriptDocument(v.name, v.text) for v in old_pending)
                    if (state.transcript_paths != paths or len(state.documents) != len(paths)
                            or state.documents[-len(promoted):] != promoted or state.result is None
                            or state.result.document_count != len(paths)
                            or not any(e.action == "analysis_run"
                                       for e in state.audit_events[len(old_payload.get("audit_events", [])):])):
                        raise ValueError("Pending visits must be retained until included in an explicit analysis.")
                if old_case and expected_revision is None:
                    if state.file_revision is None:
                        raise ProjectCopyRequired("That filename belongs to a case master. Choose a new filename for this study.",
                                                  suffix="-copy", title="Save this study to a new file")
                    if state.file_revision != previous:
                        raise ProjectChanged()
                if case_master and not old_case:
                    raise ProjectCopyRequired("Older project unchanged. Use Save project to create a separate case master.",
                                              suffix="-case-master", title="Save a separate case master (original stays unchanged)")
                if nullable and not old_nullable and not old_case:
                    raise ProjectCopyRequired("Older project unchanged. Use Save project to create a separate nullable-score copy.")
                case_master = case_master or old_case
                nullable = nullable or old_nullable
        except FileNotFoundError:
            if expected_revision is not None:
                raise ProjectChanged() from None
            new_only = case_master or nullable
        except zipfile.BadZipFile as exc:
            raise ValueError("The existing project is unreadable; choose a new file instead.") from exc
    if state.pending_visits:
        member = CASE_MEMBER
        format_version = STAGED_NULLABLE_PROFILE if nullable else STAGED_PROFILE
    elif case_master:
        member = CASE_MEMBER
        format_version = CASE_NULLABLE_PROFILE if nullable else CASE_PROFILE
    else:
        member = NULLABLE_MEMBER if nullable else PROJECT_JSON
        format_version = NULLABLE_PROFILE if nullable else PROJECT_FORMAT_VERSION
    if new_only and os.path.lexists(path):
        raise FileExistsError(path)
    if transcript_inputs:
        # Withdrawal may have been left open in the save picker.
        _check_input_revisions(input_revisions, staged_paths)
    # Serialize before touching disk, then replace only a complete sibling archive.
    serialized = json.dumps(_project_payload(state, format_version), ensure_ascii=False, indent=2)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w+b", prefix=f".{path.name}.", suffix=".tmp", dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr(member, serialized)
            stream.flush()
            os.fsync(stream.fileno())
            written = _revision(stream)
        with _publish_guard(path):
            _check_input_revisions(input_revisions, staged_paths)
            if not new_only:
                if file_revision(path) != previous:
                    raise ProjectChanged()
                os.replace(temporary, path)
            elif os.name == "nt":
                os.rename(temporary, path)  # Windows fails atomically if the destination already exists.
            else:
                os.link(temporary, path)  # POSIX rename would replace an existing file.
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return written


def _project_payload(state: ProjectState, format_version: int | str) -> dict:
    payload = {
        "format_version": format_version,
        "transcript_paths": [str(item) for item in state.transcript_paths],
        "codebook_path": str(state.codebook_path) if state.codebook_path is not None else None,
        "documents": [_document_to_json(document) for document in state.documents],
        "settings": _settings_to_json(state.settings),
        "result": _result_to_json(state.result),
        "researcher_name": state.researcher_name,
        "audit_events": [asdict(event) for event in state.audit_events],
    }
    if state.pending_visits:
        payload["pending_visits"] = [{"path": str(v.path), "text": v.text} for v in state.pending_visits]
    return payload


def _unique_fields(pairs: list[tuple]) -> dict:
    fields = dict(pairs)
    if len(fields) != len(pairs):
        raise ValueError("This project contains duplicate metadata fields. Keep the original project.")
    return fields


def _preserves_json_values(original, copy) -> bool:
    """Allow newly explicit defaults, but preserve each recorded JSON value and its type."""
    if isinstance(original, dict):
        return isinstance(copy, dict) and all(
            key in copy and _preserves_json_values(value, copy[key]) for key, value in original.items()
        )
    if isinstance(original, list):
        return isinstance(copy, list) and len(original) == len(copy) and all(
            _preserves_json_values(a, b) for a, b in zip(original, copy)
        )
    # JSON numbers may load as int or float. True and 1 are different JSON values.
    return isinstance(original, bool) == isinstance(copy, bool) and original == copy


def load_project(path: Path, *, expected_revision: bytes | None = None,
                 require_lossless_staged_copy: bool = False) -> ProjectState:
    try:
        stream = Path(path).open("rb")
    except FileNotFoundError:
        if expected_revision is not None:
            raise ProjectChanged() from None
        raise
    with stream:
        if expected_revision is not None and _revision(stream) != expected_revision:
            raise ProjectChanged()
        with zipfile.ZipFile(stream) as archive:
            member = _data_member(archive)
            if require_lossless_staged_copy and (len(archive.infolist()) != 1 or archive.comment
                                                  or archive.getinfo(member).comment or archive.getinfo(member).extra):
                raise ValueError("This project has additional archive content that cannot be preserved in a staged copy.")
            payload = json.loads(archive.read(member).decode("utf-8"),
                                 object_pairs_hook=_unique_fields if require_lossless_staged_copy else None)
        revision = _revision(stream)
        if expected_revision is not None and revision != expected_revision:
            raise ProjectChanged()

    case_master, nullable = _project_profile(member, payload)
    state = ProjectState(
        transcript_paths=tuple(Path(item) for item in payload.get("transcript_paths", [])),
        codebook_path=_optional_path(payload.get("codebook_path")),
        documents=tuple(_document_from_json(item) for item in payload.get("documents", []) if isinstance(item, dict)),
        settings=_settings_from_json(payload.get("settings", {})),
        result=_result_from_json(payload.get("result"), nullable=nullable),
        researcher_name=str(payload.get("researcher_name", "")),
        audit_events=tuple(_audit_event_from_json(item) for item in payload.get("audit_events", []) if isinstance(item, dict)),
        file_revision=revision,
        is_case_master=case_master,
        pending_visits=_pending_from_json(payload),
    )
    if state.pending_visits:
        state.collection_sources()
        if require_lossless_staged_copy:
            profile = STAGED_NULLABLE_PROFILE if state.needs_nullable_profile else STAGED_PROFILE
            if not _preserves_json_values(payload, _project_payload(state, profile)):
                raise ValueError("This project contains metadata that cannot be preserved in a staged copy.")
    return state


def _pending_from_json(payload: dict) -> tuple[PendingVisit, ...]:
    if payload.get("format_version") not in (STAGED_PROFILE, STAGED_NULLABLE_PROFILE):
        return ()
    rows = payload.get("pending_visits")
    if not isinstance(rows, list) or not rows:
        raise ValueError("The staged case master has no pending visits.")
    documents = payload.get("documents")
    paths = payload.get("transcript_paths")
    if (not isinstance(documents, list) or not isinstance(paths, list) or len(paths) != len(documents)
            or not all(isinstance(p, str) for p in paths)
            or not all(isinstance(d, dict) and isinstance(d.get("name"), str)
                       and isinstance(d.get("text"), str) for d in documents)):
        raise ValueError("Expected a source path, name and transcript for each earlier visit.")
    visits = []
    for row in rows:
        if (not isinstance(row, dict) or set(row) != {"path", "text"}
                or not isinstance(row["path"], str) or not isinstance(row["text"], str)):
            raise ValueError("Invalid pending visit. Expected an unassigned source path and transcript text.")
        visits.append(PendingVisit(Path(row["path"]), row["text"]))
    return tuple(visits)


def _optional_path(value: str | None) -> Path | None:
    if not value:
        return None
    return Path(value)


def _settings_to_json(settings: AnalysisSettings) -> dict[str, object]:  # noqa: OBJECT_OK - JSON boundary.
    payload = {
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
    if settings.speaker_overrides:
        payload["speaker_overrides"] = [asdict(rule) for rule in settings.speaker_overrides]
    return payload


def _speaker_overrides_from_json(rows: object) -> tuple[SpeakerOverride, ...]:  # noqa: OBJECT_OK - JSON boundary.
    if not isinstance(rows, list):
        raise ValueError("Expected a list of speaker selections.")
    rules = []
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("included"), bool):
            raise ValueError("Invalid include/exclude decision in speaker selections.")
        source, speaker = row.get("source_name"), row.get("speaker")
        if not isinstance(source, str) or not source or not isinstance(speaker, str) or not speaker:
            raise ValueError("A speaker selection needs a transcript and speaker label.")
        if (source, speaker) in seen:
            raise ValueError("Duplicate speaker selection for one transcript.")
        seen.add((source, speaker))
        rules.append(SpeakerOverride(source, speaker, row["included"]))
    return tuple(sorted(rules))


def _settings_from_json(payload: dict[str, ValidationValue | list[dict[str, str | list[str] | bool]]]) -> AnalysisSettings:
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
        speaker_overrides=_speaker_overrides_from_json(payload.get("speaker_overrides", [])),
        codebook_entries=tuple(_codebook_entry_from_json(item) for item in entries if isinstance(item, dict)),
    )


def _codebook_entry_from_json(payload: dict[str, str | list[str]]) -> CodebookEntry:
    examples = payload.get("examples", [])
    return CodebookEntry(
        name=str(payload.get("name", "")),
        description=str(payload.get("description", "")),
        examples=tuple(str(item) for item in examples) if isinstance(examples, list) else (),
    )


def _document_to_json(document: TranscriptDocument) -> dict[str, str]:
    row = {"name": document.name, "text": document.text, "memo": document.memo}
    if label := document.case_label.strip():
        row["case_label"] = label
    return row


def _document_from_json(payload: dict[str, object]) -> TranscriptDocument:  # noqa: OBJECT_OK - JSON boundary.
    label = payload.get("case_label", "")
    if not isinstance(label, str):
        raise ValueError("Expected a text case label on the transcript.")
    return TranscriptDocument(
        name=str(payload.get("name", "")),
        text=str(payload.get("text", "")),
        memo=str(payload.get("memo", "")),
        case_label=label.strip(),
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

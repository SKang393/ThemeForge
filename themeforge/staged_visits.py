"""Change pending transcript snapshots while keeping the previous review pass intact."""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
import stat
from xml.etree.ElementTree import ParseError
import zipfile

from .io import load_transcript_text
from .project_io import PendingVisit, ProjectChanged, ProjectState, file_revision, load_project


def _read_visit(path: Path) -> tuple[str, bytes, tuple[int, int]]:
    if not path.is_absolute() or path.suffix.lower() not in {".txt", ".md", ".csv", ".docx", ".rtf", ".pdf"}:
        raise ValueError(f"Expected an original transcript path in a supported format: {path.name}")
    try:
        attributes = path.stat()
        if not stat.S_ISREG(attributes.st_mode):
            raise ValueError(f"Expected a transcript file: {path.name}")
        revision = file_revision(path)
        if path.suffix.lower() != ".docx" and zipfile.is_zipfile(path):
            raise ValueError(f"Expected a transcript, but {path.name} is an archive.")
        text = load_transcript_text(path)
        if revision is None or file_revision(path) != revision:
            raise ValueError(f"The transcript changed while it was being read: {path.name}")
    except (OSError, KeyError, ParseError, zipfile.BadZipFile) as exc:
        raise ValueError(f"Could not verify transcript {path.name}: {exc}") from exc
    return text, revision, (attributes.st_dev, attributes.st_ino)


def prepare_staged_visits(master: ProjectState, paths: Sequence[Path]) -> tuple[
    ProjectState, tuple[tuple[Path, bytes], ...]
]:
    """Prepare a new project and the input revisions to check when publishing it."""
    if not master.is_case_master or master.file_revision is None:
        raise ValueError("Open a saved protected case master before appending a visit.")
    if not paths:
        raise ValueError("Select at least one new transcript.")
    earlier = master.collection_sources()
    revisions = []
    identities = set()
    additions = []
    for index, path in enumerate([v.path for v in earlier] + list(paths)):
        text, revision, identity = _read_visit(path)
        if identity in identities:
            raise ValueError(f"The same source path or file is present more than once: {path.name}")
        identities.add(identity)
        if index < len(earlier):
            if text != earlier[index].text:
                raise ValueError(f"The original file differs from the saved transcript: {path.name}")
        else:
            additions.append(PendingVisit(path, text))
        revisions.append((path, revision))
    staged = replace(master, pending_visits=master.pending_visits + tuple(additions), file_revision=None)
    staged.collection_sources()
    return staged, tuple(revisions)


def prepare_pending_withdrawal(project_path: Path, expected_revision: bytes, selected: PendingVisit) -> tuple[
    ProjectState, tuple[tuple[Path, bytes], ...]
]:
    """Prepare a separate staged master using the exact pending snapshot displayed to the researcher."""
    if expected_revision is None:
        raise ProjectChanged()
    master = load_project(project_path, expected_revision=expected_revision, require_lossless_staged_copy=True)
    if not master.is_case_master or not master.pending_visits:
        raise ValueError("Open a saved staged case master with pending visits.")
    if selected not in master.pending_visits:
        raise ValueError("The selected visit differs from the saved pending snapshot. Reopen the staged project.")
    if len(master.pending_visits) == 1:
        raise ValueError("This is the last pending visit. Keep it, or reopen the reviewed project from before staging.")
    text, revision, _ = _read_visit(selected.path)
    if text != selected.text:
        raise ValueError(f"The original file differs from the saved transcript: {selected.name}")
    remaining = tuple(visit for visit in master.pending_visits if visit != selected)
    prepared = replace(master, pending_visits=remaining, file_revision=None)
    return prepared, ((project_path, expected_revision), (selected.path, revision))

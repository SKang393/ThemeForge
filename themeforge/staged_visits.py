"""Append unassigned transcript snapshots while keeping the previous review pass intact."""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
import stat
from xml.etree.ElementTree import ParseError
import zipfile

from .io import load_transcript_text
from .project_io import PendingVisit, ProjectState, file_revision


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
        if not path.is_absolute() or path.suffix.lower() not in {".txt", ".md", ".csv", ".docx", ".rtf", ".pdf"}:
            raise ValueError(f"Expected an original transcript path in a supported format: {path.name}")
        try:
            attributes = path.stat()
            if not stat.S_ISREG(attributes.st_mode):
                raise ValueError(f"Expected a transcript file: {path.name}")
            identity = (attributes.st_dev, attributes.st_ino)
            if identity in identities:
                raise ValueError(f"The same source path or file is present more than once: {path.name}")
            identities.add(identity)
            revision = file_revision(path)
            if path.suffix.lower() != ".docx" and zipfile.is_zipfile(path):
                raise ValueError(f"Expected a transcript, but {path.name} is an archive.")
            text = load_transcript_text(path)
            if revision is None or file_revision(path) != revision:
                raise ValueError(f"The transcript changed while it was being read: {path.name}")
        except (OSError, KeyError, ParseError, zipfile.BadZipFile) as exc:
            raise ValueError(f"Could not verify transcript {path.name}: {exc}") from exc
        if index < len(earlier):
            if text != earlier[index].text:
                raise ValueError(f"The original file differs from the saved transcript: {path.name}")
        else:
            additions.append(PendingVisit(path, text))
        revisions.append((path, revision))
    staged = replace(master, pending_visits=master.pending_visits + tuple(additions), file_revision=None)
    staged.collection_sources()
    return staged, tuple(revisions)

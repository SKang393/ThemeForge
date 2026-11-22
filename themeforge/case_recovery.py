"""Make a legacy working copy or restore explicitly selected case assignments."""
from __future__ import annotations

from collections import Counter
from dataclasses import replace

from .match_reviews import MatchReviews, match_id, match_snapshot, recorded_review
from .project_io import ProjectState
from .source_location import TranscriptIndex


def _without_cases(master: ProjectState) -> ProjectState:
    if master.pending_visits:
        raise ValueError("Older versions cannot retain pending visits. Use the original protected master for recovery.")
    names = {d.name for d in master.documents}
    if len(names) != len(master.documents) or any(not name.strip() for name in names):
        raise ValueError("Each transcript needs a unique, nonempty name to match a returned copy.")
    documents = tuple(replace(d, case_label="") for d in master.documents)
    return replace(master, documents=documents, is_case_master=False, file_revision=None)


def older_working_copy(master: ProjectState) -> ProjectState:
    if not master.is_case_master:
        raise ValueError("Save a protected grouped project before making an older-version copy.")
    return _without_cases(master)


def recovered_cases(returned: ProjectState, master: ProjectState) -> ProjectState:
    """Validate a selected master's study and original history, then keep the returned reader's work."""
    if returned.is_case_master or any(d.case_label for d in returned.documents):
        raise ValueError("Open the ungrouped, older-version copy to recover its case assignments.")
    if not any(d.case_label for d in master.documents):
        raise ValueError("The selected project has no case assignments to recover.")
    original = _without_cases(master)
    previous = len(original.audit_events)
    if returned.audit_events[:previous] != original.audit_events:
        raise ValueError("The older copy does not retain the selected master’s original audit history.")
    # The reader may have changed the active coder and appended decisions on the same stored passages.
    if replace(returned, audit_events=original.audit_events, researcher_name=original.researcher_name) != original:
        raise ValueError("The older copy’s sources, memos, settings or analysis differ from the selected master.")
    added = returned.audit_events[previous:]
    if added:
        sources = TranscriptIndex(returned.documents)
        reviews = MatchReviews((), codebook_entries=returned.settings.codebook_entries)
        targets = Counter(
            match_id(match_snapshot(theme, quote, sources))
            for theme in (returned.result.themes if returned.result else ())
            if reviews.definition_is_current(theme)
            for quote in theme.quotes if sources.locate(quote).status == "exact_span"
        )
        for event in added:
            if targets[event.target_id] != 1 or not event.actor.strip() or recorded_review(event) is None:
                raise ValueError("The older copy has later changes beyond verified reviews of the stored passages.")
    documents = tuple(replace(doc, case_label=source.case_label)
                      for doc, source in zip(returned.documents, master.documents, strict=True))
    return replace(returned, documents=documents, is_case_master=True, file_revision=None)

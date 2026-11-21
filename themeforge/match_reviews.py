"""Review notes describe one recorded match, never a code assignment or a negative code."""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import re

from .analysis_types import CodebookEntry, Theme, ThemeQuote
from .audit import AuditEvent
from .source_location import TranscriptIndex


REVIEW_ACTION = "match_review_note"
REVIEW_LABELS = {"supported": "Supports this theme", "deferred": "Revisit later", "declined": "Not this match"}
DISPOSITIONS = set(REVIEW_LABELS)
NO_CURRENT_REVIEW = "No current match review."
HISTORICAL_DEFINITION = ("Historical codebook match. Its original entry cannot be verified in the loaded codebook. "
                         "See Audit log for earlier decisions. Run Analyze to review current suggestions.")
REVIEW_SCOPE = ("Each review records one reader's judgment about one theme/passage match. "
                "A codebook suggestion needs its original entry in the study's codebook for a current review. "
                "Theme edit labels apply to the whole group. Coding assignments and relevance scores are separate. "
                "A missing note means there is no current review on record for this pairing.")


@dataclass(frozen=True, slots=True)
class ReviewNote:
    disposition: str
    actor: str
    reason: str
    timestamp_utc: str
    audit_target_id: str = ""
    reviewed_theme_name: str = ""

    def describe(self, theme_name: str = "") -> str:
        label = f" (reviewed as {self.reviewed_theme_name!r})" if theme_name and self.reviewed_theme_name != theme_name else ""
        return f"Latest match review: {self.disposition} by {self.actor}{label} ({self.timestamp_utc}): {self.reason}"


def match_snapshot(theme: Theme, quote: ThemeQuote, sources: TranscriptIndex) -> dict:
    row = asdict(quote)
    del row["memo"]  # A passage memo is not part of the proposed relationship.
    if quote.relevance is not None:
        row["relevance"] = float(quote.relevance)
    snapshot = {"theme_id": theme.id, "theme_name": theme.name, "keywords": theme.keywords,
            "parent_theme_id": theme.parent_theme_id, "definition": theme.validation.get("manual_codebook_definition"),
            "quote": row, "source_sha256": sources.digest(quote.source_name)}
    # Do not invent an originating definition or change the identity of a legacy match.
    if "generated_from_codebook_entry" in theme.validation:
        snapshot["generated_from_codebook_entry"] = theme.validation["generated_from_codebook_entry"]
    return snapshot


def match_id(snapshot: dict) -> str:
    encoded = json.dumps(snapshot, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return sha256(encoded).hexdigest()


def review_key(snapshot: dict, target_id: str = "") -> str:
    # Only word spacing in a display label can differ. The original codebook
    # entry (including its own name), source, score and all other fields still match.
    if "generated_from_codebook_entry" in snapshot and isinstance(snapshot.get("theme_name"), str):
        display_name = snapshot["theme_name"]
        normalized = " ".join(display_name.split())
        if normalized != display_name:
            return match_id({**snapshot, "theme_name": normalized})
    return target_id or match_id(snapshot)


def recorded_review(event: AuditEvent) -> tuple[dict, ReviewNote] | None:
    """Read a match decision only when its recorded snapshot matches the audit target."""
    if event.action != REVIEW_ACTION or event.target_type != "theme_quote_match":
        return None
    try:
        details = json.loads(event.details)
        snapshot = details["match"]
        status, reason = details["disposition"], details["reason"]
        if (isinstance(snapshot, dict) and isinstance(snapshot.get("theme_name"), str)
                and status in DISPOSITIONS and isinstance(reason, str) and reason.strip()
                and (status != "supported" or event.actor.strip()) and event.target_id == match_id(snapshot)):
            return snapshot, ReviewNote(status, event.actor, reason, event.timestamp_utc,
                                        event.target_id, snapshot["theme_name"])
    except (TypeError, ValueError, KeyError):
        pass  # Unrecognized audit data remains in the full project history.
    return None


class MatchReviews:
    """Only the latest note on the same saved match after the latest Analyze is current."""

    def __init__(self, events: Iterable[AuditEvent] = (), *, reviewer: str | None = None,
                 codebook_entries: Iterable[CodebookEntry] = ()):
        self._reviewer = reviewer
        self.set_codebook(codebook_entries)
        self._notes: dict[tuple[str, str | None], ReviewNote] = {}
        self._current_targets: dict[tuple[str, str | None], str] = {}
        for event in events:
            self.observe(event)

    @property
    def has_reviews_since_analysis(self) -> bool:
        return bool(self._notes)

    def set_codebook(self, entries: Iterable[CodebookEntry]) -> None:
        self._definitions = {entry.snapshot(): entry for entry in entries}

    def definition_is_current(self, theme: Theme) -> bool:
        if "generated_from_codebook_entry" in theme.validation:
            return self.codebook_entry(theme) is not None
        # The generator reserves C01, C02, ... for codebook suggestions. Edited
        # groups can lose their generation status or gain a suffix on rerun.
        # The ID identifies their origin but cannot recover a missing definition.
        return re.fullmatch(r"C[0-9]+(?:-r[0-9]+)?", theme.id) is None

    def codebook_entry(self, theme: Theme) -> CodebookEntry | None:
        origin = theme.validation.get("generated_from_codebook_entry")
        return self._definitions.get(origin) if isinstance(origin, str) else None

    def observe(self, event: AuditEvent) -> None:
        if event.action == "analysis_run":
            self._notes.clear()
            self._current_targets.clear()
        elif self._reviewer is None or event.actor == self._reviewer:
            recorded = recorded_review(event)
            if recorded is not None:
                snapshot, note = recorded
                key = review_key(snapshot, event.target_id)
                for reader in (event.actor, None):  # None is the latest note across readers.
                    self._notes[event.target_id, reader] = note
                    self._current_targets[key, reader] = event.target_id

    def latest(self, theme: Theme, quote: ThemeQuote, sources: TranscriptIndex,
               *, reviewer: str | None = None) -> ReviewNote | None:
        if not self._notes or not self.definition_is_current(theme) or not sources.has_unique_source(quote.source_name):
            return None
        return self.by_key(review_key(match_snapshot(theme, quote, sources)), reviewer=reviewer)

    def by_key(self, key: str, *, reviewer: str | None = None) -> ReviewNote | None:
        target = self._current_targets.get((key, reviewer))
        return self.by_target(target, reviewer=reviewer) if target is not None else None

    def by_target(self, target_id: str, *, reviewer: str | None = None) -> ReviewNote | None:
        return self._notes.get((target_id, reviewer))

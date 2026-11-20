"""Review notes describe one recorded match, never a code assignment or a negative code."""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass
from hashlib import sha256
import json

from .analysis_types import Theme, ThemeQuote
from .audit import AuditEvent
from .source_location import TranscriptIndex


REVIEW_ACTION = "match_review_note"
REVIEW_LABELS = {"supported": "Supports this theme", "deferred": "Revisit later", "declined": "Not this match"}
DISPOSITIONS = set(REVIEW_LABELS)
NO_CURRENT_REVIEW = "No current match review."
REVIEW_SCOPE = ("Each review records one reader's judgment about one theme/passage match. "
                "Theme edit labels apply to the whole group. Coding assignments and relevance scores are separate. "
                "A missing note means there is no current review on record for this pairing.")


@dataclass(frozen=True, slots=True)
class ReviewNote:
    disposition: str
    actor: str
    reason: str
    timestamp_utc: str
    audit_target_id: str = ""

    def describe(self) -> str:
        return f"Latest match review: {self.disposition} by {self.actor} ({self.timestamp_utc}): {self.reason}"


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


class MatchReviews:
    """Only the latest note on the same saved match after the latest Analyze is current."""

    def __init__(self, events: Iterable[AuditEvent] = (), *, reviewer: str | None = None):
        self._reviewer = reviewer
        self._notes: dict[tuple[str, str | None], ReviewNote] = {}
        for event in events:
            self.observe(event)

    def observe(self, event: AuditEvent) -> None:
        if event.action == "analysis_run":
            self._notes.clear()
        elif event.action == REVIEW_ACTION and event.target_type == "theme_quote_match":
            if self._reviewer is not None and event.actor != self._reviewer:
                return
            try:
                details = json.loads(event.details)
                status, reason = details["disposition"], details["reason"]
                if status == "supported" and not event.actor.strip():
                    return
                if (status in DISPOSITIONS and isinstance(reason, str) and reason.strip()
                        and event.target_id == match_id(details["match"])):
                    note = ReviewNote(status, event.actor, reason, event.timestamp_utc, event.target_id)
                    self._notes[event.target_id, event.actor] = note
                    self._notes[event.target_id, None] = note  # Latest across readers for the shared report.
            except (TypeError, ValueError, KeyError):
                pass  # Unrecognized audit data remains historical; do not attach it to a row.

    def latest(self, theme: Theme, quote: ThemeQuote, sources: TranscriptIndex,
               *, reviewer: str | None = None) -> ReviewNote | None:
        if not self._notes or not sources.has_unique_source(quote.source_name):
            return None
        return self.by_target(match_id(match_snapshot(theme, quote, sources)), reviewer=reviewer)

    def by_target(self, target_id: str, *, reviewer: str | None = None) -> ReviewNote | None:
        return self._notes.get((target_id, reviewer))

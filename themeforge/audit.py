from __future__ import annotations

import csv
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
import io
from typing import Final

AUDIT_CSV_COLUMNS: Final = ("timestamp_utc", "actor", "action", "target_type", "target_id", "details")
DEFAULT_ACTOR: Final = "Researcher"


def neutralize_csv_cell(value: str) -> str:
    return f"'{value}" if value.startswith(("=", "+", "-", "@")) else value


@dataclass(frozen=True, slots=True)
class AuditEvent:
    timestamp_utc: str
    actor: str
    action: str
    target_type: str
    target_id: str
    details: str


def create_audit_event(
    *,
    actor: str,
    action: str,
    target_type: str,
    target_id: str,
    details: str,
    now: datetime | None = None,
    timestamp: str | None = None,
    timestamp_utc: str | None = None,
) -> AuditEvent:
    return AuditEvent(
        timestamp_utc=_timestamp_value(now=now, timestamp=timestamp, timestamp_utc=timestamp_utc),
        actor=_actor_or_default(actor),
        action=action,
        target_type=target_type,
        target_id=target_id,
        details=details,
    )


def export_audit_events_csv(events: Iterable[AuditEvent]) -> str:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=AUDIT_CSV_COLUMNS)
    writer.writeheader()
    for event in events:
        writer.writerow(
            {
                "timestamp_utc": neutralize_csv_cell(event.timestamp_utc),
                "actor": neutralize_csv_cell(event.actor),
                "action": neutralize_csv_cell(event.action),
                "target_type": neutralize_csv_cell(event.target_type),
                "target_id": neutralize_csv_cell(event.target_id),
                "details": neutralize_csv_cell(event.details),
            }
        )
    return output.getvalue()


def _timestamp_value(*, now: datetime | None, timestamp: str | None, timestamp_utc: str | None) -> str:
    if timestamp_utc is not None:
        return timestamp_utc
    if timestamp is not None:
        return timestamp
    return _timestamp_utc(now)


def _timestamp_utc(now: datetime | None) -> str:
    source = datetime.now(UTC) if now is None else now
    return source.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _actor_or_default(actor: str) -> str:
    normalized = actor.strip()
    return normalized if normalized else DEFAULT_ACTOR

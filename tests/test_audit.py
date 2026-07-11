from datetime import UTC, datetime
import csv
import io
import unittest

from themeforge.audit import AuditEvent, create_audit_event, export_audit_events_csv


class AuditTests(unittest.TestCase):
    def test_create_audit_event_uses_injected_datetime_and_default_actor(self):
        event = create_audit_event(
            actor="   ",
            action="code_applied",
            target_type="quote",
            target_id="Q1",
            details="Accepted by reviewer",
            now=datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC),
        )

        self.assertEqual(
            event,
            AuditEvent(
                timestamp_utc="2026-01-02T03:04:05Z",
                actor="Researcher",
                action="code_applied",
                target_type="quote",
                target_id="Q1",
                details="Accepted by reviewer",
            ),
        )

    def test_export_audit_events_csv_escapes_commas_quotes_and_newlines(self):
        event = AuditEvent(
            timestamp_utc="2026-01-02T03:04:05Z",
            actor="이수진",
            action="memo_updated",
            target_type="theme",
            target_id="T1",
            details='Line 1, with comma\nLine 2 "quoted"',
        )

        exported = export_audit_events_csv((event,))

        rows = list(csv.DictReader(io.StringIO(exported)))
        self.assertEqual(
            rows,
            [
                {
                    "timestamp_utc": "2026-01-02T03:04:05Z",
                    "actor": "이수진",
                    "action": "memo_updated",
                    "target_type": "theme",
                    "target_id": "T1",
                    "details": 'Line 1, with comma\nLine 2 "quoted"',
                }
            ],
        )
        self.assertEqual(
            exported.splitlines()[0],
            "timestamp_utc,actor,action,target_type,target_id,details",
        )

    def test_create_audit_event_accepts_injected_timestamp(self):
        event = create_audit_event(
            actor="Reviewer",
            action="memo_updated",
            target_type="theme",
            target_id="T1",
            details="Manual timestamp",
            timestamp="2026-02-03T04:05:06Z",
        )

        self.assertEqual(event.timestamp_utc, "2026-02-03T04:05:06Z")

    def test_export_audit_events_csv_neutralizes_each_formula_prefix_in_every_field(self) -> None:
        # Given: every audit field starts with each spreadsheet formula marker.
        events = tuple(
            AuditEvent(
                timestamp_utc=f"{prefix}timestamp",
                actor=f"{prefix}actor",
                action=f"{prefix}action",
                target_type=f"{prefix}target type",
                target_id=f"{prefix}target id",
                details=f"{prefix}details",
            )
            for prefix in ("=", "+", "-", "@")
        )

        # When: the events are exported and parsed as CSV.
        reader = csv.DictReader(io.StringIO(export_audit_events_csv(events)))
        rows = list(reader)

        # Then: every marker in every field is preserved behind an apostrophe.
        self.assertEqual(
            reader.fieldnames,
            ["timestamp_utc", "actor", "action", "target_type", "target_id", "details"],
        )
        self.assertEqual(
            rows,
            [
                {
                    "timestamp_utc": f"'{prefix}timestamp",
                    "actor": f"'{prefix}actor",
                    "action": f"'{prefix}action",
                    "target_type": f"'{prefix}target type",
                    "target_id": f"'{prefix}target id",
                    "details": f"'{prefix}details",
                }
                for prefix in ("=", "+", "-", "@")
            ],
        )


if __name__ == "__main__":
    unittest.main()

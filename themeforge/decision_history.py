"""Selected historical judgments from one reader and one verified source."""
from collections.abc import Iterable
import csv
import io
from typing import NamedTuple

from .analysis_types import TranscriptDocument, TranscriptSegment
from .audit import AuditEvent, neutralize_csv_cell
from .match_reviews import recorded_review
from .source_location import TranscriptIndex


class DecisionRow(NamedTuple):
    recorded_theme: str
    source_line: int
    source_start: int
    source_end: int
    reviewed_excerpt: str
    recorded_decision: str
    recorded_reason: str


def decision_rows(documents: Iterable[TranscriptDocument], events: Iterable[AuditEvent],
                  source: str, reviewer: str) -> list[DecisionRow]:
    index = TranscriptIndex(d for d in documents if d.name == source)
    digest = index.digest(source)
    if not source.strip() or not digest or not reviewer.strip():
        return []
    rows = []
    for event in events:
        if event.actor != reviewer:
            continue
        recorded = recorded_review(event)
        if recorded is None:
            continue
        snapshot, note = recorded
        quote = snapshot.get("quote")
        if (snapshot.get("source_sha256") != digest or not isinstance(quote, dict)
                or quote.get("source_name") != source or not isinstance(quote.get("text"), str)
                or any(type(quote.get(k)) is not int for k in ("source_line", "source_start", "source_end"))):
            continue
        turn = TranscriptSegment("", quote["text"], quote["source_line"], source,
                                 quote["source_start"], quote["source_end"])
        if index.locate(turn).status == "exact_span":
            rows.append(DecisionRow(note.reviewed_theme_name, turn.source_line, turn.source_start, turn.source_end,
                                    turn.text, note.disposition, note.reason))
    return rows


def decision_history_csv(selected: Iterable[DecisionRow]) -> str:
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(DecisionRow._fields)
    for row in selected:
        writer.writerow(neutralize_csv_cell(value) if isinstance(value, str) else value for value in row)
    return output.getvalue()

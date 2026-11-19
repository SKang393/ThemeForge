"""Report and compare original, positive manual codebook records. Never infer negative decisions."""
from collections import defaultdict
import csv
from dataclasses import dataclass
from hashlib import sha256
import io
import json

from .analysis_types import CodebookEntry, ThemeQuote
from .audit import AuditEvent, neutralize_csv_cell
from .manual_editing import manual_assignment_details, manual_assignment_rationale
from .rigor import CodedProject

SCOPE = ("Exact recorded manual definition and source span only. An absent record is not a negative decision. "
         "Review completeness and coder independence are not established; no agreement statistic.")
RecordKey = tuple[str, int, int, str]  # Source, start, end, canonical definition snapshot.


@dataclass(frozen=True)
class RecordedComparison:
    csv_text: str
    both: int
    current_only: int
    comparison_only: int


@dataclass(frozen=True)
class _Assignment:
    theme_id: str
    entry: CodebookEntry
    quote: ThemeQuote
    event: AuditEvent


def _sources(project: CodedProject) -> dict[str, str]:
    sources = {d.name: d.text for d in project.documents}
    if not sources or len(sources) != len(project.documents):
        raise ValueError("Each project needs a nonempty, unambiguous set of named sources.")
    return sources


def _definition(snapshot: object) -> tuple[CodebookEntry, str]:
    if not isinstance(snapshot, str):
        raise ValueError("A manual assignment has no original definition snapshot in its current group.")
    raw = json.loads(snapshot)
    if not (isinstance(raw, dict) and set(raw) == {"name", "description", "examples"}
            and isinstance(raw["name"], str) and raw["name"].strip() and isinstance(raw["description"], str)
            and isinstance(raw["examples"], list) and all(isinstance(v, str) for v in raw["examples"])):
        raise ValueError("The recorded definition is incomplete or unsupported.")
    entry = CodebookEntry(raw["name"], raw["description"], tuple(raw["examples"]))
    return entry, json.dumps(raw, ensure_ascii=False, sort_keys=True)


def _verified_assignments(project: CodedProject, sources: dict[str, str]) -> tuple[dict[RecordKey, _Assignment], set[str], int]:
    events = defaultdict(list)
    for event in project.audit_events:
        if event.action == "codebook_passage_assigned" and event.target_type == "quote":
            events[event.target_id].append(event)
    records = {}
    current_ids = set()
    skipped = 0
    for theme in project.result.themes:
        assigned = [q for q in theme.quotes if q.manual_definition_assignment]
        skipped += len(theme.quotes) - len(assigned)
        if not assigned:
            continue
        entry, snapshot = _definition(theme.validation.get("manual_codebook_definition"))
        if entry.name != theme.name:
            raise ValueError("A manual assignment's current group differs from its recorded definition.")
        for q in assigned:
            source, start, end = q.source_name, q.source_start, q.source_end
            text = sources.get(source)
            if (text is None or not 0 <= start < end <= len(text) or text[start:end] != q.text
                    or text.count("\n", 0, start) + 1 != q.source_line):
                raise ValueError("A manual source anchor does not match the saved transcript.")
            matches = events.get(q.quote_id, ())
            if len(matches) != 1 or not matches[0].actor.strip():
                raise ValueError("A manual assignment lacks one unambiguous, attributed creation audit event.")
            event = matches[0]
            if (event.details != manual_assignment_details(entry, source, start, end, theme.id)
                    or q.rationale != manual_assignment_rationale(entry, source, start, end, event.actor)):
                raise ValueError("An assignment changed or no longer matches its original attributed record.")
            key = source, start, end, snapshot
            if key in records or q.quote_id in current_ids:
                raise ValueError("Duplicate manual records make the assignment identities ambiguous.")
            current_ids.add(q.quote_id)
            records[key] = _Assignment(theme.id, entry, q, event)
    return records, set(events), skipped


def _records(project: CodedProject, sources: dict[str, str]) -> tuple[dict[RecordKey, str], str, set[str], int]:
    assignments, history, skipped = _verified_assignments(project, sources)
    actors = {a.event.actor for a in assignments.values()}
    if len(actors) != 1:
        raise ValueError("Each project must have current manual assignments from exactly one declared coder.")
    return {key: a.quote.quote_id for key, a in assignments.items()}, actors.pop(), history, skipped


def current_assignment_csv(project: CodedProject) -> tuple[str, int, int]:
    """One current project; report the original assigning actor on each verified row."""
    sources = _sources(project)
    assignments, _, skipped = _verified_assignments(project, sources)
    if not assignments:
        raise ValueError(f"No current manual codebook assignments. {skipped} other stored quote row(s); no CSV written.")
    digests = {name: sha256(sources[name].encode("utf-8")).hexdigest() for name in {key[0] for key in assignments}}
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(("assignment_id", "theme_id", "assigned_by", "assigned_utc", "code_name", "definition",
                     "examples_json", "source", "source_snapshot_sha256", "source_line", "source_start",
                     "source_end", "passage", "current_quote_memo", "other_current_rows_excluded", "scope"))
    scope = ("Current manual codebook assignments only, checked against creation records and the cached source. "
             "Other stored quote rows, including suggestions and freehand codes, are excluded. Absence is not a negative code. "
             "These records do not establish findings or complete review. Spreadsheet-leading =, +, -, @ are apostrophe-prefixed.")
    for key, a in sorted(assignments.items()):
        source, start, end, _ = key
        values = (a.quote.quote_id, a.theme_id, a.event.actor, a.event.timestamp_utc, a.entry.name, a.entry.description,
                  json.dumps(a.entry.examples, ensure_ascii=False), source, digests[source], a.quote.source_line,
                  start, end, a.quote.text, a.quote.memo, skipped, scope)
        writer.writerow(neutralize_csv_cell(v) if isinstance(v, str) else v for v in values)
    return output.getvalue(), len(assignments), skipped


def compare_recorded_assignments(current: CodedProject, comparison: CodedProject) -> RecordedComparison:
    sources = _sources(current)
    if sources != _sources(comparison):
        raise ValueError("Source sets and exact transcript text must match in full.")
    left, coder_a, history_a, skipped_a = _records(current, sources)
    right, coder_b, history_b, skipped_b = _records(comparison, sources)
    if history_a & history_b:
        raise ValueError("The projects contain shared assignment history, not separately recorded decisions.")
    if coder_a.strip().casefold() == coder_b.strip().casefold():
        raise ValueError("The same declared coder appears on both sides.")
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(("recorded_by", "current_coder", "comparison_coder", "definition_json",
                     "source", "source_start", "source_end", "passage", "current_quote_id", "comparison_quote_id",
                     "matching_documents", "other_current_quote_rows_excluded", "other_comparison_quote_rows_excluded", "scope"))
    both = len(left.keys() & right.keys())
    for key in sorted(left.keys() | right.keys()):
        source, start, end, snapshot = key
        side = "both" if key in left and key in right else "current only" if key in left else "comparison only"
        writer.writerow((side, neutralize_csv_cell(coder_a), neutralize_csv_cell(coder_b), snapshot,
                         neutralize_csv_cell(source), start, end, neutralize_csv_cell(sources[source][start:end]),
                         neutralize_csv_cell(left.get(key, "")), neutralize_csv_cell(right.get(key, "")),
                         len(sources), skipped_a, skipped_b, SCOPE))
    return RecordedComparison(output.getvalue(), both, len(left) - both, len(right) - both)

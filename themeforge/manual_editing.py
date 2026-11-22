from __future__ import annotations

# noqa: SIZE_OK - immutable manual-coding operations share result refresh helpers.

from copy import deepcopy
from dataclasses import asdict, dataclass, replace
from hashlib import sha256
import json
from math import isfinite
from uuid import uuid4

from .analysis_types import AnalysisResult, CodebookEntry, Theme, ThemeQuote, TranscriptDocument, ValidationValue
from .audit import AuditEvent
from .transcript_parser import parse_transcript

MANUAL_ONLY_NOTE = "No automated analysis has been run. Unselected passages have not been assessed."


@dataclass(frozen=True, slots=True)
class EditHistoryChange:
    result: AnalysisResult
    history: EditHistory


@dataclass(frozen=True, slots=True)
class EditHistory:
    undo_stack: tuple[AnalysisResult, ...] = ()
    redo_stack: tuple[AnalysisResult, ...] = ()

    def record(self, current: AnalysisResult) -> EditHistory:
        return EditHistory(
            undo_stack=(*self.undo_stack, deepcopy(current)),
            redo_stack=(),
        )

    def undo(self, current: AnalysisResult) -> EditHistoryChange | None:
        if not self.undo_stack:
            return None
        previous = deepcopy(self.undo_stack[-1])
        return EditHistoryChange(
            result=previous,
            history=EditHistory(
                undo_stack=self.undo_stack[:-1],
                redo_stack=(deepcopy(current), *self.redo_stack),
            ),
        )

    def redo(self, current: AnalysisResult) -> EditHistoryChange | None:
        if not self.redo_stack:
            return None
        next_result = deepcopy(self.redo_stack[0])
        return EditHistoryChange(
            result=next_result,
            history=EditHistory(
                undo_stack=(*self.undo_stack, deepcopy(current)),
                redo_stack=self.redo_stack[1:],
            ),
        )


@dataclass(frozen=True, slots=True)
class ManualSelection:
    source_name: str
    text: str
    source_line: int
    source_start: int
    source_end: int
    speaker: str = "Researcher selection"


def rename_theme(theme: Theme, name: str, keywords_text: str) -> Theme:
    clean_name = name.strip() or theme.name
    keywords = [item.strip() for item in keywords_text.replace(";", ",").split(",") if item.strip()]
    return _refresh_theme(
        replace(theme, name=clean_name, keywords=keywords or theme.keywords),
    )


def quote_move_conflict(source: Theme, target: Theme, quote: ThemeQuote) -> str:
    if sum(q.quote_id == quote.quote_id for q in source.quotes) != 1:
        return "The source assignment is ambiguous; nothing was moved."
    anchor = (quote.source_name, quote.source_start, quote.source_end)
    for existing in target.quotes:
        same_span = quote.source_end > quote.source_start and anchor == (
            existing.source_name, existing.source_start, existing.source_end)
        if existing.quote_id == quote.quote_id or same_span:
            return "The destination already has this quote ID or exact passage; both assignments were kept."
    return ""


def reassign_quote(
    result: AnalysisResult,
    source_theme_id: str,
    quote_id: str,
    target_theme_id: str,
    *, actor: str = "", reason: str = "",
) -> AnalysisResult:
    if source_theme_id == target_theme_id:
        return result
    source = _find_theme(result, source_theme_id)
    target = _find_theme(result, target_theme_id)
    if source is None or target is None:
        return result
    original = _find_quote(result, source_theme_id, quote_id)
    if original is None:
        return result
    conflict = quote_move_conflict(source, target, original)
    if conflict:
        raise ValueError(conflict)
    if not actor.strip() or not reason.strip():
        raise ValueError("A move needs a named editor and a reason; nothing was moved.")
    moved_quote = replace(original, relevance=None, rationale=(
        f"Manual move by {actor.strip()}: {source.name} ({source.id}) to {target.name} ({target.id}). "
        f"Not scored for the destination. Reason: {reason.strip()}\n"
        f"Previous rationale under {source.name} ({source.id}): {original.rationale}"))

    themes: list[Theme] = []
    for theme in result.themes:
        if theme.id == source_theme_id:  # noqa: IF_VARIANT_OK - ID routing, not a closed variant.
            themes.append(_refresh_theme(replace(theme, score=None, quotes=[q for q in theme.quotes if q.quote_id != quote_id])))
        elif theme.id == target_theme_id:
            themes.append(_refresh_theme(replace(theme, score=None, quotes=[*theme.quotes, moved_quote])))
        else:
            themes.append(theme)
    return replace(result, themes=themes)


def reassignment_details(source: Theme, target: Theme, original: ThemeQuote, reason: str) -> str:
    def group(theme: Theme) -> dict:
        return {"id": theme.id, "name": theme.name, "previous_stored_score": theme.score,
                "manual_definition_snapshot": theme.validation.get("manual_codebook_definition")}
    return json.dumps({"from_theme": group(source), "to_theme": group(target), "reason": reason.strip(),
                       "original_quote": asdict(original),
                       "score_scope": "Stored values before the move only; no destination score was calculated."},
                      ensure_ascii=False)


def merge_theme(
    result: AnalysisResult, target_theme_id: str, source_theme_id: str, *,
    documents: list[TranscriptDocument], actor: str, reason: str,
) -> tuple[AnalysisResult, str]:
    """Merge active passages and return the exact pre-merge records for the project audit."""
    source = _find_theme(result, source_theme_id)
    target = _find_theme(result, target_theme_id)
    if source is None or target is None or source is target or len({t.id for t in result.themes}) != len(result.themes):
        raise ValueError("Choose two distinct, unambiguous groups to merge.")
    if not actor.strip() or not reason.strip():
        raise ValueError("A merge needs a named editor and a reason; nothing was merged.")
    texts = {d.name: d.text for d in documents}
    if len(texts) != len(documents):
        raise ValueError("Source document names are ambiguous; nothing was merged.")
    by_id = {}
    dest_by_span = {}
    quotes = list(target.quotes)
    links = []
    used_sources = set()
    for group in (target, source):
        seen_spans = set()
        for q in group.quotes:
            name, start, end = span = (q.source_name, q.source_start, q.source_end)
            text = texts.get(name)
            if (text is None or not 0 <= start < end <= len(text) or text[start:end] != q.text
                    or text.count("\n", 0, start) + 1 != q.source_line):
                raise ValueError("A quote anchor does not match the saved transcript; nothing was merged.")
            if not q.quote_id or span in seen_spans or by_id.setdefault(q.quote_id, span) != span:
                raise ValueError("A duplicate assignment or conflicting quote ID is ambiguous; nothing was merged.")
            used_sources.add(name)
            seen_spans.add(span)
            if group is target:
                dest_by_span[span] = q
                continue
            existing = dest_by_span.get(span)
            links.append({"source_quote_id": q.quote_id, "destination_quote_id": existing.quote_id if existing else q.quote_id,
                          "relationship": "already present" if existing else "transferred without score"})
            if existing is None:
                quotes.append(replace(q, relevance=None, rationale=(
                    f"Manual merge by {actor.strip()}: {source.name} ({source.id}) into {target.name} ({target.id}). "
                    f"Not scored for this group. Reason: {reason.strip()}\nPrior rationale under {source.name}: {q.rationale}")))

    themes: list[Theme] = []
    for theme in result.themes:
        if theme.id == target_theme_id:
            parent_id = source.parent_theme_id if _has_ancestor(result, target_theme_id, source_theme_id) else theme.parent_theme_id
            themes.append(
                _refresh_theme(
                    replace(
                        theme,
                        parent_theme_id=parent_id,
                        quotes=quotes, score=None,
                    )
                )
            )
        elif theme.id != source_theme_id:
            parent_id = target_theme_id if theme.parent_theme_id == source_theme_id else theme.parent_theme_id
            themes.append(replace(theme, parent_theme_id=parent_id) if parent_id != theme.parent_theme_id else theme)
    note = (f"Merge by {actor.strip()}: {source.name} ({source.id}) into {target.name} ({target.id}). "
            "Shared passages count once. Both original coding records are available in the project audit log.")
    details = json.dumps({"reason": reason.strip(), "source_before": asdict(source), "destination_before": asdict(target),
                          "passage_links": links, "source_sha256": {
                              name: sha256(texts[name].encode("utf-8")).hexdigest() for name in sorted(used_sources)},
                          "score_scope": "Original scores apply only to their pre-merge groups and assignments."},
                         ensure_ascii=False)
    return replace(result, themes=themes, notes=[*result.notes, note]), details


def split_quote_to_theme(
    result: AnalysisResult,
    source_theme_id: str,
    quote_id: str,
    new_theme_name: str,
) -> AnalysisResult:
    moved_quote = _find_quote(result, source_theme_id, quote_id)
    source = _find_theme(result, source_theme_id)
    if moved_quote is None or source is None:
        return result

    next_number = _next_manual_theme_number(result)
    new_theme = Theme(
        id=f"M{next_number:02d}",
        name=new_theme_name.strip() or f"Split from {source.name}",
        color=source.color,
        keywords=[],
        quote_count=1,
        score=moved_quote.relevance,
        quotes=[moved_quote],
        validation=_manual_validation([moved_quote]),
    )
    themes = [
        _refresh_theme(replace(theme, quotes=[quote for quote in theme.quotes if quote.quote_id != quote_id]))
        if theme.id == source_theme_id
        else theme
        for theme in result.themes
    ]
    return replace(result, themes=[*themes, new_theme])


def code_selected_text(
    result: AnalysisResult,
    target_theme_id: str,
    selection: ManualSelection,
    new_theme_name: str = "", *, document: TranscriptDocument, actor: str, retrieval_similarity: float | None = None,
) -> AnalysisResult:
    start, end = selection.source_start, selection.source_end
    if not actor.strip() or not (document.name == selection.source_name and 0 <= start < end <= len(document.text)
            and document.text[start:end] == selection.text and selection.text.strip()
            and document.text.count("\n", 0, start) + 1 == selection.source_line):
        raise ValueError("A named coder and an exact passage in the active transcript are required.")
    if retrieval_similarity is not None and (not isfinite(retrieval_similarity) or retrieval_similarity <= 0):
        raise ValueError("The suggestion's retrieval similarity is invalid; no assignment was made.")
    target = _find_theme(result, target_theme_id)
    if target_theme_id and target is None:
        raise ValueError("The destination group no longer exists.")
    method = ("Selected directly from the transcript." if retrieval_similarity is None else
              f"Explicitly applied from similar passages; retrieval similarity {retrieval_similarity:g} is not a coding score.")

    quote = ThemeQuote(
        quote_id=f"MQF{uuid4().hex}",
        speaker=("Not attributed (manual selection)" if retrieval_similarity is None and selection.speaker == "Researcher selection"
                 else selection.speaker),
        text=selection.text,
        relevance=None,
        source_line=selection.source_line,
        source_name=selection.source_name,
        rationale=f"Manual coding decision by {actor.strip()}. {method} Assignment not scored.",
        source_start=selection.source_start,
        source_end=selection.source_end,
    )
    if target is None:
        return _add_manual_theme(result, quote, new_theme_name)
    themes = [
        _refresh_theme(replace(theme, quotes=_unique_quotes([*theme.quotes, quote])))
        if theme.id == target_theme_id
        else theme
        for theme in result.themes
    ]
    return replace(result, themes=themes)


def definition_text(entry: CodebookEntry) -> str:
    lines = [f"Code: {entry.name}", f"Definition: {entry.description or '(not supplied)'}"]
    return "\n".join([*lines, *(f"Example: {example}" for example in entry.examples)])


def manual_assignment_details(entry: CodebookEntry, source: str, start: int, end: int, theme_id: str) -> str:
    return f"{source}: chars {start}-{end}; theme={theme_id}\n{definition_text(entry)}"


def manual_assignment_rationale(entry: CodebookEntry, source: str, start: int, end: int, actor: str) -> str:
    return (f"Original manual codebook assignment by {actor.strip()} at {source} "
            f"[{start}:{end}]. Not scored.\n{definition_text(entry)}")


def entry_from_snapshot(snapshot: ValidationValue | None) -> CodebookEntry:
    if not isinstance(snapshot, str):
        raise ValueError("No recorded codebook entry is available.")
    raw = json.loads(snapshot)
    if not (isinstance(raw, dict) and set(raw) == {"name", "description", "examples"}
            and isinstance(raw["name"], str) and raw["name"].strip() and isinstance(raw["description"], str)
            and isinstance(raw["examples"], list) and all(isinstance(v, str) for v in raw["examples"])):
        raise ValueError("The recorded definition is incomplete or unsupported.")
    return CodebookEntry(raw["name"], raw["description"], tuple(raw["examples"]))


def recorded_definition(theme: Theme) -> CodebookEntry:
    snapshot = theme.validation.get("manual_codebook_definition")
    if not isinstance(snapshot, str):
        raise ValueError("A manual assignment has no original definition snapshot in its current group.")
    entry = entry_from_snapshot(snapshot)
    if entry.name != theme.name:
        raise ValueError("A manual assignment's current group differs from its recorded definition.")
    return entry


def assignment_creation(theme: Theme, quote: ThemeQuote, entry: CodebookEntry,
                        matches: list[AuditEvent]) -> AuditEvent:
    """Check the current assignment against the creation events indexed by its ID."""
    if len(matches) != 1 or not matches[0].actor.strip():
        raise ValueError("A manual assignment lacks one unambiguous, attributed creation audit event.")
    event = matches[0]
    anchor = quote.source_name, quote.source_start, quote.source_end
    if (event.details != manual_assignment_details(entry, *anchor, theme.id)
            or quote.rationale != manual_assignment_rationale(entry, *anchor, event.actor)):
        raise ValueError("An assignment changed or no longer matches its original attributed record.")
    return event


def apply_codebook_definition(
    result: AnalysisResult, entry: CodebookEntry, document: TranscriptDocument,
    selection: ManualSelection, actor: str, *, replacing: tuple[Theme, ThemeQuote] | None = None,
) -> tuple[AnalysisResult, str, str]:
    """Group by the exact loaded definition, never by its label or row number."""
    start, end = selection.source_start, selection.source_end
    if not actor.strip() or not (0 <= start < end <= len(document.text)) or (
        document.name != selection.source_name or document.text[start:end] != selection.text
        or not selection.text.strip() or document.text.count("\n", 0, start) + 1 != selection.source_line
    ):
        raise ValueError("A named coder and an exact selection in the active transcript are required.")
    snapshot = entry.snapshot()
    original = None
    if replacing:
        target, original = replacing
        if (not any(t is target for t in result.themes) or original not in target.quotes
                or not original.manual_definition_assignment or original.source_name != document.name
                or recorded_definition(target) != entry
                or sum(q.quote_id == original.quote_id for t in result.themes for q in t.quotes) != 1):
            raise ValueError("The original assignment, source or definition could not be identified.")
    else:
        target = next((t for t in result.themes if t.validation.get("manual_codebook_definition") == snapshot), None)
    if target and any(q.manual_definition_assignment and
                      (q.source_name, q.source_start, q.source_end) == (document.name, start, end)
                      for q in target.quotes):
        return result, target.id, ""
    quote = ThemeQuote(
        quote_id=f"MQD{uuid4().hex}", speaker="Not attributed (manual selection)",
        text=selection.text, relevance=None, source_line=selection.source_line, source_name=document.name,
        source_start=start, source_end=end,
        rationale=manual_assignment_rationale(entry, document.name, start, end, actor),
    )
    if target is None:
        updated = _add_manual_theme(result, quote, entry.name)
        target = updated.themes[-1]
        bound = _refresh_theme(replace(target, validation={"manual_codebook_definition": snapshot}))
        return replace(updated, themes=[*updated.themes[:-1], bound]), bound.id, quote.quote_id
    if original is not None:
        quotes = [replace(quote, memo=q.memo) if q.quote_id == original.quote_id else q for q in target.quotes]
        bound = replace(target, quotes=quotes)
    else:
        bound = _refresh_theme(replace(target, quotes=[*target.quotes, quote]))
    return replace(result, themes=[bound if t is target else t for t in result.themes]), bound.id, quote.quote_id


def uncode_quote(result: AnalysisResult, theme_id: str, quote_id: str) -> AnalysisResult:
    if _find_quote(result, theme_id, quote_id) is None:
        return result
    themes = [
        _refresh_theme(replace(theme, quotes=[quote for quote in theme.quotes if quote.quote_id != quote_id]))
        if theme.id == theme_id
        else theme
        for theme in result.themes
    ]
    return replace(result, themes=themes)


def update_quote_boundary(
    result: AnalysisResult,
    theme_id: str,
    quote_id: str,
    selection: ManualSelection,
    *,
    document: TranscriptDocument | None = None,
) -> AnalysisResult:
    text = selection.text.strip()
    original = _find_quote(result, theme_id, quote_id)
    if not text or original is None:
        return result
    if original.manual_definition_assignment:
        raise ValueError("Review the recorded definition to replace a manual codebook assignment.")
    start, end = selection.source_start, selection.source_end
    if (selection.source_name, start, end, text, selection.source_line) == (
        original.source_name, original.source_start, original.source_end, original.text, original.source_line
    ):
        return result
    speaker = "Not attributed (manual selection)"
    if document is not None:
        if selection.source_name != original.source_name:
            raise ValueError("Select text in the original transcript to update this boundary.")
        if not (document.name == original.source_name and 0 <= start < end <= len(document.text)
                and document.text[start:end] == text and document.text.count("\n", 0, start) + 1 == selection.source_line):
            raise ValueError("Select the exact replacement passage in its source transcript.")
        if original.speaker not in (speaker, "Researcher selection"):
            turns = parse_transcript(document.text, document.name)
            if (document.text[original.source_start:original.source_end] != original.text
                    or not any(turn.speaker == original.speaker
                               and turn.source_start <= min(start, original.source_start)
                               and max(end, original.source_end) <= turn.source_end for turn in turns)):
                raise ValueError("Keep the boundary inside the original speaker turn, or code the new selection separately.")
            speaker = original.speaker
    revised = _replace_quote_boundary(original, selection, text, speaker)
    themes = [
        _refresh_theme(
            replace(
                theme,
                quotes=[
                    revised if quote.quote_id == quote_id else quote
                    for quote in theme.quotes
                ],
            )
        )
        if theme.id == theme_id
        else theme
        for theme in result.themes
    ]
    return replace(result, themes=themes)


def update_theme_memo(result: AnalysisResult, theme_id: str, memo: str) -> AnalysisResult:
    if _find_theme(result, theme_id) is None:
        return result
    themes = [
        _refresh_theme(replace(theme, memo=memo.strip()))
        if theme.id == theme_id
        else theme
        for theme in result.themes
    ]
    return replace(result, themes=themes)


def update_quote_memo(result: AnalysisResult, theme_id: str, quote_id: str, memo: str) -> AnalysisResult:
    if _find_quote(result, theme_id, quote_id) is None:
        return result
    themes = [
        _refresh_theme(
            replace(
                theme,
                quotes=[
                    replace(quote, memo=memo.strip()) if quote.quote_id == quote_id else quote
                    for quote in theme.quotes
                ],
            )
        )
        if theme.id == theme_id
        else theme
        for theme in result.themes
    ]
    return replace(result, themes=themes)


def update_document_memo(
    documents: tuple[TranscriptDocument, ...],
    source_name: str,
    memo: str,
) -> tuple[TranscriptDocument, ...]:
    if not source_name:
        return documents
    return tuple(
        replace(document, memo=memo.strip()) if document.name == source_name else document
        for document in documents
    )


def set_theme_parent(result: AnalysisResult, theme_id: str, parent_theme_id: str) -> AnalysisResult:
    parent_id = parent_theme_id.strip()
    if _find_theme(result, theme_id) is None:
        return result
    if parent_id and _find_theme(result, parent_id) is None:
        return result
    if parent_id == theme_id or _has_ancestor(result, parent_id, theme_id):
        return result
    themes = [
        _refresh_theme(replace(theme, parent_theme_id=parent_id))
        if theme.id == theme_id
        else theme
        for theme in result.themes
    ]
    return replace(result, themes=themes)


def preserve_manual_themes(previous: AnalysisResult | None, generated: AnalysisResult) -> AnalysisResult:
    if previous is None:
        return generated
    manual_themes = [theme for theme in previous.themes if _is_manual_theme(theme)]
    if not manual_themes:
        return generated

    codebook_run = bool(generated.themes) and all(
        "generated_from_codebook_entry" in t.validation for t in generated.themes
    )
    if codebook_run:
        # C01 is an entry position, not a definition identity. Q0001 identifies a
        # passage, not its relationship to one particular definition.
        retained = [t if t.id.startswith("M") else replace(t, validation={
            **t.validation, "reanalysis_role": "Retained earlier edits"}) for t in manual_themes]
        candidates = [
            replace(t, validation={**t.validation, "reanalysis_role": "Fresh codebook suggestion",
                                   "review_status": "Fresh codebook match; researcher review required"})
            for t in generated.themes
        ]
    else:
        retained = manual_themes
        manual_theme_ids = {t.id for t in retained}
        manual_quote_ids = {q.quote_id for t in retained for q in t.quotes}
        candidates = []
        for theme in generated.themes:
            if theme.id in manual_theme_ids or not theme.quotes:
                continue
            quotes = [q for q in theme.quotes if q.quote_id not in manual_quote_ids]
            if len(quotes) == len(theme.quotes):
                candidates.append(theme)  # The full cluster can be larger than its displayed examples.
            elif quotes:
                candidates.append(_refresh_generated_theme(replace(theme, quotes=quotes)))

    parents, fresh = _preserve_hierarchy_parents(previous, retained, candidates)
    notes = list(generated.notes)
    if codebook_run:
        notes.append("Retained edited groups: " + ", ".join(t.id for t in retained) + ". Fresh codebook suggestions: "
                     + ", ".join(t.id for t in fresh) + ". Matching group positions or passage IDs do not transfer review.")
    else:
        notes.append(f"Preserved {len(retained)} researcher-edited theme(s) during reanalysis.")
    if parents:
        notes.append("Earlier suggestions kept as hierarchy parents: " + ", ".join(t.id for t in parents)
                     + ". Their evidence and scores describe the earlier grouping.")
    return replace(generated, themes=[*retained, *parents, *fresh], notes=notes)


def _retained_parent(theme: Theme) -> Theme:
    return replace(theme, validation={
        **theme.validation,
        "reanalysis_role": "Retained hierarchy parent",
        "review_status": "Earlier suggestion retained for hierarchy. Researcher review required.",
    })


def _preserve_hierarchy_parents(previous: AnalysisResult, retained: list[Theme],
                                candidates: list[Theme]) -> tuple[list[Theme], list[Theme]]:
    by_id = {t.id: t for t in previous.themes}
    retained_ids = {t.id for t in retained}
    pending = [t.parent_theme_id for t in retained if t.parent_theme_id]
    parent_ids: set[str] = set()
    while pending:
        parent_id = pending.pop()
        if parent_id in parent_ids:
            continue
        parent_ids.add(parent_id)
        parent = by_id.get(parent_id)
        if parent and parent.parent_theme_id:
            pending.append(parent.parent_theme_id)

    parents = {t.id: _retained_parent(t) for t in previous.themes if t.id in parent_ids and t.id not in retained_ids}
    # Reserve even unresolved older references. A new suggestion cannot supply the missing identity.
    taken = retained_ids | parent_ids
    reserved = taken | {t.id for t in candidates}
    fresh = []
    for candidate in candidates:
        group_id = candidate.id
        if group_id in parents and parents[group_id] == _retained_parent(candidate):
            # The underlying group matches, including evidence and definition. One copy serves both uses.
            del parents[group_id]
        elif group_id in taken:
            n = 2
            while f"{group_id}-r{n}" in reserved:
                n += 1
            group_id = f"{group_id}-r{n}"
            reserved.add(group_id)
            candidate = replace(candidate, id=group_id)
        fresh.append(candidate)
    return list(parents.values()), fresh


def _find_theme(result: AnalysisResult, theme_id: str) -> Theme | None:
    return next((theme for theme in result.themes if theme.id == theme_id), None)


def _has_ancestor(result: AnalysisResult, theme_id: str, ancestor_id: str) -> bool:
    current = _find_theme(result, theme_id)
    seen: set[str] = set()
    while current is not None and current.parent_theme_id:
        if current.parent_theme_id == ancestor_id:
            return True
        if current.parent_theme_id in seen:
            return False
        seen.add(current.parent_theme_id)
        current = _find_theme(result, current.parent_theme_id)
    return False


def _find_quote(result: AnalysisResult, theme_id: str, quote_id: str) -> ThemeQuote | None:
    theme = _find_theme(result, theme_id)
    if theme is None:
        return None
    return next((quote for quote in theme.quotes if quote.quote_id == quote_id), None)


def _unique_quotes(quotes: list[ThemeQuote]) -> list[ThemeQuote]:
    seen: set[str] = set()
    unique: list[ThemeQuote] = []
    for quote in quotes:
        if quote.quote_id in seen:
            continue
        seen.add(quote.quote_id)
        unique.append(quote)
    return unique


def _next_manual_theme_number(result: AnalysisResult) -> int:
    used = {
        int(theme.id[1:])
        for theme in result.themes
        if theme.id.startswith("M") and theme.id[1:].isdigit()
    }
    number = len(result.themes) + 1
    while number in used:
        number += 1
    return number


def _add_manual_theme(result: AnalysisResult, quote: ThemeQuote, name: str) -> AnalysisResult:
    theme = Theme(
        id=f"M{_next_manual_theme_number(result):02d}",
        name=name.strip() or "Manual Theme",
        color="#2563eb",
        keywords=[],
        quote_count=1,
        score=quote.relevance,
        quotes=[quote],
        validation=_manual_validation([quote]),
    )
    return replace(result, themes=[*result.themes, _refresh_theme(theme)])


def _replace_quote_boundary(quote: ThemeQuote, selection: ManualSelection, text: str, speaker: str) -> ThemeQuote:
    boundary_note = "Researcher updated this quote boundary from selected transcript text."
    rationale = boundary_note
    if quote.reported_relevance is None:
        rationale = quote.rationale.removesuffix("\n" + boundary_note) + "\n" + boundary_note
    return replace(
        quote,
        text=text,
        speaker=speaker,
        relevance=None,
        source_name=selection.source_name,
        source_line=selection.source_line,
        source_start=selection.source_start,
        source_end=selection.source_end,
        rationale=rationale,
    )


def _refresh_theme(theme: Theme) -> Theme:
    validation = _manual_validation(theme.quotes)
    if "generated_from_codebook_entry" in theme.validation:
        validation["generated_from_codebook_entry"] = theme.validation["generated_from_codebook_entry"]
    if "manual_codebook_definition" in theme.validation:
        validation["manual_codebook_definition"] = theme.validation["manual_codebook_definition"]
    if theme.reported_score is None:
        validation.pop("central_theme_alignment", None)
        validation["score_scope"] = "Not scored"
    return replace(
        theme,
        quote_count=len(theme.quotes),
        validation=validation,
    )


def _refresh_generated_theme(theme: Theme) -> Theme:
    validation = dict(theme.validation)
    validation["evidence_count"] = len(theme.quotes)
    validation["displayed_quote_count"] = len(theme.quotes)
    validation["source_count"] = len({quote.source_name for quote in theme.quotes})
    validation["speaker_count"] = len({quote.speaker for quote in theme.quotes})
    return replace(theme, quote_count=len(theme.quotes), validation=validation)


def _is_manual_theme(theme: Theme) -> bool:
    return theme.validation.get("review_status") == "Researcher edited" or theme.id.startswith("M")


def _manual_validation(quotes: list[ThemeQuote]) -> dict[str, ValidationValue]:
    return {
        "evidence_count": len(quotes),
        "displayed_quote_count": len(quotes),
        "source_count": len({quote.source_name for quote in quotes}),
        "speaker_count": len({quote.speaker for quote in quotes if not quote.manual_definition_assignment
                              and quote.speaker != "Not attributed (manual selection)"}),
        "central_theme_alignment": 0.0,
        "review_status": "Researcher edited",
    }

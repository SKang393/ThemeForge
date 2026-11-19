from __future__ import annotations

# noqa: SIZE_OK - immutable manual-coding operations share result refresh helpers.

from copy import deepcopy
from dataclasses import asdict, dataclass, replace
import json
from uuid import uuid4

from .analysis_types import AnalysisResult, CodebookEntry, Theme, ThemeQuote, TranscriptDocument, ValidationValue

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


def merge_theme(result: AnalysisResult, target_theme_id: str, source_theme_id: str) -> AnalysisResult:
    if target_theme_id == source_theme_id:
        return result
    source = _find_theme(result, source_theme_id)
    if source is None or _find_theme(result, target_theme_id) is None:
        return result

    themes: list[Theme] = []
    for theme in result.themes:
        if theme.id == target_theme_id:
            parent_id = "" if theme.parent_theme_id == source_theme_id else theme.parent_theme_id
            themes.append(
                _refresh_theme(
                    replace(
                        theme,
                        parent_theme_id=parent_id,
                        quotes=_unique_quotes([*theme.quotes, *source.quotes]),
                    )
                )
            )
        elif theme.id != source_theme_id:
            parent_id = target_theme_id if theme.parent_theme_id == source_theme_id else theme.parent_theme_id
            themes.append(
                _refresh_theme(replace(theme, parent_theme_id=parent_id))
                if parent_id != theme.parent_theme_id
                else theme
            )
    return replace(result, themes=themes)


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
    new_theme_name: str = "",
) -> AnalysisResult:
    text = selection.text.strip()
    if not text:
        return result

    quote = ThemeQuote(
        quote_id=_next_manual_quote_id(result),
        speaker=selection.speaker,
        text=text,
        relevance=1.0,
        source_line=selection.source_line,
        source_name=selection.source_name,
        rationale="Researcher manually coded this selected transcript passage.",
        source_start=selection.source_start,
        source_end=selection.source_end,
    )
    target = _find_theme(result, target_theme_id)
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


def apply_codebook_definition(
    result: AnalysisResult, entry: CodebookEntry, document: TranscriptDocument,
    selection: ManualSelection, actor: str,
) -> tuple[AnalysisResult, str, str]:
    """Group by the exact loaded definition, never by its label or row number."""
    start, end = selection.source_start, selection.source_end
    if not actor.strip() or not (0 <= start < end <= len(document.text)) or (
        document.name != selection.source_name or document.text[start:end] != selection.text
        or not selection.text.strip() or document.text.count("\n", 0, start) + 1 != selection.source_line
    ):
        raise ValueError("A named coder and an exact selection in the active transcript are required.")
    snapshot = json.dumps(asdict(entry), ensure_ascii=False, sort_keys=True)
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
    bound = _refresh_theme(replace(target, quotes=[*target.quotes, quote]))
    return replace(result, themes=[bound if t.id == target.id else t for t in result.themes]), bound.id, quote.quote_id


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
) -> AnalysisResult:
    text = selection.text.strip()
    if not text or _find_quote(result, theme_id, quote_id) is None:
        return result
    themes = [
        _refresh_theme(
            replace(
                theme,
                quotes=[
                    _replace_quote_boundary(quote, selection, text) if quote.quote_id == quote_id else quote
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

    manual_theme_ids = {theme.id for theme in manual_themes}
    manual_quote_ids = {quote.quote_id for theme in manual_themes for quote in theme.quotes}
    generated_themes = [
        _refresh_generated_theme(
            replace(
                theme,
                quotes=[quote for quote in theme.quotes if quote.quote_id not in manual_quote_ids],
            )
        )
        for theme in generated.themes
        if theme.id not in manual_theme_ids
    ]
    return replace(
        generated,
        themes=[*manual_themes, *[theme for theme in generated_themes if theme.quotes]],
        notes=[
            *generated.notes,
            f"Preserved {len(manual_themes)} researcher-edited theme(s) during reanalysis.",
        ],
    )


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


def _next_manual_quote_id(result: AnalysisResult) -> str:
    used = {
        int(quote.quote_id[2:])
        for theme in result.themes
        for quote in theme.quotes
        if quote.quote_id.startswith("MQ") and quote.quote_id[2:].isdigit()
    }
    number = 1
    while number in used:
        number += 1
    return f"MQ{number:02d}"


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
    return replace(result, themes=[*result.themes, theme])


def _replace_quote_boundary(quote: ThemeQuote, selection: ManualSelection, text: str) -> ThemeQuote:
    boundary_note = "Researcher updated this quote boundary from selected transcript text."
    rationale = boundary_note
    if quote.reported_relevance is None:
        rationale = quote.rationale.removesuffix("\n" + boundary_note) + "\n" + boundary_note
    return replace(
        quote,
        text=text,
        source_name=selection.source_name,
        source_line=selection.source_line,
        source_start=selection.source_start,
        source_end=selection.source_end,
        rationale=rationale,
    )


def _refresh_theme(theme: Theme) -> Theme:
    validation = _manual_validation(theme.quotes)
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
        "speaker_count": len({quote.speaker for quote in quotes if not quote.manual_definition_assignment}),
        "central_theme_alignment": 0.0,
        "review_status": "Researcher edited",
    }

from __future__ import annotations

# noqa: SIZE_OK - immutable manual-coding operations share result refresh helpers.

from copy import deepcopy
from dataclasses import dataclass, replace
from hashlib import sha256
import re

from .analysis_types import AnalysisResult, Theme, ThemeQuote, TranscriptDocument, ValidationValue


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
    source_text: str | None = None


def rename_theme(theme: Theme, name: str, keywords_text: str) -> Theme:
    clean_name = name.strip() or theme.name
    keywords = [item.strip() for item in keywords_text.replace(";", ",").split(",") if item.strip()]
    return _refresh_theme(
        replace(theme, name=clean_name, keywords=keywords or theme.keywords),
    )


def reassign_quote(
    result: AnalysisResult,
    source_theme_id: str,
    quote_id: str,
    target_theme_id: str,
) -> AnalysisResult:
    if source_theme_id == target_theme_id:
        return result
    moved_quote = _find_quote(result, source_theme_id, quote_id)
    if moved_quote is None or _find_theme(result, target_theme_id) is None:
        return result

    themes: list[Theme] = []
    for theme in result.themes:
        if theme.id == source_theme_id:  # noqa: IF_VARIANT_OK - ID routing, not a closed variant.
            themes.append(_refresh_theme(replace(theme, quotes=[quote for quote in theme.quotes if quote.quote_id != quote_id])))
        elif theme.id == target_theme_id:
            themes.append(_refresh_theme(replace(theme, quotes=[*theme.quotes, moved_quote])))
        else:
            themes.append(theme)
    return replace(result, themes=themes)


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
    selection = _trim_selection(selection)
    text = selection.text
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
        **_selection_metadata(result, selection),
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
    selection = _trim_selection(selection)
    text = selection.text
    if not text or _find_quote(result, theme_id, quote_id) is None:
        return result
    themes = [
        _refresh_theme(
            replace(
                theme,
                quotes=[
                    _replace_quote_boundary(quote, selection, text, result) if quote.quote_id == quote_id else quote
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

    old_codes = {entry.code_id: entry for entry in previous.codebook_snapshot}
    new_codes = {entry.code_id: entry for entry in generated.codebook_snapshot}
    if generated.discovery:
        # Discovery definitions originate in generated proposals, not an imported
        # codebook. Keep that provenance separate while checking actual revisions.
        new_codes.update({theme.code_id: theme.code_definition for theme in generated.themes
                          if theme.code_id and theme.code_definition is not None})
    preserved: list[Theme] = []
    for theme in manual_themes:
        reasons: list[str] = []
        if previous.analytic_intent != generated.analytic_intent:
            reasons.append("analytic intent changed")
        if theme.code_id and (
            (theme.code_definition or old_codes.get(theme.code_id)) != new_codes.get(theme.code_id)
            or theme.code_version != getattr(new_codes.get(theme.code_id), "version", 0)
        ):
            reasons.append("code definition changed or was removed")
        for quote in theme.quotes:
            old_hash = quote.source_hash or previous.source_hashes.get(quote.source_name, "")
            if generated.source_hashes and old_hash != generated.source_hashes.get(quote.source_name, ""):
                reasons.append("source changed, was removed, or historical evidence is unversioned")
                break
        if reasons:
            theme = replace(theme, validation={**theme.validation, "recheck_reason": "; ".join(reasons)})
        preserved.append(theme)
    manual_themes = preserved

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
            *[
                f"Review preserved theme {theme.id}: {theme.validation['recheck_reason']}."
                for theme in manual_themes if theme.validation.get("recheck_reason")
            ],
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


def _trim_selection(selection: ManualSelection) -> ManualSelection:
    left = len(selection.text) - len(selection.text.lstrip())
    right = len(selection.text) - len(selection.text.rstrip())
    start = selection.source_start + left
    line = (
        1 + len(re.findall(r"\r\n|\r|\n", selection.source_text[:start]))
        if selection.source_text is not None else
        selection.source_line + len(re.findall(r"\r\n|\r|\n", selection.text[:left]))
    )
    return replace(selection, text=selection.text.strip(),
                   source_start=start, source_end=selection.source_end - right, source_line=line)


def _selection_metadata(result: AnalysisResult, selection: ManualSelection) -> dict[str, str | int]:
    if selection.source_start < 0 or selection.source_end <= selection.source_start:
        raise ValueError("Selection offsets must describe a nonempty source span.")
    source_length = result.source_lengths.get(selection.source_name)
    if source_length is not None and selection.source_end > source_length:
        raise ValueError("Selection offsets exceed the recorded source length.")
    verified_hash = ""
    if selection.source_text is not None:
        if selection.source_text[selection.source_start:selection.source_end] != selection.text:
            raise ValueError("Selected text does not resolve to the supplied source span.")
        verified_hash = sha256(selection.source_text.encode("utf-8")).hexdigest()
        previous_hash = result.source_hashes.get(selection.source_name)
        if previous_hash and previous_hash != verified_hash:
            raise ValueError("The source changed since analysis; rerun analysis before coding.")
    for unit in result.evidence_units:
        if (unit.source_name == selection.source_name and unit.context_text
                and unit.context_start <= selection.source_start < selection.source_end <= unit.context_end):
            start = selection.source_start - unit.context_start
            end = selection.source_end - unit.context_start
            if unit.context_text[start:end] != selection.text:
                raise ValueError("Selected text does not resolve to the analysis source version; reload or rerun analysis.")
            return {"source_hash": unit.source_hash, "context_start": unit.context_start,
                    "context_end": unit.context_end, "context_text": unit.context_text}
    # An arbitrary manual span may extend beyond every stored turn window.
    # Do not carry unrelated context from the previous quote after boundary changes.
    return {"source_hash": verified_hash,
            "context_start": selection.source_start, "context_end": selection.source_end,
            "context_text": selection.text}


def _replace_quote_boundary(quote: ThemeQuote, selection: ManualSelection, text: str, result: AnalysisResult) -> ThemeQuote:
    return replace(
        quote,
        text=text,
        source_name=selection.source_name,
        source_line=selection.source_line,
        source_start=selection.source_start,
        source_end=selection.source_end,
        rationale="Researcher updated this quote boundary from selected transcript text.",
        **_selection_metadata(result, selection),
    )


def _refresh_theme(theme: Theme) -> Theme:
    return replace(
        theme,
        quote_count=len(theme.quotes),
        validation={**theme.validation, **_manual_validation(theme.quotes)},
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
        "speaker_count": len({quote.speaker for quote in quotes}),
        "central_theme_alignment": 0.0,
        "review_status": "Researcher edited",
        "score_kind": "researcher_assigned",
        "origin": "manual",
    }

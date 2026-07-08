from __future__ import annotations

from dataclasses import replace

from .analysis_types import AnalysisResult, Theme, ThemeQuote, ValidationValue


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
            themes.append(_refresh_theme(replace(theme, quotes=_unique_quotes([*theme.quotes, *source.quotes]))))
        elif theme.id != source_theme_id:
            themes.append(theme)
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


def _find_theme(result: AnalysisResult, theme_id: str) -> Theme | None:
    return next((theme for theme in result.themes if theme.id == theme_id), None)


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


def _refresh_theme(theme: Theme) -> Theme:
    return replace(
        theme,
        quote_count=len(theme.quotes),
        validation=_manual_validation(theme.quotes),
    )


def _manual_validation(quotes: list[ThemeQuote]) -> dict[str, ValidationValue]:
    return {
        "evidence_count": len(quotes),
        "displayed_quote_count": len(quotes),
        "source_count": len({quote.source_name for quote in quotes}),
        "speaker_count": len({quote.speaker for quote in quotes}),
        "central_theme_alignment": 0.0,
        "review_status": "Researcher edited",
    }

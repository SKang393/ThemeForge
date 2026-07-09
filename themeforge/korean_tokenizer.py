from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Protocol

from .analysis_constants import STOPWORDS

HANGUL_START = "\uac00"
HANGUL_END = "\ud7a3"
CONTENT_TAG_PREFIXES = ("N", "V", "SL", "SN", "XR")


class KoreanToken(Protocol):
    form: str
    tag: str


def has_hangul(text: str) -> bool:
    return any(HANGUL_START <= char <= HANGUL_END for char in text)


def korean_terms(text: str) -> list[str]:
    tokens = _kiwi_tokenize(text)
    if tokens is None:
        return []
    return _terms_from_tokens(tokens)


def _kiwi_tokenize(text: str) -> Sequence[KoreanToken] | None:
    try:
        from kiwipiepy import Kiwi
    except ImportError:
        return None

    try:
        return Kiwi().tokenize(text)
    except (OSError, RuntimeError):
        return None


def _terms_from_tokens(tokens: Iterable[KoreanToken]) -> list[str]:
    terms = []
    for token in tokens:
        term = token.form.strip().lower()
        if not _is_content_term(term, token.tag):
            continue
        terms.append(term)
    return terms


def _is_content_term(term: str, tag: str) -> bool:
    if len(term) < 2 and has_hangul(term):
        return False
    if term in STOPWORDS:
        return False
    return tag.startswith(CONTENT_TAG_PREFIXES)

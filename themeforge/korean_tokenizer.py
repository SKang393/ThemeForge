from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Protocol
import re

from .analysis_constants import STOPWORDS

HANGUL_START = "\uac00"
HANGUL_END = "\ud7a3"
CONTENT_TAG_PREFIXES = ("N", "V", "SL", "SN", "XR")


# Keep only whole, separately attested bases. A prefix shared by two compounds is insufficient.
_PARTICLE_FORM = re.compile(r"([가-힣]{3,}?)(에서는|에는|에서|에게|으로|을|를|은|는|이|가|에|로)")


def add_particle_terms(documents: list[list[str]], reference: Sequence[list[str]]) -> None:
    vocabulary = {term for terms in reference for term in terms if "_" not in term}
    aliases = {}
    for term in {word for terms in documents for word in terms if "_" not in word}:
        match = _PARTICLE_FORM.fullmatch(term)
        if not match:
            continue
        base, particle = match.groups()
        if base not in vocabulary:
            continue
        final = (ord(base[-1]) - ord(HANGUL_START)) % 28
        if (particle in ("을", "은", "이") and not final) or (particle in ("를", "는", "가") and final):
            continue
        if (particle == "으로" and final in (0, 8)) or (particle == "로" and final not in (0, 8)):
            continue
        aliases[term] = base
    if aliases:
        for terms in documents:
            terms.extend([aliases[term] for term in terms if term in aliases])


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

from __future__ import annotations

import re

from .analysis_constants import STOPWORDS, TOKEN_NOISE_RE, TOKEN_RE
from .korean_tokenizer import has_hangul, korean_terms


def split_sentences(text: str) -> list[str]:
    from .analysis_constants import SENTENCE_RE

    return [sentence.strip() for sentence in SENTENCE_RE.split(text) if sentence.strip()]


def tokenize(text: str, keep_stopwords: bool = False, extra_stopwords: set[str] | None = None) -> list[str]:
    extra = extra_stopwords or set()
    tokens = []
    korean = korean_terms(text) if has_hangul(text) else []
    for match in TOKEN_RE.finditer(text.lower()):
        token = match.group(0).strip("'-")
        if not token or TOKEN_NOISE_RE.fullmatch(token):
            continue
        if korean and has_hangul(token):
            continue
        if not keep_stopwords and (token in STOPWORDS or token in extra):
            continue
        tokens.append(token)
    tokens.extend(token for token in korean if keep_stopwords or token not in extra)
    return tokens


def normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def normalize_for_match(text: str) -> str:
    text = text.lower().replace("-", " ")
    text = re.sub("[^a-z0-9\\uac00-\\ud7a3-]+", " ", text)
    return normalize_space(text)

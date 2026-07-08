from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

from .analysis_vectors import normalize_vector

ENGLISH_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
MULTILINGUAL_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


@dataclass(frozen=True, slots=True)
class EmbeddingResult:
    vectors: list[dict[str, float]]
    note: str


class EmbeddingModel(Protocol):
    def encode(self, texts: list[str], normalize_embeddings: bool = True) -> Sequence[Sequence[float]]:
        ...


def local_embedding_vectors(texts: Sequence[str], language_mode: str) -> EmbeddingResult:
    model_name = _model_name(language_mode)
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        return EmbeddingResult(
            vectors=[],
            note=(
                "Local embeddings unavailable; install ThemeForge with the optional nlp extra "
                "after downloading the model once, then analysis can run offline."
            ),
        )

    try:
        return _encode_with_model(texts, model_name, SentenceTransformer)
    except (OSError, RuntimeError):
        return EmbeddingResult(
            vectors=[],
            note=(
                f"Local embeddings unavailable; {model_name} is not in the local model cache yet. "
                "TF-IDF clustering was used instead."
            ),
        )


def _model_name(language_mode: str) -> str:
    if language_mode in {"Korean", "Multilingual"}:
        return MULTILINGUAL_MODEL
    return ENGLISH_MODEL


def _encode_with_model(
    texts: Sequence[str],
    model_name: str,
    model_factory: Callable[[str], EmbeddingModel],
) -> EmbeddingResult:
    model = model_factory(model_name)
    raw_vectors = model.encode(list(texts), normalize_embeddings=True)
    vectors = [
        normalize_vector({f"e{index}": float(value) for index, value in enumerate(vector)})
        for vector in raw_vectors
    ]
    return EmbeddingResult(
        vectors=vectors,
        note=f"Local embedding clustering used: {model_name}.",
    )

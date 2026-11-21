import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from themeforge.local_embeddings import (
    ENGLISH_MODEL,
    MULTILINGUAL_MODEL,
    _encode_with_model,
    _model_name,
    local_embedding_vectors,
)


class FakeEmbeddingModel:
    def __init__(self, model_name: str):
        self.model_name = model_name

    def encode(self, texts: list[str], normalize_embeddings: bool = True):
        scale = 1.0 if normalize_embeddings else 2.0
        return [[scale, float(len(text))] for text in texts]


class LocalEmbeddingTests(unittest.TestCase):
    def test_missing_cached_model_falls_back_without_enabling_downloads(self):
        loader = Mock(side_effect=FileNotFoundError)
        package = SimpleNamespace(SentenceTransformer=loader)
        with patch.dict("sys.modules", {"sentence_transformers": package}):
            result = local_embedding_vectors(["The latch held."], "English")

        loader.assert_called_once_with(ENGLISH_MODEL, local_files_only=True, trust_remote_code=False)
        self.assertEqual(result.vectors, [])
        self.assertEqual(result.model_name, "")
        self.assertIn("TF-IDF", result.note)

    def test_language_mode_selects_english_or_multilingual_model(self):
        self.assertEqual(_model_name("English"), ENGLISH_MODEL)
        self.assertEqual(_model_name("Auto"), ENGLISH_MODEL)
        self.assertEqual(_model_name("Korean"), MULTILINGUAL_MODEL)
        self.assertEqual(_model_name("Multilingual"), MULTILINGUAL_MODEL)

    def test_encode_with_model_returns_normalized_sparse_vectors(self):
        result = _encode_with_model(["short", "longer text"], ENGLISH_MODEL, FakeEmbeddingModel)

        self.assertEqual(len(result.vectors), 2)
        self.assertIn("Local embedding clustering used", result.note)
        self.assertAlmostEqual(sum(value * value for value in result.vectors[0].values()), 1.0)


if __name__ == "__main__":
    unittest.main()

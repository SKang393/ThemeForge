import unittest

from themeforge.local_embeddings import (
    ENGLISH_MODEL,
    MULTILINGUAL_MODEL,
    _encode_with_model,
    _model_name,
)


class FakeEmbeddingModel:
    def __init__(self, model_name: str):
        self.model_name = model_name

    def encode(self, texts: list[str], normalize_embeddings: bool = True):
        scale = 1.0 if normalize_embeddings else 2.0
        return [[scale, float(len(text))] for text in texts]


class LocalEmbeddingTests(unittest.TestCase):
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

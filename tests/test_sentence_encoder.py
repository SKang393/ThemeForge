"""Recipe checks only. Live encoder comparison is separately frozen and recorded."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

import numpy as np

from themeforge.sentence_encoder import masked_mean_normalize, token_chunks, local_file


class SentenceEncoderTests(unittest.TestCase):
    def test_masked_pooling_ignores_padding_not_cls_pooling(self):
        hidden = np.zeros((1, 3, 384))
        hidden[0, 0, 0] = 2
        hidden[0, 1, 1] = 2
        hidden[0, 2, 2] = 999
        vector = masked_mean_normalize(hidden, [[1, 1, 0]])[0]
        self.assertAlmostEqual(vector[0], 2 ** -.5)
        self.assertAlmostEqual(vector[1], 2 ** -.5)
        self.assertEqual(vector[2], 0)
        self.assertAlmostEqual(float(np.linalg.norm(vector)), 1)

    def test_invalid_model_outputs_fail(self):
        for values, mask in [(np.zeros((1, 2, 384)), [[0, 0]]),
                             (np.zeros((1, 2, 384)), [[1, 1]]),
                             (np.full((1, 2, 384), float("nan")), [[1, 1]]),
                             (np.ones((1, 2, 383)), [[1, 1]]),
                             (np.ones((1, 2, 384)), [[1, 2]])]:
            with self.subTest(shape=values.shape, mask=mask), self.assertRaises(ValueError):
                masked_mean_normalize(values, mask)

    def test_long_turn_keeps_every_token_in_recorded_chunks(self):
        tokenizer = Mock()
        tokenizer.encode.return_value = SimpleNamespace(ids=list(range(510)), offsets=[(i, i + 1) for i in range(510)])
        chunks = token_chunks(tokenizer, "x" * 510)
        self.assertEqual([c["payload_tokens"] for c in chunks], [254, 254, 2])
        self.assertEqual([i for c in chunks for i in c["input_ids"][1:-1]], list(range(510)))
        self.assertTrue(all(len(c["input_ids"]) <= 256 for c in chunks))
        self.assertEqual(chunks[-1]["source_char_end"], 510)
        tokenizer.encode.assert_called_once_with("x" * 510, add_special_tokens=False)

    def test_budget_rejects_before_silent_truncation(self):
        tokenizer = Mock()
        tokenizer.encode.return_value = SimpleNamespace(ids=list(range(2033)), offsets=[(0, 1)] * 2033)
        with self.assertRaises(ValueError):
            token_chunks(tokenizer, "x")
        for text in ("", " ", "x" * 50001):
            with self.assertRaises(ValueError):
                token_chunks(tokenizer, text)

    def test_network_and_relative_profile_paths_rejected(self):
        for path in ("https://example.org/model", "//server/model", "\\\\server\\model", "relative.json"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                local_file(path)


if __name__ == "__main__":
    unittest.main()

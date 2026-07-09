import unittest
from dataclasses import dataclass
from unittest.mock import patch

from themeforge.korean_tokenizer import _terms_from_tokens, korean_terms
from themeforge.text_utils import tokenize


@dataclass(frozen=True, slots=True)
class FakeKoreanToken:
    form: str
    tag: str


class KoreanTokenizerTests(unittest.TestCase):
    def test_terms_from_tokens_keep_content_morphemes_when_korean_text_has_particles(self):
        tokens = [
            FakeKoreanToken("교육과정", "NNG"),
            FakeKoreanToken("은", "JX"),
            FakeKoreanToken("개발", "NNG"),
            FakeKoreanToken("하", "VV"),
            FakeKoreanToken("고", "EC"),
            FakeKoreanToken("협력", "NNG"),
            FakeKoreanToken(".", "SF"),
        ]

        terms = _terms_from_tokens(tokens)

        self.assertEqual(terms, ["교육과정", "개발", "협력"])

    def test_korean_terms_returns_empty_fallback_when_kiwipiepy_is_unavailable(self):
        with patch("themeforge.korean_tokenizer._kiwi_tokenize", return_value=None):
            terms = korean_terms("교육과정은 개발과 협력이 중요합니다")

        self.assertEqual(terms, [])

    def test_tokenize_uses_korean_terms_instead_of_raw_hangul_chunks_when_available(self):
        with patch("themeforge.text_utils.korean_terms", return_value=["교육과정", "개발", "협력"]):
            tokens = tokenize("교육과정은 개발과 협력이 중요합니다")

        self.assertEqual(tokens, ["교육과정", "개발", "협력"])


if __name__ == "__main__":
    unittest.main()

from dataclasses import replace
from unittest.mock import patch
import unittest

from themeforge.analysis import AnalysisResult, AnalysisSettings, Theme, ThemeQuote, TranscriptDocument
from themeforge.analysis_vectors import cosine, tfidf_vectors
from themeforge.local_embeddings import EmbeddingResult
from themeforge.quote_units import extract_quote_units
from themeforge.semantic_suggestions import find_similar_passages
from themeforge.transcript_parser import parse_transcript


def result_with_quote(quote: ThemeQuote) -> AnalysisResult:
    return AnalysisResult(
        version="1.4.1",
        document_count=1,
        quote_count=1,
        themes=[
            Theme(
                id="T01",
                name="Collaborative curriculum planning",
                color="#2563eb",
                keywords=["peer collaboration", "curriculum planning"],
                quote_count=1,
                score=0.9,
                quotes=[quote],
            )
        ],
        notes=[],
    )


class SemanticSuggestionTests(unittest.TestCase):
    def test_theme_can_find_a_passage_stored_under_another_code(self):
        lines = (
            "Fitter: I used the manometer to copy the gauge reading onto my card.",
            "Fitter: I started a lockout and copied the gauge reading onto my card.",
            "Fitter: I finished a lockout before stacking the empty drums.",
            "Fitter: I copied the gauge reading onto a spare slip.",
        )
        doc = TranscriptDocument("floor.txt", "\n".join(lines))
        units = extract_quote_units(parse_transcript(doc.text, source_name=doc.name))

        def stored(unit):
            return ThemeQuote(unit.id, unit.speaker, unit.text, 0.9, unit.source_line, doc.name,
                              source_start=unit.source_start, source_end=unit.source_end)

        seed, shared, unrelated, fresh = units
        result = result_with_quote(stored(seed))
        target = replace(result.themes[0], name="Pressure logging", keywords=["manometer"])
        isolation = replace(target, id="T02", name="Switch isolation", keywords=["lockout"],
                            quotes=[stored(shared), stored(unrelated)], quote_count=2)
        result = replace(result, themes=[target, isolation])
        request = ((doc,), result, target.id, AnalysisSettings())
        hits = {p.source_start: p for p in find_similar_passages(*request).passages}
        self.assertEqual(set(hits), {shared.source_start, fresh.source_start})
        hit = hits[shared.source_start]
        self.assertEqual(doc.text[hit.source_start:hit.source_end], shared.text)
        reference, _ = tfidf_vectors([hit.query.text, fresh.text])
        self.assertEqual(hits[fresh.source_start].similarity, cosine(*reference))
        from_quote = find_similar_passages(*request, quote_id=seed.id)
        self.assertEqual([p.source_start for p in from_quote.passages], [fresh.source_start])
        isolation = replace(isolation, quotes=[*isolation.quotes, stored(fresh)], quote_count=3)
        fully_stored = replace(result, themes=[target, isolation])
        hits = find_similar_passages((doc,), fully_stored, target.id, AnalysisSettings()).passages
        self.assertEqual({p.source_start for p in hits}, {shared.source_start, fresh.source_start})

    def test_tfidf_ranking_returns_related_uncoded_passages_and_excludes_coded_span(self):
        transcript = "\n".join(
            (
                "Teacher 1: Peer planning helped our curriculum team revise lessons together.",
                "Teacher 2: Peer collaboration improved our curriculum planning process.",
                "Teacher 3: The cafeteria menu changed during the spring semester.",
            )
        )
        units = extract_quote_units(parse_transcript(transcript, source_name="teachers.txt"), min_quote_words=4)
        coded = units[0]
        result = result_with_quote(
            ThemeQuote(
                quote_id=coded.id,
                speaker=coded.speaker,
                text=coded.text,
                relevance=0.9,
                source_line=coded.source_line,
                source_name=coded.source_name,
                source_start=coded.source_start,
                source_end=coded.source_end,
            )
        )

        suggestions = find_similar_passages(
            (TranscriptDocument("teachers.txt", transcript),),
            result,
            "T01",
            AnalysisSettings(semantic_backend="tfidf"),
        )

        self.assertEqual([item.speaker for item in suggestions.passages], ["Teacher 2"])
        self.assertIn("Peer collaboration", suggestions.passages[0].text)
        self.assertGreater(suggestions.passages[0].similarity, 0.0)
        self.assertIn("TF-IDF", suggestions.note)

    def test_same_text_in_another_source_remains_eligible(self):
        text = "Teacher: Peer planning helped our curriculum team revise lessons together."
        first_unit = extract_quote_units(parse_transcript(text, source_name="first.txt"), min_quote_words=4)[0]
        result = result_with_quote(
            ThemeQuote(
                quote_id=first_unit.id,
                speaker=first_unit.speaker,
                text=first_unit.text,
                relevance=0.9,
                source_line=first_unit.source_line,
                source_name=first_unit.source_name,
                source_start=first_unit.source_start,
                source_end=first_unit.source_end,
            )
        )

        suggestions = find_similar_passages(
            (TranscriptDocument("first.txt", text), TranscriptDocument("second.txt", text)),
            result,
            "T01",
            AnalysisSettings(),
        )

        self.assertEqual(len(suggestions.passages), 1)
        self.assertEqual(suggestions.passages[0].source_name, "second.txt")

    def test_passage_overlapping_an_existing_code_is_not_suggested(self):
        transcript = "\n".join(
            (
                "Teacher 1: Peer planning helped our curriculum team revise lessons together.",
                "Teacher 2: Peer collaboration improved our curriculum planning process.",
            )
        )
        units = extract_quote_units(parse_transcript(transcript, source_name="teachers.txt"), min_quote_words=4)
        first = units[0]
        partially_coded = ThemeQuote(
            quote_id="MQ01",
            speaker=first.speaker,
            text=first.text[5:30],
            relevance=1.0,
            source_line=first.source_line,
            source_name=first.source_name,
            source_start=first.source_start + 5,
            source_end=first.source_start + 30,
        )

        suggestions = find_similar_passages(
            (TranscriptDocument("teachers.txt", transcript),),
            result_with_quote(partially_coded),
            "T01",
            AnalysisSettings(),
        )

        self.assertEqual([item.speaker for item in suggestions.passages], ["Teacher 2"])

    @patch("themeforge.semantic_suggestions.local_embedding_vectors")
    def test_unavailable_local_embeddings_fall_back_to_tfidf(self, embedding_vectors):
        transcript = "\n".join(
            (
                "Teacher 1: Peer planning helped our curriculum team revise lessons together.",
                "Teacher 2: Peer collaboration improved our curriculum planning process.",
            )
        )
        units = extract_quote_units(parse_transcript(transcript, source_name="teachers.txt"), min_quote_words=4)
        coded = units[0]
        embedding_vectors.return_value = EmbeddingResult([], "Local embeddings unavailable.")

        suggestions = find_similar_passages(
            (TranscriptDocument("teachers.txt", transcript),),
            result_with_quote(
                ThemeQuote(
                    quote_id=coded.id,
                    speaker=coded.speaker,
                    text=coded.text,
                    relevance=0.9,
                    source_line=coded.source_line,
                    source_name=coded.source_name,
                    source_start=coded.source_start,
                    source_end=coded.source_end,
                )
            ),
            "T01",
            AnalysisSettings(semantic_backend="local_embeddings"),
        )

        self.assertEqual([item.speaker for item in suggestions.passages], ["Teacher 2"])
        self.assertIn("TF-IDF similarity", suggestions.note)
        self.assertEqual(suggestions.method_label, "TF-IDF fallback")

    @patch("themeforge.semantic_suggestions.local_embedding_vectors")
    def test_local_embedding_mode_ranks_by_cosine_and_reports_backend(self, embedding_vectors):
        transcript = "\n".join(
            (
                "Teacher 1: Peer planning helped our curriculum team revise lessons together.",
                "Teacher 2: Colleagues jointly redesigned units for student needs.",
                "Teacher 3: The parking lot was resurfaced during summer.",
            )
        )
        units = extract_quote_units(parse_transcript(transcript, source_name="teachers.txt"), min_quote_words=4)
        coded = units[0]
        result = result_with_quote(
            ThemeQuote(
                quote_id=coded.id,
                speaker=coded.speaker,
                text=coded.text,
                relevance=0.9,
                source_line=coded.source_line,
                source_name=coded.source_name,
                source_start=coded.source_start,
                source_end=coded.source_end,
            )
        )
        embedding_vectors.return_value = EmbeddingResult(
            vectors=[{"e0": 1.0}, {"e0": 0.9, "e1": 0.1}, {"e1": 1.0}],
            note="Local embedding semantic retrieval used.",
            model_name="sentence-transformers/all-MiniLM-L6-v2",
        )

        suggestions = find_similar_passages(
            (TranscriptDocument("teachers.txt", transcript),),
            result,
            "T01",
            AnalysisSettings(semantic_backend="local_embeddings"),
            quote_id=coded.id,
        )

        self.assertEqual([item.speaker for item in suggestions.passages], ["Teacher 2"])
        self.assertIn("Local embedding", suggestions.note)
        self.assertEqual(suggestions.method_label, "all-MiniLM-L6-v2")


if __name__ == "__main__":
    unittest.main()

import unittest

from themeforge import __version__
import themeforge.analysis as analysis
from themeforge.analysis import (
    AnalysisSettings,
    CodebookEntry,
    analyze_transcript,
    extract_quote_units,
    parse_transcript,
)
from themeforge.exports import export_markdown


SAMPLE_TRANSCRIPT = """
Participant 1: I felt isolated when online classes started. It was hard to ask questions without seeing classmates.
Participant 1: Small group meetings helped because peers explained assignments in simple language.
Participant 2: The teacher's weekly feedback made me feel supported and more confident about my writing.
Participant 2: I still needed clearer deadlines because the learning platform was confusing.
Participant 3: Working with classmates in breakout rooms helped me feel part of the course community.
Participant 3: I wanted more examples from the instructor before submitting big projects.
""".strip()


ASD_4H_TRANSCRIPT = """
Parent 1: Because my son is on the autism spectrum, the noise and crowds at the county fair made 4-H participation stressful. He wanted to show animals, but the judging area was too loud and unpredictable.
Parent 2: The biggest barrier was not the project itself. The barrier was transitions, waiting in line, and not knowing what would happen next during club meetings.
Volunteer 1: We had to train leaders to use visual schedules, short directions, and a calm break space. After that training, he could stay engaged much longer.
Youth 1: I liked the rabbit project because it was hands-on and I knew exactly what job I had to do. Cooking club was harder because everyone talked at once.
Parent 3: The small special interest club worked better than the large general club. The smaller 4-H setting gave him a predictable routine and fewer social demands.
Volunteer 2: His involvement changed over time. At first he watched from the side, then he helped set up supplies, and later he gave a short demonstration.
Parent 4: Peer buddies and extension staff gave support by checking in quietly, explaining rules ahead of time, and helping him take breaks before he melted down.
Youth 2: 4-H helped me feel proud because I could teach other kids about my project. I still needed help when plans changed suddenly.
""".strip()


class ThematicAnalysisTests(unittest.TestCase):
    def test_analyze_transcript_discovers_themes_with_ranked_quotes(self):
        result = analyze_transcript(
            SAMPLE_TRANSCRIPT,
            AnalysisSettings(theme_count=3, quotes_per_theme=2, min_theme_size=1),
        )

        self.assertEqual(result.version, __version__)
        self.assertGreaterEqual(len(result.themes), 2)
        self.assertLessEqual(len(result.themes), 3)

        all_theme_words = " ".join(
            [*(theme.name.lower() for theme in result.themes), *(keyword.lower() for theme in result.themes for keyword in theme.keywords)]
        )
        self.assertTrue(
            any(word in all_theme_words for word in ["peer", "classmates", "community", "group"])
        )
        self.assertTrue(
            any(word in all_theme_words for word in ["teacher", "instructor", "feedback", "support"])
        )

        for theme in result.themes:
            self.assertGreater(len(theme.quotes), 0)
            self.assertLessEqual(len(theme.quotes), 2)
            self.assertTrue(all(quote.speaker.startswith("Participant") for quote in theme.quotes))
            self.assertTrue(0.0 <= theme.score <= 1.0)

    def test_export_markdown_contains_theme_quote_and_researcher_warning(self):
        result = analyze_transcript(
            SAMPLE_TRANSCRIPT,
            AnalysisSettings(theme_count=2, quotes_per_theme=1, min_theme_size=1),
        )

        markdown = export_markdown(result)

        self.assertIn("# Qualitative Thematic Coding Report", markdown)
        self.assertIn("Researcher review required", markdown)
        self.assertIn("## Theme 1:", markdown)
        self.assertIn("Theme color:", markdown)
        self.assertIn("> ", markdown)
        self.assertIn("Participant", markdown)

    def test_large_transcript_uses_bounded_clustering_path(self):
        transcript = "\n".join(
            f"Participant {index % 8}: Online learning created cognitive load and required clearer support sentence {index}."
            for index in range(90)
        )

        result = analyze_transcript(
            transcript,
            AnalysisSettings(
                theme_count=4,
                quotes_per_theme=2,
                min_theme_size=1,
                agglomerative_limit=20,
            ),
        )

        self.assertEqual(result.quote_count, 90)
        self.assertLessEqual(len(result.themes), 4)
        self.assertTrue(any("bounded clustering" in note for note in result.notes))

    def test_autism_4h_corpus_uses_normal_clustering_path(self):
        result = analyze_transcript(
            ASD_4H_TRANSCRIPT,
            AnalysisSettings(theme_count=5, quotes_per_theme=1, min_theme_size=1),
        )

        self.assertTrue(result.themes)
        self.assertTrue(all(theme.id.startswith("T") for theme in result.themes))
        self.assertFalse(any("Contextual coding model used" in note for note in result.notes))
        labels = " ".join(theme.name.lower() for theme in result.themes)
        self.assertTrue(any(term in labels for term in ["barrier", "support", "training", "club", "project"]))

    def test_asd_codebook_requires_4h_context(self):
        transcript = """
Participant 1: Autism shaped my online learning because sensory overload made video calls difficult.
Participant 2: Neurodiversity support improved when teachers offered captions and quiet breaks.
Participant 3: Cognitive load was lower when instructions were predictable and available before class.
""".strip()

        result = analyze_transcript(
            transcript,
            AnalysisSettings(theme_count=3, quotes_per_theme=0, min_theme_size=1),
        )

        labels = {theme.name for theme in result.themes}
        self.assertNotIn("Barriers to 4-H participation", labels)
        self.assertNotIn("Useful hands-on 4-H activities", labels)
        self.assertTrue(all(theme.id.startswith("T") for theme in result.themes))
        self.assertFalse(any("Contextual coding model used" in note for note in result.notes))


if __name__ == "__main__":
    unittest.main()

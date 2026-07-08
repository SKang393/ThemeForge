import unittest

from themeforge.analysis import AnalysisSettings, CodebookEntry, analyze_transcript


class FocusAndCodebookTests(unittest.TestCase):
    def test_embedded_interviewer_prompts_do_not_become_themes_or_quotes(self):
        transcript = " ".join(
            [
                "Interviewer Person can you talk a little bit more about training?",
                "Participant: Training helped us design curriculum lessons with peer collaboration.",
                "Interviewer Person what else would help?",
                "Participant: Teachers needed protected planning time to adapt curriculum materials.",
                "Admin: Scheduling support made curriculum development easier for teachers.",
            ]
        )

        result = analyze_transcript(
            transcript,
            AnalysisSettings(theme_count=5, quotes_per_theme=2, min_theme_size=1, central_theme="curriculum"),
        )

        generated_text = " ".join(
            [
                *(theme.name for theme in result.themes),
                *(keyword for theme in result.themes for keyword in theme.keywords),
                *(quote.text for theme in result.themes for quote in theme.quotes),
            ]
        ).lower()
        self.assertGreater(result.quote_count, 0)
        self.assertNotIn("marie", generated_text)
        self.assertNotIn("david", generated_text)
        self.assertNotIn("little bit", generated_text)
        self.assertTrue(any("training" in theme.name.lower() or "training" in " ".join(theme.keywords).lower() for theme in result.themes))

    def test_focus_alignment_uses_contextual_relationships_not_only_exact_overlap(self):
        transcript = "\n".join(
            [
                "Participant 1: Curriculum development required shared lesson design, assessment planning, and standards alignment.",
                "Participant 2: Training gave teachers practical examples, planning routines, and design time.",
                "Participant 3: Peer collaboration helped teachers revise activities and share lesson materials.",
                "Participant 4: Admin scheduling gave teachers protected time for planning and revision.",
            ]
        )

        result = analyze_transcript(
            transcript,
            AnalysisSettings(theme_count=4, quotes_per_theme=1, min_theme_size=1, central_theme="curriculum"),
        )

        by_text = {
            " ".join([theme.name, *theme.keywords]).lower(): theme
            for theme in result.themes
        }
        training_theme = next(theme for text, theme in by_text.items() if "training" in text)
        peer_theme = next(theme for text, theme in by_text.items() if "peer" in text or "collaboration" in text)
        admin_theme = next(theme for text, theme in by_text.items() if "admin" in text or "scheduling" in text)

        self.assertGreater(training_theme.validation["central_theme_alignment"], 0.0)
        self.assertGreater(peer_theme.validation["central_theme_alignment"], 0.0)
        self.assertGreater(
            training_theme.validation["central_theme_alignment"],
            admin_theme.validation["central_theme_alignment"],
        )

    def test_imported_codebook_matches_quotes_to_researcher_themes(self):
        transcript = "\n".join(
            [
                "Participant 1: Training gave teachers practical examples for adapting curriculum activities.",
                "Participant 2: Scheduling support gave staff protected planning time for curriculum revision.",
                "Participant 3: Peer collaboration helped instructors share lesson materials across classrooms.",
            ]
        )

        result = analyze_transcript(
            transcript,
            AnalysisSettings(
                theme_count=3,
                quotes_per_theme=2,
                min_theme_size=1,
                codebook_entries=(
                    CodebookEntry(
                        name="Training Needs",
                        description="training examples staff capacity adapting curriculum activities",
                    ),
                    CodebookEntry(
                        name="Administrative Support",
                        description="scheduling protected planning time curriculum revision",
                    ),
                ),
            ),
        )

        by_name = {theme.name: theme for theme in result.themes}
        self.assertIn("Training Needs", by_name)
        self.assertIn("Administrative Support", by_name)
        self.assertIn("Training gave teachers", by_name["Training Needs"].quotes[0].text)
        self.assertIn("Scheduling support", by_name["Administrative Support"].quotes[0].text)
        self.assertEqual(by_name["Training Needs"].validation["review_status"], "Codebook match requires researcher review")


if __name__ == "__main__":
    unittest.main()

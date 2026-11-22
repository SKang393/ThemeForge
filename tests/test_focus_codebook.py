import unittest
from unicodedata import normalize

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

    def test_canonically_equivalent_hangul_matches_at_original_source_offsets(self):
        positive = "OK 🫧 꽃무늬 표시를 다시 확인했어요."
        negative = "지붕색 견본을 골라 벽에 붙였어요."
        for source_form, entry_form in (("NFD", "NFC"), ("NFC", "NFD")):
            with self.subTest(source_form=source_form):
                words = normalize(source_form, positive)
                prefix = f"Sol: {normalize(source_form, negative)}\nSol: "
                entry = CodebookEntry("Check", normalize(entry_form, "Include: 꽃무늬. Exclude: 지붕색."))
                result = analyze_transcript(prefix + words, AnalysisSettings(codebook_entries=(entry,)))
                theme, = result.themes
                self.assertEqual(theme.validation["generated_from_codebook_entry"], entry.snapshot())
                quote, = theme.quotes
                self.assertEqual((quote.text, quote.source_line, quote.source_start, quote.source_end),
                                 (words, 2, len(prefix), len(prefix) + len(words)))

    def test_exclusion_guidance_is_preserved_but_does_not_supply_search_terms(self):
        entry = CodebookEntry(
            "Trial", "Exercise the mechanism. Exclude: paint swatches and catalog prices. "
            "Include: repeat measurements.",
            ("Exclude: warranty paperwork lists what to include: copper washers. Include: inspect the pressure dial.",
             "Exclude: stock numbers.",
             "Use the plumb line."),
        )
        transcript = "\n".join([
            "Bench: We ordered paint swatches and catalog prices.",
            "Bench: Beside the paint swatches I did repeat measurements.",
            "Bench: I had to inspect the pressure dial.",
            "Bench: I did not exercise the mechanism during the evening shift.",
            "Bench: Filing the warranty paperwork took most of the morning.",
            "Bench: We copied the stock numbers from the cover.",
            "Bench: Under the stock numbers I hung the plumb line.",
            "Bench: We put the copper washers back in the drawer.",
        ])
        theme, = analyze_transcript(transcript, AnalysisSettings(codebook_entries=(entry,))).themes
        self.assertEqual({q.source_line for q in theme.quotes}, {2, 3, 4, 7})
        self.assertTrue(all("swatches" not in term and "prices" not in term for term in theme.keywords))
        self.assertEqual(entry.snapshot(), theme.validation["generated_from_codebook_entry"])
        self.assertIn("requires researcher review", theme.validation["review_status"])


if __name__ == "__main__":
    unittest.main()

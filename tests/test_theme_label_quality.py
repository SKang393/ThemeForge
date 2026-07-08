import unittest

from themeforge.analysis import AnalysisSettings, analyze_transcript


class ThemeLabelQualityTests(unittest.TestCase):
    def test_discourse_fillers_do_not_become_theme_labels(self):
        transcript = "\n".join(
            [
                "Participant 1: Yeah I think like the student loan system shaped whether I could stay in university.",
                "Participant 2: I think like interest on the student loan felt incompatible with my faith.",
                "Participant 3: Yeah like family support helped me continue studying during financial pressure.",
                "Participant 4: I think university support should explain alternative finance options more clearly.",
                "Participant 5: That's fine but I don't get why student finance options are unclear.",
            ]
        )

        result = analyze_transcript(
            transcript,
            AnalysisSettings(theme_count=2, quotes_per_theme=2, min_theme_size=1),
        )

        labels = {theme.name.lower() for theme in result.themes}
        keywords = {keyword.lower() for theme in result.themes for keyword in theme.keywords}
        self.assertNotIn("like", labels)
        self.assertNotIn("think", labels)
        self.assertNotIn("think", keywords)
        self.assertNotIn("think like", keywords)
        self.assertNotIn("that's", keywords)
        self.assertNotIn("don't", keywords)
        self.assertNotIn("get", keywords)
        self.assertTrue(any("student" in label or "loan" in label or "support" in label or "finance" in label for label in labels))

    def test_theme_labels_prefer_contextual_phrases_over_generic_words(self):
        transcript = "\n".join(
            [
                "Participant 1: I was a little bit nervous about curriculum planning before we had examples.",
                "Participant 2: We did a little bit of lesson design, but standards alignment made the work useful.",
                "Participant 3: People need training time to build curriculum activities together.",
                "Participant 4: The useful finding was that shared planning helped teachers revise lesson materials.",
            ]
        )

        result = analyze_transcript(
            transcript,
            AnalysisSettings(theme_count=4, quotes_per_theme=1, min_theme_size=1, central_theme="curriculum"),
        )

        labels = {theme.name.lower() for theme in result.themes}
        keywords = {keyword.lower() for theme in result.themes for keyword in theme.keywords}
        self.assertFalse({"little bit", "people", "need", "finding"} & labels)
        self.assertFalse(any(keyword.startswith("little bit") for keyword in keywords))
        self.assertTrue(
            any(
                "curriculum planning" in label
                or "lesson design" in label
                or "standards alignment" in label
                or "training time" in label
                or "shared planning" in label
                for label in labels
            )
        )

    def test_nonverbal_and_time_fillers_do_not_become_theme_labels(self):
        transcript = "\n".join(
            [
                "Participant 1: No, I sat there listening like mhm mhm mhm and everyone laughs.",
                "Participant 2: A couple weeks ago volunteers used curriculum activities to train peer mentors.",
                "Participant 3: Training volunteers helped youth participate in accessible project activities.",
                "Participant 4: Peer mentors adapted activities so youth could stay involved in the program.",
            ]
        )

        result = analyze_transcript(
            transcript,
            AnalysisSettings(theme_count=3, quotes_per_theme=1, min_theme_size=1, central_theme="curriculum"),
        )

        generated = " ".join(
            [
                *(theme.name for theme in result.themes),
                *(keyword for theme in result.themes for keyword in theme.keywords),
            ]
        ).lower()
        for filler in ["mhm", "laugh", "everyone laughs", "couple weeks ago", "kinda kinda"]:
            self.assertNotIn(filler, generated)

    def test_generic_conversational_phrases_do_not_become_theme_labels(self):
        transcript = "\n".join(
            [
                "Participant 1: Even though someone else was there, sensory overload made the day difficult.",
                "Participant 2: Direct communication helped classmates notice overload and offer quiet support.",
                "Participant 3: Social cues were easier when peers explained expectations with concrete examples.",
                "Participant 4: I have done better when teachers used visual schedules and predictable routines.",
                "Participant 5: Sometimes we couldn't use the activity, but accommodations helped youth participate.",
            ]
        )

        result = analyze_transcript(
            transcript,
            AnalysisSettings(theme_count=4, quotes_per_theme=1, min_theme_size=1),
        )

        generated = " ".join(
            [
                *(theme.name for theme in result.themes),
                *(keyword for theme in result.themes for keyword in theme.keywords),
            ]
        ).lower()
        for filler in ["even though", "someone else", "have done", "bad day", "sometimes", "couldn't"]:
            self.assertNotIn(filler, generated)

    def test_pause_only_pdf_turns_do_not_become_theme_labels(self):
        transcript = "\n".join(
            [
                "Interviewee 1: [2-second pause] Hmm. [7-second pause] Hmm!",
                "Interviewee 1: Kindness felt meaningful when friends understood sensory needs.",
                "Interviewee 1: Direct communication helped people offer support without guessing.",
                "Interviewee 1: Community acceptance made autistic identity feel less isolated.",
            ]
        )

        result = analyze_transcript(
            transcript,
            AnalysisSettings(theme_count=3, quotes_per_theme=1, min_theme_size=1),
        )
        generated = " ".join(
            [
                *(theme.name for theme in result.themes),
                *(keyword for theme in result.themes for keyword in theme.keywords),
                *(quote.text for theme in result.themes for quote in theme.quotes),
            ]
        ).lower()

        self.assertNotIn("pause", generated)
        self.assertNotIn("second pause", generated)

    def test_pdf_anonymization_placeholders_do_not_become_theme_labels(self):
        transcript = "\n".join(
            [
                "Interviewee 1: I was diagnosed when I was [a specified age under 10] years old.",
                "Interviewee 1: Kindness from close friends helped me manage sensory overload.",
                "Interviewee 1: Community acceptance supported autistic identity and direct communication.",
                "Interviewee 1: Friends noticed distress and offered practical emotional support.",
            ]
        )

        result = analyze_transcript(
            transcript,
            AnalysisSettings(theme_count=3, quotes_per_theme=1, min_theme_size=1),
        )
        generated = " ".join(
            [
                *(theme.name for theme in result.themes),
                *(keyword for theme in result.themes for keyword in theme.keywords),
            ]
        ).lower()

        self.assertNotIn("specified", generated)
        self.assertNotIn("specified age", generated)

    def test_ctfidf_phrase_labels_override_fixed_theme_hints(self):
        transcript = "\n".join(
            [
                "Participant 1: Peer reflection circles helped students repair conflict after lunch. Peer reflection circles gave students language for apology.",
                "Participant 2: Peer reflection circles made discipline feel fair because classmates heard each other.",
                "Participant 3: Family text messages helped attendance because caregivers saw schedule changes quickly.",
                "Participant 4: Family text messages clarified homework routines before students came home.",
            ]
        )

        result = analyze_transcript(
            transcript,
            AnalysisSettings(theme_count=2, quotes_per_theme=1, min_theme_size=1),
        )

        labels = {theme.name.lower() for theme in result.themes}
        keywords = {keyword.lower() for theme in result.themes for keyword in theme.keywords}
        self.assertTrue("peer reflection circles" in labels or "peer reflection circles" in keywords)
        self.assertTrue("family text messages" in labels or "family text messages" in keywords)
        self.assertNotIn("peer collaboration", labels)

    def test_pdf_extraction_artifacts_do_not_become_theme_labels(self):
        transcript = "\n".join(
            [
                "Page 1: P03 SoI needed captions because cognitive load increased during online lectures.",
                "Page 2: P10 AndI used recorded lectures when accessibility features were missing.",
                "Page 3: P11 Andthenit helped when instructors gave predictable instructions before class.",
                "Page 4: P12 Neurodiversity support was strongest when quiet breaks were available.",
            ]
        )

        result = analyze_transcript(
            transcript,
            AnalysisSettings(
                theme_count=3,
                quotes_per_theme=1,
                min_theme_size=1,
            ),
        )

        labels = {theme.name.lower() for theme in result.themes}
        keywords = {keyword.lower() for theme in result.themes for keyword in theme.keywords}
        self.assertFalse({"p03", "p10", "p11", "p12", "soi", "andi", "andthenit"} & labels)
        self.assertFalse(
            any(
                keyword.startswith(("p03", "p10", "p11", "p12", "soi", "andi", "andthenit"))
                for keyword in keywords
            )
        )
        self.assertTrue(
            any(
                "accessibility" in label
                or "cognitive" in label
                or "neurodiversity" in label
                or "support" in label
                for label in labels
            )
        )

        no_hint_transcript = "\n".join(
            [
                "Page 1: P03 SoI described overload during seminars and advising.",
                "Page 2: P10 AndI described overload during seminars and advising.",
                "Page 3: P11 Andthenit described overload during seminars and advising.",
            ]
        )
        no_hint_result = analyze_transcript(
            no_hint_transcript,
            AnalysisSettings(theme_count=2, quotes_per_theme=1, min_theme_size=1),
        )
        no_hint_labels = {theme.name.lower() for theme in no_hint_result.themes}
        no_hint_keywords = {keyword.lower() for theme in no_hint_result.themes for keyword in theme.keywords}
        self.assertFalse({"p03", "p10", "p11", "soi", "andi", "andthenit"} & no_hint_labels)
        self.assertFalse(
            any(
                keyword.startswith(("p03", "p10", "p11", "soi", "andi", "andthenit"))
                for keyword in no_hint_keywords
            )
        )


if __name__ == "__main__":
    unittest.main()

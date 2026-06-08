import unittest

from themeforge import __version__
import themeforge.analysis as analysis
from themeforge.analysis import (
    AnalysisSettings,
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


class TranscriptParsingTests(unittest.TestCase):
    def test_parse_transcript_preserves_speaker_labels(self):
        segments = parse_transcript(SAMPLE_TRANSCRIPT)

        self.assertEqual(len(segments), 6)
        self.assertEqual(segments[0].speaker, "Participant 1")
        self.assertIn("online classes", segments[0].text)
        self.assertEqual(segments[-1].speaker, "Participant 3")

    def test_extract_quote_units_splits_long_speaker_turns(self):
        transcript = "P1: Online class was difficult. Peer meetings helped me continue."

        quotes = extract_quote_units(parse_transcript(transcript))

        self.assertEqual([quote.text for quote in quotes], [
            "Online class was difficult.",
            "Peer meetings helped me continue.",
        ])
        self.assertEqual({quote.speaker for quote in quotes}, {"P1"})


class ThematicAnalysisTests(unittest.TestCase):
    def test_analyze_transcript_discovers_themes_with_ranked_quotes(self):
        result = analyze_transcript(
            SAMPLE_TRANSCRIPT,
            AnalysisSettings(theme_count=3, quotes_per_theme=2, min_theme_size=1),
        )

        self.assertEqual(result.version, __version__)
        self.assertGreaterEqual(len(result.themes), 2)
        self.assertLessEqual(len(result.themes), 3)

        all_theme_words = " ".join(theme.name.lower() for theme in result.themes)
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
        self.assertTrue(any("student" in label or "loan" in label or "support" in label for label in labels))

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

    def test_contextual_4h_asd_codes_are_specific_and_multi_quote(self):
        result = analyze_transcript(
            ASD_4H_TRANSCRIPT,
            AnalysisSettings(theme_count=8, quotes_per_theme=0, min_theme_size=1),
        )

        labels = [theme.name for theme in result.themes]
        expected_labels = {
            "ASD shapes 4-H participation",
            "Barriers to 4-H participation",
            "Sensory and transition challenges",
            "Leader training and accommodations",
            "Useful hands-on 4-H activities",
            "Smaller clubs support inclusion",
            "Gradual involvement builds confidence",
            "Peer and staff support strategies",
        }

        self.assertTrue(expected_labels.issubset(set(labels)))
        for label in expected_labels:
            word_count = len(label.split())
            self.assertTrue(4 <= word_count <= 6, label)

        by_name = {theme.name: theme for theme in result.themes}
        self.assertTrue(all(theme.color.startswith("#") for theme in result.themes))
        self.assertGreaterEqual(len(by_name["Barriers to 4-H participation"].quotes), 3)
        self.assertGreaterEqual(len(by_name["Peer and staff support strategies"].quotes), 2)
        self.assertGreaterEqual(len(by_name["Useful hands-on 4-H activities"].quotes), 2)
        self.assertTrue(
            any(
                "checking in quietly" in quote.text
                for quote in by_name["Peer and staff support strategies"].quotes
            )
        )
        self.assertTrue(
            any(
                "visual schedules" in quote.text
                for quote in by_name["Leader training and accommodations"].quotes
            )
        )

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


class ValidationAndMultiFileTests(unittest.TestCase):
    def _settings_with_central_theme(self, central_theme: str) -> AnalysisSettings:
        if "central_theme" not in AnalysisSettings.__dataclass_fields__:
            self.fail("AnalysisSettings.central_theme is required for focus-theme analysis")
        return AnalysisSettings(
            theme_count=4,
            quotes_per_theme=0,
            min_theme_size=1,
            central_theme=central_theme,
        )

    def _documents(self, *items: tuple[str, str]):
        self.assertTrue(hasattr(analysis, "TranscriptDocument"), "TranscriptDocument is required")
        return [analysis.TranscriptDocument(name=name, text=text) for name, text in items]

    def test_analyze_documents_preserves_file_sources_and_validation_metadata(self):
        self.assertTrue(hasattr(analysis, "analyze_documents"), "analyze_documents is required")
        documents = self._documents(
            (
                "teacher_focus_group.txt",
                """
Teacher A: The accessibility issue was that captions were missing, so students waited for peers to explain the video.
Teacher B: I needed clearer accessibility guidance from the district before adapting assignments.
""".strip(),
            ),
            (
                "parent_interview.txt",
                """
Parent A: Accessibility mattered at home because my child could not follow the online platform without repeated support.
Parent B: Family workload increased when the school website changed every week.
""".strip(),
            ),
        )

        result = analysis.analyze_documents(
            documents,
            self._settings_with_central_theme("accessibility support"),
        )

        self.assertEqual(result.document_count, 2)
        self.assertGreaterEqual(result.quote_count, 4)
        self.assertTrue(hasattr(result, "validation_summary"))
        self.assertTrue(any("researcher review" in note.lower() for note in result.validation_summary))
        self.assertTrue(any("central theme" in note.lower() for note in result.notes))

        quoted_sources = {
            quote.source_name
            for theme in result.themes
            for quote in theme.quotes
        }
        self.assertIn("teacher_focus_group.txt", quoted_sources)
        self.assertIn("parent_interview.txt", quoted_sources)
        self.assertTrue(all(theme.validation["evidence_count"] >= len(theme.quotes) for theme in result.themes))
        self.assertTrue(all("review_status" in theme.validation for theme in result.themes))

    def test_central_theme_moves_matching_quotes_to_top_theme(self):
        self.assertTrue(hasattr(analysis, "analyze_documents"), "analyze_documents is required")
        documents = self._documents(
            (
                "mixed_focus_group.txt",
                """
Participant 1: Transportation barriers made attendance inconsistent because the bus schedule changed often.
Participant 2: Teacher perspective matters because teachers noticed students needed accessible instructions before activities.
Participant 3: Lunch choices were popular and students liked having familiar food during events.
Participant 4: From the teacher perspective, accessibility meant giving families visual schedules and quiet choices.
""".strip(),
            )
        )

        result = analysis.analyze_documents(
            documents,
            self._settings_with_central_theme("teacher perspective accessibility"),
        )

        self.assertGreater(result.themes, [])
        top_theme = result.themes[0]
        joined = " ".join([top_theme.name, *top_theme.keywords]).lower()
        self.assertTrue("teacher" in joined or "accessibility" in joined)
        self.assertTrue(
            any("teacher perspective" in quote.text.lower() for quote in top_theme.quotes),
            top_theme.quotes,
        )

    def test_export_markdown_contains_color_coded_transcript_evidence_and_sources(self):
        self.assertTrue(hasattr(analysis, "analyze_documents"), "analyze_documents is required")
        result = analysis.analyze_documents(
            self._documents(
                (
                    "focus_group_a.txt",
                    "P1: Peer support helped me interpret confusing directions. P2: Instructor feedback clarified the next assignment.",
                )
            ),
            self._settings_with_central_theme("peer support"),
        )

        markdown = export_markdown(result)

        self.assertIn("## Validation Framework", markdown)
        self.assertIn("## Color-Coded Transcript Evidence", markdown)
        self.assertIn("Source: focus_group_a.txt", markdown)
        self.assertIn("Theme color:", markdown)
        self.assertRegex(markdown, r"\[[CT]\d{2} #[0-9a-f]{6}\]")

    def test_korean_transcript_produces_korean_keywords_and_quotes(self):
        self.assertTrue(hasattr(analysis, "analyze_documents"), "analyze_documents is required")
        result = analysis.analyze_documents(
            self._documents(
                (
                    "korean_focus_group.txt",
                    """
참여자 1: 온라인 수업에서 접근성이 부족해서 자막과 쉬운 설명이 필요했습니다.
참여자 2: 부모 부담은 과제 안내가 자주 바뀔 때 커졌고 가족이 계속 도와야 했습니다.
참여자 3: 선생님 지원은 시각 자료와 반복 설명이 있을 때 가장 도움이 되었습니다.
""".strip(),
                )
            ),
            self._settings_with_central_theme("접근성 부모 부담"),
        )

        self.assertGreater(result.quote_count, 0)
        joined_keywords = " ".join(keyword for theme in result.themes for keyword in theme.keywords)
        self.assertRegex(joined_keywords, r"[가-힣]")
        self.assertTrue(
            any("접근성" in quote.text or "부모 부담" in quote.text for theme in result.themes for quote in theme.quotes)
        )


if __name__ == "__main__":
    unittest.main()

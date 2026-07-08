import unittest

import themeforge.analysis as analysis
from themeforge.analysis import AnalysisSettings
from themeforge.exports import export_markdown
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



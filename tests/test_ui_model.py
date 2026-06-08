import unittest
from pathlib import Path

from themeforge.ui_model import (
    PROFESSIONAL_THEME,
    analysis_status_text,
    file_selection_summary,
    format_validation_summary,
    theme_list_label,
)


class UiModelTests(unittest.TestCase):
    def test_file_selection_summary_is_compact_for_professional_header(self):
        self.assertEqual(file_selection_summary([]), "No transcripts selected")
        self.assertEqual(file_selection_summary([Path("teacher.txt")]), "teacher.txt")
        self.assertEqual(
            file_selection_summary([Path("teacher.txt"), Path("parent.txt"), Path("student.txt")]),
            "3 transcripts selected",
        )

    def test_analysis_status_text_is_short_and_scannable(self):
        status = analysis_status_text(document_count=3, quote_count=42, theme_count=6)

        self.assertEqual(status, "3 documents | 42 quote units | 6 themes")
        self.assertLessEqual(len(status), 48)

    def test_theme_label_and_validation_summary_are_review_focused(self):
        label = theme_list_label("Accessibility Support", "#2563eb", 12, 0.72)
        validation = format_validation_summary(
            {
                "evidence_count": 12,
                "source_count": 3,
                "speaker_count": 7,
                "central_theme_alignment": 0.72,
                "review_status": "Researcher review required",
            }
        )

        self.assertEqual(label, "Accessibility Support  |  12 quotes  |  72% focus")
        self.assertEqual(
            validation,
            "Evidence 12 | Sources 3 | Speakers 7 | Focus 72% | Researcher review required",
        )

    def test_professional_theme_uses_minimal_neutral_surface(self):
        self.assertEqual(PROFESSIONAL_THEME["background"], "#f7f8fa")
        self.assertEqual(PROFESSIONAL_THEME["surface"], "#ffffff")
        self.assertEqual(PROFESSIONAL_THEME["text"], "#1f2937")
        self.assertNotIn("gradient", " ".join(PROFESSIONAL_THEME.values()).lower())


if __name__ == "__main__":
    unittest.main()

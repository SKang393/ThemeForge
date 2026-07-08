from pathlib import Path
import tempfile
import unittest

from themeforge.analysis import (
    AnalysisResult,
    AnalysisSettings,
    CodebookEntry,
    Theme,
    ThemeQuote,
    TranscriptDocument,
)
from themeforge.project_io import ProjectState, load_project, save_project


class ProjectIoTests(unittest.TestCase):
    def test_project_round_trips_documents_settings_codebook_and_manual_result(self):
        quote = ThemeQuote(
            quote_id="Q1",
            speaker="Participant",
            text="Manual coding should be saved.",
            relevance=0.9,
            source_line=3,
            source_name="interview.txt",
            rationale="Researcher edited.",
            source_start=12,
            source_end=43,
            memo="Quote memo",
        )
        result = AnalysisResult(
            version="1.3.0",
            document_count=1,
            quote_count=1,
            themes=[
                Theme(
                    id="T01",
                    name="Manual Theme",
                    color="#2563eb",
                    keywords=["manual", "coding"],
                    quote_count=1,
                    score=0.9,
                    quotes=[quote],
                    validation={"evidence_count": 1, "review_status": "Researcher edited"},
                    memo="Theme memo",
                )
            ],
            notes=["saved project"],
            validation_summary=["review required"],
        )
        state = ProjectState(
            transcript_paths=(Path("interview.txt"),),
            codebook_path=Path("codebook.csv"),
            documents=(TranscriptDocument(name="interview.txt", text="Cached transcript text", memo="Document memo"),),
            settings=AnalysisSettings(
                theme_count=6,
                quotes_per_theme=0,
                central_theme="curriculum",
                codebook_entries=(CodebookEntry("Training", "How training appears", ("example",)),),
            ),
            result=result,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "study.tfproj"
            save_project(path, state)

            loaded = load_project(path)

        self.assertEqual(loaded.transcript_paths, (Path("interview.txt"),))
        self.assertEqual(loaded.codebook_path, Path("codebook.csv"))
        self.assertEqual(loaded.documents[0].text, "Cached transcript text")
        self.assertEqual(loaded.documents[0].memo, "Document memo")
        self.assertEqual(loaded.settings.theme_count, 6)
        self.assertEqual(loaded.settings.codebook_entries[0].examples, ("example",))
        self.assertIsNotNone(loaded.result)
        self.assertEqual(loaded.result.themes[0].name, "Manual Theme")
        self.assertEqual(loaded.result.themes[0].memo, "Theme memo")
        self.assertEqual(loaded.result.themes[0].quotes[0].source_start, 12)
        self.assertEqual(loaded.result.themes[0].quotes[0].memo, "Quote memo")


if __name__ == "__main__":
    unittest.main()

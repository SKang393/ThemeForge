from pathlib import Path
import json
import tempfile
import unittest
import zipfile

from themeforge.analysis import (
    AnalysisResult,
    AnalysisSettings,
    CodebookEntry,
    Theme,
    ThemeQuote,
    TranscriptDocument,
)
from themeforge.audit import AuditEvent
from themeforge.project_io import ProjectState, UnsupportedProjectFormatError, load_project, save_project


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
                    parent_theme_id="T00",
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
                semantic_backend="local_embeddings",
                language_mode="Multilingual",
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
        self.assertEqual(loaded.settings.semantic_backend, "local_embeddings")
        self.assertEqual(loaded.settings.language_mode, "Multilingual")
        self.assertEqual(loaded.settings.codebook_entries[0].examples, ("example",))
        self.assertIsNotNone(loaded.result)
        self.assertEqual(loaded.result.themes[0].name, "Manual Theme")
        self.assertEqual(loaded.result.themes[0].memo, "Theme memo")
        self.assertEqual(loaded.result.themes[0].parent_theme_id, "T00")
        self.assertEqual(loaded.result.themes[0].quotes[0].source_start, 12)
        self.assertEqual(loaded.result.themes[0].quotes[0].memo, "Quote memo")

    def test_load_project_defaults_v1_researcher_and_audit_fields(self):
        payload = {
            "format_version": 1,
            "transcript_paths": ["interview.txt"],
            "codebook_path": None,
            "documents": [],
            "settings": {},
            "result": None,
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "v1.tfproj"
            with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("project.json", json.dumps(payload))

            loaded = load_project(path)

        self.assertEqual(loaded.researcher_name, "")
        self.assertEqual(loaded.audit_events, ())

    def test_project_round_trips_v2_researcher_and_audit_events_as_utf8(self):
        event = AuditEvent(
            timestamp_utc="2026-01-02T03:04:05Z",
            actor="김민지",
            action="code_applied",
            target_type="quote",
            target_id="Q1",
            details="세부 내용, comma\n다음 줄",
        )
        state = ProjectState(
            transcript_paths=(Path("interview.txt"),),
            codebook_path=None,
            documents=(),
            settings=AnalysisSettings(),
            researcher_name="Dr. 한",
            audit_events=(event,),
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "study.tfproj"
            save_project(path, state)

            with zipfile.ZipFile(path) as archive:
                payload = json.loads(archive.read("project.json").decode("utf-8"))
            loaded = load_project(path)

        self.assertEqual(payload["format_version"], 2)
        self.assertEqual(loaded.researcher_name, "Dr. 한")
        self.assertEqual(loaded.audit_events, (event,))

    def test_load_project_rejects_future_format(self):
        payload = {"format_version": 99, "settings": {}}

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "future.tfproj"
            with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("project.json", json.dumps(payload))

            with self.assertRaises(UnsupportedProjectFormatError) as raised:
                load_project(path)

        self.assertIn("format 99", str(raised.exception))

    def test_load_project_rejects_malformed_format_with_typed_error(self):
        payload = {"format_version": "99x", "settings": {}}

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "malformed.tfproj"
            with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("project.json", json.dumps(payload))

            with self.assertRaises(UnsupportedProjectFormatError) as raised:
                load_project(path)

        self.assertIn("99x", str(raised.exception))


if __name__ == "__main__":
    unittest.main()

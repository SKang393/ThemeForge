import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from themeforge.analysis_types import AnalysisResult, Theme, ThemeQuote, TranscriptDocument


def _write_docx(path: Path, document_xml: str, comments_xml: str | None = None) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", document_xml)
        if comments_xml is not None:
            archive.writestr("word/comments.xml", comments_xml)


class ValidationHarnessTests(unittest.TestCase):
    def test_validation_harness_reports_core_metrics(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            transcript_dir = root / "Adult day service curriculum" / "Interview Sessions" / "DSPs" / "DSP 1"
            transcript_dir.mkdir(parents=True)
            transcript = transcript_dir / "sample.txt"
            transcript.write_text(
                "\n".join(
                    [
                        "marie  0:03",
                        "The goal of this interview is to understand curriculum strengths.",
                        "",
                        "DSP 1  0:22",
                        "Training helped staff adapt curriculum activities for emerging adults.",
                        "Peer planning helped instructors choose useful community lessons.",
                    ]
                ),
                encoding="utf-8",
            )
            codebook = root / "Adult day service curriculum" / "Interview Sessions" / "Combined code book.docx"
            codebook.write_text(
                "\n".join(
                    [
                        "Training Needs of Stakeholders",
                        "Training helped staff adapt curriculum activities for emerging adults.",
                        "Curriculum Development",
                        "Peer planning helped instructors choose useful community lessons.",
                    ]
                ),
                encoding="utf-8",
            )

            completed = subprocess.run(
                [
                    sys.executable,
                    "scripts/validate_against_codebooks.py",
                    "--root",
                    str(root),
                    "--themes",
                    "2",
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(completed.returncode, 1, completed.stderr)
            self.assertIn("Dataset: Adult day service curriculum", completed.stdout)
            self.assertIn("Dataset: Familiarity and perceptions of preference assessment", completed.stdout)
            self.assertIn("Inputs skipped: 0", completed.stdout)
            self.assertIn("Parsing sanity:", completed.stdout)
            self.assertIn("Transcript artifact quote units: 0", completed.stdout)
            self.assertIn("Known leak terms: none", completed.stdout)
            self.assertIn("Theme coverage:", completed.stdout)
            self.assertIn("Codebook-assisted coverage:", completed.stdout)
            self.assertIn("Codebook entries: 5", completed.stdout)
            self.assertIn("Example quote token matches:", completed.stdout)
            self.assertIn("Validation failures:", completed.stdout)
            self.assertIn("4-H: no analyzable transcripts found", completed.stdout)

    def test_validation_harness_fails_when_all_datasets_are_missing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            completed = subprocess.run(
                [sys.executable, "scripts/validate_against_codebooks.py", "--root", tmpdir],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(completed.returncode, 1)
        self.assertEqual(completed.stdout.count("Status: no analyzable transcripts found"), 4)

    def test_name_leak_detection_uses_token_boundaries(self):
        from scripts.validate_against_codebooks import _clean_coder_label, _clean_theme_label, find_name_leaks

        self.assertEqual(
            find_name_leaks("MD and DRM worked with Samira and the moderator."),
            ["drm", "md", "moderator", "samira"],
        )
        self.assertEqual(find_name_leaks("The response was immediately useful."), [])
        self.assertEqual(_clean_theme_label("[Consensus]"), "")
        self.assertEqual(_clean_theme_label("retrieving data. wait a few seconds and try to cut or copy again."), "")
        self.assertEqual(_clean_coder_label("CA: Staff training", "Coder Alpha"), "Staff training")

    def test_table_ground_truth_supports_standard_and_comment_scope_headers(self):
        from scripts.validate_against_codebooks import load_table_ground_truth

        document = TranscriptDocument(
            name="P1",
            text="P1: Shared planning helped staff.\nP1: Choice making supported children.",
        )
        document_xml = (
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
            '<w:tbl><w:tr><w:tc><w:p><w:r><w:t>Pt</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>Text</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>Theme</w:t></w:r></w:p></w:tc></w:tr>'
            '<w:tr><w:tc><w:p><w:r><w:t>P1</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>Shared planning helped staff.</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>Support</w:t></w:r></w:p></w:tc></w:tr></w:tbl>'
            '<w:tbl><w:tr><w:tc><w:p><w:r><w:t>Pt</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>Comment Scope</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>Comment Text</w:t></w:r></w:p></w:tc></w:tr>'
            '<w:tr><w:tc><w:p><w:r><w:t>P1</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>Choice making supported children.</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>Choice</w:t></w:r></w:p></w:tc></w:tr></w:tbl>'
            '</w:body></w:document>'
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "coding.docx"
            _write_docx(path, document_xml)

            spans = load_table_ground_truth(path, document)

        self.assertEqual([(span.theme_name, span.resolved) for span in spans], [("Support", True), ("Choice", True)])
        self.assertTrue(all(document.text[span.source_start : span.source_end] == span.text for span in spans))

    def test_table_ground_truth_anchors_passage_with_edited_middle(self):
        from scripts.validate_against_codebooks import load_table_ground_truth

        source = "Start alpha beta gamma delta one two three changed words seven eight nine omega finish."
        document = TranscriptDocument(name="P1", text=source)
        document_xml = (
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:tbl>'
            '<w:tr><w:tc><w:p><w:r><w:t>Text</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>Theme</w:t></w:r></w:p></w:tc></w:tr>'
            '<w:tr><w:tc><w:p><w:r><w:t>alpha beta gamma delta one two three omitted text seven eight nine omega</w:t></w:r></w:p></w:tc>'
            '<w:tc><w:p><w:r><w:t>Support</w:t></w:r></w:p></w:tc></w:tr>'
            '</w:tbl></w:body></w:document>'
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "coding.docx"
            _write_docx(path, document_xml)
            span = load_table_ground_truth(path, document)[0]

        self.assertTrue(span.resolved)
        self.assertEqual(span.text, "alpha beta gamma delta one two three changed words seven eight nine omega")

    def test_comment_ground_truth_preserves_author_theme_and_offsets(self):
        from scripts.validate_against_codebooks import load_commented_ground_truth

        document_xml = (
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p>'
            '<w:r><w:t>P1: </w:t></w:r><w:commentRangeStart w:id="0"/><w:r><w:t>Shared planning helped staff.</w:t></w:r>'
            '<w:commentRangeEnd w:id="0"/><w:r><w:commentReference w:id="0"/></w:r></w:p></w:body></w:document>'
        )
        comments_xml = (
            '<w:comments xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            '<w:comment w:id="0" w:author="Coder A"><w:p><w:r><w:t>Support</w:t></w:r></w:p></w:comment>'
            '</w:comments>'
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "coded.docx"
            _write_docx(path, document_xml, comments_xml)

            document, spans = load_commented_ground_truth(path, "Shared", author="Coder A")

        self.assertEqual(document.text, "P1: Shared planning helped staff.")
        self.assertEqual(len(spans), 1)
        self.assertEqual((spans[0].theme_name, spans[0].author, spans[0].resolved), ("Support", "Coder A", True))
        self.assertEqual(document.text[spans[0].source_start : spans[0].source_end], spans[0].text)

    def test_quote_metrics_report_one_to_one_precision_and_recall(self):
        from scripts.validate_against_codebooks import CodedSpan, quote_metrics

        ground_truth = (
            CodedSpan("Support", "Shared", "Alpha", 0, 5),
            CodedSpan("Support", "Shared", "Bravo", 10, 15),
        )
        predictions = [
            ThemeQuote("Q1", "P1", "Alpha", 1.0, 1, "Shared", source_start=0, source_end=5),
            ThemeQuote("Q2", "P1", "Charlie", 1.0, 1, "Shared", source_start=20, source_end=27),
        ]
        result = AnalysisResult(
            version="test",
            document_count=1,
            quote_count=2,
            themes=[Theme("T1", "support", "#000000", [], 2, 1.0, predictions)],
            notes=[],
        )

        metric = quote_metrics(ground_truth, result)[0]

        self.assertEqual((metric.true_positive, metric.false_positive, metric.false_negative), (1, 1, 1))
        self.assertEqual((metric.precision, metric.recall), (0.5, 0.5))

    def test_quote_metrics_use_best_theme_match_and_maximum_overlap_matching(self):
        from scripts.validate_against_codebooks import CodedSpan, quote_metrics

        ground_truth = (
            CodedSpan("Staff Support", "Shared", "Alpha", 0, 10),
            CodedSpan("Staff Support", "Shared", "Bravo", 10, 20),
        )
        predictions = [
            ThemeQuote("Q1", "P1", "Bridge", 1.0, 1, "Shared", source_start=5, source_end=15),
            ThemeQuote("Q2", "P1", "Alpha", 1.0, 1, "Shared", source_start=0, source_end=5),
        ]
        result = AnalysisResult(
            version="test",
            document_count=1,
            quote_count=2,
            themes=[Theme("T1", "Support for Staff", "#000000", [], 2, 1.0, predictions)],
            notes=[],
        )

        metric = quote_metrics(ground_truth, result)[0]

        self.assertEqual((metric.true_positive, metric.false_positive, metric.false_negative), (2, 0, 0))

    def test_quote_metrics_do_not_map_zero_similarity_theme(self):
        from scripts.validate_against_codebooks import CodedSpan, quote_metrics

        ground_truth = (CodedSpan("Support", "Shared", "Alpha", 0, 5),)
        result = AnalysisResult(
            version="test",
            document_count=1,
            quote_count=1,
            themes=[
                Theme(
                    "T1",
                    "Unrelated",
                    "#000000",
                    [],
                    1,
                    1.0,
                    [ThemeQuote("Q1", "P1", "Alpha", 1.0, 1, "Shared", source_start=0, source_end=5)],
                )
            ],
            notes=[],
        )

        metric = quote_metrics(ground_truth, result)[0]

        self.assertEqual((metric.true_positive, metric.false_positive, metric.false_negative), (0, 0, 1))

    def test_quote_validation_codebook_does_not_reuse_ground_truth_passages(self):
        from scripts.validate_against_codebooks import CodedSpan, quote_validation_entries

        entries = quote_validation_entries(
            (
                CodedSpan("Support", "Shared", "private coded passage", 0, 21),
                CodedSpan("Training", "Shared", "another private passage", 22, 45),
            )
        )

        self.assertEqual([entry.name for entry in entries], ["Support", "Training"])
        self.assertTrue(all(entry.examples == () for entry in entries))
        self.assertNotIn("private coded passage", " ".join(entry.description for entry in entries))

    def test_summary_theme_parser_preserves_and_inside_single_theme(self):
        from scripts.validate_against_codebooks import extract_summary_themes

        self.assertEqual(
            extract_summary_themes(
                "The analysis identified three GETs for the group: The Autistic Experience, Rules (Should) Rule, and Genuine Kindness."
            ),
            ("The Autistic Experience", "Rules (Should) Rule", "Genuine Kindness"),
        )
        self.assertEqual(
            extract_summary_themes(
                "The analysis identified a single PET for Interviewee 8: Going Slightly Altruistically Above and Beyond Normative Requirements."
            ),
            ("Going Slightly Altruistically Above and Beyond Normative Requirements",),
        )

    def test_mccormick_table_parser_reads_published_theme_column(self):
        from scripts.validate_against_codebooks import extract_mccormick_themes_from_layout

        layout = "\n".join(
            [
                "TABLE 2 | Results: Themes and subthemes.",
                "Theme" + " " * 80 + "Subtheme",
                "Enrollment process and barriers" + " " * 40 + "Autistic participation",
                "Benefits" + " " * 70 + "Skill development",
                " " * 58 + "New Trainings",
            ]
        )

        self.assertEqual(
            extract_mccormick_themes_from_layout(layout),
            ("Enrollment process and barriers", "Benefits"),
        )

    def test_mccormick_table_labels_include_first_and_indented_subthemes(self):
        from scripts.validate_against_codebooks import mccormick_table_labels

        labels = mccormick_table_labels(
            ("Enrollment process and barriers", "Benefits"),
            (
                (44.8, "Enrollment process and barriers Autistic participation"),
                (207.8, "Getting involved"),
                (207.8, "4-H culture"),
                (44.8, "Benefits Skill development"),
                (207.8, "Personal development"),
            ),
        )

        self.assertEqual(
            labels,
            (
                "Enrollment process and barriers",
                "Autistic participation",
                "Getting involved",
                "4-H culture",
                "Benefits",
                "Skill development",
                "Personal development",
            ),
        )

    def test_published_theme_extraction_rejects_missing_pdf_inputs(self):
        from scripts.validate_against_codebooks import DATASETS, published_theme_names

        with tempfile.TemporaryDirectory() as tmpdir:
            four_h = next(spec for spec in DATASETS if spec.name == "4-H")
            autism = next(spec for spec in DATASETS if spec.name == "Autism and kindness")

            with self.assertRaisesRegex(ValueError, "missing published paper"):
                published_theme_names(four_h, Path(tmpdir))
            with self.assertRaisesRegex(ValueError, "expected 1 GET and 10 PET PDFs"):
                published_theme_names(autism, Path(tmpdir))

    def test_reliability_validation_rejects_empty_real_report(self):
        from scripts.validate_against_codebooks import reliability_report_failures
        from themeforge.rigor import IntercoderReliabilityReport

        report = IntercoderReliabilityReport("Coder A", "Coder B", 0, (), ())

        self.assertEqual(
            reliability_report_failures("Study", report),
            (
                "Study: no shared meaning units",
                "Study: no coded themes",
                "Study: no defined kappa values",
                "Study: no prevalence warnings",
                "Study: no disagreements",
            ),
        )

    def test_coded_project_round_trip_preserves_offsets_and_coder(self):
        from scripts.validate_against_codebooks import CodedSpan, round_trip_coded_project

        document = TranscriptDocument("Shared", "P1: Shared planning helped staff.")
        span = CodedSpan("Support", "Shared", "Shared planning", 4, 19, "Coder A")
        with tempfile.TemporaryDirectory() as tmpdir:
            project_path = Path(tmpdir) / "coder-a.tfproj"
            project = round_trip_coded_project(project_path, (document,), (span,), "Coder A")

        quote = project.result.themes[0].quotes[0]
        self.assertEqual((quote.source_name, quote.source_start, quote.source_end), ("Shared", 4, 19))
        self.assertEqual(project.documents, (document,))


if __name__ == "__main__":
    unittest.main()

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class CliTests(unittest.TestCase):
    def test_cli_accepts_multiple_inputs_and_central_theme(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            teacher = root / "teacher.txt"
            parent = root / "parent.txt"
            report = root / "report.md"
            teacher.write_text(
                "Teacher: Accessibility improved when visual schedules made each activity predictable.\n",
                encoding="utf-8",
            )
            parent.write_text(
                "Parent: Parent burden increased when online directions changed without warning.\n",
                encoding="utf-8",
            )

            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "themeforge.cli",
                    str(teacher),
                    str(parent),
                    "--central-theme",
                    "accessibility parent burden",
                    "--out",
                    str(report),
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            markdown = report.read_text(encoding="utf-8")
            self.assertIn("Documents analyzed: 2", markdown)
            self.assertIn("Central theme priority used", markdown)
            self.assertIn("Source: teacher.txt", markdown)
            self.assertIn("Source: parent.txt", markdown)

    def test_cli_reports_pdf_error_without_traceback(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            pdf = root / "transcript.pdf"
            report = root / "report.md"
            pdf.write_bytes(b"%PDF-1.7\nbinary transcript data")

            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "themeforge.cli",
                    str(pdf),
                    "--out",
                    str(report),
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(completed.returncode, 2)
            self.assertIn("PDF text could not be extracted", completed.stderr)
            self.assertNotIn("Traceback", completed.stderr)

    def test_cli_reports_missing_input_without_traceback(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            missing = root / "missing.txt"
            report = root / "report.md"

            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "themeforge.cli",
                    str(missing),
                    "--out",
                    str(report),
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(completed.returncode, 2)
            self.assertIn("missing.txt", completed.stderr)
            self.assertNotIn("Traceback", completed.stderr)

    def test_cli_accepts_codebook_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            transcript = root / "transcript.txt"
            codebook = root / "codebook.csv"
            report = root / "report.md"
            transcript.write_text(
                "Teacher: Training examples helped staff adapt curriculum activities.\n"
                "Teacher: Scheduling support protected planning time for revision.\n",
                encoding="utf-8",
            )
            codebook.write_text(
                "theme,description\n"
                "Training Needs,training examples staff adapt curriculum activities\n"
                "Administrative Support,scheduling support protected planning time\n",
                encoding="utf-8",
            )

            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "themeforge.cli",
                    str(transcript),
                    "--codebook",
                    str(codebook),
                    "--quotes",
                    "1",
                    "--out",
                    str(report),
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            markdown = report.read_text(encoding="utf-8")
            self.assertIn("Theme 1: Training Needs", markdown)
            self.assertIn("Training examples helped staff", markdown)

    def test_cli_accepts_local_embedding_backend_with_fallback(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            transcript = root / "transcript.txt"
            report = root / "report.md"
            transcript.write_text(
                "Teacher: Peer planning helped curriculum teams revise lessons.\n"
                "Teacher: Training examples helped curriculum teams design activities.\n",
                encoding="utf-8",
            )

            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "themeforge.cli",
                    str(transcript),
                    "--semantic-backend",
                    "local-embeddings",
                    "--language",
                    "Multilingual",
                    "--out",
                    str(report),
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            markdown = report.read_text(encoding="utf-8")
            self.assertIn("Local embedding", markdown)

    def test_cli_reports_korean_output_path_with_legacy_console_encoding(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            transcript = root / "transcript.txt"
            report = root / "결과.md"
            transcript.write_text(
                "Teacher: Peer planning helped curriculum teams revise lessons.\n",
                encoding="utf-8",
            )
            environment = os.environ.copy()
            environment["PYTHONIOENCODING"] = "cp1252"

            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "themeforge.cli",
                    str(transcript),
                    "--out",
                    str(report),
                ],
                cwd=Path(__file__).resolve().parents[1],
                env=environment,
                capture_output=True,
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr.decode("ascii", errors="replace"))
            self.assertTrue(report.exists())
            self.assertIn(b"Wrote ", completed.stdout)
            self.assertNotIn(b"Traceback", completed.stderr)


if __name__ == "__main__":
    unittest.main()

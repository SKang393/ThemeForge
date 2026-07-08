import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


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

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn("Dataset: Adult day service curriculum", completed.stdout)
            self.assertIn("Parsing sanity:", completed.stdout)
            self.assertIn("Transcript artifact quote units: 0", completed.stdout)
            self.assertIn("Known leak terms: none", completed.stdout)
            self.assertIn("Theme coverage:", completed.stdout)
            self.assertIn("Codebook-assisted coverage:", completed.stdout)
            self.assertIn("Codebook entries: 5", completed.stdout)
            self.assertIn("Example quote token matches:", completed.stdout)


if __name__ == "__main__":
    unittest.main()

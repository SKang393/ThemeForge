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


if __name__ == "__main__":
    unittest.main()

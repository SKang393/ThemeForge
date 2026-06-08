import tempfile
import unittest
import zipfile
from pathlib import Path

from themeforge.io import load_transcript_text


class TranscriptInputTests(unittest.TestCase):
    def test_load_docx_extracts_paragraph_text(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "transcript.docx"
            document_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:r><w:t>Participant 1: Peer discussion helped me understand classwork.</w:t></w:r></w:p>
    <w:p><w:r><w:t>Participant 2: Instructor feedback made the deadline clearer.</w:t></w:r></w:p>
  </w:body>
</w:document>
"""
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("word/document.xml", document_xml)

            text = load_transcript_text(path)

        self.assertIn("Participant 1: Peer discussion helped", text)
        self.assertIn("Participant 2: Instructor feedback", text)

    def test_load_rtf_strips_basic_markup(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "transcript.rtf"
            path.write_text(
                r"{\rtf1\ansi Participant 1: Peer support helped.\par Participant 2: Clear feedback mattered.}",
                encoding="cp1252",
            )

            text = load_transcript_text(path)

        self.assertIn("Participant 1: Peer support helped.", text)
        self.assertIn("Participant 2: Clear feedback mattered.", text)
        self.assertNotIn(r"\rtf", text)

    def test_load_rtf_removes_font_table_text(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "transcript.rtf"
            path.write_text(
                r"{\rtf1\ansi{\fonttbl{\f0\fnil Times New Roman;}}{\colortbl;\red0\green0\blue0;}Participant 1: Accessibility support helped.\par}",
                encoding="cp1252",
            )

            text = load_transcript_text(path)

        self.assertIn("Participant 1: Accessibility support helped.", text)
        self.assertNotIn("Times New Roman", text)
        self.assertNotIn("red0", text)


if __name__ == "__main__":
    unittest.main()

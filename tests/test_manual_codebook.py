"""A selected codebook entry is a manual decision, not a lexical match."""
import csv
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from themeforge.app import ThemeForgeApp
from themeforge.project_io import load_project
from themeforge.rigor import CodedProject, ManualDefinitionComparisonError, intercoder_reliability


class HiddenApp(ThemeForgeApp):
    def _build_layout(self):
        self.withdraw()
        super()._build_layout()


class ManualCodebookTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.app = HiddenApp()
        self.addCleanup(self.app.destroy)
        source = self.folder / "studio.txt"
        self.passage = "Wait for the lamp."
        self.text = f"Lee: {self.passage}\nJo: \U0001f4a1  {self.passage}  \nLee: The bell signals the end of rehearsal."
        source.write_text(self.text, encoding="utf-8")
        with patch("themeforge.app.filedialog.askopenfilenames", return_value=[str(source)]):
            self.app.open_transcript()
        book = self.folder / "codes.csv"
        book.write_text("code,description,example\nSignal,Ending the rehearsal,The bell sounds\n"
                        "Signal,Waiting for permission,Wait for the lamp\n", encoding="utf-8")
        with patch("themeforge.app.filedialog.askopenfilename", return_value=str(book)):
            self.app.open_codebook()
        self.app.researcher_name.set("Coder A")

    def select(self, index=1):
        widget = self.app.transcript_widgets["studio.txt"]
        start = widget.search(self.passage, "2.0", stopindex="2.end")
        widget.tag_add("sel", f"{start}-1c", "2.end")
        self.app.definition_box.current(index)

    def apply(self, confirm=True):
        with patch("themeforge.app.messagebox.askyesno", return_value=confirm) as prompt:
            self.app.code_selection_to_definition()
        return prompt

    def report(self, suffix):
        path = self.folder / ("review" + suffix)
        with patch("themeforge.app.filedialog.asksaveasfilename", return_value=str(path)):
            self.app.save_report()
        return path.read_text(encoding="utf-8")

    def test_exact_duplicate_named_definition_and_source_survive_save_analyze_and_exports(self):
        self.select()
        with patch("themeforge.app.analyze_documents", side_effect=AssertionError):
            prompt = self.apply()
            self.assertIn("Waiting for permission", prompt.call_args.args[1])
            self.assertIn("Wait for the lamp", prompt.call_args.args[1])
            self.assertNotIn("Ending the rehearsal", prompt.call_args.args[1])
            self.select()
            self.apply()
        self.assertEqual(len(self.app.result.themes), 1)
        theme, = self.app.result.themes
        quote, = theme.quotes
        start = self.text.rindex(self.passage)
        self.assertEqual((quote.source_name, quote.source_line, quote.source_start, quote.source_end, quote.text),
                         ("studio.txt", 2, start, start + len(self.passage), self.passage))
        self.assertEqual(self.app.result.quote_count, 0)  # No units analyzed.
        self.assertEqual(theme.validation["speaker_count"], 0)  # The selection did not assert a speaker.
        self.assertNotIn("central_theme_alignment", theme.validation)
        self.assertIn("unscored", self.app.theme_list.get(0))
        self.assertIn("Coder A", quote.rationale)
        self.assertIn("Waiting for permission", quote.rationale)
        self.assertIn("No automated analysis", self.report(".md"))
        self.assertIsNone(json.loads(self.report(".json"))["themes"][0]["quotes"][0]["relevance"])
        project = self.folder / "studio.tfproj"
        with patch("themeforge.app.filedialog.asksaveasfilename", return_value=str(project)):
            self.assertTrue(self.app.save_project())
        with zipfile.ZipFile(project) as archive:
            self.assertEqual(json.loads(archive.read("project.json"))["format_version"], 2)
        with patch("themeforge.app.filedialog.askopenfilename", return_value=str(project)):
            self.app.open_project()
        self.assertEqual(self.app.result.themes, [theme])
        events = [e for e in self.app.audit_events if e.action == "codebook_passage_assigned"]
        self.assertEqual([(e.actor, e.target_id) for e in events], [("Coder A", quote.quote_id)])
        self.select(0)
        self.apply()
        self.assertEqual(len(self.app.result.themes), 2)
        self.app.analyze()
        saved = load_project(project)
        self.assertIn(theme, saved.result.themes)
        self.assertEqual(sum(t.id.startswith("M") for t in saved.result.themes), 2)
        rows = list(csv.DictReader(io.StringIO(self.report(".csv"))))
        manual = [row for row in rows if row["theme_id"].startswith("M")]
        self.assertEqual([row["relevance"] for row in manual], ["", ""])
        self.assertTrue(any("Waiting for permission" in row["selection_rationale"] for row in manual))
        self.assertTrue(any(row["relevance"] and row["theme_id"].startswith("C") for row in rows))
        coded = CodedProject(saved.documents, saved.result)
        with self.assertRaisesRegex(ManualDefinitionComparisonError, "matches names, not the definitions"):
            intercoder_reliability(coded, coded)

    def test_no_coder_or_cancel_does_not_create_workspace(self):
        self.select()
        before = tuple(self.app.audit_events)
        self.app.researcher_name.set("")
        self.apply().assert_not_called()
        self.app.researcher_name.set("Coder B")
        self.apply(False).assert_called_once()
        self.assertIsNone(self.app.result)
        self.assertEqual(tuple(self.app.audit_events), before)

    def test_second_passage_uses_same_definition_and_focuses_its_own_anchor(self):
        self.select()
        self.apply()
        widget = self.app.transcript_widgets["studio.txt"]
        widget.tag_remove("sel", "1.0", "end")
        widget.tag_add("sel", "1.0", "1.end")
        self.app.code_selection_to_theme()
        self.assertEqual(len(self.app.result.themes[0].quotes), 1)
        self.apply()
        only, = self.app.result.themes
        self.assertEqual(len(only.quotes), 2)
        selected = self.app._current_quote_match().quote
        self.assertEqual((selected.source_start, selected.text), (0, self.text.splitlines()[0]))

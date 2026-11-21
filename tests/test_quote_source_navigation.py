import csv
import io
import json
from dataclasses import asdict, replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from themeforge.analysis_types import AnalysisResult, AnalysisSettings, Theme, ThemeQuote, TranscriptDocument
from themeforge.app import ThemeForgeApp
from themeforge.exports import export_json
from themeforge.project_io import ProjectState, load_project, save_project


class HiddenApp(ThemeForgeApp):
    def _build_layout(self):
        self.withdraw()
        super()._build_layout()


class QuoteSourceNavigationTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.app = HiddenApp()
        self.addCleanup(self.app.destroy)
        phrase = "I filed the shift log."
        negative = "I did not file the shift log."
        self.monologue = "The performer described every label on the table before indicating what happened at the end: "
        self.doc = TranscriptDocument("Workshop",
            f"Ren: {phrase}\nJo: {negative}\nLee: 💡 {phrase}\nKim: {self.monologue}the bell sounded.")
        start = self.doc.text.rindex(phrase)
        self.good = ThemeQuote("Q1", "Lee", phrase, .95, 3, self.doc.name, "A lexical suggestion only.",
                               start, start + len(phrase), "Check who acted.")
        p = self.doc.text.index(negative)
        self.stale = replace(self.good, quote_id="Q2", speaker="Jo", source_line=2, source_start=p, source_end=p+len(phrase))
        p = self.doc.text.index(self.monologue)
        bad = self.monologue + "the bell stayed silent."
        self.prefix = replace(self.good, quote_id="Q3", speaker="Kim", text=bad, source_start=p, source_line=4, source_end=p+len(bad))

    def open_rows(self, quotes, *, other_documents=()):
        group = Theme("C01", "Self-submission", "#345678", [], len(quotes), .95, quotes)
        documents = (self.doc, *other_documents)
        state = ProjectState((), None, documents, AnalysisSettings(),
                             AnalysisResult("example", len(documents), 4, [group], []), "Coder")
        path = self.folder / "unchanged.tfproj"
        save_project(path, state, new_only=True)
        original = path.read_bytes()
        with patch("themeforge.app.filedialog.askopenfilename", return_value=str(path)):
            self.app.open_project()
        return state, path, original

    def selected(self):
        widget = self.app.transcript_widgets[self.doc.name]
        anchors = widget.tag_ranges("selected_quote")
        return (widget.index(anchors[0]), widget.get(*anchors)) if anchors else None

    def test_uncode_requires_the_visible_quote_even_when_wording_repeats(self):
        first = replace(self.good, quote_id="First", speaker="Ren", source_line=1, source_start=5,
                        source_end=5 + len(self.good.text))
        annex = TranscriptDocument("Annex", f"Ren: {first.text}")
        other = replace(first, quote_id="Annex1", source_name=annex.name)
        state, path, saved = self.open_rows([first, self.good, other], other_documents=(annex,))
        app = self.app
        app.transcript_notebook.select(app.transcript_frames[annex.name])
        app.update()
        app.transcript_widgets[annex.name].tag_add("sel", "1.5", "1.end")
        app.uncode_current_quote()  # Identical offsets and wording in another file.
        self.assertEqual(app._project_state(), state)
        self.assertEqual(path.read_bytes(), saved)

        app.transcript_notebook.select(app.transcript_frames[self.doc.name])
        app.update()
        widget = app.transcript_widgets[self.doc.name]
        start = f"1.0 + {self.good.source_start} chars"
        widget.tag_add("sel", start, f"{start} + {len(first.text)} chars")
        self.assertEqual(app._selected_transcript_text().text, first.text)
        app.uncode_current_quote()  # Same file and wording, different occurrence.
        self.assertEqual(app._project_state(), state)
        self.assertEqual(path.read_bytes(), saved)

        app.next_quote()  # The selected text now belongs to the active quote.
        self.assertEqual(app._visible_review_match().quote, self.good)
        app.researcher_name.set("  ")
        app.uncode_current_quote()
        self.assertIn("name or pseudonym", app.status_text.get())
        self.assertEqual(path.read_bytes(), saved)
        self.assertEqual(app.result, state.result)
        self.assertFalse(app.audit_events)
        self.assertEqual(app._visible_review_match().quote, self.good)

        app.researcher_name.set("Second reader")
        app.uncode_current_quote()
        reopened = load_project(path)
        self.assertEqual(reopened.result.themes[0].quotes, [first, other])
        self.assertEqual([(e.action, e.target_id, e.actor) for e in reopened.audit_events],
                         [("quote_uncoded", self.good.quote_id, "Second reader")])

    def test_stale_span_and_partial_prefix_do_not_highlight_another_speaker_or_omit_records(self):
        state, path, original = self.open_rows([self.stale, self.good, self.prefix])
        widget = self.app.transcript_widgets[self.doc.name]
        self.assertIn("1 of 3", self.app.quote_status.get())
        self.assertIn("Recorded speaker: Jo", self.app.quote_meta.get())
        self.assertIn("offsets do not match", self.app.quote_meta.get())
        self.assertIn("recorded line as context only", self.app.quote_meta.get())
        self.assertIn(self.stale.text, self.app.quote_reason.get())
        self.assertIsNone(self.selected())
        self.assertEqual(len(widget.tag_ranges("theme_C01")), 2)  # Only Lee's valid span.
        self.assertEqual(str(self.app.quote_memo_text.cget("state")), "disabled")
        self.app.uncode_current_quote()
        self.assertEqual(self.app.result, state.result)
        self.app.next_quote()
        self.assertEqual(self.selected()[0].split(".")[0], "3")
        self.assertEqual(self.selected()[1], self.good.text)
        self.assertIn("Recorded span matches", self.app.quote_meta.get())
        self.assertEqual(self.app._current_quote_match().quote, self.good)
        self.app.next_quote()
        self.assertIn("3 of 3", self.app.quote_status.get())
        self.assertIsNone(self.selected())
        self.assertIn("bell stayed silent.", self.app.quote_reason.get())
        self.assertIsNone(self.app._current_quote_match())
        self.assertFalse(self.app.audit_events)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(load_project(path), state)
        report = self.folder / "stored.json"
        with patch("themeforge.app.filedialog.asksaveasfilename", return_value=str(report)):
            self.app.save_report()
        rows = json.loads(report.read_text(encoding="utf-8"))["themes"][0]["quotes"]
        self.assertEqual([(r["quote_id"], r["source_start"], r["text"]) for r in rows],
                         [(q.quote_id, q.source_start, q.text) for q in state.result.themes[0].quotes])

    def test_missing_offsets_require_unique_complete_text_and_recorded_line_and_remain_read_only(self):
        repeated = replace(self.good, source_start=0, source_end=0)
        unique = replace(self.stale, text="I did not file the shift log.", source_start=0, source_end=0)
        wrong_line = replace(unique, quote_id="Q3", source_line=4)
        no_source = replace(unique, quote_id="Q4", source_name="Missing source")
        _, path, original = self.open_rows([repeated, unique, wrong_line, no_source])
        self.assertIsNone(self.selected())
        self.assertIn("no unique full-text match", self.app.quote_meta.get())
        self.app.next_quote()
        self.assertEqual(self.selected(), ("2.4", unique.text))
        self.assertIn("Text-only location", self.app.quote_meta.get())
        self.assertIn("Read-only", self.app.quote_meta.get())
        self.assertIsNone(self.app._current_quote_match())
        self.app.next_quote()
        self.assertIsNone(self.selected())
        self.assertIn("recorded line", self.app.quote_meta.get())
        self.app.next_quote()
        self.assertIsNone(self.selected())
        self.assertIn("No unique named source", self.app.quote_meta.get())
        self.assertEqual(path.read_bytes(), original)

    def test_saved_reports_distinguish_exact_text_only_conflicting_and_unverifiable_records(self):
        legacy = replace(self.stale, quote_id="Legacy", text="I did not file the shift log.", source_start=0, source_end=0)
        ambiguous = replace(self.good, quote_id="Ambiguous", source_start=0, source_end=0)
        wrong_line = replace(self.good, quote_id="WrongLine", source_line=1)
        missing = replace(self.good, quote_id="Missing", source_name="Other workshop")
        quotes = [self.good, legacy, self.stale, ambiguous, wrong_line, missing]
        statuses = ["exact_span", "text_only", "conflict", "unverifiable", "conflict", "unverifiable"]
        state, path, original = self.open_rows(quotes)
        outputs = {}
        for ext in ("json", "csv", "md"):
            target = self.folder / f"report.{ext}"
            with patch("themeforge.app.filedialog.asksaveasfilename", return_value=str(target)):
                self.app.save_report()
            outputs[ext] = target.read_text(encoding="utf-8")
        json_rows = json.loads(outputs["json"])["themes"][0]["quotes"]
        csv_rows = list(csv.DictReader(io.StringIO(outputs["csv"])))
        self.assertEqual(len(json_rows), len(quotes))
        self.assertEqual(len(csv_rows), len(quotes))
        for q, status, j, c in zip(quotes, statuses, json_rows, csv_rows, strict=True):
            self.assertEqual({k: j[k] for k in asdict(q)}, asdict(q))
            self.assertEqual((c["quote_id"], c["speaker"], c["quote"]), (q.quote_id, q.speaker, q.text))
            self.assertEqual((int(c["source_start"]), int(c["source_end"])), (q.source_start, q.source_end))
            for row in (j, c):
                self.assertEqual(row["source_location_status"], status)
                self.assertEqual(row["source_location_note"], j["source_location_note"])
            # Both Markdown citation lists must qualify the same recorded quote ID.
            citations = [line for line in outputs["md"].splitlines() if f"Quote ID: {q.quote_id} |" in line]
            self.assertEqual(len(citations), 2)
            self.assertTrue(all(f"Location: {status}" in line for line in citations))
        self.assertIn("does not verify speaker identity or whether a code fits", outputs["md"])
        self.assertFalse(self.app.audit_events)
        self.assertEqual(self.app.result, state.result)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(load_project(path), state)
        # A result on its own or two indistinguishably named snapshots cannot verify source evidence.
        for docs in ((), (self.doc, self.doc)):
            rows = json.loads(export_json(state.result, docs))["themes"][0]["quotes"]
            self.assertTrue(all(r["source_location_status"] == "unverifiable" for r in rows))

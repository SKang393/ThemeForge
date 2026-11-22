from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from themeforge.analysis_types import AnalysisResult, AnalysisSettings, CodebookEntry, Theme, ThemeQuote, TranscriptDocument
from themeforge.audit import AuditEvent
from themeforge.case_reviews import case_review_cells
from themeforge.exports import export_json, export_markdown, export_quotes_csv
from themeforge.match_reviews import match_id, match_snapshot
from themeforge.project_io import CASE_MEMBER, ProjectState, load_project, save_project
from themeforge.source_location import TranscriptIndex


def review_event(theme, quote, documents, actor, disposition):
    snapshot = match_snapshot(theme, quote, TranscriptIndex(documents))
    return AuditEvent("2030-01-01T00:00:00Z", actor, "match_review_note", "theme_quote_match",
                      match_id(snapshot), json.dumps({"match": snapshot, "disposition": disposition, "reason": "Checked."}))


class CaseReviewTests(unittest.TestCase):
    def setUp(self):
        # An explicit case label intentionally collides with an unassigned source name.
        self.docs = (
            TranscriptDocument("visit-one", "Guest: 🧵 Blue thread.\nGuest: Ochre thread.", "Unrelated memo", "loose"),
            TranscriptDocument("visit-two", "Guest: 🧵 Blue thread.\nGuest: Ochre thread.", "", "loose"),
            TranscriptDocument("loose", "Guest: 🧵 Blue thread.", "loose"),
        )
        quotes = []
        for visit in self.docs[:2]:
            for line, excerpt in enumerate(("🧵 Blue thread.", "Ochre thread."), 1):
                start = visit.text.index(excerpt)
                quotes.append(ThemeQuote(f"Q{len(quotes)}", "Guest", excerpt, 0.5, line, visit.name,
                                         source_start=start, source_end=start + len(excerpt)))
        self.theme = Theme("T01", "Thread", "", [], len(quotes), 0.5, quotes)
        self.result = AnalysisResult("1", 3, 4, [self.theme], [])
        self.events = tuple(
            review_event(self.theme, quotes[index], self.docs, actor, disposition)
            for index, actor, disposition in (
                (0, "Reader", "deferred"), (0, "Reader", "supported"), (1, "Reader", "declined"),
                (2, "Reader", "deferred"), (3, "Second", "supported"),
            )
        )

    def test_case_pooling_and_reassignment_preserve_reader_and_occurrence_identity(self):
        assigned, unassigned = case_review_cells(self.docs, self.result, self.events, reviewer="Reader")
        self.assertEqual(assigned.sources, ("visit-one", "visit-two"))
        self.assertEqual(assigned.counts, {"supported": 1, "declined": 1, "deferred": 1, "not_reviewed": 1})
        self.assertIs(assigned.theme, self.theme)
        self.assertTrue(all(p.navigable for p in assigned.passages))
        self.assertIs(assigned.passages[2].quote, self.theme.quotes[2])
        self.assertEqual(assigned.passages[2].quote.source_name, "visit-two")
        self.assertEqual(assigned.passages[0].note.actor, "Reader")
        self.assertNotEqual(assigned.group_key, unassigned.group_key)
        self.assertEqual(unassigned.sources, ("loose",))
        self.assertEqual(unassigned.case_label, "")
        self.assertEqual(unassigned.passages, ())

        changed = (self.docs[0], replace(self.docs[1], case_label="second"), self.docs[2])
        first, second, _ = case_review_cells(changed, self.result, self.events, reviewer="Reader")
        self.assertEqual(first.counts, {"supported": 1, "declined": 1})
        self.assertEqual(second.counts, {"deferred": 1, "not_reviewed": 1})
        self.assertEqual(second.passages[0].note, assigned.passages[2].note)
        other, _ = case_review_cells(self.docs, self.result, self.events, reviewer="Second")
        self.assertEqual(other.counts, {"supported": 1, "not_reviewed": 3})
        for events, reviewer in ((self.events, ""), ((*self.events, AuditEvent("", "", "analysis_run", "", "", "")), "Reader")):
            cell, _ = case_review_cells(self.docs, self.result, events, reviewer=reviewer)
            self.assertEqual(cell.counts, {"not_reviewed": 4})

    def test_historical_definitions_and_unverified_citations_cannot_supply_current_support(self):
        entry = CodebookEntry("Weave", "Observe the work.")
        good = self.theme.quotes[0]
        altered = replace(good, quote_id="changed", source_start=1)
        legacy = replace(good, quote_id="legacy", source_start=0, source_end=0)
        lost = replace(good, quote_id="lost", source_name="missing")
        theme = replace(self.theme, id="C01", quotes=[good, altered, legacy, lost],
                        validation={"generated_from_codebook_entry": entry.snapshot()})
        result = replace(self.result, themes=[theme])
        event = review_event(theme, good, self.docs, "Reader", "supported")
        cell, _, unresolved = case_review_cells(self.docs, result, [event], reviewer="Reader")
        self.assertEqual(cell.counts, {"historical": 1, "unverified": 2})
        self.assertTrue(all(not p.navigable for p in cell.passages))
        self.assertTrue(unresolved.unresolved)
        self.assertFalse(unresolved.passages[0].navigable)
        self.assertIs(unresolved.passages[0].quote, lost)
        current, _, _ = case_review_cells(self.docs, result, [event], reviewer="Reader", codebook_entries=[entry])
        self.assertEqual(current.counts, {"supported": 1, "unverified": 2})

        duplicate = replace(self.docs[0], case_label="")
        cells = case_review_cells((*self.docs, duplicate), result, [event], reviewer="Reader", codebook_entries=[entry])
        disputed = next(c for c in cells if c.unresolved and c.sources == ("visit-one",))
        self.assertEqual(disputed.counts, {"unverified": 3})
        self.assertTrue(all(not p.navigable for p in disputed.passages))
        self.assertTrue(all(c.counts["supported"] == 0 for c in cells))

    def test_project_round_trip_omits_unassigned_fields_and_preserves_history_and_exports(self):
        private_label = "Visit grouping 甲"
        grouped = (replace(self.docs[0], case_label=private_label), *self.docs[1:])
        state = ProjectState((), None, grouped, AnalysisSettings(), self.result, "Reader", self.events)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "visits.tfproj"
            save_project(path, state)
            with zipfile.ZipFile(path) as archive:
                raw = json.loads(archive.read(CASE_MEMBER))
            loaded = load_project(path)
        self.assertEqual(raw["documents"][0]["case_label"], private_label)
        self.assertEqual(raw["documents"][2], {"name": "loose", "text": self.docs[2].text, "memo": "loose"})
        self.assertEqual(loaded.documents, grouped)
        self.assertEqual(loaded.audit_events, self.events)
        self.assertEqual(loaded.result, self.result)
        for export in (export_json, export_markdown, export_quotes_csv):
            with self.subTest(export=export.__name__):
                self.assertEqual(export(self.result, self.docs, self.events), export(self.result, grouped, self.events))

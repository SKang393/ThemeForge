from dataclasses import replace
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from themeforge.analysis_types import AnalysisResult, AnalysisSettings, Theme, ThemeQuote, TranscriptDocument
from themeforge.audit import AuditEvent
from themeforge.case_recovery import older_working_copy, recovered_cases
from themeforge.match_reviews import match_id, match_snapshot
from themeforge.project_io import ProjectState, load_project, save_project
from themeforge.source_location import TranscriptIndex


class CaseRecoveryTests(unittest.TestCase):
    def setUp(self):
        docs = (TranscriptDocument("first.txt", "Reader: Clay 🟦 rang.", "First visit.", "Blue"),
                TranscriptDocument("next.txt", "Reader: Clay 🟦 rang.", "Next visit."))
        quotes = [ThemeQuote(f"Q{i}", "Reader", "Clay 🟦 rang.", 1.0, 1, d.name,
                             source_start=8, source_end=len(d.text)) for i, d in enumerate(docs)]
        theme = Theme("T1", "Sound", "", [], 2, 1.0, quotes)
        self.master = ProjectState((), None, docs, AnalysisSettings(), AnalysisResult("1", 2, 2, [theme], []),
                                   "First", (AuditEvent("2030-01-01T00:00:00Z", "First", "analysis_run", "", "", ""),),
                                   is_case_master=True)
        self.old = older_working_copy(self.master)
        snap = match_snapshot(theme, quotes[1], TranscriptIndex(docs))
        self.review = AuditEvent("2030-01-02T00:00:00Z", "Second", "match_review_note", "theme_quote_match",
                                 match_id(snap), json.dumps({"match": snap, "disposition": "declined",
                                                            "reason": "A sample from another visit."}))
        self.returned = replace(self.old, researcher_name="Second", audit_events=(*self.old.audit_events, self.review))

    def test_restores_only_selected_labels_and_retains_new_review(self):
        recovered = recovered_cases(self.returned, self.master)
        self.assertEqual(recovered.documents, self.master.documents)
        self.assertTrue(recovered.is_case_master)
        self.assertFalse(self.returned.is_case_master)
        self.assertEqual(older_working_copy(recovered), self.returned)
        self.assertEqual(recovered.audit_events, self.returned.audit_events)
        self.assertEqual(recovered.researcher_name, "Second")
        # A retained copy from the earlier case-aware writer is also a valid explicit donor.
        prior = replace(self.master, is_case_master=False)
        self.assertEqual(recovered_cases(self.returned, prior), recovered)
        with self.assertRaises(ValueError):
            older_working_copy(prior)

    def test_missing_or_conflicting_assignments_refuse(self):
        with self.assertRaises(ValueError):
            recovered_cases(self.old, self.old)
        with self.assertRaises(ValueError):
            recovered_cases(self.master, self.master)
        with self.assertRaises(ValueError):
            recovered_cases(replace(self.master, is_case_master=False), self.master)
        for name in ("", self.old.documents[0].name):
            bad = replace(self.master, documents=(self.master.documents[0], replace(self.master.documents[1], name=name)))
            with self.subTest(name=name), self.assertRaises(ValueError):
                recovered_cases(self.old, bad)

    def test_recovery_checks_whole_study_and_original_history(self):
        first, second = self.old.documents
        incompatible = [
            replace(self.old, documents=(replace(first, text=first.text+" "), second)),
            replace(self.old, documents=(replace(first, memo="Another observation"), second)),
            replace(self.old, documents=(second, first)),
            replace(self.old, transcript_paths=(Path("other.txt"),)),
            replace(self.old, settings=replace(self.old.settings, min_quote_words=self.old.settings.min_quote_words+1)),
            replace(self.old, result=None),
            replace(self.old, audit_events=()),
            replace(self.old, audit_events=(replace(self.old.audit_events[0], details="changed"),)),
        ]
        for returned in incompatible:
            with self.subTest(returned=returned), self.assertRaises(ValueError):
                recovered_cases(returned, self.master)
        forged = json.loads(self.review.details)
        forged["match"]["quote"]["source_start"] += 1
        altered = replace(self.review, target_id=match_id(forged["match"]), details=json.dumps(forged))
        for event in [altered, replace(self.review, details="unrecognized"), replace(self.review, actor=""),
                      replace(self.review, action="analysis_run")]:
            changed = replace(self.old, audit_events=(*self.old.audit_events, event))
            with self.subTest(event=event), self.assertRaises(ValueError):
                recovered_cases(changed, self.master)

    def test_interrupted_new_copy_preserves_inputs_then_retries_without_overwriting(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            master, old, recovered = (base/n for n in ("master.tfproj", "returned.tfproj", "recovered.tfproj"))
            save_project(master, self.master, new_only=True)
            save_project(old, self.returned, new_only=True)
            inputs = {p: p.read_bytes() for p in (master, old)}
            result = recovered_cases(load_project(old), load_project(master))
            publish = "os.rename" if os.name == "nt" else "os.link"
            with patch("themeforge.project_io."+publish, side_effect=OSError("Interrupted publication")):
                with self.assertRaisesRegex(OSError, "Interrupted publication"):
                    save_project(recovered, result, new_only=True)
            self.assertEqual(set(base.iterdir()), set(inputs))
            save_project(recovered, result, new_only=True)
            self.assertEqual(load_project(recovered), result)
            for original, data in inputs.items():
                with self.assertRaises(FileExistsError):
                    save_project(original, result, new_only=True)
                self.assertEqual(original.read_bytes(), data)

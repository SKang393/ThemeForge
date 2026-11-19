import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from themeforge.project_io import (
    NULLABLE_MEMBER, NULLABLE_PROFILE, PROJECT_JSON, ProjectCopyRequired, UnsupportedProjectFormatError,
    load_project, save_project,
)


def archive(path, entries):
    with zipfile.ZipFile(path, "w") as zipped:
        for name, payload in entries:
            zipped.writestr(name, json.dumps(payload, ensure_ascii=False))


def legacy(*, version=2):
    def group(tid, score, qid, relevance, rationale="", validation=None):
        q = {"quote_id": qid, "speaker": "Jo", "text": "Wait.", "source_name": "Studio",
             "source_line": 1, "source_start": 4, "source_end": 9, "relevance": relevance,
             "rationale": rationale, "memo": "Keep this exact memo."}
        return {"id": tid, "name": "Signal", "color": "#123456", "score": score,
                "quote_count": 1, "quotes": [q], "validation": validation or {}}
    return {"format_version": version, "documents": [{"name": "Studio", "text": "Jo: Wait."}],
            "result": {"version": "1.5.0", "document_count": 1, "quote_count": 1, "themes": [
                group("M01", 0.0, "MQDexample", 0.0, validation={"manual_codebook_definition":
                    json.dumps({"name": "Signal", "description": "Wait for a cue.", "examples": ["Wait."]})}),
                group("T01", -1.0, "Q1", -1.0, "Manual move by Editor: prior rationale.",
                      validation={"score_scope": "Not scored"}),
                group("T02", 0.0, "Q0", 0.0), group("T03", 0.82, "Q3", 0.82)]},
            "researcher_name": "Coder", "audit_events": [{"actor": "Editor", "action": "quote_reassigned",
                "target_type": "quote", "target_id": "Q1", "details": "Original audit text.", "timestamp_utc": "2026-01-01T00:00:00Z"}]}


class NullableProjectTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name)
        self.old = self.folder / "old.tfproj"
        self.new = self.folder / "copy.tfproj"

    def test_migrate_known_placeholders_to_null_without_converting_real_zero_or_historical_records(self):
        payload = legacy()
        archive(self.old, [(PROJECT_JSON, payload)])
        before = self.old.read_bytes()
        state = load_project(self.old)
        self.assertEqual([t.score for t in state.result.themes], [None, None, 0.0, 0.82])
        self.assertEqual([t.quotes[0].relevance for t in state.result.themes], [None, None, 0.0, 0.82])
        with self.assertRaises(ProjectCopyRequired):
            save_project(self.old, state)
        save_project(self.new, state, new_only=True)
        with zipfile.ZipFile(self.new) as zipped:
            self.assertEqual(zipped.namelist(), [NULLABLE_MEMBER])
            written = json.loads(zipped.read(NULLABLE_MEMBER))
        self.assertEqual(written["format_version"], NULLABLE_PROFILE)
        self.assertEqual(written["audit_events"], payload["audit_events"])
        self.assertEqual([t["quotes"][0]["relevance"] for t in written["result"]["themes"]], [None, None, 0, 0.82])
        self.assertEqual(load_project(self.new), state)
        self.assertEqual(self.old.read_bytes(), before)
        copy_bytes = self.new.read_bytes()
        with patch("themeforge.project_io.os.replace", side_effect=PermissionError("locked")):
            with self.assertRaises(PermissionError):
                save_project(self.new, state)
        self.assertEqual(self.new.read_bytes(), copy_bytes)
        self.assertEqual(self.old.read_bytes(), before)
        self.assertEqual(set(self.folder.iterdir()), {self.old, self.new})

    def test_unreported_preexisting_group_value_is_retained_separately_not_exported_as_current(self):
        payload = legacy()
        payload["result"]["themes"][0]["score"] = 0.62
        archive(self.old, [(PROJECT_JSON, payload)])
        state = load_project(self.old)
        save_project(self.new, state)
        with zipfile.ZipFile(self.new) as zipped:
            group = json.loads(zipped.read(NULLABLE_MEMBER))["result"]["themes"][0]
        self.assertIsNone(group["score"])
        self.assertEqual(group["unreported_stored_score"], 0.62)
        reopened = load_project(self.new)
        self.assertEqual(reopened, state)
        self.assertIsNone(reopened.result.themes[0].reported_score)

    def test_reject_ambiguous_or_unknown_profile_without_writing(self):
        ordinary = legacy()
        tagged = dict(ordinary, format_version=NULLABLE_PROFILE)
        for entries, error in (
            ([(PROJECT_JSON, ordinary), (NULLABLE_MEMBER, tagged)], ValueError),
            ([(PROJECT_JSON, tagged)], UnsupportedProjectFormatError),
            ([(NULLABLE_MEMBER, ordinary)], UnsupportedProjectFormatError),
            ([(PROJECT_JSON, dict(ordinary, format_version=3))], UnsupportedProjectFormatError),
            ([(PROJECT_JSON, dict(ordinary, format_version=4))], UnsupportedProjectFormatError),
            ([(NULLABLE_MEMBER, tagged)], ValueError),  # Exact manual assignment has a numeric current score.
        ):
            with self.subTest(entries=[name for name, _ in entries], error=error):
                archive(self.old, entries)
                before = self.old.read_bytes()
                with self.assertRaises(error):
                    load_project(self.old)
                self.assertEqual(self.old.read_bytes(), before)

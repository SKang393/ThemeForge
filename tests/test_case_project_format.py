from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from themeforge.analysis_types import AnalysisResult, AnalysisSettings, Theme, TranscriptDocument
from themeforge.project_io import (
    CASE_MEMBER, CASE_NULLABLE_PROFILE, CASE_PROFILE, NULLABLE_MEMBER, NULLABLE_PROFILE, PROJECT_JSON,
    ProjectChanged, ProjectCopyRequired, ProjectState, UnsupportedProjectFormatError,
    ensure_project_revision, load_project, save_project,
)


def archive_profile(path):
    with zipfile.ZipFile(path) as archive:
        member, = archive.namelist()
        return member, json.loads(archive.read(member))["format_version"]


class CaseProjectFormatTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.path = self.root / "study.tfproj"
        self.doc = TranscriptDocument("visit.txt", "Speaker: A recorded visit.")
        self.ordinary = ProjectState((), None, (self.doc,), AnalysisSettings())
        self.grouped = replace(self.ordinary, documents=(replace(self.doc, case_label="Group A"),))
        self.unscored = AnalysisResult("1", 1, 0, [Theme("M1", "Manual", "", [], 0, None)], [])

    def test_ordinary_and_nullable_studies_keep_prior_format_when_ungrouped(self):
        for name, result, expected in (
            ("ordinary", None, (PROJECT_JSON, 2)),
            ("nullable", self.unscored, (NULLABLE_MEMBER, NULLABLE_PROFILE)),
        ):
            with self.subTest(name=name):
                path = self.root / (name + ".tfproj")
                state = replace(self.ordinary, result=result)
                save_project(path, state)
                self.assertEqual(archive_profile(path), expected)
                loaded = load_project(path)
                self.assertEqual(loaded, state)
                self.assertFalse(loaded.is_case_master)
                original = path.read_bytes()
                with self.assertRaises(ProjectCopyRequired) as error:
                    save_project(path, replace(loaded, documents=self.grouped.documents),
                                 expected_revision=loaded.file_revision)
                self.assertEqual(error.exception.suffix, "-case-master")
                self.assertEqual(path.read_bytes(), original)

    def test_optional_legacy_label_is_readable_and_migrates_only_to_a_separate_master(self):
        payload = {"format_version": 2, "documents": [{"name": "visit.txt", "text": self.doc.text,
                                                       "memo": "", "case_label": "Group A"}]}
        with zipfile.ZipFile(self.path, "w") as archive:
            archive.writestr(PROJECT_JSON, json.dumps(payload))
        old_bytes = self.path.read_bytes()
        loaded = load_project(self.path)
        self.assertFalse(loaded.is_case_master)
        self.assertEqual(loaded.documents, self.grouped.documents)
        with self.assertRaises(ProjectCopyRequired):
            save_project(self.path, loaded)
        separate = self.root / "master.tfproj"
        save_project(separate, loaded, new_only=True)
        self.assertEqual(archive_profile(separate), (CASE_MEMBER, CASE_PROFILE))
        master = load_project(separate)
        self.assertTrue(master.is_case_master)
        self.assertEqual(master, loaded)
        self.assertEqual(self.path.read_bytes(), old_bytes)

    def test_grouped_and_nullable_master_stays_protected_after_all_groups_are_cleared(self):
        save_project(self.path, self.grouped)
        numeric = load_project(self.path)
        self.assertTrue(numeric.is_case_master)
        self.assertEqual(archive_profile(self.path), (CASE_MEMBER, CASE_PROFILE))
        save_project(self.path, replace(numeric, result=self.unscored), expected_revision=numeric.file_revision)
        nullable = load_project(self.path)
        self.assertTrue(nullable.is_case_master)
        self.assertIsNone(nullable.result.themes[0].score)
        self.assertEqual(archive_profile(self.path), (CASE_MEMBER, CASE_NULLABLE_PROFILE))
        save_project(self.path, self.ordinary, expected_revision=nullable.file_revision)
        empty = load_project(self.path)
        self.assertEqual(empty.documents, self.ordinary.documents)
        self.assertTrue(empty.is_case_master)
        self.assertEqual(archive_profile(self.path), (CASE_MEMBER, CASE_NULLABLE_PROFILE))
        new = self.root / "empty-master.tfproj"
        save_project(new, empty, new_only=True)
        self.assertTrue(load_project(new).is_case_master)

    def test_revision_check_and_unbound_writes_cannot_replace_an_existing_master(self):
        revision = save_project(self.path, self.grouped)
        loaded = load_project(self.path)
        ensure_project_revision(self.path, revision)
        original = self.path.read_bytes()
        with self.assertRaises(ProjectCopyRequired):
            save_project(self.path, self.ordinary)
        self.assertEqual(self.path.read_bytes(), original)
        save_project(self.path, replace(loaded, researcher_name="Changed"))
        updated = self.path.read_bytes()
        with self.assertRaises(ProjectChanged):
            ensure_project_revision(self.path, revision)
        with self.assertRaises(ProjectChanged):
            save_project(self.path, loaded)
        self.assertEqual(self.path.read_bytes(), updated)
        with self.assertRaises(ProjectChanged):
            ensure_project_revision(self.root / "missing.tfproj", revision)

    def test_changed_recovery_input_prevents_publication_after_serialization(self):
        for changed in ("master", "returned"):
            with self.subTest(changed=changed):
                folder = self.root / changed
                folder.mkdir()
                master = folder / "master.tfproj"
                returned = folder / "returned.tfproj"
                output = folder / "recovered.tfproj"
                revisions = ((master, save_project(master, self.grouped)),
                             (returned, save_project(returned, self.ordinary)))
                expected = {path: path.read_bytes() for path, _ in revisions}
                updated = folder / "external-save.tfproj"
                source, state = (master, self.grouped) if changed == "master" else (returned, self.ordinary)
                save_project(updated, replace(state, researcher_name="External edit"))
                expected[source] = updated.read_bytes()

                with patch("themeforge.project_io.os.fsync", side_effect=lambda _fd: updated.replace(source)):
                    with self.assertRaises(ProjectChanged):
                        save_project(output, self.grouped, new_only=True, input_revisions=revisions)

                self.assertEqual(set(folder.iterdir()), {master, returned})
                for path, contents in expected.items():
                    self.assertEqual(path.read_bytes(), contents)

    def test_wrong_markers_and_ambiguous_members_refuse_open_and_replacement(self):
        for entries, error in (
            ([(CASE_MEMBER, 2)], UnsupportedProjectFormatError),
            ([(CASE_MEMBER, "future-case")], UnsupportedProjectFormatError),
            ([(PROJECT_JSON, CASE_PROFILE)], UnsupportedProjectFormatError),
            ([(NULLABLE_MEMBER, CASE_NULLABLE_PROFILE)], UnsupportedProjectFormatError),
            ([(CASE_MEMBER, CASE_PROFILE), (PROJECT_JSON, 2)], ValueError),
        ):
            with self.subTest(entries=entries):
                with zipfile.ZipFile(self.path, "w") as archive:
                    for member, version in entries:
                        archive.writestr(member, json.dumps({"format_version": version}))
                original = self.path.read_bytes()
                with self.assertRaises(error):
                    load_project(self.path)
                with self.assertRaises(error):
                    save_project(self.path, self.ordinary)
                self.assertEqual(self.path.read_bytes(), original)

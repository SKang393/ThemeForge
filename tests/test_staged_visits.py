from copy import deepcopy
from dataclasses import replace
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from themeforge.analysis_types import AnalysisResult, AnalysisSettings, Theme, TranscriptDocument
from themeforge.audit import create_audit_event
from themeforge.case_recovery import older_working_copy, recovered_cases
from themeforge.project_io import (
    CASE_MEMBER, CASE_NULLABLE_PROFILE, STAGED_NULLABLE_PROFILE, ProjectChanged,
    ProjectCopyRequired, ProjectState, load_project, save_project,
)
from themeforge.staged_visits import prepare_pending_withdrawal, prepare_staged_visits


class StagedVisitTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.one = self.root / "first.txt"
        self.two = self.root / "followup.txt"
        self.one.write_bytes(b"Bird: I counted two knots.\r\n")
        self.two.write_bytes("Bird: \U0001fab6 \ub9e4\ub4ed\uc774 \ud480\ub838\uc5b4\uc694.\r\n".encode("utf-16"))
        self.original = self.root / "study.tfproj"
        self.output = self.root / "expanded.tfproj"
        doc = TranscriptDocument(self.one.name, self.one.read_text(), "Earlier memo", "Pine")
        result = AnalysisResult("1", 1, 0, [Theme("M01", "Knots", "", [], 0, None)], [])
        save_project(self.original, ProjectState((self.one,), None, (doc,), AnalysisSettings(), result))
        self.master = load_project(self.original)
        self.frozen = self.original.read_bytes()

    def stage(self):
        return prepare_staged_visits(self.master, [self.two])

    def publish(self):
        staged, revisions = self.stage()
        save_project(self.output, staged, new_only=True,
                     input_revisions=((self.original, self.master.file_revision), *revisions))
        return load_project(self.output)

    def payload(self, path):
        with zipfile.ZipFile(path) as archive:
            self.assertEqual(archive.namelist(), [CASE_MEMBER])
            return json.loads(archive.read(CASE_MEMBER))

    def withdrawal_input(self):
        morning, evening = self.root / "morning.txt", self.root / "evening.txt"
        morning.write_text("Bird: I folded the reed mat.")
        evening.write_text("Bird: I placed it on the shelf.")
        staged, _ = prepare_staged_visits(self.master, [morning, self.two, evening])
        event = create_audit_event(actor="Finch", action="theme_memo_edited", target_type="theme",
                                   target_id="M01", details="Earlier review memo.")
        save_project(self.output, replace(staged, researcher_name="Finch", audit_events=(event,)), new_only=True)
        return load_project(self.output)

    def test_withdrawal_preserves_the_previous_pass_and_remaining_snapshots_in_order(self):
        prior = self.withdrawal_input()
        first, selected, last = prior.pending_visits
        archive = self.output.read_bytes()
        payload = self.payload(self.output)
        before = selected.path.read_bytes()
        # Original files outside the selected visit can be offline.
        first.path.unlink()
        self.one.unlink()
        prepared, guards = prepare_pending_withdrawal(self.output, prior.file_revision, selected)
        self.assertEqual(prepared.pending_visits, (first, last))
        self.assertEqual(prepared.audit_events, prior.audit_events)
        self.assertEqual(guards[0], (self.output, prior.file_revision))
        self.assertEqual(guards[1][0], selected.path)
        destination = self.root / "retained.tfproj"
        self.assertFalse(destination.exists())
        with patch("themeforge.project_io.tempfile.NamedTemporaryFile") as temporary:
            with self.assertRaises(FileExistsError):
                save_project(self.output, prepared, new_only=True, input_revisions=guards, transcript_inputs=(selected.path,))
            temporary.assert_not_called()
        save_project(destination, prepared, new_only=True, input_revisions=guards, transcript_inputs=(selected.path,))
        del payload["pending_visits"][1]
        self.assertEqual(self.payload(destination), payload)
        self.assertTrue(load_project(destination).is_case_master)
        self.assertEqual(load_project(destination).pending_visits, (first, last))
        self.assertEqual(self.output.read_bytes(), archive)
        self.assertEqual(selected.path.read_bytes(), before)

    def test_withdrawal_requires_a_current_pending_snapshot_and_source(self):
        prior = self.withdrawal_input()
        selected = prior.pending_visits[1]
        for wrong in (replace(selected, text="Different saved text"), replace(selected, path=self.one)):
            with self.subTest(wrong=wrong), self.assertRaisesRegex(ValueError, "saved pending snapshot"):
                prepare_pending_withdrawal(self.output, prior.file_revision, wrong)
        self.two.write_text("A different visit.")
        with self.assertRaisesRegex(ValueError, "differs from the saved transcript"):
            prepare_pending_withdrawal(self.output, prior.file_revision, selected)
        self.two.unlink()
        with self.assertRaisesRegex(ValueError, "Could not verify transcript"):
            prepare_pending_withdrawal(self.output, prior.file_revision, selected)
        with self.assertRaises(ProjectChanged):
            prepare_pending_withdrawal(self.output, b"stale", selected)

    def test_withdrawal_refuses_the_last_pending_visit_and_changes_during_confirmation(self):
        prior = self.publish()
        with self.assertRaisesRegex(ValueError, "last pending visit.*before staging"):
            prepare_pending_withdrawal(self.output, prior.file_revision, prior.pending_visits[0])
        self.output.unlink()
        prior = self.withdrawal_input()
        selected = prior.pending_visits[1]
        prepared, guards = prepare_pending_withdrawal(self.output, prior.file_revision, selected)
        destination = self.root / "retained.tfproj"
        raw = selected.path.read_bytes()
        selected.path.write_bytes(selected.text.encode("utf-8"))
        with patch("themeforge.project_io.tempfile.NamedTemporaryFile") as temporary:
            with self.assertRaisesRegex(ValueError, "transcript changed.*" + selected.name):
                save_project(destination, prepared, new_only=True, input_revisions=guards, transcript_inputs=(selected.path,))
            temporary.assert_not_called()
        self.assertFalse(destination.exists())
        selected.path.write_bytes(raw)
        self.output.write_bytes(b"Different project")
        with self.assertRaises(ProjectChanged):
            save_project(destination, prepared, new_only=True, input_revisions=guards, transcript_inputs=(selected.path,))
        self.assertFalse(destination.exists())
        self.assertEqual(self.output.read_bytes(), b"Different project")

    def test_withdrawal_preserves_a_destination_created_during_save(self):
        prior = self.withdrawal_input()
        selected = prior.pending_visits[1]
        prepared, guards = prepare_pending_withdrawal(self.output, prior.file_revision, selected)
        destination = self.root / "retained.tfproj"
        other = b"A project saved by someone else."
        with patch("themeforge.project_io.os.fsync", side_effect=lambda _: destination.write_bytes(other)):
            with self.assertRaises(FileExistsError):
                save_project(destination, prepared, new_only=True, input_revisions=guards, transcript_inputs=(selected.path,))
        self.assertEqual(destination.read_bytes(), other)
        self.assertEqual(load_project(self.output), prior)
        self.assertFalse(list(self.root.glob("*.tmp")))

    def test_withdrawal_refuses_to_drop_unknown_archive_content_or_nested_metadata(self):
        self.withdrawal_input()
        original = self.payload(self.output)
        for extra in ("member", "field", "changed_type"):
            with self.subTest(extra=extra):
                payload = deepcopy(original)
                with zipfile.ZipFile(self.output, "w") as archive:
                    if extra == "member":
                        archive.writestr("annotation.txt", "Earlier note")
                    elif extra == "field":
                        payload["documents"][0]["annotation"] = "Earlier note"
                    else:
                        payload["settings"]["min_theme_size"] = True
                    archive.writestr(CASE_MEMBER, json.dumps(payload))
                prior = load_project(self.output)
                before = self.output.read_bytes()
                with self.assertRaisesRegex(ValueError, "cannot be preserved"):
                    prepare_pending_withdrawal(self.output, prior.file_revision, prior.pending_visits[1])
                self.assertEqual(self.output.read_bytes(), before)

    def test_withdrawal_accepts_an_omitted_default_from_an_earlier_staged_project(self):
        self.withdrawal_input()
        payload = self.payload(self.output)
        del payload["settings"]["language_mode"]
        with zipfile.ZipFile(self.output, "w") as archive:
            archive.writestr(CASE_MEMBER, json.dumps(payload))
        prior = load_project(self.output)
        prepared, _ = prepare_pending_withdrawal(self.output, prior.file_revision, prior.pending_visits[1])
        self.assertEqual(prepared.settings.language_mode, "Auto")

    def test_pending_uses_separate_profile_and_preserves_every_prior_payload_field(self):
        staged = self.publish()
        expected = self.payload(self.original)
        actual = self.payload(self.output)
        self.assertEqual(actual.pop("format_version"), STAGED_NULLABLE_PROFILE)
        self.assertEqual(expected.pop("format_version"), CASE_NULLABLE_PROFILE)
        self.assertEqual(actual.pop("pending_visits"), [{"path": str(self.two), "text": self.two.read_text("utf-16")}])
        self.assertEqual(expected, actual)
        self.assertEqual(self.original.read_bytes(), self.frozen)
        self.assertEqual(staged.pending_visits[0].name, self.two.name)
        with self.assertRaises(ProjectCopyRequired):
            save_project(self.original, staged, expected_revision=self.master.file_revision)
        self.assertEqual(self.original.read_bytes(), self.frozen)
        ungrouped = older_working_copy(self.master)
        self.assertEqual(recovered_cases(ungrouped, self.master), self.master)
        with self.assertRaisesRegex(ValueError, "pending visits"):
            older_working_copy(staged)
        with self.assertRaisesRegex(ValueError, "pending visits"):
            recovered_cases(ungrouped, staged)
        self.one.unlink()
        self.two.unlink()
        cleared = replace(staged, documents=(replace(staged.documents[0], case_label=""),))
        save_project(self.output, cleared, expected_revision=staged.file_revision)
        reopened = load_project(self.output)
        self.assertEqual(reopened, cleared)
        self.assertTrue(reopened.is_case_master)
        self.assertEqual(self.payload(self.output)["format_version"], STAGED_NULLABLE_PROFILE)

    def test_conflicting_inputs_never_publish_or_mutate_the_original(self):
        folder = self.root / "later"
        folder.mkdir()
        collision = folder / self.one.name.upper()
        collision.write_text("Conflicting transcript.")
        copy = folder / "renamed.txt"
        copy.write_bytes(self.one.read_bytes())
        scenarios = (
            ([self.one], "same source path"),
            ([collision], "filename"), ([copy], "identical transcript"),
            ([self.two, self.two], "same source path"), ([self.original], "supported format"),
        )
        for paths, reason in scenarios:
            with self.subTest(paths=paths), self.assertRaisesRegex(ValueError, reason):
                prepare_staged_visits(self.master, paths)
        self.one.write_text("A change after recording.")
        with self.assertRaisesRegex(ValueError, "differs from the saved transcript"):
            self.stage()
        self.one.rename(self.root / "moved.txt")
        with self.assertRaisesRegex(ValueError, "first.txt"):
            self.stage()
        self.assertFalse(self.output.exists())
        self.assertEqual(self.master.pending_visits, ())
        self.assertEqual(self.original.read_bytes(), self.frozen)

    def test_hard_link_cannot_add_the_same_file_under_another_name(self):
        alias = self.root / "alias.txt"
        try:
            os.link(self.one, alias)
        except OSError as exc:
            self.skipTest(f"Test volume cannot create a hard link: {exc}")
        with self.assertRaisesRegex(ValueError, "same source path"):
            prepare_staged_visits(self.master, [alias])

    def test_changed_inputs_at_publication_keep_both_projects_safe(self):
        for edited in (self.original, self.one, self.two):
            with self.subTest(edited=edited):
                staged, sources = self.stage()
                old = edited.read_bytes()
                guards = ((self.original, self.master.file_revision), *sources)
                with patch("themeforge.project_io.os.fsync", side_effect=lambda _: edited.write_bytes(old + b"!")):
                    error = ProjectChanged if edited == self.original else ValueError
                    with self.assertRaises(error):
                        save_project(self.output, staged, new_only=True, input_revisions=guards)
                self.assertEqual(edited.read_bytes(), old + b"!")
                self.assertFalse(self.output.exists())
                self.assertFalse(list(self.root.glob("*.tmp")))
                edited.write_bytes(old)

    def test_changed_project_is_refused_before_decoding_its_replacement(self):
        revision = self.master.file_revision
        self.assertEqual(load_project(self.original, expected_revision=revision), self.master)
        self.original.write_bytes(b"An unrelated file")
        with self.assertRaises(ProjectChanged):
            load_project(self.original, expected_revision=revision)
        with self.assertRaises(zipfile.BadZipFile):
            load_project(self.original)
        with zipfile.ZipFile(self.original, "w") as archive:
            archive.writestr(CASE_MEMBER, "{")
        with self.assertRaises(ProjectChanged):
            load_project(self.original, expected_revision=revision)
        with self.assertRaises(json.JSONDecodeError):
            load_project(self.original)

    def test_wrong_profile_or_ambiguous_embedded_visits_refuse_reopen(self):
        self.publish()
        payload = self.payload(self.output)
        visit, = payload["pending_visits"]
        for changed in (
            {"format_version": CASE_NULLABLE_PROFILE}, {"pending_visits": []},
            {"pending_visits": [dict(visit, case_label="Pine")]},
            {"pending_visits": [visit, visit]}, {"pending_visits": [dict(visit, path=str(self.one))]},
            {"pending_visits": [dict(visit, text=self.master.documents[0].text)]},
            {"transcript_paths": [str(self.two)]}, {"documents": ["invalid"]},
        ):
            with self.subTest(changed=changed):
                with zipfile.ZipFile(self.output, "w") as package:
                    package.writestr(CASE_MEMBER, json.dumps({**payload, **changed}))
                with self.assertRaises(ValueError):
                    load_project(self.output)

    def test_supported_extension_does_not_turn_a_project_or_corrupt_docx_into_a_visit(self):
        for name in ("other.txt", "other.docx"):
            candidate = self.root / name
            candidate.write_bytes(self.frozen)
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, name):
                prepare_staged_visits(self.master, [candidate])
        with zipfile.ZipFile(candidate, "w") as archive:
            archive.writestr("word/document.xml", "<document")
        with self.assertRaisesRegex(ValueError, candidate.name):
            prepare_staged_visits(self.master, [candidate])

    def test_only_a_completed_full_pass_can_consume_pending_visits_in_place(self):
        staged = self.publish()
        visit, = staged.pending_visits
        prior = self.output.read_bytes()
        full = replace(staged, transcript_paths=staged.transcript_paths + (visit.path,),
                       documents=staged.documents + (TranscriptDocument(visit.name, visit.text),),
                       result=replace(staged.result, document_count=2), pending_visits=())
        with self.assertRaisesRegex(ValueError, "explicit analysis"):
            save_project(self.output, full, expected_revision=staged.file_revision)
        run = create_audit_event(actor="Reader", action="analysis_run", target_type="project",
                                 target_id=self.output.name, details="documents=2")
        full = replace(full, audit_events=(*staged.audit_events, run))
        wrong = replace(full, documents=(*staged.documents, TranscriptDocument(visit.name, "Different")))
        with self.assertRaisesRegex(ValueError, "explicit analysis"):
            save_project(self.output, wrong, expected_revision=staged.file_revision)
        self.assertEqual(self.output.read_bytes(), prior)
        save_project(self.output, full, expected_revision=staged.file_revision)
        self.assertEqual(load_project(self.output), full)
        self.assertEqual(self.payload(self.output)["format_version"], CASE_NULLABLE_PROFILE)
        self.assertEqual(self.original.read_bytes(), self.frozen)

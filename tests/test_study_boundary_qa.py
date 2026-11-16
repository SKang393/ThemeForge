"""Synthetic tokenizer/worker fixtures with actual withdrawn Tk recovery UI."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from themeforge.analysis_types import TranscriptDocument
from themeforge.study_batches import prepare_study
from themeforge import study_encoder as encoder
from themeforge import study_segments as segments
from themeforge.study_boundary_qa import main, study_boundary_smoke
from tests import test_study_segments as segment_fixtures


class StudyBoundaryQATests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'tmp')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.tok = segment_fixtures.Tokens()
        self.calls = []
        self.raw = dict(artifact_manifest_sha256='a'*64,
            runtime=dict(numpy_version='fake-numpy', version='fake-ort', tokenizers_version='fake-tokenizers'))
        self.profile = self.root/'profile.json'
        self.profile.write_text(json.dumps(self.raw), encoding='utf-8')
        for guard in (
            patch.object(encoder.encoder, 'verify_profile', return_value=(self.raw, None, None, 2)),
            patch.object(segments, '_tokenizer', return_value=self.tok),
            patch('subprocess.Popen', side_effect=AssertionError('Synthetic boundary QA never starts a process.'))):
            guard.start()
            self.addCleanup(guard.stop)
        parent = ('Unresolved parent note.\n' + ''.join('I keep item %s when consent applies. \n' % i for i in range(60))
            + 'Participant 3 Responses, Synthetic\n'
            + ''.join('Another source item %s remains provisional.\n' % i for i in range(45)))
        self.study = prepare_study([
            TranscriptDocument('synthetic-parent', 'Interviewer: What happens?\nP1: '+parent),
            TranscriptDocument('synthetic-other-a', 'Interviewer: What happens?\nP1: We preserve our samples.'),
            TranscriptDocument('synthetic-other-b', 'Interviewer: What happens?\nP1: We share our measurements.')])
        def original_worker(packet, *args, **kwargs):
            if packet['nodes'][0]['source_name'] == 'synthetic-parent':
                return dict(status='failed', error='Original explicit boundary/token failure.',
                            resources=dict(owned_processes_closed=True, synthetic_no_process=True))
            return self.worker(packet, *args, **kwargs)
        with patch.object(encoder, '_run_chunk', side_effect=original_worker):
            self.original = encoder.encode_study(self.study, self.profile, output_directory=self.root/'original')
        self.parent_id = next(uid for uid, row in self.original['coverage'].items() if row['status'] == 'failed')
        self.prepared = segments.prepare_segment_recovery(self.original, self.parent_id, self.profile, tokenizer=self.tok)
        self.assertGreater(len(self.prepared['plan']['segments']), 2)
        self.recovery = self.root/'prepared.json'
        self.recovery.write_text(segments.export_segment_recovery_json(self.prepared), encoding='utf-8')
        self.study_path = self.root/'full-study.json'
        self.study_path.write_text(json.dumps(self.study), encoding='utf-8')
        self.calls.clear()
        guard = patch.object(encoder, '_run_chunk', side_effect=self.worker)
        guard.start()
        self.addCleanup(guard.stop)

    def worker(self, *args, **kwargs):
        # Reuse the repository's deterministic fake recipe, not an encoder.
        return segment_fixtures.StudySegmentTests.worker(self, *args, **kwargs)

    def run_qa(self, recovery, output, maximum):
        code = study_boundary_smoke(recovery, self.profile, output, study=self.study_path,
                                   max_new_cores=maximum, deadline_seconds=30)
        report = json.loads((output/'QA.json').read_text(encoding='utf-8'))
        self.assertEqual(code, 0, report.get('error'))
        return report

    def test_actual_hidden_ui_partial_then_resume_only_remaining_cores(self):
        before = self.recovery.read_bytes(), self.study_path.read_bytes(), self.profile.read_bytes()
        first = self.run_qa(self.recovery, self.root/'first-qa', 1)
        first_ids = set(self.calls)
        self.assertEqual(len(first_ids), 1)
        self.assertEqual(first['new_completed_cores'], 1)
        self.assertEqual(first['original_completed_vectors_preserved'], 2)
        self.assertEqual(first['original_failed_batches_preserved'], 1)
        self.assertEqual(first['full_UI_rows'], len(self.study['units']))
        self.assertTrue(first['actual_hidden_Tk'] and first['exact_UI_parent_and_recovery_overlay'])
        self.assertTrue(first['export_JSON_reopen_exact'] and first['export_HTML_verified'])
        partial_path = Path(first['recovery_export_path'])
        partial_bytes = partial_path.read_bytes()
        self.calls.clear()
        second = self.run_qa(partial_path, self.root/'resume-qa', 16)
        self.assertFalse(first_ids & set(self.calls))
        self.assertEqual(second['previous_completed_cores'], 1)
        self.assertEqual(second['new_completed_cores'], len(self.prepared['plan']['segments']) - 1)
        self.assertEqual(second['recovery_coverage']['status'], 'covered_via_segments_not_attribution_validated')
        self.assertFalse(second['parent_vector_created'] or second['attribution_validated'])
        self.assertTrue(second['all_completed_core_tokens_exact_one_window'])
        self.assertTrue(second['full_parent_character_reconstruction_exact'])
        self.assertEqual(second['observed_owned_descendants_remaining'], [])
        self.assertFalse(second['owned_worker_remaining'])
        self.assertEqual(partial_path.read_bytes(), partial_bytes)
        self.assertEqual((self.recovery.read_bytes(), self.study_path.read_bytes(), self.profile.read_bytes()), before)

    def test_complete_envelope_allows_zero_new_workers(self):
        completed = segments.run_segment_recovery(self.prepared, self.profile,
            output_directory=self.root/'complete', max_units_per_run=16)
        path = self.root/'completed.json'
        path.write_text(segments.export_segment_recovery_json(completed), encoding='utf-8')
        self.calls.clear()
        report = self.run_qa(path, self.root/'zero-new-qa', 1)
        self.assertEqual(self.calls, [])
        self.assertEqual(report['new_completed_cores'], 0)
        self.assertEqual(report['new_worker_batches'], 0)
        self.assertTrue(report['completed_segment_vectors_not_repeated'])

    def test_failed_core_work_is_not_reported_as_success_and_checkpoint_remains(self):
        output = self.root/'failed-qa'
        with patch.object(encoder, '_run_chunk', return_value=dict(status='memory_limit',
                error='Synthetic available-RAM stop.', resources=dict(owned_processes_closed=True, worker_started=False))):
            code = study_boundary_smoke(self.recovery, self.profile, output,
                                       max_new_cores=1, deadline_seconds=30)
        report = json.loads((output/'QA.json').read_text(encoding='utf-8'))
        self.assertEqual(code, 1)
        self.assertEqual(report['status'], 'failed')
        self.assertFalse(report['owned_worker_remaining'])
        saved = next((output/'work').glob('segment-recovery-*/segment-recovery.json'))
        failed = segments.load_segment_recovery(saved)
        self.assertEqual(failed['original_encoder_ledger'], self.original)
        self.assertGreater(failed['recovery_coverage']['segment_counts'].get('pending', 0), 0)

    def test_wrong_profile_mismatched_source_and_invalid_bounds_rejected_before_work(self):
        other = self.root/'other-profile.json'
        other.write_text(json.dumps(self.raw, indent=2), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'original exact encoder profile'):
            study_boundary_smoke(self.recovery, other, self.root/'wrong-profile')
        wrong = prepare_study([TranscriptDocument('different', 'Participant 1: Different source.')])
        path = self.root/'wrong-study.json'
        path.write_text(json.dumps(wrong), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'same unchanged source parent'):
            study_boundary_smoke(self.recovery, self.profile, self.root/'wrong-source', study=path)
        for cap in (0, 17, True):
            with self.subTest(cap=cap), self.assertRaises(ValueError):
                study_boundary_smoke(self.recovery, self.profile, self.root/'invalid', max_new_cores=cap)
        self.assertEqual(self.calls, [])

    def test_cli_routes_explicit_paths_and_core_cap(self):
        with patch('themeforge.study_boundary_qa.study_boundary_smoke', return_value=0) as smoke:
            result = main(['--recovery','r','--profile','p','--out','o','--study','s','--max-new-cores','16'])
        self.assertEqual(result, 0)
        smoke.assert_called_once_with('r', 'p', 'o', study='s', max_new_cores=16)


if __name__ == '__main__':
    unittest.main()

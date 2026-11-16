"""Synthetic hidden Tk QA fixtures; no ONNX, tokenizer or subprocess runs."""
from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from themeforge import study_encoder as encoder
from themeforge.analysis_types import TranscriptDocument
from themeforge.study_batches import prepare_study
from themeforge.study_encoder_qa import main, study_encoder_smoke, _worker_check
import time


class StudyEncoderQATests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.study = prepare_study([TranscriptDocument('synthetic-%s' % i,
            'Interviewer: What do you retain?\n' + '\n'.join(
                'Participant %s: We archive measurement %s from project %s.' % (j, j, i)
                for j in range(1, 10))) for i in range(3)])
        self.study_path = self.root / 'study.json'
        self.study_path.write_text(json.dumps(self.study), encoding='utf-8')
        self.raw = dict(artifact_manifest_sha256='a' * 64, runtime=dict(
            numpy_version='synthetic-numpy', version='synthetic-ort', tokenizers_version='synthetic-tokenizer'))
        self.profile = self.root / 'profile.json'
        self.profile.write_text(json.dumps(self.raw), encoding='utf-8')
        verifier = patch.object(encoder.encoder, 'verify_profile', return_value=(self.raw, None, None, 2))
        verifier.start()
        self.addCleanup(verifier.stop)
        subprocess_guard = patch('subprocess.Popen', side_effect=AssertionError('No synthetic QA process starts.'))
        subprocess_guard.start()
        self.addCleanup(subprocess_guard.stop)
        eligible = [u['id'] for u in self.study['units'] if u['eligible']]
        self.failed = set(eligible[:16])
        with patch.object(encoder, '_run_chunk', side_effect=self.worker):
            first = encoder.encode_study(self.study, self.profile, output_directory=self.root / 'first')
            self.assertEqual(first['coverage_counts']['failed'], 16)
            retried = encoder.retry_failed_units(first, eligible[:15], reason='Synthetic retry retaining original failed attempts.')
            self.failed = {eligible[15]}
            self.ledger = encoder.encode_study(self.study, self.profile, prior=retried,
                                             output_directory=self.root / 'retry')
        self.assertEqual(self.ledger['coverage_counts'], {'skipped':3, 'completed':26, 'failed':1})
        self.ledger_path = self.root / 'ledger.json'
        self.ledger_path.write_text(encoder.export_encoder_ledger_json(self.ledger), encoding='utf-8')

    def worker(self, packet, path, pin, output, **kwargs):
        if any(node['id'] in self.failed for node in packet['nodes']):
            return dict(status='failed', error='Synthetic explicit token recipe failure.',
                        resources=dict(owned_processes_closed=True))
        diagnostics = [dict(node_id=node['id'], text_sha256=sha256(node['text'].encode()).hexdigest(),
            truncated_tokens=0, payload_tokens=3, chunks=[dict(payload_token_start=0, payload_token_end=3,
                source_char_start=0, source_char_end=len(node['text']), payload_tokens=3,
                input_ids=[101, 201, 202, 203, 102])]) for node in packet['nodes']]
        provenance = dict(adapter=encoder.encoder.VERSION, repository=encoder.encoder.MODEL_REPOSITORY,
            revision=encoder.encoder.MODEL_REVISION, model_sha256=encoder.encoder.MODEL_SHA256,
            tokenizer_sha256=encoder.encoder.TOKENIZER_SHA256, profile_sha256=pin,
            provider='CPUExecutionProvider', intra_op_threads=2, inter_op_threads=1, dimension=384,
            pooling=encoder.POOLING, original_text_preserved=True, input_fields=['node.text'],
            training=False, network_calls=0, tokenization=diagnostics,
            runtime_versions={'numpy':'synthetic-numpy', 'onnxruntime':'synthetic-ort', 'tokenizers':'synthetic-tokenizer'})
        return dict(status='completed', encoded=dict(vectors=[[1.] + [0.] * 383 for _ in packet['nodes']],
            provenance=provenance), resources=dict(owned_processes_closed=True, synthetic_no_process=True))

    def test_real_hidden_tk_full_queue_and_exports_preserve_sixteen_failures(self):
        before = {p:p.read_bytes() for p in (self.study_path, self.profile, self.ledger_path)}
        output = self.root / 'qa'
        result = study_encoder_smoke(self.study_path, self.profile, self.ledger_path, output,
                                    deadline_seconds=30)
        report = json.loads((output / 'QA.json').read_text(encoding='utf-8'))
        self.assertEqual(result, 0, report.get('error'))
        self.assertEqual(report['status'], 'passed')
        self.assertTrue(report['actual_hidden_Tk'] and report['actual_StudyDialog_neighbors'])
        self.assertEqual(report['full_UI_rows'], 30)
        self.assertEqual(report['encoded_vectors'], 26)
        self.assertEqual(report['source_count'], 3)
        self.assertEqual(report['encoding_coverage'], {'skipped':3, 'completed':26, 'failed':1})
        self.assertEqual(report['retrieval_coverage'], {'skipped_encoding':3, 'complete':26, 'failed_encoding':1})
        self.assertEqual(report['historical_failed_batches_preserved'], 16)
        self.assertEqual(report['recovery_events_preserved'], 1)
        self.assertEqual(report['new_study_encoder_workers'], 0)
        self.assertEqual(report['new_LLM_calls'], 0)
        self.assertEqual(report['blocked_startup_attempts'], 0)
        self.assertTrue(report['selected_source_and_evidence_exact'])
        self.assertTrue(report['export_JSON_reopen_exact'] and report['export_HTML_verified'])
        self.assertFalse(report['owned_worker_remaining'])
        self.assertEqual(report['observed_owned_descendants_remaining'], [])
        self.assertEqual({p:p.read_bytes() for p in before}, before)

    def test_production_neighbor_failure_is_reported_and_cleaned(self):
        output = self.root / 'failed-qa'
        with patch('themeforge.study_neighbors.build_study_neighbors', side_effect=RuntimeError('Synthetic scan stopped.')):
            result = study_encoder_smoke(self.study_path, self.profile, self.ledger_path, output,
                                        deadline_seconds=30)
        report = json.loads((output / 'QA.json').read_text(encoding='utf-8'))
        self.assertEqual(result, 1)
        self.assertEqual(report['status'], 'failed')
        self.assertIn('Synthetic scan stopped.', report['error'])
        self.assertFalse(report['owned_worker_remaining'])
        self.assertTrue(report['app_closed'])

    def test_fresh_absolute_inputs_and_zero_pending_required(self):
        existing = self.root / 'existing'
        existing.mkdir()
        with self.assertRaisesRegex(ValueError, 'fresh'):
            study_encoder_smoke(self.study_path, self.profile, self.ledger_path, existing)
        with self.assertRaisesRegex(ValueError, 'absolute'):
            study_encoder_smoke('relative.json', self.profile, self.ledger_path, self.root / 'unused')
        failed_uid = next(uid for uid, state in self.ledger['coverage'].items() if state['status'] == 'failed')
        pending = encoder.retry_failed_units(self.ledger, [failed_uid], reason='Synthetic pending work.')
        pending_path = self.root / 'pending.json'
        pending_path.write_text(encoder.export_encoder_ledger_json(pending), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'zero pending'):
            study_encoder_smoke(self.study_path, self.profile, pending_path, self.root / 'pending-qa')
        self.assertFalse((self.root / 'pending-qa').exists())

    def test_cli_routes_required_inputs_and_explicit_optional_worker(self):
        with patch('themeforge.study_encoder_qa.study_encoder_smoke', return_value=0) as smoke:
            self.assertEqual(main(['--study','s','--profile','p','--ledger','l','--out','o','--worker-check']), 0)
        smoke.assert_called_once_with('s', 'p', 'l', 'o', worker_check=True)

    def test_optional_worker_verification_checks_existing_tokens_and_vectors(self):
        with patch.object(encoder, '_run_chunk', side_effect=self.worker) as worker:
            report = _worker_check(self.study, self.ledger, self.profile, self.root,
                                   time.monotonic() + 30)
        self.assertEqual(worker.call_count, 1)
        self.assertEqual(report['new_study_coverage'], 0)
        self.assertTrue(report['exact_token_diagnostics'] and report['exact_token_windows'])
        self.assertEqual(report['vector_maximum_absolute_error'], 0)
        self.assertEqual(len(worker.call_args.args[0]['nodes']), 1)


if __name__ == '__main__':
    unittest.main()

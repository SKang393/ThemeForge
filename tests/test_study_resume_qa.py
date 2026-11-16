"""Synthetic hidden Tk resume QA; no native model, network, or process starts."""
from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from themeforge.analysis_types import TranscriptDocument
from themeforge.study_batches import prepare_study
from themeforge.study_resume_qa import main, study_resume_smoke
from tests.test_study_session import SessionClient


class StudyResumeQATests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'tmp')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.study = prepare_study([TranscriptDocument('synthetic-%02d' % i,
            'Interviewer 1: What do you preserve?\nParticipant 1: We preserve a backup copy from project %s.' % i)
            for i in range(25)])
        self.study_path = self.root/'study.json'
        self.study_path.write_text(json.dumps(self.study), encoding='utf-8')
        self.exe, self.model = self.root/'llama-server.exe', self.root/'synthetic.gguf'
        self.exe.write_bytes(b'MZ-synthetic-not-executable')
        self.model.write_bytes(b'GGUF-synthetic-not-model')
        self.profile = self.root/'profile.json'
        self.profile.write_text(json.dumps(dict(executable_path=str(self.exe),
            executable_sha256=sha256(self.exe.read_bytes()).hexdigest(), model_path=str(self.model),
            model_sha256=sha256(self.model.read_bytes()).hexdigest(), limits=dict(memory_limit_bytes=1024**3))), encoding='utf-8')
        self.clients = []
        guard = patch('subprocess.Popen', side_effect=AssertionError('No process starts in synthetic QA.'))
        guard.start()
        self.addCleanup(guard.stop)

    def factory(self, config, **kwargs):
        client = SessionClient()
        client.config = config
        client.native_calls = []
        client.was_started = False
        client.was_closed = False
        def start(cancel):
            client.was_started = True
            client.process = object()
        def close():
            client.was_closed = True
            client.process = None
        original_measure = client.measure_messages
        def measure(messages, **kwargs):
            result = original_measure(messages, **kwargs)
            client.native_calls.extend([dict(endpoint='/apply-template'), dict(endpoint='/tokenize')])
            return result
        client.start, client.close, client.measure_messages = start, close, measure
        self.clients.append(client)
        return client

    def test_full_hidden_tk_two_period_resume_retains_native_measurement_and_no_generation(self):
        before = self.study_path.read_bytes(), self.profile.read_bytes()
        output = self.root/'qa'
        code = study_resume_smoke(self.study_path, self.profile, output,
                                 client_factory=self.factory, deadline_seconds=30)
        report = json.loads((output/'QA.json').read_text(encoding='utf-8'))
        self.assertEqual(code, 0, report.get('error'))
        self.assertEqual(report['status'], 'passed')
        self.assertTrue(report['actual_hidden_Tk'])
        self.assertTrue(report['synthetic_test_only'])
        self.assertFalse(report['real_native_requested'])
        self.assertEqual(report['imported_turns'], 50)
        self.assertEqual(report['eligible_original_turns'], 25)
        self.assertEqual(report['native_prompt_measurements'], 2)
        self.assertEqual(report['generation_calls'], 0)
        self.assertEqual([p['cumulative_native_measurements'] for p in report['periods']], [1, 2])
        self.assertEqual([p['new_native_measurements'] for p in report['periods']], [1, 1])
        self.assertTrue(all(p['checkpoint_status'] == 'interrupted' for p in report['periods']))
        self.assertTrue(all(p['owned_helper_closed'] and p['exported_and_reopened_exactly'] for p in report['periods']))
        self.assertTrue(report['cached_request_not_remeasured'])
        self.assertTrue(report['source_app_native_request_bindings_verified'])
        self.assertTrue(report['main_project_unchanged'] and report['original_inputs_unchanged'])
        self.assertTrue(report['owned_clients_closed'])
        self.assertFalse(report['owned_worker_remaining'])
        self.assertEqual(len(self.clients), 2)
        self.assertEqual([len(c.measured) for c in self.clients], [1, 1])
        self.assertTrue(all(c.was_started and c.was_closed and not c.generated for c in self.clients))
        self.assertEqual((self.study_path.read_bytes(), self.profile.read_bytes()), before)

    def test_startup_failure_is_reported_and_client_closed(self):
        def factory(config, **kwargs):
            client = self.factory(config, **kwargs)
            def fail(cancel):
                raise RuntimeError('Synthetic startup blocked.')
            client.start = fail
            return client
        output = self.root/'startup-failure'
        code = study_resume_smoke(self.study_path, self.profile, output,
                                 client_factory=factory, deadline_seconds=30)
        report = json.loads((output/'QA.json').read_text(encoding='utf-8'))
        self.assertEqual(code, 1)
        self.assertEqual(report['status'], 'failed')
        self.assertTrue(report['owned_clients_closed'])
        self.assertTrue(self.clients[0].was_closed)
        failures = list(output.glob('period-1/study-*/failure.json'))
        self.assertEqual(len(failures), 1)
        self.assertIn('Synthetic startup blocked.', json.loads(failures[0].read_text())['error'])

    def test_cancelled_measurement_remains_pending_without_false_success(self):
        def factory(config, **kwargs):
            client = self.factory(config, **kwargs)
            original_start = client.start
            def start(cancel):
                original_start(cancel)
                cancel.set()
            client.start = start
            return client
        output = self.root/'cancelled'
        code = study_resume_smoke(self.study_path, self.profile, output,
                                 client_factory=factory, deadline_seconds=30)
        report = json.loads((output/'QA.json').read_text(encoding='utf-8'))
        self.assertEqual(code, 1)
        self.assertEqual(report['status'], 'failed')
        planning_path = next(output.glob('period-1/study-*/planning/planning-session.json'))
        planning = json.loads(planning_path.read_text())
        self.assertEqual(planning['study'], self.study)
        self.assertEqual(planning['checkpoint']['status'], 'interrupted')
        self.assertEqual(planning['checkpoint']['measurements'], {})
        self.assertTrue(self.clients[0].was_closed)
        self.assertFalse(self.clients[0].generated)

    def test_fresh_output_absolute_inputs_and_nontrivial_study_required(self):
        existing = self.root/'existing'
        existing.mkdir()
        with self.assertRaisesRegex(ValueError, 'fresh'):
            study_resume_smoke(self.study_path, self.profile, existing, client_factory=self.factory)
        with self.assertRaisesRegex(ValueError, 'absolute'):
            study_resume_smoke('relative.json', self.profile, self.root/'unused', client_factory=self.factory)
        small = prepare_study([TranscriptDocument('one', 'Participant 1: We archive our samples.')])
        small_path = self.root/'small.json'
        small_path.write_text(json.dumps(small), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'at least three'):
            study_resume_smoke(small_path, self.profile, self.root/'too-small', client_factory=self.factory)
        self.assertFalse(self.clients)
        self.assertFalse((self.root/'too-small').exists())

    def test_cli_routes_only_explicit_required_inputs(self):
        with patch('themeforge.study_resume_qa.study_resume_smoke', return_value=0) as smoke:
            self.assertEqual(main(['--study','s','--profile','p','--out','o']), 0)
        smoke.assert_called_once_with('s', 'p', 'o')


if __name__ == '__main__':
    unittest.main()

"""Synthetic planning replay and checkpoint proofs, no model or encoder runs."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from themeforge.analysis_types import TranscriptDocument
from themeforge.study_batches import (NativePrompt, PlanningInterrupted, TokenBudget,
                                     digest, plan_batches, prepare_study, validate_plan)
from themeforge.study_planning import (PlanningCheckpoint, planning_identity,
                                      validate_planning_identity)


def imported(count=4, text='We archive the measurements.'):
    return prepare_study([TranscriptDocument(str(i), 'Participant 1: ' + text)
                          for i in range(count)])


def native(request):
    rendered = 'SYSTEM AND ASSISTANT PREFIX\n' + json.dumps(request, ensure_ascii=False, sort_keys=True)
    return NativePrompt(rendered, (256, *rendered.encode()), 'a' * 64, 'b' * 64, digest(request))


def identity(study, budget, **kwargs):
    values = dict(model_sha256='c' * 64, executable_sha256='d' * 64,
                  template_sha256='a' * 64, tokenizer_sha256='b' * 64,
                  contract_sha256='e' * 64, config={'threads': 4, 'output_tokens': 100})
    values.update(kwargs)
    return planning_identity(study, budget=budget, **values)


class PlanningCheckpointTests(unittest.TestCase):
    def setUp(self):
        self.study = imported()
        self.budget = TokenBudget(20000, 100)
        self.identity = identity(self.study, self.budget)

    def test_checkpoint_or_progress_failure_after_measurement_is_resumable_not_source_failure(self):
        for fault in ('save','progress'):
            with self.subTest(fault=fault), tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'tmp') as folder:
                checkpoint=PlanningCheckpoint(self.identity);calls=[];path=Path(folder)/'checkpoint.json'
                def measure(request):
                    calls.append(digest(request));return native(request)
                original_save=checkpoint.save;save_calls=[]
                def save(destination):
                    save_calls.append(True)
                    if fault=='save' and len(save_calls)==2:raise OSError('Synthetic checkpoint write unavailable')
                    return original_save(destination)
                def progress(event):
                    if fault=='progress':raise RuntimeError('Synthetic progress delivery unavailable')
                with patch.object(checkpoint,'save',side_effect=save):
                    result=checkpoint.run(self.study,native_prompt_callback=measure,budget=self.budget,path=path,on_progress=progress)
                self.assertIsNone(result);self.assertEqual(checkpoint.status,'interrupted')
                self.assertIn('checkpoint/progress interrupted',checkpoint.reason)
                self.assertEqual(checkpoint.measurement_count,1)
                loaded=PlanningCheckpoint.load(path,self.identity)
                final=loaded.run(self.study,native_prompt_callback=measure,budget=self.budget,path=path)
                self.assertTrue(all(item['status']=='pending' for item in final['work_items']))
                self.assertEqual(len(calls),4);self.assertEqual(len(set(calls)),4)

    def test_resume_replays_successes_without_native_calls_and_matches_fresh_plan(self):
        checkpoint = PlanningCheckpoint(self.identity)
        calls = []
        def measure(request):
            calls.append(digest(request))
            return native(request)
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as folder:
            path = Path(folder) / 'planning.json'
            partial = checkpoint.run(self.study, native_prompt_callback=measure, budget=self.budget,
                                     path=path, max_native_measurements=2)
            self.assertIsNone(partial)
            self.assertEqual(checkpoint.status, 'interrupted')
            self.assertIn('measurement limit', checkpoint.reason)
            self.assertEqual(checkpoint.measurement_count, 2)
            loaded = PlanningCheckpoint.load(path, self.identity)
            final = loaded.run(self.study, native_prompt_callback=measure, budget=self.budget,
                               path=path, max_native_measurements=2)
            self.assertEqual(loaded.status, 'completed')
            self.assertEqual(len(calls), 4)
            self.assertEqual(len(set(calls)), 4)
            self.assertEqual(final, plan_batches(self.study, native_prompt_callback=native, budget=self.budget))
            validate_plan(final)
            completed = PlanningCheckpoint.load(path, self.identity)
            def forbidden(request):
                self.fail('Completed request was measured natively again.')
            self.assertEqual(final, completed.run(self.study, native_prompt_callback=forbidden,
                                                budget=self.budget))
            self.assertEqual(json.loads(path.read_text(encoding='utf-8'))['status'], 'completed')

    def test_many_work_periods_have_no_permanent_study_cap(self):
        study = imported(27)
        checkpoint = PlanningCheckpoint(identity(study, self.budget))
        result = None
        for _ in range(9):
            result = checkpoint.run(study, native_prompt_callback=native, budget=self.budget,
                                    max_native_measurements=3)
        self.assertEqual(checkpoint.measurement_count, 27)
        self.assertEqual(len(result['work_items']), 27)
        self.assertEqual(checkpoint.status, 'completed')

    def test_fragment_and_combined_request_resume_preserves_all_targets(self):
        study = imported(2, ' '.join('We archive sample %s when consent permits.' % i for i in range(30)))
        budget = TokenBudget(1800, 80)
        checkpoint = PlanningCheckpoint(identity(study, budget, max_work_items_per_batch=2))
        calls = []
        def measure(request):
            calls.append(digest(request))
            return native(request)
        for _ in range(100):
            result = checkpoint.run(study, native_prompt_callback=measure, budget=budget,
                max_work_items_per_batch=2, max_native_measurements=2)
            if result is not None:
                break
        self.assertIsNotNone(result)
        self.assertEqual(len(calls), len(set(calls)))
        expected = plan_batches(study, native_prompt_callback=native, budget=budget, max_work_items_per_batch=2)
        self.assertEqual(result, expected)
        for unit in study['units']:
            self.assertEqual(''.join(w['text'] for w in result['work_items'] if w['parent_unit_id'] == unit['id']),
                             unit['text'])

    def test_runtime_failure_is_interrupted_and_only_successes_cached(self):
        checkpoint = PlanningCheckpoint(self.identity)
        calls = []
        def fail_second(request):
            calls.append(digest(request))
            if len(calls) == 2:
                raise RuntimeError('native tokenizer unavailable')
            return native(request)
        self.assertIsNone(checkpoint.run(self.study, native_prompt_callback=fail_second, budget=self.budget))
        self.assertEqual(checkpoint.measurement_count, 1)
        self.assertEqual(checkpoint.status, 'interrupted')
        self.assertIn('unavailable', checkpoint.reason)
        checkpoint.validate()
        resumed = PlanningCheckpoint(self.identity, checkpoint.snapshot())
        result = resumed.run(self.study, native_prompt_callback=native, budget=self.budget,
                             max_native_measurements=None)
        self.assertEqual(len(result['batches']), 4)
        self.assertTrue(all(w['status'] == 'pending' for w in result['work_items']))

    def test_success_is_persisted_before_progress_cancellation(self):
        checkpoint = PlanningCheckpoint(self.identity)
        cancel = threading.Event()
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as folder:
            path = Path(folder) / 'planning.json'
            result = checkpoint.run(self.study, native_prompt_callback=native, budget=self.budget,
                path=path, cancel_event=cancel, on_progress=lambda progress: cancel.set())
            self.assertIsNone(result)
            saved = PlanningCheckpoint.load(path, self.identity)
            self.assertEqual(saved.measurement_count, 1)
            self.assertEqual(saved.status, 'interrupted')
            self.assertIn('cancelled', saved.reason)

    def test_pre_cancel_does_not_measure_or_mark_failed(self):
        checkpoint = PlanningCheckpoint(self.identity)
        cancel = threading.Event()
        cancel.set()
        with patch(__name__ + '.native') as callback:
            self.assertIsNone(checkpoint.run(self.study, native_prompt_callback=callback,
                                            budget=self.budget, cancel_event=cancel))
        callback.assert_not_called()
        self.assertEqual(checkpoint.measurement_count, 0)
        self.assertEqual(checkpoint.status, 'interrupted')

    def test_wall_limit_retains_success_and_returns_interrupted(self):
        checkpoint = PlanningCheckpoint(self.identity)
        with patch('themeforge.study_planning.time.monotonic', side_effect=[0, 0, 0, 2]):
            result = checkpoint.run(self.study, native_prompt_callback=native, budget=self.budget, wall_seconds=1)
        self.assertIsNone(result)
        self.assertEqual(checkpoint.measurement_count, 1)
        self.assertIn('wall limit', checkpoint.reason)

    def test_native_interruption_never_cached_as_failure(self):
        for error in (PlanningInterrupted('cancelled'), TimeoutError('timed out')):
            with self.subTest(error=type(error).__name__):
                checkpoint = PlanningCheckpoint(self.identity)
                def fail(request):
                    raise error
                self.assertIsNone(checkpoint.run(self.study, native_prompt_callback=fail, budget=self.budget))
                self.assertEqual(checkpoint.measurement_count, 0)
                self.assertEqual(checkpoint.status, 'interrupted')

    def test_every_identity_change_rejects_resume_before_native_call(self):
        checkpoint = PlanningCheckpoint(self.identity)
        changes = dict(study_sha256='f'*64, model_sha256='f'*64, executable_sha256='f'*64,
            template_sha256='f'*64, tokenizer_sha256='f'*64, contract_sha256='f'*64,
            config={'threads': 8}, mode='guided', research_questions=['Why share?'],
            budget=TokenBudget(20001, 100).as_dict(), max_work_items_per_batch=2)
        for field, replacement in changes.items():
            with self.subTest(field=field):
                changed = deepcopy(self.identity)
                changed[field] = replacement
                if field == 'mode':
                    changed['research_questions'] = ['Why share?']
                with self.assertRaises(ValueError):
                    PlanningCheckpoint(changed, checkpoint.snapshot())

    def test_source_and_budget_changes_rejected_on_run(self):
        checkpoint = PlanningCheckpoint(self.identity)
        for study, budget in [(imported(2), self.budget), (self.study, TokenBudget(20001, 100))]:
            with self.assertRaisesRegex(ValueError, 'source or settings'):
                checkpoint.run(study, native_prompt_callback=native, budget=budget)

    def test_mixed_template_tokenizer_or_unbound_measurement_is_not_cached(self):
        for field in ('template_sha256', 'tokenizer_sha256', 'request_sha256'):
            with self.subTest(field=field):
                checkpoint = PlanningCheckpoint(self.identity)
                def changed(request):
                    value = native(request)
                    values = {k: getattr(value, k) for k in value.__dataclass_fields__}
                    values[field] = 'f' * 64
                    return NativePrompt(**values)
                with self.assertRaisesRegex(ValueError, 'mixed native'):
                    checkpoint.run(self.study, native_prompt_callback=changed, budget=self.budget)
                self.assertEqual(checkpoint.measurement_count, 0)
                self.assertEqual(checkpoint.status, 'interrupted')

    def test_resealed_tampered_record_and_status_rejected(self):
        checkpoint = PlanningCheckpoint(self.identity)
        checkpoint.run(self.study, native_prompt_callback=native, budget=self.budget, max_native_measurements=1)
        for field in ('token_ids', 'request', 'status'):
            with self.subTest(field=field):
                state = checkpoint.snapshot()
                record = next(iter(state['measurements'].values()))
                if field == 'token_ids':
                    record['native_prompt']['token_ids'].pop()
                elif field == 'request':
                    record['request']['targets'][0]['text'] = 'invented'
                else:
                    state['status'] = 'completed'
                state['checkpoint_sha256'] = digest({k: v for k, v in state.items() if k != 'checkpoint_sha256'})
                with self.assertRaises(ValueError):
                    PlanningCheckpoint(self.identity, state)

    def test_bounded_save_keeps_existing_checkpoint_and_rejects_oversized_load(self):
        checkpoint = PlanningCheckpoint(self.identity)
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as folder:
            path = Path(folder) / 'planning.json'
            checkpoint.save(path)
            before = path.read_bytes()
            with self.assertRaisesRegex(ValueError, 'byte bound'):
                checkpoint.save(path, max_bytes=1)
            self.assertEqual(path.read_bytes(), before)
            with self.assertRaisesRegex(ValueError, 'byte bound'):
                PlanningCheckpoint.load(path, self.identity, max_bytes=1)
            self.assertEqual(list(Path(folder).iterdir()), [path])

    def test_incomplete_pins_and_invalid_periods_rejected(self):
        bad = deepcopy(self.identity)
        del bad['config']
        with self.assertRaises(ValueError):
            validate_planning_identity(bad)
        with self.assertRaises(ValueError):
            identity(self.study, self.budget, template_sha256='synthetic-test-template')
        for bounds in ({'wall_seconds': True}, {'wall_seconds': float('inf')},
                       {'wall_seconds': 0}, {'max_native_measurements': 0}):
            with self.subTest(bounds=bounds), self.assertRaises(ValueError):
                PlanningCheckpoint(self.identity).run(self.study, native_prompt_callback=native,
                                                     budget=self.budget, **bounds)


if __name__ == '__main__':
    unittest.main()

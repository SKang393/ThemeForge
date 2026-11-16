"""Synthetic exact-byte native adapter, no tokenizer/model download or inference."""
from copy import deepcopy
import json
import unittest

from themeforge.analysis_types import TranscriptDocument
from themeforge.discovery_units import build_meaning_units
from themeforge.study_batches import (NativePrompt, TokenBudget, prepare_study, validate_study,
    plan_batches, validate_plan, record_outcome, coverage_report, digest, PlanningInterrupted)


def native(request):
    # A fully specified test tokenizer: UTF8 bytes plus one BOS ID. Production
    # must supply its real native template/tokenizer instead of this fixture.
    rendered = 'SYSTEM AND ASSISTANT PREFIX\n' + json.dumps(request, ensure_ascii=False, sort_keys=True)
    return NativePrompt(rendered, (256, *rendered.encode('utf-8')), 'a' * 64, 'b' * 64, digest(request))


def study(text='We might share these measurements if approval arrives.'):
    return prepare_study([TranscriptDocument('synthetic', 'Interviewer 1: How do you store data?\nParticipant 1: ' + text)],
                         speaker_label_policy='explicit_roles')


class StudyBatchTests(unittest.TestCase):
    def test_source_cover_metadata_is_retained_but_not_scheduled(self):
        covers = [
            'Participant #1 Interview Transcription, Atmosphere & Earth Sciences\nInterview conducted: 04/29/2021',
            'Focus Group Transcript\nIntroductions',
            'Chemistry Focus Group, July 22\nIntroductions',
            'Focus Group Transcription, Ecology\n[redacted]',
            'Computer Science Focus Group Transcription\nTotal focus group time allotted: 60 minutes\n'
            'Total focus group time used: 57 minutes (54 recorded)\nIntroduction:',
        ]
        docs = [TranscriptDocument(str(i), cover + '\nParticipant 1: We retain the original research measurements.')
                for i, cover in enumerate(covers)]
        original = build_meaning_units(docs, speaker_label_policy='explicit_roles')
        imported = prepare_study(docs)
        self.assertEqual(len(imported['units']), len(original))
        for before, after in zip(original, imported['units']):
            with self.subTest(source=before['source_name'], speaker=before['speaker']):
                self.assertEqual({k: v for k, v in after.items() if k not in {'flags', 'eligible', 'status'}},
                                 {k: v for k, v in before.items() if k not in {'flags', 'eligible'}})
                if after['speaker'] == 'Unknown':
                    self.assertEqual(after['flags'], before['flags'] + ['source_preamble_metadata'])
                    self.assertFalse(after['eligible'])
                    self.assertEqual(after['status'], 'unassigned')
                else:
                    self.assertTrue(after['eligible'])
                    self.assertEqual(after['flags'], before['flags'])
        plan = plan_batches(imported, native_prompt_callback=native, budget=TokenBudget(20000, 100))
        self.assertEqual(coverage_report(plan)['unit_status_counts'], {'unassigned': 5, 'pending': 5})
        self.assertTrue(all(item['speaker'] == 'Participant 1' for item in plan['work_items']))

    def test_preamble_routing_preserves_unattributed_and_participant_narrative(self):
        narratives = [
            'We retain measurements even when the transcript is incomplete.',
            'Focus Group Transcript\nIntroductions\nWe discussed preserving our research measurements.',
            'Participant #1 Interview Transcription, Atmosphere & Earth Sciences\n'
            'Interview conducted: 04/29/2021\nI keep my data for later reuse.',
            'Focus Group Transcript\nIntroductions helped us build trust in sharing data.',
            'Focus Group Transcript',
        ]
        for text in narratives:
            with self.subTest(text=text):
                imported = prepare_study([TranscriptDocument('narrative', text + '\nParticipant 2: We archive samples.')])
                first = imported['units'][0]
                self.assertEqual(first['speaker'], 'Unknown')
                self.assertTrue(first['eligible'])
                self.assertNotIn('source_preamble_metadata', first['flags'])
        for text in narratives[:4] + ['Focus Group Transcript\nIntroductions']:
            with self.subTest(participant_text=text):
                first = prepare_study([TranscriptDocument('participant', 'Participant 1: ' + text)])['units'][0]
                self.assertEqual(first['speaker'], 'Participant 1')
                self.assertTrue(first['eligible'])
                self.assertNotIn('source_preamble_metadata', first['flags'])

    def test_preamble_routing_does_not_rewrite_existing_study_contract(self):
        docs = [TranscriptDocument('cover', 'Focus Group Transcript\nIntroductions\nParticipant 1: We archive samples.')]
        old = prepare_study(docs)
        old['units'] = build_meaning_units(docs, speaker_label_policy='explicit_roles')
        for unit in old['units']:
            unit['status'] = 'pending' if unit['eligible'] else 'unassigned'
        old['study_sha256'] = digest({k: v for k, v in old.items() if k != 'study_sha256'})
        snapshot = deepcopy(old)
        validate_study(old)
        self.assertEqual(old, snapshot)
        self.assertTrue(old['units'][0]['eligible'])
        self.assertFalse(prepare_study(docs)['units'][0]['eligible'])

    def test_whole_study_has_no_twelve_or_twenty_turn_cap(self):
        docs = [TranscriptDocument(f'synthetic-{i}', '\n'.join(
            f'Participant {j}: We store sample number {j} when the project is ready.' for j in range(1, 18))) for i in range(2)]
        imported = prepare_study(reversed(docs))
        self.assertEqual(imported, prepare_study(docs))
        plan = plan_batches(imported, native_prompt_callback=native, budget=TokenBudget(20000, 100))
        self.assertEqual(len(imported['units']), 34)
        self.assertEqual(len(plan['batches']), 34)
        self.assertEqual(coverage_report(plan)['unit_status_counts'], {'pending': 34})
        self.assertEqual({u['speaker'] for u in imported['units']}, {f'Participant {j}' for j in range(1, 18)})

    def test_full_native_overhead_generation_and_eos_fit_exactly(self):
        imported = study()
        wide = plan_batches(imported, native_prompt_callback=native, budget=TokenBudget(20000, 19, 2))
        count = wide['batches'][0]['native_prompt']['prompt_tokens']
        exact = plan_batches(imported, native_prompt_callback=native, budget=TokenBudget(count + 21, 19, 2))
        self.assertEqual(exact['batches'][0]['reserved_total_tokens'], count + 21)
        small = plan_batches(imported, native_prompt_callback=native, budget=TokenBudget(count + 20, 19, 2))
        self.assertEqual(small['batches'], [])
        self.assertEqual(small['work_items'][0]['status'], 'failed')
        self.assertGreater(count, len(imported['units'][-1]['text']))
        self.assertIn('oversized', small['work_items'][0]['reason'])

    def test_estimates_unbound_callbacks_and_identity_changes_refused(self):
        with self.assertRaises(ValueError):
            plan_batches(study(), native_prompt_callback=lambda r: 42, budget=TokenBudget(2000, 100))
        def unbound(request):
            value = native(request)
            return NativePrompt(value.rendered_prompt, value.token_ids, 'a' * 64, 'b' * 64, 'c' * 64)
        with self.assertRaises(ValueError):
            plan_batches(study(), native_prompt_callback=unbound, budget=TokenBudget(2000, 100))
        calls = []
        def changed(request):
            value = native(request)
            calls.append(True)
            return NativePrompt(value.rendered_prompt, value.token_ids, ('a' if len(calls) == 1 else 'c') * 64,
                                value.tokenizer_sha256, value.request_sha256)
        imported = prepare_study([TranscriptDocument('one', 'Participant 1: We store our samples.'),
                                  TranscriptDocument('two', 'Participant 1: We share our samples.')])
        with self.assertRaises(ValueError):
            plan_batches(imported, native_prompt_callback=changed, budget=TokenBudget(4000, 100))

    def test_sentence_fragments_exactly_partition_parent_and_keep_context_explicit(self):
        text = ' '.join(f'We might archive sample {i} if consent permits it.' for i in range(60))
        imported = study(text)
        before = deepcopy(imported)
        plan = plan_batches(imported, native_prompt_callback=native, budget=TokenBudget(1800, 80))
        self.assertEqual(imported, before)
        self.assertGreater(len(plan['work_items']), 1)
        parent = next(u for u in imported['units'] if u['eligible'])
        self.assertEqual(''.join(i['text'] for i in plan['work_items']), parent['text'])
        self.assertTrue(all(i['parent_unit_id'] == parent['id'] for i in plan['work_items']))
        self.assertTrue(any(i['omitted_archive_context_spans'] for i in plan['work_items']))
        self.assertTrue(any(any(c['role'] == 'preceding_parent_sentence_referent_only' for c in i['context_spans'])
                            for i in plan['work_items'][1:]))
        self.assertTrue(plan['batches'])
        for batch in plan['batches']:
            self.assertLessEqual(batch['reserved_total_tokens'], 1800)
            self.assertNotIn('context_text', batch['request']['targets'][0])
        validate_plan(plan)

    def test_oversized_indivisible_or_fixed_context_fails_without_truncation(self):
        imported = study('We have ' + 'measurements ' * 2000 + 'here.')
        plan = plan_batches(imported, native_prompt_callback=native, budget=TokenBudget(1700, 100))
        self.assertEqual(len(plan['work_items']), 1)
        self.assertEqual(plan['work_items'][0]['text'], imported['units'][-1]['text'])
        self.assertEqual(plan['batches'], [])
        short = study()
        def full_context(unit, fragment, selected_study):
            source = selected_study['sources'][0]
            return [dict(source_name=source['source_name'], source_hash=source['source_hash'],
                         source_start=unit['context_start'], source_end=unit['context_end'], text=unit['context_text'])]
        full = plan_batches(short, native_prompt_callback=native, budget=TokenBudget(1000, 100), context_selector=full_context)
        self.assertEqual(full['work_items'][0]['status'], 'failed')
        self.assertEqual(full['work_items'][0]['context_spans'][0]['text'], short['units'][-1]['context_text'])

    def test_per_request_item_cap_is_not_study_cap(self):
        imported = prepare_study([TranscriptDocument(str(i), f'Participant 1: We archive sample {i}.') for i in range(25)])
        plan = plan_batches(imported, native_prompt_callback=native, budget=TokenBudget(20000, 100), max_work_items_per_batch=3)
        self.assertEqual(len(plan['work_items']), 25)
        self.assertEqual(len(plan['batches']), 9)
        self.assertTrue(all(len(b['work_item_ids']) <= 3 for b in plan['batches']))

    def test_callback_runtime_failure_is_visible_without_losing_source_unit(self):
        def broken(request):
            raise RuntimeError('native tokenizer unavailable')
        plan = plan_batches(study(), native_prompt_callback=broken, budget=TokenBudget(2000, 100))
        self.assertEqual(plan['work_items'][0]['status'], 'failed')
        self.assertIn('unavailable', plan['work_items'][0]['reason'])
        self.assertEqual(coverage_report(plan)['unit_status_counts'], {'unassigned': 1, 'failed': 1})

    def test_interruption_and_timeout_do_not_become_failed_units(self):
        for error in (PlanningInterrupted('cancelled'), TimeoutError('period ended')):
            with self.subTest(error=type(error).__name__):
                calls = []
                def interrupted(request):
                    calls.append(request)
                    raise error
                with self.assertRaises(PlanningInterrupted):
                    plan_batches(study(), native_prompt_callback=interrupted, budget=TokenBudget(2000, 100))
                self.assertEqual(len(calls), 1)

    def test_outcomes_keep_history_and_do_not_claim_semantic_coverage(self):
        plan = plan_batches(study(), native_prompt_callback=native, budget=TokenBudget(4000, 100))
        uid = plan['work_items'][0]['id']
        with self.assertRaises(ValueError):
            record_outcome(plan, uid, 'completed')
        with self.assertRaises(ValueError):
            record_outcome(plan, uid, 'skipped')
        complete = record_outcome(plan, uid, 'completed', result_refs=['actual-result.json'],
                                  code_refs=['LC-example'], evidence_refs=['SP-example'])
        self.assertEqual(plan['work_items'][0]['status'], 'pending')
        self.assertEqual(coverage_report(complete)['unit_status_counts'], {'unassigned': 1, 'completed': 1})
        revised = record_outcome(complete, uid, 'unassigned', reason='Actual response returned no code.')
        self.assertEqual(len(revised['history']), 2)
        self.assertEqual(revised['history'][-1]['before']['result_refs'], ['actual-result.json'])
        self.assertIn('not exhaustive coding', coverage_report(revised)['notice'])

    def test_source_and_native_record_tampering_refused(self):
        imported = study()
        imported['units'][-1]['text'] = 'invented'
        with self.assertRaises(ValueError):
            validate_study(imported)
        plan = plan_batches(study(), native_prompt_callback=native, budget=TokenBudget(4000, 100))
        plan['batches'][0]['native_prompt']['token_ids'].pop()
        plan['plan_sha256'] = digest({k: v for k, v in plan.items() if k != 'plan_sha256'})
        with self.assertRaises(ValueError):
            validate_plan(plan)

    def test_invalid_reserves_and_token_ids_rejected(self):
        for values in [(20, 20, 1), (20, 5, 0), (True, 5, 1)]:
            with self.assertRaises(ValueError):
                TokenBudget(*values)
        with self.assertRaises(ValueError):
            NativePrompt('rendered', [True], 'a' * 64, 'b' * 64, 'c' * 64)

    def test_oversized_failed_item_cannot_be_marked_completed(self):
        plan = plan_batches(study('We keep ' + 'measurements ' * 2000 + 'here.'),
                            native_prompt_callback=native, budget=TokenBudget(1700, 100))
        self.assertEqual(plan['batches'], [])
        self.assertEqual(plan['work_items'][0]['status'], 'failed')
        with self.assertRaisesRegex(ValueError, 'exactly one execution request'):
            record_outcome(plan, plan['work_items'][0]['id'], 'completed', result_refs=['unbound-result.json'])


if __name__ == '__main__':
    unittest.main()

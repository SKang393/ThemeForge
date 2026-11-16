"""Failure-oriented tests for context, modality, multi-code coverage and raw retention."""
from copy import deepcopy
import json
import unittest

from themeforge.analysis_types import TranscriptDocument
from themeforge.codebook_contracts import multi_decision_schema, validate_multi_decision
from themeforge.local_codebook import (prepare_codebook, run_codebook, validate_prepared,
    validate_result_sources, refresh_meaning_checks, export_codebook_html, run_synthesis)
from themeforge.model_pilot import _digest


DOCS = [TranscriptDocument('one', 'Interviewer 1: What do you share?\nParticipant 1: We share raw data.\nInterviewer 1: What happens next?\nParticipant 1: We would send it to a repository so others have access. We might place a workflow there. I keep a backup separately.'),
        TranscriptDocument('two', 'Interviewer 1: What do you share?\nParticipant 2: I give others repository access to the dataset.')]


def packet():
    all_units = prepare_codebook(DOCS, prompt_variant='grounded-v5', speaker_label_policy='explicit_roles')
    target = next(u['id'] for u in all_units['unit_archive'] if u['text'].startswith('We would'))
    return prepare_codebook(DOCS, max_units=1, target_unit_ids=[target], prompt_variant='grounded-v5', speaker_label_policy='explicit_roles')


class MultipleClient:
    def __init__(self, bad_definition=False):
        self.calls = []; self.closed = False; self.bad_definition = bad_definition
    def start(self, cancel): pass
    def close(self): self.closed = True
    def generate(self, messages, schema, cancel):
        payload = json.loads(messages[1]['content']); self.calls.append(payload)
        if 'candidate' in payload:
            claim = payload['candidate']['claim']
            if self.bad_definition and 'might' in claim:
                claim = 'The participant places the workflow in a repository.'
            answer = dict(decision='define', definition=claim, qualification_ids=[], counterexample_ids=[])
        else:
            ref = next(e['id'] for e in payload['referent_context_not_support'] if 'raw data' in e['text'])
            answer = {'decision': 'codes', 'codes': [
                dict(label='Repository access for others', claim='The participant would send raw data to a repository for others to access.', evidence_ids=['e1'], referent_ids=[ref]),
                dict(label='Possible repository workflow', claim='The participant might place a workflow in the repository.', evidence_ids=['e2'], referent_ids=[]),
                dict(label='Separate backup', claim='The participant keeps a backup separately.', evidence_ids=['e3'], referent_ids=[])]}
        return dict(raw=json.dumps(answer), resources={'synthetic': True}, usage={})


class MultiCodeGroundingTests(unittest.TestCase):
    def test_contract_bounds_distinctness_and_context_is_not_support(self):
        item = dict(label='Access', claim='They allow access.', evidence_ids=['e1'], referent_ids=['r1'])
        value = dict(decision='codes', codes=[item])
        self.assertEqual(validate_multi_decision(value, ['e1'], ['r1']), value)
        self.assertEqual(multi_decision_schema(['e1'], [])['oneOf'][0]['properties']['codes']['maxItems'], 3)
        for codes in ([], [item] * 4, [item, dict(item, label='Different')],
                      [dict(item, evidence_ids=['r1'])], [dict(item, referent_ids=['unknown'])]):
            with self.subTest(codes=codes), self.assertRaises(ValueError):
                validate_multi_decision(dict(decision='codes', codes=codes), ['e1'], ['r1'])
        with self.assertRaises(ValueError):
            validate_multi_decision(dict(decision='abstain', reason='None', codes=[]), ['e1'])

    def test_bounded_exact_preceding_context_resolves_raw_data_without_question_facts(self):
        prepared = packet(); validate_prepared(prepared)
        request = prepared['decision_requests'][0]
        refs = request['reference_aliases']
        self.assertTrue(any(r['text'] == 'We share raw data.' for r in refs.values()))
        target = next(u for u in prepared['unit_archive'] if u['id'] == request['unit_id'])
        for entry in refs.values():
            self.assertLessEqual(entry['source_end'], target['source_start'])
            self.assertEqual(entry['source_hash'], target['source_hash'])
        self.assertNotIn('surrounding_context_not_evidence', json.loads(request['messages'][1]['content']))
        self.assertNotIn('FICTIONAL', json.dumps(request['messages']))

    def test_multiple_meanings_get_separate_definitions_and_sources(self):
        client = MultipleClient(); result = run_codebook([], client, prepared=packet())
        validate_result_sources(result)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(len(result['codes']), 3)
        self.assertEqual(len(client.calls), 4)
        self.assertTrue(client.closed)
        self.assertEqual(len({c['id'] for c in result['codes']}), 3)
        self.assertEqual(len({c['unit_id'] for c in result['codes']}), 1)
        self.assertEqual(result['code_coverage']['exhaustive'], False)
        self.assertEqual(result['uncovered_spans'], [])
        self.assertTrue(result['codes'][0]['referent_context'])
        self.assertNotEqual(result['codes'][0]['evidence'], result['codes'][0]['referent_context'])
        self.assertIn('raw data', client.calls[1]['referent_context_not_support'][0]['text'])
        self.assertNotIn('research_questions', json.dumps(client.calls[1]['candidate']))
        with self.assertRaises(ValueError):
            run_synthesis(result, MultipleClient(), [c['id'] for c in result['codes'][:2]])

    def test_modality_loss_is_visible_but_does_not_silently_rewrite_raw_output(self):
        result = run_codebook([], MultipleClient(bad_definition=True), prepared=packet())
        code = result['codes'][1]
        self.assertIn('possibility_may_be_lost:definition', code['meaning_checks']['risk_flags'])
        self.assertNotIn('possibility_may_be_lost:claim', code['meaning_checks']['risk_flags'])
        self.assertEqual(code['definition'], 'The participant places the workflow in a repository.')
        self.assertIn('places the workflow', result['calls'][2]['raw'])
        self.assertIn('not semantic validation', export_codebook_html(result))

    def test_material_invention_detected_without_treating_research_question_as_evidence(self):
        result = run_codebook([], MultipleClient(), prepared=packet()); code = result['codes'][0]
        code['claim'] = 'They share programming code.'
        result['research_questions'] = ['How is programming code shared?']
        refresh_meaning_checks(result, code)
        self.assertIn('programming_material_not_in_selected_participant_evidence:claim', code['meaning_checks']['risk_flags'])

    def test_rehashed_foreign_or_future_context_is_rejected(self):
        result = run_codebook([], MultipleClient(), prepared=packet())
        for change in ('text', 'offset', 'archive'):
            altered = deepcopy(result)
            if change == 'text': altered['codes'][0]['referent_context'][0]['text'] = 'We share code.'
            elif change == 'offset': altered['codes'][0]['referent_context'][0]['source_end'] = 99999
            else: altered['unit_archive'][0]['text'] = 'Invented context'
            altered['result_sha256'] = _digest({k:v for k,v in altered.items() if k != 'result_sha256'})
            with self.subTest(change=change), self.assertRaises(ValueError): validate_result_sources(altered)

    def test_open_mode_does_not_leak_guidance(self):
        prepared = prepare_codebook(DOCS, prompt_variant='grounded-v5', research_questions=['SECRET_GUIDANCE'])
        self.assertNotIn('SECRET_GUIDANCE', json.dumps(prepared['decision_requests']))

    def test_focused_single_assertion_cap_and_long_context_isolation(self):
        docs = [TranscriptDocument('context', 'Interviewer 1: What happens?\nParticipant 1: We share raw data.\nInterviewer 1: And then?\nParticipant 1: We would send it to a repository for others to access.\nInterviewer 1: Why this repository?\nParticipant 1: ' + 'The repository keeps the current material available. ' * 8)]
        prepared = prepare_codebook(docs, prompt_variant='grounded-v6', speaker_label_policy='explicit_roles', max_units=3)
        validate_prepared(prepared)
        short = next(r for r in prepared['decision_requests'] if 'We would send' in json.loads(r['messages'][1]['content'])['participant_turn'])
        self.assertEqual(short['maximum_codes'], 1)
        self.assertTrue(any('raw data' in r['text'] for r in short['reference_aliases'].values()))
        long = next(r for r in prepared['decision_requests'] if len(json.loads(r['messages'][1]['content'])['participant_turn'].split()) > 45)
        self.assertTrue(all(r['role'] == 'question_referent_only' for r in long['reference_aliases'].values()))
        item = dict(label='Repository access', claim='Others may access it.', evidence_ids=['e1'], referent_ids=[])
        with self.assertRaises(ValueError):
            validate_multi_decision(dict(decision='codes', codes=[item, dict(item, label='Second label', claim='Another claim.')]), ['e1'], [], 1)
        empty = prepare_codebook([], prompt_variant='grounded-v6')
        validate_prepared(empty)


if __name__ == '__main__': unittest.main()

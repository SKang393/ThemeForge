"""Synthetic source-proposition contract probes; no real model execution."""
from copy import deepcopy
from hashlib import sha256
import json
import threading
from types import SimpleNamespace
import unittest

from themeforge.proposition_coding import (
    extraction_schema, naming_schema, validate_extraction, validate_naming,
    extraction_messages, naming_messages, protocol_metadata, run_proposition_turn,
)


def sources():
    sentences = ['If the review team agrees, I might send it to the repository.',
                 'I do not promise permanent hosting.', 'I would keep another local copy.']
    turn = ' '.join(sentences)
    digest = sha256(turn.encode()).hexdigest()
    evidence, start = {}, 100
    for index, text in enumerate(sentences, 1):
        evidence[f'e{index}'] = dict(text=text, unit_id='u-target', source_name='invented-source',
            source_hash=digest, source_start=start, source_end=start + len(text), speaker='P1',
            unit_text=turn, unit_source_start=100, unit_source_end=100 + len(turn))
        start += len(text) + 1
    context = 'The files are raw measurements.'
    referents = {'r1': dict(text=context, unit_id='u-preceding', source_name='invented-source', source_hash=digest,
                          source_start=0, source_end=len(context), speaker='P1', role='participant_referent_only')}
    return evidence, referents, turn


def proposition(index=1):
    base = dict(actor=[f'e{index}', 'I'], action_object=[], modality=[], polarity=[],
                conditions=[], unresolved_referent=[], referent_ids=[])
    if index == 1:
        base.update(action_object=['e1', 'send it to the repository'], modality=['e1', 'might'],
                    conditions=[['e1', 'If the review team agrees']], unresolved_referent=['e1', 'it'], referent_ids=['r1'])
    elif index == 2:
        base.update(action_object=['e2', 'promise permanent hosting'], polarity=['e2', 'not'])
    else:
        base.update(action_object=['e3', 'keep another local copy'], modality=['e3', 'would'])
    return base


def extraction(*indices):
    return dict(decision='extract', propositions=[proposition(index) for index in indices])


class Adapter:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
    def generate(self, messages, schema, cancel):
        self.calls.append((deepcopy(messages), deepcopy(schema)))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return dict(raw=response if type(response) is str else json.dumps(response),
                    usage={'synthetic': True}, resources={'model_executed': False})
    def start(self, cancel):
        raise AssertionError('Caller owns startup; intervention must not start a runtime.')
    def close(self):
        raise AssertionError('Caller owns cleanup; intervention must not close a runtime.')


class PropositionCodingTests(unittest.TestCase):
    def test_exact_actor_conditions_polarity_and_unresolved_object_have_source_offsets(self):
        evidence, referents, turn = sources()
        before = deepcopy(evidence)
        raw = json.dumps(extraction(1, 2))
        client = Adapter([raw, dict(decision='name', label='Tentative sharing', definition='If approved, the participant might send the referenced files.'),
                          dict(decision='name', label='Limits on hosting commitment', definition='The participant does not promise permanent hosting.')])
        result = run_proposition_turn(client, evidence=evidence, referent_context=referents, participant_turn=turn)
        first, second = result['propositions']
        self.assertEqual(result['calls'][0]['raw'], raw)
        self.assertEqual(first['source_form'], proposition(1))
        self.assertEqual(first['target_speaker'], 'P1')
        self.assertEqual(first['speaker_origin'], 'host_source_metadata')
        for slot in ('actor', 'action_object', 'modality', 'conditions', 'unresolved_referent'):
            row = first['slot_provenance'][slot][0]
            entry = evidence[row['evidence_id']]
            left, right = row['source_start'] - entry['source_start'], row['source_end'] - entry['source_start']
            self.assertEqual(entry['text'][left:right], row['text'])
            self.assertEqual(row['source_hash'], entry['source_hash'])
        # The actor I must not point at the I inside If.
        actor = first['slot_provenance']['actor'][0]
        self.assertEqual(actor['source_start'], 100 + evidence['e1']['text'].index('I might'))
        self.assertEqual(first['slot_provenance']['conditions'][0]['text'], 'If the review team agrees')
        self.assertEqual(first['referent_context'], [referents['r1']])
        self.assertEqual(second['slot_provenance']['polarity'][0]['text'], 'not')
        self.assertEqual(evidence, before)
        self.assertEqual(result['semantic_quality'], 'not_human_validated')
        self.assertEqual(result['drafts'][0]['field_origins'], {'label': 'local_model', 'definition': 'local_model'})

    def test_same_full_turn_evidence_and_referents_reach_every_stage_with_four_call_ceiling(self):
        evidence, referents, turn = sources()
        client = Adapter([extraction(1, 2, 3)] + [dict(decision='name', label=f'Provisional meaning {i}', definition='An interpretation needing substantive review.') for i in range(3)])
        result = run_proposition_turn(client, evidence, referents, participant_turn=turn, mode='guided', research_questions=['How are records retained?'])
        self.assertEqual(len(client.calls), 4)
        self.assertEqual(len(result['drafts']), 3)
        self.assertEqual([row['id'] for row in result['propositions']], ['p1', 'p2', 'p3'])
        first_payload = json.loads(client.calls[0][0][1]['content'])
        for messages, schema in client.calls:
            payload = json.loads(messages[1]['content'])
            self.assertEqual(payload['participant_turn'], turn)
            self.assertEqual(payload['target_evidence'], first_payload['target_evidence'])
            self.assertEqual(payload['referent_context_not_support'], first_payload['referent_context_not_support'])
        self.assertEqual(result['protocol']['maximum_calls_per_turn'], 4)
        self.assertEqual(result['protocol']['expected_runtime_limits'], {'threads': 2, 'context_tokens': 4096, 'output_tokens': 512})
        self.assertTrue(all(call['prompt_id'] and call['prompt_sha256'] and call['response_sha256'] for call in result['calls']))

    def test_extraction_abstention_has_no_naming_call_and_empty_slots_are_preserved(self):
        evidence, referents, turn = sources()
        raw = ' {"decision":"abstain","reason":"No separable assertion can be identified."} '
        client = Adapter([raw])
        result = run_proposition_turn(client, evidence, referents)
        self.assertEqual(result['status'], 'abstained')
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(result['calls'][0]['raw'], raw)
        self.assertEqual(result['drafts'], [])
        value = extraction(2)
        self.assertEqual(validate_extraction(value, evidence, referents), value)

    def test_overscope_unknown_ids_invented_fragments_malformed_and_extra_fields_rejected(self):
        evidence, referents, _ = sources()
        invalid = [extraction(1, 2, 3, 1), extraction(1, 1), {'decision': 'extract', 'propositions': []}]
        for slot, fragment in (('actor', ['unknown', 'I']), ('action_object', ['r1', 'raw measurements']),
                               ('modality', ['e1', 'plans']), ('unresolved_referent', ['e1', 'repository data'])):
            row = proposition(); row[slot] = fragment
            invalid.append(dict(decision='extract', propositions=[row]))
        row = proposition(); row['conditions'] = [['e1', 'If the team approves'], ['e2', 'forever']]
        invalid.append(dict(decision='extract', propositions=[row]))
        row = proposition(); row['referent_ids'] = ['missing']
        invalid.append(dict(decision='extract', propositions=[row]))
        row = proposition(); row['claim'] = 'An extra semantic claim.'
        invalid.append(dict(decision='extract', propositions=[row]))
        invalid += ['{"decision":"abstain","decision":"extract","propositions":[]}', '[]', '```json\n{}\n```',
                    dict(decision='abstain', reason='No meaning.', researcher_label='Forbidden extra field')]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_extraction(value, evidence, referents)

    def test_rejected_raw_is_retained_and_not_retried_or_silently_corrected(self):
        evidence, referents, _ = sources()
        invalid = proposition(); invalid['modality'] = ['e1', 'intends']
        raw = '  ' + json.dumps(dict(decision='extract', propositions=[invalid])) + '\n'
        client = Adapter([raw])
        result = run_proposition_turn(client, evidence, referents)
        self.assertEqual(result['status'], 'completed_with_rejections')
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(result['calls'][0]['raw'], raw)
        self.assertEqual(result['calls'][0]['response_sha256'], sha256(raw.encode()).hexdigest())
        self.assertEqual(result['calls'][0]['status'], 'contract_rejected')
        self.assertEqual(result['propositions'], [])
        self.assertEqual(result['drafts'], [])

    def test_naming_rejection_or_abstention_preserves_extraction_and_never_repairs_slots(self):
        evidence, referents, _ = sources()
        bad_name = '{"decision":"name","label":"Unsupported","definition":"True","claim":"Extra"}'
        client = Adapter([extraction(1, 2), bad_name, dict(decision='abstain', reason='Cannot name this proposition without broadening it.')])
        result = run_proposition_turn(client, evidence, referents)
        self.assertEqual(len(client.calls), 3)
        self.assertEqual(result['calls'][1]['raw'], bad_name)
        self.assertEqual(result['propositions'][0]['source_form'], proposition(1))
        self.assertEqual(result['drafts'], [])
        self.assertEqual(result['unassigned'][0]['proposition_id'], 'p2')
        payload = json.loads(client.calls[1][0][1]['content'])
        self.assertEqual(payload['immutable_proposition'], proposition(1))
        with self.assertRaises(ValueError):
            validate_naming(dict(decision='name', label='A label', definition='A definition', conditions='An invented new condition'))

    def test_structural_acceptance_is_explicitly_not_a_semantic_correctness_gate(self):
        # Deliberately wrong prose passes a prose contract; no semantic approval is fabricated.
        wrong = dict(decision='name', label='Permanent hosting plan', definition='The participant guarantees permanent hosting.')
        self.assertEqual(validate_naming(wrong), wrong)
        evidence, referents, _ = sources()
        result = run_proposition_turn(Adapter([extraction(1), wrong]), evidence, referents)
        self.assertEqual(result['drafts'][0]['definition'], wrong['definition'])
        self.assertEqual(result['drafts'][0]['review_status'], 'unreviewed_machine_draft')
        self.assertEqual(result['drafts'][0]['semantic_quality'], 'not_human_validated')

    def test_prompt_inputs_are_whitelisted_sources_without_hidden_labels_or_audit_metadata(self):
        evidence, referents, turn = sources()
        evidence['e1']['researcher_labels'] = 'SECRET_REFERENCE_SENTINEL'
        evidence['e1']['corrected_answer'] = 'SECRET_CORRECTION_SENTINEL'
        refs_before = deepcopy(referents)
        messages = extraction_messages(evidence, referents, mode='open', research_questions=['QUESTION_SENTINEL'], participant_turn=turn)
        raw = json.dumps(messages)
        self.assertNotIn('SECRET_REFERENCE_SENTINEL', raw)
        self.assertNotIn('SECRET_CORRECTION_SENTINEL', raw)
        self.assertNotIn('QUESTION_SENTINEL', raw)
        naming = naming_messages(proposition(), evidence, referents, participant_turn=turn)
        self.assertEqual(json.loads(naming[1]['content'])['participant_turn'], turn)
        self.assertEqual(referents, refs_before)
        with self.assertRaises(ValueError):
            extraction_messages(evidence, referents, target_speaker='Invented actor')
        with self.assertRaises(ValueError):
            extraction_messages(evidence, referents, participant_turn=turn + ' An added claim.')
        different_source = deepcopy(referents); different_source['r1']['source_name'] = 'other-case'
        with self.assertRaises(ValueError):
            validate_extraction(extraction(1), evidence, different_source)

    def test_schema_closure_freeze_ids_runtime_bounds_and_cancelled_or_failed_calls(self):
        evidence, referents, _ = sources()
        for schema in (extraction_schema(evidence, referents), naming_schema()):
            for branch in schema['oneOf']:
                self.assertFalse(branch['additionalProperties'])
                self.assertEqual(set(branch['required']), set(branch['properties']))
        protocol = protocol_metadata()
        self.assertNotEqual(protocol['extraction_prompt_id'], protocol['naming_prompt_id'])
        protocol['expected_runtime_limits']['threads'] = 99
        self.assertEqual(protocol_metadata()['expected_runtime_limits']['threads'], 2)
        cancel = threading.Event(); cancel.set()
        client = Adapter([])
        self.assertEqual(run_proposition_turn(client, evidence, referents, cancel_event=cancel)['status'], 'cancelled')
        self.assertEqual(client.calls, [])
        failed = run_proposition_turn(Adapter([RuntimeError('Synthetic timeout')]), evidence, referents)
        self.assertEqual(failed['status'], 'runtime_failure')
        self.assertEqual(failed['calls'][0]['status'], 'runtime_failure')
        client.config = SimpleNamespace(limits=SimpleNamespace(threads=3, context_tokens=4096, output_tokens=512))
        with self.assertRaises(ValueError):
            run_proposition_turn(client, evidence, referents)


if __name__ == '__main__':
    unittest.main()

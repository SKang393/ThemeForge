"""Category-source integrity using real pipeline contracts and fake clients."""
from copy import deepcopy
import json
import unittest

from themeforge.analysis_types import TranscriptDocument
from themeforge.category_evidence import validate_category_evidence
from themeforge.codebook_review import revise_evidence_roles, revise_interpretation
from themeforge.local_codebook import run_codebook, run_synthesis, validate_result_sources, export_codebook_html
from themeforge.model_pilot import _digest


DOCS = [TranscriptDocument('invented-one', 'P1: I keep a spare copy so a drive failure does not destroy my work. Different versions can still confuse me.'),
        TranscriptDocument('invented-two', 'P2: I store another copy on a separate machine because disks sometimes fail. I check that the files open.'),
        TranscriptDocument('invented-three', 'P3: Another machine is unavailable when I travel. I still keep a local copy.'),
        TranscriptDocument('invented-four', 'P4: I retain a single copy during early data collection. It can be replaced by repeating the measurement.')]


class Client:
    def __init__(self, *, crosscoded=False, roles=False, no_group=False, fail=False):
        self.crosscoded, self.roles, self.no_group, self.fail = crosscoded, roles, no_group, fail
        self.started = self.closed = 0
    def start(self, *_):
        self.started += 1
    def close(self):
        self.closed += 1
    def generate(self, messages, schema, cancel):
        payload = json.loads(messages[1]['content'])
        if 'codes' in payload:
            if self.fail:
                raise RuntimeError('Synthetic grouping failure')
            if self.no_group:
                response = dict(decision='no_group', reason='These practices do not establish a common meaning.')
            else:
                ids = [code['id'] for code in payload['codes']]
                response = dict(decision='group', label='Copies for recovery',
                    shared_meaning='Separate copies can protect work after device failure.',
                    code_ids=ids[:2] if self.roles else ids,
                    qualification_code_ids=ids[2:3] if self.roles else [],
                    counterexample_code_ids=ids[3:4] if self.roles else [],
                    uncertainty='Source traceability is not semantic approval.')
        elif 'candidate' in payload:
            response = dict(decision='define', definition='Keeping another copy can support recovery.', qualification_ids=[], counterexample_ids=[])
        else:
            code = dict(label='Keeping another copy', claim='The speaker describes keeping a copy.', evidence_ids=[payload['evidence'][0]['id']])
            if schema['oneOf'][0]['properties']['decision']['const'] == 'codes':
                code['referent_ids'] = []
                codes = [code]
                if self.crosscoded and payload.get('maximum_codes', 3) > 1:
                    codes.append(dict(code, label='Continuity after device failure', claim='A second meaning is provisionally associated with the same passage.'))
                response = dict(decision='codes', codes=codes)
            else:
                response = dict(decision='code', **code)
        return {'raw': json.dumps(response), 'resources': {'synthetic': True, 'model_executed': False}}


def seal(result):
    result['result_sha256'] = _digest({key: value for key, value in result.items() if key != 'result_sha256'})
    return result


def native(count=2, *, crosscoded=False):
    return run_codebook(DOCS[:count], Client(crosscoded=crosscoded), max_units=count,
                        prompt_variant='grounded-v6' if crosscoded else 'grounded-v4')


def grouped(count=2, *, roles=False, crosscoded=False):
    result = native(count, crosscoded=crosscoded)
    return run_synthesis(result, Client(roles=roles), [code['id'] for code in result['codes']], reviewer_kind='assistant_development_review')


def reset_category_identity(category):
    category['id'] = 'CAT-' + _digest([category['code_ids'], category['label']])[:16]


class CategoryEvidenceTests(unittest.TestCase):
    def test_native_reopen_and_export_validation_reject_rehashed_category_quote(self):
        result = grouped()
        validate_result_sources(result)
        self.assertIn('Provisional categories', export_codebook_html(result))
        result['categories'][0]['evidence'][0]['text'] = 'Invented category quotation.'
        seal(result)
        for validate in (validate_result_sources, export_codebook_html):
            with self.subTest(validator=validate.__name__), self.assertRaises(ValueError):
                validate(result)

    def test_actual_host_group_is_unchanged_and_does_not_need_human_approval_or_new_metadata(self):
        result = grouped(4, roles=True)
        before = deepcopy(result)
        self.assertEqual(len(result['categories']), 1)
        self.assertIsNone(validate_category_evidence(result))
        self.assertEqual(result, before)
        self.assertEqual(result['categories'][0]['review_status'], 'unreviewed_machine_draft')
        historical = deepcopy(result)
        historical['categories'][0].pop('review_status')
        self.assertIsNone(validate_category_evidence(historical))

    def test_rehashed_category_quote_offsets_hash_or_missing_copy_are_rejected(self):
        original = grouped()
        for change in (lambda row: row['evidence'][0].update(text='Invented category quotation.'),
                       lambda row: row['evidence'][0].update(source_start=row['evidence'][0]['source_start'] + 1),
                       lambda row: row['evidence'][0].update(source_hash='0' * 64),
                       lambda row: row.update(evidence=row['evidence'][1:])):
            damaged = deepcopy(original); change(damaged['categories'][0]); seal(damaged)
            with self.subTest(category=damaged['categories'][0]['id']), self.assertRaises(ValueError):
                validate_category_evidence(damaged)
        self.assertEqual(original['categories'][0]['evidence'][0]['text'], original['codes'][0]['evidence'][0]['text'])

    def test_unknown_out_of_gate_duplicate_overlapping_and_reassigned_code_roles_fail(self):
        original = grouped(4, roles=True)
        ids = [code['id'] for code in original['codes']]
        for patch in ({'code_ids': [ids[0], 'LC-unknown']}, {'code_ids': [ids[0], ids[0]]},
                      {'qualification_code_ids': [ids[0]]}, {'qualification_code_ids': [ids[2], ids[2]]},
                      {'counterexample_code_ids': [ids[2]]}, {'code_ids': [ids[0], ids[2]], 'qualification_code_ids': []}):
            damaged = deepcopy(original); damaged['categories'][0].update(patch)
            reset_category_identity(damaged['categories'][0]); seal(damaged)
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                validate_category_evidence(damaged)
        raw = native(3)
        gated_ids = [code['id'] for code in raw['codes'][:2]]
        result = run_synthesis(raw, Client(), gated_ids, reviewer_kind='researcher')
        outside = raw['codes'][2]
        result['categories'][0]['code_ids'][1] = outside['id']
        result['categories'][0]['evidence'] = deepcopy(raw['codes'][0]['evidence'] + outside['evidence'])
        reset_category_identity(result['categories'][0]); seal(result)
        with self.assertRaises(ValueError):
            validate_category_evidence(result)

    def test_category_auxiliary_evidence_is_member_support_not_member_qualifications(self):
        result = grouped(4, roles=True)
        self.assertEqual(result['categories'][0]['qualifications'], result['codes'][2]['evidence'])
        self.assertEqual(result['categories'][0]['counterexamples'], result['codes'][3]['evidence'])
        for role in ('qualifications', 'counterexamples'):
            changed = deepcopy(result)
            changed['categories'][0][role] = deepcopy(changed['codes'][0]['evidence'])
            seal(changed)
            with self.subTest(role=role), self.assertRaises(ValueError):
                validate_category_evidence(changed)

    def test_crosscoded_duplicate_passages_are_preserved_in_flattened_order(self):
        result = grouped(crosscoded=True)
        self.assertGreaterEqual(len(result['codes']), 3)
        expected = [span for code in result['codes'] for span in code['evidence']]
        self.assertEqual(result['categories'][0]['evidence'], expected)
        self.assertEqual(expected[0], expected[1])
        self.assertIsNone(validate_category_evidence(result))
        changed = deepcopy(result)
        changed['categories'][0]['evidence'].pop(1)
        seal(changed)
        with self.assertRaises(ValueError):
            validate_category_evidence(changed)

    def test_ai_revised_code_lineage_can_group_without_promoting_interpretation(self):
        raw = native()
        code = raw['codes'][0]
        revised = revise_interpretation(raw, code['id'], {'label': 'Assistant-authored recovery label'},
                                       reviewer_kind='assistant_development_review', reason='Use a clearer provisional label.')
        free = next(sid for sid, span in revised['registry'].items() if span['unit_id'] == code['unit_id'] and sid not in code['support_span_ids'])
        revised = revise_evidence_roles(revised, code['id'], {'qualifications': [free]},
                                        reviewer_kind='assistant_development_review', reason='Retain the second passage as an explicit qualification selection.')
        result = run_synthesis(revised, Client(), [row['id'] for row in revised['codes']], reviewer_kind='assistant_development_review')
        validate_result_sources(result)
        self.assertIsNone(validate_category_evidence(result))
        self.assertEqual(result['review_history'], revised['review_history'])
        self.assertEqual(result['codes'][0]['field_origins']['label'], 'assistant_development_review')
        self.assertEqual(result['semantic_quality'], 'not_human_validated')

    def test_same_source_content_kind_identity_and_malformed_gate_rejected(self):
        original = grouped()
        for patch in ({'kind': 'theme'}, {'id': 'CAT-arbitrary'}, {'id': True}):
            changed = deepcopy(original); changed['categories'][0].update(patch); seal(changed)
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                validate_category_evidence(changed)
        changed = deepcopy(original)
        changed['categories'].append(deepcopy(changed['categories'][0])); seal(changed)
        with self.assertRaises(ValueError):
            validate_category_evidence(changed)
        changed = deepcopy(original)
        first, second = changed['codes']
        second.update(unit_id=first['unit_id'], support_span_ids=list(first['support_span_ids']), evidence=deepcopy(first['evidence']))
        changed['categories'][0]['evidence'] = deepcopy(first['evidence'] + second['evidence']); seal(changed)
        with self.assertRaisesRegex(ValueError, 'distinct source contents'):
            validate_category_evidence(changed)
        for gate in ({}, {'reviewer_kind': 'automatic', 'code_ids': [row['id'] for row in original['codes']]},
                     {'reviewer_kind': 'researcher', 'code_ids': [original['codes'][0]['id']] * 2}):
            changed = deepcopy(original); changed['synthesis_gate'] = gate; seal(changed)
            with self.subTest(gate=gate), self.assertRaises(ValueError):
                validate_category_evidence(changed)

    def test_empty_categories_no_group_and_raw_failures_remain_unchanged(self):
        raw = native()
        for client in (Client(no_group=True), Client(fail=True)):
            result = run_synthesis(raw, client, [row['id'] for row in raw['codes']], reviewer_kind='assistant_development_review')
            before = deepcopy(result)
            self.assertEqual(result['categories'], [])
            self.assertIsNone(validate_category_evidence(result))
            self.assertEqual(result, before)
        self.assertIsNone(validate_category_evidence({'categories': []}))
        with self.assertRaises(ValueError):
            validate_category_evidence({'categories': {}})
        cleanup_failed = grouped(); cleanup_failed['synthesis_status'] = 'cleanup_failed'; seal(cleanup_failed)
        self.assertIsNone(validate_category_evidence(cleanup_failed))


if __name__ == '__main__':
    unittest.main()

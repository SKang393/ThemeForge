from copy import deepcopy
from hashlib import sha256
import unittest

from themeforge.drift_review import review_drift


def fixture(text, *, label='', claim='', definition='', qualification=None):
    source = text + ('\n' + qualification if qualification else '')
    source_hash = sha256(source.encode()).hexdigest()
    e = dict(unit_id='u', source_name='fiction', source_hash=source_hash,
             source_start=0, source_end=len(text), text=text)
    registry = {'e1': e}
    qs = []
    if qualification:
        q = dict(unit_id='u', source_name='fiction', source_hash=source_hash,
                 source_start=len(text)+1, source_end=len(source), text=qualification)
        registry['e2'] = q
        qs.append(q)
    return dict(registry=registry, unit_archive=[]), dict(unit_id='u', label=label, claim=claim,
        definition=definition, evidence=[e], qualifications=qs, counterexamples=[], referent_context=[]), {
        'fiction': dict(source_hash=source_hash, text=source)}


class DriftReviewTests(unittest.TestCase):
    def test_desire_finite_label_warns_but_abbreviation_and_preference_do_not(self):
        result, code, sources = fixture('I want to try first.', label='Tries first')
        self.assertEqual(review_drift(result, code, sources=sources)['warnings'][0]['kind'],
                         'desired_action_as_performed_needs_review')
        for label in ('Try first', 'Wants to try first'):
            code['label'] = label
            self.assertEqual(review_drift(result, code, sources=sources)['warnings'], [])

    def test_actor_target_binding_keeps_qualification_role_and_does_not_mutate(self):
        result, code, sources = fixture('I can offer that option if the pupil wants it.',
            definition='The offer notes a lack of inquiry into family preference.',
            qualification='I have not asked the pupil yet.')
        before = deepcopy((result, code, sources))
        review = review_drift(result, code, sources=sources)
        warning = next(w for w in review['warnings'] if w['kind']=='inquiry_target_shift_needs_review')
        self.assertEqual(warning['source_evidence'][0]['role'], 'qualifications')
        self.assertEqual((result, code, sources), before)
        source = sources['fiction']['text']
        for evidence in warning['source_evidence']:
            self.assertEqual(source[evidence['source_start']:evidence['source_end']], evidence['text'])
        for evidence in warning['field_evidence']:
            self.assertEqual(code[evidence['field']][evidence['start']:evidence['end']], evidence['text'])

    def test_can_to_will_and_reported_wish_have_distinct_cues(self):
        result, code, sources = fixture('I can offer the option if requested.', definition='I will offer it if requested.')
        self.assertEqual(review_drift(result, code, sources=sources)['warnings'][0]['kind'],
                         'ability_to_commitment_needs_review')
        result, code, sources = fixture('My tutor sometimes wants me to check first.', label='Tutor requests checking first')
        self.assertEqual(review_drift(result, code, sources=sources)['warnings'][0]['kind'],
                         'reported_wish_as_request_needs_review')

    def test_faithful_paraphrase_and_generic_qualification_not_classified_as_error(self):
        result, code, sources = fixture('I use the checklist when I do not know the shelf.',
            definition='The checklist is used for unfamiliar shelves.',
            qualification='For known shelves I want to try first.')
        review = review_drift(result, code, sources=sources)
        self.assertEqual(review['warnings'], [])
        self.assertEqual(review['qualification_relevance_prediction'], 'not_implemented_generic_attention_only')
        self.assertEqual(review['attention_cues'][0]['kind'], 'qualification_relationship_unverified')
        self.assertEqual(review['baseline_review']['version'], 'themeforge.meaning-review.v2')

    def test_registry_offsets_hash_and_missing_full_source_rejected(self):
        for mutation in ('source', 'offset', 'hash', 'registry', 'missing'):
            result, code, sources = fixture('I want to try first.', label='Tries first')
            if mutation == 'source': sources['fiction']['text'] += '!'
            elif mutation == 'offset': code['evidence'][0]['source_start'] = 1
            elif mutation == 'hash': code['evidence'][0]['source_hash'] = '0'*64
            elif mutation == 'registry':
                result['registry']['e2'] = dict(result['registry']['e1'], unit_id='conflicting-unit')
            else: sources = {}
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                review_drift(result, code, sources=sources)

    def test_exact_registry_aliases_do_not_inflate_warnings(self):
        result, code, sources = fixture('I want to try first.', label='Tries first')
        result['registry']['alias'] = deepcopy(result['registry']['e1'])
        warnings = review_drift(result, code, sources=sources)['warnings']
        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0]['source_evidence'][0]['span_id'], 'alias')

    def test_explicit_faithful_will_and_adjacent_negation_do_not_trigger(self):
        result, code, sources = fixture('I can offer it. I will offer it tomorrow.', definition='I will offer it tomorrow.')
        self.assertEqual(review_drift(result, code, sources=sources)['warnings'], [])
        result, code, sources = fixture('I can offer it.', definition='I will offer it.' )
        code['definition']='I can offer it, not will offer it.'
        self.assertEqual(review_drift(result, code, sources=sources)['warnings'], [])


if __name__ == '__main__':
    unittest.main()

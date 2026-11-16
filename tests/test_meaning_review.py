"""Conservative warning/provenance checks on explicitly invented source fixtures."""
from copy import deepcopy
import unittest

from themeforge.analysis_types import TranscriptDocument
from themeforge.codebook_meaning import review_meaning
from themeforge.codebook_review import revise_evidence_roles, revise_supporting_evidence
from themeforge.local_codebook import prepare_codebook, validate_result_sources
from tests.test_codebook_review import fixture, seal


def source_case(text, claim, definition=None):
    result, ids = fixture('P1: ' + text)
    code = result['codes'][0]
    code.update(label='Synthetic wording test', claim=claim, definition=definition or claim,
                support_span_ids=ids, evidence=[deepcopy(result['registry'][sid]) for sid in ids])
    seal(result)
    return result, code


class MeaningReviewTests(unittest.TestCase):
    def kinds(self, result, code):
        return {item['kind'] for item in review_meaning(result, code)['warnings']}

    def test_explicit_modality_deletion_and_faithful_control(self):
        result, code = source_case('I might share my dataset if the team approves.',
                                  'The participant shares their dataset.')
        before = deepcopy(result)
        warnings = review_meaning(result, code)
        self.assertIn('possibility_scope_needs_review', self.kinds(result, code))
        self.assertIn('conditional_scope_needs_review', self.kinds(result, code))
        self.assertEqual(result, before)
        for warning in warnings['warnings']:
            for entry in warning['source_evidence']:
                source = next(s['text'] for s in result['source_documents'] if s['name'] == entry['source_name'])
                self.assertEqual(source[entry['source_start']:entry['source_end']], entry['text'])
        for cue in warnings['source_cues']:
            source = next(s['text'] for s in result['source_documents'] if s['name'] == cue['source_name'])
            self.assertEqual(source[cue['source_start']:cue['source_end']], cue['text'])
        code['claim'] = code['definition'] = 'The participant might share their dataset if the team approves.'
        self.assertNotIn('possibility_scope_needs_review', self.kinds(result, code))
        self.assertNotIn('conditional_scope_needs_review', self.kinds(result, code))

    def test_best_of_knowledge_and_negation_are_not_universal_facts(self):
        result, code = source_case('To the best of my knowledge, our group has not uploaded the data.',
                                  'The group has uploaded the data.')
        self.assertTrue({'knowledge_limit_scope_needs_review', 'negation_scope_needs_review'} <= self.kinds(result, code))
        code['claim'] = code['definition'] = 'To the best of their knowledge, their group has not uploaded the data.'
        self.assertNotIn('knowledge_limit_scope_needs_review', self.kinds(result, code))
        self.assertNotIn('negation_scope_needs_review', self.kinds(result, code))

    def test_condition_paraphrases_do_not_require_literal_if(self):
        for source, claim in (
            ("If I am doing a modeling study, I will place its outputs in a repository.",
             'For modeling studies, the participant will place outputs in a repository.'),
            ('If my computer crashes, I still have the code on the other machine.',
             'The separate copy retains access in the event of a computer crash.'),
        ):
            result, code = source_case(source, claim)
            self.assertNotIn('conditional_scope_needs_review', self.kinds(result, code))
        # Suppression is deliberately not a claim of equivalent meaning.
        self.assertIn('without proving equivalent meaning', ' '.join(review_meaning(result, code)['limitations']))

    def test_would_is_not_forced_into_a_future_plan(self):
        result, code = source_case('We would send it to the repository.', 'The participant plans to send it to the repository.')
        self.assertTrue({'would_scope_needs_review', 'intention_inference_needs_review'} <= self.kinds(result, code))
        code['claim'] = code['definition'] = 'The participant describes a planned or habitual workflow of sending it to the repository.'
        self.assertNotIn('would_scope_needs_review', self.kinds(result, code))

    def test_object_role_warning_does_not_resolve_or_attach_context(self):
        docs = [TranscriptDocument('synthetic-referents', 'Interviewer 1: What is shared?\nParticipant 1: We share raw data.\nInterviewer 1: What happens next?\nParticipant 1: We would send it to our repository for others to access.')]
        packet = prepare_codebook(docs, prompt_variant='grounded-v6', speaker_label_policy='explicit_roles')
        unit = next(u for u in packet['unit_archive'] if u['text'].startswith('We would'))
        evidence = {sid: entry for sid, entry in packet['registry'].items() if entry['unit_id'] == unit['id']}
        result = dict(packet)
        code = dict(unit_id=unit['id'], label='Proposed sharing', claim=unit['text'],
                    definition='Sending a repository to the hosting site for others to access.',
                    support_span_ids=list(evidence), evidence=list(evidence.values()), qualifications=[], counterexamples=[], referent_context=[])
        before = deepcopy(code)
        kinds = self.kinds(result, code)
        self.assertIn('referent_context_not_selected', kinds)
        self.assertIn('location_object_substitution_needs_review', kinds)
        self.assertEqual(code, before)

    def test_role_reminders_never_assign_truth_or_repair_a_claim(self):
        result, ids = fixture()
        revised = revise_evidence_roles(result, 'LC-synthetic', {'qualifications': [ids[1]], 'counterexamples': [ids[2]]},
            reviewer_kind='assistant_development_review', reason='Synthetic role editing test, not study findings.')
        before = deepcopy(revised)
        kinds = self.kinds(revised, revised['codes'][0])
        self.assertIn('qualification_relationship_unverified', kinds)
        self.assertIn('counterexample_relationship_unverified', kinds)
        self.assertEqual(revised, before)
        self.assertEqual(revised['semantic_quality'], 'not_human_validated')

    def test_rehashed_provenance_tampering_and_missing_history_are_rejected(self):
        result, ids = fixture()
        revised = revise_evidence_roles(result, 'LC-synthetic', {'qualifications': [ids[1]]},
            reviewer_kind='assistant_development_review', reason='Synthetic explicit role edit.')
        for mode in ('foreign_snapshot', 'actor', 'history', 'selection', 'metadata_removed'):
            bad = deepcopy(revised)
            if mode == 'foreign_snapshot':
                foreign = next(sid for sid, entry in bad['registry'].items() if entry['unit_id'] != bad['codes'][0]['unit_id'])
                bad['codes'][0]['original_model_evidence_roles']['support'] = [foreign]
            elif mode == 'actor': bad['codes'][0]['evidence_role_origins']['qualifications'] = 'researcher'
            elif mode == 'history': bad['review_history'] = []
            elif mode == 'metadata_removed':
                bad['codes'][0].pop('original_model_evidence_roles')
                bad['codes'][0].pop('evidence_role_origins')
            else: bad['codes'][0]['qualifications'] = [deepcopy(bad['registry'][ids[2]])]
            seal(bad)
            with self.subTest(mode=mode), self.assertRaises(ValueError): validate_result_sources(bad)

    def test_legacy_support_history_can_precede_new_role_edits(self):
        original, ids = fixture()
        revised = revise_supporting_evidence(original, 'LC-synthetic', ids[:2],
            reviewer_kind='assistant_development_review', reason='Synthetic earlier support revision.')
        # Simulate the earlier persisted format, which predates role metadata.
        revised['codes'][0].pop('original_model_evidence_roles')
        revised['codes'][0].pop('evidence_role_origins')
        for key in ('old_roles', 'new_roles', 'old_role_origins', 'new_role_origins'):
            revised['review_history'][0].pop(key)
        seal(revised)
        next_result = revise_evidence_roles(revised, 'LC-synthetic', {'qualifications': [ids[2]]},
            reviewer_kind='researcher', reason='Synthetic researcher qualification edit.')
        validate_result_sources(next_result)
        self.assertEqual(next_result['codes'][0]['original_model_evidence_roles']['support'], ids[:1])
        self.assertEqual(next_result['codes'][0]['evidence_role_origins']['support'], 'assistant_development_review')


if __name__ == '__main__':
    unittest.main()

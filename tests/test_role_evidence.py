"""Fictional structural fixtures, not qualitative accuracy or human review."""
from copy import deepcopy
from hashlib import sha256
import json
import unittest

from themeforge.role_evidence import (append_interpretation, build_role_evidence,
    experimental_input, export_role_evidence, new_assertion, source_span,
    unresolved_reading, unresolved_role, validate_role_evidence)


TEXT = ('Interviewer 1: What happened?\n'
        'Participant 1: [Participant 2], do you think someone might say "I will ask her"?\n'
        'Participant 2: My tutor said she may offer help if I ask.\n')


def fixture():
    source = dict(source_name='fictional-role-test', source_hash=sha256(TEXT.encode()).hexdigest(), text=TEXT)
    start = TEXT.index('[Participant 2]')
    end = TEXT.index('\nParticipant 2:')
    unit = dict(id='test-turn-1', speaker='Participant 1', **source_span(source, start, end))
    qstart = TEXT.index('What happened?')
    context = source_span(source, qstart, qstart + len('What happened?'))
    return source, unit, build_role_evidence(unit, source, contexts=[context])


def snippet(source, text, *, start=0):
    begin = source['text'].index(text, start)
    return source_span(source, begin, begin + len(text))


def proposed_role(mention, evidence, rationale='Provisional interpretation of this fictional fixture.'):
    return dict(status='proposed', mentions=[mention], evidence=evidence,
                rationale=rationale, alternatives=[])


def append(record, source, assertions, **kwargs):
    return append_interpretation(record, source, assertions, origin_kind=kwargs.get('kind', 'ai_inference'),
                                 author=kwargs.get('author', 'unit-test AI'), method='fictional structural fixture')


class RoleEvidenceTests(unittest.TestCase):
    def test_correct_source_speaker_and_unknown_interpretive_roles(self):
        source, unit, record = fixture()
        self.assertEqual(record['imported']['source_speaker']['source_local_label'], 'Participant 1')
        self.assertFalse(record['imported']['saved_label_conflict'])
        view = experimental_input(record, source)
        self.assertEqual(view['imported']['target']['text'], unit['text'])
        self.assertTrue(all(r['status'] == 'unknown' and not r['mentions'] for r in view['role_slots'].values()))
        self.assertEqual(view['action_status']['status'], 'unknown')
        self.assertFalse(view['annotation_history_included'])
        self.assertEqual(view['imported']['contexts'][0]['source_speaker']['source_local_label'], 'Interviewer 1')

    def test_disagreement_with_saved_metadata_is_visible_not_source_mutation(self):
        source, unit, _ = fixture()
        unit['speaker'] = 'Participant 2'
        before = deepcopy(unit)
        record = build_role_evidence(unit, source)
        self.assertTrue(record['imported']['saved_label_conflict'])
        self.assertEqual(record['imported']['saved_speaker_label'], 'Participant 2')
        self.assertEqual(record['imported']['source_speaker']['source_local_label'], 'Participant 1')
        self.assertEqual(unit, before)

    def test_literal_addressee_does_not_replace_source_speaker_or_inner_actor(self):
        source, unit, record = fixture()
        assertion = new_assertion('outer-question', record['imported']['target'])
        mention = snippet(source, 'Participant 2', start=unit['source_start'])
        assertion['roles']['addressee'] = proposed_role(mention, [mention])
        annotated = append(record, source, [assertion])
        self.assertEqual(annotated['imported']['source_speaker']['source_local_label'], 'Participant 1')
        self.assertEqual(annotated['revisions'][0]['assertions'][0]['roles']['attributed_actor']['status'], 'unknown')
        self.assertEqual(record['revisions'], [])

    def test_nested_quoted_first_person_stays_unknown_and_question_stays_literal(self):
        source, unit, record = fixture()
        outer = new_assertion('outer', record['imported']['target'])
        inner = new_assertion('inner', snippet(source, 'I will ask her'), parent_id='outer')
        inner['speech_form'] = dict(status='proposed', description='Quoted imagined speech, not evidence it was said.',
                                    evidence=[inner['span']], scope_evidence=[snippet(source, 'do you think')],
                                    rationale='Synthetic fixture uses a quoted illustration inside a question.', alternatives=[])
        inner['action_status'] = unresolved_reading('Question does not establish that asking happened.',
            status='ambiguous', evidence=[inner['span']], alternatives=['Imagined intention', 'Illustrated possible future speech'])
        inner['roles']['perspective_holder'] = unresolved_role('Inner I has no identified person.', evidence=[snippet(source, 'I will')])
        annotated = append(record, source, [outer, inner])
        self.assertEqual(annotated['revisions'][0]['assertions'][1]['parent_id'], 'outer')
        self.assertEqual(annotated['revisions'][0]['assertions'][1]['roles']['perspective_holder']['status'], 'unknown')
        self.assertIn('?', annotated['imported']['target']['text'])

    def test_pronoun_cannot_become_resolved_identity_or_free_invented_name(self):
        source, _, record = fixture()
        for mention in (snippet(source, 'I', start=TEXT.index('"I')), snippet(source, 'her')):
            assertion = new_assertion('a', record['imported']['target'])
            assertion['roles']['attributed_actor'] = proposed_role(mention, [mention])
            with self.assertRaisesRegex(ValueError, 'Pronouns'):
                append(record, source, [assertion])
        assertion = new_assertion('a', record['imported']['target'])
        assertion['roles']['attributed_actor']['invented_person_id'] = 'a guessed pupil'
        with self.assertRaisesRegex(ValueError, 'unexpected'):
            append(record, source, [assertion])

    def test_context_cannot_supply_substantive_action_status_or_assertion(self):
        source, _, record = fixture()
        context = record['imported']['contexts'][0]['span']
        with self.assertRaisesRegex(ValueError, 'target'):
            append(record, source, [new_assertion('context-as-target', context)])
        assertion = new_assertion('a', record['imported']['target'])
        assertion['action_status'] = dict(status='proposed', description='Something happened.',
            evidence=[assertion['span'], context], scope_evidence=[], rationale='Intentionally invalid context import.', alternatives=[])
        with self.assertRaisesRegex(ValueError, 'context is not support'):
            append(record, source, [assertion])

    def test_wrong_offsets_stale_source_and_silent_evidence_edits_fail(self):
        source, unit, record = fixture()
        for bad_source in (dict(source, text=source['text'] + 'changed'), dict(source, source_hash='0' * 64)):
            with self.assertRaises(ValueError):
                validate_role_evidence(record, bad_source)
        with self.assertRaises(ValueError):
            source_span(source, True, 2)
        damaged = deepcopy(record)
        damaged['imported']['source_speaker']['source_local_label'] = 'Participant 2'
        with self.assertRaises(ValueError):
            validate_role_evidence(damaged, source)
        assertion = new_assertion('a', record['imported']['target'])
        assertion['span']['text'] = 'A rewritten source.'
        with self.assertRaises(ValueError):
            append(record, source, [assertion])

    def test_parent_scope_and_ambiguous_abstention_are_explicit(self):
        source, _, record = fixture()
        inner = new_assertion('inner', snippet(source, 'I will ask her'), parent_id='missing')
        with self.assertRaisesRegex(ValueError, 'containing parent'):
            append(record, source, [inner])
        outer = new_assertion('outer', record['imported']['target'])
        outer['roles']['attributed_actor'] = unresolved_role('No actor can be resolved here.', status='abstain')
        outer['action_status'] = unresolved_reading('Two readings remain.', status='ambiguous',
            evidence=[outer['span']], alternatives=['Literal inquiry', 'Rhetorical illustration'])
        annotated = append(record, source, [outer])
        self.assertEqual(annotated['revisions'][0]['assertions'][0]['roles']['attributed_actor']['status'], 'abstain')

    def test_ai_and_declared_researcher_revisions_remain_separate(self):
        source, _, record = fixture()
        first = append(record, source, [new_assertion('a', record['imported']['target'])])
        snapshot = deepcopy(first)
        revised_assertions = deepcopy(first['revisions'][0]['assertions'])
        revised_assertions[0]['action_status'] = unresolved_reading('Researcher fixture abstains.', status='abstain')
        second = append(first, source, revised_assertions, kind='researcher_annotation', author='synthetic test author, not a human reviewer')
        self.assertEqual(first, snapshot)
        self.assertEqual(second['revisions'][0], first['revisions'][0])
        self.assertEqual(second['revisions'][1]['previous_revision_id'], first['revisions'][0]['id'])
        self.assertFalse(second['revisions'][1]['origin']['authorship_verified'])
        self.assertEqual(json.loads(export_role_evidence(second, source)), second)
        self.assertFalse(second['training_eligible'])
        self.assertFalse(second['semantic_validation'])
        self.assertEqual(experimental_input(second, source), experimental_input(record, source))

    def test_changed_history_and_attempted_semantic_promotion_fail(self):
        source, _, record = fixture()
        annotated = append(record, source, [new_assertion('a', record['imported']['target'])])
        for key in ('semantic_validation', 'training_eligible'):
            damaged = deepcopy(annotated)
            damaged[key] = True
            with self.assertRaises(ValueError):
                validate_role_evidence(damaged, source)
        damaged = deepcopy(annotated)
        damaged['revisions'][0]['origin']['kind'] = 'researcher_annotation'
        with self.assertRaisesRegex(ValueError, 'digest'):
            validate_role_evidence(damaged, source)

    def test_structural_validity_does_not_certify_a_wrong_interpretation(self):
        source, _, record = fixture()
        assertion = new_assertion('a', record['imported']['target'])
        assertion['action_status'] = dict(status='proposed', description='An observed completed action.',
            evidence=[assertion['span']], scope_evidence=[], rationale='Intentionally semantically wrong fixture with a real citation.', alternatives=[])
        annotated = append(record, source, [assertion])
        validate_role_evidence(annotated, source)
        self.assertFalse(annotated['semantic_validation'])
        self.assertEqual(annotated['revisions'][0]['origin']['kind'], 'ai_inference')

    def test_multiple_turns_or_unlabeled_text_never_yield_a_single_person(self):
        source, unit, _ = fixture()
        mixed = dict(unit, **source_span(source, unit['source_start'], len(source['text'])))
        record = build_role_evidence(mixed, source)
        self.assertEqual(record['imported']['source_speaker']['status'], 'ambiguous')
        self.assertIsNone(record['imported']['source_speaker']['source_local_label'])
        text = 'Someone might have asked her. I cannot tell who.'
        unlabeled = dict(source_name='fictional-unlabeled', source_hash=sha256(text.encode()).hexdigest(), text=text)
        value = build_role_evidence(dict(source_span(unlabeled, 0, len(text)), id='unlabeled', speaker='Unknown'), unlabeled)
        self.assertEqual(value['imported']['source_speaker']['status'], 'unknown')

    def test_source_only_input_excludes_interpretation_bearing_saved_metadata(self):
        source, unit, record = fixture()
        changed = dict(unit, speaker='AI proposes an observed completed action', id='A developer label')
        other = build_role_evidence(changed, source, contexts=[record['imported']['contexts'][0]['span']])
        self.assertEqual(experimental_input(other, source), experimental_input(record, source))
        with self.assertRaises(ValueError):
            build_role_evidence(dict(unit, speaker={'interpretation': 'invented'}), source)

    def test_outer_scope_citation_cannot_be_context_or_an_unlinked_turn(self):
        source, _, record = fixture()
        outer = new_assertion('outer', record['imported']['target'])
        inner = new_assertion('inner', snippet(source, 'I will ask her'), parent_id='outer')
        inner['action_status'] = unresolved_reading('Unknown inside an outer question.', evidence=[inner['span']])
        inner['action_status']['scope_evidence'] = [snippet(source, 'do you think')]
        append(record, source, [outer, inner])
        inner['action_status']['scope_evidence'] = [record['imported']['contexts'][0]['span']]
        with self.assertRaisesRegex(ValueError, 'ancestor'):
            append(record, source, [outer, inner])

    def test_literal_phrase_anchor_is_not_certified_as_a_person(self):
        source, _, record = fixture()
        assertion = new_assertion('a', record['imported']['target'])
        phrase = snippet(source, 'I will')
        assertion['roles']['attributed_actor'] = proposed_role(phrase, [phrase], 'Deliberately questionable nominal selection.')
        value = append(record, source, [assertion])
        self.assertFalse(value['semantic_validation'])
        self.assertEqual(value['revisions'][0]['origin']['kind'], 'ai_inference')


if __name__ == '__main__':
    unittest.main()

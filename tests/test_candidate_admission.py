"""Offline synthetic contract checks, separate from frozen saved-case evaluation.

Researcher events below are declared fixture data, never actual human approval.
Passing this gate proves neither semantic correctness nor theme acceptance.
"""
from copy import deepcopy
from hashlib import sha256
import json
import unittest

from themeforge.candidate_admission import (
    LEGACY_VERSION, build_native_admissions, create_admission, effective_admission, grouping_inputs,
    override_admission, render_admission_html, run_admitted_synthesis, validate_admission,
)
from themeforge.role_evidence import source_span


def fixture(body='My tutor might offer help if I ask.', *, speaker='Participant 1',
            label='Participant 1 reports receiving help.', claim=None):
    text = f'{speaker}: {body}\n'
    source = dict(source_name='synthetic-admission-fixture', text=text,
                  source_hash=sha256(text.encode()).hexdigest())
    start = len(speaker) + 2
    target = dict(source_span(source, start, start + len(body)),
                  id='synthetic-unit', speaker=speaker)
    evidence = source_span(source, start, start + len(body))
    candidate = dict(id='synthetic-code', label=label, claim=claim or label,
                     extra={'preserve': ['literal', 17]})
    return source, target, candidate, evidence


def build(**kwargs):
    source, target, candidate, evidence = fixture(**kwargs)
    record = create_admission(candidate, target, source, evidence=[evidence],
                              provenance={'kind': 'synthetic_test_only'})
    return source, record


def digest(value):
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                             separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def rehash(record):
    record['record_sha256'] = digest({k: v for k, v in record.items()
                                      if k != 'record_sha256'})
    return record


class CandidateAdmissionTests(unittest.TestCase):
    def test_wrong_reporting_subject_is_review_needed(self):
        source, record = build(label='Participant 2 reports receiving help.')
        self.assertEqual(record['assessment']['state'], 'review_needed')
        self.assertFalse(effective_admission(record)['eligible'])
        validate_admission(record, source)

    def test_surface_match_can_pass_semantically_false_content(self):
        source, record = build(label='Participant 1 reports that the moon is made of cheese.')
        self.assertEqual(record['assessment']['state'], 'pass')
        self.assertTrue(effective_admission(record)['eligible'])
        self.assertIn('scope', record['assessment'])
        validate_admission(record, source)

    def test_embedded_other_actor_is_not_compared_with_source_speaker(self):
        for claim in ('Participant 1 says Participant 2 offered help.',
                      'Participant 1 asks Participant 2 whether a tutor can help.'):
            with self.subTest(claim=claim):
                _, record = build(label=claim)
                self.assertEqual(record['assessment']['state'], 'pass')

    def test_literal_other_reporter_in_target_causes_uncertainty(self):
        _, record = build(body='Yesterday Participant 2 said the tutor could help.',
                          label='Participant 2 reports receiving help.')
        self.assertEqual(record['assessment']['state'], 'abstain')

    def test_uncheckable_leading_forms_abstain_without_conflict(self):
        labels = ['"Participant 2 says hello"', 'If Participant 2 says hello, I listen.',
                  'Participant 2 might report receiving help.',
                  'Participant 2 receives help.', 'She reports receiving help.',
                  'A tutor says help is available.', 'Receiving help from Participant 2']
        for label in labels:
            with self.subTest(label=label):
                _, record = build(label=label)
                self.assertEqual(record['assessment']['state'], 'abstain')

    def test_claim_and_definition_leading_conflicts_are_checked(self):
        source, target, candidate, evidence = fixture()
        for field in ('claim', 'definition'):
            with self.subTest(field=field):
                changed = dict(candidate, **{field: 'Participant 2 says help arrived.'})
                record = create_admission(changed, target, source, evidence=[evidence])
                self.assertEqual(record['assessment']['state'], 'review_needed')

    def test_saved_label_conflict_retains_original_metadata(self):
        source, target, candidate, evidence = fixture()
        target['speaker'] = 'Participant 2'
        record = create_admission(candidate, target, source, evidence=[evidence])
        self.assertEqual(record['assessment']['state'], 'review_needed')
        self.assertEqual(record['original']['target']['speaker'], 'Participant 2')

    def test_unsupported_teacher_header_retains_parser_result_but_flags_saved_conflict(self):
        text = 'Interviewer: What happened?\nTeacher: I could offer help if asked.\n'
        body = 'I could offer help if asked.'
        source = dict(source_name='synthetic-unsupported-teacher-header', text=text,
                      source_hash=sha256(text.encode()).hexdigest())
        start = text.index(body)
        span = source_span(source, start, start + len(body))
        target = dict(span, speaker='Teacher', id='synthetic-teacher-turn')
        candidate = dict(label='Teacher offers possible help.',
                         claim='Teacher says help may be available.')
        record = create_admission(candidate, target, source, evidence=[span], version=LEGACY_VERSION)
        self.assertEqual(record['version'], 'themeforge.experimental-candidate-admission.v1.1')
        assessment = record['assessment']
        self.assertEqual(assessment['state'], 'review_needed')
        self.assertEqual(assessment['source_speaker']['status'], 'conflicting_source_metadata')
        self.assertIsNone(assessment['source_speaker']['source_local_label'])
        self.assertEqual(assessment['imported']['source_speaker']['source_local_label'], 'Interviewer')
        self.assertEqual(assessment['imported']['saved_speaker_label'], 'Teacher')
        self.assertTrue(assessment['imported']['saved_label_conflict'])
        self.assertIn('saved_label_conflict', [reason['code'] for reason in assessment['reasons']])
        self.assertFalse(effective_admission(record)['eligible'])
        self.assertFalse(record['semantic_validation'])
        self.assertEqual(record['original']['target'], target)
        validate_admission(record, source)

    def test_unknown_multiple_and_interviewer_attribution_abstain(self):
        for text, start, end in [
            ('An unlabeled answer.', 0, len('An unlabeled answer.')),
            ('Participant 1: First.\nParticipant 2: Second.', 15,
             len('Participant 1: First.\nParticipant 2: Second.')),
            ('Interviewer: What happened?', 13, len('Interviewer: What happened?')),
        ]:
            with self.subTest(text=text):
                source = dict(source_name='synthetic-unresolved', text=text,
                              source_hash=sha256(text.encode()).hexdigest())
                target = source_span(source, start, end)
                record = create_admission(dict(label='Participant 1 says help arrived.',
                                                claim='Participant 1 says help arrived.'),
                                          target, source, evidence=[deepcopy(target)])
                self.assertEqual(record['assessment']['state'], 'abstain')

    def test_bad_source_proof_is_quarantined_and_cannot_be_released(self):
        source, target, candidate, evidence = fixture()
        for field, value in [('text', 'Changed text.'), ('source_start', target['source_start'] + 1),
                             ('source_hash', '0' * 64), ('source_name', 'wrong-source')]:
            with self.subTest(field=field):
                bad = dict(target, **{field: value})
                record = create_admission(candidate, bad, source, evidence=[evidence])
                self.assertEqual(record['assessment']['state'], 'abstain')
                self.assertFalse(effective_admission(record)['eligible'])
                with self.assertRaises(ValueError):
                    override_admission(record, source, decision='admit', reviewer_kind='researcher',
                                       author='synthetic declared researcher fixture',
                                       reason='Synthetic release attempt with failed source proof.')

    def test_override_history_is_append_only_reversible_and_preserves_original(self):
        source, record = build(label='Participant 2 reports receiving help.')
        original = deepcopy(record)
        current = record
        for decision, eligible in [('admit', True), ('withhold', False), ('reset', False)]:
            previous = deepcopy(current)
            current = override_admission(current, source, decision=decision,
                reviewer_kind='researcher', author='synthetic declared researcher fixture',
                reason='Synthetic contract event, not actual human approval.')
            self.assertEqual(current['history'][:-1], previous['history'])
            self.assertEqual(current['assessment'], original['assessment'])
            self.assertEqual(current['original'], original['original'])
            self.assertEqual(effective_admission(current)['eligible'], eligible)
            validate_admission(current, source)
        self.assertEqual(record, original)
        self.assertEqual(len(current['history']), 3)

    def test_ai_cannot_release_as_researcher(self):
        source, record = build(label='Participant 2 reports receiving help.')
        with self.assertRaises(ValueError):
            override_admission(record, source, decision='admit',
                reviewer_kind='assistant_development_review', author='unit test assistant',
                reason='AI fixture must never authorize researcher release.')

    def test_ai_reset_cannot_release_researcher_hold_on_automatic_pass(self):
        source, record = build()
        self.assertEqual(record['assessment']['state'], 'pass')
        held = override_admission(record, source, decision='withhold',
            reviewer_kind='researcher', author='synthetic declared researcher fixture',
            reason='Synthetic hold fixture, not actual human review.')
        before = deepcopy(held)
        self.assertFalse(effective_admission(held)['eligible'])
        with self.assertRaises(ValueError):
            override_admission(held, source, decision='reset',
                reviewer_kind='assistant_development_review', author='unit test assistant',
                reason='AI fixture must not release the existing hold through reset.')
        self.assertEqual(held, before)

    def test_definition_only_saved_candidate_is_preserved_without_manufactured_fields(self):
        source, target, _, evidence = fixture()
        candidate = dict(decision='define', definition='Participant 1 states that help may be available.',
                         qualification_ids=[], counterexample_ids=[])
        before = deepcopy(candidate)
        record = create_admission(candidate, target, source, evidence=[evidence])
        self.assertEqual(record['assessment']['state'], 'pass')
        self.assertEqual(record['original']['candidate'], before)
        self.assertNotIn('label', record['original']['candidate'])
        self.assertNotIn('claim', record['original']['candidate'])
        validate_admission(record, source)
        html = render_admission_html([record], {source['source_name']: source})
        self.assertIn(candidate['definition'], html)
        selected = grouping_inputs([record], {source['source_name']: source}, [record['id']])
        self.assertEqual(selected['candidates'], [before])

    def test_tampered_assessment_fails_even_after_outer_rehash(self):
        source, record = build(label='Participant 2 reports receiving help.')
        changed = deepcopy(record)
        changed['assessment']['state'] = 'pass'
        with self.assertRaises(ValueError):
            validate_admission(rehash(changed), source)

    def test_tampered_history_fails_even_after_outer_rehash(self):
        source, record = build(label='Participant 2 reports receiving help.')
        record = override_admission(record, source, decision='withhold',
            reviewer_kind='researcher', author='synthetic declared researcher fixture',
            reason='Synthetic event, not actual human approval.')
        changed = deepcopy(record)
        changed['history'][0]['decision'] = 'admit'
        with self.assertRaises(ValueError):
            validate_admission(rehash(changed), source)

    def test_original_candidate_and_inputs_are_deep_copied(self):
        source, target, candidate, evidence = fixture()
        expected = deepcopy(candidate)
        record = create_admission(candidate, target, source, evidence=[evidence])
        candidate['extra']['preserve'].append('changed')
        target['speaker'] = 'Participant 99'
        evidence['text'] = 'changed'
        self.assertEqual(record['original']['candidate'], expected)
        validate_admission(record, source)

    def test_support_cannot_be_forged_or_imported_from_another_source(self):
        source, target, candidate, evidence = fixture()
        for bad in [dict(evidence, text='Invented support.'),
                    dict(evidence, source_name='another-source')]:
            with self.subTest(evidence=bad):
                record = create_admission(candidate, target, source, evidence=[bad])
                self.assertEqual(record['assessment']['state'], 'abstain')
                self.assertFalse(effective_admission(record)['eligible'])

    def test_unknown_override_decision_and_reviewer_kind_are_rejected(self):
        source, record = build()
        for changes in [dict(decision='accept_theme', reviewer_kind='researcher'),
                        dict(decision='admit', reviewer_kind='human'),
                        dict(decision='admit', reviewer_kind='AI')]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                override_admission(record, source, author='synthetic test author',
                                   reason='Invalid synthetic API invocation.', **changes)

    def test_grouping_refuses_unknown_duplicate_or_withheld_without_partial_output(self):
        source, passed = build()
        _, held = build(label='Participant 2 reports receiving help.')
        records = [passed, held]
        sources = {source['source_name']: source}
        for ids in [[passed['id'], held['id']], [passed['id'], 'missing'],
                    [passed['id'], passed['id']]]:
            with self.subTest(ids=ids), self.assertRaises(ValueError):
                grouping_inputs(records, sources, ids)

    def test_grouping_preserves_selected_candidate_and_refuses_stale_sources(self):
        source, record = build()
        sources = {source['source_name']: source}
        payload = grouping_inputs([record], sources, [record['id']])
        self.assertIn(record['original']['candidate'], payload['candidates'])
        changed = dict(source, text=source['text'] + 'changed')
        with self.assertRaises(ValueError):
            grouping_inputs([record], {source['source_name']: changed}, [record['id']])

    def test_bounds_reject_large_fields_and_preserve_excess_support_as_abstention(self):
        source, target, candidate, evidence = fixture()
        with self.assertRaises(ValueError):
            create_admission(dict(candidate, label='x' * 8001), target, source,
                             evidence=[evidence])
        record = create_admission(candidate, target, source, evidence=[evidence] * 13)
        self.assertEqual(len(record['original']['evidence']), 13)
        self.assertEqual(record['assessment']['state'], 'abstain')
        self.assertFalse(record['assessment']['source_proof'])

    def test_review_html_escapes_candidates_and_retains_quarantined_rows(self):
        source, record = build(label='<script>alert("fixture")</script>')
        html = render_admission_html([record], {source['source_name']: source})
        self.assertNotIn('<script>', html)
        self.assertIn('&lt;script&gt;', html)
        self.assertIn(record['id'], html)
        self.assertIn('abstain', html)
        self.assertIn('Original support', html)
        self.assertIn('Append-only review history', html)

    def test_native_wrapper_rejects_quarantine_before_client_start(self):
        from tests.test_category_evidence import Client, native
        result = native()
        before = deepcopy(result)
        records = build_native_admissions(result)
        client = Client(no_group=True)
        with self.assertRaises(ValueError):
            run_admitted_synthesis(result, client, [c['id'] for c in result['codes']],
                                   records, reviewer_kind='assistant_development_review')
        self.assertEqual(client.started, 0)
        self.assertEqual(result, before)

    def test_native_wrapper_rejects_candidate_and_provenance_snapshot_mismatch(self):
        from tests.test_category_evidence import Client, native
        result = native()
        records = build_native_admissions(result)
        ids = [c['id'] for c in result['codes']]
        for change in ('candidate', 'provenance'):
            changed = deepcopy(records)
            if change == 'candidate':
                changed[0]['original']['candidate']['label'] = 'Stale candidate label'
            else:
                changed[0]['original']['provenance']['native_result_sha256'] = '0' * 64
            client = Client(no_group=True)
            with self.subTest(change=change), self.assertRaises(ValueError):
                run_admitted_synthesis(result, client, ids, changed,
                                       reviewer_kind='assistant_development_review')
            self.assertEqual(client.started, 0)

    def test_native_wrapper_preserves_explicit_researcher_fixture_history_and_no_group(self):
        from tests.test_category_evidence import Client, native
        from themeforge.local_codebook import validate_result_sources
        result = native()
        before = deepcopy(result)
        sources = {d['name']: dict(source_name=d['name'], source_hash=d['sha256'],
                                  text=d['text']) for d in result['source_documents']}
        records = [override_admission(record,
            sources[record['original']['target']['source_name']], decision='admit',
            reviewer_kind='researcher', author='synthetic declared researcher fixture',
            reason='Offline synthetic integration event, not actual human approval.')
            for record in build_native_admissions(result)]
        client = Client(no_group=True)
        grouped = run_admitted_synthesis(result, client, [c['id'] for c in result['codes']],
                                        records, reviewer_kind='assistant_development_review')
        self.assertEqual(client.started, 1)
        self.assertEqual(client.closed, 1)
        self.assertEqual(grouped['synthesis_status'], 'no_group')
        self.assertEqual(grouped['categories'], [])
        self.assertEqual(grouped['interpretive_themes'], [])
        self.assertEqual(grouped['candidate_admission_gate']['records'], records)
        self.assertEqual(grouped['codes'], before['codes'])
        self.assertEqual(result, before)
        validate_result_sources(grouped)

    def test_native_reopen_and_export_reject_rehashed_admission_sidecars(self):
        from tests.test_category_evidence import Client, native, seal
        from themeforge.local_codebook import export_codebook_html, validate_result_sources
        result = native()
        sources = {d['name']: dict(source_name=d['name'], source_hash=d['sha256'],
                                  text=d['text']) for d in result['source_documents']}
        records = [override_admission(record,
            sources[record['original']['target']['source_name']], decision='admit',
            reviewer_kind='researcher', author='synthetic declared researcher fixture',
            reason='Offline synthetic integration event, not actual human approval.')
            for record in build_native_admissions(result)]
        grouped = run_admitted_synthesis(result, Client(no_group=True),
            [c['id'] for c in result['codes']], records,
            reviewer_kind='assistant_development_review')
        for change in ('assessment', 'history', 'candidate'):
            altered = deepcopy(grouped)
            sidecar = altered['candidate_admission_gate']['records'][0]
            if change == 'assessment':
                sidecar['assessment']['state'] = 'pass'
            elif change == 'history':
                sidecar['history'][0]['decision'] = 'withhold'
            else:
                sidecar['original']['candidate']['label'] = 'Changed after grouping'
            rehash(sidecar)
            seal(altered)
            for validator in (validate_result_sources, export_codebook_html):
                with self.subTest(change=change, validator=validator.__name__), self.assertRaises(ValueError):
                    validator(altered)


if __name__ == '__main__':
    unittest.main()

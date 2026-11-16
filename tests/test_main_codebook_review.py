"""MAIN candidate-review contracts with fictional sources and zero inference/Tk."""
from copy import deepcopy
import json
import unittest

from themeforge.analysis_types import TranscriptDocument
from themeforge import codebook_review as review
from themeforge.codebook_meaning import reference_candidates
from themeforge.local_codebook import prepare_codebook, validate_result_sources, export_codebook_html
from themeforge.model_pilot import _digest
from themeforge.reviewed_grouping import add_reviewed_category


def seal(result):
    result['result_sha256'] = _digest({k: v for k, v in result.items() if k != 'result_sha256'})
    return result


def fictional_draft():
    documents = [TranscriptDocument('fictional-interview.txt',
        'Interviewer: What did your tutor say about next term?\n'
        'Participant 1: My tutor wants me to try work if transport is available. '
        'I do not know whether the placement will happen.\n'
        'Participant 2: I might try a course if my support continues.'),
        TranscriptDocument('fictional-unattributed.txt',
        'A placement may be possible if transport is available. No decision has been made.')]
    packet = prepare_codebook(documents, max_units=6)
    result = {key: deepcopy(packet[key]) for key in
              ('source_documents', 'registry', 'unit_archive', 'selected_unit_ids')}
    units = [u for u in packet['unit_archive'] if u['eligible']]
    codes = []
    for index, unit in enumerate(units):
        ids = [sid for sid, span in packet['registry'].items() if span['unit_id'] == unit['id']]
        codes.append(dict(id=f'LC-fiction-{index}', unit_id=unit['id'],
            label='Conditional placement possibility',
            claim='A placement remains possible subject to practical conditions.',
            definition='A possible placement depends on available practical support.',
            inclusion='Review proposed analyst guidance against the exact source.',
            exclusion='Exclusion boundaries have not been established.',
            uncertainty='The account does not establish that a placement happened.',
            support_span_ids=[ids[0]], evidence=[deepcopy(packet['registry'][ids[0]])],
            qualifications=[], counterexamples=[], status='definition_drafted',
            origin='local_model', review_status='unreviewed_machine_draft',
            referent_context=list(reference_candidates(unit, packet['unit_archive']).values())))
    result.update(codes=codes, calls=[{'stage': 'decision', 'raw': 'Frozen fictional AI wording.'}],
                  categories=[], interpretive_themes=[], unassigned=[], mode='open', research_questions=[],
                  semantic_quality='not_human_validated', review_status='unreviewed_machine_draft',
                  training_performed=False, status='completed',
                  packet_sha256=packet['packet_sha256'], elapsed_seconds=0.0)
    seal(result)
    validate_result_sources(result)
    return result


class MainCodebookReviewTests(unittest.TestCase):
    def decision(self, result, decision='accept', *, kind='assistant_development_review', code_id=None):
        return review.record_candidate_decision(result, code_id or result['codes'][0]['id'], decision,
            reviewer_kind=kind, reviewer_name='Offline fictional review',
            reason='Compare exact source speaker, reported actor, conditions and uncertainty.')

    def parts(self, result):
        parent = result['codes'][0]
        ids = [sid for sid, span in result['registry'].items() if span['unit_id'] == parent['unit_id']]
        self.assertGreaterEqual(len(ids), 2)
        return [dict(label='Tutor reported wish',
                     claim='The speaker reports a tutor wish conditional on transport.',
                     definition='A reported tutor wish to try work depends on transport.',
                     support_span_ids=[ids[0]]),
                dict(label='Placement outcome unknown',
                     claim='The speaker does not know whether the placement will happen.',
                     definition='The placement outcome remains explicitly unknown to the speaker.',
                     support_span_ids=[ids[1]])]

    def split(self, result, parts=None):
        return review.split_candidate(result, result['codes'][0]['id'],
            self.parts(result) if parts is None else parts,
            reviewer_kind='assistant_development_review', reviewer_name='Offline fictional review',
            reason='Separate a reported wish from explicitly limited outcome knowledge.')

    def test_legacy_candidate_remains_visible_unreviewed_and_groupable_by_existing_gate(self):
        result = fictional_draft()
        original = deepcopy(result)
        state = review.candidate_review_state(result, result['codes'][0]['id'])
        self.assertEqual(state['state'], 'unreviewed')
        self.assertFalse(state['researcher_approved'])
        self.assertIsNone(review.grouping_review_issue(result, result['codes'][0]['id']))
        self.assertEqual(result, original)

    def test_assistant_accept_preserves_original_and_never_becomes_human_validation(self):
        result = fictional_draft()
        original = deepcopy(result)
        updated = self.decision(result)
        self.assertEqual(result, original)
        self.assertEqual(updated['codes'], result['codes'])
        for field in ('calls', 'source_documents', 'registry', 'unit_archive'):
            self.assertEqual(updated[field], result[field])
        state = review.candidate_review_state(updated, result['codes'][0]['id'])
        self.assertEqual(state['state'], 'accept')
        self.assertEqual(state['reviewer_kind'], 'assistant_development_review')
        self.assertEqual(state['reviewer_name'], 'Offline fictional review')
        self.assertFalse(state['researcher_approved'])
        self.assertEqual(updated['semantic_quality'], 'not_human_validated')
        self.assertFalse(updated['training_performed'])
        self.assertNotEqual(updated['result_sha256'], result['result_sha256'])
        validate_result_sources(updated)

    def test_researcher_declared_acceptance_stays_distinct_from_ai_acceptance(self):
        result = self.decision(fictional_draft(), kind='researcher')
        state = review.candidate_review_state(result, result['codes'][0]['id'])
        self.assertTrue(state['researcher_approved'])
        self.assertEqual(state['reviewer_kind'], 'researcher')
        self.assertEqual(result['semantic_quality'], 'not_human_validated')

    def test_reject_and_uncertain_do_not_remove_candidates_or_raw_outputs(self):
        for decision in ('reject', 'uncertain'):
            with self.subTest(decision=decision):
                result = fictional_draft()
                updated = self.decision(result, decision)
                cid = result['codes'][0]['id']
                self.assertEqual(review.candidate_review_state(updated, cid)['state'], decision)
                self.assertTrue(review.grouping_review_issue(updated, cid))
                self.assertEqual(updated['codes'], result['codes'])
                self.assertEqual(updated['calls'], result['calls'])
                validate_result_sources(updated)

    def test_wording_revision_stales_acceptance_until_explicit_new_decision(self):
        result = self.decision(fictional_draft())
        cid = result['codes'][0]['id']
        revised = review.revise_interpretation(result, cid,
            {'claim': 'A reported tutor wish depends on transport; the outcome is unknown.'},
            reviewer_kind='assistant_development_review', reason='Retain reported actor and unknown outcome.')
        self.assertEqual(review.candidate_review_state(revised, cid)['state'], 'needs_review')
        self.assertTrue(review.grouping_review_issue(revised, cid))
        edited = self.decision(revised, 'edit')
        self.assertEqual(review.candidate_review_state(edited, cid)['state'], 'edit')
        self.assertFalse(review.candidate_review_state(edited, cid)['researcher_approved'])
        self.assertIsNone(review.grouping_review_issue(edited, cid))
        self.assertEqual(edited['calls'], result['calls'])
        validate_result_sources(edited)

    def test_source_context_switch_preserves_exact_text_offsets_and_speaker_separation(self):
        result = fictional_draft()
        original = deepcopy(result)
        view = review.source_review_view(result, result['codes'][0]['id'])
        target = next(e for e in view['entries'] if e['kind'] == 'target')
        contexts = [e for e in view['entries'] if e['kind'] == 'context']
        self.assertTrue(contexts)
        source = result['source_documents'][0]['text']
        for entry in (target, *contexts):
            span = entry['span']
            self.assertEqual(source[span['source_start']:span['source_end']], span['text'])
            self.assertIn(span['text'], review.source_review_text(view, entry['id']))
            self.assertFalse(entry['source_speaker']['person_identity_verified'])
            self.assertFalse(entry['source_speaker']['reported_actor_or_assertion_validated'])
        self.assertIn('Participant 1', target['source_speaker']['source_local_label'])
        self.assertIn('Interviewer', contexts[0]['source_speaker']['source_local_label'])
        self.assertIn('My tutor wants', target['span']['text'])
        self.assertIn('if transport is available', target['span']['text'])
        self.assertIn('do not know', target['span']['text'])
        self.assertNotEqual(target['source_speaker']['source_local_label'], 'Tutor')
        self.assertEqual(view['role_evidence']['revisions'], [])
        self.assertFalse(view['role_evidence']['semantic_validation'])
        self.assertEqual(result, original)

    def test_unknown_source_label_is_not_filled_from_candidate_or_reported_actor(self):
        result = fictional_draft()
        view = review.source_review_view(result, result['codes'][-1]['id'])
        target = next(e for e in view['entries'] if e['kind'] == 'target')
        self.assertEqual(target['source_speaker']['status'], 'unknown')
        self.assertIsNone(target['source_speaker']['source_local_label'])
        self.assertEqual(view['role_evidence']['revisions'], [])
        self.assertIn('No decision has been made', review.source_review_text(view))

    def test_split_retains_parent_and_raw_output_and_children_need_review(self):
        result = fictional_draft()
        original = deepcopy(result)
        updated = self.split(result)
        self.assertEqual(result, original)
        parent = result['codes'][0]
        self.assertEqual(next(c for c in updated['codes'] if c['id'] == parent['id']), parent)
        children = [c for c in updated['codes'] if c.get('split_parent_id') == parent['id']]
        self.assertEqual(len(children), 2)
        self.assertEqual(len({c['id'] for c in updated['codes']}), len(updated['codes']))
        for child in children:
            self.assertEqual(child['unit_id'], parent['unit_id'])
            self.assertEqual(child['origin'], 'assistant_development_review')
            self.assertIn('split_origin_prose', child)
            self.assertIn('split_origin_evidence_roles', child)
            self.assertEqual(review.candidate_review_state(updated, child['id'])['state'], 'unreviewed')
            self.assertTrue(review.grouping_review_issue(updated, child['id']))
        self.assertEqual(review.candidate_review_state(updated, parent['id'])['state'], 'split_parent')
        self.assertTrue(review.grouping_review_issue(updated, parent['id']))
        for field in ('calls', 'source_documents', 'registry', 'unit_archive'):
            self.assertEqual(updated[field], result[field])
        validate_result_sources(updated)

    def test_split_rejects_invalid_counts_foreign_support_and_overlapping_roles(self):
        result = fictional_draft()
        parts = self.parts(result)
        foreign = next(sid for sid, span in result['registry'].items()
                       if span['unit_id'] != result['codes'][0]['unit_id'])
        cross_source = deepcopy(parts)
        cross_source[1]['support_span_ids'] = [foreign]
        overlap = deepcopy(parts)
        overlap[0]['qualification_span_ids'] = overlap[0]['support_span_ids'][:]
        for invalid in ([], parts[:1], parts * 2, cross_source, overlap):
            with self.subTest(parts=invalid), self.assertRaises(ValueError):
                self.split(result, invalid)

    def test_json_roundtrip_and_html_keep_decisions_ai_authorship_and_exact_sources(self):
        result = self.decision(fictional_draft(), 'uncertain')
        reopened = json.loads(json.dumps(result, ensure_ascii=False))
        validate_result_sources(reopened)
        cid = result['codes'][0]['id']
        self.assertEqual(review.candidate_review_state(result, cid), review.candidate_review_state(reopened, cid))
        html = export_codebook_html(reopened)
        self.assertIn('uncertain', html)
        self.assertIn('assistant_development_review', html)
        self.assertIn('Offline fictional review', html)
        self.assertIn('if transport is available', html)
        self.assertEqual(reopened['calls'], result['calls'])

    def test_grouping_refuses_reject_uncertain_stale_and_split_parent_in_direct_api(self):
        base = fictional_draft()
        stale = review.revise_interpretation(self.decision(base), base['codes'][0]['id'],
            {'label': 'Transport conditional reported wish'},
            reviewer_kind='assistant_development_review', reason='Retain the stated condition.')
        for result in (self.decision(base, 'reject'), self.decision(base, 'uncertain'), stale, self.split(base)):
            ids = [base['codes'][0]['id'], base['codes'][-1]['id']]
            with self.subTest(state=review.candidate_review_state(result, ids[0])['state']), self.assertRaises(ValueError):
                add_reviewed_category(result, ids, label='Conditional possibilities',
                    shared_meaning='Practical support constrains a possible next step.',
                    member_notes={cid: 'Retains its distinct stated condition and unknown outcome.' for cid in ids},
                    limits='No completed placement or common actor has been established.',
                    reviewer_kind='assistant_development_review', reason='Offline fictional grouping check.')

    def test_ai_reviewed_group_preserves_ai_authorship_without_human_approval(self):
        result = fictional_draft()
        ids = [result['codes'][0]['id'], result['codes'][-1]['id']]
        for cid in ids:
            result = self.decision(result, code_id=cid)
        grouped = add_reviewed_category(result, ids, label='Conditional possibilities',
            shared_meaning='Practical support constrains possible next steps.',
            member_notes={cid: 'Possible next step retains its own condition.' for cid in ids},
            limits='No completed placement or shared actor is established.',
            reviewer_kind='assistant_development_review', reason='Demonstrate review with fictional sources.')
        self.assertEqual(grouped['reviewed_categories'][0]['origin'], 'assistant_development_review')
        self.assertEqual(grouped['semantic_quality'], 'not_human_validated')
        self.assertFalse(any(review.candidate_review_state(grouped, cid)['researcher_approved'] for cid in ids))
        validate_result_sources(grouped)

    def test_decision_requires_known_candidate_explicit_reviewer_and_reason(self):
        result = fictional_draft()
        kwargs = dict(reviewer_kind='assistant_development_review', reviewer_name='Offline review', reason='Source checked.')
        for patch in ({'reviewer_kind': 'automatic'}, {'reviewer_name': ''}, {'reason': ''}):
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                review.record_candidate_decision(result, result['codes'][0]['id'], 'accept', **(kwargs | patch))
        for cid, decision in (('missing', 'accept'), (result['codes'][0]['id'], 'human_validated')):
            with self.subTest(cid=cid, decision=decision), self.assertRaises(ValueError):
                review.record_candidate_decision(result, cid, decision, **kwargs)

    def test_workflow_tampering_rejected_even_after_result_digest_recomputed(self):
        result = self.decision(fictional_draft())
        tampered = deepcopy(result)
        workflow = tampered['candidate_review_workflow']
        self.assertTrue(workflow['decisions'])
        workflow['decisions'][0]['reviewer_kind'] = 'researcher'
        seal(tampered)
        with self.assertRaises(ValueError):
            validate_result_sources(tampered)

    def test_split_checks_persist_as_parent_rationale_without_child_approval(self):
        result = fictional_draft()
        kwargs = dict(reviewer_kind='assistant_development_review', reviewer_name='Offline split reviewer',
                      reason='Separate the reported wish from the unknown outcome.')
        checks = {'actor_perspective': 'The tutor wish is reported by the participant.',
                  'remaining_uncertainty': 'The placement outcome remains unknown.'}
        revised = review.split_candidate(result, result['codes'][0]['id'], self.parts(result), checks=checks, **kwargs)
        event = revised['candidate_review_workflow']['splits'][0]
        self.assertEqual(event['checks'], checks)
        self.assertEqual(event['checks_origin'], 'parent_split_rationale_not_child_judgments_or_imported_fact')
        for cid in event['child_ids']:
            self.assertEqual(review.candidate_review_state(revised, cid)['state'], 'unreviewed')
            self.assertEqual(review.candidate_review_state(revised, cid)['checks'], {})
        reopened = json.loads(json.dumps(revised))
        validate_result_sources(reopened)
        html = export_codebook_html(reopened)
        self.assertIn('The placement outcome remains unknown.', html)
        self.assertIn('parent_split_rationale_not_child_judgments_or_imported_fact', html)
        member_ids = [event['child_ids'][0], result['codes'][-1]['id']]
        grouped = reopened
        for cid in member_ids:
            grouped = review.record_candidate_decision(grouped, cid, 'accept',
                reviewer_kind='assistant_development_review', reviewer_name='Explicit member reviewer',
                reason='Retain separate conditional meanings.', checks={'context_use': 'Member review note preserved.'})
        grouped = add_reviewed_category(grouped, member_ids, label='Conditional possibilities',
            shared_meaning='Practical conditions constrain possible next steps.',
            member_notes={cid: 'Retain each distinct reported condition.' for cid in member_ids},
            limits='No common actor or completed placement is established.',
            reviewer_kind='assistant_development_review', reason='Offline category snapshot check.')
        from themeforge.reviewed_grouping import render_reviewed_categories
        member_html = render_reviewed_categories(grouped)
        self.assertIn('Explicit member reviewer', member_html)
        self.assertIn('Member review note preserved.', member_html)
        self.assertIn('accept', member_html)
        legacy = deepcopy(reopened)
        legacy_event = legacy['candidate_review_workflow']['splits'][0]
        legacy_event.pop('checks'); legacy_event.pop('checks_origin'); legacy_event.pop('event_sha256')
        legacy_event['event_sha256'] = _digest(legacy_event)
        validate_result_sources(seal(legacy))
        malformed = deepcopy(reopened)
        changed = malformed['candidate_review_workflow']['splits'][0]
        changed['checks'] = {'automatic_truth': 'No'}
        changed.pop('event_sha256'); changed['event_sha256'] = _digest(changed)
        with self.assertRaises(ValueError):
            validate_result_sources(seal(malformed))
        for bad in ({'unsupported_key': 'text'}, {'context_use': ''}, {'context_use': 'x' * 2001}):
            with self.subTest(checks=bad), self.assertRaises(ValueError):
                review.split_candidate(result, result['codes'][0]['id'], self.parts(result), checks=bad, **kwargs)

    def test_context_text_separates_saved_referent_snippets_from_full_review_turns(self):
        result = fictional_draft()
        code = result['codes'][0]
        view = review.source_review_view(result, code['id'])
        entry = next(entry for entry in view['entries'] if entry['kind'] == 'context' and entry['selected_referent_unit'])
        text = review.source_review_text(view, entry['id'])
        self.assertIn('This full turn is a saved selected referent unit: True.', text)
        self.assertIn('Raw requests remain authoritative', text)
        self.assertIn('Exact saved selected_referent_context snippets', text)
        for snippet in view['selected_referent_context']:
            if entry['span']['source_start'] <= snippet['source_start'] < snippet['source_end'] <= entry['span']['source_end']:
                self.assertIn(snippet['text'], text)
                self.assertIn(f"chars {snippet['source_start']}:{snippet['source_end']}", text)
                self.assertIn(snippet['source_hash'], text)
        unselected = deepcopy(result)
        unselected['codes'][0]['referent_context'] = []
        unselected_view = review.source_review_view(seal(unselected), code['id'])
        unselected_text = review.source_review_text(unselected_view, entry['id'])
        self.assertIn(entry['span']['text'], unselected_text)
        self.assertIn('This full turn is a saved selected referent unit: False.', unselected_text)
        self.assertIn('None. This full turn is shown only as adjacent review context.', unselected_text)


if __name__ == '__main__':
    unittest.main()

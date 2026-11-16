"""Explicit review categories with invented texts and fake-client native inputs."""
from copy import deepcopy
import unittest
from unittest.mock import patch

from test_category_evidence import Client, grouped, native, seal
from themeforge.codebook_review import revise_interpretation, revise_evidence_roles
from themeforge.local_codebook import run_synthesis, validate_result_sources, export_codebook_html
from themeforge.model_pilot import _digest
from themeforge.reviewed_grouping import (
    COLLECTION_FIELDS, add_reviewed_category, validate_reviewed_categories, render_reviewed_categories)


def arguments(result, **changes):
    ids = [code['id'] for code in result['codes'][:2]]
    values = dict(code_ids=ids, label='Copies for recovery',
        shared_meaning='These provisional codes connect retained copies with possible recovery.',
        member_notes={cid: 'The selected code describes an additional copy; its particular context remains distinct.' for cid in ids},
        limits='These two invented accounts do not establish an exhaustive or independently validated relationship.',
        reviewer_kind='assistant_development_review', reason='Explicitly author a tentative relationship after inspecting source passages.')
    values.update(changes)
    return values


class ReviewedGroupingTests(unittest.TestCase):
    def test_after_no_group_is_copy_only_and_preserves_all_original_fields_and_history(self):
        raw = native()
        original = run_synthesis(raw, Client(no_group=True), [c['id'] for c in raw['codes']],
                                 reviewer_kind='assistant_development_review')
        before = deepcopy(original)
        with patch('themeforge.local_inference.LlamaServerClient', side_effect=AssertionError('No client allowed')):
            result = add_reviewed_category(original, **arguments(original))
        self.assertEqual(original, before)
        for key, value in original.items():
            if key != 'result_sha256':
                self.assertEqual(result[key], value, key)
        self.assertEqual(result['synthesis_status'], 'no_group')
        self.assertEqual(result['review_status'], 'unreviewed_machine_draft')
        self.assertEqual(result['reviewed_category_base_sha256'], original['result_sha256'])
        self.assertEqual(result['reviewed_category_history'][0]['before_result_sha256'], original['result_sha256'])
        self.assertEqual(result['reviewed_categories'][0]['origin'], 'assistant_development_review')
        self.assertEqual(result['reviewed_categories'][0]['kind'], 'category')
        self.assertEqual(result['reviewed_categories'][0]['review_status'], 'provisional_unvalidated')
        validate_result_sources(result)
        member = result['reviewed_categories'][0]['members'][0]
        member['evidence'][0]['text'] = 'Mutated returned copy only.'
        self.assertEqual(original, before)

    def test_model_group_and_raw_draft_both_allow_separate_review_categories(self):
        for original in (native(), grouped()):
            with self.subTest(model_categories=len(original['categories'])):
                result = add_reviewed_category(original, **arguments(original, reviewer_kind='researcher'))
                self.assertEqual(result['categories'], original['categories'])
                self.assertEqual(result['calls'], original['calls'])
                validate_result_sources(result)

    def test_multiple_appends_reconstruct_exact_previous_digest_without_rewriting_first(self):
        raw = native()
        first = add_reviewed_category(raw, **arguments(raw))
        first_copy = deepcopy(first)
        second = add_reviewed_category(first, **arguments(first, label='A different provisional relationship', reviewer_kind='researcher'))
        self.assertEqual(first, first_copy)
        self.assertEqual(second['reviewed_categories'][0], first['reviewed_categories'][0])
        self.assertEqual(second['reviewed_category_history'][0], first['reviewed_category_history'][0])
        self.assertEqual(second['reviewed_category_history'][1]['before_result_sha256'], first['result_sha256'])
        self.assertEqual(second['reviewed_category_base_sha256'], raw['result_sha256'])
        validate_reviewed_categories(second)
        validate_result_sources(second)

    def test_current_wording_and_role_origins_and_exact_rows_are_snapshotted(self):
        raw = native()
        cid = raw['codes'][0]['id']
        revised = revise_interpretation(raw, cid, {'label': 'Explicitly revised provisional label'},
            reviewer_kind='assistant_development_review', reason='Make this author change explicit.')
        free = next(sid for sid, span in revised['registry'].items()
                    if span['unit_id'] == raw['codes'][0]['unit_id'] and sid not in raw['codes'][0]['support_span_ids'])
        revised = revise_evidence_roles(revised, cid, {'qualifications': [free]},
            reviewer_kind='researcher', reason='Keep the second source sentence as a qualification of the member code.')
        result = add_reviewed_category(revised, **arguments(revised))
        member = result['reviewed_categories'][0]['members'][0]
        code = revised['codes'][0]
        self.assertEqual(member['wording_origins']['label'], 'assistant_development_review')
        self.assertEqual(member['evidence_role_origins']['qualifications'], 'researcher')
        self.assertEqual(member['code_sha256'], _digest(code))
        for field in ('label', 'claim', 'definition', 'support_span_ids', 'evidence', 'qualifications', 'counterexamples'):
            self.assertEqual(member[field], code[field], field)
        self.assertEqual(result['review_history'], revised['review_history'])

    def test_unknown_duplicate_insufficient_and_non_list_member_selections_rejected(self):
        raw = native()
        ids = [code['id'] for code in raw['codes']]
        for selected in ([ids[0]], [ids[0]] * 2, [ids[0], 'LC-unknown'], ids * 4, tuple(ids), ids[0]):
            with self.subTest(selected=selected), self.assertRaises(ValueError):
                add_reviewed_category(raw, **arguments(raw, code_ids=selected))

    def test_undefined_or_same_source_member_codes_rejected(self):
        raw = native()
        raw['codes'][1]['status'] = 'scope_insufficient'
        seal(raw)
        with self.assertRaises(ValueError):
            add_reviewed_category(raw, **arguments(raw))
        raw = native()
        raw['codes'][1]['definition'] = ' '
        seal(raw)
        with self.assertRaises(ValueError):
            add_reviewed_category(raw, **arguments(raw))
        raw = native(crosscoded=True)
        first_unit = raw['codes'][0]['unit_id']
        ids = [c['id'] for c in raw['codes'] if c['unit_id'] == first_unit][:2]
        with self.assertRaisesRegex(ValueError, 'distinct source contents'):
            add_reviewed_category(raw, **arguments(raw, code_ids=ids,
                member_notes={cid: 'This is the same source content.' for cid in ids}))

    def test_required_bounded_rationale_limits_notes_and_author(self):
        raw = native()
        ids = [c['id'] for c in raw['codes']]
        for changes in (dict(reason=' '), dict(reason='x' * 2001), dict(limits=''),
                        dict(limits='x' * 2001), dict(shared_meaning='None'), dict(shared_meaning='x' * 601),
                        dict(label='x' * 121), dict(member_notes={}),
                        dict(member_notes={**arguments(raw)['member_notes'], 'LC-extra': 'Not selected.'}),
                        dict(member_notes={ids[0]: ' ', ids[1]: 'Explicit second note.'}),
                        dict(member_notes={cid: 'x' * 1201 for cid in ids}),
                        dict(reviewer_kind='local_model'), dict(reviewer_kind=None)):
            with self.subTest(changes=list(changes)), self.assertRaises(ValueError):
                add_reviewed_category(raw, **arguments(raw, **changes))

    def test_identical_duplicate_rejected_even_with_reordered_members_or_changed_author(self):
        first = add_reviewed_category(native(), **arguments(native()))
        args = arguments(first, reviewer_kind='researcher', reason='A different reason does not create a different category.')
        args['code_ids'].reverse()
        with self.assertRaisesRegex(ValueError, 'identical'):
            add_reviewed_category(first, **args)

    def test_rehashed_member_quote_hash_offsets_provenance_or_snapshot_tamper_rejected(self):
        raw = native()
        first = add_reviewed_category(raw, **arguments(raw))
        changes = (
            lambda c, e: c['members'][0]['evidence'][0].update(text='Invented quote.'),
            lambda c, e: c['members'][0]['evidence'][0].update(source_hash='0' * 64),
            lambda c, e: c['members'][0]['evidence'][0].update(source_start=-1),
            lambda c, e: c['members'][0]['wording_origins'].update(label='researcher'),
            lambda c, e: c['members'][0].update(code_sha256='0' * 64),
            lambda c, e: c['members'][0].update(claim='A stale rewritten member claim.'),
            lambda c, e: c.update(origin='researcher'),
            lambda c, e: c.update(kind='interpretive_theme'),
            lambda c, e: c.update(review_status='human_validated'),
            lambda c, e: c.update(category_sha256='0' * 64),
            lambda c, e: e.update(reviewer_kind='researcher'),
            lambda c, e: e.update(reason='A fabricated retrospective rationale.'),
            lambda c, e: e.update(before_result_sha256='0' * 64),
        )
        for change in changes:
            broken = deepcopy(first)
            change(broken['reviewed_categories'][0], broken['reviewed_category_history'][0])
            seal(broken)
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_reviewed_categories(broken)
            with self.assertRaises(ValueError):
                validate_result_sources(broken)

    def test_stale_native_base_partial_metadata_and_history_removal_rejected(self):
        raw = native()
        first = add_reviewed_category(raw, **arguments(raw))
        for field in COLLECTION_FIELDS:
            broken = deepcopy(first); broken.pop(field); seal(broken)
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_reviewed_categories(broken)
        broken = deepcopy(first)
        broken['codes'][0]['claim'] = 'This code changed after grouping.'
        seal(broken)
        with self.assertRaisesRegex(ValueError, 'native base changed'):
            validate_reviewed_categories(broken)
        broken = deepcopy(first); broken['reviewed_category_history'].clear(); seal(broken)
        with self.assertRaises(ValueError):
            validate_reviewed_categories(broken)
        self.assertIsNone(validate_reviewed_categories(raw))

    def test_reordered_or_rewritten_append_chain_rejected(self):
        raw = native()
        first = add_reviewed_category(raw, **arguments(raw))
        second = add_reviewed_category(first, **arguments(first, label='Different label'))
        broken = deepcopy(second)
        broken['reviewed_categories'].reverse(); broken['reviewed_category_history'].reverse(); seal(broken)
        with self.assertRaises(ValueError):
            validate_reviewed_categories(broken)
        broken = deepcopy(second)
        broken['reviewed_category_history'][1]['before_result_sha256'] = raw['result_sha256']; seal(broken)
        with self.assertRaises(ValueError):
            validate_reviewed_categories(broken)

    def test_category_and_metadata_resource_limits(self):
        raw = native()
        first = add_reviewed_category(raw, **arguments(raw))
        with patch('themeforge.reviewed_grouping.MAX_CATEGORIES', 1), self.assertRaisesRegex(ValueError, '24'):
            add_reviewed_category(first, **arguments(first, label='Another category'))
        with patch('themeforge.reviewed_grouping.MAX_COLLECTION_BYTES', 10), self.assertRaisesRegex(ValueError, '4 MiB'):
            add_reviewed_category(raw, **arguments(raw))
        with patch('themeforge.reviewed_grouping.MAX_NATIVE_BYTES', 10), self.assertRaisesRegex(ValueError, '16 MiB'):
            add_reviewed_category(raw, **arguments(raw))

    def test_HTML_distinguishes_authorship_member_roles_and_model_outcome_and_escapes_text(self):
        raw = native()
        original = run_synthesis(raw, Client(no_group=True), [c['id'] for c in raw['codes']],
                                 reviewer_kind='assistant_development_review')
        result = add_reviewed_category(original, **arguments(original, label='<script>Review label</script>'))
        fragment = render_reviewed_categories(result)
        self.assertIn('Categories added during review', fragment)
        self.assertIn('assistant_development_review', fragment)
        self.assertIn('not automatic category counterclaims', fragment)
        self.assertIn('&lt;script&gt;', fragment)
        self.assertNotIn('<script>', fragment)
        self.assertIn(result['reviewed_category_history'][0]['before_result_sha256'], fragment)
        for member in result['reviewed_categories'][0]['members']:
            for span in member['evidence']:
                self.assertIn(span['source_hash'], fragment)
        html = export_codebook_html(result)
        self.assertIn('Grouping outcome:</b> no_group', html)
        self.assertIn('Categories added during review', html)
        self.assertEqual(render_reviewed_categories(original), '')


if __name__ == '__main__':
    unittest.main()

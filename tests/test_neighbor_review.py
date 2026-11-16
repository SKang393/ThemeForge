"""Synthetic explicit review lineage; no authentic cases or model clients."""
from copy import deepcopy
import json
import unittest

from themeforge.neighbor_review import (
    MAX_RATIONALE_CHARS, create_review, validate_review, append_review,
    export_review_json, export_review_html,
)
from themeforge.semantic_neighbors import build_neighbor_graph, digest
from tests.test_semantic_neighbors import fixture


def graph():
    return build_neighbor_graph(fixture(), [[1, 0], [.8, .6], [.28, .96], [-1, 0]],
                                backend='sentence_embeddings', provenance={'encoder': 'synthetic_test_only'})


def reseal(review):
    review['review_sha256'] = digest({k: v for k, v in review.items() if k != 'review_sha256'})


class NeighborReviewTests(unittest.TestCase):
    def test_creation_has_no_implicit_reviewer_and_graph_is_a_deep_copy(self):
        original = graph()
        review = create_review(original)
        self.assertEqual(review['history'], [])
        self.assertNotIn('reviewer_kind', review)
        self.assertEqual(review['graph'], original)
        self.assertEqual(review['graph_sha256'], original['graph_sha256'])
        self.assertIn('not_semantic_validation', review['status'])
        validate_review(review)
        review['graph']['packet']['nodes'][0]['text'] = 'Changed copy.'
        self.assertNotEqual(review['graph'], original)
        with self.assertRaises(ValueError):
            validate_review(review)

    def test_opposing_judgments_append_preserving_graph_and_ai_human_origins(self):
        original_graph = graph(); initial = create_review(original_graph)
        before = deepcopy(initial)
        ai = append_review(initial, ['n1', 'n0'], 'keep_related_comparison',
                           'The pair warrants comparison, without asserting equivalent views.', reviewer_kind='ai')
        self.assertEqual(initial, before)
        self.assertEqual(ai['history'][0]['member_ids'], ['n0', 'n1'])
        self.assertEqual(ai['history'][0]['before_review_sha256'], initial['review_sha256'])
        human = append_review(ai, ['n0', 'n1'], 'reject_pair',
                              'I judge the proposed comparison unhelpful for this review question.', reviewer_kind='researcher')
        self.assertEqual(human['history'][0], ai['history'][0])
        self.assertEqual(human['history'][1]['before_review_sha256'], ai['review_sha256'])
        self.assertEqual([e['reviewer_kind'] for e in human['history']], ['ai', 'researcher'])
        self.assertEqual([e['sequence'] for e in human['history']], [1, 2])
        self.assertEqual(human['graph'], original_graph)
        self.assertEqual(human['status'], initial['status'])
        self.assertNotIn('approved', human)
        validate_review(human)
        # Nested previous events are not shared across returned branches.
        human['history'][0]['rationale'] = 'Changed returned branch only.'
        self.assertNotEqual(human['history'][0], ai['history'][0])

    def test_only_actual_selected_edges_are_reviewable(self):
        review = create_review(graph())
        for ids in (['n0', 'n2'], ['n0', 'n3'], ['n0', 'missing'], ['n0', 'n0'],
                    ['n0'], ['n0', 'n1', 'n2'], ('n0', 'n1'), [True, 'n1']):
            with self.subTest(ids=ids), self.assertRaises(ValueError):
                append_review(review, ids, 'uncertain', 'Cannot establish a useful relationship.', reviewer_kind='ai')
        self.assertEqual(review['history'], [])

    def test_bounded_explicit_decisions_authors_and_reasons(self):
        review = create_review(graph())
        for author in (None, '', 'human', 'assistant_development_review', True):
            with self.subTest(author=author), self.assertRaises(ValueError):
                append_review(review, ['n0', 'n1'], 'uncertain', 'Unresolved.', reviewer_kind=author)
        with self.assertRaises(TypeError):
            append_review(review, ['n0', 'n1'], 'uncertain', 'Unresolved.')
        for decision in ('approved', 'theme', None):
            with self.assertRaises(ValueError):
                append_review(review, ['n0', 'n1'], decision, 'Unresolved.', reviewer_kind='researcher')
        for reason in ('', '  \n', None, 1, 'x' * (MAX_RATIONALE_CHARS + 1)):
            with self.assertRaises(ValueError):
                append_review(review, ['n0', 'n1'], 'keep_contrast', reason, reviewer_kind='ai')
        accepted = append_review(review, ['n0', 'n1'], 'keep_contrast',
                                 'x' * MAX_RATIONALE_CHARS, reviewer_kind='ai')
        validate_review(accepted)

    def test_rehashed_changes_to_authors_history_source_and_graph_are_rejected(self):
        first = append_review(create_review(graph()), ['n0', 'n1'], 'keep_contrast', 'Different positions.', reviewer_kind='ai')
        second = append_review(first, ['n1', 'n2'], 'uncertain', 'Useful relation remains unclear.', reviewer_kind='researcher')
        for change in (
            lambda r: r['history'][0].update(reviewer_kind='researcher'),
            lambda r: r['history'][0].update(rationale='Changed prior judgment.'),
            lambda r: r['history'][0].update(sequence=True),
            lambda r: r['history'][0].update(timestamp='yesterday'),
            lambda r: r['history'][0].update(member_ids=['n0', 'n2']),
            lambda r: r['history'].reverse(),
            lambda r: r['history'].pop(0),
            lambda r: r.update(graph_sha256='a' * 64),
            lambda r: r['graph']['packet']['nodes'][0].update(text='Invented raw text.'),
            lambda r: r['graph']['edges'][0].update(cosine=.99),
        ):
            altered = deepcopy(second); change(altered); reseal(altered)
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_review(altered)

    def test_json_html_roundtrip_keeps_exact_quotes_provenance_and_judgment_limits(self):
        review = append_review(create_review(graph()), ['n0', 'n1'], 'uncertain',
                               '<script>invented markup</script> is merely a reviewer note.', reviewer_kind='ai')
        raw = export_review_json(review)
        loaded = json.loads(raw)
        validate_review(loaded)
        self.assertEqual(loaded, review)
        html = export_review_html(loaded)
        self.assertNotIn('<script>invented markup</script>', html)
        self.assertIn('&lt;script&gt;invented markup&lt;/script&gt;', html)
        self.assertIn('AI-authored assertion', html)
        self.assertIn('not established truth', html)
        self.assertIn('not confidence', html)
        for node in review['graph']['packet']['nodes']:
            self.assertIn(node['source_hash'], html)
            self.assertIn(node['text'], html)
        self.assertEqual(loaded['graph'], review['graph'])


if __name__ == '__main__':
    unittest.main()

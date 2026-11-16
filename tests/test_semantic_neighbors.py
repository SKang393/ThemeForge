"""Synthetic source proofs and deterministic retrieval, without model calls."""
from copy import deepcopy
from hashlib import sha256
import json
import math
import unittest
from unittest.mock import patch

from themeforge.semantic_neighbors import (
    BACKENDS, K, MIN_SIMILARITY, digest, prepare_packet, validate_packet,
    lexical_vectors, build_neighbor_graph, validate_neighbor_graph,
    export_graph_json, export_graph_html,
)


def fixture(texts=None):
    texts = texts or ['I might not archive the measurements.',
                      'I would archive the measurements if permission arrives.',
                      'I never archive the measurements.', 'We discuss a different issue.']
    sources, nodes = [], []
    for i, text in enumerate(texts):
        full = 'Moderator: Context remains separate.\r\nP1: ' + text + '\r\n'
        name, source_hash = f'synthetic-{i}', sha256(full.encode('utf-8')).hexdigest()
        start = full.index(text)
        sources.append(dict(source_name=name, source_hash=source_hash, text=full))
        nodes.append(dict(id=f'n{i}', source_name=name, source_hash=source_hash, speaker='P1',
                          source_start=start, source_end=start + len(text), text=text,
                          context_start=0, context_end=len(full), context_text=full,
                          referent_context=[dict(source_name=name, source_hash=source_hash,
                              source_start=0, source_end=35, text=full[:35], role='preceding_context')],
                          provenance={'native_parent_sha256': 'a' * 64}))
    return prepare_packet(nodes, sources=sources, provenance={'selection': 'synthetic_test_only'})


def reseal(packet):
    packet['packet_sha256'] = digest({k: v for k, v in packet.items() if k != 'packet_sha256'})


class SemanticNeighborTests(unittest.TestCase):
    def test_reciprocal_cross_source_top_two_not_transitive_groups(self):
        packet = fixture()
        # n0-n1-n2 chain; n0/n2 cosine .28 is below threshold despite connectivity.
        rows = [[1, 0], [.8, .6], [.28, .96], [-1, 0]]
        graph = build_neighbor_graph(packet, rows, backend='sentence_embeddings',
                                     provenance={'model_sha256': 'b' * 64})
        self.assertEqual((K, MIN_SIMILARITY), (2, .30))
        self.assertEqual([e['member_ids'] for e in graph['edges']], [['n0', 'n1'], ['n1', 'n2']])
        self.assertEqual(graph['isolates'], ['n3'])
        self.assertEqual(len(graph['pair_scores']), 6)
        self.assertFalse({'categories', 'groups', 'themes', 'codes'} & graph.keys())
        self.assertIn('not equivalence', ' '.join(graph['warnings']))
        validate_neighbor_graph(graph)

    def test_asymmetric_neighbors_are_not_edges_and_ties_use_stable_ids(self):
        packet = fixture(['First source turn.', 'Second source turn.', 'Third source turn.',
                          'Fourth source turn.', 'Fifth source turn.'])
        vectors = [[1, 0]] * 5
        graph = build_neighbor_graph(packet, vectors, backend='sentence_embeddings')
        self.assertEqual([e['member_ids'] for e in graph['edges']],
                         [['n0', 'n1'], ['n0', 'n2'], ['n1', 'n2']])
        self.assertEqual(graph['isolates'], ['n3', 'n4'])
        reordered = prepare_packet(list(reversed(packet['nodes'])), sources=packet['sources'])
        again = build_neighbor_graph(reordered, list(reversed(vectors)), backend='sentence_embeddings')
        self.assertEqual(again['pair_scores'], graph['pair_scores'])
        self.assertEqual(again['edges'], graph['edges'])

    def test_same_content_under_different_names_cannot_be_cross_source_neighbors(self):
        packet = fixture()
        source = deepcopy(packet['sources'][0]); source['source_name'] = 'renamed-source'
        node = deepcopy(packet['nodes'][0]); node.update(id='renamed-node', source_name='renamed-source')
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            prepare_packet([packet['nodes'][0], node], sources=[packet['sources'][0], source])
        # Two different turns from identical source contents remain same-source.
        full = 'First exact turn. Second exact turn.'
        source_hash = sha256(full.encode()).hexdigest()
        sources = [dict(source_name=name, source_hash=source_hash, text=full) for name in ('a', 'b')]
        nodes = [dict(id='a', source_name='a', source_hash=source_hash, speaker='P1',
                      text=full[:17], source_start=0, source_end=17),
                 dict(id='b', source_name='b', source_hash=source_hash, speaker='P1',
                      text=full[18:], source_start=18, source_end=len(full))]
        graph = build_neighbor_graph(prepare_packet(nodes, sources=sources), [[1], [1]], backend=BACKENDS[0])
        self.assertEqual(graph['edges'], [])
        self.assertFalse(graph['pair_scores'][0]['source_distinct'])
        self.assertIsNone(graph['pair_scores'][0]['left_rank'])

    def test_source_digest_slice_and_context_tampering_rejected_even_if_resealed(self):
        original = fixture()
        for change in (
            lambda p: p['nodes'][0].update(text='Invented exact quotation.'),
            lambda p: p['nodes'][0].update(source_start=True),
            lambda p: p['nodes'][0].update(source_hash='invalid'),
            lambda p: p['nodes'][0].update(speaker=1),
            lambda p: p['nodes'][0].update(context_text='Invented context.'),
            lambda p: p['nodes'][0]['referent_context'][0].update(text='Invented referent.'),
            lambda p: p['sources'][0].update(text='Changed original source'),
        ):
            packet = deepcopy(original); change(packet); reseal(packet)
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_packet(packet)
        packet = deepcopy(original); packet['provenance']['selection'] = 'changed'
        with self.assertRaisesRegex(ValueError, 'digest'):
            validate_packet(packet)
        packet = deepcopy(original); packet['nodes'] = packet['nodes'] * 6; reseal(packet)
        with self.assertRaisesRegex(ValueError, '1..20'):
            validate_packet(packet)

    def test_vector_shape_finiteness_and_backend_fail_closed(self):
        packet = fixture()
        for rows in ([], [[1]] * 3, [[1], [1, 2], [1], [1]],
                     [[True]] * 4, [[float('nan')]] * 4, [[float('inf')]] * 4,
                     [['1']] * 4, [[]] * 4):
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                build_neighbor_graph(packet, rows, backend='sentence_embeddings')
        with self.assertRaises(ValueError):
            build_neighbor_graph(packet, [[1]] * 4, backend='embedding_or_fallback')
        scaled = build_neighbor_graph(packet, [[5, 0], [0, 7], [3, 0], [0, -1]], backend=BACKENDS[0])
        pair = next(p for p in scaled['pair_scores'] if p['member_ids'] == ['n0', 'n2'])
        self.assertEqual(pair['cosine'], 1.0)

    def test_zero_vectors_remain_isolates_with_explicit_non_success(self):
        packet = fixture()
        graph = build_neighbor_graph(packet, [[0, 0]] * 4, backend=BACKENDS[0])
        self.assertEqual(graph['status'], 'no_usable_vectors')
        self.assertEqual(graph['edges'], [])
        self.assertEqual(graph['isolates'], graph['zero_vector_ids'])
        partial = build_neighbor_graph(packet, [[0, 0], [1, 0], [1, 0], [0, 0]], backend=BACKENDS[0])
        self.assertEqual(partial['status'], 'partial_zero_vectors')
        self.assertEqual(partial['isolates'], ['n0', 'n3'])

    def test_lexical_unigrams_bigrams_preserve_negation_modality_and_contractions(self):
        packet = fixture(["I can't archive if permission is absent.",
                          'I can archive if permission is absent.'])
        original = deepcopy(packet)
        vectors = lexical_vectors(packet)
        # Independent formula: every unique term in one turn has smoothed IDF>1.
        expected_idf = math.log(3 / 2) + 1
        words = ["i", "can't", 'archive', 'if', 'permission', 'is', 'absent']
        self.assertEqual(len(vectors[0]), 16)  # union includes can't/can + distinct adjacent bigrams
        self.assertTrue(any(abs(v - expected_idf / (len(words) * 2 - 1)) < 1e-12 for v in vectors[0]))
        self.assertNotEqual(vectors[0], vectors[1])
        changed = deepcopy(packet)
        changed['nodes'][0]['provenance']['native_parent_sha256'] = 'c' * 64
        changed['nodes'][0]['rationale'] = 'Text that must never affect vectors.'
        reseal(changed)
        self.assertEqual(lexical_vectors(changed), vectors)
        self.assertEqual(packet, original)

    def test_json_html_roundtrip_escape_exact_sources_and_derived_tamper(self):
        packet = fixture(['I do not share <script>alert(1)</script>.', 'I might share if permitted.'])
        original = deepcopy(packet)
        graph = build_neighbor_graph(packet, [[1, 0], [1, 0]], backend='sentence_embeddings',
                                     provenance={'adapter': 'synthetic_only_no_model'})
        loaded = json.loads(export_graph_json(graph))
        validate_neighbor_graph(loaded)
        self.assertEqual(loaded['packet'], original)
        html = export_graph_html(graph)
        self.assertNotIn('<script>alert(1)</script>', html)
        self.assertIn('&lt;script&gt;alert(1)&lt;/script&gt;', html)
        self.assertIn(packet['nodes'][0]['source_hash'], html)
        self.assertIn('context_text', html)
        self.assertIn('not probabilities', html)
        for field, value in (('edges', []), ('isolates', ['n0']), ('backend', 'fake_backend')):
            tampered = deepcopy(graph); tampered[field] = value
            tampered['graph_sha256'] = digest({k: v for k, v in tampered.items() if k != 'graph_sha256'})
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_neighbor_graph(tampered)
        self.assertEqual(packet, original)

    def test_core_imports_no_encoder_and_does_not_call_native_generation(self):
        import builtins
        actual_import = builtins.__import__
        def guarded(name, *args, **kwargs):
            if name.startswith(('sentence_transformers', 'torch', 'themeforge.local_inference')):
                raise AssertionError('Graph core must not load a model client or encoder.')
            return actual_import(name, *args, **kwargs)
        with patch('builtins.__import__', side_effect=guarded):
            packet = fixture()
            graph = build_neighbor_graph(packet, lexical_vectors(packet), backend='lexical_tfidf')
            validate_neighbor_graph(graph)


if __name__ == '__main__':
    unittest.main()

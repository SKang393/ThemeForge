"""Source-exact reciprocal candidate pairs, without coding or theme claims.

Both vector backends consume node.text only. This module loads no model and
performs no downloads. The caller supplies sentence embeddings and their pinned
provenance; lexical_vectors provides the deterministic comparison baseline.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from hashlib import sha256
from html import escape
import json
import math
from numbers import Real
import re

VERSION = 'themeforge.semantic-neighbors.v1'
PACKET_TYPE = 'source_turn_neighbor_packet'
GRAPH_TYPE = 'source_turn_candidate_pairs'
POLICY_GRAPH_VERSION = 'themeforge.semantic-neighbors.v2'
K = 2
MIN_SIMILARITY = 0.30
RETRIEVAL_POLICIES = {
    'mutual_k2_t030': {'k': 2, 'neighbor_rule': 'mutual', 'min_similarity': .30},
    'mutual_k4_t030': {'k': 4, 'neighbor_rule': 'mutual', 'min_similarity': .30},
    'either_k2_t030': {'k': 2, 'neighbor_rule': 'either', 'min_similarity': .30},
    'mutual_k2_t020': {'k': 2, 'neighbor_rule': 'mutual', 'min_similarity': .20},
    'mutual_k2_t040': {'k': 2, 'neighbor_rule': 'mutual', 'min_similarity': .40},
    'mutual_k4_t040': {'k': 4, 'neighbor_rule': 'mutual', 'min_similarity': .40},
}
MAX_NODES = 20
MAX_SOURCE_BYTES = 4 * 1024**2
MAX_PACKET_BYTES = 16 * 1024**2
MAX_GRAPH_BYTES = 32 * 1024**2
MAX_DIMENSIONS = 8192
BACKENDS = ('lexical_tfidf', 'sentence_embeddings')
_HASH = re.compile(r'^[0-9a-f]{64}$')
_TOKEN = re.compile(r"[^\W_]+(?:['’][^\W_]+)*", re.UNICODE)
WARNINGS = [
    'Scores are cosine similarities, not probabilities, confidence or semantic approval.',
    'Edges are candidate pair memberships, not equivalence, categories or themes. Do not merge transitively.',
    'Negation, conditions and opposing views remain distinct source assertions even when neighbors.',
    'The score cutoff is an experimental retrieval rule, not a calibrated semantic boundary.',
    'Speaker identifiers are local to each source; matching labels do not identify the same person.',
]


def _canonical(value):
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    except (TypeError, ValueError, RecursionError) as error:
        raise ValueError('Artifact must contain finite JSON values.') from error


def digest(value):
    """Canonical JSON SHA256, compatible with native codebook digests."""
    return sha256(_canonical(value).encode('utf-8')).hexdigest()


def _seal(value, key):
    value[key] = digest({k: v for k, v in value.items() if k != key})
    return value


def _bounded(value, maximum):
    if len(_canonical(value).encode('utf-8')) > maximum:
        raise ValueError('Artifact exceeds its byte limit.')


def _text(value, name, maximum=None):
    if type(value) is not str or not value.strip() or (maximum is not None and len(value) > maximum):
        raise ValueError(f'{name} must be a bounded nonblank string.')


def _source_key(entry):
    if type(entry) is not dict:
        raise ValueError('Sources and source spans must be objects.')
    _text(entry.get('source_name'), 'source_name', 1024)
    if type(entry.get('source_hash')) is not str or not _HASH.fullmatch(entry['source_hash']):
        raise ValueError('source_hash must be a lowercase SHA256.')
    return entry['source_name'], entry['source_hash']


def _span(entry, sources):
    key = _source_key(entry)
    start, end, text = (entry.get(k) for k in ('source_start', 'source_end', 'text'))
    if (key not in sources or type(start) is not int or type(end) is not int
            or type(text) is not str or not 0 <= start < end <= len(sources[key])
            or sources[key][start:end] != text):
        raise ValueError('Span text/offsets do not resolve to an embedded original source.')


def _validate_nodes(nodes, source_rows):
    if type(source_rows) is not list or not 1 <= len(source_rows) <= MAX_NODES:
        raise ValueError('Provide 1..20 embedded original sources.')
    sources, names, total = {}, set(), 0
    for source in source_rows:
        key = _source_key(source)
        text = source.get('text')
        if type(text) is not str or not text:
            raise ValueError('Source text must be nonempty.')
        total += len(text.encode('utf-8'))
        if total > MAX_SOURCE_BYTES:
            raise ValueError('Embedded sources exceed 4 MiB.')
        if sha256(text.encode('utf-8')).hexdigest() != key[1]:
            raise ValueError('Original source hash is stale or modified.')
        if key[0] in names:
            raise ValueError('Source names must be unique.')
        names.add(key[0]); sources[key] = text
    if type(nodes) is not list or not 1 <= len(nodes) <= MAX_NODES:
        raise ValueError('Provide 1..20 source-turn nodes.')
    ids, spans = set(), set()
    for node in nodes:
        if type(node) is not dict:
            raise ValueError('Node must be an object.')
        _text(node.get('id'), 'node ID', 128)
        _text(node.get('speaker'), 'source-local speaker', 512)
        _text(node.get('text'), 'node text')
        _span(node, sources)
        identity = (node['source_hash'], node['source_start'], node['source_end'])
        if node['id'] in ids or identity in spans:
            raise ValueError('Duplicate node ID or source-turn identity.')
        ids.add(node['id']); spans.add(identity)
        fields = {'context_start', 'context_end', 'context_text'}
        if fields.intersection(node):
            if not fields.issubset(node):
                raise ValueError('Flat context requires start, end and exact text.')
            _span(dict(source_name=node['source_name'], source_hash=node['source_hash'],
                       source_start=node['context_start'], source_end=node['context_end'], text=node['context_text']), sources)
        if 'context' in node:
            _span(node['context'], sources)
        for field in ('referent_context', 'contexts'):
            if field in node:
                if type(node[field]) is not list or len(node[field]) > MAX_NODES:
                    raise ValueError(f'{field} must be a bounded exact-source span list.')
                for span in node[field]:
                    _span(span, sources)


def prepare_packet(nodes, *, sources, provenance=None):
    """Copy and seal exact turns/full sources; no native eligibility is inferred.

    Node extras are retained, but never vectorized. The preparer owns native-book
    eligibility and selection policy. Optional context spans are source-checked.
    """
    packet = dict(version=VERSION, type=PACKET_TYPE, nodes=deepcopy(nodes),
                  sources=deepcopy(sources), provenance=deepcopy(provenance if provenance is not None else {}))
    _seal(packet, 'packet_sha256')
    validate_packet(packet)
    return packet


def validate_packet(packet):
    if (type(packet) is not dict or set(packet) != {'version', 'type', 'nodes', 'sources', 'provenance', 'packet_sha256'}
            or packet['version'] != VERSION or packet['type'] != PACKET_TYPE):
        raise ValueError('Not a source-turn neighbor packet.')
    _bounded(packet, MAX_PACKET_BYTES)
    if packet['packet_sha256'] != digest({k: v for k, v in packet.items() if k != 'packet_sha256'}):
        raise ValueError('Packet digest is stale or modified.')
    if type(packet['provenance']) is not dict:
        raise ValueError('Packet provenance must be an object.')
    _validate_nodes(packet['nodes'], packet['sources'])


def lexical_vectors(packet):
    """Dense unigram+bigram TF-IDF rows in node order, with no stopword removal.

    Unicode words and apostrophe contractions are retained. TF is count divided
    by all unigram/bigram occurrences; IDF=log((1+N)/(1+df))+1. Vocabulary order is
    lexical and the graph normalizes each row. Only exact full target text enters.
    """
    validate_packet(packet)
    counts = []
    for node in packet['nodes']:
        words = _TOKEN.findall(node['text'].casefold())
        terms = [('u', word) for word in words] + [('b', a, b) for a, b in zip(words, words[1:])]
        counts.append(Counter(terms))
    vocabulary = sorted({term for row in counts for term in row})
    # One explicit zero dimension handles text with no lexical tokens.
    if not vocabulary:
        return [[0.0] for _ in counts]
    frequency = Counter(term for row in counts for term in row)
    return [[row.get(term, 0) / (sum(row.values()) or 1)
             * (math.log((1 + len(counts)) / (1 + frequency[term])) + 1)
             for term in vocabulary] for row in counts]


def _vectors(vectors, count):
    if type(vectors) not in (list, tuple) or len(vectors) != count:
        raise ValueError('Vector count must match packet node order.')
    rows, normalized, dimensions = [], [], None
    for row in vectors:
        if type(row) not in (list, tuple) or not 1 <= len(row) <= MAX_DIMENSIONS:
            raise ValueError('Each vector needs 1..8192 dimensions.')
        if dimensions is None:
            dimensions = len(row)
        if len(row) != dimensions or any(isinstance(v, bool) or not isinstance(v, Real) for v in row):
            raise ValueError('Vector dimensions/types must be consistent numeric values.')
        values = [float(v) for v in row]
        if any(not math.isfinite(v) for v in values):
            raise ValueError('Vectors must be finite.')
        norm = math.hypot(*values)
        if not math.isfinite(norm):
            raise ValueError('Vector magnitude exceeds finite numeric bounds.')
        rows.append(values)
        normalized.append([v / norm for v in values] if norm else [0.0] * dimensions)
    return rows, normalized


def build_neighbor_graph(packet, vectors, *, backend, provenance=None, policy_id=None):
    """Build candidate pairs under the original or an explicit bounded rule.

    With no policy_id, the original v1 graph is reproduced exactly. An explicit
    registered policy produces a v2 artifact recording k, direction and floor.
    These are retrieval rules, not semantic agreement or acceptance decisions.

    Vectors must correspond exactly to packet.nodes order. Backend provenance is
    caller-attested (model hashes/tokenization/truncation checks belong to the
    encoder adapter); this pure function cannot identify which model made rows.
    """
    validate_packet(packet)
    if backend not in BACKENDS:
        raise ValueError('Backend must be lexical_tfidf or sentence_embeddings.')
    provenance = deepcopy(provenance if provenance is not None else {})
    if type(provenance) is not dict:
        raise ValueError('Backend provenance must be an object.')
    if policy_id is None:
        k, floor, rule = K, MIN_SIMILARITY, 'mutual'
    elif type(policy_id) is str and policy_id in RETRIEVAL_POLICIES:
        policy = RETRIEVAL_POLICIES[policy_id]
        k, floor, rule = policy['k'], policy['min_similarity'], policy['neighbor_rule']
    else:
        raise ValueError('Choose a registered bounded retrieval policy.')
    rows, normalized = _vectors(vectors, len(packet['nodes']))
    nodes = packet['nodes']
    order = sorted(range(len(nodes)), key=lambda i: nodes[i]['id'])
    zero = {i for i, row in enumerate(normalized) if not any(row)}
    matrix = [[max(-1.0, min(1.0, math.fsum(a * b for a, b in zip(left, right))))
               for right in normalized] for left in normalized]
    ranks = {}
    for i in order:
        candidates = [j for j in order if j != i and nodes[j]['source_hash'] != nodes[i]['source_hash']
                      and i not in zero and j not in zero]
        candidates.sort(key=lambda j: (-matrix[i][j], nodes[j]['id']))
        for rank, j in enumerate(candidates, 1):
            ranks[i, j] = rank
    pairs, edges = [], []
    for offset, i in enumerate(order):
        for j in order[offset + 1:]:
            distinct = nodes[i]['source_hash'] != nodes[j]['source_hash']
            left_rank, right_rank = ranks.get((i, j)), ranks.get((j, i))
            rank_eligible = (left_rank is not None and right_rank is not None and
                ((left_rank <= k and right_rank <= k) if rule == 'mutual' else
                 (left_rank <= k or right_rank <= k)))
            selected = distinct and rank_eligible and matrix[i][j] >= floor
            pair = dict(member_ids=[nodes[i]['id'], nodes[j]['id']], cosine=matrix[i][j],
                        source_distinct=distinct, left_rank=left_rank, right_rank=right_rank,
                        candidate_pair=selected)
            pairs.append(pair)
            if selected:
                edges.append(deepcopy(pair))
    connected = {cid for edge in edges for cid in edge['member_ids']}
    zero_ids = sorted(nodes[i]['id'] for i in zero)
    algorithm = dict(k=k, min_similarity=floor, tie_break='ascending_node_id',
                     neighbor_pool='distinct_source_hash_nonzero_vectors')
    if policy_id is not None:
        algorithm.update(policy_id=policy_id, neighbor_rule=rule)
    graph = dict(version=VERSION if policy_id is None else POLICY_GRAPH_VERSION,
                 type=GRAPH_TYPE, packet=deepcopy(packet),
                 packet_sha256=packet['packet_sha256'], backend=backend, backend_provenance=provenance,
                 vector_text_sha256=digest([node['text'] for node in nodes]), vectors=rows,
                 vectors_sha256=digest(rows), algorithm=algorithm,
                 pair_scores=pairs, edges=edges, isolates=sorted(n['id'] for n in nodes if n['id'] not in connected),
                 zero_vector_ids=zero_ids, status=('no_usable_vectors' if len(zero) == len(nodes)
                 else 'partial_zero_vectors' if zero else 'candidate_membership_only'),
                 semantic_status='unassessed_not_equivalence_or_semantic_approval', warnings=deepcopy(WARNINGS))
    if zero:
        graph['warnings'].append('Zero vectors provide no neighbor evidence; affected nodes are explicit isolates.')
    if rule == 'either':
        graph['warnings'].append('Either-direction retrieval permits asymmetric neighbors. Extra coverage can add misleading comparisons; it does not establish shared meaning.')
    _seal(graph, 'graph_sha256')
    _bounded(graph, MAX_GRAPH_BYTES)
    return graph


def validate_neighbor_graph(graph):
    """Recompute all source proofs, scores, ranks and memberships on reopen."""
    if type(graph) is not dict or graph.get('type') != GRAPH_TYPE:
        raise ValueError('Not a source-turn candidate-pair graph.')
    _bounded(graph, MAX_GRAPH_BYTES)
    try:
        if graph.get('version') == VERSION:
            policy_id = None
        elif graph.get('version') == POLICY_GRAPH_VERSION:
            policy_id = graph['algorithm']['policy_id']
            if policy_id is None:
                raise ValueError('A v2 graph requires an explicit registered policy.')
        else:
            raise ValueError('Unknown neighbor graph version.')
        expected = build_neighbor_graph(graph['packet'], graph['vectors'], backend=graph['backend'],
                                        provenance=graph['backend_provenance'], policy_id=policy_id)
        if _canonical(graph) != _canonical(expected):
            raise ValueError('Graph digest, provenance or derived neighbor records are stale or modified.')
    except (KeyError, TypeError, OverflowError) as error:
        raise ValueError('Incomplete or invalid neighbor graph.') from error


def export_graph_json(graph):
    validate_neighbor_graph(graph)
    return json.dumps(graph, ensure_ascii=False, indent=2, allow_nan=False)


def export_graph_html(graph):
    """Standalone escaped source review, with no native categories or themes."""
    validate_neighbor_graph(graph)
    e = lambda value: escape(str(value), quote=True)
    rows = ['<!doctype html><html lang="en"><meta charset="utf-8">',
            '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; base-uri \'none\'; form-action \'none\'">',
            '<title>Source-turn neighbor candidates</title><style>body{font:16px/1.5 system-ui;max-width:1100px;margin:auto;padding:20px}pre{white-space:pre-wrap;overflow-wrap:anywhere}</style>',
            '<h1>Source-turn neighbor candidates</h1>',
            '<p>Candidate pair memberships only. No generated category names, equivalence or themes.</p>',
            '<pre>' + e(json.dumps({k: graph[k] for k in ('backend', 'status', 'algorithm', 'packet_sha256', 'graph_sha256', 'backend_provenance')}, ensure_ascii=False, indent=2)) + '</pre>']
    rows += ['<p>' + e(warning) + '</p>' for warning in graph['warnings']]
    rows.append('<h2>Candidate pairs under the recorded retrieval rule</h2>')
    rows += ['<pre>' + e(json.dumps(edge, ensure_ascii=False)) + '</pre>' for edge in graph['edges']]
    if not graph['edges']:
        rows.append('<p>No candidate pairs under the frozen retrieval rule.</p>')
    rows.append('<h2>Isolates</h2><pre>' + e(json.dumps(graph['isolates'], ensure_ascii=False)) + '</pre>')
    rows.append('<h2>Exact source turns and member distinctions</h2>')
    for node in graph['packet']['nodes']:
        rows.append('<article><h3>' + e(node['id']) + '</h3><pre>' + e(node['text']) + '</pre>')
        rows.append('<details><summary>Exact source provenance and context, not additional support</summary><pre>'
                    + e(json.dumps({k: v for k, v in node.items() if k != 'text'}, ensure_ascii=False, indent=2)) + '</pre></details></article>')
    rows.append('<details><summary>All pair scores and directional ranks</summary><pre>'
                + e(json.dumps(graph['pair_scores'], ensure_ascii=False, indent=2)) + '</pre></details>')
    rows.append('<details><summary>Packet selection provenance</summary><pre>'
                + e(json.dumps(graph['packet']['provenance'], ensure_ascii=False, indent=2)) + '</pre></details>')
    rows.append('</html>')
    return '\n'.join(rows)

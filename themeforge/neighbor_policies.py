"""Re-select bounded candidate neighbors from saved vectors, without inference.

The original graph and every rule are retained. Evaluation labels are never
inputs to selection. No connected components, category names or accepted themes
are produced, and earlier human/AI review decisions are not transferred.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
from html import escape
import json
from pathlib import Path
import time

from .semantic_neighbors import (MAX_GRAPH_BYTES, RETRIEVAL_POLICIES, build_neighbor_graph,
    digest, export_graph_json, export_graph_html, validate_neighbor_graph)
from .sentence_encoder import local_file, file_sha256


def reselect_neighbor_graph(graph, policy_id):
    """Make a separately attributed retrieval branch; input and vectors stay intact."""
    validate_neighbor_graph(graph)
    provenance = deepcopy(graph['backend_provenance'])
    provenance['retrieval_reselection'] = dict(
        source_graph_sha256=graph['graph_sha256'], source_vectors_sha256=graph['vectors_sha256'],
        source_algorithm=deepcopy(graph['algorithm']), policy_id=policy_id,
        new_encoder_calls=0, review_decisions_transferred=False, semantic_approval=False)
    return build_neighbor_graph(graph['packet'], graph['vectors'], backend=graph['backend'],
                                provenance=provenance, policy_id=policy_id)


def diagnose_pair(pair, algorithm):
    """Explain exclusion mechanically. A passed filter is not a useful meaning."""
    if not pair['source_distinct']:
        return 'same_source_excluded'
    a, b = pair['left_rank'], pair['right_rank']
    if a is None or b is None:
        return 'zero_vector_excluded'
    below = pair['cosine'] < algorithm['min_similarity']
    rule, k = algorithm.get('neighbor_rule', 'mutual'), algorithm['k']
    ranks_pass = (a <= k and b <= k) if rule == 'mutual' else (a <= k or b <= k)
    if below and not ranks_pass:
        return 'below_floor_and_outside_rank_rule'
    if below:
        return 'below_floor_only'
    if not ranks_pass:
        if rule == 'mutual' and (a <= k or b <= k):
            return 'asymmetric_rank_only'
        return 'outside_rank_rule_only'
    return 'retained_candidate_not_semantic_approval'


def comparison_summary(original, branches):
    validate_neighbor_graph(original)
    if type(branches) is not dict or set(branches) != set(RETRIEVAL_POLICIES):
        raise ValueError('Compare exactly the six registered policies; do not hide failed rules.')
    before = {tuple(e['member_ids']) for e in original['edges']}
    rows = []
    for policy_id in RETRIEVAL_POLICIES:
        graph = branches[policy_id]
        validate_neighbor_graph(graph)
        expected = reselect_neighbor_graph(original, policy_id)
        if graph != expected:
            raise ValueError('Policy branch does not derive from the supplied original graph.')
        current = {tuple(e['member_ids']) for e in graph['edges']}
        rows.append(dict(policy_id=policy_id, algorithm=graph['algorithm'], candidate_pairs=len(current),
                         covered_nodes=len(graph['packet']['nodes']) - len(graph['isolates']),
                         isolates=graph['isolates'], added_pair_ids=[list(x) for x in sorted(current-before)],
                         removed_pair_ids=[list(x) for x in sorted(before-current)], graph_sha256=graph['graph_sha256']))
    return dict(version='themeforge.neighbor-rule-comparison.v1', source_graph_sha256=original['graph_sha256'],
                vectors_sha256=original['vectors_sha256'], packet_sha256=original['packet_sha256'],
                backend=original['backend'], policies=rows, new_encoder_calls=0,
                semantic_assessment='not_assessed_by_algorithm', review_decisions_transferred=False,
                warning='More pairs increase review burden and may add misleading matches. No setting establishes agreement, categories or themes.')


def export_policy_comparison(graph, output, *, cancel=None):
    """Write a fresh local bundle containing all rules and the untouched input."""
    validate_neighbor_graph(graph)
    output = Path(output).resolve()
    if str(output).startswith(('\\\\', '//')):
        raise ValueError('Choose a local output directory.')
    output.mkdir(parents=True, exist_ok=False)
    started = time.perf_counter()
    def check_budget():
        if cancel is not None and cancel.is_set():
            raise RuntimeError('Saved-vector comparison cancelled; existing artifacts were preserved.')
        if time.perf_counter() - started > 180:
            raise RuntimeError('Saved-vector comparison exceeded its 180-second check between bounded operations.')
    branches = {}
    for policy_id in RETRIEVAL_POLICIES:
        check_budget()
        branches[policy_id] = reselect_neighbor_graph(graph, policy_id)
    check_budget()
    summary = comparison_summary(graph, branches)
    check_budget()
    with (output / 'original-graph.json').open('x', encoding='utf-8') as stream:
        stream.write(export_graph_json(graph))
    for policy_id, branch in branches.items():
        check_budget()
        for suffix, content in (('json', export_graph_json(branch)), ('html', export_graph_html(branch))):
            with (output / f'{policy_id}.{suffix}').open('x', encoding='utf-8') as stream:
                stream.write(content)
            check_budget()
    summary['selection_export_seconds'] = time.perf_counter() - started
    summary['recorded_utc'] = datetime.now(timezone.utc).isoformat()
    with (output / 'comparison.json').open('x', encoding='utf-8') as stream:
        json.dump(summary, stream, ensure_ascii=False, indent=2, allow_nan=False)
    e = lambda value: escape(str(value), quote=True)
    html = ['<!doctype html><meta charset="utf-8"><title>Bounded neighbor rule comparison</title>',
            '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; base-uri \'none\'; form-action \'none\'">',
            '<style>body{font:16px/1.5 system-ui;max-width:1100px;margin:auto;padding:24px}td,th{padding:10px;border:1px solid #bbb}table{border-collapse:collapse}</style>',
            '<h1>Bounded neighbor rule comparison</h1><p>'+e(summary['warning'])+'</p>',
            '<p>Saved vectors only. No model calls, semantic acceptance or transfer of earlier review judgments. Exact quotes remain in every branch.</p>',
            '<table><tr><th>Rule</th><th>Candidate pairs</th><th>Covered turns</th><th>Added / removed</th><th>Evidence</th></tr>']
    for row in summary['policies']:
        pid = row['policy_id']
        html.append(f'<tr><td>{e(pid)}</td><td>{row["candidate_pairs"]}</td><td>{row["covered_nodes"]}</td><td>{len(row["added_pair_ids"])} / {len(row["removed_pair_ids"])}</td><td><a href="{e(pid)}.html">Read exact sources</a> | <a href="{e(pid)}.json">Open in ThemeForge</a></td></tr>')
    html.append('</table><p>Cosine cutoffs are not calibrated across TF-IDF and sentence encoders. Pair count alone is not quality.</p>')
    with (output / 'index.html').open('x', encoding='utf-8') as stream:
        stream.write('\n'.join(html))
    check_budget()
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--graph', required=True, help='Existing raw graph JSON, not an edited review ledger')
    parser.add_argument('--out', required=True, help='Fresh local output directory')
    args = parser.parse_args(argv)
    path = local_file(args.graph)
    if path.stat().st_size > MAX_GRAPH_BYTES:
        parser.error('Graph exceeds the local artifact size bound.')
    original_pin = file_sha256(path)
    graph = json.loads(path.read_text(encoding='utf-8-sig'))
    export_policy_comparison(graph, args.out)
    if file_sha256(path) != original_pin:
        raise ValueError('Original graph changed during comparison.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

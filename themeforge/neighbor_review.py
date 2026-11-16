"""Add-only, explicitly authored judgments over immutable candidate pairs.

Hashes bind recorded content and history; they do not authenticate a reviewer
identity, prevent replacement of a whole artifact, or establish semantic truth.
No model, native code/category mutation, or automatic classification is involved.
"""
from copy import deepcopy
from datetime import datetime, timezone
from html import escape
import json
import re

from .semantic_neighbors import digest, validate_neighbor_graph

VERSION = 'themeforge.neighbor-review.v1'
ARTIFACT_TYPE = 'source_turn_candidate_pair_review'
DECISIONS = ('keep_related_comparison', 'keep_contrast', 'reject_pair', 'uncertain')
REVIEWER_KINDS = ('ai', 'researcher')
MAX_RATIONALE_CHARS = 2000
MAX_EVENTS = 256
MAX_REVIEW_BYTES = 40 * 1024**2
STATUS = 'recorded_judgments_not_semantic_validation'
_UTC = re.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}\+00:00$')


def _utc_now():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds')


def _timestamp(value):
    if type(value) is not str or not _UTC.fullmatch(value):
        raise ValueError('Timestamp must be an explicit UTC timestamp.')
    try:
        datetime.fromisoformat(value)
    except ValueError as error:
        raise ValueError('Invalid UTC timestamp.') from error


def _seal(value, key):
    value[key] = digest({k: v for k, v in value.items() if k != key})
    return value


def _bounded(review):
    try:
        raw = json.dumps(review, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')
    except (TypeError, ValueError, RecursionError) as error:
        raise ValueError('Review must contain finite JSON values.') from error
    if len(raw) > MAX_REVIEW_BYTES:
        raise ValueError('Review exceeds its byte limit.')


def _base(graph, created_at):
    return _seal(dict(version=VERSION, type=ARTIFACT_TYPE, graph=deepcopy(graph),
                     graph_sha256=graph['graph_sha256'], created_at=created_at,
                     status=STATUS, history=[]), 'review_sha256')


def create_review(graph):
    """Copy a validated graph into a new empty ledger, without assigning an author."""
    validate_neighbor_graph(graph)
    review = _base(graph, _utc_now())
    _bounded(review)
    return review


def _selection(graph, member_ids):
    if (type(member_ids) is not list or len(member_ids) != 2
            or any(type(identifier) is not str for identifier in member_ids)
            or member_ids[0] == member_ids[1]):
        raise ValueError('Review requires two distinct original member IDs.')
    for edge in graph['edges']:
        if set(member_ids) == set(edge['member_ids']):
            return deepcopy(edge['member_ids'])
    raise ValueError('Review membership must match an actual selected graph edge.')


def _event(review, member_ids, decision, rationale, reviewer_kind, timestamp):
    if type(decision) is not str or decision not in DECISIONS:
        raise ValueError('Unknown pair review decision.')
    if type(reviewer_kind) is not str or reviewer_kind not in REVIEWER_KINDS:
        raise ValueError('Reviewer kind must explicitly be ai or researcher.')
    if type(rationale) is not str or not rationale.strip() or len(rationale) > MAX_RATIONALE_CHARS:
        raise ValueError('Rationale must be nonblank and at most 2000 characters.')
    _timestamp(timestamp)
    return _seal(dict(event='pair_judgment_recorded', sequence=len(review['history']) + 1,
                      member_ids=_selection(review['graph'], member_ids), decision=decision,
                      rationale=rationale, reviewer_kind=reviewer_kind,
                      author_assertion=('AI-authored assertion' if reviewer_kind == 'ai'
                                        else 'Researcher-authored assertion; identity not authenticated'),
                      status=STATUS, timestamp=timestamp,
                      graph_sha256=review['graph_sha256'], before_review_sha256=review['review_sha256']),
                 'event_sha256')


def _append(review, event):
    updated = dict(review, history=[*review['history'], event])
    return _seal(updated, 'review_sha256')


def validate_review(review):
    """Validate exact graph/source proof and replay every append/hash binding."""
    if (type(review) is not dict or set(review) != {'version', 'type', 'graph', 'graph_sha256',
            'created_at', 'status', 'history', 'review_sha256'} or review.get('version') != VERSION
            or review.get('type') != ARTIFACT_TYPE or review.get('status') != STATUS):
        raise ValueError('Not an unvalidated candidate-pair review ledger.')
    _bounded(review)
    validate_neighbor_graph(review['graph'])
    if review['graph_sha256'] != review['graph']['graph_sha256']:
        raise ValueError('Review graph binding is stale or modified.')
    _timestamp(review['created_at'])
    if type(review['history']) is not list or len(review['history']) > MAX_EVENTS:
        raise ValueError('Review history exceeds its bounded event count.')
    current = _base(review['graph'], review['created_at'])
    for saved in review['history']:
        if type(saved) is not dict:
            raise ValueError('Review events must be objects.')
        try:
            expected = _event(current, saved['member_ids'], saved['decision'], saved['rationale'],
                              saved['reviewer_kind'], saved['timestamp'])
        except KeyError as error:
            raise ValueError('Review event is incomplete.') from error
        # Canonical digests distinguish bool from int, and reject extra fields.
        if digest(saved) != digest(expected):
            raise ValueError('Review event content, authorship or hash lineage is stale or modified.')
        current = _append(current, expected)
    if digest(review) != digest(current):
        raise ValueError('Review digest/history binding is stale or modified.')


def append_review(review, member_ids, decision, rationale, *, reviewer_kind):
    """Return a new branch with one explicit judgment; previous entries survive.

    An opposing/revised judgment appends another event for the same pair. It does
    not overwrite earlier authors or imply that the new author has validated them.
    """
    validate_review(review)
    if len(review['history']) >= MAX_EVENTS:
        raise ValueError('Review history is full; preserve this artifact and start a separately recorded review.')
    event = _event(review, member_ids, decision, rationale, reviewer_kind, _utc_now())
    updated = _append(deepcopy(review), event)
    _bounded(updated)
    return updated


def export_review_json(review):
    validate_review(review)
    return json.dumps(review, ensure_ascii=False, indent=2, allow_nan=False)


def export_review_html(review):
    """Standalone escaped judgments plus exact quotes and original provenance."""
    validate_review(review)
    e = lambda value: escape(str(value), quote=True)
    graph = review['graph']
    nodes = {node['id']: node for node in graph['packet']['nodes']}
    rows = ['<!doctype html><html lang="en"><meta charset="utf-8">',
        '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; base-uri \'none\'; form-action \'none\'">',
        '<title>Candidate-pair review ledger</title><style>body{font:16px/1.5 system-ui;max-width:1100px;margin:auto;padding:20px}pre{white-space:pre-wrap;overflow-wrap:anywhere}article{border:1px solid #aaa;padding:16px;margin:16px 0}</style>',
        '<h1>Candidate-pair review ledger</h1><p>AI and researcher judgments are attributed assertions, not established truth. '
        'Scores are not confidence. Pair membership is not equivalence, a category or a theme. Reviewer identity is caller-declared.</p>',
        '<pre>' + e(json.dumps({key: review[key] for key in ('created_at', 'status', 'graph_sha256', 'review_sha256')}, indent=2)) + '</pre>',
        '<h2>Original graph provenance</h2><pre>' + e(json.dumps({key: graph[key] for key in
            ('backend', 'backend_provenance', 'algorithm', 'packet_sha256', 'status', 'warnings')}, ensure_ascii=False, indent=2)) + '</pre>']
    for edge in graph['edges']:
        ids = edge['member_ids']
        rows.append('<article><h2>' + e(' / '.join(ids)) + '</h2><pre>' + e(json.dumps(edge, ensure_ascii=False)) + '</pre>')
        for identifier in ids:
            node = nodes[identifier]
            rows.append('<h3>' + e(identifier) + '</h3><pre>' + e(node['text']) + '</pre>')
            rows.append('<details><summary>Exact source metadata and context, not added evidence</summary><pre>'
                        + e(json.dumps({k: v for k, v in node.items() if k != 'text'}, ensure_ascii=False, indent=2)) + '</pre></details>')
        events = [event for event in review['history'] if event['member_ids'] == ids]
        if not events:
            rows.append('<p>No judgment recorded for this pair.</p>')
        for event in events:
            rows.append('<h3>' + e(event['author_assertion']) + ': ' + e(event['decision']) + '</h3>')
            rows.append('<pre>' + e(json.dumps(event, ensure_ascii=False, indent=2)) + '</pre>')
        rows.append('</article>')
    if not graph['edges']:
        rows.append('<p>No selected edges; there are no reviewable pairs in this graph.</p>')
    rows.append('<h2>Unpaired source turns (isolates)</h2><pre>' + e(json.dumps(graph['isolates'], ensure_ascii=False)) + '</pre>')
    for identifier in graph['isolates']:
        rows.append('<details><summary>' + e(identifier) + '</summary><pre>'
                    + e(json.dumps(nodes[identifier], ensure_ascii=False, indent=2)) + '</pre></details>')
    rows.append('<details><summary>Packet selection provenance</summary><pre>'
                + e(json.dumps(graph['packet']['provenance'], ensure_ascii=False, indent=2)) + '</pre></details></html>')
    return '\n'.join(rows)

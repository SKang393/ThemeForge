"""Add-only, explicitly authored categories, separate from model synthesis.

Hashes bind native snapshots and the append history; they are not signatures or
semantic approval. Run native source validation before the collection validator.
"""
from copy import deepcopy
from html import escape
import json

from .codebook_contracts import _text
from .model_pilot import _digest


COLLECTION_FIELDS = frozenset(('reviewed_categories', 'reviewed_category_history',
                               'reviewed_category_base_sha256'))
MAX_CATEGORIES = 24
MAX_COLLECTION_BYTES = 4 * 1024**2
MAX_NATIVE_BYTES = 16 * 1024**2
_AUTHORS = frozenset(('researcher', 'assistant_development_review'))
_CATEGORY_FIELDS = frozenset(('id', 'kind', 'origin', 'label', 'shared_meaning', 'limits',
                              'code_ids', 'members', 'review_status', 'reason', 'category_sha256'))
_EVENT_FIELDS = frozenset(('event', 'category_id', 'category_sha256', 'reviewer_kind',
                           'reason', 'before_result_sha256'))


def _seal(result):
    result['result_sha256'] = _digest({k: v for k, v in result.items() if k != 'result_sha256'})
    return result


def _bounded_text(value, name, maximum):
    _text(value, name, maximum, substantive=True)
    return value.strip()


def _author(value):
    if type(value) is not str or value not in _AUTHORS:
        raise ValueError('Explicit researcher or assistant_development_review authorship is required.')


def _members(result, code_ids, member_notes):
    # Runtime import avoids a module initialization cycle with native validation.
    from .local_codebook import has_completed_definition
    if (type(code_ids) is not list or not 2 <= len(code_ids) <= 6
            or any(type(cid) is not str for cid in code_ids) or len(set(code_ids)) != len(code_ids)):
        raise ValueError('Select exactly 2..6 unique known code IDs.')
    if type(member_notes) is not dict or set(member_notes) != set(code_ids):
        raise ValueError('Provide one explicit member note for each selected code, with no extra IDs.')
    codes = {code['id']: code for code in result['codes']}
    if any(cid not in codes or not has_completed_definition(codes[cid]) for cid in code_ids):
        raise ValueError('Reviewed category members must be known codes with completed definitions.')
    from .codebook_review import grouping_review_issue, candidate_review_state
    for cid in code_ids:
        issue = grouping_review_issue(result, cid)
        if issue:
            raise ValueError(issue)
    if len({span['source_hash'] for cid in code_ids for span in codes[cid]['evidence']}) < 2:
        raise ValueError('Reviewed categories require at least two distinct source contents.')
    members = []
    for cid in code_ids:
        code = codes[cid]
        for field, maximum in (('label', 120), ('claim', 600), ('definition', 600)):
            _text(code.get(field), 'member ' + field, maximum, substantive=True)
        wording = {field: code.get('interpretation_field_origins', {}).get(field,
                    code.get('field_origins', {}).get(field, 'local_model'))
                   for field in ('label', 'claim', 'definition')}
        roles = {role: code.get('evidence_role_origins', {}).get(role,
                    code.get('evidence_selection_origin', 'local_model') if role == 'support' else 'local_model')
                 for role in ('support', 'qualifications', 'counterexamples')}
        member = dict(code_id=cid, code_sha256=_digest(code),
                      label=code['label'], claim=code['claim'], definition=code['definition'],
                      wording_origins=wording, evidence_role_origins=roles,
                      member_note=_bounded_text(member_notes[cid], 'member note', 1200))
        for field in ('support_span_ids', 'evidence', 'qualifications', 'counterexamples'):
            member[field] = deepcopy(code[field])
        member['referent_context'] = deepcopy(code.get('referent_context', []))
        member['referent_context_origin'] = code.get('referent_context_origin')
        if 'candidate_review_workflow' in result:
            member['candidate_review'] = candidate_review_state(result, cid)
        members.append(member)
    return members


def _category(result, code_ids, *, label, shared_meaning, member_notes, limits, reviewer_kind, reason):
    _author(reviewer_kind)
    category = dict(kind='category', origin=reviewer_kind,
                    label=_bounded_text(label, 'category label', 120),
                    shared_meaning=_bounded_text(shared_meaning, 'shared meaning', 600),
                    limits=_bounded_text(limits, 'category limits', 2000),
                    reason=_bounded_text(reason, 'review reason', 2000),
                    code_ids=deepcopy(code_ids), members=_members(result, code_ids, member_notes),
                    review_status='provisional_unvalidated')
    category['id'] = 'RCAT-' + _digest(category)[:16]
    category['category_sha256'] = _digest(category)
    return category


def _duplicate_key(category):
    return _digest(dict(label=category['label'], shared_meaning=category['shared_meaning'],
                        limits=category['limits'],
                        members=sorted((member['code_id'], member['member_note']) for member in category['members'])))


def validate_reviewed_categories(result):
    """Validate exact snapshots and reconstruct each append from the native base.

    Legacy results without these fields remain readable. Native source validation
    owns individual code/registry validity; this function adds reviewed lineage.
    It never calls back into the full native validator.
    """
    present = COLLECTION_FIELDS.intersection(result)
    if not present:
        return
    if present != COLLECTION_FIELDS:
        raise ValueError('Reviewed category collection, history and base hash must remain together.')
    try:
        categories, history = result['reviewed_categories'], result['reviewed_category_history']
        if (type(categories) is not list or not 1 <= len(categories) <= MAX_CATEGORIES
                or type(history) is not list or len(history) != len(categories)):
            raise ValueError('Reviewed categories need 1..24 entries with one append event per entry.')
        if len(json.dumps({k: result[k] for k in COLLECTION_FIELDS}, ensure_ascii=False).encode('utf-8')) > MAX_COLLECTION_BYTES:
            raise ValueError('Reviewed category metadata exceeds 4 MiB.')
        base = {k: deepcopy(v) for k, v in result.items() if k not in COLLECTION_FIELDS and k != 'result_sha256'}
        _seal(base)
        if base['result_sha256'] != result['reviewed_category_base_sha256']:
            raise ValueError('The original native base changed after reviewed category creation.')
        previous_digest = base['result_sha256']
        seen = set()
        for index, (category, event) in enumerate(zip(categories, history)):
            if type(category) is not dict or set(category) != _CATEGORY_FIELDS:
                raise ValueError('Reviewed category fields are malformed or incomplete.')
            if type(event) is not dict or set(event) != _EVENT_FIELDS:
                raise ValueError('Reviewed category history fields are malformed or incomplete.')
            expected = _category(base, category['code_ids'], label=category['label'],
                shared_meaning=category['shared_meaning'], limits=category['limits'],
                member_notes={member['code_id']: member['member_note'] for member in category['members']},
                reviewer_kind=category['origin'], reason=category['reason'])
            if _digest(category) != _digest(expected):
                raise ValueError('Reviewed category identity, authorship or exact member snapshots changed.')
            duplicate = _duplicate_key(category)
            if duplicate in seen:
                raise ValueError('An identical reviewed category already exists.')
            seen.add(duplicate)
            if (event['event'] != 'reviewed_category_added' or event['category_id'] != category['id']
                    or event['category_sha256'] != category['category_sha256']
                    or event['reviewer_kind'] != category['origin']
                    or event['reason'] != category['reason']
                    or event['before_result_sha256'] != previous_digest):
                raise ValueError('Reviewed category append history or authorship changed.')
            if _bounded_text(event['reason'], 'review reason', 2000) != event['reason']:
                raise ValueError('Review reasons must retain their canonical text.')
            prefix = dict(base)
            prefix.update(reviewed_category_base_sha256=result['reviewed_category_base_sha256'],
                          reviewed_categories=categories[:index + 1], reviewed_category_history=history[:index + 1])
            previous_digest = _seal(prefix)['result_sha256']
        if result.get('result_sha256') != previous_digest:
            raise ValueError('Reviewed category result digest differs from its reconstructed append history.')
    except (KeyError, TypeError, AttributeError) as error:
        raise ValueError('Malformed reviewed category metadata or member snapshots.') from error


def add_reviewed_category(result, code_ids, *, label, shared_meaning, member_notes, limits, reviewer_kind, reason):
    """Add one explicit authored category without calling a client or mutating input.

    Revisions require a separate parent branch. Original model group/no_group,
    source codes, statuses, calls and histories remain intact as native values.
    """
    from .local_codebook import validate_result_sources
    validate_result_sources(result)
    validate_reviewed_categories(result)
    reason = _bounded_text(reason, 'review reason', 2000)
    category = _category(result, code_ids, label=label, shared_meaning=shared_meaning,
                         member_notes=member_notes, limits=limits, reviewer_kind=reviewer_kind, reason=reason)
    old_categories = result.get('reviewed_categories', [])
    if len(old_categories) >= MAX_CATEGORIES:
        raise ValueError('At most 24 reviewed categories may be added; use a separate parent branch.')
    if any(_duplicate_key(row) == _duplicate_key(category) for row in old_categories):
        raise ValueError('An identical reviewed category already exists; revisions require a separate parent branch.')
    revised = deepcopy(result)
    revised.setdefault('reviewed_category_base_sha256', result['result_sha256'])
    revised.setdefault('reviewed_categories', []).append(category)
    revised.setdefault('reviewed_category_history', []).append(dict(
        event='reviewed_category_added', category_id=category['id'], category_sha256=category['category_sha256'],
        reviewer_kind=reviewer_kind, reason=reason, before_result_sha256=result['result_sha256']))
    _seal(revised)
    if len(json.dumps(revised, ensure_ascii=False, indent=2).encode('utf-8')) > MAX_NATIVE_BYTES:
        raise ValueError('Reviewed draft would exceed the native 16 MiB reopen limit.')
    validate_reviewed_categories(revised)
    validate_result_sources(revised)
    return revised


def render_reviewed_categories(result):
    """Validated HTML fragment, keeping authored categories distinct from themes."""
    validate_reviewed_categories(result)
    if not result.get('reviewed_categories'):
        return ''
    e = lambda value: escape(str(value), quote=True)
    rows = ['<h2>Categories added during review</h2>',
            '<p>Explicit authorship, not model grouping success, interpretive themes or human ground truth. '
            'Source proof is not semantic approval. Add-only; revisions require a separate parent branch. '
            'Member qualifications and counterexamples retain their original code roles; they are not automatic category counterclaims.</p>']
    for category, event in zip(result['reviewed_categories'], result['reviewed_category_history']):
        rows += [f"<article><h3>{e(category['label'])}</h3><p><b>Origin:</b> {e(category['origin'])}; provisional_unvalidated</p>",
                 f"<p>{e(category['shared_meaning'])}</p><p><b>Limits:</b> {e(category['limits'])}</p>",
                 f"<p><b>Reason:</b> {e(event['reason'])}</p>",
                 '<details><summary>Category append provenance</summary><pre>' + e(json.dumps(event, ensure_ascii=False, indent=2)) + '</pre></details>']
        for member in category['members']:
            rows += [f"<h4>{e(member['code_id'])}: {e(member['label'])}</h4><p>{e(member['claim'])}</p>",
                     f"<p>{e(member['definition'])}</p><p><b>Member note:</b> {e(member['member_note'])}</p>",
                     '<pre>' + e(json.dumps(dict(wording_origins=member['wording_origins'], evidence_role_origins=member['evidence_role_origins']), ensure_ascii=False, indent=2)) + '</pre>']
            if 'candidate_review' in member:
                rows.append('<details><summary>Member candidate review, declared reviewer and review notes</summary><pre>' +
                            e(json.dumps(member['candidate_review'], ensure_ascii=False, indent=2)) + '</pre></details>')
            rows.append('<details><summary>Selected exact referent context, not additional support</summary><pre>' +
                        e(json.dumps(dict(origin=member['referent_context_origin'], entries=member['referent_context']), ensure_ascii=False, indent=2)) + '</pre></details>')
            for role in ('evidence', 'qualifications', 'counterexamples'):
                rows.append('<h5>Member ' + e(role) + '</h5>')
                for span in member[role]:
                    rows.append('<blockquote>' + e(span['text']) + '</blockquote><pre>' +
                                e(json.dumps({k: v for k, v in span.items() if k != 'text'}, ensure_ascii=False, indent=2)) + '</pre>')
        rows.append('</article>')
    return '\n'.join(rows)

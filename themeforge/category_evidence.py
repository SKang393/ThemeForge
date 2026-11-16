"""Bind category evidence to gated source-checked member codes, not semantics."""
from __future__ import annotations

from .codebook_contracts import validate_synthesis
from .model_pilot import _digest


def validate_category_evidence(result):
    """Check host category IDs and exact role flattening without mutating data.

    The native source validator must validate individual codes first. Category
    evidence deliberately retains duplicate spans when different member codes
    legitimately cite the same source passage. No-group and failed calls with
    no categories require no additional fields and remain untouched.
    """
    categories = result.get('categories', [])
    if type(categories) is not list:
        raise ValueError('Categories must be an array.')
    if not categories:
        return
    try:
        rows = result['codes']
        if type(rows) is not list or any(type(code) is not dict or type(code.get('id')) is not str for code in rows):
            raise ValueError('Categories require known member codes.')
        codes = {code['id']: code for code in rows}
        if len(codes) != len(rows):
            raise ValueError('Category member code IDs must be unique.')
        gate = result['synthesis_gate']
        if type(gate) is not dict or gate.get('reviewer_kind') not in {'researcher', 'assistant_development_review'}:
            raise ValueError('Categories require an explicit synthesis review gate.')
        allowed = gate.get('code_ids')
        if (type(allowed) is not list or not 2 <= len(allowed) <= 6
                or any(type(cid) is not str or cid not in codes for cid in allowed)
                or len(set(allowed)) != len(allowed)):
            raise ValueError('The category gate must select 2..6 unique known code IDs.')
        if any(codes[cid].get('status') not in {'scope_passed', 'definition_drafted', 'definition_revised'} for cid in allowed):
            raise ValueError('Gated category members need completed definitions, not semantic approval.')
        for cid in allowed:
            code = codes[cid]
            ids, evidence = code['support_span_ids'], code['evidence']
            if (type(ids) is not list or not ids or type(evidence) is not list
                    or any(type(sid) is not str or sid not in result['registry'] for sid in ids)
                    or _digest([result['registry'][sid] for sid in ids]) != _digest(evidence)
                    or any(entry['unit_id'] != code['unit_id'] for entry in evidence)):
                raise ValueError('Gated member evidence must retain its exact original source registry selections.')
        seen = set()
        for category in categories:
            if type(category) is not dict or category.get('kind') != 'category':
                raise ValueError('Category kind must remain category, separate from codes and themes.')
            proposal = dict(decision='group', **{field: category[field] for field in (
                'label', 'shared_meaning', 'uncertainty', 'code_ids', 'qualification_code_ids', 'counterexample_code_ids')})
            validate_synthesis(proposal, allowed)
            expected_id = 'CAT-' + _digest([category['code_ids'], category['label']])[:16]
            if type(category.get('id')) is not str or category['id'] != expected_id or category['id'] in seen:
                raise ValueError('Category ID differs from its host member/label identity or is duplicated.')
            seen.add(category['id'])
            for role, key in (('evidence', 'code_ids'), ('qualifications', 'qualification_code_ids'),
                              ('counterexamples', 'counterexample_code_ids')):
                expected = [entry for cid in category[key] for entry in codes[cid]['evidence']]
                if type(category.get(role)) is not list or _digest(category[role]) != _digest(expected):
                    raise ValueError('Category role evidence must exactly copy its member codes in order, including cross-coded duplicates.')
            if len({entry['source_hash'] for cid in category['code_ids'] for entry in codes[cid]['evidence']}) < 2:
                raise ValueError('Category support requires at least two distinct source contents.')
    except (KeyError, TypeError, AttributeError) as error:
        raise ValueError('Category evidence or gate metadata is malformed or incomplete.') from error

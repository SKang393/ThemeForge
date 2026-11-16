"""Closed contracts for staged local drafting, not semantic-quality validation.

The host supplies IDs from its verified generation_schema source registry and
retains immutable support selections between stages. These functions do not
resolve quotations, alter source records or establish interpretive validity.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from copy import deepcopy
import json
import re


VERSION = 'themeforge.staged-codebook.v1'
MAX_RESPONSE_BYTES = 16_384
MAX_SELECTIONS = 6
MAX_SYNTHESIS_REASON_CHARS = 1600
_ID = re.compile(r'^[A-Za-z][A-Za-z0-9_.:-]{0,63}$')
_PLACEHOLDERS = frozenset(('true', 'false', 'yes', 'no', 'none', 'n-a', 'n/a', 'na'))


def _known_ids(values, name):
    if isinstance(values, (str, bytes)) or not isinstance(values, Iterable):
        raise ValueError(f'{name} must be an iterable of known IDs.')
    result = list(values.keys() if isinstance(values, Mapping) else values)
    if any(type(value) is not str or not _ID.fullmatch(value) for value in result):
        raise ValueError(f'{name} must contain nonempty short string IDs (at most 64 characters).')
    if len(set(result)) != len(result):
        raise ValueError(f'Duplicate IDs in {name}.')
    return sorted(result)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'Duplicate JSON key: {key}')
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError(f'Nonstandard JSON constant: {value}')


def _read(raw):
    try:
        if type(raw) is dict:
            encoded = json.dumps(raw, ensure_ascii=False, allow_nan=False).encode('utf-8')
        elif type(raw) is str:
            encoded = raw.encode('utf-8')
        elif type(raw) is bytes:
            encoded = raw
        else:
            raise ValueError('Response must be a JSON object, JSON string or UTF-8 bytes.')
        if len(encoded) > MAX_RESPONSE_BYTES:
            raise ValueError(f'Response exceeds {MAX_RESPONSE_BYTES} bytes.')
        # Do not coerce tuple selections or non-string object keys supplied by
        # Python callers into valid JSON types before enforcing the contract.
        value = deepcopy(raw) if type(raw) is dict else json.loads(
            encoded.decode('utf-8'), object_pairs_hook=_unique_object,
            parse_constant=_invalid_constant)
    except (TypeError, RecursionError) as exc:
        raise ValueError('Response is not a bounded JSON object.') from exc
    if type(value) is not dict:
        raise ValueError('Response must be a JSON object.')
    return value


def _text(value, name, limit, *, substantive=False):
    if type(value) is not str or not value.strip() or len(value) > limit:
        raise ValueError(f'{name} must be a nonblank string of at most {limit} characters.')
    if substantive and value.strip().casefold() in _PLACEHOLDERS:
        raise ValueError(f'{name} cannot be a placeholder such as True/False, Yes/No, None or n-a.')
    return value


def _fields(value, decision, fields):
    expected = {'decision', *fields}
    if set(value) != expected or value.get('decision') != decision:
        raise ValueError(f'{decision} must contain exactly: {", ".join(sorted(expected))}.')


def _selection(value, allowed, name, minimum=0):
    if type(value) is not list or not minimum <= len(value) <= MAX_SELECTIONS:
        raise ValueError(f'{name} must be an array with {minimum} to {MAX_SELECTIONS} IDs.')
    if any(type(item) is not str or item not in allowed for item in value):
        raise ValueError(f'Unknown or incorrectly typed ID in {name}.')
    if len(value) != len(set(value)):
        raise ValueError(f'Duplicate IDs in {name}.')
    return set(value)


def _text_schema(limit):
    return {'type': 'string', 'minLength': 1, 'maxLength': limit}


def _selection_schema(ids, minimum=0):
    # Empty optional selections remain valid without an invalid empty enum.
    return {'type': 'array', 'minItems': minimum,
            'maxItems': min(MAX_SELECTIONS, len(ids)), 'uniqueItems': True,
            'items': {'type': 'string', 'enum': list(ids)} if ids else {'type': 'string'}}


def _branch(decision, properties):
    fields = {'decision': {'type': 'string', 'const': decision}, **properties}
    return {'type': 'object', 'additionalProperties': False,
            'required': list(fields), 'properties': fields}


def _schema(positive, negative):
    # Each branch is a complete, independently closed object for grammar engines.
    # Cross-field disjointness, total selection caps, nonblank prose and placeholder
    # rejection also require the corresponding host validator after decoding.
    return {'oneOf': [positive, negative]}


def decision_schema(evidence_ids):
    """Stage 1: propose one source-bound claim or abstain, never both."""
    ids = _known_ids(evidence_ids, 'evidence IDs')
    return _schema(_branch('code', {'label': _text_schema(120), 'claim': _text_schema(600),
                                   'evidence_ids': _selection_schema(ids, 1)}),
                   _branch('abstain', {'reason': _text_schema(400)}))


def validate_decision(raw, allowed_ids):
    allowed = set(_known_ids(allowed_ids, 'evidence IDs'))
    value = _read(raw)
    if value.get('decision') == 'code':
        _fields(value, 'code', ('label', 'claim', 'evidence_ids'))
        _text(value['label'], 'label', 120)
        _text(value['claim'], 'claim', 600)
        _selection(value['evidence_ids'], allowed, 'evidence_ids', 1)
    elif value.get('decision') == 'abstain':
        _fields(value, 'abstain', ('reason',))
        _text(value['reason'], 'reason', 400)
    else:
        raise ValueError('decision must be code or abstain.')
    return deepcopy(value)


def multi_decision_schema(evidence_ids, referent_ids=(), max_codes=3):
    """One to three distinct meanings, or abstain; context is not support."""
    ids = _known_ids(evidence_ids, 'evidence IDs')
    refs = _known_ids(referent_ids, 'referent IDs')
    if type(max_codes) is not int or not 1 <= max_codes <= 3:
        raise ValueError('The per-turn code cap must be in 1..3.')
    candidate = {'type': 'object', 'additionalProperties': False,
                 'required': ['label', 'claim', 'evidence_ids', 'referent_ids'],
                 'properties': {'label': _text_schema(80), 'claim': _text_schema(360),
                                'evidence_ids': _selection_schema(ids, 1),
                                'referent_ids': {**_selection_schema(refs), 'maxItems': min(2, len(refs))}}}
    return _schema(_branch('codes', {'codes': {'type': 'array', 'minItems': 1, 'maxItems': max_codes, 'items': candidate}}),
                   _branch('abstain', {'reason': _text_schema(400)}))


def validate_multi_decision(raw, allowed_ids, referent_ids=(), max_codes=3):
    allowed = set(_known_ids(allowed_ids, 'evidence IDs'))
    refs = set(_known_ids(referent_ids, 'referent IDs'))
    if type(max_codes) is not int or not 1 <= max_codes <= 3:
        raise ValueError('The per-turn code cap must be in 1..3.')
    value = _read(raw)
    if value.get('decision') == 'abstain':
        _fields(value, 'abstain', ('reason',))
        _text(value['reason'], 'reason', 400)
    elif value.get('decision') == 'codes':
        _fields(value, 'codes', ('codes',))
        if type(value['codes']) is not list or not 1 <= len(value['codes']) <= max_codes:
            raise ValueError(f'codes must contain 1..{max_codes} distinct candidates.')
        seen_labels, seen_claims = set(), set()
        for code in value['codes']:
            if type(code) is not dict or set(code) != {'label', 'claim', 'evidence_ids', 'referent_ids'}:
                raise ValueError('Each code needs only label, claim, evidence_ids and referent_ids.')
            _text(code['label'], 'label', 80, substantive=True)
            _text(code['claim'], 'claim', 360, substantive=True)
            _selection(code['evidence_ids'], allowed, 'evidence_ids', 1)
            selected_refs = _selection(code['referent_ids'], refs, 'referent_ids')
            if len(selected_refs) > 2:
                raise ValueError('Select at most two referent contexts per code.')
            label = ' '.join(code['label'].casefold().split())
            claim = ' '.join(code['claim'].casefold().split())
            if label in seen_labels or claim in seen_claims:
                raise ValueError('Repeated candidate labels or claims do not establish multiple meanings.')
            seen_labels.add(label); seen_claims.add(claim)
    else:
        raise ValueError('decision must be codes or abstain.')
    return deepcopy(value)


def _scope_ids(evidence_ids, support_ids):
    allowed = set(_known_ids(evidence_ids, 'evidence IDs'))
    support = set(_known_ids(support_ids, 'support IDs'))
    if not support <= allowed:
        raise ValueError('Support IDs must be known evidence IDs supplied by the host.')
    return allowed, support


def scope_schema(evidence_ids, support_ids):
    """Stage 2: immutable support stays outside the generated definition object."""
    allowed, support = _scope_ids(evidence_ids, support_ids)
    remaining = sorted(allowed - support)
    return _schema(_branch('define', {
        'definition': _text_schema(600), 'inclusion': _text_schema(400),
        'exclusion': _text_schema(400), 'qualification_ids': _selection_schema(remaining),
        'counterexample_ids': _selection_schema(remaining), 'uncertainty': _text_schema(400)}),
        _branch('insufficient', {'reason': _text_schema(400)}))


def validate_scope(raw, allowed_ids, support_ids):
    allowed, support = _scope_ids(allowed_ids, support_ids)
    value = _read(raw)
    if value.get('decision') == 'define':
        _fields(value, 'define', ('definition', 'inclusion', 'exclusion',
                                  'qualification_ids', 'counterexample_ids', 'uncertainty'))
        for name, limit in (('definition', 600), ('inclusion', 400), ('exclusion', 400), ('uncertainty', 400)):
            _text(value[name], name, limit, substantive=True)
        qualifications = _selection(value['qualification_ids'], allowed, 'qualification_ids')
        counterexamples = _selection(value['counterexample_ids'], allowed, 'counterexample_ids')
        if qualifications & counterexamples or (qualifications | counterexamples) & support:
            raise ValueError('Support, qualification and counterexample roles must be disjoint.')
    elif value.get('decision') == 'insufficient':
        _fields(value, 'insufficient', ('reason',))
        _text(value['reason'], 'reason', 400)
    else:
        raise ValueError('decision must be define or insufficient.')
    return deepcopy(value)


def grounded_scope_schema(evidence_ids, support_ids):
    """Source-bound definition with host-owned support and no invented boundaries.

    The caller must run validate_grounded_scope after decoding to enforce the
    combined selection cap, role disjointness and substantive definition text.
    """
    allowed, support = _scope_ids(evidence_ids, support_ids)
    remaining = sorted(allowed - support)
    return _schema(_branch('define', {
        'definition': _text_schema(600),
        'qualification_ids': _selection_schema(remaining),
        'counterexample_ids': _selection_schema(remaining)}),
        _branch('insufficient', {'reason': _text_schema(400)}))


def validate_grounded_scope(raw, allowed_ids, support_ids):
    """Validate structure and source roles, without claiming semantic validity."""
    allowed, support = _scope_ids(allowed_ids, support_ids)
    value = _read(raw)
    if value.get('decision') == 'define':
        _fields(value, 'define', ('definition', 'qualification_ids', 'counterexample_ids'))
        _text(value['definition'], 'definition', 600, substantive=True)
        qualifications = _selection(value['qualification_ids'], allowed, 'qualification_ids')
        counterexamples = _selection(value['counterexample_ids'], allowed, 'counterexample_ids')
        if qualifications & counterexamples or (qualifications | counterexamples) & support:
            raise ValueError('Support, qualification and counterexample roles must be disjoint.')
        if len(qualifications) + len(counterexamples) > MAX_SELECTIONS:
            raise ValueError('Grounded scope may select at most 6 IDs across qualification and counterexample roles.')
    elif value.get('decision') == 'insufficient':
        _fields(value, 'insufficient', ('reason',))
        _text(value['reason'], 'reason', 400)
    else:
        raise ValueError('decision must be define or insufficient.')
    return deepcopy(value)


def synthesis_schema(code_ids):
    """Stage 3: category proposal only; cross-source/semantic gates belong to caller."""
    ids = _known_ids(code_ids, 'code IDs')
    return _schema(_branch('group', {
        'label': _text_schema(120), 'shared_meaning': _text_schema(600),
        'code_ids': _selection_schema(ids, 2),
        'qualification_code_ids': _selection_schema(ids),
        'counterexample_code_ids': _selection_schema(ids), 'uncertainty': _text_schema(400)}),
        # Let the decoder finish a refusal instead of closing its string at a
        # character boundary. Raw-response/runtime budgets still bound generation;
        # the host enforces the generous explanation limit after decoding.
        _branch('no_group', {'reason': {'type': 'string', 'minLength': 1}}))


def validate_synthesis(raw, allowed_code_ids):
    allowed = set(_known_ids(allowed_code_ids, 'code IDs'))
    value = _read(raw)
    if value.get('decision') == 'group':
        _fields(value, 'group', ('label', 'shared_meaning', 'code_ids',
                                 'qualification_code_ids', 'counterexample_code_ids', 'uncertainty'))
        _text(value['label'], 'label', 120)
        _text(value['shared_meaning'], 'shared_meaning', 600, substantive=True)
        _text(value['uncertainty'], 'uncertainty', 400, substantive=True)
        roles = [_selection(value[name], allowed, name, 2 if name == 'code_ids' else 0)
                 for name in ('code_ids', 'qualification_code_ids', 'counterexample_code_ids')]
        if any(roles[i] & roles[j] for i in range(3) for j in range(i)):
            raise ValueError('Category support, qualification and counterexample roles must be disjoint.')
        if sum(map(len, roles)) > MAX_SELECTIONS:
            raise ValueError('Category proposal may select at most 6 IDs across all roles.')
    elif value.get('decision') == 'no_group':
        _fields(value, 'no_group', ('reason',))
        _text(value['reason'], 'reason', MAX_SYNTHESIS_REASON_CHARS)
    else:
        raise ValueError('decision must be group or no_group.')
    return deepcopy(value)

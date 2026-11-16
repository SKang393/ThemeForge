"""Experimental source/role separation, with no inference or coding side effects.

Imported turn labels describe the transcript, not verified people or the actor of
every embedded proposition. Role and action-status readings are append-only AI or
researcher annotations. Exact citations establish correspondence, not meaning.
"""
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
import re

from .source_attribution import resolve_attribution, LEGACY_POLICY, ROLE_HEADER_POLICY

LEGACY_VERSION = 'themeforge.experimental-role-evidence.v1'
VERSION = 'themeforge.experimental-role-evidence.v2'
ROLE_FIELDS = ('addressee', 'attributed_actor', 'perspective_holder')
READING_FIELDS = ('speech_form', 'action_status')
STATUSES = {'unknown', 'proposed', 'ambiguous', 'abstain'}
_PRONOUN = re.compile(r"(?:i|me|my|mine|we|us|our|ours|you|your|yours|he|him|his|she|her|hers|they|them|their|theirs|it|its|this|that|these|those)", re.I)


def _hash(value):
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                             separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _text(value, name, limit=1200):
    if type(value) is not str or not value.strip() or len(value) > limit:
        raise ValueError(f'{name} requires nonblank text of at most {limit} characters.')


def _fields(value, keys, name):
    if type(value) is not dict or set(value) != set(keys):
        raise ValueError(f'{name} has unexpected or missing fields.')


def _source(source):
    if (type(source) is not dict or type(source.get('text')) is not str
            or type(source.get('source_name')) is not str or not source['source_name']
            or sha256(source['text'].encode()).hexdigest() != source.get('source_hash')):
        raise ValueError('Source name/hash/text proof failed.')


def source_span(source, start, end):
    """Select literal text; selection confers no role or proposition meaning."""
    _source(source)
    if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(source['text']):
        raise ValueError('Source span requires valid character offsets.')
    return dict(source_name=source['source_name'], source_hash=source['source_hash'],
                source_start=start, source_end=end, text=source['text'][start:end])


def _prove(span, source):
    _fields(span, ('source_name', 'source_hash', 'source_start', 'source_end', 'text'), 'Exact source span')
    if span != source_span(source, span['source_start'], span['source_end']):
        raise ValueError('Source span does not match original text/hash/offsets.')


def _contains(outer, inner):
    return (outer['source_name'] == inner['source_name'] and outer['source_hash'] == inner['source_hash']
            and outer['source_start'] <= inner['source_start'] < inner['source_end'] <= outer['source_end'])


def _speaker(unit, source, version):
    attribution = resolve_attribution(unit, source,
        policy=LEGACY_POLICY if version == LEGACY_VERSION else ROLE_HEADER_POLICY)
    label = attribution['speaker_attribution']
    status = 'ambiguous' if label == 'multiple' else 'unknown' if label == 'unknown' else 'explicit_source_label'
    evidence = {}
    for part in attribution['partitions']:
        span = part['speaker_label_evidence']
        if span:
            evidence[(span['source_start'], span['source_end'])] = span
    value = dict(status=status, source_local_label=label if status == 'explicit_source_label' else None,
                label_evidence=list(evidence.values()),
                observed_labels=attribution['source_labeled_speakers'],
                origin='deterministic_source_structure', person_identity_verified=False,
                reported_actor_or_assertion_validated=False)
    if version == VERSION:
        value.update(structural_role=attribution['speaker_role'], header_policy=ROLE_HEADER_POLICY)
    return value


def _imported(unit, source, contexts, version):
    _source(source)
    target = source_span(source, unit['source_start'], unit['source_end'])
    if any(unit.get(k) != v for k, v in target.items()):
        raise ValueError('Target must match the original source, including its name and hash.')
    if type(contexts) is not list or len(contexts) > 8:
        raise ValueError('At most eight explicit context spans; never silently trim.')
    speaker = _speaker(unit, source, version)
    contextual = []
    for context in contexts:
        span = {k: context[k] for k in target}
        _prove(span, source)
        if span['source_start'] < target['source_end'] and target['source_start'] < span['source_end']:
            raise ValueError('Context must be separate from target evidence.')
        contextual.append(dict(span=span, source_speaker=_speaker(span, source, version),
                               use='context_only_not_target_assertion_evidence'))
    saved_label = unit.get('speaker')
    saved_id = unit.get('parent_unit_id', unit.get('id'))
    for name, value in (('Saved speaker label', saved_label), ('Saved unit id', saved_id)):
        if value is not None:
            _text(value, name, 256)
    return dict(target=target, saved_unit_id=saved_id,
                saved_speaker_label=saved_label, source_speaker=speaker,
                saved_label_conflict=(speaker['status'] == 'explicit_source_label'
                                      and saved_label not in (None, speaker['source_local_label'])),
                contexts=contextual, origin='source_structure_with_separate_saved_metadata',
                notice='Source-local labels are not global identities, addressees, actors, belief owners or observed events.')


def build_role_evidence(unit, source, *, contexts=(), version=VERSION):
    """Build evidence only. All interpretive roles remain unknown by default."""
    if version not in (VERSION, LEGACY_VERSION):
        raise ValueError('Unsupported role-evidence version.')
    value = _imported(unit, source, list(contexts), version)
    return dict(version=version, imported=value, imported_sha256=_hash(value), revisions=[],
                semantic_validation=False, training_eligible=False)


def unresolved_role(reason='No role interpretation supplied.', *, status='unknown', evidence=(), alternatives=()):
    return dict(status=status, mentions=[], evidence=deepcopy(list(evidence)),
                rationale=reason, alternatives=list(alternatives))


def unresolved_reading(reason='No discourse/action interpretation supplied.', *, status='unknown', evidence=(), alternatives=()):
    return dict(status=status, description=None, evidence=deepcopy(list(evidence)),
                scope_evidence=[], rationale=reason, alternatives=list(alternatives))


def new_assertion(assertion_id, span, *, parent_id=None):
    """A source-linked proposition scope, not a thematic code or generated claim."""
    return dict(id=assertion_id, span=deepcopy(span), parent_id=parent_id,
                roles={k: unresolved_role() for k in ROLE_FIELDS},
                **{k: unresolved_reading() for k in READING_FIELDS}, limits=[])


def _judgment(value, source, allowed, assertion_span, *, role, ancestors=()):
    value_field = 'mentions' if role else 'description'
    fields = ['status', value_field, 'evidence', 'rationale', 'alternatives']
    if not role:
        fields.append('scope_evidence')
    _fields(value, fields, 'Interpretive judgment')
    if type(value['status']) is not str or value['status'] not in STATUSES:
        raise ValueError('Interpretations require proposed, unknown, ambiguous or abstain status.')
    _text(value['rationale'], 'Interpretation rationale')
    if type(value['alternatives']) is not list or len(value['alternatives']) > 8:
        raise ValueError('Alternatives must be a bounded list.')
    for alternative in value['alternatives']:
        _text(alternative, 'Alternative')
    if value['status'] == 'ambiguous' and len(value['alternatives']) < 2:
        raise ValueError('Ambiguous judgments retain at least two alternative readings.')
    evidence = value['evidence']
    if type(evidence) is not list or len(evidence) > 16:
        raise ValueError('Judgment evidence must be a bounded list of exact spans.')
    for span in evidence:
        _prove(span, source)
        if not any(_contains(a, span) for a in allowed):
            raise ValueError('Judgment evidence lies outside the selected target/context/header.')
    if value['status'] in ('proposed', 'ambiguous') and not any(_contains(assertion_span, s) for s in evidence):
        raise ValueError('Proposed/ambiguous judgment requires evidence inside its target assertion.')
    if not role and any(not _contains(assertion_span, s) for s in evidence):
        raise ValueError('Discourse/action evidence must be within its target assertion; context is not support.')
    if not role:
        scope = value['scope_evidence']
        if type(scope) is not list or len(scope) > 16:
            raise ValueError('Scope evidence must be a bounded list.')
        for span in scope:
            _prove(span, source)
            if not any(_contains(parent, span) for parent in ancestors):
                raise ValueError('Scope evidence must cite an explicitly linked ancestor in the same target.')
    if role:
        if type(value['mentions']) is not list or len(value['mentions']) > 4:
            raise ValueError('Role mentions must be a bounded list of literal source anchors.')
        if value['status'] != 'proposed' and value['mentions']:
            raise ValueError('Unresolved roles cannot quietly select an identity.')
        if value['status'] == 'proposed' and not value['mentions']:
            raise ValueError('Proposed roles need literal source mentions, not invented names.')
        for mention in value['mentions']:
            _prove(mention, source)
            if mention not in evidence:
                raise ValueError('Each literal role mention must also be cited as evidence.')
            if _PRONOUN.fullmatch(mention['text'].strip()):
                raise ValueError('Pronouns alone cannot resolve an identity; retain unknown/ambiguous.')
    elif value['status'] == 'proposed':
        _text(value['description'], 'Proposed discourse/action reading')
    elif value['description'] is not None:
        raise ValueError('Unresolved readings have no selected interpretation.')


def _assertions(assertions, imported, source):
    if type(assertions) is not list or not 1 <= len(assertions) <= 32:
        raise ValueError('A revision requires one to 32 source-linked scopes, without truncation.')
    target = imported['target']
    allowed = [target, *imported['source_speaker']['label_evidence']]
    for context in imported['contexts']:
        allowed.extend([context['span'], *context['source_speaker']['label_evidence']])
    prior = {}
    for item in assertions:
        _fields(item, ('id', 'span', 'parent_id', 'roles', *READING_FIELDS, 'limits'), 'Assertion')
        _text(item['id'], 'Assertion id', 64)
        if item['id'] in prior:
            raise ValueError('Duplicate assertion id.')
        _prove(item['span'], source)
        if not _contains(target, item['span']):
            raise ValueError('An assertion must cite the target, never another speaker context.')
        parent = item['parent_id']
        if parent is not None and (parent not in prior or not _contains(prior[parent]['span'], item['span'])):
            raise ValueError('An embedded perspective requires an earlier containing parent scope.')
        ancestors = []
        while parent is not None:
            ancestors.append(prior[parent]['span'])
            parent = prior[parent]['parent_id']
        _fields(item['roles'], ROLE_FIELDS, 'Separated roles')
        for role in ROLE_FIELDS:
            _judgment(item['roles'][role], source, allowed, item['span'], role=True)
        for field in READING_FIELDS:
            _judgment(item[field], source, allowed, item['span'], role=False, ancestors=ancestors)
        if type(item['limits']) is not list or len(item['limits']) > 16:
            raise ValueError('Limits must be a bounded list of exact source spans.')
        for span in item['limits']:
            _prove(span, source)
            if not _contains(item['span'], span):
                raise ValueError('Condition/modality/frequency limits must cite the scoped target.')
        prior[item['id']] = item


def validate_role_evidence(record, source):
    _fields(record, ('version', 'imported', 'imported_sha256', 'revisions',
                     'semantic_validation', 'training_eligible'), 'Role evidence record')
    if record['version'] not in (VERSION, LEGACY_VERSION) or record['semantic_validation'] is not False or record['training_eligible'] is not False:
        raise ValueError('Experimental evidence cannot be promoted to semantic truth or training eligibility.')
    imported = record['imported']
    unit = dict(imported['target'], id=imported['saved_unit_id'], speaker=imported['saved_speaker_label'])
    rebuilt = _imported(unit, source, [c['span'] for c in imported['contexts']], record['version'])
    if imported != rebuilt or record['imported_sha256'] != _hash(rebuilt):
        raise ValueError('Imported evidence was changed or does not reproduce from source.')
    if type(record['revisions']) is not list or len(record['revisions']) > 128:
        raise ValueError('Revision history exceeds the experimental bound; export without dropping history.')
    previous = None
    for revision in record['revisions']:
        _fields(revision, ('id', 'previous_revision_id', 'origin', 'assertions'), 'Revision')
        origin = revision['origin']
        _fields(origin, ('kind', 'author', 'method', 'created_utc', 'authorship_verified'), 'Annotation origin')
        if origin['kind'] not in ('ai_inference', 'researcher_annotation') or origin['authorship_verified'] is not False:
            raise ValueError('Annotation origin must stay separate from imported facts; authorship is declared, not certified.')
        for key in ('author', 'method', 'created_utc'):
            _text(origin[key], key)
        if revision['previous_revision_id'] != previous:
            raise ValueError('Revision history must preserve its previous-record link.')
        if revision['id'] != 'RE-' + _hash({k: v for k, v in revision.items() if k != 'id'})[:24]:
            raise ValueError('Revision content digest changed.')
        _assertions(revision['assertions'], imported, source)
        previous = revision['id']
    return record


def append_interpretation(record, source, assertions, *, origin_kind, author, method):
    """Append a full interpretation snapshot; never revise imported facts or old AI text."""
    validate_role_evidence(record, source)
    value = deepcopy(record)
    revision = dict(previous_revision_id=value['revisions'][-1]['id'] if value['revisions'] else None,
                    origin=dict(kind=origin_kind, author=author, method=method,
                                created_utc=datetime.now(timezone.utc).isoformat(), authorship_verified=False),
                    assertions=deepcopy(assertions))
    revision['id'] = 'RE-' + _hash(revision)[:24]
    value['revisions'].append(revision)
    validate_role_evidence(value, source)
    return value


def experimental_input(record, source):
    """Source-only proposal for a future controlled test; never includes AI/researcher answers."""
    validate_role_evidence(record, source)
    # Saved labels and IDs are caller metadata, not verbatim source evidence.
    # Keep them in the review record, never let them become prompt answers.
    structural = {k: deepcopy(record['imported'][k]) for k in ('target', 'source_speaker', 'contexts')}
    return dict(version=record['version'], experimental=True, imported=structural,
                role_slots={k: unresolved_role() for k in ROLE_FIELDS},
                **{k: unresolved_reading() for k in READING_FIELDS},
                annotation_history_included=False, source_only=True,
                notice='Evidence representation only. No model call, conceptual labels, identity inference or theme acceptance.')


def export_role_evidence(record, source):
    """JSON review export keeps declared authorship and all revisions visible."""
    validate_role_evidence(record, source)
    return json.dumps(record, ensure_ascii=False, indent=2, allow_nan=False) + '\n'

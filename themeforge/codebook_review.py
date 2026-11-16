"""Explicit, audited review edits without automatic semantic approval."""
from collections.abc import Sequence
from copy import deepcopy
from datetime import datetime, timezone

from .local_codebook import has_completed_definition, validate_result_sources
from .model_pilot import _digest
from .codebook_contracts import _text


def _review_target(result, code_id, reviewer_kind, reason, *, allow_incomplete=False):
    validate_result_sources(result)
    if reviewer_kind not in ('researcher', 'assistant_development_review'):
        raise ValueError('A researcher or assistant_development_review is required.')
    if type(reason) is not str or not reason.strip() or len(reason) > 2000:
        raise ValueError('Provide an explicit nonblank review reason of at most 2000 characters.')
    if (result.get('categories') or result.get('reviewed_categories') or result.get('interpretive_themes')
            or any(key.startswith('synthesis') for key in result)
            or any(call.get('stage') == 'synthesis' for call in result.get('calls', []))):
        raise ValueError('Revisions require a draft before synthesis or category creation; reopen the original raw draft.')
    if type(code_id) is not str:
        raise ValueError('Select a known candidate code ID.')
    codes = {code['id']: code for code in result['codes']}
    if code_id not in codes or not (has_completed_definition(codes[code_id])
            or (allow_incomplete and codes[code_id].get('status') in {'scope_insufficient', 'scope_rejected', 'decision_only', 'scope_review_required'})):
        raise ValueError('Select a candidate with a completed definition.')
    if type(result.get('review_history', [])) is not list:
        raise ValueError('Review history must be an event list.')
    return codes[code_id]


def _finish_revision(result, code, *, refresh=True):
    # Warnings are recalculated from the current prose and exact source context;
    # an explicit edit never clears semantic warnings or promotes validity.
    from .local_codebook import refresh_meaning_checks
    if refresh:
        refresh_meaning_checks(result, code)
    if 'uncovered_spans' in result:
        selected = {sid for candidate in result['codes'] for sid in candidate['support_span_ids']}
        result['uncovered_spans'] = [dict(span_id=sid, **deepcopy(entry)) for sid, entry in result['registry'].items() if sid not in selected]
    if refresh:
        code['review_status'] = 'unreviewed_machine_draft'
    result['review_status'] = 'unreviewed_machine_draft'
    result['semantic_quality'] = 'not_human_validated'
    result['result_sha256'] = _digest({key: value for key, value in result.items() if key != 'result_sha256'})
    if 'candidate_review_workflow' in result:
        import json
        if len(json.dumps(result, ensure_ascii=False, indent=2).encode('utf-8')) > 16 * 1024**2:
            raise ValueError('Reviewed draft exceeds the native 16 MiB reopen limit; preserve the original and use a smaller separate branch.')
    validate_result_sources(result)
    return result


def revise_interpretation(result, code_id, changes, *, reviewer_kind, reason, reviewer_name=None):
    """Revise only explicit prose fields, preserving model wording and lineage.

    Assistant authorship remains separate from researcher authorship on each
    field and in every event, including repeated edits. This is not acceptance
    of an interpretation and performs neither inference nor file writes.
    """
    original = _review_target(result, code_id, reviewer_kind, reason,
                             allow_incomplete=type(changes) is dict and 'definition' in changes)
    if reviewer_name is not None:
        _author_details(reviewer_kind, reviewer_name, reason)
    repairs_definition = original.get('status') in {'scope_insufficient', 'scope_rejected', 'decision_only', 'scope_review_required'}
    limits = {'label': 120, 'claim': 600, 'definition': 600}
    if type(changes) is not dict or not changes or not set(changes) <= set(limits):
        raise ValueError('Provide explicit changes limited to label, claim and definition.')
    for field, value in changes.items():
        _text(value, field, limits[field], substantive=True)
    changes = {field: value for field, value in changes.items()
               if original[field] != value or (repairs_definition and field == 'definition')}
    if not changes:
        raise ValueError('At least one interpretation field must change.')
    revised = deepcopy(result)
    code = next(row for row in revised['codes'] if row['id'] == code_id)
    if not code.get('split_parent_id'):
        code.setdefault('original_model_prose', {field: deepcopy(original[field]) for field in limits})
    origins = code.setdefault('interpretation_field_origins', {
        field: code.get('field_origins', {}).get(field, 'local_model') for field in limits})
    old_values = {field: deepcopy(original[field]) for field in changes}
    old_origins = {field: origins[field] for field in changes}
    for field, value in changes.items():
        code[field] = value
        origins[field] = reviewer_kind
        code.setdefault('field_origins', {})[field] = reviewer_kind
    if repairs_definition:
        from .local_codebook import _HOST_SCOPE_NOTICES
        code.update(deepcopy(_HOST_SCOPE_NOTICES), status='definition_revised',
                    scope_origin=reviewer_kind, scope_boundary_status='not_established')
        code['field_origins'].update(inclusion='host_proposed_analyst_rule',
                                     exclusion='host_unestablished_notice', uncertainty='host_review_notice')
    revised.setdefault('review_history', []).append({
        'event': 'interpretation_revised', 'code_id': code_id, 'reviewer_kind': reviewer_kind,
        'reason': reason.strip(), 'before_result_sha256': result['result_sha256'],
        'old_values': old_values, 'new_values': deepcopy(changes), 'old_field_origins': old_origins,
        'new_field_origins': {field: reviewer_kind for field in changes},
        'old_status': original.get('status'), 'new_status': code.get('status'),
        'old_scope_origin': original.get('scope_origin'), 'new_scope_origin': code.get('scope_origin'),
    })
    if reviewer_name is not None:
        revised['review_history'][-1]['reviewer_name'] = reviewer_name.strip()
    return _finish_revision(revised, code)


def _role_ids(result, code):
    roles = {'support': list(code['support_span_ids'])}
    for role in ('qualifications', 'counterexamples'):
        roles[role] = [next(sid for sid, entry in result['registry'].items() if entry == span)
                       for span in code[role]]
    return roles


def _role_origins(code):
    recorded = code.get('evidence_role_origins', {})
    return {role: recorded.get(role, code.get('evidence_selection_origin', 'local_model')
                              if role == 'support' else 'local_model')
            for role in ('support', 'qualifications', 'counterexamples')}


def _original_role_ids(result, code, current, origins):
    if 'split_origin_evidence_roles' in code:
        return deepcopy(code['split_origin_evidence_roles'])
    if 'original_model_evidence_roles' in code:
        return deepcopy(code['original_model_evidence_roles'])
    original = deepcopy(current)
    for role in current:
        if role == 'support' and 'original_model_support_span_ids' in code:
            original[role] = list(code['original_model_support_span_ids'])
            continue
        found = False
        for event in result.get('review_history', []):
            if event.get('code_id') != code['id']:
                continue
            if role in event.get('old_roles', {}):
                if event.get('old_role_origins', {}).get(role, 'local_model') != 'local_model':
                    raise ValueError('Original model evidence roles are unavailable for a previously reviewed selection.')
                original[role] = list(event['old_roles'][role]); found = True
                break
            if role == 'support' and 'old_support_span_ids' in event:
                original[role] = list(event['old_support_span_ids']); found = True
                break
        if not found and origins[role] != 'local_model':
            raise ValueError('Original model evidence roles are unavailable for a previously reviewed selection.')
    return original


def _revise_evidence_roles(result, code_id, roles, reviewer_kind, reason, *, legacy_support=False, reviewer_name=None):
    original = _review_target(result, code_id, reviewer_kind, reason)
    if reviewer_name is not None:
        _author_details(reviewer_kind, reviewer_name, reason)
    if type(roles) is not dict or not roles or not set(roles) <= {'support', 'qualifications', 'counterexamples'}:
        raise ValueError('Provide support, qualifications or counterexamples selections.')
    current = _role_ids(result, original)
    selected = deepcopy(current)
    for role, ids in roles.items():
        if type(ids) is not list or not (1 if role == 'support' else 0) <= len(ids) <= 6:
            raise ValueError('Support needs 1..6 IDs; other roles need 0..6 IDs.')
        if any(type(sid) is not str or sid not in result['registry']
               or result['registry'][sid]['unit_id'] != original['unit_id'] for sid in ids):
            raise ValueError('Evidence roles require exact registry IDs from the original source unit.')
        if len(set(ids)) != len(ids):
            raise ValueError('Evidence role IDs must be unique.')
        selected[role] = list(ids)
    if len(selected['qualifications']) + len(selected['counterexamples']) > 6:
        raise ValueError('Qualifications and counterexamples may select at most 6 IDs combined.')
    all_ids = [sid for ids in selected.values() for sid in ids]
    if len(set(all_ids)) != len(all_ids):
        raise ValueError('Support, qualifications and counterexamples must be disjoint.')
    # Role membership is a selection, not a ranked list. Reordering the same
    # IDs must not transfer authorship or create an apparent evidence revision.
    changed = [role for role in roles if set(selected[role]) != set(current[role])]
    if not changed:
        raise ValueError('At least one evidence role must change.')
    old_origins = _role_origins(original)
    model_roles = _original_role_ids(result, original, current, old_origins)
    revised = deepcopy(result)
    code = next(item for item in revised["codes"] if item["id"] == code_id)
    if not code.get('split_parent_id'):
        code.setdefault('original_model_evidence_roles', model_roles)
    code['evidence_role_origins'] = dict(old_origins)
    for role in changed:
        code['evidence_role_origins'][role] = reviewer_kind
        if role == 'support':
            if not code.get('split_parent_id'):
                code.setdefault('original_model_support_span_ids', list(model_roles['support']))
            code['support_span_ids'] = list(selected['support'])
            code['evidence'] = [deepcopy(result['registry'][sid]) for sid in selected['support']]
            code['evidence_selection_origin'] = reviewer_kind
        else:
            code[role] = [deepcopy(result['registry'][sid]) for sid in selected[role]]
    event = {'event': 'supporting_evidence_revised' if legacy_support else 'evidence_roles_revised',
             'code_id': code_id, 'reviewer_kind': reviewer_kind, 'reason': reason.strip(),
             'before_result_sha256': result['result_sha256'],
             'old_roles': {role: current[role] for role in changed},
             'new_roles': {role: selected[role] for role in changed},
             'old_role_origins': {role: old_origins[role] for role in changed},
             'new_role_origins': {role: reviewer_kind for role in changed}}
    if legacy_support:
        event.update(old_support_span_ids=current['support'], new_support_span_ids=selected['support'])
    if reviewer_name is not None:
        event['reviewer_name'] = reviewer_name.strip()
    revised.setdefault('review_history', []).append(event)
    return _finish_revision(revised, code)


def revise_evidence_roles(result, code_id, roles, *, reviewer_kind, reason, reviewer_name=None):
    """Atomically revise disjoint same-turn roles with per-role authorship."""
    return _revise_evidence_roles(result, code_id, roles, reviewer_kind, reason, reviewer_name=reviewer_name)


def revise_supporting_evidence(result, code_id, span_ids, *, reviewer_kind, reason, reviewer_name=None):
    """Compatibility API for support-only edits, retaining legacy event keys."""
    if isinstance(span_ids, (str, bytes)) or not isinstance(span_ids, Sequence):
        raise ValueError('Select 1..6 distinct registry span IDs.')
    return _revise_evidence_roles(result, code_id, {'support': list(span_ids)}, reviewer_kind, reason, legacy_support=True, reviewer_name=reviewer_name)


WORKFLOW_VERSION = 'themeforge.candidate-review-workflow.v1'
_AUTHORS = {'researcher', 'assistant_development_review'}
_CHECKS = {'actor_perspective', 'action_status', 'conditions_frequency', 'context_use', 'remaining_uncertainty'}


def _author_details(kind, name, reason):
    if type(kind) is not str or kind not in _AUTHORS:
        raise ValueError('Explicit researcher or assistant_development_review authorship is required.')
    for value, label, bound in ((name, 'reviewer name', 200), (reason, 'review reason', 2000)):
        if type(value) is not str or not value.strip() or len(value) > bound:
            raise ValueError(f'Provide a nonblank {label}, at most {bound} characters.')


def _review_checks(checks):
    checks = {} if checks is None else checks
    if type(checks) is not dict or not set(checks) <= _CHECKS:
        raise ValueError('Review checks must use the bounded review note fields.')
    if any(type(value) is not str or not value.strip() or len(value) > 2000 for value in checks.values()):
        raise ValueError('Review checks are nonblank reviewer notes of at most 2000 characters each.')
    return deepcopy(checks)


def _candidate(result, code_id):
    for code in result['codes']:
        if code['id'] == code_id:
            return code
    raise ValueError('Select a known candidate code ID.')


def _workflow(result):
    return result.setdefault('candidate_review_workflow', dict(version=WORKFLOW_VERSION, decisions=[], splits=[]))


def candidate_review_state(result, code_id):
    """Live decision, bound to the complete current candidate, never a validity claim."""
    code = _candidate(result, code_id)
    workflow = result.get('candidate_review_workflow', {})
    latest = next((event for event in reversed(workflow.get('decisions', [])) if event['code_id'] == code_id), None)
    state = latest['decision'] if latest else 'unreviewed'
    if latest and latest['code_sha256'] != _digest(code):
        state = 'needs_review'
    if any(event['parent_id'] == code_id for event in workflow.get('splits', [])):
        state = 'split_parent'
    return dict(state=state, reviewer_kind=latest['reviewer_kind'] if latest else None,
                reviewer_name=latest['reviewer_name'] if latest else None,
                reason=latest['reason'] if latest else None, checks=deepcopy(latest['checks']) if latest else {},
                researcher_approved=bool(latest and state in {'accept', 'edit'} and latest['reviewer_kind'] == 'researcher'),
                authorship_verified=False, semantic_validation=False)


def grouping_review_issue(result, code_id):
    code = _candidate(result, code_id)
    if not has_completed_definition(code):
        return 'Candidate needs an explicit completed definition before grouping.'
    if 'candidate_review_workflow' in result:
        state = candidate_review_state(result, code_id)['state']
        if state not in {'accept', 'edit'}:
            return f'Candidate review is {state}; record a current accept or edit decision before grouping.'
    return None


def _source_review_view(result, code_id):
    from .role_evidence import build_role_evidence, source_span
    code = _candidate(result, code_id)
    unit = next(unit for unit in result['unit_archive'] if unit['id'] == code['unit_id'])
    document = next(doc for doc in result['source_documents'] if doc['name'] == unit['source_name'])
    source = dict(source_name=document['name'], source_hash=document['sha256'], text=document['text'])
    same_source = [item for item in result['unit_archive'] if item['source_name'] == unit['source_name']]
    previous = [item for item in same_source if item['source_end'] <= unit['source_start']][-2:]
    # Show full original turns for adjacent review context and selected referents.
    # These spans are reconstructed from the source archive, never sidecar input.
    selected_units = {entry['unit_id'] for entry in code.get('referent_context', [])}
    contexts = previous + [item for item in same_source if item['id'] in selected_units]
    unique = {(item['source_start'], item['source_end']): item for item in contexts}
    contexts = sorted(unique.values(), key=lambda item: item['source_start'])
    if len(contexts) > 8:
        raise ValueError('Review context exceeds eight exact turns.')
    spans = [source_span(source, item['source_start'], item['source_end']) for item in contexts]
    role_evidence = build_role_evidence(unit, source, contexts=spans)
    imported = role_evidence['imported']
    entries = [dict(id='target', kind='target', span=deepcopy(imported['target']),
                    source_speaker=deepcopy(imported['source_speaker']), saved_speaker_label=unit.get('speaker'),
                    use='target_turn_not_automatic_support_for_every_claim')]
    for item, context in zip(contexts, imported['contexts']):
        entries.append(dict(id='context-' + _digest(context['span'])[:16], kind='context',
                            **deepcopy(context), saved_speaker_label=item.get('speaker'),
                            selected_referent_unit=item['id'] in selected_units,
                            generation_context_status='Selected referent excerpts are listed separately; full review turns do not establish generation input.'))
    provenance = {key: deepcopy(value) for key, value in result.items()
                  if 'provenance' in key or key in {'imported_fixture', 'packet_sha256', 'prompt_variant', 'source_origin', 'mode', 'research_questions', 'speaker_label_policy'}}
    return dict(candidate=deepcopy(code), review=candidate_review_state(result, code_id),
                role_evidence=role_evidence, entries=entries, provenance=provenance,
                target_registry_spans=[dict(id=sid, span=deepcopy(span), role=(
                    'support' if sid in code['support_span_ids'] else 'qualifications' if span in code['qualifications']
                    else 'counterexamples' if span in code['counterexamples'] else 'unassigned_not_automatic_qualification'))
                    for sid, span in result['registry'].items() if span['unit_id'] == code['unit_id']],
                selected_referent_context=deepcopy(code.get('referent_context', [])),
                notice=imported['notice'], source_document_metadata={key: deepcopy(value) for key, value in document.items() if key != 'text'})


def source_review_view(result, code_id):
    validate_result_sources(result)
    return _source_review_view(result, code_id)


def source_review_text(view, entry_id='target'):
    """Display literal text plus separate, structural source attribution."""
    import json
    entry = next((entry for entry in view['entries'] if entry['id'] == entry_id), None)
    if entry is None:
        raise ValueError('Unknown source review entry.')
    span = entry['span']
    rendered = (f"{entry['kind'].upper()} | {span['source_name']} | chars {span['source_start']}:{span['source_end']}\n"
            f"SHA-256: {span['source_hash']}\n"
            f"Source speaker (structural label only): {json.dumps(entry['source_speaker'], ensure_ascii=False)}\n"
            f"Saved speaker metadata (unchanged): {entry.get('saved_speaker_label')}\n"
            f"{view['notice']}\nUse: {entry['use']}\n\n{span['text']}")
    if entry['kind'] == 'target':
        code = view['candidate']
        rendered += '\n\nCurrent candidate wording and explicit origins (review prose, not source facts):\n' + json.dumps(
            {key: code.get(key) for key in ('id', 'label', 'claim', 'definition', 'origin', 'field_origins', 'interpretation_field_origins', 'split_parent_id')}, ensure_ascii=False, indent=2)
        rendered += '\n\nExact target registry spans. Unassigned spans are not automatic qualifications:\n'
        for item in view['target_registry_spans']:
            selected = item['span']
            rendered += f"\n{item['id']} | {item['role']} | chars {selected['source_start']}:{selected['source_end']} | SHA-256 {selected['source_hash']}\n{selected['text']}\n"
    else:
        rendered += ('\n\nFull adjacent review context, separate from saved referent selections.'
                     '\nFull review context does not establish original generation input. Raw requests remain authoritative for rendered prompt content.'
                     f"\nThis full turn is a saved selected referent unit: {bool(entry.get('selected_referent_unit'))}."
                     '\nExact saved selected_referent_context snippets within this turn (selection provenance only):\n')
        snippets = [selected for selected in view['selected_referent_context']
                    if selected['source_name'] == span['source_name'] and selected['source_hash'] == span['source_hash']
                    and span['source_start'] <= selected['source_start'] < selected['source_end'] <= span['source_end']]
        if not snippets:
            rendered += 'None. This full turn is shown only as adjacent review context.\n'
        for selected in snippets:
            rendered += (f"\nSaved referent unit {selected['unit_id']} | chars {selected['source_start']}:{selected['source_end']}"
                         f" | SHA-256 {selected['source_hash']}\n{selected['text']}\n")
    return rendered


def _source_snapshot(result, code_id):
    view = _source_review_view(result, code_id)
    return {key: deepcopy(view[key]) for key in ('role_evidence', 'entries', 'provenance', 'selected_referent_context', 'source_document_metadata')}


def _seal_event(event):
    event['event_sha256'] = _digest(event)
    return event


def _has_explicit_edit(result, code):
    return bool(code.get('split_parent_id') or any(event.get('code_id') == code['id'] and event.get('event') in {
        'interpretation_revised', 'evidence_roles_revised', 'supporting_evidence_revised'} for event in result.get('review_history', [])))


def record_candidate_decision(result, code_id, decision, *, reviewer_kind, reviewer_name, reason, checks=None, _recorded_at_utc=None):
    """Append an explicitly authored reviewer judgment; optional checks are notes."""
    if _recorded_at_utc is None:
        _recorded_at_utc = datetime.now(timezone.utc).isoformat()
    recorded = datetime.fromisoformat(_recorded_at_utc)
    if recorded.tzinfo is None or recorded.utcoffset().total_seconds() != 0:
        raise ValueError('Decision timestamp must have an explicit UTC offset.')
    validate_result_sources(result)
    _author_details(reviewer_kind, reviewer_name, reason)
    if type(decision) is not str or decision not in {'accept', 'edit', 'reject', 'uncertain'}:
        raise ValueError('Choose accept, edit, reject or uncertain.')
    # Decisions, like revisions, branch only before category creation.
    code = _review_target(result, code_id, reviewer_kind, reason, allow_incomplete=True)
    if decision in {'accept', 'edit'} and not has_completed_definition(code):
        raise ValueError('Accept/edit requires a completed definition; explicitly repair it first.')
    if decision == 'edit' and not _has_explicit_edit(result, code):
        raise ValueError('An edit decision requires an actual explicit interpretation, evidence revision or split lineage.')
    if candidate_review_state(result, code_id)['state'] == 'split_parent':
        raise ValueError('The retained split parent cannot be accepted; review its children.')
    checks = _review_checks(checks)
    revised = deepcopy(result)
    workflow = _workflow(revised)
    workflow['decisions'].append(_seal_event(dict(code_id=code_id, decision=decision,
        recorded_at_utc=_recorded_at_utc,
        reviewer_kind=reviewer_kind, reviewer_name=reviewer_name.strip(), reason=reason.strip(),
        checks=deepcopy(checks), checks_origin='reviewer_judgment_not_imported_fact',
        code_sha256=_digest(code), code_snapshot=deepcopy(code), source_snapshot=_source_snapshot(result, code_id),
        edit_lineage=deepcopy([event for event in result.get('review_history', []) if event.get('code_id') == code_id]),
        before_result_sha256=result['result_sha256'],
        previous_event_sha256=workflow['decisions'][-1]['event_sha256'] if workflow['decisions'] else None)))
    return _finish_revision(revised, _candidate(revised, code_id), refresh=False)


def split_candidate(result, code_id, parts, *, reviewer_kind, reviewer_name, reason, checks=None, _recorded_at_utc=None):
    """Retain the raw parent and add two or three explicitly authored same-unit children."""
    # Internal replay timestamp is provenance, never proof of authorship or truth.
    if _recorded_at_utc is None:
        _recorded_at_utc = datetime.now(timezone.utc).isoformat()
    recorded = datetime.fromisoformat(_recorded_at_utc)
    if recorded.tzinfo is None or recorded.utcoffset().total_seconds() != 0:
        raise ValueError('Split timestamp must have an explicit UTC offset.')
    _author_details(reviewer_kind, reviewer_name, reason)
    checks = _review_checks(checks)
    original = _review_target(result, code_id, reviewer_kind, reason, allow_incomplete=True)
    if candidate_review_state(result, code_id)['state'] == 'split_parent':
        raise ValueError('This candidate already has a retained split lineage.')
    if type(parts) is not list or not 2 <= len(parts) <= 3:
        raise ValueError('A split requires two or three explicit parts.')
    required = {'label', 'claim', 'definition', 'support_span_ids'}
    optional = {'qualification_span_ids', 'counterexample_span_ids'}
    revised = deepcopy(result)
    children = []
    from .local_codebook import _HOST_SCOPE_NOTICES, refresh_meaning_checks
    for index, part in enumerate(parts, 1):
        if type(part) is not dict or not required <= set(part) or not set(part) <= required | optional:
            raise ValueError('Split parts require explicit label, claim, definition and supporting IDs.')
        for field, limit in (('label', 120), ('claim', 600), ('definition', 600)):
            _text(part[field], field, limit, substantive=True)
        roles = dict(support=part['support_span_ids'], qualifications=part.get('qualification_span_ids', []),
                     counterexamples=part.get('counterexample_span_ids', []))
        all_ids = []
        for role, ids in roles.items():
            if (type(ids) is not list or not (1 if role == 'support' else 0) <= len(ids) <= 6
                    or any(type(sid) is not str or sid not in result['registry'] or result['registry'][sid]['unit_id'] != original['unit_id'] for sid in ids)):
                raise ValueError('Split evidence requires bounded exact IDs in the original source unit.')
            all_ids.extend(ids)
        if len(set(all_ids)) != len(all_ids) or len(roles['qualifications']) + len(roles['counterexamples']) > 6:
            raise ValueError('Split evidence roles must be disjoint and bounded.')
        child = deepcopy(original)
        for key in ('original_model_prose', 'original_model_evidence_roles', 'original_model_support_span_ids',
                    'interpretation_field_origins', 'split_origin_prose', 'split_origin_evidence_roles', 'split_parent_id'):
            child.pop(key, None)
        child.update({field: part[field] for field in ('label', 'claim', 'definition')})
        child.update(deepcopy(_HOST_SCOPE_NOTICES), status='definition_revised', origin=reviewer_kind,
                     scope_origin=reviewer_kind, scope_boundary_status='not_established', split_parent_id=code_id,
                     id='SPLIT-' + _digest([code_id, result['result_sha256'], index, part, reviewer_kind, reviewer_name])[:20],
                     support_span_ids=list(roles['support']), evidence=[deepcopy(result['registry'][sid]) for sid in roles['support']],
                     qualifications=[deepcopy(result['registry'][sid]) for sid in roles['qualifications']],
                     counterexamples=[deepcopy(result['registry'][sid]) for sid in roles['counterexamples']],
                     evidence_role_origins={role: reviewer_kind for role in roles}, evidence_selection_origin=reviewer_kind,
                     split_origin_prose={field: part[field] for field in ('label', 'claim', 'definition')},
                     split_origin_evidence_roles=deepcopy(roles), review_status='unreviewed_machine_draft',
                     field_origins=dict(label=reviewer_kind, claim=reviewer_kind, definition=reviewer_kind,
                         inclusion='host_proposed_analyst_rule', exclusion='host_unestablished_notice', uncertainty='host_review_notice'))
        refresh_meaning_checks(revised, child)
        children.append(child)
    if len({_digest({field: child[field] for field in ('label', 'claim', 'definition', 'support_span_ids')}) for child in children}) != len(children):
        raise ValueError('Split parts must have distinct meanings or evidence selections.')
    revised['codes'].extend(children)
    workflow = _workflow(revised)
    workflow['splits'].append(_seal_event(dict(parent_id=code_id, parent_snapshot=deepcopy(original),
        recorded_at_utc=_recorded_at_utc,
        parent_sha256=_digest(original), child_ids=[child['id'] for child in children], child_snapshots=deepcopy(children),
        reviewer_kind=reviewer_kind, reviewer_name=reviewer_name.strip(), reason=reason.strip(),
        checks=checks, checks_origin='parent_split_rationale_not_child_judgments_or_imported_fact',
        source_snapshot=_source_snapshot(result, code_id), before_result_sha256=result['result_sha256'],
        previous_event_sha256=workflow['splits'][-1]['event_sha256'] if workflow['splits'] else None)))
    return _finish_revision(revised, children[-1])


def validate_candidate_review_workflow(result):
    """Validate declared authorship and exact history/source correspondence, not identity or truth."""
    import json
    if 'candidate_review_workflow' not in result:
        return
    workflow = result['candidate_review_workflow']
    if (type(workflow) is not dict or set(workflow) != {'version', 'decisions', 'splits'}
            or workflow['version'] != WORKFLOW_VERSION):
        raise ValueError('Malformed candidate review workflow version or fields.')
    if len(json.dumps(workflow, ensure_ascii=False).encode('utf-8')) > 8 * 1024**2:
        raise ValueError('Candidate review history exceeds 8 MiB; retain history on a separate branch.')
    codes = {code['id']: code for code in result['codes']}
    history = result.get('review_history', [])
    def snapshot(code):
        if type(code) is not dict or code.get('id') not in codes or code.get('unit_id') != codes[code['id']]['unit_id']:
            raise ValueError('Review snapshot must retain its candidate identity and source unit.')
        for field, limit in (('label', 120), ('claim', 600)):
            _text(code.get(field), field, limit, substantive=True)
        roles = _role_ids(result, code)
        all_ids = []
        for role, ids in roles.items():
            if (type(ids) is not list or not (1 if role == 'support' else 0) <= len(ids) <= 6
                    or any(type(sid) is not str or sid not in result['registry'] or result['registry'][sid]['unit_id'] != code['unit_id'] for sid in ids)):
                raise ValueError('Review snapshots must retain exact same-unit evidence.')
            all_ids.extend(ids)
        if len(set(all_ids)) != len(all_ids) or code['evidence'] != [result['registry'][sid] for sid in roles['support']]:
            raise ValueError('Review snapshot evidence roles changed or overlap.')
    def prove_source(code, value):
        native = dict(result, codes=[code if current['id'] == code['id'] else current for current in result['codes']])
        if value != _source_snapshot(native, code['id']):
            raise ValueError('Review source snapshot differs from the exact native source and structural speakers.')
    def events(key):
        rows = workflow[key]
        if type(rows) is not list or len(rows) > 256:
            raise ValueError('Review decisions and splits must be bounded append histories.')
        previous = None
        for event in rows:
            if type(event) is not dict:
                raise ValueError('Review history event must be an object.')
            _author_details(event.get('reviewer_kind'), event.get('reviewer_name'), event.get('reason'))
            if (event.get('previous_event_sha256') != previous or event.get('event_sha256') !=
                    _digest({key: value for key, value in event.items() if key != 'event_sha256'})):
                raise ValueError('Candidate review event history digest or link changed.')
            if any(event[key] != event[key].strip() for key in ('reviewer_name', 'reason')):
                raise ValueError('Review author/reason text must remain canonical.')
            if type(event.get('before_result_sha256')) is not str or len(event['before_result_sha256']) != 64:
                raise ValueError('Review events require the original result digest.')
            recorded = datetime.fromisoformat(event.get('recorded_at_utc', ''))
            if recorded.tzinfo is None or recorded.utcoffset().total_seconds() != 0:
                raise ValueError('Review event timestamp must retain an explicit UTC offset.')
            previous = event['event_sha256']
            yield event
    try:
        for event in history:
            if event.get('event') in {'interpretation_revised', 'evidence_roles_revised', 'supporting_evidence_revised'} and 'reviewer_name' in event:
                _author_details(event.get('reviewer_kind'), event['reviewer_name'], event.get('reason'))
        split_children, parents = {}, set()
        split_fields = {'parent_id', 'parent_snapshot', 'parent_sha256', 'child_ids', 'child_snapshots',
                        'reviewer_kind', 'reviewer_name', 'reason', 'source_snapshot', 'before_result_sha256',
                        'previous_event_sha256', 'event_sha256', 'recorded_at_utc'}
        for event in events('splits'):
            if set(event) not in (split_fields, split_fields | {'checks', 'checks_origin'}):
                raise ValueError('Split event fields are malformed.')
            if 'checks' in event:
                if type(event['checks']) is not dict:
                    raise ValueError('Stored split review notes must be an explicit bounded object.')
                _review_checks(event['checks'])
                if event['checks_origin'] != 'parent_split_rationale_not_child_judgments_or_imported_fact':
                    raise ValueError('Split review notes must remain parent split rationale, separate from child judgments and source facts.')
            parent = event['parent_snapshot']
            snapshot(parent)
            if (event['parent_id'] != parent['id'] or parent['id'] in parents or codes[parent['id']] != parent
                    or event['parent_sha256'] != _digest(parent)):
                raise ValueError('Retained split parent or original snapshot changed.')
            parents.add(parent['id'])
            prove_source(parent, event['source_snapshot'])
            children = event['child_snapshots']
            if (type(children) is not list or not 2 <= len(children) <= 3
                    or event['child_ids'] != [child['id'] for child in children]
                    or len(set(event['child_ids'])) != len(children)):
                raise ValueError('Split requires two or three distinct recorded children.')
            for child in children:
                snapshot(child)
                if child['id'] in split_children or child.get('split_parent_id') != parent['id'] or child['unit_id'] != parent['unit_id']:
                    raise ValueError('Split child identity, parent or source lineage changed.')
                actor = event['reviewer_kind']
                prose = {field: child[field] for field in ('label', 'claim', 'definition')}
                if (child.get('origin') != actor or child.get('scope_origin') != actor
                        or child.get('split_origin_prose') != prose
                        or child.get('split_origin_evidence_roles') != _role_ids(result, child)
                        or any(child.get('field_origins', {}).get(field) != actor for field in prose)
                        or child.get('evidence_role_origins') != {role: actor for role in _role_ids(result, child)}
                        or any(key.startswith('original_model_') for key in child)):
                    raise ValueError('Split wording and evidence must retain their explicit birth author, separate from model output.')
                split_children[child['id']] = child
        for code in result['codes']:
            if code.get('split_parent_id') and code['id'] not in split_children:
                raise ValueError('Split child is missing its birth event.')
            birth = split_children.get(code['id'])
            if birth:
                if (code.get('origin') != birth['origin'] or code.get('split_parent_id') != birth['split_parent_id']
                        or code.get('split_origin_prose') != birth['split_origin_prose']
                        or code.get('split_origin_evidence_roles') != birth['split_origin_evidence_roles']
                        or any(key.startswith('original_model_') for key in code)):
                    raise ValueError('Split child birth lineage changed after revision.')
            # Replay explicit wording edits from the preserved origin.
            edits = [event for event in history if event.get('code_id') == code['id'] and event.get('event') == 'interpretation_revised']
            if edits:
                prose = deepcopy(birth['split_origin_prose'] if birth else code.get('original_model_prose'))
                if type(prose) is not dict or set(prose) != {'label', 'claim', 'definition'}:
                    raise ValueError('Wording edit requires preserved original prose.')
                origins = {field: birth['origin'] if birth else code.get('interpretation_field_origins', {}).get(field, 'local_model') for field in prose}
                if not birth:
                    for event in reversed(edits):
                        origins.update(event.get('old_field_origins', {}))
                for event in edits:
                    actor = event.get('reviewer_kind')
                    changes = event.get('new_values')
                    if (actor not in _AUTHORS or type(changes) is not dict or not changes or not set(changes) <= set(prose)
                            or event.get('old_values') != {field: prose[field] for field in changes}
                            or event.get('old_field_origins') != {field: origins[field] for field in changes}
                            or event.get('new_field_origins') != {field: actor for field in changes}):
                        raise ValueError('Candidate wording edit authorship/history is inconsistent.')
                    for field, value in changes.items():
                        _text(value, field, 120 if field == 'label' else 600, substantive=True)
                        prose[field], origins[field] = value, actor
                if (any(code[field] != prose[field] for field in prose)
                        or code.get('interpretation_field_origins') != origins
                        or any(code.get('field_origins', {}).get(field, 'local_model') != origins[field] for field in prose)):
                    raise ValueError('Current candidate wording/authorship differs from explicit edit history.')
            elif birth and any(code[field] != birth[field] for field in ('label', 'claim', 'definition')):
                raise ValueError('Split child wording changed without an explicit edit event.')
        decision_fields = {'code_id', 'decision', 'reviewer_kind', 'reviewer_name', 'reason', 'checks', 'checks_origin',
                           'code_sha256', 'code_snapshot', 'source_snapshot', 'edit_lineage', 'before_result_sha256',
                           'previous_event_sha256', 'event_sha256', 'recorded_at_utc'}
        for event in events('decisions'):
            if set(event) != decision_fields or event['decision'] not in {'accept', 'edit', 'reject', 'uncertain'}:
                raise ValueError('Candidate decision fields or state are invalid.')
            code = event['code_snapshot']
            snapshot(code)
            if event['code_id'] != code['id'] or event['code_sha256'] != _digest(code):
                raise ValueError('Candidate decision snapshot digest changed.')
            prove_source(code, event['source_snapshot'])
            checks = event['checks']
            if (type(checks) is not dict or not set(checks) <= _CHECKS
                    or event['checks_origin'] != 'reviewer_judgment_not_imported_fact'
                    or any(type(value) is not str or not value.strip() or len(value) > 2000 for value in checks.values())):
                raise ValueError('Review checks must remain optional bounded reviewer judgments.')
            lineage = [row for row in history if row.get('code_id') == code['id']]
            if type(event['edit_lineage']) is not list or event['edit_lineage'] != lineage[:len(event['edit_lineage'])]:
                raise ValueError('Decision edit lineage differs from the retained explicit revision history.')
            if event['decision'] in {'accept', 'edit'} and not has_completed_definition(code):
                raise ValueError('Accepted review snapshot requires a completed definition.')
            if event['decision'] == 'edit' and not (code.get('split_parent_id') in parents or event['edit_lineage']):
                raise ValueError('Edit decision must retain an actual explicit edit or split lineage.')
        if result.get('review_status') != 'unreviewed_machine_draft' or result.get('semantic_quality') != 'not_human_validated':
            raise ValueError('Candidate review cannot promote legacy semantic validity or human admission.')
    except (KeyError, TypeError, AttributeError, StopIteration) as error:
        raise ValueError('Malformed candidate review snapshots, lineage or source evidence.') from error

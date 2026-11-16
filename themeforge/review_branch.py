"""Offline versioned branches. Historical groups are never current approval."""
from copy import deepcopy
import json
import os
import tempfile
from pathlib import Path
from .model_pilot import _digest
from .local_codebook import validate_result_sources
from .codebook_review import revise_interpretation, revise_evidence_roles, split_candidate, record_candidate_decision
from .reviewed_grouping import COLLECTION_FIELDS

MAX_BYTES = 32 * 1024**2
MAX_REVISIONS = 256

def _seal(value):
    value['branch_sha256'] = _digest({k:v for k,v in value.items() if k != 'branch_sha256'})
    if len(json.dumps(value, ensure_ascii=False).encode()) > MAX_BYTES:
        raise ValueError('Branch exceeds 32 MiB; original remains unchanged.')
    return value

def _draft(parent):
    draft = deepcopy(parent)
    for key in list(draft):
        if key in COLLECTION_FIELDS or key.startswith('synthesis'):
            del draft[key]
    draft['categories'] = []
    draft['interpretive_themes'] = []
    draft['calls'] = [c for c in draft.get('calls', []) if c.get('stage') != 'synthesis']
    draft['result_sha256'] = _digest({k:v for k,v in draft.items() if k != 'result_sha256'})
    validate_result_sources(draft)
    return draft

def begin_branch(parent, *, reviewer_kind, reviewer_name, reason):
    from .codebook_review import _author_details
    _author_details(reviewer_kind, reviewer_name, reason)
    validate_result_sources(parent)
    return _seal(dict(version=1, parent=deepcopy(parent), draft=_draft(parent), revisions=[],
        author=dict(reviewer_kind=reviewer_kind, reviewer_name=reviewer_name, reason=reason),
        semantic_validation=False, training_eligible=False))

def validate_branch(branch):
    if type(branch) is not dict or type(branch.get('revisions')) is not list or len(branch['revisions']) > MAX_REVISIONS:
        raise ValueError('Malformed branch or too many revisions; use a separate branch.')
    if branch.get('branch_sha256') != _digest({k:v for k,v in branch.items() if k != 'branch_sha256'}):
        raise ValueError('Branch digest mismatch.')
    if branch.get('version') != 1 or branch.get('semantic_validation') is not False or branch.get('training_eligible') is not False:
        raise ValueError('Unsupported branch or unsupported validation claim.')
    from .codebook_review import _author_details
    _author_details(**{('kind' if k=='reviewer_kind' else 'name' if k=='reviewer_name' else k):v for k,v in branch['author'].items()})
    validate_result_sources(branch['parent'])
    expected = _draft(branch['parent'])
    for event in branch['revisions']:
        if event.get('operation')=='group_rereview' and (event.get('collection') not in
                ('categories','reviewed_categories','interpretive_themes') or not any(
                g['id']==event.get('group_id') for g in branch['parent'].get(event.get('collection'),[]))):
            raise ValueError('Group review must identify a preserved historical group.')
        expected = _apply_event(expected, event)
    if expected != branch['draft']:
        raise ValueError('Draft does not reproduce its revision history.')
    validate_result_sources(branch['draft'])
    if len(json.dumps(branch, ensure_ascii=False).encode()) > MAX_BYTES:
        raise ValueError('Oversized branch.')

def revise_branch(branch, code_id, changes, *, reviewer_kind, reviewer_name, reason):
    event = dict(code_id=code_id, changes=deepcopy(changes), reviewer_kind=reviewer_kind,
                 reviewer_name=reviewer_name, reason=reason)
    return _append_event(branch, event)

def _apply_event(draft, event):
    values=deepcopy(event)
    operation=values.pop('operation', 'interpretation')
    if operation == 'interpretation':
        return revise_interpretation(draft, **values)
    if operation == 'evidence_roles':
        return revise_evidence_roles(draft, **values)
    if operation == 'split':
        return split_candidate(draft, **values)
    if operation == 'candidate_decision':
        return record_candidate_decision(draft, **values)
    if operation == 'group_rereview':
        from .reviewed_grouping import add_reviewed_category
        from .codebook_review import _author_details
        _author_details(values['fields']['reviewer_kind'], values['reviewer_name'], values['fields']['reason'])
        from .codebook_review import candidate_review_state
        if any(candidate_review_state(draft,cid)['state'] not in ('accept','edit') for cid in values['fields']['code_ids']):
            raise ValueError('Every group member needs a current explicit accept/edit decision.')
        proposed = add_reviewed_category(draft, **values['fields'])['reviewed_categories'][-1]
        if proposed != values['category']:
            raise ValueError('Group review no longer reproduces exact member snapshots.')
        return draft
    raise ValueError('Unsupported revision operation; no implicit approval.')

def _append_event(branch, event):
    validate_branch(branch)
    if len(branch['revisions']) >= MAX_REVISIONS:
        raise ValueError('Revision limit reached; preserve this branch.')
    revised=deepcopy(branch)
    revised['draft']=_apply_event(revised['draft'],event)
    revised['revisions'].append(deepcopy(event))
    return _seal(revised)

def revise_branch_roles(branch, code_id, roles, *, reviewer_kind, reviewer_name, reason):
    return _append_event(branch,dict(operation='evidence_roles',code_id=code_id,
        roles=deepcopy(roles),reviewer_kind=reviewer_kind,reviewer_name=reviewer_name,reason=reason))

def split_branch(branch, code_id, parts, *, reviewer_kind, reviewer_name, reason, checks=None):
    from datetime import datetime, timezone
    return _append_event(branch, dict(operation='split', code_id=code_id, parts=deepcopy(parts),
        reviewer_kind=reviewer_kind, reviewer_name=reviewer_name, reason=reason, checks=deepcopy(checks),
        _recorded_at_utc=datetime.now(timezone.utc).isoformat()))

def decide_branch_candidate(branch, code_id, decision, *, reviewer_kind, reviewer_name, reason, checks=None):
    from datetime import datetime, timezone
    return _append_event(branch,dict(operation='candidate_decision', code_id=code_id, decision=decision,
        reviewer_kind=reviewer_kind,reviewer_name=reviewer_name,reason=reason,checks=checks,
        _recorded_at_utc=datetime.now(timezone.utc).isoformat()))

def rereview_branch_group(branch, collection, group_id, *, reviewer_name, **fields):
    """Explicit new provisional relationship, not reinstatement of old approval."""
    validate_branch(branch)
    if collection not in ('categories','reviewed_categories','interpretive_themes') or not any(
            g['id']==group_id for g in branch['parent'].get(collection,[])):
        raise ValueError('Choose an existing historical group.')
    from .reviewed_grouping import add_reviewed_category
    proposed=add_reviewed_category(branch['draft'],**fields)['reviewed_categories'][-1]
    return _append_event(branch,dict(operation='group_rereview', collection=collection, group_id=group_id,
        reviewer_name=reviewer_name,fields=deepcopy(fields),category=proposed))

def group_states(branch):
    """Derived statuses cannot silently carry approval after changed dependencies."""
    validate_branch(branch)
    original = {c['id']:c for c in branch['parent']['codes']}
    current = {c['id']:c for c in branch['draft']['codes']}
    changed = {cid for cid in original if current.get(cid) != original[cid]}
    old_splits={e['parent_id'] for e in branch['parent'].get('candidate_review_workflow',{}).get('splits',[])}
    new_splits={e['parent_id'] for e in branch['draft'].get('candidate_review_workflow',{}).get('splits',[])}
    changed.update(new_splits-old_splits)
    rows=[]
    for field in ('categories', 'reviewed_categories', 'interpretive_themes'):
        for group in branch['parent'].get(field, []):
            dependencies=set()
            for key in ('code_ids','qualification_code_ids','counterexample_code_ids'):
                dependencies.update(group.get(key, []))
            affected=sorted(dependencies & changed)
            latest=next((e for e in reversed(branch['revisions']) if e.get('operation')=='group_rereview'
                and e['collection']==field and e['group_id']==group['id']),None)
            fresh=bool(latest and all(current.get(m['code_id']) is not None and
                _digest(current[m['code_id']])==m['code_sha256'] for m in latest['category']['members']))
            if fresh:
                from .codebook_review import grouping_review_issue
                fresh=not any(grouping_review_issue(branch['draft'],m['code_id']) for m in latest['category']['members'])
            rows.append(dict(id=group['id'], collection=field, changed_code_ids=affected,
                status=('rereviewed_provisional_not_validated' if fresh else 'review_required' if latest or affected or not dependencies else 'unchanged_historical_snapshot'),
                current_approval=False, latest_group_review=deepcopy(latest)))
    return rows

def undo_revision(branch):
    validate_branch(branch)
    if not branch['revisions']:
        raise ValueError('No revision to undo.')
    revised=begin_branch(branch['parent'], **branch['author'])
    for event in branch['revisions'][:-1]:
        revised=_append_event(revised, event)
    return revised

def _publish_branch_file(source, destination):
    # Windows rename fails if destination exists; POSIX rename replaces it.
    if os.name == 'nt':
        os.rename(source, destination)
    else:
        os.link(source, destination)


def save_branch(branch, path):
    validate_branch(branch)
    # Validate the ACTUAL saved representation, before opening destination.
    payload=json.dumps(branch,ensure_ascii=False,indent=2).encode('utf-8')
    if len(payload)>MAX_BYTES:
        raise ValueError('Formatted branch exceeds reopen limit; no file written.')
    # Publish only a completely written file, without replacing an existing
    # destination (including a concurrent save). Windows uses its exclusive
    # rename; other platforms use an exclusive hard-link operation.
    destination = Path(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='wb', prefix='.themeforge-branch-',
                suffix='.tmp', dir=destination.parent, delete=False) as out:
            temporary = Path(out.name)
            out.write(payload)
            out.flush()
            os.fsync(out.fileno())
        _publish_branch_file(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)

def load_branch(path):
    # Bounded read also handles files growing after stat; never read arbitrary size.
    with Path(path).open('rb') as source:
        payload=source.read(MAX_BYTES+1)
    if len(payload)>MAX_BYTES:
        raise ValueError('Oversized branch file.')
    branch=json.loads(payload.decode('utf-8'))
    validate_branch(branch)
    return branch

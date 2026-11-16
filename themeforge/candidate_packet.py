"""Offline provisional-data review. No inference, training admission or network.

Raw fiction and initial AI proposals remain immutable; edits are attributed
events. Self-declared human editing is not authenticated human adjudication.
"""
from __future__ import annotations
from copy import deepcopy
import csv
from hashlib import sha256
from html import escape
import io
import json

VERSION = 'themeforge.provisional-education-candidate-packet.v1'
EDITABLE = {'label', 'claim', 'definition', 'evidence_ids', 'qualification_ids', 'counterexample_ids'}
ROLE_FIELDS = ('evidence_ids', 'qualification_ids', 'counterexample_ids')

def digest(value):
    return sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode('utf-8')).hexdigest()

def text_digest(text):return sha256(text.encode('utf-8')).hexdigest()

def seal(packet):
    packet['packet_sha256']=digest({k:v for k,v in packet.items() if k!='packet_sha256'})
    return packet

def _require(test,message):
    if not test:raise ValueError(message)

def _index(rows,key):
    _require(type(rows) is list,'Expected an explicit list.')
    values={}
    for row in rows:
        _require(type(row) is dict and type(row.get(key)) is str and row[key].strip(),'Missing record ID.')
        _require(row[key] not in values,'Duplicate record ID.')
        values[row[key]]=row
    return values

def _string(value,limit=4000):
    _require(type(value) is str and bool(value.strip()) and len(value)<=limit,'Expected bounded nonblank text.')

def scope_binding_status(code):
    """A source binding is an audit aid, never semantic acceptance of a definition."""
    original=code['original_scope_input']
    unchanged=(code['label']==original['label'] and code['claim']==original['claim']
               and set(code['evidence_ids'])==set(original['evidence_ids']))
    return 'original_input_unchanged' if unchanged else 'changed_label_claim_or_support_definition_needs_review'

def _code(code,record):
    for key in ('label','claim','definition'):_string(code.get(key),120 if key=='label' else 1200)
    actors=_index(record['actors'],'actor_id');segments=_index(record['segments'],'segment_id');anchors=_index(record['anchors'],'anchor_id')
    _require(code['speaker_actor_id'] in actors,'Unknown reporting speaker.')
    targets=code.get('target_segment_ids',[])
    _require(targets and len(set(targets))==len(targets) and all(t in segments for t in targets),'Unknown/repeated target segment.')
    _require(all(segments[t]['speaker_actor_id']==code['speaker_actor_id'] for t in targets),'Target belongs to another speaker.')
    _require(code.get('origin',{}).get('kind')=='AI','Initial candidates must retain AI origin.')
    _string(code['origin'].get('author_id'))
    _require(code.get('actor_ids') and all(a in actors for a in code['actor_ids']),'Unknown claim actor.')
    for key in ('perspective','modality'):_string(code.get(key))
    for field in ('conditions','limits','alternatives'):
        values=code.get(field)
        _require(type(values) is list and len(values)<=32,'Expected bounded '+field+' list.')
        for value in values:_string(value,2000)
    _require(code['limits'] and code['alternatives'],'Keep interpretation limits and alternatives.')
    selected=[]
    for field in ROLE_FIELDS:
        ids=code.get(field)
        _require(type(ids) is list and len(ids)<=12 and len(set(ids))==len(ids),'Invalid evidence role IDs.')
        _require(all(i in anchors for i in ids),'Unknown evidence anchor.')
        selected+=ids
    _require(code['evidence_ids'],'A proposed code needs exact supporting evidence.')
    _require(len(selected)==len(set(selected)),'Evidence roles cannot share the same anchor ID.')
    for aid in code['evidence_ids']:
        a=anchors[aid]
        _require(a['segment_id'] in targets and a['speaker_actor_id']==code['speaker_actor_id'],'Context or another speaker cannot become target support.')
    for aid in selected:
        _require(segments[anchors[aid]['segment_id']]['role']!='facilitator_context','Facilitator context is not participant evidence.')
    # Overlapping spans can be distinct views of one occurrence, never extra recurrence.
    _require(set(code.get('field_origins',{}))==EDITABLE,'Every editable field needs explicit origin.')
    for origin in code['field_origins'].values():
        _require(type(origin) is dict and origin.get('kind') in ('AI','human_declared'),'Invalid field author kind.')
        _string(origin.get('author_id'))
    scope=code.get('original_scope_input')
    _require(type(scope) is dict and code.get('original_scope_input_sha256')==digest(scope),'Original definition-input binding changed.')
    _require(scope.get('source_sha256')==record['source_sha256'],'Definition-input source binding changed.')
    _require(code.get('scope_status')=='AI_authored_unreviewed_definition_not_accepted_scope','Definition binding cannot become semantic acceptance.')

def validate_packet(packet):
    _require(type(packet) is dict and packet.get('version')==VERSION,'Not a provisional candidate packet.')
    _require(packet.get('training_eligible') is False and packet.get('human_truth') is False,'Review cannot establish training eligibility or truth.')
    _require(packet.get('human_adjudications')==[],'This preparation workflow cannot create authenticated human adjudications.')
    _require(packet.get('packet_sha256')==digest({k:v for k,v in packet.items() if k!='packet_sha256'}),'Packet digest is stale.')
    records=_index(packet['records'],'record_id')
    _require(1<=len(records)<=8 and len({r['family_id'] for r in records.values()})<=4,'Packet exceeds the authorized eight-record/four-family bound.')
    initial={};current={}
    for rid,r in records.items():
        _string(r.get('family_id'),120);_string(r.get('analytic_purpose'))
        _require(type(r.get('unresolved_decisions')) is list and len(r['unresolved_decisions'])<=32,'Expected bounded unresolved decisions.')
        for decision in r['unresolved_decisions']:_string(decision,2000)
        source=r['source_text'];_string(source,32000)
        _require(text_digest(source)==r['source_sha256'],'Source hash mismatch.')
        _require(r['training_eligible'] is False and r['human_truth'] is False,'Record cannot be promoted by editing.')
        _require(r['split']['current_use']=='quarantine' and r['split']['component_closure_status']=='quarantined','Unresolved ancestry stays quarantined.')
        _require(r['split']['historical_assignment']=='unassigned','This packet cannot assign a train/validation/test split.')
        _require(r['rights']['participant_story_or_researcher_label_derivation'] is False,'Participant-derived material is outside this packet.')
        _require(r['rights']['training_permission']=='not_established','This packet has no training-rights admission mechanism.')
        actors=_index(r['actors'],'actor_id');segments=_index(r['segments'],'segment_id')
        for s in segments.values():
            _require(s['speaker_actor_id'] in actors,'Unknown segment speaker.')
            a,b=s['start'],s['end']
            _require(type(a) is int and type(b) is int and 0<=a<b<=len(source) and source[a:b]==s['text'],'Segment does not reconstruct source.')
            la,lb=s['label_start'],s['label_end']
            _require(source[la:lb]==actors[s['speaker_actor_id']]['source_label'] and lb+2==a and source[lb:a]==': ','Speaker label/utterance boundary mismatch.')
        for a in _index(r['anchors'],'anchor_id').values():
            start,end=a['start'],a['end'];s=segments.get(a['segment_id'])
            _require(type(start) is int and type(end) is int and 0<=start<end<=len(source),'Invalid anchor offsets.')
            _require(a['source_sha256']==r['source_sha256'] and source[start:end]==a['text'],'Evidence does not exactly match source.')
            _require(s is not None and s['start']<=start<end<=s['end'] and a['speaker_actor_id']==s['speaker_actor_id'],'Evidence crosses or misattributes a speaker turn.')
        old=_index(r['original_proposals'],'code_id');now=_index(r['proposals'],'code_id')
        _require(old.keys()==now.keys() and 1<=len(now)<=3,'Codes cannot silently appear/disappear during review.')
        _require(r['original_proposals_sha256']==digest(r['original_proposals']),'Original AI proposals changed.')
        for cid,c in old.items():
            _code(c,r)
            _require(c['original_scope_input']==dict(label=c['label'],claim=c['claim'],evidence_ids=c['evidence_ids'],source_sha256=r['source_sha256']),'Original definition input does not match original proposal.')
            _require(all(o==c['origin'] for o in c['field_origins'].values()),'Initial fields must retain their initial AI author.')
            initial[rid,cid]=deepcopy(c)
        for cid,c in now.items():_code(c,r);current[rid,cid]=c
        _require(r['construction_source_sha256']==r['source_sha256'],'Construction source changed.')
    replay=deepcopy(initial);previous=digest({r['record_id']:r['original_proposals_sha256'] for r in records.values()})
    history=packet.get('review_history');_require(type(history) is list and len(history)<=256,'Invalid revision history.')
    for event in history:
        _require(event['previous_event_sha256']==previous and event['event_sha256']==digest({k:v for k,v in event.items() if k!='event_sha256'}),'Revision chain changed.')
        key=event['record_id'],event['code_id'];_require(key in replay,'Revision addresses unknown code.')
        _require(event['reviewer_kind'] in ('AI','human_declared'),'Unknown editor provenance.')
        _string(event['reviewer_id']);_string(event['reason'],2000)
        _require(event['human_adjudication'] is False and event['training_admission'] is False,'An edit is not adjudication/admission.')
        changes=event['new_values'];_require(changes and set(changes)<=EDITABLE,'Revision changes immutable fields.')
        c=replay[key]
        _require(event['old_values']=={k:c[k] for k in changes},'Revision does not match prior wording/roles.')
        _require(event['old_origins']=={k:c['field_origins'][k] for k in changes},'Revision origin history mismatch.')
        for k,v in changes.items():
            c[k]=deepcopy(v);c['field_origins'][k]=dict(kind=event['reviewer_kind'],author_id=event['reviewer_id'])
        previous=event['event_sha256']
    _require(replay==current,'Current wording or evidence was changed without a revision event.')
    return dict(records=len(records),families=len({r['family_id'] for r in records.values()}),codes=len(current),
        revisions=len(history),training_eligible=False,human_adjudications=0,
        scope='Source/identity/provenance consistency only; no semantic accuracy, rights or independence established.')

def revise_code(packet,record_id,code_id,changes,*,reviewer_kind,reviewer_id,reason):
    validate_packet(packet)
    _require(reviewer_kind in ('AI','human_declared'),'Choose explicit AI or self-declared human editing.')
    _string(reviewer_id);_string(reason,2000)
    _require(type(changes) is dict and changes and set(changes)<=EDITABLE,'Only explicit wording/evidence-role edits are allowed.')
    value=deepcopy(packet);record=next((r for r in value['records'] if r['record_id']==record_id),None)
    _require(record is not None,'Unknown record.')
    code=next((c for c in record['proposals'] if c['code_id']==code_id),None);_require(code is not None,'Unknown code.')
    changes={k:deepcopy(v) for k,v in changes.items() if (set(v)!=set(code[k]) if k in ROLE_FIELDS and type(v) is list else v!=code[k])}
    _require(changes,'No changed interpretation or role selection.')
    previous=value['review_history'][-1]['event_sha256'] if value['review_history'] else digest({r['record_id']:r['original_proposals_sha256'] for r in value['records']})
    event=dict(event='provisional_candidate_revision',record_id=record_id,code_id=code_id,
        reviewer_kind=reviewer_kind,reviewer_id=reviewer_id,reason=reason.strip(),
        authorship_status='AI' if reviewer_kind=='AI' else 'self_declared_not_identity_authenticated',
        previous_event_sha256=previous,before_packet_sha256=packet['packet_sha256'],
        old_values={k:deepcopy(code[k]) for k in changes},new_values=changes,
        old_origins={k:deepcopy(code['field_origins'][k]) for k in changes},
        human_adjudication=False,training_admission=False)
    event['event_sha256']=digest(event)
    for k,v in changes.items():
        code[k]=v;code['field_origins'][k]=dict(kind=reviewer_kind,author_id=reviewer_id)
    value['review_history'].append(event);seal(value);validate_packet(value)
    return value

def _cell(value):
    value=str(value)
    # CSV is for reading/review, not executable spreadsheet formulas.
    return "'"+value if value.lstrip().startswith(('=','+','-','@','\t','\r')) else value

def export_csv(packet):
    validate_packet(packet);stream=io.StringIO(newline='');writer=csv.writer(stream)
    writer.writerow(['record_id','family_id','code_id','speaker','label','claim','definition','support_exact','qualifications_exact','counterexamples_exact','modality','conditions','limits','alternatives','field_origins','unresolved_decisions','scope_input_binding','training_eligible','human_adjudication'])
    for r in packet['records']:
        anchors=_index(r['anchors'],'anchor_id')
        for c in r['proposals']:
            evidence=[json.dumps([anchors[i] for i in c[k]],ensure_ascii=False) for k in ROLE_FIELDS]
            row=[r['record_id'],r['family_id'],c['code_id'],c['speaker_actor_id'],c['label'],c['claim'],c['definition'],*evidence,c['modality'],json.dumps(c['conditions'],ensure_ascii=False),json.dumps(c['limits'],ensure_ascii=False),json.dumps(c['alternatives'],ensure_ascii=False),json.dumps(c['field_origins'],ensure_ascii=False),json.dumps(r['unresolved_decisions'],ensure_ascii=False),scope_binding_status(c),'false','false']
            writer.writerow([_cell(v) for v in row])
    return stream.getvalue()

def export_html(packet):
    validate_packet(packet);e=lambda v:escape(str(v),quote=True)
    rows=['<!doctype html><html lang="en"><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'"><title>Education candidate review</title><style>body{max-width:1050px;margin:2em auto;font:16px/1.5 system-ui;color:#17252b}pre{white-space:pre-wrap;background:#f4f6f7;padding:1em}article{border-top:2px solid #aec2c9;margin-top:2em}blockquote{border-left:3px solid #708890;padding-left:1em}small{overflow-wrap:anywhere}details{margin:1em 0}.notice{background:#fff3cd;padding:1em}</style><body><h1>Education candidate review packet</h1><p class="notice">Original AI-authored fiction and provisional AI interpretations. No authentic participant findings, human adjudication, training eligibility or validated themes. Interpretations and objections remain reviewable. Editing authorship is distinct from semantic acceptance.</p>']
    rows.append('<p>Packet digest: <small>'+e(packet['packet_sha256'])+'</small></p>')
    for r in packet['records']:
        rows.extend(['<article><h2>'+e(r['record_id'])+' · '+e(r['family_id'])+'</h2><p>'+e(r['analytic_purpose'])+'</p>','<pre>'+e(r['source_text'])+'</pre><small>Source SHA-256: '+e(r['source_sha256'])+'</small>','<h3>Unresolved decisions</h3><ul>'+''.join('<li>'+e(v)+'</li>' for v in r['unresolved_decisions'])+'</ul>'])
        anchors=_index(r['anchors'],'anchor_id')
        for c in r['proposals']:
            rows.extend(['<h3>'+e(c['code_id'])+': '+e(c['label'])+'</h3><p><b>Claim:</b> '+e(c['claim'])+'</p><p><b>Scope:</b> '+e(c['definition'])+'</p>','<p><b>Speaker:</b> '+e(c['speaker_actor_id'])+'; <b>Modality:</b> '+e(c['modality'])+'</p>'])
            rows.append('<p><b>Definition input:</b> '+e(scope_binding_status(c))+'; definition remains provisional.</p>')
            for k in ROLE_FIELDS:
                rows.append('<p><b>'+e(k)+'</b>: '+('none selected' if not c[k] else '')+'</p>')
                for aid in c[k]:
                    a=anchors[aid];rows.append('<blockquote>'+e(a['text'])+'<br><small>'+e(aid)+' · '+e(a['speaker_actor_id'])+' · ['+str(a['start'])+','+str(a['end'])+')</small></blockquote>')
            rows.append('<details><summary>Alternatives, limits and field authorship</summary><pre>'+e(json.dumps({k:c[k] for k in ('conditions','limits','alternatives','field_origins')},ensure_ascii=False,indent=2))+'</pre></details>')
        rows.append('<details><summary>Original AI proposals, independent review and rights</summary><pre>'+e(json.dumps({k:r.get(k) for k in ('original_proposals','independent_review','rights','split','ancestry')},ensure_ascii=False,indent=2))+'</pre></details></article>')
    rows.append('<details><summary>Complete revision history</summary><pre>'+e(json.dumps(packet['review_history'],ensure_ascii=False,indent=2))+'</pre></details></body></html>')
    return '\n'.join(rows)

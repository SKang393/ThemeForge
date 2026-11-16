"""Separate source-exact boundary recovery. Never invent a pooled parent vector."""
from collections import Counter
from copy import deepcopy
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import re
import threading
import time
from uuid import uuid4

from . import study_encoder as encoder
from .semantic_neighbors import digest
from .study_batches import VERSION as STUDY_VERSION, validate_study

VERSION='themeforge.study-segment-recovery.v1'
POLICY='sentence-cores-254-adjacent-context.v1'
MAX_PAYLOAD_TOKENS=254
MAX_BYTES=64*1024**2
_HEADING=re.compile(r'(?mi)^[ \t]*(?P<heading>Participant\s+\d+\s+Responses\b|Interview\s+with\s+Participant\s+\d+\b)')
NOTICE=('Source partitions are not respondent assertions or validated speaker turns. '
        'Inherited attribution is unresolved; adjacent context is referent-only and not guaranteed complete.')


def _hash_text(text):return sha256(text.encode('utf-8')).hexdigest()


def _seal(value):
    value['recovery_sha256']=digest({k:v for k,v in value.items() if k!='recovery_sha256'})
    return value


def _span(source,a,b,**extra):
    return dict(source_name=source['source_name'],source_hash=source['source_hash'],
        source_start=a,source_end=b,text=source['text'][a:b],**extra)


def _tokenizer(profile_path):
    from tokenizers import Tokenizer
    _,_,path,_=encoder.encoder.verify_profile(profile_path,encoder.encoder.file_sha256(profile_path))
    value=Tokenizer.from_file(str(path));value.no_padding();value.no_truncation()
    return value


def _ids(tokenizer,text):
    value=tokenizer.encode(text,add_special_tokens=False)
    ids=list(value.ids)
    if not ids or any(type(x) is not int or x<0 for x in ids):
        raise ValueError('Pinned tokenizer must supply every exact nonblank core token.')
    return ids


def _coverage(value):
    segments=value['plan']['segments'];saved=value['encoder_ledger']
    states=({s['id']:saved['coverage'][s['id']]['status'] for s in segments} if saved else
        {s['id']:('failed' if s['planning_status']=='failed_oversized_sentence' else 'pending') for s in segments})
    counts=dict(Counter(states.values()));done=counts.get('completed',0)
    return dict(parent_unit_id=value['parent_unit_id'],original_parent_status='failed',
        status=('covered_via_segments_not_attribution_validated' if done==len(segments) else
            'partially_encoded_via_segments' if done else 'segment_encoding_not_completed'),
        segment_counts=counts,total_segments=len(segments),
        completed_core_characters=sum(s['source_end']-s['source_start'] for s in segments if states[s['id']]=='completed'),
        total_core_characters=len(value['plan']['parent']['text']),parent_vector_created=False,
        attribution_validated=False,semantic_coverage_measured=False,notice=NOTICE)


def prepare_segment_recovery(prior,parent_unit_id,profile,*,tokenizer=None):
    """Plan contiguous sentence cores with exact character and native-token proof.

    tokenizer injection is for deterministic tests. Execution independently
    recreates this plan from the real pinned local tokenizer before any worker.
    No generation reserve applies to this encoder: CLS+SEP=2, output tokens=0.
    """
    if isinstance(prior,(str,Path)):prior=encoder.load_encoder_ledger(prior)
    encoder.validate_encoder_ledger(prior)
    if parent_unit_id not in prior['coverage'] or prior['coverage'][parent_unit_id]['status']!='failed':
        raise ValueError('Boundary recovery requires an explicitly failed original parent, never a completed unit.')
    path,binding=encoder._profile(profile)
    if binding!=prior['profile_binding']:
        raise ValueError('Boundary recovery requires the original exact local encoder profile.')
    tok=tokenizer if tokenizer is not None else _tokenizer(path)
    parent=next(u for u in prior['study']['units'] if u['id']==parent_unit_id)
    source=next(s for s in prior['study']['sources'] if s['source_name']==parent['source_name'])
    start,end=parent['source_start'],parent['source_end']
    hard=sorted({start,end}|{start+m.start('heading') for m in _HEADING.finditer(parent['text'])})
    sentence_starts={s['source_start'] for s in parent.get('sentences',[]) if start<s['source_start']<end}
    segments=[]
    for block_start,block_end in zip(hard,hard[1:]):
        cuts=sorted({block_start,block_end}|{c for c in sentence_starts if block_start<c<block_end})
        atoms=[(a,b) for a,b in zip(cuts,cuts[1:])]
        cursor=0
        while cursor<len(atoms):
            last=cursor;a,b=atoms[cursor];ids=_ids(tok,source['text'][a:b])
            oversized=len(ids)>MAX_PAYLOAD_TOKENS
            if not oversized:
                while last+1<len(atoms):
                    proposed=_ids(tok,source['text'][a:atoms[last+1][1]])
                    if len(proposed)>MAX_PAYLOAD_TOKENS:break
                    last+=1;b=atoms[last][1];ids=proposed
            uid='ES-'+digest([POLICY,parent_unit_id,source['source_hash'],a,b,binding['tokenizer_sha256']])[:20]
            contexts=[]
            for role,index in [('preceding_sentence_referent_only',cursor-1),('following_sentence_referent_only',last+1)]:
                if 0<=index<len(atoms):
                    left,right=atoms[index]
                    contexts.append(_span(source,left,right,role=role,payload_tokens=len(_ids(tok,source['text'][left:right])),
                        vectorized=False,context_only_not_support=True))
            row=_span(source,a,b,id=uid,parent_unit_id=parent_unit_id,parent_source_start=start,parent_source_end=end,
                text_sha256=_hash_text(source['text'][a:b]),payload_token_ids=ids,payload_tokens=len(ids),
                special_tokens=2,input_tokens=len(ids)+2,encoder_output_tokens=0,
                planning_status='failed_oversized_sentence' if oversized else 'pending',
                structural_block_start=block_start,structural_block_end=block_end,
                context_spans=contexts,context_payload_tokens=sum(s['payload_tokens'] for s in contexts),
                attribution_status='unresolved_composite_parent',original_parent_speaker=parent['speaker'])
            segments.append(row);cursor=last+1
    derived=[]
    for segment in segments:
        a,b=segment['source_start'],segment['source_end']
        derived.append(_span(source,a,b,id=segment['id'],speaker='Unresolved composite source attribution',
            parent_unit_id=parent_unit_id,parent_source_start=start,parent_source_end=end,
            original_parent_speaker=parent['speaker'],eligible=True,status='pending',
            flags=['boundary_recovery_source_partition','composite_attribution_unresolved','context_required'],
            context_start=parent['context_start'],context_end=parent['context_end'],context_text=parent['context_text'],
            reference_context='',relation_cues=[],
            sentences=[deepcopy(s) for s in parent.get('sentences',[]) if a<=s['source_start'] and s['source_end']<=b],
            execution_context_spans=deepcopy(segment['context_spans']),
            execution_context_policy='boundary_recovery_adjacent_context_only',attribution_notice=NOTICE))
    study=dict(version=STUDY_VERSION,sources=[deepcopy(source)],units=derived,
        speaker_label_policy='boundary_recovery_unresolved_attribution',coverage_notice=NOTICE)
    study['study_sha256']=digest(study);validate_study(study)
    plan=dict(policy=POLICY,before_ledger_sha256=prior['ledger_sha256'],parent=deepcopy(parent),
        original_parent_coverage=deepcopy(prior['coverage'][parent_unit_id]),profile_binding=binding,
        max_payload_tokens=MAX_PAYLOAD_TOKENS,special_tokens=2,encoder_output_tokens=0,
        structural_hard_cuts=hard,segments=segments,derived_study=study,
        context_policy='exact adjacent sentence within structural block; context excluded from encoder node.text',
        parent_archive_context=_span(source,parent['context_start'],parent['context_end']),notice=NOTICE)
    plan['plan_sha256']=digest(plan)
    value=dict(version=VERSION,before_ledger_sha256=prior['ledger_sha256'],original_encoder_ledger=deepcopy(prior),
        parent_unit_id=parent_unit_id,plan=plan,encoder_ledger=None,status='planned',work_periods=[])
    value['recovery_coverage']=_coverage(value);_seal(value);validate_segment_recovery(value)
    return value


def validate_segment_recovery(value):
    """Validate immutable source/history and saved vectors; no inference proof."""
    if type(value) is not dict or value.get('version')!=VERSION:
        raise ValueError('Not a source-bound segment recovery envelope.')
    if value.get('recovery_sha256')!=digest({k:v for k,v in value.items() if k!='recovery_sha256'}):
        raise ValueError('Segment recovery digest is stale.')
    original=value['original_encoder_ledger'];encoder.validate_encoder_ledger(original)
    if value['before_ledger_sha256']!=original['ledger_sha256']:
        raise ValueError('Original before-ledger SHA256 binding is stale.')
    uid=value['parent_unit_id'];parent=next(u for u in original['study']['units'] if u['id']==uid)
    if original['coverage'][uid]['status']!='failed':
        raise ValueError('Only failed parents support boundary recovery.')
    plan=value['plan']
    if (plan.get('plan_sha256')!=digest({k:v for k,v in plan.items() if k!='plan_sha256'})
            or plan['before_ledger_sha256']!=original['ledger_sha256'] or plan['parent']!=parent
            or plan['original_parent_coverage']!=original['coverage'][uid]
            or plan['profile_binding']!=original['profile_binding'] or plan['policy']!=POLICY
            or plan['max_payload_tokens']!=254 or plan['special_tokens']!=2 or plan['encoder_output_tokens']!=0):
        raise ValueError('Source/parent/profile/segmentation policy binding is stale.')
    study=plan['derived_study'];validate_study(study)
    sources={s['source_name']:s for s in original['study']['sources']};source=sources[parent['source_name']]
    if study['sources']!=[source]:raise ValueError('Derived source differs from the immutable original.')
    segments=plan['segments'];units=study['units']
    if not segments or len(segments)!=len(units):raise ValueError('Every core requires one exact derived unit.')
    expected_hard=sorted({parent['source_start'],parent['source_end']}|
        {parent['source_start']+m.start('heading') for m in _HEADING.finditer(parent['text'])})
    if plan['structural_hard_cuts']!=expected_hard:raise ValueError('Structural source heading cuts changed.')
    cursor=parent['source_start']
    for s,u in zip(segments,units):
        a,b=s['source_start'],s['source_end'];ids=s['payload_token_ids']
        if (a!=cursor or not a<b<=parent['source_end'] or source['text'][a:b]!=s['text']
                or s['source_hash']!=source['source_hash'] or s['source_name']!=source['source_name']
                or s['parent_unit_id']!=uid or s['text_sha256']!=_hash_text(s['text'])
                or type(ids) is not list or not ids or any(type(t) is not int or t<0 for t in ids)
                or s['payload_tokens']!=len(ids) or s['input_tokens']!=len(ids)+2
                or s['special_tokens']!=2 or s['encoder_output_tokens']!=0
                or s['planning_status']!=('failed_oversized_sentence' if len(ids)>254 else 'pending')
                or any(a<c<b for c in expected_hard)):
            raise ValueError('Nonoverlapping core/token partition is invalid.')
        if any(s.get(k)!=u.get(k) for k in ('id','parent_unit_id','source_name','source_hash','source_start','source_end','text')):
            raise ValueError('Derived unit does not bind its exact source core.')
        expected_id='ES-'+digest([POLICY,uid,source['source_hash'],a,b,plan['profile_binding']['tokenizer_sha256']])[:20]
        block_start,block_end=s['structural_block_start'],s['structural_block_end']
        if (s['id']!=expected_id or (block_start,block_end) not in list(zip(expected_hard,expected_hard[1:]))
                or not block_start<=a<b<=block_end):
            raise ValueError('Segment identity or structural block binding is stale.')
        atom_cuts=sorted({block_start,block_end}|{p['source_start'] for p in parent.get('sentences',[])
            if block_start<p['source_start']<block_end})
        if a not in atom_cuts or b not in atom_cuts:raise ValueError('Core boundary is not a sentence/heading boundary.')
        contexts=s['context_spans']
        if (len(contexts)>2 or u.get('execution_context_spans')!=contexts
                or u.get('execution_context_policy')!='boundary_recovery_adjacent_context_only'):
            raise ValueError('Execution contexts are not the explicit adjacent source spans.')
        expected_contexts=[];left_index=atom_cuts.index(a);right_index=atom_cuts.index(b)
        if left_index>0:expected_contexts.append(('preceding_sentence_referent_only',atom_cuts[left_index-1],a))
        if right_index<len(atom_cuts)-1:expected_contexts.append(('following_sentence_referent_only',b,atom_cuts[right_index+1]))
        if [(c['role'],c['source_start'],c['source_end']) for c in contexts]!=expected_contexts:
            raise ValueError('Adjacent context selection was silently omitted or moved across a structural boundary.')
        for c in contexts:
            left,right=c['source_start'],c['source_end']
            if (c['source_name']!=source['source_name'] or c['source_hash']!=source['source_hash']
                    or not 0<=left<right<=len(source['text']) or source['text'][left:right]!=c['text']
                    or c.get('vectorized') is not False or c.get('context_only_not_support') is not True
                    or not block_start<=left<right<=block_end or not (right<=a or left>=b)
                    or type(c['payload_tokens']) is not int or c['payload_tokens']<1):
                raise ValueError('Neighbor context must remain exact, separate, and context-only.')
        if s['context_payload_tokens']!=sum(c['payload_tokens'] for c in contexts):
            raise ValueError('Context token accounting is stale.')
        cursor=b
    if cursor!=parent['source_end'] or ''.join(s['text'] for s in segments)!=parent['text']:
        raise ValueError('Core partition does not reconstruct all original characters including whitespace.')
    if plan['parent_archive_context']!=_span(source,parent['context_start'],parent['context_end']):
        raise ValueError('Full immutable parent context was changed.')
    saved=value['encoder_ledger']
    if saved is not None:
        encoder.validate_encoder_ledger(saved)
        if saved['study']!=study or saved['profile_binding']!=original['profile_binding']:
            raise ValueError('Saved segment encoding differs from this plan/source/profile.')
        if set(saved['vectors']) & set(original['vectors']):raise ValueError('Original completed units cannot be reencoded.')
        for segment in segments:
            if segment['planning_status']=='failed_oversized_sentence' and saved['coverage'][segment['id']]['status']!='failed':
                raise ValueError('Indivisible oversized sentence cannot be encoded under this recovery policy.')
            if saved['coverage'][segment['id']]['status']=='completed':
                batch=next(b for b in saved['batches'] if b['id']==saved['batch_ids'][segment['id']])
                index=batch['unit_ids'].index(segment['id'])
                diagnostic=batch['encoded']['provenance']['tokenization'][index]
                chunks=diagnostic['chunks']
                if (diagnostic['payload_tokens']!=segment['payload_tokens'] or len(chunks)!=1
                        or chunks[0]['input_ids']!=[101]+segment['payload_token_ids']+[102]):
                    raise ValueError('Completed core must bind the exact planned token IDs in one encoder window.')
    for period in value['work_periods']:
        if (type(period['wall_seconds']) not in (int,float) or not math.isfinite(period['wall_seconds']) or period['wall_seconds']<=0
                or type(period['max_units_per_run']) is not int or period['max_units_per_run']<1
                or period['chunk_size']!=1 or period['cpu_threads']!=2
                or period['sampled_tree_rss_limit_bytes']!=2*1024**3 or period['available_ram_gate_bytes']!=3*1024**3):
            raise ValueError('Boundary recovery work budget is invalid.')
    if value['recovery_coverage']!=_coverage(value):raise ValueError('Segment recovery coverage is stale.')


def recovery_coverage(value,study=None):
    validate_segment_recovery(value)
    if study is not None:
        validate_study(study)
        parent=value['plan']['parent'];matches=[u for u in study['units'] if u['id']==parent['id']]
        if len(matches)!=1 or any(matches[0][k]!=parent[k] for k in ('source_name','source_hash','source_start','source_end','text')):
            raise ValueError('Recovery overlay requires the same unchanged source parent.')
    return deepcopy(value['recovery_coverage'])


def run_segment_recovery(value,profile,*,prior=None,output_directory,wall_seconds=120,
                         max_units_per_run=64,cancel_event=None,on_progress=None):
    """Run new segment IDs only; checkpoint a resumable envelope after each worker.

    Fresh output branch per invocation, one sequential owned worker at a time.
    Existing encode_study supplies CPU=2, sampled 2 GiB and 3 GiB available gate.
    No parent vector is created and no legacy coverage/history is changed.
    """
    started=time.perf_counter();validate_segment_recovery(value)
    if type(wall_seconds) not in (int,float) or not math.isfinite(wall_seconds) or wall_seconds<=0:
        raise ValueError('Use a positive finite boundary-recovery work period.')
    if type(max_units_per_run) is not int or max_units_per_run<1:
        raise ValueError('Use a positive integer segment work-period limit.')
    if prior is not None:
        validate_segment_recovery(prior)
        if prior['plan']['plan_sha256']!=value['plan']['plan_sha256']:
            raise ValueError('Resume requires unchanged source/tokenizer/segmentation settings.')
        value=prior
    fresh=prepare_segment_recovery(value['original_encoder_ledger'],value['parent_unit_id'],profile)
    if fresh['plan']['plan_sha256']!=value['plan']['plan_sha256']:
        raise ValueError('Real pinned tokenizer/source/settings differ from the saved segment plan.')
    root=Path(output_directory)
    if not root.is_absolute() or str(root).startswith(('\\\\','//')) or '://' in str(root):
        raise ValueError('Choose an absolute local recovery output directory.')
    branch=root.resolve()/('segment-recovery-'+uuid4().hex);branch.mkdir(parents=True,exist_ok=False)
    result=deepcopy(value);saved=result['encoder_ledger'];sequence=0
    period=dict(id=branch.name,wall_seconds=wall_seconds,max_units_per_run=max_units_per_run,chunk_size=1,
        cpu_threads=2,sampled_tree_rss_limit_bytes=2*1024**3,available_ram_gate_bytes=3*1024**3,
        status='running',elapsed_seconds=0.)
    result['work_periods'].append(period)
    if saved is None and any(s['planning_status']=='failed_oversized_sentence' for s in result['plan']['segments']):
        stop=threading.Event();stop.set()
        saved=encoder.encode_study(result['plan']['derived_study'],profile,cancel_event=stop)
        for segment in result['plan']['segments']:
            if segment['planning_status']!='failed_oversized_sentence':continue
            uid=segment['id'];bid='SEGMENT-PLAN-FAIL-'+digest([result['plan']['plan_sha256'],uid])[:20]
            saved['batches'].append(dict(id=bid,unit_ids=[uid],status='failed',
                error='Indivisible sentence exceeds the explicit 254-payload-token segment policy; no inference attempted.'))
            saved['coverage'][uid].update(status='failed',reason=saved['batches'][-1]['error'],batch_id=bid)
            saved['batch_ids'][uid]=bid
        encoder._counts(saved);encoder._seal(saved);encoder.validate_encoder_ledger(saved)
    worker_output=branch/'workers'
    def attach(current):
        nonlocal sequence
        sequence+=1;result['encoder_ledger']=current
        period.update(status=current['status'],elapsed_seconds=time.perf_counter()-started)
        result['recovery_coverage']=_coverage(result)
        result['status']=result['recovery_coverage']['status'];_seal(result)
        text=export_segment_recovery_json(result)+'\n'
        with (branch/f'segment-recovery-progress-{sequence%2}.json').open('wb') as stream:
            stream.write(text.encode('utf-8'));stream.flush();os.fsync(stream.fileno())
    def progress(event):
        paths=list(worker_output.glob('run-*/encoder-ledger-progress-*.json'))
        if paths:
            current=max((encoder.load_encoder_ledger(p) for p in paths),key=lambda l:l['runs'][-1]['checkpoint_sequence'])
            attach(current)
        if on_progress is not None:on_progress(dict(event,recovery_coverage=deepcopy(result['recovery_coverage'])))
    remaining=wall_seconds-(time.perf_counter()-started)
    if remaining<=0:
        if saved is not None:attach(saved)
        period.update(status='paused_before_worker_time_limit',elapsed_seconds=time.perf_counter()-started)
        result['status']='paused_before_worker_time_limit';_seal(result)
    else:
        saved=encoder.encode_study(result['plan']['derived_study'],profile,prior=saved,output_directory=worker_output,
            wall_seconds=remaining,max_units_per_run=max_units_per_run,chunk_size=1,
            cancel_event=cancel_event,on_progress=progress)
        attach(saved)
    (branch/'segment-recovery.json').write_text(export_segment_recovery_json(result)+'\n',encoding='utf-8')
    return result


def export_segment_recovery_json(value):
    validate_segment_recovery(value)
    text=json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)
    if len(text.encode('utf-8'))>MAX_BYTES:raise ValueError('Recovery envelope exceeds its 64 MiB bound.')
    return text


def load_segment_recovery(path):
    path=encoder.encoder.local_file(path)
    if path.stat().st_size>MAX_BYTES:raise ValueError('Recovery envelope exceeds its byte bound.')
    value=json.loads(path.read_text(encoding='utf-8-sig'));validate_segment_recovery(value)
    return value

"""Pure source-record retrieval: distinct-parent max cosine, never parent means."""
from collections import Counter,defaultdict
from copy import deepcopy
from html import escape
import json
import math
import time

from .semantic_neighbors import digest
from . import study_encoder as encoder
from .study_batches import validate_study
from .study_neighbors import _limits,NeighborLimits,validate_study_neighbors
from .study_segments import validate_segment_recovery

VERSION='themeforge.recovered-parent-neighbors.v1'
MAX_JSON_BYTES=128*1024**2
ALGORITHM=dict(policy='mutual',k=2,min_similarity=.30,source_distinct='source_hash',
    ranking='distinct_parent_max_exact_member_cosine',tie_break='parent_id_then_aligned_member_ids',
    parent_vector_aggregation='none',same_parent_pairs=False,same_source_pairs=False)
WARNINGS=[
    'Nodes are source records, not participants; no participant or semantic recurrence count is supported.',
    'Maximum member cosine can favor parents with more or longer segments: residual length/opportunity bias remains.',
    'Different source hashes do not establish independent participants or independent studies.',
    'Legacy whole-turn vectors can mix source sections; structural attribution does not unmix their semantic content.',
    'Input hashes and provenance bind trusted supplied artifacts; they do not independently prove model output or semantic accuracy.',
    'Exact cores and context preserve source text, not validated assertions, attribution, or semantic equivalence.',
    'Same-parent and same-source comparisons are excluded; no parent mean or transitive category is created.',
    'Incomplete scan endpoints cannot publish pairs; partial coverage is not evidence of no relationship.']


def _seal(value):
    value['result_sha256']=digest({k:v for k,v in value.items() if k!='result_sha256'})
    return value


def _attributions(units,sources):
    from .source_attribution import resolve_attribution
    source_map={s['source_name']:s for s in sources}
    return {u['id']:resolve_attribution(u,source_map[u['source_name']]) for u in units}


def _pins(value):
    if value is None:return {}
    allowed={'baseline_file_sha256','recovery_file_sha256'}
    if type(value) is not dict or set(value)-allowed:
        raise ValueError('Input file pins use baseline_file_sha256 and/or recovery_file_sha256.')
    for v in value.values():
        if type(v) is not str or len(v)!=64 or any(c not in '0123456789abcdef' for c in v):
            raise ValueError('Input file SHA256 pins must be lowercase hexadecimal.')
    return deepcopy(value)


def _member_layout(study,units,vectors,recovered_parent):
    validate_study(study)
    originals={u['id']:u for u in study['units']};members={};spans=defaultdict(list)
    if len({u['id'] for u in units})!=len(units):raise ValueError('Duplicate graph unit IDs.')
    for unit in units:
        uid=unit['id'];pid=unit.get('parent_unit_id',uid)
        if pid not in originals:raise ValueError('Every graph member needs one original parent record.')
        parent=originals[pid];source=next(s for s in study['sources'] if s['source_name']==unit['source_name'])
        if (unit['source_hash']!=source['source_hash'] or not parent['source_start']<=unit['source_start']<unit['source_end']<=parent['source_end']
                or source['text'][unit['source_start']:unit['source_end']]!=unit['text']
                or unit['source_name']!=parent['source_name'] or unit['source_hash']!=parent['source_hash']):
            raise ValueError('Graph member span is not exact within its original source parent.')
        if uid in vectors:
            encoder._vector(vectors[uid]);members[uid]=pid
            spans[unit['source_hash']].append((unit['source_start'],unit['source_end'],uid))
    if set(vectors)!=set(members):raise ValueError('Vectors require exact graph units.')
    if recovered_parent in vectors:raise ValueError('Parent and recovered child vectors cannot coexist.')
    for rows in spans.values():
        rows.sort()
        if any(a[1]>b[0] for a,b in zip(rows,rows[1:])):
            raise ValueError('Overlapping vector source spans are not permitted.')
    return members


def _top_update(top,item):
    top.append(item);top.sort(key=lambda r:(-r['cosine'],r['parent_id'],r['member_ids']))
    del top[2:]


def _scan(units,vectors,members,limits,*,stop_after=None,deadline=None,cancel_event=None,on_progress=None):
    lookup={u['id']:u for u in units};groups=defaultdict(list)
    normalized={}
    for uid,row in vectors.items():
        norm=math.hypot(*row);normalized[uid]=[v/norm for v in row]
    for uid,pid in members.items():groups[pid].append(uid)
    for row in groups.values():row.sort()
    parents=sorted(groups);sources={p:lookup[groups[p][0]]['source_hash'] for p in parents}
    source_parent_counts=Counter(sources.values());source_member_counts=Counter(lookup[u]['source_hash'] for u in vectors)
    parent_expected={p:len(parents)-source_parent_counts[sources[p]] for p in parents}
    member_expected={u:len(vectors)-source_member_counts[lookup[u]['source_hash']] for u in vectors}
    parent_scored=Counter();member_scored=Counter();top={p:[] for p in parents};count=0;parent_count=0;stop=None
    for index,left in enumerate(parents):
        for right in parents[index+1:]:
            if sources[left]==sources[right]:continue
            best=None
            for a in groups[left]:
                for b in groups[right]:
                    if cancel_event is not None and cancel_event.is_set():stop='cancelled'
                    elif stop_after is not None and count>=stop_after:stop='pair_limit'
                    elif deadline is not None and time.perf_counter()>=deadline:stop='time_limit'
                    if stop:break
                    score=max(-1.,min(1.,math.fsum(x*y for x,y in zip(normalized[a],normalized[b]))))
                    item=dict(cosine=score,member_ids=[a,b])
                    if best is None or (-score,item['member_ids'])<(-best['cosine'],best['member_ids']):best=item
                    count+=1;member_scored[a]+=1;member_scored[b]+=1
                if stop:break
            if stop:break
            parent_count+=1;parent_scored[left]+=1;parent_scored[right]+=1
            _top_update(top[left],dict(best,parent_id=right))
            _top_update(top[right],dict(best,parent_id=left))
        if stop:break
        if on_progress is not None:on_progress(dict(phase='recovered_parent_scan',completed_outer_parents=index+1,
            parent_records=len(parents),member_pairs_scored=count,parent_pairs_scored=parent_count))
    parent_coverage={p:dict(status='complete' if parent_scored[p]==parent_expected[p] else 'partial',
        pairs_scored=parent_scored[p],expected_cross_source_parent_pairs=parent_expected[p]) for p in parents}
    member_coverage={u:dict(status='complete' if member_scored[u]==member_expected[u] else 'partial',
        pairs_scored=member_scored[u],expected_cross_source_member_pairs=member_expected[u]) for u in vectors}
    edges=[]
    for left in parents:
        for rank,item in enumerate(top[left],1):
            right=item['parent_id']
            if left>=right or item['cosine']<.30 or parent_coverage[left]['status']!='complete' or parent_coverage[right]['status']!='complete':continue
            reverse=next((i for i,r in enumerate(top[right],1) if r['parent_id']==left),None)
            if reverse is None:continue
            edges.append(dict(parent_ids=[left,right],member_ids=item['member_ids'],cosine=item['cosine'],
                ranks=[rank,reverse],source_hashes=[sources[left],sources[right]]))
    edges.sort(key=lambda e:e['parent_ids']);connected={p for e in edges for p in e['parent_ids']}
    connected_members={u for e in edges for u in e['member_ids']}
    return dict(edges=edges,parent_top_neighbors=top,parent_scan_coverage=parent_coverage,member_scan_coverage=member_coverage,
        parent_isolates=sorted(p for p in parents if parent_coverage[p]['status']=='complete' and p not in connected),
        isolates=sorted(u for u in vectors if member_coverage[u]['status']=='complete' and u not in connected_members),
        comparisons=dict(expected_cross_source_member_pairs=sum(member_expected.values())//2,member_pairs_scored=count,
            expected_cross_source_parent_pairs=sum(parent_expected.values())//2,parent_pairs_scored=parent_count,stop_reason=stop),
        status=stop or 'completed')


def _difference(old,new):
    before={tuple(e.get('parent_ids',e['member_ids'])):e for e in old}
    after={tuple(e['parent_ids']):e for e in new};a,b=set(before),set(after)
    old_members={tuple(sorted(e['member_ids'])) for e in old};new_members={tuple(sorted(e['member_ids'])) for e in new}
    lists=lambda rows:[list(r) for r in sorted(rows)]
    return dict(parent_pairs=dict(added=lists(b-a),removed=lists(a-b),unchanged=lists(a&b),
            added_count=len(b-a),removed_count=len(a-b),unchanged_count=len(a&b)),
        member_pairs=dict(added=lists(new_members-old_members),removed=lists(old_members-new_members),
            unchanged=lists(old_members&new_members),added_count=len(new_members-old_members),
            removed_count=len(old_members-new_members),unchanged_count=len(old_members&new_members)),
        rank_changes=[dict(parent_ids=list(p),before=before[p]['ranks'],after=after[p]['ranks'])
            for p in sorted(a&b) if before[p]['ranks']!=after[p]['ranks']],
        best_member_changes=[dict(parent_ids=list(p),before=before[p]['member_ids'],after=after[p]['member_ids'])
            for p in sorted(a&b) if tuple(sorted(before[p]['member_ids']))!=tuple(sorted(after[p]['member_ids']))])


def _parent_coverage(study,baseline,recovery_parent,segments):
    result={}
    for u in study['units']:
        uid=u['id'];state=baseline[uid]['status']
        if not u['eligible']:status='skipped';member_ids=[]
        elif uid==recovery_parent:status='covered_via_segments_not_attribution_validated';member_ids=list(segments)
        elif state=='completed':status='direct';member_ids=[uid]
        else:status=state;member_ids=[]
        result[uid]=dict(status=status,member_ids=member_ids,attribution_validated=False)
    return result


def _coverage_delta(states,baseline,core_ids):
    counts=Counter(s['status'] for s in states.values())
    return dict(parent_records=dict(added_via_segments=1,removed=0,unchanged_direct=counts['direct'],
        unchanged_skipped=counts['skipped']),vector_source_records=dict(added=len(core_ids),removed=0,
        unchanged=len(baseline['vector_hashes'])))


def _estimate(result):
    generated={'edges','parent_top_neighbors','parent_scan_coverage','member_scan_coverage','parent_isolates','isolates',
        'comparisons','status','difference','counts','resources','result_sha256','coverage_delta'}
    size=len(json.dumps({k:v for k,v in result.items() if k not in generated},ensure_ascii=False,allow_nan=False).encode())
    return size,size*4+sum(len(v) for v in result['vectors'].values())*112+len(result['member_parents'])*4096


def build_recovered_graph(baseline,recovery,*,limits=None,input_pins=None,cancel_event=None,on_progress=None):
    """Pure immutable-input build. File pins are caller-supplied SHA256 metadata."""
    started=time.perf_counter();bounds=_limits(limits)
    validate_study_neighbors(baseline);validate_segment_recovery(recovery)
    if baseline['algorithm'].get('policy')!='mutual' or baseline['algorithm'].get('k')!=2 or baseline['algorithm'].get('min_similarity')!=.30:
        raise ValueError('Baseline must use the frozen mutual k2 cutoff .30 policy.')
    if (baseline['source_study'] is None or baseline['units']!=baseline['source_study']['units']
            or baseline['comparisons']['stop_reason'] is not None
            or any(baseline['coverage'][u]['status']!='complete' for u in baseline['vectors'])):
        raise ValueError('Baseline requires original parent records and a completed cross-source scan.')
    saved=recovery['encoder_ledger'];parent_id=recovery['parent_unit_id'];plan=recovery['plan']
    if (saved is None or recovery['recovery_coverage']['status']!='covered_via_segments_not_attribution_validated'
            or any(v['status']!='completed' for v in saved['coverage'].values())):
        raise ValueError('Every recovery core must be completed before graph integration.')
    study=baseline['source_study'];originals={u['id']:u for u in study['units']}
    if parent_id not in originals or any(originals[parent_id][k]!=plan['parent'][k] for k in ('source_name','source_hash','source_start','source_end','text')):
        raise ValueError('Recovered parent does not match the frozen baseline source identity.')
    if baseline['encoding_coverage'][parent_id]['status']!='failed' or parent_id in baseline['vectors']:
        raise ValueError('Recovered parent must remain failed and vector-free in the original baseline.')
    if baseline['embedding_provenance']!=saved['embedding_provenance']:
        raise ValueError('Baseline and core vectors require identical embedding provenance.')
    core_units=plan['derived_study']['units'];core_ids=[u['id'] for u in core_units]
    units=[deepcopy(u) for u in study['units'] if u['id']!=parent_id]+deepcopy(core_units)
    units.sort(key=lambda u:(u['source_name'],u['source_start'],u['source_end'],u['id']))
    vectors=deepcopy(baseline['vectors'])
    if set(vectors)&set(saved['vectors']):raise ValueError('Duplicate legacy/core vector identity.')
    vectors.update(deepcopy(saved['vectors']));members=_member_layout(study,units,vectors,parent_id)
    provenance={uid:dict(kind='baseline_direct',parent_id=uid,vector_sha256=digest(row),
        batch_id=baseline['batch_ids'][uid]) for uid,row in baseline['vectors'].items()}
    diagnostics={}
    for batch in saved['batches']:
        if batch['status']=='completed':
            for uid,d in zip(batch['unit_ids'],batch['encoded']['provenance']['tokenization']):diagnostics[uid]=deepcopy(d)
    for uid,row in saved['vectors'].items():
        provenance[uid]=dict(kind='recovered_core',parent_id=parent_id,vector_sha256=digest(row),
            batch_id=saved['batch_ids'][uid],tokenization=diagnostics[uid])
    parent_states=_parent_coverage(study,baseline['encoding_coverage'],parent_id,core_ids)
    result=dict(version=VERSION,type='source_record_parent_candidate_pairs',source_study=deepcopy(study),units=units,
        vectors=vectors,member_parents=members,vector_provenance=provenance,
        embedding_provenance=deepcopy(baseline['embedding_provenance']),algorithm=deepcopy(ALGORITHM),limits=bounds,
        parent_coverage=parent_states,
        baseline=dict(edges=deepcopy(baseline['edges']),vector_hashes={u:digest(v) for u,v in baseline['vectors'].items()},
            encoding_coverage=deepcopy(baseline['encoding_coverage']),algorithm=deepcopy(baseline['algorithm'])),
        recovery_binding=dict(parent_unit_id=parent_id,segments=deepcopy(plan['segments']),
            profile_binding=deepcopy(plan['profile_binding']),parent_archive_context=deepcopy(plan['parent_archive_context']),
            original_parent_coverage=deepcopy(plan['original_parent_coverage']),notice=plan['notice']),
        input_bindings=dict(baseline_result_sha256=baseline['result_sha256'],recovery_sha256=recovery['recovery_sha256'],
            before_encoder_ledger_sha256=recovery['before_ledger_sha256'],recovery_plan_sha256=plan['plan_sha256'],
            segment_encoder_ledger_sha256=saved['ledger_sha256'],file_pins=_pins(input_pins)),
        attributions=_attributions(units,study['sources']),parent_attributions=_attributions(study['units'],study['sources']),
        warnings=deepcopy(WARNINGS),semantic_status='candidate_parent_pairs_only_not_participants_or_semantic_recurrence')
    size,estimate=_estimate(result)
    source_size=sum(len(s['text'].encode()) for s in study['sources'])
    if estimate>bounds['max_working_bytes'] or source_size>bounds['max_source_bytes']:
        raise ValueError('Recovered graph exceeds the explicit working/source memory bound before pair scanning.')
    scan=_scan(units,vectors,members,bounds,stop_after=bounds['max_candidate_pairs'],deadline=started+bounds['max_seconds'],
        cancel_event=cancel_event,on_progress=on_progress)
    result.update(scan);result['difference']=_difference(result['baseline']['edges'],result['edges'])
    result['coverage_delta']=_coverage_delta(parent_states,result['baseline'],core_ids)
    result['counts']=dict(original_parent_records=len(study['units']),vector_source_records=len(vectors),
        represented_parent_records=len(set(members.values())),source_documents=len(study['sources']),
        parent_pairs=len(result['edges']),parent_coverage=dict(Counter(s['status'] for s in parent_states.values())))
    result['resources']=dict(estimated_working_bytes=estimate,input_serialized_bytes=size,source_utf8_bytes=source_size,
        estimate_not_process_RSS=True,elapsed_seconds=time.perf_counter()-started,inference_calls=0,network_calls=0)
    _seal(result);validate_recovered_graph(result)
    return result


def validate_recovered_graph(result):
    """Replay deterministic source-record ranking without model execution."""
    if type(result) is not dict or result.get('version')!=VERSION or result.get('result_sha256')!=digest({k:v for k,v in result.items() if k!='result_sha256'}):
        raise ValueError('Recovered parent graph digest/version is stale.')
    if result['algorithm']!=ALGORITHM:raise ValueError('Recovered parent ranking policy changed.')
    bounds=_limits(result['limits']);study=result['source_study'];validate_study(study)
    baseline=result['baseline'];binding=result['recovery_binding'];pid=binding['parent_unit_id']
    originals={u['id']:u for u in study['units']};parent=originals[pid]
    members=_member_layout(study,result['units'],result['vectors'],pid)
    if result['member_parents']!=members:raise ValueError('Member/parent mapping is stale.')
    segments=binding['segments'];core_ids=[s['id'] for s in segments];lookup={u['id']:u for u in result['units']}
    if len(set(core_ids))!=len(core_ids) or set(lookup)!=(set(originals)-{pid})|set(core_ids):
        raise ValueError('Graph units must replace exactly the failed parent with its exact cores.')
    for uid,u in originals.items():
        if uid!=pid and lookup[uid]!=u:raise ValueError('Original graph unit was modified.')
    encoder._validate_binding(binding['profile_binding'])
    if result['embedding_provenance']!=encoder._provenance(binding['profile_binding']):
        raise ValueError('Graph embedding provenance differs from its pinned recovery profile.')
    if binding['original_parent_coverage']['status']!='failed' or baseline['encoding_coverage'][pid]['status']!='failed':
        raise ValueError('Original failed parent/history must remain explicit.')
    source=next(s for s in study['sources'] if s['source_name']==parent['source_name']);cursor=parent['source_start']
    for s in segments:
        uid=s['id'];u=lookup[uid];a,b=s['source_start'],s['source_end'];p=result['vector_provenance'][uid]
        if (a!=cursor or not a<b<=parent['source_end'] or s['text']!=source['text'][a:b]
                or s['text_sha256']!=encoder.sha256(s['text'].encode()).hexdigest()
                or any(s[k]!=u[k] for k in ('id','source_name','source_hash','source_start','source_end','text'))
                or uid not in result['vectors'] or s['parent_unit_id']!=pid
                or s['planning_status']!='pending' or s['payload_tokens']!=len(s['payload_token_ids'])
                or not 1<=s['payload_tokens']<=254 or s['input_tokens']!=s['payload_tokens']+2
                or s['special_tokens']!=2 or s['encoder_output_tokens']!=0):
            raise ValueError('Recovered exact core partition/token budget is invalid.')
        d=p['tokenization'];chunks=d['chunks'];encoder._windows(u,d)
        if (p['kind']!='recovered_core' or p['parent_id']!=pid or d['payload_tokens']!=s['payload_tokens']
                or len(chunks)!=1 or chunks[0]['input_ids']!=[101]+s['payload_token_ids']+[102]):
            raise ValueError('Recovered core vector does not bind its exact one-window token plan.')
        if u.get('execution_context_spans')!=s['context_spans']:
            raise ValueError('Recovered context differs from its saved core plan.')
        for c in s['context_spans']:
            if (c['source_hash']!=source['source_hash'] or c['source_name']!=source['source_name']
                    or type(c['source_start']) is not int or type(c['source_end']) is not int
                    or not 0<=c['source_start']<c['source_end']<=len(source['text'])
                    or source['text'][c['source_start']:c['source_end']]!=c['text']
                    or not (c['source_end']<=a or c['source_start']>=b) or c['vectorized'] is not False):
                raise ValueError('Recovered context must remain source-exact and separate.')
        cursor=b
    if cursor!=parent['source_end']:raise ValueError('Recovered cores do not cover all original parent characters.')
    if binding['parent_archive_context']!=dict(source_name=source['source_name'],source_hash=source['source_hash'],
            source_start=parent['context_start'],source_end=parent['context_end'],text=parent['context_text']):
        raise ValueError('Original full parent context changed.')
    expected_baseline={uid for uid,v in baseline['encoding_coverage'].items() if v['status']=='completed'}
    if set(baseline['encoding_coverage'])!=set(originals) or set(baseline['vector_hashes'])!=expected_baseline:
        raise ValueError('Baseline vector/parent coverage snapshot is incomplete.')
    if set(result['vectors'])!=expected_baseline|set(core_ids) or set(result['vector_provenance'])!=set(result['vectors']):
        raise ValueError('Every exact baseline/core vector must be retained, including isolates.')
    for uid,state in baseline['encoding_coverage'].items():
        u=originals[uid]
        if (state['parent_unit_id']!=uid or state['text_sha256']!=encoder.sha256(u['text'].encode()).hexdigest()
                or state['status'] not in ('completed','pending','failed','skipped')
                or (not u['eligible'] and state['status']!='skipped')):
            raise ValueError('Baseline encoding coverage does not bind its original source row.')
    for uid,row in result['vectors'].items():
        p=result['vector_provenance'][uid]
        if p['vector_sha256']!=digest(row):raise ValueError('Per-vector provenance hash changed.')
        if uid in expected_baseline and (baseline['vector_hashes'][uid]!=digest(row) or p['kind']!='baseline_direct' or p['parent_id']!=uid):
            raise ValueError('An unchanged original baseline vector was replaced or removed.')
        if uid in expected_baseline and (baseline['encoding_coverage'][uid].get('vector_sha256',digest(row))!=digest(row)
                or p['batch_id']!=baseline['encoding_coverage'][uid]['batch_id']):
            raise ValueError('Original vector/coverage/batch binding changed.')
    size,estimate=_estimate(result);source_size=sum(len(s['text'].encode()) for s in study['sources'])
    if (estimate>bounds['max_working_bytes'] or source_size>bounds['max_source_bytes']
            or result['resources']['estimated_working_bytes']!=estimate
            or result['resources']['input_serialized_bytes']!=size or result['resources']['source_utf8_bytes']!=source_size):
        raise ValueError('Graph working/source resource accounting exceeds its recorded bounds.')
    if (baseline['algorithm'].get('policy')!='mutual' or baseline['algorithm'].get('k')!=2
            or baseline['algorithm'].get('min_similarity')!=.30):
        raise ValueError('Frozen baseline ranking policy changed.')
    baseline_vectors={uid:result['vectors'][uid] for uid in expected_baseline}
    baseline_members={uid:uid for uid in expected_baseline}
    replay_baseline=_scan(study['units'],baseline_vectors,baseline_members,bounds,
        stop_after=NeighborLimits().max_candidate_pairs,deadline=time.perf_counter()+NeighborLimits().max_seconds)
    if replay_baseline['status']!='completed':raise ValueError('Baseline snapshot replay exceeded its bounded validation budget.')
    edge_fields=lambda edges:[{k:e[k] for k in ('member_ids','cosine','ranks')} for e in edges]
    if edge_fields(baseline['edges'])!=edge_fields(replay_baseline['edges']):
        raise ValueError('Frozen baseline edges do not replay from unchanged legacy vectors.')
    expected_states=_parent_coverage(study,baseline['encoding_coverage'],pid,core_ids)
    if result['parent_coverage']!=expected_states:raise ValueError('Parent source coverage was inflated or changed.')
    if result['coverage_delta']!=_coverage_delta(expected_states,baseline,core_ids):raise ValueError('Source coverage delta is stale.')
    if result['attributions']!=_attributions(result['units'],study['sources']) or result['parent_attributions']!=_attributions(study['units'],study['sources']):
        raise ValueError('Structural source attribution is stale.')
    _pins(result['input_bindings']['file_pins'])
    for key,value in result['input_bindings'].items():
        if key!='file_pins':_pins({'baseline_file_sha256':value})
    source_counts=Counter(lookup[uid]['source_hash'] for uid in result['vectors'])
    max_pairs=(len(members)**2-sum(n*n for n in source_counts.values()))//2
    recorded=result['comparisons']['member_pairs_scored']
    if type(recorded) is not int or not 0<=recorded<=min(bounds['max_candidate_pairs'],max_pairs):
        raise ValueError('Recorded replay pair count exceeds the actual source/pair budget.')
    scan=_scan(result['units'],result['vectors'],members,bounds,stop_after=recorded,
        deadline=time.perf_counter()+bounds['max_seconds'])
    for key in ('edges','parent_top_neighbors','parent_scan_coverage','member_scan_coverage','parent_isolates','isolates'):
        if result[key]!=scan[key]:raise ValueError('Recovered parent ranking/scan replay differs.')
    expected=scan['comparisons']
    for key in ('expected_cross_source_member_pairs','member_pairs_scored','expected_cross_source_parent_pairs','parent_pairs_scored'):
        if result['comparisons'][key]!=expected[key]:raise ValueError('Recorded source-pair scan counts are stale.')
    stop=result['comparisons']['stop_reason'];complete=result['comparisons']['member_pairs_scored']==expected['expected_cross_source_member_pairs']
    if (result['status'] not in ('completed','cancelled','pair_limit','time_limit')
            or result['status']!=('completed' if stop is None else stop)
            or (result['status']=='completed')!=complete
            or result['comparisons']['member_pairs_scored']>bounds['max_candidate_pairs']):
        raise ValueError('Incomplete graph cannot claim a completed or unbounded scan.')
    if result['difference']!=_difference(baseline['edges'],result['edges']):raise ValueError('Baseline difference is stale.')
    counts=dict(original_parent_records=len(study['units']),vector_source_records=len(result['vectors']),
        represented_parent_records=len(set(members.values())),source_documents=len(study['sources']),parent_pairs=len(result['edges']),
        parent_coverage=dict(Counter(s['status'] for s in expected_states.values())))
    if result['counts']!=counts:raise ValueError('Parent/member/document counts were inflated.')


def export_recovered_graph_json(result):
    validate_recovered_graph(result)
    text=json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)
    if len(text.encode())>MAX_JSON_BYTES:raise ValueError('Recovered graph exceeds its 128 MiB output bound.')
    return text


def export_recovered_graph_html(result):
    validate_recovered_graph(result);e=lambda x:escape(str(x),quote=True)
    source_ids={s['source_name']:'source-'+str(i) for i,s in enumerate(result['source_study']['sources'])}
    units={u['id']:u for u in result['units']};rows=[]
    for edge in result['edges']:
        cells=[]
        for uid in edge['member_ids']:
            u=units[uid];at=result['attributions'][uid]
            cells.append('<td><a href="#'+source_ids[u['source_name']]+'">'+e(uid)+'</a><br>'+e(u['source_name'])+
                ' ['+e(u['source_start'])+','+e(u['source_end'])+')<pre>'+e(u['text'])+'</pre><details><summary>Structural attribution</summary><pre>'+e(json.dumps(at,ensure_ascii=False,indent=2))+'</pre></details></td>')
        rows.append('<tr><td>'+e(' / '.join(edge['parent_ids']))+'<br>'+e(edge['cosine'])+'</td>'+''.join(cells)+'</tr>')
    inventory=[]
    for uid in sorted(result['vectors']):
        u=units[uid]
        inventory.append('<details><summary>'+e(uid)+' / parent '+e(result['member_parents'][uid])+'</summary><a href="#'+source_ids[u['source_name']]+'">Full source</a><p>['+e(u['source_start'])+','+e(u['source_end'])+')</p><pre>'+e(u['text'])+'</pre><h3>Structural attribution</h3><pre>'+e(json.dumps(result['attributions'][uid],ensure_ascii=False,indent=2))+'</pre><h3>Context only, not support</h3><pre>'+e(json.dumps(u.get('execution_context_spans',[]),ensure_ascii=False,indent=2))+'</pre></details>')
    sources=''.join('<section id="'+source_ids[s['source_name']]+'"><h2>'+e(s['source_name'])+'</h2><p>SHA256 '+e(s['source_hash'])+'</p><pre>'+e(s['text'])+'</pre></section>' for s in result['source_study']['sources'])
    return '<!doctype html><meta charset="utf-8"><title>Source parent candidate pairs</title><style>body{font:16px sans-serif;max-width:1500px;margin:24px auto}pre{white-space:pre-wrap}td{vertical-align:top;border:1px solid #ccc;padding:8px}table{width:100%;border-collapse:collapse}</style><h1>Source parent candidate pairs</h1><p>'+e(json.dumps(result['counts']))+'</p><ul>'+''.join('<li>'+e(w)+'</li>' for w in result['warnings'])+'</ul><details><summary>Parent coverage</summary><pre>'+e(json.dumps(result['parent_coverage'],ensure_ascii=False,indent=2))+'</pre></details><details><summary>Baseline differences</summary><pre>'+e(json.dumps(result['difference'],ensure_ascii=False,indent=2))+'</pre></details><table><tr><th>Parent pair / cosine</th><th>Exact member</th><th>Exact member</th></tr>'+''.join(rows)+'</table><h2>All vector source records, including isolates</h2>'+''.join(inventory)+sources

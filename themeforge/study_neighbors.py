"""Cross-batch source-turn neighbors from supplied vectors, without inference.

Space is O(vector values + source text + N*k), not O(N squared). Pair scanning
is quadratic in time and explicitly guarded. Incomplete endpoints never form
published candidate pairs. Source-local speaker labels do not establish identity.
"""
from collections import Counter
from copy import deepcopy
from dataclasses import asdict, dataclass
from html import escape
import json
import math
from numbers import Real
import re
import time

from .semantic_neighbors import digest

VERSION = 'themeforge.study-neighbors.v1'
ARTIFACT_TYPE = 'cross_batch_source_candidate_pairs'
_HASH = re.compile(r'^[0-9a-f]{64}$')


@dataclass(frozen=True)
class NeighborLimits:
    max_working_bytes: int = 256 * 1024**2
    max_source_bytes: int = 64 * 1024**2
    max_candidate_pairs: int = 2_000_000
    max_seconds: float = 60.0


def _limits(value):
    if value is None:
        value = NeighborLimits()
    if isinstance(value, NeighborLimits):
        value = asdict(value)
    if type(value) is not dict or set(value) != set(asdict(NeighborLimits())):
        raise ValueError('Supply NeighborLimits or its exact fields.')
    for key in ('max_working_bytes', 'max_source_bytes', 'max_candidate_pairs'):
        if type(value[key]) is not int or value[key] < 1:
            raise ValueError('Resource limits must be positive integers.')
    seconds = value['max_seconds']
    if isinstance(seconds, bool) or not isinstance(seconds, Real) or not math.isfinite(seconds) or seconds <= 0:
        raise ValueError('max_seconds must be positive and finite.')
    return deepcopy(value)


def _string(value, field):
    if type(value) is not str or not value.strip():
        raise ValueError(f'{field} must be a nonblank string.')


def _source_index(rows, limit):
    from hashlib import sha256
    if type(rows) is not list:
        raise ValueError('source_index must be a list of embedded source records.')
    index, names, size = {}, set(), 0
    for row in rows:
        if type(row) is not dict:
            raise ValueError('Source records must be objects.')
        _string(row.get('source_name'), 'source_name')
        value = row.get('source_hash')
        if type(value) is not str or not _HASH.fullmatch(value) or type(row.get('text')) is not str:
            raise ValueError('Each source needs original text and lowercase SHA256.')
        if row['source_name'] in names:
            raise ValueError('Source names must be unique.')
        encoded = row['text'].encode('utf-8'); size += len(encoded)
        if size > limit:
            raise ValueError('Embedded full sources exceed max_source_bytes; no units processed.')
        if sha256(encoded).hexdigest() != value:
            raise ValueError('Original source hash is stale or modified.')
        names.add(row['source_name']); index[row['source_name'], value] = row['text']
    return index, size


def _span(row, sources):
    if type(row) is not dict:
        raise ValueError('Source spans must be objects.')
    key = row.get('source_name'), row.get('source_hash')
    start, end, text = (row.get(field) for field in ('source_start', 'source_end', 'text'))
    if (key not in sources or type(start) is not int or type(end) is not int or type(text) is not str
            or not 0 <= start < end <= len(sources[key]) or sources[key][start:end] != text):
        raise ValueError('Source span does not resolve exactly to its embedded original.')


def _units(rows, sources):
    if type(rows) is not list:
        raise ValueError('unit_records must be a list.')
    ids, identities = set(), set()
    for row in rows:
        _span(row, sources)
        _string(row.get('id'), 'unit ID'); _string(row.get('speaker'), 'source-local speaker')
        if 'eligible' in row and type(row['eligible']) is not bool:
            raise ValueError('Unit eligibility must be boolean.')
        identity = row['source_hash'], row['source_start'], row['source_end']
        if row['id'] in ids or identity in identities:
            raise ValueError('Duplicate unit ID or source identity.')
        ids.add(row['id']); identities.add(identity)
        flat = {'context_start', 'context_end', 'context_text'}
        if flat.intersection(row):
            if not flat.issubset(row):
                raise ValueError('Incomplete exact context.')
            _span(dict(source_name=row['source_name'], source_hash=row['source_hash'],
                       source_start=row['context_start'], source_end=row['context_end'], text=row['context_text']), sources)
        for field in ('context_spans', 'referent_context'):
            if field in row:
                if type(row[field]) is not list:
                    raise ValueError('Context spans must be a list.')
                for entry in row[field]:
                    _span(entry, sources)
    return ids


def _seal(result):
    result['result_sha256'] = digest({k: v for k, v in result.items() if k != 'result_sha256'})
    return result


def _encoding_coverage(mapping, units, vectors, batches, *, allow_omitted_completed=False):
    """Bind declared encoder states to exact original units, never infer them."""
    from hashlib import sha256
    if mapping is None:
        return
    if type(mapping) is not dict or set(mapping)!={u['id'] for u in units}:
        raise ValueError('Encoding coverage must account for every exact input unit ID.')
    dimensions=set()
    for unit in units:
        uid=unit['id'];entry=mapping[uid]
        if (type(entry) is not dict or entry.get('parent_unit_id')!=uid
                or entry.get('text_sha256')!=sha256(unit['text'].encode()).hexdigest()
                or entry.get('status') not in ('pending','completed','failed','skipped')
                or type(entry.get('reason','')) is not str):
            raise ValueError('Encoding coverage does not bind the exact source unit/status.')
        for field in ('source_name','source_hash','source_start','source_end'):
            if field in entry and (type(entry[field]) is not type(unit[field]) or entry[field]!=unit[field]):
                raise ValueError('Encoding coverage source identity is mismatched.')
        if 'batch_id' in entry and entry['batch_id']!=batches[uid]:
            raise ValueError('Encoding coverage batch identity is mismatched.')
        ineligible=unit.get('eligible') is False or unit.get('status')=='unassigned'
        if ineligible and entry['status']!='skipped':
            raise ValueError('Ineligible source units require skipped encoding coverage.')
        if entry['status']=='completed':
            if vectors.get(uid) is None:
                if allow_omitted_completed:continue
                raise ValueError('Completed encoding coverage requires its supplied vector.')
            row=vectors[uid]
            if (type(row) not in (list,tuple) or not row
                    or any(isinstance(v,bool) or not isinstance(v,Real) or not math.isfinite(v) for v in row)
                    or not math.isfinite(math.hypot(*row)) or not math.hypot(*row)):
                raise ValueError('Completed encoding coverage requires a usable finite vector.')
            dimensions.add(len(row))
            if 'vector_sha256' in entry and entry['vector_sha256']!=digest(vectors[uid]):
                raise ValueError('Encoding coverage vector digest is mismatched.')
        elif vectors.get(uid) is not None:
            raise ValueError('Unfinished/failed/skipped encoding cannot supply a completed vector.')
    if len(dimensions)>1:
        raise ValueError('Completed encoding vectors must share one vector dimension.')


def _input_binding(units,sources,batches,encoding_coverage):
    value=dict(units=units,sources=sources,batches=batches)
    if encoding_coverage is not None:value['encoding_coverage']=encoding_coverage
    return value


def _top_update(top, candidate, k):
    top.append(candidate)
    top.sort(key=lambda value: (-value['cosine'], value['member_id']))
    del top[k:]


def _scan(units, normalized, k, stop_after=None, deadline=None, cancel_event=None, on_progress=None):
    ids = sorted(normalized)
    lookup = {u['id']: u for u in units}
    top = {uid: [] for uid in ids}; compared = Counter(); count = 0; stop = None
    if cancel_event is not None and cancel_event.is_set():
        return top, compared, count, 'cancelled'
    if deadline is not None and time.perf_counter() >= deadline:
        return top, compared, count, 'time_limit'
    for offset, left in enumerate(ids):
        for right in ids[offset + 1:]:
            if lookup[left]['source_hash'] == lookup[right]['source_hash']:
                continue
            if cancel_event is not None and cancel_event.is_set():
                stop = 'cancelled'; break
            if stop_after is not None and count >= stop_after:
                stop = 'pair_limit'; break
            if deadline is not None and time.perf_counter() >= deadline:
                stop = 'time_limit'; break
            score = max(-1.0, min(1.0, math.fsum(a*b for a,b in zip(normalized[left], normalized[right]))))
            _top_update(top[left], dict(member_id=right, cosine=score), k)
            _top_update(top[right], dict(member_id=left, cosine=score), k)
            compared[left] += 1; compared[right] += 1; count += 1
        if stop:
            break
        if on_progress is not None:
            on_progress(dict(phase='pair_scan', completed_outer_units=offset+1,
                             units=len(ids), pairs_scored=count))
    return top, compared, count, stop


def _derive(units, normalized, initial, top, compared, policy, minimum):
    by_id = {u['id']: u for u in units}
    source_counts = Counter(by_id[uid]['source_hash'] for uid in normalized)
    coverage = deepcopy(initial)
    for uid in normalized:
        expected = len(normalized) - source_counts[by_id[uid]['source_hash']]
        coverage[uid] = dict(initial[uid], status='complete' if compared[uid] == expected else 'partial',
                             pairs_scored=compared[uid], expected_cross_source_pairs=expected)
    edges, seen = [], set()
    for left in sorted(top):
        for left_rank, item in enumerate(top[left], 1):
            right = item['member_id']; key = tuple(sorted((left, right)))
            if key in seen or item['cosine'] < minimum:
                continue
            if coverage[left]['status'] != 'complete' or coverage[right]['status'] != 'complete':
                continue
            reverse_rank = next((i for i,v in enumerate(top[right],1) if v['member_id']==left), None)
            if policy == 'mutual' and reverse_rank is None:
                continue
            seen.add(key)
            rank_map = {left: left_rank, right: reverse_rank}
            edges.append(dict(member_ids=list(key), cosine=item['cosine'],
                ranks=[rank_map[key[0]], rank_map[key[1]]],
                batch_ids=[initial[uid]['batch_id'] for uid in key],
                cross_batch=initial[left]['batch_id'] != initial[right]['batch_id']))
    edges.sort(key=lambda edge: edge['member_ids'])
    connected = {uid for edge in edges for uid in edge['member_ids']}
    isolates = sorted(uid for uid in coverage if coverage[uid]['status']=='complete' and uid not in connected)
    return coverage, edges, isolates


def build_study_neighbors(unit_records, vectors, *, source_index=None, batch_ids,
                          embedding_provenance, policy='mutual', k=2, min_similarity=.30,
                          limits=None, cancel_event=None, on_progress=None, encoding_coverage=None):
    """Compare all supplied units across batches without loading an encoder.

    vectors maps exact unit IDs to numeric rows; missing/invalid/zero rows receive
    explicit coverage states. batch_ids must bind every input unit to one batch.
    A complete study_batches ledger may replace unit_records; it is then validated
    through validate_study before retrieval. Native eligibility remains host-owned.
    Resource estimates are conservative accounting, not measured process RSS.
    """
    started = time.perf_counter(); limits = _limits(limits)
    if policy not in ('mutual','either') or type(k) is not int or not 1 <= k <= 32:
        raise ValueError('Use mutual/either policy and k between 1 and 32.')
    if isinstance(min_similarity,bool) or not isinstance(min_similarity,Real) or not math.isfinite(min_similarity) or not -1 <= min_similarity <= 1:
        raise ValueError('min_similarity must be a finite cosine cutoff.')
    ledger = None
    if type(unit_records) is dict:
        from .study_batches import validate_study
        validate_study(unit_records)
        ledger = unit_records
        if source_index is not None and source_index != ledger['sources']:
            raise ValueError('Source index differs from the whole-study ledger.')
        source_index, unit_records = ledger['sources'], ledger['units']
    sources, source_bytes = _source_index(source_index, limits['max_source_bytes'])
    ids = _units(unit_records, sources)
    if type(vectors) is not dict or set(vectors)-ids:
        raise ValueError('Vectors must map only exact input unit IDs to rows.')
    if type(batch_ids) is not dict or set(batch_ids)!=ids or any(type(v) is not str or not v.strip() for v in batch_ids.values()):
        raise ValueError('Every input unit must have an explicit batch ID.')
    if type(embedding_provenance) is not dict or not embedding_provenance:
        raise ValueError('Explicit embedding provenance is required; no fallback is implicit.')
    _encoding_coverage(encoding_coverage,unit_records,vectors,batch_ids)
    # Account for caller rows, normalized copies, ledger/source/node copies and
    # top-k records. JSON size is measured; Python overhead remains an estimate.
    input_bytes = len(json.dumps(dict(units=unit_records, sources=source_index, ledger=ledger,
        encoding_coverage=encoding_coverage),ensure_ascii=False,allow_nan=False).encode())
    values_count = sum(len(row) for row in vectors.values() if type(row) in (list,tuple))
    estimate = input_bytes*4 + values_count*112 + len(ids)*(2048+k*1024)
    initial, accepted, normalized, dimensions = {}, {}, {}, None
    dimension_mismatch = False
    blocked = estimate > limits['max_working_bytes']
    for unit in unit_records:
        uid=unit['id']; state=dict(batch_id=batch_ids[uid])
        initial[uid]=state
        if encoding_coverage is not None:
            entry=encoding_coverage[uid]
            state.update(encoding_status=entry['status'],encoding_reason=entry.get('reason',''))
            if entry['status']!='completed':
                state.update(status={'pending':'pending_encoding','failed':'failed_encoding',
                    'skipped':'skipped_encoding'}[entry['status']],reason=entry.get('reason',''))
                continue
        if unit.get('eligible') is False or unit.get('status')=='unassigned':
            state['status']='skipped_ineligible';continue
        if blocked:
            state['status']='skipped_memory_limit';continue
        if cancel_event is not None and cancel_event.is_set():
            state['status']='skipped_cancelled';continue
        if time.perf_counter() >= started+limits['max_seconds']:
            state['status']='skipped_time_limit';continue
        row=vectors.get(uid)
        if row is None:
            state['status']='failed_missing_vector';continue
        if (type(row) not in (list,tuple) or not row
                or any(isinstance(v,bool) or not isinstance(v,Real) for v in row)):
            state['status']='failed_invalid_vector';continue
        try:
            values=[float(v) for v in row]
        except (OverflowError, ValueError):
            state['status']='failed_nonfinite_vector';continue
        if any(not math.isfinite(v) for v in values):
            state['status']='failed_nonfinite_vector';continue
        if dimensions is None:dimensions=len(values)
        if len(values)!=dimensions:
            dimension_mismatch=True
            state['status']='failed_dimension_mismatch';continue
        norm=math.hypot(*values)
        if not math.isfinite(norm):
            state['status']='failed_nonfinite_magnitude';continue
        accepted[uid]=values
        if not norm:
            state['status']='zero_vector_no_evidence';continue
        normalized[uid]=[v/norm for v in values]
    if dimension_mismatch:
        # Fail the mixed vector space as a whole, independent of input ordering.
        for uid in accepted:
            initial[uid]['status']='failed_dimension_mismatch'
        accepted.clear(); normalized.clear()
    vector_finished=time.perf_counter()
    by_id={unit['id']:unit for unit in unit_records}
    source_counts=Counter(by_id[uid]['source_hash'] for uid in normalized)
    expected_pairs=(len(normalized)**2-sum(count**2 for count in source_counts.values()))//2
    top,compared,count,stop=_scan(unit_records,normalized,k,
        stop_after=limits['max_candidate_pairs'], deadline=started+limits['max_seconds'],
        cancel_event=cancel_event,on_progress=on_progress)
    coverage,edges,isolates=_derive(unit_records,normalized,initial,top,compared,policy,min_similarity)
    scan_finished=time.perf_counter()
    for uid,state in coverage.items():
        state['batch_id']=batch_ids[uid]
        if state['status']=='partial':state['reason']=stop or 'incomplete_scan'
    counts=dict(Counter(state['status'] for state in coverage.values()))
    status=('memory_limit' if blocked else stop if stop else
            'completed_with_incomplete_encoding' if any(key in counts for key in ('pending_encoding','failed_encoding','skipped_encoding'))
            and any(u.get('eligible') is not False and u.get('status')!='unassigned'
                    and initial[u['id']].get('encoding_status')!='completed' for u in unit_records) else
            'completed_with_vector_failures' if any(key.startswith('failed_') for key in counts)
            else 'no_usable_vectors' if not normalized else 'completed')
    result=dict(version=VERSION,type=ARTIFACT_TYPE,units=deepcopy(unit_records),source_index=deepcopy(source_index),
        source_study=deepcopy(ledger),study_sha256=ledger.get('study_sha256') if ledger else None,
        vectors=accepted,batch_ids=deepcopy(batch_ids),embedding_provenance=deepcopy(embedding_provenance),
        algorithm=dict(policy=policy,k=k,min_similarity=float(min_similarity),tie_break='ascending_unit_id',
                       source_distinct='source_hash',stored_pairs='top_k_only_no_quadratic_matrix'),
        input_sha256=digest(_input_binding(unit_records,source_index,batch_ids,encoding_coverage)),
        vectors_sha256=digest(accepted),limits=limits,
        resources=dict(estimated_working_bytes=estimate,input_serialized_bytes=input_bytes,source_utf8_bytes=source_bytes,
            input_vector_values=values_count,estimate_not_process_RSS=True,
            validation_and_vectors_seconds=vector_finished-started,scan_seconds=scan_finished-vector_finished),
        coverage=coverage,coverage_counts=counts,top_neighbors=top,edges=edges,isolates=isolates,
        comparisons=dict(expected_cross_source_pairs=expected_pairs,pairs_scored=count,stop_reason=stop),
        status=status,semantic_status='candidate_membership_only_not_equivalence_or_accuracy',
        warnings=['Cosine scores are retrieval values, not confidence or semantic approval.',
            'Do not merge pairs transitively or generate forced categories/themes from this artifact.',
            'Negation, conditions and opposite views remain distinct source assertions.',
            'Same speaker labels across sources do not establish person identity.',
            'Partial, missing and failed coverage is not an isolate or evidence of no relationship.',
            'Embedding provenance is caller-attested; this module loads no encoder and never substitutes a backend.'])
    if encoding_coverage is not None:
        result['encoding_coverage']=deepcopy(encoding_coverage)
        result['encoding_coverage_sha256']=digest(encoding_coverage)
    return _seal(result)


def validate_study_neighbors(result):
    """Check sources/hash and replay the recorded deterministic scan prefix."""
    if type(result) is not dict or result.get('version')!=VERSION or result.get('type')!=ARTIFACT_TYPE:
        raise ValueError('Not a cross-batch source-neighbor artifact.')
    if result.get('result_sha256')!=digest({k:v for k,v in result.items() if k!='result_sha256'}):
        raise ValueError('Study neighbor digest is stale or modified.')
    try:
        limits=_limits(result['limits']);sources,_=_source_index(result['source_index'],limits['max_source_bytes'])
        ids=_units(result['units'],sources)
        encoding_coverage=result.get('encoding_coverage')
        if 'encoding_coverage' in result or 'encoding_coverage_sha256' in result:
            if encoding_coverage is None or result.get('encoding_coverage_sha256')!=digest(encoding_coverage):
                raise ValueError('Encoding coverage digest is stale or incomplete.')
            _encoding_coverage(encoding_coverage,result['units'],result['vectors'],result['batch_ids'],allow_omitted_completed=True)
        if (set(result['coverage'])!=ids or set(result['batch_ids'])!=ids
                or set(result['vectors'])-ids or not result['embedding_provenance']):
            raise ValueError('Every input unit needs explicit coverage, batch and vector provenance.')
        allowed={'complete','partial','skipped_ineligible','skipped_memory_limit','skipped_cancelled',
                 'skipped_time_limit','failed_missing_vector','failed_invalid_vector','failed_nonfinite_vector',
                 'failed_dimension_mismatch','failed_nonfinite_magnitude','zero_vector_no_evidence'}
        allowed.update(('pending_encoding','failed_encoding','skipped_encoding'))
        for unit in result['units']:
            uid=unit['id'];state=result['coverage'][uid]
            if state['status'] not in allowed or state['batch_id']!=result['batch_ids'][uid]:
                raise ValueError('Invalid source-unit coverage/batch record.')
            ineligible=unit.get('eligible') is False or unit.get('status')=='unassigned'
            expected_ineligible='skipped_encoding' if encoding_coverage is not None else 'skipped_ineligible'
            if ineligible and state['status']!=expected_ineligible:
                raise ValueError('Saved eligibility coverage is stale.')
            if not ineligible and state['status']=='skipped_ineligible':
                raise ValueError('Saved eligibility coverage is stale.')
            if encoding_coverage is None and state['status'] in ('pending_encoding','failed_encoding','skipped_encoding'):
                raise ValueError('Encoding status needs its bound source coverage.')
            if encoding_coverage is not None:
                entry=encoding_coverage[uid]
                if state.get('encoding_status')!=entry['status'] or state.get('encoding_reason')!=entry.get('reason',''):
                    raise ValueError('Declared encoding state/reason is stale.')
                if entry['status']!='completed':
                    expected={'pending':'pending_encoding','failed':'failed_encoding','skipped':'skipped_encoding'}[entry['status']]
                    if state['status']!=expected or state.get('reason')!=entry.get('reason',''):
                        raise ValueError('Missing-vector encoding status/reason is stale.')
                elif uid not in result['vectors'] and state['status'] not in (
                        'skipped_memory_limit','skipped_cancelled','skipped_time_limit'):
                    raise ValueError('Completed encoding vector omission needs an explicit retrieval guard state.')
        if result['source_study'] is not None:
            from .study_batches import validate_study
            validate_study(result['source_study'])
            if (result['source_study']['units']!=result['units'] or result['source_study']['sources']!=result['source_index']
                    or result['study_sha256']!=result['source_study']['study_sha256']):
                raise ValueError('Neighbor source ledger binding is stale.')
        expected_input=digest(_input_binding(result['units'],result['source_index'],result['batch_ids'],encoding_coverage))
        if result['input_sha256']!=expected_input or result['vectors_sha256']!=digest(result['vectors']):
            raise ValueError('Source/vector binding is stale.')
        normalized={};dimensions=set()
        for uid,row in result['vectors'].items():
            if not row or any(type(v) not in (int,float) or not math.isfinite(v) for v in row):
                raise ValueError('Saved vectors must be finite numeric rows.')
            norm=math.hypot(*row)
            if not math.isfinite(norm):raise ValueError('Nonfinite saved vector magnitude.')
            dimensions.add(len(row))
            if norm:
                normalized[uid]=[v/norm for v in row]
                if result['coverage'][uid]['status'] not in ('complete','partial'):
                    raise ValueError('Nonzero vector coverage is inconsistent.')
            elif result['coverage'][uid]['status']!='zero_vector_no_evidence':
                raise ValueError('Zero vector coverage is inconsistent.')
        if len(dimensions)>1:raise ValueError('Mixed saved vector dimensions.')
        if any(s['status'] in ('complete','partial','zero_vector_no_evidence') and uid not in result['vectors']
               for uid,s in result['coverage'].items()):
            raise ValueError('Processed coverage has no saved vector.')
        compared_count=result['comparisons']['pairs_scored']
        if type(compared_count) is not int or not 0<=compared_count<=limits['max_candidate_pairs']:
            raise ValueError('Invalid bounded scan count.')
        algorithm=result['algorithm']
        if (algorithm['policy'] not in ('mutual','either') or type(algorithm['k']) is not int
                or not 1<=algorithm['k']<=32 or type(algorithm['min_similarity']) not in (int,float)
                or not math.isfinite(algorithm['min_similarity']) or not -1<=algorithm['min_similarity']<=1):
            raise ValueError('Invalid frozen neighbor policy.')
        groups=Counter(u['source_hash'] for u in result['units'] if u['id'] in normalized)
        expected_pairs=(len(normalized)**2-sum(n*n for n in groups.values()))//2
        stop=result['comparisons']['stop_reason']
        if (result['comparisons']['expected_cross_source_pairs']!=expected_pairs
                or compared_count>expected_pairs or stop not in (None,'pair_limit','time_limit','cancelled')
                or (compared_count<expected_pairs and stop is None)):
            raise ValueError('Comparison accounting is stale.')
        top,compared,count,_=_scan(result['units'],normalized,algorithm['k'],stop_after=compared_count)
        initial={uid:dict(state) for uid,state in result['coverage'].items()}
        coverage,edges,isolates=_derive(result['units'],normalized,initial,top,compared,algorithm['policy'],algorithm['min_similarity'])
        if (top!=result['top_neighbors'] or edges!=result['edges'] or isolates!=result['isolates'] or count!=compared_count):
            raise ValueError('Saved cross-batch scores/ranks/memberships are stale or modified.')
        for uid in normalized:
            for key in ('status','pairs_scored','expected_cross_source_pairs'):
                if coverage[uid][key]!=result['coverage'][uid][key]:
                    raise ValueError('Saved comparison coverage is stale.')
            if coverage[uid]['status']=='partial' and result['coverage'][uid].get('reason')!=stop:
                raise ValueError('Incomplete comparison reason is stale.')
        if dict(Counter(s['status'] for s in result['coverage'].values()))!=result['coverage_counts']:
            raise ValueError('Saved coverage counts are stale.')
    except (KeyError,TypeError,OverflowError) as error:
        raise ValueError('Incomplete or invalid study neighbor artifact.') from error


def export_study_neighbors_json(result):
    validate_study_neighbors(result)
    return json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)


def export_study_neighbors_html(result):
    validate_study_neighbors(result)
    e=lambda value:escape(str(value),quote=True)
    units={u['id']:u for u in result['units']}
    from .source_context import review_context
    sources={s['source_name']:s for s in result['source_index']}
    rows=['<!doctype html><html lang="en"><meta charset="utf-8"><title>Cross-batch candidate pairs</title>',
        '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; base-uri \'none\'; form-action \'none\'">',
        '<style>body{font:16px/1.5 system-ui;max-width:1100px;margin:auto;padding:20px}pre{white-space:pre-wrap;overflow-wrap:anywhere}</style>',
        '<h1>Cross-batch candidate pairs</h1><p>Retrieval candidates only, not categories, themes, equivalence or accuracy.</p>']
    rows+=['<p>'+e(warning)+'</p>' for warning in result['warnings']]
    rows.append('<pre>'+e(json.dumps({key:result[key] for key in ('status','algorithm','comparisons','coverage_counts','resources','embedding_provenance','result_sha256')},ensure_ascii=False,indent=2))+'</pre>')
    for edge in result['edges']:
        rows.append('<article><h2>'+e(' / '.join(edge['member_ids']))+'</h2><pre>'+e(json.dumps(edge,ensure_ascii=False))+'</pre>')
        for uid in edge['member_ids']:
            rows.append('<pre>'+e(json.dumps(units[uid],ensure_ascii=False,indent=2))+'</pre>')
            source=sources[units[uid]['source_name']]
            context=review_context(units[uid],source)
            rows.append('<details><summary>Exact surrounding context and attribution warnings</summary><pre>'+e(json.dumps(context,ensure_ascii=False,indent=2))+'</pre></details>')
            rows.append('<p><a href="#source-'+e(source['source_hash'])+'">Inspect full original source and distant context</a></p>')
        rows.append('</article>')
    rows.append('<h2>Full original sources for context review</h2>')
    for source in result['source_index']:
        rows.append('<details id="source-'+e(source['source_hash'])+'"><summary>'+e(source['source_name'])+' | '+e(source['source_hash'])+'</summary><pre>'+e(source['text'])+'</pre></details>')
    rows.append('<h2>Every unit coverage state</h2><pre>'+e(json.dumps(result['coverage'],ensure_ascii=False,indent=2))+'</pre></html>')
    return '\n'.join(rows)

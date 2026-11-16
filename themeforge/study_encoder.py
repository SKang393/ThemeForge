"""Resumable source-bound sentence encoding, never per-chunk lexical comparison.

Only the existing pinned sentence encoder runs, in an owned guarded worker.
The orchestration has no total-turn quota. A work period leaves explicit pending
coverage for later resume; completed units are not encoded again.
"""
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
from uuid import uuid4

from . import sentence_encoder as encoder
from .semantic_neighbors import prepare_packet, digest
from .study_batches import validate_study

VERSION = 'themeforge.study-encoder.v1'
MAX_LEDGER_BYTES = 64 * 1024**2
MAX_WORKER_OUTPUT_BYTES = 16 * 1024**2
POOLING = 'attention-masked mean then L2; payload-token-weighted chunk mean then L2'


def _validate_binding(binding):
    fixed=dict(repository=encoder.MODEL_REPOSITORY,revision=encoder.MODEL_REVISION,
        model_sha256=encoder.MODEL_SHA256,tokenizer_sha256=encoder.TOKENIZER_SHA256,
        dimension=encoder.DIMENSION,pooling=POOLING)
    if type(binding) is not dict or any(binding.get(k)!=v for k,v in fixed.items()):
        raise ValueError('Ledger is not bound to the existing reviewed encoder recipe.')
    for key in ('profile_sha256','manifest_sha256'):
        if type(binding.get(key)) is not str or not re.fullmatch(r'[0-9a-f]{64}',binding[key]):
            raise ValueError('Profile and manifest need exact SHA256 bindings.')


def _seal(ledger):
    ledger['ledger_sha256'] = digest({k:v for k,v in ledger.items() if k!='ledger_sha256'})
    return ledger


def _utc():
    return datetime.now(timezone.utc).isoformat()


def _json(value):
    text=json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)
    if len(text.encode())>MAX_LEDGER_BYTES:
        raise ValueError('Encoder ledger exceeds its 64 MiB persistence bound.')
    return text


def _profile(profile):
    if isinstance(profile,(str,Path)):
        path=encoder.local_file(profile);expected=encoder.file_sha256(path)
    elif type(profile) is dict and set(profile)=={'path','sha256'}:
        path=encoder.local_file(profile['path']);expected=profile['sha256']
    else:
        raise ValueError('Use an absolute local encoder profile path or {path,sha256}.')
    raw,_,_,_=encoder.verify_profile(path,expected)
    binding=dict(profile_sha256=expected,repository=encoder.MODEL_REPOSITORY,revision=encoder.MODEL_REVISION,
        model_sha256=encoder.MODEL_SHA256,tokenizer_sha256=encoder.TOKENIZER_SHA256,
        manifest_sha256=raw['artifact_manifest_sha256'],runtime=deepcopy(raw['runtime']),
        dimension=encoder.DIMENSION,pooling=POOLING)
    return str(path),binding


def _provenance(binding):
    return dict(backend='sentence_embeddings',adapter=encoder.VERSION,
        repository=binding['repository'],revision=binding['revision'],model_sha256=binding['model_sha256'],
        tokenizer_sha256=binding['tokenizer_sha256'],profile_sha256=binding['profile_sha256'],
        dimension=encoder.DIMENSION,pooling=POOLING,input_fields=['unit.text'],
        provider='CPUExecutionProvider',intra_op_threads=2,inter_op_threads=1,
        training=False,network_calls=0,semantic_accuracy_measured=False,
        chunk_boundary_relations_not_guaranteed=True)


def _vector(row):
    if (type(row) is not list or len(row)!=encoder.DIMENSION
            or any(type(v) not in (int,float) or not math.isfinite(v) for v in row)):
        raise ValueError('Encoder must supply a finite 384-dimensional vector.')
    norm=math.hypot(*row)
    if abs(norm-1.)>1e-5:
        raise ValueError('Encoder vector must be nonzero and L2-normalized.')


def _windows(unit,diagnostic):
    if (diagnostic.get('node_id')!=unit['id'] or diagnostic.get('text_sha256')!=sha256(unit['text'].encode()).hexdigest()
            or diagnostic.get('truncated_tokens')!=0):
        raise ValueError('Encoder token diagnostics do not bind the unchanged source turn.')
    chunks=diagnostic.get('chunks')
    if type(chunks) is not list or not 1<=len(chunks)<=encoder.MAX_CHUNKS:
        raise ValueError('Encoder must account for every bounded token chunk.')
    cursor=0;windows=[]
    for chunk in chunks:
        a,b=chunk.get('payload_token_start'),chunk.get('payload_token_end')
        left,right=chunk.get('source_char_start'),chunk.get('source_char_end')
        ids=chunk.get('input_ids')
        if (type(a) is not int or type(b) is not int or a!=cursor or not a<b
                or b-a>encoder.PAYLOAD_TOKENS or chunk.get('payload_tokens')!=b-a
                or type(left) is not int or type(right) is not int or not 0<=left<right<=len(unit['text'])
                or type(ids) is not list or len(ids)!=b-a+2 or ids[0]!=101 or ids[-1]!=102
                or any(type(t) is not int or t<0 for t in ids)):
            raise ValueError('Malformed or incomplete exact token-window accounting.')
        windows.append(dict(parent_unit_id=unit['id'],payload_token_start=a,payload_token_end=b,
            source_name=unit['source_name'],source_hash=unit['source_hash'],
            source_start=unit['source_start']+left,source_end=unit['source_start']+right,
            text=unit['text'][left:right]))
        cursor=b
    if diagnostic.get('payload_tokens')!=cursor:
        raise ValueError('Token coverage is incomplete; no truncation repair is allowed.')
    return windows


def _accept_encoded(units,encoded,binding):
    if type(encoded) is not dict or type(encoded.get('vectors')) is not list or len(encoded['vectors'])!=len(units):
        raise ValueError('Worker vector count does not match exact source units.')
    provenance=encoded.get('provenance',{})
    expected=dict(adapter=encoder.VERSION,repository=binding['repository'],revision=binding['revision'],
        model_sha256=binding['model_sha256'],tokenizer_sha256=binding['tokenizer_sha256'],
        profile_sha256=binding['profile_sha256'],provider='CPUExecutionProvider',intra_op_threads=2,
        inter_op_threads=1,dimension=encoder.DIMENSION,pooling=POOLING,
        original_text_preserved=True,input_fields=['node.text'],training=False,network_calls=0)
    if any(provenance.get(k)!=v for k,v in expected.items()):
        raise ValueError('Worker uses incompatible model/profile/pooling/source settings.')
    runtime=binding['runtime']
    if provenance.get('runtime_versions')!={'numpy':runtime['numpy_version'],
            'onnxruntime':runtime['version'],'tokenizers':runtime['tokenizers_version']}:
        raise ValueError('Worker runtime differs from the pinned profile.')
    diagnostics=provenance.get('tokenization')
    if type(diagnostics) is not list or len(diagnostics)!=len(units):
        raise ValueError('Worker omitted token diagnostics.')
    windows=[]
    for unit,row,diagnostic in zip(units,encoded['vectors'],diagnostics):
        _vector(row);windows.append(_windows(unit,diagnostic))
    return windows


def _wait_owned_closed(owned,process_api,*,timeout=5.):
    """Wait for exit of each exact observed identity within one total deadline.

    A Windows descendant can still have a PID while terminating after kill.
    Successful wait proves exit; mere PID existence is not a running-state test.
    Reused PIDs are neither waited on nor treated as our still-running children.
    """
    deadline=time.perf_counter()+timeout
    for pid,created in reversed(list(owned.items())):
        try:
            child=process_api.Process(pid)
            if child.create_time()!=created:
                continue
            child.wait(timeout=max(0.,deadline-time.perf_counter()))
        except process_api.NoSuchProcess:
            pass
        except (process_api.TimeoutExpired,process_api.AccessDenied):
            return False
    return True


def _run_chunk(packet,profile_path,profile_hash,work_dir,*,wall_seconds,cancel_event):
    """Reuse the existing sentence-only neighbor worker with sampled watchdogs."""
    import psutil
    from .neighbor_runner import TREE_MEMORY_LIMIT_BYTES
    if psutil.virtual_memory().available < 3 * 1024**3:
        return dict(status='memory_limit',error='Existing runner guard requires 3 GiB currently available RAM.',
                    resources=dict(owned_processes_closed=True,worker_started=False))
    work_dir=Path(work_dir);work_dir.mkdir(parents=True,exist_ok=False)
    packet_path=work_dir/'source-packet.json'
    packet_path.write_bytes((json.dumps(packet,ensure_ascii=False,allow_nan=False)+'\n').encode())
    prefix=[sys.executable,'--neighbor-worker'] if getattr(sys,'frozen',False) else [sys.executable,'-m','themeforge.neighbor_runner','--worker']
    command=prefix+['--packet',str(packet_path),'--packet-sha256',encoder.file_sha256(packet_path),
        '--profile',profile_path,'--profile-sha256',profile_hash,'--backend','sentence_embeddings',
        '--out',str(work_dir/'worker')]
    env=dict(os.environ,OMP_NUM_THREADS='2',OPENBLAS_NUM_THREADS='2',MKL_NUM_THREADS='2',
        NUMEXPR_NUM_THREADS='2',TOKENIZERS_PARALLELISM='false',HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1')
    process=None;owned={};started=time.perf_counter();stop=None;peak=0
    try:
        with (work_dir/'worker.log').open('xb') as log:
            process=subprocess.Popen(command,env=env,stdout=log,stderr=subprocess.STDOUT,
                cwd=Path(__file__).resolve().parent.parent if not getattr(sys,'frozen',False) else work_dir,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            observed=psutil.Process(process.pid);owned[process.pid]=observed.create_time()
            while process.poll() is None:
                rss=0
                try:
                    for child in [observed]+observed.children(recursive=True):
                        owned[child.pid]=child.create_time();rss+=child.memory_info().rss
                except psutil.NoSuchProcess:
                    pass
                peak=max(peak,rss)
                if cancel_event is not None and cancel_event.is_set():stop='cancelled'
                elif time.perf_counter()-started>=wall_seconds:stop='time_limit'
                elif rss>TREE_MEMORY_LIMIT_BYTES:stop='memory_limit'
                if stop:break
                time.sleep(.05)
    finally:
        for pid,created in reversed(list(owned.items())):
            try:
                child=psutil.Process(pid)
                if child.create_time()==created and child.is_running():child.kill()
            except psutil.NoSuchProcess:
                pass
        if process is not None:
            if process.poll() is None:process.kill()
            process.wait(timeout=10)
    cleaned=_wait_owned_closed(owned,psutil)
    resources=dict(wall_seconds=time.perf_counter()-started,peak_sampled_tree_rss_bytes=peak,
        memory_limit_bytes=TREE_MEMORY_LIMIT_BYTES,memory_limit_kind='sampled_watchdog_not_OS_hard_cap',
        owned_processes_closed=cleaned)
    if not cleaned:return dict(status='cleanup_failed',resources=resources)
    if stop:return dict(status=stop,resources=resources)
    path=work_dir/'worker'/'encoder-output.json'
    if process.returncode!=0 or not path.is_file():
        report=work_dir/'worker'/'worker.json'
        error=json.loads(report.read_text(encoding='utf-8')).get('error','Encoder worker failed.') if report.is_file() else 'Encoder worker failed; inspect preserved log.'
        return dict(status='failed',error=str(error)[:2000],resources=resources)
    if path.stat().st_size>MAX_WORKER_OUTPUT_BYTES:
        return dict(status='failed',error='Encoder output exceeds its byte limit.',resources=resources)
    return dict(status='completed',encoded=json.loads(path.read_text(encoding='utf-8')),resources=resources)


def validate_encoder_ledger(ledger):
    """Source/vector/token-window integrity, not encoder execution or meaning proof."""
    if type(ledger) is not dict or ledger.get('version')!=VERSION:
        raise ValueError('Not a whole-study encoder ledger.')
    _json(ledger)
    if ledger.get('ledger_sha256')!=digest({k:v for k,v in ledger.items() if k!='ledger_sha256'}):
        raise ValueError('Encoder ledger digest is stale.')
    validate_study(ledger['study'])
    if ledger['study_sha256']!=ledger['study']['study_sha256']:
        raise ValueError('Encoder source-study binding is stale.')
    units={u['id']:u for u in ledger['study']['units']}
    if set(ledger['coverage'])!=set(units) or set(ledger['batch_ids'])!=set(units):
        raise ValueError('Every original unit needs encoding coverage.')
    completed=set()
    for uid,state in ledger['coverage'].items():
        if (state['parent_unit_id']!=uid or state['text_sha256']!=sha256(units[uid]['text'].encode()).hexdigest()
                or state['status'] not in ('pending','completed','failed','skipped')
                or state['batch_id']!=ledger['batch_ids'][uid]):
            raise ValueError('Source-unit identity or encoding coverage is stale.')
        if not units[uid]['eligible']:
            if state['status']!='skipped':raise ValueError('Ineligible units cannot be encoded.')
        elif state['status']=='completed':
            completed.add(uid);_vector(ledger['vectors'][uid])
            if state['vector_sha256']!=digest(ledger['vectors'][uid]):raise ValueError('Vector digest is stale.')
    if set(ledger['vectors'])!=completed:
        raise ValueError('Only completed units may supply vectors.')
    if ledger['coverage_counts']!=dict(__import__('collections').Counter(s['status'] for s in ledger['coverage'].values())):
        raise ValueError('Encoding coverage counts are stale.')
    binding=ledger['profile_binding']
    if binding is None:
        if any(u['eligible'] for u in units.values()) or ledger['vectors']:
            raise ValueError('Eligible encoding needs an explicit profile binding.')
    else:
        _validate_binding(binding)
        if ledger['embedding_provenance']!=_provenance(binding):
            raise ValueError('Mixed embedding provenance is not allowed.')
    batches={}
    for batch in ledger['batches']:
        if batch['id'] in batches:raise ValueError('Duplicate encoder batch ID.')
        batches[batch['id']]=batch
        if batch['status']=='completed':
            chosen=[units[uid] for uid in batch['unit_ids']]
            windows=_accept_encoded(chosen,batch['encoded'],binding)
            for unit,row,exact_windows in zip(chosen,batch['encoded']['vectors'],windows):
                uid=unit['id']
                if (uid not in completed or ledger['coverage'][uid]['batch_id']!=batch['id']
                        or ledger['vectors'][uid]!=row or ledger['coverage'][uid]['token_windows']!=exact_windows):
                    raise ValueError('Completed batch does not bind the exact saved vectors and token windows.')
    if any(ledger['coverage'][uid]['batch_id'] not in batches for uid in completed):
        raise ValueError('Completed unit has no original encoder batch.')


def _counts(ledger):
    from collections import Counter
    ledger['coverage_counts']=dict(Counter(state['status'] for state in ledger['coverage'].values()))


def retry_failed_units(prior, unit_ids, *, reason):
    """Explicitly requeue exact failed units, retaining their attempts and state.

    Completed units are never accepted. The original ledger remains unchanged.
    A later encode_study call owns execution and the same profile/source checks.
    """
    validate_encoder_ledger(prior)
    if (type(unit_ids) is not list or not unit_ids or len(set(unit_ids))!=len(unit_ids)
            or type(reason) is not str or not reason.strip()):
        raise ValueError('Retry requires unique failed unit IDs and an explicit reason.')
    if any(uid not in prior['coverage'] or prior['coverage'][uid]['status']!='failed' for uid in unit_ids):
        raise ValueError('Only explicitly selected failed units can be retried.')
    ledger=deepcopy(prior)
    event=dict(action='retry_failed_units',created_utc=_utc(),reason=reason,
        before_ledger_sha256=prior['ledger_sha256'],study_sha256=prior['study_sha256'],
        prior_coverage={uid:deepcopy(prior['coverage'][uid]) for uid in unit_ids})
    ledger.setdefault('recovery_events',[]).append(event)
    for uid in unit_ids:
        ledger['coverage'][uid].update(status='pending',reason='explicit_retry: '+reason)
    ledger['status']='retry_pending';_counts(ledger);_seal(ledger)
    validate_encoder_ledger(ledger)
    return ledger


def recover_failed_batch(prior, batch_id, *, packet_path, encoded_path, report_path,
                         cleanup_evidence, reason):
    """Recover a completed, source-bound local output from a failed attempt.

    The caller must supply current cleanup evidence. Historical failure/resource
    records remain intact; a distinct completed batch records artifact hashes.
    This validates provenance and exact windows, without running the model.
    """
    validate_encoder_ledger(prior)
    if not all(type(v) is str and v.strip() for v in (reason,cleanup_evidence)):
        raise ValueError('Recovery needs a reason and current cleanup evidence.')
    matches=[b for b in prior['batches'] if b['id']==batch_id and b['status']=='failed']
    if len(matches)!=1:
        raise ValueError('Recovery requires one original failed batch.')
    old=matches[0];ids=old['unit_ids']
    if any(prior['coverage'][uid]['status']!='failed' or prior['batch_ids'][uid]!=batch_id for uid in ids):
        raise ValueError('Only exact currently failed units from this batch can be recovered.')
    paths=[encoder.local_file(p) for p in (packet_path,encoded_path,report_path)]
    values=[]
    for path in paths:
        if path.stat().st_size>MAX_WORKER_OUTPUT_BYTES:
            raise ValueError('Recovery artifact exceeds its byte bound.')
        values.append(json.loads(path.read_text(encoding='utf-8-sig')))
    packet,encoded,report=values
    from .semantic_neighbors import validate_packet
    validate_packet(packet)
    units={u['id']:u for u in prior['study']['units']};chosen=[units[uid] for uid in ids]
    names={u['source_name'] for u in chosen}
    expected=prepare_packet(chosen,sources=[s for s in prior['study']['sources'] if s['source_name'] in names],
        provenance=dict(parent_study_sha256=prior['study_sha256'],parent_unit_ids=ids))
    if (packet!=expected or packet['packet_sha256']!=old.get('packet_sha256')
            or report.get('status')!='completed' or report.get('backend')!='sentence_embeddings'
            or report.get('packet_sha256')!=packet['packet_sha256'] or report.get('source_turns')!=len(ids)):
        raise ValueError('Recovery artifacts do not bind the completed original source packet.')
    windows=_accept_encoded(chosen,encoded,prior['profile_binding'])
    ledger=deepcopy(prior)
    recovered_id='ENC-REC-'+digest([prior['ledger_sha256'],batch_id,encoded])[:20]
    ledger['batches'].append(dict(id=recovered_id,unit_ids=deepcopy(ids),status='completed',
        encoded=deepcopy(encoded),packet_sha256=packet['packet_sha256'],
        recovered_from_batch_id=batch_id,recovery_reason=reason,cleanup_evidence=cleanup_evidence,
        artifact_bindings=[dict(path=str(p),sha256=encoder.file_sha256(p)) for p in paths],
        resources=dict(worker_started=False,owned_processes_closed=True)))
    ledger.setdefault('recovery_events',[]).append(dict(action='recover_failed_batch',created_utc=_utc(),
        before_ledger_sha256=prior['ledger_sha256'],study_sha256=prior['study_sha256'],
        prior_coverage={uid:deepcopy(prior['coverage'][uid]) for uid in ids},
        recovered_from_batch_id=batch_id,recovered_batch_id=recovered_id,reason=reason))
    for unit,row,exact_windows in zip(chosen,encoded['vectors'],windows):
        uid=unit['id'];ledger['vectors'][uid]=deepcopy(row);ledger['batch_ids'][uid]=recovered_id
        ledger['coverage'][uid].update(status='completed',reason='',batch_id=recovered_id,
            vector_sha256=digest(row),token_windows=exact_windows)
    ledger['status']='recovered_checkpoint';_counts(ledger);_seal(ledger)
    validate_encoder_ledger(ledger)
    return ledger


def encode_study(study,profile,*,prior=None,output_directory=None,wall_seconds=120,
                 cancel_event=None,on_progress=None,max_units_per_run=64,chunk_size=1):
    """Encode a bounded work period; resume only pending units, without repeats.

    profile is an absolute local profile path or {path,sha256}. Every worker uses
    the existing reviewed 384-D recipe. Oversize/token-limit turns fail explicitly
    rather than being truncated. Chunk size is a per-worker cap, not a study cap.
    """
    started=time.perf_counter();validate_study(study)
    if (type(wall_seconds) not in (int,float) or not math.isfinite(wall_seconds) or wall_seconds<=0
            or type(max_units_per_run) is not int or max_units_per_run<1
            or type(chunk_size) is not int or not 1<=chunk_size<=20):
        raise ValueError('Use positive work-period bounds and 1..20 units per worker.')
    eligible=[u for u in study['units'] if u['eligible']]
    profile_path,binding=_profile(profile) if eligible else (None,None)
    if prior is not None:
        validate_encoder_ledger(prior)
        if prior['study_sha256']!=study['study_sha256'] or prior['profile_binding']!=binding:
            raise ValueError('Resume requires the same exact study and encoder profile.')
        ledger=deepcopy(prior)
    else:
        coverage={u['id']:dict(parent_unit_id=u['id'],text_sha256=sha256(u['text'].encode()).hexdigest(),
            status='pending' if u['eligible'] else 'skipped',reason='' if u['eligible'] else 'source_ineligible',
            batch_id='pending' if u['eligible'] else 'unassigned') for u in study['units']}
        ledger=dict(version=VERSION,study=deepcopy(study),study_sha256=study['study_sha256'],profile_binding=binding,
            embedding_provenance=_provenance(binding) if binding else dict(backend='sentence_embeddings',encoding='not_started_no_eligible_units'),
            vectors={},batch_ids={uid:s['batch_id'] for uid,s in coverage.items()},coverage=coverage,batches=[],runs=[])
        _counts(ledger);_seal(ledger)
    parent_hash=ledger['ledger_sha256']
    run=dict(id='run-'+uuid4().hex,started_utc=_utc(),before_ledger_sha256=parent_hash,wall_seconds=wall_seconds,
             max_units_per_run=max_units_per_run,chunk_size=chunk_size,status='running',attempted_units=0)
    ledger['runs'].append(run)
    pending=[u for u in eligible if ledger['coverage'][u['id']]['status']=='pending']
    temporary=None
    if output_directory is None:
        temporary=tempfile.TemporaryDirectory(prefix='themeforge-study-encoder-');root=Path(temporary.name)
    else:
        raw=str(output_directory)
        if raw.startswith(('\\\\','//')) or '://' in raw or not Path(raw).is_absolute():
            raise ValueError('Choose an absolute local output directory.')
        root=Path(raw).resolve()/run['id'];root.mkdir(parents=True,exist_ok=False)
    def checkpoint():
        run['checkpoint_sequence']=run.get('checkpoint_sequence',0)+1
        ledger['status']=run['status'];_counts(ledger);_seal(ledger)
        if output_directory is not None:
            # Bounded alternating copies avoid rename/delete requirements in
            # restricted Windows hosts. A torn write leaves the other valid slot.
            target=root/f"encoder-ledger-progress-{run['checkpoint_sequence']%2}.json"
            with target.open('wb') as stream:
                stream.write((_json(ledger)+'\n').encode());stream.flush();os.fsync(stream.fileno())
        if on_progress is not None:
            on_progress(dict(phase='study_encoding',status=run['status'],**ledger['coverage_counts']))
    try:
        for offset in range(0,len(pending),chunk_size):
            remaining=wall_seconds-(time.perf_counter()-started)
            if cancel_event is not None and cancel_event.is_set():run['status']='cancelled';break
            if remaining<=0:run['status']='paused_time_limit';break
            available=max_units_per_run-run['attempted_units']
            if available<=0:run['status']='paused_work_limit';break
            chosen=pending[offset:offset+min(chunk_size,available)]
            batch_id='ENC-'+digest([study['study_sha256'],binding['profile_sha256'],[u['id'] for u in chosen],len(ledger['batches'])])[:20]
            record=dict(id=batch_id,unit_ids=[u['id'] for u in chosen],status='failed')
            ledger['batches'].append(record);run['attempted_units']+=len(chosen)
            try:
                names={u['source_name'] for u in chosen}
                packet=prepare_packet(chosen,sources=[s for s in study['sources'] if s['source_name'] in names],
                    provenance=dict(parent_study_sha256=study['study_sha256'],parent_unit_ids=record['unit_ids']))
                record['packet_sha256']=packet['packet_sha256']
                result=_run_chunk(packet,profile_path,binding['profile_sha256'],root/batch_id,
                                  wall_seconds=remaining,cancel_event=cancel_event)
                record.update(status=result['status'],resources=result.get('resources',{}))
                record['worker_stop_reason']=result['status']
                if result['status']=='completed':
                    windows=_accept_encoded(chosen,result['encoded'],binding)
                    record['encoded']=deepcopy(result['encoded'])
                    for unit,row,exact_windows in zip(chosen,result['encoded']['vectors'],windows):
                        uid=unit['id'];ledger['vectors'][uid]=deepcopy(row)
                        ledger['batch_ids'][uid]=batch_id
                        ledger['coverage'][uid].update(status='completed',reason='',batch_id=batch_id,
                            vector_sha256=digest(row),token_windows=exact_windows)
                elif result['status'] in ('cancelled','time_limit'):
                    run['status']='cancelled' if result['status']=='cancelled' else 'paused_time_limit'
                    checkpoint();break
                else:
                    raise RuntimeError(result.get('error',result['status']))
            except Exception as error:
                record.update(status='failed',error=f'{type(error).__name__}: {error}'[:2000])
                for unit in chosen:
                    uid=unit['id'];ledger['batch_ids'][uid]=batch_id
                    ledger['coverage'][uid].update(status='failed',reason=record['error'],batch_id=batch_id)
            checkpoint()
            if record.get('worker_stop_reason')=='memory_limit':
                run['status']='paused_memory_limit';break
            if record.get('resources',{}).get('owned_processes_closed') is False:
                run['status']='cleanup_failed';break
        if run['status']=='running':
            counts=[s['status'] for s in ledger['coverage'].values()]
            run['status']=('paused_work_limit' if 'pending' in counts else
                'completed_with_failures' if 'failed' in counts else 'completed' if eligible else 'no_eligible_units')
    finally:
        run['finished_utc']=_utc();run['elapsed_seconds']=time.perf_counter()-started
        if run['status']=='running':run['status']='interrupted'
        checkpoint()
        if temporary is not None:temporary.cleanup()
    ledger['status']=run['status'];_counts(ledger);_seal(ledger)
    validate_encoder_ledger(ledger)
    return ledger


def export_encoder_ledger_json(ledger):
    validate_encoder_ledger(ledger)
    return _json(ledger)


def load_encoder_ledger(path):
    path=encoder.local_file(path)
    if path.stat().st_size>MAX_LEDGER_BYTES:raise ValueError('Encoder ledger exceeds its byte bound.')
    ledger=json.loads(path.read_text(encoding='utf-8-sig'));validate_encoder_ledger(ledger)
    return ledger

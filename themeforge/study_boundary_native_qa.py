"""Bounded native accounting of boundary cores, never LLM text generation."""
import argparse
from dataclasses import asdict,replace
from hashlib import sha256
import json
from pathlib import Path
import sys
import threading
import time

from .study_native_qa import _local,_Resources
from .study_dialog import read_local_json
from .study_segments import load_segment_recovery
from .study_session import build_resumable_plan,validate_planning_session
from .study_runtime import StudyLlamaClient,available_ram_bytes
from .local_inference import LocalInferenceConfig,InferenceLimits,GIB


def boundary_native_smoke(recovery,profile,output,*,prior=None,max_measurements=1):
    if type(max_measurements) is not int or not 1<=max_measurements<=24:raise ValueError('Use 1..24 new accounting measurements.')
    recovery,profile,output=map(_local,(recovery,profile,output))
    if output.exists():raise ValueError('Native boundary QA output must be fresh.')
    value=load_segment_recovery(recovery);study=value['plan']['derived_study']
    saved=read_local_json(_local(prior)) if prior else None
    if saved is not None:validate_planning_session(saved)
    data=read_local_json(profile,65536);limits=InferenceLimits(**data['limits'])
    if limits.threads>2 or limits.context_tokens!=4096 or limits.output_tokens!=512 or limits.memory_limit_bytes!=5*GIB:
        raise ValueError('This QA requires the bounded CPU2 /4096 /512 /5 GiB profile.')
    config=LocalInferenceConfig(**{k:data[k] for k in ('executable_path','executable_sha256','model_path','model_sha256')},limits=limits)
    if getattr(sys,'frozen',False):
        from .bundled_runtime import RUNTIME_MANIFEST_SHA256
        from .local_model_assets import resolve_app_local_runtime
        config=replace(config,executable_path=resolve_app_local_runtime(Path(sys.executable).parent,RUNTIME_MANIFEST_SHA256))
    output.mkdir(parents=True)
    started=time.monotonic();resources=_Resources();stop=threading.Event()
    def observe():
        while not stop.wait(.25):resources.sample()
    monitor=threading.Thread(target=observe,daemon=True);monitor.start()
    report=dict(status='started',frozen_executable=bool(getattr(sys,'frozen',False)),generation_calls=0,
        recovery_sha256=sha256(recovery.read_bytes()).hexdigest(),profile_sha256=sha256(profile.read_bytes()).hexdigest(),
        input_context_tokens=4096,generation_reserve_tokens=512,eos_reserve_tokens=1,max_new_measurements=max_measurements,
        available_ram_before_bytes=available_ram_bytes(),resource_limit_kind='sampled_process_tripwire_plus_2GiB_prestart_headroom',
        model_sha256=config.model_sha256,runtime_sha256=config.executable_sha256,deadline_seconds=180,
        meaning_validated=False,selected_source_cores=len(study['units']))
    client=StudyLlamaClient(config,audit_directory=output/'native-calls');code=1
    try:
        client.period_deadline=time.monotonic()+180
        client.start(threading.Event())
        session=build_resumable_plan(study,client,output/'planning',prior=saved,
            wall_seconds=max(1,180-(time.monotonic()-started)),max_native_measurements=max_measurements)
        checkpoint=read_local_json(output/'planning/planning-session.json');validate_planning_session(checkpoint)
        old={} if saved is None else saved['checkpoint']['measurements']
        current=checkpoint['checkpoint']['measurements']
        assert all(current.get(k)==v for k,v in old.items()),'Completed native accounting changed.'
        new=set(current)-set(old)
        assert len([x for x in client.native_calls if x['endpoint']=='/apply-template'])==len(new),'Accounting was replayed.'
        assert not any(x['endpoint']=='/completion' for x in client.native_calls),'Generation is forbidden.'
        if session is not None:
            (output/'boundary-native-plan.json').write_text(json.dumps(session,ensure_ascii=False,indent=2),encoding='utf-8')
            assert len(session['plan']['batches'])==len(study['units'])
            assert all(b['reserved_total_tokens']<=4096 for b in session['plan']['batches'])
            assert all(i['context_spans']==next(u for u in study['units'] if u['id']==i['parent_unit_id'])['execution_context_spans'] for i in session['plan']['work_items'])
            report['native_budget_rows']=[dict(work_item_ids=b['work_item_ids'],prompt_tokens=b['native_prompt']['prompt_tokens'],generation_reserve=512,eos_reserve=1,total=b['reserved_total_tokens']) for b in session['plan']['batches']]
        report.update(status='passed',planning_status=checkpoint['checkpoint']['status'],
            prior_measurements=len(old),new_measurements=len(new),total_measurements=len(current),
            completed_measurements_preserved=True,accounting_replayed=False,
            all_core_requests_planned=session is not None,exact_context_preserved=session is not None)
        code=0
    except Exception as error:report.update(status='failed',error_type=type(error).__name__,error=str(error))
    finally:
        client.close();report['diagnostics']=client.diagnostics();report['owned_helper_closed']=client.process is None
        stop.set();monitor.join(timeout=2);resources.sample(force=True)
        report.update(elapsed_seconds=time.monotonic()-started,resources=resources.report(),native_calls=client.native_calls)
        if not report['owned_helper_closed']:report['status']='cleanup_failed';code=1
        (output/'QA.json').write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
    return code


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    for key in ('recovery','profile','out'):parser.add_argument('--'+key,required=True)
    parser.add_argument('--prior');parser.add_argument('--max-measurements',type=int,default=1)
    args=parser.parse_args(argv)
    return boundary_native_smoke(args.recovery,args.profile,args.out,prior=args.prior,max_measurements=args.max_measurements)


if __name__=='__main__':raise SystemExit(main())

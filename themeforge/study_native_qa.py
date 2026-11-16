"""Explicit developer native study QA. Never runs on import or normal app launch."""
from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
import sys
import time


def _local(value):
    path = Path(value)
    if not path.is_absolute() or str(path).startswith(('\\\\', '//')):
        raise ValueError('An explicit absolute local path is required.')
    path = path.resolve()
    if str(path).startswith(('\\\\', '//')):
        raise ValueError('Network-share paths are prohibited.')
    return path


class _FirstPassageQueue:
    """Cancel in the worker's progress callback, before its next batch check."""
    def __init__(self, original, cancel_event):
        self.original, self.cancel_event = original, cancel_event
        self.first_coding_progress = None

    def put(self, event, *args, **kwargs):
        kind, payload = event
        if kind == 'progress' and payload.get('stage') == 'coding' and self.first_coding_progress is None:
            self.first_coding_progress = deepcopy(payload)
            self.cancel_event.set()
        return self.original.put(event, *args, **kwargs)

    def get_nowait(self):
        return self.original.get_nowait()


class _Resources:
    def __init__(self):
        import psutil
        self.psutil = psutil
        self.parent = psutil.Process()
        self.samples = []
        self.identities = {}
        self.last_sample = -float('inf')
        self.started = time.monotonic()

    def sample(self, force=False):
        now = time.monotonic()
        if not force and now-self.last_sample < 1:
            return
        self.last_sample = now
        rows = []
        try:
            processes = [self.parent, *self.parent.children(recursive=True)]
        except self.psutil.Error:
            processes = [self.parent]
        for process in processes:
            try:
                if not process.is_running():
                    continue
                created = process.create_time()
                identity = f'{process.pid}:{created}'
                cpu = process.cpu_times()
                row = {'pid':process.pid,'created':created,'name':process.name(),
                       'rss_bytes':process.memory_info().rss,'cpu_seconds':cpu.user+cpu.system}
                prior = self.identities.setdefault(identity,{'pid':process.pid,'created':created,'name':row['name'],
                    'first_cpu_seconds':row['cpu_seconds'],'last_cpu_seconds':row['cpu_seconds']})
                prior['last_cpu_seconds'] = row['cpu_seconds']
                rows.append(row)
            except self.psutil.Error:
                pass
        self.samples.append({'elapsed_seconds':now-self.started,'owned_tree_rss_bytes':sum(r['rss_bytes'] for r in rows),'processes':rows})

    def report(self):
        return {'scope':'current QA process plus sampled descendants; no command lines; observed CPU deltas may omit work between samples',
                'sample_interval_seconds':1,'sample_count':len(self.samples),
                'peak_sampled_owned_tree_rss_bytes':max((s['owned_tree_rss_bytes'] for s in self.samples),default=0),
                'observed_cpu_seconds':sum(max(0,r['last_cpu_seconds']-r['first_cpu_seconds']) for r in self.identities.values()),
                'process_identities':list(self.identities.values()),'samples':self.samples}


def study_native_smoke(study, profile, output, *, session=None, client_factory=None, deadline_seconds=300):
    """Explicit one-passage native run through real hidden Tk UI, returning 0/1.

    client_factory is only a synthetic-test seam; CLI has no injection option.
    The real runtime remains owned by StudyDialog/StudyLlamaClient. This helper
    neither downloads nor invokes PATH/global model services.
    """
    if not isinstance(deadline_seconds,(int,float)) or not 10 <= deadline_seconds <= 300:
        raise ValueError('QA deadline must be between 10 and 300 seconds.')
    study_path,profile_path,output = map(_local,(study,profile,output))
    if output.exists():
        raise ValueError('Native study QA output must be fresh.')
    originals = {}
    for name,path,limit in (('study',study_path,128*1024**2),('profile',profile_path,65536)):
        if path.stat().st_size > limit:
            raise ValueError('Study/profile exceeds its explicit input bound.')
        originals[name] = path.read_bytes()
    source = json.loads(originals['study'].decode('utf-8-sig'))
    from .study_batches import validate_study
    from .study_session import validate_session
    validate_study(source)
    resumed = None
    session_path = _local(session) if session is not None else None
    if session_path is not None:
        if session_path.stat().st_size > 128*1024**2:
            raise ValueError('Session exceeds its explicit input bound.')
        originals['session'] = session_path.read_bytes()
        resumed = json.loads(originals['session'].decode('utf-8-sig'))
        validate_session(resumed)
        if resumed['plan']['study'] != source:
            raise ValueError('Resume fixture must retain the identical whole study.')
        if resumed['calls'] or resumed['codes']:
            raise ValueError('Native QA resume requires an unexecuted plan, not previous answers.')
    output.mkdir(parents=True,exist_ok=False)
    started = time.monotonic()
    deadline = started+deadline_seconds
    report = {'status':'started','real_native_requested':client_factory is None,
              'synthetic_test_only':client_factory is not None,'semantic_validation':False,
              'frozen_executable':bool(getattr(sys,'frozen',False)), 'deadline_seconds':deadline_seconds,
              'inputs':{name:{'path':str(path),'sha256':sha256(originals[name]).hexdigest(),'bytes':len(originals[name])} for name,path in (('study',study_path),('profile',profile_path))}}
    if session_path is not None:
        report['inputs']['session'] = {'path':str(session_path),'sha256':sha256(originals['session']).hexdigest(),'bytes':len(originals['session'])}
        report['explicit_unexecuted_plan_resume'] = True
    app = dialog = resources = hook = None
    clients = []
    code = 1
    def within_budget():
        if time.monotonic() >= deadline:
            raise TimeoutError('Native study QA overall deadline exceeded.')
        if resources:
            resources.sample()
    try:
        from .app import ThemeForgeApp
        from .study_runtime import StudyLlamaClient
        resources = _Resources(); resources.sample(force=True)
        class HiddenApp(ThemeForgeApp):
            def _build_layout(self):
                self.withdraw()
                super()._build_layout()
        app = HiddenApp(); app.update()
        project_before = deepcopy({'documents':app.documents,'result':app.result,'audit_events':app.audit_events})
        dialog = app.open_study_coding(); dialog.withdraw(); app.update()
        assert dialog.config is None and dialog._worker is None
        assert dialog.load(study_path), dialog.status.get()
        assert dialog.session is None and dialog.study == source
        if session_path is not None:
            assert dialog.load(session_path), dialog.status.get()
            assert dialog.session == resumed and dialog.study == source
        assert dialog.load_profile(profile_path), dialog.status.get()
        if getattr(sys,'frozen',False):
            from .bundled_runtime import RUNTIME_MANIFEST_SHA256
            from .local_model_assets import resolve_app_local_runtime
            runtime = resolve_app_local_runtime(Path(sys.executable).parent,RUNTIME_MANIFEST_SHA256)
            dialog.config = replace(dialog.config,executable_path=runtime)
            report['bundled_runtime'] = {'path':str(runtime),'manifest_sha256':RUNTIME_MANIFEST_SHA256,'executable_sha256':sha256(runtime.read_bytes()).hexdigest()}
        report['selected_identity'] = {'model_sha256':dialog.config.model_sha256,'executable_sha256':dialog.config.executable_sha256}
        def factory(config, **kwargs):
            client = (client_factory or StudyLlamaClient)(config,**kwargs)
            clients.append(client)
            return client
        dialog._client_factory = factory
        hook = _FirstPassageQueue(dialog._events,dialog._cancel)
        dialog._events = hook
        dialog.minutes.set(4 if resumed is not None else 3); dialog.definitions.set(True)
        report['explicit_start_called'] = True
        within_budget()
        assert dialog.start_work(True,output/'work'), dialog.status.get()
        while dialog._worker is not None:
            app.update(); resources.sample()
            if time.monotonic() >= deadline-10:
                dialog._cancel.set()
                for client in clients:
                    client.close()
                raise TimeoutError('Native study QA work stopped for overall cleanup reserve.')
            time.sleep(.01)
        # Drain final UI events that may have raced the worker's final exit.
        for _ in range(4):
            app.update(); time.sleep(.025)
        within_budget()
        coding_files = sorted((output/'work').glob('study-*/coding/session.json'))
        report['persisted_session_paths'] = [str(p) for p in coding_files]
        assert len(coding_files)==1, 'No completed coding session saved; startup/planning failure is retained in work/.'
        result = json.loads(coding_files[0].read_text(encoding='utf-8'))
        validate_session(result)
        assert dialog.session == result, 'Final UI session differs from saved native session.'
        assert hook.first_coding_progress is not None, 'No passage coding progress reached.'
        passage_calls = [c for c in result['calls'] if c['stage']=='codes']
        assert len(passage_calls)==1, 'QA must stop before a second passage.'
        report.update(session_status=result['status'],coverage=result['coverage'],codes=len(result['codes']),
            raw_call_count=len(result['calls']),passage_call_count=len(passage_calls),
            call_status_counts={status:sum(c.get('status')==status for c in result['calls']) for status in {c.get('status') for c in result['calls']}},
            first_coding_progress=hook.first_coding_progress,
            imported_turns=len(source['units']),eligible_original_turns=sum(u['eligible'] for u in source['units']),
            planned_work_items=len(result['plan']['work_items']),native_batches=len(result['plan']['batches']),
            definition_statuses=[c['definition_status'] for c in result['codes']])
        assert passage_calls[0]['status']=='structure_valid_not_semantic_accuracy', 'First passage raw/schema outcome failed; not semantic abstention.'
        assert not result['unfinished_definition_code_ids'], 'Definition work incomplete; raw failures retained.'
        assert all(c['origin']=='local_model_draft' and c['human_review'] is False for c in result['codes'])
        assert list(dialog.tree.get_children()) == [u['id'] for u in source['units']]
        processed = next(u for u in source['units'] if any(i['parent_unit_id']==u['id'] and i['id']==passage_calls[0]['work_item_id'] for i in result['plan']['work_items']))
        dialog.tree.selection_set(processed['id']); dialog.show_selected()
        shown = json.loads(dialog.detail.get('1.0','end'))
        assert shown['original_source_turn']==processed
        assert shown['unreviewed_model_codes']==[c for c in result['codes'] if c['parent_unit_id']==processed['id']]
        for kind in ('json','html'):
            within_budget()
            assert dialog.export(kind,output/('native-session-export.'+kind)), dialog.status.get()
        assert dialog.load(output/'native-session-export.json'), dialog.status.get()
        assert dialog.session==result
        assert project_before=={'documents':app.documents,'result':app.result,'audit_events':app.audit_events}
        assert study_path.read_bytes()==originals['study'] and profile_path.read_bytes()==originals['profile']
        if session_path is not None:
            assert session_path.read_bytes()==originals['session']
        within_budget()
        report.update(status='passed',actual_hidden_Tk=True,exact_UI_source_and_code_rows=True,
                      export_JSON_reopen_exact=True,export_HTML_written=True,original_inputs_unchanged=True,
                      main_project_unchanged=True,model_outputs_unreviewed=True)
        code = 0
    except Exception as error:
        report.update(status='failed',error_type=type(error).__name__,error=str(error))
    finally:
        if dialog is not None:
            dialog._cancel.set()
        cleanup_errors = []
        for client in clients:
            try:
                client.close()
            except Exception as error:
                cleanup_errors.append(type(error).__name__)
        worker = getattr(dialog,'_worker',None) if dialog else None
        cleanup_deadline = min(deadline,time.monotonic()+10)
        while worker is not None and worker.is_alive() and time.monotonic()<cleanup_deadline:
            if app is not None:
                app.update()
            time.sleep(.01)
        report['owned_worker_remaining'] = bool(worker and worker.is_alive())
        report['owned_clients'] = [client.diagnostics() for client in clients]
        report['cleanup_errors'] = cleanup_errors
        report['owned_clients_closed'] = all(client.process is None for client in clients)
        if cleanup_errors or report['owned_worker_remaining'] or not report['owned_clients_closed']:
            report.update(status='failed',cleanup_incomplete=True); code=1
        if resources:
            resources.sample(force=True)
            report['resources'] = resources.report()
        if dialog is not None and dialog.winfo_exists():
            dialog.close()
        if app is not None:
            app.destroy()
        report.update(elapsed_seconds=time.monotonic()-started,app_closed=app is not None)
        report['outputs'] = {str(p.relative_to(output)):{'bytes':p.stat().st_size,'sha256':sha256(p.read_bytes()).hexdigest()} for p in output.rglob('*') if p.is_file()}
        with (output/'QA.json').open('x',encoding='utf-8') as stream:
            json.dump(report,stream,ensure_ascii=False,indent=2,allow_nan=False)
    return code


def main(argv=None):
    parser = argparse.ArgumentParser(description='Explicit bounded native whole-study developer QA; starts one local model work period.')
    parser.add_argument('--study',required=True)
    parser.add_argument('--profile',required=True)
    parser.add_argument('--session',help='Explicit unexecuted source-bound native plan to resume; never prior model answers.')
    parser.add_argument('--out',required=True)
    args = parser.parse_args(argv)
    return study_native_smoke(args.study,args.profile,args.out,session=args.session)


if __name__ == '__main__':
    raise SystemExit(main())

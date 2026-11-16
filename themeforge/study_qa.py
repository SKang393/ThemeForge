"""Explicit no-inference whole-study UI/export smoke, source or packaged."""
from __future__ import annotations

import argparse
from contextlib import ExitStack
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser
from unittest.mock import patch


def _local(value):
    path = Path(value)
    if not path.is_absolute() or str(path).startswith(('\\\\', '//')):
        raise ValueError('Use an explicit absolute local path.')
    path = path.resolve()
    if str(path).startswith(('\\\\', '//')):
        raise ValueError('Network-share paths are not permitted.')
    return path


def study_smoke(output, *, study=None, session=None):
    """Return 0/1 and write SMOKE.json. Opens/exports only; no model startup.

    Inputs are optional independently. With neither, checks startup and controls.
    Each supplied study/session is reopened and exported in its native format.
    """
    output = _local(output)
    if output.exists():
        raise ValueError('Study smoke output must be fresh.')
    inputs = {}
    for kind, value in (('study',study), ('session',session)):
        if value is not None:
            path = _local(value)
            if path.stat().st_size > 128*1024**2:
                raise ValueError('Study/session input exceeds 128 MiB.')
            raw = path.read_bytes()
            inputs[kind] = (path, raw, json.loads(raw.decode('utf-8-sig')))
    output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    report = {'status':'started','new_model_calls':0,'semantic_validation':False,
              'frozen_executable':bool(getattr(sys,'frozen',False)), 'inputs':{kind:{'path':str(path),'bytes':len(raw),'sha256':sha256(raw).hexdigest()} for kind,(path,raw,_) in inputs.items()}}
    app = dialog = None
    attempts = []
    def blocked(*args, **kwargs):
        attempts.append(True)
        raise RuntimeError('Model/process/network/browser actions prohibited during study review smoke.')
    try:
        from . import study_dialog
        from .study_batches import validate_study
        from .study_session import validate_session
        from .app import ThemeForgeApp
        with ExitStack() as guards:
            for owner,name in ((subprocess,'Popen'),(socket,'socket'),(urllib.request.OpenerDirector,'open'),
                               (webbrowser,'open'),(webbrowser,'open_new'),(webbrowser,'open_new_tab'),
                               (study_dialog,'StudyLlamaClient')):
                guards.enter_context(patch.object(owner,name,blocked))
            class HiddenApp(ThemeForgeApp):
                def _build_layout(self):
                    self.withdraw()
                    super()._build_layout()
            app = HiddenApp()
            app.update()
            before = deepcopy({'documents':app.documents,'result':app.result,'audit_events':app.audit_events})
            dialog = app.open_study_coding()
            dialog.withdraw()
            app.update()
            assert dialog.config is None and dialog._worker is None
            checks = []
            for kind,(path,raw,value) in inputs.items():
                if kind == 'study':
                    validate_study(value)
                else:
                    validate_session(value)
                assert dialog.load(path), dialog.status.get()
                app.update()
                ledger = value if kind == 'study' else value['plan']['study']
                assert dialog.study == ledger
                assert dialog.session == (value if kind == 'session' else None)
                ids = list(dialog.tree.get_children())
                assert ids == [unit['id'] for unit in ledger['units']]
                sources = {s['source_name']:s for s in ledger['sources']}
                spans = 0
                for unit in ledger['units']:
                    source = sources[unit['source_name']]
                    assert source['source_hash'] == unit['source_hash']
                    assert source['text'][unit['source_start']:unit['source_end']] == unit['text']
                    dialog.tree.selection_set(unit['id'])
                    dialog.show_selected()
                    shown = json.loads(dialog.detail.get('1.0','end'))
                    assert shown['original_source_turn'] == unit
                    if kind == 'session':
                        expected = [c for c in value['codes'] if c['parent_unit_id']==unit['id']]
                        assert shown['unreviewed_model_codes'] == expected
                        for code in expected:
                            for span in code['evidence']+code['referent_context_not_support']+code.get('qualifications',[])+code.get('counterexamples',[]):
                                src = sources[span['source_name']]
                                assert src['source_hash']==span['source_hash']
                                assert src['text'][span['source_start']:span['source_end']]==span['text']
                                spans += 1
                json_path = output/(kind+'-export.json')
                assert dialog.export('json',json_path), dialog.status.get()
                assert json.loads(json_path.read_text(encoding='utf-8')) == value
                if kind == 'session':
                    assert dialog.export('html',output/'session-export.html'), dialog.status.get()
                assert dialog.load(json_path), dialog.status.get()
                assert (dialog.session if kind=='session' else dialog.study) == value
                assert path.read_bytes() == raw
                assert dialog.config is None and dialog._worker is None
                checks.append({'kind':kind,'unit_rows':len(ids),'exact_code_role_spans':spans,'source_exact':True,'json_export_exact':True,'json_UI_reopen_exact':True,'input_unchanged':True})
            assert before == {'documents':app.documents,'result':app.result,'audit_events':app.audit_events}
            report['runtime'] = {'status':'not_required_source_checkout'}
            if getattr(sys,'frozen',False):
                from .bundled_runtime import RUNTIME_MANIFEST_SHA256
                from .local_model_assets import resolve_app_local_runtime
                executable = resolve_app_local_runtime(Path(sys.executable).parent,RUNTIME_MANIFEST_SHA256)
                report['runtime'] = {'status':'verified_offline_not_started','path':str(executable),'manifest_sha256':RUNTIME_MANIFEST_SHA256,'executable_sha256':sha256(executable.read_bytes()).hexdigest()}
            assert not attempts
            report.update(status='passed',actual_hidden_Tk=True,checks=checks,no_model_autoload=True,main_project_unchanged=True)
        code = 0
    except Exception as error:
        report.update(status='failed',error_type=type(error).__name__,error=str(error))
        code = 1
    finally:
        worker = getattr(dialog,'_worker',None) if dialog else None
        if dialog is not None and dialog.winfo_exists():
            dialog.close()
            deadline = time.monotonic()+15
            while worker is not None and worker.is_alive() and time.monotonic()<deadline:
                app.update()
                time.sleep(.01)
        report['owned_worker_remaining'] = bool(worker and worker.is_alive())
        if report['owned_worker_remaining']:
            report.update(status='failed',cleanup_error='Owned worker did not settle during bounded cleanup.')
            code = 1
        if app is not None:
            app.destroy()
        report.update(elapsed_seconds=time.monotonic()-started,app_closed=app is not None,blocked_action_attempts=len(attempts))
        report['outputs'] = {p.name:{'bytes':p.stat().st_size,'sha256':sha256(p.read_bytes()).hexdigest()} for p in output.iterdir() if p.is_file()}
        with (output/'SMOKE.json').open('x',encoding='utf-8') as stream:
            json.dump(report,stream,ensure_ascii=False,indent=2,allow_nan=False)
    return code


def main(argv=None):
    parser = argparse.ArgumentParser(description='Offline whole-study UI/export smoke; no inference.')
    parser.add_argument('--out',required=True)
    parser.add_argument('--study')
    parser.add_argument('--session')
    args = parser.parse_args(argv)
    return study_smoke(args.out,study=args.study,session=args.session)


if __name__ == '__main__':
    raise SystemExit(main())

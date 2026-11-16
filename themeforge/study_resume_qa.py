"""Explicit two-period hidden Tk native planning resume QA; no generation.

The complete input study stays in the UI and checkpoint. Each explicitly
started period may measure only one new native request. This bounded smoke is
not whole-study planning, coding, or semantic acceptance.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import asdict, replace
from hashlib import sha256
import json
import math
from pathlib import Path
import sys
import time
from unittest.mock import patch

from .study_native_qa import _Resources, _local


def _check_checkpoint(value, source, count):
    from .study_batches import digest
    from .study_session import coding_request, validate_planning_session
    validate_planning_session(value)
    assert value['study'] == source, 'Planning checkpoint dropped or changed original study units.'
    checkpoint = value['checkpoint']
    assert checkpoint['status'] == 'interrupted', 'The one-measurement period was not interrupted.'
    assert 'measurement limit' in checkpoint['reason'], 'Unexpected planning interruption reason.'
    assert len(checkpoint['measurements']) == count, 'Unexpected cumulative native measurement count.'
    assert checkpoint['identity']['study_sha256'] == source['study_sha256']
    sources = {s['source_name']:s for s in source['sources']}
    units = {u['id']:u for u in source['units']}
    for key, record in checkpoint['measurements'].items():
        request, native = record['request'], record['native_prompt']
        assert key == digest(request) == native['request_sha256']
        actual = coding_request(request, mode=checkpoint['identity']['mode'],
                                research_questions=checkpoint['identity']['research_questions'])
        assert actual['request_sha256'] == value['application_request_sha256'][key]
        for target in request['targets']:
            parent = units[target['parent_unit_id']]
            original = sources[target['source_name']]
            assert target['source_hash'] == original['source_hash'] == parent['source_hash']
            assert original['text'][target['source_start']:target['source_end']] == target['text']
            assert parent['source_start'] <= target['source_start'] < target['source_end'] <= parent['source_end']
    return checkpoint


def study_resume_smoke(study, profile, output, *, client_factory=None, deadline_seconds=180):
    """Run real UI/native accounting twice; factory injection is synthetic-only."""
    if (type(deadline_seconds) not in (int, float) or not math.isfinite(deadline_seconds)
            or not 10 <= deadline_seconds <= 180):
        raise ValueError('Use a finite 10..180 second overall QA deadline.')
    study_path, profile_path, output = map(_local, (study, profile, output))
    if output.exists():
        raise ValueError('Native resume QA output must be fresh.')
    originals = {}
    for name, path, maximum in (('study', study_path, 64*1024**2), ('profile', profile_path, 65536)):
        with path.open('rb') as stream:
            originals[name] = stream.read(maximum + 1)
        if len(originals[name]) > maximum:
            raise ValueError('Native resume QA input exceeds its explicit byte bound: ' + name)
    source = json.loads(originals['study'].decode('utf-8-sig'))
    from .study_batches import validate_study
    validate_study(source)
    if sum(u['eligible'] for u in source['units']) < 3:
        raise ValueError('Two interrupted periods require at least three eligible original turns.')
    output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    deadline = started + deadline_seconds
    report = dict(status='started', real_native_requested=client_factory is None,
        synthetic_test_only=client_factory is not None, frozen_executable=bool(getattr(sys, 'frozen', False)),
        deadline_seconds=deadline_seconds, generation_calls=0, semantic_validation=False,
        scope='two explicit one-new-measurement planning periods on the unchanged full input study',
        inputs={name:dict(path=str(path), bytes=len(originals[name]), sha256=sha256(originals[name]).hexdigest())
                for name, path in (('study', study_path), ('profile', profile_path))}, periods=[])
    app = dialog = resources = None
    clients = []
    headroom = []
    code = 1

    def within_budget():
        if time.monotonic() >= deadline - 10:
            raise TimeoutError('Native resume QA deadline reached; cleanup reserve retained.')
        resources.sample()

    try:
        from . import study_dialog
        from .app import ThemeForgeApp
        from .study_runtime import StudyLlamaClient, available_ram_bytes
        from .study_session import build_resumable_plan
        resources = _Resources()
        resources.sample(force=True)
        class HiddenApp(ThemeForgeApp):
            def _build_layout(self):
                self.withdraw()
                super()._build_layout()
        app = HiddenApp()
        app.update()
        before = deepcopy(dict(documents=app.documents, result=app.result, audit_events=app.audit_events))
        dialog = app.open_study_coding()
        dialog.withdraw()
        app.update()
        assert dialog.load(study_path), dialog.status.get()
        assert dialog.load_profile(profile_path), dialog.status.get()
        if getattr(sys, 'frozen', False):
            from .bundled_runtime import RUNTIME_MANIFEST_SHA256
            from .local_model_assets import resolve_app_local_runtime
            runtime = resolve_app_local_runtime(Path(sys.executable).parent, RUNTIME_MANIFEST_SHA256)
            assert sha256(runtime.read_bytes()).hexdigest() == dialog.config.executable_sha256
            dialog.config = replace(dialog.config, executable_path=runtime)
            report['bundled_runtime'] = dict(path=str(runtime), manifest_sha256=RUNTIME_MANIFEST_SHA256,
                                             executable_sha256=dialog.config.executable_sha256)
        report['selected_identity'] = dict(model_sha256=dialog.config.model_sha256,
            executable_sha256=dialog.config.executable_sha256, limits=asdict(dialog.config.limits))
        assert dialog.config.limits.memory_limit_bytes is not None and dialog.config.limits.threads <= 2

        def factory(config, **kwargs):
            client = (client_factory or StudyLlamaClient)(config, **kwargs)
            clients.append(client)
            headroom.append(dict(available_ram_before_start=available_ram_bytes(),
                memory_limit_bytes=config.limits.memory_limit_bytes,
                headroom_reserve_bytes=getattr(client, 'headroom_reserve_bytes', None),
                real_StudyLlamaClient=client_factory is None))
            return client

        def one_measurement(*args, **kwargs):
            kwargs['max_native_measurements'] = 1
            return build_resumable_plan(*args, **kwargs)

        dialog._client_factory = factory
        dialog.minutes.set(1)
        dialog.definitions.set(False)
        first = None
        with patch.object(study_dialog, 'build_resumable_plan', side_effect=one_measurement):
            for period in (1, 2):
                within_budget()
                assert dialog.session is None and dialog.study == source
                assert list(dialog.tree.get_children()) == [u['id'] for u in source['units']]
                period_parent = output / ('period-%s' % period)
                assert dialog.start_work(False, period_parent), dialog.status.get()
                while dialog._worker is not None:
                    app.update()
                    within_budget()
                    time.sleep(.01)
                app.update()
                within_budget()
                assert len(clients) == period, 'Expected a distinct owned native client for each explicit period.'
                client = clients[-1]
                assert client.process is None, 'Owned native helper remained open after period.'
                assert dialog.session is None and dialog.planning is not None, dialog.status.get()
                snapshot = deepcopy(dialog.planning)
                checkpoint = _check_checkpoint(snapshot, source, period)
                assert dialog.study == source
                assert [dialog.tree.item(u['id'], 'values')[2] for u in source['units']] == [u['status'] for u in source['units']]
                calls = deepcopy(client.native_calls)
                endpoints = [call['endpoint'] for call in calls]
                assert endpoints == ['/apply-template', '/tokenize'], 'Expected exactly one new native request and no generation.'
                assert not getattr(client, 'generated', []), 'Synthetic seam unexpectedly generated output.'
                assert not list(period_parent.rglob('coding')), 'Planning-only work created a coding folder.'
                files = list(period_parent.glob('study-*/planning/planning-session.json'))
                assert len(files) == 1, 'Planning-session envelope not saved.'
                saved_bytes = files[0].read_bytes()
                assert json.loads(saved_bytes) == snapshot
                if first is None:
                    first = deepcopy(snapshot)
                else:
                    assert checkpoint['identity'] == first['checkpoint']['identity'], 'Planning settings changed between periods.'
                    for key, value in first['checkpoint']['measurements'].items():
                        assert checkpoint['measurements'].get(key) == value, 'Previously measured native request changed.'
                    assert report['periods'][0]['planning_file_sha256'] == sha256(Path(report['periods'][0]['planning_path']).read_bytes()).hexdigest()
                exported = output / ('planning-period-%s.json' % period)
                assert dialog.export('json', exported), dialog.status.get()
                assert json.loads(exported.read_text(encoding='utf-8')) == snapshot
                assert dialog.load(exported), dialog.status.get()
                assert dialog.planning == snapshot and dialog.session is None and dialog.study == source
                assert list(dialog.tree.get_children()) == [u['id'] for u in source['units']]
                report['periods'].append(dict(period=period, cumulative_native_measurements=period,
                    new_native_measurements=1, checkpoint_status=checkpoint['status'],
                    checkpoint_reason=checkpoint['reason'], planning_path=str(files[0]),
                    planning_file_sha256=sha256(saved_bytes).hexdigest(),
                    full_UI_rows=len(source['units']), native_calls=calls,
                    exported_and_reopened_exactly=True, owned_helper_closed=True,
                    diagnostics=client.diagnostics(), headroom=headroom[-1]))
        assert before == dict(documents=app.documents, result=app.result, audit_events=app.audit_events)
        assert study_path.read_bytes() == originals['study'] and profile_path.read_bytes() == originals['profile']
        within_budget()
        report.update(status='passed', actual_hidden_Tk=True, full_study_unchanged=True,
            original_inputs_unchanged=True, main_project_unchanged=True,
            first_native_measurement_retained_exactly=True, cached_request_not_remeasured=True,
            source_app_native_request_bindings_verified=True, planning_JSON_export_reopen_exact=True,
            imported_turns=len(source['units']), eligible_original_turns=sum(u['eligible'] for u in source['units']),
            source_count=len(source['sources']), native_prompt_measurements=2, native_accounting_endpoints=4)
        code = 0
    except Exception as error:
        report.update(status='failed', error_type=type(error).__name__, error=str(error))
    finally:
        if dialog is not None:
            dialog._cancel.set()
        cleanup_errors = []
        for client in clients:
            try:
                client.close()
            except Exception as error:
                cleanup_errors.append(type(error).__name__ + ': ' + str(error))
        worker = getattr(dialog, '_worker', None) if dialog is not None else None
        cleanup_deadline = min(deadline, time.monotonic()+10)
        while worker is not None and worker.is_alive() and time.monotonic() < cleanup_deadline:
            if app is not None:
                app.update()
            time.sleep(.01)
        report['owned_worker_remaining'] = bool(worker and worker.is_alive())
        report['owned_clients_closed'] = all(client.process is None for client in clients)
        report['cleanup_errors'] = cleanup_errors
        report['owned_clients'] = [dict(diagnostics=client.diagnostics(),
            native_calls=deepcopy(getattr(client, 'native_calls', []))) for client in clients]
        report['generation_calls'] = sum(call.get('endpoint') == '/completion'
            for client in report['owned_clients'] for call in client['native_calls'])
        if resources is not None:
            resources.sample(force=True)
            report['resources'] = resources.report()
        if cleanup_errors or report['owned_worker_remaining'] or not report['owned_clients_closed']:
            report.update(status='failed', cleanup_incomplete=True)
            code = 1
        if dialog is not None and dialog.winfo_exists():
            dialog.close()
        if app is not None:
            app.destroy()
        report.update(elapsed_seconds=time.monotonic()-started, app_closed=app is not None)
        report['outputs'] = {str(p.relative_to(output)):dict(bytes=p.stat().st_size,
            sha256=sha256(p.read_bytes()).hexdigest()) for p in output.rglob('*') if p.is_file()}
        with (output/'QA.json').open('x', encoding='utf-8') as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
    return code


def main(argv=None):
    parser = argparse.ArgumentParser(description='Explicit native interrupted planning/resume UI QA; two accounting requests, no generation.')
    for name in ('study', 'profile', 'out'):
        parser.add_argument('--'+name, required=True)
    args = parser.parse_args(argv)
    return study_resume_smoke(args.study, args.profile, args.out)


if __name__ == '__main__':
    raise SystemExit(main())

"""Explicit hidden Tk boundary-core encoder QA, source or packaged.

One invocation starts one bounded recovery period. A later invocation can reopen
the exported partial envelope and encode only remaining ES cores. No GGUF or
generation is used, and old full-study vectors/history remain immutable.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
from copy import deepcopy
from hashlib import sha256
import json
import math
from pathlib import Path
import socket
import sys
import time
import urllib.request
import webbrowser
from unittest.mock import patch

from .study_native_qa import _Resources, _local


def _read(path, maximum):
    with path.open('rb') as stream:
        raw = stream.read(maximum + 1)
    if len(raw) > maximum:
        raise ValueError('Boundary QA input exceeds its explicit byte bound.')
    return raw


def _check_transition(before, after, max_new_cores):
    from .study_segments import validate_segment_recovery
    from .semantic_neighbors import digest
    validate_segment_recovery(after)
    assert after['original_encoder_ledger'] == before['original_encoder_ledger'], 'Original vectors/history changed.'
    assert after['plan'] == before['plan'], 'Source/core/token/context plan changed.'
    assert after['before_ledger_sha256'] == before['before_ledger_sha256']
    old = before['encoder_ledger']
    saved = after['encoder_ledger']
    assert saved is not None, 'No derived encoder checkpoint returned.'
    old_vectors = old['vectors'] if old else {}
    old_batches = old['batches'] if old else []
    pending = {s['id'] for s in before['plan']['segments']
               if (old['coverage'][s['id']]['status'] == 'pending' if old else s['planning_status'] == 'pending')}
    new_ids = set(saved['vectors']) - set(old_vectors)
    assert len(new_ids) == min(max_new_cores, len(pending)), 'Recovery did not finish the requested bounded new-core work.'
    assert new_ids <= pending and all(uid.startswith('ES-') for uid in new_ids)
    assert all(saved['vectors'].get(uid) == row for uid, row in old_vectors.items()), 'A completed segment vector was repeated or changed.'
    assert saved['batches'][:len(old_batches)] == old_batches, 'Previous segment attempts changed.'
    if old:
        assert saved['runs'][:-1] == old['runs'], 'Previous segment work periods changed.'
    new_batches = saved['batches'][len(old_batches):]
    attempts = [uid for batch in new_batches for uid in batch['unit_ids']]
    assert set(attempts) == new_ids and len(attempts) == len(new_ids), 'A completed/original core was repeated or a new core failed.'
    original = after['original_encoder_ledger']
    assert not set(saved['vectors']) & set(original['vectors'])
    assert after['parent_unit_id'] not in saved['vectors'], 'A pooled parent vector was invented.'
    segments = after['plan']['segments']
    assert ''.join(s['text'] for s in segments) == after['plan']['parent']['text']
    by_id = {s['id']:s for s in segments}
    token_accounting = []
    for batch in new_batches:
        assert batch['status'] == 'completed'
        assert batch['resources']['owned_processes_closed'], 'A segment worker did not close.'
        for uid, diagnostic in zip(batch['unit_ids'], batch['encoded']['provenance']['tokenization']):
            core = by_id[uid]
            assert diagnostic['node_id'] == uid and diagnostic['truncated_tokens'] == 0
            assert diagnostic['payload_tokens'] == core['payload_tokens'] <= 254
            assert len(diagnostic['chunks']) == 1
            assert diagnostic['chunks'][0]['input_ids'] == [101] + core['payload_token_ids'] + [102]
            assert core['input_tokens'] == core['payload_tokens'] + 2 <= 256
            assert core['encoder_output_tokens'] == 0
            expected_ids = [101] + core['payload_token_ids'] + [102]
            token_accounting.append(dict(core_id=uid, payload_tokens=core['payload_tokens'],
                input_tokens=core['input_tokens'], special_tokens=2, encoder_output_tokens=0,
                planned_input_ids_sha256=digest(expected_ids),
                worker_input_ids_sha256=digest(diagnostic['chunks'][0]['input_ids']),
                exact_worker_planned_token_ids=True, windows=1, truncated_tokens=0))
    assert after['work_periods'][:-1] == before['work_periods']
    period = after['work_periods'][-1]
    assert period['max_units_per_run'] == max_new_cores and period['chunk_size'] == 1
    assert period['cpu_threads'] == 2 and period['sampled_tree_rss_limit_bytes'] == 2*1024**3
    assert period['available_ram_gate_bytes'] == 3*1024**3
    return dict(previous_completed_cores=len(old_vectors), new_completed_core_ids=sorted(new_ids),
        new_completed_cores=len(new_ids), new_worker_batches=len(new_batches),
        new_worker_resources=[b['resources'] for b in new_batches],
        original_completed_vectors_preserved=len(original['vectors']),
        original_failed_batches_preserved=sum(b['status'] == 'failed' for b in original['batches']),
        previous_segment_batches_preserved=len(old_batches),
        total_recovery_cores=len(segments), total_completed_recovery_cores=len(saved['vectors']),
        remaining_pending_cores=saved['coverage_counts'].get('pending', 0),
        failed_recovery_cores=saved['coverage_counts'].get('failed', 0),
        new_core_token_accounting=token_accounting,
        all_completed_core_tokens_exact_one_window=True, full_parent_character_reconstruction_exact=True,
        original_failed_parent_retained=True, parent_vector_created=False,
        recovery_coverage=after['recovery_coverage'], work_period=period)


def study_boundary_smoke(recovery, profile, output, *, study=None, max_new_cores=1, deadline_seconds=180):
    """Exercise actual UI recovery and reopen; production computations unchanged."""
    if (type(max_new_cores) is not int or not 1 <= max_new_cores <= 16
            or type(deadline_seconds) not in (int, float) or not math.isfinite(deadline_seconds)
            or not 10 <= deadline_seconds <= 180):
        raise ValueError('Use 1..16 new cores and a finite 10..180 second QA deadline.')
    paths = dict(recovery=_local(recovery), profile=_local(profile))
    if study is not None:
        paths['study'] = _local(study)
    output = _local(output)
    if output.exists():
        raise ValueError('Boundary QA output must be fresh.')
    originals = {name:_read(path, 65536 if name == 'profile' else 64*1024**2) for name, path in paths.items()}
    before = json.loads(originals['recovery'].decode('utf-8-sig'))
    from .study_segments import validate_segment_recovery, recovery_coverage
    validate_segment_recovery(before)
    if sha256(originals['profile']).hexdigest() != before['plan']['profile_binding']['profile_sha256']:
        raise ValueError('Boundary QA requires the original exact encoder profile.')
    source = (json.loads(originals['study'].decode('utf-8-sig')) if study is not None
              else before['original_encoder_ledger']['study'])
    recovery_coverage(before, source)
    output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    deadline = started + deadline_seconds
    report = dict(status='started', frozen_executable=bool(getattr(sys, 'frozen', False)),
        deadline_seconds=deadline_seconds, max_new_cores=max_new_cores, generation_calls=0,
        full_study_encoder_workers=0, semantic_validation=False, attribution_validated=False,
        inputs={name:dict(path=str(paths[name]), bytes=len(raw), sha256=sha256(raw).hexdigest())
                for name, raw in originals.items()})
    app = dialog = resources = None
    attempts = []
    code = 1

    def blocked(*args, **kwargs):
        attempts.append(True)
        raise RuntimeError('GGUF, network and browser startup prohibited during boundary-core QA.')

    def within_budget():
        if time.monotonic() >= deadline - 10:
            raise TimeoutError('Boundary-core QA overall deadline reached; cleanup reserve retained.')
        resources.sample()

    try:
        from . import study_dialog
        from .app import ThemeForgeApp
        import psutil
        resources = _Resources()
        resources.sample(force=True)
        report['available_ram_before_work_bytes'] = psutil.virtual_memory().available
        with ExitStack() as guards:
            for owner, name in ((socket, 'socket'), (urllib.request.OpenerDirector, 'open'),
                    (webbrowser, 'open'), (webbrowser, 'open_new'), (webbrowser, 'open_new_tab'),
                    (study_dialog, 'StudyLlamaClient')):
                guards.enter_context(patch.object(owner, name, blocked))
            class HiddenApp(ThemeForgeApp):
                def _build_layout(self):
                    self.withdraw()
                    super()._build_layout()
            app = HiddenApp()
            app.update()
            main_before = deepcopy(dict(documents=app.documents, result=app.result, audit_events=app.audit_events))
            dialog = app.open_study_coding()
            dialog.withdraw()
            dialog._client_factory = blocked
            app.update()
            if study is not None:
                assert dialog.load(paths['study']), dialog.status.get()
            assert dialog.load(paths['recovery']), dialog.status.get()
            assert dialog.segment_recovery == before and dialog.study == source
            assert dialog.session is None and dialog.config is None
            assert list(dialog.tree.get_children()) == [u['id'] for u in source['units']]
            dialog.minutes.set(2)
            within_budget()
            assert dialog.start_segment_recovery(str(paths['profile']), output/'work',
                                                  max_units_per_run=max_new_cores), dialog.status.get()
            while dialog._worker is not None:
                app.update()
                within_budget()
                time.sleep(.01)
            app.update()
            within_budget()
            after = dialog.segment_recovery
            assert after is not None and len(after['work_periods']) == len(before['work_periods']) + 1, dialog.status.get()
            report.update(_check_transition(before, after, max_new_cores))
            assert not attempts, 'A prohibited model/network action was attempted.'
            assert dialog.study == source and dialog.encoder_ledger == before['original_encoder_ledger']
            assert list(dialog.tree.get_children()) == [u['id'] for u in source['units']]
            parent = next(u for u in source['units'] if u['id'] == after['parent_unit_id'])
            dialog.tree.selection_set(parent['id'])
            dialog.show_selected()
            shown = json.loads(dialog.detail.get('1.0', 'end'))
            assert shown['original_source_turn'] == parent
            assert shown['separate_boundary_recovery']['coverage'] == after['recovery_coverage']
            assert shown['separate_boundary_recovery']['segments'] == after['plan']['segments']
            assert shown['separate_boundary_recovery']['original_failed_state'] == after['plan']['original_parent_coverage']
            final_paths = list((output/'work').glob('segment-recovery-*/segment-recovery.json'))
            assert len(final_paths) == 1, 'Final source-bound recovery checkpoint missing.'
            assert json.loads(final_paths[0].read_text(encoding='utf-8')) == after
            json_path, html_path = output/'boundary-recovery.json', output/'boundary-recovery.html'
            assert dialog.export('json', json_path), dialog.status.get()
            assert dialog.export('html', html_path), dialog.status.get()
            assert json.loads(json_path.read_text(encoding='utf-8')) == after
            html = html_path.read_text(encoding='utf-8')
            assert after['recovery_sha256'] in html and 'unresolved attribution' in html
            assert all(s['id'] in html for s in after['plan']['segments'])
            assert dialog.load(json_path), dialog.status.get()
            assert dialog.segment_recovery == after and dialog.study == source
            assert before == json.loads(originals['recovery'].decode('utf-8-sig'))
            assert main_before == dict(documents=app.documents, result=app.result, audit_events=app.audit_events)
            assert all(path.read_bytes() == originals[name] for name, path in paths.items())
            within_budget()
            report.update(status='passed', actual_hidden_Tk=True, actual_UI_recovery_start=True,
                full_UI_rows=len(source['units']), source_count=len(source['sources']),
                source_study_sha256=source['study_sha256'], recovery_sha256=after['recovery_sha256'],
                recovery_export_path=str(json_path), recovery_checkpoint_path=str(final_paths[0]),
                full_study_and_original_inputs_unchanged=True, main_project_unchanged=True,
                exact_UI_parent_and_recovery_overlay=True, export_JSON_reopen_exact=True,
                export_HTML_verified=True, completed_segment_vectors_not_repeated=True)
            code = 0
    except Exception as error:
        report.update(status='failed', error_type=type(error).__name__, error=str(error))
    finally:
        if dialog is not None:
            dialog._cancel.set()
        worker = getattr(dialog, '_worker', None) if dialog is not None else None
        cleanup_deadline = min(deadline, time.monotonic() + 10)
        while worker is not None and worker.is_alive() and time.monotonic() < cleanup_deadline:
            if app is not None:
                app.update()
            time.sleep(.01)
        report['owned_worker_remaining'] = bool(worker and worker.is_alive())
        report['prohibited_startup_attempts'] = len(attempts)
        remaining = []
        if resources is not None:
            resources.sample(force=True)
            report['resources'] = resources.report()
            for row in resources.identities.values():
                if row['pid'] == resources.parent.pid:
                    continue
                try:
                    process = resources.psutil.Process(row['pid'])
                    if process.create_time() == row['created'] and process.is_running():
                        remaining.append(row['pid'])
                except resources.psutil.NoSuchProcess:
                    pass
        report['observed_owned_descendants_remaining'] = remaining
        if remaining or report['owned_worker_remaining']:
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
    parser = argparse.ArgumentParser(description='Explicit bounded hidden Tk segment recovery QA; no full-study reencoding or LLM.')
    for name in ('recovery', 'profile', 'out'):
        parser.add_argument('--'+name, required=True)
    parser.add_argument('--study', help='Optional corrected full study to load before the recovery overlay.')
    parser.add_argument('--max-new-cores', type=int, default=1)
    args = parser.parse_args(argv)
    return study_boundary_smoke(args.recovery, args.profile, args.out, study=args.study,
                                max_new_cores=args.max_new_cores)


if __name__ == '__main__':
    raise SystemExit(main())

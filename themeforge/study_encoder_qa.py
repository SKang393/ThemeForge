"""Explicit hidden Tk whole-queue encoder-ledger QA, source or packaged.

The default pass uses only existing completed vectors and refuses pending work.
The optional worker check is additional one-unit verification, never new study
coverage. This module never runs work on import or normal application startup.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
from copy import deepcopy
from hashlib import sha256
from html import escape
import json
import math
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from unittest.mock import patch

from .study_native_qa import _Resources, _local


def _check_preserved(prior, current):
    """Historical failed attempts remain even when current vectors recovered."""
    assert current['study'] == prior['study'], 'Original full study changed.'
    for field in ('vectors', 'coverage', 'batch_ids', 'coverage_counts',
                  'profile_binding', 'embedding_provenance', 'batches'):
        assert current[field] == prior[field], 'Resumed encoder changed ' + field
    assert current.get('recovery_events', []) == prior.get('recovery_events', []), 'Recovery history changed.'
    assert current['runs'][:-1] == prior['runs'], 'Historical work periods changed.'
    assert current['runs'][-1]['attempted_units'] == 0, 'Unexpected new study encoding.'
    failed = [b for b in prior['batches'] if b['status'] == 'failed']
    current_by_id = {b['id']: b for b in current['batches']}
    assert all(current_by_id.get(b['id']) == b for b in failed), 'A historical failed batch was lost.'
    return dict(historical_failed_batches_preserved=len(failed),
                recovery_events_preserved=len(prior.get('recovery_events', [])))


def _worker_check(source, ledger, profile, output, deadline):
    """Run one exact completed source packet through the existing owned worker."""
    from . import study_encoder as encoder
    from .semantic_neighbors import prepare_packet
    unit = next(u for u in source['units'] if ledger['coverage'][u['id']]['status'] == 'completed')
    uid = unit['id']
    packet = prepare_packet([unit], sources=[s for s in source['sources'] if s['source_name'] == unit['source_name']],
        provenance=dict(parent_study_sha256=source['study_sha256'], parent_unit_ids=[uid]))
    remaining = deadline - time.monotonic() - 10
    if remaining <= 1:
        raise TimeoutError('Insufficient remaining QA time for the explicit worker verification.')
    outcome = encoder._run_chunk(packet, str(profile), ledger['profile_binding']['profile_sha256'],
        output / 'additional-worker-check', wall_seconds=min(remaining, 90), cancel_event=threading.Event())
    assert outcome['status'] == 'completed', 'Additional worker check did not complete: ' + str(outcome)
    assert outcome['resources']['owned_processes_closed'], 'Additional worker cleanup incomplete.'
    windows = encoder._accept_encoded([unit], outcome['encoded'], ledger['profile_binding'])
    assert windows[0] == ledger['coverage'][uid]['token_windows'], 'Worker token windows changed.'
    original_batch = next(b for b in ledger['batches'] if b['id'] == ledger['batch_ids'][uid])
    original_index = original_batch['unit_ids'].index(uid)
    original_tokens = original_batch['encoded']['provenance']['tokenization'][original_index]
    assert outcome['encoded']['provenance']['tokenization'][0] == original_tokens, 'Worker exact token IDs changed.'
    vector = outcome['encoded']['vectors'][0]
    maximum_error = max(abs(a-b) for a, b in zip(vector, ledger['vectors'][uid]))
    assert math.isfinite(maximum_error) and maximum_error <= 1e-6, 'Worker vector differs from saved vector.'
    return dict(status='passed', unit_id=uid, new_study_coverage=0,
        scope='additional verification of one already-completed source unit',
        exact_token_diagnostics=True, exact_token_windows=True,
        vector_maximum_absolute_error=maximum_error, vector_tolerance=1e-6,
        packaged_neighbor_worker=bool(getattr(sys, 'frozen', False)), resources=outcome['resources'])


def study_encoder_smoke(study, profile, ledger, output, *, deadline_seconds=180, worker_check=False):
    """Exercise actual StudyDialog neighbors on the full already-encoded queue."""
    if (type(deadline_seconds) not in (int, float) or not math.isfinite(deadline_seconds)
            or not 10 <= deadline_seconds <= 180 or type(worker_check) is not bool):
        raise ValueError('Use a 10..180 second deadline and an explicit boolean worker check.')
    paths = dict(zip(('study', 'profile', 'ledger'), map(_local, (study, profile, ledger))))
    output = _local(output)
    if output.exists():
        raise ValueError('Encoder QA output must be fresh.')
    originals = {}
    for name, path in paths.items():
        maximum = 65536 if name == 'profile' else 64 * 1024**2
        with path.open('rb') as stream:
            originals[name] = stream.read(maximum + 1)
        if len(originals[name]) > maximum:
            raise ValueError('Encoder QA input exceeds its explicit byte bound: ' + name)
    source = json.loads(originals['study'].decode('utf-8-sig'))
    prior = json.loads(originals['ledger'].decode('utf-8-sig'))
    from .study_batches import validate_study
    from .study_encoder import validate_encoder_ledger
    validate_study(source)
    validate_encoder_ledger(prior)
    if prior['study'] != source:
        raise ValueError('Encoder ledger must bind the exact complete study.')
    if prior['coverage_counts'].get('pending', 0):
        raise ValueError('Full-queue review requires zero pending encoder units; no new study inference is authorized.')
    output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    deadline = started + deadline_seconds
    report = dict(status='started', frozen_executable=bool(getattr(sys, 'frozen', False)),
        deadline_seconds=deadline_seconds, new_study_encoder_workers=0, new_LLM_calls=0,
        downloads=0, semantic_validation=False, optional_worker_check_requested=worker_check,
        inputs={name:dict(path=str(paths[name]), bytes=len(raw), sha256=sha256(raw).hexdigest())
                for name, raw in originals.items()})
    app = dialog = resources = None
    blocked_attempts = []
    code = 1

    def blocked(*args, **kwargs):
        blocked_attempts.append(True)
        raise RuntimeError('Process, model, network and browser startup prohibited during full-queue review.')

    def within_budget():
        if time.monotonic() >= deadline - 10:
            raise TimeoutError('Whole-study encoder QA deadline reached; cleanup reserve retained.')
        resources.sample()

    try:
        from . import study_dialog, study_encoder
        from .app import ThemeForgeApp
        from .study_neighbors import validate_study_neighbors
        resources = _Resources()
        resources.sample(force=True)
        with ExitStack() as guards:
            for owner, name in ((subprocess, 'Popen'), (socket, 'socket'),
                    (urllib.request.OpenerDirector, 'open'), (webbrowser, 'open'),
                    (webbrowser, 'open_new'), (webbrowser, 'open_new_tab'),
                    (study_dialog, 'StudyLlamaClient'), (study_encoder, '_run_chunk')):
                guards.enter_context(patch.object(owner, name, blocked))
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
            assert dialog.load(paths['study']), dialog.status.get()
            assert dialog.study == source and dialog.session is None
            assert list(dialog.tree.get_children()) == [u['id'] for u in source['units']]
            # These are the real UI file-selection paths. All computation stays
            # in the production encode_study/build_study_neighbors functions.
            dialog.minutes.set(2)
            with patch.object(study_dialog.filedialog, 'askopenfilename',
                              side_effect=[str(paths['profile']), str(paths['ledger'])]), \
                    patch.object(study_dialog.filedialog, 'askdirectory', return_value=str(output)):
                assert dialog._start_neighbors(), dialog.status.get()
            while dialog._worker is not None:
                app.update()
                within_budget()
                time.sleep(.01)
            app.update()
            within_budget()
            assert not blocked_attempts, 'A prohibited model/process/network action was attempted.'
            assert dialog.encoder_ledger is not None and dialog.neighbors is not None, dialog.status.get()
            current, graph = dialog.encoder_ledger, dialog.neighbors
            validate_encoder_ledger(current)
            validate_study_neighbors(graph)
            report.update(_check_preserved(prior, current))
            assert graph['units'] == source['units'] and graph['source_study'] == source
            assert graph['source_index'] == source['sources']
            assert graph['vectors'] == prior['vectors']
            assert graph['embedding_provenance'] == prior['embedding_provenance']
            assert graph['encoding_coverage'] == prior['coverage']
            assert set(graph['coverage']) == {u['id'] for u in source['units']}
            assert graph['comparisons']['pairs_scored'] == graph['comparisons']['expected_cross_source_pairs']
            assert graph['comparisons']['stop_reason'] is None
            assert list(dialog.tree.get_children()) == [u['id'] for u in source['units']]
            for unit in source['units']:
                assert dialog.tree.item(unit['id'], 'values')[0:2] == (unit['source_name'], unit['speaker'])
            selected_id = graph['edges'][0]['member_ids'][0] if graph['edges'] else next(iter(prior['vectors']))
            unit = next(u for u in source['units'] if u['id'] == selected_id)
            dialog.tree.selection_set(selected_id)
            dialog.show_selected()
            shown = json.loads(dialog.detail.get('1.0', 'end'))
            assert shown['original_source_turn'] == unit
            assert shown['encoding_status'] == current['coverage'][selected_id]
            assert shown['retrieval_status'] == graph['coverage'][selected_id]
            by_id = {u['id']:u for u in source['units']}
            from .source_context import pair_overview
            expected_pairs = [pair_overview(edge,by_id)
                              for edge in graph['edges'] if selected_id in edge['member_ids']]
            assert shown['candidate_source_pairs'] == expected_pairs
            work = list(output.glob('study-neighbors-*'))
            assert len(work) == 1, 'Expected one real UI work folder.'
            exported_json = work[0] / 'cross-batch-neighbors.json'
            exported_html = work[0] / 'cross-batch-neighbors.html'
            loaded = json.loads(exported_json.read_text(encoding='utf-8'))
            validate_study_neighbors(loaded)
            assert loaded == graph, 'Saved graph differs from loaded UI graph.'
            html = exported_html.read_text(encoding='utf-8')
            assert graph['result_sha256'] in html and 'Every unit coverage state' in html
            assert all(uid in html for uid in graph['coverage'])
            assert escape(graph['embedding_provenance']['model_sha256'], quote=True) in html
            checkpoints = [json.loads(p.read_text(encoding='utf-8'))
                           for p in (work[0] / 'encoding').glob('run-*/encoder-ledger-progress-*.json')]
            assert current in checkpoints, 'Final encoder ledger not persisted.'
            assert before == dict(documents=app.documents, result=app.result, audit_events=app.audit_events)
            report.update(actual_hidden_Tk=True, actual_StudyDialog_neighbors=True,
                full_UI_rows=len(source['units']), source_count=len(source['sources']),
                encoded_vectors=len(current['vectors']), encoding_coverage=current['coverage_counts'],
                retrieval_coverage=graph['coverage_counts'], candidate_pairs=len(graph['edges']),
                comparisons=graph['comparisons'], source_study_sha256=source['study_sha256'],
                graph_result_sha256=graph['result_sha256'], final_encoder_ledger_sha256=current['ledger_sha256'],
                selected_unit_id=selected_id, selected_source_and_evidence_exact=True,
                export_JSON_reopen_exact=True, export_HTML_verified=True,
                completed_vectors_and_provenance_unchanged=True, main_project_unchanged=True)
        if worker_check:
            within_budget()
            report['additional_worker_check'] = _worker_check(source, prior, paths['profile'], output, deadline)
        assert all(path.read_bytes() == originals[name] for name, path in paths.items()), 'An original input changed.'
        within_budget()
        report.update(status='passed', original_inputs_unchanged=True)
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
        report['blocked_startup_attempts'] = len(blocked_attempts)
        if resources is not None:
            resources.sample(force=True)
            report['resources'] = resources.report()
            remaining = []
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
        if report['owned_worker_remaining'] or report.get('observed_owned_descendants_remaining'):
            report.update(status='failed', cleanup_incomplete=True)
            code = 1
        if dialog is not None and dialog.winfo_exists():
            dialog.close()
        if app is not None:
            app.destroy()
        report.update(elapsed_seconds=time.monotonic() - started, app_closed=app is not None)
        report['outputs'] = {str(p.relative_to(output)):dict(bytes=p.stat().st_size,
            sha256=sha256(p.read_bytes()).hexdigest()) for p in output.rglob('*') if p.is_file()}
        with (output / 'QA.json').open('x', encoding='utf-8') as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
    return code


def main(argv=None):
    parser = argparse.ArgumentParser(description='Explicit full-queue hidden Tk encoder-ledger QA; no new study inference.')
    for name in ('study', 'profile', 'ledger', 'out'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--worker-check', action='store_true',
        help='Additionally run one already-completed unit through the owned neighbor worker; no new study coverage.')
    args = parser.parse_args(argv)
    return study_encoder_smoke(args.study, args.profile, args.ledger, args.out, worker_check=args.worker_check)


if __name__ == '__main__':
    raise SystemExit(main())

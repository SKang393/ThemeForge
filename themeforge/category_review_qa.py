"""Explicit offline, hidden UI integrity smoke for native synthesis outcomes."""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys
import time
import webbrowser
from unittest.mock import patch


def _local(value, label):
    path = Path(value)
    if not path.is_absolute() or str(path).startswith(('\\\\', '//')):
        raise ValueError(label + ' must be an absolute local path')
    return path


def category_review_smoke(output, draft):
    """Reopen/export only. Source integrity checks do not approve semantics."""
    output, draft = _local(output, 'Output'), _local(draft, 'Draft')
    if output.exists():
        raise ValueError('Category review output must be fresh')
    if draft.stat().st_size > 16 * 1024**2:
        raise ValueError('Draft exceeds 16 MiB')
    raw = draft.read_bytes()
    original = json.loads(raw.decode('utf-8-sig'))
    from .local_codebook import validate_result_sources
    from .model_pilot import _digest
    validate_result_sources(original)
    if original.get('synthesis_status') not in {'category_proposed_semantics_unreviewed', 'no_group'}:
        raise ValueError('Requires a completed category or no_group outcome')
    output.mkdir(parents=True, exist_ok=False)
    report = dict(status='started', input_path=str(draft), input_sha256=sha256(raw).hexdigest(),
                  frozen_executable=bool(getattr(sys, 'frozen', False)), executable=sys.executable,
                  model_calls=0, semantic_validation=False)
    started = time.monotonic()
    app = dialog = None
    attempts, modals = [], []

    def blocked(*args, **kwargs):
        attempts.append('process_or_browser')
        raise PermissionError('Process/browser launch prohibited during category review smoke')

    def no_modal(*args, **kwargs):
        modals.append('modal_suppressed')

    try:
        from contextlib import ExitStack
        from tkinter import messagebox
        with ExitStack() as guards:
            for owner, name, replacement in ((subprocess, 'Popen', blocked),
                    (webbrowser, 'open', blocked), (webbrowser, 'open_new', blocked),
                    (webbrowser, 'open_new_tab', blocked),
                    (messagebox, 'showerror', no_modal), (messagebox, 'showinfo', no_modal),
                    (messagebox, 'showwarning', no_modal)):
                guards.enter_context(patch.object(owner, name, replacement))
            from .app import ThemeForgeApp

            class HiddenApp(ThemeForgeApp):
                def _build_layout(self):
                    self.withdraw()
                    super()._build_layout()

            app = HiddenApp()
            app.update()
            dialog = app.open_local_codebook()
            dialog.withdraw()
            clients = []
            def no_client(*args, **kwargs):
                clients.append(True)
                raise RuntimeError('Client construction prohibited in category review')
            dialog._client_factory = no_client
            assert dialog.load_result(draft), dialog.status.get()
            app.update()
            assert app.state() == dialog.state() == 'withdrawn'
            assert dialog.result == original and not dialog.reviewed.get()
            assert not app.documents and not dialog.documents and dialog._thread is None
            assert len(dialog.code_tree.get_children()) == len(original['codes'])
            pane = dialog.categories.get('1.0', 'end-1c')
            assert pane.splitlines()[0] == original['synthesis_status']
            assert json.loads(pane.split('\n\n', 1)[1]) == original.get('categories', [])
            if original['synthesis_status'] == 'no_group':
                assert not original.get('categories') and original.get('synthesis_reason')
                assert original['synthesis_reason'] in pane
            codes = {code['id']: code for code in original['codes']}
            relation_checks = []
            for category in original.get('categories', []):
                for role, key in (('evidence', 'code_ids'), ('qualifications', 'qualification_code_ids'),
                                  ('counterexamples', 'counterexample_code_ids')):
                    expected = [span for cid in category[key] for span in codes[cid]['evidence']]
                    assert category[role] == expected
                    relation_checks.append(dict(category_id=category['id'], role=role,
                                                member_ids=category[key], exact_span_count=len(expected)))
            for format in ('json', 'html'):
                assert dialog.export(format, output / ('category-export.' + format)), dialog.status.get()
            exported = json.loads((output / 'category-export.json').read_text(encoding='utf-8'))
            assert exported == original
            assert dialog.load_result(output / 'category-export.json')
            assert dialog.result == original
            report['tampered_quote_check'] = 'not_applicable_no_group'
            if original.get('categories'):
                tampered = deepcopy(original)
                tampered['categories'][0]['evidence'][0]['text'] += ' [locally tampered QA quote]'
                tampered['result_sha256'] = _digest({k: v for k, v in tampered.items() if k != 'result_sha256'})
                path = output / 'tampered-category-quote.json'
                with path.open('x', encoding='utf-8') as stream:
                    json.dump(tampered, stream, ensure_ascii=False, indent=2)
                assert not dialog.load_result(path)
                assert dialog.result == original
                report['tampered_quote_check'] = 'rehashed_copy_rejected_valid_draft_preserved'
            assert draft.read_bytes() == raw and not clients and dialog._thread is None
            assert dialog.result == original and not dialog.reviewed.get()
            report.update(status='passed', actual_hidden_Tk=True, categories_UI_exact=True,
                          category_member_evidence_checks=relation_checks,
                          exported_JSON_exact=True, JSON_UI_reopen_exact=True,
                          original_unchanged=True, raw_calls_gate_AI_history_preserved=True,
                          client_construction_attempts=len(clients), new_revisions=0)
        code = 0
    except Exception as error:
        report.update(status='failed', error_type=type(error).__name__, error=str(error))
        code = 1
    finally:
        if app is not None:
            app.destroy()
        report.update(wall_seconds=time.monotonic() - started, app_closed=app is not None,
                      blocked_process_browser_attempts=len(attempts), suppressed_modals=len(modals))
        report['outputs'] = {p.name: dict(bytes=p.stat().st_size, sha256=sha256(p.read_bytes()).hexdigest())
                             for p in output.iterdir() if p.is_file()}
        with (output / 'category-review-smoke.json').open('x', encoding='utf-8') as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
    return code

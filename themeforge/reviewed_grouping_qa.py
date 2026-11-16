"""Explicit hidden UI authoring check with external, AI-attributed input only."""
from contextlib import ExitStack
from copy import deepcopy
from hashlib import sha256
from html import escape
import json
from pathlib import Path
import socket
import subprocess
import sys
import time
from unittest.mock import patch
import webbrowser

from .category_review_qa import _local


def _read(path, label, maximum):
    path = _local(path, label)
    if path.stat().st_size > maximum:
        raise ValueError(label + ' exceeds its bounded input size')
    raw = path.read_bytes()
    return path, raw, json.loads(raw.decode('utf-8-sig'))


def reviewed_grouping_smoke(output, draft, category_input):
    """Use the actual editor, never a model; preserve both original input files."""
    output = _local(output, 'Output')
    if output.exists():
        raise ValueError('Reviewed grouping output must be fresh')
    draft, raw, original = _read(draft, 'Draft', 16 * 1024**2)
    category_input, spec_raw, spec = _read(category_input, 'Category input', 64 * 1024)
    fields = {'code_ids', 'label', 'shared_meaning', 'member_notes', 'limits', 'reviewer_kind', 'reason'}
    if type(spec) is not dict or set(spec) != fields or spec.get('reviewer_kind') != 'assistant_development_review':
        raise ValueError('Category input must explicitly supply all fields with assistant_development_review authorship')
    from .local_codebook import validate_result_sources
    from .model_pilot import _digest
    validate_result_sources(original)
    output.mkdir(parents=True, exist_ok=False)
    report = dict(status='started', draft_path=str(draft), input_sha256=sha256(raw).hexdigest(),
                  category_input_path=str(category_input), category_input_sha256=sha256(spec_raw).hexdigest(),
                  frozen_executable=bool(getattr(sys, 'frozen', False)), executable=sys.executable,
                  model_calls=0, semantic_validation=False, new_human_authorship=False)
    app = dialog = None
    started = time.monotonic()
    blocked_actions, clients, configuration, modals = [], [], [], []

    def blocked(kind):
        def refuse(*args, **kwargs):
            blocked_actions.append(kind)
            raise PermissionError(kind + ' prohibited during reviewed category QA')
        return refuse

    def modal(*args, **kwargs):
        modals.append('suppressed')

    try:
        from tkinter import messagebox
        with ExitStack() as guards:
            for owner, name, value in ((subprocess, 'Popen', blocked('process')),
                    (webbrowser, 'open', blocked('browser')), (webbrowser, 'open_new', blocked('browser')),
                    (webbrowser, 'open_new_tab', blocked('browser')),
                    (socket.socket, 'connect', blocked('network')), (socket.socket, 'connect_ex', blocked('network')),
                    (socket, 'create_connection', blocked('network')),
                    (messagebox, 'showerror', modal), (messagebox, 'showinfo', modal), (messagebox, 'showwarning', modal)):
                guards.enter_context(patch.object(owner, name, value))
            from .app import ThemeForgeApp

            class HiddenApp(ThemeForgeApp):
                def _build_layout(self):
                    self.withdraw()
                    super()._build_layout()

            app = HiddenApp()
            app.update()
            dialog = app.open_local_codebook()
            dialog.withdraw()

            def no_client(*args, **kwargs):
                clients.append('blocked')
                raise RuntimeError('No inference client allowed')

            def no_configuration(*args, **kwargs):
                configuration.append('blocked')
                raise RuntimeError('No model profile/configuration needed')

            dialog._client_factory = no_client
            dialog._configuration = no_configuration
            assert dialog.load_result(draft), dialog.status.get()
            app.update()
            assert dialog.result == original and app.state() == dialog.state() == 'withdrawn'
            assert not app.documents and not dialog.documents and dialog._thread is None
            original_model_pane = dialog.categories.get('1.0', 'end-1c')

            def editor():
                dialog.code_tree.selection_set(*spec['code_ids'])
                dialog._selection_changed()
                app.update()  # Drain selection events before setting the explicit QA gate.
                dialog.reviewed.set(True)
                dialog._update_actions()
                window = dialog.open_reviewed_category(reviewer_kind=spec['reviewer_kind'])
                assert window is not None, dialog.status.get()
                window.withdraw()
                app.update()
                assert window.state() == 'withdrawn'
                assert 'assistant_development_review' in window.author_notice
                window.label_entry.insert(0, spec['label'])
                for widget, value in ((window.shared_text, spec['shared_meaning']),
                                      (window.limits_text, spec['limits']), (window.reason_text, spec['reason'])):
                    widget.insert('1.0', value)
                assert set(window.member_notes) == set(spec['code_ids'])
                for cid, widget in window.member_notes.items():
                    widget.insert('1.0', spec['member_notes'][cid])
                return window

            cancelled = editor()
            cancelled.cancel_button.invoke()
            app.update()
            assert not cancelled.winfo_exists() and dialog.result == original
            report['filled_editor_cancel_unchanged'] = True
            window = editor()
            checked = []
            units = {unit['id']: unit for unit in original['unit_archive']}
            for cid, preview in window.source_previews.items():
                text = preview.get('1.0', 'end-1c')
                code = dialog._codes[cid]
                assert code['claim'] in text and str(code.get('field_origins', {})) in text
                assert str(code.get('evidence_role_origins', {})) in text
                unit = units[code['unit_id']]
                assert unit['text'] in text and unit['source_hash'] in text
                assert f"{unit['source_start']}:{unit['source_end']}" in text
                if unit['context_text']:
                    assert unit['context_text'] in text
                for context in code.get('referent_context', []):
                    assert context['text'] in text and context['source_hash'] in text
                    assert f"{context['source_start']}:{context['source_end']}" in text
                for role in ('evidence', 'qualifications', 'counterexamples'):
                    for span in code[role]:
                        assert span['text'] in text and span['source_hash'] in text
                        assert f"{span['source_start']}:{span['source_end']}" in text
                checked.append(dict(code_id=cid, exact_support_count=len(code['evidence']),
                                    exact_qualification_count=len(code['qualifications']),
                                    exact_counterexample_count=len(code['counterexamples']), current_origins_previewed=True))
                checked[-1].update(full_target_source_exact=True, surrounding_context_exact=True,
                                   separate_referent_context_count=len(code.get('referent_context', [])))
            window.apply_button.invoke()
            app.update()
            assert not window.winfo_exists(), window.error_text.get()
            revised = deepcopy(dialog.result)
            assert len(revised['reviewed_categories']) == len(original.get('reviewed_categories', [])) + 1
            assert not dialog.reviewed.get()
            for key, value in original.items():
                if key not in {'result_sha256', 'reviewed_categories', 'reviewed_category_history', 'reviewed_category_base_sha256'}:
                    assert revised[key] == value, key
            assert revised['reviewed_categories'][:-1] == original.get('reviewed_categories', [])
            assert revised['reviewed_category_history'][:-1] == original.get('reviewed_category_history', [])
            category, event = revised['reviewed_categories'][-1], revised['reviewed_category_history'][-1]
            assert category['origin'] == event['reviewer_kind'] == 'assistant_development_review'
            assert event['before_result_sha256'] == original['result_sha256']
            assert dialog.categories.get('1.0', 'end-1c') == original_model_pane
            reviewed_pane = dialog.review_categories.get('1.0', 'end-1c')
            assert category['label'] in reviewed_pane and 'assistant_development_review' in reviewed_pane
            for member in category['members']:
                code = dialog._codes[member['code_id']]
                assert member['referent_context'] == code.get('referent_context', [])
                if 'referent_context_origin' in code:
                    assert member['referent_context_origin'] == code['referent_context_origin']
                for context in member['referent_context']:
                    assert context['text'] in reviewed_pane and context['source_hash'] in reviewed_pane
            validate_result_sources(revised)
            for format in ('json', 'html'):
                assert dialog.export(format, output / ('reviewed-category.' + format)), dialog.status.get()
            html = (output / 'reviewed-category.html').read_text(encoding='utf-8')
            assert escape(category['label']) in html and category['category_sha256'] in html
            for member in category['members']:
                assert escape(member['member_note']) in html
                for row in member['evidence'] + member['qualifications'] + member['counterexamples'] + member['referent_context']:
                    assert escape(row['text']) in html and row['source_hash'] in html
            assert json.loads((output / 'reviewed-category.json').read_text(encoding='utf-8')) == revised
            assert dialog.load_result(output / 'reviewed-category.json')
            assert dialog.result == revised
            tampered = deepcopy(revised)
            tampered['reviewed_categories'][-1]['members'][0]['evidence'][0]['text'] += ' [locally altered QA quote]'
            tampered['result_sha256'] = _digest({k: v for k, v in tampered.items() if k != 'result_sha256'})
            with (output / 'tampered-reviewed-quote.json').open('x', encoding='utf-8') as stream:
                json.dump(tampered, stream, ensure_ascii=False, indent=2)
            assert not dialog.load_result(output / 'tampered-reviewed-quote.json')
            assert dialog.result == revised
            assert draft.read_bytes() == raw and category_input.read_bytes() == spec_raw
            assert not clients and not configuration and not blocked_actions and dialog._thread is None
            report.update(status='passed', actual_hidden_Tk_editor_apply=True,
                          member_source_preview_checks=checked, original_model_codes_calls_history_unchanged=True,
                          original_model_pane_unchanged=True, new_category_origin='assistant_development_review',
                          new_category_count=1, new_result_sha256=revised['result_sha256'],
                          new_category_id=category['id'], JSON_export_and_UI_reopen_exact=True,
                          HTML_authorship_notes_evidence_and_context_present=True,
                          tampered_quote_rejected_valid_result_preserved=True, input_files_unchanged=True)
        code = 0
    except Exception as error:
        report.update(status='failed', error_type=type(error).__name__, error=str(error))
        code = 1
    finally:
        if app is not None:
            app.destroy()
        report.update(wall_seconds=time.monotonic() - started, app_closed=app is not None,
                      client_construction_attempts=len(clients), model_configuration_attempts=len(configuration),
                      prohibited_action_attempts=blocked_actions, suppressed_modals=len(modals))
        report['outputs'] = {p.name: dict(bytes=p.stat().st_size, sha256=sha256(p.read_bytes()).hexdigest())
                             for p in output.iterdir() if p.is_file()}
        with (output / 'reviewed-grouping-smoke.json').open('x', encoding='utf-8') as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
    return code

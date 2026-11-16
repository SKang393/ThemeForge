"""Hidden Tk QA over an existing candidate packet, without inference or network."""
from __future__ import annotations

import argparse
from contextlib import ExitStack
from copy import deepcopy
import csv
from hashlib import sha256
from html.parser import HTMLParser
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import traceback
import urllib.request
import webbrowser
from unittest.mock import patch


def _local(value):
    raw = str(value)
    path = Path(value)
    if not path.is_absolute() or raw.startswith(('\\\\', '//')) or '://' in raw:
        raise ValueError('Use an explicit absolute local path.')
    path = path.resolve()
    if str(path).startswith(('\\\\', '//')):
        raise ValueError('Network-share paths are prohibited.')
    return path


class _HTMLText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, value):
        self.parts.append(value)


def qa_packet(packet_path, output_directory, *, revisions_path, deadline_seconds=120):
    """Load actual saved data, exercise widgets, and export separate AI QA copies."""
    if type(deadline_seconds) not in (int, float) or not 1 <= deadline_seconds <= 300:
        raise ValueError('Choose a bounded 1..300-second QA period.')
    source = _local(packet_path)
    revision_source = _local(revisions_path)
    revision_raw = revision_source.read_bytes()
    edits = json.loads(revision_raw.decode('utf-8-sig'))['edits']
    if not edits or any(edit.get('reviewer_kind') != 'AI' for edit in edits):
        raise ValueError('Supply explicit AI-only source-review revisions.')
    output = _local(output_directory)
    if output.exists():
        raise ValueError('Choose a fresh output directory; existing evidence is preserved.')
    raw = source.read_bytes()
    if len(raw) > 32 * 1024 * 1024:
        raise ValueError('Packet exceeds the 32 MiB review import limit.')
    output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    app = dialog = reopened = None
    attempts = []
    report = {'status': 'started', 'checks': [], 'actual_hidden_Tk': False,
              'source_checkout': not bool(getattr(sys, 'frozen', False)),
              'runtime_kind': 'frozen_executable' if getattr(sys, 'frozen', False) else 'source_checkout',
              'packaged_executable_tested': bool(getattr(sys, 'frozen', False)),
              'visual_layout_verified': False, 'human_usability_verified': False,
              'semantic_accuracy_validated': False, 'new_model_calls': 0,
              'human_authored_changes': 0, 'training_eligible': False,
              'input': {'path': str(source), 'bytes': len(raw), 'sha256': sha256(raw).hexdigest()},
              'revisions_input': {'path': str(revision_source), 'sha256': sha256(revision_raw).hexdigest()},
              'limits': ['Hidden widget content and export checks only.',
                         'AI QA edits are separate copies, not human adjudication or training targets.',
                         'App entry point and hidden widget content are exercised; visual layout and human usability are unverified.',
                         'A frozen runtime check, when used, covers this review flow only; semantic quality and teaching outcomes are unverified.']}

    def blocked(*args, **kwargs):
        attempts.append({'argument_types': [type(a).__name__ for a in args]})
        raise RuntimeError('Process, model, browser, and network actions are prohibited during candidate QA.')

    def check(name, condition):
        if time.monotonic() - started >= deadline_seconds:
            raise TimeoutError('Candidate review QA deadline exceeded.')
        if not condition:
            raise AssertionError(name)
        report['checks'].append(name)

    try:
        with ExitStack() as guards:
            owners = [(subprocess, 'Popen'), (os, 'system'), (socket, 'socket'),
                      (socket, 'create_connection'), (urllib.request.OpenerDirector, 'open'),
                      (webbrowser, 'open'), (webbrowser, 'open_new'), (webbrowser, 'open_new_tab')]
            if hasattr(os, 'startfile'):
                owners.append((os, 'startfile'))
            for owner, name in owners:
                guards.enter_context(patch.object(owner, name, blocked))
            from themeforge import candidate_packet
            from themeforge.app import ThemeForgeApp
            from themeforge.candidate_review_dialog import CandidateReviewDialog

            value = json.loads(raw.decode('utf-8-sig'))
            candidate_packet.validate_packet(value)
            class HiddenApp(ThemeForgeApp):
                def _build_layout(self):
                    self.withdraw()
                    super()._build_layout()

            app = HiddenApp()
            app.update()
            app_before = deepcopy({key: getattr(app, key) for key in
                                   ('documents', 'result', 'audit_events', 'input_paths', 'project_path')})

            def widgets(parent):
                for widget in parent.winfo_children():
                    yield widget
                    yield from widgets(widget)

            buttons = [w for w in widgets(app) if w.winfo_class() == 'TButton' and
                       str(w.cget('text')) == 'Review candidate dataset']
            check('actual_candidate_review_button_exists', len(buttons) == 1)
            check('actual_button_bound_to_open_candidate_dataset',
                  str(buttons[0].cget('command')).endswith('open_candidate_dataset'))
            buttons[0].invoke()
            dialogs = [w for w in app.winfo_children() if isinstance(w, CandidateReviewDialog)]
            check('actual_button_opens_candidate_dialog', len(dialogs) == 1)
            dialog = dialogs[0]
            check('load_actual_packet', dialog.load(source))
            app.update()
            check('actual_hidden_Tk', not app.winfo_viewable() and not dialog.winfo_viewable())
            report['actual_hidden_Tk'] = True
            check('load_preserves_packet_exactly', dialog.packet == value)
            check('all_records_selectable', list(dialog.record_tree.get_children()) ==
                  [r['record_id'] for r in value['records']])
            inspected = []
            for record in value['records']:
                dialog.select_record(record['record_id'])
                app.update()
                source_display = dialog.source_text.get('1.0', 'end-1c')
                check(record['record_id'] + ':source_and_hash_visible',
                      record['source_text'] in source_display and record['source_sha256'] in source_display)
                check(record['record_id'] + ':source_readonly', str(dialog.source_text.cget('state')) == 'disabled')
                review_display = dialog.review_text.get('1.0', 'end-1c')
                check(record['record_id'] + ':review_and_unresolved_visible',
                      'independent_review' in review_display and 'unresolved_decisions' in review_display)
                check(record['record_id'] + ':all_codes_selectable',
                      list(dialog.code_tree.get_children()) == [c['code_id'] for c in record['proposals']])
                for proposal in record['proposals']:
                    dialog.select_code(proposal['code_id'])
                    app.update()
                    detail = dialog.details_text.get('1.0', 'end-1c')
                    check(proposal['code_id'] + ':details_visible',
                          proposal['label'] in dialog.edit_fields['label'].get('1.0', 'end-1c') and
                          'exact_source_anchors_by_role' in detail and 'alternatives' in detail)
                    anchors = {a['anchor_id']: a for a in record['anchors']}
                    for role in ('evidence_ids', 'qualification_ids', 'counterexample_ids'):
                        for aid in proposal.get(role, []):
                            check(proposal['code_id'] + ':' + role + ':' + aid + ':exact_anchor',
                                  record['source_text'][anchors[aid]['start']:anchors[aid]['end']] ==
                                  anchors[aid]['text'])
                    inspected.append({'record_id': record['record_id'], 'code_id': proposal['code_id']})
            report['inspected'] = inspected
            before = deepcopy(dialog.packet)
            report['applied_AI_edits'] = []
            for edit in edits:
                dialog.select_record(edit['record_id'])
                dialog.select_code(edit['code_id'])
                dialog.notebook.select(dialog.source_text.master)
                app.update()
                record = dialog._record()
                proposal = dialog._code()
                check(edit['code_id'] + ':source_first_review',
                      record['source_text'] in dialog.source_text.get('1.0', 'end-1c'))
                current = deepcopy(dialog.packet)
                dialog.notebook.select(dialog.edit_fields['definition'].master)
                for field, change in edit['changes'].items():
                    if field in dialog.edit_fields:
                        dialog._replace(dialog.edit_fields[field], change, readonly=False)
                    else:
                        for aid in change:
                            if aid not in dialog._role_draft[field]:
                                index = dialog._anchor_rows.index(aid)
                                dialog.anchor_list.selection_clear(0, 'end')
                                dialog.anchor_list.selection_set(index)
                                dialog._preview_anchor()
                                anchor = next(a for a in record['anchors'] if a['anchor_id'] == aid)
                                check(edit['code_id'] + ':' + aid + ':anchor_preview_exact',
                                      anchor['text'] in dialog.anchor_preview.get('1.0', 'end-1c'))
                                dialog.role_destination.set(field)
                                dialog._stage_anchor_move()
                        check(edit['code_id'] + ':' + field + ':role_staged', dialog._role_draft[field] == change)
                dialog.reason_text.insert('1.0', edit['reason'])
                app.update()
                check(edit['code_id'] + ':staged_widgets_do_not_mutate_packet', dialog.packet == current)
                dialog.discard_edits()
                check(edit['code_id'] + ':discard_restores_without_human_apply',
                      dialog.packet == current and all(
                          widget.get('1.0', 'end-1c') == proposal[field]
                          for field, widget in dialog.edit_fields.items()) and all(
                          dialog._role_draft[field] == proposal[field] for field in dialog._role_draft))
                dialog.revise(edit['code_id'], edit['changes'], edit['reason'],
                              reviewer_kind='AI', reviewer_id=edit['reviewer_id'])
                app.update()
                check(edit['code_id'] + ':explicit_AI_revision_visible', all(
                    dialog._code()[field] == change for field, change in edit['changes'].items()))
                report['applied_AI_edits'].append(deepcopy(edit))
            candidate_packet.validate_packet(dialog.packet)
            check('AI_revisions_append_history', len(dialog.packet['review_history']) == len(before['review_history']) + len(edits))
            check('AI_revision_does_not_create_human_truth', dialog.packet['training_eligible'] is False and
                  dialog.packet.get('human_adjudications', []) == before.get('human_adjudications', []))
            immutable = ('source_text', 'source_sha256', 'actors', 'segments', 'anchors', 'original_proposals')
            check('sources_anchors_and_original_proposals_preserved', all(
                all(old.get(key) == new.get(key) for key in immutable)
                for old, new in zip(before['records'], dialog.packet['records'])))
            json_path = dialog.export('json', output / 'AI_QA_REVISED_PACKET.json')
            html_path = dialog.export('html', output / 'AI_QA_REVISED_PACKET.html')
            csv_path = dialog.export('csv', output / 'AI_QA_REVISED_PACKET.csv')
            reopened = app.open_candidate_dataset()
            check('actual_app_method_opens_reopen_dialog', isinstance(reopened, CandidateReviewDialog))
            check('JSON_export_reopens', reopened.load(json_path))
            candidate_packet.validate_packet(reopened.packet)
            check('JSON_reopen_preserves_revised_packet', reopened.packet == dialog.packet)
            parsed_html = _HTMLText()
            parsed_html.feed(html_path.read_text(encoding='utf-8'))
            html_content = ''.join(parsed_html.parts)
            changed_strings = [v for edit in edits for v in edit['changes'].values() if isinstance(v, str)]
            check('HTML_export_reopens_with_revised_text', all(v in html_content for v in changed_strings))
            rows = list(csv.DictReader(io.StringIO(csv_path.read_text(encoding='utf-8-sig'))))
            check('CSV_export_reopens_with_revised_text', all(any(v in row.values() for row in rows) for v in changed_strings))
            check('CSV_contains_all_proposals', len(rows) == sum(len(r['proposals']) for r in value['records']))
            existing_target = output / 'EXISTING_TARGET_PRESERVED.txt'
            sentinel = b'Existing export destination; these bytes must be preserved.\n'
            existing_target.write_bytes(sentinel)
            for kind in ('json', 'html', 'csv'):
                try:
                    dialog.export(kind, existing_target)
                except ValueError as exc:
                    check(kind + ':existing_target_actionable_error', 'fresh filename' in str(exc))
                else:
                    raise AssertionError(kind + ': existing export target must be refused.')
                check(kind + ':existing_target_bytes_preserved', existing_target.read_bytes() == sentinel)
            # The reopened exported packet is a disposable loaded input. Its alias
            # reproduces the original hardlink flaw without linking the frozen original.
            hardlink = output / 'DISPOSABLE_LOADED_PACKET_HARDLINK.json'
            disposable_raw = json_path.read_bytes()
            try:
                os.link(json_path, hardlink)
            except OSError as exc:
                report['hardlink_check'] = {'status': 'unsupported', 'reason': str(exc)}
            else:
                check('hardlink_is_alias_of_disposable_loaded_input', os.path.samefile(json_path, hardlink))
                try:
                    reopened.export('json', hardlink)
                except ValueError as exc:
                    check('hardlink_actionable_fresh_filename_error', 'fresh filename' in str(exc))
                else:
                    raise AssertionError('Hardlink to the loaded input must be refused.')
                check('hardlink_and_disposable_loaded_input_bytes_preserved',
                      hardlink.read_bytes() == disposable_raw and json_path.read_bytes() == disposable_raw)
                report['hardlink_check'] = {'status': 'PASS', 'source': str(json_path),
                                           'alias': str(hardlink), 'sha256': sha256(disposable_raw).hexdigest()}
            try:
                dialog.export('json', source)
            except ValueError:
                pass
            else:
                raise AssertionError('Loaded input overwrite must be rejected.')
            check('original_input_bytes_preserved', source.read_bytes() == raw)
            check('app_documents_results_and_audit_unchanged', app_before == {
                key: getattr(app, key) for key in app_before})
            check('no_prohibited_actions_attempted', not attempts)
            report['exports'] = {p.name: {'sha256': sha256(p.read_bytes()).hexdigest(), 'bytes': p.stat().st_size}
                                 for p in (json_path, html_path, csv_path)}
            report['status'] = 'PASS'
    except Exception as exc:
        report['status'] = 'FAILED'
        report['error'] = type(exc).__name__ + ': ' + str(exc)
        report['traceback'] = traceback.format_exc()
    finally:
        for window in (reopened, dialog, app):
            if window is not None:
                try:
                    window.destroy()
                except Exception:
                    pass
        report['prohibited_action_attempts'] = attempts
        report['elapsed_seconds'] = round(time.monotonic() - started, 3)
        report['original_input_bytes_preserved'] = source.read_bytes() == raw
        if not report['original_input_bytes_preserved']:
            report['status'] = 'FAILED'
        (output / 'QA_CANDIDATE_REVIEW.json').write_text(
            json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    return report


def main(argv=None):
    """Shared source/portable CLI; return an exit status after writing evidence."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('packet', help='Absolute local path to an existing packet JSON')
    parser.add_argument('output', help='Absolute local path for a fresh QA directory')
    parser.add_argument('--revisions', required=True,
                        help='Absolute local path to explicit AI-only source-review edits JSON')
    parser.add_argument('--deadline-seconds', type=float, default=120)
    args = parser.parse_args(argv)
    result = qa_packet(args.packet, args.output, revisions_path=args.revisions,
                       deadline_seconds=args.deadline_seconds)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['status'] == 'PASS' else 1

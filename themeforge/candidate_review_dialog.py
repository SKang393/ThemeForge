"""Local review of frozen fictional candidate packets; never starts inference."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, ttk

from . import candidate_packet


def _local_path(value):
    raw = str(value)
    path = Path(value)
    if not path.is_absolute() or raw.startswith(('\\\\', '//')) or '://' in raw:
        raise ValueError('Choose an absolute local file path.')
    path = path.resolve()
    if str(path).startswith(('\\\\', '//')):
        raise ValueError('Network-share paths are not supported.')
    return path


class CandidateReviewDialog(tk.Toplevel):
    """Keep source text immutable and save provisional revisions with authorship."""

    def __init__(self, parent, packet=None):
        super().__init__(parent)
        self.title('Education candidate packet review')
        self.geometry('1080x820')
        self.minsize(800, 620)
        self.packet = None
        self.loaded_path = None
        self._record_id = None
        self._code_id = None
        self._role_draft = None
        self._anchor_rows = []
        self.status = tk.StringVar(self, 'Open a candidate packet. No inference is available here.')
        self.reviewer_id = tk.StringVar(self, '')
        self.role_destination = tk.StringVar(self, 'qualification_ids')
        self._build()
        if parent.state() == 'withdrawn':
            self.withdraw()
        if packet is not None:
            self.set_packet(packet)

    def _build(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)
        ttk.Label(self, text='AI-authored fictional candidates | provisional review | training ineligible',
                  wraplength=1000).grid(row=0, column=0, sticky='w', padx=10, pady=7)
        toolbar = ttk.Frame(self)
        toolbar.grid(row=1, column=0, sticky='ew', padx=10, pady=4)
        ttk.Button(toolbar, text='Open JSON', command=self._open).pack(side='left')
        for kind in ('json', 'html', 'csv'):
            ttk.Button(toolbar, text='Export ' + kind.upper(),
                       command=lambda kind=kind: self._save(kind)).pack(side='left', padx=4)
        panes = ttk.Panedwindow(self, orient='horizontal')
        panes.grid(row=2, column=0, sticky='nsew', padx=10, pady=5)
        left = ttk.Frame(panes)
        left.columnconfigure(0, weight=1)
        left.rowconfigure(1, weight=1)
        left.rowconfigure(3, weight=1)
        panes.add(left, weight=1)
        ttk.Label(left, text='Records and linked families').grid(row=0, column=0, sticky='w')
        self.record_tree = ttk.Treeview(left, columns=('family',), show='tree headings', height=8,
                                       selectmode='browse')
        self.record_tree.heading('#0', text='Record')
        self.record_tree.heading('family', text='Family')
        self.record_tree.column('#0', width=115)
        self.record_tree.column('family', width=115)
        self.record_tree.grid(row=1, column=0, sticky='nsew', pady=(3, 8))
        self.record_tree.bind('<<TreeviewSelect>>', self._record_selected)
        ttk.Label(left, text='Provisional code drafts').grid(row=2, column=0, sticky='w')
        self.code_tree = ttk.Treeview(left, show='tree', height=10, selectmode='browse')
        self.code_tree.column('#0', width=240)
        self.code_tree.grid(row=3, column=0, sticky='nsew', pady=3)
        self.code_tree.bind('<<TreeviewSelect>>', self._code_selected)
        self.notebook = ttk.Notebook(panes)
        panes.add(self.notebook, weight=3)
        self.source_text = self._text_tab('Source and actors')
        self.details_text = self._text_tab('Draft and evidence')
        self.review_text = self._text_tab('Alternatives and unresolved')
        self.history_text = self._text_tab('Revision history')
        edit = ttk.Frame(self.notebook)
        edit.columnconfigure(1, weight=1)
        self.notebook.add(edit, text='Provisional edit')
        self.edit_fields = {}
        for row, field in enumerate(('label', 'claim', 'definition')):
            ttk.Label(edit, text=field.capitalize()).grid(row=row, column=0, sticky='nw', padx=6, pady=4)
            widget = tk.Text(edit, height=2 if field == 'label' else 3, wrap='word')
            widget.grid(row=row, column=1, sticky='ew', padx=6, pady=4)
            self.edit_fields[field] = widget
        ttk.Label(edit, text='Existing evidence anchors (moves preserve exact source spans)').grid(
            row=3, column=0, columnspan=2, sticky='w', padx=6, pady=(8, 2))
        self.anchor_list = tk.Listbox(edit, height=4, exportselection=False)
        self.anchor_list.grid(row=4, column=0, columnspan=2, sticky='ew', padx=6)
        self.anchor_list.bind('<<ListboxSelect>>', self._preview_anchor)
        roles = ttk.Frame(edit)
        roles.grid(row=5, column=0, columnspan=2, sticky='w', padx=6, pady=4)
        ttk.Combobox(roles, textvariable=self.role_destination, state='readonly', width=18,
                     values=('evidence_ids', 'qualification_ids', 'counterexample_ids', 'unassigned')).pack(side='left')
        ttk.Button(roles, text='Stage selected anchor role', command=self._stage_anchor_move).pack(side='left', padx=6)
        self.anchor_preview = tk.Text(edit, height=3, wrap='word', state='disabled')
        self.anchor_preview.grid(row=6, column=0, columnspan=2, sticky='ew', padx=6, pady=3)
        ttk.Label(edit, text='Revision rationale').grid(row=7, column=0, sticky='nw', padx=6, pady=5)
        self.reason_text = tk.Text(edit, height=3, wrap='word')
        self.reason_text.grid(row=7, column=1, sticky='ew', padx=6, pady=5)
        ttk.Label(edit, text='Researcher ID').grid(row=8, column=0, sticky='w', padx=6)
        ttk.Entry(edit, textvariable=self.reviewer_id).grid(row=8, column=1, sticky='ew', padx=6)
        ttk.Label(edit, text='Apply records self-declared researcher authorship. It does not establish human truth, '
                  'adjudication, agreement, or training eligibility. Edits remain in memory until export.',
                  wraplength=650).grid(row=9, column=0, columnspan=2, sticky='w', padx=6, pady=8)
        actions = ttk.Frame(edit)
        actions.grid(row=10, column=0, columnspan=2, sticky='e', padx=6, pady=5)
        self.apply_button = ttk.Button(actions, text='Apply provisional researcher revision',
                                       command=self._apply_researcher_revision)
        self.apply_button.pack(side='left', padx=4)
        ttk.Button(actions, text='Discard staged edits', command=self.discard_edits).pack(side='left')
        ttk.Label(self, textvariable=self.status, wraplength=1020).grid(
            row=3, column=0, sticky='w', padx=10, pady=8)

    def _text_tab(self, title):
        frame = ttk.Frame(self.notebook)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        self.notebook.add(frame, text=title)
        widget = tk.Text(frame, wrap='word', state='disabled')
        widget.grid(row=0, column=0, sticky='nsew')
        scroll = ttk.Scrollbar(frame, orient='vertical', command=widget.yview)
        scroll.grid(row=0, column=1, sticky='ns')
        widget.configure(yscrollcommand=scroll.set)
        return widget

    @staticmethod
    def _replace(widget, value, *, readonly=True):
        widget.configure(state='normal')
        widget.delete('1.0', 'end')
        widget.insert('1.0', value)
        if readonly:
            widget.configure(state='disabled')

    def _record(self):
        return next((r for r in (self.packet or {}).get('records', [])
                     if r['record_id'] == self._record_id), None)

    def _code(self):
        return next((c for c in (self._record() or {}).get('proposals', [])
                     if c['code_id'] == self._code_id), None)

    def set_packet(self, packet):
        candidate_packet.validate_packet(packet)
        self.packet = deepcopy(packet)
        self.record_tree.delete(*self.record_tree.get_children())
        for record in self.packet['records']:
            self.record_tree.insert('', 'end', iid=record['record_id'], text=record['record_id'],
                                    values=(record['family_id'],))
        self._record_id = None
        self._code_id = None
        if self.packet['records']:
            self.select_record(self.packet['records'][0]['record_id'])
        self._replace(self.history_text, json.dumps(self.packet.get('review_history', []),
                                                   ensure_ascii=False, indent=2))
        self.status.set('Packet loaded. Source text is immutable; revisions remain provisional.')

    def load(self, path):
        try:
            path = _local_path(path)
            with path.open('rb') as stream:
                raw = stream.read(32 * 1024 * 1024 + 1)
            if len(raw) > 32 * 1024 * 1024:
                raise ValueError('Packet exceeds the 32 MiB review import limit.')
            self.set_packet(json.loads(raw.decode('utf-8-sig')))
            self.loaded_path = path
            return True
        except (OSError, ValueError, TypeError, KeyError) as exc:
            self.status.set('Open failed: ' + str(exc))
            return False

    def select_record(self, record_id):
        if not self.record_tree.exists(record_id):
            raise ValueError('Unknown record ID.')
        self.record_tree.selection_set(record_id)
        self.record_tree.see(record_id)
        self._record_selected()

    def _record_selected(self, _event=None):
        selected = self.record_tree.selection()
        if not selected or selected[0] == self._record_id:
            return
        self._record_id = selected[0]
        self._code_id = None
        record = self._record()
        self._replace(self.source_text, record['source_text'] + '\n\nACTORS\n' +
                      json.dumps(record.get('actors', []), ensure_ascii=False, indent=2) +
                      '\n\nSOURCE SHA-256\n' + record['source_sha256'])
        self.code_tree.delete(*self.code_tree.get_children())
        for code in record.get('proposals', []):
            self.code_tree.insert('', 'end', iid=code['code_id'],
                                  text=code['code_id'] + ': ' + code['label'])
        self._refresh_review()
        if record.get('proposals'):
            self.select_code(record['proposals'][0]['code_id'])
        else:
            self._replace(self.details_text, 'No proposals in this record.')
            self.discard_edits()

    def select_code(self, code_id):
        if not self.code_tree.exists(code_id):
            raise ValueError('Unknown code ID in selected record.')
        self.code_tree.selection_set(code_id)
        self.code_tree.see(code_id)
        self._code_selected()

    def _code_selected(self, _event=None):
        selected = self.code_tree.selection()
        if not selected or selected[0] == self._code_id:
            return
        self._code_id = selected[0]
        self._refresh_code()

    def _refresh_review(self):
        record = self._record() or {}
        value = {key: record.get(key) for key in (
            'record_id', 'family_id', 'ancestry', 'alternatives', 'independent_review',
            'unresolved_decisions', 'construction_provenance')}
        self._replace(self.review_text, 'Record-level alternatives, review, and unresolved decisions\n\n' +
                      json.dumps(value, ensure_ascii=False, indent=2))

    def _refresh_code(self):
        code = self._code()
        anchors = {a['anchor_id']: a for a in self._record().get('anchors', [])}
        expanded = {role: [anchors.get(aid, {'missing_anchor_id': aid}) for aid in code.get(role, [])]
                    for role in ('evidence_ids', 'qualification_ids', 'counterexample_ids')}
        detail = {'current_provisional_code': code, 'exact_source_anchors_by_role': expanded,
                  'definition_scope_binding': candidate_packet.scope_binding_status(code),
                  'definition_acceptance': 'Unaccepted provisional wording; unchanged scope inputs do not establish semantic acceptance.',
                  'original_proposal': next((c for c in self._record().get('original_proposals', [])
                                             if c['code_id'] == code['code_id']), None)}
        self._replace(self.details_text, json.dumps(detail, ensure_ascii=False, indent=2))
        self.discard_edits()
        self._refresh_review()

    def discard_edits(self):
        code = self._code() or {}
        for field, widget in self.edit_fields.items():
            self._replace(widget, str(code.get(field, '')), readonly=False)
        self._replace(self.reason_text, '', readonly=False)
        self._role_draft = {role: deepcopy(code.get(role, []))
                            for role in ('evidence_ids', 'qualification_ids', 'counterexample_ids')}
        self._refresh_anchors()

    def _refresh_anchors(self):
        self.anchor_list.delete(0, 'end')
        self._anchor_rows = []
        self._replace(self.anchor_preview, 'Select an anchor to inspect its exact text and actor before staging a role.')
        anchors = {a['anchor_id']: a for a in (self._record() or {}).get('anchors', [])}
        for aid, span in anchors.items():
            role = next((role for role, ids in (self._role_draft or {}).items() if aid in ids), 'unassigned')
            self._anchor_rows.append(aid)
            self.anchor_list.insert('end', f"{role}: {aid} / {span.get('speaker_actor_id')} " +
                                    f"[{span.get('start')}:{span.get('end')}] " +
                                    str(span.get('text', '')).replace('\n', ' ')[:85])

    def _preview_anchor(self, _event=None):
        selected = self.anchor_list.curselection()
        if selected:
            aid = self._anchor_rows[selected[0]]
            anchor = next(a for a in self._record()['anchors'] if a['anchor_id'] == aid)
            self._replace(self.anchor_preview, f"{aid} / {anchor['speaker_actor_id']} " +
                          f"[{anchor['start']}:{anchor['end']}]\n{anchor['text']}")

    def _stage_anchor_move(self):
        selected = self.anchor_list.curselection()
        if not selected or self._code() is None:
            self.status.set('Select an existing anchor to move.')
            return
        aid = self._anchor_rows[selected[0]]
        destination = self.role_destination.get()
        if destination not in (*self._role_draft, 'unassigned'):
            self.status.set('Choose an existing evidence role or unassigned.')
            return
        for ids in self._role_draft.values():
            if aid in ids:
                ids.remove(aid)
        if destination != 'unassigned':
            self._role_draft[destination].append(aid)
        self._refresh_anchors()
        self.status.set('Exact anchor role staged. Enter a rationale before applying; source and actor rules are validated.')

    def revise(self, code_id, changes, reason, *, reviewer_kind, reviewer_id):
        if self.packet is None or self._record_id is None:
            raise ValueError('Open a packet and select a record first.')
        if code_id not in {c['code_id'] for c in self._record().get('proposals', [])}:
            raise ValueError('Code does not belong to the selected record.')
        updated = candidate_packet.revise_code(self.packet, self._record_id, code_id, changes,
                                               reviewer_kind=reviewer_kind, reviewer_id=reviewer_id,
                                               reason=reason)
        candidate_packet.validate_packet(updated)
        self.packet = updated
        self._code_id = code_id
        self.code_tree.item(code_id, text=code_id + ': ' + self._code()['label'])
        self._refresh_code()
        self._replace(self.history_text, json.dumps(self.packet.get('review_history', []),
                                                   ensure_ascii=False, indent=2))
        self.status.set('Provisional revision recorded as ' + reviewer_kind + '. Export to save a copy.')
        return self.packet

    def _apply_researcher_revision(self):
        try:
            code = self._code()
            if code is None:
                raise ValueError('Select a code first.')
            changes = {field: widget.get('1.0', 'end-1c') for field, widget in self.edit_fields.items()
                       if widget.get('1.0', 'end-1c') != code.get(field, '')}
            changes.update({role: spans for role, spans in self._role_draft.items()
                            if spans != code.get(role, [])})
            if not self.reviewer_id.get().strip():
                raise ValueError('Enter a researcher ID for self-declared authorship.')
            if not self.reason_text.get('1.0', 'end-1c').strip():
                raise ValueError('Enter a revision rationale.')
            if not changes:
                raise ValueError('No changes are staged.')
            self.revise(code['code_id'], changes, self.reason_text.get('1.0', 'end-1c'),
                        reviewer_kind='human_declared', reviewer_id=self.reviewer_id.get().strip())
        except (ValueError, TypeError, KeyError) as exc:
            self.status.set('Revision not applied: ' + str(exc))

    def export(self, kind, path):
        if self.packet is None:
            raise ValueError('Open a packet before exporting.')
        candidate_packet.validate_packet(self.packet)
        path = _local_path(path)
        if kind == 'json':
            value = json.dumps(self.packet, ensure_ascii=False, indent=2, allow_nan=False) + '\n'
        elif kind == 'html':
            value = candidate_packet.export_html(self.packet)
        elif kind == 'csv':
            value = candidate_packet.export_csv(self.packet)
        else:
            raise ValueError('Choose json, html, or csv.')
        try:
            with path.open('x', encoding='utf-8') as stream:
                stream.write(value)
        except FileExistsError as exc:
            raise ValueError('Choose a fresh filename; existing files are preserved: ' + str(path)) from exc
        self.status.set('Exported ' + kind.upper() + ' copy: ' + str(path))
        return path

    def _open(self):
        path = filedialog.askopenfilename(parent=self, title='Open candidate packet',
                                          filetypes=[('Candidate JSON', '*.json')])
        if path:
            self.load(path)

    def _save(self, kind):
        path = filedialog.asksaveasfilename(parent=self, title='Export provisional packet copy',
                                           defaultextension='.' + kind,
                                           confirmoverwrite=False,
                                           filetypes=[(kind.upper(), '*.' + kind)])
        if path:
            try:
                self.export(kind, path)
            except (OSError, ValueError, TypeError, KeyError) as exc:
                self.status.set('Export failed: ' + str(exc))

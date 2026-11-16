"""Explicit whole-study work periods, with local source and execution coverage."""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict
from hashlib import sha256
import gc
import json
from pathlib import Path
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, ttk

from .local_inference import GIB, InferenceLimits, LocalInferenceConfig
from .study_batches import prepare_study, validate_study, coverage_report
from .study_runtime import StudyLlamaClient
from .study_session import (build_native_plan, build_resumable_plan, execute_pending, retry_definitions,
    validate_session, validate_planning_session, PLANNING_SESSION_VERSION, export_session_html, source_review_cues)


def read_local_json(path, maximum=128 * 1024**2):
    path = Path(path).resolve()
    if str(path).startswith(('\\\\', '//')):
        raise ValueError('Choose a local file.')
    with path.open('rb') as stream:
        raw = stream.read(maximum + 1)
    if len(raw) > maximum:
        raise ValueError(f'File exceeds the explicit {maximum}-byte import limit.')
    return json.loads(raw.decode('utf-8-sig'))


class StudyDialog(tk.Toplevel):
    def __init__(self, parent, documents=(), research_questions=(), *, config=None,
                 client_factory=None, speaker_label_policy='explicit_roles'):
        super().__init__(parent)
        self.title('Whole-study candidate coding')
        self.geometry('1120x790')
        self.minsize(850, 620)
        self.documents = tuple(documents)
        self.config = config
        self.study = self.session = self.encoder_ledger = self.neighbors = None
        self.planning = None
        self.segment_recovery = None
        self._pair_cursor=-1;self._pair_unit=None
        self._client_factory = client_factory or StudyLlamaClient
        self._events = queue.Queue()
        self._cancel = threading.Event()
        self._worker = None
        self._closing = False
        self._after = None
        self.status = tk.StringVar(self, value='Import coverage is separate from interpretation. No model starts automatically.')
        self.summary = tk.StringVar(self)
        self.resources = tk.StringVar(self)
        self.mode = tk.StringVar(self, value='guided' if research_questions else 'open')
        self.policy = tk.StringVar(self, value=speaker_label_policy)
        self.minutes = tk.IntVar(self, value=10)
        self.max_calls = tk.IntVar(self, value=8)
        self.definitions = tk.BooleanVar(self, value=True)
        top = ttk.Frame(self, padding=10)
        top.pack(fill='x')
        self._buttons = []
        def button(text, command):
            value = ttk.Button(top, text=text, command=command)
            value.pack(side='left', padx=3)
            self._buttons.append(value)
        button('Load local profile', self.load_profile)
        button('Optional models', self.open_models)
        button('Open study/session', self.load)
        button('Export JSON', lambda: self.export('json'))
        button('Export HTML', lambda: self.export('html'))
        ttk.Label(self, textvariable=self.resources, wraplength=1080).pack(fill='x', padx=12)
        options = ttk.Frame(self, padding=10)
        options.pack(fill='x')
        ttk.Label(options, text='New plan:').pack(side='left')
        self.mode_box = ttk.Combobox(options, textvariable=self.mode, values=('open', 'guided'), state='readonly', width=9)
        self.mode_box.pack(side='left', padx=5)
        self.policy_box = ttk.Combobox(options, textvariable=self.policy, values=('explicit_roles', 'legacy'), state='readonly', width=15)
        self.policy_box.pack(side='left')
        ttk.Label(options, text='Work period (minutes)').pack(side='left', padx=(15, 3))
        ttk.Spinbox(options, from_=1, to=60, width=4, textvariable=self.minutes).pack(side='left')
        ttk.Label(options, text='Call cap incl. definitions').pack(side='left', padx=(8,3))
        ttk.Spinbox(options, from_=1, to=100, width=4, textvariable=self.max_calls).pack(side='left')
        ttk.Checkbutton(options, text='Separate scope definitions', variable=self.definitions).pack(side='left', padx=10)
        ttk.Label(self, text='Research questions, one per line. Open mode ignores these. Loaded sessions keep their original mode and questions.').pack(anchor='w', padx=12)
        self.questions = tk.Text(self, height=2, wrap='word')
        self.questions.pack(fill='x', padx=12)
        self.questions.insert('1.0', '\n'.join(research_questions))
        actions = ttk.Frame(self, padding=10)
        actions.pack(fill='x')
        for text, command in (
            ('Refresh whole import', self.refresh_import), ('Plan with native tokens', lambda: self.start_work(False)),
            ('Run pending coding', lambda: self.start_work(True)), ('Cross-batch neighbors', self.start_neighbors),
            ('Retry unfinished definitions', lambda: self.start_work('definitions')),
        ):
            value = ttk.Button(actions, text=text, command=command)
            value.pack(side='left', padx=3)
            self._buttons.append(value)
        self.cancel_button = ttk.Button(actions, text='Cancel work', command=self._cancel.set, state='disabled')
        self.cancel_button.pack(side='left', padx=3)
        recovery_actions=ttk.Frame(self,padding=(10,0));recovery_actions.pack(fill='x')
        for text,command in (('Inspect full source',self.show_full_source),
                             ('Inspect next candidate pair',self.show_next_pair),
                             ('Recover/resume selected boundary span',self.start_segment_recovery),
                             ('Build recovered graph',self.start_recovered_neighbors)):
            control=ttk.Button(recovery_actions,text=text,command=command)
            control.pack(side='left',padx=3);self._buttons.append(control)
        ttk.Label(self, textvariable=self.summary, wraplength=1080).pack(fill='x', padx=12)
        pane = ttk.Panedwindow(self, orient='horizontal')
        pane.pack(fill='both', expand=True, padx=12, pady=8)
        self.tree = ttk.Treeview(pane, columns=('source', 'speaker', 'status'), show='headings', selectmode='browse')
        for key in ('source', 'speaker', 'status'):
            self.tree.heading(key, text=key.capitalize())
            self.tree.column(key, width=140)
        self.tree.bind('<<TreeviewSelect>>', self.show_selected)
        self.detail = tk.Text(pane, wrap='word', state='disabled')
        pane.add(self.tree, weight=1); pane.add(self.detail, weight=2)
        ttk.Label(self, text='CPU work is sequential and bounded. Every imported turn remains visible. Completed means execution, not correct meaning, exhaustive coverage or a theme.', wraplength=1080).pack(fill='x', padx=12)
        ttk.Label(self, textvariable=self.status, wraplength=1080).pack(fill='x', padx=12, pady=8)
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.bind('<Destroy>', self._on_destroy, add=True)
        self._resource_note()
        self.refresh_import()
        self._after = self.after(60, self._poll)

    def _resource_note(self):
        if self.config is None:
            self.resources.set('Select a verified local profile or an optional model with the bundled runtime. No global service is required.')
        else:
            limits = self.config.limits
            self.resources.set(f'{Path(self.config.model_path).name}: CPU {limits.threads} threads; {limits.context_tokens} native context tokens; '
                f'{limits.output_tokens} generation + 1 EOS reserved; {limits.wall_seconds:g}s/request; '
                f'{limits.memory_limit_bytes / GIB:g} GiB sampled helper cap + 2 GiB prestart reserve. Caps are not measured minimum requirements.')

    def load_profile(self, path=None):
        if self._worker is not None:
            return False
        path = path or filedialog.askopenfilename(parent=self, filetypes=[('Local profile', '*.json')])
        if not path:
            return False
        try:
            value = read_local_json(path, 65536)
            defaults = dict(context_tokens=4096, output_tokens=512, threads=2, wall_seconds=180,
                            startup_seconds=90, max_model_bytes=3*GIB, memory_limit_bytes=5*GIB)
            defaults.update(value.get('limits', {}))
            limits = InferenceLimits(**defaults)
            if limits.threads > 2 or limits.max_model_bytes > 6*GIB or limits.memory_limit_bytes is None or limits.memory_limit_bytes > 8*GIB:
                raise ValueError('Whole-study development profile requires CPU <=2, model <=6 GiB and enabled memory cap <=8 GiB.')
            self.config = LocalInferenceConfig(**{key: value[key] for key in ('executable_path', 'executable_sha256', 'model_path', 'model_sha256')}, limits=limits)
            self._resource_note()
            self.status.set('Profile loaded without execution. Existing session can resume only with identical model/runtime/budgets.')
            return True
        except Exception as error:
            self.status.set(f'Profile not loaded: {error}')
            return False

    def open_models(self):
        from .local_model_assets_dialog import LocalModelAssetsDialog
        from .bundled_runtime import RUNTIME_MANIFEST_SHA256, RUNTIME_EXECUTABLE_SHA256
        def selected(model, runtime):
            if runtime is None:
                self.status.set(f'Verified model at {model["path"]}. This source checkout has no app-local runtime; load its explicit local profile.')
                return
            large = model['bytes'] > 4*GIB
            self.config = LocalInferenceConfig(runtime['path'], RUNTIME_EXECUTABLE_SHA256, model['path'], model['sha256'],
                limits=InferenceLimits(context_tokens=4096, output_tokens=512, threads=2, wall_seconds=180, startup_seconds=90,
                    max_model_bytes=(6 if large else 3)*GIB, memory_limit_bytes=(7 if large else 5)*GIB))
            self._resource_note()
            self.status.set('Explicit model selection verified. No inference started; a different model requires a separate new plan.')
        package_root = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parents[1]
        return LocalModelAssetsDialog(self, package_root=package_root, runtime_manifest_sha256=RUNTIME_MANIFEST_SHA256, on_selected=selected)

    def refresh_import(self):
        if self._worker is not None:
            return False
        try:
            if not self.documents:
                self.status.set('Import transcript documents in the main app, or open a saved whole-study JSON. No inference started.')
                return False
            if sum(len(d.text.encode('utf-8')) for d in self.documents) > 64*1024**2:
                raise ValueError('Whole-study import has an explicit 64 MiB source-text resource bound; no turn count cap.')
            study = prepare_study(self.documents, speaker_label_policy=self.policy.get())
            self.study = study
            self.session = self.encoder_ledger = self.neighbors = None
            self.planning = None
            self.segment_recovery = None
            self.render()
            self.status.set('All parsed turns imported. Inspect speaker roles and exact text before planning. Originals are unchanged.')
            return True
        except Exception as error:
            self.status.set(f'Import not changed: {error}')
            return False

    def load(self, path=None):
        if self._worker is not None:
            return False
        path = path or filedialog.askopenfilename(parent=self, filetypes=[('Whole-study JSON', '*.json')])
        if not path:
            return False
        try:
            value = read_local_json(path)
            recovery=ledger=neighbors=None
            if value.get('version') == 'themeforge.study-session.v1':
                validate_session(value); study = value['plan']['study']; session = value; planning = None
            elif value.get('version') == PLANNING_SESSION_VERSION:
                validate_planning_session(value); study=value['study'];session=None;planning=value
            elif value.get('version')=='themeforge.study-segment-recovery.v1':
                from .study_segments import validate_segment_recovery,recovery_coverage
                validate_segment_recovery(value);recovery=value
                ledger=value['original_encoder_ledger'];study=ledger['study'];session=planning=None
                if self.study is not None:
                    try:recovery_coverage(value,self.study);study=self.study
                    except ValueError:pass
            elif value.get('version')=='themeforge.study-encoder.v1':
                from .study_encoder import validate_encoder_ledger
                validate_encoder_ledger(value);ledger=value;study=value['study'];session=planning=None
            elif value.get('version')=='themeforge.study-neighbors.v1':
                from .study_neighbors import validate_study_neighbors
                validate_study_neighbors(value);neighbors=value;study=value.get('source_study');session=planning=None
                if study is None:raise ValueError('This neighbor file has no bound whole-study archive; open its source study separately.')
                validate_study(study)
            elif value.get('version')=='themeforge.recovered-parent-neighbors.v1':
                from .study_recovered_graph import validate_recovered_graph
                validate_recovered_graph(value);neighbors=value;study=value['source_study'];session=planning=None
            else:
                validate_study(value); study = value; session = None;planning = None
            self.study, self.session = study, session
            self.planning=planning
            self.encoder_ledger=ledger;self.neighbors=neighbors;self.segment_recovery=recovery
            self._pair_cursor=-1;self._pair_unit=None
            self.render()
            self.status.set('Opened exact source/session ledger without model execution. Existing session retains its original analytic lens.')
            return True
        except Exception as error:
            self.status.set(f'Study not loaded: {error}')
            return False

    def render(self):
        for row in self.tree.get_children():
            self.tree.delete(row)
        if self.study is None:
            return
        rows = coverage_report(self.session['plan'])['units'] if self.session else self.study['units']
        states = {r.get('unit_id', r.get('id')): r['status'] for r in rows}
        recovered = self._is_recovered_graph()
        if recovered:
            states = {uid:entry['status'] for uid,entry in self.neighbors['parent_coverage'].items()}
        for unit in self.study['units']:
            attribution = self.neighbors.get('parent_attributions',{}).get(unit['id'],{}) if recovered else {}
            speaker = attribution.get('speaker_display',attribution.get('status',unit['speaker']))
            self.tree.insert('', 'end', iid=unit['id'], values=(unit['source_name'], speaker, states[unit['id']]))
        counts = dict(Counter(states.values()))
        row_label='source records' if recovered else 'turns'
        self.summary.set(f'{len(self.study["sources"])} sources; {len(rows)} {row_label}; {counts}; '
            f'{len(self.session["codes"]) if self.session else 0} unreviewed model codes. Source-local speaker labels do not prove independent participants.')
        if self.planning is not None and self.session is None:
            cp=self.planning['checkpoint']
            self.summary.set(self.summary.get()+f' Planning {cp["status"]}: {len(cp["measurements"])} exact measurements retained. {cp["reason"]}')
        if self.session:
            incomplete=sum(c['definition_status'] in {'not_requested','failed','interrupted'} for c in self.session['codes'])
            self.summary.set(self.summary.get()+f' {incomplete} unfinished definitions; retry is explicit and never repeats passage codes.')
        if self.encoder_ledger is not None:
            self.summary.set(self.summary.get() + f' Original encoder-ledger routing: {self.encoder_ledger["coverage_counts"]}. '
                f'{len(self.neighbors["edges"]) if self.neighbors else 0} candidate source pairs, not themes.')
        elif recovered:
            self.summary.set(self.summary.get()+f' {len(self.neighbors["vectors"])} exact vector members across parent records; '
                f'{len(self.neighbors["edges"])} distinct parent-pair candidates. Recovered cores retain their parent identity. '
                'No participant count or semantic recurrence is inferred. Inspect a parent row for its cores and graph changes.')
        elif self.neighbors is not None:
            self.summary.set(self.summary.get()+f' {len(self.neighbors["edges"])} saved candidate pairs. Full graph JSON/HTML export and pair inspection are available; continuation requires its matching encoder ledger.')
        if self.segment_recovery is not None:
            from .study_segments import recovery_coverage
            covered=recovery_coverage(self.segment_recovery,self.study)
            base=self.segment_recovery['original_encoder_ledger'];old={u['id']:u for u in base['study']['units']}
            direct=sum(u['eligible'] and u['id'] in base['vectors'] and all(u[k]==old[u['id']][k] for k in ('source_hash','source_start','source_end','text')) for u in self.study['units'])
            self.summary.set(self.summary.get()+f' Current routing: {direct} direct vectors; boundary recovery {covered["segment_counts"]}, {covered["completed_core_characters"]}/{covered["total_core_characters"]} core characters. Attribution unresolved; original failure retained.')
        overview = coverage_report(self.session['plan']) if self.session else dict(study_sha256=self.study['study_sha256'], states=counts)
        if recovered:
            overview.update(graph_version=self.neighbors['version'], graph_changes=self.neighbors['difference'],
                coverage_changes=self.neighbors['coverage_delta'],
                algorithm=self.neighbors['algorithm'], notice='Source records and core partitions are not independent participants or validated themes.')
        self._put_detail(json.dumps(overview, ensure_ascii=False, indent=2))

    def _is_recovered_graph(self):
        return self.neighbors is not None and self.neighbors.get('version')=='themeforge.recovered-parent-neighbors.v1'

    def _put_detail(self, text):
        self.detail.configure(state='normal'); self.detail.delete('1.0', 'end'); self.detail.insert('1.0', text); self.detail.configure(state='disabled')

    def show_selected(self, _event=None):
        chosen = self.tree.selection()
        if not chosen or self.study is None:
            return
        unit = next(u for u in self.study['units'] if u['id'] == chosen[0])
        value = dict(original_source_turn=unit)
        from .source_context import review_context
        source=next(s for s in self.study['sources'] if s['source_name']==unit['source_name'])
        value['source_context_and_attribution_cues']=review_context(unit,source)
        if self.segment_recovery is not None and unit['id']==self.segment_recovery['parent_unit_id']:
            value['separate_boundary_recovery']=dict(coverage=self.segment_recovery['recovery_coverage'],
                segments=self.segment_recovery['plan']['segments'],
                original_failed_state=self.segment_recovery['plan']['original_parent_coverage'],
                notice='Distinct core vectors, never a pooled parent or additional participants. Original failure and completed units stay unchanged.')
        if self.session:
            value['work_items'] = [i for i in self.session['plan']['work_items'] if i['parent_unit_id'] == unit['id']]
            value['unreviewed_model_codes'] = [c for c in self.session['codes'] if c['parent_unit_id'] == unit['id']]
            value['computed_review_cues_not_revisions'] = {c['id']:source_review_cues(self.session,c) for c in value['unreviewed_model_codes']}
        if self._is_recovered_graph():
            by_id={u['id']:u for u in self.neighbors['units']}
            from .source_context import pair_overview
            coverage=self.neighbors['parent_coverage'][unit['id']]
            member_ids=coverage['member_ids']
            value.update(parent_coverage=coverage,
                structural_attribution=self.neighbors['parent_attributions'][unit['id']],
                exact_graph_members=[dict(source_unit=by_id[uid],
                    attribution=self.neighbors['attributions'][uid],
                    vector_provenance=self.neighbors['vector_provenance'].get(uid),
                    retrieval_status=self.neighbors['member_scan_coverage'].get(uid)) for uid in member_ids],
                candidate_source_pairs=[pair_overview(edge,by_id) for edge in self.neighbors['edges'] if unit['id'] in edge['parent_ids']],
                neighbors_notice='One edge per distinct source-parent pair. Cores, mixed source sections and repeated mentions are not independent participants or semantic recurrence.')
        elif self.neighbors:
            by_id = {u['id']:u for u in self.neighbors['units']}
            from .source_context import pair_overview
            encoding=self.encoder_ledger['coverage'] if self.encoder_ledger else self.neighbors.get('encoding_coverage')
            value['encoding_status'] = encoding[unit['id']] if encoding is not None else dict(status='encoding_coverage_not_supplied')
            value['retrieval_status'] = self.neighbors['coverage'][unit['id']]
            value['candidate_source_pairs'] = [pair_overview(edge,by_id)
                for edge in self.neighbors['edges'] if unit['id'] in edge['member_ids']]
            value['neighbors_notice'] = 'Related opposite views are not interchangeable. These are source-turn neighbors, not accepted codes, categories or themes.'
        self._put_detail(json.dumps(value, ensure_ascii=False, indent=2))

    def show_full_source(self):
        chosen=self.tree.selection()
        if not chosen or self.study is None:return False
        unit=next(u for u in self.study['units'] if u['id']==chosen[0])
        source=next(s for s in self.study['sources'] if s['source_name']==unit['source_name'])
        self._put_detail(json.dumps(dict(original_source=source,selected_source_start=unit['source_start'],
            selected_source_end=unit['source_end'],notice='Full original source for distant context. Select a row again to return; this is not added model evidence.'),ensure_ascii=False,indent=2))
        return True

    def show_next_pair(self):
        if self.neighbors is None:return False
        selected=self.tree.selection();uid=selected[0] if selected else None
        pairs=[p for p in self.neighbors['edges'] if uid is None or uid in p.get('parent_ids',p['member_ids'])]
        if not pairs:return False
        if self._pair_unit!=uid:self._pair_cursor=-1;self._pair_unit=uid
        self._pair_cursor=(self._pair_cursor+1)%len(pairs);edge=pairs[self._pair_cursor]
        from .source_context import review_context
        units={u['id']:u for u in self.neighbors['units']}
        sources={s['source_name']:s for s in (self.neighbors['source_study']['sources'] if self._is_recovered_graph() else self.neighbors['source_index'])}
        members=[dict(original_source_unit=units[k],context_and_attribution=review_context(units[k],sources[units[k]['source_name']])) for k in edge['member_ids']]
        if self._is_recovered_graph():
            for member,member_id,parent_id in zip(members,edge['member_ids'],edge['parent_ids']):
                member.update(parent_unit_id=parent_id,structural_attribution=self.neighbors['attributions'][member_id],
                    vector_provenance=self.neighbors['vector_provenance'][member_id])
        self._put_detail(json.dumps(dict(pair_number=self._pair_cursor+1,pairs_for_selected_unit=len(pairs),
            original_candidate=edge,exact_members=members,
            notice='Similarity is not shared stance, correct attribution or an accepted category. Compare each claim, conditions and distant context.'),ensure_ascii=False,indent=2))
        return True

    def export(self, kind, path=None):
        if self.study is None or self._worker is not None:
            return False
        path = path or filedialog.asksaveasfilename(parent=self, defaultextension='.'+kind)
        if not path:
            return False
        try:
            target = Path(path).resolve()
            if str(target).startswith(('\\\\', '//')):
                raise ValueError('Export locally.')
            if kind == 'html' and self.session is None and self.segment_recovery is None and self.neighbors is None:
                raise ValueError('HTML coding review requires a native plan/session. Export the whole source ledger as JSON first.')
            if self._is_recovered_graph() and self.session is None and self.planning is None:
                from .study_recovered_graph import export_recovered_graph_json,export_recovered_graph_html
                content=export_recovered_graph_html(self.neighbors) if kind=='html' else export_recovered_graph_json(self.neighbors)
            elif self.segment_recovery is not None and self.session is None and self.planning is None:
                from .study_segments_export import export_recovery_html
                from .study_segments import export_segment_recovery_json
                content=export_recovery_html(self.segment_recovery) if kind=='html' else export_segment_recovery_json(self.segment_recovery)
            elif self.neighbors is not None and self.session is None and self.planning is None:
                from .study_neighbors import export_study_neighbors_json,export_study_neighbors_html
                content=export_study_neighbors_html(self.neighbors) if kind=='html' else export_study_neighbors_json(self.neighbors)
            else:
                content = export_session_html(self.session) if kind == 'html' else json.dumps(self.session or self.planning or self.study, ensure_ascii=False, indent=2, allow_nan=False)
            encoded = content.encode('utf-8')
            if kind == 'json' and len(encoded) > 128*1024**2:
                raise ValueError('JSON exceeds the explicit 128 MiB reopen limit; no export file was created.')
            with target.open('xb') as stream:
                stream.write(encoded)
            self.status.set(f'Exported {target}. Original files and raw AI outputs are unchanged.')
            return True
        except Exception as error:
            self.status.set(f'Export not written: {error}')
            return False

    def _begin(self, work):
        # Destroyed Tk object cycles must be collected on the UI thread before
        # allocating heavily in the worker (the existing sample UI does this).
        gc.collect()
        self._cancel.clear()
        for button in self._buttons:
            button.configure(state='disabled')
        self.cancel_button.configure(state='normal')
        def execute():
            try:
                work()
            except Exception as error:
                self._events.put(('error', str(error)))
            finally:
                self._events.put(('done', None))
        self._worker = threading.Thread(target=execute, name='ThemeForge-study-work', daemon=False)
        self._worker.start()

    def start_work(self, run, directory=None):
        if self._worker is not None:
            return False
        try:
            if self.study is None or self.config is None:
                raise ValueError('Import a study and choose an explicit local model/profile first.')
            minutes = int(self.minutes.get())
            if not 1 <= minutes <= 60:
                raise ValueError('Choose 1..60 minutes per work period.')
            call_cap=int(self.max_calls.get())
            if not 1<=call_cap<=100:raise ValueError('Choose 1..100 calls, including definition attempts, per work period.')
            retry_ids=[c['id'] for c in (self.session or {}).get('codes',[]) if c['definition_status'] in {'not_requested','failed','interrupted'}]
            if run=='definitions' and not retry_ids:raise ValueError('No unfinished definitions are available for explicit retry.')
            mode = self.mode.get(); questions = tuple(q.strip() for q in self.questions.get('1.0', 'end').splitlines() if q.strip())
            if self.session is None and self.planning is None and mode == 'guided' and not questions:
                raise ValueError('Guided mode needs a research question.')
            directory = directory or filedialog.askdirectory(parent=self, title='Choose parent for a new local study work folder')
            if not directory:
                return False
            output = Path(directory).resolve() / ('study-' + time.strftime('%Y%m%d-%H%M%S') + '-' + str(time.time_ns() % 1000000))
            if str(output).startswith(('\\\\', '//')):
                raise ValueError('Choose a local output folder.')
            output.mkdir(parents=True, exist_ok=False)
            # Even blocked startup leaves a source ledger and the requested budgets.
            (output/'study.json').write_text(json.dumps(self.study, ensure_ascii=False, indent=2), encoding='utf-8')
            (output/'requested-limits.json').write_text(json.dumps(asdict(self.config.limits), indent=2), encoding='utf-8')
            study, session, config, definitions, planning = self.study, self.session, self.config, self.definitions.get(), self.planning
            def work():
                client = self._client_factory(config, audit_directory=output/'native-calls')
                try:
                    client.start(self._cancel)
                    period_deadline = time.monotonic() + minutes*60
                    client.period_deadline = period_deadline
                    planned = session or build_resumable_plan(study, client, output/'planning', prior=planning,
                        mode=mode,research_questions=questions,wall_seconds=minutes*60,
                        cancel_event=self._cancel,on_progress=lambda p:self._events.put(('progress',p)),
                        on_checkpoint=lambda p:self._events.put(('planning',p)))
                    client.period_deadline = None
                    if planned is None:
                        self._events.put(('message',f'Planning interrupted; exact successful measurements saved in {output}/planning. Reopen planning-session.json or continue this queue to resume.'))
                        return
                    (output/'planned-session.json').write_text(json.dumps(planned, ensure_ascii=False, indent=2), encoding='utf-8')
                    self._events.put(('session', planned))
                    remaining = int(period_deadline-time.monotonic())
                    if run and remaining >= 1 and not self._cancel.is_set():
                        if run=='definitions':
                            planned=retry_definitions(planned,client,output/'definition-retry',code_ids=retry_ids,
                                wall_seconds=remaining,cancel_event=self._cancel,max_generation_calls=call_cap,
                                on_progress=lambda p:self._events.put(('progress',p)))
                        else:
                            planned = execute_pending(planned, client, output/'coding', wall_seconds=remaining, cancel_event=self._cancel,
                                on_progress=lambda p: self._events.put(('progress', p)), define_scope=definitions,max_generation_calls=call_cap)
                        self._events.put(('session', planned))
                    self._events.put(('message', f'Saved {output}. Unfinished and failed work remains explicit. Each new work period starts only on request.'))
                except Exception as error:
                    (output/'failure.json').write_text(json.dumps(dict(error=str(error), diagnostics=client.diagnostics()), indent=2), encoding='utf-8')
                    raise
                finally:
                    client.close()
            self._begin(work)
            self.status.set(f'Preparing native work in {output}; every owned helper will stop at the end of this period.')
            return True
        except Exception as error:
            self.status.set(f'Work not started: {error}')
            return False

    def start_neighbors(self):
        # Separate sentence-encoder work never loads or changes the selected GGUF.
        if self._worker is not None or self.study is None:
            return False
        if self._is_recovered_graph():
            self.status.set('This versioned graph already uses saved vectors. Use Build recovered graph with the original graph and recovery JSON; no encoder queue starts from this view.')
            return False
        self.status.set('Select a pinned local encoder profile for whole-study cross-batch retrieval.')
        return self._start_neighbors()

    def start_recovered_neighbors(self,baseline_path=None,recovery_path=None,directory=None):
        """Build a new immutable version from existing vectors, with no model setup."""
        if self._worker is not None:return False
        try:
            from .study_recovered_graph import build_recovered_graph,export_recovered_graph_json,export_recovered_graph_html
            baseline_path=baseline_path or filedialog.askopenfilename(parent=self,title='Original cross-batch graph JSON (preserved)',filetypes=[('JSON','*.json')])
            if not baseline_path:return False
            recovery_path=recovery_path or filedialog.askopenfilename(parent=self,title='Completed boundary-recovery JSON (all cores required)',filetypes=[('JSON','*.json')])
            if not recovery_path:return False
            directory=directory or filedialog.askdirectory(parent=self,title='Parent folder for a new recovered graph version')
            if not directory:return False
            output=Path(directory).resolve()/('recovered-graph-'+str(time.time_ns()))
            if str(output).startswith(('\\\\','//')):raise ValueError('Choose a local graph output directory.')
            def work():
                values={};pins={}
                for name,path in (('baseline_graph',baseline_path),('recovery',recovery_path)):
                    file=Path(path).resolve()
                    if str(file).startswith(('\\\\','//')):raise ValueError('Graph inputs must be local files.')
                    with file.open('rb') as stream:raw=stream.read(128*1024**2+1)
                    if len(raw)>128*1024**2:raise ValueError('Graph input exceeds the 128 MiB local import bound.')
                    values[name]=json.loads(raw.decode('utf-8-sig'))
                    pins['baseline_file_sha256' if name=='baseline_graph' else 'recovery_file_sha256']=sha256(raw).hexdigest()
                graph=build_recovered_graph(values['baseline_graph'],values['recovery'],input_pins=pins,
                    cancel_event=self._cancel,on_progress=lambda p:self._events.put(('progress',p)))
                output.mkdir(parents=True,exist_ok=False)
                (output/'recovered-graph.json').write_text(export_recovered_graph_json(graph),encoding='utf-8')
                (output/'recovered-graph.html').write_text(export_recovered_graph_html(graph),encoding='utf-8')
                self._events.put(('recovered_graph',graph))
                self._events.put(('message',f'Saved new recovered graph at {output}. Original graph and failures are unchanged; no encoder or LLM ran.'))
            self._begin(work)
            self.status.set('Comparing saved vectors only, with distinct-parent ranking and explicit coverage. Original graph is preserved.')
            return True
        except Exception as error:
            self.status.set(f'Recovered graph not started: {error}');return False

    def start_segment_recovery(self,profile=None,directory=None,*,max_units_per_run=8):
        """Explicit separate encoder branch; never alters completed source vectors."""
        if self._worker is not None or self.study is None:return False
        try:
            from .study_segments import prepare_segment_recovery,run_segment_recovery,recovery_coverage
            prior=self.segment_recovery
            if prior is None:
                chosen=self.tree.selection()
                if not chosen or self.encoder_ledger is None or self.encoder_ledger['coverage'].get(chosen[0],{}).get('status')!='failed':
                    raise ValueError('Open an encoder ledger and select a failed source span, or reopen a recovery JSON to resume.')
                uid=chosen[0]
            else:uid=prior['parent_unit_id']
            profile=profile or filedialog.askopenfilename(parent=self,title='Original pinned encoder profile',filetypes=[('JSON','*.json')])
            if not profile:return False
            directory=directory or filedialog.askdirectory(parent=self,title='New local boundary-recovery work folder')
            if not directory:return False
            seconds=int(self.minutes.get())*60
            if not 60<=seconds<=3600 or type(max_units_per_run) is not int or not 1<=max_units_per_run<=64:
                raise ValueError('Use 1..60 minutes and 1..64 new core segments per work period.')
            output=Path(directory).resolve()
            if str(output).startswith(('\\\\','//')):raise ValueError('Boundary recovery output must be local.')
            old=self.encoder_ledger;study=self.study
            def work():
                prepared=prior or prepare_segment_recovery(old,uid,profile)
                recovery_coverage(prepared,study)
                self._events.put(('segments',prepared))
                result=run_segment_recovery(prepared,profile,output_directory=output,wall_seconds=seconds,
                    max_units_per_run=max_units_per_run,cancel_event=self._cancel,
                    on_progress=lambda event:self._events.put(('progress',event)))
                self._events.put(('segments',result))
                self._events.put(('message','Boundary recovery saved. Reopen its recovery JSON to resume remaining cores; original ledger/failure and completed vectors are unchanged.'))
            self._begin(work)
            self.status.set('Encoding only source-exact core segments, up to eight by default. Context is retained separately; no LLM generation or parent-vector averaging.')
            return True
        except Exception as error:
            self.status.set(f'Boundary recovery not started: {error}');return False

    def _start_neighbors(self):
        from .study_encoder import encode_study, load_encoder_ledger
        from .study_neighbors import build_study_neighbors, export_study_neighbors_json, export_study_neighbors_html
        try:
            profile = filedialog.askopenfilename(parent=self, title='Pinned local encoder profile', filetypes=[('JSON', '*.json')])
            if not profile:
                return False
            if self.encoder_ledger is None:
                title=('Matching encoder ledger required to continue saved graph' if self.neighbors is not None else
                       'Optional prior encoder ledger to resume (Cancel starts a new queue)')
                prior_path = filedialog.askopenfilename(parent=self, title=title, filetypes=[('JSON', '*.json')])
                if self.neighbors is not None and not prior_path:return False
                prior = load_encoder_ledger(prior_path) if prior_path else None
            else:
                prior = self.encoder_ledger
            directory = filedialog.askdirectory(parent=self, title='Parent for a new local neighbor work folder')
            if not directory:
                return False
            output = Path(directory).resolve() / ('study-neighbors-'+str(time.time_ns()))
            if str(output).startswith(('\\\\', '//')):
                raise ValueError('Choose a local output folder.')
            seconds = int(self.minutes.get())*60
            if not 60 <= seconds <= 3600:
                raise ValueError('Choose 1..60 minutes.')
            study = self.study
            output.mkdir(parents=True, exist_ok=False)
            def work():
                ledger = encode_study(study, profile, prior=prior, output_directory=output/'encoding', wall_seconds=seconds,
                    cancel_event=self._cancel, on_progress=lambda p: self._events.put(('progress', p)), chunk_size=1)
                graph = build_study_neighbors(study, ledger['vectors'], batch_ids=ledger['batch_ids'],
                    embedding_provenance=ledger['embedding_provenance'], encoding_coverage=ledger['coverage'], cancel_event=self._cancel)
                (output/'cross-batch-neighbors.json').write_text(export_study_neighbors_json(graph), encoding='utf-8')
                (output/'cross-batch-neighbors.html').write_text(export_study_neighbors_html(graph), encoding='utf-8')
                self._events.put(('neighbors', (ledger, graph)))
                self._events.put(('message', f'Saved source-bound neighbor candidates in {output}. Remaining encoder work is explicit; run again to resume. Neighbors are not codes or themes.'))
            self._begin(work)
            return True
        except Exception as error:
            self.status.set(f'Neighbor work not started: {error}')
            return False

    def _poll(self):
        while True:
            try:
                event, value = self._events.get_nowait()
            except queue.Empty:
                break
            if event == 'session':
                self.session = value; self.study = value['plan']['study']; self.render()
            elif event=='planning':
                self.planning=value;self.render()
            elif event=='segments':
                self.segment_recovery=value;self.render()
            elif event == 'neighbors':
                self.encoder_ledger, self.neighbors = value
                self.render()
            elif event == 'recovered_graph':
                self.neighbors=value;self.study=value['source_study']
                self.encoder_ledger=self.segment_recovery=self.session=self.planning=None
                self._pair_cursor=-1;self._pair_unit=None
                self.render()
            elif event == 'progress':
                self.status.set(json.dumps({k:v for k,v in value.items() if k != 'coverage'}, ensure_ascii=False))
            elif event in {'message', 'error'}:
                self.status.set(('Work stopped: ' if event == 'error' else '') + value)
        if self._worker is not None and not self._worker.is_alive():
            self._worker.join(); self._worker = None
            for button in self._buttons:
                button.configure(state='normal')
            self.cancel_button.configure(state='disabled')
        if self._closing and self._worker is None:
            self.destroy(); return
        self._after = self.after(60, self._poll)

    def close(self):
        self._closing = True
        self._cancel.set()
        self.status.set('Closing after the owned local helper stops.')
        if self._worker is None:
            if self._after is not None:
                self.after_cancel(self._after)
            self.destroy()

    def _on_destroy(self, event):
        if event.widget is self:
            # Parent-window destruction also cancels owned work. The non-daemon
            # worker uses no Tk calls and closes its helper in its finally block.
            self._cancel.set()
            self._closing = True
            if self._after is not None:
                try:
                    self.after_cancel(self._after)
                except tk.TclError:
                    pass
                self._after = None

"""Optional local neighbor retrieval with exact evidence and separate review."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import filedialog, ttk

from .semantic_neighbors import MAX_GRAPH_BYTES, MAX_PACKET_BYTES, validate_packet, validate_neighbor_graph
from .sentence_encoder import file_sha256, local_file
from .neighbor_runner import run_comparison
from .neighbor_review import create_review, validate_review, append_review, export_review_json, export_review_html


class NeighborDialog(tk.Toplevel):
    def __init__(self, app, packet=None, *, runner=None):
        super().__init__(app)
        if app.state() == 'withdrawn':
            self.withdraw()
        self.title('ThemeForge: candidate source neighbors')
        self.geometry('1120x760')
        self.packet = None
        self.reviews = {}
        self._runner = runner or run_comparison
        self._busy = False
        self._closing = False
        self._cancel = threading.Event()
        self._events = queue.Queue()
        self._after = None
        self.profile = tk.StringVar(self)
        self.profile_hash = tk.StringVar(self)
        self.backend = tk.StringVar(self, value='sentence_embeddings')
        self.reviewer = tk.StringVar(self, value='')
        self.decision = tk.StringVar(self, value='uncertain')
        self.status = tk.StringVar(self, value='Open a source packet, or use selected passages from Local candidate codebook.')
        self.coverage = tk.StringVar(self)
        self._build()
        if packet is not None:
            self.set_packet(packet)
        self.protocol('WM_DELETE_WINDOW', self.request_close)
        self.bind('<Destroy>', self._destroyed, add=True)
        self._after = self.after(60, self._drain)

    def _build(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)
        header = ttk.Frame(self, padding=10)
        header.grid(row=0, column=0, sticky='ew')
        header.columnconfigure(1, weight=1)
        ttk.Label(header, text='Local encoder profile').grid(row=0, column=0, sticky='w')
        ttk.Entry(header, textvariable=self.profile).grid(row=0, column=1, sticky='ew', padx=8)
        ttk.Button(header, text='Select profile', command=self.load_profile).grid(row=0, column=2)
        ttk.Label(header, text='Profile SHA-256').grid(row=1, column=0, sticky='w')
        ttk.Entry(header, textvariable=self.profile_hash).grid(row=1, column=1, sticky='ew', padx=8)
        ttk.Label(header, text='CPU only; no automatic download. Exact turns are compared locally.', wraplength=750).grid(row=2, column=0, columnspan=3, sticky='w', pady=5)
        controls = ttk.Frame(self, padding=(10, 0))
        controls.grid(row=1, column=0, sticky='ew')
        ttk.Button(controls, text='Open source packet', command=self.load_packet).pack(side='left')
        self.run_button = ttk.Button(controls, text='Compare encoder and TF-IDF', command=self.start)
        self.run_button.pack(side='left', padx=8)
        self.cancel_button = ttk.Button(controls, text='Cancel', command=self._cancel.set, state='disabled')
        self.cancel_button.pack(side='left')
        ttk.Button(controls, text='Open graph or review', command=self.load_review).pack(side='left', padx=8)
        self.rules_button = ttk.Button(controls, text='Compare saved-vector rules', command=self.compare_saved_rules)
        self.rules_button.pack(side='left', padx=4)
        self.backend_choice = ttk.Combobox(controls, textvariable=self.backend,
            values=('sentence_embeddings', 'lexical_tfidf'), state='readonly', width=22)
        self.backend_choice.pack(side='right')
        self.backend_choice.bind('<<ComboboxSelected>>', lambda event: self._render())
        body = ttk.Panedwindow(self, orient='horizontal')
        body.grid(row=2, column=0, sticky='nsew', padx=10, pady=10)
        left = ttk.Frame(body)
        left.columnconfigure(0, weight=1); left.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(left, columns=('pair', 'cosine'), show='headings', selectmode='browse')
        self.tree.heading('pair', text='Candidate pair'); self.tree.heading('cosine', text='Cosine')
        self.tree.column('pair', width=220); self.tree.column('cosine', width=65)
        self.tree.grid(row=0, column=0, sticky='nsew')
        self.tree.bind('<<TreeviewSelect>>', lambda event: self.show_pair())
        body.add(left, weight=1)
        right = ttk.Frame(body)
        right.columnconfigure(0, weight=1); right.rowconfigure(0, weight=1)
        self.detail = tk.Text(right, wrap='word', state='disabled', width=65)
        self.detail.grid(row=0, column=0, sticky='nsew')
        scroll = ttk.Scrollbar(right, command=self.detail.yview)
        scroll.grid(row=0, column=1, sticky='ns'); self.detail.configure(yscrollcommand=scroll.set)
        body.add(right, weight=3)
        footer = ttk.Frame(self, padding=10)
        footer.grid(row=3, column=0, sticky='ew'); footer.columnconfigure(4, weight=1)
        ttk.Label(footer, textvariable=self.coverage, wraplength=1050).grid(row=0, column=0, columnspan=6, sticky='w')
        ttk.Label(footer, text='Review author').grid(row=1, column=0, sticky='w')
        ttk.Combobox(footer, textvariable=self.reviewer, values=('researcher', 'ai'), state='readonly', width=12).grid(row=1, column=1, padx=6)
        ttk.Combobox(footer, textvariable=self.decision, values=('keep_related_comparison', 'keep_contrast', 'reject_pair', 'uncertain'), state='readonly', width=25).grid(row=1, column=2)
        ttk.Button(footer, text='Record review', command=self.record_review).grid(row=1, column=3, padx=8)
        ttk.Button(footer, text='Export review JSON', command=lambda: self.export('json')).grid(row=1, column=4, sticky='e')
        ttk.Button(footer, text='Export review HTML', command=lambda: self.export('html')).grid(row=1, column=5, padx=5)
        ttk.Label(footer, text='Reason, including member differences or uncertainty').grid(row=2, column=0, columnspan=6, sticky='w', pady=(5, 0))
        self.reason = tk.Text(footer, height=2, wrap='word')
        self.reason.grid(row=3, column=0, columnspan=6, sticky='ew')
        ttk.Label(footer, text='Neighbors suggest comparisons. Related views can conflict; no code, category or theme is automatically accepted.', wraplength=1050).grid(row=4, column=0, columnspan=6, sticky='w', pady=5)
        ttk.Label(footer, textvariable=self.status, wraplength=1050).grid(row=5, column=0, columnspan=6, sticky='w')

    def set_packet(self, packet):
        if self._busy:
            raise ValueError('Wait for the current comparison to finish.')
        validate_packet(packet)
        self.packet = deepcopy(packet)
        self.reviews = {}
        self._render()
        self.status.set(f"Selected {len(packet['nodes'])} exact turns from {len({n['source_hash'] for n in packet['nodes']})} source contents. No inference has started.")

    def load_packet(self, path=None):
        if self._busy:
            return False
        path = path or filedialog.askopenfilename(parent=self, title='Open source packet JSON', filetypes=[('Source packet', '*.json')])
        if not path:
            return False
        try:
            file = local_file(path)
            if file.stat().st_size > MAX_PACKET_BYTES:
                raise ValueError('Source packet exceeds the size bound.')
            self.set_packet(json.loads(file.read_text(encoding='utf-8-sig')))
            return True
        except (ValueError, OSError, KeyError) as error:
            self.status.set(str(error)); return False

    def load_profile(self, path=None):
        if self._busy:
            return False
        path = path or filedialog.askopenfilename(parent=self, title='Select existing pinned encoder profile', filetypes=[('Encoder profile', '*.json')])
        if path:
            try:
                file = local_file(path)
                if file.stat().st_size > 128 * 1024:
                    raise ValueError('Encoder profile is oversized.')
                self.profile.set(str(file)); self.profile_hash.set(file_sha256(file))
                self.status.set('Selected profile integrity pin. The reviewed model and tokenizer hashes are independently enforced before inference.')
                return True
            except (ValueError, OSError) as error:
                self.status.set(str(error))
        return False

    def start(self, output=None):
        if self._busy:
            return False
        try:
            if self.packet is None:
                raise ValueError('Select a source packet first.')
            validate_packet(self.packet)
            if len({n['source_hash'] for n in self.packet['nodes']}) < 2:
                raise ValueError('Select turns from at least two distinct source contents.')
            profile = local_file(self.profile.get())
            if profile.stat().st_size > 128 * 1024:
                raise ValueError('Encoder profile exceeds 128 KiB; select a profile JSON, not model weights.')
            pin = self.profile_hash.get().strip()
            if file_sha256(profile) != pin:
                raise ValueError('Profile SHA-256 does not match.')
            output = output or filedialog.asksaveasfilename(parent=self, title='Name a NEW output folder', initialfile='neighbor-comparison')
            if not output:
                return False
            output = Path(output).resolve()
            if str(output).startswith(('\\\\', '//')):
                raise ValueError('Choose a local output folder.')
            output.mkdir(parents=True, exist_ok=False)
            packet_path = output / 'input-packet.json'
            packet_path.write_text(json.dumps(self.packet, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
            arguments = (packet_path, file_sha256(packet_path), profile, pin, output / 'comparison')
        except (ValueError, OSError) as error:
            self.status.set(str(error)); return False
        self._cancel.clear(); self._busy = True
        self.run_button.configure(state='disabled'); self.cancel_button.configure(state='normal')
        self.rules_button.configure(state='disabled')
        self.status.set('Comparing exact turns locally, two CPU threads; sampled 2 GiB / 180 seconds per backend. No training.')
        def work():
            try:
                report = self._runner(*arguments, cancel=self._cancel)
                self._events.put(('done', report, arguments[-1]))
            except Exception as error:
                self._events.put(('error', str(error), arguments[-1]))
        # Application shutdown waits for cancellation/owned-child cleanup.
        self._thread = threading.Thread(target=work, daemon=False)
        self._thread.start()
        return True

    def compare_saved_rules(self, output=None):
        """Automatic re-selection only; leave loaded evidence/review history intact."""
        if self._busy:
            return False
        try:
            review = self.reviews.get(self.backend.get())
            if not review:
                raise ValueError('Open a completed neighbor graph or review first.')
            validate_review(review)
            output = output or filedialog.asksaveasfilename(parent=self, title='Name a NEW rule-comparison folder', initialfile='neighbor-rule-comparison')
            if not output:
                return False
            output = Path(output).resolve()
            if output.exists() or str(output).startswith(('\\\\', '//')):
                raise ValueError('Choose a fresh local directory; existing artifacts are preserved.')
            graph = deepcopy(review['graph'])
        except (ValueError, OSError) as error:
            self.status.set(str(error)); return False
        self._cancel.clear(); self._busy = True
        self.run_button.configure(state='disabled'); self.rules_button.configure(state='disabled')
        self.cancel_button.configure(state='normal')
        self.status.set('Comparing six bounded rules on saved vectors. No encoder calls or transfer of prior review decisions.')
        def work():
            try:
                from .neighbor_policies import export_policy_comparison
                summary = export_policy_comparison(graph, output, cancel=self._cancel)
                self._events.put(('policies_done', summary, output))
            except Exception as error:
                self._events.put(('error', str(error), output))
        self._thread = threading.Thread(target=work, daemon=False)
        self._thread.start()
        return True

    def _drain(self):
        self._after = None
        try:
            while True:
                event, payload, output = self._events.get_nowait()
                self._busy = False
                self.run_button.configure(state='normal'); self.cancel_button.configure(state='disabled')
                self.rules_button.configure(state='normal')
                if event == 'policies_done':
                    self.status.set(f'Saved all six rules to {output}. Open index.html for the tradeoff, or a branch JSON here. Current review history is unchanged; new branches are unreviewed.')
                elif event == 'done' and payload['status'] == 'completed':
                    self.reviews = {}
                    for backend in ('lexical_tfidf', 'sentence_embeddings'):
                        graph = json.loads((output / backend / 'graph.json').read_text(encoding='utf-8'))
                        self.reviews[backend] = create_review(graph)
                    self.backend.set('sentence_embeddings'); self._render()
                    self.status.set(f'Comparison complete. Raw graphs, logs and resource measurements retained in {output}. No semantic approval was inferred.')
                else:
                    self.status.set(f'Comparison failed or cancelled: {payload}. Partial artifacts remain in {output}.')
        except queue.Empty:
            pass
        if self._closing and not self._busy:
            self.destroy()
        else:
            self._after = self.after(60, self._drain)

    def load_review(self, path=None):
        if self._busy:
            return False
        path = path or filedialog.askopenfilename(parent=self, title='Open candidate graph or review', filetypes=[('JSON artifact', '*.json')])
        if not path:
            return False
        try:
            file = local_file(path)
            if file.stat().st_size > MAX_GRAPH_BYTES * 2:
                raise ValueError('Review artifact exceeds the size bound.')
            value = json.loads(file.read_text(encoding='utf-8-sig'))
            if value.get('type') == 'source_turn_candidate_pairs':
                validate_neighbor_graph(value); review = create_review(value)
            else:
                validate_review(value); review = value
            backend = review['graph']['backend']
            if any(r['graph']['packet_sha256'] != review['graph']['packet_sha256'] for r in self.reviews.values()):
                self.reviews = {}
            self.packet = deepcopy(review['graph']['packet'])
            self.reviews[backend] = review; self.backend.set(backend)
            self._render(); self.status.set('Opened saved neighbor evidence and revision history. No inference or acceptance occurred.')
            return True
        except (ValueError, OSError, KeyError) as error:
            self.status.set(str(error)); return False

    def _render(self):
        self.tree.delete(*self.tree.get_children())
        review = self.reviews.get(self.backend.get())
        if not review:
            self.coverage.set('This backend has no loaded result.'); self._detail('No graph loaded.'); return
        graph = review['graph']; nodes = {n['id']: n for n in graph['packet']['nodes']}
        for index, edge in enumerate(graph['edges']):
            label = ' / '.join(nodes[i]['speaker'] + ': ' + nodes[i]['text'][:34].replace('\n', ' ') for i in edge['member_ids'])
            self.tree.insert('', 'end', iid=str(index), values=(label, f"{edge['cosine']:.3f}"))
        for uid in graph['isolates']:
            node = nodes[uid]
            self.tree.insert('', 'end', iid='iso:' + uid, values=('Unassigned: ' + node['speaker'] + ': ' + node['text'][:45].replace('\n', ' '), ''))
        rule = graph['algorithm']
        rule_text = f"{rule.get('neighbor_rule', 'mutual')} top-{rule['k']}, floor {rule['min_similarity']:.2f}"
        self.coverage.set(f"{graph['backend']} ({rule_text}): {len(graph['edges'])} candidate pairs; {len(graph['isolates'])}/{len(nodes)} turns have no retained neighbor. Scores are not accuracy or probabilities.")
        self._detail('Isolated turns remain available for coding and are not evidence of absent themes:\n' + '\n'.join(graph['isolates']))

    def _detail(self, text):
        self.detail.configure(state='normal'); self.detail.delete('1.0', 'end'); self.detail.insert('1.0', text); self.detail.configure(state='disabled')

    def selected_pair(self):
        review = self.reviews.get(self.backend.get()); selected = self.tree.selection()
        if not review or not selected:
            raise ValueError('Select a candidate pair first.')
        if selected[0].startswith('iso:'):
            raise ValueError('This turn has no candidate pair. It remains available for independent coding.')
        return review, review['graph']['edges'][int(selected[0])]['member_ids']

    def show_pair(self):
        selected = self.tree.selection()
        if selected and selected[0].startswith('iso:'):
            uid = selected[0][4:]
            graph = self.reviews[self.backend.get()]['graph']
            node = next(n for n in graph['packet']['nodes'] if n['id'] == uid)
            self._detail('Unassigned turn under the current neighbor rule. This is not evidence of absent meaning.\n\n' +
                         json.dumps(node, ensure_ascii=False, indent=2))
            return
        try:
            review, ids = self.selected_pair()
        except ValueError:
            return
        graph = review['graph']; nodes = {n['id']: n for n in graph['packet']['nodes']}
        parts = ['Candidate comparison only. Preserve actor, referent, negation, modality, timing and conditions. Do not treat related positions as interchangeable.']
        for uid in ids:
            node = nodes[uid]
            parts += [f"\n{node['speaker']} | {node['source_name']} | {node['source_start']}:{node['source_end']}\nSHA256 {node['source_hash']}\n{node['text']}",
                      '\nExact surrounding context, not additional support:\n' + node.get('context_text', '(No additional context supplied.)')]
        parts += ['\nAppend-only review history:\n' + json.dumps(review['history'], ensure_ascii=False, indent=2)]
        self._detail('\n'.join(parts))

    def record_review(self, *, reviewer_kind=None):
        if self._busy:
            return False
        try:
            review, ids = self.selected_pair()
            updated = append_review(review, ids, self.decision.get(), self.reason.get('1.0', 'end-1c'),
                                    reviewer_kind=reviewer_kind if reviewer_kind is not None else self.reviewer.get())
            self.reviews[self.backend.get()] = updated
            self.show_pair(); self.status.set('Recorded a separate review decision. Raw candidates and earlier revisions remain unchanged.')
            return True
        except (ValueError, KeyError) as error:
            self.status.set(str(error)); return False

    def export(self, kind, path=None):
        try:
            if self._busy or self.backend.get() not in self.reviews:
                raise ValueError('Load a completed comparison or saved review first.')
            path = path or filedialog.asksaveasfilename(parent=self, title='Export neighbor review', defaultextension='.' + kind)
            if not path:
                return False
            review = self.reviews[self.backend.get()]
            text = export_review_json(review) if kind == 'json' else export_review_html(review)
            with Path(path).open('x', encoding='utf-8') as stream:
                stream.write(text)
            self.status.set(f'Exported exact evidence and explicit revision provenance: {path}'); return True
        except (ValueError, OSError) as error:
            self.status.set(str(error)); return False

    def request_close(self):
        if self._busy:
            self._closing = True; self._cancel.set()
            self.status.set('Stopping the owned comparison process before closing.')
        else:
            self.destroy()

    def _destroyed(self, event):
        if event.widget is self:
            self._cancel.set()
            if self._after is not None:
                self.after_cancel(self._after); self._after = None

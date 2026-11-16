"""Explicit local inference and source review in Tk, without browser launch."""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import gc
import importlib
import json
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import filedialog, ttk

from .local_inference import GIB, LocalInferenceConfig, staged_inference_limits
from .study_runtime import StudyLlamaClient
from .local_codebook import has_completed_definition, validate_result_sources

_SOURCE_LABELS = {"Participant/P1 labels": "explicit_roles", "General speaker labels": "legacy"}


class LocalCodebookDialog(tk.Toplevel):
    def __init__(self, app, documents, research_questions=(), *, client_factory=None, codebook_api=None, target_unit_ids=None, prompt_variant=None, speaker_label_policy="legacy"):
        super().__init__(app)
        self.title("Local candidate codebook")
        self.geometry("1180x820")
        self.minsize(900, 680)
        self.documents = tuple(documents)
        self.target_unit_ids = tuple(target_unit_ids) if target_unit_ids is not None else None
        self.prompt_variant = prompt_variant
        self.speaker_label_policy = speaker_label_policy
        self.result = None
        self.loaded_result_path = None
        self.profile_sha256 = ""
        self._limits = staged_inference_limits()
        self._client_factory = client_factory or StudyLlamaClient
        self._codebook_api = codebook_api
        self._events = queue.Queue()
        self._cancel_event = threading.Event()
        self._client_lock = threading.Lock()
        self._client = None
        self._thread = None
        self._busy = False
        self._closing = False
        self._destroyed = False
        self._after_id = None
        self._codes = {}
        self.runtime = tk.StringVar(self)
        self.runtime_sha256 = tk.StringVar(self)
        self.model = tk.StringVar(self)
        self.model_sha256 = tk.StringVar(self)
        self.source_labels = tk.StringVar(self, value=next(label for label, policy in _SOURCE_LABELS.items() if policy == self._speaker_label_policy))
        self.mode = tk.StringVar(self, value="guided" if research_questions else "open")
        self.max_units = tk.IntVar(self, value=6)
        self.reviewed = tk.BooleanVar(self, value=False)
        self.status = tk.StringVar(self, value="Choose existing local files and hashes. Run explicitly starts local inference.")
        self.resource_note = tk.StringVar(self, value=self._resource_description())
        self.gate_note = tk.StringVar(self, value="Select defined codes from at least two sources, then review their claims, definitions and evidence.")
        self.sample_note = tk.StringVar(self, value="This run samples a bounded number of eligible turns. Unselected and rejected passages remain in the Unassigned tab.")
        self._build(research_questions)
        self._selection_policy = self.speaker_label_policy
        self._policy_trace_guard = False
        self.source_labels.trace_add('write', self._source_policy_changed)
        self.protocol("WM_DELETE_WINDOW", self.request_close)
        self.bind("<Destroy>", self._on_destroy, add=True)
        self._after_id = self.after(60, self._drain_events)

    @property
    def speaker_label_policy(self):
        if hasattr(self, "source_labels"):
            label = self.source_labels.get()
            if label not in _SOURCE_LABELS:
                raise ValueError("Choose Participant/P1 labels or General speaker labels.")
            return _SOURCE_LABELS[label]
        return self._speaker_label_policy

    @speaker_label_policy.setter
    def speaker_label_policy(self, policy):
        if policy not in _SOURCE_LABELS.values():
            raise ValueError("Speaker label policy must be explicit_roles or legacy.")
        if getattr(self, '_busy', False):
            raise ValueError('Cannot change speaker label policy during local inference.')
        self._speaker_label_policy = policy
        if hasattr(self, "source_labels"):
            self.source_labels.set(next(label for label, value in _SOURCE_LABELS.items() if value == policy))

    def _build(self, questions):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)
        config = ttk.LabelFrame(self, text="1. Set up local suggestions (not needed to reopen saved work)")
        config.grid(row=0, column=0, sticky="ew", padx=12, pady=10)
        config.columnconfigure(1, weight=1)
        ttk.Button(config, text="Choose saved model profile", command=self.load_profile).grid(row=0, column=0, padx=8, pady=5, sticky="w")
        ttk.Label(config, textvariable=self.resource_note, wraplength=600).grid(row=0, column=1, padx=8, sticky="w")
        self.runtime_details = ttk.Frame(config)
        self.runtime_details.grid(row=2, column=0, columnspan=3, sticky="ew", padx=8)
        self.runtime_details.columnconfigure(1, weight=1)
        for row, (label, variable, browse) in enumerate((
            ("Runtime", self.runtime, True), ("Runtime SHA-256", self.runtime_sha256, False),
            ("GGUF model", self.model, True), ("Model SHA-256", self.model_sha256, False),
        )):
            ttk.Label(self.runtime_details, text=label).grid(row=row, column=0, sticky="w", padx=8, pady=3)
            ttk.Entry(self.runtime_details, textvariable=variable).grid(row=row, column=1, sticky="ew", padx=8, pady=3)
            if browse:
                ttk.Button(self.runtime_details, text="Browse", command=lambda value=variable: self._browse(value)).grid(row=row, column=2, padx=8)
        self.runtime_details.grid_remove()
        self.runtime_details_button = ttk.Button(config, text="Advanced: runtime paths and verification", command=self._toggle_runtime_details)
        self.runtime_details_button.grid(row=1, column=0, columnspan=3, padx=8, pady=3, sticky="w")
        ttk.Label(config, text="Speaker labels").grid(row=3, column=0, sticky="w", padx=8, pady=5)
        self.source_label_choice = ttk.Combobox(config, textvariable=self.source_labels, values=tuple(_SOURCE_LABELS), state="readonly", width=28)
        self.source_label_choice.grid(row=3, column=1, sticky="w", padx=8)
        ttk.Label(config, text="Use General speaker labels for named or mixed speakers; check source evidence before accepting a suggestion.", wraplength=500).grid(row=4, column=0, columnspan=3, sticky="w", padx=8)
        controls = ttk.Frame(self)
        controls.grid(row=1, column=0, sticky="ew", padx=12)
        controls.columnconfigure(4, weight=1)
        ttk.Combobox(controls, textvariable=self.mode, values=("open", "guided"), state="readonly", width=10).grid(row=0, column=0)
        ttk.Label(controls, text="Sample units").grid(row=0, column=1, padx=(12, 4))
        ttk.Spinbox(controls, from_=1, to=12, textvariable=self.max_units, width=5).grid(row=0, column=2)
        self.run_button = ttk.Button(controls, text="2. Generate suggestions", command=self.run_codes)
        self.run_button.grid(row=0, column=3, padx=12)
        self.open_button = ttk.Button(controls, text="Open saved review (JSON)", command=self.load_result)
        self.open_button.grid(row=0, column=5, padx=8)
        self.cancel_button = ttk.Button(controls, text="Cancel", command=self.cancel, state="disabled")
        self.cancel_button.grid(row=0, column=4, sticky="w")
        passage_controls = ttk.Frame(controls)
        passage_controls.grid(row=3, column=0, columnspan=6, sticky='ew', pady=(6, 0))
        self.choose_passages_button = ttk.Button(passage_controls, text='Choose passages', command=self.choose_passages)
        self.choose_passages_button.pack(side='left')
        self.automatic_sample_button = ttk.Button(passage_controls, text='Automatic sample', command=self.automatic_sample)
        self.automatic_sample_button.pack(side='left', padx=8)
        self.target_note = tk.StringVar(self, value='Automatic sample: eligible turns are selected within the maximum unit count.')
        ttk.Label(passage_controls, textvariable=self.target_note, wraplength=700).pack(side='left', fill='x', expand=True)
        self.source_cards_button = ttk.Button(controls, text='Source-only review (no model)', command=self.open_source_cards)
        self.source_cards_button.grid(row=4, column=0, columnspan=3, sticky='w', pady=(6, 0))
        self.load_source_cards_button = ttk.Button(controls, text='Open source-card JSON', command=self.load_source_cards)
        self.load_source_cards_button.grid(row=4, column=3, columnspan=3, sticky='w', pady=(6, 0))
        self.neighbor_button = ttk.Button(controls, text='Compare selected source neighbors', command=self.open_source_neighbors)
        self.neighbor_button.grid(row=5, column=0, columnspan=6, sticky='w', pady=(6, 0))
        ttk.Button(controls, text='Whole-study coding queue', command=self.open_study_coding).grid(row=6, column=0, columnspan=6, sticky='w', pady=(6, 0))
        ttk.Label(controls, text="Research questions, one per line (used only in guided mode)").grid(row=1, column=0, columnspan=5, sticky="w", pady=(8, 2))
        self.questions = tk.Text(controls, height=2, wrap="word")
        self.questions.grid(row=2, column=0, columnspan=5, sticky="ew")
        self.questions.insert("1.0", "\n".join(questions))
        book = ttk.Notebook(self)
        book.grid(row=2, column=0, sticky="nsew", padx=12, pady=10)
        codes = ttk.Frame(book)
        codes.columnconfigure(1, weight=1); codes.rowconfigure(0, weight=1)
        self.code_tree = ttk.Treeview(codes, columns=("label", "status", "review"), show="headings", selectmode="extended", height=8)
        self.code_tree.heading("label", text="Candidate code"); self.code_tree.heading("status", text="Draft stage")
        self.code_tree.heading("review", text="Review decision")
        self.code_tree.column("label", width=190); self.code_tree.column("status", width=120)
        self.code_tree.column("review", width=135)
        self.code_tree.grid(row=0, column=0, sticky="nsew")
        self.code_tree.bind("<<TreeviewSelect>>", self._selection_changed)
        detail_panes = ttk.Panedwindow(codes, orient='vertical')
        detail_panes.grid(row=0, column=1, sticky='nsew', padx=(10, 0))
        self.detail = self._text(detail_panes)
        detail_panes.add(self.detail, weight=1)
        source_frame = ttk.Frame(detail_panes)
        source_frame.columnconfigure(0, weight=1); source_frame.rowconfigure(1, weight=1)
        self.source_choice = ttk.Combobox(source_frame, state='readonly')
        self.source_choice.grid(row=0, column=0, sticky='ew')
        self.source_choice.bind('<<ComboboxSelected>>', self._source_changed)
        self.source_detail = self._text(source_frame)
        self.source_detail.grid(row=1, column=0, sticky='nsew')
        source_scroll = ttk.Scrollbar(source_frame, command=self.source_detail.yview)
        source_scroll.grid(row=1, column=1, sticky='ns')
        self.source_detail.configure(yscrollcommand=source_scroll.set)
        detail_panes.add(source_frame, weight=1)
        self._source_view = None
        self._source_entry_ids = []
        book.add(codes, text="Codes and source evidence")
        self.provenance_detail = self._text(book)
        book.add(self.provenance_detail, text='Provenance / raw output')
        self.categories = self._text(book); book.add(self.categories, text="Category proposals")
        self.review_categories = self._text(book); book.add(self.review_categories, text="Categories added during review")
        self.codebook_notebook = book
        self.themes = self._text(book); book.add(self.themes, text="Interpretive themes")
        self.unassigned = self._text(book); book.add(self.unassigned, text="Unassigned passages")
        footer = ttk.Frame(self)
        footer.grid(row=3, column=0, sticky="ew", padx=12, pady=(0, 12))
        footer.columnconfigure(0, weight=1)
        ttk.Checkbutton(footer, text="I reviewed the selected claims, definitions and source evidence", variable=self.reviewed, command=self._update_actions).grid(row=0, column=0, sticky="w")
        self.category_button = ttk.Button(footer, text="Propose categories", command=self.propose_categories, state="disabled")
        self.category_button.grid(row=0, column=1, padx=6)
        self.evidence_button = ttk.Button(footer, text="Review source evidence", command=self.open_evidence_review, state="disabled")
        self.evidence_button.grid(row=0, column=4, padx=6)
        self.interpretation_button = ttk.Button(footer, text='3. Review / correct selected code', command=self.open_interpretation_review, state='disabled')
        self.interpretation_button.grid(row=0, column=5, padx=6)
        self.evidence_roles_button = ttk.Button(footer, text='Edit evidence roles', command=self.open_evidence_roles_review, state='disabled')
        self.evidence_roles_button.grid(row=1, column=4, columnspan=2, padx=6, sticky='e')
        self.review_category_button = ttk.Button(footer, text='Add category (no model)', command=self.open_reviewed_category, state='disabled')
        self.review_category_button.grid(row=2, column=4, columnspan=2, padx=6, sticky='e')
        self.branch_button = ttk.Button(footer, text='Separate revision branch', command=self.open_revision_branch, state='disabled')
        self.branch_button.grid(row=3, column=4, columnspan=2, padx=6, sticky='e')
        self.json_button = ttk.Button(footer, text="Save review (JSON)", command=lambda: self.export("json"), state="disabled")
        self.json_button.grid(row=0, column=2, padx=6)
        self.html_button = ttk.Button(footer, text="Export readable report", command=lambda: self.export("html"), state="disabled")
        self.html_button.grid(row=0, column=3, padx=6)
        ttk.Label(footer, textvariable=self.gate_note, wraplength=1000).grid(row=1, column=0, columnspan=4, sticky="w", pady=5)
        ttk.Label(footer, text="Experimental local prompting is not validated. Codes and category proposals remain unreviewed drafts; source grounding is not semantic correctness.", wraplength=1000).grid(row=2, column=0, columnspan=4, sticky="w")
        ttk.Label(footer, textvariable=self.sample_note, wraplength=1050).grid(row=3, column=0, columnspan=4, sticky="w", pady=(5, 0))
        ttk.Label(footer, textvariable=self.status, wraplength=1050).grid(row=4, column=0, columnspan=4, sticky="w", pady=(5, 0))
        self._replace(self.categories, "No category proposals. Reviewed source-supported codes are required.")
        self._replace(self.review_categories, 'No category has been added during review.')
        self._replace(self.themes, "No interpretive themes generated. Researcher synthesis remains separate.")

    def _toggle_runtime_details(self):
        """Presentation only: keep all verification fields and checks intact."""
        if self.runtime_details.winfo_manager():
            self.runtime_details.grid_remove()
            self.runtime_details_button.configure(text="Advanced: runtime paths and verification")
        else:
            self.runtime_details.grid()
            self.runtime_details_button.configure(text="Hide advanced runtime details")

    @staticmethod
    def _text(parent):
        return tk.Text(parent, wrap="word", state="disabled", width=55, height=12)

    @staticmethod
    def _replace(widget, text):
        widget.configure(state="normal"); widget.delete("1.0", tk.END); widget.insert("1.0", text); widget.configure(state="disabled")

    def _api(self):
        return self._codebook_api or importlib.import_module("themeforge.local_codebook")

    def open_source_neighbors(self):
        if self._busy:
            self.status.set('Wait for or cancel the current local run first.')
            return None
        from .neighbor_inputs import packet_from_native
        from .neighbor_dialog import NeighborDialog
        try:
            if self.documents:
                native = self._api().prepare_codebook(self.documents, mode='open', research_questions=(),
                    max_units=self.max_units.get(), target_unit_ids=self.target_unit_ids,
                    prompt_variant='grounded-v6', speaker_label_policy=self.speaker_label_policy)
                origin = 'current transcript passage selection'
            elif self.result is not None:
                native = self.result
                origin = 'loaded draft original target turns'
            else:
                self.status.set('Open transcripts or a native draft first. The main app can open a saved neighbor packet or graph.')
                return None
            window = NeighborDialog(self, packet_from_native(native))
            self.status.set(f'Opened source neighbors from {origin}. Generated labels are excluded from encoder input.')
            window.status.set(f'Source selection: {origin}; {len(window.packet["nodes"])} turns. The current transcript selection takes priority when transcripts are loaded.')
            return window
        except (ValueError, OSError, TypeError, tk.TclError) as error:
            self.status.set(f'Could not prepare source neighbors: {error}')
            return None

    def open_source_cards(self):
        """Review selected exact source turns without creating conceptual codes."""
        if self._busy:
            self.status.set('Wait for or cancel the current local run before opening source-only review.')
            return None
        from .source_cards import build_source_cards
        from .source_card_dialog import SourceCardDialog
        try:
            if self.documents:
                packet = self._api().prepare_codebook(
                    self.documents, mode='open', research_questions=(),
                    max_units=self.max_units.get(), target_unit_ids=self.target_unit_ids,
                    prompt_variant='grounded-v6', speaker_label_policy=self.speaker_label_policy)
                cards = build_source_cards(packet)
                origin = 'current passage selection'
            elif self.result is not None:
                cards = build_source_cards(self.result)
                origin = 'loaded draft target turns'
            else:
                self.status.set('Open transcripts or a draft first, or choose Open source-card JSON.')
                return None
            window = SourceCardDialog(self, cards)
            self.status.set(f'Opened exact source cards from {origin}. No model, conceptual coding or category generation ran.')
            return window
        except (ValueError, OSError, TypeError, tk.TclError) as error:
            self.status.set(f'Source-only review failed: {error}')
            return None

    def load_source_cards(self, path=None):
        if self._busy:
            self.status.set('Wait for or cancel the current local run before opening source cards.')
            return None
        from .source_card_dialog import SourceCardDialog
        path = path or filedialog.askopenfilename(parent=self, title='Open exact source cards', filetypes=[('JSON source cards', '*.json')])
        if not path:
            return None
        window = SourceCardDialog(self)
        if not window.load(path):
            self.status.set(window.status.get())
            window.destroy()
            return None
        self.status.set('Opened exact source cards without importing codes or starting a model.')
        return window

    def _browse(self, variable):
        path = filedialog.askopenfilename(parent=self, title="Choose an existing local file")
        if path:
            variable.set(path)

    def load_profile(self, path=None, *, expected_sha256=None):
        if self._busy:
            return False
        path = path or filedialog.askopenfilename(parent=self, title="Load existing local profile", filetypes=[("JSON profile", "*.json")])
        if not path:
            return False
        try:
            with Path(path).open("rb") as source:
                raw = source.read(65_537)
            if len(raw) > 65_536:
                raise ValueError("Local profile exceeds 64 KiB.")
            digest = sha256(raw).hexdigest()
            if expected_sha256 is not None and digest != expected_sha256.lower():
                raise ValueError("Local profile SHA-256 does not match the expected profile.")
            profile = json.loads(raw.decode("utf-8"))
            limits = staged_inference_limits(profile)
            config = LocalInferenceConfig(**{key: profile[key] for key in ("executable_path", "executable_sha256", "model_path", "model_sha256")}, limits=limits)
            self.runtime.set(str(config.executable_path)); self.runtime_sha256.set(config.executable_sha256)
            self.model.set(str(config.model_path)); self.model_sha256.set(config.model_sha256)
            self.profile_sha256 = digest
            self._limits = limits
            self.resource_note.set(self._resource_description())
            self.status.set(f"Profile loaded without execution. Profile SHA-256: {digest}")
            return True
        except (OSError, ValueError, KeyError, TypeError) as error:
            self.status.set(f"Profile not loaded: {error}")
            return False

    def load_result(self, path=None):
        """Reopen a generated draft without executing a model or changing sources."""
        if self._busy:
            return False
        path = path or filedialog.askopenfilename(parent=self, title="Open candidate codebook JSON", filetypes=[("JSON draft", "*.json")])
        if not path:
            return False
        try:
            source_path = Path(path).resolve()
            if str(source_path).startswith(("\\\\", "//")):
                raise ValueError("Choose a local draft file, not a network share.")
            with source_path.open("rb") as source:
                raw = source.read(16 * 1024 * 1024 + 1)
            if len(raw) > 16 * 1024 * 1024:
                raise ValueError("Generated draft exceeds 16 MiB.")
            result = json.loads(raw.decode("utf-8"))
            if type(result) is not dict:
                raise ValueError("Generated draft must be a result object.")
            validate_result_sources(result)
            self.set_result(result)
            self.loaded_result_path = source_path
            self.status.set(f"Opened {source_path.name} without model execution. Loaded mode: {result.get('mode', 'unspecified')}; controls above configure new runs. Saved decisions and original provenance retained; grouping checkbox reset.")
            return True
        except (OSError, ValueError, KeyError, TypeError, AttributeError, RecursionError) as error:
            self.status.set(f"Draft not loaded: {error}")
            return False

    def _configuration(self):
        return LocalInferenceConfig(self.runtime.get().strip(), self.runtime_sha256.get().strip(), self.model.get().strip(), self.model_sha256.get().strip(),
                                    limits=self._limits)

    def open_study_coding(self):
        from .study_dialog import StudyDialog
        try:
            config = self._configuration()
        except (ValueError, TypeError):
            config = None
        return StudyDialog(self, self.documents,
            research_questions=tuple(q.strip() for q in self.questions.get('1.0', 'end').splitlines() if q.strip()),
            config=config, speaker_label_policy=self.speaker_label_policy)

    def _resource_description(self):
        limits = self._limits
        return (f"CPU · {limits.context_tokens} context · {limits.output_tokens} output · "
                f"{limits.threads} threads · {limits.wall_seconds:g}s/request · "
                f"sampled {limits.memory_limit_bytes / GIB:g} GiB memory tripwire")

    def _eligible_target_units(self):
        from .discovery_units import build_meaning_units
        from .generation_schema import build_span_registry
        if not self.documents:
            raise ValueError('Open transcript documents before choosing passages.')
        if sum(len(document.text.encode('utf-8')) for document in self.documents) > 16 * 1024 * 1024:
            raise ValueError('This bounded workflow accepts at most 16 MiB of source text.')
        policy = self.speaker_label_policy
        units = build_meaning_units(self.documents, speaker_label_policy=policy)
        registry = build_span_registry(units, self.documents, speaker_label_policy=policy)
        eligible = {entry['unit_id'] for entry in registry.values()}
        return [unit for unit in units if unit['id'] in eligible]

    def _validated_target_ids(self, ids):
        if isinstance(ids, (str, bytes)):
            raise ValueError('Choose 1..12 unique eligible passage IDs.')
        try:
            selected = tuple(ids)
        except TypeError as error:
            raise ValueError('Choose 1..12 unique eligible passage IDs.') from error
        if (not 1 <= len(selected) <= 12 or any(type(uid) is not str for uid in selected)
                or len(set(selected)) != len(selected)):
            raise ValueError('Choose 1..12 unique eligible passage IDs.')
        eligible = {unit['id'] for unit in self._eligible_target_units()}
        if not set(selected) <= eligible:
            raise ValueError('Passage selection contains stale, unknown or ineligible source turns.')
        return selected

    def set_target_units(self, ids):
        """Select immutable source turns for the next run, without inference."""
        if self._busy:
            return False
        try:
            selected = self._validated_target_ids(ids)
        except (ValueError, KeyError, TypeError) as error:
            self.status.set(f'Passages not selected: {error}')
            return False
        self.target_unit_ids = selected
        self._selection_policy = self.speaker_label_policy
        self.max_units.set(len(selected))
        self.target_note.set(f'{len(selected)} explicit passages selected under {self.source_labels.get()}. Applies to the next run.')
        self.status.set('Exact source passages selected without model execution; existing drafts and source documents are unchanged.')
        return True

    def automatic_sample(self):
        if self._busy:
            return False
        self.target_unit_ids = None
        self.target_note.set('Automatic sample: eligible turns are selected within the maximum unit count.')
        self.status.set('Explicit passage selection cleared for the next run; source documents and existing drafts are unchanged.')
        return True

    def _source_policy_changed(self, *_args):
        if self._policy_trace_guard:
            return
        try:
            policy = self.speaker_label_policy
        except ValueError as error:
            self.status.set(str(error))
            return
        if self._busy:
            self._policy_trace_guard = True
            self.source_labels.set(next(label for label, value in _SOURCE_LABELS.items() if value == self._selection_policy))
            self._policy_trace_guard = False
            self.status.set('Speaker policy and passage selection cannot change during local inference.')
            return
        if policy == self._selection_policy:
            return
        self._selection_policy = policy
        if self.target_unit_ids is None:
            return
        try:
            self._validated_target_ids(self.target_unit_ids)
        except (ValueError, KeyError, TypeError):
            self.target_unit_ids = None
            self.target_note.set('Automatic sample: previous passage selection was cleared after the speaker-label policy changed.')
            self.status.set('Stale passage selection cleared. Choose passages again under the new speaker-label policy.')
        else:
            self.target_note.set(f'{len(self.target_unit_ids)} explicit passages revalidated under {self.source_labels.get()}.')
            self.status.set('Selected exact passage IDs remain eligible under the new speaker-label policy; no source mapping changed.')

    def choose_passages(self):
        if self._busy:
            return None
        try:
            units = self._eligible_target_units()
            if not units:
                raise ValueError('No eligible passages under the current speaker-label policy.')
        except (ValueError, KeyError, TypeError) as error:
            self.status.set(str(error))
            return None
        lookup = {unit['id']: unit for unit in units}
        policy = self.speaker_label_policy
        window = tk.Toplevel(self)
        window.title('Choose passages for the next local run')
        window.geometry('960x700')
        window.transient(self)
        window.columnconfigure(0, weight=1)
        window.rowconfigure(1, weight=1)
        window.rowconfigure(2, weight=1)
        ttk.Label(window, text=f'Select 1..12 complete eligible turns under {self.source_labels.get()}. Source text stays unchanged; this is a bounded sample, not whole-corpus coverage.', wraplength=900).grid(row=0, column=0, columnspan=2, sticky='w', padx=12, pady=8)
        tree = ttk.Treeview(window, columns=('source', 'speaker', 'preview'), show='headings', selectmode='extended')
        for column, title, width in (('source', 'Source', 230), ('speaker', 'Speaker', 120), ('preview', 'Source preview', 550)):
            tree.heading(column, text=title); tree.column(column, width=width)
        tree.grid(row=1, column=0, sticky='nsew', padx=(12, 0))
        scroll = ttk.Scrollbar(window, orient='vertical', command=tree.yview)
        scroll.grid(row=1, column=1, sticky='ns', padx=(0, 12)); tree.configure(yscrollcommand=scroll.set)
        for unit in units:
            tree.insert('', tk.END, iid=unit['id'], values=(unit['source_name'], unit['speaker'], unit['text']))
        tree.selection_set([uid for uid in (self.target_unit_ids or ()) if uid in lookup])
        preview = self._text(window)
        preview.grid(row=2, column=0, columnspan=2, sticky='nsew', padx=12, pady=8)
        def show_exact(_event=None):
            self._replace(preview, '\n\n'.join(f"{lookup[uid]['source_name']} / {lookup[uid]['speaker']}\n{uid}\nSHA256: {lookup[uid]['source_hash']}\nCharacters {lookup[uid]['source_start']}:{lookup[uid]['source_end']}\nExact full turn:\n{lookup[uid]['text']}" for uid in tree.selection()))
        tree.bind('<<TreeviewSelect>>', show_exact); show_exact()
        notice = ttk.Label(window, text='', wraplength=900)
        notice.grid(row=3, column=0, columnspan=2, sticky='w', padx=12)
        buttons = ttk.Frame(window); buttons.grid(row=4, column=0, columnspan=2, sticky='e', padx=12, pady=10)
        def apply():
            if self.speaker_label_policy != policy:
                notice.configure(text='Speaker-label policy changed; close and choose passages again.')
            elif self.set_target_units(list(tree.selection())):
                window.destroy()
            else:
                notice.configure(text=self.status.get())
        ttk.Button(buttons, text='Cancel', command=window.destroy).pack(side='left', padx=8)
        apply_button = ttk.Button(buttons, text='Use selected passages', command=apply)
        apply_button.pack(side='left')
        window.passage_tree, window.preview, window.apply_button = tree, preview, apply_button
        window.grab_set()
        return window

    def run_codes(self):
        if self._busy:
            return False
        try:
            if not self.documents:
                raise ValueError("Open transcript documents before running local codes.")
            count = self.max_units.get()
            if type(count) is not int or not 1 <= count <= 12:
                raise ValueError("Maximum units must be from 1 to 12.")
            if self.target_unit_ids is not None:
                selected = self._validated_target_ids(self.target_unit_ids)
                if len(selected) > count:
                    raise ValueError('Maximum units is smaller than the explicit passage selection; increase it or choose fewer passages.')
            mode = self.mode.get()
            if mode not in {"open", "guided"}:
                raise ValueError("Choose open or guided mode.")
            questions = tuple(line.strip() for line in self.questions.get("1.0", tk.END).splitlines() if line.strip())
            if mode == "guided" and not questions:
                raise ValueError("Guided mode requires a research question.")
            config = self._configuration()
        except (ValueError, tk.TclError) as error:
            self.status.set(str(error)); return False
        return self._launch("codes", config, {"documents": self.documents, "mode": mode, "research_questions": questions, "max_units": count, "target_unit_ids": self.target_unit_ids, "prompt_variant": self.prompt_variant, "speaker_label_policy": self.speaker_label_policy})

    def _selected_usable(self):
        ids = list(self.code_tree.selection())
        if not 2 <= len(ids) <= 6 or any(not has_completed_definition(self._codes[uid]) for uid in ids):
            return []
        from .codebook_review import grouping_review_issue
        if any(grouping_review_issue(self.result, uid) for uid in ids):
            return []
        sources = {entry.get("source_hash") for uid in ids for entry in self._codes[uid].get("evidence", []) if entry.get("source_hash")}
        return ids if len(sources) >= 2 else []

    def _can_review_evidence(self, *, allow_incomplete=False):
        if self._busy or self.result is None:
            return False
        selection = self.code_tree.selection()
        return (len(selection) == 1 and (has_completed_definition(self._codes[selection[0]])
                or (allow_incomplete and self._codes[selection[0]].get('status') in {'scope_insufficient', 'scope_rejected', 'decision_only'}))
                and not self._has_synthesis())

    def apply_candidate_review(self, code_id, changes, decision, *, reviewer_kind,
                               reviewer_name, reason, checks=None, expected_digest=None):
        """Apply the existing wording editor and a named decision atomically."""
        if self._busy or self.result is None:
            return False
        try:
            from .codebook_review import revise_interpretation, record_candidate_decision
            if expected_digest is not None and self.result['result_sha256'] != expected_digest:
                raise ValueError('The draft changed. Reopen this editor before applying a decision.')
            if changes and decision != 'edit':
                raise ValueError('Choose edit to save changed wording, or restore the wording before another decision.')
            revised = self.result
            if changes:
                revised = revise_interpretation(revised, code_id, changes,
                    reviewer_kind=reviewer_kind, reviewer_name=reviewer_name, reason=reason)
            revised = record_candidate_decision(revised, code_id, decision,
                reviewer_kind=reviewer_kind, reviewer_name=reviewer_name, reason=reason, checks=checks)
            self.set_result(revised)
            self.code_tree.selection_set(code_id)
            self._selection_changed()
            self.status.set(f'{decision} recorded by {reviewer_name} ({reviewer_kind}). Export JSON to keep this revision. Original outputs retained; review is not validated accuracy.')
            return True
        except (ValueError, KeyError, TypeError) as error:
            self.status.set(f'Review not applied: {error}')
            return False

    def apply_candidate_split(self, code_id, parts, *, reviewer_kind, reviewer_name,
                              reason, checks=None, expected_digest=None):
        if self._busy or self.result is None:
            return False
        try:
            from .codebook_review import split_candidate
            if expected_digest is not None and self.result['result_sha256'] != expected_digest:
                raise ValueError('The draft changed. Reopen this editor before splitting.')
            revised = split_candidate(self.result, code_id, parts, reviewer_kind=reviewer_kind,
                reviewer_name=reviewer_name, reason=reason, checks=checks)
            self.set_result(revised)
            children = [code['id'] for code in revised['codes'] if code.get('split_parent_id') == code_id]
            if children:
                self.code_tree.selection_set(children[0])
            self._selection_changed()
            self.status.set('Split applied with explicit authorship. Export JSON to keep it. Original retained; review each child before grouping. Parts share one source turn, not independent accounts.')
            return True
        except (ValueError, KeyError, TypeError) as error:
            self.status.set(f'Candidate not split: {error}')
            return False

    def _has_synthesis(self):
        return self.result is not None and (
            bool(self.result.get('categories'))
            or bool(self.result.get('reviewed_categories'))
            or any(key.startswith('synthesis') for key in self.result)
            or any(call.get('stage') == 'synthesis' for call in self.result.get('calls', [])))

    def revise_evidence(self, code_id, span_ids, reason, reviewer_kind='researcher', reviewer_name=None):
        """Record an explicit evidence selection edit, without model execution."""
        if self._busy or self.result is None:
            return False
        try:
            api = importlib.import_module('themeforge.codebook_review')
            revised = api.revise_supporting_evidence(self.result, code_id, span_ids,
                                                   reviewer_kind=reviewer_kind, reason=reason, reviewer_name=reviewer_name)
            self.set_result(revised)
            self.code_tree.selection_set(code_id)
            self._selection_changed()
            self.status.set('Source evidence selection revised with an audit record. Model text and raw calls retained; review gate reset. Interpretation remains unreviewed.')
            return True
        except (ValueError, KeyError, TypeError) as error:
            self.status.set(f'Evidence not revised: {error}')
            return False

    def revise_interpretation(self, code_id, changes, reason, *, reviewer_kind, reviewer_name=None):
        """Apply explicitly authored wording with provenance and no validity promotion."""
        if self._busy or self.result is None:
            return False
        try:
            api = importlib.import_module('themeforge.codebook_review')
            revised = api.revise_interpretation(self.result, code_id, changes,
                                               reviewer_kind=reviewer_kind, reason=reason, reviewer_name=reviewer_name)
            self.set_result(revised)
            self.code_tree.selection_set(code_id)
            self._selection_changed()
            self.status.set(f'Interpretation revised by {reviewer_kind}; original model wording and raw calls retained. Warnings recalculated; review gate reset. Meaning remains unvalidated.')
            return True
        except (ValueError, KeyError, TypeError) as error:
            self.status.set(f'Interpretation not revised: {error}')
            return False

    def revise_evidence_roles(self, code_id, roles, reason, *, reviewer_kind, reviewer_name=None):
        """Apply explicit atomic role edits with a separate author for each role."""
        if self._busy or self.result is None:
            return False
        try:
            api = importlib.import_module('themeforge.codebook_review')
            revised = api.revise_evidence_roles(self.result, code_id, roles, reviewer_kind=reviewer_kind, reason=reason, reviewer_name=reviewer_name)
            self.set_result(revised)
            self.code_tree.selection_set(code_id)
            self._selection_changed()
            self.status.set(f'Evidence roles revised by {reviewer_kind}; original model selections, prose and raw calls retained. Review gate reset; no interpretation approval.')
            return True
        except (ValueError, KeyError, TypeError) as error:
            self.status.set(f'Evidence roles not revised: {error}')
            return False

    @staticmethod
    def _reviewer_controls(window, row, *, columnspan=2):
        frame = ttk.Frame(window)
        frame.grid(row=row, column=0, columnspan=columnspan, sticky='ew', padx=12, pady=6)
        ttk.Label(frame, text='Reviewer name / identifier').pack(side='left')
        name = tk.StringVar(window)
        ttk.Entry(frame, textvariable=name, width=24).pack(side='left', padx=6)
        ttk.Label(frame, text='Authorship').pack(side='left')
        kind = tk.StringVar(window)
        ttk.Combobox(frame, textvariable=kind, values=('researcher', 'assistant_development_review'),
                     state='readonly', width=29).pack(side='left', padx=6)
        return name, kind

    def open_evidence_roles_review(self):
        if not self._can_review_evidence():
            self.status.set('Select one defined code before grouping to edit evidence roles.')
            return None
        code_id = self.code_tree.selection()[0]
        code = self._codes[code_id]
        api = importlib.import_module('themeforge.codebook_review')
        current = api._role_ids(self.result, code)
        origins = api._role_origins(code)
        spans = {sid: span for sid, span in self.result['registry'].items() if span['unit_id'] == code['unit_id']}
        assignments = {sid: 'unassigned' for sid in spans}
        for role, ids in current.items():
            for sid in ids:
                assignments[sid] = role
        window = tk.Toplevel(self)
        window.title('Edit evidence roles')
        window.geometry('960x760')
        window.transient(self)
        window.columnconfigure(0, weight=1)
        window.rowconfigure(2, weight=1); window.rowconfigure(3, weight=1)
        ttk.Label(window, text=f"{code['label']}\nAssign exact same-turn spans atomically. Support needs 1..6; qualifications and counterexamples allow 0..6 each, at most 6 combined. Roles cannot overlap. Empty other roles mean none selected, not absence.", wraplength=900).grid(row=0, column=0, columnspan=2, sticky='w', padx=12, pady=8)
        authors = ttk.Label(window, text='Current role authors: ' + '; '.join(f'{role}: {origin}' for role, origin in origins.items()) + '. Choose authorship for this edit below.', wraplength=900)
        authors.grid(row=1, column=0, columnspan=2, sticky='w', padx=12)
        tree = ttk.Treeview(window, columns=('role', 'id', 'text'), show='headings', selectmode='extended')
        for column, title, width in (('role', 'Draft role', 120), ('id', 'Frozen span ID', 220), ('text', 'Exact source text', 560)):
            tree.heading(column, text=title); tree.column(column, width=width)
        tree.grid(row=2, column=0, sticky='nsew', padx=(12, 0), pady=8)
        scroll = ttk.Scrollbar(window, orient='vertical', command=tree.yview)
        scroll.grid(row=2, column=1, sticky='ns', padx=(0, 12)); tree.configure(yscrollcommand=scroll.set)
        for sid, span in spans.items():
            tree.insert('', tk.END, iid=sid, values=(assignments[sid], sid, span['text']))
        tree.selection_set(code['support_span_ids'])
        preview = self._text(window)
        preview.grid(row=3, column=0, columnspan=2, sticky='nsew', padx=12)
        def show_exact(_event=None):
            self._replace(preview, '\n\n'.join(f"{sid}\nDraft role: {assignments[sid]}\n{spans[sid]['source_name']} / {spans[sid]['speaker']}\nSHA256: {spans[sid]['source_hash']}\nCharacters {spans[sid]['source_start']}:{spans[sid]['source_end']}\nExact source text:\n{spans[sid]['text']}" for sid in tree.selection()))
        tree.bind('<<TreeviewSelect>>', show_exact); show_exact()
        assignment_controls = ttk.Frame(window)
        assignment_controls.grid(row=4, column=0, columnspan=2, sticky='w', padx=12, pady=8)
        role_choice = tk.StringVar(window, value='support')
        ttk.Combobox(assignment_controls, textvariable=role_choice, values=('unassigned', 'support', 'qualifications', 'counterexamples'), state='readonly', width=22).pack(side='left')
        def assign():
            role = role_choice.get()
            if role not in {'unassigned', 'support', 'qualifications', 'counterexamples'}:
                return
            for sid in tree.selection():
                assignments[sid] = role
                tree.set(sid, 'role', role)
            show_exact()
        assign_button = ttk.Button(assignment_controls, text='Assign selected spans', command=assign)
        assign_button.pack(side='left', padx=8)
        reviewer_name, reviewer_kind = self._reviewer_controls(window, 5)
        ttk.Label(window, text='Required rationale for the role changes:').grid(row=6, column=0, columnspan=2, sticky='w', padx=12)
        rationale = tk.Text(window, height=3, wrap='word')
        rationale.grid(row=7, column=0, columnspan=2, sticky='ew', padx=12)
        notice = ttk.Label(window, text='', wraplength=900)
        notice.grid(row=8, column=0, columnspan=2, sticky='w', padx=12)
        buttons = ttk.Frame(window); buttons.grid(row=9, column=0, columnspan=2, sticky='e', padx=12, pady=10)
        def apply():
            if not reviewer_name.get().strip() or reviewer_kind.get() not in ('researcher', 'assistant_development_review'):
                notice.configure(text='Supply reviewer name and choose authorship. AI edits are not researcher review.'); return
            roles = {role: [sid for sid in spans if assignments[sid] == role] for role in current}
            if self.revise_evidence_roles(code_id, roles, rationale.get('1.0', 'end-1c'), reviewer_kind=reviewer_kind.get(), reviewer_name=reviewer_name.get()):
                window.destroy()
            else:
                notice.configure(text=self.status.get())
        ttk.Button(buttons, text='Cancel', command=window.destroy).pack(side='left', padx=8)
        apply_button = ttk.Button(buttons, text='Apply evidence roles', command=apply)
        apply_button.pack(side='left')
        window.span_tree, window.preview, window.author_label = tree, preview, authors
        window.role_choice, window.assign_button = role_choice, assign_button
        window.rationale, window.apply_button, window.notice = rationale, apply_button, notice
        window.reviewer_name, window.reviewer_kind = reviewer_name, reviewer_kind
        window.grab_set()
        return window

    def open_interpretation_review(self):
        if not self._can_review_evidence(allow_incomplete=True):
            self.status.set('Select one candidate before grouping to review or revise it. Reopen the parent draft after grouping.')
            return None
        code_id = self.code_tree.selection()[0]
        code = self._codes[code_id]
        before_digest = self.result['result_sha256']
        window = tk.Toplevel(self)
        window.title('Review / revise candidate')
        window.geometry('1140x800')
        window.minsize(900, 650)
        window.transient(self)
        window.columnconfigure(0, weight=1); window.columnconfigure(1, weight=1)
        window.rowconfigure(2, weight=1)
        ttk.Label(window, text='Compare candidate meaning with the exact target. Context speakers, reported actors and addressees can differ. Source labels prove attribution only. No decision here establishes human ground truth.', wraplength=1080).grid(row=0, column=0, columnspan=2, sticky='w', padx=12, pady=8)
        authors = ttk.Frame(window)
        authors.grid(row=1, column=0, columnspan=2, sticky='ew', padx=12, pady=5)
        ttk.Label(authors, text='Reviewer name / identifier').pack(side='left')
        reviewer_name = tk.StringVar(window)
        ttk.Entry(authors, textvariable=reviewer_name, width=26).pack(side='left', padx=6)
        ttk.Label(authors, text='Authorship').pack(side='left')
        reviewer_kind = tk.StringVar(window)
        ttk.Combobox(authors, textvariable=reviewer_kind,
            values=('researcher', 'assistant_development_review'), state='readonly', width=30).pack(side='left', padx=6)
        book = ttk.Notebook(window)
        book.grid(row=2, column=0, sticky='nsew', padx=12, pady=8)
        author_labels = {}
        def wording_tab(title, initial, *, first=False):
            frame = ttk.Frame(book); frame.columnconfigure(0, weight=1)
            book.add(frame, text=title)
            fields = {}
            for row, field in enumerate(('label', 'claim', 'definition')):
                origin = code.get('interpretation_field_origins', {}).get(field, code.get('field_origins', {}).get(field, code.get('origin', 'local_model')))
                label = ttk.Label(frame, text=f'{field.title()} (saved origin: {origin})' if first else field.title())
                label.grid(row=row * 2, column=0, sticky='w', padx=8)
                if first:
                    author_labels[field] = label
                editor = tk.Text(frame, height=2 if field == 'label' else 4, wrap='word')
                editor.insert('1.0', initial.get(field, ''))
                editor.grid(row=1 + row * 2, column=0, sticky='ew', padx=8, pady=(0, 8))
                fields[field] = editor
            ttk.Label(frame, text='Support IDs for split only (comma-separated, same target)').grid(row=6, column=0, sticky='w', padx=8)
            span_ids = tk.StringVar(window, value=', '.join(code['support_span_ids']))
            ttk.Entry(frame, textvariable=span_ids).grid(row=7, column=0, sticky='ew', padx=8)
            return frame, fields, span_ids
        _, editors, first_spans = wording_tab('Candidate / first part', code, first=True)
        checks_frame = ttk.Frame(book); checks_frame.columnconfigure(0, weight=1)
        book.add(checks_frame, text='Actor, status and limits')
        checks = {}
        check_labels = {
            'actor_perspective': 'Who expresses, acts or holds the reported perspective? Unknown is valid.',
            'action_status': 'Question, request, intention, possibility, observed action or outcome?',
            'conditions_frequency': 'What conditions, frequency or certainty must remain?',
            'context_use': 'What does context clarify, and what belongs to another speaker?',
            'remaining_uncertainty': 'Alternative readings, omissions or unresolved distinctions',
        }
        for row, (field, title) in enumerate(check_labels.items()):
            ttk.Label(checks_frame, text=title, wraplength=490).grid(row=2 * row, column=0, sticky='w', padx=8)
            checks[field] = tk.Text(checks_frame, height=2, wrap='word')
            checks[field].grid(row=2 * row + 1, column=0, sticky='ew', padx=8, pady=(0, 6))
        original = self._text(book); book.add(original, text='Original and history')
        self._replace(original, json.dumps({
            'original_model_prose': code.get('original_model_prose'),
            'split_origin_prose': code.get('split_origin_prose'),
            'saved_candidate': code,
            'revisions': [e for e in self.result.get('review_history', []) if e.get('code_id') == code_id],
            'review_records': {
                'decisions': [event for event in self.result.get('candidate_review_workflow', {}).get('decisions', []) if event.get('code_id') == code_id],
                'splits': [event for event in self.result.get('candidate_review_workflow', {}).get('splits', []) if event.get('parent_id') == code_id or code_id in event.get('child_ids', [])],
            },
        }, ensure_ascii=False, indent=2))
        source_frame = ttk.Frame(window)
        source_frame.grid(row=2, column=1, sticky='nsew', padx=(0, 12), pady=8)
        source_frame.columnconfigure(0, weight=1); source_frame.rowconfigure(1, weight=1)
        source_choice = ttk.Combobox(source_frame, state='readonly')
        source_choice.grid(row=0, column=0, columnspan=2, sticky='ew')
        source_preview = self._text(source_frame)
        source_preview.grid(row=1, column=0, sticky='nsew')
        scrollbar = ttk.Scrollbar(source_frame, command=source_preview.yview)
        scrollbar.grid(row=1, column=1, sticky='ns'); source_preview.configure(yscrollcommand=scrollbar.set)
        from .codebook_review import source_review_view, source_review_text
        try:
            view = source_review_view(self.result, code_id)
            entries = view['entries']
            source_choice.configure(values=self._source_labels_for(view))
            source_choice.current(0)
            def show_source(_event=None):
                index = source_choice.current()
                self._replace(source_preview, source_review_text(view, entries[index]['id']) if 0 <= index < len(entries) else 'Select target or context.')
            source_choice.bind('<<ComboboxSelected>>', show_source); show_source()
        except (ValueError, KeyError, TypeError) as error:
            self._replace(source_preview, f'Source view unavailable: {error}. Do not infer a source identity.')
        action_row = ttk.Frame(window)
        action_row.grid(row=3, column=0, columnspan=2, sticky='ew', padx=12)
        ttk.Label(action_row, text='Decision').pack(side='left')
        decision = tk.StringVar(window)
        ttk.Combobox(action_row, textvariable=decision, values=('accept', 'edit', 'reject', 'uncertain'), state='readonly', width=12).pack(side='left', padx=8)
        split_mode = tk.BooleanVar(window, value=False)
        second = {}
        def toggle_split():
            if split_mode.get() and not second:
                frame, fields, ids = wording_tab('Second part', {})
                second.update(frame=frame, fields=fields, ids=ids)
                book.select(frame)
            elif not split_mode.get() and second:
                book.forget(second['frame']); second['frame'].destroy(); second.clear()
        ttk.Checkbutton(action_row, text='Split compound candidate into two parts (each needs later review)', variable=split_mode, command=toggle_split).pack(side='left')
        ttk.Label(window, text='Required reason. Notes are reviewer interpretations. Authorship is self-declared. Split parts start with chosen support; assign any qualifications/counterexamples explicitly afterward. Empty roles do not prove absence.', wraplength=1080).grid(row=4, column=0, columnspan=2, sticky='w', padx=12, pady=(8, 0))
        rationale = tk.Text(window, height=3, wrap='word')
        rationale.grid(row=5, column=0, columnspan=2, sticky='ew', padx=12)
        notice = ttk.Label(window, text='', wraplength=1080)
        notice.grid(row=6, column=0, columnspan=2, sticky='w', padx=12)
        buttons = ttk.Frame(window); buttons.grid(row=7, column=0, columnspan=2, sticky='e', padx=12, pady=10)
        def apply():
            identity = dict(reviewer_kind=reviewer_kind.get(), reviewer_name=reviewer_name.get(),
                reason=rationale.get('1.0', 'end-1c'), expected_digest=before_digest)
            if split_mode.get():
                if not second:
                    notice.configure(text='Open the second part and supply its separate meaning.'); return
                parts = []
                for fields, ids in ((editors, first_spans), (second['fields'], second['ids'])):
                    part = {field: editor.get('1.0', 'end-1c') for field, editor in fields.items()}
                    part['support_span_ids'] = [sid.strip() for sid in ids.get().split(',') if sid.strip()]
                    parts.append(part)
                success = self.apply_candidate_split(code_id, parts, **identity,
                    checks={field: editor.get('1.0', 'end-1c') for field, editor in checks.items()
                            if editor.get('1.0', 'end-1c').strip()})
            else:
                changes = {field: editor.get('1.0', 'end-1c') for field, editor in editors.items()
                           if editor.get('1.0', 'end-1c') != code.get(field, '')}
                success = self.apply_candidate_review(code_id, changes, decision.get(), **identity,
                    checks={field: editor.get('1.0', 'end-1c') for field, editor in checks.items()
                            if editor.get('1.0', 'end-1c').strip()})
            if success:
                window.destroy()
            else:
                notice.configure(text=self.status.get())
        ttk.Button(buttons, text='Cancel', command=window.destroy).pack(side='left', padx=6)
        apply_button = ttk.Button(buttons, text='Apply review / split', command=apply)
        apply_button.pack(side='left')
        window.editors, window.rationale, window.apply_button = editors, rationale, apply_button
        window.author_labels = author_labels
        window.notice = notice
        window.reviewer_kind, window.reviewer_name, window.decision = reviewer_kind, reviewer_name, decision
        window.checks, window.split_mode, window.second_part = checks, split_mode, second
        window.source_choice, window.source_preview = source_choice, source_preview
        window.grab_set()
        return window

    def open_evidence_review(self):
        if not self._can_review_evidence():
            self.status.set('Select one defined code before category synthesis to review its frozen source evidence.')
            return None
        code_id = self.code_tree.selection()[0]
        code = self._codes[code_id]
        spans = {sid: span for sid, span in self.result['registry'].items() if span['unit_id'] == code['unit_id']}
        window = tk.Toplevel(self)
        window.title('Review source evidence')
        window.geometry('850x650')
        window.transient(self)
        window.columnconfigure(0, weight=1)
        window.rowconfigure(2, weight=1)
        ttk.Label(window, text=f"{code['label']}\nSelect 1..6 frozen spans from this same source turn. This records a citation revision, not semantic approval.", wraplength=800).grid(row=0, column=0, padx=12, pady=8, sticky='w')
        tree = ttk.Treeview(window, columns=('id', 'text'), show='headings', selectmode='extended', height=7)
        tree.heading('id', text='Frozen span ID'); tree.heading('text', text='Exact source text')
        tree.column('id', width=220); tree.column('text', width=570)
        tree.grid(row=1, column=0, sticky='ew', padx=12)
        for sid, span in spans.items():
            tree.insert('', tk.END, iid=sid, values=(sid, span['text']))
        tree.selection_set(code['support_span_ids'])
        preview = self._text(window)
        preview.grid(row=2, column=0, sticky='nsew', padx=12, pady=8)
        def show_quotes(_event=None):
            self._replace(preview, '\n\n'.join(f"{sid}\nExact quote:\n{spans[sid]['text']}" for sid in tree.selection()))
        tree.bind('<<TreeviewSelect>>', show_quotes)
        show_quotes()
        reviewer_name, reviewer_kind = self._reviewer_controls(window, 3, columnspan=1)
        ttk.Label(window, text='Required rationale for this evidence selection revision:').grid(row=4, column=0, sticky='w', padx=12)
        rationale = tk.Text(window, height=3, wrap='word')
        rationale.grid(row=5, column=0, sticky='ew', padx=12)
        notice = ttk.Label(window, text='', wraplength=800)
        notice.grid(row=6, column=0, sticky='w', padx=12)
        buttons = ttk.Frame(window); buttons.grid(row=7, column=0, sticky='e', padx=12, pady=10)
        def apply():
            if not reviewer_name.get().strip() or reviewer_kind.get() not in ('researcher', 'assistant_development_review'):
                notice.configure(text='Supply reviewer name and choose authorship. AI edits are not researcher review.'); return
            if self.revise_evidence(code_id, list(tree.selection()), rationale.get('1.0', 'end-1c'),
                    reviewer_kind=reviewer_kind.get(), reviewer_name=reviewer_name.get()):
                window.destroy()
            else:
                notice.configure(text=self.status.get())
        ttk.Button(buttons, text='Cancel', command=window.destroy).pack(side='left', padx=6)
        apply_button = ttk.Button(buttons, text='Apply evidence revision', command=apply)
        apply_button.pack(side='left')
        # Actual modal controls are also inspectable by withdrawn Tk QA.
        window.span_tree, window.rationale, window.apply_button = tree, rationale, apply_button
        window.preview, window.notice = preview, notice
        window.reviewer_name, window.reviewer_kind = reviewer_name, reviewer_kind
        window.grab_set()
        return window

    def open_reviewed_category(self, *, reviewer_kind='researcher'):
        ids = self._selected_usable()
        if self._busy or self.result is None or not ids or not self.reviewed.get():
            self.status.set('Select and review 2 to 6 defined codes from at least two source contents before adding a category.')
            return None
        if reviewer_kind not in {'researcher', 'assistant_development_review'}:
            self.status.set('Explicit researcher or assistant development authorship is required.')
            return None
        from .reviewed_grouping_dialog import open_reviewed_category_editor
        return open_reviewed_category_editor(self, ids, reviewer_kind=reviewer_kind)

    def add_reviewed_category(self, code_ids, **fields):
        if self._busy or self.result is None or not self.reviewed.get():
            self.status.set('Review the selected source codes before adding a category.')
            return False
        if list(code_ids) != self._selected_usable():
            self.status.set('The selected members changed. Reopen the category editor.')
            return False
        try:
            from .reviewed_grouping import add_reviewed_category
            result = add_reviewed_category(self.result, code_ids, **fields)
        except (ValueError, TypeError, KeyError) as error:
            self.status.set(str(error))
            return False
        self.set_result(result)
        self.code_tree.selection_set(*code_ids)
        self._selection_changed()
        self.codebook_notebook.select(self.review_categories)
        self.status.set('Provisional category added with explicit authorship. Original model outcome and raw calls preserved; the review checkbox is reset.')
        return True

    def propose_categories(self, *, reviewer_kind="researcher"):
        if self._busy or self.result is None:
            return False
        if self._has_synthesis():
            self.status.set('Grouping is already recorded in this draft. Reopen the original pre-grouping draft to create a new review branch; prior outcomes and history are preserved.')
            return False
        ids = self._selected_usable()
        if not self.reviewed.get() or not ids:
            self.status.set("Review selected claims, definitions and evidence from at least two sources before proposing categories.")
            return False
        try:
            config = self._configuration()
        except ValueError as error:
            self.status.set(str(error)); return False
        return self._launch("synthesis", config, {"result": deepcopy(self.result), "code_ids": ids, "reviewer_kind": reviewer_kind})

    def _launch(self, operation, config, arguments):
        # Tk variable finalizers must run on the UI thread. Reclaim previously
        # destroyed dialogs before allocation-heavy source/model work begins.
        gc.collect()
        self._cancel_event = threading.Event()
        self._busy = True
        self.status.set("Starting bounded local inference…")
        self._update_actions()
        self._thread = threading.Thread(target=self._work, args=(operation, config, arguments), name="themeforge-local-codebook", daemon=False)
        self._thread.start()
        return True

    def _work(self, operation, config, arguments):
        client, result, error = None, None, None
        try:
            if self._cancel_event.is_set():
                raise RuntimeError("Cancelled before local client creation.")
            client = self._client_factory(config)
            with self._client_lock:
                self._client = client
            if self._cancel_event.is_set():
                raise RuntimeError("Cancelled before inference.")
            def progress(event):
                self._events.put(("progress", event))
            function = self._api().run_codebook if operation == "codes" else self._api().run_synthesis
            result = function(client=client, cancel_event=self._cancel_event, on_progress=progress, **arguments)
        except Exception as caught:
            error = f"{type(caught).__name__}: {caught}"
        finally:
            if client is not None:
                try:
                    client.close()
                except Exception as caught:
                    error = f"Local process cleanup failed: {caught}"
            with self._client_lock:
                self._client = None
            self._events.put(("error", error) if error else ("result", result))
            self._events.put(("stopped", None))

    def _drain_events(self):
        if self._destroyed:
            return
        try:
            while True:
                kind, value = self._events.get_nowait()
                if kind == "progress":
                    self.status.set("Local inference: " + (json.dumps(value, ensure_ascii=False) if isinstance(value, dict) else str(value))[:220])
                elif kind == "result" and not self._closing:
                    self.set_result(value)
                elif kind == "error":
                    self.status.set(("Cancelled. " if self._cancel_event.is_set() else "Local inference failed. ") + str(value))
                elif kind == "stopped":
                    self._busy = False
                    self._update_actions()
        except queue.Empty:
            pass
        if self._closing and (self._thread is None or not self._thread.is_alive()):
            self.destroy()
            return
        self._after_id = self.after(60, self._drain_events)

    def set_result(self, result):
        self.result = deepcopy(result)
        self._codes = {code["id"]: code for code in result.get("codes", [])}
        self.code_tree.delete(*self.code_tree.get_children())
        from .codebook_review import candidate_review_state
        for uid, code in self._codes.items():
            review = candidate_review_state(result, uid)
            author = review.get('reviewer_kind')
            review_label = review['state'] + (' (AI)' if author == 'assistant_development_review' else ' (researcher)' if author == 'researcher' else '')
            self.code_tree.insert("", tk.END, iid=uid, values=(code["label"], code.get("status", "unreviewed"), review_label))
        self.reviewed.set(False)
        category_status = result.get("synthesis_status", "No category proposal stage run.")
        category_reason = result.get("synthesis_reason", result.get("synthesis_error", ""))
        self._replace(self.categories, f"{category_status}\n{category_reason}\n\n" + json.dumps(result.get("categories", []), ensure_ascii=False, indent=2))
        from .reviewed_grouping_dialog import reviewed_categories_text
        self._replace(self.review_categories, reviewed_categories_text(result))
        themes = result.get("interpretive_themes", [])
        self._replace(self.themes, json.dumps(themes, ensure_ascii=False, indent=2) if themes else "No interpretive themes generated. Researcher synthesis remains separate.")
        unassigned = result.get("unassigned", [])
        self._replace(self.unassigned, "\n\n".join(f"{item.get('source_name', '')} / {item.get('unit_id', '')}\nReason: {item.get('reason', '')}\n{item.get('text', '')}" for item in unassigned) or "No unassigned passages reported.")
        selected = result.get("selected_unit_ids")
        if selected is not None:
            self.sample_note.set(f"Bounded sample: {len(selected)} selected turns; {len(unassigned)} unassigned or deferred passages remain in the Unassigned tab. This is not whole-corpus coding completion.")
        self.status.set(f"{result.get('synthesis_status', result.get('status', 'completed'))}: {len(self._codes)} retained candidates including split parents and unresolved decisions. Review is not semantic validation.")
        if self._codes:
            self.code_tree.selection_set(next(iter(self._codes)))
        self._selection_changed()
        self._update_actions()

    @staticmethod
    def _source_labels_for(view):
        values = []
        for entry in view['entries']:
            speaker = entry['source_speaker']
            label = speaker.get('source_local_label') or speaker.get('status', 'unknown')
            span = entry['span']
            values.append(f"{entry['kind'].upper()} | {label} | {span['source_start']}:{span['source_end']}")
        return values

    def _load_source_view(self, code_id):
        # Always clear the prior code's context, including on an unavailable view.
        self._source_view = None
        self._source_entry_ids = []
        self.source_choice.set('')
        self.source_choice.configure(values=())
        try:
            from .codebook_review import source_review_view
            view = source_review_view(self.result, code_id)
            self._source_view = view
            self._source_entry_ids = [entry['id'] for entry in view['entries']]
            self.source_choice.configure(values=self._source_labels_for(view))
            self.source_choice.current(0)
            self._source_changed()
            code = view['candidate']
            ancestors = {code_id}
            current = code
            while current.get('split_parent_id') and current['split_parent_id'] not in ancestors:
                ancestors.add(current['split_parent_id'])
                current = self._codes[current['split_parent_id']]
            provenance = deepcopy(view['provenance'])
            for value in provenance.values():
                if isinstance(value, dict) and isinstance(value.get('mappings'), list):
                    value['mappings'] = [row for row in value['mappings'] if row.get('native_code_id') in ancestors]
            calls = [call for call in self.result.get('calls', []) if call.get('unit_id') == code['unit_id']]
            self._replace(self.provenance_detail,
                'RECORDED SOURCE AND MODEL PROVENANCE\nOriginal raw calls are linked by source unit. A split child inherits the parent source; it was authored during review. Saved checker verdicts, if present, are historical experimental outputs and do not approve or hide candidates.\n\n' +
                json.dumps({'candidate_id': code_id, 'source_document': view['source_document_metadata'],
                    'recorded_provenance': provenance, 'original_calls_for_unit': calls,
                    'note': 'No unit-linked call is available; the complete JSON retains all recorded calls.' if not calls else 'Raw records are unchanged; revisions are recorded separately.'}, ensure_ascii=False, indent=2))
        except (ValueError, KeyError, TypeError) as error:
            self._replace(self.source_detail, f'Source view unavailable: {error}. Previous source context has been cleared.')
            self._replace(self.provenance_detail, 'Source provenance unavailable for this selection. Original JSON remains unchanged.')

    def _source_changed(self, _event=None):
        from .codebook_review import source_review_text
        index = self.source_choice.current()
        if self._source_view is None or not 0 <= index < len(self._source_entry_ids):
            self._replace(self.source_detail, 'Select a candidate and an exact target or context entry.')
            return
        self._replace(self.source_detail, source_review_text(self._source_view, self._source_entry_ids[index]))

    def _selection_changed(self, _event=None):
        self.reviewed.set(False)
        selection = self.code_tree.selection()
        if selection:
            code = self._codes[selection[0]]
            from .codebook_review import candidate_review_state
            review = candidate_review_state(self.result, code['id'])
            sections = [f"{field}: {code.get(field, '')}" for field in ("label", "claim", "definition", "inclusion", "exclusion", "origin", "scope_origin", "field_origins", "interpretation_field_origins", "original_model_prose", "split_origin_prose", "scope_boundary_status", "status", "review_status", "quality_flags", "risk_flags", "uncertainty", "evidence_selection_origin", "support_span_ids", "original_model_support_span_ids", "original_model_evidence_roles", "evidence_role_origins")]
            sections.insert(0, 'EXPLICIT REVIEW DECISION\n' + json.dumps(review, ensure_ascii=False, indent=2) +
                '\nAI decisions are not researcher approvals. Imported speaker metadata establishes attribution only; actors, addressees and meaning require interpretation.')
            if code.get('split_parent_id'):
                sections.insert(1, f"Split from {code['split_parent_id']}. Original parent remains accessible. These parts share one source turn, not independent participants.")
            for field in ('referent_context', 'meaning_checks', 'uncovered_spans'):
                if field in code:
                    sections.append(f'{field}:\n' + json.dumps(code[field], ensure_ascii=False, indent=2))
            meaning_api = importlib.import_module('themeforge.codebook_meaning')
            current_review = getattr(meaning_api, 'review_meaning', None)
            if current_review is not None:
                try:
                    warnings = json.dumps(current_review(self.result, code), ensure_ascii=False, indent=2)
                except (KeyError, ValueError, TypeError) as error:
                    warnings = f'Unavailable for this draft: {error}. Exact registry evidence is required.'
                sections.append('CURRENT REVIEW WARNINGS\nComputed for this view; saved legacy checks and digest are unchanged.\n' + warnings)
            from .drift_surfaces import legacy_drift_view
            sections.append('ADDITIONAL SOURCE DRIFT CUES\nReview assistance only; source meanings and saved drafts are unchanged.\n' +
                            json.dumps(legacy_drift_view(self.result,code),ensure_ascii=False,indent=2))
            uncovered = [span for span in self.result.get('uncovered_spans', []) if span.get('unit_id') == code.get('unit_id')]
            if uncovered:
                sections.append('UNCOVERED SOURCE SPANS FOR THIS TURN\nThese spans are not covered by the current candidate supports; this does not classify their meaning.\n' + json.dumps(uncovered, ensure_ascii=False, indent=2))
            sections.append('Meaning checks and coverage flags are review prompts; they do not prove interpretation accuracy.')
            history = [event for event in self.result.get('review_history', []) if event.get('code_id') == code['id']]
            if history:
                sections.append('EXPLICIT EVIDENCE REVIEW HISTORY / INTERPRETATION REVISIONS\nThese edits are reviewer actions, not raw model output.\n' + json.dumps(history, ensure_ascii=False, indent=2))
            for role in ("evidence", "qualifications", "counterexamples"):
                sections.append(f"\n{role.upper()}")
                for entry in code.get(role, []):
                    sections.append(f"{entry.get('source_name', '')} / saved speaker metadata: {entry.get('speaker', '')} chars {entry.get('source_start', '')}:{entry.get('source_end', '')}\nSHA256: {entry.get('source_hash', '')}\nExact quote:\n{entry.get('text', '')}\nFull target turn:\n{entry.get('unit_text', '')}\nSeparate reference context:\n{entry.get('reference_context', '')}\nSeparate source context:\n{entry.get('context_text', '')}\nUse the source pane for the explicit structural label and any ambiguity; saved labels do not establish claim meaning.")
            self._replace(self.detail, "\n\n".join(sections))
            self._load_source_view(code['id'])
        else:
            self._replace(self.detail, 'Select a retained candidate. No rejected or uncertain candidate is silently deleted.')
            self._replace(self.provenance_detail, 'Select a candidate to inspect its recorded source and original model output.')
            self._source_view = None
            self._source_entry_ids = []
            self.source_choice.configure(values=())
            self.source_choice.set('')
            self._source_changed()
        self._update_actions()

    def open_revision_branch(self):
        if self._busy or self.result is None:
            self.status.set('Wait for current work and load a draft first.')
            return None
        from .review_branch_dialog import ReviewBranchDialog
        return ReviewBranchDialog(self, self.result)

    def _update_actions(self):
        if hasattr(self, "branch_button"):
            self.branch_button.configure(state="normal" if self.result is not None and not self._busy else "disabled")
        self.source_label_choice.configure(state="disabled" if self._busy else "readonly")
        self.run_button.configure(state="disabled" if self._busy else "normal")
        self.open_button.configure(state="disabled" if self._busy else "normal")
        self.choose_passages_button.configure(state='disabled' if self._busy else 'normal')
        self.automatic_sample_button.configure(state='disabled' if self._busy else 'normal')
        self.evidence_button.configure(state="normal" if self._can_review_evidence() else "disabled")
        self.evidence_roles_button.configure(state='normal' if self._can_review_evidence() else 'disabled')
        self.interpretation_button.configure(state='normal' if self._can_review_evidence(allow_incomplete=True) else 'disabled')
        self.cancel_button.configure(state="normal" if self._busy else "disabled")
        usable = self._selected_usable()
        self.category_button.configure(state="normal" if not self._busy and usable and self.reviewed.get() and not self._has_synthesis() else "disabled")
        self.review_category_button.configure(state='normal' if not self._busy and usable and self.reviewed.get() else 'disabled')
        for button in (self.json_button, self.html_button):
            button.configure(state="normal" if self.result is not None and not self._busy else "disabled")
        if self._has_synthesis():
            self.gate_note.set('Grouping is already recorded. You may add categories during review without changing the model outcome. Reopen the original pre-grouping draft to revise codes or run another model branch.')
        else:
            from .codebook_review import grouping_review_issue
            issues = [grouping_review_issue(self.result, cid) for cid in self.code_tree.selection()] if self.result else []
            issue = next((item for item in issues if item), None)
            self.gate_note.set(issue or (f"{len(usable)} selected defined codes across distinct source texts. Category proposals remain provisional." if usable else "Select defined codes from at least two distinct source texts, then review their claims, definitions and evidence."))

    def cancel(self):
        self._cancel_event.set()
        self.status.set("Cancelling; waiting for the owned local process to close…")

    def destroy(self):
        from .review_branch_dialog import close_review_branches
        if not close_review_branches(self):
            return
        super().destroy()

    def request_close(self):
        if self._busy or (self._thread is not None and self._thread.is_alive()):
            self._closing = True
            self.cancel()
        else:
            self.destroy()

    def _on_destroy(self, event):
        if event.widget is not self:
            return
        self._destroyed = True
        self._cancel_event.set()
        if self._after_id:
            try:
                self.after_cancel(self._after_id)
            except tk.TclError:
                pass
        with self._client_lock:
            client = self._client
        if client is not None:
            try:
                client.close()
            except Exception as error:
                self._cleanup_error = str(error)

    def export(self, format, path=None):
        if self.result is None or self._busy:
            return False
        path = path or filedialog.asksaveasfilename(parent=self, title="Save candidate codebook with review history", defaultextension="." + format,
                                                   filetypes=[(format.upper(), "*." + format)])
        if not path:
            return False
        try:
            if format not in ('json', 'html'):
                raise ValueError('Choose JSON for reopening or HTML for reading.')
            if 'result_sha256' in self.result or 'candidate_review_workflow' in self.result:
                validate_result_sources(self.result)
            content = self._api().export_codebook_html(self.result) if format == "html" else json.dumps(self.result, ensure_ascii=False, indent=2, allow_nan=False)
            encoded = content.encode('utf-8')
            if format != 'html' and len(encoded) > 16 * 1024 * 1024:
                raise ValueError('Native JSON exceeds the 16 MiB reopen limit. No file was created.')
            # Write the measured bytes exactly, including LF, on Windows too.
            with Path(path).open("xb") as stream:
                stream.write(encoded)
            self.status.set(f"Exported candidate codebook and retained review history to {Path(path).name}")
            return True
        except (OSError, ValueError, TypeError) as error:
            self.status.set(f"Export failed: {error}")
            return False

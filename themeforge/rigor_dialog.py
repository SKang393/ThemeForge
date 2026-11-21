from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, simpledialog, ttk
from tkinter.scrolledtext import ScrolledText
import zipfile

from .analysis_types import AnalysisResult, CodebookEntry, Theme, ThemeQuote, TranscriptDocument
from .audit import AuditEvent, export_audit_events_csv
from .decision_history import DecisionRow, decision_history_csv, decision_rows
from .exports import export_quotes_csv
from .manual_record_comparison import compare_recorded_assignments, current_assignment_csv, manual_assignment_links
from .match_reviews import MatchReviews, REVIEW_ACTION
from .paired_reviews import PairedReviewReport, compare_match_reviews
from .project_io import UnsupportedProjectFormatError, load_project
from .source_location import TranscriptIndex
from .rigor import (
    CodedProject,
    CoderLabels,
    DocumentTextMismatchError,
    IntercoderReliabilityReport,
    ManualDefinitionComparisonError,
    MatrixCell,
    MatrixQueryReport,
    NoSharedDocumentsError,
    intercoder_reliability,
    matrix_queries,
)
from .rigor_exports import export_disagreements_csv, export_matrix_csv, export_reliability_csv


@dataclass(frozen=True, slots=True)
class RigorContext:
    documents: Sequence[TranscriptDocument]
    result: AnalysisResult | None
    audit_events: Sequence[AuditEvent]
    coder_name: str
    codebook_entries: Sequence[CodebookEntry] = ()


class ReviewHistoryScope(simpledialog.Dialog):
    def __init__(self, parent: tk.Misc, source: str, reviewer: str, rows: Sequence[DecisionRow]):
        self.rows = rows
        self.source = source
        self.reviewer = reviewer
        self.output = ""
        super().__init__(parent, "Choose decisions for a history CSV")

    def body(self, master):
        master.columnconfigure(0, weight=1)
        master.rowconfigure(3, weight=1)
        ttk.Label(master, wraplength=520, text=f"Decisions by {self.reviewer} in {self.source}, in recorded order. "
                  "Select each decision to share, including any earlier support and subsequent withdrawal.").grid(
            row=0, column=0, columnspan=2, sticky="w")
        self.choices = tk.Listbox(master, selectmode=tk.MULTIPLE, exportselection=False, height=6)
        self.choices.grid(row=1, column=0, sticky="ew")
        for row in self.rows:
            self.choices.insert(tk.END, f"Line {row.source_line} [{row.source_start}, {row.source_end}) · "
                                       f"{row.recorded_decision} · {row.recorded_theme}")
        scroll = ttk.Scrollbar(master, command=self.choices.yview)
        scroll.grid(row=1, column=1, sticky="ns")
        self.choices.configure(yscrollcommand=scroll.set)
        self.choices.bind("<<ListboxSelect>>", self.show_preview)
        ttk.Label(master, text="CSV preview · Selected judgments may be superseded. "
                  "Selected quotes, theme labels and free-text reasons can identify people. Read them before sharing.",
                  wraplength=520).grid(row=2, column=0, columnspan=2, sticky="w", pady=(8, 2))
        self.preview = ScrolledText(master, height=8, width=64, wrap="word", state="disabled")
        self.preview.grid(row=3, column=0, columnspan=2, sticky="nsew")
        self.show_preview()
        return self.choices

    def show_preview(self, _event=None):
        indices = self.choices.curselection()
        self.output = decision_history_csv(self.rows[i] for i in indices) if indices else ""
        self.preview.configure(state="normal")
        self.preview.delete("1.0", tk.END)
        self.preview.insert("1.0", self.output or "Choose at least one decision to preview.")
        self.preview.configure(state="disabled")

    def validate(self):
        previous = self.output
        self.show_preview()
        return bool(self.output) and self.output == previous

    def apply(self):
        self.result = self.output


class PairedExportScope(simpledialog.Dialog):
    def __init__(self, parent: tk.Misc, report: PairedReviewReport):
        self.counts = Counter(row.quote.source_name for row in report.matches)
        self.definition_exclusions = Counter(row.quote.source_name for row in report.matches
                                             if row.outcome == "different_definitions")
        self.report = report
        self.preview = tk.StringVar(parent)
        self.counts_only = tk.BooleanVar(parent, value=False)
        super().__init__(parent, "Choose sources for the paired CSV")

    def body(self, master):
        master.columnconfigure(0, weight=1)
        ttk.Label(master, text=f"Paired reviews by {self.report.reviewers.coder_a} and {self.report.reviewers.coder_b}",
                  wraplength=480).grid(row=0, column=0, sticky="w")
        ttk.Label(master, text="Click a source to include or remove its shared matches.").grid(
            row=1, column=0, sticky="w", pady=(6, 2))
        self.sources = tk.Listbox(master, selectmode=tk.MULTIPLE, exportselection=False,
                                  height=min(8, len(self.counts)))
        self.sources.grid(row=2, column=0, sticky="ew")
        self.sources.insert(tk.END, *sorted(self.counts))
        vertical = ttk.Scrollbar(master, orient="vertical", command=self.sources.yview)
        vertical.grid(row=2, column=1, sticky="ns")
        horizontal = ttk.Scrollbar(master, orient="horizontal", command=self.sources.xview)
        horizontal.grid(row=3, column=0, sticky="ew")
        self.sources.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self.sources.bind("<<ListboxSelect>>", self.show_preview)
        ttk.Button(master, text="Select all sources", command=self.select_all).grid(row=4, column=0, sticky="w", pady=4)
        ttk.Checkbutton(master, text="Export outcome counts only", variable=self.counts_only,
                        command=self.show_preview).grid(row=5, column=0, sticky="w", pady=(0, 4))
        ttk.Label(master, textvariable=self.preview, wraplength=480).grid(row=6, column=0, sticky="w")
        self.warning = ttk.Label(master, wraplength=480)
        self.warning.grid(row=7, column=0, sticky="w", pady=(6, 0))
        self.show_preview()
        return self.sources

    def select_all(self):
        self.sources.selection_set(0, tk.END)
        self.show_preview()

    def selected_names(self) -> frozenset[str]:
        return frozenset(self.sources.get(i) for i in self.sources.curselection())

    def show_preview(self, _event=None):
        selected = self.selected_names()
        rows = sum(self.counts[name] for name in selected)
        if self.counts_only.get():
            self.preview.set(f"The totals will cover {rows} of {self.report.shared} shared matches from "
                             f"{len(selected)} of {len(self.counts)} sources.")
            self.warning.configure(text="The CSV contains only outcome labels and counts pooled across the selected "
                                   "sources. Small counts can still identify people in context.")
        else:
            self.preview.set(f"The CSV will include {rows} of {self.report.shared} paired rows from "
                             f"{len(selected)} of {len(self.counts)} sources.")
            self.warning.configure(text="The CSV includes all review outcomes for the highlighted sources. Excerpts, "
                                   "labels, names and either reader’s free-text reasons can identify people or refer "
                                   "to other transcripts. Check these fields before sharing.")
        excluded = sum(self.definition_exclusions[name] for name in selected)
        if excluded:
            self.preview.set(f"{self.preview.get()} Comparable pairs ({rows - excluded} of {rows}) · "
                             f"Different codebook entries ({excluded})")

    def validate(self):
        if self.selected_names():
            return True
        self.preview.set("Choose at least one source. The CSV preview is empty.")
        return False

    def apply(self):
        self.result = self.selected_names(), self.counts_only.get()


class RigorDialog(tk.Toplevel):
    def __init__(
        self,
        master: tk.Misc,
        context: RigorContext,
        context_source: Callable[[], RigorContext] | None = None,
        locate_match: Callable[[Theme, ThemeQuote], None] | None = None,
    ) -> None:
        super().__init__(master)
        self.context = context
        self._context_source = context_source
        self._locate_match = locate_match
        self._source_review_matches: dict[str, tuple[Theme, ThemeQuote]] = {}
        self.source_review_hint = tk.StringVar(self)
        self.my_manual_assignments = tk.BooleanVar(self, value=False)
        self._last_disagreement: str | None = None
        self.report: IntercoderReliabilityReport | None = None
        self._comparison_project: CodedProject | None = None
        self._comparison_label = ""
        self.status = tk.StringVar(value="Choose a saved project to compare.")
        self.audit_status = tk.StringVar()
        self.assignment_status = tk.StringVar(value="This project's manual codebook CSV includes current assignments that match their "
                                               "original records. It excludes suggestions and freehand codes.")
        self.title("Rigor tools")
        self.minsize(900, 600)
        self.transient(master)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self._build()
        self.bind("<FocusIn>", self.refresh_current_state, add="+")

    def _build(self) -> None:
        self.matrix_report = (
            matrix_queries(self.context.documents, self.context.result)
            if self.context.result is not None
            else MatrixQueryReport((), ())
        )
        notebook = ttk.Notebook(self)
        notebook.grid(row=0, column=0, sticky="nsew", padx=12, pady=12)
        self.source_matrix_tree = self._add_matrix_tab(notebook, "By source", "source")
        self.speaker_matrix_tree = self._add_matrix_tab(notebook, "By speaker", "speaker")
        self._add_intercoder_tab(notebook)
        self._add_audit_tab(notebook)
        self._render_current_state()

    def _add_matrix_tab(
        self,
        notebook: ttk.Notebook,
        title: str,
        axis_label: str,
    ) -> ttk.Treeview:
        tab = ttk.Frame(notebook, padding=10)
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(1, weight=1)
        actions = ttk.Frame(tab)
        actions.grid(row=0, column=0, sticky="w", pady=(0, 8))
        ttk.Button(actions, text="Export matrix CSV", command=lambda: self._export_matrix(axis_label)).pack(side="left")
        if axis_label == "source":
            ttk.Button(actions, text="Export this project's manual codebook assignments",
                       command=self._export_current_assignments).pack(side="left", padx=8)
            ttk.Checkbutton(actions, text="Show only my manual codebook assignments",
                            variable=self.my_manual_assignments, command=self._render_source_matrix).pack(side="left")
            ttk.Label(tab, textvariable=self.source_review_hint, wraplength=800).grid(row=2, column=0, sticky="w")
            ttk.Label(tab, textvariable=self.assignment_status, wraplength=780).grid(row=3, column=0, sticky="w")
            tree = ttk.Treeview(tab, columns=("source", "reviews", "quotes", "chars", "coverage", "match_link"),
                                selectmode="browse")
            tree.column("#0", width=210)
            self._configure_tree(tree, ("Transcript", "Match reviews", "Stored quotes", "Covered chars", "Coverage %",
                                        "Direct match link"))
            tree.column("reviews", width=230)
            for col in ("quotes", "chars", "coverage"):
                tree.column(col, width=90)
            tree.bind("<Double-1>", self._open_source_review)
            tree.bind("<Return>", self._open_source_review)
        else:
            tree = ttk.Treeview(tab, columns=("theme", axis_label, "quotes", "chars", "coverage"), show="headings")
            self._configure_tree(tree, ("Theme", "Speaker label", "Stored quotes", "Unique coded chars", "Coverage %"))
            ttk.Label(tab, wraplength=800, text=(
                "Matching speaker labels are pooled across transcripts. Different people can share a label. "
                "Stored quotes include suggestions. Use By source to trace reviewed passages to the cases you defined."
            )).grid(row=2, column=0, sticky="w")
        self._grid_tree(tab, tree, 1)
        notebook.add(tab, text=title)
        return tree

    def _add_intercoder_tab(self, notebook: ttk.Notebook) -> None:
        tab = ttk.Frame(notebook, padding=10)
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(2, weight=1)
        tab.rowconfigure(4, weight=1)
        actions = ttk.Frame(tab)
        actions.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ttk.Button(actions, text="Choose comparison project", command=self._choose_comparison).grid(row=0, column=0, sticky="w")
        ttk.Button(actions, text="Export recorded manual assignments", command=self._export_manual_records).grid(
            row=0, column=1, sticky="w", padx=8)
        reviews = ttk.Frame(actions)
        reviews.grid(row=1, column=0, columnspan=3, sticky="w", pady=6)
        ttk.Label(reviews, text="Current reviewer").pack(side="left")
        self.current_reviewer = ttk.Combobox(reviews, state="readonly", width=16)
        self.current_reviewer.pack(side="left", padx=(4, 12))
        ttk.Label(reviews, text="Comparison reviewer").pack(side="left")
        self.comparison_reviewer = ttk.Combobox(reviews, state="readonly", width=16)
        self.comparison_reviewer.pack(side="left", padx=(4, 12))
        ttk.Button(reviews, text="Export paired match reviews", command=self._export_paired_reviews).pack(side="left")
        if self._locate_match is not None:
            ttk.Button(actions, text="Next review disagreement", command=self._next_review_disagreement).grid(row=0, column=2)
        for box in (self.current_reviewer, self.comparison_reviewer):
            box.bind("<<ComboboxSelected>>", self._clear_review_location)
        ttk.Label(actions, wraplength=800, text="The comparison on screen covers all shared sources. Choose sources "
                  "when exporting paired reviews. Check excerpts, names and both readers’ reasons before sharing.").grid(
            row=2, column=0, columnspan=3, sticky="w", pady=(0, 6))
        ttk.Label(actions, text="Quote coverage by meaning unit").grid(row=3, column=0, sticky="w")
        ttk.Button(actions, text="Export coverage statistics", command=self._export_reliability).grid(row=3, column=1)
        ttk.Button(actions, text="Export coverage differences", command=self._export_disagreements).grid(row=3, column=2)
        summary = ttk.Frame(tab)
        summary.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        ttk.Label(summary, textvariable=self.status, wraplength=825).pack(anchor="w")
        self.dispute_text = ScrolledText(summary, height=6, width=1, wrap="word", state="disabled")
        self.reliability_tree = ttk.Treeview(
            tab,
            columns=("theme", "both", "current", "comparison", "neither", "agreement", "kappa", "warning"),
            show="headings",
        )
        self._configure_tree(
            self.reliability_tree,
            ("Theme", "Both", "Current only", "Comparison only", "Neither", "Agreement %", "Kappa", "Warning"),
        )
        self._grid_tree(tab, self.reliability_tree, 2)
        ttk.Label(tab, text="Quote coverage differences").grid(row=3, column=0, sticky="w", pady=(10, 6))
        self.disagreement_tree = ttk.Treeview(
            tab,
            columns=("source", "speaker", "text", "current", "comparison"),
            show="headings",
        )
        self._configure_tree(self.disagreement_tree, ("Source", "Speaker", "Unit text", "Current coder", "Comparison coder"))
        self._grid_tree(tab, self.disagreement_tree, 4)
        notebook.add(tab, text="Intercoder")

    def _add_audit_tab(self, notebook: ttk.Notebook) -> None:
        tab = ttk.Frame(notebook, padding=10)
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(2, weight=1)
        handoff = ttk.LabelFrame(tab, text="Scoped exports", padding=6)
        handoff.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        handoff.columnconfigure(0, weight=1)
        ttk.Label(handoff, wraplength=780, text="Choose transcripts for supported evidence, or one transcript for review history. "
                  "Click a name to include or remove it.").grid(
            row=0, column=0, columnspan=3, sticky="w")
        self.supported_sources = tk.Listbox(handoff, height=3, selectmode=tk.MULTIPLE, exportselection=False)
        self.supported_sources.grid(row=1, column=0, sticky="ew")
        scrollbar = ttk.Scrollbar(handoff, orient=tk.VERTICAL, command=self.supported_sources.yview)
        scrollbar.grid(row=1, column=1, sticky="ns")
        self.supported_sources.configure(yscrollcommand=scrollbar.set)
        ttk.Button(handoff, text="Select all sources", command=lambda: self.supported_sources.selection_set(0, tk.END)).grid(
            row=1, column=2, padx=6)
        ttk.Label(handoff, wraplength=780, text="The supported table includes exact matches whose latest review across coders is supported. "
                  "Excerpts, source and speaker names, theme labels, reviewer names and free-text reasons can identify "
                  "people. Check these fields before sharing.").grid(row=2, column=0, columnspan=3, sticky="w", pady=4)
        ttk.Button(handoff, text="Export supported matches CSV", command=self._export_supported_matches).grid(
            row=3, column=0, sticky="w")
        ttk.Button(handoff, text="Choose review history…", command=self._export_review_history).grid(
            row=3, column=1, columnspan=2, sticky="e")
        actions = ttk.Frame(tab)
        actions.grid(row=1, column=0, sticky="w", pady=(0, 8))
        ttk.Button(actions, text="Export audit CSV", command=self._export_audit).pack(side="left")
        ttk.Label(actions, wraplength=600, text="Full project history for all sources and coders, including earlier, "
                  "declined and deferred excerpts, reasons and edit details.").pack(side="left", padx=8)
        self.audit_tree = ttk.Treeview(tab, columns=("timestamp", "actor", "action", "target", "details"), show="headings")
        self._configure_tree(self.audit_tree, ("Timestamp", "Actor", "Action", "Target", "Details"))
        self._grid_tree(tab, self.audit_tree, 2)
        ttk.Label(tab, textvariable=self.audit_status, wraplength=780).grid(row=3, column=0, sticky="w")
        notebook.add(tab, text="Audit log")

    def refresh_current_state(self, _event: tk.Event | None = None) -> None:
        current = self._context_source() if self._context_source else self.context
        if _event is not None and self.context == current:
            return  # Moving keyboard focus within the dialog should preserve expanded rows.
        if self.context != current:
            self._clear_review_location()
        self.context = current
        if self.context.result is None:
            self.matrix_report = MatrixQueryReport((), ())
            self._render_current_state()
            self.report = None
            self._comparison_project = None
            self._comparison_label = ""
            self.comparison_reviewer.configure(values=())
            self.comparison_reviewer.set("")
            self._render_intercoder()
            self.status.set("Current project has no analysis result.")
            return
        self.matrix_report = matrix_queries(self.context.documents, self.context.result)
        self._render_current_state()
        self._refresh_comparison()

    def _render_current_state(self) -> None:
        _reviewer_choices(self.current_reviewer, self.context.coder_name, self.context.audit_events)
        self._render_source_matrix()
        self._render_matrix(self.speaker_matrix_tree, self.matrix_report.speaker_rows)
        self._render_audit()

    def _render_source_matrix(self) -> None:
        tree = self.source_matrix_tree
        self._clear_tree(tree)
        self._source_review_matches.clear()
        reviewer = self.context.coder_name.strip()
        if self.my_manual_assignments.get():
            tree.configure(displaycolumns=("source", "reviews", "match_link"))
            tree.heading("#0", text="Code / assigned passage")
            hint = self._render_manual_sources(reviewer)
            self.source_review_hint.set(f"{hint} Both CSV buttons keep their project-wide scope.")
            return
        tree.configure(displaycolumns=("source", "reviews", "quotes", "chars", "coverage"))
        tree.heading("#0", text="Theme / reviewed passage")
        hint = (f"Match reviews by {reviewer}. Historical decisions in this pass under a changed or removed entry "
                "are excluded from current support. Expand a row and double-click a passage or press Enter for its "
                "quote and reason." if reviewer else
                "Enter a coder name in the main window to see that reader’s match reviews.")
        self.source_review_hint.set(f"One summary row per theme and transcript. {hint} Counts of stored quotes and "
                                    "coverage include suggestions. A decline can record a counterexample or an irrelevant "
                                    "match. For repeat visits, check your case mapping in Document memo after opening the passage.")
        result = self.context.result
        if result is None:
            return
        sources = TranscriptIndex(self.context.documents)
        reviews = MatchReviews(self.context.audit_events, reviewer=reviewer, codebook_entries=self.context.codebook_entries)
        by_source = defaultdict(list)
        if reviewer:
            for index, theme in enumerate(result.themes):
                for quote in theme.quotes:
                    note = reviews.for_navigation(theme, quote, sources, reviewer=reviewer)
                    if note:
                        by_source[index, quote.source_name].append((quote, note.disposition))
        for row in self.matrix_report.source_rows:
            theme = result.themes[row.theme_index]
            matches = by_source[row.theme_index, row.axis_value]
            counts = Counter(status for _, status in matches)
            summary = ", ".join(f"{counts[s]} {s}" for s in ("supported", "declined", "deferred") if counts[s])
            historical = bool(matches) and not reviews.definition_is_current(theme)
            if historical:
                summary = f"{len(matches)} historical review{'s' if len(matches) != 1 else ''}"
            elif not summary:
                summary = "No current reviews" if reviewer else "Choose a coder"
            parent = tree.insert("", tk.END, text=f"{theme.name} ({theme.id})", values=(
                row.axis_value, summary, row.quote_count, row.covered_chars, f"{row.coverage_ratio * 100:.2f}"))
            for quote, status in sorted(matches, key=lambda pair: pair[0].source_start):
                self._insert_source_passage(parent, theme, quote, f"Historical: {status}" if historical else status)

    def _insert_source_passage(self, parent: str, theme: Theme, quote: ThemeQuote, status: str) -> str:
        location = f"Line {quote.source_line} [{quote.source_start}, {quote.source_end})"
        item = self.source_matrix_tree.insert(parent, tk.END, text=location, values=(quote.source_name, status))
        self._source_review_matches[item] = theme, quote
        return item

    def _render_manual_sources(self, reviewer: str) -> str:
        result = self.context.result
        if not reviewer or result is None:
            return "Open a coded study and enter your coder name to see your manual assignments."
        project = CodedProject(self.context.documents, result, self.context.audit_events)
        try:
            links = manual_assignment_links(project, reviewer)
        except ValueError as exc:
            return f"Manual assignments could not be verified: {exc}"
        if not links:
            return f"No current manual codebook assignments recorded by {reviewer}."
        sources = TranscriptIndex(project.documents)
        reviews = MatchReviews(project.audit_events, reviewer=reviewer, codebook_entries=self.context.codebook_entries)
        tree = self.source_matrix_tree
        for theme in result.themes:
            by_source = defaultdict(list)
            for quote in theme.quotes:
                if quote.manual_definition_assignment and quote.quote_id in links:
                    by_source[quote.source_name].append(quote)
            for source, quotes in sorted(by_source.items()):
                current = reviews.definition_is_current(theme)
                definition = "Current definition" if current else "Earlier definition"
                parent = tree.insert("", tk.END, text=f"{theme.name} ({theme.id})", values=(source, definition), open=True)
                for quote in sorted(quotes, key=lambda q: q.source_start):
                    note = reviews.for_navigation(theme, quote, sources, reviewer=reviewer)
                    if current:
                        status = note.disposition if note else "No current review"
                    else:
                        status = f"Historical: {note.disposition}" if note else "No review this pass"
                    item = self._insert_source_passage(parent, theme, quote, status)
                    tree.set(item, "match_link", "Recorded" if links[quote.quote_id] else "None recorded")
        return (f"Assignments by {reviewer}. Double-click a passage or press Enter to open it. "
                "A direct link records use of Apply definition to this quote. With no link, the discovery route is unknown.")

    def _open_source_review(self, event: tk.Event) -> str | None:
        tree = self.source_matrix_tree
        item = tree.identify_row(event.y) if event.num == 1 else tree.focus()
        match = self._source_review_matches.get(item)
        if match is None or self._locate_match is None:
            return None  # Keep the normal expand/collapse behavior on summary rows.
        if self._context_source and self._context_source() != self.context:
            self.refresh_current_state()
            self.source_review_hint.set("The study or coder changed. Expand a row and choose the passage again.")
            return "break"
        try:
            self._locate_match(*match)
        except ValueError as exc:
            self.source_review_hint.set(str(exc))
        else:
            self.withdraw()  # Reopening Rigor tools returns to the expanded source row.
        return "break"

    def _render_matrix(self, tree: ttk.Treeview, rows: Sequence[MatrixCell]) -> None:
        self._clear_tree(tree)
        for row in rows:
            tree.insert(
                "",
                tk.END,
                values=(row.theme_name, row.axis_value, row.quote_count, row.covered_chars, f"{row.coverage_ratio * 100:.2f}"),
            )

    def _refresh_supported_sources(self, documents: Sequence[TranscriptDocument]) -> None:
        names = tuple(dict.fromkeys(doc.name for doc in documents))
        if self.supported_sources.get(0, tk.END) != names:
            self.supported_sources.delete(0, tk.END)
            self.supported_sources.insert(tk.END, *names)

    def _render_audit(self) -> None:
        self._refresh_supported_sources(self.context.documents)
        self._clear_tree(self.audit_tree)
        for event in self.context.audit_events:
            self.audit_tree.insert(
                "",
                tk.END,
                values=(event.timestamp_utc, event.actor, event.action, f"{event.target_type}:{event.target_id}", event.details),
            )

    def _choose_comparison(self) -> None:
        chosen = filedialog.askopenfilename(
            parent=self,
            title="Choose comparison ThemeForge project",
            filetypes=[("ThemeForge project", "*.tfproj"), ("All files", "*.*")],
        )
        if not chosen:
            return
        self._clear_review_location()
        self.report = None
        self._comparison_project = None
        self._comparison_label = ""
        self.comparison_reviewer.configure(values=())
        self.comparison_reviewer.set("")
        self._render_intercoder()
        self.status.set("")
        path = Path(chosen)
        try:
            state = load_project(path)
            if state.result is None:
                self.status.set("Comparison project has no analysis result.")
                return
            self._comparison_project = CodedProject(state.documents, state.result, state.audit_events, state.settings.codebook_entries)
            self._comparison_label = _label(state.researcher_name, path.stem)
            _reviewer_choices(self.comparison_reviewer, state.researcher_name, state.audit_events)
        except (UnsupportedProjectFormatError, OSError, zipfile.BadZipFile, KeyError, TypeError, ValueError) as exc:
            self.status.set(f"Could not load comparison project: {exc}")
            return
        if self._refresh_comparison():
            self.status.set(f"Compared {path.name}: {self.report.unit_count if self.report else 0} shared unit(s).")

    def _refresh_comparison(self) -> bool:
        if self._comparison_project is None:
            return False
        try:
            self.report = intercoder_reliability(
                CodedProject(self.context.documents, self.context.result),
                self._comparison_project,
                CoderLabels(_label(self.context.coder_name, "Current coder"), self._comparison_label),
            )
        except (DocumentTextMismatchError, NoSharedDocumentsError, ManualDefinitionComparisonError) as exc:
            self.report = None
            self._render_intercoder()
            self.status.set(f"Quote coverage unavailable. {exc}")
            return False
        self._render_intercoder()
        return True

    def _render_intercoder(self) -> None:
        self._clear_tree(self.reliability_tree)
        self._clear_tree(self.disagreement_tree)
        if self.report is None:
            return
        for row in self.report.theme_rows:
            kappa = "Undefined" if row.kappa_undefined else f"{row.cohens_kappa:.6f}"
            self.reliability_tree.insert(
                "",
                tk.END,
                values=(row.theme_name, row.both_present, row.a_only, row.b_only, row.neither, f"{row.percent_agreement * 100:.2f}", kappa, row.prevalence_warning),
            )
        for record in self.report.disagreements:
            self.disagreement_tree.insert(
                "",
                tk.END,
                values=(record.source_name, record.speaker, record.text, _yes_no(record.coder_a_present), _yes_no(record.coder_b_present)),
            )

    def _export_reliability(self) -> None:
        self.refresh_current_state()
        if self.report is None:
            self.status.set("Choose a comparison project before exporting reliability.")
            return
        self._export_text(export_reliability_csv(self.report), "quote-coverage-statistics.csv")

    def _export_disagreements(self) -> None:
        self.refresh_current_state()
        if self.report is None:
            self.status.set("Choose a comparison project before exporting disagreements.")
            return
        self._export_text(export_disagreements_csv(self.report), "quote-coverage-differences.csv")

    def _paired_reviews(self, *, source_names: frozenset[str] | None = None) -> PairedReviewReport | None:
        context = self._context_source() if self._context_source else self.context
        if context.result is None or self._comparison_project is None:
            self.status.set("Choose a comparison project for paired match reviews.")
            return None
        selected = self.current_reviewer.get()
        _reviewer_choices(self.current_reviewer, context.coder_name, context.audit_events)
        if self.current_reviewer.get() != selected:
            self.status.set("The available reviewers changed. Check the reviewer names and try again.")
            return None
        try:
            return compare_match_reviews(
                CodedProject(context.documents, context.result, context.audit_events, context.codebook_entries), self._comparison_project,
                CoderLabels(self.current_reviewer.get(), self.comparison_reviewer.get()), source_names=source_names,
            )
        except ValueError as exc:
            self.status.set(str(exc))
            return None

    def _clear_review_location(self, _event=None) -> None:
        if self._last_disagreement is not None:
            self.dispute_text.pack_forget()
            self.dispute_text.configure(state="normal")
            self.dispute_text.delete("1.0", tk.END)
            self.dispute_text.configure(state="disabled")
            self._last_disagreement = None
        self.status.set("")

    def _next_review_disagreement(self) -> None:
        previous = self._last_disagreement
        self._clear_review_location()
        report = self._paired_reviews()
        if report is None or self._locate_match is None:
            return
        disputes = [row for row in report.matches if row.outcome == "disagreement"]
        counts = report.outcome_counts
        missing = counts["missing_current_review"] + counts["missing_comparison_review"] + counts["neither_reviewed"]
        summary = f"Both reviewed with a deferral ({counts['deferred']}) · Missing one or both reviews ({missing})"
        if report.current_excluded or report.comparison_excluded:
            summary += f" · Unpaired quote rows ({report.current_excluded} current, {report.comparison_excluded} comparison)"
        excluded = counts["different_definitions"]
        if excluded:
            summary += f" · Comparable pairs ({report.shared - excluded} of {report.shared}) · Different codebook entries ({excluded})"
        if not disputes:
            self.status.set(f"No support-versus-decline disagreements. {summary}")
            return
        index = next((i + 1 for i, row in enumerate(disputes) if row.key == previous), 0) % len(disputes)
        pair = disputes[index]
        quote = pair.quote
        try:
            self._locate_match(pair.theme, quote)
        except ValueError as exc:
            self.status.set(str(exc))
            return
        self._last_disagreement = pair.key
        self.status.set(f"Review disagreement {index + 1} of {len(disputes)} · {summary}\n"
                        "Close Rigor tools to read the highlighted passage in the transcript.")
        notes = (f"{note.actor} ({note.disposition})\n{note.reason}" for note in (pair.current, pair.comparison))
        citation = f"{pair.theme.name} | {quote.source_name}, line {quote.source_line} [{quote.source_start}, {quote.source_end})"
        self.dispute_text.configure(state="normal")
        self.dispute_text.insert("1.0", "\n\n".join((citation + "\n" + quote.text, *notes)))
        self.dispute_text.configure(state="disabled")
        self.dispute_text.pack(fill="x")

    def _export_paired_reviews(self) -> None:
        preview = self._paired_reviews()
        if preview is None:
            return
        choice = PairedExportScope(self, preview).result
        if choice is None:
            self.status.set("Export canceled.")
            return
        selected, counts_only = choice
        comparison = self._paired_reviews(source_names=selected)
        if comparison is None:
            return
        # The match key binds the preview to the transcript digest as well as the exact span.
        expected = tuple(row for row in preview.matches if row.quote.source_name in selected)
        if comparison.matches != expected or comparison.reviewers != preview.reviewers:
            self.status.set("The paired reviews changed since the preview. Choose sources again.")
            return
        if counts_only:
            self._export_text(comparison.counts_csv, "paired-review-counts.csv", new_file=True)
            return
        if self._export_text(comparison.csv_text, "paired-match-reviews.csv", new_file=True):
            self.status.set(f"Exported {comparison.shared} shared match(es) from {len(selected)} selected source(s). "
                            f"Those sources had {comparison.current_excluded} current and "
                            f"{comparison.comparison_excluded} comparison quote row(s) outside the shared set.")

    def _export_current_assignments(self) -> None:
        context = self._context_source() if self._context_source is not None else self.context
        if context.result is None:
            self.assignment_status.set("No current manual codebook assignments to export.")
            return
        try:
            text, count, skipped = current_assignment_csv(CodedProject(context.documents, context.result, context.audit_events))
        except ValueError as exc:
            self.assignment_status.set(f"Manual codebook report unavailable: {exc} The full review report is still available.")
            return
        if self._export_text(text, "manual-codebook-assignments.csv", new_file=True, status=self.assignment_status):
            self.assignment_status.set(f"Exported {count} original manual assignment(s); {skipped} other stored quote row(s) excluded.")

    def _export_manual_records(self) -> None:
        context = self._context_source() if self._context_source is not None else self.context
        if context.result is None or self._comparison_project is None:
            self.status.set("Choose a saved comparison project with recorded manual assignments.")
            return
        try:
            comparison = compare_recorded_assignments(
                CodedProject(context.documents, context.result, context.audit_events), self._comparison_project)
        except ValueError as exc:
            self.status.set(f"Manual record comparison unavailable: {exc}")
            return
        if self._export_text(comparison.csv_text, "recorded-manual-assignments.csv", new_file=True):
            self.status.set(f"Exported exact manual records: {comparison.both} both, "
                            f"{comparison.current_only} current only, {comparison.comparison_only} comparison only. "
                            "The CSV lists overlapping ranges for one-sided records. No agreement statistic.")

    def _export_matrix(self, axis_label: str) -> None:
        self.refresh_current_state()
        rows = self.matrix_report.source_rows if axis_label == "source" else self.matrix_report.speaker_rows
        self._export_text(export_matrix_csv(rows, axis_label=axis_label), f"{axis_label}-matrix.csv")

    def _export_audit(self) -> None:
        self.refresh_current_state()
        self._export_text(export_audit_events_csv(self.context.audit_events), "audit-log.csv", status=self.audit_status)

    def _export_review_history(self) -> None:
        context = self._context_source() if self._context_source else self.context
        self._refresh_supported_sources(context.documents)
        indices = self.supported_sources.curselection()
        if len(indices) != 1 or not context.coder_name.strip():
            self.audit_status.set("Choose one transcript and enter a coder name for review history.")
            return
        source = self.supported_sources.get(indices[0])
        reviewer = context.coder_name.strip()
        rows = decision_rows(context.documents, context.audit_events, source, reviewer)
        if not rows:
            self.audit_status.set("No verified review decisions for this coder and transcript.")
            return
        output = ReviewHistoryScope(self, source, reviewer, rows).result
        if not output:
            self.audit_status.set("Export canceled.")
            return
        current = self._context_source() if self._context_source else self.context
        if context != current or self.supported_sources.curselection() != indices:
            self.audit_status.set("The study or source selection changed. Preview the decisions again.")
            return
        self._export_text(output, "selected-review-history.csv", new_file=True, status=self.audit_status)

    def _export_supported_matches(self) -> None:
        context = self._context_source() if self._context_source else self.context
        if context.result is None:
            self.audit_status.set("Open a project with quote evidence to export supported matches.")
            return
        self._refresh_supported_sources(context.documents)
        selected = {self.supported_sources.get(i) for i in self.supported_sources.curselection()}
        if not selected:
            self.audit_status.set("Choose at least one transcript for the supported evidence table.")
            return
        documents = (doc for doc in context.documents if doc.name in selected)
        output = export_quotes_csv(context.result, documents, context.audit_events, supported_only=True,
                                   codebook_entries=context.codebook_entries)
        self._export_text(output, "supported-matches.csv", status=self.audit_status)

    def _export_text(self, text: str, default_name: str, *, new_file: bool = False,
                     status: tk.StringVar | None = None) -> bool:
        status = self.status if status is None else status
        chosen = filedialog.asksaveasfilename(parent=self, defaultextension=".csv", initialfile=default_name)
        if not chosen:
            status.set("Export canceled.")
            return False
        try:
            with Path(chosen).open("x" if new_file else "w", encoding="utf-8", newline="") as stream:
                stream.write(text)
        except FileExistsError:
            status.set("Choose a new CSV filename; the existing file was not changed.")
            return False
        except OSError as exc:
            status.set(f"Could not export CSV: {exc}")
            return False
        status.set(f"Exported {Path(chosen).name}")
        return True

    def _configure_tree(self, tree: ttk.Treeview, headings: Sequence[str]) -> None:
        for column, heading in zip(tree["columns"], headings, strict=True):
            tree.heading(column, text=heading)
            tree.column(column, width=120, minwidth=80, stretch=True)

    def _grid_tree(self, parent: ttk.Frame, tree: ttk.Treeview, row: int) -> None:
        tree.grid(row=row, column=0, sticky="nsew")
        scroll_y = ttk.Scrollbar(parent, orient=tk.VERTICAL, command=tree.yview)
        scroll_y.grid(row=row, column=1, sticky="ns")
        tree.configure(yscrollcommand=scroll_y.set)

    def _clear_tree(self, tree: ttk.Treeview) -> None:
        for item in tree.get_children():
            tree.delete(item)


def _reviewer_choices(box: ttk.Combobox, declared: str, events: Sequence[AuditEvent]) -> None:
    names = {event.actor for event in events if event.action == REVIEW_ACTION and event.actor.strip()}
    declared = declared.strip()
    if declared:
        names.add(declared)
    box.configure(values=sorted(names))
    if box.get() not in names:
        box.set(declared or (next(iter(names)) if len(names) == 1 else ""))


def _label(value: str, fallback: str) -> str:
    normalized = value.strip()
    return normalized if normalized else fallback


def _yes_no(value: bool) -> str:
    return "yes" if value else "no"

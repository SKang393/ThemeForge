from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, ttk
import zipfile

from .analysis_types import AnalysisResult, TranscriptDocument
from .audit import AuditEvent, export_audit_events_csv
from .exports import export_quotes_csv
from .manual_record_comparison import compare_recorded_assignments, current_assignment_csv
from .match_reviews import REVIEW_ACTION
from .paired_reviews import compare_match_reviews
from .project_io import UnsupportedProjectFormatError, load_project
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


class RigorDialog(tk.Toplevel):
    def __init__(
        self,
        master: tk.Misc,
        context: RigorContext,
        context_source: Callable[[], RigorContext] | None = None,
    ) -> None:
        super().__init__(master)
        self.context = context
        self._context_source = context_source
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
            ttk.Label(tab, textvariable=self.assignment_status, wraplength=780).grid(row=2, column=0, sticky="w")
        tree = ttk.Treeview(
            tab,
            columns=("theme", axis_label, "quotes", "chars", "coverage"),
            show="headings",
        )
        self._configure_tree(tree, ("Theme", axis_label.title(), "Quote count", "Unique coded chars", "Coverage %"))
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
        ttk.Label(actions, text="Quote coverage by meaning unit").grid(row=2, column=0, sticky="w")
        ttk.Button(actions, text="Export coverage statistics", command=self._export_reliability).grid(row=2, column=1)
        ttk.Button(actions, text="Export coverage differences", command=self._export_disagreements).grid(row=2, column=2)
        ttk.Label(tab, textvariable=self.status, wraplength=825).grid(row=1, column=0, sticky="ew", pady=(0, 8))
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
        handoff = ttk.LabelFrame(tab, text="Supported evidence", padding=6)
        handoff.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        handoff.columnconfigure(0, weight=1)
        ttk.Label(handoff, text="Choose transcripts for the evidence table. Click a name to include or remove it.").grid(
            row=0, column=0, columnspan=3, sticky="w")
        self.supported_sources = tk.Listbox(handoff, height=3, selectmode=tk.MULTIPLE, exportselection=False)
        self.supported_sources.grid(row=1, column=0, sticky="ew")
        scrollbar = ttk.Scrollbar(handoff, orient=tk.VERTICAL, command=self.supported_sources.yview)
        scrollbar.grid(row=1, column=1, sticky="ns")
        self.supported_sources.configure(yscrollcommand=scrollbar.set)
        ttk.Button(handoff, text="Select all sources", command=lambda: self.supported_sources.selection_set(0, tk.END)).grid(
            row=1, column=2, padx=6)
        ttk.Label(handoff, wraplength=780, text="Only exact matches whose latest review across coders is supported. "
                  "The CSV includes excerpts, source and speaker names, theme labels, reviewer names and reasons. "
                  "Check these fields before sharing.").grid(row=2, column=0, columnspan=3, sticky="w", pady=4)
        ttk.Button(handoff, text="Export supported matches CSV", command=self._export_supported_matches).grid(
            row=3, column=0, sticky="w")
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
        if self._context_source is not None:
            self.context = self._context_source()
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
        self._render_matrix(self.source_matrix_tree, self.matrix_report.source_rows)
        self._render_matrix(self.speaker_matrix_tree, self.matrix_report.speaker_rows)
        self._render_audit()

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
            self._comparison_project = CodedProject(state.documents, state.result, state.audit_events)
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

    def _export_paired_reviews(self) -> None:
        context = self._context_source() if self._context_source else self.context
        if context.result is None or self._comparison_project is None:
            self.status.set("Choose a comparison project before exporting paired match reviews.")
            return
        selected = self.current_reviewer.get()
        _reviewer_choices(self.current_reviewer, context.coder_name, context.audit_events)
        if self.current_reviewer.get() != selected:
            self.status.set("The available reviewers changed. Check the reviewer names and export again.")
            return
        try:
            comparison = compare_match_reviews(
                CodedProject(context.documents, context.result, context.audit_events), self._comparison_project,
                CoderLabels(self.current_reviewer.get(), self.comparison_reviewer.get()),
            )
        except ValueError as exc:
            self.status.set(str(exc))
            return
        if self._export_text(comparison.csv_text, "paired-match-reviews.csv", new_file=True):
            self.status.set(f"Exported {comparison.shared} shared match(es). Other quote rows excluded: "
                            f"{comparison.current_excluded} current, {comparison.comparison_excluded} comparison.")

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
                            "No agreement statistic.")

    def _export_matrix(self, axis_label: str) -> None:
        self.refresh_current_state()
        rows = self.matrix_report.source_rows if axis_label == "source" else self.matrix_report.speaker_rows
        self._export_text(export_matrix_csv(rows, axis_label=axis_label), f"{axis_label}-matrix.csv")

    def _export_audit(self) -> None:
        self.refresh_current_state()
        self._export_text(export_audit_events_csv(self.context.audit_events), "audit-log.csv", status=self.audit_status)

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
        output = export_quotes_csv(context.result, documents, context.audit_events, supported_only=True)
        self._export_text(output, "supported-matches.csv", status=self.audit_status)

    def _export_text(self, text: str, default_name: str, *, new_file: bool = False,
                     status: tk.StringVar | None = None) -> bool:
        status = self.status if status is None else status
        chosen = filedialog.asksaveasfilename(parent=self, defaultextension=".csv", initialfile=default_name)
        if not chosen:
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

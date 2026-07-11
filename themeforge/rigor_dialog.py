from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, ttk
import zipfile

from .analysis_types import AnalysisResult, TranscriptDocument
from .audit import AuditEvent, export_audit_events_csv
from .project_io import UnsupportedProjectFormatError, load_project
from .rigor import (
    CodedProject,
    CoderLabels,
    DocumentTextMismatchError,
    IntercoderReliabilityReport,
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
        self.status = tk.StringVar(value="Choose a comparison .tfproj for intercoder reliability.")
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
        ttk.Button(
            tab,
            text="Export CSV",
            command=lambda: self._export_matrix(axis_label),
        ).grid(row=0, column=0, sticky="w", pady=(0, 8))
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
        ttk.Button(actions, text="Export reliability CSV", command=self._export_reliability).grid(row=0, column=1, padx=(8, 0))
        ttk.Button(actions, text="Export disagreements CSV", command=self._export_disagreements).grid(row=0, column=2, padx=(8, 0))
        ttk.Label(tab, textvariable=self.status).grid(row=1, column=0, sticky="ew", pady=(0, 8))
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
        ttk.Label(tab, text="Disagreements").grid(row=3, column=0, sticky="w", pady=(10, 6))
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
        tab.rowconfigure(1, weight=1)
        ttk.Button(
            tab,
            text="Export CSV",
            command=self._export_audit,
        ).grid(row=0, column=0, sticky="w", pady=(0, 8))
        self.audit_tree = ttk.Treeview(tab, columns=("timestamp", "actor", "action", "target", "details"), show="headings")
        self._configure_tree(self.audit_tree, ("Timestamp", "Actor", "Action", "Target", "Details"))
        self._grid_tree(tab, self.audit_tree, 1)
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
            self._render_intercoder()
            self.status.set("Current project has no analysis result.")
            return
        self.matrix_report = matrix_queries(self.context.documents, self.context.result)
        self._render_current_state()
        self._refresh_comparison()

    def _render_current_state(self) -> None:
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

    def _render_audit(self) -> None:
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
        self._render_intercoder()
        self.status.set("")
        path = Path(chosen)
        try:
            state = load_project(path)
            if state.result is None:
                self.status.set("Comparison project has no analysis result.")
                return
            self._comparison_project = CodedProject(state.documents, state.result)
            self._comparison_label = _label(state.researcher_name, path.stem)
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
        except (DocumentTextMismatchError, NoSharedDocumentsError) as exc:
            self.report = None
            self._render_intercoder()
            self.status.set(str(exc))
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
        self._export_text(export_reliability_csv(self.report), "intercoder-reliability.csv")

    def _export_disagreements(self) -> None:
        self.refresh_current_state()
        if self.report is None:
            self.status.set("Choose a comparison project before exporting disagreements.")
            return
        self._export_text(export_disagreements_csv(self.report), "intercoder-disagreements.csv")

    def _export_matrix(self, axis_label: str) -> None:
        self.refresh_current_state()
        rows = self.matrix_report.source_rows if axis_label == "source" else self.matrix_report.speaker_rows
        self._export_text(export_matrix_csv(rows, axis_label=axis_label), f"{axis_label}-matrix.csv")

    def _export_audit(self) -> None:
        self.refresh_current_state()
        self._export_text(export_audit_events_csv(self.context.audit_events), "audit-log.csv")

    def _export_text(self, text: str, default_name: str) -> None:
        chosen = filedialog.asksaveasfilename(parent=self, defaultextension=".csv", initialfile=default_name)
        if not chosen:
            return
        Path(chosen).write_text(text, encoding="utf-8")
        self.status.set(f"Exported {Path(chosen).name}")

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


def _label(value: str, fallback: str) -> str:
    normalized = value.strip()
    return normalized if normalized else fallback


def _yes_no(value: bool) -> str:
    return "yes" if value else "no"

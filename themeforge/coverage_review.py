"""Inspect displayed codebook leads alongside the passages eligible for retrieval."""
from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
import tkinter as tk
from tkinter import ttk

from .analysis_types import CodebookEntry, QuoteUnit, Theme


class CodeCoverage:
    """Snapshot the matcher output before manual assignments and automatic fallback themes."""

    def __init__(self, entries: Sequence[CodebookEntry], eligible: Sequence[QuoteUnit], themes: Sequence[Theme]):
        self.entries = tuple(entries)
        source_of = {unit.id: unit.source_name for unit in eligible}
        self._eligible = Counter(source_of.values())
        by_id = {theme.id: theme for theme in themes
                 if "generated_from_codebook_entry" in theme.validation
                 and not str(theme.validation.get("reanalysis_role", "")).startswith("Retained")}
        self.suggested_ids: set[str] = set()
        self._leads: list[Counter[str]] = []
        for number, entry in enumerate(self.entries, 1):
            # C01, C02, ... are assigned before empty entries are omitted. Names may repeat.
            theme = by_id.get(f"C{number:02d}")
            ids = set()
            if theme and theme.validation["generated_from_codebook_entry"] == entry.snapshot():
                ids = {q.quote_id for q in theme.quotes if source_of.get(q.quote_id) == q.source_name}
            self.suggested_ids.update(ids)
            self._leads.append(Counter(source_of[qid] for qid in ids))
        self._any_lead = Counter(source_of[qid] for qid in self.suggested_ids)

    def counts(self, source: str | None = None) -> tuple[int, tuple[int, ...]]:
        if source is None:
            return self._eligible.total(), tuple(leads.total() for leads in self._leads)
        return self._eligible[source], tuple(leads[source] for leads in self._leads)

    def covered_passages(self, source: str | None) -> int:
        return len(self.suggested_ids) if source is None else self._any_lead[source]

    def button_text(self, source: str | None = None) -> str:
        eligible, counts = self.counts(source)
        if not eligible:
            return "Coverage: no eligible passages…"
        return f"Coverage: {counts.count(0)}/{len(counts)} codes without leads…"


class CoverageReview(tk.Toplevel):
    def __init__(self, parent: tk.Misc, coverage: CodeCoverage, source_names: tuple[str, ...],
                 initial_source: str | None, *, preview: bool):
        super().__init__(parent)
        self.transient(parent)
        self.title("Codebook coverage")
        self.coverage = coverage
        self.source_names = source_names
        self.origin = "Preview (current settings)" if preview else "Last Analyze"
        self.result: str | None = None

        body = ttk.Frame(self, padding=12)
        body.pack(fill="both", expand=True)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(2, weight=1)
        ttk.Label(body, text="Source").grid(row=0, column=0, sticky="w", padx=(0, 8))
        names = tuple(f'“{n}”' for n in source_names) if "All sources" in source_names else source_names
        self.source_box = ttk.Combobox(body, state="readonly", values=("All sources", *names))
        self.source_box.grid(row=0, column=1, columnspan=2, sticky="ew")
        self.source_box.current(1 + source_names.index(initial_source) if initial_source in source_names else 0)
        self.source_box.bind("<<ComboboxSelected>>", self._refresh)

        self.summary = tk.StringVar(self)
        ttk.Label(body, textvariable=self.summary, wraplength=540).grid(
            row=1, column=0, columnspan=3, sticky="w", pady=8)
        self.table = ttk.Treeview(body, columns=("entry", "leads", "eligible"), show="headings",
                                  selectmode="browse", height=min(10, max(3, len(coverage.entries))))
        for name, heading, width in (("entry", "Codebook entry", 325), ("leads", "Displayed leads", 105),
                                     ("eligible", "Eligible passages", 110)):
            self.table.heading(name, text=heading)
            self.table.column(name, width=width, minwidth=width, stretch=name == "entry",
                              anchor="w" if name == "entry" else "center")
        self.table.grid(row=2, column=0, columnspan=2, sticky="nsew")
        vertical = ttk.Scrollbar(body, orient="vertical", command=self.table.yview)
        vertical.grid(row=2, column=2, sticky="ns")
        horizontal = ttk.Scrollbar(body, orient="horizontal", command=self.table.xview)
        horizontal.grid(row=3, column=0, columnspan=2, sticky="ew")
        self.table.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        ttk.Label(body, wraplength=540, text="The quote limit applies to displayed leads. Evidence decisions require manual review. "
                  "Choose a transcript to read the full text, including ineligible passages.").grid(
            row=4, column=0, columnspan=3, sticky="w", pady=8)
        actions = ttk.Frame(body)
        actions.grid(row=5, column=0, columnspan=3, sticky="e")
        self.read_button = ttk.Button(actions, text="Read transcript", command=self._read)
        self.read_button.pack(side="left", padx=(0, 6))
        ttk.Button(actions, text="Close", command=self.destroy).pack(side="left")
        self.bind("<Escape>", lambda _event: self.destroy())
        self._refresh()

    def _source(self) -> str | None:
        index = self.source_box.current()
        return self.source_names[index - 1] if index > 0 else None

    def _read_source(self) -> str | None:
        if len(self.source_names) == 1:
            return self.source_names[0]
        return self._source()

    def _refresh(self, _event: tk.Event | None = None) -> None:
        source = self._source()
        eligible, counts = self.coverage.counts(source)
        if eligible:
            covered = self.coverage.covered_passages(source)
            self.summary.set(f"{self.origin} · {covered}/{eligible} eligible passages have a displayed lead. "
                             f"{counts.count(0)}/{len(counts)} codebook entries have no displayed leads.")
        else:
            self.summary.set(f"{self.origin} · 0 eligible passages. Code coverage cannot be assessed in this scope.")
        self.table.delete(*self.table.get_children())
        for number, (entry, count) in enumerate(zip(self.coverage.entries, counts, strict=True), 1):
            self.table.insert("", "end", values=(f"{number}. {entry.name}", count if eligible else "N/A", eligible))
        self.read_button.state(["!disabled"] if self._read_source() is not None else ["disabled"])

    def _read(self) -> None:
        self.result = self._read_source()
        if self.result is not None:
            self.destroy()

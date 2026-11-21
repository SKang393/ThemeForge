"""Explicit transcript selection for desktop dialogs."""
from collections.abc import Sequence
import tkinter as tk
from tkinter import simpledialog, ttk


def source_listbox(parent: tk.Misc, names: Sequence[str], row: int) -> tk.Listbox:
    box = tk.Listbox(parent, selectmode=tk.MULTIPLE, exportselection=False, height=min(8, len(names)))
    box.insert(tk.END, *names)
    box.grid(row=row, column=0, sticky="ew")
    vertical = ttk.Scrollbar(parent, orient="vertical", command=box.yview)
    vertical.grid(row=row, column=1, sticky="ns")
    horizontal = ttk.Scrollbar(parent, orient="horizontal", command=box.xview)
    horizontal.grid(row=row + 1, column=0, sticky="ew")
    box.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
    return box


class FindTextScope(simpledialog.Dialog):
    def __init__(self, parent, names: tuple[str, ...], current: str, initial: str):
        self.names = names
        self.current = current
        self.initial = initial
        super().__init__(parent, "Find text")

    def body(self, parent):
        parent.columnconfigure(0, weight=1)
        ttk.Label(parent, text="Exact text (case-sensitive)").grid(row=0, column=0, sticky="w")
        self.query = ttk.Entry(parent, width=38)
        self.query.insert(0, self.initial)
        self.query.select_range(0, tk.END)
        self.query.grid(row=1, column=0, sticky="ew")
        ttk.Label(parent, text="Click transcript names to include or remove them.").grid(
            row=2, column=0, sticky="w", pady=(8, 2))
        self.sources = source_listbox(parent, self.names, 3)
        index = self.names.index(self.current)
        self.sources.selection_set(index)
        self.sources.see(index)
        self.notice = ttk.Label(parent, text="Find visits the selected transcripts in tab order.")
        self.notice.grid(row=5, column=0, sticky="w")
        return self.query

    def validate(self):
        if self.query.get().strip() and self.sources.curselection():
            return True
        self.notice.configure(text="Enter text and select at least one transcript.")
        return False

    def apply(self):
        self.result = self.query.get(), tuple(self.sources.get(i) for i in self.sources.curselection())

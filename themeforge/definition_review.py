"""Confirm an exact transcript passage against the full entries sharing a codebook label."""
from __future__ import annotations

import tkinter as tk
from collections.abc import Sequence
from tkinter import ttk

from .analysis_types import CodebookEntry, TranscriptDocument
from .manual_editing import ManualSelection, definition_text


def entry_label(index: int, entry: CodebookEntry) -> str:
    hint = " ".join((entry.description or next(iter(entry.examples), "")).split())
    if len(hint) > 76:
        hint = hint[:73] + "..."
    return f"{index + 1}. {entry.name}" + (f": {hint}" if hint else "")


class DefinitionReview(tk.Toplevel):
    def __init__(self, parent: tk.Misc, entries: Sequence[CodebookEntry], index: int,
                 document: TranscriptDocument, selection: ManualSelection, actor: str):
        super().__init__(parent)
        self.withdraw()
        self.transient(parent)
        self.title("Review codebook assignment")
        self.result: int | None = None
        label = entries[index].name.strip().casefold()
        self.indices = [i for i, entry in enumerate(entries) if entry.name.strip().casefold() == label]
        body = ttk.Frame(self, padding=12)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(1, weight=1)
        body.rowconfigure(3, weight=2)

        start, end = selection.source_start, selection.source_end
        last_line = selection.source_line + selection.text.rstrip("\n").count("\n")
        location = f"Line {last_line}" if last_line == selection.source_line else f"Lines {selection.source_line}–{last_line}"
        self.location = ttk.Label(body, text=f"{document.name} | {location} | "
                                  f"Characters [{start}:{end}] | Coder: {actor}\n"
                                  "Only the highlighted text will be assigned. Surrounding text is context.")
        self.location.grid(row=0, column=0, sticky="w")
        self.passage = self._text(body, 1, 5)
        lo, hi = max(0, start - 240), min(len(document.text), end + 240)
        self.passage.insert("end", ("… " if lo else "") + document.text[lo:start])
        self.passage.insert("end", selection.text, "evidence")
        self.passage.insert("end", document.text[end:hi] + (" …" if hi < len(document.text) else ""))
        self.passage.tag_configure("evidence", background="#f5dea2", foreground="#1c2023", underline=True)
        self.passage.configure(state="disabled")
        self.passage.see("evidence.first")

        ttk.Label(body, text="Compare the full definitions and examples for this label:").grid(row=2, column=0, sticky="w")
        self.definitions = self._text(body, 3, 8)
        for i in self.indices:
            self.definitions.insert("end", f"Entry {i + 1}\n" + definition_text(entries[i]) + "\n\n", f"entry{i}")
        self.definitions.configure(state="disabled")
        ttk.Label(body, text="Definition to apply (no matching or scoring):").grid(row=4, column=0, sticky="w")
        self.choice = ttk.Combobox(body, state="readonly", exportselection=False,
                                   values=[entry_label(i, entries[i]) for i in self.indices])
        self.choice.grid(row=5, column=0, sticky="ew", pady=4)
        self.choice.current(self.indices.index(index))
        self.choice.bind("<<ComboboxSelected>>", self.show_choice)
        self.show_choice()
        actions = ttk.Frame(body)
        actions.grid(row=6, column=0, sticky="e")
        ttk.Button(actions, text="Cancel", command=self.destroy).pack(side="left", padx=4)
        self.apply_button = ttk.Button(actions, text="Apply selected definition", command=self.apply)
        self.apply_button.pack(side="left")
        self.bind("<Escape>", lambda _e: self.destroy())
        self.protocol("WM_DELETE_WINDOW", self.destroy)

    @staticmethod
    def _text(parent, row, height):
        frame = ttk.Frame(parent)
        frame.grid(row=row, column=0, sticky="nsew", pady=(4, 10))
        text = tk.Text(frame, width=74, height=height, wrap="word", exportselection=False)
        scroll = ttk.Scrollbar(frame, command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        text.pack(side="left", fill="both", expand=True)
        return text

    def show_choice(self, _event=None):
        active = self.choice.current()
        for n, i in enumerate(self.indices):
            self.definitions.tag_configure(f"entry{i}", underline=n == active)
        if active >= 0:
            self.definitions.see(f"entry{self.indices[active]}.first")

    def apply(self):
        n = self.choice.current()
        if n >= 0:
            self.result = self.indices[n]
            self.destroy()


def review_definition(parent: tk.Misc, entries: Sequence[CodebookEntry], index: int, document: TranscriptDocument,
                      selection: ManualSelection, actor: str) -> int | None:
    dialog = DefinitionReview(parent, entries, index, document, selection, actor)
    if parent.winfo_viewable():
        dialog.deiconify()
        dialog.wait_visibility()
        dialog.grab_set()
        dialog.choice.focus_set()
    parent.wait_window(dialog)
    return dialog.result

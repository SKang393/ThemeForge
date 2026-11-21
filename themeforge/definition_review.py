"""Review passages in context, apply codebook definitions, and preview codebook reuse."""
from __future__ import annotations

import tkinter as tk
from collections.abc import Sequence
from dataclasses import replace
from tkinter import simpledialog, ttk
from tkinter.scrolledtext import ScrolledText

from .analysis_types import CodebookEntry, TranscriptDocument
from .manual_editing import ManualSelection, definition_text


class CodebookReuseDialog(simpledialog.Dialog):
    """Preview the codebook text that will enter a separate study."""

    def __init__(self, parent: tk.Misc, entries: tuple[CodebookEntry, ...]):
        self.options = {"Start without a codebook": (),
                        "Names and definitions only": tuple(replace(e, examples=()) for e in entries)}
        if any(e.examples for e in entries):
            self.options["Names, definitions and examples"] = entries
        self.choice = tk.StringVar(parent, value="Start without a codebook")
        super().__init__(parent, "Reuse codebook in the new study")

    def body(self, master):
        ttk.Label(master, wraplength=460, text="Check the preview for excerpts, identifying details and source "
                  "references. Copied text can appear in the new study’s exports. Theme hierarchy, transcripts, "
                  "memos and review decisions stay in the original study.").pack(fill="x", pady=(0, 6))
        for label in self.options:
            ttk.Radiobutton(master, text=label, value=label, variable=self.choice,
                            command=self.show_preview).pack(anchor="w")
        ttk.Label(master, text="Codebook text to copy").pack(anchor="w", pady=(8, 2))
        self.preview = ScrolledText(master, width=58, height=8, wrap="word")
        self.preview.pack(fill="both", expand=True)
        self.show_preview()

    def show_preview(self):
        entries = self.options[self.choice.get()]
        self.preview.configure(state="normal")
        self.preview.delete("1.0", "end")
        self.preview.insert("1.0", "\n\n".join(definition_text(entry) for entry in entries) or "No entries selected.")
        self.preview.configure(state="disabled")

    def apply(self):
        self.result = self.options[self.choice.get()]


class MatchReviewReason(simpledialog.Dialog):
    """Read the verified match in its transcript context while recording a decision."""

    def __init__(self, parent: tk.Misc, title: str, citation: str, excerpt: str,
                 neighbors: tuple[str, str], entry: CodebookEntry | None):
        self.citation = citation
        self.excerpt = excerpt
        self.neighbors = neighbors
        self.entry = entry
        super().__init__(parent, title)

    def body(self, master):
        context = ScrolledText(master, width=64, height=12, wrap="word")
        before, after = self.neighbors
        context.insert("end", self.citation + "Review the highlighted passage. Surrounding text is context.\n\n")
        context.insert("end", before)
        context.insert("end", self.excerpt, "evidence")
        context.insert("end", after)
        context.tag_configure("evidence", background="#f5dea2", foreground="#1c2023", underline=True)
        if self.entry is not None:
            context.insert("end", "\n\nSaved codebook entry\n" + definition_text(self.entry))
        context.configure(state="disabled")
        context.pack(fill="both", expand=True)
        context.bind("<Map>", lambda event: context.see("evidence.first"))
        ttk.Label(master, text="What in the passage led to this decision?").pack(anchor="w", pady=(8, 2))
        self.reason = ttk.Entry(master)
        self.reason.pack(fill="x")
        return self.reason

    def apply(self):
        self.result = self.reason.get()


def entry_label(index: int, entry: CodebookEntry) -> str:
    hint = " ".join((entry.description or next(iter(entry.examples), "")).split())
    if len(hint) > 76:
        hint = hint[:73] + "..."
    return f"{index + 1}. {entry.name}" + (f": {hint}" if hint else "")


class DefinitionReview(tk.Toplevel):
    def __init__(self, parent: tk.Misc, entries: Sequence[CodebookEntry], index: int | None,
                 document: TranscriptDocument, selection: ManualSelection, actor: str, *, source_note: str = ""):
        super().__init__(parent)
        self.withdraw()
        self.transient(parent)
        self.title("Review codebook assignment")
        self.result: int | None = None
        self.entries = entries
        label = entries[index].name.strip().casefold() if index is not None else None
        self.indices = [i for i, entry in enumerate(entries) if label is None or entry.name.strip().casefold() == label]
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
                                  "Only the highlighted text will be assigned. Surrounding text is context."
                                  + ("\n" + source_note if source_note else ""), wraplength=640)
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
        ttk.Label(body, text="Definition to apply (no matching or scoring):").grid(row=4, column=0, sticky="w")
        self.choice = ttk.Combobox(body, state="readonly", exportselection=False,
                                   values=[entry_label(i, entries[i]) for i in self.indices])
        self.choice.grid(row=5, column=0, sticky="ew", pady=4)
        if index is not None:
            self.choice.current(self.indices.index(index))
        self.choice.bind("<<ComboboxSelected>>", self.show_choice)
        actions = ttk.Frame(body)
        actions.grid(row=6, column=0, sticky="e")
        ttk.Button(actions, text="Cancel", command=self.destroy).pack(side="left", padx=4)
        self.apply_button = ttk.Button(actions, text="Apply selected definition", command=self.apply)
        self.apply_button.pack(side="left")
        self.show_choice()
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
        self.definitions.configure(state="normal")
        self.definitions.delete("1.0", "end")
        if active >= 0:
            chosen = self.indices[active]
            label = self.entries[chosen].name.strip().casefold()
            for i in self.indices:
                if self.entries[i].name.strip().casefold() == label:
                    self.definitions.insert("end", f"Entry {i + 1}\n" + definition_text(self.entries[i]) + "\n\n", f"entry{i}")
                    self.definitions.tag_configure(f"entry{i}", underline=i == chosen)
            self.definitions.see(f"entry{chosen}.first")
        else:
            self.definitions.insert("end", "Choose a current codebook entry below. No definition is inferred from the match.")
        self.definitions.configure(state="disabled")
        self.apply_button.state(["!disabled"] if active >= 0 else ["disabled"])

    def apply(self):
        n = self.choice.current()
        if n >= 0:
            self.result = self.indices[n]
            self.destroy()


def review_definition(parent: tk.Misc, entries: Sequence[CodebookEntry], index: int | None, document: TranscriptDocument,
                      selection: ManualSelection, actor: str, *, source_note: str = "") -> int | None:
    dialog = DefinitionReview(parent, entries, index, document, selection, actor, source_note=source_note)
    if parent.winfo_viewable():
        dialog.deiconify()
        dialog.wait_visibility()
        dialog.grab_set()
        dialog.choice.focus_set()
    parent.wait_window(dialog)
    return dialog.result

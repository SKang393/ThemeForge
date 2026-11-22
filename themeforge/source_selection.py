"""Explicit transcript selection for desktop dialogs."""
from collections.abc import Sequence
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, simpledialog, ttk
from tkinter.scrolledtext import ScrolledText
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .project_io import PendingVisit


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


def choose_transcript_files(parent: tk.Misc, *, append: bool = False) -> list[Path]:
    selected = filedialog.askopenfilenames(
        parent=parent, title=("Append unassigned visits and verify the original transcript files"
                             if append else "Open transcripts"),
        filetypes=[("Transcript files", "*.txt *.md *.csv *.docx *.rtf *.pdf"),
                   ("Text files", "*.txt *.md *.csv"), ("Word documents", "*.docx *.rtf"),
                   ("PDF files", "*.pdf"), ("All files", "*.*")],
    )
    paths = [Path(p) for p in selected]
    if len(paths) != len(set(paths)):
        if append:
            raise ValueError("The same path was selected more than once. Select each new visit once.")
        raise ValueError("The file picker returned the same path more than once. "
                         "Use Across folders… and choose files from one folder at a time.")
    return paths


class PendingVisits(simpledialog.Dialog):
    def __init__(self, parent: tk.Misc, visits: Sequence["PendingVisit"]):
        self.visits = visits
        super().__init__(parent, "Pending visits")

    def body(self, parent):
        ttk.Label(parent, text="Unassigned visits pending analysis").pack(anchor="w")
        self.source = ttk.Combobox(parent, state="readonly", values=[visit.name for visit in self.visits])
        self.source.pack(fill="x", pady=4)
        self.location = ttk.Label(parent, wraplength=520)
        self.location.pack(anchor="w")
        self.transcript = ScrolledText(parent, width=64, height=12, wrap="word")
        self.transcript.pack(fill="both", expand=True, pady=4)
        guidance = ("To leave out the last pending visit, reopen the project saved before it was appended."
                    if len(self.visits) == 1 else
                    "Select a visit to inspect it. Analyze includes all pending visits in the next pass.")
        ttk.Label(parent, text=guidance,
                  wraplength=520).pack(anchor="w")
        self.source.bind("<<ComboboxSelected>>", self._show_visit)
        self.transcript.configure(state="disabled")
        return self.source

    def _show_visit(self, _event=None):
        visit = self.visits[self.source.current()]
        self.location.configure(text=str(visit.path))
        self.transcript.configure(state="normal")
        self.transcript.delete("1.0", "end")
        self.transcript.insert("1.0", visit.text)
        self.transcript.configure(state="disabled")
        if len(self.visits) > 1:
            self.withdraw_button.state(["!disabled"])

    def buttonbox(self):
        buttons = ttk.Frame(self)
        buttons.pack(pady=6)
        self.withdraw_button = ttk.Button(buttons, text="Withdraw visit…", command=self.ok)
        self.withdraw_button.pack(side="left", padx=4)
        self.withdraw_button.state(["disabled"])
        ttk.Button(buttons, text="Close", command=self.cancel).pack(side="left", padx=4)
        if len(self.visits) == 1:
            self.source.current(0)
            self._show_visit()
        self.bind("<Escape>", self.cancel)

    def apply(self):
        self.result = self.visits[self.source.current()]


class PendingWithdrawal(simpledialog.Dialog):
    def __init__(self, parent: tk.Misc, selected: "PendingVisit", remaining: Sequence["PendingVisit"]):
        self.selected = selected
        self.remaining = remaining
        super().__init__(parent, "Withdraw pending visit")

    def body(self, parent):
        ttk.Label(parent, text=f"Withdraw {self.selected.name} from a new project copy?",
                  wraplength=500).pack(anchor="w")
        ttk.Label(parent, text=f"Still pending for analysis ({len(self.remaining)})").pack(anchor="w", pady=(8, 0))
        preview = ScrolledText(parent, width=58, height=min(8, len(self.remaining)), wrap="word")
        preview.insert("1.0", "\n".join(visit.name for visit in self.remaining))
        preview.configure(state="disabled")
        preview.pack(fill="both", expand=True)

    def buttonbox(self):
        buttons = ttk.Frame(self)
        buttons.pack(pady=6)
        cancel = ttk.Button(buttons, text="Cancel", command=self.cancel)
        cancel.pack(side="left", padx=4)
        ttk.Button(buttons, text="Save copy…", command=self.ok).pack(side="left", padx=4)
        self.initial_focus = cancel
        self.bind("<Escape>", self.cancel)

    def apply(self):
        self.result = True


class TranscriptFolders(simpledialog.Dialog):
    def __init__(self, parent: tk.Misc):
        self.paths: list[Path] = []
        super().__init__(parent, "Choose transcripts across folders")

    def body(self, parent):
        parent.columnconfigure(0, weight=1)
        ttk.Label(parent, text="Add files one folder at a time. OK imports the list in order. "
                  "Repeated filenames get [2], [3], … after the first.", wraplength=480).grid(
            row=0, column=0, sticky="w")
        self.files = source_listbox(parent, (), 1)
        self.files.configure(height=6, width=64, selectmode=tk.BROWSE)
        add = ttk.Button(parent, text="Add files from a folder…", command=self.add_files)
        add.grid(row=3, column=0, sticky="w", pady=4)
        add.bind("<Return>", self.ok)
        self.notice = ttk.Label(parent, wraplength=480)
        self.notice.grid(row=4, column=0, sticky="w")
        return add

    def add_files(self):
        try:
            batch = choose_transcript_files(self)
        except ValueError as exc:
            self.notice.configure(text=str(exc))
            return
        if not batch:
            return
        if set(self.paths).intersection(batch):
            self.notice.configure(text="This selection includes a file already listed. Choose only additional files.")
            return
        self.paths.extend(batch)
        self.files.insert(tk.END, *(str(path) for path in batch))
        self.files.see(tk.END)
        self.notice.configure(text="")

    def ok(self, event=None):
        # Return on Add or Cancel should perform that action, not import the list.
        if event is not None and isinstance(event.widget, (tk.Button, ttk.Button)):
            event.widget.invoke()
            return "break"
        return super().ok(event)

    def validate(self):
        if self.paths:
            return True
        self.notice.configure(text="Add at least one file to import.")
        return False

    def apply(self):
        self.result = self.paths


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

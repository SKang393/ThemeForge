"""Local analyst-defined case assignments and passage review in Rigor tools."""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
from tkinter.scrolledtext import ScrolledText
from typing import TYPE_CHECKING
from zipfile import BadZipFile

from .analysis_types import Theme, ThemeQuote, TranscriptDocument
from .case_recovery import older_working_copy, recovered_cases
from .case_reviews import case_review_cells
from .project_io import ProjectChanged, load_project, save_project

if TYPE_CHECKING:
    from .rigor_dialog import RigorContext


_STATUS = {"supported": "supported", "declined": "declined", "deferred": "deferred",
           "not_reviewed": "not reviewed", "historical": "earlier definition", "unverified": "unverified citation"}
_PROJECT_FILES = [("ThemeForge project", "*.tfproj")]


class CaseRecoveryPreview(simpledialog.Dialog):
    def __init__(self, parent: tk.Misc, master_path: Path, documents: tuple[TranscriptDocument, ...]):
        self.master_path = master_path
        self.documents = documents
        super().__init__(parent, "Confirm case assignments")

    def body(self, master):
        ttk.Label(master, wraplength=500, text="Check the retained master and each assignment before continuing. "
                  "Two projects with identical study content can have different case assignments. "
                  "Only you can identify the intended master.").pack(fill="x", pady=(0, 5))
        self.preview = ScrolledText(master, height=11, width=64, wrap="word", takefocus=True)
        self.preview.pack(fill="both", expand=True)
        self.preview.insert("end", f"Selected master\n{self.master_path}\n")
        for index, doc in enumerate(self.documents, 1):
            assignment = f"Case {doc.case_label!r}" if doc.case_label else "Unassigned"
            self.preview.insert("end", f"\n{index}. {doc.name!r}\n    {assignment}\n")
        self.preview.configure(state="disabled")
        self.preview.bind("<Tab>", lambda _event: self._focus_button(self.cancel_button))
        self.preview.bind("<Shift-Tab>", lambda _event: self._focus_button(self.confirm_button))

    @staticmethod
    def _focus_button(button) -> str:
        button.focus_set()
        return "break"

    def buttonbox(self):
        bar = ttk.Frame(self)
        bar.pack(pady=5)
        self.cancel_button = ttk.Button(bar, text="Cancel", default="active", command=self.cancel)
        self.cancel_button.pack(side="left", padx=5)
        self.confirm_button = ttk.Button(bar, text="Use these assignments", command=self.ok)
        self.confirm_button.pack(side="left", padx=5)
        self.initial_focus = self.cancel_button
        self.bind("<Return>", self.cancel)
        self.confirm_button.bind("<Return>", self._accept)
        self.bind("<Escape>", self.cancel)

    def _accept(self, _event):
        self.ok()
        return "break"

    def apply(self):
        self.result = True


class CaseTab(ttk.Frame):
    def __init__(self, parent: ttk.Notebook, assign: Callable[[TranscriptDocument, str], str] | None,
                 locate: Callable[[Theme, ThemeQuote], None],
                 saved_file: Callable[[], tuple[Path, bytes]] | None = None):
        super().__init__(parent, padding=10)
        self._assign = assign
        self._locate = locate
        self._saved_file = saved_file
        self._documents: tuple[TranscriptDocument, ...] = ()
        self._loaded: tuple[int, str, str] | None = None
        self._matches: dict[str, tuple[Theme, ThemeQuote]] = {}
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)
        controls = ttk.Frame(self)
        controls.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 4))
        for col in (0, 1):
            controls.columnconfigure(col, weight=1)
        ttk.Label(controls, text="Transcript").grid(row=0, column=0, sticky="w")
        ttk.Label(controls, text="Case label").grid(row=0, column=1, sticky="w")
        self.source_box = ttk.Combobox(controls, state="readonly", exportselection=False)
        self.source_box.grid(row=1, column=0, sticky="ew", padx=(0, 8))
        self.source_box.bind("<<ComboboxSelected>>", self._select_source)
        self.label_box = ttk.Combobox(controls, exportselection=False)
        self.label_box.grid(row=1, column=1, sticky="ew", padx=(0, 8))
        state = "normal" if assign else "disabled"
        ttk.Button(controls, text="Assign case", state=state, command=self._assign_label).grid(row=1, column=2)
        ttk.Button(controls, text="Clear assignment", state=state, command=lambda: self._assign_label(clear=True)).grid(
            row=1, column=3, padx=(6, 0))
        self.status = tk.StringVar(self)
        ttk.Label(self, textvariable=self.status, wraplength=800).grid(row=1, column=0, sticky="w", pady=(0, 6))
        self.tree = ttk.Treeview(self, columns=("case", "source", "reviews"), selectmode="browse")
        for col, heading, width in (("#0", "Theme / passage location", 210), ("case", "Case", 160),
                                    ("source", "Transcript", 190), ("reviews", "Review status", 280)):
            self.tree.heading(col, text=heading)
            self.tree.column(col, width=width, minwidth=75)
        self.tree.grid(row=2, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        scroll.grid(row=2, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.bind("<Return>", self._open_passage)
        self.tree.bind("<Double-1>", self._open_passage)
        self.review_scope = tk.StringVar(self)
        ttk.Label(self, textvariable=self.review_scope, wraplength=800).grid(row=3, column=0, sticky="w", pady=(6, 0))
        if saved_file:
            actions = ttk.Frame(self)
            actions.grid(row=4, column=0, sticky="w", pady=(6, 0))
            ttk.Button(actions, text="Save older-version copy…", command=self._copy_project).pack(side="left")
            ttk.Button(actions, text="Recover case assignments…",
                       command=lambda: self._copy_project(recover=True)).pack(side="left", padx=6)

    def _active_file(self) -> tuple[Path, bytes]:
        i = self.source_box.current()
        if i >= 0 and self.label_box.get() != self._documents[i].case_label:
            raise ValueError("Apply the case label or reselect the transcript before making a project copy.")
        return self._saved_file()

    def _copy_project(self, *, recover: bool = False) -> None:
        parent = self.winfo_toplevel()
        try:
            path, revision = self._active_file()
            active = load_project(path)
            if active.file_revision != revision:
                raise ProjectChanged()
            if recover:
                if active.needs_case_master:
                    raise ValueError("Open the saved older-version working copy without case assignments to recover them.")
                chosen = filedialog.askopenfilename(parent=parent, title="Choose the intended retained case master",
                                                    initialdir=path.parent, filetypes=_PROJECT_FILES)
                if not chosen:
                    self.status.set("Recovery cancelled. The active project stays open.")
                    return
                master_path = Path(chosen)
                master = load_project(master_path)
                output = recovered_cases(active, master)
                if not CaseRecoveryPreview(parent, master_path, output.documents).result:
                    self.status.set("Recovery cancelled. The active project stays open.")
                    return
                title, suffix = "Save recovered case master to an unused filename", "-recovered"
            else:
                output = older_working_copy(active)
                if not messagebox.askokcancel(
                    "Save older-version copy",
                    f"The working copy omits the case assignments. Keep this master for recovery:\n\n{path}\n\n"
                    "The master will stay open. Open the separate working copy in the older version.",
                    parent=parent, default=messagebox.CANCEL,
                ):
                    self.status.set("Copy cancelled. The master stays open.")
                    return
                title, suffix = "Save older-version working copy to an unused filename", "-older"
            chosen = filedialog.asksaveasfilename(parent=parent, title=title, initialdir=path.parent,
                                                  initialfile=f"{path.stem}{suffix}.tfproj", defaultextension=".tfproj",
                                                  filetypes=_PROJECT_FILES, confirmoverwrite=False)
            if not chosen:
                self.status.set("Copy cancelled. The active project stays open.")
                return
            target = Path(chosen)
            if self._active_file() != (path, revision):
                raise ValueError("The active project changed. Check the open study and start again.")
            input_revisions = ((path, revision),)
            if recover:
                input_revisions += ((master_path, master.file_revision),)
            save_project(target, output, new_only=True, input_revisions=input_revisions)
        except FileExistsError:
            self.status.set("Copy not saved. Choose an unused filename.")
        except (OSError, KeyError, TypeError, ValueError, BadZipFile) as exc:
            self.status.set(f"Copy not saved. {exc}")
        else:
            self.status.set(f"Saved {target}. {path.name} stays open. Use Open project to work in the new file.")

    def _select_source(self, _event=None) -> None:
        i = self.source_box.current()
        if 0 <= i < len(self._documents):
            doc = self._documents[i]
            self._loaded = (i, doc.name, doc.case_label)
            self.label_box.set(doc.case_label)
            assignment = f"Case: {doc.case_label}" if doc.case_label else "Unassigned"
            self.status.set(f"{assignment}. "
                            "Assign a label only when the whole transcript belongs to one case.")

    def _assign_label(self, *, clear: bool = False) -> None:
        i = self.source_box.current()
        if self._assign is None or i < 0:
            return
        label = "" if clear else self.label_box.get().strip()
        if not label and not clear:
            self.status.set("Enter or choose a case label, or choose Clear assignment.")
            self.label_box.focus_set()
            return
        try:
            message = self._assign(self._documents[i], label)
        except ValueError as exc:
            self.status.set(str(exc))
        else:
            self.label_box.set(label)
            self.status.set(message)

    def render(self, context: RigorContext) -> None:
        self._documents = tuple(context.documents)
        selected = self.source_box.current()
        self.source_box.configure(values=[d.name for d in self._documents])
        self.label_box.configure(values=sorted({d.case_label for d in self._documents if d.case_label}))
        if self._documents:
            i = selected if 0 <= selected < len(self._documents) else 0
            self.source_box.current(i)
            doc = self._documents[i]
            if self._loaded != (i, doc.name, doc.case_label):
                self._select_source()
        self._matches.clear()
        self.tree.delete(*self.tree.get_children())
        reviewer = context.coder_name.strip()
        if not context.result or not context.result.themes:
            self.review_scope.set("Assign visits to cases here. Code passages to see their themes and review status.")
            return
        scope = (f"Reviews by {reviewer}." if reviewer else
                 "Enter a coder name in the workspace to see that reader’s decisions.")
        self.review_scope.set(scope + " Expand a row and press Enter or double-click a passage to read it in context. "
                              "A decline needs interpretation. Empty cells do not establish absence.")
        cells = case_review_cells(context.documents, context.result, context.audit_events,
                                  reviewer=reviewer, codebook_entries=context.codebook_entries)
        for cell in cells:
            if cell.unresolved:
                case = "Unresolved source"
            elif cell.case_label:
                case = f"Case: {cell.case_label}"
            else:
                case = f"Unassigned: {cell.sources[0]}"
            counts = cell.counts
            summary = ", ".join(f"{counts[key]} {label}" for key, label in _STATUS.items()
                                if counts[key] and (reviewer or key != "not_reviewed"))
            if not reviewer and counts["not_reviewed"]:
                summary = ", ".join(filter(None, (summary, "Choose a coder to see reviews")))
            group = self.tree.insert("", "end", text=f"{cell.theme.name} ({cell.theme.id})",
                                     values=(case, ", ".join(cell.sources), summary or "No stored passage"))
            for passage in cell.passages:
                q = passage.quote
                location = f"Line {q.source_line} [{q.source_start}, {q.source_end})"
                status = "Choose a coder" if not reviewer and passage.status == "not_reviewed" else _STATUS[passage.status]
                row = self.tree.insert(group, "end", text=location,
                                       values=("", q.source_name, status))
                if passage.navigable:
                    self._matches[row] = (cell.theme, q)

    def _open_passage(self, event: tk.Event) -> str | None:
        row = self.tree.identify_row(event.y) if event.num == 1 else self.tree.focus()
        if row not in self._matches:
            return None
        try:
            self._locate(*self._matches[row])
        except ValueError as exc:
            self.status.set(str(exc))
        return "break"

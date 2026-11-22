"""Recipient-side check for scoped supported evidence."""
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, ttk
from tkinter.scrolledtext import ScrolledText

from .handoff_verification import verify_handoff


class HandoffDialog(tk.Toplevel):
    def __init__(self, parent: tk.Misc):
        super().__init__(parent)
        self.title("Verify handoff")
        self.transient(parent)
        body = ttk.Frame(self, padding=12)
        body.pack(fill="both", expand=True)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(6, weight=1)
        ttk.Label(body, text="Choose the linked evidence for one transcript, the transcript revision you hold "
                             "and the shared codebook used for the export.", wraplength=540).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))

        self.evidence = tk.StringVar(self)
        self.transcript = tk.StringVar(self)
        self.codebook = tk.StringVar(self)
        csv_types = [("CSV files", "*.csv"), ("All files", "*.*")]
        transcript_types = [("Transcript files", "*.txt *.md *.csv *.docx *.rtf *.pdf"), ("All files", "*.*")]
        files = (
            ("Evidence CSV…", "Choose linked evidence CSV", self.evidence, csv_types),
            ("Transcript…", "Choose transcript revision", self.transcript, transcript_types),
            ("Codebook CSV…", "Choose shared codebook CSV", self.codebook, csv_types),
        )
        for row, (label, title, variable, types) in enumerate(files, start=1):
            button = ttk.Button(body, text=label, command=lambda t=title, v=variable, f=types: self._choose(t, v, f))
            button.grid(row=row, column=0, sticky="ew", padx=(0, 6), pady=2)
            ttk.Entry(body, textvariable=variable, width=46, state="readonly").grid(
                row=row, column=1, sticky="ew", pady=2)
            if row == 1:
                self.first_button = button

        ttk.Button(body, text="Verify files", command=self.verify).grid(
            row=4, column=0, sticky="ew", padx=(0, 6), pady=(6, 8))
        ttk.Label(body, text="Verification result").grid(row=5, column=0, columnspan=2, sticky="w")
        self.report = ScrolledText(body, wrap="word", width=66, height=13, font="TkTextFont",
                                   state="disabled", takefocus=True)
        self.report.grid(row=6, column=0, columnspan=2, sticky="nsew", pady=(2, 6))
        self.report.bind("<Tab>", lambda event: self._leave_report(forward=True))
        self.report.bind("<Shift-Tab>", lambda event: self._leave_report(forward=False))
        ttk.Button(body, text="Close", command=self.destroy).grid(row=7, column=1, sticky="e")
        self.bind("<Escape>", lambda event: self.destroy())
        if parent.winfo_viewable():
            self.wait_visibility()
            self.grab_set()
            self.first_button.focus_set()

    def _choose(self, title: str, variable: tk.StringVar, filetypes) -> None:
        path = filedialog.askopenfilename(parent=self, title=title, filetypes=filetypes)
        if path:
            variable.set(path)
            self._show("Choose Verify files to check this selection.")

    def _show(self, message: str) -> None:
        self.report.configure(state="normal")
        self.report.delete("1.0", "end")
        self.report.insert("1.0", message)
        self.report.mark_set("insert", "1.0")
        self.report.see("1.0")
        self.report.configure(state="disabled")

    def _leave_report(self, *, forward: bool) -> str:
        target = self.report.tk_focusNext() if forward else self.report.tk_focusPrev()
        target.focus_set()
        return "break"

    def verify(self) -> None:
        paths = [v.get() for v in (self.evidence, self.transcript, self.codebook)]
        if not all(paths):
            self._show("Verification needs all three files. Choose the evidence CSV, transcript and codebook CSV.")
        else:
            try:
                message = verify_handoff(*(Path(path) for path in paths))
            except (ValueError, OSError) as exc:
                self._show(f"Verification failed.\n\n{exc}")
            else:
                self._show(message)
        self.report.focus_set()

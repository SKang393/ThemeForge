"""Inspect parsed speaker turns and set suggestion eligibility for each source."""
from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
import tkinter as tk
from tkinter import ttk

from .analysis_types import SpeakerOverride, TranscriptDocument
from .quote_units import automatic_speaker_selection
from .transcript_parser import document_segments


class SpeakerReview(tk.Toplevel):
    def __init__(self, parent: tk.Misc, documents: Sequence[TranscriptDocument], overrides: tuple[SpeakerOverride, ...]):
        super().__init__(parent)
        self.transient(parent)
        self.title("Speakers for suggestions")
        self.result: tuple[SpeakerOverride, ...] | None = None
        self.choices = {(rule.source_name, rule.speaker): rule.included for rule in overrides}
        turns = document_segments(documents)
        self.automatic = automatic_speaker_selection(turns)
        self.source_counts = Counter(doc.name or "Transcript" for doc in documents)
        counts = Counter((turn.source_name, turn.speaker) for turn in turns)
        self.examples = {}
        for turn in turns:
            self.examples.setdefault((turn.source_name, turn.speaker), turn)
        self.keys = list(self.automatic)

        body = ttk.Frame(self, padding=12)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(1, weight=1)
        guidance = ttk.Label(body, text="Select speakers for the next Analyze or Find. "
                             "Passage length and prompt filters still apply.", wraplength=440)
        guidance.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        columns = ("transcript", "speaker", "turns", "automatic", "choice")
        self.table = ttk.Treeview(body, columns=columns, show="headings", selectmode="extended", height=9)
        for column, label, width in zip(columns, ("Transcript", "Speaker", "Turns", "Automatic", "Your choice"),
                                        (108, 78, 46, 86, 95), strict=True):
            self.table.heading(column, text=label)
            self.table.column(column, width=width, stretch=column in ("transcript", "speaker"))
        for i, key in enumerate(self.keys):
            automatic = "Include" if self.automatic[key] else "Exclude"
            self.table.insert("", "end", iid=str(i), values=(*key, counts[key], automatic, self._choice(key)))
        self.table.grid(row=1, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(body, command=self.table.yview)
        scroll.grid(row=1, column=1, sticky="ns")
        self.table.configure(yscrollcommand=scroll.set)
        self.preview = tk.StringVar(value="No parsed speaker turns.")
        across = ttk.Scrollbar(body, orient="horizontal", command=self.table.xview)
        across.grid(row=2, column=0, sticky="ew")
        self.table.configure(xscrollcommand=across.set)
        excerpt = ttk.Label(body, textvariable=self.preview, wraplength=440)
        excerpt.grid(row=3, column=0, columnspan=2, sticky="ew", pady=8)
        for label in (guidance, excerpt):
            label.bind("<Configure>", lambda event: event.widget.configure(wraplength=max(1, event.width - 4)))
        self.table.bind("<<TreeviewSelect>>", self._show_example)
        if self.keys:
            self.table.selection_set("0")
            self._show_example()
        buttons = ttk.Frame(body)
        buttons.grid(row=4, column=0, columnspan=2, sticky="ew")
        for label, choice in (("Include", True), ("Exclude", False), ("Use automatic", None)):
            ttk.Button(buttons, text=label, width=0, command=lambda c=choice: self.set_choice(c)).pack(side="left")
        ttk.Button(buttons, text="Apply", width=0, command=self.apply).pack(side="right")
        ttk.Button(buttons, text="Cancel", width=0, command=self.destroy).pack(side="right")
        self.bind("<Escape>", lambda _event: self.destroy())

    def _choice(self, key: tuple[str, str]) -> str:
        if key not in self.choices:
            return "Automatic"
        return "Include" if self.choices[key] else "Exclude"

    def _show_example(self, _event=None) -> None:
        selected = self.table.selection()
        if len(selected) == 1:
            turn = self.examples[self.keys[int(selected[0])]]
            excerpt = " ".join(turn.text.split())
            self.preview.set(f"{turn.source_name} · {turn.speaker} · Line {turn.source_line} · {excerpt[:200]}" + ("…" if len(excerpt) > 200 else ""))
        else:
            self.preview.set(f"{len(selected)} transcript speakers selected.")

    def set_choice(self, included: bool | None) -> None:
        if included is not None and any(self.source_counts[self.keys[int(i)][0]] != 1 for i in self.table.selection()):
            self.preview.set("These transcripts share a name. A speaker choice needs a unique transcript name.")
            return
        for item in self.table.selection():
            key = self.keys[int(item)]
            if included is None:
                self.choices.pop(key, None)
            else:
                self.choices[key] = included
            self.table.set(item, "choice", self._choice(key))

    def apply(self) -> None:
        self.result = tuple(SpeakerOverride(*key, included) for key, included in sorted(self.choices.items()))
        self.destroy()


def review_speakers(parent: tk.Misc, documents: Sequence[TranscriptDocument],
                    overrides: tuple[SpeakerOverride, ...]) -> tuple[SpeakerOverride, ...] | None:
    dialog = SpeakerReview(parent, documents, overrides)
    if parent.winfo_viewable():
        dialog.wait_visibility()
        dialog.grab_set()
        dialog.table.focus_set()
    parent.wait_window(dialog)
    return dialog.result

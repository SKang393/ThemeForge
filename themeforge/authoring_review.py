"""Expose the open workspace's verified passage and normal review actions to the Windows coding dialog."""
from __future__ import annotations

import csv
from dataclasses import replace
from hashlib import sha256
from io import StringIO
from pathlib import Path
from tkinter import filedialog
from typing import TYPE_CHECKING

from .codebook import SharedCodebook
from .exports import export_quotes_csv
from .manual_editing import definition_text
from .match_reviews import match_id, match_snapshot

if TYPE_CHECKING:
    from .app import ThemeForgeApp


class CodingReview:
    def __init__(self, app: ThemeForgeApp):
        self.app = app
        self._shown_target = ""
        self._shown_result = None

    def _fields(self) -> dict[str, str]:
        app = self.app
        theme = app._current_theme()
        fields = dict(analyst=app.researcher_name.get(), source=app.file_summary.get(), speaker="", excerpt="",
                      code=app.codebook_summary.get(), definition="", review="", status=app.status_text.get(), target="")
        if theme is None:
            return fields
        fields["code"] = f"{theme.name} ({theme.id})"
        entry = app.match_reviews.codebook_entry(theme)
        fields["definition"] = definition_text(entry) if entry is not None else "No current codebook entry for this theme."
        match = app._visible_review_match()
        if match is None or app._display_sources.locate(match.quote).status != "exact_span":
            fields["review"] = "Use Next passage to locate an exact recorded excerpt."
            return fields
        quote = match.quote
        fields.update(source=f"{quote.source_name}, line {quote.source_line}, characters [{quote.source_start}, {quote.source_end})",
                      speaker=quote.speaker, excerpt=quote.text, review=app.match_review_text.get())
        if app.match_reviews.definition_is_current(theme):
            fields["target"] = match_id(match_snapshot(theme, quote, app._display_sources))
        return fields

    def snapshot(self) -> dict[str, str]:
        fields = self._fields()
        self._shown_target = fields["target"]
        self._shown_result = self.app.result
        return fields

    def _require_shown_target(self) -> None:
        if (not self._shown_target or self.app.result is not self._shown_result or
                self._fields()["target"] != self._shown_target):
            raise ValueError("Review the current code and exact source passage again before continuing.")

    def perform(self, action: str, *, analyst: str = "", reason: str = "") -> bool:
        app = self.app
        if action == "set_analyst":
            if analyst != app.researcher_name.get():
                app.researcher_name.set(analyst)
            return False
        if action in ("support", "decline"):
            self._require_shown_target()
            if analyst.strip() != app.researcher_name.get().strip():
                raise ValueError("The analyst changed. Check the current analyst before recording a decision.")
            return app.review_current_match("supported" if action == "support" else "declined", reason=reason)
        if reason.strip() and action in {"next_code", "next_passage", "import_source", "import_codebook", "open_project", "analyze"}:
            raise ValueError("Record or clear the decision reason before changing the review selection.")
        if action in {"next_code", "next_passage"}:
            if app._pending_review_edits(include_theme=action == "next_code"):
                raise ValueError("Save or undo unfinished workspace edits before changing the review selection.")
            if not app.result or not app.result.themes:
                raise ValueError("Import a transcript and analyze it before choosing a passage or code.")
            if action == "next_passage":
                app.next_quote()
            else:
                index = app.current_theme_index if app.current_theme_index is not None else -1
                app._select_theme((index + 1) % len(app.result.themes))
        elif action == "export":
            self._export()
        else:
            workspace_actions = {"import_source": app.open_transcript, "import_codebook": app.open_codebook,
                                 "open_project": app.open_project, "save_project": app.save_project, "analyze": app.analyze}
            workspace_actions[action]()
        return False

    def _export(self) -> None:
        app = self.app
        self._require_shown_target()
        context = app._rigor_context()
        theme = app._current_theme()
        source = app._visible_review_match().source_name
        if not app.match_reviews.codebook_entry(theme):
            raise ValueError("Choose a passage associated with a current codebook entry before exporting linked evidence.")
        documents = [d for d in context.documents if d.name == source]
        if len(documents) != 1:
            raise ValueError("Choose a uniquely named transcript for the supported evidence export.")
        book_name = filedialog.askopenfilename(parent=app, title="Choose the CSV codebook your collaborator will use",
                                               filetypes=[("CSV codebook", "*.csv")])
        if not book_name:
            app.status_text.set("Export canceled.")
            return
        self._require_shown_target()
        if context != app._rigor_context():
            raise ValueError("The study changed. Review the export selection again.")
        book = Path(book_name)
        shared = SharedCodebook.from_csv(book)
        output = export_quotes_csv(replace(context.result, themes=[theme]), documents, context.audit_events,
                                   supported_only=True, codebook_entries=context.codebook_entries, shared_codebook=shared)
        rows = csv.DictReader(StringIO(output))
        count = sum(1 for _ in rows)
        if not count:
            raise ValueError("No supported exact passages for this code and transcript.")
        destination = filedialog.asksaveasfilename(parent=app, title="Export supported evidence", defaultextension=".csv",
                                                   filetypes=[("CSV evidence", "*.csv")], initialfile="supported-matches-linked.csv")
        if not destination:
            app.status_text.set("Export canceled.")
            return
        self._require_shown_target()
        if context != app._rigor_context() or sha256(book.read_bytes()).hexdigest() != shared.file_sha256:
            raise ValueError("The study or shared codebook changed. Review the export selection again.")
        try:
            with Path(destination).open("x", encoding="utf-8", newline="") as stream:
                stream.write(output)
        except FileExistsError as exc:
            raise ValueError("Choose a new CSV filename. The existing file was preserved.") from exc
        app.status_text.set(f"Exported {count} supported passage(s) from {source} for {theme.name}.")

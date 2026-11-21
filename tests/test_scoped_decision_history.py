"""Reconstruct a fictional pickup correction using only explicitly chosen decisions."""
import csv
from dataclasses import replace
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from tkinter import ttk
import unittest
from unittest.mock import patch

from test_evidence_handoff import descendants, export, open_audit, press
from themeforge.analysis_types import TranscriptDocument
from themeforge.app import ThemeForgeApp
from themeforge.audit import AuditEvent
from themeforge.decision_history import decision_history_csv, decision_rows
from themeforge.match_reviews import REVIEW_ACTION, match_id
from themeforge.rigor_dialog import ReviewHistoryScope
from themeforge.source_location import TranscriptIndex


PASSAGE = "I rolled the amber cart across the line."
CONTEXT = "The line was inside our holding bay. The porter never took the cart."
DOCK = (f"Pine: {PASSAGE}\nPine: That was the practice run on the empty floor.\n"
        f"Elm: {PASSAGE}\nElm: {CONTEXT}")
STORE = "Ash: The porter signed for the violet tote after collecting it."
EARLIER = "I read crossing the line as a handover to the porter."
WITHDRAWAL = "A movement within the holding bay does not establish a change of custody."
FINAL = "Elm moved the cart within the holding bay to wait for collection."
HISTORY_BUTTON = "Choose review history…"
COLUMNS = ["recorded_theme", "source_line", "source_start", "source_end", "reviewed_excerpt",
           "recorded_decision", "recorded_reason"]


class ScopedDecisionHistoryTests(unittest.TestCase):
    def test_correction_after_reopen_only_leaves_with_explicit_decisions(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        dock, store, book = (root / f for f in ("dock.txt", "store.txt", "codes.csv"))
        dock.write_text(DOCK, encoding="utf-8")
        store.write_text(STORE, encoding="utf-8")
        book.write_text("code,definition\nCompleted pickup,Cart collection by a receiving porter.\n"
                        "Staged for pickup,Moving a cart across the line into a holding bay before collection.\n",
                        encoding="utf-8")
        app = ThemeForgeApp()
        self.addCleanup(app.destroy)
        with patch("themeforge.app.filedialog.askopenfilenames", return_value=[str(dock), str(store)]):
            press(app, "Choose files")
        with patch("themeforge.app.filedialog.askopenfilename", return_value=str(book)):
            press(app, "Choose codebook")
        press(app, "Analyze")

        def decide(actor, code, source, line, verdict, reason):
            app.researcher_name.set(actor)
            theme, = [t for t in app.result.themes if t.name == code]
            app._select_theme_by_id(theme.id)
            quote, = [q for q in theme.quotes if (q.source_name, q.source_line) == (source, line)]
            app._focus_quote_by_id(quote.quote_id)
            self.assertEqual(app.transcript_widgets[source].get("selected_quote.first", "selected_quote.last"), quote.text)
            with patch("themeforge.app.MatchReviewReason") as prompt:
                prompt.return_value.result = reason
                app.review_current_match(verdict)
                prompt.assert_called_once()
            return quote

        decide("Reed", "Completed pickup", dock.name, 1, "deferred", "Check the separate rehearsal.")
        q = decide("Reed", "Completed pickup", dock.name, 3, "supported", EARLIER)
        self.assertEqual((q.source_start, q.source_end, q.text), (104, 144, PASSAGE))
        decide("Reed", "Completed pickup", store.name, 1, "supported", "Receipt of the violet tote.")
        decide("Flax", "Completed pickup", dock.name, 3, "deferred", "A second reader's private question.")
        decide("Reed", "Completed pickup", dock.name, 3, "declined", WITHDRAWAL)
        decide("Reed", "Staged for pickup", dock.name, 3, "supported", FINAL)
        app.theme_memo_text.insert("1.0", "A private group memo.")
        press(app, "Save theme memo")
        project = root / "cart-study.tfproj"
        with patch("themeforge.app.filedialog.asksaveasfilename", return_value=str(project)):
            press(app, "Save project")
        original = project.read_bytes()

        dialog = open_audit(app, project)
        scope = dialog.supported_sources
        picker = "themeforge.rigor_dialog.filedialog.asksaveasfilename"
        with patch(picker) as choose_name, patch.object(ReviewHistoryScope, "wait_window") as modal:
            press(dialog, HISTORY_BUTTON)
            press(dialog, "Select all sources")
            press(dialog, HISTORY_BUTTON)
            modal.assert_not_called()
            choose_name.assert_not_called()
        scope.selection_clear(0, "end")
        scope.selection_set(scope.get(0, "end").index("dock.txt"))
        baseline = root / "before.csv"
        row, = export(dialog, "Export supported matches CSV", baseline)
        self.assertEqual((row["theme_name"], row["match_review_reason"]), ("Staged for pickup", FINAL))
        project_state = app._project_state()

        def select(history):
            history.update()
            self.assertEqual(history.choices.curselection(), ())
            self.assertEqual(history.output, "")
            warning, = [w for w in descendants(history) if isinstance(w, ttk.Label)
                        and "can identify people" in w.cget("text")]
            self.assertIn("Selected quotes", warning.cget("text"))
            self.assertIn("free-text reasons", warning.cget("text"))
            self.assertTrue(warning.winfo_viewable())
            self.assertGreaterEqual(warning.winfo_height(), warning.winfo_reqheight())
            self.assertGreaterEqual(warning.winfo_width(), warning.winfo_reqwidth())
            self.assertEqual([r.recorded_reason for r in history.rows],
                             ["Check the separate rehearsal.", EARLIER, WITHDRAWAL, FINAL])
            for i in (3, 2, 1):
                history.choices.selection_set(i)
            self.assertEqual(history.choices.curselection(), (1, 2, 3))
            history.choices.event_generate("<<ListboxSelect>>")
            history.update()
            self.assertEqual(history.preview.cget("state"), "disabled")
            return history.preview.get("1.0", "end-1c")

        def empty_then_cancel(history, _window):
            history.ok()
            self.assertTrue(history.winfo_exists())
            select(history)
            history.cancel()

        def accept(history, _window):
            shown.append(select(history))
            history.ok()

        shown = []
        with (
            patch.object(ReviewHistoryScope, "wait_window", autospec=True, side_effect=empty_then_cancel),
            patch(picker) as choose_name,
        ):
            press(dialog, HISTORY_BUTTON)
            choose_name.assert_not_called()
        with (
            patch.object(ReviewHistoryScope, "wait_window", autospec=True, side_effect=accept),
            patch(picker, return_value="") as choose_name,
        ):
            press(dialog, HISTORY_BUTTON)
            self.assertEqual(choose_name.call_count, 1, (dialog.audit_status.get(), shown))
            self.assertIn("canceled", dialog.audit_status.get())
        with (
            patch.object(ReviewHistoryScope, "wait_window", autospec=True, side_effect=accept),
            patch(picker, return_value=str(baseline)),
        ):
            before = baseline.read_bytes()
            press(dialog, HISTORY_BUTTON)
            self.assertEqual(baseline.read_bytes(), before)
            self.assertIn("Choose a new CSV filename", dialog.audit_status.get())
        destination = root / "chosen.csv"
        with patch.object(ReviewHistoryScope, "wait_window", autospec=True, side_effect=accept):
            history = export(dialog, HISTORY_BUTTON, destination)
        self.assertEqual(destination.read_bytes(), shown[-1].encode("utf-8"))
        self.assertEqual(list(history[0]), COLUMNS)
        self.assertEqual(history, [dict(zip(COLUMNS, [theme, "3", "104", "144", PASSAGE, status, why], strict=True))
                                   for theme, status, why in (("Completed pickup", "supported", EARLIER),
                                                              ("Completed pickup", "declined", WITHDRAWAL),
                                                              ("Staged for pickup", "supported", FINAL))])
        self.assertEqual(app._project_state(), project_state)
        self.assertEqual(project.read_bytes(), original)
        dialog.destroy()
        reopened = open_audit(app, project)
        self.assertEqual(reopened.supported_sources.curselection(), ())
        reopened.supported_sources.selection_set(reopened.supported_sources.get(0, "end").index("dock.txt"))
        with patch.object(ReviewHistoryScope, "wait_window", autospec=True, side_effect=accept):
            self.assertEqual(export(reopened, HISTORY_BUTTON, root / "reopened.csv"), history)

        def change_scope(history, window):
            accept(history, window)
            reopened.supported_sources.selection_clear(0, "end")

        with (
            patch.object(ReviewHistoryScope, "wait_window", autospec=True, side_effect=change_scope),
            patch(picker) as choose_name,
        ):
            press(reopened, HISTORY_BUTTON)
            choose_name.assert_not_called()
        reopened.supported_sources.selection_set(reopened.supported_sources.get(0, "end").index("dock.txt"))
        after = root / "after.csv"
        export(reopened, "Export supported matches CSV", after)
        self.assertEqual(after.read_bytes(), baseline.read_bytes())
        self.assertEqual(app._project_state(), project_state)
        self.assertEqual(project.read_bytes(), original)

    def test_only_intact_historical_snapshots_and_exact_source_occurrences_qualify(self):
        doc = TranscriptDocument("dock.txt", DOCK)
        quote = dict(source_name=doc.name, source_line=3, source_start=104, source_end=144, text=PASSAGE)
        snapshot = dict(theme_name="Completed pickup", quote=quote, source_sha256=TranscriptIndex([doc]).digest(doc.name))

        def event(snap):
            details = json.dumps(dict(match=snap, disposition="declined", reason="=private,\nquoted"))
            return AuditEvent("", "Reed", REVIEW_ACTION, "theme_quote_match", match_id(snap), details)

        good = event(snapshot)
        events = [good, replace(good, action="analysis_run"), replace(good, target_id="stale"),
                  event({**snapshot, "quote": {**quote, "source_line": 1}}),
                  event({**snapshot, "quote": {**quote, "source_start": "104"}}),
                  event({**snapshot, "quote": {**quote, "source_start": 0, "source_end": 0}})]
        rows = decision_rows([doc], events, doc.name, "Reed")
        self.assertEqual(len(rows), 1)
        row, = csv.DictReader(io.StringIO(decision_history_csv(rows)))
        self.assertEqual(row["recorded_reason"], "'=private,\nquoted")
        for docs in ([], [doc, doc], [replace(doc, text=DOCK + " ")],
                     [replace(doc, name="another.txt")]):
            self.assertEqual(decision_rows(docs, events, doc.name, "Reed"), [])

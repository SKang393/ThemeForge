"""Read a fictional correction and distinguish two identical proposed excerpts."""
from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from tkinter import Button
from tkinter.scrolledtext import ScrolledText
import unittest
from unittest.mock import patch

from test_evidence_handoff import descendants, export, open_audit, press
from themeforge.analysis_types import ThemeQuote, TranscriptDocument
from themeforge.app import ThemeForgeApp
from themeforge.definition_review import MatchReviewReason
from themeforge.source_location import TranscriptIndex


REPEAT = "We paused the loom because the bell rang."
CORRECTION = "I was repeating the rehearsal script there; the wheel actually kept spinning the whole time."
CONFIRMATION = "I watched the shuttle stand still until the signal cleared."
HEARSAY = "They said a bell had interrupted the loom, though I never saw it happen."
TRANSCRIPT = "\n".join((
    "Interviewer: Start with the rehearsal in the felt room.",
    "Weaver: I put the yellow spool 🧶 on the rail for the practice run.",
    "Weaver: " + REPEAT,
    "Weaver: " + CORRECTION,
    "Interviewer: What about Tuesday's demonstration in the indigo room?",
    "Weaver: " + REPEAT,
    "Weaver: " + CONFIRMATION,
    "Interviewer: And what did the evening crew tell you?",
    "Weaver: " + HEARSAY,
    "Weaver: The evening ledger was still sealed in its green envelope.",
))
LINES = TRANSCRIPT.splitlines()


class ReviewContextTests(unittest.TestCase):
    def test_context_follows_multiline_turns_only_at_the_verified_location(self):
        turns = ("A: Far introduction.", "B: A previous turn\nwith a continuation.",
                 "C: First part. Repeated phrase. Extra words.", "D: A following turn\ncontinuing here.",
                 "E: Repeated phrase.")
        source = TranscriptDocument("room.txt", "\n\n".join(turns))
        index = TranscriptIndex([source])
        phrase = "Repeated phrase."
        start = source.text.index(phrase)
        quote = ThemeQuote("Q1", "C", phrase, None, 1 + source.text.count("\n", 0, start), source.name,
                           source_start=start, source_end=start + len(phrase))
        self.assertEqual(index.neighboring_turn_text(quote),
                         (turns[1] + "\n\nC: First part. ", " Extra words.\n\n" + turns[3]))
        self.assertEqual(index.neighboring_turn_text(replace(quote, source_start=start + 1)), ("", ""))
        self.assertEqual(TranscriptIndex([source, source]).neighboring_turn_text(quote), ("", ""))

    def test_neighboring_turns_in_decision_dialog_keep_exact_occurrence_and_export_scope(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        source, other, codes = root / "loom.txt", root / "inventory.txt", root / "codes.csv"
        source.write_text(TRANSCRIPT, encoding="utf-8")
        other.write_text("Clerk: The apricot stencil is hanging by the fountain.", encoding="utf-8")
        codes.write_text("code,definition\nSignal pause,The bell caused the loom to pause.\n", encoding="utf-8")
        app = ThemeForgeApp()
        self.addCleanup(app.destroy)
        screen = app.winfo_screenwidth(), app.winfo_screenheight()
        if app.tk.call("tk", "windowingsystem") == "win32" and screen == (1024, 768):
            app.state("zoomed")
        else:
            app.minsize(1, 1)
            app.geometry("1024x705+0+0")
        with patch("themeforge.app.filedialog.askopenfilenames", return_value=(str(source), str(other))):
            press(app, "Choose files")
        with patch("themeforge.app.filedialog.askopenfilename", return_value=str(codes)):
            press(app, "Choose codebook")
        app.researcher_name.set("Linen")
        press(app, "Analyze")
        app.update()
        self.assertEqual(app._analysis_settings().semantic_backend, "tfidf")
        widget = app.transcript_widgets[source.name]

        def decide(line, button, reason):
            match = app._current_quote_match()
            self.assertEqual(match.quote.source_line, line)
            excerpt = LINES[line - 1].split(": ", 1)[1]
            self.assertEqual(widget.get("selected_quote.first", "selected_quote.last"), excerpt)
            position = app.current_quote_index
            self.assertEqual(TRANSCRIPT[match.quote.source_start:match.quote.source_end], excerpt)

            def answer(dialog, _window):
                self.assertEqual(app.grab_current(), dialog)
                dialog.update()
                area, = [w for w in descendants(dialog) if isinstance(w, ScrolledText)]
                actions = {w.cget("text"): w for w in descendants(dialog) if isinstance(w, Button)}

                def check_visible(control):
                    self.assertTrue(control.winfo_viewable())
                    x, y = control.winfo_rootx(), control.winfo_rooty()
                    for px, py in ((x, y), (x + control.winfo_width() - 1, y + control.winfo_height() - 1)):
                        self.assertTrue(0 <= px < min(screen[0], 1024) and 0 <= py < min(screen[1], 768))
                        self.assertEqual(dialog.winfo_containing(px, py), control)

                for control in (area, area.vbar, dialog.reason, actions["OK"], actions["Cancel"]):
                    check_visible(control)
                self.assertEqual(dialog.focus_lastfor(), dialog.reason)
                focus = dialog.focus_get()
                if focus is not None:
                    self.assertEqual(focus, dialog.reason)
                self.assertEqual(area.get("evidence.first", "evidence.last"), excerpt)
                self.assertIn(LINES[line - 2], area.get("1.0", "evidence.first"))
                self.assertIn(LINES[line], area.get("evidence.last", "end-1c"))
                self.assertIn("The bell caused the loom to pause.", area.get("evidence.last", "end-1c"))
                self.assertEqual(area.cget("state"), "disabled")
                self.assertIsNotNone(area.bbox("evidence.first"))
                following = area.search(LINES[line], "evidence.last", stopindex="end")
                area.see(f"{following} lineend -1c")
                dialog.update_idletasks()
                self.assertIsNotNone(area.bbox(f"{following} lineend -1c"))
                area.see("evidence.first")
                dialog.update_idletasks()
                self.assertIsNotNone(area.bbox("evidence.first"))
                self.assertEqual(area.get("evidence.first", "evidence.last"), excerpt)
                if line == 3 and reason is None:
                    focus_state = "reason entry" if focus is dialog.reason else "desktop inactive"
                    print(f"Review dialog {dialog.winfo_width()}x{dialog.winfo_height()} on "
                          f"{screen[0]}x{screen[1]}. Keyboard focus: {focus_state}. "
                          "Following turn and exact highlight reachable.")
                if line == 3:
                    self.assertIn("Required", dialog.reason_label.cget("text"))
                    dialog.reason.insert(0, "   " if reason is not None else "")
                    actions["OK"].invoke()
                    dialog.update_idletasks()
                    self.assertEqual(app.grab_current(), dialog)
                    feedback = dialog.reason_label
                    self.assertIn("Enter a reason", feedback.cget("text"))
                    check_visible(feedback)
                    self.assertGreaterEqual(feedback.winfo_width(), feedback.winfo_reqwidth())
                    self.assertGreaterEqual(feedback.winfo_height(), feedback.winfo_reqheight())
                    self.assertEqual(area.get("evidence.first", "evidence.last"), excerpt)
                    self.assertEqual(app.audit_events, initial_events)
                    dialog.reason.delete(0, "end")
                if reason is None:
                    # A typed reason must not turn keyboard cancellation into evidence.
                    dialog.reason.insert(0, "Tentative reading; check the correction.")
                    cancel = actions["Cancel"]
                    cancel.focus_force()
                    dialog.update()
                    self.assertEqual(dialog.focus_get(), cancel)
                    cancel.event_generate("<Return>")
                else:
                    dialog.reason.insert(0, reason)
                    actions["OK"].invoke()

            with patch.object(MatchReviewReason, "wait_window", autospec=True, side_effect=answer) as modal, \
                    patch.object(app, "report_callback_exception") as errors:
                press(app, button)
                if errors.called:
                    raise errors.call_args.args[1]
                modal.assert_called_once()
            self.assertEqual((app.current_quote_index, app._current_quote_match()), (position, match))
            self.assertEqual(widget.get("selected_quote.first", "selected_quote.last"), excerpt)

        initial_events = list(app.audit_events)
        decide(3, "Supports this theme", None)
        self.assertEqual(app.audit_events, initial_events)
        self.assertIn("No current match review", app.match_review_text.get())
        decide(3, "Not this match", "The speaker corrects this account.")
        press(app, "Next unreviewed")
        decide(6, "Supports this theme", "The stoppage was observed.")
        # Walk the displayed matches to the evening report without judging other passages.
        for _ in app.current_quote_matches:
            press(app, "Next quote")
            if app._current_quote_match().quote.source_line == 9:
                break
        decide(9, "Revisit later", "Check the sequence with the evening crew.")
        project = root / "weaving.tfproj"
        with patch("themeforge.app.filedialog.asksaveasfilename", return_value=str(project)):
            press(app, "Save project")

        fresh = ThemeForgeApp()
        self.addCleanup(fresh.destroy)
        audit = open_audit(fresh, project)
        reviews = [e for e in fresh.audit_events if e.action == "match_review_note"]
        saved = [json.loads(e.details) for e in reviews]
        self.assertEqual({e.actor for e in reviews}, {"Linen"})
        self.assertEqual([(n["match"]["quote"]["source_line"], n["disposition"]) for n in saved],
                         [(3, "declined"), (6, "supported"), (9, "deferred")])
        first = TRANSCRIPT.index(REPEAT)
        retried = saved[0]["match"]["quote"]
        self.assertEqual((retried["source_name"], retried["source_start"], retried["source_end"], saved[0]["reason"]),
                         (source.name, first, first + len(REPEAT), "The speaker corrects this account."))
        selector = audit.supported_sources
        self.assertEqual(selector.curselection(), ())
        selector.selection_set(selector.get(0, "end").index(source.name))
        table = root / "handoff.csv"
        row, = export(audit, "Export supported matches CSV", table)
        start = TRANSCRIPT.rindex(REPEAT)
        self.assertEqual((row["quote"], row["source_file"], row["source_line"], row["source_start"], row["source_end"],
                          row["match_review_actor"], row["match_review_target_id"], row["match_review_reason"]),
                         (REPEAT, source.name, "6", str(start), str(start + len(REPEAT)), "Linen", reviews[1].target_id,
                          "The stoppage was observed."))
        contents = table.read_text(encoding="utf-8")
        for surrounding in (CORRECTION, CONFIRMATION, "yellow spool", "indigo room", HEARSAY, "green envelope",
                            "apricot stencil", other.name):
            self.assertNotIn(surrounding, contents)
        audit.destroy()
        press(fresh, "Next deferred")
        self.assertEqual(fresh._current_quote_match().quote.text, HEARSAY)
        self.assertIn("deferred", fresh.match_review_text.get().lower())

"""Revisit one of two identical accounts in a fictional cart workshop."""
import json
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from tkinter import Button, ttk
from tkinter.scrolledtext import ScrolledText
import unittest
from unittest.mock import patch

from test_evidence_handoff import descendants, export, press
from themeforge.app import ThemeForgeApp
from themeforge.definition_review import MatchReviewReason
from themeforge.rigor_dialog import RigorDialog


QUOTE = "I pulled the red cord to stop the kiln cart."
CORRECTION = "I was reading the practice card aloud. That wooden model had no working brake."
SHED = "\n".join(("Examiner: What happened on the first run?", "Porter: " + QUOTE,
                  "Porter: Its wheels stopped beside the blue chalk mark.",
                  "Porter: I coiled the red cord on a hook after lunch.",
                  "Porter: I laid the kiln cart cover out on a bench.",
                  "Examiner: What words did you read during the demonstration?",
                  "Porter: " + QUOTE, "Porter: " + CORRECTION))
STORES = ("Storekeeper: I counted the spare carts and brake cords in storage.\n"
          "Storekeeper: I pulled the emergency cord to stop the lemon-yellow trolley near the warehouse.")
BOOK = ("code,definition\nEquipment inventory,Counting the spare carts and brake cords in storage.\n"
        "Emergency braking,Pulling the red cord to stop the kiln cart.\n")
ORIGINAL = "The first-person account describes using the brake."
OTHER = "I interpret the demonstration as an account of the braking action."
REVISION = "The speaker identifies the words as a rehearsal."


class ReviewRevisionTests(unittest.TestCase):
    def test_revisit_own_support_and_export_current_evidence(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        for name, text in (("shed.txt", SHED), ("stores.txt", STORES), ("cart.csv", BOOK)):
            (root / name).write_text(text, encoding="utf-8")
        project = root / "cart.tfproj"

        def launch():
            with patch.object(ThemeForgeApp, "maxsize", return_value=(1024, 705)):
                return ThemeForgeApp()

        app = launch()
        self.addCleanup(lambda: app.destroy())
        with patch("themeforge.app.filedialog.askopenfilenames",
                   return_value=[str(root / f) for f in ("shed.txt", "stores.txt")]):
            press(app, "Choose files")
        with patch("themeforge.app.filedialog.askopenfilename", return_value=str(root / "cart.csv")):
            press(app, "Choose codebook")
        app.researcher_name.set("Ochre")
        press(app, "Analyze")
        app.update()

        def act(label):
            press(app, label)
            app.update()

        def mark(label, reason, following=""):
            match = app._current_quote_match()

            def answer(dialog, _window):
                dialog.update()
                self.assertEqual(app.grab_current(), dialog)
                area, = [w for w in descendants(dialog) if isinstance(w, ScrolledText)]
                self.assertEqual(area.get("evidence.first", "evidence.last"), match.quote.text)
                if following:
                    self.assertIn(following, area.get("evidence.last", "end-1c"))
                    location = area.search(following, "evidence.last", stopindex="end")
                    area.see(location)
                    dialog.update_idletasks()
                    self.assertIsNotNone(area.bbox(location))
                dialog.reason.insert(0, reason)
                ok, = [w for w in descendants(dialog) if isinstance(w, Button) and w.cget("text") == "OK"]
                ok.invoke()

            with patch.object(MatchReviewReason, "wait_window", autospec=True, side_effect=answer) as modal, \
                    patch.object(app, "report_callback_exception") as errors:
                act(label)
                if errors.called:
                    raise errors.call_args.args[1]
                modal.assert_called_once()

        def select_braking_theme():
            index = next(i for i, theme in enumerate(app.result.themes) if theme.name == "Emergency braking")
            app.theme_list.selection_clear(0, "end")
            app.theme_list.selection_set(index)
            app.theme_list.event_generate("<<ListboxSelect>>")
            app.update()

        def save_and_reopen():
            nonlocal app
            act("Save project")
            app.request_close()
            app = launch()
            with patch("themeforge.app.filedialog.askopenfilename", return_value=str(project)):
                act("Open project")

        def export_shed():
            act("Rigor tools")
            dialog, = [w for w in app.winfo_children() if isinstance(w, RigorDialog)]
            dialog.audit_tree.master.master.select(dialog.audit_tree.master)
            app.update()
            selector = dialog.supported_sources
            self.assertEqual(selector.curselection(), ())
            selector.selection_set(selector.get(0, "end").index("shed.txt"))
            table = root / "supported.csv"
            rows = export(dialog, "Export supported matches CSV", table)
            dialog.destroy()
            body = table.read_text(encoding="utf-8")
            for outside in ("stores.txt", "lemon-yellow", "blue chalk mark", CORRECTION):
                self.assertNotIn(outside, body)
            for row in rows:
                self.assertEqual(row["source_sha256"], sha256(SHED.encode("utf-8")).hexdigest())
                self.assertEqual((row["source_file"], row["quote"], row["source_location_status"],
                                  row["match_review_disposition"]), ("shed.txt", QUOTE, "exact_span", "supported"))
            return [(int(r["source_start"]), int(r["source_end"]), r["match_review_actor"],
                     r["match_review_reason"]) for r in rows]

        mark("Supports this theme", "I count this inventory list.")  # Withheld source.
        select_braking_theme()
        other_reason = "The stationary wheels corroborate the first account."
        mark("Supports this theme", other_reason)
        act("Next quote")
        mark("Supports this theme", OTHER)
        app.researcher_name.set("Flint")
        self.assertIn("No current match review for this coder", app.match_review_text.get())
        mark("Supports this theme", ORIGINAL)
        with patch("themeforge.app.filedialog.asksaveasfilename", return_value=str(project)):
            save_and_reopen()
        original_events = [e for e in app.audit_events if e.action == "match_review_note"]
        self.assertEqual(json.loads(original_events[0].details)["match"]["quote"]["source_name"], "stores.txt")
        first_occurrence = (50, 94, "Ochre", other_reason)
        self.assertEqual(export_shed(), [first_occurrence, (322, 366, "Flint", ORIGINAL)])
        self.assertEqual(app._current_theme().name, "Equipment inventory")
        prior = list(app.audit_events)
        button, = [w for w in descendants(app) if isinstance(w, ttk.Button) and w.cget("text") == "Next supported"]
        self.assertGreaterEqual(button.winfo_width(), button.winfo_reqwidth())
        x, y = button.winfo_rootx() + button.winfo_width() - 1, button.winfo_rooty() + button.winfo_height() - 1
        self.assertLess(x, app.winfo_rootx() + app.winfo_width())
        self.assertLess(y, app.winfo_rooty() + app.winfo_height())
        self.assertEqual(app.winfo_containing(x, y), button)
        act("Next supported")
        self.assertEqual(app.audit_events, prior)
        self.assertIn(ORIGINAL, app.match_review_text.get())
        q = app._current_quote_match().quote
        self.assertEqual((q.source_name, q.source_line, q.source_start, q.source_end, q.text),
                         ("shed.txt", 7, 322, 366, QUOTE))
        mark("Not this match", REVISION, CORRECTION)
        self.assertEqual(export_shed(), [first_occurrence])
        save_and_reopen()
        self.assertEqual(export_shed(), [first_occurrence])
        events = [e for e in app.audit_events if e.action == "match_review_note"]
        self.assertEqual(events[:-1], original_events)
        same_match = [e for e in events if e.target_id == original_events[-1].target_id]
        decisions = [(e.actor, json.loads(e.details)["disposition"], json.loads(e.details)["reason"]) for e in same_match]
        self.assertEqual(decisions, [("Ochre", "supported", OTHER), ("Flint", "supported", ORIGINAL),
                                     ("Flint", "declined", REVISION)])
        position = app._current_quote_match()
        act("Next supported")
        self.assertIn("No verified supported matches for Flint", app.status_text.get())
        self.assertEqual(app._current_quote_match(), position)
        app.researcher_name.set("Ochre")
        act("Next supported")
        self.assertIn(other_reason, app.match_review_text.get())
        act("Next supported")
        self.assertEqual(app._current_quote_match().quote, q)
        self.assertIn(OTHER, app.match_review_text.get())
        app.researcher_name.set("Flint")
        self.assertIn(REVISION, app.match_review_text.get())

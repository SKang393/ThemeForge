"""A revised exclusion can reverse a judgment without changing a lexical score."""
import csv
from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
from shutil import copyfile
import tempfile
from time import monotonic
from tkinter import Button, simpledialog, ttk
from tkinter.scrolledtext import ScrolledText
import unittest
from unittest.mock import patch

from test_evidence_handoff import descendants, export, press
from themeforge.app import ThemeForgeApp
from themeforge.definition_review import MatchReviewReason
from themeforge.match_reviews import match_id
from themeforge.paired_reviews import compare_match_reviews
from themeforge.project_io import load_project
from themeforge.rigor import CodedProject, CoderLabels
from themeforge.rigor_dialog import RigorDialog


TRAY = ("Clay: I checked the tray after cooling by looking at its color.\n"
        "Glaze: I checked the tray after cooling by weighing it.")
PACK = "Fold: I wrapped the finished tile in a packing sleeve."
EXAMPLE = "Inspect a cooled tray. Exclude: packing sleeve."
OLD = "Tray check after cooling. Exclude: weight-only checks."
NEW = ("Tray check after cooling. Exclude: color-only checks.\n"
       "These observations alone do not establish a physical check:\n"
       "A change in the glaze from pink to white.\n"
       "A photograph of the tray from the previous shift.\n"
       "A color noted on a packing slip.\n"
       "A description of the tray under a different lamp.\n"
       "A comparison with a color sample on the shelf.\n"
       "A guess about the weight based on the shade of the glaze.")


class ReviewedCodebookRevisionTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        with patch.object(ThemeForgeApp, "maxsize", return_value=(1024, 705)):
            self.app = ThemeForgeApp()
        self.addCleanup(self.app.destroy)
        docs = {"kiln.txt": TRAY, "packing.txt": PACK}
        for name, text in docs.items():
            (self.root / name).write_text(text, encoding="utf-8")
        with patch("themeforge.app.filedialog.askopenfilenames", return_value=[str(self.root / n) for n in docs]):
            press(self.app, "Choose files")
        self.book(OLD)
        self.app.researcher_name.set("Lark")
        press(self.app, "Analyze")
        self.project = self.root / "ceramics.tfproj"

    def book(self, description):
        book = self.root / "codes.csv"  # Same name and row position on both imports.
        with book.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerows([("code", "description", "example"), ("Tray check", description, EXAMPLE),
                              ("Packing", "packing sleeve", "")])
        with patch("themeforge.app.filedialog.askopenfilename", return_value=str(book)):
            press(self.app, "Choose codebook")

    def show(self, theme, source, line):
        self.app._select_theme_by_id(theme.id)
        q, = [q for q in theme.quotes if (q.source_name, q.source_line) == (source, line)]
        self.app._focus_quote_by_id(q.quote_id)
        self.assertEqual(self.app.transcript_widgets[source].get("selected_quote.first", "selected_quote.last"), q.text)
        return q

    def assert_visible_inside(self, widget, window):
        self.assertTrue(widget.winfo_viewable(), str(widget))
        x = widget.winfo_rootx() - window.winfo_rootx()
        y = widget.winfo_rooty() - window.winfo_rooty()
        self.assertGreaterEqual(min(x, y), 0, str(widget))
        self.assertLessEqual(x + widget.winfo_width(), window.winfo_width(), str(widget))
        self.assertLessEqual(y + widget.winfo_height(), window.winfo_height(), str(widget))

    def decide(self, button, reason):
        observed = []
        errors = []
        deadline = monotonic() + 5

        def answer():
            dialog = next((w for w in self.app.winfo_children() if isinstance(w, simpledialog.Dialog)), None)
            try:
                if dialog is None or self.app.grab_current() != dialog:
                    self.assertLess(monotonic(), deadline, "Review dialog did not take its modal grab")
                    self.app.after(10, answer)
                    return
                self.assertIsInstance(dialog, MatchReviewReason)
                dialog.geometry("600x360+20+20")
                dialog.update_idletasks()
                self.assertEqual((dialog.winfo_width(), dialog.winfo_height()), (600, 360))
                widgets = list(descendants(dialog))
                area, = [w for w in widgets if isinstance(w, ScrolledText)]
                actions = {w.cget("text"): w for w in widgets if isinstance(w, Button)}
                for control in (area, area.vbar, dialog.reason, actions["OK"], actions["Cancel"]):
                    self.assert_visible_inside(control, dialog)
                for action in actions.values():
                    self.assertGreaterEqual(action.winfo_width(), action.winfo_reqwidth())
                    self.assertGreaterEqual(action.winfo_height(), action.winfo_reqheight())
                context = area.get("1.0", "end-1c")
                observed.append(context)
                if NEW in context:
                    self.assertIsNotNone(area.bbox("1.0"))
                    self.assertIsNone(area.bbox("end-2c"), "The revised example should initially be below the fold")
                    area.yview_moveto(1)
                    dialog.update_idletasks()
                    self.assertIsNotNone(area.bbox("end-2c"))
                    self.assertTrue(context.endswith(f"Example: {EXAMPLE}"))
                dialog.reason.insert(0, reason)
                actions["OK"].invoke()
            except Exception as exc:
                errors.append(exc)
                if dialog is not None and dialog.winfo_exists():
                    dialog.cancel()
        self.app.after_idle(answer)
        press(self.app, button)
        if errors:
            raise errors[0]
        context, = observed
        return context

    def open(self, path):
        with patch("themeforge.app.filedialog.askopenfilename", return_value=str(path)):
            press(self.app, "Open project")

    def save_reopen(self):
        with patch("themeforge.app.filedialog.asksaveasfilename", return_value=str(self.project)):
            self.assertTrue(self.app.save_project())
        self.open(self.project)

    def rows(self, audit=False):
        press(self.app, "Rigor tools")
        dialog = next(w for w in self.app.winfo_children() if isinstance(w, RigorDialog))
        tab = dialog.audit_tree.master
        tab.master.select(tab)
        dialog.update_idletasks()
        press(dialog, "Select all sources")
        label = "Export audit CSV" if audit else "Export supported matches CSV"
        rows = export(dialog, label, self.root / "table.csv")
        dialog.destroy()
        return rows

    def tray_row(self):
        row, = [r for r in self.rows() if r["source_file"] == "kiln.txt"]
        return row

    def test_repeated_match_under_a_revised_exclusion_needs_a_new_decision(self):
        app = self.app
        tray, packing = app.result.themes
        self.assertEqual({q.source_name for q in tray.quotes}, {"kiln.txt"})
        self.assertEqual(self.rows(), [])
        self.show(packing, "packing.txt", 1)
        self.decide("Supports this theme", "The tile was wrapped.")
        old_quote = self.show(tray, "kiln.txt", 1)
        reason = "The reader checked the color after cooling."
        context = self.decide("Supports this theme", reason)
        self.assertIn(OLD, context)
        self.assertIn(f"Example: {EXAMPLE}", context)
        self.assertIn(old_quote.text, context)
        self.save_reopen()
        initial = self.tray_row()
        self.assertEqual((initial["source_start"], initial["source_end"], initial["quote"]), ("6", "63", TRAY[6:63]))
        self.assertEqual(initial["source_sha256"], sha256(TRAY.encode()).hexdigest())
        old_events = [r for r in self.rows(audit=True) if r["action"] == "match_review_note"]
        self.assertEqual(len(old_events), 2)
        self.assertEqual(initial["match_review_target_id"], old_events[-1]["target_id"])
        baseline = self.root / "original.tfproj"
        copyfile(self.project, baseline)

        self.show(tray, "kiln.txt", 1)
        app.theme_name.set("Tray  check")
        press(app, "Save theme edits")
        self.save_reopen()
        self.assertEqual(self.tray_row(), {**initial, "theme_name": "Tray  check"})
        self.assertEqual(initial["match_review_theme_name"], "Tray check")
        self.show(app.result.themes[0], "kiln.txt", 1)
        self.assertIn("reviewed as 'Tray check'", app.match_review_text.get())
        app.theme_name.set("Visual inspection")
        press(app, "Save theme edits")
        self.assertEqual([r["source_file"] for r in self.rows()], ["packing.txt"])
        app.theme_name.set("Tray  check")
        press(app, "Save theme edits")

        # A second reader in a separate copy sees no inherited vote. Both raw
        # audit targets remain traceable even though the display spacing differs.
        other = self.root / "second.tfproj"
        copyfile(self.project, other)
        self.open(other)
        app.researcher_name.set("Rook")
        self.show(app.result.themes[0], "kiln.txt", 1)
        self.assertIn("No current match review", app.match_review_text.get())
        self.decide("Revisit later", "Check the drying interval.")
        def coded(path):
            state = load_project(path)
            return CodedProject(state.documents, state.result, state.audit_events, state.settings.codebook_entries)
        pair = compare_match_reviews(coded(baseline), coded(other), CoderLabels("Lark", "Rook"))
        paired, = [r for r in csv.DictReader(pair.csv_text.splitlines()) if r["source_file"] == "kiln.txt" and r["source_line"] == "1"]
        second_id = next(e.target_id for e in load_project(other).audit_events if e.actor == "Rook" and e.action == "match_review_note")
        self.assertNotEqual(initial["match_review_target_id"], second_id)
        self.assertEqual((paired["review_comparison"], paired["match_review_target_id"], paired["current_review_target_id"],
                          paired["comparison_review_target_id"]), ("deferred", "", initial["match_review_target_id"], second_id))

        self.open(self.project)
        self.show(app.result.themes[0], "kiln.txt", 1)
        app.theme_name.set("Tray check")
        press(app, "Save theme edits")
        self.book(NEW)
        self.assertEqual([r["source_file"] for r in self.rows()], ["packing.txt"])
        self.save_reopen()
        self.show(app.result.themes[0], "kiln.txt", 1)
        app.update_idletasks()
        self.assertEqual((app.winfo_width(), app.winfo_height()), (1024, 705))
        warning, = [w for w in descendants(app) if isinstance(w, ttk.Label)
                    and str(w.cget("textvariable")) == str(app.match_review_text)]
        self.assertIn("Historical codebook match", app.match_review_text.get())
        self.assertIn("original entry cannot be verified in the loaded codebook", app.match_review_text.get())
        self.assertIn("See Audit log", app.match_review_text.get())
        self.assert_visible_inside(warning, app)
        self.assertGreaterEqual(warning.winfo_width(), warning.winfo_reqwidth())
        self.assertGreaterEqual(warning.winfo_height(), warning.winfo_reqheight())
        with patch("themeforge.app.MatchReviewReason") as prompt:
            press(app, "Supports this theme")
            prompt.assert_not_called()
        self.assertIn("Historical codebook match", app.status_text.get())

        def inspect_entries(dialog):
            self.assertIn("Historical match", dialog.location.cget("text"))
            self.assertEqual(dialog.passage.get("evidence.first", "evidence.last"), old_quote.text)
            self.assertEqual(dialog.choice.current(), -1)
            self.assertTrue(dialog.apply_button.instate(["disabled"]))
            self.assertIn(OLD, dialog.definitions.get("1.0", "end"))
            dialog.choice.current(0)
            dialog.show_choice()
            loaded, recorded = dialog.definitions.get("1.0", "end").split("Earlier entry recorded with this passage")
            self.assertIn(NEW, loaded)
            self.assertIn(OLD, recorded)
            self.assertIn(EXAMPLE, recorded)
            press(dialog, "Cancel")

        with patch.object(app, "wait_window", side_effect=inspect_entries) as opened, \
                patch.object(app, "report_callback_exception") as errors:
            press(app, "Apply definition to this quote…")
            if errors.called:
                raise errors.call_args.args[1]
            opened.assert_called_once()
        self.assertEqual([r["source_file"] for r in self.rows()], ["packing.txt"])
        self.assertEqual([r for r in self.rows(audit=True) if r["action"] == "match_review_note"], old_events)
        for suffix in ("md", "csv", "json"):
            path = self.root / f"quotes.{suffix}"
            with patch("themeforge.app.filedialog.asksaveasfilename", return_value=str(path)):
                app.save_report()
            report = path.read_text(encoding="utf-8")
            self.assertIn("The tile was wrapped.", report)
            self.assertNotIn(reason, report)
        pair = compare_match_reviews(coded(self.project), coded(other), CoderLabels("Lark", "Rook"))
        self.assertEqual({r["source_file"] for r in csv.DictReader(pair.csv_text.splitlines())}, {"packing.txt"})
        press(app, "Next unreviewed")
        self.assertIn("No unreviewed", app.status_text.get())

        with patch("themeforge.app.messagebox.askokcancel", return_value=True):
            press(app, "Analyze")
        self.save_reopen()
        retained, fresh, packing = app.result.themes
        self.assertEqual(self.rows(), [])
        self.show(retained, "kiln.txt", 1)
        self.assertIn("Historical codebook match", app.match_review_text.get())
        revised_quote = self.show(fresh, "kiln.txt", 1)
        self.assertEqual(revised_quote, old_quote)
        self.assertIn("No current match review", app.match_review_text.get())
        context = self.decide("Not this match", "Color alone is excluded by the revised definition.")
        self.assertIn(NEW, context)
        self.assertNotIn(OLD, context)
        self.assertEqual(self.rows(), [])
        approved = self.show(fresh, "kiln.txt", 2)
        self.decide("Supports this theme", "The speaker weighed the cooled tray.")
        self.save_reopen()
        row, = self.rows()
        self.assertEqual((row["source_line"], row["quote"], row["match_review_actor"]), ("2", approved.text, "Lark"))
        self.assertEqual((row["source_start"], row["source_end"]), ("71", "119"))
        self.assertEqual(TRAY[int(row["source_start"]):int(row["source_end"])], approved.text)
        history = [r for r in self.rows(audit=True) if r["action"] == "match_review_note"]
        self.assertEqual(history[:2], old_events)
        self.assertEqual(row["match_review_target_id"], history[-1]["target_id"])
        self.assertNotEqual(initial["match_review_target_id"], history[-2]["target_id"])
        def reviewed_definition(event):
            detail = json.loads(event["details"])
            return detail["disposition"], json.loads(detail["match"]["generated_from_codebook_entry"])["description"]
        self.assertEqual([reviewed_definition(r) for r in history[1:]], [("supported", OLD), ("declined", NEW), ("supported", NEW)])

    def test_legacy_codebook_review_without_its_entry_stays_in_the_audit(self):
        app = self.app
        tray, packing = app.result.themes
        self.show(tray, "kiln.txt", 1)
        self.decide("Supports this theme", "The speaker inspected the color.")
        state = app._project_state()
        # Reconstruct the pre-snapshot format, including its original audit target.
        legacy = replace(tray, validation={k: v for k, v in tray.validation.items() if k != "generated_from_codebook_entry"})
        note = state.audit_events[-1]
        details = json.loads(note.details)
        del details["match"]["generated_from_codebook_entry"]
        old_note = replace(note, details=json.dumps(details), target_id=match_id(details["match"]))
        state = replace(state, result=replace(state.result, themes=[legacy, packing]),
                        audit_events=(*state.audit_events[:-1], old_note))
        app._restore_project(state, None)
        self.show(legacy, "kiln.txt", 1)
        self.assertIn("Historical codebook match", app.match_review_text.get())
        self.assertEqual(self.rows(), [])
        history = self.rows(audit=True)
        self.assertEqual((history[-1]["target_id"], history[-1]["details"]), (old_note.target_id, old_note.details))

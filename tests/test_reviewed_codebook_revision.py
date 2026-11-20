"""A revised exclusion can reverse a judgment without changing a lexical score."""
import csv
from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
from shutil import copyfile
import tempfile
from tkinter import simpledialog
from tkinter.scrolledtext import ScrolledText
import unittest
from unittest.mock import patch

from test_evidence_handoff import descendants, export, press
from themeforge.app import ThemeForgeApp
from themeforge.definition_review import CodebookMatchReason
from themeforge.match_reviews import match_id
from themeforge.paired_reviews import compare_match_reviews
from themeforge.project_io import load_project
from themeforge.rigor import CodedProject, CoderLabels
from themeforge.rigor_dialog import RigorDialog


TRAY = ("Clay: I checked the tray after cooling by looking at its color.\n"
        "Glaze: I checked the tray after cooling by weighing it.")
PACK = "Fold: I wrapped the finished tile in a packing sleeve."
OLD = "Tray check after cooling. Exclude: weight-only checks."
NEW = "Tray check after cooling. Exclude: color-only checks."


class ReviewedCodebookRevisionTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
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
            writer.writerows([("code", "description", "example"), ("Tray check", description, "Inspect a cooled tray."),
                              ("Packing", "packing sleeve", "")])
        with patch("themeforge.app.filedialog.askopenfilename", return_value=str(book)):
            press(self.app, "Choose codebook")

    def show(self, theme, source, line):
        self.app._select_theme_by_id(theme.id)
        q, = [q for q in theme.quotes if (q.source_name, q.source_line) == (source, line)]
        self.app._focus_quote_by_id(q.quote_id)
        self.assertEqual(self.app.transcript_widgets[source].get("selected_quote.first", "selected_quote.last"), q.text)
        return q

    def decide(self, button, reason):
        observed = []
        def answer():
            dialog = next(w for w in self.app.winfo_children() if isinstance(w, simpledialog.Dialog))
            try:
                if isinstance(dialog, CodebookMatchReason):
                    area, = [w for w in descendants(dialog) if isinstance(w, ScrolledText)]
                    observed.append(area.get("1.0", "end-1c"))
                    dialog.reason.insert(0, reason)
                    dialog.ok()
            finally:
                if dialog.winfo_exists():
                    dialog.cancel()
        self.app.after(50, answer)
        press(self.app, button)
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
        self.show(packing, "packing.txt", 1)
        self.decide("Supports this theme", "The tile was wrapped.")
        old_quote = self.show(tray, "kiln.txt", 1)
        reason = "The reader checked the color after cooling."
        context = self.decide("Supports this theme", reason)
        self.assertIn(OLD, context)
        self.assertIn("Example: Inspect a cooled tray.", context)
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
        self.assertIn("Historical codebook match", app.match_review_text.get())
        with patch("themeforge.app.CodebookMatchReason") as prompt:
            press(app, "Supports this theme")
            prompt.assert_not_called()
        self.assertIn("Historical codebook match", app.status_text.get())
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

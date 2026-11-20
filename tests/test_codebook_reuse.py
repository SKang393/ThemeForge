"""Cross-study reuse of definitions and reviewer decisions using synthetic data only."""
import csv
from dataclasses import replace
import json
from pathlib import Path
import tempfile
from tkinter import Button
from tkinter import ttk
import unittest
from unittest.mock import patch
import zipfile

from test_evidence_handoff import descendants, export, press
from themeforge.app import ThemeForgeApp
from themeforge.definition_review import CodebookReuseDialog
from themeforge.project_io import load_project
from themeforge.rigor_dialog import RigorDialog

PASSAGE = "I logged the amber shuttle under stencil PEBBLE-47."
SOURCE = "amber-workshop.txt"
EXAMPLE = f"{SOURCE}, line 1: {PASSAGE}"
OLD = "Record a shuttle observation. Include: planned entries."
NEW = "Record a completed shuttle log. Include: written entries. Exclude: planned entries."
A_REASON = "The amber ledger contains the finished mark."
B_REASON = "The blue entry is still planned."


class CodebookReuseTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.a = self.folder / SOURCE
        self.b = self.folder / "blue-workshop.txt"
        self.a.write_text(f"Lark: {PASSAGE}\nLark: The shuttle rested on a drying rack.", encoding="utf-8")
        self.b.write_text("Pine: I plan to log the blue shuttle on Monday.", encoding="utf-8")
        self.app = ThemeForgeApp()
        self.addCleanup(self.app.destroy)
        self.choose(self.a)
        self.app.researcher_name.set("Amber reader")

    def choose(self, path):
        with patch("themeforge.app.filedialog.askopenfilenames", return_value=(str(path),)):
            press(self.app, "Choose files")

    def discover(self, definition):
        path = self.folder / "amber-definitions.csv"
        with path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(("code", "definition", "example", "parent", "include", "exclude"))
            writer.writerow(("Shuttle handling", "Handling a shuttle.", "", "", "", ""))
            writer.writerow(("Shuttle log", definition, EXAMPLE, "Shuttle handling", "separate include", "separate exclude"))
        with patch("themeforge.app.filedialog.askopenfilename", return_value=str(path)):
            press(self.app, "Choose codebook")
        press(self.app, "Analyze")

    def review(self, button, reason):
        theme, = [t for t in self.app.result.themes if
                  t.validation.get("generated_from_codebook_entry") == self.app.codebook_entries[1].snapshot()]
        self.app._select_theme_by_id(theme.id)
        quote, = [q for q in theme.quotes if q.source_line == 1]
        self.app._focus_quote_by_id(quote.quote_id)
        with patch("themeforge.app.CodebookMatchReason") as dialog:
            dialog.return_value.result = reason
            press(self.app, button)
            dialog.assert_called_once()

    def save(self, path):
        with patch("themeforge.app.filedialog.asksaveasfilename", return_value=str(path)):
            press(self.app, "Save project")
        return load_project(path)

    def open(self, path):
        with patch("themeforge.app.filedialog.askopenfilename", return_value=str(path)):
            press(self.app, "Open project")
        self.app.update()

    def rows(self, label):
        press(self.app, "Rigor tools")
        dialog, = [w for w in self.app.winfo_children() if isinstance(w, RigorDialog)]
        tab = dialog.audit_tree.master
        tab.master.select(tab)
        press(dialog, "Select all sources")
        rows = export(dialog, label, self.folder / "table.csv")
        dialog.destroy()
        return rows

    def first_project(self):
        self.discover(OLD)
        self.review("Supports this theme", "A shuttle log was mentioned.")
        self.discover(NEW)
        child, = [t for t in self.app.result.themes if t.name == "Shuttle log"]
        parent, = [t for t in self.app.result.themes if t.name == "Shuttle handling"]
        self.assertFalse(child.parent_theme_id)
        self.app._select_theme_by_id(child.id)
        self.app.parent_theme.set(self.app._theme_label(parent.id))
        press(self.app, "Save hierarchy")
        self.review("Supports this theme", A_REASON)
        path = self.folder / "amber.tfproj"
        state = self.save(path)
        self.open(path)
        self.assertEqual(state, self.app._project_state())
        self.assertEqual(next(t for t in state.result.themes if t.id == child.id).parent_theme_id, parent.id)
        self.assertEqual(state.settings.codebook_entries[1].description, NEW)
        self.assertEqual(state.settings.codebook_entries[1].examples, (EXAMPLE,))
        row, = self.rows("Export supported matches CSV")
        self.assertEqual((row["source_file"], row["source_start"], row["source_end"]), (SOURCE, "6", "57"))
        self.assertEqual((row["quote"], row["match_review_actor"], row["match_review_reason"]),
                         (PASSAGE, "Amber reader", A_REASON))
        return path, state

    def transfer(self, scope, action="OK"):
        previews = []

        def answer(dialog, window):
            self.assertIs(window.grab_current(), dialog)
            widgets = list(descendants(dialog))
            choices = {w.cget("text"): w for w in widgets if isinstance(w, ttk.Radiobutton)}
            self.assertEqual(dialog.choice.get(), "Start without a codebook")
            for name in ("Names, definitions and examples", scope):
                choices[name].invoke()
            previews.append(dialog.preview.get("1.0", "end-1c"))
            button, = [w for w in widgets if isinstance(w, Button) and w.cget("text") == action]
            button.invoke()

        with patch.object(CodebookReuseDialog, "wait_window", autospec=True, side_effect=answer), \
                patch("themeforge.app.messagebox.askyesnocancel", return_value=False):
            self.choose(self.b)
        preview, = previews
        return preview

    def test_preview_then_reuse_definitions_without_the_old_example_or_decision(self):
        original, first = self.first_project()
        original_bytes = original.read_bytes()
        preview = self.transfer("Names and definitions only")
        self.assertIn(NEW, preview)
        self.assertNotIn(OLD, preview)
        self.assertNotIn(EXAMPLE, preview)
        self.assertEqual(self.app.codebook_entries,
                         tuple(replace(entry, examples=()) for entry in first.settings.codebook_entries))
        self.assertIsNone(self.app.codebook_path)
        self.assertEqual(self.app.audit_events, [])
        self.assertIsNone(self.app.result)
        self.assertEqual(self.app.researcher_name.get(), "")
        self.assertEqual(self.app.codebook_summary.get(), "Project codebook | 2 entries")
        self.app.researcher_name.set("Blue reader")
        press(self.app, "Analyze")
        self.assertEqual(self.rows("Export supported matches CSV"), [])
        self.assertTrue(all(not t.parent_theme_id for t in self.app.result.themes))
        self.review("Not this match", B_REASON)
        path = self.folder / "blue.tfproj"
        second = self.save(path)
        self.open(path)
        self.assertEqual(self.app._project_state(), second)
        self.assertEqual(self.rows("Export supported matches CSV"), [])
        audit = self.rows("Export audit CSV")
        self.assertEqual([r["action"] for r in audit], ["analysis_run", "match_review_note"])
        self.assertEqual(audit[-1]["actor"], "Blue reader")
        decision = json.loads(audit[-1]["details"])
        self.assertEqual((decision["reason"], decision["disposition"]), (B_REASON, "declined"))
        quote = decision["match"]["quote"]
        self.assertEqual(quote["source_name"], self.b.name)
        self.assertEqual(self.b.read_text(encoding="utf-8")[quote["source_start"]:quote["source_end"]], quote["text"])
        self.assertNotIn(audit[-1]["target_id"], [e.target_id for e in first.audit_events])
        with zipfile.ZipFile(path) as archive:
            member, = archive.namelist()
            payload = archive.read(member).decode()
        for marker in (PASSAGE, SOURCE, "Amber reader", "amber-definitions.csv", OLD, A_REASON,
                       "separate include", "separate exclude"):
            self.assertNotIn(marker, payload)
            self.assertNotIn(marker, str(audit))
        self.assertEqual(original.read_bytes(), original_bytes)

    def test_cancel_and_explicit_reuse_with_examples(self):
        path, first = self.first_project()
        self.assertIn(EXAMPLE, self.transfer("Names, definitions and examples", "Cancel"))
        self.assertEqual((self.app.project_path, self.app._project_state()), (path, first))
        self.assertIn(EXAMPLE, self.transfer("Names, definitions and examples"))
        copied = self.save(self.folder / "with-examples.tfproj")
        self.assertEqual(copied.settings.codebook_entries, first.settings.codebook_entries)
        self.assertEqual(copied.audit_events, ())
        self.assertIsNone(copied.result)

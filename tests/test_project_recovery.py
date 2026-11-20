"""Recover an open synthetic study and distinguish the saved source from a later import."""
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from textwrap import dedent
from tkinter import ttk
import unittest
from unittest.mock import patch

from test_evidence_handoff import descendants, export, press
from themeforge.app import ThemeForgeApp
from themeforge.project_io import load_project
from themeforge.rigor_dialog import RigorDialog


PASSAGE = "I marked the return card after counting the tiles."
COUNT = "Ash: I marked the return card before counting the tiles.\nElm: " + PASSAGE
HANDOFF = "I passed the crate to the next shift."
SHIFT = f"Oak: {HANDOFF}\nYew: {HANDOFF}"
OLD = "Mark a return card. Exclude: closing counts."
REVISED = "Mark a return card. Exclude: marks made before counting."


class ProjectRecoveryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        folder = self.root / "working"
        folder.mkdir()
        self.original = folder / "tiles.tfproj"
        self.count = self.root / "count.txt"
        self.shift = self.root / "shift.txt"
        self.count.write_text(COUNT, encoding="utf-8")
        self.shift.write_text(SHIFT, encoding="utf-8")
        with patch.object(ThemeForgeApp, "maxsize", return_value=(1024, 705)):
            self.app = ThemeForgeApp()
        self.addCleanup(self.app.destroy)
        self.import_sources()
        self.app.researcher_name.set("Wren")
        self.app.transcript_notebook.select(self.app.transcript_frames[self.shift.name])
        self.app.theme_name.set("Crate handoff")
        self.app.transcript_widgets[self.shift.name].tag_add("sel", "2.5", "2.end")
        press(self.app, "New code from selection")
        self.manual = self.app._current_theme()
        quote, = self.manual.quotes
        self.assertEqual((quote.source_name, quote.source_start, quote.source_end, quote.text),
                         ("shift.txt", SHIFT.rindex(HANDOFF), len(SHIFT), HANDOFF))
        self.discover(OLD)
        self.review(1, "Supports this theme", "The card records the initial count.")
        self.discover(REVISED)
        self.review(2, "Supports this theme", "The card records a completed count.")
        with patch("themeforge.app.filedialog.asksaveasfilename", return_value=str(self.original)):
            press(self.app, "Save project")
        self.good = load_project(self.original)
        self.assertEqual(self.good, self.app._project_state())

    def import_sources(self):
        with patch("themeforge.app.filedialog.askopenfilenames", return_value=[str(self.count), str(self.shift)]):
            press(self.app, "Choose files")

    def discover(self, description):
        book = self.root / "codes.csv"
        book.write_text(f"code,description\nReturn card,{description}\n", encoding="utf-8")
        with patch("themeforge.app.filedialog.askopenfilename", return_value=str(book)):
            press(self.app, "Choose codebook")
        press(self.app, "Analyze")

    def review(self, line, button, reason):
        theme, = [t for t in self.app.result.themes if t.name == "Return card"]
        self.app._select_theme_by_id(theme.id)
        q, = [q for q in theme.quotes if q.source_name == "count.txt" and q.source_line == line]
        self.app._focus_quote_by_id(q.quote_id)
        with patch("themeforge.app.CodebookMatchReason") as dialog:
            dialog.return_value.result = reason
            press(self.app, button)
        dialog.assert_called_once()

    def open(self, path):
        with patch("themeforge.app.filedialog.askopenfilename", return_value=str(path)):
            press(self.app, "Open project")
        self.app.update()

    def supported(self):
        press(self.app, "Rigor tools")
        dialog = next(w for w in self.app.winfo_children() if isinstance(w, RigorDialog))
        tab = dialog.audit_tree.master
        tab.master.select(tab)
        press(dialog, "Select all sources")
        rows = export(dialog, "Export supported matches CSV", self.root / "supported.csv")
        dialog.destroy()
        return rows

    def test_failed_save_recovers_pending_decision_and_keeps_source_revision(self):
        original_bytes = self.original.read_bytes()
        row, = self.supported()
        self.assertEqual((row["quote"], row["source_line"], int(row["source_start"]), int(row["source_end"])),
                         (PASSAGE, "2", COUNT.index(PASSAGE), len(COUNT)))
        self.assertEqual(row["source_sha256"], sha256(COUNT.encode()).hexdigest())
        reviews = [json.loads(e.details) for e in self.good.audit_events if e.action == "match_review_note"]
        self.assertEqual([json.loads(r["match"]["generated_from_codebook_entry"])["description"] for r in reviews],
                         [OLD, REVISED])

        # Keep the name, cited text and offsets identical. Only the source revision changes.
        revised_source = COUNT + "\nElm: The spare tray arrived after packing."
        self.count.write_text(revised_source, encoding="utf-8")
        copy = self.root / "recovered.tfproj"
        with patch("themeforge.project_io.os.replace", side_effect=PermissionError("destination is locked")):
            self.review(2, "Not this match", "On rereading, the card records no completed total.")
            self.assertIn("autosave failed", self.app.status_text.get().lower())
            self.assertIn("Save as…", self.app.status_text.get())
            self.app.update_idletasks()
            save_as, = [w for w in descendants(self.app) if isinstance(w, ttk.Button) and w.cget("text") == "Save as…"]
            for widget in save_as.master.winfo_children():
                self.assertTrue(widget.winfo_viewable())
                x = widget.winfo_rootx() - self.app.winfo_rootx()
                y = widget.winfo_rooty() - self.app.winfo_rooty()
                self.assertGreaterEqual(min(x, y), 0)
                self.assertLessEqual(x + widget.winfo_width(), self.app.winfo_width())
                self.assertLessEqual(y + widget.winfo_height(), self.app.winfo_height())
            with patch("themeforge.app.messagebox.showerror") as error:
                press(self.app, "Save project")
                error.assert_called_once()
            pending = self.app._project_state()
            self.assertTrue(self.app._has_unsaved_work())
            self.assertEqual(pending.audit_events[:-1], self.good.audit_events)
            self.assertEqual(json.loads(pending.audit_events[-1].details)["disposition"], "declined")
            self.assertEqual(self.original.read_bytes(), original_bytes)
            self.assertEqual(list(self.original.parent.iterdir()), [self.original])

            # Cancel, a name collision, and a successful new file all use the visible control.
            with patch("themeforge.app.filedialog.asksaveasfilename", return_value=""):
                press(self.app, "Save as…")
            copy.write_bytes(b"another study already claimed this name")
            with patch("themeforge.app.filedialog.asksaveasfilename", return_value=str(copy)), \
                    patch("themeforge.app.messagebox.showerror") as error:
                press(self.app, "Save as…")
                error.assert_called_once()
            self.assertEqual(copy.read_bytes(), b"another study already claimed this name")
            self.assertEqual(self.app.project_path, self.original)
            self.assertEqual(self.app._project_state(), pending)
            self.assertTrue(self.app._has_unsaved_work())
            copy = self.root / "recovered-2.tfproj"
            draft = "Working from the recovered copy."
            self.app.document_memo_text.insert("1.0", draft)
            with patch("themeforge.app.filedialog.asksaveasfilename", return_value=str(copy)):
                press(self.app, "Save as…")
        self.assertEqual(self.app.project_path, copy)
        self.assertIn("unfinished editor changes", self.app.status_text.get())
        self.assertTrue(self.app._has_unsaved_work())
        self.assertEqual(load_project(copy), pending)
        self.assertEqual(load_project(self.original), self.good)
        self.assertIn(self.manual, pending.result.themes)
        self.assertEqual(self.app.document_memo_text.get("1.0", "end-1c"), draft)
        press(self.app, "Save document memo")
        applied = load_project(copy)
        self.assertEqual(applied.audit_events[:-1], pending.audit_events)
        self.assertEqual(applied.audit_events[-1].action, "document_memo_updated")
        self.assertEqual(next(d.memo for d in applied.documents if d.name == self.count.name), draft)
        self.open(copy)
        self.assertIn("saved transcripts", self.app.status_text.get())
        self.assertEqual(self.app._project_state(), applied)
        self.assertFalse(self.app._has_unsaved_work())
        self.assertEqual(self.supported(), [])
        self.assertEqual(self.app.transcript_widgets["count.txt"].get("1.0", "end-1c"), COUNT)
        self.assertEqual(self.original.read_bytes(), original_bytes)

        self.open(self.original)
        self.assertEqual(self.app._project_state(), self.good)
        self.assertEqual(self.supported(), [row])
        with patch("themeforge.app.messagebox.askyesnocancel", side_effect=[False, True]):
            self.import_sources()
        self.assertIsNone(self.app.project_path)
        self.assertEqual(self.app.audit_events, [])
        self.assertIsNone(self.app.result)
        self.assertEqual(self.app.documents[0].text, revised_source)
        press(self.app, "Analyze")
        self.assertEqual(self.supported(), [])
        self.app.researcher_name.set("Wren")
        self.review(2, "Supports this theme", "Independent review of this revision.")
        current, = self.supported()
        self.assertEqual(current["source_sha256"], sha256(revised_source.encode()).hexdigest())
        self.assertEqual((current["quote"], current["source_start"], current["source_end"]),
                         (row["quote"], row["source_start"], row["source_end"]))
        self.assertNotEqual(current["match_review_target_id"], row["match_review_target_id"])
        self.assertEqual(self.original.read_bytes(), original_bytes)

    def test_writer_exit_mid_archive_leaves_the_last_project_reopenable(self):
        original = self.original.read_bytes()
        child = dedent("""
            from dataclasses import replace
            from pathlib import Path
            import os
            import sys
            from unittest.mock import patch
            import zipfile
            from themeforge.project_io import load_project, save_project

            def interrupt(archive, name, text):
                member = archive.open(name, "w")
                member.write(text[:100].encode("utf-8"))
                archive.fp.flush()
                os._exit(73)  # Stop before closing the member or running cleanup.

            path = Path(sys.argv[1])
            pending = replace(load_project(path), researcher_name="Finch")
            with patch.object(zipfile.ZipFile, "writestr", interrupt):
                save_project(path, pending)
        """)
        stopped = subprocess.run([sys.executable, "-c", child, str(self.original)], capture_output=True, timeout=15)
        self.assertEqual(stopped.returncode, 73, stopped.stderr)
        self.assertEqual(self.original.read_bytes(), original)
        self.app = ThemeForgeApp()
        self.addCleanup(self.app.destroy)
        self.open(self.original)
        self.assertEqual(self.app._project_state(), self.good)
        row, = self.supported()
        self.assertEqual(row["match_review_target_id"], self.good.audit_events[-1].target_id)

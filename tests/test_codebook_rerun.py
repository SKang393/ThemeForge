"""Changed codebooks must not lose current suggestions to reused group or quote IDs."""
import csv
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from themeforge.analysis import analyze_documents
from themeforge.app import ThemeForgeApp
from themeforge.project_io import load_project
from themeforge.rigor_dialog import RigorDialog


class HiddenApp(ThemeForgeApp):
    def _build_layout(self):
        self.withdraw()
        super()._build_layout()


class CodebookRerunTests(unittest.TestCase):
    def test_revised_definitions_keep_all_fresh_matches_but_no_prior_endorsement(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            src = root / "studio.txt"
            text = ("Jo: I arranged copper lanterns inside the studio.\n"
                    "Ren: We recorded painted banners outside the theatre.\n"
                    "Liv: The copper lanterns illuminated painted banners.")
            src.write_text(text, encoding="utf-8")
            old_book, new_book = root / "old.csv", root / "new.csv"
            old_book.write_text("code,description\nSignal,copper lanterns\n", encoding="utf-8")
            new_book.write_text("code,description\nSignal,painted banners\nLights,copper lanterns\n", encoding="utf-8")
            app = HiddenApp()
            self.addCleanup(app.destroy)
            with patch("themeforge.app.filedialog.askopenfilenames", return_value=[str(src)]):
                app.open_transcript()
            with patch("themeforge.app.filedialog.askopenfilename", return_value=str(old_book)):
                app.open_codebook()
            app.researcher_name.set("Reader")
            app.analyze()
            first, = app.result.themes
            self.assertEqual([q.source_line for q in first.quotes], [1, 3])
            app.quote_memo_text.insert("1.0", "Compare the account with rehearsal notes.")
            app.save_quote_memo()
            edited = app.result.themes[0]
            with patch("themeforge.app.review_definition", return_value=0):
                app.code_match_to_definition()
            manual = app._current_theme()
            original = root / "before.tfproj"
            with patch("themeforge.app.filedialog.asksaveasfilename", return_value=str(original)):
                self.assertTrue(app.save_project())
            before = original.read_bytes()
            working = root / "after.tfproj"
            working.write_bytes(before)
            with patch("themeforge.app.filedialog.askopenfilename", return_value=str(working)):
                app.open_project()
            with patch("themeforge.app.filedialog.askopenfilename", return_value=str(new_book)):
                app.open_codebook()
            independent = analyze_documents(app.documents, app._analysis_settings())
            self.assertEqual([(g.name, [q.source_line for q in g.quotes]) for g in independent.themes],
                             [("Signal", [2, 3]), ("Lights", [1, 3])])
            app.analyze()
            groups = app.result.themes
            self.assertEqual(len(groups), 4)
            retained, applied, *fresh = groups
            self.assertEqual((retained.id, retained.name, retained.quotes, retained.memo, retained.score),
                             (edited.id, edited.name, edited.quotes, edited.memo, edited.score))
            self.assertEqual(applied, manual)
            self.assertEqual(len({g.id for g in groups}), len(groups))
            self.assertEqual([(g.name, g.quotes, g.score) for g in fresh],
                             [(g.name, g.quotes, g.score) for g in independent.themes])
            self.assertIn("Retained", app.theme_list.get(0))
            self.assertTrue(all("Fresh" in app.theme_list.get(i) for i in (2, 3)))
            self.assertEqual([json.loads(g.validation["generated_from_codebook_entry"])["description"]
                              for g in (retained, *fresh)], ["copper lanterns", "painted banners", "copper lanterns"])
            self.assertTrue(all(not q.memo and not q.manual_definition_assignment for g in fresh for q in g.quotes))
            # An untouched rerun replaces its preceding candidates rather than accumulating copies.
            app.analyze()
            self.assertEqual(app.result.themes, groups)
            with patch("themeforge.app.filedialog.askopenfilename", return_value=str(working)):
                app.open_project()
            self.assertEqual(app.result.themes, groups)
            self.assertEqual(load_project(working).result.themes, groups)
            linked, = [json.loads(e.details) for e in app.audit_events if e.action == "assignment_source_match"]
            self.assertEqual(json.loads(linked["match"]["generated_from_codebook_entry"]), linked["applied_definition"])
            app.open_rigor_tools()
            dialog = next(w for w in app.winfo_children() if isinstance(w, RigorDialog))
            dialog.withdraw()
            out = root / "assignments.csv"
            with patch("themeforge.rigor_dialog.filedialog.asksaveasfilename", return_value=str(out)):
                dialog._export_current_assignments()
            with out.open(encoding="utf-8", newline="") as f:
                row, = csv.DictReader(f)
            self.assertEqual((row["assignment_id"], row["assigned_by"], row["definition"], row["other_current_rows_excluded"]),
                             (manual.quotes[0].quote_id, "Reader", "copper lanterns", "6"))
            self.assertEqual(row["passage"], text[int(row["source_start"]):int(row["source_end"])])
            self.assertEqual(original.read_bytes(), before)

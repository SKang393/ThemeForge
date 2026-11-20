import csv
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from themeforge.analysis import analyze_documents
from themeforge.analysis_types import AnalysisSettings, CodebookEntry, TranscriptDocument
from themeforge.app import ThemeForgeApp
from themeforge.manual_editing import ManualSelection, code_selected_text, preserve_manual_themes, set_theme_parent
from themeforge.project_io import ProjectState


class DiscoveryHierarchyTests(unittest.TestCase):
    def test_reused_then_absent_parent_id_preserves_the_chosen_group(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "workshop.txt"
            passage = "Bright bells ring above the tower stairs."
            text = ("A: We fold the blue sail before sunset.\nB: The folded sail fits inside the canvas bag.\n"
                    "C: Brush loose clay from the kiln shelf.\nD: The clay tiles dry on the kiln shelf.\n"
                    f"E: {passage}\nF: {passage}\nG: A brass bell rings across the tower.")
            source.write_text(text, encoding="utf-8")
            app = ThemeForgeApp()
            self.addCleanup(app.destroy)
            app.withdraw()
            with patch("themeforge.app.filedialog.askopenfilenames", return_value=[str(source)]):
                app.open_transcript()
            app.researcher_name.set("Fixture coder")
            app.theme_name.set("Tower signal")
            app.transcript_widgets[source.name].tag_add("sel", "6.3", "6.end")
            app.code_selection_to_new_theme()
            app.quote_memo_text.insert("1.0", "Only the second occurrence.")
            app.save_quote_memo()
            app.theme_memo_text.insert("1.0", "Signal during the second shift.")
            app.save_theme_memo()
            original = app._current_theme()
            quote, = original.quotes
            self.assertEqual((quote.text, quote.source_start, quote.source_end, quote.source_line),
                             (passage, text.rindex(passage), text.rindex(passage) + len(passage), 6))

            app.theme_count.set(2)
            app.analyze()
            self.assertEqual(app._current_theme(), original)
            parent, = [t for t in app.result.themes if any(q.source_line == 5 for q in t.quotes)]
            app.parent_theme.set(app._theme_label(parent.id))
            app.save_theme_hierarchy()
            child = app._current_theme()
            self.assertEqual(child.parent_theme_id, parent.id)
            project = root / "workshop.tfproj"
            with patch("themeforge.app.filedialog.asksaveasfilename", return_value=str(project)):
                self.assertTrue(app.save_project())

            app.theme_count.set(3)
            candidates = analyze_documents(app.documents, app._analysis_settings()).themes
            reused, = [t for t in candidates if t.id == parent.id]
            self.assertNotEqual(reused.name, parent.name)
            app.analyze()
            groups = {t.id: t for t in app.result.themes}
            kept = groups[child.parent_theme_id]
            self.assertEqual(replace(kept, validation=parent.validation), parent)
            self.assertIn("hierarchy", kept.validation["review_status"])
            self.assertTrue(all(kept.validation[key] == value for key, value in parent.validation.items()
                                if key not in ("reanalysis_role", "review_status")))
            self.assertIn("[Retained]", app.theme_list.get(app.result.themes.index(kept)))
            self.assertEqual(groups[child.id], child)
            self.assertEqual(len(groups), len(candidates) + 2)
            fresh = [t for t in app.result.themes if t.id not in (child.id, parent.id)]
            self.assertEqual([(t.name, t.quotes, t.score) for t in fresh],
                             [(t.name, t.quotes, t.score) for t in candidates])
            self.assertNotEqual(next(t for t in fresh if t.name == reused.name).id, parent.id)
            app.analyze()
            self.assertEqual(app.result.themes, list(groups.values()))

            app.theme_count.set(1)
            app.analyze()
            with patch("themeforge.app.filedialog.askopenfilename", return_value=str(project)):
                app.open_project()
            app._select_theme_by_id(child.id)
            self.assertEqual(app._current_theme(), child)
            self.assertEqual(app.parent_theme.get(), f"{parent.id}: {parent.name}")
            widget = app.transcript_widgets[source.name]
            self.assertEqual(widget.index("selected_quote.first"), "6.3")
            self.assertEqual(widget.get("selected_quote.first", "selected_quote.last"), passage)
            for suffix in (".json", ".csv", ".md"):
                target = root / ("quotes" + suffix)
                with patch("themeforge.app.filedialog.asksaveasfilename", return_value=str(target)):
                    app.save_report()
            report = json.loads((root / "quotes.json").read_text(encoding="utf-8"))
            exported = {t["id"]: t for t in report["themes"]}
            manual = exported[child.id]
            self.assertEqual(exported[manual["parent_theme_id"]]["name"], parent.name)
            self.assertEqual(manual["memo"], "Signal during the second shift.")
            row, = manual["quotes"]
            self.assertEqual((row["quote_id"], row["memo"], row["text"], row["source_name"], row["source_line"]),
                             (quote.quote_id, "Only the second occurrence.", passage, source.name, 6))
            self.assertEqual((row["source_location_status"], row["source_start"], row["source_end"], row["relevance"]),
                             ("exact_span", quote.source_start, quote.source_end, None))
            other_occurrences = [q for t in exported.values() for q in t["quotes"]
                                 if q["text"] == passage and q["quote_id"] != quote.quote_id]
            self.assertEqual({q["source_line"] for q in other_occurrences}, {5, 6})
            self.assertTrue(all(not q["memo"] and q["relevance"] is not None for q in other_occurrences))
            with (root / "quotes.csv").open(encoding="utf-8", newline="") as stream:
                row, = [r for r in csv.DictReader(stream) if r["quote_id"] == quote.quote_id]
            self.assertEqual((row["theme_id"], row["quote"], int(row["source_start"]), int(row["source_end"]),
                              row["relevance"], row["source_location_status"]),
                             (child.id, passage, quote.source_start, quote.source_end, "", "exact_span"))
            markdown = (root / "quotes.md").read_text(encoding="utf-8")
            self.assertIn(f"Earlier suggestions kept as hierarchy parents: {parent.id}.", markdown)
            self.assertIn(f"Recorded line: 6 | Recorded speaker: {quote.speaker} | "
                          f"Quote ID: {quote.quote_id} | Location: exact_span", markdown)
            self.assertIn(f"> {passage}\n", markdown)

            app.theme_count.set(2)
            app.analyze()
            self.assertIn(parent, app.result.themes)
            self.assertEqual(len(app.result.themes), 3)  # One manual group and the original two candidates.

    def test_unchanged_parent_with_capped_examples_is_reused_once(self):
        text = "A: Stack the copper trays by the kiln.\nB: Stack the copper trays by the kiln."
        document = TranscriptDocument("kiln.txt", text)
        generated = analyze_documents([document], AnalysisSettings(theme_count=1, quotes_per_theme=1))
        parent, = generated.themes
        self.assertEqual((parent.quote_count, len(parent.quotes)), (2, 1))
        start = text.rindex("copper trays")
        selection = ManualSelection(document.name, "copper trays", 2, start, start + len("copper trays"))
        coded = code_selected_text(generated, "", selection, "Stacking", document=document, actor="Fixture coder")
        child = coded.themes[-1]
        previous = set_theme_parent(coded, child.id, parent.id)
        result = preserve_manual_themes(previous, generated)
        self.assertEqual(result.themes, [previous.themes[-1], parent])

    def test_changed_codebook_parent_with_same_label_and_span_remains_distinct(self):
        document = TranscriptDocument("kiln.txt", "A: Stack the copper trays by the kiln.")
        old = CodebookEntry("Copper trays", "Stack the copper trays by the kiln.")
        new = CodebookEntry("Copper trays", "Carry the copper trays to the kiln.")
        earlier = analyze_documents([document], AnalysisSettings(codebook_entries=(old,)))
        current = analyze_documents([document], AnalysisSettings(codebook_entries=(new,)))
        parent, = earlier.themes
        candidate, = current.themes
        phrase = "copper trays"
        start = document.text.index(phrase)
        selection = ManualSelection(document.name, phrase, 1, start, start + len(phrase))
        coded = code_selected_text(earlier, "", selection, "Stacking", document=document, actor="Fixture coder")
        previous = set_theme_parent(coded, coded.themes[-1].id, parent.id)
        child, kept, fresh = preserve_manual_themes(previous, current).themes
        self.assertEqual((child.parent_theme_id, kept.id), (parent.id, parent.id))
        self.assertEqual((kept.name, fresh.name), (old.name, new.name))
        self.assertNotEqual(kept.id, fresh.id)
        self.assertEqual([g.validation["generated_from_codebook_entry"] for g in (kept, fresh)],
                         [g.validation["generated_from_codebook_entry"] for g in (parent, candidate)])

    def test_unresolved_parent_from_older_run_cannot_bind_to_a_new_suggestion(self):
        document = TranscriptDocument("loom.txt", "A: We thread the wooden loom before sunrise.")
        settings = AnalysisSettings(theme_count=1)
        generated = analyze_documents([document], settings)
        candidate, = generated.themes
        start = document.text.index("wooden loom")
        selection = ManualSelection(document.name, "wooden loom", 1, start, start + len("wooden loom"))
        coded = code_selected_text(generated, "", selection, "Setup", document=document, actor="Fixture coder")
        # Earlier reruns could leave the child pointing to a group that had disappeared.
        child = replace(coded.themes[-1], parent_theme_id=candidate.id)
        orphaned = replace(coded, themes=[child])
        app = ThemeForgeApp()
        self.addCleanup(app.destroy)
        app.withdraw()
        app._restore_project(ProjectState((), None, (document,), settings, orphaned, "Fixture coder"), None)
        app.analyze()
        self.assertEqual(app._current_theme(), child)
        self.assertNotIn(candidate.id, [t.id for t in app.result.themes])
        self.assertEqual(app.parent_theme.get(), f"Unavailable parent ({candidate.id})")
        app.save_theme_hierarchy()
        self.assertEqual(app._current_theme(), child)

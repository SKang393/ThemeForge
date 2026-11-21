"""Independent fictional reviewers used two editions of a workshop codebook."""
import csv
from dataclasses import replace
import io
import json
from pathlib import Path
from shutil import copyfile
import tempfile
import unittest
from unittest.mock import patch

from test_evidence_handoff import export, press
from test_paired_match_reviews import answer_paired_scope
from themeforge.analysis import analyze_documents
from themeforge.analysis_types import AnalysisSettings, CodebookEntry, TranscriptDocument
from themeforge.app import ThemeForgeApp
from themeforge.paired_reviews import compare_match_reviews
from themeforge.project_io import load_project
from themeforge.rigor import CodedProject, CoderLabels
from themeforge.rigor_dialog import RigorDialog


TEXT = ("Maker: The spindle hummed during my spindle inspection.\n"
        "Maker: I measured the spindle during my spindle inspection.\n"
        "Maker: I counted the parcels on the loading dock.\n"
        "Maker: I soldered the broken copper bell.")
ORIGINAL = (("Spindle inspection", "Spindle inspection. Exclude: plans."),
            ("Parcel tally", "Counted parcels at the loading dock."),
            ("Bell repair", "Soldered copper bell."))
REVISED = (("Spindle inspection", "Measured spindle inspection. Exclude: sound alone."),
           ("Parcel tally", "Counted  parcels at the\tloading dock."), ORIGINAL[2])
NOTES = {
    "Flint": [("supported", "The hum was an inspection cue."), ("supported", "The spindle was measured."),
              ("supported", "The parcels were counted."), ("supported", "The bell was soldered.")],
    "Ochre": [("declined", "Sound alone is excluded."), ("supported", "There was a direct measurement."),
              ("declined", "The tally unit is unclear."), ("supported", "The bell was repaired.")],
}


class PairedDefinitionTests(unittest.TestCase):
    def test_changed_examples_zero_denominator_and_ambiguous_code_names(self):
        docs = (TranscriptDocument("bench.txt", TEXT.splitlines()[0]),
                TranscriptDocument("bells.txt", TEXT.splitlines()[3]))
        spindle = CodebookEntry(*ORIGINAL[0], examples=("An audible hum.",))
        changed = replace(spindle, examples=("A measured diameter.",))

        def project(*entries):
            entries = (CodebookEntry(*ORIGINAL[2]), *entries)
            result = analyze_documents(docs, AnalysisSettings(codebook_entries=entries))
            return CodedProject(docs, result, codebook_entries=entries)

        left, right = project(spindle), project(changed)
        readers = CoderLabels("Flint", "Ochre")
        bench = frozenset({"bench.txt"})
        report = compare_match_reviews(left, right, readers, source_names=bench)
        self.assertEqual(report.outcome_counts, {"different_definitions": 1})
        _, *counts = csv.reader(io.StringIO(report.counts_csv))
        self.assertEqual([int(value) for _, value in counts], [0, 0, 0, 0, 0, 0, 0, 1, 1, 0, 0])

        # A second possible entry at the same location prevents a revision pairing.
        ambiguous = project(changed, replace(changed, description="Inspect the spindle surface."))
        report = compare_match_reviews(left, ambiguous, readers)
        self.assertEqual([m.quote.source_name for m in report.matches], ["bells.txt"])
        self.assertEqual((report.current_excluded, report.comparison_excluded), (1, 2))
        with self.assertRaisesRegex(ValueError, "no verified shared matches"):
            compare_match_reviews(left, ambiguous, readers, source_names=bench)

    def test_saved_reviews_under_revised_and_respaced_definitions(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        app = ThemeForgeApp()
        self.addCleanup(app.destroy)
        source = root / "shop.txt"
        source.write_text(TEXT, encoding="utf-8")
        with patch("themeforge.app.filedialog.askopenfilenames", return_value=[str(source)]):
            press(app, "Choose files")

        def book(entries):
            path = root / "codes.csv"
            with path.open("w", encoding="utf-8", newline="") as file:
                csv.writer(file).writerows([("code", "definition"), *entries])
            with patch("themeforge.app.filedialog.askopenfilename", return_value=str(path)):
                press(app, "Choose codebook")

        def reopen(path):
            with patch("themeforge.app.filedialog.askopenfilename", return_value=str(path)):
                press(app, "Open project")

        book(ORIGINAL)
        press(app, "Analyze")
        seed = root / "seed.tfproj"
        with patch("themeforge.app.filedialog.asksaveasfilename", return_value=str(seed)):
            self.assertTrue(app.save_project())

        saved = {}
        targets = {}
        for actor, decisions in NOTES.items():
            path = root / (actor + ".tfproj")
            working = root / f"working-{actor}.tfproj"
            copyfile(seed, working)
            reopen(working)
            app.researcher_name.set(actor)
            if actor == "Ochre":
                book(REVISED)
                with patch("themeforge.app.messagebox.askokcancel", return_value=True):
                    press(app, "Analyze")
            for theme in app.result.themes:
                app._select_theme_by_id(theme.id)
                for quote in theme.quotes:
                    app._focus_quote_by_id(quote.quote_id)
                    self.assertEqual(TEXT[quote.source_start:quote.source_end], quote.text)
                    verdict, reason = decisions[quote.source_line - 1]
                    with patch("themeforge.app.MatchReviewReason") as prompt:
                        prompt.return_value.result = reason
                        app.review_current_match(verdict)
                        prompt.assert_called_once()
            # Independently select different boundaries under one revised and one shared definition.
            widget = app.transcript_widgets[source.name]
            for entry, line in ((0, 1), (2, 4)):
                widget.tag_remove("sel", "1.0", "end")
                widget.tag_add("sel", f"{line}.7", f"{line}.end" if actor == "Flint" else f"{line}.17")
                app.definition_box.current(entry)
                with patch.object(app, "wait_window", side_effect=lambda d: press(d, "Apply selected definition")):
                    press(app, "Apply codebook entry…")
            with patch("themeforge.app.filedialog.asksaveasfilename", return_value=str(path)):
                press(app, "Save as…")
            self.assertEqual(app.project_path, path)
            saved[path] = path.read_bytes()
            events = [e for e in load_project(path).audit_events if e.action == "match_review_note"]
            self.assertEqual(len(events), 4)
            for event in events:
                snapshot = json.loads(event.details)["match"]
                line = snapshot["quote"]["source_line"]
                targets[actor, line] = event.target_id, event.timestamp_utc
                entries = ORIGINAL if actor == "Flint" else REVISED
                self.assertEqual(json.loads(snapshot["generated_from_codebook_entry"])["description"],
                                 dict(entries)[snapshot["theme_name"]])

        reopen(root / "Flint.tfproj")
        press(app, "Rigor tools")
        dialog, = [w for w in app.winfo_children() if isinstance(w, RigorDialog)]
        with patch("themeforge.rigor_dialog.filedialog.askopenfilename", return_value=str(root / "Ochre.tfproj")):
            press(dialog, "Choose comparison project")
        with answer_paired_scope():
            rows = export(dialog, "Export paired match reviews", root / "detail.csv")

        def choose_counts(scope, _window):
            scope.select_all()
            self.assertIn("Comparable pairs (2 of 4) · Different codebook entries (2)", scope.preview.get())
            scope.ok()

        with answer_paired_scope(choose_counts, counts_only=True):
            export(dialog, "Export paired match reviews", root / "counts.csv")
        with (root / "counts.csv").open(encoding="utf-8", newline="") as file:
            _, *totals = csv.reader(file)
        self.assertEqual([r["review_comparison"] for r in rows],
                         ["different_definitions", "different_definitions", "disagreement", "both_supported"])
        for line, row in enumerate(rows, 1):
            start, end = int(row["source_start"]), int(row["source_end"])
            self.assertEqual((row["source_file"], int(row["source_line"]), row["quote"]),
                             ("shop.txt", line, TEXT[start:end]))
            for side, actor in (("current", "Flint"), ("comparison", "Ochre")):
                self.assertEqual(row[f"{side}_reviewer"], actor)
                self.assertEqual((row[f"{side}_disposition"], row[f"{side}_reason"]), NOTES[actor][line - 1])
                self.assertEqual((row[f"{side}_review_target_id"], row[f"{side}_time_utc"]), targets[actor, line])
            if line <= 3:
                self.assertNotEqual(targets["Flint", line][0], targets["Ochre", line][0])
                self.assertEqual(row["match_review_target_id"], "")
        self.assertEqual([int(count) for _, count in totals], [1, 0, 1, 0, 0, 0, 2, 2, 4, 2, 2])
        manual = export(dialog, "Export recorded manual assignments", root / "boundaries.csv")
        bell = TEXT.index("I soldered")
        self.assertCountEqual([(r["recorded_by"], r["passage"], r["other_project_overlapping_spans"])
                               for r in manual], [
            ("current only", TEXT[bell:], f"[{bell}, {bell + 10})"),
            ("comparison only", TEXT[bell:bell + 10], f"[{bell}, {len(TEXT)})"),
            ("current only", TEXT.splitlines()[0][7:], ""),
            ("comparison only", TEXT[7:17], ""),
        ])
        press(dialog, "Next review disagreement")
        self.assertIn("Unpaired quote rows (2 current, 2 comparison)", dialog.status.get())
        self.assertIn("Review disagreement 1 of 1", dialog.status.get())
        self.assertIn("Comparable pairs (2 of 4)", dialog.status.get())
        self.assertEqual(app._current_quote_match().quote.source_line, 3)
        self.assertEqual({p: p.read_bytes() for p in saved}, saved)

        dialog.destroy()
        for actor, reason in (("Flint", "After discussion, the unit counted is unclear."),
                              ("Ochre", "I still cannot identify the tally unit.")):
            original = root / f"{actor}.tfproj"
            reopen(original)
            first_pass = tuple(app.audit_events)
            discussion = root / f"{actor}-discussion.tfproj"
            with patch("themeforge.app.filedialog.asksaveasfilename", return_value=str(discussion)):
                press(app, "Save as…")
            self.assertEqual(app.project_path, discussion)
            theme, = [t for t in app.result.themes if t.name == "Parcel tally"]
            quote, = theme.quotes
            app._select_theme_by_id(theme.id)
            app._focus_quote_by_id(quote.quote_id)
            with patch("themeforge.app.MatchReviewReason") as prompt:
                prompt.return_value.result = reason
                press(app, "Not this match")
            press(app, "Save project")
            reopen(discussion)
            self.assertEqual(tuple(app.audit_events[:-1]), first_pass)
            note = app.audit_events[-1]
            self.assertEqual((note.actor, note.target_id), (actor, targets[actor, 3][0]))
            detail = json.loads(note.details)
            self.assertEqual((detail["disposition"], detail["reason"]), ("declined", reason))
        self.assertEqual({p: p.read_bytes() for p in saved}, saved)

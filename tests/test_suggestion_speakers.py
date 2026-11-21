import csv
from dataclasses import replace
from io import StringIO
import json
from pathlib import Path
import tempfile
from time import monotonic
from tkinter import ttk
import unittest
from unittest.mock import patch
import zipfile

from themeforge.analysis import AnalysisSettings, TranscriptDocument, analyze_documents
from themeforge.analysis_types import SpeakerOverride
from themeforge.app import ThemeForgeApp
from themeforge.audit import export_audit_events_csv
from themeforge.exports import export_quotes_csv
from themeforge.local_embeddings import EmbeddingResult
from themeforge.semantic_suggestions import find_similar_passages
from themeforge.speaker_review import SpeakerReview

PASSAGE = "We moved  the paper display\n  beside the blue cabinet."
WORKSHOP = ("Maker: We moved our paper display beside a cabinet.\n"
            f"Host 0:43\n{PASSAGE} Thank you.\n{PASSAGE}")
# Both labels belong to interviewers in this source. Give them content that
# would match the query so that a zero cosine score cannot mask a scope leak.
INTERVIEW = ("Interviewer: Tell me about the paper display beside the blue cabinet.\n"
             "Host: We discussed the blue cabinet beside the paper display.")


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


def button(widget, label):
    return next(child for child in descendants(widget) if isinstance(child, ttk.Button) and child.cget("text") == label)


class SpeakerSelectionTests(unittest.TestCase):
    def test_source_scoped_choices_precede_discovery_and_both_find_queries(self):
        docs = [TranscriptDocument("workshop.txt", WORKSHOP), TranscriptDocument("interview.txt", INTERVIEW)]
        settings = AnalysisSettings(theme_count=1)
        seed = analyze_documents(docs, settings)
        theme, = seed.themes
        quote, = theme.quotes
        self.assertEqual(quote.speaker, "Maker")
        expected = [(WORKSHOP.index(PASSAGE), 3), (WORKSHOP.rindex(PASSAGE), 5)]
        for backend in ("tfidf", "local_embeddings"):
            scope = replace(settings, semantic_backend=backend, speaker_overrides=(
                SpeakerOverride("workshop.txt", "Host", True), SpeakerOverride("workshop.txt", "Maker", False)))
            def constant_vectors(texts, language):
                return EmbeddingResult([{"x": 1.0} for _ in texts], "Test encoder")
            with patch("themeforge.analysis._local_embedding_vectors", side_effect=constant_vectors), \
                 patch("themeforge.semantic_suggestions.local_embedding_vectors", side_effect=constant_vectors):
                result = analyze_documents(docs, scope)
                queues = [[q for t in result.themes for q in t.quotes]]
                for key in ("", quote.quote_id):
                    queues.append(find_similar_passages(docs, seed, theme.id, scope, quote_id=key).passages)
                for queue in queues:
                    self.assertEqual(sorted((q.source_start, q.source_line) for q in queue), expected)
                    for q in queue:
                        self.assertEqual((q.source_name, q.speaker, q.text), ("workshop.txt", "Host", PASSAGE))
                        self.assertEqual(WORKSHOP[q.source_start:q.source_end], PASSAGE)

    def test_ambiguous_source_cannot_include_an_interviewer_with_the_same_label(self):
        docs = [TranscriptDocument("shared.txt", WORKSHOP), TranscriptDocument("shared.txt", INTERVIEW)]
        settings = AnalysisSettings(speaker_overrides=(SpeakerOverride("shared.txt", "Host", True),))
        seed = analyze_documents(docs[:1])
        with self.assertRaisesRegex(ValueError, "unique transcript"):
            analyze_documents(docs, settings)
        with self.assertRaisesRegex(ValueError, "unique transcript"):
            find_similar_passages(docs, seed, seed.themes[0].id, settings)

    def test_find_uses_the_same_automatic_decision_as_discovery(self):
        docs = [TranscriptDocument("one", "Guide: Should we move the paper display today?\n"
                                          "Guide: Should we use the blue cabinet?\nMaker: We moved the paper display today."),
                TranscriptDocument("two", "Guide: We moved the paper display today.")]
        settings = AnalysisSettings()
        seed = analyze_documents(docs, settings)
        self.assertEqual(seed.quote_count, 1)
        found = find_similar_passages(docs, seed, seed.themes[0].id, settings)
        self.assertEqual(found.passages, ())
        corrected = replace(settings, speaker_overrides=(SpeakerOverride("two", "Guide", True),))
        found = find_similar_passages(docs, seed, seed.themes[0].id, corrected)
        self.assertEqual([q.source_name for q in found.passages], ["two"])

    def test_first_import_narrow_dialog_save_reopen_and_export(self):
        app = ThemeForgeApp()
        self.addCleanup(app.destroy)
        app.geometry("1180x760+0+0")
        app.update()
        errors = []

        def check_visible(widget, window):
            self.assertTrue(widget.winfo_ismapped())
            parent = widget.master
            while parent is not None:
                x = widget.winfo_rootx() - parent.winfo_rootx()
                y = widget.winfo_rooty() - parent.winfo_rooty()
                self.assertGreaterEqual(min(x, y), 0)
                self.assertLessEqual(x + widget.winfo_width(), parent.winfo_width())
                self.assertLessEqual(y + widget.winfo_height(), parent.winfo_height())
                if parent == window:
                    break
                parent = parent.master

        def inspect():
            dialog = next((w for w in app.winfo_children() if isinstance(w, SpeakerReview)), None)
            try:
                if dialog is None or app.grab_current() != dialog:
                    self.assertLess(monotonic(), deadline, "Speaker review did not open and take focus")
                    app.after(10, inspect)
                    return
                dialog.geometry("480x360+20+20")
                dialog.update_idletasks()
                self.assertEqual(dialog.winfo_width(), 480)
                tk_version = dialog.tk.call("package", "provide", "Tk")
                print(f"Tk {tk_version}, workspace {app.winfo_width()}x{app.winfo_height()}, "
                      f"speaker dialog {dialog.winfo_width()}x{dialog.winfo_height()}", flush=True)
                table = dialog.table
                item, = [i for i in table.get_children()
                         if (table.set(i, "transcript"), table.set(i, "speaker")) == ("workshop.txt", "Host")]
                table.selection_set(item)
                dialog.update()
                self.assertIn("workshop.txt · Host · Line 3", dialog.preview.get())
                self.assertEqual((table.set(item, "automatic"), table.set(item, "choice")), ("Exclude", "Automatic"))
                for label in ("Include", "Exclude", "Use automatic", "Cancel", "Apply"):
                    action = button(dialog, label)
                    check_visible(action, dialog)
                    self.assertGreaterEqual(action.winfo_width(), action.winfo_reqwidth())
                for widget in descendants(dialog):
                    if isinstance(widget, ttk.Label):
                        self.assertGreaterEqual(widget.winfo_height(), widget.winfo_reqheight())
                        self.assertLessEqual(widget.winfo_pixels(widget.cget("wraplength")), widget.winfo_width())
                x, _, width, _ = table.bbox(item, "choice")
                self.assertGreaterEqual(x, 0)
                self.assertLessEqual(x + width, table.winfo_width())
                button(dialog, "Include").invoke()
                self.assertEqual(table.set(item, "choice"), "Include")
                button(dialog, "Apply").invoke()
            except Exception as exc:
                errors.append(exc)
                if dialog is not None:
                    dialog.destroy()

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = [root / "workshop.txt", root / "interview.txt"]
            for path, content in zip(paths, (WORKSHOP, INTERVIEW), strict=True):
                path.write_text(content, encoding="utf-8")
            with patch("themeforge.app.filedialog.askopenfilenames", return_value=paths):
                button(app, "Choose files").invoke()
            self.assertIsNone(app.result)
            self.assertIn("Speakers for suggestions", app.status_text.get())
            self.assertEqual(app.semantic_mode.get(), "TF-IDF")
            action = button(app, "Speakers for suggestions…")
            app.update_idletasks()
            for label in ("Choose files", "Speakers for suggestions…", "Analyze", "Supported CSV…", "Full report…"):
                check_visible(button(app, label), app)
            app.researcher_name.set("Workshop reviewer")
            deadline = monotonic() + 8
            app.after(10, inspect)
            action.invoke()
            if errors:
                raise errors[0]
            self.assertEqual(app.speaker_overrides, (SpeakerOverride("workshop.txt", "Host", True),))
            self.assertIsNone(app.result)
            button(app, "Analyze").invoke()
            project = root / "workshop.tfproj"
            with patch("themeforge.app.filedialog.asksaveasfilename", return_value=str(project)):
                button(app, "Save project").invoke()
            with patch("themeforge.app.filedialog.askopenfilename", return_value=str(project)):
                button(app, "Open project").invoke()
            self.assertEqual(app.speaker_overrides, (SpeakerOverride("workshop.txt", "Host", True),))
            event, = [e for e in app.audit_events if e.action == "suggestion_speakers_changed"]
            self.assertEqual(event.actor, "Workshop reviewer")
            report = root / "quotes.csv"
            with patch("themeforge.app.filedialog.asksaveasfilename", return_value=str(report)):
                button(app, "Full report…").invoke()
            with report.open(encoding="utf-8", newline="") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual({r["source_file"] for r in rows}, {"workshop.txt"})
            host = [r for r in rows if r["speaker"] == "Host"]
            self.assertEqual(sorted((int(r["source_line"]), int(r["source_start"])) for r in host),
                             [(3, WORKSHOP.index(PASSAGE)), (5, WORKSHOP.rindex(PASSAGE))])
            for r in host:
                self.assertEqual(WORKSHOP[int(r["source_start"]):int(r["source_end"])], r["quote"])
                self.assertEqual((r["quote"], r["source_location_status"], r["match_review_actor"]),
                                 (PASSAGE, "exact_span", ""))

    def test_desktop_change_save_find_and_export(self):
        app = ThemeForgeApp()
        app.withdraw()
        self.addCleanup(app.destroy)

        def choose(included, apply=True):
            def edit():
                dialog = next(w for w in app.winfo_children() if isinstance(w, SpeakerReview))
                item = str(dialog.keys.index(("workshop.txt", "Host")))
                dialog.table.selection_set(item)
                dialog.set_choice(included)
                if apply:
                    dialog.apply()
                else:
                    dialog.destroy()
            app.after_idle(edit)
            app.review_suggestion_speakers()

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = [root / "workshop.txt", root / "interview.txt"]
            for path, content in zip(paths, (WORKSHOP, INTERVIEW)):
                path.write_text(content, encoding="utf-8")
            with patch("themeforge.app.filedialog.askopenfilenames", return_value=paths):
                app.open_transcript()
            app.researcher_name.set("Fixture coder")
            app.analyze()
            original = app.result
            project = root / "study.tfproj"
            with patch("themeforge.app.filedialog.asksaveasfilename", return_value=str(project)):
                self.assertTrue(app.save_project())
            with zipfile.ZipFile(project) as archive:
                payload = json.loads(archive.read("project.json"))
                self.assertNotIn("speaker_overrides", payload["settings"])
            initial = project.read_bytes()
            with patch("themeforge.app.filedialog.askopenfilename", return_value=str(project)):
                app.open_project()
            choose(True, apply=False)
            self.assertEqual(app.speaker_overrides, ())
            choose(True)
            app.find_more_from_quote()
            self.assertEqual(len(app.similar_passages), 2)
            choose(None)
            self.assertEqual(app.similar_passages, [])
            self.assertFalse(app.transcript_widgets["workshop.txt"].tag_ranges("similar_suggestion"))
            app.find_more_from_quote()
            self.assertEqual(app.similar_passages, [])
            choose(True)
            self.assertEqual(app.result, original)
            self.assertEqual(project.read_bytes(), initial)
            self.assertTrue(app.save_project())
            with patch("themeforge.app.filedialog.askopenfilename", return_value=str(project)):
                app.open_project()
            self.assertEqual(app.speaker_overrides, (SpeakerOverride("workshop.txt", "Host", True),))
            self.assertEqual([d.text for d in app.documents], [WORKSHOP, INTERVIEW])
            app.find_more_from_quote()
            self.assertEqual(len(app.similar_passages), 2)
            app.similar_list.selection_clear(0, "end")
            app.similar_list.selection_set(1)
            app.show_selected_similar_passage()
            self.assertEqual(app.transcript_widgets["workshop.txt"].get("similar_suggestion.first", "similar_suggestion.last"), PASSAGE)
            app.code_selected_similar_passage()
            assigned = next(q for t in app.result.themes for q in t.quotes if q.speaker == "Host")
            self.assertEqual((assigned.text, assigned.source_start, assigned.source_line, assigned.relevance),
                             (PASSAGE, WORKSHOP.rindex(PASSAGE), 5, None))
            copy = root / "coded.tfproj"
            with patch("themeforge.app.filedialog.asksaveasfilename", return_value=str(copy)):
                self.assertTrue(app.save_project())
            with patch("themeforge.app.filedialog.askopenfilename", return_value=str(app.project_path)):
                app.open_project()
            rows = list(csv.DictReader(StringIO(export_quotes_csv(app.result, app.documents))))
            row, = [r for r in rows if r["speaker"] == "Host"]
            self.assertEqual((row["source_file"], row["source_location_status"], int(row["source_start"])),
                             ("workshop.txt", "exact_span", WORKSHOP.rindex(PASSAGE)))
            self.assertEqual(list(csv.DictReader(StringIO(export_quotes_csv(app.result, app.documents, supported_only=True)))), [])
            audit = list(csv.DictReader(StringIO(export_audit_events_csv(app.audit_events))))
            changes = [r for r in audit if r["action"] == "suggestion_speakers_changed"]
            self.assertEqual([r["actor"] for r in changes], ["Fixture coder"] * 3)
            self.assertEqual(json.loads(changes[-1]["details"])["current"],
                             [{"source_name": "workshop.txt", "speaker": "Host", "included": True}])
            applied, = [json.loads(r["details"]) for r in audit if r["action"] == "selection_assignment_recorded"]
            self.assertEqual((applied["text"], applied["start"], applied["retrieval_query"]["quote_id"]),
                             (PASSAGE, WORKSHOP.rindex(PASSAGE), original.themes[0].quotes[0].quote_id))

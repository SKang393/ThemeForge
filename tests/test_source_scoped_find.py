"""Trace a fictional weaving interruption from a reviewed quote to an uncoded passage."""
import csv
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from tkinter import ttk
import unittest
from unittest.mock import patch

from test_evidence_handoff import descendants, open_audit, export, press
from themeforge.app import ThemeForgeApp
from themeforge.exports import export_quotes_csv
from themeforge.semantic_suggestions import find_similar_passages

SEED = "I stopped weaving when the brass shuttle began to rattle against the guard."
MISSED = "I laid the shuttle in my lap and left the cloth half done until the pin sat tight."
CONTEXT = "We carried on with the same strip once it held."
STUDIO = (f"Tern: {SEED}\nIbis: We had agreed a bent pin meant tools down before another row.\n"
          f"Ibis: {SEED}\nIbis: {MISSED}\nTern: {CONTEXT}")
GALLERY = """Felt: The brass shuttle began to rattle against the guard in our recording.
Lark: Our brass shuttle sat against the guard while the sound began to fade.
Felt: Visitors heard the brass shuttle rattle against the guard on the tape.
Lark: We hung the brass shuttle against the guard beside a picture of the mill.
Felt: I polished the brass shuttle and the guard before the evening tour began.
Lark: Children copied the rattle of the brass shuttle against the guard.
Felt: Our guide held up the guard as the brass shuttle began to rattle again.
Lark: The audio track began with a brass shuttle rattling against the guard."""
WHY = "The maker explicitly stopped work after hearing the loose shuttle."


def prepare(app, folder):
    root = Path(folder)
    studio, gallery, codebook = (root / f for f in ("studio.txt", "gallery.txt", "codes.csv"))
    for path, text in ((studio, STUDIO), (gallery, GALLERY)):
        path.write_text(text, encoding="utf-8")
    codebook.write_text("code,definition\nSafety pause,Weaving stopped for a safety inspection.\n", encoding="utf-8")
    with patch("themeforge.app.filedialog.askopenfilenames", return_value=[str(studio), str(gallery)]):
        press(app, "Choose files")
    with patch("themeforge.app.filedialog.askopenfilename", return_value=str(codebook)):
        press(app, "Choose codebook")
    app.researcher_name.set("Fern")
    press(app, "Analyze")
    theme, = app.result.themes
    assert [(q.source_line, q.text) for q in theme.quotes] == [(1, SEED), (3, SEED)]
    press(app, "Next quote")
    with patch("themeforge.app.MatchReviewReason") as prompt:
        prompt.return_value.result = WHY
        press(app, "Supports this theme")
        prompt.assert_called_once()
    return app._current_quote_match().quote


def set_scope(app, name):
    box = app.review_source_box
    review = box.master.master.master
    review.master.select(review)
    app.update()
    box.current(box["values"].index(name))
    box.event_generate("<<ComboboxSelected>>")
    app.update()


def open_similar(app):
    frame = app.similar_list.master
    notebook = frame.master
    app.update()
    page = notebook.nametowidget(notebook.select())
    y = (page.winfo_rooty() - notebook.winfo_rooty()) // 2
    target = notebook.index(frame)
    # Click the label as rendered, including when the notebook shrinks its tabs.
    pixels = [x for x in range(notebook.winfo_width())
              if notebook.identify(x, y) == "label" and notebook.index(f"@{x},{y}") == target]
    assert pixels, "Similar passages tab label is missing"
    x = pixels[len(pixels) // 2]
    assert app.winfo_containing(notebook.winfo_rootx() + x, notebook.winfo_rooty() + y) == notebook
    for event in ("<ButtonPress-1>", "<ButtonRelease-1>"):
        notebook.event_generate(event, x=x, y=y)
    app.update()
    assert notebook.select() == str(frame)


class ScopedFindTests(unittest.TestCase):
    def test_scoped_find_manual_capture_and_return_to_repeated_quote(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        app = ThemeForgeApp()
        self.addCleanup(app.destroy)
        screen = app.winfo_screenwidth(), app.winfo_screenheight()
        if app.tk.call("tk", "windowingsystem") == "win32" and screen == (1024, 768):
            app.state("zoomed")
        else:
            app.minsize(1, 1)
            app.geometry("1024x705+0+0")
        seed = prepare(app, folder.name)
        self.assertEqual((seed.source_name, seed.source_start, seed.source_end), ("studio.txt", 156, 231))
        unchanged = app._project_state()

        def supported():
            return export_quotes_csv(app.result, app.documents, app.audit_events, supported_only=True,
                                     codebook_entries=app.codebook_entries)

        original_table = supported()
        # With no source restriction, eight closer results in the gallery fill the list.
        open_similar(app)
        press(app, "Find from quote")
        self.assertEqual([p.source_name for p in app.similar_passages], ["gallery.txt"] * 8)
        gallery_results = app.similar_passages.copy()
        set_scope(app, "studio.txt")
        self.assertFalse(app.similar_passages)
        self.assertFalse(app.transcript_widgets["gallery.txt"].tag_ranges("similar_suggestion"))
        open_similar(app)
        press(app, "Code selected suggestion")
        self.assertEqual(app._project_state(), unchanged)
        for label in ("Find from theme", "Find from quote"):
            press(app, label)
            hit, = app.similar_passages
            self.assertEqual((hit.source_name, hit.source_line, hit.source_start, hit.source_end, hit.text),
                             ("studio.txt", 4, 238, 320, MISSED))
            self.assertIn("studio.txt", app.similar_note.get())
            self.assertIn("TF-IDF", app.similar_note.get())
        set_scope(app, "All sources")
        open_similar(app)
        press(app, "Find from quote")
        self.assertEqual(app.similar_passages, gallery_results)
        set_scope(app, "studio.txt")
        open_similar(app)
        button, = [w for w in descendants(app.similar_list.master)
                   if isinstance(w, ttk.Button) and w.cget("text") == "Find from quote"]
        x, y = button.winfo_rootx(), button.winfo_rooty()
        width, height = button.winfo_width(), button.winfo_height()
        self.assertGreaterEqual(width, button.winfo_reqwidth())
        self.assertGreaterEqual(height, button.winfo_reqheight())
        for px in (x, x + width - 1):
            for py in (y, y + height - 1):
                self.assertTrue(0 <= px < min(1024, screen[0]) and 0 <= py < min(768, screen[1]), (px, py))
                self.assertEqual(app.winfo_containing(px, py), button)
        for event in ("<ButtonPress-1>", "<ButtonRelease-1>"):
            button.event_generate(event, x=width // 2, y=height // 2)
        app.update()
        print(f"Find from quote clicked at compact size: button ({x},{y}) {width}x{height}, "
              f"workspace {app.winfo_width()}x{app.winfo_height()}, desktop {screen[0]}x{screen[1]}")
        widget = app.transcript_widgets["studio.txt"]
        self.assertEqual(widget.get("similar_suggestion.first", "similar_suggestion.last"), MISSED)
        self.assertIn(CONTEXT, widget.get("5.0", "5.end"))
        self.assertIsNone(app._visible_review_match())
        self.assertEqual(app._project_state(), unchanged)

        hit, = app.similar_passages
        full = find_similar_passages(app.documents, app.result, app._current_theme().id, app._analysis_settings(),
                                     quote_id=seed.quote_id, limit=20)
        whole_study_hit = full.passages[8]
        self.assertEqual((whole_study_hit.source_name, whole_study_hit.source_start, whole_study_hit.similarity),
                         (hit.source_name, hit.source_start, hit.similarity))
        press(app, "Code selected suggestion")
        captured = app._current_quote_match().quote
        self.assertEqual((captured.speaker, captured.source_line, captured.source_start, captured.source_end,
                          captured.text, captured.reported_relevance), ("Ibis", 4, 238, 320, MISSED, None))
        press(app, "Previous quote")
        review = app.review_source_box.master.master.master
        review.master.select(review)
        app.update()
        self.assertEqual(app._visible_review_match().quote, seed)
        self.assertEqual(widget.index("selected_quote.first"), "3.6")
        self.assertIn(WHY, app.match_review_text.get())
        self.assertEqual(supported(), original_table)

        path = root / "studio.tfproj"
        with patch("themeforge.app.filedialog.asksaveasfilename", return_value=str(path)):
            press(app, "Save project")
        saved = path.read_bytes()
        audit = open_audit(app, path)
        self.assertEqual(app.review_source_box.get(), "All sources")
        theme, = app.result.themes
        self.assertIn(captured, theme.quotes)
        event, = [e for e in app.audit_events if e.action == "selection_assignment_recorded"]
        details = json.loads(event.details)
        self.assertEqual((event.actor, event.target_id, details["method"], details["source"], details["start"],
                          details["end"], details["text"], details["assignment_score"]),
                         ("Fern", captured.quote_id, "applied similarity suggestion", "studio.txt", 238, 320, MISSED, None))
        self.assertEqual((details["retrieval_query"]["quote_id"], details["retrieval_query"]["text"],
                          details["retrieval_similarity"]), (seed.quote_id, SEED, hit.similarity))
        self.assertIn("TF-IDF", details["retrieval_query"]["method_note"])
        self.assertIn("studio.txt", details["retrieval_query"]["method_note"])
        audit.supported_sources.selection_set(audit.supported_sources.get(0, "end").index("studio.txt"))
        self.assertEqual(export(audit, "Export supported matches CSV", root / "supported.csv"),
                         list(csv.DictReader(StringIO(original_table))))
        self.assertEqual(path.read_bytes(), saved)

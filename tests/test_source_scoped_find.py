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

    def test_find_literal_in_already_suggested_text_then_assign_by_hand(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        source = "진열실.txt"
        passage = "다리가 갈라져서 임시 받침을 끼워 진열장을 잠시 세워 뒀어요."
        turns = [passage, "임시 받침대 그림은 안내판 아래쪽에 있었어요.",
                 "지난 장부에서 임시 받침을 찾아 철자 하나를 고쳤어요."]
        (root / source).write_text("\n".join("모래: " + text for text in turns), encoding="utf-8")
        (root / "shelf.txt").write_text("Robin: I braced the shelf with a temporary prop.\n"
                                       "솔: 임시 받침을 어디 두었는지 기억이 안 나요.", encoding="utf-8")
        book = root / "book.csv"
        book.write_text("code,definition,example\nInterim support,temporary prop,임시 받침\n"
                        "Leg damage,A cracked leg,다리가 갈라져서\n", encoding="utf-8")
        app = ThemeForgeApp()
        self.addCleanup(app.destroy)
        app.minsize(1, 1)
        app.geometry("1024x705+0+0")
        paths = [str(root / source), str(root / "shelf.txt")]
        with patch("themeforge.app.filedialog.askopenfilenames", return_value=paths):
            press(app, "Choose files")
        with patch("themeforge.app.filedialog.askopenfilename", return_value=str(book)):
            press(app, "Choose codebook")
        app.researcher_name.set("Jade")
        press(app, "Analyze")
        prop, leg = app.result.themes
        self.assertFalse(any(q.text == passage for q in prop.quotes))
        app._select_theme_by_id(leg.id)
        self.assertEqual(app._current_quote_match().quote.text, passage)
        with patch("themeforge.app.MatchReviewReason") as prompt:
            prompt.return_value.result = "The display case leg had cracked."
            press(app, "Supports this theme")
        project = root / "display.tfproj"
        with patch("themeforge.app.filedialog.asksaveasfilename", return_value=str(project)):
            press(app, "Save project")
        saved = project.read_bytes()
        state = app._project_state()
        original = export_quotes_csv(app.result, app.documents, app.audit_events, supported_only=True,
                                     codebook_entries=app.codebook_entries)
        app.update()
        find, = [w for w in descendants(app) if isinstance(w, ttk.Button) and w.cget("text") == "Find text…"]
        self.assertTrue(find.winfo_viewable())
        self.assertEqual(app.winfo_containing(find.winfo_rootx() + find.winfo_width() - 1,
                                              find.winfo_rooty() + find.winfo_height() - 1), find)
        text = app.transcript_widgets[source]
        with patch("themeforge.app.simpledialog.askstring", return_value="임시 받침") as prompt:
            find.invoke()
            app.update()
            prompt.assert_called_once()
        self.assertTrue(text.winfo_viewable())
        self.assertIsNotNone(text.bbox("sel.first"))
        text.focus_force()
        app.update()
        for index, line in enumerate((1, 2, 3, 1)):
            if index:
                text.event_generate("<F3>")
                app.update()
            selected = app._selected_transcript_text()
            self.assertEqual((selected.source_name, selected.source_line, selected.text), (source, line, "임시 받침"))
        self.assertIsNone(app._current_quote_match())
        with patch("themeforge.app.MatchReviewReason") as prompt:
            press(app, "Supports this theme")
            prompt.assert_not_called()
        for query in (None, "unprinted phrase"):
            with patch("themeforge.app.simpledialog.askstring", return_value=query) as prompt:
                text.event_generate("<Control-f>")
                app.update()
                prompt.assert_called_once()
            self.assertEqual(app._selected_transcript_text(), selected)
        self.assertEqual(app._project_state(), state)
        self.assertEqual(project.read_bytes(), saved)

        # The reader extends the literal hit to the full supporting turn.
        text.tag_remove("sel", "1.0", "end")
        text.tag_add("sel", "1.4", "1.end")
        app.definition_box.current(0)
        with patch.object(app, "wait_window", side_effect=lambda d: press(d, "Apply selected definition")):
            press(app, "Apply codebook entry…")
        recovered = root / "recovered.tfproj"
        with patch("themeforge.app.filedialog.asksaveasfilename", return_value=str(recovered)):
            press(app, "Save as…")
        dialog = open_audit(app, recovered)
        self.assertEqual(project.read_bytes(), saved)
        assignment, = export(dialog, "Export this project's manual codebook assignments", root / "manual.csv")
        self.assertEqual((assignment["code_name"], assignment["assigned_by"], assignment["source"],
                          assignment["source_start"], assignment["source_end"], assignment["passage"]),
                         ("Interim support", "Jade", source, "4", "38", passage))
        dialog.supported_sources.selection_set(0, "end")
        rows = export(dialog, "Export supported matches CSV", root / "evidence.csv")
        self.assertEqual(rows, list(csv.DictReader(StringIO(original))))
        self.assertEqual([e for e in app.audit_events if e.action == "match_review_note"],
                         [e for e in state.audit_events if e.action == "match_review_note"])

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
from themeforge.source_selection import FindTextScope

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


def answer_find(query, selected=None, *, initial=None):
    def respond(dialog, _window):
        assert dialog.sources.curselection() == (dialog.names.index(dialog.current),)
        if initial is not None:
            assert dialog.query.get() == initial
        if query is None:
            dialog.cancel()
            return
        dialog.query.delete(0, "end")
        dialog.query.insert(0, query)
        if selected is not None:
            for index, name in enumerate(dialog.names):
                if (index in dialog.sources.curselection()) != (name in selected):
                    dialog.sources.see(index)
                    dialog.update_idletasks()
                    x, y, width, height = dialog.sources.bbox(index)
                    for event in ("<ButtonPress-1>", "<ButtonRelease-1>"):
                        dialog.sources.event_generate(event, x=x + 2, y=y + height // 2)
        dialog.ok()
        if selected == ():
            assert dialog.winfo_exists() and "select at least one" in dialog.notice.cget("text")
            dialog.cancel()
    return patch.object(FindTextScope, "wait_window", autospec=True, side_effect=respond)


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
        with answer_find("임시 받침") as prompt:
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
            with answer_find(query) as prompt:
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

    def test_literal_search_selected_tabs_wrap_and_return_to_exact_review(self):
        sources = {
            "kiln.txt": "Potter: I put a wax dot on the first tile.\nPotter: The next tile carried a wax dot underneath.",
            "ledger.txt": ("Clerk: I entered the tray number in the ledger.\nClerk: The drawing showed a wax dot.\n"
                           "Clerk: I entered the tray number in the ledger."),
            "rack.txt": "Glazer: A wax dot survived the rinse on the glazed tile.",
        }
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        for name, content in sources.items():
            (root / name).write_text(content, encoding="utf-8")
        book = root / "codes.csv"
        book.write_text("code,definition\nInventory,tray number ledger\nMarker,physical marker on tile\n", encoding="utf-8")
        with patch.object(ThemeForgeApp, "maxsize", return_value=(1024, 705)):
            app = ThemeForgeApp()
        self.addCleanup(app.destroy)
        with patch("themeforge.app.filedialog.askopenfilenames", return_value=[str(root / n) for n in sources]):
            press(app, "Choose files")
        with patch("themeforge.app.filedialog.askopenfilename", return_value=str(book)):
            press(app, "Choose codebook")
        app.researcher_name.set("Slate")
        press(app, "Analyze")
        press(app, "Next quote")
        origin = app._visible_review_match().quote
        self.assertEqual((origin.source_name, origin.source_line), ("ledger.txt", 3))
        reason = "The later entry records the tray number."
        with patch("themeforge.app.MatchReviewReason") as prompt:
            prompt.return_value.result = reason
            press(app, "Supports this theme")
        project = root / "tiles.tfproj"
        with patch("themeforge.app.filedialog.asksaveasfilename", return_value=str(project)):
            press(app, "Save project")
        saved = project.read_bytes()
        events = list(app.audit_events)

        def table(only_supported):
            return export_quotes_csv(app.result, app.documents, app.audit_events, supported_only=only_supported,
                                     codebook_entries=app.codebook_entries).encode("utf-8")

        supported, full = table(True), table(False)
        set_scope(app, "ledger.txt")
        scope = ("kiln.txt", "rack.txt")
        with answer_find("drawing", ()):
            press(app, "Find text…")
        with answer_find("drawing", scope):
            press(app, "Find text…")  # The literal exists only in the excluded transcript.
        self.assertIn("No exact occurrence", app.status_text.get())
        self.assertIn("other words", app.status_text.get())
        self.assertEqual(app._visible_review_match().quote, origin)

        with answer_find("wax dot", scope) as prompt:
            press(app, "Find text…")
            app.update()
            for button in (app.next_hit_button, app.back_to_review):
                self.assertTrue(button.winfo_viewable())
                x = button.winfo_rootx() + button.winfo_width() - 1
                y = button.winfo_rooty() + button.winfo_height() - 1
                self.assertTrue(0 <= x < 1024 and 0 <= y < 768)
                self.assertEqual(app.winfo_containing(x, y), button)
            for index, (name, start) in enumerate((("kiln.txt", 16), ("kiln.txt", 75), ("rack.txt", 10),
                                                    ("kiln.txt", 16))):
                if index == 2:
                    app.transcript_widgets["kiln.txt"].focus_force()
                    app.update()
                    app.transcript_widgets["kiln.txt"].event_generate("<F3>")
                elif index:
                    press(app, "Next hit")
                app.update()
                selected = app._selected_transcript_text()
                self.assertEqual((selected.source_name, selected.source_start, selected.source_end, selected.text),
                                 (name, start, start + 7, "wax dot"))
                self.assertIsNone(app._visible_review_match())
                self.assertEqual("Wrapped" in app.status_text.get(), index == 3)
            prompt.assert_called_once()
        self.assertEqual(app.review_source_box.get(), "ledger.txt")
        press(app, "Back to review")
        app.update()
        self.assertEqual(app._visible_review_match().quote, origin)
        self.assertIn(reason, app.match_review_text.get())
        self.assertEqual((app.audit_events, table(True), table(False), project.read_bytes()),
                         (events, supported, full, saved))

        with answer_find("wax dot", scope):
            press(app, "Find text…")
        press(app, "Next hit")
        press(app, "Next hit")
        widget = app.transcript_widgets["rack.txt"]
        widget.tag_remove("sel", "1.0", "end")
        widget.tag_add("sel", "1.8", "1.end")
        app.definition_box.current(1)
        with patch.object(app, "wait_window", side_effect=lambda d: press(d, "Apply selected definition")):
            press(app, "Apply codebook entry…")
        assigned = app._visible_review_match().quote
        self.assertEqual((assigned.source_name, assigned.source_start, assigned.text, assigned.reported_relevance),
                         ("rack.txt", 8, sources["rack.txt"][8:], None))
        press(app, "Back to review")
        self.assertEqual(app._visible_review_match().quote, origin)
        self.assertIn(reason, app.match_review_text.get())
        self.assertEqual([e.action for e in app.audit_events[len(events):]], ["codebook_passage_assigned"])
        self.assertEqual(table(True), supported)
        self.assertEqual(project.read_bytes(), saved)

        copy = root / "tiles-with-marker.tfproj"
        with patch("themeforge.app.filedialog.asksaveasfilename", return_value=str(copy)):
            press(app, "Save as…")
        with answer_find("wax dot", scope):
            press(app, "Find text…")
        with patch("themeforge.app.filedialog.askopenfilename", return_value=str(copy)):
            press(app, "Open project")
        app.update()
        self.assertFalse(app.next_hit_button.winfo_ismapped())
        self.assertFalse(app.back_to_review.winfo_ismapped())
        self.assertEqual(table(True), supported)
        self.assertEqual([q for t in app.result.themes for q in t.quotes if q.manual_definition_assignment], [assigned])
        self.assertEqual(project.read_bytes(), saved)

    def test_reader_change_ends_find_and_clears_only_its_selection(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        with patch.object(ThemeForgeApp, "maxsize", return_value=(1024, 705)):
            app = ThemeForgeApp()
        self.addCleanup(app.destroy)
        original = prepare(app, folder.name)
        project = Path(folder.name) / "weaving.tfproj"
        with patch("themeforge.app.filedialog.asksaveasfilename", return_value=str(project)):
            press(app, "Save project")

        def evidence():
            return export_quotes_csv(app.result, app.documents, app.audit_events, supported_only=True,
                                     codebook_entries=app.codebook_entries)

        before = project.read_bytes(), tuple(app.audit_events), evidence()
        with answer_find("brass shuttle", ("studio.txt", "gallery.txt")):
            press(app, "Find text…")
        press(app, "Next hit")
        press(app, "Next hit")
        app.update()
        hit = app._selected_transcript_text()
        self.assertEqual((hit.source_name, hit.source_start, hit.source_end), ("gallery.txt", 10, 23))
        reviewer = next(w for w in descendants(app) if isinstance(w, ttk.Entry)
                        and str(w.cget("textvariable")) == str(app.researcher_name))
        reviewer.insert("end", " ")
        self.assertEqual(app._selected_transcript_text(), hit)
        draft = "Compare the recording with the demonstration."
        app.document_memo_text.insert("end", draft)
        reviewer.delete(0, "end")
        reviewer.insert(0, "Ochre")
        app.update()
        self.assertEqual(app._current_document_name(), "gallery.txt")
        self.assertFalse(app.next_hit_button.winfo_ismapped())
        self.assertFalse(app.back_to_review.winfo_ismapped())
        self.assertIsNone(app._selected_transcript_text())
        self.assertFalse(app.quote_meta.get() or app.quote_reason.get())
        self.assertEqual(app.document_memo_text.get("1.0", "end-1c"), draft)
        app.document_memo_text.delete("1.0", "end")

        # F3 opens a new, current-tab-only query, with no return into the first reader's review.
        gallery = app.transcript_widgets["gallery.txt"]
        gallery.focus_force()
        app.update()
        with answer_find("held up", initial="") as prompt:
            gallery.event_generate("<F3>")
            app.update()
            prompt.assert_called_once()
        self.assertEqual(app._selected_transcript_text().text, "held up")
        self.assertFalse(app.back_to_review.winfo_ismapped())
        # A failed query leaves the previous reading selection; a hand-selected span is independent.
        with answer_find("unprinted phrase"):
            press(app, "Find text…")
        self.assertIn("No exact occurrence", app.status_text.get())
        gallery.tag_remove("sel", "1.0", "end")
        gallery.tag_add("sel", "1.0", "1.4")
        reviewer.delete(0, "end")
        reviewer.insert(0, "Fern")
        self.assertEqual(app._selected_transcript_text().text, "Felt")
        self.assertFalse(gallery.tag_ranges("literal_hit"))

        press(app, "Next supported")
        self.assertEqual(app._visible_review_match().quote, original)
        self.assertIn(WHY, app.match_review_text.get())
        self.assertEqual((project.read_bytes(), tuple(app.audit_events), evidence()), before)

    def test_switch_sources_while_drafting_memos_and_coding_repeated_text(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        phrase = "I slipped the folded tab beneath the foot."
        sources = {"loft.txt": "Finch: I used a shim to steady the frame.\nFinch: " + phrase,
                   "bay.txt": "Reed: The shim was drawn on the box for the spare parts.\nReed: " + phrase}
        offsets = {"loft.txt": (49, 91), "bay.txt": (63, 105)}
        memos = {"loft.txt": "Check the folded tab against the earlier wobble.",
                 "bay.txt": "The drawing and the folded tab describe different actions."}
        for name, text in sources.items():
            start, end = offsets[name]
            self.assertEqual(text[start:end], phrase)
            (root / name).write_text(text, encoding="utf-8")
        book = root / "codes.csv"
        book.write_text("code,definition\nLeveling,Using a shim to steady a frame.\n", encoding="utf-8")
        with patch.object(ThemeForgeApp, "maxsize", return_value=(1024, 705)):
            app = ThemeForgeApp()
        self.addCleanup(app.destroy)
        with patch("themeforge.app.filedialog.askopenfilenames", return_value=[str(root / n) for n in sources]):
            press(app, "Choose files")
        with patch("themeforge.app.filedialog.askopenfilename", return_value=str(book)):
            press(app, "Choose codebook")
        app.researcher_name.set("Lark")
        press(app, "Analyze")
        theme, = app.result.themes
        self.assertEqual({(q.source_name, q.source_line) for q in theme.quotes}, {(n, 1) for n in sources})
        memo = app.document_memo_text

        def switch(name):
            app.transcript_notebook.select(app.transcript_frames[name])
            app.update()
            self.assertEqual(app._current_document_name(), name)

        def review(name, button, reason):
            app.quote_review_page.master.select(app.quote_review_page)
            app._select_theme_by_id(theme.id)
            if app._current_quote_match().source_name != name:
                press(app, "Next quote")
            app.update()
            self.assertEqual(app._visible_review_match().source_name, name)
            with patch("themeforge.app.MatchReviewReason") as prompt:
                prompt.return_value.result = reason
                press(app, button)
                prompt.assert_called_once()

        def assign(name):
            with answer_find(phrase):
                press(app, "Find text…")
            selection = app._selected_transcript_text()
            self.assertEqual((selection.source_name, selection.source_start, selection.source_end, selection.text),
                             (name, *offsets[name], phrase))
            app.definition_box.current(0)
            with patch.object(app, "wait_window", side_effect=lambda d: press(d, "Apply selected definition")):
                press(app, "Apply codebook entry…")

        def save_draft(name):
            switch(name)
            memo.master.master.select(memo.master)
            self.assertEqual(memo.get("1.0", "end-1c"), memos[name])
            press(app, "Save document memo")

        reason = "The shim steadied the frame here."
        decline = "A drawing does not level the frame."
        review("loft.txt", "Supports this theme", reason)
        memo.master.master.select(memo.master)
        memo.insert("1.0", memos["loft.txt"])
        switch("bay.txt")
        self.assertEqual(memo.get("1.0", "end-1c"), "")
        assign("bay.txt")
        review("bay.txt", "Not this match", decline)
        memo.master.master.select(memo.master)
        memo.insert("1.0", memos["bay.txt"])
        save_draft("loft.txt")
        assign("loft.txt")
        save_draft("bay.txt")

        path = root / "mounts.tfproj"
        with patch("themeforge.app.filedialog.asksaveasfilename", return_value=str(path)):
            press(app, "Save project")
        saved = path.read_bytes()
        events = tuple(app.audit_events)
        self.assertEqual([e.target_id for e in events if e.action == "document_memo_updated"], list(sources))

        # Cancel keeps an unfinished deletion. Explicit discard clears that draft even
        # when the incoming project has the same transcript names and tab order.
        memo.delete("1.0", "end")
        switch("loft.txt")
        with patch("themeforge.app.messagebox.askyesnocancel", return_value=True) as prompt:
            app.request_close()
            prompt.assert_called_once()
        self.assertIn("bay.txt", app.status_text.get())
        with patch("themeforge.app.filedialog.askopenfilename", return_value=str(path)), \
                patch("themeforge.app.messagebox.askyesnocancel", return_value=None):
            press(app, "Open project")
        switch("bay.txt")
        self.assertEqual(memo.get("1.0", "end-1c"), "")
        switch("loft.txt")
        with patch("themeforge.app.messagebox.askyesnocancel", return_value=False) as discard:
            dialog = open_audit(app, path)
            discard.assert_called_once()
        switch("bay.txt")
        self.assertEqual(memo.get("1.0", "end-1c"), memos["bay.txt"])
        self.assertEqual({d.name: d.memo for d in app.documents}, memos)
        self.assertEqual(tuple(app.audit_events), events)
        reviews = [json.loads(e.details) for e in events if e.action == "match_review_note"]
        self.assertEqual([(r["disposition"], r["reason"]) for r in reviews],
                         [("supported", reason), ("declined", decline)])
        assignments = export(dialog, "Export this project's manual codebook assignments", root / "manual.csv")
        anchors = [(r["source"], int(r["source_start"]), int(r["source_end"]), r["passage"], r["assigned_by"])
                   for r in assignments]
        self.assertCountEqual(anchors, [(name, *offsets[name], phrase, "Lark") for name in sources])
        dialog.supported_sources.selection_set(dialog.supported_sources.get(0, "end").index("loft.txt"))
        row, = export(dialog, "Export supported matches CSV", root / "supported.csv")
        self.assertEqual((row["source_file"], row["source_start"], row["source_end"], row["quote"],
                          row["match_review_reason"]), ("loft.txt", "7", "41", sources["loft.txt"][7:41], reason))
        self.assertEqual(path.read_bytes(), saved)

"""Citations in an original fictional shift interview and an English control."""
import csv
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unicodedata import normalize
import unittest
from unittest.mock import patch

from test_evidence_handoff import export, press
from test_paired_match_reviews import answer_paired_scope
from themeforge.app import ThemeForgeApp
from themeforge.definition_review import MatchReviewReason
from themeforge.rigor_dialog import RigorDialog

QUOTE = "파란 봉투를 다음 조에 직접 건넸어요."
INTERVIEW = (
    "별: 탁자 위에 🧵 표시가 붙은 종이가 있었어요.\n"
    "Renée: I thought “handoff” was just the name on the rehearsal card.\n"
    f"별: {QUOTE}\n"
    "Renée: On the next shift, I took my own envelope to the desk.\n"
    f"Renée: {QUOTE}\n"
    "별: 그때 누가 받았는지 기억나요?\n"
    "Renée: The evening crew took it and signed the log."
)
CONTROL_QUOTE = "I handed the blue envelope to the next crew."
CONTROL = (
    f"Star: {CONTROL_QUOTE}\n"
    "Wren: I checked the number on the packet first.\n"
    f"Wren: {CONTROL_QUOTE}"
)
INTERVIEW_FILE = "교대 기록.txt"
CONTROL_FILE = "desk-control.txt"
CODE = "봉투 전달 / Envelope handoff"
DEFINITION = "파란 봉투를 다음 조에 직접 건넨 일. Handing the blue envelope to the next crew."
ACTOR = "검토자 솔"
OTHER_ACTOR = "검토자 달"
REASON = "두 번째 교대 때 봉투를 건넸고 저녁 조가 수령을 확인했다."
DEFER = "첫 번째 교대 때 누가 받았는지 확인이 필요하다."
OTHER_REASON = "두 번째 봉투의 수령 기록을 다시 보고 싶다."
CONTROL_REASON = "The second control speaker describes handing over the envelope."

# Half-open Unicode code-point offsets in the imported LF text, fixed before running Analyze.
INTERVIEW_SHA = "1c19606a8d5e2fba88c325b5cf8a767eb25d4ed96842011f630a500c2aeace03"
CONTROL_SHA = "86697376d71220bef9292c9c86c551813149abc22c8ca0a5c8f3352baa03d1ef"
FIRST = (INTERVIEW_FILE, 3, 99, 120, "별", QUOTE)
SECOND = (INTERVIEW_FILE, 5, 190, 211, "Renée", QUOTE)
CONTROL_FIRST = (CONTROL_FILE, 1, 6, 50, "Star", CONTROL_QUOTE)
CONTROL_SECOND = (CONTROL_FILE, 3, 105, 149, "Wren", CONTROL_QUOTE)


class BilingualCitationTests(unittest.TestCase):
    def review(self, app, citation, button, reason):
        name, line, start, end, speaker, passage = citation
        for _ in app.current_quote_matches:
            q = app._current_quote_match().quote
            if (q.source_name, q.source_start, q.source_end) == (name, start, end):
                break
            press(app, "Next quote")
        else:
            self.fail(f"Could not navigate to {name} [{start},{end})")
        self.assertEqual((q.source_line, q.speaker, q.text), (line, speaker, passage))
        self.assertEqual(app._current_document_name(), name)
        self.assertEqual(app.transcript_widgets[name].get("selected_quote.first", "selected_quote.last"), passage)

        def answer(dialog, _window):
            self.assertEqual(dialog.excerpt, passage)
            dialog.reason.insert(0, reason)
            dialog.ok()

        with patch.object(MatchReviewReason, "wait_window", autospec=True, side_effect=answer) as prompt:
            press(app, button)
            prompt.assert_called_once()

    def export_tables(self, app, root, stage):
        press(app, "Rigor tools")
        audit, = [w for w in app.winfo_children() if isinstance(w, RigorDialog)]
        audit.audit_tree.master.master.select(audit.audit_tree.master)
        choices = audit.supported_sources
        self.assertEqual(choices.curselection(), ())
        path = root / f"{stage}.csv"
        for name, citation, reason, digest in ((CONTROL_FILE, CONTROL_SECOND, CONTROL_REASON, CONTROL_SHA),
                                               (INTERVIEW_FILE, SECOND, REASON, INTERVIEW_SHA)):
            choices.selection_clear(0, "end")
            choices.selection_set(choices.get(0, "end").index(name))
            target = path if name == INTERVIEW_FILE else root / f"control-{stage}.csv"
            row, = export(audit, "Export supported matches CSV", target)
            self.assertEqual((row["source_file"], int(row["source_line"]), int(row["source_start"]),
                              int(row["source_end"]), row["speaker"], row["quote"]), citation)
            self.assertEqual((row["theme_name"], row["source_sha256"], row["source_location_status"],
                              row["match_review_actor"], row["match_review_reason"], row["match_review_disposition"]),
                             (CODE, digest, "exact_span", ACTOR, reason, "supported"))
        output = path.read_bytes()
        for withheld in (DEFER, OTHER_ACTOR, OTHER_REASON, CONTROL_FILE, CONTROL_QUOTE, CONTROL_REASON):
            self.assertNotIn(withheld, output.decode("utf-8"))
        manual_path = root / f"manual-{stage}.csv"
        manual, = export(audit, "Export this project's manual codebook assignments", manual_path)
        self.assertEqual((manual["source"], int(manual["source_line"]), int(manual["source_start"]),
                          int(manual["source_end"]), manual["passage"]), (*SECOND[:4], QUOTE))
        self.assertEqual((manual["assigned_by"], manual["code_name"], manual["definition"],
                          manual["source_snapshot_sha256"]), (ACTOR, CODE, DEFINITION, INTERVIEW_SHA))
        audit.destroy()
        return output, manual_path.read_bytes()

    def test_repeated_passage_assignment_and_scoped_review_survive_reopen(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        paths = []
        for name, text, digest in ((INTERVIEW_FILE, INTERVIEW, INTERVIEW_SHA), (CONTROL_FILE, CONTROL, CONTROL_SHA)):
            self.assertEqual(sha256(text.encode("utf-8")).hexdigest(), digest)
            path = root / name
            path.write_bytes(text.replace("\n", "\r\n").encode("utf-8-sig"))
            paths.append(str(path))
        book = root / "codes.csv"
        book.write_text(f"code,definition\n{CODE},{DEFINITION}\n", encoding="utf-8")
        app = ThemeForgeApp()
        self.addCleanup(lambda: app.destroy())
        with patch("themeforge.app.filedialog.askopenfilenames", return_value=paths):
            press(app, "Choose files")
        with patch("themeforge.app.filedialog.askopenfilename", return_value=str(book)):
            press(app, "Choose codebook")
        self.assertEqual([d.text for d in app.documents], [INTERVIEW, CONTROL])
        app.researcher_name.set(ACTOR)
        press(app, "Analyze")
        app.update()
        theme, = app.result.themes
        self.assertEqual(theme.name, CODE)
        self.assertEqual(app._analysis_settings().semantic_backend, "tfidf")
        citations = [(q.source_name, q.source_line, q.source_start, q.source_end, q.speaker, q.text)
                     for q in theme.quotes]
        for expected in (FIRST, SECOND, CONTROL_FIRST, CONTROL_SECOND):
            self.assertIn(expected, citations)

        self.review(app, FIRST, "Revisit later", DEFER)
        self.review(app, CONTROL_SECOND, "Supports this theme", CONTROL_REASON)
        app.researcher_name.set(OTHER_ACTOR)
        self.review(app, SECOND, "Revisit later", OTHER_REASON)
        app.researcher_name.set(ACTOR)
        self.review(app, SECOND, "Supports this theme", REASON)
        # Select the second copy directly in the transcript, independently of the match highlight.
        widget = app.transcript_widgets[INTERVIEW_FILE]
        begin = widget.search(QUOTE, "5.0", stopindex="5.end", exact=True)
        widget.tag_add("sel", begin, "5.end")
        selection = app._selected_transcript_text()
        self.assertEqual((selection.source_name, selection.source_line, selection.source_start,
                          selection.source_end, selection.text), (*SECOND[:4], QUOTE))
        app.definition_box.current(0)

        def confirm(dialog):
            self.assertEqual(dialog.passage.get("evidence.first", "evidence.last"), QUOTE)
            self.assertIn(DEFINITION, dialog.definitions.get("1.0", "end"))
            press(dialog, "Apply selected definition")

        with patch.object(app, "wait_window", side_effect=confirm) as prompt:
            press(app, "Apply codebook entry…")
            prompt.assert_called_once()
        project = root / "교대.tfproj"
        with patch("themeforge.app.filedialog.asksaveasfilename", return_value=str(project)):
            press(app, "Save project")
        original = project.read_bytes()

        before = self.export_tables(app, root, "before")
        app.request_close()
        app = ThemeForgeApp()
        with patch("themeforge.app.filedialog.askopenfilename", return_value=str(project)):
            press(app, "Open project")
        self.assertEqual(app.researcher_name.get(), ACTOR)
        notes = [(event.actor, json.loads(event.details)) for event in app.audit_events if event.action == "match_review_note"]
        self.assertEqual([(actor, n["match"]["quote"]["source_start"], n["disposition"], n["reason"]) for actor, n in notes],
                         [(ACTOR, 99, "deferred", DEFER), (ACTOR, 105, "supported", CONTROL_REASON),
                          (OTHER_ACTOR, 190, "deferred", OTHER_REASON), (ACTOR, 190, "supported", REASON)])
        app.review_source_box.current(app.review_source_box["values"].index(INTERVIEW_FILE))
        for actor, button, reason in ((OTHER_ACTOR, "Next deferred", OTHER_REASON), (ACTOR, "Next supported", REASON)):
            app.researcher_name.set(actor)
            press(app, button)
            quote = app._current_quote_match().quote
            self.assertEqual((quote.source_name, quote.source_line, quote.source_start, quote.source_end,
                              quote.speaker, quote.text), SECOND)
            self.assertIn(reason, app.match_review_text.get())
        self.assertEqual(self.export_tables(app, root, "after"), before)
        self.assertEqual(project.read_bytes(), original)

    def test_heading_normalization_preserves_each_slice_and_keeps_unlinked_renderings_unpaired(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        book = root / "codes.csv"
        book.write_text("code,definition\n상자 이동,상자를 선반으로 옮긴 일. Moving a crate onto a shelf.\n", encoding="utf-8")
        korean = "나무 상자를 파란 선반으로 옮겼어요."
        english = "I moved the wooden crate onto the blue shelf."
        control = "확인용 상자를 낮은 선반으로 옮겼어요."
        projects = []
        for form, reader, start, end in (("NFC", "Reed", 10, 30), ("NFD", "Flax", 17, 37)):
            with self.subTest(form=form):
                edition = root / form
                edition.mkdir()
                rendering = f"rendering-{reader}.txt"  # Independently supplied, with no common source identity.
                sources = {"shared.txt": f"봄: {control}",
                           "shift.txt": normalize(form, "작업 기록") + f"\n\n솔: {korean}",
                           rendering: f"Guide: {english}"}
                for name, text in sources.items():
                    (edition / name).write_bytes(text.replace("\n", "\r\n").encode("utf-8-sig"))
                # Offsets are frozen in each imported LF document's Unicode code points.
                shift = ("shift.txt", 3, start, end, "솔", korean)
                translation = (rendering, 1, 7, 52, "Guide", english)
                shared = ("shared.txt", 1, 3, 24, "봄", control)
                app = ThemeForgeApp()
                try:
                    with patch("themeforge.app.filedialog.askopenfilenames",
                               return_value=[str(edition / name) for name in sources]):
                        press(app, "Choose files")
                    with patch("themeforge.app.filedialog.askopenfilename", return_value=str(book)):
                        press(app, "Choose codebook")
                    app.researcher_name.set(reader)
                    press(app, "Analyze")
                    for citation in (shared, shift, translation):
                        self.review(app, citation, "Supports this theme", "The speaker describes moving the crate.")
                    for name, line, begin, stop, speaker, passage in (shift, translation):
                        app.transcript_notebook.select(app.transcript_frames[name])
                        app.update()
                        widget = app.transcript_widgets[name]
                        widget.tag_add("sel", f"{line}.{len(speaker) + 2}", f"{line}.end")
                        selected = app._selected_transcript_text()
                        self.assertEqual((selected.source_name, selected.source_line, selected.source_start,
                                          selected.source_end, selected.text), (name, line, begin, stop, passage))
                        self.assertEqual(sources[name][begin:stop], passage)
                        app.definition_box.current(0)
                        with patch.object(app, "wait_window", side_effect=lambda d: press(d, "Apply selected definition")):
                            press(app, "Apply codebook entry…")
                    project = edition / "saved.tfproj"
                    with patch("themeforge.app.filedialog.asksaveasfilename", return_value=str(project)):
                        self.assertTrue(app.save_project())
                    projects.append(project)
                finally:
                    app.destroy()

                reopened = ThemeForgeApp()
                self.addCleanup(reopened.destroy)
                with patch("themeforge.app.filedialog.askopenfilename", return_value=str(project)):
                    press(reopened, "Open project")
                restored = {d.name: d.text for d in reopened.documents}
                self.assertEqual(restored, sources)
                press(reopened, "Rigor tools")
                audit, = [w for w in reopened.winfo_children() if isinstance(w, RigorDialog)]
                rows = export(audit, "Export this project's manual codebook assignments", edition / "assignments.csv")
                self.assertEqual({r["source"] for r in rows}, {"shift.txt", rendering})
                self.assertEqual(len(rows), 2)
                for row in rows:
                    name, line, begin, stop, _, passage = shift if row["source"] == "shift.txt" else translation
                    self.assertEqual((int(row["source_line"]), int(row["source_start"]), int(row["source_end"]),
                                      row["passage"], row["assigned_by"]), (line, begin, stop, passage, reader))
                    self.assertEqual(restored[name][int(row["source_start"]):int(row["source_end"])], row["passage"])
                    self.assertEqual(row["source_snapshot_sha256"], sha256(sources[name].encode()).hexdigest())
                audit.destroy()

        # The same Korean passage under different headings and the separately sourced
        # English text were actually reviewed in both studies. Only the shared control aligns.
        press(reopened, "Rigor tools")
        dialog, = [w for w in reopened.winfo_children() if isinstance(w, RigorDialog)]
        with patch("themeforge.rigor_dialog.filedialog.askopenfilename", return_value=str(projects[0])):
            press(dialog, "Choose comparison project")
        report = dialog._paired_reviews()
        self.assertEqual([(m.quote.source_name, m.quote.text, m.outcome) for m in report.matches],
                         [("shared.txt", control, "both_supported")])
        self.assertEqual((report.current_excluded, report.comparison_excluded), (4, 4))
        with answer_paired_scope(counts_only=True):
            rows = export(dialog, "Export paired match reviews", root / "paired-counts.csv")
        counts = {r["Paired review outcome or scope"]: int(r["Count"]) for r in rows}
        self.assertEqual(counts["Comparable pairs (denominator for the six review outcomes)"], 1)
        self.assertEqual(counts["Agreement (both supported)"], 1)
        self.assertEqual(counts["Total pairs across selected sources"], 1)

    def test_plain_example_and_edited_guidance_preserve_exact_suggestion_boundaries(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        app = ThemeForgeApp()
        self.addCleanup(app.destroy)
        app.minsize(1, 1)
        app.geometry("1024x705+0+0")
        retained = ("wardrobe.txt", 1, 6, 76, "Dyer",
                    "The buckled clasps caught on my glove so I laid them on the side tray.")
        removed = ("wardrobe.txt", 2, 83, 134, "Dyer", "We returned the frayed braid to the mending basket.")
        added = ("종이방.txt", 1, 4, 31, "다래", "번짐 자국 때문에 종이 한 장을 따로 빼 뒀어요.")
        for name, rows in ((retained[0], (retained, removed)), (added[0], (added,))):
            (root / name).write_text("\n".join(f"{speaker}: {text}" for _, _, _, _, speaker, text in rows),
                                     encoding="utf-8")
        paths = [str(root / n) for n in (retained[0], added[0])]
        with patch("themeforge.app.filedialog.askopenfilenames", return_value=paths):
            press(app, "Choose files")

        def book(example):
            path = root / "codes.csv"
            with path.open("w", encoding="utf-8", newline="") as stream:
                csv.writer(stream).writerows([("code", "example"), ("Screening", example)])
            with patch("themeforge.app.filedialog.askopenfilename", return_value=str(path)):
                press(app, "Choose codebook")

        def citations():
            theme, = app.result.themes
            return {(q.source_name, q.source_line, q.source_start, q.source_end, q.speaker, q.text) for q in theme.quotes}

        book("I listed what to exclude: buckled clasps and frayed braid.")
        app.researcher_name.set("Copper")
        press(app, "Analyze")
        self.assertEqual(citations(), {retained, removed})
        self.review(app, retained, "Supports this theme", "The clasp snagged and the garment was set aside.")
        project = root / "original.tfproj"
        with patch("themeforge.app.filedialog.asksaveasfilename", return_value=str(project)):
            press(app, "Save project")
        saved = project.read_bytes()
        with patch("themeforge.app.filedialog.askopenfilename", return_value=str(project)):
            press(app, "Open project")
        self.assertEqual(citations(), {retained, removed})
        with patch("themeforge.app.filedialog.asksaveasfilename", return_value=str(root / "revised.tfproj")):
            press(app, "Save as…")
        book("I listed what to exclude: buckled clasps.\n  Exclude: frayed braid.\nInclude: 번짐 자국.")
        before = tuple(app.audit_events)
        press(app, "Preview coverage")
        press(app, "Next without suggestion")
        selection = app._selected_transcript_text()
        self.assertEqual((selection.source_name, selection.source_start, selection.source_end, selection.text),
                         (removed[0], removed[2], removed[3], removed[5]))
        self.assertIn("Review decisions are separate", app.quote_reason.get())
        self.assertEqual(tuple(app.audit_events), before)
        with patch("themeforge.app.messagebox.askokcancel", return_value=True):
            press(app, "Analyze")
        self.assertEqual(citations(), {retained, added})
        self.review(app, added, "Supports this theme", "번짐 자국을 보고 종이를 따로 빼 두었다.")
        self.assertEqual([e for e in app.audit_events if e.action == "match_review_note"][:-1],
                         [e for e in before if e.action == "match_review_note"])
        self.assertEqual(project.read_bytes(), saved)

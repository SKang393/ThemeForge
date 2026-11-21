"""Citations in an original fictional shift interview and an English control."""
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from test_evidence_handoff import export, press
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
REASON = "두 번째 교대 때 봉투를 건넸고 저녁 조가 수령을 확인했다."
DEFER = "첫 번째 교대 때 누가 받았는지 확인이 필요하다."
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
        for withheld in (DEFER, CONTROL_FILE, CONTROL_QUOTE, CONTROL_REASON):
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
        notes = [json.loads(event.details) for event in app.audit_events if event.action == "match_review_note"]
        self.assertEqual([(n["match"]["quote"]["source_start"], n["disposition"], n["reason"]) for n in notes],
                         [(99, "deferred", DEFER), (105, "supported", CONTROL_REASON), (190, "supported", REASON)])
        self.assertEqual(self.export_tables(app, root, "after"), before)
        self.assertEqual(project.read_bytes(), original)

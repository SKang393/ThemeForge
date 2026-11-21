"""Review a fictional correction while preserving the original two citations."""
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from test_evidence_handoff import export, press
from themeforge.analysis_types import TranscriptDocument
from themeforge.app import ThemeForgeApp
from themeforge.definition_review import CodebookReuseDialog, MatchReviewReason
from themeforge.rigor_dialog import RigorDialog
from themeforge.ui_model import transcript_import_comparison


QUOTE = "I seated the amber peg in the rail."
CORRECTION = "I only marked a place for the amber peg."
ORIGINAL = f"Joiner: {QUOTE}\nJoiner: A spare hung beside the bench.\nJoiner: {QUOTE}"
REASONS = ("The first fitting was completed.", "The later fitting was also completed.")


class TranscriptAmendmentTests(unittest.TestCase):
    def test_reopen_cancel_and_review_two_amendments_as_separate_studies(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        source = root / "bench.txt"
        source.write_text(ORIGINAL, encoding="utf-8")
        book = root / "codes.csv"
        book.write_text("code,definition\nFitting,The joiner seated the amber peg in the rail.\n", encoding="utf-8")
        app = ThemeForgeApp()
        self.addCleanup(app.destroy)

        def choose():
            with patch("themeforge.app.filedialog.askopenfilenames", return_value=[str(source)]):
                press(app, "Choose files")

        def reopen(path):
            with patch("themeforge.app.filedialog.askopenfilename", return_value=str(path)), \
                    patch("themeforge.app.load_transcript_text", side_effect=AssertionError("unexpected disk read")):
                press(app, "Open project")
            self.assertEqual(app.project_path, path)

        def save(path):
            with patch("themeforge.app.filedialog.asksaveasfilename", return_value=str(path)):
                press(app, "Save project")
            self.assertEqual(app.project_path, path)

        def review(button, reason):
            def answer(dialog, *_args):
                dialog.reason.insert(0, reason)
                dialog.ok()
            with patch.object(MatchReviewReason, "wait_window", autospec=True, side_effect=answer) as prompt:
                press(app, button)
            prompt.assert_called_once()

        table = root / "scoped.csv"

        def supported():
            press(app, "Rigor tools")
            dialog, = [w for w in app.winfo_children() if isinstance(w, RigorDialog)]
            dialog.audit_tree.master.master.select(dialog.audit_tree.master)
            self.assertEqual(dialog.supported_sources.get(0, "end"), (source.name,))
            dialog.supported_sources.selection_set(0)
            rows = export(dialog, "Export supported matches CSV", table)
            dialog.destroy()
            return rows

        def citation(row):
            return (int(row["source_start"]), int(row["source_end"]), row["quote"],
                    row["match_review_actor"], row["match_review_reason"], row["source_sha256"])

        choose()
        with patch("themeforge.app.filedialog.askopenfilename", return_value=str(book)):
            press(app, "Choose codebook")
        app.researcher_name.set("Bench reader")
        for start, end, reason in ((8, 43, REASONS[0]), (91, 126, REASONS[1])):
            self.assertEqual(ORIGINAL[start:end], QUOTE)
            widget = app.transcript_widgets[source.name]
            widget.tag_remove("sel", "1.0", "end")
            widget.tag_add("sel", f"1.0+{start}c", f"1.0+{end}c")
            app.definition_box.current(0)
            with patch.object(app, "wait_window", side_effect=lambda d: press(d, "Apply selected definition")):
                press(app, "Apply codebook entry…")
            review("Supports this theme", reason)

        project = root / "original.tfproj"
        save(project)
        saved_state = app._project_state()
        project_bytes = project.read_bytes()
        digest = sha256(ORIGINAL.encode()).hexdigest()
        self.assertCountEqual([citation(row) for row in supported()],
                              [(8, 43, QUOTE, "Bench reader", REASONS[0], digest),
                               (91, 126, QUOTE, "Bench reader", REASONS[1], digest)])
        evidence_bytes = table.read_bytes()

        def cancel_and_check(comparison):
            with patch("themeforge.app.messagebox.askyesnocancel", return_value=None) as confirm:
                choose()
            self.assertIn(comparison, confirm.call_args.args[1])
            self.assertEqual((app.project_path, app._project_state()), (project, saved_state))
            self.assertEqual(project.read_bytes(), project_bytes)

        # Encoding and line endings can differ even when the imported text is identical.
        source.write_bytes(ORIGINAL.replace("\n", "\r\n").encode("utf-8-sig"))
        cancel_and_check("1 identical, 0 different")
        for variant, revised, last in (
            ("elsewhere", ORIGINAL.replace("bench.", "lathe."), QUOTE),
            ("second-fitting", ORIGINAL[:91] + CORRECTION, CORRECTION),
        ):
            with self.subTest(variant=variant):
                source.write_text(revised, encoding="utf-8")
                reopen(project)
                self.assertEqual(app.documents[0].text, ORIGINAL)
                cancel_and_check("0 identical, 1 different. Different text in bench.txt")
                supported()
                self.assertEqual(table.read_bytes(), evidence_bytes)

                def reuse(dialog, *_args):
                    dialog.choice.set("Names and definitions only")
                    dialog.show_preview()
                    dialog.ok()

                with patch("themeforge.app.messagebox.askyesnocancel", return_value=False), \
                        patch.object(CodebookReuseDialog, "wait_window", autospec=True, side_effect=reuse):
                    choose()
                self.assertIsNone(app.project_path)
                self.assertIsNone(app.result)
                self.assertEqual(app.audit_events, [])
                self.assertEqual(app.documents[0].text, revised)
                app.researcher_name.set("Amendment reader")
                press(app, "Analyze")
                self.assertEqual(supported(), [])
                for _ in app.current_quote_matches:
                    if app._current_quote_match().quote.source_start == 91:
                        break
                    press(app, "Next quote")
                quote = app._current_quote_match().quote
                self.assertEqual((quote.source_start, quote.source_end, quote.text), (91, len(revised), last))
                self.assertEqual(revised[91:quote.source_end], last)
                if last == QUOTE:
                    reason = "The amended account still records the second fitting."
                    review("Supports this theme", reason)
                    expected = [(91, 126, QUOTE, "Amendment reader", reason, sha256(revised.encode()).hexdigest())]
                else:
                    reason = "The correction reports marking a position, without fitting the peg."
                    review("Not this match", reason)
                    expected = []
                amendment = root / f"{variant}.tfproj"
                save(amendment)
                reopen(amendment)
                self.assertEqual(app.documents[0].text, revised)
                self.assertEqual([citation(row) for row in supported()], expected)
                event, = [e for e in app.audit_events if e.action == "match_review_note"]
                decision = json.loads(event.details)
                match = decision["match"]
                self.assertEqual((event.actor, decision["reason"], decision["disposition"]),
                                 ("Amendment reader", reason, "supported" if last == QUOTE else "declined"))
                self.assertEqual(match["source_sha256"], sha256(revised.encode()).hexdigest())
                self.assertEqual((match["quote"]["source_start"], match["quote"]["source_end"]), (91, len(revised)))
                self.assertEqual(project.read_bytes(), project_bytes)
        reopen(project)
        self.assertEqual(app._project_state(), saved_state)
        supported()
        self.assertEqual(table.read_bytes(), evidence_bytes)

    def test_duplicate_filenames_are_not_paired_by_import_order(self):
        old = [Path(r"C:\one\bench.txt"), Path(r"C:\two\bench.txt"), Path(r"C:\one\codes.txt")]
        docs = [TranscriptDocument("bench.txt", "First."), TranscriptDocument("bench.txt [2]", "Second."),
                TranscriptDocument("codes.txt", "Third.")]
        selected = [Path("new/bench.txt"), Path("new/codes.txt")]
        summary = transcript_import_comparison(old, docs, selected, [docs[0], docs[2]])
        self.assertIn("1 identical, 0 different", summary)
        self.assertIn("1 selected without a unique filename match", summary)

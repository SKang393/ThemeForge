"""Revise one of two overlapping manual meanings in a fictional weaving interview."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from test_evidence_handoff import export, open_audit, press
from themeforge.app import ThemeForgeApp
from themeforge.definition_review import MatchReviewReason

SOURCE = "Tern: I chose the pattern while my tutor steadied the loom, and then wove a row on my own."
CHOICE = "I chose the pattern while my tutor steadied the loom"
GUIDANCE = "while my tutor steadied the loom, and then wove a row on my own"
SHORT = "I chose the pattern"
MANUAL = "Export this project's manual codebook assignments"


class OverlappingAssignmentsTests(unittest.TestCase):
    def test_revise_one_boundary_and_reopen_its_evidence(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        source = root / "loom.txt"
        source.write_text(SOURCE, encoding="utf-8")
        book = root / "codes.csv"
        book.write_text("code,definition\nLearner choice,The learner chooses a pattern or task.\n"
                        "Guided practice,Support is withdrawn as the learner takes over.\n", encoding="utf-8")
        app = ThemeForgeApp()
        self.addCleanup(app.destroy)
        app.update()
        with patch("themeforge.app.filedialog.askopenfilenames", return_value=[str(source)]):
            press(app, "Choose files")
        with patch("themeforge.app.filedialog.askopenfilename", return_value=str(book)):
            press(app, "Choose codebook")

        def select(start, end, expected):
            widget = app.transcript_widgets[source.name]
            widget.tag_remove("sel", "1.0", "end")
            widget.tag_add("sel", f"1.0+{start}c", f"1.0+{end}c")
            self.assertEqual(app._selected_transcript_text().text, expected)

        def support(reason):
            def answer(dialog, *_args):
                dialog.reason.insert(0, reason)
                dialog.ok()
            with patch.object(MatchReviewReason, "wait_window", autospec=True, side_effect=answer) as modal:
                press(app, "Supports this theme")
            modal.assert_called_once()

        for index, (name, start, end, passage, reason) in enumerate((
            ("Finch", 6, 58, CHOICE, "The learner chose the pattern."),
            ("Lark", 26, 89, GUIDANCE, "The tutor steadied the loom before the learner took over."),
        )):
            app.researcher_name.set(name)
            select(start, end, passage)
            app.definition_box.current(index)
            with patch.object(app, "wait_window", side_effect=lambda d: press(d, "Apply selected definition")):
                press(app, "Apply codebook entry…")
            support(reason)

        app.theme_list.selection_clear(0, "end")
        app.theme_list.selection_set(0)
        app.theme_list.event_generate("<<ListboxSelect>>")
        app.update()
        app.quote_memo_text.insert("1.0", "Track who chose the task.")
        press(app, "Save quote memo")
        project = root / "loom.tfproj"
        with patch("themeforge.app.filedialog.asksaveasfilename", return_value=str(project)):
            press(app, "Save project")

        def tables(stage):
            dialog = open_audit(app, project)
            manual = export(dialog, MANUAL, root / f"{stage}-assignments.csv")
            dialog.supported_sources.selection_set(0)
            supported = export(dialog, "Export supported matches CSV", root / f"{stage}-supported.csv")
            dialog.destroy()
            return manual, supported

        (original, other), (old_support, other_support) = tables("before")
        for row, start, end, passage in ((original, 6, 58, CHOICE), (other, 26, 89, GUIDANCE)):
            self.assertEqual((row["source"], int(row["source_start"]), int(row["source_end"]), row["passage"]),
                             (source.name, start, end, passage))
            self.assertEqual(SOURCE[start:end], passage)
        self.assertEqual((original["assigned_by"], other["assigned_by"]), ("Finch", "Lark"))
        self.assertNotEqual(original["theme_id"], other["theme_id"])
        self.assertEqual(len({original["assignment_id"], other["assignment_id"]}), 2)
        self.assertEqual((old_support["match_review_reason"], other_support["match_review_reason"]),
                         ("The learner chose the pattern.", "The tutor steadied the loom before the learner took over."))
        prior_events = tuple(app.audit_events)
        saved = project.read_bytes()
        select(6, 25, SHORT)

        def boundary(dialog, action):
            self.assertEqual(dialog.passage.get("evidence.first", "evidence.last"), SHORT)
            location = dialog.location.cget("text")
            for value in ("loom.txt", "Learner choice", original["theme_id"], CHOICE, "[6:58]", "[6:25]", "Lark"):
                self.assertIn(value, location)
            self.assertIn("The learner chooses a pattern or task.", dialog.definitions.get("1.0", "end"))
            self.assertEqual(len(dialog.choice.cget("values")), 1)
            press(dialog, action)

        with patch.object(app, "wait_window", side_effect=lambda d: boundary(d, "Cancel")) as modal:
            press(app, "Update boundary")
        modal.assert_called_once()
        self.assertEqual(project.read_bytes(), saved)
        self.assertEqual(tuple(app.audit_events), prior_events)
        with patch.object(app, "wait_window", side_effect=lambda d: boundary(d, "Replace assignment")) as modal:
            press(app, "Update boundary")
        modal.assert_called_once()
        new_id = app._current_quote_match().quote.quote_id
        self.assertNotEqual(new_id, original["assignment_id"])
        self.assertEqual(app._current_quote_match().quote.text, SHORT)
        press(app, "Undo")
        self.assertEqual(app._current_quote_match().quote.quote_id, original["assignment_id"])
        press(app, "Redo")
        self.assertEqual(app._current_quote_match().quote.quote_id, new_id)

        (revised, untouched), supported = tables("after")
        self.assertEqual(untouched, other)
        self.assertEqual(supported, [other_support])
        self.assertEqual((revised["assignment_id"], revised["theme_id"], revised["code_name"], revised["definition"]),
                         (new_id, original["theme_id"], original["code_name"], original["definition"]))
        self.assertEqual((revised["source_start"], revised["source_end"], revised["passage"], revised["assigned_by"],
                          revised["current_quote_memo"]), ("6", "25", SHORT, "Lark", "Track who chose the task."))
        self.assertEqual(tuple(app.audit_events[:len(prior_events)]), prior_events)
        revision, = [e for e in app.audit_events if e.action == "quote_boundary_updated"]
        self.assertEqual((revision.target_id, revision.actor), (original["assignment_id"], "Lark"))
        self.assertEqual(json.loads(revision.details), {"replacement_id": new_id, "theme_id": original["theme_id"],
                         "source": source.name, "previous_span": [6, 58], "replacement_span": [6, 25]})
        support("The shorter excerpt identifies the choice on its own.")
        _, current = tables("reviewed")
        self.assertEqual(current[1], other_support)
        self.assertEqual((current[0]["quote"], current[0]["match_review_reason"], current[0]["match_review_actor"]),
                         (SHORT, "The shorter excerpt identifies the choice on its own.", "Lark"))

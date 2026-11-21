"""Find the disputed occurrence after two readers save separate fictional studies."""
import json
from pathlib import Path
from shutil import copyfile
from tempfile import TemporaryDirectory
from tkinter import Button, ttk
from tkinter.scrolledtext import ScrolledText
import unittest
from unittest.mock import patch

from test_evidence_handoff import descendants, export, open_audit, press
from themeforge.app import ThemeForgeApp
from themeforge.definition_review import MatchReviewReason
from themeforge.match_reviews import REVIEW_ACTION, REVIEW_LABELS
from themeforge.rigor_dialog import RigorDialog


KNOT = "I knotted the blue thread around the packet."
LINES = (KNOT, "I tucked a loose loop under the fold for later.", KNOT,
         "I only counted the cut lengths that morning.", "I threaded the ends through the paper ring.")
TEXT = "\n".join(f"Binder: {line}" for line in LINES)
WITHHELD = "I fastened a quince ticket to the plum crate."
# Zero denotes the withheld source. Other keys are one-based lines in packet.txt.
NOTES = {
    "Jute": {1: ("supported", "A completed knot on the first packet."),
             2: ("deferred", "Check whether the loop was tightened later."),
             3: ("supported", "The second account also describes tying the packet."),
             5: ("declined", "Threading alone leaves the ring open."),
             0: ("supported", "The plum crate was fastened.")},
    "Slate": {1: ("supported", "The first parcel was tied."),
              2: ("supported", "The loop held the fold."),
              3: ("declined", "=I read the repeated words as a demonstration."),
              4: ("declined", "Counting lengths does not secure the packet."),
              5: ("supported", "Passing both ends through the ring secures it."),
              0: ("supported", "The quince ticket was attached.")},
}


class LocateDisagreementTests(unittest.TestCase):
    def test_two_saved_first_passes_locate_exact_disputes_without_changing_them(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        paths = [root / "packet.txt", root / "reserve.txt"]
        paths[0].write_text(TEXT, encoding="utf-8")
        paths[1].write_text("Keeper: " + WITHHELD, encoding="utf-8")
        with patch.object(ThemeForgeApp, "maxsize", return_value=(1024, 705)):
            app = ThemeForgeApp()
        self.addCleanup(app.destroy)
        with patch("themeforge.app.filedialog.askopenfilenames", return_value=list(map(str, paths))):
            press(app, "Choose files")
        app.theme_count.set(1)
        app.quotes_per_theme.set(0)
        press(app, "Analyze")
        app.theme_name.set("Securing a packet")
        press(app, "Save theme edits")
        app.researcher_name.set("Slate")  # A comparison with this seed has no first-pass decisions.
        seed = root / "unreviewed.tfproj"
        with patch("themeforge.app.filedialog.asksaveasfilename", return_value=str(seed)):
            press(app, "Save project")

        def reopen(path):
            with patch("themeforge.app.filedialog.askopenfilename", return_value=str(path)):
                press(app, "Open project")
            app.update()

        copies = {}
        for actor, decisions in NOTES.items():
            path = root / f"{actor}.tfproj"
            copyfile(seed, path)
            reopen(path)
            app.researcher_name.set(actor)
            seen = set()
            for _ in app.current_quote_matches:
                quote = app._current_quote_match().quote
                line = quote.source_line if quote.source_name == "packet.txt" else 0
                if line in decisions:
                    disposition, reason = decisions[line]

                    def answer(dialog, _window):
                        area, = [w for w in descendants(dialog) if isinstance(w, ScrolledText)]
                        self.assertEqual(area.get("evidence.first", "evidence.last"), LINES[line - 1] if line else WITHHELD)
                        dialog.reason.insert(0, reason)
                        ok, = [w for w in descendants(dialog) if isinstance(w, Button) and w.cget("text") == "OK"]
                        ok.invoke()

                    with patch.object(MatchReviewReason, "wait_window", autospec=True, side_effect=answer) as modal, \
                            patch.object(app, "report_callback_exception") as errors:
                        press(app, REVIEW_LABELS[disposition])
                        if errors.called:
                            raise errors.call_args.args[1]
                        modal.assert_called_once()
                    seen.add(line)
                press(app, "Next quote")
            self.assertEqual(seen, decisions.keys())
            press(app, "Save project")
            reopen(path)
            restored = {}
            for event in app.audit_events:
                if event.action == REVIEW_ACTION:
                    self.assertEqual(event.actor, actor)
                    details = json.loads(event.details)
                    q = details["match"]["quote"]
                    restored[q["source_line"] if q["source_name"] == "packet.txt" else 0] = (
                        details["disposition"], details["reason"])
            self.assertEqual(restored, decisions)
            copies[path] = path.read_bytes()

        reopen(root / "Jute.tfproj")
        before = app._project_state()
        press(app, "Rigor tools")
        dialog, = [w for w in app.winfo_children() if isinstance(w, RigorDialog)]
        dialog.geometry("900x600+0+0")
        tab = dialog.disagreement_tree.master
        tab.master.select(tab)
        with patch("themeforge.rigor_dialog.filedialog.askopenfilename", return_value=str(root / "Slate.tfproj")):
            press(dialog, "Choose comparison project")
        app.update()
        self.assertEqual(dialog.disagreement_tree.get_children(), ())
        button, = [w for w in descendants(tab) if isinstance(w, ttk.Button) and w.cget("text") == "Next review disagreement"]
        self.assertTrue(button.winfo_viewable())
        self.assertGreaterEqual(button.winfo_width(), button.winfo_reqwidth())
        x, y = button.winfo_rootx(), button.winfo_rooty()
        for px, py in ((x, y), (x + button.winfo_width() - 1, y + button.winfo_height() - 1)):
            self.assertTrue(0 <= px < 900 and 0 <= py < 600)
            self.assertEqual(dialog.winfo_containing(px, py), button)
        self.assertEqual((dialog.current_reviewer.get(), dialog.comparison_reviewer.get()), ("Jute", "Slate"))
        paired = export(dialog, "Export paired match reviews", root / "paired.csv")
        self.assertEqual({int(r["source_line"]): r["review_comparison"] for r in paired if r["source_file"] == "packet.txt"},
                         {1: "both_supported", 2: "deferred", 3: "disagreement", 4: "missing_current_review",
                          5: "disagreement"})

        app.quote_memo_text.insert("1.0", "An unfinished thought.")
        previous = app._current_quote_match()
        press(dialog, "Next review disagreement")
        self.assertIn("unfinished", dialog.status.get())
        self.assertEqual(app._current_quote_match(), previous)
        self.assertEqual(app.quote_memo_text.get("1.0", "end-1c"), "An unfinished thought.")
        app.quote_memo_text.delete("1.0", "end")
        for expected, number in ((3, 1), (5, 2), (3, 1)):
            press(dialog, "Next review disagreement")
            app.update()
            self.assertIn(f"{number} of 2", dialog.status.get())
            self.assertIn("Both reviewed with a deferral (1)", dialog.status.get())
            self.assertIn("Missing one or both reviews (1)", dialog.status.get())
            quote = app._current_quote_match().quote
            start = TEXT.rindex(LINES[expected - 1])
            end = start + len(LINES[expected - 1])
            self.assertEqual((app._current_document_name(), quote.source_line, quote.source_start, quote.source_end),
                             ("packet.txt", expected, start, end))
            widget = app.transcript_widgets["packet.txt"]
            self.assertEqual(widget.index("selected_quote.first"), f"{expected}.8")
            self.assertEqual(widget.get("selected_quote.first", "selected_quote.last"), quote.text)
            details = dialog.dispute_text.get("1.0", "end-1c")
            self.assertIn(f"packet.txt, line {expected} [{start}, {end})", details)
            for actor, notes in NOTES.items():
                verdict, why = notes[expected]
                self.assertIn(f"{actor} ({verdict})\n{why}", details)
                pos = dialog.dispute_text.search(why, "1.0")
                dialog.dispute_text.see(pos)
                app.update_idletasks()
                self.assertIsNotNone(dialog.dispute_text.bbox(pos))
            self.assertTrue(dialog.dispute_text.winfo_viewable())
            self.assertEqual(dialog.dispute_text.cget("state"), "disabled")
            self.assertEqual(app._project_state(), before)

        def check_scoped(audit, reviewer):
            audit.audit_tree.master.master.select(audit.audit_tree.master)
            scope = audit.supported_sources
            self.assertEqual(scope.curselection(), ())
            scope.selection_set(scope.get(0, "end").index("packet.txt"))
            handoff = root / f"{reviewer}-scoped.csv"
            rows = export(audit, "Export supported matches CSV", handoff)
            expected = [(str(line), LINES[line - 1], reviewer, reason)
                        for line, (state, reason) in NOTES[reviewer].items() if line and state == "supported"]
            self.assertCountEqual([(r["source_line"], r["quote"], r["match_review_actor"], r["match_review_reason"])
                                   for r in rows], expected)
            contents = handoff.read_text(encoding="utf-8")
            for outside in ("reserve.txt", WITHHELD, NOTES["Jute"][0][1], NOTES["Slate"][0][1], NOTES["Slate"][3][1]):
                self.assertNotIn(outside, contents)

        check_scoped(dialog, "Jute")
        tab.master.select(tab)
        with patch("themeforge.rigor_dialog.filedialog.askopenfilename", return_value=str(seed)):
            press(dialog, "Choose comparison project")
        press(dialog, "Next review disagreement")
        self.assertIn("No support-versus-decline disagreements", dialog.status.get())
        self.assertIn("Missing one or both reviews (6)", dialog.status.get())
        self.assertEqual(dialog.dispute_text.get("1.0", "end-1c"), "")
        dialog.destroy()
        app.update()
        self.assertEqual(app._current_quote_match().quote.source_line, 3)
        self.assertIsNotNone(app.transcript_widgets["packet.txt"].bbox("selected_quote.first"))
        self.assertEqual(app._project_state(), before)
        check_scoped(open_audit(app, root / "Slate.tfproj"), "Slate")
        self.assertEqual({p: p.read_bytes() for p in copies}, copies)

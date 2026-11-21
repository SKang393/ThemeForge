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
from test_paired_match_reviews import answer_paired_scope
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
        app = ThemeForgeApp()
        screen = app.winfo_screenwidth(), app.winfo_screenheight()
        if app.tk.call("tk", "windowingsystem") == "win32" and screen == (1024, 768):
            app.state("zoomed")
        else:
            app.minsize(1, 1)
            app.geometry("1024x705+0+0")
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
        controls = {w.cget("text"): w for w in descendants(tab) if isinstance(w, ttk.Button)}
        notice, = [w for w in descendants(tab) if isinstance(w, ttk.Label) and "all shared sources" in w.cget("text")]
        self.assertIn("Choose sources", notice.cget("text"))
        self.assertIn("before sharing", notice.cget("text"))
        for control in (controls["Next review disagreement"], controls["Export paired match reviews"], notice):
            self.assertTrue(control.winfo_viewable())
            self.assertGreaterEqual(control.winfo_width(), control.winfo_reqwidth())
            self.assertGreaterEqual(control.winfo_height(), control.winfo_reqheight())
            x, y = control.winfo_rootx(), control.winfo_rooty()
            for px, py in ((x, y), (x + control.winfo_width() - 1, y + control.winfo_height() - 1)):
                self.assertTrue(0 <= px < min(screen[0], 1024) and 0 <= py < min(screen[1], 768))
                self.assertEqual(dialog.winfo_containing(px, py), control)
        self.assertEqual((dialog.current_reviewer.get(), dialog.comparison_reviewer.get()), ("Jute", "Slate"))
        all_pairs = dialog._paired_reviews()
        coverage = dialog.report
        with answer_paired_scope():
            paired = export(dialog, "Export paired match reviews", root / "paired.csv")
        self.assertEqual(len(paired), 6)
        reserve, = [r for r in paired if r["source_file"] == "reserve.txt"]
        self.assertEqual((reserve["quote"], reserve["current_reason"], reserve["comparison_reason"]),
                         (WITHHELD, NOTES["Jute"][0][1], NOTES["Slate"][0][1]))
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
                last = f"{pos} + {len(why) - 1} chars"
                dialog.dispute_text.see(last)
                app.update_idletasks()
                for char in (pos, last):
                    box = dialog.dispute_text.bbox(char)
                    self.assertIsNotNone(box)
                    x = dialog.dispute_text.winfo_rootx() + box[0] + box[2] // 2
                    y = dialog.dispute_text.winfo_rooty() + box[1] + box[3] // 2
                    self.assertEqual(dialog.winfo_containing(x, y), dialog.dispute_text)
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
        app.update()

        def choose_packet(scope, _window):
            scope.update()
            self.assertEqual(scope.sources.curselection(), ())
            self.assertIn("0 of 6 paired rows", scope.preview.get())
            index = scope.sources.get(0, "end").index("packet.txt")
            x, y, width, height = scope.sources.bbox(index)
            for event in ("<ButtonPress-1>", "<ButtonRelease-1>"):
                scope.sources.event_generate(event, x=x + width // 2, y=y + height // 2)
            scope.update()
            preview, = [w for w in descendants(scope) if isinstance(w, ttk.Label)
                        and str(w.cget("textvariable")) == str(scope.preview)]
            self.assertIn("5 of 6 paired rows from 1 of 2 sources", preview.cget("text"))
            warning, = [w for w in descendants(scope) if isinstance(w, ttk.Label) and "free-text" in w.cget("text")]
            self.assertIn("identify people or refer to other transcripts", warning.cget("text"))
            buttons = {w.cget("text"): w for w in descendants(scope) if isinstance(w, Button)}
            for widget in (scope.sources, preview, warning, buttons["OK"], buttons["Cancel"]):
                self.assertTrue(widget.winfo_viewable())
                self.assertGreaterEqual(widget.winfo_height(), widget.winfo_reqheight())
                self.assertGreaterEqual(widget.winfo_width(), widget.winfo_reqwidth())
                x, y = widget.winfo_rootx(), widget.winfo_rooty()
                self.assertTrue(0 <= x < x + widget.winfo_width() <= min(screen[0], 1024))
                self.assertTrue(0 <= y < y + widget.winfo_height() <= min(screen[1], 768))
            buttons["OK"].invoke()

        scoped_path = root / "paired-packet.csv"
        with answer_paired_scope(choose_packet):
            scoped = export(dialog, "Export paired match reviews", scoped_path)
        self.assertEqual(scoped, [r for r in paired if r["source_file"] == "packet.txt"])
        disputes = [row for row in scoped if row["review_comparison"] == "disagreement"]
        self.assertEqual([row["source_line"] for row in disputes], ["3", "5"])
        for row in disputes:
            line = int(row["source_line"])
            quote = LINES[line - 1]
            start = TEXT.rindex(quote)
            self.assertEqual((int(row["source_start"]), int(row["source_end"]), row["quote"]),
                             (start, start + len(quote), quote))
            for role, actor in (("current", "Jute"), ("comparison", "Slate")):
                verdict, reason = NOTES[actor][line]
                expected_reason = "'" + reason if reason.startswith("=") else reason
                self.assertEqual((row[f"{role}_reviewer"], row[f"{role}_disposition"], row[f"{role}_reason"]),
                                 (actor, verdict, expected_reason))
        contents = scoped_path.read_text(encoding="utf-8")
        for outside in ("reserve.txt", WITHHELD, NOTES["Jute"][0][1], NOTES["Slate"][0][1], reserve["quote_id"],
                        reserve["match_review_target_id"], reserve["current_review_target_id"],
                        reserve["comparison_review_target_id"]):
            self.assertNotIn(outside, contents)
        self.assertEqual(dialog._paired_reviews(), all_pairs)
        self.assertEqual(dialog.report, coverage)
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
        print(f"Desktop {screen[0]}x{screen[1]}. Read both reasons at each disputed span. "
              "Explicit all-source CSV had six matches. Packet-only CSV had five, including two disputes. "
              "Both saved copies retained their bytes.")

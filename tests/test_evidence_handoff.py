"""Exercise a two-reader evidence handoff through the desktop export controls."""
import csv
from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
import tempfile
from tkinter import ttk
import unittest
from unittest.mock import patch

from themeforge.app import ThemeForgeApp
from themeforge.match_reviews import REVIEW_LABELS
from themeforge.project_io import load_project, save_project
from themeforge.rigor_dialog import RigorDialog

REPEAT = "I passed the brass stencil across the counter."
SOURCES = {
    "counter.txt": [
        ("Oak", REPEAT),
        ("Yew", "The 🧵 thread basket stayed under my chair."),
        ("Yew", REPEAT),
        ("Oak", "I only drew a pear on the stencil wrapper."),
    ],
    "studio.txt": [
        ("Ash", "I gave the stencil to the next printer."),
        ("Ash", "The violet ticket might accompany a handoff tomorrow."),
    ],
    "annex.txt": [
        ("Pine", "I handed the stencil to the vermilion custodian."),
        ("Pine", "The vermilion cubby held an extra box of pins."),
    ],
}
DECISIONS = [
    ("counter.txt", 1, "Wren", "declined", "I cannot establish the recipient in this account."),
    ("counter.txt", 3, "Moth", "deferred", "Look up the previous shift before deciding."),
    ("counter.txt", 3, "Wren", "supported", "The printer describes a completed transfer."),
    ("counter.txt", 4, "Wren", "declined", "Decoration alone does not transfer the tool."),
    ("studio.txt", 1, "Moth", "supported", "The printer with the Blue Apron badge received the stencil."),
    ("studio.txt", 2, "Wren", "deferred", "This is a plan for tomorrow."),
    ("annex.txt", 1, "Moth", "supported", "The custodian received it at the annex."),
]


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


def press(widget, label):
    for child in descendants(widget):
        if isinstance(child, ttk.Button) and child.cget("text") == label:
            child.invoke()
            return
    raise AssertionError(label)


def study(app, root):
    files = []
    for name, lines in SOURCES.items():
        text = "\n".join(f"{speaker}: {words}" for speaker, words in lines)
        path = root / name
        path.write_bytes(text.replace("\n", "\r\n").encode("utf-8-sig"))
        files.append(str(path))
    with patch("themeforge.app.filedialog.askopenfilenames", return_value=files):
        press(app, "Choose files")
    app.theme_count.set(1)
    app.quotes_per_theme.set(0)
    app.researcher_name.set("Wren")
    press(app, "Analyze")
    app.theme_name.set("Passing the stencil")
    app.theme_keywords.set("stencil, vermilion cubby")
    press(app, "Save theme edits")
    for source, line, actor, status, reason in DECISIONS:
        app.researcher_name.set(actor)
        for _ in app.current_quote_matches:
            q = app._current_quote_match().quote
            if (q.source_name, q.source_line) == (source, line):
                break
            press(app, "Next quote")
        else:
            raise AssertionError((source, line))
        with patch("themeforge.app.simpledialog.askstring", return_value=reason) as prompt:
            press(app, REVIEW_LABELS[status])
        prompt.assert_called_once()
    app.researcher_name.set("Wren")
    project = root / "printing.tfproj"
    with patch("themeforge.app.filedialog.asksaveasfilename", return_value=str(project)):
        press(app, "Save project")
    return project


def open_audit(app, path):
    with patch("themeforge.app.filedialog.askopenfilename", return_value=str(path)):
        press(app, "Open project")
    press(app, "Rigor tools")
    dialog = next(w for w in app.winfo_children() if isinstance(w, RigorDialog))
    dialog.geometry("900x600+0+0")
    notebook = dialog.audit_tree.master.master
    notebook.select(dialog.audit_tree.master)
    app.update()
    return dialog


def export(dialog, label, path):
    with patch("themeforge.rigor_dialog.filedialog.asksaveasfilename", return_value=str(path)) as choose:
        press(dialog, label)
    choose.assert_called_once()
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


class EvidenceHandoffTests(unittest.TestCase):
    def test_selected_sources_trace_to_text_and_decision_without_the_project_audit(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        with patch.object(ThemeForgeApp, "maxsize", return_value=(1024, 705)):
            app = ThemeForgeApp()
        self.addCleanup(app.destroy)
        project = study(app, root)
        dialog = open_audit(app, project)
        selector = dialog.supported_sources
        self.assertEqual((dialog.winfo_width(), dialog.winfo_height()), (900, 600))
        controls = list(descendants(dialog.audit_tree.master))
        warning, = [w for w in controls if isinstance(w, ttk.Label) and "can identify people" in w.cget("text")]
        self.assertIn("theme labels", warning.cget("text"))
        self.assertIn("free-text reasons", warning.cget("text"))
        audit_scope, = [w for w in controls if isinstance(w, ttk.Label) and "Full project history" in w.cget("text")]
        for label in (warning, audit_scope):
            self.assertGreaterEqual(label.winfo_width(), label.winfo_reqwidth())
            self.assertGreaterEqual(label.winfo_height(), label.winfo_reqheight())
        buttons = [w for w in controls if isinstance(w, ttk.Button)]
        for widget in (selector, warning, audit_scope, *buttons):
            self.assertTrue(widget.winfo_viewable(), str(widget))
            x = widget.winfo_rootx() - dialog.winfo_rootx()
            y = widget.winfo_rooty() - dialog.winfo_rooty()
            self.assertGreaterEqual(min(x, y), 0)
            self.assertLessEqual(x + widget.winfo_width(), dialog.winfo_width())
            self.assertLessEqual(y + widget.winfo_height(), dialog.winfo_height())

        self.assertEqual(selector.curselection(), ())
        with patch("themeforge.rigor_dialog.filedialog.asksaveasfilename") as save_dialog:
            press(dialog, "Export supported matches CSV")
        save_dialog.assert_not_called()
        self.assertIn("Choose at least one transcript", dialog.audit_status.get())
        press(dialog, "Select all sources")
        withheld = selector.get(0, "end").index("annex.txt")
        x, y, width, height = selector.bbox(withheld)
        selector.event_generate("<ButtonPress-1>", x=x + width // 2, y=y + height // 2)
        selector.event_generate("<ButtonRelease-1>", x=x + width // 2, y=y + height // 2)
        app.update()
        scope = tuple(selector.curselection())
        self.assertEqual({selector.get(i) for i in scope}, {"counter.txt", "studio.txt"})
        saved_files = {p.name: p.read_bytes() for p in root.iterdir()}
        with patch("themeforge.rigor_dialog.filedialog.asksaveasfilename", return_value="") as save_dialog:
            press(dialog, "Export supported matches CSV")
        save_dialog.assert_called_once()
        self.assertEqual(dialog.audit_status.get(), "Export canceled.")
        self.assertEqual(selector.curselection(), scope)
        self.assertEqual({p.name: p.read_bytes() for p in root.iterdir()}, saved_files)
        table = root / "handoff.csv"
        supported = export(dialog, "Export supported matches CSV", table)
        by_source = {r["source_file"]: r for r in supported}
        self.assertEqual(len(supported), 2)
        self.assertEqual(set(by_source), {"counter.txt", "studio.txt"})
        self.assertEqual((by_source["counter.txt"]["source_line"], by_source["counter.txt"]["match_review_actor"]),
                         ("3", "Wren"))
        self.assertEqual((by_source["studio.txt"]["source_line"], by_source["studio.txt"]["match_review_actor"]),
                         ("1", "Moth"))
        self.assertEqual(by_source["studio.txt"]["theme_name"], "Passing the stencil")
        self.assertIn("Blue Apron", by_source["studio.txt"]["match_review_reason"])
        omitted = {"theme_keywords", "theme_color", "selection_rationale", "validation_review_status"}
        self.assertTrue(omitted.isdisjoint(by_source["studio.txt"]))
        contents = table.read_text(encoding="utf-8")
        for excluded in ("annex.txt", "vermilion", "previous shift", "violet ticket", "pear on the stencil",
                         "thread basket", "The custodian received", "I cannot establish", str(root)):
            self.assertNotIn(excluded, contents)
        # A collaborator with only the permitted texts can identify the revision and the occurrence.
        for name, row in by_source.items():
            text = (root / name).read_text(encoding="utf-8-sig")
            start, end = int(row["source_start"]), int(row["source_end"])
            self.assertEqual(row["source_sha256"], sha256(text.encode("utf-8")).hexdigest())
            self.assertEqual(text[start:end], row["quote"])
            self.assertEqual(text.count("\n", 0, start) + 1, int(row["source_line"]))
        self.assertEqual((by_source["counter.txt"]["source_start"], by_source["counter.txt"]["source_end"],
                          by_source["counter.txt"]["quote"]), ("105", "151", REPEAT))

        # The custodian keeps the complete history, including the other reader's earlier decision.
        audit = export(dialog, "Export audit CSV", root / "audit.csv")
        self.assertEqual(len(audit), 9)
        reviews = [(event, json.loads(event["details"])) for event in audit if event["action"] == "match_review_note"]
        self.assertEqual([(d["match"]["quote"]["source_name"], d["match"]["quote"]["source_line"],
                           e["actor"], d["disposition"], d["reason"]) for e, d in reviews], DECISIONS)
        for row in supported:
            decision = (row["match_review_target_id"], row["match_review_actor"], row["match_review_time_utc"],
                        row["match_review_disposition"], row["match_review_reason"])
            details, = [d for e, d in reviews
                        if (e["target_id"], e["actor"], e["timestamp_utc"], d["disposition"], d["reason"]) == decision]
            self.assertEqual(details["match"]["source_sha256"], row["source_sha256"])
            self.assertEqual(details["match"]["quote"]["source_start"], int(row["source_start"]))
        press(dialog, "Select all sources")
        all_rows = export(dialog, "Export supported matches CSV", root / "all.csv")
        self.assertCountEqual([r["source_file"] for r in all_rows], SOURCES.keys())
        annex, = [r for r in all_rows if r["source_file"] == "annex.txt"]
        for field in ("speaker", "quote", "match_review_reason", "match_review_target_id", "source_sha256"):
            self.assertNotIn(annex[field], contents, field)

        # Same filename, quote and offsets, different source text elsewhere in the document.
        state = load_project(project)
        docs = tuple(replace(d, text=d.text + "\nYew: We now keep a copy.") if d.name == "counter.txt" else d
                     for d in state.documents)
        revised_text = next(d.text for d in docs if d.name == "counter.txt")
        self.assertEqual(revised_text[105:151], REPEAT)
        self.assertNotEqual(sha256(revised_text.encode()).hexdigest(), by_source["counter.txt"]["source_sha256"])
        variant = root / "changed.tfproj"
        save_project(variant, replace(state, documents=docs))
        changed_dialog = open_audit(app, variant)
        self.assertEqual(changed_dialog.supported_sources.curselection(), ())
        press(changed_dialog, "Select all sources")
        changed = export(changed_dialog, "Export supported matches CSV", root / "changed.csv")
        self.assertCountEqual([r["source_file"] for r in changed], ["studio.txt", "annex.txt"])
        self.assertEqual(export(changed_dialog, "Export audit CSV", root / "changed-audit.csv"), audit)

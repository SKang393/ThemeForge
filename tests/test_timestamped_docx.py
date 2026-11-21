"""Two fictional speakers repeat a sentence under different Word cue headers."""
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from test_evidence_handoff import export, open_audit, press
from themeforge.app import ThemeForgeApp
from themeforge.definition_review import MatchReviewReason

QUOTE = "I returned the brass marker to the tray."
CUES = (("Lark 0:11", "br", 2, 10, 50), ("Finch 1:42", "cr", 4, 62, 102))
TEXT = f"Lark 0:11\n{QUOTE}\nFinch 1:42\n{QUOTE}"
REASONS = ("The first turn describes returning the marker.", "The later turn reports a separate return.")


def write_cues(path):
    paragraphs = "".join(f"<w:p><w:r><w:t>{header}</w:t><w:{line_break}/><w:t>{QUOTE}</w:t></w:r></w:p>"
                         for header, line_break, *_ in CUES)
    with ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                         '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                         '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.'
                         'wordprocessingml.document.main+xml"/></Types>')
        archive.writestr("_rels/.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                         '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
                         'officeDocument" Target="word/document.xml"/></Relationships>')
        archive.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/'
                         f'2006/main"><w:body>{paragraphs}</w:body></w:document>')


class TimestampedDocxTests(unittest.TestCase):
    def test_repeated_cues_keep_their_headers_and_manual_citations(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        source, withheld = root / "workshop.docx", root / "annex.txt"
        write_cues(source)
        withheld.write_text("Gull: I returned the silver marker to the rack.", encoding="utf-8")
        book = root / "codes.csv"
        book.write_text("code,definition\nMarker return,Returning a marker to storage.", encoding="utf-8")
        project = root / "workshop.tfproj"
        app = ThemeForgeApp()
        self.addCleanup(lambda: app.destroy())
        with patch("themeforge.app.filedialog.askopenfilenames", return_value=[str(source), str(withheld)]):
            press(app, "Choose files")
        with patch("themeforge.app.filedialog.askopenfilename", return_value=str(book)):
            press(app, "Choose codebook")
        app.researcher_name.set("Reed")
        press(app, "Analyze")
        generated = {}
        for _ in app.current_quote_matches:
            press(app, "Next quote")
            if app._current_document_name() == source.name:
                widget = app.transcript_widgets[source.name]
                line = widget.index("selected_quote.first")
                generated[line] = (app.quote_meta.get(), widget.get("selected_quote.first", "selected_quote.last"))
        self.assertEqual(set(generated), {"2.0", "4.0"})
        for header, _, line, *_ in CUES:
            citation, highlighted = generated[f"{line}.0"]
            self.assertIn(f"Recorded speaker: {header.split()[0]} | {source.name}: line {line}", citation)
            self.assertEqual(highlighted, QUOTE)

        def assign(name, passage, start_index, reason):
            app.transcript_notebook.select(app.transcript_frames[name])
            app.update()
            widget = app.transcript_widgets[name]
            begin = widget.search(passage, start_index, stopindex="end", exact=True)
            self.assertTrue(begin)
            end = widget.index(f"{begin}+{len(passage)}c")
            widget.tag_remove("sel", "1.0", "end")
            widget.tag_add("sel", begin, end)
            app.definition_box.current(0)
            with patch.object(app, "wait_window", side_effect=lambda d: press(d, "Apply selected definition")):
                press(app, "Apply codebook entry…")

            def support(dialog, _window):
                self.assertEqual(dialog.excerpt, passage)
                dialog.reason.insert(0, reason)
                dialog.ok()

            with patch.object(MatchReviewReason, "wait_window", autospec=True, side_effect=support) as prompt:
                press(app, "Supports this theme")
                prompt.assert_called_once()
            return end

        end = assign(source.name, QUOTE, "1.0", REASONS[0])
        assign(source.name, QUOTE, end, REASONS[1])
        assign(withheld.name, "silver marker", "1.0", "The silver example is outside the handoff.")
        with patch("themeforge.app.filedialog.asksaveasfilename", return_value=str(project)):
            press(app, "Save project")
        original = project.read_bytes()
        history = tuple(app.audit_events)
        app.request_close()
        app = ThemeForgeApp()
        source.unlink()  # Reopening must rely on the saved snapshot.
        audit = open_audit(app, project)
        audit.supported_sources.selection_set(audit.supported_sources.get(0, "end").index(source.name))
        rows = export(audit, "Export supported matches CSV", root / "scoped.csv")
        self.assertEqual(project.read_bytes(), original)
        self.assertEqual(tuple(app.audit_events), history)
        self.assertEqual(len(rows), 2)
        self.assertEqual({r["source_file"] for r in rows}, {source.name})
        self.assertEqual(len({r["match_review_target_id"] for r in rows}), 2)
        tab_text = app.transcript_widgets[source.name].get("1.0", "end-1c")
        self.assertEqual(tab_text, TEXT)
        for row, cue, reason in zip(sorted(rows, key=lambda r: int(r["source_start"])), CUES, REASONS, strict=True):
            header, _, line, start, stop = cue
            self.assertEqual((int(row["source_line"]), int(row["source_start"]), int(row["source_end"]),
                              row["quote"]), (line, start, stop, TEXT[start:stop]))
            self.assertEqual(tab_text.splitlines()[line - 2], header)
            self.assertEqual((row["match_review_actor"], row["match_review_reason"], row["source_location_status"]),
                             ("Reed", reason, "exact_span"))
            self.assertEqual(row["source_sha256"], sha256(TEXT.encode()).hexdigest())
            self.assertEqual(row["speaker"], "Not attributed (manual selection)")

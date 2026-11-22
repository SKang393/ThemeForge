import csv
from dataclasses import replace
from hashlib import sha256
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from themeforge.analysis_types import AnalysisResult, CodebookEntry, Theme, ThemeQuote, TranscriptDocument
from themeforge.audit import create_audit_event
from themeforge.codebook import SharedCodebook
from themeforge.exports import export_quotes_csv
from themeforge.handoff_verification import verify_handoff
from themeforge.match_reviews import match_id, match_snapshot
from themeforge.source_location import TranscriptIndex


class HandoffVerificationTests(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.evidence = self.root / "supported.csv"
        self.book = self.root / "codes.csv"
        self.source = self.root / "받은 사본.txt"
        self.book.write_text("code,definition,example\n@Signal,Verified cue,소리가 났다\n"
                             "Proposal,Future cue,Later\n", encoding="utf-8-sig")
        self.entry = CodebookEntry("@Signal", "Verified cue", ("소리가 났다",))
        passage = "🔔 도장을 남겼다."
        text = f"Vale: {passage}\n미루: {passage}"
        self.source.write_bytes(text.replace("\n", "\r\n").encode("utf-16"))
        doc = TranscriptDocument("기록.txt", text)
        quote = ThemeQuote("Q12", "미루", passage, 0.4, 2, doc.name, source_start=text.rindex(passage),
                           source_end=len(text))
        theme = Theme("C01", "+Cue  inspection", "#888", [], 1, 0.4, [quote],
                      validation={"generated_from_codebook_entry": self.entry.snapshot()})
        reviewed = replace(theme, name="+Cue inspection")
        snapshot = match_snapshot(reviewed, quote, TranscriptIndex([doc]))
        event = create_audit_event(actor="@Wren", action="match_review_note", target_type="theme_quote_match",
                                   target_id=match_id(snapshot), timestamp_utc="2025-03-06T12:00:00Z",
                                   details=json.dumps(dict(match=snapshot, disposition="supported", reason="=heard twice")))
        result = AnalysisResult("1.5.0", 1, 1, [theme], [], [])
        self.exported = export_quotes_csv(result, [doc], [event], supported_only=True, codebook_entries=[self.entry],
                                          shared_codebook=SharedCodebook.from_csv(self.book))
        self.fields = csv.DictReader(io.StringIO(self.exported)).fieldnames
        self.row, = csv.DictReader(io.StringIO(self.exported))
        self.evidence.write_text(self.exported, encoding="utf-8")
        self.files = self.evidence, self.source, self.book

    def write_rows(self, rows):
        with self.evidence.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, self.fields)
            writer.writeheader()
            writer.writerows(rows)

    def test_actual_export_matches_renamed_unicode_source_and_numbered_entry(self):
        before = [p.read_bytes() for p in self.files]
        report = verify_handoff(*self.files)
        for value in (self.source.name, "기록.txt", "line 2", self.row["quote"], "entry 1: @Signal", "'@Wren",
                      "'=heard twice", "'+Cue inspection", self.row["match_review_target_id"],
                      "cannot authenticate", sha256(self.book.read_bytes()).hexdigest()):
            self.assertIn(value, report)
        self.assertIn("Definition: Verified cue", report)
        self.assertIn("Example: 소리가 났다", report)
        self.assertNotIn("Future cue", report)
        self.assertEqual(before, [p.read_bytes() for p in self.files])

    def test_evidence_digest_identifies_changed_decision_wording(self):
        original_digest = sha256(self.evidence.read_bytes()).hexdigest()
        self.assertIn(f"Evidence CSV SHA-256: {original_digest}", verify_handoff(*self.files))
        original_bytes = self.evidence.read_bytes()
        self.evidence.write_bytes(original_bytes.replace(b"heard twice", b"heard once"))
        changed_digest = sha256(self.evidence.read_bytes()).hexdigest()
        report = verify_handoff(*self.files)
        self.assertNotEqual(original_digest, changed_digest)
        self.assertIn(f"Evidence CSV SHA-256: {changed_digest}", report)
        self.assertIn("heard once", report)
        self.assertIn("CSV alone cannot authenticate", report)

    def test_renamed_source_needs_identical_extracted_text(self):
        self.source.write_text("Different revision.\n" + self.row["quote"], encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "source SHA-256"):
            verify_handoff(*self.files)

    def test_similar_codebook_needs_identical_bytes(self):
        self.book.write_text(self.book.read_text(encoding="utf-8-sig").replace("Verified cue", "Proposed cue"),
                             encoding="utf-8-sig")
        with self.assertRaisesRegex(ValueError, "codebook differs"):
            verify_handoff(*self.files)

    def test_proof_requires_exact_citation_and_recorded_decision(self):
        for field, value in (
            ("source_start", "0"), ("source_end", "0"), ("source_line", "1"), ("source_line", "two"),
            ("quote", "different"), ("shared_codebook_entry_number", "0"), ("match_review_disposition", "declined"),
            ("source_location_status", "text_only"), ("match_review_target_id", "unknown"),
            ("match_review_time_utc", "2025-03-06"), ("match_review_reason", " "), ("match_review_actor", ""),
            ("match_review_theme_name", "Other"),
        ):
            with self.subTest(field=field):
                self.write_rows([{**self.row, field: value}])
                with self.assertRaises(ValueError):
                    verify_handoff(*self.files)

    def test_mixed_duplicate_legacy_and_empty_tables_fail_explicitly(self):
        for rows, message in (([self.row, {**self.row, "source_file": "other.txt"}], "multiple recorded"),
                              ([self.row, self.row], "repeats"), ([], "no supported rows")):
            with self.subTest(message=message):
                self.write_rows(rows)
                with self.assertRaisesRegex(ValueError, message):
                    verify_handoff(*self.files)
        for data in ("recorded_theme,recorded_reason\nCue,heard\n",
                     self.exported.replace("source_sha256,", "source_file,"),
                     self.exported + '"unterminated'):
            with self.subTest(header=data[:24]):
                self.evidence.write_text(data, encoding="utf-8")
                with self.assertRaises(ValueError):
                    verify_handoff(*self.files)

    def test_ambiguous_or_ignored_codebook_entries_fail_even_with_matching_digest(self):
        for content in ("code,definition\nCue,yes\nCue,yes\n", "code,include\nCue,Only on a second signal\n"):
            with self.subTest(book=content):
                self.book.write_text(content, encoding="utf-8")
                self.write_rows([{**self.row, "shared_codebook_file_sha256": sha256(self.book.read_bytes()).hexdigest()}])
                with self.assertRaisesRegex(ValueError, "unique codebook entry"):
                    verify_handoff(*self.files)

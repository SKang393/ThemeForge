from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from xml.sax.saxutils import escape
import zipfile

from themeforge.benchmark import StudyInput, prepare_manifest, sha256_text, validate_manifest
from themeforge.benchmark_pilot import prepare_pilot
from themeforge.benchmark_sources import QuoteAligner


STUDY = "Synthetic pilot study"
NS = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def load_text(path):
    return path.read_text(encoding="utf-8")


class PilotPreparationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.transcripts = []
        self.rows = {}
        self.annotations = []
        self.labels = []
        for doc in range(5):
            self.rows[doc] = []
            folder = self.root / f"participant-{doc}"
            folder.mkdir()
            self.annotations.append(folder / "annotation.docx")
            lines = [f"Synthetic document {doc} evidence number {code}." for code in range(18)]
            path = folder / f"transcript-{doc}.txt"
            path.write_text("\n".join(lines), encoding="utf-8")
            self.transcripts.append(path)
            for code in range(6):
                label = f"QA: Literal code {code}"
                self.rows[doc].append((label, lines[code]))
                self.labels.append(label)
            if doc == 4:
                for _ in range(15):
                    self.rows[doc].append(("Heldout only qualifier", lines[9]))
                    self.labels.append("Heldout only qualifier")
        self.rows[0].append(("please check this", "Synthetic document 0 evidence number 8."))
        self.labels.append("please check this")
        self.write_annotations()
        self.inputs = (StudyInput(STUDY, tuple(self.transcripts), tuple(self.annotations), tuple(self.labels)),)
        self.original = prepare_manifest(self.root, self.inputs, load_text)

    def write_annotations(self):
        def cell(text):
            return '<w:tc><w:p><w:r><w:t>' + escape(text) + '</w:t></w:r></w:p></w:tc>'
        for doc, annotation in enumerate(self.annotations):
            table = '<w:tr>' + cell("Code") + cell("Quote") + '</w:tr>'
            table += ''.join('<w:tr>' + cell(label) + cell(quote) + '</w:tr>' for label, quote in self.rows[doc])
            with zipfile.ZipFile(annotation, "w") as archive:
                archive.writestr("word/document.xml", f'<w:document {NS}><w:body><w:tbl>{table}</w:tbl></w:body></w:document>')

    def build(self, **kwargs):
        return prepare_pilot(self.original, self.root, self.inputs, load_text, study_key=STUDY, **kwargs)

    def test_complete_bound_review_packet_and_uncertain_draft(self):
        before = deepcopy(self.original)
        result = self.build()
        draft, packet = result["manifest"], result["packet"]
        self.assertEqual(self.original, before)
        self.assertEqual(len(packet["items"]), 44)
        self.assertEqual(len(packet["catalog"]), 8)
        self.assertEqual(len(draft["units"]), 30)
        self.assertEqual([doc["split"] for doc in draft["documents"]], ["development"] * 3 + ["calibration", "test"])
        self.assertFalse(draft["split_policy"]["held_out_study_required"])
        self.assertFalse(draft["pilot_design"]["approved_by"])
        self.assertEqual(packet["packet_id"], "")
        self.assertEqual(validate_manifest(draft, self.root, load_text).errors, ())
        self.assertFalse(validate_manifest(draft, self.root, load_text).evaluation_ready)
        for item in packet["items"]:
            self.assertEqual(item["kind"], item["binding"]["kind"])
            self.assertIn(item["priority"], {"high", "normal"})
            self.assertIsInstance(item["proposed"], str)
            for field in item["fields"]:
                if field["type"] == "checkbox":
                    self.assertFalse(field["default"])
        for unit in draft["units"]:
            self.assertEqual(len(unit["labels"]), 6)
            self.assertTrue(all(label["decision"] == "uncertain" and not label["reviewed_by"] for label in unit["labels"]))
        for code in draft["codebooks"][0]["codes"]:
            self.assertTrue(code["name"].startswith("QA:"))
            self.assertEqual(code["definition"], "")
        self.assertEqual(result, self.build())

    def test_full_transcript_viewer_offsets_and_hashes_match_manifest(self):
        result = self.build()
        documents = {row["id"]: row for row in result["packet"]["documents"]}
        units = {row["id"]: row for row in result["manifest"]["units"]}
        sources = {row["id"]: row for row in result["manifest"]["sources"]}
        self.assertEqual(len(documents), 5)
        for doc in result["manifest"]["documents"]:
            viewer = documents[doc["id"]]
            self.assertEqual(sha256_text(viewer["text"]), doc["text_sha256"])
            self.assertEqual(viewer["text_sha256"], doc["text_sha256"])
            self.assertEqual(viewer["source_sha256"], sources[doc["source_id"]]["sha256"])
            self.assertTrue(viewer["name"].endswith(".txt"))
        for card in result["packet"]["items"]:
            if card["kind"] != "annotation":
                continue
            unit = units[card["binding"]["unit_id"]]
            self.assertEqual(card["source_span"], {key: unit[key] for key in ("document_id", "source_start", "source_end")})
            text = documents[unit["document_id"]]["text"][unit["source_start"]:unit["source_end"]]
            self.assertEqual(sha256_text(text), unit["span_sha256"])

    def test_code_selection_development_only_and_admin_excluded(self):
        result = self.build()
        selected = set(result["summary"]["selected_label_ids"])
        literal = {row["label_id"]: row["label"] for row in result["packet"]["catalog"]}
        self.assertEqual({literal[lid] for lid in selected}, {f"QA: Literal code {code}" for code in range(6)})
        self.assertEqual(result["summary"]["administrative_exclusions"][0]["label"], "please check this")
        self.assertTrue(all(row["aligned_count"] == 3 for row in result["summary"]["development_selection_counts"].values()))
        self.assertTrue(all(row["document_coverage"] == 3 for row in result["summary"]["development_selection_counts"].values()))

    def test_integrity_failure_stops_extraction(self):
        self.transcripts[0].write_text("tampered", encoding="utf-8")
        with patch("themeforge.benchmark_pilot.read_annotation_records") as extract:
            with self.assertRaisesRegex(ValueError, "integrity validation"):
                self.build()
            extract.assert_not_called()

    def test_catalog_fingerprints_reconstructed(self):
        bad = (StudyInput(STUDY, tuple(self.transcripts), tuple(self.annotations), ("invented label",)),)
        with self.assertRaisesRegex(ValueError, "inventory"):
            prepare_pilot(self.original, self.root, bad, load_text, study_key=STUDY)

    def test_units_exact_and_nonoverlapping_and_enriched(self):
        result = self.build()
        units = result["manifest"]["units"]
        for doc in result["manifest"]["documents"]:
            spans = sorted((unit["source_start"], unit["source_end"]) for unit in units if unit["document_id"] == doc["id"])
            self.assertEqual(len(spans), 6)
            self.assertTrue(all(a[1] <= b[0] for a, b in zip(spans, spans[1:])))
        self.assertEqual(sum(value == "source_positive" for value in result["plan"]["unit_sampling"].values()), 9)
        self.assertFalse(result["summary"]["population_estimate"])

    def test_insufficient_meaningful_background_fails_without_token_fallback(self):
        with patch("themeforge.benchmark_pilot.extract_evidence_units", return_value=[]):
            with self.assertRaisesRegex(ValueError, "Insufficient non-overlapping evidence"):
                self.build()

    def test_heldout_repetition_does_not_change_code_selection_statistics(self):
        baseline = self.build()["summary"]["development_selection_counts"]
        heldout = self.transcripts[4]
        heldout.write_text(load_text(heldout) + "\nSynthetic document 0 evidence number 0.", encoding="utf-8")
        self.original = prepare_manifest(self.root, self.inputs, load_text)
        result = self.build()
        self.assertEqual(result["summary"]["development_selection_counts"], baseline)
        self.assertTrue(all(row["source_association"]["state"] == "proposed_unique" for row in result["alignments"]))

    def test_heldout_annotations_cannot_rank_shared_phrase_or_change_heldout_units(self):
        heldout = self.transcripts[4]
        shared = "Synthetic document 0 evidence number 0."
        heldout.write_text(load_text(heldout) + "\n" + shared, encoding="utf-8")
        self.original = prepare_manifest(self.root, self.inputs, load_text)
        baseline = self.build()
        for _ in range(100):
            self.rows[4].append(("Heldout only qualifier", shared))
            self.labels.append("Heldout only qualifier")
        self.write_annotations()
        self.inputs = (StudyInput(STUDY, tuple(self.transcripts), tuple(self.annotations), tuple(self.labels)),)
        self.original = prepare_manifest(self.root, self.inputs, load_text)
        result = self.build()
        self.assertEqual(result["summary"]["selected_label_ids"], baseline["summary"]["selected_label_ids"])
        self.assertEqual(result["summary"]["development_selection_counts"], baseline["summary"]["development_selection_counts"])
        heldout_ids = {doc["id"] for doc in result["manifest"]["documents"] if doc["split"] != "development"}
        get_spans = lambda build: [(unit["document_id"], unit["source_start"], unit["source_end"])
                                   for unit in build["manifest"]["units"] if unit["document_id"] in heldout_ids]
        self.assertEqual(get_spans(result), get_spans(baseline))
        for item in result["packet"]["items"]:
            if item["kind"] == "annotation" and item["binding"]["document_id"] in heldout_ids:
                self.assertFalse(any("Historical" in row["label"] for row in item["evidence"]))

    def test_code_cards_use_only_development_examples_and_optional_criteria(self):
        result = self.build()
        for item in result["packet"]["items"]:
            if item["kind"] != "code_definition":
                continue
            examples = [row for row in item["evidence"] if row["label"] == "Development source example, not gold"]
            self.assertEqual(len(examples), 2)
            self.assertTrue(all("participant-3" not in row["source"] and "participant-4" not in row["source"] for row in examples))
            self.assertTrue(item["title"].startswith("Review code: QA:"))
            for field in item["fields"]:
                if field["key"] in {"inclusion_criteria", "exclusion_criteria"}:
                    self.assertFalse(field["required"])

    def test_external_definitions_match_literal_only_and_hash(self):
        workbook = self.root / "definitions.xlsx"
        workbook.write_bytes(b"synthetic workbook input")
        module = type("Definitions", (), {"read_workbook_definitions": staticmethod(lambda path: [
            dict(sheet="Codes", row=2, label="QA: Literal code 0", definition="Explicit synthetic definition"),
            dict(sheet="Codes", row=3, label="literal code 1", definition="Must not strip prefix"),
        ])})
        with patch.dict("sys.modules", {"themeforge.benchmark_definitions": module}):
            result = self.build(definition_sources=(workbook,))
        code = next(code for code in result["manifest"]["codebooks"][0]["codes"] if code["name"] == "QA: Literal code 0")
        self.assertEqual(code["definition"], "Explicit synthetic definition")
        other = next(code for code in result["manifest"]["codebooks"][0]["codes"] if code["name"] == "QA: Literal code 1")
        self.assertEqual(other["definition"], "")
        self.assertEqual(len(result["manifest"]["sources"]), len(self.original["sources"]) + 1)

    def test_explicit_consistent_workbook_definition_preferred_for_bounded_selection(self):
        workbook = self.root / "definitions.xlsx"
        workbook.write_bytes(b"synthetic")
        module = type("Definitions", (), {"read_workbook_definitions": staticmethod(lambda path: [
            dict(sheet="Codes", row=2, label="qa:  literal CODE 5", definition="Explicit source-backed synthetic criterion"),
            dict(sheet="Codes", row=3, label="QA: Literal code 0", definition="Conflicting criterion one"),
            dict(sheet="Codes", row=4, label="QA: Literal code 0", definition="Conflicting criterion two"),
        ])})
        baseline = self.build(max_codes=1)
        self.assertEqual(baseline["manifest"]["codebooks"][0]["codes"][0]["name"], "QA: Literal code 0")
        with patch.dict("sys.modules", {"themeforge.benchmark_definitions": module}):
            result = self.build(max_codes=1, definition_sources=(workbook,))
        self.assertEqual(result["manifest"]["codebooks"][0]["codes"][0]["name"], "QA: Literal code 5")
        self.assertIn("consistent explicit workbook definition", result["plan"]["selection_rule"])
        unselected = next(row for row in result["packet"]["catalog"] if row["label"] == "QA: Literal code 0")
        self.assertIn("definition review needed", unselected["note"])


class ReusableAlignmentTests(unittest.TestCase):
    def test_reuses_normalization_and_snapshots_documents(self):
        import themeforge.benchmark_sources as source_module
        source = {"doc": "a\tword"}
        with patch.object(source_module, "_normalized", wraps=source_module._normalized) as normalize:
            aligner = QuoteAligner(source)
            source["doc"] = "changed"
            self.assertEqual(aligner.align("a word")["status"], "unique_whitespace")
            self.assertEqual(aligner.align("word")["status"], "unique_exact")
            self.assertEqual(sum(call.args[0] == "a\tword" for call in normalize.call_args_list), 1)


if __name__ == "__main__":
    unittest.main()

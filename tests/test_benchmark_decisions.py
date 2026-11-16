"""Synthetic explicit-review and source-integrity contract checks."""

import copy
from pathlib import Path
import tempfile
import unittest

from themeforge.benchmark import StudyInput, prepare_manifest, sha256_text
from themeforge.benchmark_decisions import apply_review, finalize_packet


def load_text(path):
    return path.read_text(encoding="utf-8")


class BenchmarkDecisionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.paths = []
        for index, text in enumerate(("Synthetic support helped attendance.", "An invented participant described a barrier.", "A fictional meeting provided assistance.")):
            path = self.root / f"synthetic-{index}.txt"
            path.write_text(text, encoding="utf-8")
            self.paths.append(path)
        self.source_bytes = {path: path.read_bytes() for path in self.paths}
        self.manifest = prepare_manifest(self.root, [StudyInput("synthetic-study", tuple(self.paths), labels=("Support",))], load_text)
        self.manifest["split_policy"] = {"group_by": "session", "held_out_study_required": False}
        self.manifest["pilot_design"] = {"approved_by": "", "scope": "Synthetic three-document pilot"}
        self.manifest["unitization"] = {"name": "exact-whole-synthetic-document", "version": "1", "reviewed_by": ""}
        book = self.manifest["codebooks"][0]
        self.code_id = book["codes"][0]["id"]
        self.code_key = "code_" + self.code_id
        for index, (document, path) in enumerate(zip(self.manifest["documents"], self.paths)):
            text = load_text(path)
            document["expected_unit_count"] = 1
            self.manifest["units"].append({"id": f"unit-{index}", "document_id": document["id"], "source_start": 0, "source_end": len(text), "span_sha256": sha256_text(text), "labels": [{"code_id": self.code_id, "decision": "uncertain", "reviewed_by": ""}]})
        self.items = []

        def field(key, value, options=None):
            kind = "checkbox" if isinstance(value, bool) else "select" if options else "text"
            result = {"key": key, "label": key, "type": kind, "default": value}
            if options:
                result["options"] = [{"value": option, "label": option} for option in options]
            return result

        def card(item_id, binding, values, choices=None):
            self.items.append({"id": item_id, "binding": binding, "fields": [field(key, value, (choices or {}).get(key)) for key, value in values.items()]})

        card("design", {"kind": "pilot_design"}, {"approve": True})
        card("unitization", {"kind": "unitization"}, {"approve": True})
        card("permission", {"kind": "study_permission", "study_id": self.manifest["studies"][0]["id"]}, {"evaluation_permission": "allowed", "training_permission": "denied", "deidentified": True}, {"evaluation_permission": ["unknown", "allowed", "denied"], "training_permission": ["unknown", "allowed", "denied"]})
        card("code", {"kind": "code_definition", "codebook_id": book["id"], "code_id": self.code_id, "label_id": book["label_aliases"][0]["label_id"]}, {"confirmed": True, "name": "Support", "definition": "Explicit descriptions of assistance.", "inclusion_criteria": "Direct help", "exclusion_criteria": "Barriers alone"})
        for index, (document, split) in enumerate(zip(self.manifest["documents"], ("development", "calibration", "test"))):
            card(f"group-{index}", {"kind": "document_group", "document_id": document["id"]}, {"confirmed": True, "session_id": f"session-{index}", "participant_ids": f"participant-{index}", "split": split}, {"split": ["development", "calibration", "test"]})
            card(f"annotation-{index}", {"kind": "annotation", "document_id": document["id"], "unit_id": f"unit-{index}"}, {"span_confirmed": True, self.code_key: "present" if index != 1 else "absent"}, {self.code_key: ["present", "absent", "uncertain"]})
        self.packet = finalize_packet({"title": "Synthetic pilot", "items": self.items}, self.manifest)
        self.export = {"format_version": 1, "packet_id": self.packet["packet_id"], "reviewer_id": "session-default", "decisions": [{"item_id": item["id"], "action": "accept", "reviewer_id": "reviewer-" + item["id"], "values": {f["key"]: f["default"] for f in item["fields"]}, "note": "Explicit synthetic review", "reviewed_at": "2026-01-01T11:00:00+00:00" if item["id"].startswith("annotation") else "2026-01-01T10:00:00Z"} for item in self.items]}
        self.before_manifest = copy.deepcopy(self.manifest)
        self.before_packet = copy.deepcopy(self.packet)
        self.before_export = copy.deepcopy(self.export)

    def decision(self, item_id, export=None):
        return next(row for row in (export or self.export)["decisions"] if row["item_id"] == item_id)

    def apply(self, export=None, manifest=None, packet=None):
        return apply_review(manifest if manifest is not None else self.manifest, packet if packet is not None else self.packet, export if export is not None else self.export, self.root, load_text)

    def test_explicit_review_readies_evaluation_without_training_or_mutation(self):
        result = self.apply()
        self.assertTrue(result["report"]["evaluation_ready"], result["report"])
        self.assertFalse(result["report"]["training_ready"])
        self.assertEqual(result["report"]["status"], "ready")
        self.assertEqual(result["report"]["errors"], [])
        self.assertTrue(result["report"]["training_blockers"])
        self.assertEqual(result["summary"]["pending_items"], 0)
        self.assertEqual(self.manifest, self.before_manifest)
        self.assertEqual(self.packet, self.before_packet)
        self.assertEqual(self.export, self.before_export)
        self.assertEqual({path: path.read_bytes() for path in self.paths}, self.source_bytes)

    def test_empty_export_does_not_infer_negative_or_approve_defaults(self):
        export = copy.deepcopy(self.export)
        export["decisions"] = []
        result = self.apply(export)
        self.assertFalse(result["report"]["evaluation_ready"])
        self.assertEqual(result["summary"]["pending_items"], len(self.items))
        self.assertEqual(result["summary"]["accepted_items"], 0)
        self.assertTrue(all(label["decision"] == "uncertain" and not label["reviewed_by"] for unit in result["manifest"]["units"] for label in unit["labels"]))
        self.assertFalse(result["manifest"]["pilot_design"]["approved_by"])

    def test_exact_per_decision_attribution_survives_session_default(self):
        manifest = self.apply()["manifest"]
        self.assertEqual(manifest["pilot_design"]["approved_by"], "reviewer-design")
        self.assertEqual(manifest["unitization"]["reviewed_by"], "reviewer-unitization")
        self.assertEqual(manifest["studies"][0]["permission_reviewed_by"], "reviewer-permission")
        self.assertEqual(manifest["codebooks"][0]["codes"][0]["reviewed_by"], "reviewer-code")
        for index, (document, unit) in enumerate(zip(manifest["documents"], manifest["units"])):
            self.assertEqual(document["group_reviewed_by"], f"reviewer-group-{index}")
            self.assertEqual(unit["labels"][0]["reviewed_by"], f"reviewer-annotation-{index}")
        self.assertNotIn("session-default", {row["id"] for row in manifest["annotators"]})
        self.assertEqual(manifest["review_history"][0]["decisions"], self.export["decisions"])

    def test_missing_or_extra_code_fields_rejected(self):
        for extra in (False, True):
            with self.subTest(extra=extra):
                export = copy.deepcopy(self.export)
                values = self.decision("annotation-0", export)["values"]
                if extra:
                    values["code_unknown"] = "absent"
                else:
                    del values[self.code_key]
                with self.assertRaisesRegex(ValueError, "exactly the displayed fields"):
                    self.apply(export)
        self.assertEqual(self.manifest, self.before_manifest)

    def test_unknown_and_duplicate_item_ids_rejected(self):
        for duplicate in (False, True):
            export = copy.deepcopy(self.export)
            if duplicate:
                export["decisions"].append(copy.deepcopy(export["decisions"][0]))
            else:
                export["decisions"][0]["item_id"] = "unknown-item"
            with self.subTest(duplicate=duplicate), self.assertRaisesRegex(ValueError, "unknown|duplicate"):
                self.apply(export)

    def test_wrong_packet_and_tampered_manifest_or_packet_rejected(self):
        export = copy.deepcopy(self.export)
        export["packet_id"] = "another-packet"
        with self.assertRaisesRegex(ValueError, "another packet"):
            self.apply(export)
        manifest = copy.deepcopy(self.manifest)
        manifest["purpose"] = "tampered"
        with self.assertRaisesRegex(ValueError, "Packet or draft changed"):
            self.apply(manifest=manifest)
        packet = copy.deepcopy(self.packet)
        packet["title"] = "Tampered review scope"
        with self.assertRaisesRegex(ValueError, "Packet or draft changed"):
            self.apply(packet=packet)

    def test_changed_source_is_refused_before_review_application(self):
        self.paths[0].write_text("Changed synthetic source", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Draft/source integrity failed:.*SHA256 mismatch"):
            self.apply()
        self.assertEqual(self.manifest, self.before_manifest)

    def test_explicit_uncertain_decision_blocks_readiness(self):
        export = copy.deepcopy(self.export)
        self.decision("annotation-0", export)["values"][self.code_key] = "uncertain"
        result = self.apply(export)
        self.assertFalse(result["report"]["evaluation_ready"])
        self.assertEqual(result["manifest"]["units"][0]["labels"][0]["decision"], "uncertain")
        self.assertTrue(any("unresolved code decision" in text for text in result["report"]["review_required"]))

    def test_later_changed_definition_invalidates_earlier_annotations(self):
        export = copy.deepcopy(self.export)
        code = self.decision("code", export)
        code["values"]["definition"] = "A newly narrowed assistance definition."
        code["reviewed_at"] = "2026-01-01T12:00:00Z"
        result = self.apply(export)
        self.assertFalse(result["report"]["evaluation_ready"])
        self.assertEqual(result["summary"]["stale_annotations"], 3)
        self.assertTrue(all(not unit["labels"][0]["reviewed_by"] for unit in result["manifest"]["units"]))
        self.assertTrue(any("again after the code-definition change" in text for text in result["report"]["review_required"]))

    def test_changed_definition_approved_before_labels_succeeds(self):
        export = copy.deepcopy(self.export)
        self.decision("code", export)["values"]["definition"] = "A narrowed assistance definition reviewed before labels."
        result = self.apply(export)
        self.assertTrue(result["report"]["evaluation_ready"], result["report"])
        self.assertEqual(result["summary"]["stale_annotations"], 0)

    def test_deferred_annotation_stays_unresolved(self):
        export = copy.deepcopy(self.export)
        self.decision("annotation-0", export)["action"] = "defer"
        result = self.apply(export)
        self.assertFalse(result["report"]["evaluation_ready"])
        self.assertEqual(result["summary"]["deferred_items"], 1)
        label = result["manifest"]["units"][0]["labels"][0]
        self.assertEqual(label["decision"], "uncertain")
        self.assertFalse(label["reviewed_by"])
        self.assertEqual(result["manifest"]["documents"][0]["annotation_status"], "partial")

    def test_session_and_participant_leakage_are_invalid(self):
        for key in ("session_id", "participant_ids"):
            with self.subTest(group=key):
                export = copy.deepcopy(self.export)
                self.decision("group-1", export)["values"][key] = self.decision("group-0", export)["values"][key]
                result = self.apply(export)
                self.assertEqual(result["report"]["status"], "invalid")
                self.assertFalse(result["report"]["evaluation_ready"])
                self.assertTrue(any("leakage across splits" in error for error in result["report"]["errors"]))

    def test_same_source_bound_to_two_split_groups_is_invalid(self):
        manifest = copy.deepcopy(self.manifest)
        first, second = manifest["documents"][:2]
        second["source_id"] = first["source_id"]
        second["text_sha256"] = first["text_sha256"]
        manifest["units"][1]["source_end"] = manifest["units"][0]["source_end"]
        manifest["units"][1]["span_sha256"] = manifest["units"][0]["span_sha256"]
        packet = finalize_packet(self.packet, manifest)
        export = copy.deepcopy(self.export)
        export["packet_id"] = packet["packet_id"]
        result = self.apply(export, manifest, packet)
        self.assertEqual(result["report"]["status"], "invalid")
        self.assertFalse(result["report"]["evaluation_ready"])
        self.assertTrue(any("source_hash" in error and "leakage across splits" in error for error in result["report"]["errors"]))
        self.assertEqual({path: path.read_bytes() for path in self.paths}, self.source_bytes)

    def test_each_decision_requires_valid_reviewer_and_zoned_timestamp(self):
        for field, value in (("reviewer_id", None), ("reviewer_id", "invalid reviewer"), ("reviewer_id", "_invalid"), ("reviewer_id", "x" * 65), ("reviewed_at", None), ("reviewed_at", "2026-01-01T10:00:00"), ("reviewed_at", "invalid")):
            with self.subTest(field=field, value=value):
                export = copy.deepcopy(self.export)
                self.decision("annotation-0", export)[field] = value
                with self.assertRaises(ValueError):
                    self.apply(export)


if __name__ == "__main__":
    unittest.main()

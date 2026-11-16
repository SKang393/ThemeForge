"""Fictional source-contract tests. No model, download, or study labels used."""
from copy import deepcopy
import json
import unittest

from themeforge.analysis_types import TranscriptDocument
from themeforge.discovery_units import build_meaning_units
from themeforge.generation_schema import (
    MAX_RESPONSE_BYTES, VERSION, build_span_registry, candidate_json_schema, validate_candidate_response,
)


class GenerationSchemaTests(unittest.TestCase):
    def setUp(self):
        self.documents = [TranscriptDocument("unicode.txt", (
            "Moderator: 🙂 Did the journey become easier?\n"
            "P1: Yo no pude subir al ferry. Mi hermana podría ayudarme.\n"
            "P2: 저는 가족 없이 결정하지 않았어요. 가족과 함께 결정했어요.\n"
            "P3: I could travel alone, but I did not feel safe.\n"
            "P4: Yes."
        ))]
        self.units = build_meaning_units(self.documents)
        self.registry = build_span_registry(self.units, self.documents)
        self.expected = [unit["id"] for unit in self.units if unit["eligible"]]

    def span_for(self, uid):
        return next(sid for sid, entry in self.registry.items() if entry["unit_id"] == uid)

    def response(self):
        return {"version": VERSION, "codes": [{
            "id": "local-1", "label": "Assistance during travel", "definition": "Assistance may shape travel decisions.",
            "inclusion": "Travel decisions made with assistance.", "exclusion": "Unrelated choices.",
            "evidence_span_ids": [self.span_for(self.expected[0])], "qualification_span_ids": [],
            "counterexample_span_ids": [], "uncertainty": "Interpretation requires source and researcher review.",
        }], "no_code": [{"unit_id": uid, "reason": "No proposed code in this batch."} for uid in self.expected[1:]]}

    def test_registry_preserves_unicode_negation_full_turn_and_separate_context(self):
        source = self.documents[0].text
        self.assertEqual(len(self.registry), 5)
        self.assertEqual({entry["unit_id"] for entry in self.registry.values()}, set(self.expected))
        for entry in self.registry.values():
            self.assertEqual(source[entry["source_start"]:entry["source_end"]], entry["text"])
            self.assertEqual(source[entry["unit_source_start"]:entry["unit_source_end"]], entry["unit_text"])
            self.assertEqual(source[entry["context_start"]:entry["context_end"]], entry["context_text"])
            self.assertEqual(entry["reference_context"], "🙂 Did the journey become easier?")
            self.assertNotIn("Did the journey", entry["text"])
        resolved = validate_candidate_response(json.dumps(self.response(), ensure_ascii=False), self.registry, self.expected)
        evidence = resolved["codes"][0]["evidence"][0]
        self.assertEqual(evidence["text"], "Yo no pude subir al ferry.")
        self.assertIn("Mi hermana podría ayudarme.", evidence["unit_text"])
        self.assertEqual(resolved["review_status"], "unreviewed_machine_draft")
        self.assertEqual(resolved["grounding_status"], "source_spans_verified_not_semantic_fit")
        self.assertEqual(resolved["codes"][0]["review_status"], "unreviewed_machine_draft")

    def test_registry_ids_are_stable_across_order_and_distinguish_repeated_text(self):
        docs = [TranscriptDocument("a", "P1: I refused the fare.\nP2: I refused the fare."), TranscriptDocument("b", "P1: I refused the fare.")]
        units = build_meaning_units(docs)
        first = build_span_registry(units, docs)
        second = build_span_registry(list(reversed(units)), list(reversed(docs)))
        self.assertEqual(first, second)
        self.assertEqual(len(first), 3)

    def test_missing_sentence_list_falls_back_to_complete_turn(self):
        unit = deepcopy(next(unit for unit in self.units if unit["eligible"]))
        del unit["sentences"]
        registry = build_span_registry([unit], self.documents)
        self.assertEqual(len(registry), 1)
        self.assertEqual(next(iter(registry.values()))["text"], unit["text"])

    def test_stale_source_version_rejected_even_when_target_text_unchanged(self):
        documents = [TranscriptDocument(self.documents[0].name, self.documents[0].text + "\n")]
        with self.assertRaisesRegex(ValueError, "Stale source hash"):
            build_span_registry(self.units, documents)

    def test_duplicate_documents_units_and_ranges_rejected(self):
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            build_span_registry(self.units, self.documents * 2)
        with self.assertRaisesRegex(ValueError, "Duplicate unit"):
            build_span_registry([self.units[1], self.units[1]], self.documents)
        duplicate = deepcopy(self.units[1]); duplicate["id"] = "different-id"
        with self.assertRaisesRegex(ValueError, "Duplicate source unit range"):
            build_span_registry([self.units[1], duplicate], self.documents)

    def test_forged_interviewer_eligibility_does_not_create_citable_ids(self):
        interviewer = deepcopy(self.units[0]); interviewer["eligible"] = True; interviewer["flags"] = []
        self.assertEqual(build_span_registry([interviewer], self.documents), {})
        interviewer["speaker"] = "P1"
        with self.assertRaisesRegex(ValueError, "verified source turn"):
            build_span_registry([interviewer], self.documents)

    def test_context_is_not_a_sentence_selection_or_target_unit(self):
        unit = deepcopy(self.units[1])
        unit["sentences"] = [{"text": self.units[0]["text"], "source_start": self.units[0]["source_start"], "source_end": self.units[0]["source_end"]}]
        with self.assertRaisesRegex(ValueError, "outside its target turn"):
            build_span_registry([unit], self.documents)
        unit = deepcopy(self.units[1]); unit["source_start"] = unit["context_start"]; unit["source_end"] = unit["context_end"]; unit["text"] = unit["context_text"]
        with self.assertRaisesRegex(ValueError, "verified source turn"):
            build_span_registry([unit], self.documents)

    def test_duplicate_sentence_ranges_bad_offsets_and_context_rejected(self):
        for mutate in (
            lambda unit: unit["sentences"].append(unit["sentences"][0]),
            lambda unit: unit["sentences"][0].update(source_start=True),
            lambda unit: unit.update(context_text="Invented context"),
        ):
            unit = deepcopy(self.units[1]); mutate(unit)
            with self.assertRaises(ValueError):
                build_span_registry([unit], self.documents)

    def test_schema_bounds_and_closed_objects(self):
        schema = candidate_json_schema(list(self.registry), self.expected)
        self.assertFalse(schema["additionalProperties"])
        code = schema["properties"]["codes"]
        self.assertEqual(code["maxItems"], 3)
        self.assertFalse(code["items"]["additionalProperties"])
        self.assertEqual(set(code["items"]["properties"]["evidence_span_ids"]["items"]["enum"]), set(self.registry))
        self.assertNotIn("quotes", code["items"]["properties"])
        self.assertNotIn("source_start", code["items"]["properties"])
        empty = candidate_json_schema([], [])
        self.assertEqual(empty["properties"]["no_code"]["maxItems"], 0)

    def test_schema_and_response_reject_bool_cap_duplicate_ids(self):
        with self.assertRaises(ValueError): candidate_json_schema([], [], max_codes=True)
        with self.assertRaises(ValueError): candidate_json_schema(["same", "same"], [])
        with self.assertRaises(ValueError): validate_candidate_response(self.response(), self.registry, self.expected, max_codes=False)

    def test_dict_input_is_not_mutated_and_resolved_records_are_copied(self):
        raw = self.response(); before = deepcopy(raw)
        resolved = validate_candidate_response(raw, self.registry, self.expected)
        self.assertEqual(raw, before)
        resolved["codes"][0]["evidence"][0]["text"] = "Changed later"
        self.assertNotEqual(next(iter(self.registry.values()))["text"], "Changed later")

    def test_qualification_or_countercase_does_not_replace_assignment_accountability(self):
        for role in ("qualification_span_ids", "counterexample_span_ids"):
            raw = self.response(); raw["codes"][0][role] = [self.span_for(self.expected[1])]
            raw["no_code"] = raw["no_code"][1:]
            with self.assertRaisesRegex(ValueError, "Every expected unit"):
                validate_candidate_response(raw, self.registry, self.expected)
            raw["no_code"].append({"unit_id": self.expected[1], "reason": "Qualification retained, no assignment proposed."})
            self.assertTrue(validate_candidate_response(raw, self.registry, self.expected)["codes"])

    def test_same_span_cannot_occupy_opposing_roles(self):
        raw = self.response(); raw["codes"][0]["counterexample_span_ids"] = raw["codes"][0]["evidence_span_ids"][:]
        with self.assertRaisesRegex(ValueError, "multiple roles"):
            validate_candidate_response(raw, self.registry, self.expected)

    def test_missing_evidence_duplicate_references_or_duplicate_codes_rejected(self):
        for mutate in (
            lambda raw: raw["codes"][0].update(evidence_span_ids=[]),
            lambda raw: raw["codes"][0]["evidence_span_ids"].extend(raw["codes"][0]["evidence_span_ids"]),
            lambda raw: raw["codes"].append(deepcopy(raw["codes"][0])),
        ):
            raw = self.response(); mutate(raw)
            with self.assertRaises(ValueError): validate_candidate_response(raw, self.registry, self.expected)

    def test_unknown_and_out_of_batch_span_ids_rejected(self):
        raw = self.response(); raw["codes"][0]["evidence_span_ids"] = ["SP-invented"]
        with self.assertRaisesRegex(ValueError, "out-of-batch"):
            validate_candidate_response(raw, self.registry, self.expected)
        raw = self.response(); raw["codes"][0]["qualification_span_ids"] = [self.span_for(self.expected[1])]; raw["no_code"] = []
        with self.assertRaisesRegex(ValueError, "out-of-batch"):
            validate_candidate_response(raw, self.registry, self.expected[:1])

    def test_missing_duplicate_unknown_and_conflicting_no_code_rejected(self):
        for items in ([], [{"unit_id": "invented", "reason": "Unknown"}],
                      [{"unit_id": self.expected[0], "reason": "Already assigned"}]):
            raw = self.response(); raw["no_code"] = items
            with self.assertRaises(ValueError): validate_candidate_response(raw, self.registry, self.expected)
        raw = self.response(); raw["no_code"][1] = deepcopy(raw["no_code"][0])
        with self.assertRaisesRegex(ValueError, "duplicate no_code"):
            validate_candidate_response(raw, self.registry, self.expected)

    def test_all_no_code_and_empty_batch_are_valid_without_forced_code(self):
        raw = {"version": VERSION, "codes": [], "no_code": [{"unit_id": uid, "reason": "Insufficient context to interpret."} for uid in self.expected]}
        self.assertFalse(validate_candidate_response(raw, self.registry, self.expected)["codes"])
        self.assertEqual(validate_candidate_response({"version": VERSION, "codes": [], "no_code": []}, {}, [])["codes"], [])
        with self.assertRaisesRegex(ValueError, "verified registry"):
            validate_candidate_response(raw, {}, self.expected)

    def test_extra_approval_training_quotes_and_groundtruth_keys_rejected(self):
        for key in ("approved", "training", "ground_truth", "quotes", "source_start"):
            for at_code in (False, True):
                raw = self.response(); (raw["codes"][0] if at_code else raw)[key] = "not allowed"
                with self.assertRaises(ValueError): validate_candidate_response(raw, self.registry, self.expected)

    def test_wrong_versions_malformed_truncated_and_duplicate_json_keys_rejected(self):
        for raw in ('{"version":', '```json\n{}\n```', '{"version":"one","version":"two","codes":[],"no_code":[]}', '[]'):
            with self.assertRaises(ValueError): validate_candidate_response(raw, self.registry, self.expected)
        raw = self.response(); raw["version"] = "future-v2"
        with self.assertRaisesRegex(ValueError, "version"):
            validate_candidate_response(raw, self.registry, self.expected)
        nested = json.dumps(self.response()).replace('"local-1"', '"local-1", "id":"other"')
        with self.assertRaisesRegex(ValueError, "Duplicate JSON key"):
            validate_candidate_response(nested, self.registry, self.expected)

    def test_boolean_nan_null_and_wrong_field_types_rejected(self):
        for value in (True, False, float("nan"), float("inf"), 1, None, ["Claim"]):
            raw = self.response(); raw["codes"][0]["definition"] = value
            with self.assertRaises(ValueError): validate_candidate_response(raw, self.registry, self.expected)
        with self.assertRaisesRegex(ValueError, "Nonfinite"):
            validate_candidate_response(json.dumps(self.response()).replace('"local-1"', 'NaN'), self.registry, self.expected)

    def test_oversized_output_fields_and_excess_code_count_rejected(self):
        with self.assertRaisesRegex(ValueError, "256 KiB"):
            validate_candidate_response(" " * (MAX_RESPONSE_BYTES + 1), self.registry, self.expected)
        raw = self.response(); raw["codes"][0]["label"] = "x" * 241
        with self.assertRaises(ValueError): validate_candidate_response(raw, self.registry, self.expected)
        raw = self.response(); raw["codes"] *= 4
        with self.assertRaisesRegex(ValueError, "max_codes"):
            validate_candidate_response(raw, self.registry, self.expected)

    def test_tampered_registry_span_is_rejected(self):
        registry = deepcopy(self.registry)
        registry[self.span_for(self.expected[0])]["text"] = "A newly invented quote."
        with self.assertRaisesRegex(ValueError, "no longer resolves"):
            validate_candidate_response(self.response(), registry, self.expected)
        with self.assertRaises(ValueError):
            validate_candidate_response(self.response(), {"broken": {}}, self.expected)


if __name__ == "__main__":
    unittest.main()

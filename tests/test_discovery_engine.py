import json
import unittest

from themeforge.discovery_engine import build_candidate_codebook


def unit(identifier, text, *, speaker="Participant", source="interview.txt", start=100, eligible=True, flags=()):
    return {"id": identifier, "source_name": source, "speaker": speaker, "start": start, "end": start + len(text), "hash": "retained-source-hash", "text": text, "context_text": text, "reference_context": [], "eligible": eligible, "flags": list(flags), "relation_cues": [], "sentences": [{"text": text, "source_start": start, "source_end": start + len(text)}]}


class DiscoveryEngineTests(unittest.TestCase):
    def test_negation_and_condition_remain_in_complete_exact_anchor(self):
        text = "I cannot trust the alarm because it sometimes fails during storms."
        result = build_candidate_codebook([unit("u1", text)])
        code = result["codes"][0]
        self.assertEqual(code["label"], text)
        self.assertEqual(code["anchor_text"], text)
        self.assertEqual((code["anchor_source_start"], code["anchor_source_end"]), (100, 100 + len(text)))
        self.assertEqual(code["definition_kind"], "extractive_anchor")
        self.assertIn("Extractive proposal", code["definition"])
        self.assertEqual(result["interpretive_themes"], [])
        json.dumps(result)

    def test_interviewer_questions_and_question_shaped_participant_text_cannot_seed(self):
        units = [unit("q1", "Do you feel the platform improved your confidence?", speaker="Interviewer"), unit("q2", "Could the platform improve our confidence?"), unit("p1", "The platform reduced the time I spent checking records.")]
        result = build_candidate_codebook(units)
        self.assertEqual([code["anchor_unit_id"] for code in result["codes"]], ["p1"])
        self.assertIn("q1", result["flagged_unit_ids"])
        self.assertEqual(result["unassigned_unit_ids"], ["q1", "q2"])

    def test_labels_are_assertions_across_domains_not_bags_of_words(self):
        units = [unit("health", "I delayed my appointment because the bus stopped running."), unit("farm", "The irrigation timer saved water when the soil remained wet."), unit("music", "I practice slowly because speed makes the difficult passages uneven.")]
        result = build_candidate_codebook(units, max_candidates=12)
        self.assertEqual(len(result["codes"]), 3)
        self.assertEqual({code["label"] for code in result["codes"]}, {item["text"] for item in units})
        self.assertEqual(result["diagnostics"]["candidate_cap"], 12)

    def test_filler_fragments_never_become_labels(self):
        result = build_candidate_codebook([unit("f1", "Able count let's."), unit("f2", "Yes okay well actually."), unit("p", "I repair the bicycle before riding to work.")])
        self.assertEqual([code["anchor_unit_id"] for code in result["codes"]], ["p"])

    def test_context_changes_actual_anchor_selection_and_keeps_out_of_focus(self):
        units = [unit("bus", "I missed my appointment because the bus route closed."), unit("river", "The river floods our garden after heavy rainfall.")]
        guided = build_candidate_codebook(units, mode="context_guided", research_questions=("How do bus routes affect appointments?",))
        self.assertEqual([code["anchor_unit_id"] for code in guided["codes"]], ["bus"])
        self.assertIn("river", guided["unassigned_unit_ids"])
        self.assertGreater(guided["codes"][0]["context_relevance"], 0)
        self.assertIn("bus", guided["codes"][0]["context_guidance"]["matched_terms"])
        other = build_candidate_codebook(units, mode="guided", theoretical_lens="river rainfall")
        self.assertEqual([code["anchor_unit_id"] for code in other["codes"]], ["river"])

    def test_open_mode_ignores_all_supplied_context(self):
        units = [unit("bus", "I missed my appointment because the bus route closed."), unit("river", "The river floods our garden after heavy rainfall.")]
        plain = build_candidate_codebook(units)
        context = build_candidate_codebook(units, research_questions=("river",), theoretical_lens="rainfall")
        self.assertEqual(plain, context)
        self.assertTrue(any("ignores" in note for note in context["notes"]))

    def test_guidance_cannot_leak_from_interviewer_context(self):
        u = unit("u", "I prefer working quietly in the morning.")
        u["context_text"] = "Interviewer: How does diabetes affect your health? " + u["text"]
        result = build_candidate_codebook([u], mode="guided", research_questions=("diabetes health",))
        self.assertEqual(result["codes"], [])
        self.assertEqual(result["unassigned_unit_ids"], ["u"])

    def test_overlap_rare_cases_and_cap_preserve_access(self):
        first = "I trust the sensor because the readings remain stable."
        second = "I adjust the valve because the pressure remains high."
        both = unit("both", first + " " + second)
        both["sentences"] = [{"text": first, "source_start": 100, "source_end": 100 + len(first)}, {"text": second, "source_start": 101 + len(first), "source_end": 101 + len(first) + len(second)}]
        units = [both, unit("sensor", first, speaker="P2"), unit("valve", second, source="another.txt"), unit("rare", "I stopped rehearsing because the abandoned hall frightened me.")]
        result = build_candidate_codebook(units)
        self.assertGreaterEqual(sum("both" in code["evidence_unit_ids"] for code in result["codes"]), 2)
        self.assertTrue(any("rare" in code["evidence_unit_ids"] for code in result["codes"]))
        capped = build_candidate_codebook(units, max_candidates=1)
        self.assertLessEqual(len(capped["codes"]), 1)
        self.assertTrue(capped["unassigned_unit_ids"])
        self.assertTrue(any(relation["kind"] == "possible_split" for relation in result["relations"]))

    def test_contrast_and_support_are_disjoint_and_uncertain(self):
        units = [unit("yes", "I trust the sensor because the readings remain stable."), unit("no", "I do not trust the sensor because the readings remain stable."), unit("modal", "I might trust the sensor when the readings remain stable.")]
        result = build_candidate_codebook(units)
        self.assertGreater(len(result["codes"]), 1)
        for code in result["codes"]:
            self.assertFalse(set(code["evidence_unit_ids"]) & set(code["counterexample_unit_ids"]))
            self.assertTrue(code["counterexample_unit_ids"])
            self.assertTrue(any("does not establish contradiction" in note for note in code["uncertainty"]))
        self.assertTrue(any(item["kind"] == "possible_qualification" for item in result["relations"]))

    def test_unrelated_negatives_are_not_counterexamples(self):
        units = [unit("a", "I do not trust the sensor during heavy rainfall."), unit("b", "I never enjoyed practicing music in crowded rooms.")]
        result = build_candidate_codebook(units)
        self.assertTrue(all(not code["counterexample_unit_ids"] for code in result["codes"]))

    def test_input_order_and_stable_identifiers_and_original_references(self):
        units = [unit("a", "The clinic changed my appointment because the doctor was away.", source="a.txt", speaker="P1"), unit("b", "The clinic changed the appointment because the doctor was away.", source="b.txt", speaker="P2"), unit("c", "I secure the greenhouse when the strong wind arrives.")]
        snapshot = json.dumps(units, sort_keys=True)
        normal = build_candidate_codebook(units)
        reversed_result = build_candidate_codebook(list(reversed(units)))
        self.assertEqual(normal, reversed_result)
        self.assertEqual(json.dumps(units, sort_keys=True), snapshot)
        original = {item["id"]: item for item in units}
        for code in normal["codes"]:
            u = original[code["anchor_unit_id"]]
            self.assertEqual(u["text"][code["anchor_source_start"] - u["start"]:code["anchor_source_end"] - u["start"]], code["anchor_text"])
            self.assertTrue(set(code["evidence_unit_ids"]) <= set(original))

    def test_missing_or_noncontiguous_spans_abstain(self):
        u = unit("a", "I travel by train because it saves time.")
        u["sentences"][0]["text"] = "I travel because it saves time."
        self.assertEqual(build_candidate_codebook([u])["codes"], [])
        u["sentences"] = [{"text": u["text"], "source_start": None, "source_end": None}]
        self.assertEqual(build_candidate_codebook([u])["codes"], [])

    def test_optional_vector_provider_and_empty_input(self):
        texts = []
        def provider(items):
            texts.extend(items)
            return [{"local-dimension": 1.0} for _ in items]
        units = [unit("a", "I travel by train because it saves time."), unit("b", "The journey feels shorter on the railway.")]
        result = build_candidate_codebook(units, vector_provider=provider)
        self.assertEqual(len(texts), 2)
        self.assertEqual(result["diagnostics"]["vector_backend"], "provided_local_vectors")
        self.assertEqual(build_candidate_codebook([])["codes"], [])
        self.assertEqual(build_candidate_codebook(units, max_candidates=0)["unassigned_unit_ids"], ["a", "b"])
        with self.assertRaises(ValueError):
            build_candidate_codebook(units, vector_provider=lambda _: [])

    def test_prepositional_fragments_cannot_hide_valid_claims_in_same_unit(self):
        fragments = ["For each equipment inspection.", "about when a delivery schedule will be used.", "During the annual equipment inspection."]
        valid = "I postpone the delivery because the access road floods."
        text = " ".join([*fragments, valid])
        u = unit("u", text)
        offset = 100
        u["sentences"] = []
        for sentence in [*fragments, valid]:
            u["sentences"].append({"text": sentence, "source_start": offset, "source_end": offset + len(sentence)})
            offset += len(sentence) + 1
        result = build_candidate_codebook([u])
        self.assertEqual([code["label"] for code in result["codes"]], [valid])

    def test_noun_lists_are_not_assertions_and_prepositional_main_clauses_survive(self):
        units = [unit("list", "Orange apple berry lemon."), unit("cutoff", "I would choose a route from the like."), unit("main", "During winter, I store the bicycle indoors.")]
        result = build_candidate_codebook(units)
        self.assertEqual([code["anchor_unit_id"] for code in result["codes"]], ["main"])

    def test_ubiquitous_query_terms_are_deweighted_by_corpus_frequency(self):
        units = [unit(str(i), "The camera records a sequence of still images.", source=f"doc{i}.txt") for i in range(8)]
        units.append(unit("rare", "I find calibration difficult because the controls drift.", source="rare.txt"))
        result = build_candidate_codebook(units, mode="guided", research_questions=("camera calibration",), max_candidates=1)
        weights = result["diagnostics"]["context_term_weights"]
        self.assertGreater(weights["calibration"], weights["camera"])
        self.assertEqual(result["codes"][0]["anchor_unit_id"], "rare")

    def test_question_reference_can_rank_an_answer_without_becoming_evidence(self):
        answer = unit("answer", "It takes much longer because the handles slip.")
        answer["reference_context"] = "How usable is the new camera?"
        unrelated = unit("unrelated", "The moon rises above the orchard before midnight.")
        result = build_candidate_codebook([answer, unrelated], mode="guided", research_questions=("camera usability",))
        code = result["codes"][0]
        self.assertEqual(code["anchor_unit_id"], "answer")
        self.assertEqual(code["label"], answer["text"])
        self.assertEqual(code["evidence_unit_ids"], ["answer"])
        self.assertEqual(code["context_guidance"]["matched_terms"], [])
        self.assertGreater(code["context_relevance"], 0)
        self.assertLessEqual(code["context_guidance"]["reference_relevance"], 0.2)
        self.assertNotIn("camera", code["scope_terms"])
        self.assertNotIn("usable", code["scope_terms"])

    def test_generic_nominal_morphology_without_concept_dictionary(self):
        u = unit("a", "The route is feasible when the ferry operates.")
        result = build_candidate_codebook([u], mode="guided", research_questions=("route feasibility",))
        self.assertIn("feasible", result["codes"][0]["context_guidance"]["matched_terms"])

    def test_concise_labels_are_contiguous_complete_clauses_with_full_anchor(self):
        text = "The first shipment arrived yesterday; I repair damaged containers before departure."
        result = build_candidate_codebook([unit("a", text)])
        code = result["codes"][0]
        self.assertLessEqual(len(code["label"]), len(text))
        self.assertEqual(code["label"], "I repair damaged containers before departure.")
        self.assertEqual(code["anchor_text"], text)
        self.assertEqual(text[code["label_source_start"] - 100:code["label_source_end"] - 100], code["label"])
        scoped = "The courier arrived yesterday; I cannot leave unless the fuel arrives."
        code = build_candidate_codebook([unit("b", scoped)])["codes"][0]
        self.assertIn("cannot", code["label"])
        self.assertIn("unless", code["label"])

    def test_experiential_assertion_ranks_ahead_of_repeated_scripted_definition(self):
        units = [unit(f"definition{i}", "A camera is a device that records images using a lens.", source=f"source{i}.txt") for i in range(5)]
        units.append(unit("experience", "I find the camera difficult because its controls are slippery.", source="experience.txt"))
        result = build_candidate_codebook(units, mode="guided", research_questions=("camera",), max_candidates=1)
        self.assertEqual(result["codes"][0]["anchor_unit_id"], "experience")

    def test_short_complete_claim_beats_sprawling_claim_without_truncation(self):
        short = "I find the camera useful because it works in rain."
        long = "I find the camera useful because it works in rain, and " + "I would sometimes use the controls when the lighting changes, and " * 7 + "I think it could improve our work."
        result = build_candidate_codebook([unit("short", short), unit("long", long)], max_candidates=1)
        self.assertEqual(result["codes"][0]["anchor_text"], short)
        self.assertIn("long", result["unassigned_unit_ids"] + result["codes"][0]["qualification_unit_ids"] + result["codes"][0]["counterexample_unit_ids"])

    def test_conditions_and_modality_are_qualifications_not_contradictions(self):
        units = [unit("a", "I use the camera because it records clear images."), unit("b", "I might use the camera when it records clear images.")]
        result = build_candidate_codebook(units)
        self.assertEqual(len(result["codes"]), 2)
        for code in result["codes"]:
            self.assertFalse(code["counterexample_unit_ids"])
            self.assertEqual(len(code["qualification_unit_ids"]), 1)
            self.assertFalse(set(code["evidence_unit_ids"]) & set(code["qualification_unit_ids"]))
            for uid in code["qualification_unit_ids"]:
                self.assertEqual(code["evidence_status"][uid], "qualifying_assertion")

    def test_unpunctuated_interrogatives_and_anonymization_are_not_anchors(self):
        units = [unit("prompt", "How do we keep the route safe when the river floods."), unit("metadata", "I started work when I was [a specified age over 10] years old."), unit("valid", "I close the gate when the river floods.")]
        result = build_candidate_codebook(units)
        self.assertEqual([code["anchor_unit_id"] for code in result["codes"]], ["valid"])
        self.assertEqual(result["unassigned_unit_ids"], ["metadata", "prompt"])

    def test_matches_resolve_exactly_and_qualifiers_and_contrasts_are_disjoint(self):
        units = [unit("a", "I trust the sensor because the readings remain stable."), unit("b", "I might trust the sensor when the readings remain stable."), unit("c", "I do not trust the sensor because the readings remain stable.")]
        result = build_candidate_codebook(units)
        original = {u["id"]: u for u in units}
        for code in result["codes"]:
            sets = [set(code[key]) for key in ("evidence_unit_ids", "qualification_unit_ids", "counterexample_unit_ids")]
            self.assertFalse(sets[0] & sets[1] or sets[0] & sets[2] or sets[1] & sets[2])
            self.assertEqual({match["unit_id"] for match in code["matches"]}, set.union(*sets))
            for match in code["matches"]:
                u = original[match["unit_id"]]
                self.assertEqual(u["text"][match["source_start"]-u["start"]:match["source_end"]-u["start"]], match["text"])
                self.assertGreaterEqual(match["similarity"], 0)

    def test_two_isolated_shared_words_do_not_establish_assertion_match(self):
        units = [unit("a", "I pick the long forest route because the bridge has enough shade near the distant reservoir."), unit("b", "The presenter can pick up the microphone easily enough.")]
        result = build_candidate_codebook(units)
        self.assertTrue(all(len(code["matches"]) == 1 for code in result["codes"]))


if __name__ == "__main__":
    unittest.main()

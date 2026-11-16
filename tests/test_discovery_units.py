import hashlib
import unittest

from themeforge.analysis_types import TranscriptDocument
from themeforge.discovery_units import build_meaning_units


class DiscoveryUnitTests(unittest.TestCase):
    def units(self, text, name="interview.txt"):
        units = build_meaning_units([TranscriptDocument(name, text)])
        for unit in units:
            self.assertEqual(text[unit["source_start"]:unit["source_end"]], unit["text"])
            self.assertEqual(text[unit["context_start"]:unit["context_end"]], unit["context_text"])
            self.assertEqual(hashlib.sha256(text.encode("utf-8")).hexdigest(), unit["source_hash"])
            self.assertLessEqual(unit["context_start"], unit["source_start"])
            self.assertGreaterEqual(unit["context_end"], unit["source_end"])
            for sentence in unit["sentences"]:
                self.assertEqual(text[sentence["source_start"]:sentence["source_end"]], sentence["text"])
        self.assertEqual(len(units), len({unit["id"] for unit in units}))
        return units

    def test_negated_continuation_and_actor_survive_across_lines(self):
        text = (
            "Interviewer: Was operating the boat easy?\r\n"
            "P1: I could use the engine. We managed the rope.\r\n"
            "But I could not\r\nsteer alone unless my sister was there."
        )
        units = self.units(text)
        participant = units[1]
        self.assertEqual(participant["text"], text.split("P1: ", 1)[1])
        self.assertTrue(participant["eligible"])
        self.assertEqual(len(participant["sentences"]), 3)
        self.assertTrue({"negation", "modality", "contrast", "condition"}.issubset(participant["relation_cues"]))

    def test_whole_turn_retains_initial_fragment_and_following_explanation(self):
        text = "P1: Because of the noise. I could not hear the nurse. My daughter repeated her words."
        unit = self.units(text)[0]
        self.assertTrue(unit["eligible"])
        self.assertEqual(len(unit["sentences"]), 3)
        self.assertTrue(unit["text"].startswith("Because of the noise."))

    def test_leading_question_is_context_never_participant_assertion(self):
        text = (
            "Moderator: So online sales made your business more profitable?\n"
            "P1: No. It might have helped shipping, but I did not earn more.\n"
            "P2: This was different for me; I lost two customers."
        )
        units = self.units(text)
        self.assertFalse(units[0]["eligible"])
        for unit in units[1:]:
            self.assertNotIn("more profitable", unit["text"])
            self.assertEqual(units[0]["text"], unit["reference_context"])
            self.assertTrue(unit["eligible"])
            self.assertIn("Moderator: So online sales", unit["context_text"])
        self.assertIn("context_required", units[2]["flags"])

    def test_mislabeled_recording_closure_is_retained_and_flagged(self):
        units = self.units(
            "P1: I will stop recording now. Thank you for participating. I will email you the transcript.\n"
            "P2: I recorded birds near the wetland to compare their songs."
        )
        self.assertIn("administration", units[0]["flags"])
        self.assertFalse(units[0]["eligible"])
        self.assertTrue(units[1]["eligible"])
        self.assertNotIn("administration", units[1]["flags"])

    def test_short_and_incomplete_turns_are_reviewable(self):
        units = self.units("P1: Yes.\nP2: Because...\nP3: Engine failed.")
        self.assertIn("brief_response", units[0]["flags"])
        self.assertIn("possible_fragment", units[1]["flags"])
        self.assertFalse(units[0]["eligible"])
        self.assertFalse(units[1]["eligible"])
        self.assertTrue(units[2]["eligible"])

    def test_rare_case_not_rejected_by_document_or_word_frequency(self):
        documents = [
            TranscriptDocument("farm.txt", "P1: A hailstorm broke every pane in the greenhouse."),
            TranscriptDocument("clinic.txt", "P2: My prosthesis pinched when I climbed the stairs."),
            TranscriptDocument("studio.txt", "P3: I refused."),
        ]
        units = build_meaning_units(documents)
        self.assertEqual(len(units), 3)
        self.assertTrue(all(unit["eligible"] for unit in units))

    def test_document_order_and_repeated_text_have_stable_distinct_ids(self):
        text = "P1: The café closed early.\nP2: The café closed early.\nP1: The café closed early."
        documents = [TranscriptDocument("zeta.txt", text), TranscriptDocument("alpha.txt", text)]
        units = build_meaning_units(documents)
        self.assertEqual(units, build_meaning_units(list(reversed(documents))))
        self.assertEqual(len(units), 6)
        self.assertEqual(len({unit["id"] for unit in units}), 6)
        self.units(text)

    def test_duplicate_source_names_fail_explicitly(self):
        with self.assertRaisesRegex(ValueError, "unique source names"):
            build_meaning_units([TranscriptDocument("same.txt", "P1: First."), TranscriptDocument("same.txt", "P2: Second.")])

    def test_plain_narrative_retains_unverified_speaker_but_not_prompts(self):
        text = (
            "Could you describe the difficult journey?\n\n"
            "I missed the last ferry. My neighbor drove me home.\n\n"
            "What helped was having someone nearby. I could finally rest."
        )
        units = self.units(text)
        self.assertEqual(len(units), 3)
        self.assertTrue(all(unit["speaker"] == "Unknown" for unit in units))
        self.assertTrue(all("speaker_unverified" in unit["flags"] for unit in units))
        self.assertFalse(units[0]["eligible"])
        self.assertIn("unattributed_prompt", units[0]["flags"])
        self.assertTrue(units[1]["eligible"])
        self.assertTrue(units[2]["eligible"])
        self.assertEqual(units[1]["reference_context"], units[0]["text"])
        self.assertNotIn("Could you describe", units[1]["text"])

    def test_question_only_unknown_turn_is_flagged_without_invented_actor(self):
        units = self.units("Was it safe? Did anyone accompany you?")
        self.assertEqual(units[0]["speaker"], "Unknown")
        self.assertIn("unattributed_prompt", units[0]["flags"])
        self.assertFalse(units[0]["eligible"])

    def test_unicode_exact_context_and_timestamp_header(self):
        text = (
            "진행자 0:03\r\n어떤 도움이 필요했나요？\r\n\r\n"
            "참여자 0:08\r\n쉬운   설명이 필요했어요.\r\n가족과 함께 결정을 내렸어요.\r\n"
            "P2: The café’s entrance was narrow."
        )
        units = self.units(text, "한국어.txt")
        participant = units[1]
        self.assertEqual(participant["speaker"], "참여자")
        self.assertEqual(participant["reference_context"], "어떤 도움이 필요했나요？")
        self.assertEqual(participant["context_text"], text)
        self.assertTrue(participant["eligible"])
        self.assertFalse(units[0]["eligible"])

    def test_adjacent_paragraphs_merge_only_as_exact_source_span(self):
        text = "P1: The bus was late.\n\nI could not reach work.\nP1: But my supervisor waited."
        units = self.units(text)
        self.assertEqual(len(units), 2)
        self.assertEqual(units[0]["text"], "The bus was late.\n\nI could not reach work.")
        self.assertEqual(units[1]["text"], "But my supervisor waited.")
        self.assertFalse(any("P1:" in unit["text"] for unit in units))
        self.assertIn("inferred_paragraph_continuation", units[0]["flags"])

    def test_removed_noise_is_never_stitched_out_of_the_evidence(self):
        text = "P1: The first device failed.\nhttps://example.org/export\nP1: But the second device worked."
        units = self.units(text)
        self.assertEqual(len(units), 2)
        self.assertFalse(any("first device" in unit["text"] and "second device" in unit["text"] for unit in units))
        self.assertIn("https://example.org/export", units[0]["context_text"])

    def test_inferred_named_interviewer_is_scoped_to_document(self):
        documents = [
            TranscriptDocument("questions.txt", "Alex: What happened?\nP1: I missed the train.\nAlex: How did you return?\nP1: I walked home."),
            TranscriptDocument("account.txt", "Alex: I missed the train and walked home."),
        ]
        units = build_meaning_units(documents)
        self.assertTrue(next(unit for unit in units if unit["source_name"] == "account.txt")["eligible"])
        interviewers = [unit for unit in units if unit["source_name"] == "questions.txt" and unit["speaker"] == "Alex"]
        self.assertEqual(len(interviewers), 2)
        self.assertTrue(all("interviewer" in unit["flags"] and not unit["eligible"] for unit in interviewers))

    def test_initials_interviewer_with_fillers_keeps_exact_reference_question(self):
        text = (
            "AX: Okay. So, what happened on the crossing?\n"
            "P1: The ferry broke down before we reached the island.\n"
            "AX: Right, thanks. And could you describe what happened next?\n"
            "P1: A fishing boat brought us ashore."
        )
        units = self.units(text)
        prompts = [unit for unit in units if unit["speaker"] == "AX"]
        self.assertTrue(all("interviewer_inferred" in unit["flags"] and not unit["eligible"] for unit in prompts))
        self.assertEqual(units[1]["reference_context"], prompts[0]["text"])
        self.assertEqual(units[3]["reference_context"], prompts[1]["text"])

    def test_protocol_leader_inference_handles_many_explanatory_turns(self):
        turns = [
            "We're conducting this study to understand journeys.",
            "During this interview I will ask about your experiences.",
            "Okay. So, what happened first?",
            "The next section describes a sample journey.",
            "Right. And how did you return?",
            "This diagram shows an example route.",
            "The final section covers a hypothetical journey.",
        ]
        text = "\n".join(f"AX: {turn}\nP1: I crossed the island by bicycle that evening." for turn in turns)
        units = self.units(text)
        self.assertTrue(all(not unit["eligible"] for unit in units if unit["speaker"] == "AX"))
        self.assertTrue(all(unit["eligible"] for unit in units if unit["speaker"] == "P1"))

    def test_mislabeled_protocol_turn_does_not_exclude_the_entire_participant(self):
        text = (
            "Participant 20: We are conducting this study to understand journeys. During this interview I will ask about travel.\n"
            "Interviewer: What happened on your journey?\n"
            "Participant 20: I missed the ferry because the road was flooded.\n"
            "Interviewer: What changed after that?\n"
            "Participant 20: I brought a bicycle and crossed the bridge instead."
        )
        participants = [unit for unit in self.units(text) if unit["speaker"] == "Participant 20"]
        self.assertFalse(participants[0]["eligible"])
        self.assertIn("administration", participants[0]["flags"])
        self.assertTrue(all(unit["eligible"] for unit in participants[1:]))
        self.assertFalse(any("interviewer" in unit["flags"] for unit in participants))

    def test_punctuated_brief_dissent_retains_exact_question_and_exclusion(self):
        units = self.units("Moderator: Are fares still a barrier?\nParticipant 1: No, not anymore.")
        reply = units[1]
        self.assertEqual(reply["text"], "No, not anymore.")
        self.assertIn("brief_response", reply["flags"])
        self.assertFalse(reply["eligible"])
        self.assertEqual(reply["reference_context"], "Are fares still a barrier?")

    def test_pause_only_numbered_interviewee_turn_does_not_contaminate_next_turn(self):
        units = self.units(
            "Interviewee 1: [2-second pause] Hmm. [7-second pause] Hmm!\n"
            "Interviewee 1: I could hear the station announcements clearly.\n"
            "Interviewee 1: I refused."
        )
        self.assertEqual(len(units), 3)
        self.assertIn("nonverbal_or_pause", units[0]["flags"])
        self.assertFalse(units[0]["eligible"])
        self.assertTrue(units[1]["eligible"])
        self.assertTrue(units[2]["eligible"])
        self.assertTrue(all("Interviewee 1:" not in unit["text"] for unit in units))

    def test_generic_single_protocol_intro_is_not_substantive_candidate(self):
        units = self.units(
            "Casey 0:03\nThe goal of this interview is to understand travel.\n\n"
            "Participant 1 0:22\nI reached the island before the storm."
        )
        self.assertIn("administration", units[0]["flags"])
        self.assertFalse(units[0]["eligible"])
        self.assertTrue(units[1]["eligible"])

    def test_extraction_artifact_is_flagged_not_deleted_or_silently_rewritten(self):
        text = "Page 1: P03 SoI needed captions to understand the announcements."
        unit = self.units(text)[0]
        self.assertEqual(unit["text"], text.removeprefix("Page 1: "))
        self.assertIn("source_format_uncertain", unit["flags"])
        self.assertFalse(unit["eligible"])

    def test_unpunctuated_question_clauses_infer_protocol_leader(self):
        units = self.units(
            "AX: I will stop the recording when we finish\nP1: I crossed the bridge yesterday.\n"
            "AX: Okay. The next part is what has been most difficult\nP1: The ferry was late.\n"
            "AX: Right, so what resources did you need\nP1: I needed fuel for my boat.\n"
            "AX: But what would you like to change\nP1: I would depart before sunrise."
        )
        self.assertTrue(all(not unit["eligible"] for unit in units if unit["speaker"] == "AX"))
        self.assertTrue(all(unit["eligible"] for unit in units if unit["speaker"] == "P1"))
        self.assertEqual(units[-1]["reference_context"], "But what would you like to change")

    def test_pdf_line_numbers_do_not_turn_backchannels_into_assertions(self):
        text = "Interviewee 1: Yes. 204\nInterviewee 1: [2-second pause] Hmm. 205\nInterviewee 1: I refused. 206"
        units = self.units(text)
        self.assertFalse(units[0]["eligible"])
        self.assertFalse(units[1]["eligible"])
        self.assertTrue(units[2]["eligible"])
        self.assertEqual(units[0]["text"], "Yes. 204")

    def test_technical_checks_do_not_exclude_substantive_hearing_experience(self):
        units = self.units(
            "P1: And then, can you hear it.\nP2: I could not hear the nurse when she described my medication."
        )
        self.assertFalse(units[0]["eligible"])
        self.assertIn("administration", units[0]["flags"])
        self.assertTrue(units[1]["eligible"])

    def test_numbered_interviewee_role_is_not_globally_inferred_from_questions(self):
        units = self.units(
            "Interviewee 12: Why did the ferry leave before sunrise? I was left behind.\n"
            "Moderator: How did you feel?\n"
            "Interviewee 12: Why was nobody waiting? I walked home alone."
        )
        replies = [unit for unit in units if unit["speaker"] == "Interviewee 12"]
        self.assertEqual(len(replies), 2)
        self.assertTrue(all(unit["eligible"] for unit in replies))
        self.assertFalse(any("interviewer_inferred" in unit["flags"] for unit in replies))

    def test_sparse_questions_need_repeated_protocol_evidence_for_role_inference(self):
        questions = ["Okay, so what did you experience on the journey", "And what resources did you need"]
        protocol = ["During this interview I will ask about journeys.", "The next question concerns your return."]
        accounts = [f"I traveled across the island on day {number}." for number in range(6)]
        interviewer = [*protocol, *questions, *accounts]
        participant = [*questions, *accounts, "I carried supplies for my neighbor.", "I came home before dawn."]
        docs = [
            TranscriptDocument("protocol.txt", "\n".join(f"AX: {turn}\nP1: I traveled by boat." for turn in interviewer)),
            TranscriptDocument("account.txt", "\n".join(f"Jordan: {turn}" for turn in participant)),
        ]
        units = build_meaning_units(docs)
        inferred = [unit for unit in units if unit["speaker"] == "AX"]
        unverified_role = [unit for unit in units if unit["speaker"] == "Jordan"]
        self.assertEqual(len(inferred), 10)
        self.assertEqual(len(unverified_role), 10)
        self.assertTrue(all("interviewer_inferred" in unit["flags"] and not unit["eligible"] for unit in inferred))
        self.assertTrue(all(unit["eligible"] for unit in unverified_role))


if __name__ == "__main__":
    unittest.main()

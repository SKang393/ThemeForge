import hashlib
import unittest
from dataclasses import replace

from themeforge.analysis_types import TranscriptSegment
from themeforge.quote_units import extract_evidence_units, extract_quote_units
from themeforge.transcript_parser import parse_transcript


class EvidenceSpanTests(unittest.TestCase):
    def assert_source_resolves(self, text, units):
        for unit in units:
            self.assertGreater(unit.source_end, unit.source_start)
            self.assertEqual(text[unit.source_start:unit.source_end], unit.text)
            self.assertEqual(text[unit.context_start:unit.context_end], unit.context_text)
            self.assertEqual(unit.source_hash, hashlib.sha256(text.encode("utf-8")).hexdigest())

    def test_raw_whitespace_and_newlines_survive_quote_pairing(self):
        text = "P1: Shared   planning helped staff.\r\n\tClear examples supported better lessons.\r\nP2: Families wanted translated explanations before meetings."
        units = extract_quote_units(parse_transcript(text))
        self.assertEqual(len(units), 2)
        self.assertEqual(units[0].text, "Shared   planning helped staff.\r\n\tClear examples supported better lessons.")
        self.assert_source_resolves(text, units)

    def test_duplicate_words_keep_each_original_occurrence(self):
        text = "P1: Shared   planning helped staff adapt lessons.\nP2: Shared planning helped staff adapt lessons.\nP3: Shared   planning helped staff adapt lessons."
        units = extract_quote_units(parse_transcript(text))
        self.assertEqual(len(units), 3)
        self.assertEqual(len({unit.id for unit in units}), 3)
        self.assertEqual([unit.source_line for unit in units], [1, 2, 3])
        self.assertEqual([unit.source_start for unit in units], sorted(unit.source_start for unit in units))
        self.assert_source_resolves(text, units)

    def test_filtered_sentence_does_not_join_noncontiguous_evidence(self):
        text = "Shared planning supported accessible lessons. This interview will be recorded for research. Families wanted translated examples before meetings."
        all_units = extract_evidence_units(parse_transcript(text))
        units = [unit for unit in all_units if not unit.exclusion_reason]
        self.assertEqual([unit.text for unit in units], [
            "Shared planning supported accessible lessons.",
            "Families wanted translated examples before meetings.",
        ])
        self.assertEqual(len(all_units), 3)
        self.assertEqual(all_units[1].exclusion_reason, "interview_prompt_or_procedure")
        self.assert_source_resolves(text, all_units)

    def test_brief_dissent_is_reviewable_with_moderator_context(self):
        text = "Moderator: Do you still choose your own purchases?\nP1: No, not anymore.\nP2: Staff supported my decisions during shopping trips."
        units = extract_evidence_units(parse_transcript(text))
        reply = next(unit for unit in units if unit.speaker == "P1")
        self.assertEqual(reply.text, "No, not anymore.")
        self.assertTrue(reply.exclusion_reason)
        self.assertIn("Moderator: Do you still choose your own purchases?", reply.context_text)
        self.assertIn("P2: Staff supported", reply.context_text)
        self.assertNotIn(reply.id, {unit.id for unit in extract_quote_units(parse_transcript(text))})
        self.assert_source_resolves(text, units)

    def test_context_does_not_cross_documents(self):
        first = "Moderator: What helped?\nP1: Shared planning supported accessible lessons."
        second = "P2: Families wanted translated examples before meetings."
        units = extract_evidence_units(parse_transcript(first, "first.txt") + parse_transcript(second, "second.txt"))
        self.assertTrue(all("Families wanted" not in unit.context_text for unit in units if unit.source_name == "first.txt"))
        self.assertTrue(all("Shared planning" not in unit.context_text for unit in units if unit.source_name == "second.txt"))

    def test_named_speaker_inference_is_scoped_to_source_version(self):
        interview = "Alex: What supported your decisions?\nAlex: Can you describe the experience?\nP1: Shared planning supported accessible lessons."
        participant = "Alex: Families wanted translated examples before meetings."
        for name in ("second.txt", "first.txt"):
            with self.subTest(source_name=name):
                units = extract_evidence_units(
                    parse_transcript(interview, "first.txt") + parse_transcript(participant, name)
                )
                interviewer_hash = hashlib.sha256(interview.encode("utf-8")).hexdigest()
                interviewer_units = [unit for unit in units if unit.speaker == "Alex" and unit.source_hash == interviewer_hash]
                participant_units = [unit for unit in units if unit.speaker == "Alex" and unit.source_hash != interviewer_hash]
                self.assertEqual(len(interviewer_units), 2)
                self.assertTrue(all(unit.exclusion_reason == "excluded_speaker" for unit in interviewer_units))
                self.assertEqual(len(participant_units), 1)
                self.assertEqual(participant_units[0].exclusion_reason, "")
                self.assertEqual(participant_units[0].text, participant.removeprefix("Alex: "))

    def test_korean_text_offsets_and_lines_resolve(self):
        text = "진행자: 어떤 도움이 필요했나요?\n참여자: 쉬운   설명이 필요했어요.\n가족과 함께 결정을 내렸어요."
        units = extract_evidence_units(parse_transcript(text, "한국어.txt"))
        reply = next(unit for unit in units if unit.speaker == "참여자")
        self.assertEqual(reply.source_line, 2)
        self.assertIn("\n", reply.text)
        self.assert_source_resolves(text, units)

    def test_inline_speakers_preserve_physical_line_offsets(self):
        text = "P1: Shared planning supported accessible lessons. P2: Families wanted translated examples before meetings."
        units = extract_quote_units(parse_transcript(text))
        self.assertEqual([unit.speaker for unit in units], ["P1", "P2"])
        self.assertEqual([unit.source_line for unit in units], [1, 1])
        self.assert_source_resolves(text, units)

    def test_inline_timestamp_headers_preserve_raw_ranges(self):
        text = "P1 0:03 Shared planning supported accessible lessons. P2 1:04 Families wanted translated examples before meetings."
        units = extract_quote_units(parse_transcript(text))
        self.assertEqual([unit.speaker for unit in units], ["P1", "P2"])
        self.assertEqual([unit.source_line for unit in units], [1, 1])
        self.assertNotIn("0:03", units[0].text)
        self.assert_source_resolves(text, units)

    def test_overlapping_virtual_and_physical_breaks_do_not_duplicate_turns(self):
        text = "P1 0:03\r\nShared planning supported accessible lessons. P2: Families wanted translated examples before meetings.\r\n\r\nP3 1:04\r\nPeer feedback helped students complete difficult assignments."
        segments = parse_transcript(text)
        self.assertEqual([segment.speaker for segment in segments], ["P1", "P2", "P3"])
        units = extract_quote_units(segments)
        self.assertEqual(len(units), 3)
        self.assertEqual([unit.source_line for unit in units], [2, 2, 5])
        self.assert_source_resolves(text, units)

    def test_timestamp_context_keeps_the_speaker_header(self):
        text = "Moderator 0:03\nDo you choose your own purchases?\n\nP1 0:10\nNo."
        units = extract_evidence_units(parse_transcript(text))
        reply = next(unit for unit in units if unit.speaker == "P1")
        self.assertEqual(reply.source_line, 5)
        self.assertTrue(reply.context_text.startswith("Moderator 0:03"))
        self.assert_source_resolves(text, units)

    def test_units_have_stable_ids_independent_of_filter_threshold(self):
        text = "P1: Shared planning supported accessible lessons."
        original = extract_evidence_units(parse_transcript(text))[0]
        review_only = extract_evidence_units(parse_transcript(text), min_quote_words=100)[0]
        changed = extract_evidence_units(parse_transcript(text + "\n"))[0]
        other_document = extract_evidence_units(parse_transcript(text, "other.txt"))[0]
        self.assertEqual(original.id, review_only.id)
        self.assertEqual(review_only.exclusion_reason, "below_minimum_quote_words")
        self.assertNotEqual(original.id, changed.id)
        self.assertNotEqual(original.id, other_document.id)

    def test_inconsistent_source_range_fails_instead_of_guessing(self):
        segment = parse_transcript("P1: Shared planning supported accessible lessons.")[0]
        with self.assertRaisesRegex(ValueError, "Source range"):
            extract_evidence_units([replace(segment, source_start=0)])

    def test_uppercase_brief_participant_reply_is_retained(self):
        text = "Moderator: Did that happen again?\nP1: NEVER"
        units = extract_evidence_units(parse_transcript(text))
        reply = next(unit for unit in units if unit.speaker == "P1")
        self.assertEqual(reply.text, "NEVER")
        self.assertTrue(reply.exclusion_reason)
        self.assert_source_resolves(text, units)

    def test_supplied_segment_without_document_remains_explicitly_unversioned(self):
        segment = TranscriptSegment("P1", "Shared planning supported accessible lessons.", 3, source_start=12)
        unit = extract_evidence_units([segment])[0]
        self.assertEqual(unit.source_start, 12)
        self.assertEqual(unit.source_hash, "")
        self.assertEqual(unit.context_text, segment.text)


if __name__ == "__main__":
    unittest.main()

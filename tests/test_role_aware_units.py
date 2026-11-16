"""Opt-in roster parsing preserves source evidence and default compatibility."""
from dataclasses import asdict
from hashlib import sha256
import unittest

from themeforge.analysis_types import TranscriptDocument
from themeforge.discovery_units import build_meaning_units
from themeforge.transcript_parser import parse_transcript, explicit_role_roster


class RoleAwareUnitTests(unittest.TestCase):
    def check_source(self, source, units):
        for unit in units:
            self.assertEqual(source[unit["source_start"]:unit["source_end"]], unit["text"])
            self.assertEqual(source[unit["context_start"]:unit["context_end"]], unit["context_text"])
            self.assertEqual(sha256(source.encode()).hexdigest(), unit["source_hash"])
            for sentence in unit["sentences"]:
                self.assertEqual(source[sentence["source_start"]:sentence["source_end"]], sentence["text"])

    def test_wrapped_colon_phrase_remains_in_original_participant_turn(self):
        source = "Interviewer 9: How do you share measurements?\r\nParticipant 4: I store the results in a repository. Here is\r\nhow that works: they take the files and retain copies. I share the link.\r\nModerator 2: What changes when access is restricted?"
        docs = [TranscriptDocument("fictional.txt", source)]
        legacy = build_meaning_units(docs)
        aware = build_meaning_units(docs, speaker_label_policy="explicit_roles")
        self.assertIn("how that works", {u["speaker"] for u in legacy})
        self.assertNotIn("how that works", {u["speaker"] for u in aware})
        participant = next(u for u in aware if u["speaker"] == "Participant 4")
        expected = source.split("Participant 4: ", 1)[1].split("\r\nModerator 2:", 1)[0]
        self.assertEqual(participant["text"], expected)
        self.assertTrue(participant["eligible"])
        self.assertEqual(participant["reference_context"], "How do you share measurements?")
        self.assertIn("speaker_label_policy_explicit_roles", participant["flags"])
        self.check_source(source, aware)

    def test_inline_colon_phrase_is_not_a_virtual_speaker_boundary(self):
        source = "Interviewer 1: What do you retain? Participant 1: I retain duplicate files. For example: I keep the originals elsewhere. Participant 2: I retain a paper copy."
        segments = parse_transcript(source, speaker_label_policy="explicit_roles")
        self.assertEqual([s.speaker for s in segments], ["Interviewer 1", "Participant 1", "Participant 2"])
        self.assertIn("For example: I keep", segments[1].text)
        for segment in segments:
            self.assertEqual(source[segment.source_start:segment.source_end], segment.text)

    def test_nonrole_timestamp_like_text_remains_literal_continuation(self):
        source = "Interviewer 1 00:01\nWhat matters?\nParticipant 1 00:03\nI compare measurements with a known reference.\ncheckpoint 12:30\nThe next measurement needs the same reference.\nModerator 1 00:10\nWhat happens afterward?"
        segments = parse_transcript(source, speaker_label_policy="explicit_roles")
        self.assertEqual([s.speaker for s in segments], ["Interviewer 1", "Participant 1", "Moderator 1"])
        self.assertIn("checkpoint 12:30\n", segments[1].text)

    def test_numbered_shorthand_hash_and_case_variants_are_explicit_roles(self):
        source = "Interviewer1: Why keep a copy?\nParticipant #2: I keep a copy because the original can fail.\np03: I retain another copy in a separate building.\nmoderator2: What happens next?"
        units = build_meaning_units([TranscriptDocument("roles.txt", source)], speaker_label_policy="explicit_roles")
        self.assertEqual([u["speaker"] for u in units], ["Interviewer1", "Participant #2", "p03", "moderator2"])
        self.assertFalse(units[0]["eligible"])
        self.assertFalse(units[-1]["eligible"])
        self.assertTrue(units[1]["eligible"])
        self.assertNotIn("interviewer_inferred", units[0]["flags"])
        self.check_source(source, units)

    def test_named_or_unlabeled_transcripts_fall_back_without_new_actor_guesses(self):
        for source in ("Alice: I keep copies.\nHow this works: I retain a separate version.", "I keep copies in a folder.\nA later paragraph describes another copy."):
            with self.subTest(source=source):
                legacy = parse_transcript(source)
                aware = parse_transcript(source, speaker_label_policy="explicit_roles")
                self.assertFalse(explicit_role_roster(source))
                self.assertEqual(legacy, aware)
                default_units = build_meaning_units([TranscriptDocument("source.txt", source)])
                fallback = build_meaning_units([TranscriptDocument("source.txt", source)], speaker_label_policy="explicit_roles")
                for before, after in zip(default_units, fallback):
                    self.assertIn("speaker_label_policy_legacy_fallback", after["flags"])
                    self.assertEqual({k: v for k, v in before.items() if k != "flags"}, {k: v for k, v in after.items() if k != "flags"})
                    self.assertEqual(before["flags"], [flag for flag in after["flags"] if flag != "speaker_label_policy_legacy_fallback"])

    def test_interviewer_only_roster_keeps_named_participant_with_legacy_fallback(self):
        source = "Interviewer: What happens when the bridge closes?\nAlice: I take another route because the bridge closes during storms.\nInterviewer: What happens afterward?"
        self.assertFalse(explicit_role_roster(source))
        legacy = parse_transcript(source)
        aware = parse_transcript(source, speaker_label_policy="explicit_roles")
        self.assertEqual(legacy, aware)
        self.assertEqual([s.speaker for s in aware], ["Interviewer", "Alice", "Interviewer"])
        units = build_meaning_units([TranscriptDocument("named.txt", source)], speaker_label_policy="explicit_roles")
        participant = next(u for u in units if u["speaker"] == "Alice")
        self.assertEqual(participant["text"], "I take another route because the bridge closes during storms.")
        self.assertTrue(participant["eligible"])
        self.assertIn("speaker_label_policy_legacy_fallback", participant["flags"])
        self.assertNotIn("interviewer", participant["flags"])
        self.check_source(source, units)

    def test_zero_space_role_headers_keep_interviewer_and_participant_isolated(self):
        source = "Interviewer 4:Why retain copies?\nParticipant 2:I retain copies because the original device can fail.\nInterviewer 4:Licenses.\nParticipant 3:I request permission before sharing the records."
        units = build_meaning_units([TranscriptDocument("zero-space.txt", source)], speaker_label_policy="explicit_roles")
        self.assertEqual([u["speaker"] for u in units], ["Interviewer 4", "Participant 2", "Interviewer 4", "Participant 3"])
        self.assertEqual([u["text"] for u in units], ["Why retain copies?", "I retain copies because the original device can fail.", "Licenses.", "I request permission before sharing the records."])
        self.assertFalse(units[0]["eligible"])
        self.assertFalse(units[2]["eligible"])
        self.assertTrue(units[1]["eligible"])
        self.assertNotIn("Licenses", units[1]["text"])
        self.check_source(source, units)

    def test_inline_zero_space_role_header_is_a_source_preserving_boundary(self):
        source = "Participant 1:I retain two copies. Interviewer 1:What happens next? Participant 2:I verify the copies before deleting anything."
        segments = parse_transcript(source, speaker_label_policy="explicit_roles")
        self.assertEqual([s.speaker for s in segments], ["Participant 1", "Interviewer 1", "Participant 2"])
        self.assertEqual(segments[0].text, "I retain two copies.")
        for segment in segments:
            self.assertEqual(source[segment.source_start:segment.source_end], segment.text)

    def test_default_and_explicit_legacy_are_identical_for_existing_formats(self):
        sources = ("P1: I keep copies.\nDetails: There are two versions.", "Alice 00:01\nI keep copies.\nBob 00:05\nI retain originals.", "Participant 1: First copy. Participant 2: Second copy.")
        for source in sources:
            with self.subTest(source=source):
                self.assertEqual([asdict(s) for s in parse_transcript(source)], [asdict(s) for s in parse_transcript(source, speaker_label_policy="legacy")])
                docs = [TranscriptDocument("legacy.txt", source)]
                self.assertEqual(build_meaning_units(docs), build_meaning_units(docs, speaker_label_policy="legacy"))

    def test_policy_is_keyword_only_and_unknown_values_fail_even_without_documents(self):
        with self.assertRaises(TypeError):
            parse_transcript("P1: Text.", "source", "explicit_roles")
        with self.assertRaises(ValueError):
            parse_transcript("", speaker_label_policy="guess_names")
        with self.assertRaises(ValueError):
            build_meaning_units([], speaker_label_policy="guess_names")

    def test_document_order_stays_deterministic_in_opt_in_mode(self):
        docs = [TranscriptDocument("b", "P1: I retain originals.\nDetails: There are two copies."), TranscriptDocument("a", "Participant 1: I store copies.\nMy process: I retain originals.")]
        self.assertEqual(build_meaning_units(docs, speaker_label_policy="explicit_roles"), build_meaning_units(list(reversed(docs)), speaker_label_policy="explicit_roles"))


if __name__ == "__main__":
    unittest.main()

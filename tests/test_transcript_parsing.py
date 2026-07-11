import unittest

from themeforge.analysis import (
    AnalysisSettings,
    analyze_transcript,
    extract_quote_units,
    parse_transcript,
)


SAMPLE_TRANSCRIPT = """
Participant 1: I felt isolated when online classes started. It was hard to ask questions without seeing classmates.
Participant 1: Small group meetings helped because peers explained assignments in simple language.
Participant 2: The teacher's weekly feedback made me feel supported and more confident about my writing.
Participant 2: I still needed clearer deadlines because the learning platform was confusing.
Participant 3: Working with classmates in breakout rooms helped me feel part of the course community.
Participant 3: I wanted more examples from the instructor before submitting big projects.
""".strip()


class TranscriptParsingTests(unittest.TestCase):
    def test_parse_transcript_preserves_speaker_labels(self):
        segments = parse_transcript(SAMPLE_TRANSCRIPT)

        self.assertEqual(len(segments), 6)
        self.assertEqual(segments[0].speaker, "Participant 1")
        self.assertIn("online classes", segments[0].text)
        self.assertEqual(segments[-1].speaker, "Participant 3")

    def test_extract_quote_units_uses_speaker_turn_meaning_units(self):
        transcript = "P1: Online class was difficult. Peer meetings helped me continue."

        quotes = extract_quote_units(parse_transcript(transcript))

        self.assertEqual([quote.text for quote in quotes], [
            "Online class was difficult. Peer meetings helped me continue.",
        ])
        self.assertEqual({quote.speaker for quote in quotes}, {"P1"})
        self.assertEqual(transcript[quotes[0].source_start : quotes[0].source_end], quotes[0].text)

    def test_quote_units_keep_distinct_source_offsets_for_duplicate_text(self):
        transcript = "\n".join(
            [
                "P1: Shared planning helped the curriculum team adapt lessons.",
                "P2: Shared planning helped the curriculum team adapt lessons.",
            ]
        )

        quotes = extract_quote_units(parse_transcript(transcript))

        self.assertEqual(len(quotes), 2)
        self.assertEqual([quote.text for quote in quotes], [quotes[0].text, quotes[0].text])
        self.assertLess(quotes[0].source_start, quotes[1].source_start)
        for quote in quotes:
            self.assertEqual(transcript[quote.source_start : quote.source_end], quote.text)

    def test_unlabeled_transcript_keeps_quotes_after_procedure_text(self):
        transcript = (
            "Thank you for participating in this interview. "
            "This interview will be recorded for research purpose. "
            "Students said online tutoring helped them ask questions after class. "
            "Families needed translation support because school messages were difficult to understand. "
            "Teachers built trust when they gave examples and followed up after meetings."
        )

        quotes = extract_quote_units(parse_transcript(transcript))

        self.assertEqual(
            [quote.text for quote in quotes],
            [
                "Students said online tutoring helped them ask questions after class. Families needed translation support because school messages were difficult to understand.",
                "Teachers built trust when they gave examples and followed up after meetings.",
            ],
        )
        self.assertEqual({quote.speaker for quote in quotes}, {"Unknown"})

    def test_otter_timestamp_speakers_are_parsed_and_interviewer_excluded(self):
        transcript = """
marie  0:03
The goal of this interview is to understand curriculum strengths. You do not need to answer any questions you do not want to answer.

DSP 1  1:04
The curriculum is starting to get better because staff now plan community lessons together.

marie  2:03
What do you enjoy most about working in the emerging adults program?

DSP 1  2:13
I enjoy seeing adults grow more independent when routines and life skills are practiced.

Transcribed by https://otter.ai.
""".strip()

        segments = parse_transcript(transcript)
        quotes = extract_quote_units(segments)

        self.assertIn("marie", {segment.speaker for segment in segments})
        self.assertIn("DSP 1", {segment.speaker for segment in segments})
        self.assertEqual({quote.speaker for quote in quotes}, {"DSP 1"})
        generated = " ".join(quote.text for quote in quotes).lower()
        self.assertNotIn("marie", generated)
        self.assertNotIn("otter.ai", generated)

    def test_docx_timestamp_speakers_are_parsed_and_interviewer_excluded(self):
        transcript = """
MD 0:01
Before we start, this interview will be recorded for research purpose.

ADMIN1 0:59
The curriculum planning meetings helped staff choose practical activities.

MD 1:10
Can you describe what support would help?

ADMIN1 1:20
Protected planning time would help instructors revise the curriculum.
""".strip()

        quotes = extract_quote_units(parse_transcript(transcript))

        self.assertEqual({quote.speaker for quote in quotes}, {"ADMIN1"})
        self.assertTrue(all("MD" not in quote.text for quote in quotes))

    def test_drm_interviewer_turns_are_excluded(self):
        transcript = """
DRM: We will move to the next section about preference assessment.
P16: Classroom routines help me understand which activities each child prefers.
DRM: I want to ask about another type of assessment now.
P16: Offering choices helps children communicate what they want to use.
""".strip()

        quotes = extract_quote_units(parse_transcript(transcript))

        self.assertEqual({quote.speaker for quote in quotes}, {"P16"})

    def test_nvio_table_noise_and_moderator_rows_are_removed(self):
        transcript = """
Timespan
Content
1
0:00.0 - 0:27.0
CODE HEADER ROW
Moderator: Can you describe the club experience?
The smaller club helped students practice social skills with predictable routines.
2
0:27.0 - 0:45.0
Peer buddies explained rules before activities and helped students take breaks.
""".strip()

        quotes = extract_quote_units(parse_transcript(transcript))
        text = " ".join(quote.text for quote in quotes).lower()

        self.assertTrue(quotes)
        self.assertNotIn("timespan", text)
        self.assertNotIn("content", text)
        self.assertNotIn("0:00", text)
        self.assertNotIn("moderator", text)
        self.assertNotIn("code header", text)

    def test_nvio_colon_code_header_rows_are_removed(self):
        transcript = """
SPECTRUM: BEING ON SPECTRUM OR LIKE DISORDERBARRIER TO PARTICIPATE 4H PROGRAM TYPESCHALLENGES INVOLVEMENTTRAINING TACTICS SUPPORTSACTIVITIES
Participant: Training volunteers helped them adapt curriculum activities for youth.
""".strip()

        quotes = extract_quote_units(parse_transcript(transcript))
        text = " ".join(quote.text for quote in quotes).lower()

        self.assertEqual({quote.speaker for quote in quotes}, {"Participant"})
        self.assertNotIn("disorderbarrier", text)
        self.assertNotIn("typeschallenges", text)

    def test_pdf_page_headers_and_line_numbers_are_removed(self):
        transcript = """
Farsides et al. (2024). Autism and Kindness
1
Transcript of Interview 1 (22 June 2022): The Kindness of Autistic People 1
Interviewee 1: Autistic identity shaped how I understood kindness in daily relationships. Friends showed care by noticing sensory overload.
Farsides et al. (2024). Autism and Kindness
2
Interviewee 1: Community support helped me feel accepted when people respected direct communication.
""".strip()

        result = analyze_transcript(
            transcript,
            AnalysisSettings(theme_count=2, quotes_per_theme=1, min_theme_size=1),
        )
        generated = " ".join(
            [
                *(theme.name for theme in result.themes),
                *(keyword for theme in result.themes for keyword in theme.keywords),
                *(quote.text for theme in result.themes for quote in theme.quotes),
            ]
        ).lower()

        self.assertGreater(result.quote_count, 0)
        self.assertNotIn("farsides", generated)
        self.assertNotIn("transcript of interview", generated)
        self.assertNotIn("autism and kindness", generated)

    def test_pdf_url_footnotes_are_removed_from_quote_units(self):
        transcript = """
1 Perhaps means Galatians, Ch. 5 (https://example.org/source): 22 But the fruit of the Spirit is love and kindness.
Interviewee 7: Friends showed kindness by checking whether sensory overwhelm was building.
Interviewee 7: Practical support helped me communicate needs before distress increased.
""".strip()

        quotes = extract_quote_units(parse_transcript(transcript))
        generated = " ".join(quote.text for quote in quotes).lower()

        self.assertTrue(quotes)
        self.assertNotIn("https://", generated)
        self.assertNotIn("galatians", generated)

    def test_backchannels_are_not_quote_units(self):
        transcript = """
Participant 1: Okay, that's fine. Thank you. No, that's okay.
Participant 1: Shared planning helped staff adapt curriculum activities for adults.
""".strip()

        quotes = extract_quote_units(parse_transcript(transcript))

        self.assertEqual(
            [quote.text for quote in quotes],
            ["Shared planning helped staff adapt curriculum activities for adults."],
        )


if __name__ == "__main__":
    unittest.main()


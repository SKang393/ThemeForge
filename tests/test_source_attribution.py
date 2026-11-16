"""Small exact-source structural fixtures only; no inference or external data."""
from copy import deepcopy
from hashlib import sha256
import unittest

from themeforge.source_attribution import VERSION, resolve_attribution


def source(text, name='synthetic'):
    return dict(source_name=name, source_hash=sha256(text.encode()).hexdigest(), text=text)


def unit(record, text=None, *, start=None, end=None, **extra):
    start = record['text'].index(text) if start is None else start
    end = start + len(text) if end is None else end
    return dict(id='synthetic-unit', source_name=record['source_name'], source_hash=record['source_hash'],
                source_start=start, source_end=end, text=record['text'][start:end], speaker='Participant 2', **extra)


class SourceAttributionTests(unittest.TestCase):
    def setUp(self):
        self.source = source(
            'Participant 2 Interview Transcription, Synthetic\nInterview conducted: 01/01/2000\n'
            'Interviewer 1: Thank you for joining.\nParticipant 2: You\ufffdre welcome.\n'
            '[redacted closing for privacy]\n'
            'Participant 3 Responses, Synthetic\nPart 1 - Questions About Data\n'
            '1. Please describe the data and\nits format? If it varies, why?\n'
            'We save field data in a secure location.\n'
            '2. How long do you retain it?\nWe keep the data indefinitely.\n'
            'Participant 4 Transcript, Synthetic\nPart 2 - Questions About Sharing\n'
            '1. What do you share?\nWe share processed data only.\n'
            'Interview with Participant 5, Synthetic\nInterview conducted: 02/02/2000')

    def assert_exact(self, value, selected, record):
        self.assertEqual(value['version'], VERSION)
        self.assertFalse(value['participant_count_supported'])
        self.assertFalse(value['respondent_assertion_supported'])
        self.assertEqual(''.join(p['text'] for p in value['partitions']), selected['text'])
        cursor = selected['source_start']
        for partition in value['partitions']:
            self.assertEqual(partition['source_start'], cursor)
            cursor = partition['source_end']
            self.assertEqual(record['text'][partition['source_start']:cursor], partition['text'])
            self.assertFalse(partition['respondent_assertion_supported'])
            for field in ('section_heading_evidence', 'speaker_label_evidence', 'numbered_prompt_line_evidence'):
                evidence = partition[field]
                if evidence is not None:
                    self.assertEqual(record['text'][evidence['source_start']:evidence['source_end']], evidence['text'])
                    self.assertEqual(evidence['source_hash'], record['source_hash'])
        self.assertEqual(cursor, selected['source_end'])

    def test_composite_keeps_saved_label_and_maps_four_sections_without_counting_people(self):
        selected = unit(self.source, start=self.source['text'].index('You\ufffdre welcome.'), end=len(self.source['text']))
        value = resolve_attribution(selected, self.source)
        self.assert_exact(value, selected, self.source)
        self.assertEqual(value['status'], 'multiple_sections')
        self.assertEqual(value['section_labels'], ['Participant 2', 'Participant 3', 'Participant 4', 'Participant 5'])
        self.assertEqual(value['original_saved_label'], 'Participant 2')
        self.assertEqual(value['speaker_attribution'], 'multiple')
        self.assertTrue(any('inherited saved label' in warning for warning in value['warnings']))

    def test_recovered_core_resolves_section_from_heading_before_core_but_not_voice(self):
        selected = unit(self.source, 'We save field data in a secure location.',
                        original_parent_speaker='Participant 2')
        selected['speaker'] = 'Unresolved composite source attribution'
        value = resolve_attribution(selected, self.source)
        self.assert_exact(value, selected, self.source)
        self.assertEqual(value['status'], 'single_section')
        self.assertEqual(value['section_labels'], ['Participant 3'])
        self.assertEqual(value['speaker_attribution'], 'unknown')
        self.assertEqual(value['original_saved_label'], 'Participant 2')
        self.assertEqual(value['input_saved_label'], selected['speaker'])
        self.assertLess(value['sections'][0]['heading_evidence']['source_start'], selected['source_start'])
        self.assertEqual(value['sections'][0]['source_end'], self.source['text'].index('Participant 4 Transcript'))
        self.assertEqual(value['partitions'][0]['content_role'], 'numbered_block_prompt_or_response_unresolved')

    def test_new_written_heading_clears_previous_turn_label(self):
        selected = unit(self.source, 'Participant 3 Responses, Synthetic\nPart 1 - Questions About Data\n')
        value = resolve_attribution(selected, self.source)
        self.assert_exact(value, selected, self.source)
        self.assertEqual(value['source_labeled_speakers'], [])
        self.assertEqual(value['speaker_attribution'], 'unknown')
        self.assertEqual([p['content_role'] for p in value['partitions']], ['section_heading', 'administrative_or_part_heading'])

    def test_wrapped_prompts_and_answers_remain_unresolved_exact_blocks(self):
        begin = self.source['text'].index('1. Please describe')
        end = self.source['text'].index('Participant 4 Transcript')
        selected = unit(self.source, start=begin, end=end)
        value = resolve_attribution(selected, self.source)
        self.assert_exact(value, selected, self.source)
        self.assertEqual(len(value['partitions']), 2)
        self.assertTrue(all(p['content_role'] == 'numbered_block_prompt_or_response_unresolved' for p in value['partitions']))
        self.assertIn('its format?', value['partitions'][0]['text'])
        self.assertIn('We save field data', value['partitions'][0]['text'])
        self.assertEqual(value['speaker_attribution'], 'unknown')

    def test_final_interview_heading_and_date_are_not_participant_speech(self):
        selected = unit(self.source, start=self.source['text'].index('Interview with Participant 5'), end=len(self.source['text']))
        value = resolve_attribution(selected, self.source)
        self.assert_exact(value, selected, self.source)
        self.assertEqual(value['section_labels'], ['Participant 5'])
        self.assertEqual(value['speaker_attribution'], 'unknown')
        self.assertEqual(value['original_saved_label'], 'Participant 2')
        self.assertEqual([p['content_role'] for p in value['partitions']], ['section_heading', 'administrative_or_part_heading'])

    def test_explicit_participant_and_interviewer_labels_are_source_local_only(self):
        record = source('Participant#03: We retain samples.\nInterviewer 2: Why do you retain them?\n')
        for text, label in [('We retain samples.', 'Participant 3'), ('Why do you retain them?', 'Interviewer 2')]:
            with self.subTest(label=label):
                selected = unit(record, text)
                value = resolve_attribution(selected, record)
                self.assert_exact(value, selected, record)
                self.assertEqual(value['status'], 'unknown')
                self.assertEqual(value['speaker_attribution'], label)
                self.assertFalse(value['semantic_validation'])
                self.assertLess(value['partitions'][0]['speaker_label_evidence']['source_start'], selected['source_start'])

    def test_closing_plus_admin_does_not_become_respondent_assertion(self):
        begin = self.source['text'].index('You\ufffdre welcome.')
        end = self.source['text'].index('Participant 3 Responses')
        selected = unit(self.source, start=begin, end=end)
        value = resolve_attribution(selected, self.source)
        self.assert_exact(value, selected, self.source)
        self.assertEqual(value['speaker_attribution'], 'unknown')
        self.assertEqual([p['content_role'] for p in value['partitions']], ['closing_or_conversational_text', 'administrative_or_part_heading'])

    def test_mixed_explicit_speakers_are_not_collapsed(self):
        record = source('Participant 1: We store data.\nInterviewer: Thanks.\nParticipant 2: We do not.\n')
        selected = unit(record, start=0, end=len(record['text']))
        value = resolve_attribution(selected, record)
        self.assert_exact(value, selected, record)
        self.assertEqual(value['speaker_attribution'], 'multiple')
        self.assertEqual(value['source_labeled_speakers'], ['Participant 1', 'Interviewer', 'Participant 2'])

    def test_exact_validation_rejects_stale_or_mismatched_source_and_boolean_offsets(self):
        selected = unit(self.source, 'We share processed data only.')
        cases = [(dict(selected, source_start=True), self.source),
                 (dict(selected, source_hash='0'*64), self.source),
                 (dict(selected, text='invented'), self.source),
                 (selected, dict(self.source, text=self.source['text']+'changed')),
                 (selected, dict(self.source, source_name='another-source'))]
        for chosen, record in cases:
            with self.subTest(chosen=chosen['source_start']), self.assertRaises(ValueError):
                resolve_attribution(chosen, record)

    def test_mapping_is_deterministic_nonmutating_and_preserves_crlf_unicode_whitespace(self):
        record = source('Participant 3 Transcript, Test\r\n1. What?\r\n  Caf\u00e9 data.  \r\n')
        selected = unit(record, start=3, end=len(record['text'])-1)
        originals = deepcopy((selected, record))
        first = resolve_attribution(selected, record)
        self.assert_exact(first, selected, record)
        self.assertEqual(first, resolve_attribution(selected, record))
        self.assertEqual((selected, record), originals)

    def test_same_participant_number_across_sources_is_not_a_person_identity(self):
        left = source('Participant 3 Responses, A\n1. What?\nData A.\n', 'left')
        right = source('Participant 3 Responses, B\n1. What?\nData B.\n', 'right')
        a = resolve_attribution(unit(left, 'Data A.'), left)
        b = resolve_attribution(unit(right, 'Data B.'), right)
        self.assertEqual(a['section_labels'], b['section_labels'])
        self.assertNotEqual(a['sections'][0]['section_id'], b['sections'][0]['section_id'])
        self.assertFalse(a['participant_count_supported'] or b['participant_count_supported'])


if __name__ == '__main__':
    unittest.main()

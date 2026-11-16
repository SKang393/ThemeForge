"""Exact-source card behavior with synthetic transcripts and no inference."""
from copy import deepcopy
from hashlib import sha256
import json
import unittest
from unittest.mock import patch

from themeforge.analysis_types import TranscriptDocument
from themeforge.local_codebook import prepare_codebook, run_synthesis
from themeforge.model_pilot import _digest
from themeforge.source_cards import ARTIFACT_TYPE, VERSION, build_source_cards, validate_cards, export_cards_html


def packet():
    docs = [TranscriptDocument('invented-a', 'Interviewer: Why keep another copy?\r\nP1: I keep measurements on another disk if field equipment fails. 한글 기록 stays intact.\r\nP2: I check that the saved records can still be opened.'),
            TranscriptDocument('invented-b', 'P3: I retain <script>alert(1)</script> in an example record on a separate machine.')]
    return prepare_codebook(docs, max_units=3, speaker_label_policy='explicit_roles')


def seal(cards):
    cards['cards_sha256'] = _digest({key: value for key, value in cards.items() if key != 'cards_sha256'})
    return cards


class SourceCardTests(unittest.TestCase):
    def test_prepared_cards_preserve_exact_selected_turns_spans_hashes_and_context(self):
        prepared = packet(); original = deepcopy(prepared)
        cards = build_source_cards(prepared)
        self.assertEqual(prepared, original)
        self.assertEqual(cards['version'], VERSION)
        self.assertEqual(cards['artifact_type'], ARTIFACT_TYPE)
        self.assertEqual(cards['selected_unit_ids'], prepared['selected_unit_ids'])
        self.assertEqual(len(cards['cards']), 3)
        sources = {row['name']: row for row in prepared['source_documents']}
        units = {unit['id']: unit for unit in prepared['unit_archive']}
        for card in cards['cards']:
            source = sources[card['source_name']]
            unit = units[card['unit_id']]
            self.assertEqual(card['text'], source['text'][card['source_start']:card['source_end']])
            self.assertEqual(card['text'].encode('utf-8'), unit['text'].encode('utf-8'))
            self.assertEqual(card['source_hash'], sha256(source['text'].encode('utf-8')).hexdigest())
            context = card['context']
            self.assertEqual(context['text'], source['text'][context['source_start']:context['source_end']])
            self.assertEqual(context['reference_context'], unit['reference_context'])
            self.assertEqual(context['role'], 'context_not_additional_support')
            for span in card['support_spans']:
                self.assertEqual({key: value for key, value in span.items() if key != 'span_id'}, prepared['registry'][span['span_id']])
        self.assertEqual(cards['source_documents'][0]['text'].encode('utf-8'), prepared['source_documents'][0]['text'].encode('utf-8'))

    def test_generated_result_cards_drop_model_wording_without_touching_original_failures(self):
        prepared = packet()
        result = {key: deepcopy(prepared[key]) for key in ('version', 'source_documents', 'unit_archive', 'registry', 'selected_unit_ids', 'speaker_label_policy')}
        sid, span = next(iter(result['registry'].items()))
        result.update(codes=[dict(id='LC-synthetic', unit_id=span['unit_id'], support_span_ids=[sid], evidence=[deepcopy(span)],
            qualifications=[], counterexamples=[], label='MODEL_LABEL_SENTINEL', claim='MODEL_CLAIM_SENTINEL', definition='MODEL_DEFINITION_SENTINEL', status='definition_drafted')],
            calls=[{'stage': 'decision', 'status': 'contract_rejected', 'raw': 'RAW_FAILURE_SENTINEL'}], categories=[], interpretive_themes=[])
        result['result_sha256'] = _digest(result)
        before = deepcopy(result)
        with (patch('themeforge.local_inference.LlamaServerClient.start', side_effect=AssertionError('No model startup')),
              patch('themeforge.local_inference.LlamaServerClient.generate', side_effect=AssertionError('No model call'))):
            cards = build_source_cards(result)
        self.assertEqual(result, before)
        self.assertEqual(cards['calls'], [])
        self.assertEqual(cards['input_artifact']['kind'], 'generated_result')
        self.assertEqual(cards['input_artifact']['sha256'], result['result_sha256'])
        encoded = json.dumps(cards)
        for sentinel in ('MODEL_LABEL_SENTINEL', 'MODEL_CLAIM_SENTINEL', 'MODEL_DEFINITION_SENTINEL', 'RAW_FAILURE_SENTINEL'):
            self.assertNotIn(sentinel, encoded)

    def test_standalone_json_roundtrip_is_nonconceptual_and_has_no_ai_or_human_authorship_claim(self):
        cards = build_source_cards(packet())
        raw = json.dumps(cards, ensure_ascii=False).encode('utf-8')
        restored = validate_cards(raw)
        self.assertEqual(restored, cards)
        self.assertFalse(restored['exhaustive'])
        self.assertFalse(restored['ai_generated_interpretation'])
        self.assertFalse(restored['human_coding'])
        self.assertIsNone(restored['interpretation_authorship'])
        for card in restored['cards']:
            self.assertEqual(card['origin'], 'host_exact_source')
            self.assertEqual(card['kind'], 'source_card')
            self.assertTrue(card['card_id'].startswith('SC-'))
            self.assertNotIn('id', card)
            self.assertIsNone(card['conceptual_label'])
            self.assertIsNone(card['definition'])
        self.assertFalse({'codes', 'categories', 'interpretive_themes'} & set(restored))
        restored['cards'][0]['text'] = 'Changed copy'
        self.assertNotEqual(restored, cards)

    def test_digest_source_hash_and_original_input_tampering_fail_closed(self):
        prepared = packet()
        bad_input = deepcopy(prepared); bad_input['source_documents'][0]['text'] += ' Added words.'
        with self.assertRaises(ValueError):
            build_source_cards(bad_input)
        cards = build_source_cards(prepared)
        changed = deepcopy(cards); changed['cards'][0]['text'] += ' Invented.'
        with self.assertRaisesRegex(ValueError, 'digest mismatch'):
            validate_cards(changed)
        changed = deepcopy(cards); changed['source_documents'][0]['text'] += ' Invented.'; seal(changed)
        with self.assertRaisesRegex(ValueError, 'hash'):
            validate_cards(changed)

    def test_resealed_context_registry_and_conceptual_field_tampering_is_rejected(self):
        cards = build_source_cards(packet())
        for mutate in (lambda c: c['cards'][0]['context'].update(text='Invented context'),
                       lambda c: c['cards'][0].update(source_start=c['cards'][0]['source_start'] + 1),
                       lambda c: c['cards'][0].update(definition='A purported conceptual definition.'),
                       lambda c: c['cards'][0].update(conceptual_label='A theme'),
                       lambda c: c['registry'].pop(next(iter(c['registry']))),
                       lambda c: c['selected_unit_ids'].append(c['selected_unit_ids'][0]),
                       lambda c: c.update(codes=[])):
            changed = deepcopy(cards); mutate(changed); seal(changed)
            with self.subTest(changed_fields=list(changed)), self.assertRaises(ValueError):
                validate_cards(changed)

    def test_source_cards_are_ineligible_for_native_grouping_before_any_client_start(self):
        class NoClient:
            def start(self, *args):
                raise AssertionError('Cards must not reach inference.')
            def generate(self, *args):
                raise AssertionError('Cards must not reach inference.')
        cards = build_source_cards(packet())
        with self.assertRaises(ValueError):
            run_synthesis(cards, NoClient(), [row['card_id'] for row in cards['cards'][:2]])
        with self.assertRaises(ValueError):
            validate_cards(packet())

    def test_export_html_escapes_sources_and_marks_nonconceptual_origin_context_and_offsets(self):
        cards = build_source_cards(packet())
        html = export_cards_html(cards)
        self.assertNotIn('<script>', html)
        self.assertIn('&lt;script&gt;', html)
        self.assertIn('host_exact_source', html)
        self.assertIn('not conceptual codes', html)
        self.assertIn('Context, not additional supporting evidence', html)
        self.assertIn('Content-Security-Policy', html)
        for card in cards['cards']:
            self.assertIn(card['source_hash'], html)
            self.assertIn(f"characters {card['source_start']}:{card['source_end']}", html)
        self.assertEqual(validate_cards(json.dumps(cards, ensure_ascii=False)), cards)

    def test_duplicate_keys_nonstandard_json_and_wrong_artifact_types_rejected(self):
        for raw in ('{"version":"x","version":"y"}', '{"calls":NaN}', '[]', 'null', b'\xff', 3):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                validate_cards(raw)


if __name__ == '__main__':
    unittest.main()

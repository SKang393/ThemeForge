"""Actual withdrawn source-card widgets; no model calls or test output studies."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import tkinter as tk
import unittest
from unittest.mock import patch

from themeforge.analysis_types import TranscriptDocument
from themeforge.local_codebook import prepare_codebook
from themeforge.model_pilot import _digest
from themeforge.source_cards import build_source_cards
from themeforge.source_card_dialog import SourceCardDialog


def fixture():
    docs = [TranscriptDocument('invented', 'Interviewer: Why keep another copy?\r\nP1: I retain records on another disk if equipment fails. 한글 remains exact.\r\nP2: I check whether the saved files can still be opened.')]
    return build_source_cards(prepare_codebook(docs, max_units=2, speaker_label_policy='explicit_roles'))


class SourceCardDialogTests(unittest.TestCase):
    def setUp(self):
        self.app = tk.Tk(); self.app.withdraw()
        self.cards = fixture()
        self.dialog = SourceCardDialog(self.app, self.cards); self.dialog.withdraw()
        self.temp = tempfile.TemporaryDirectory(prefix='source-card-ui-')
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
    def tearDown(self):
        self.app.destroy()

    def test_actual_readonly_rows_show_exact_turn_hash_offsets_context_and_no_coding_actions(self):
        self.app.update()
        self.assertEqual(len(self.dialog.card_tree.get_children()), 2)
        for card in self.cards['cards']:
            self.dialog.card_tree.selection_set(card['card_id']); self.app.update()
            text = self.dialog.detail.get('1.0', 'end-1c')
            self.assertIn(card['text'], text)
            self.assertIn(card['source_hash'], text)
            self.assertIn(f"Characters {card['source_start']}:{card['source_end']}", text)
            self.assertIn(card['context']['text'], text)
            self.assertIn(card['context']['reference_context'], text)
            self.assertIn('host_exact_source', text)
        self.assertEqual(str(self.dialog.detail['state']), 'disabled')
        self.assertFalse(hasattr(self.dialog, 'category_button'))
        self.assertFalse(hasattr(self.dialog, 'reviewed'))
        self.assertEqual(self.dialog.cards, self.cards)
        self.assertEqual(self.app.state(), 'withdrawn')
        self.assertEqual(self.dialog.state(), 'withdrawn')

    def test_standalone_load_and_fresh_exports_preserve_original_file_and_create_no_client(self):
        path = self.directory / 'original.json'
        raw = json.dumps(self.cards, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        path.write_bytes(raw)
        self.dialog.destroy()
        with (patch('themeforge.local_inference.LlamaServerClient', side_effect=AssertionError('No model client')),
              patch('subprocess.Popen', side_effect=AssertionError('No process')),
              patch('webbrowser.open', side_effect=AssertionError('No browser'))):
            self.dialog = SourceCardDialog(self.app); self.dialog.withdraw()
            self.assertTrue(self.dialog.load(path))
            self.assertTrue(self.dialog.export('json', self.directory / 'roundtrip.json'))
            self.assertTrue(self.dialog.export('html', self.directory / 'review.html'))
        self.assertEqual(self.dialog.cards, self.cards)
        self.assertEqual(json.loads((self.directory / 'roundtrip.json').read_text(encoding='utf-8')), self.cards)
        self.assertIn('Exact-source review cards', (self.directory / 'review.html').read_text(encoding='utf-8'))
        self.assertEqual(path.read_bytes(), raw)
        self.assertFalse(self.dialog.export('json', path))
        self.assertEqual(path.read_bytes(), raw)
        self.assertEqual(self.dialog.loaded_path, path.resolve())

    def test_invalid_or_resealed_source_tampering_keeps_current_artifact_and_rows(self):
        path = self.directory / 'tampered.json'
        original = deepcopy(self.dialog.cards)
        for rehash in (False, True):
            invalid = deepcopy(self.cards)
            invalid['cards'][0]['text'] = 'An invented source passage.'
            if rehash:
                invalid['cards_sha256'] = _digest({key: value for key, value in invalid.items() if key != 'cards_sha256'})
            path.write_text(json.dumps(invalid), encoding='utf-8')
            self.assertFalse(self.dialog.load(path))
            self.assertEqual(self.dialog.cards, original)
            self.assertEqual(len(self.dialog.card_tree.get_children()), len(original['cards']))
        path.write_text('[]', encoding='utf-8')
        self.assertFalse(self.dialog.load(path))
        self.assertEqual(self.dialog.cards, original)

    def test_local_file_cap_remote_paths_and_cancelled_picker_fail_without_mutation(self):
        path = self.directory / 'oversize.json'
        with path.open('wb') as stream:
            stream.truncate(16 * 1024 * 1024 + 1)
        self.assertFalse(self.dialog.load(path))
        self.assertIn('exceeds 16 MiB', self.dialog.status.get())
        for remote in ('https://example.invalid/cards.json', 'http://example.invalid/cards.json', r'\\server\share\cards.json', 'relative.json'):
            self.assertFalse(self.dialog.load(remote))
        with patch('themeforge.source_card_dialog.filedialog.askopenfilename', return_value=''):
            self.assertFalse(self.dialog.load())
        self.assertEqual(self.dialog.cards, self.cards)
        self.assertFalse(self.dialog.export('csv', self.directory / 'unused.csv'))
        self.assertFalse((self.directory / 'unused.csv').exists())

    def test_constructor_and_validation_return_independent_source_copies(self):
        before = deepcopy(self.dialog.cards)
        self.cards['cards'][0]['text'] = 'Changed caller copy'
        self.assertEqual(self.dialog.cards, before)
        invalid = deepcopy(before); invalid['calls'] = [{'stage': 'synthetic'}]
        with self.assertRaises(ValueError):
            SourceCardDialog(self.app, invalid)
        self.assertEqual(self.dialog.cards, before)

    def test_json_export_limit_is_checked_in_utf8_bytes_before_file_creation(self):
        target = self.directory / 'bounded.json'
        encoded = (json.dumps(self.cards, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf-8')
        self.assertGreater(len(encoded), len(encoded.decode('utf-8')))
        before = deepcopy(self.dialog.cards)
        with patch('themeforge.source_card_dialog.MAX_FILE_BYTES', len(encoded) - 1):
            self.assertFalse(self.dialog.export('json', target))
            self.assertFalse(target.exists())
            self.assertIn('reopen limit', self.dialog.status.get())
            self.assertEqual(self.dialog.cards, before)
        with patch('themeforge.source_card_dialog.MAX_FILE_BYTES', len(encoded)):
            self.assertTrue(self.dialog.export('json', target))
            self.assertEqual(target.read_bytes(), encoded)
            self.assertTrue(self.dialog.load(target))
        self.assertEqual(self.dialog.cards, before)


if __name__ == '__main__':
    unittest.main()

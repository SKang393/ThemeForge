"""Actual Tk entry points for source-only review; invented transcripts only."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import tkinter as tk
import unittest
from unittest.mock import patch

from themeforge.analysis_types import TranscriptDocument
from themeforge.local_codebook import prepare_codebook, run_codebook
from themeforge.local_codebook_dialog import LocalCodebookDialog
from themeforge.source_cards import build_source_cards


class AbstainClient:
    def start(self, *_):
        pass
    def close(self):
        pass
    def generate(self, *_):
        return {'raw': '{"decision":"abstain","reason":"Synthetic source-only integration fixture."}'}


class SourceCardEntryTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.docs = [TranscriptDocument('invented-selection',
            'Interviewer: How are copies kept?\nP1: I retain a second file if the recorder fails.\nP2: I might ask a colleague to check the copy.')]
        self.dialogs = []

    def tearDown(self):
        for dialog in self.dialogs:
            if dialog.winfo_exists():
                dialog.request_close()
        self.root.update()
        self.root.destroy()

    def dialog(self, documents=()):
        dialog = LocalCodebookDialog(self.root, documents, speaker_label_policy='explicit_roles',
            client_factory=lambda *_: self.fail('Source review created an inference client'))
        dialog.withdraw()
        self.dialogs.append(dialog)
        return dialog

    def test_current_selection_is_used_without_profile_research_questions_or_code_mutation(self):
        prepared = prepare_codebook(self.docs, max_units=2, speaker_label_policy='explicit_roles', prompt_variant='grounded-v6')
        dialog = self.dialog(self.docs)
        dialog.max_units.set(1)
        dialog.set_target_units([prepared['selected_unit_ids'][1]])
        dialog.mode.set('guided')  # Empty RQ is irrelevant to exact source review.
        with patch('themeforge.local_inference.LlamaServerClient.start', side_effect=AssertionError('No inference')):
            view = dialog.open_source_cards()
        self.assertIsNotNone(view)
        view.withdraw()
        self.assertEqual(view.cards['selected_unit_ids'], [prepared['selected_unit_ids'][1]])
        self.assertIsNone(dialog.result)
        self.assertEqual(dialog._codes, {})
        self.assertIsNone(dialog._thread)
        self.assertIn('No model', dialog.status.get())
        view.destroy()

    def test_loaded_draft_can_supply_cards_without_mutating_calls_or_review_state(self):
        prepared = prepare_codebook(self.docs, max_units=1, speaker_label_policy='explicit_roles', prompt_variant='grounded-v6')
        result = run_codebook([], AbstainClient(), prepared=prepared)
        before = deepcopy(result)
        dialog = self.dialog()
        dialog.set_result(result)
        view = dialog.open_source_cards()
        self.assertIsNotNone(view)
        view.withdraw()
        self.assertEqual(dialog.result, before)
        self.assertFalse(dialog.reviewed.get())
        self.assertEqual(view.cards['calls'], [])
        self.assertIsNone(view.cards['interpretation_authorship'])
        view.destroy()

    def test_standalone_source_cards_open_from_empty_project_and_stay_out_of_codes(self):
        prepared = prepare_codebook(self.docs, max_units=1, speaker_label_policy='explicit_roles')
        cards = build_source_cards(prepared)
        root = Path(__file__).resolve().parents[1] / 'tmp'
        root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root, prefix='source-card-entry-') as directory:
            path = Path(directory) / 'cards.json'
            path.write_text(json.dumps(cards), encoding='utf-8')
            original = path.read_bytes()
            dialog = self.dialog()
            view = dialog.load_source_cards(path)
            self.assertIsNotNone(view)
            view.withdraw()
            self.assertEqual(view.cards, cards)
            self.assertIsNone(dialog.result)
            self.assertEqual(dialog.code_tree.get_children(), ())
            self.assertEqual(path.read_bytes(), original)
            view.destroy()

    def test_busy_and_empty_paths_do_not_start_review_or_model_work(self):
        dialog = self.dialog()
        self.assertIsNone(dialog.open_source_cards())
        dialog._busy = True
        self.assertIsNone(dialog.open_source_cards())
        self.assertIsNone(dialog.load_source_cards('unused.json'))
        self.assertIsNone(dialog._thread)
        dialog._busy = False

    def test_invalid_passage_count_is_reported_without_a_callback_error(self):
        dialog = self.dialog(self.docs)
        dialog.max_units.set('not a number')
        self.assertIsNone(dialog.open_source_cards())
        self.assertIn('Source-only review failed:', dialog.status.get())
        self.assertIsNone(dialog.result)
        self.assertIsNone(dialog._thread)


if __name__ == '__main__':
    unittest.main()

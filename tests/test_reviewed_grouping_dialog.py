"""Real withdrawn Tk authoring controls, synthetic sources and zero inference."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from themeforge.app import ThemeForgeApp
from themeforge.local_codebook import run_synthesis
from themeforge.model_pilot import _digest
from tests.test_category_evidence import Client, native


class HiddenApp(ThemeForgeApp):
    def _build_layout(self):
        self.withdraw()
        super()._build_layout()


class ReviewedGroupingDialogTests(unittest.TestCase):
    def setUp(self):
        self.app = HiddenApp()
        self.app.update()
        self.dialog = self.app.open_local_codebook()
        self.dialog.withdraw()
        self.client_attempts = []

        def no_client(*args, **kwargs):
            self.client_attempts.append(True)
            raise AssertionError('Authoring must not construct an inference client.')

        self.dialog._client_factory = no_client
        self.config_guard = patch.object(self.dialog, '_configuration', side_effect=AssertionError('No model configuration is needed.'))
        self.config_guard.start()
        self.base = native(4)
        self.ids = [c['id'] for c in self.base['codes']]
        self.refusal = run_synthesis(self.base, Client(no_group=True), self.ids[:2],
                                    reviewer_kind='assistant_development_review')
        self.dialog.set_result(self.refusal)
        self.app.update()

    def tearDown(self):
        self.config_guard.stop()
        self.app.destroy()

    def select(self, ids):
        self.dialog.code_tree.selection_set(*ids)
        self.dialog._selection_changed()
        self.dialog.reviewed.set(True)
        self.dialog._update_actions()

    def fill(self, editor, label='Copies as a recovery resource'):
        editor.label_entry.insert(0, label)
        editor.shared_text.insert('1.0', 'The selected accounts connect keeping another copy with continuity of work, in different situations.')
        for index, widget in enumerate(editor.member_notes.values(), 1):
            widget.insert('1.0', f'Member {index} contributes its own copy-keeping account; the circumstance must stay specific to this source.')
        editor.limits_text.insert('1.0', 'These selected accounts do not establish a universal practice or identical risks; member evidence remains separate.')
        editor.reason_text.insert('1.0', 'Explicit synthetic AI review action to exercise authoring, not a study finding or model success.')

    def test_review_gate_blank_validation_and_cancel_never_mutate_refusal(self):
        original = deepcopy(self.dialog.result)
        self.assertIsNone(self.dialog.open_reviewed_category(reviewer_kind='assistant_development_review'))
        self.select(self.ids[:2])
        self.assertEqual(str(self.dialog.review_category_button['state']), 'normal')
        self.assertEqual(str(self.dialog.category_button['state']), 'disabled')
        editor = self.dialog.open_reviewed_category(reviewer_kind='assistant_development_review')
        self.assertIn('assistant_development_review', editor.author_notice)
        self.assertEqual(editor.state(), 'withdrawn')
        for cid, preview in editor.source_previews.items():
            source = next(c for c in self.base['codes'] if c['id'] == cid)['evidence'][0]
            self.assertIn(source['text'], preview.get('1.0', 'end-1c'))
            self.assertIn(source['source_hash'], preview.get('1.0', 'end-1c'))
            self.assertIn(source['unit_text'], preview.get('1.0', 'end-1c'))
            self.assertIn(source['context_text'], preview.get('1.0', 'end-1c'))
        editor.apply_button.invoke()
        self.assertTrue(editor.error_text.get())
        self.assertEqual(self.dialog.result, original)
        self.fill(editor)
        editor.cancel_button.invoke()
        self.assertEqual(self.dialog.result, original)
        self.assertEqual(self.client_attempts, [])

    def test_two_authored_categories_survive_native_export_reopen_and_reject_changed_quote(self):
        original = deepcopy(self.refusal)
        self.select(self.ids[:2])
        editor = self.dialog.open_reviewed_category(reviewer_kind='assistant_development_review')
        self.fill(editor)
        editor.apply_button.invoke()
        self.assertFalse(editor.winfo_exists())
        first = deepcopy(self.dialog.result)
        self.assertEqual(len(first['reviewed_categories']), 1)
        self.assertEqual(first['reviewed_categories'][0]['origin'], 'assistant_development_review')
        self.assertFalse(self.dialog.reviewed.get())
        for key, value in original.items():
            if key != 'result_sha256':
                self.assertEqual(first[key], value, key)
        self.assertIn(original['synthesis_reason'], self.dialog.categories.get('1.0', 'end-1c'))
        self.assertIn('Copies as a recovery resource', self.dialog.review_categories.get('1.0', 'end-1c'))
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1] / 'tmp') as folder:
            folder = Path(folder)
            self.assertTrue(self.dialog.export('json', folder / 'first.json'))
            self.assertEqual((folder / 'first.json').read_bytes(),
                             json.dumps(first, ensure_ascii=False, indent=2, allow_nan=False).encode('utf-8'))
            self.assertTrue(self.dialog.export('html', folder / 'first.html'))
            html = (folder / 'first.html').read_text(encoding='utf-8')
            self.assertIn('assistant_development_review', html)
            self.assertIn('Copies as a recovery resource', html)
            self.assertIn(original['synthesis_reason'], html)
            self.assertTrue(self.dialog.load_result(folder / 'first.json'))
            self.assertEqual(self.dialog.result, first)
            self.select(self.ids[2:])
            editor = self.dialog.open_reviewed_category(reviewer_kind='assistant_development_review')
            self.fill(editor, 'Different circumstances of retaining copies')
            editor.apply_button.invoke()
            second = deepcopy(self.dialog.result)
            self.assertEqual(len(second['reviewed_categories']), 2)
            self.assertEqual(second['reviewed_categories'][0], first['reviewed_categories'][0])
            self.assertEqual(second['calls'], original['calls'])
            self.assertEqual(second['synthesis_status'], 'no_group')
            self.assertTrue(self.dialog.export('json', folder / 'second.json'))
            self.assertTrue(self.dialog.load_result(folder / 'second.json'))
            tampered = deepcopy(second)
            tampered['reviewed_categories'][0]['members'][0]['evidence'][0]['text'] += ' invented extra words'
            tampered['result_sha256'] = _digest({k: v for k, v in tampered.items() if k != 'result_sha256'})
            (folder / 'tampered.json').write_text(json.dumps(tampered), encoding='utf-8')
            with patch('tkinter.messagebox.showerror'):
                self.assertFalse(self.dialog.load_result(folder / 'tampered.json'))
            self.assertEqual(self.dialog.result, second)
        self.assertIsNone(self.dialog._thread)
        self.assertEqual(self.client_attempts, [])
        self.assertEqual(self.refusal, original)

    def test_oversized_native_export_refuses_before_creating_a_file(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1] / 'tmp') as folder:
            path = Path(folder) / 'too-large.json'
            with patch('themeforge.local_codebook_dialog.json.dumps', return_value='x' * (16 * 1024**2 + 1)):
                self.assertFalse(self.dialog.export('json', path))
            self.assertFalse(path.exists())
            self.assertIn('16 MiB reopen limit', self.dialog.status.get())

    def test_referent_context_preview_preserves_the_exact_prior_object(self):
        from themeforge.local_codebook import run_codebook
        from tests.test_multicode_grounding import MultipleClient, packet
        from themeforge.reviewed_grouping_dialog import source_context_text
        result = run_codebook([], MultipleClient(), prepared=packet())
        code = result['codes'][0]
        self.assertTrue(code['referent_context'])
        text = source_context_text(code)
        for span in code['referent_context']:
            self.assertIn(span['text'], text)
            self.assertIn(span['role'], text)
            self.assertIn(span['source_hash'], text)
        self.assertIn('not additional support', text)

    def test_stale_parent_editor_is_rejected_before_apply(self):
        self.select(self.ids[:2])
        editor = self.dialog.open_reviewed_category(reviewer_kind='assistant_development_review')
        self.fill(editor)
        self.dialog.set_result(self.base)
        before = deepcopy(self.dialog.result)
        editor.apply_button.invoke()
        self.assertIn('parent draft changed', editor.error_text.get())
        self.assertEqual(self.dialog.result, before)
        self.assertEqual(self.client_attempts, [])


if __name__ == '__main__':
    unittest.main()

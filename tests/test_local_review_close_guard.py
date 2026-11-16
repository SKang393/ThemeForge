"""No-GUI regression for unsaved main candidate-review close protection."""
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock, patch
from themeforge.local_codebook_dialog import LocalCodebookDialog as D
from themeforge.review_branch_dialog import close_review_branches

class LocalReviewCloseTests(unittest.TestCase):
    def setUp(self):
        self.obj = D.__new__(D)
        self.obj.tk = Mock()
        self.obj.result = {'codes': [], 'review': 'unsaved change'}
        self.obj._saved_result_fingerprint = None
        self.obj._close_authorized_fingerprint = None
        self.obj._busy = False
        self.obj._thread = None
        self.obj.status = Mock()
        self.obj.winfo_children = lambda: []

    def test_cancel_preserves_result_and_window(self):
        original = deepcopy(self.obj.result)
        with patch('themeforge.local_codebook_dialog.messagebox.askyesnocancel', return_value=None), patch('tkinter.Toplevel.destroy') as destroy:
            self.assertFalse(self.obj.request_close())
        destroy.assert_not_called()
        self.assertEqual(original, self.obj.result)

    def test_discard_closes_only_after_explicit_answer(self):
        with patch('themeforge.local_codebook_dialog.messagebox.askyesnocancel', return_value=False) as question, patch('tkinter.Toplevel.destroy') as destroy:
            self.assertTrue(self.obj.request_close())
        question.assert_called_once(); destroy.assert_called_once()

    def test_successful_json_save_then_close(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'review.json'
            with patch('themeforge.local_codebook_dialog.messagebox.askyesnocancel', return_value=True), patch('themeforge.local_codebook_dialog.filedialog.asksaveasfilename', return_value=str(path)), patch('tkinter.Toplevel.destroy') as destroy:
                self.assertTrue(self.obj.request_close())
            self.assertTrue(path.exists()); destroy.assert_called_once()
            self.assertEqual(self.obj._saved_result_fingerprint, self.obj._result_fingerprint())

    def test_cancelled_save_picker_prevents_close(self):
        with patch('themeforge.local_codebook_dialog.messagebox.askyesnocancel', return_value=True), patch('themeforge.local_codebook_dialog.filedialog.asksaveasfilename', return_value=''), patch('tkinter.Toplevel.destroy') as destroy:
            self.assertFalse(self.obj.request_close())
        destroy.assert_not_called()

    def test_failed_save_prevents_close_preserves_existing_file(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'review.json'; path.write_text('original')
            with patch('themeforge.local_codebook_dialog.messagebox.askyesnocancel', return_value=True), patch('themeforge.local_codebook_dialog.filedialog.asksaveasfilename', return_value=str(path)), patch('tkinter.Toplevel.destroy') as destroy:
                self.assertFalse(self.obj.request_close())
            self.assertEqual(path.read_text(), 'original'); destroy.assert_not_called()

    def test_saved_state_closes_without_prompt_but_edit_prompts(self):
        self.obj._saved_result_fingerprint = self.obj._result_fingerprint()
        with patch('themeforge.local_codebook_dialog.messagebox.askyesnocancel', return_value=None) as question, patch('tkinter.Toplevel.destroy'):
            self.assertTrue(self.obj.request_close()); question.assert_not_called()
            self.obj.result['review'] = 'changed again'
            self.assertFalse(self.obj.request_close()); question.assert_called_once()

    def test_html_export_does_not_mark_reopenable_save(self):
        self.obj._api = lambda: Mock(export_codebook_html=lambda _: '<html>review</html>')
        with tempfile.TemporaryDirectory() as root:
            self.assertTrue(self.obj.export('html', Path(root) / 'report.html'))
        self.assertIsNone(self.obj._saved_result_fingerprint)

    def test_direct_destroy_guarded(self):
        with patch('themeforge.local_codebook_dialog.messagebox.askyesnocancel', return_value=None), patch('tkinter.Toplevel.destroy') as destroy:
            self.assertFalse(self.obj.destroy())
        destroy.assert_not_called()

    def test_parent_close_traversal_honors_main_review_cancel(self):
        owner = Mock(); owner.winfo_children.return_value = [self.obj]
        with patch('themeforge.local_codebook_dialog.messagebox.askyesnocancel', return_value=None), patch('tkinter.Toplevel.destroy') as destroy:
            self.assertFalse(close_review_branches(owner))
        destroy.assert_not_called()

    def test_unserializable_review_fails_closed(self):
        self.obj.result = {'invalid': object()}
        with patch('tkinter.Toplevel.destroy') as destroy:
            self.assertFalse(self.obj.request_close())
        destroy.assert_not_called(); self.obj.status.set.assert_called_once()

    def test_nested_cancel_does_not_authorize_later_discard(self):
        with patch('themeforge.local_codebook_dialog.messagebox.askyesnocancel', return_value=False), patch('themeforge.review_branch_dialog.close_review_branches', return_value=False), patch('tkinter.Toplevel.destroy') as destroy:
            self.assertFalse(self.obj.request_close())
        destroy.assert_not_called()
        self.assertIsNone(self.obj._close_authorized_fingerprint)
        with patch('themeforge.local_codebook_dialog.messagebox.askyesnocancel', return_value=None) as question, patch('tkinter.Toplevel.destroy') as destroy:
            self.assertFalse(self.obj.request_close())
        question.assert_called_once(); destroy.assert_not_called()

    def test_changed_review_json_reopens_exactly(self):
        from themeforge.main_review_qa import make_controller
        from tests.test_main_codebook_review import fictional_draft
        draft = fictional_draft()
        controller = make_controller(draft)
        self.assertTrue(controller.apply_candidate_review(draft['codes'][0]['id'],
            {'claim': 'A reported tutor wish depends on transport; the outcome remains unknown.'},
            'edit', expected_digest=draft['result_sha256'],
            reviewer_kind='assistant_development_review', reviewer_name='Synthetic test',
            reason='Preserve actor, condition and unknown outcome.'))
        self.obj.result = deepcopy(controller.result)
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'review.json'
            with patch('themeforge.local_codebook_dialog.messagebox.askyesnocancel', return_value=True), patch('themeforge.local_codebook_dialog.filedialog.asksaveasfilename', return_value=str(path)), patch('tkinter.Toplevel.destroy'):
                self.assertTrue(self.obj.request_close())
            reopened = make_controller()
            self.assertTrue(reopened.load_result(path), reopened.status.get())
            self.assertEqual(reopened.result, controller.result)
            self.assertEqual(reopened._saved_result_fingerprint, D._result_fingerprint(reopened))

    def test_busy_close_cancellation_waits_for_worker(self):
        self.obj._busy = True; self.obj.cancel = Mock()
        with patch('themeforge.local_codebook_dialog.messagebox.askyesnocancel', return_value=False), patch('tkinter.Toplevel.destroy') as destroy:
            self.assertFalse(self.obj.request_close())
        self.obj.cancel.assert_called_once(); destroy.assert_not_called(); self.assertTrue(self.obj._closing)

if __name__ == '__main__': unittest.main()

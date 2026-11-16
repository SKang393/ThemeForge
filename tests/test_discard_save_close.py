import unittest
from copy import deepcopy
from unittest.mock import Mock,patch
import test_branch_navigation_guard as navigation
from themeforge.review_branch_dialog import ReviewBranchDialog as D

class DiscardSaveCloseTests(unittest.TestCase):
 setUp=navigation.NavigationGuardTests.setUp
 def prepare(self):
  self.obj.editors['label'].value='Unapplied wording'
  self.obj.require_branch=lambda:None
  self.obj.notice=Mock();self.obj.destroy=Mock();self.obj.saved_branch_sha256=None
  self.obj.save=lambda **kw:D.save(self.obj,**kw)
 def test_discard_wording_then_save_applied_branch(self):
  self.prepare();before=deepcopy(self.obj.branch)
  with patch('themeforge.review_branch_dialog.messagebox.askyesno',return_value=True),patch('themeforge.review_branch_dialog.messagebox.askyesnocancel',return_value=True),patch('themeforge.review_branch_dialog.filedialog.asksaveasfilename',return_value='new.json'),patch('themeforge.review_branch_dialog.save_branch') as save:
   self.assertTrue(D.request_close(self.obj));save.assert_called_once_with(before,'new.json')
  self.assertEqual(self.obj.branch,before);self.obj.destroy.assert_called_once()
 def test_cancel_picker_preserves_pending_wording(self):
  self.prepare()
  with patch('themeforge.review_branch_dialog.messagebox.askyesno',return_value=True),patch('themeforge.review_branch_dialog.messagebox.askyesnocancel',return_value=True),patch('themeforge.review_branch_dialog.filedialog.asksaveasfilename',return_value=''):
   self.assertFalse(D.request_close(self.obj))
  self.obj.destroy.assert_not_called();self.assertEqual(self.obj.editors['label'].value,'Unapplied wording')
 def test_failed_save_preserves_pending_wording(self):
  self.prepare()
  with patch('themeforge.review_branch_dialog.messagebox.askyesno',return_value=True),patch('themeforge.review_branch_dialog.messagebox.askyesnocancel',return_value=True),patch('themeforge.review_branch_dialog.filedialog.asksaveasfilename',return_value='new.json'),patch('themeforge.review_branch_dialog.save_branch',side_effect=OSError('disk full')):
   with self.assertRaises(OSError):D.request_close(self.obj)
  self.obj.destroy.assert_not_called();self.assertIsNone(self.obj.saved_branch_sha256)
  self.assertEqual(self.obj.editors['label'].value,'Unapplied wording')

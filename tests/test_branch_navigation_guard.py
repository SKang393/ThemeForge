import unittest
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock,patch
from test_codebook_review import fixture
from themeforge.review_branch import begin_branch
from themeforge.review_branch_dialog import ReviewBranchDialog as D
class Text:
 def __init__(self,value):self.value=value
 def get(self,*args):return self.value
 def set(self,value):self.value=value
class NavigationGuardTests(unittest.TestCase):
 def setUp(self):
  source,_=fixture()
  self.obj=SimpleNamespace(branch=begin_branch(source,reviewer_kind='assistant_development_review',reviewer_name='Offline test',reason='Synthetic callback test, not human validation.'),choice=Text('LC-synthetic'),displayed_code_id='LC-synthetic',show_code=Mock())
  code=self.obj.branch['draft']['codes'][0]
  self.obj.editors={f:Text(code[f]) for f in ('label','claim','definition')}
 def test_clean_switch_needs_no_prompt(self):
  self.obj.choice.set('another')
  with patch('themeforge.review_branch_dialog.messagebox.askyesno') as ask:
   self.assertTrue(D.select_code(self.obj));ask.assert_not_called()
  self.obj.show_code.assert_called_once()
 def test_cancel_switch_restores_selection_preserves_wording(self):
  self.obj.editors['label'].value='unsaved wording';self.obj.choice.set('another')
  with patch('themeforge.review_branch_dialog.messagebox.askyesno',return_value=False):
   self.assertFalse(D.select_code(self.obj))
  self.assertEqual(self.obj.choice.get(),'LC-synthetic');self.obj.show_code.assert_not_called()
  self.assertEqual(self.obj.editors['label'].value,'unsaved wording')
 def test_explicit_discard_allows_switch(self):
  self.obj.editors['label'].value='unsaved';self.obj.choice.set('another')
  with patch('themeforge.review_branch_dialog.messagebox.askyesno',return_value=True):
   self.assertTrue(D.select_code(self.obj))
  self.obj.show_code.assert_called_once()
 def test_guard_blocks_save_and_actions_without_mutation(self):
  self.obj.editors['claim'].value='pending claim';before=deepcopy(self.obj.branch)
  actions=[lambda:D.save(self.obj),lambda:D.undo(self.obj),lambda:D.decide(self.obj,'accept'),lambda:D.apply_roles(self.obj,'LC-synthetic',{}),lambda:D.apply_split(self.obj,'LC-synthetic',[]),lambda:D.apply_group(self.obj,'categories','unknown',{})]
  for action in actions:
   with self.assertRaisesRegex(ValueError,'Apply your wording'):action()
   self.assertEqual(self.obj.branch,before)
 def test_same_selection_preserves_normal_show(self):
  with patch('themeforge.review_branch_dialog.messagebox.askyesno') as ask:
   D.select_code(self.obj);ask.assert_not_called()

 def test_reselect_same_candidate_keeps_pending_text(self):
  self.obj.editors['label'].value='pending'
  with patch('themeforge.review_branch_dialog.messagebox.askyesno') as ask:
   self.assertFalse(D.select_code(self.obj));ask.assert_not_called()
  self.obj.show_code.assert_not_called()

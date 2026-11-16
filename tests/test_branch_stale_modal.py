"""No-window optimistic-concurrency checks for research review editors."""
import ast,inspect,textwrap,unittest
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock
import test_branch_navigation_guard as navigation
from themeforge.review_branch import revise_branch
from themeforge.review_branch_dialog import ReviewBranchDialog as D

def inner(method,name,bindings):
 tree=ast.parse(textwrap.dedent(inspect.getsource(method)))
 node=next(n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name==name)
 scope=dict(bindings)
 exec(compile(ast.fix_missing_locations(ast.Module(body=[node],type_ignores=[])),'actual-branch-callback','exec'),scope)
 return scope[name]

class StaleModalTests(unittest.TestCase):
 setUp=navigation.NavigationGuardTests.setUp
 def changed(self):
  old=self.obj.branch['branch_sha256']
  self.obj.branch=revise_branch(self.obj.branch,'LC-synthetic',{'label':'new label'},reviewer_kind='assistant_development_review',reviewer_name='Synthetic test',reason='Exercise stale draft guard.')
  return old
 def test_all_mutations_reject_stale_snapshot_without_changing_draft(self):
  old=self.changed();before=deepcopy(self.obj.branch)
  for operation in (lambda:D.apply_roles(self.obj,'LC-synthetic',{},expected_sha256=old),lambda:D.apply_split(self.obj,'LC-synthetic',[],expected_sha256=old),lambda:D.apply_group(self.obj,'categories','g',{},expected_sha256=old)):
   with self.assertRaisesRegex(ValueError,'branch changed'):operation()
   self.assertEqual(self.obj.branch,before)
 def test_current_snapshot_and_no_precondition_allowed(self):
  D.require_snapshot(self.obj,self.obj.branch['branch_sha256']);D.require_snapshot(self.obj,None)
 def test_roles_callback_retains_editor_on_stale_branch(self):
  old=self.changed();window=Mock();notice=Mock()
  self.obj.apply_roles=lambda *a,**kw:D.apply_roles(self.obj,*a,**kw)
  callback=inner(D.open_roles,'apply',dict(self=self.obj,code={'id':'LC-synthetic'},variables={},expected_sha256=old,window=window,note=notice))
  callback();window.destroy.assert_not_called();self.assertIn('branch changed',notice.set.call_args.args[0])
 def test_split_callback_retains_editor_on_stale_branch(self):
  old=self.changed();window=Mock();notice=Mock()
  self.obj.apply_split=lambda *a,**kw:D.apply_split(self.obj,*a,**kw)
  callback=inner(D.open_split,'submit',dict(self=self.obj,code={'id':'LC-synthetic'},entries=[],expected_sha256=old,window=window,notice=notice))
  callback();window.destroy.assert_not_called();self.assertIn('branch changed',notice.set.call_args.args[0])
 def test_group_submission_binds_original_snapshot(self):
  old=self.changed();window=Mock()
  self.obj.apply_group=lambda *a,**kw:D.apply_group(self.obj,*a,**kw)
  callback=inner(D.open_group,'submit',dict(self=self.obj,collection='categories',group={'id':'g'},selected=[],expected_sha256=old,window=window))
  with self.assertRaisesRegex(ValueError,'branch changed'):
   callback({'Relationship label':'label','Shared meaning':'meaning','Limits':'limits'})
  window.destroy.assert_not_called()

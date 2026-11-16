import json
import tempfile
from copy import deepcopy
from pathlib import Path
import unittest
from test_category_evidence import grouped
from themeforge.review_branch import *

class BranchTests(unittest.TestCase):
 def setUp(self):
  self.parent=grouped(); self.saved=deepcopy(self.parent)
  self.author=dict(reviewer_kind='assistant_development_review', reviewer_name='Offline AI test', reason='Check a separate revision, not human validation.')
  self.branch=begin_branch(self.parent, **self.author)
 def test_preserves_original_and_history(self):
  self.assertEqual(self.parent,self.saved)
  self.assertEqual(self.branch['parent'],self.parent)
  self.assertEqual(self.branch['draft']['codes'],self.parent['codes'])
  validate_branch(self.branch)
 def test_edit_invalidates_dependent_group_and_undo(self):
  cid=self.parent['categories'][0]['code_ids'][0]
  revised=revise_branch(self.branch,cid,{'label':'Retaining another recovery copy'},**self.author)
  self.assertEqual(group_states(revised)[0]['status'],'review_required')
  self.assertFalse(group_states(revised)[0]['current_approval'])
  self.assertEqual(undo_revision(revised),self.branch)
  self.assertEqual(revised['parent'],self.saved)
 def test_unchanged_groups_not_new_approval(self):
  self.assertEqual(group_states(self.branch)[0]['status'],'unchanged_historical_snapshot')
  self.assertFalse(group_states(self.branch)[0]['current_approval'])
 def test_reopen_and_no_overwrite(self):
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp)/'branch.json'; save_branch(self.branch,p)
   self.assertEqual(load_branch(p),self.branch)
   with self.assertRaises(FileExistsError): save_branch(self.branch,p)
 def test_forged_current_draft_rejected_even_if_resealed(self):
  from themeforge.review_branch import _seal
  bad=deepcopy(self.branch); bad['draft']['codes'][0]['label']='Invented revision'
  _seal(bad)
  with self.assertRaises(ValueError): validate_branch(bad)
 def test_blank_reason_rejected(self):
  with self.assertRaises(ValueError): begin_branch(self.parent,**dict(self.author,reason=''))

class Value:
 def __init__(self,value=''): self.value=value
 def get(self,*args): return self.value
 def set(self,value): self.value=value

class DialogCallbackTests(unittest.TestCase):
 def test_main_launch_busy_guard_and_isolated_parent(self):
  from types import SimpleNamespace
  from unittest.mock import patch
  from themeforge.local_codebook_dialog import LocalCodebookDialog
  obj=SimpleNamespace(_busy=True,result=grouped(),status=Value())
  with patch('themeforge.review_branch_dialog.ReviewBranchDialog') as factory:
   self.assertIsNone(LocalCodebookDialog.open_revision_branch(obj)); factory.assert_not_called()
   obj._busy=False
   LocalCodebookDialog.open_revision_branch(obj); factory.assert_called_once_with(obj,obj.result)
 def test_actual_edit_and_undo_callbacks_without_window(self):
  from types import SimpleNamespace
  from themeforge.review_branch_dialog import ReviewBranchDialog as Dialog
  parent=grouped(); author=dict(reviewer_kind='assistant_development_review',reviewer_name='Test reviewer',reason='Source-grounded development test only.')
  branch=begin_branch(parent,**author); code=branch['draft']['codes'][0]
  obj=SimpleNamespace(branch=branch, choice=Value(code['id']),author=lambda:author,
      require_branch=lambda:None, refresh=lambda:None,
      editors={f:Value('Changed candidate wording' if f=='label' else code[f]) for f in ('label','claim','definition')})
  Dialog.apply(obj); self.assertEqual(obj.branch['revisions'][0]['changes']['label'],'Changed candidate wording')
  self.assertEqual(obj.branch['parent'],parent)
  Dialog.undo(obj); self.assertEqual(obj.branch,branch)
 def test_active_branch_cannot_be_silently_replaced(self):
  from types import SimpleNamespace
  from themeforge.review_branch_dialog import ReviewBranchDialog as Dialog
  obj=SimpleNamespace(branch={'existing':'edits'})
  with self.assertRaises(ValueError): Dialog.create_branch(obj)
  with self.assertRaises(ValueError): Dialog.open_branch(obj)

class BranchRoleTests(unittest.TestCase):
 def setUp(self):
  from test_codebook_review import fixture
  self.parent,self.ids=fixture()
  self.author=dict(reviewer_kind='assistant_development_review',reviewer_name='Role reviewer',reason='Explicit source-role revision, not validation.')
  self.branch=begin_branch(self.parent,**self.author)
 def test_exact_roles_replay_reopen_undo(self):
  from themeforge.review_branch import revise_branch_roles
  roles=dict(support=self.ids[:1],qualifications=self.ids[1:2],counterexamples=[])
  revised=revise_branch_roles(self.branch,'LC-synthetic',roles,**self.author)
  validate_branch(revised)
  self.assertEqual(revised['draft']['codes'][0]['qualifications'][0],self.parent['registry'][self.ids[1]])
  self.assertEqual(revised['parent'],self.parent)
  self.assertEqual(undo_revision(revised),self.branch)
  with tempfile.TemporaryDirectory() as temp:
   p=Path(temp)/'roles.json'; save_branch(revised,p); self.assertEqual(load_branch(p),revised)
 def test_overlap_foreign_unknown_rejected_without_mutation(self):
  from themeforge.review_branch import revise_branch_roles
  foreign=next(sid for sid,span in self.parent['registry'].items() if span['unit_id']!=self.parent['codes'][0]['unit_id'])
  before=deepcopy(self.branch)
  for bad in (self.ids[:1],[foreign],['nonexistent']):
   with self.assertRaises(ValueError):
    revise_branch_roles(self.branch,'LC-synthetic',dict(support=self.ids[:1],qualifications=bad,counterexamples=[]),**self.author)
  self.assertEqual(self.branch,before)
 def test_mixed_history_replay_and_undo(self):
  from themeforge.review_branch import revise_branch_roles
  first=revise_branch(self.branch,'LC-synthetic',{'label':'Additional recovery copy'},**self.author)
  second=revise_branch_roles(first,'LC-synthetic',dict(support=self.ids[:2],qualifications=[],counterexamples=[]),**self.author)
  validate_branch(second); self.assertEqual(undo_revision(second),first)
 def test_unsupported_operation_fails_closed(self):
  from themeforge.review_branch import _append_event
  with self.assertRaises(ValueError): _append_event(self.branch,{'operation':'unknown_operation'})

class BranchSplitTests(unittest.TestCase):
 def setUp(self):
  self.parent=grouped(); self.code=self.parent['codes'][0]
  self.author=dict(reviewer_kind='assistant_development_review',reviewer_name='Split tester',reason='Separate two authored interpretations; not validated.')
  self.branch=begin_branch(self.parent,**self.author)
  self.parts=[dict(label='Recovery copy',claim='A copy is retained for recovery.',definition='Keeping a recoverable copy.',support_span_ids=self.code['support_span_ids']),dict(label='Copy availability',claim='The retained copy is available.',definition='Availability of a retained copy.',support_span_ids=self.code['support_span_ids'])]
 def test_split_replay_child_edit_undo_reopen(self):
  from themeforge.review_branch import split_branch
  from themeforge.codebook_review import candidate_review_state
  revised=split_branch(self.branch,self.code['id'],self.parts,**self.author)
  validate_branch(revised)
  self.assertEqual(candidate_review_state(revised['draft'],self.code['id'])['state'],'split_parent')
  self.assertEqual(group_states(revised)[0]['status'],'review_required')
  children=[c for c in revised['draft']['codes'] if c.get('split_parent_id')==self.code['id']]
  self.assertEqual(len(children),2)
  self.assertTrue(all(candidate_review_state(revised['draft'],c['id'])['state']=='unreviewed' for c in children))
  edited=revise_branch(revised,children[0]['id'],{'label':'Revised recovery copy'},**self.author)
  validate_branch(edited);self.assertEqual(undo_revision(edited),revised);self.assertEqual(undo_revision(revised),self.branch)
  with tempfile.TemporaryDirectory() as tmp:
   path=Path(tmp)/'split.json';save_branch(edited,path);self.assertEqual(load_branch(path),edited)
  self.assertEqual(revised['parent'],self.parent)
 def test_second_split_and_foreign_evidence_fail(self):
  from themeforge.review_branch import split_branch
  revised=split_branch(self.branch,self.code['id'],self.parts,**self.author)
  with self.assertRaises(ValueError): split_branch(revised,self.code['id'],self.parts,**self.author)
  bad=deepcopy(self.parts);bad[0]['support_span_ids']=['unknown']
  with self.assertRaises(ValueError): split_branch(self.branch,self.code['id'],bad,**self.author)
 def test_timezone_less_replay_rejected(self):
  from themeforge.review_branch import _append_event
  with self.assertRaises(ValueError):
   _append_event(self.branch,dict(operation='split',code_id=self.code['id'],parts=self.parts,_recorded_at_utc='2026-11-16T00:00:00',**self.author))

class GroupRereviewTests(unittest.TestCase):
 def setUp(self):
  self.parent=grouped();self.author=dict(reviewer_kind='assistant_development_review',reviewer_name='Development reviewer',reason='Explicit provisional relationship review.')
  self.branch=begin_branch(self.parent,**self.author)
  self.ids=self.parent['categories'][0]['code_ids']
  self.fields=dict(code_ids=self.ids,label='Retained copies for recovery',shared_meaning='Maintaining separate copies supports possible recovery.',limits='Invented sources; this is not validated.',member_notes={cid:'This candidate contributes a separate recovery copy account.' for cid in self.ids},reviewer_kind=self.author['reviewer_kind'],reason=self.author['reason'])
 def reviewed(self):
  from themeforge.review_branch import decide_branch_candidate
  branch=self.branch
  for cid in self.ids: branch=decide_branch_candidate(branch,cid,'accept',**self.author)
  return branch
 def test_reconfirmation_requires_current_candidate_decisions(self):
  from themeforge.review_branch import rereview_branch_group, decide_branch_candidate
  with self.assertRaises(ValueError): rereview_branch_group(self.branch,'categories',self.parent['categories'][0]['id'],reviewer_name=self.author['reviewer_name'],**self.fields)
  # A rejection makes even an otherwise defined candidate ineligible.
  branch=decide_branch_candidate(self.branch,self.ids[0],'reject',**self.author)
  with self.assertRaises(ValueError): rereview_branch_group(branch,'categories',self.parent['categories'][0]['id'],reviewer_name=self.author['reviewer_name'],**self.fields)
 def test_review_roundtrip_and_edit_stales_again(self):
  from themeforge.review_branch import rereview_branch_group
  branch=self.reviewed()
  reviewed=rereview_branch_group(branch,'categories',self.parent['categories'][0]['id'],reviewer_name=self.author['reviewer_name'],**self.fields)
  validate_branch(reviewed)
  self.assertEqual(group_states(reviewed)[0]['status'],'rereviewed_provisional_not_validated')
  self.assertFalse(group_states(reviewed)[0]['current_approval'])
  edited=revise_branch(reviewed,self.ids[0],{'label':'Changed after grouping review'},**self.author)
  self.assertEqual(group_states(edited)[0]['status'],'review_required')
  self.assertEqual(undo_revision(edited),reviewed)
  with tempfile.TemporaryDirectory() as tmp:
   path=Path(tmp)/'review.json';save_branch(reviewed,path);self.assertEqual(load_branch(path),reviewed)
 def test_reject_after_group_review_stales(self):
  from themeforge.review_branch import rereview_branch_group,decide_branch_candidate
  reviewed=rereview_branch_group(self.reviewed(),'categories',self.parent['categories'][0]['id'],reviewer_name=self.author['reviewer_name'],**self.fields)
  rejected=decide_branch_candidate(reviewed,self.ids[0],'uncertain',**self.author)
  self.assertEqual(group_states(rejected)[0]['status'],'review_required')
 def test_unknown_group_and_blank_reviewer_rejected(self):
  from themeforge.review_branch import rereview_branch_group
  with self.assertRaises(ValueError): rereview_branch_group(self.reviewed(),'categories','unknown',reviewer_name=self.author['reviewer_name'],**self.fields)
  with self.assertRaises(ValueError): rereview_branch_group(self.reviewed(),'categories',self.parent['categories'][0]['id'],reviewer_name='',**self.fields)

class AdvancedDialogCallbacks(unittest.TestCase):
 def test_decide_split_and_group_callbacks_use_validated_backend(self):
  from types import SimpleNamespace
  from themeforge.review_branch_dialog import ReviewBranchDialog as Dialog
  parent=grouped();author=dict(reviewer_kind='assistant_development_review',reviewer_name='Callback tester',reason='Explicit source-grounded development review.')
  branch=begin_branch(parent,**author);ids=parent['categories'][0]['code_ids']
  obj=SimpleNamespace(branch=branch,choice=Value(ids[0]),author=lambda:author,require_branch=lambda:None,refresh=lambda:None)
  for cid in ids:obj.choice.set(cid);Dialog.decide(obj,'accept')
  fields=dict(code_ids=ids,label='Related recovery copies',shared_meaning='Separate copies support possible recovery.',limits='Provisional invented test.',member_notes={cid:'Recovery copy in its own source context.' for cid in ids})
  Dialog.apply_group(obj,'categories',parent['categories'][0]['id'],fields)
  self.assertEqual(group_states(obj.branch)[0]['status'],'rereviewed_provisional_not_validated')
  code=obj.branch['draft']['codes'][0]
  parts=[dict(label='Copy recovery',claim='A copy supports recovery.',definition='Maintaining another recovery copy.',support_span_ids=code['support_span_ids']),dict(label='Copy retention',claim='A separate copy is retained.',definition='Retention of another copy.',support_span_ids=code['support_span_ids'])]
  Dialog.apply_split(obj,code['id'],parts)
  self.assertEqual(group_states(obj.branch)[0]['status'],'review_required');validate_branch(obj.branch)
 def test_roles_callback_failure_does_not_replace_branch(self):
  from types import SimpleNamespace
  from test_codebook_review import fixture
  from themeforge.review_branch_dialog import ReviewBranchDialog as Dialog
  parent,ids=fixture();author=dict(reviewer_kind='assistant_development_review',reviewer_name='Role callback tester',reason='Review exact source evidence.')
  obj=SimpleNamespace(branch=begin_branch(parent,**author),author=lambda:author,refresh=lambda:None)
  before=deepcopy(obj.branch)
  with self.assertRaises(ValueError):Dialog.apply_roles(obj,'LC-synthetic',dict(support=['bad'],qualifications=[],counterexamples=[]))
  self.assertEqual(obj.branch,before)
  Dialog.apply_roles(obj,'LC-synthetic',dict(support=ids[:2],qualifications=[],counterexamples=[]));validate_branch(obj.branch)

class GroupSelectionTests(unittest.TestCase):
 def test_selection_names_notes_without_typing_ids(self):
  from themeforge.review_branch_dialog import group_form_fields
  from themeforge.review_branch import decide_branch_candidate
  parent=grouped();author=dict(reviewer_kind='assistant_development_review',reviewer_name='Selection tester',reason='Check current member selection.')
  branch=begin_branch(parent,**author);ids=parent['categories'][0]['code_ids']
  with self.assertRaises(ValueError):group_form_fields(branch,ids)
  for cid in ids:branch=decide_branch_candidate(branch,cid,'accept',**author)
  fields=group_form_fields(branch,ids)
  self.assertEqual([f[0] for f in fields],['Relationship label','Shared meaning','Limits','Member 1 rationale','Member 2 rationale'])
  for bad in ([],ids[:1],ids+ids,['unknown',ids[0]]):
   with self.assertRaises(ValueError):group_form_fields(branch,bad)

class BranchBoundaryTests(unittest.TestCase):
 def setUp(self):
  self.author=dict(reviewer_kind='assistant_development_review',reviewer_name='Boundary tester',reason='Check safe persistence limits.')
  self.branch=begin_branch(grouped(),**self.author)
 def test_pretty_file_limit_checked_before_destination_creation(self):
  from unittest.mock import patch
  compact=len(json.dumps(self.branch,ensure_ascii=False).encode())
  pretty=len(json.dumps(self.branch,ensure_ascii=False,indent=2).encode())
  self.assertGreater(pretty,compact)
  with tempfile.TemporaryDirectory() as tmp,patch('themeforge.review_branch.MAX_BYTES',compact+1):
   path=Path(tmp)/'too_big.json'
   with self.assertRaises(ValueError):save_branch(self.branch,path)
   self.assertFalse(path.exists())
 def test_oversized_read_rejected(self):
  from unittest.mock import patch
  with tempfile.TemporaryDirectory() as tmp,patch('themeforge.review_branch.MAX_BYTES',100):
   path=Path(tmp)/'large.json';path.write_bytes(b' '*101)
   with self.assertRaises(ValueError):load_branch(path)
 def test_bad_json_shape_and_revision_bound(self):
  from unittest.mock import patch
  with self.assertRaises(ValueError):validate_branch([])
  with patch('themeforge.review_branch.MAX_REVISIONS',0):
   with self.assertRaises(ValueError):revise_branch(self.branch,self.branch['draft']['codes'][0]['id'],{'label':'Explicit revision'},**self.author)
 def test_unicode_roundtrip(self):
  branch=revise_branch(self.branch,self.branch['draft']['codes'][0]['id'],{'label':'Recovery copy \\uac00'},**self.author)
  with tempfile.TemporaryDirectory() as tmp:
   path=Path(tmp)/'unicode.json';save_branch(branch,path);self.assertEqual(load_branch(path),branch)


class BranchCloseGuardTests(unittest.TestCase):
 def make_dialog(self, saved=False, text='original'):
  from types import SimpleNamespace
  from unittest.mock import Mock
  from themeforge.review_branch_dialog import ReviewBranchDialog
  obj=SimpleNamespace(branch={'branch_sha256':'current','draft':{'codes':[{'id':'c','label':'original'}]}},
    saved_branch_sha256='current' if saved else None,choice=Value('c'),editors={'label':Mock(get=Mock(return_value=text))},destroy=Mock(),save=Mock(return_value=True))
  return obj,ReviewBranchDialog
 def test_unsaved_close_cancel_keeps_branch(self):
  from unittest.mock import patch
  obj,Dialog=self.make_dialog()
  with patch('themeforge.review_branch_dialog.messagebox.askyesnocancel',return_value=None):
   self.assertFalse(Dialog.request_close(obj))
  obj.destroy.assert_not_called();obj.save.assert_not_called()
 def test_save_picker_cancel_keeps_branch(self):
  from unittest.mock import patch
  obj,Dialog=self.make_dialog();obj.save.return_value=False
  with patch('themeforge.review_branch_dialog.messagebox.askyesnocancel',return_value=True):
   self.assertFalse(Dialog.request_close(obj))
  obj.destroy.assert_not_called()
 def test_failed_save_keeps_branch(self):
  from unittest.mock import patch
  obj,Dialog=self.make_dialog();obj.save.side_effect=OSError('disk unavailable')
  with patch('themeforge.review_branch_dialog.messagebox.askyesnocancel',return_value=True):
   with self.assertRaises(OSError):Dialog.request_close(obj)
  obj.destroy.assert_not_called()
 def test_explicit_discard_closes(self):
  from unittest.mock import patch
  obj,Dialog=self.make_dialog()
  with patch('themeforge.review_branch_dialog.messagebox.askyesnocancel',return_value=False):
   self.assertTrue(Dialog.request_close(obj))
  obj.save.assert_not_called();obj.destroy.assert_called_once()
 def test_successful_save_closes(self):
  from unittest.mock import patch
  obj,Dialog=self.make_dialog()
  with patch('themeforge.review_branch_dialog.messagebox.askyesnocancel',return_value=True):
   self.assertTrue(Dialog.request_close(obj))
  obj.save.assert_called_once();obj.destroy.assert_called_once()
 def test_saved_branch_closes_without_prompt(self):
  from unittest.mock import patch
  obj,Dialog=self.make_dialog(saved=True)
  with patch('themeforge.review_branch_dialog.messagebox.askyesnocancel') as prompt:
   self.assertTrue(Dialog.request_close(obj));prompt.assert_not_called()
 def test_unapplied_wording_cancel_keeps_window(self):
  from unittest.mock import patch
  obj,Dialog=self.make_dialog(saved=True,text='not yet applied')
  with patch('themeforge.review_branch_dialog.messagebox.askyesno',return_value=False):
   self.assertFalse(Dialog.request_close(obj))
  obj.destroy.assert_not_called()
 def test_save_updates_dirty_marker_only_after_success(self):
  from unittest.mock import patch,Mock
  obj,Dialog=self.make_dialog();obj.require_branch=lambda:None;obj.notice=Mock()
  with patch('themeforge.review_branch_dialog.filedialog.asksaveasfilename',return_value='new.json'),patch('themeforge.review_branch_dialog.save_branch',side_effect=OSError('failed')):
   with self.assertRaises(OSError):Dialog.save(obj)
  self.assertIsNone(obj.saved_branch_sha256)
  with patch('themeforge.review_branch_dialog.filedialog.asksaveasfilename',return_value='new.json'),patch('themeforge.review_branch_dialog.save_branch'):
   self.assertTrue(Dialog.save(obj))
  self.assertEqual(obj.saved_branch_sha256,'current')


class OwnerShutdownTests(unittest.TestCase):
 def test_nested_cancel_stops_owner(self):
  from unittest.mock import Mock
  from themeforge.review_branch_dialog import ReviewBranchDialog,close_review_branches
  branch=Mock(spec=ReviewBranchDialog);branch.request_close.return_value=False
  nested=Mock();nested.winfo_children.return_value=[branch]
  owner=Mock();owner.winfo_children.return_value=[nested]
  self.assertFalse(close_review_branches(owner));branch.request_close.assert_called_once()
 def test_save_failure_stops_owner_and_reports(self):
  from unittest.mock import Mock
  from themeforge.review_branch_dialog import ReviewBranchDialog,close_review_branches
  branch=Mock(spec=ReviewBranchDialog);branch.request_close.side_effect=OSError('disk full');branch.notice=Mock()
  owner=Mock();owner.winfo_children.return_value=[branch]
  self.assertFalse(close_review_branches(owner));branch.notice.set.assert_called_once_with('disk full')
 def test_all_confirmed_or_empty_allows_shutdown(self):
  from unittest.mock import Mock
  from themeforge.review_branch_dialog import ReviewBranchDialog,close_review_branches
  branch=Mock(spec=ReviewBranchDialog);branch.request_close.return_value=True
  owner=Mock();owner.winfo_children.return_value=[branch]
  self.assertTrue(close_review_branches(owner))
  owner.winfo_children.return_value=[];self.assertTrue(close_review_branches(owner))
 def test_app_cancel_does_not_cancel_workers_or_destroy(self):
  from unittest.mock import Mock,patch
  import ast
  tree=ast.parse((Path(__file__).parents[1]/'themeforge/app.py').read_text(encoding='utf-8'))
  cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='ThemeForgeApp')
  method=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='destroy')
  scope={'__name__':'themeforge.app','__package__':'themeforge'}
  exec(compile(ast.Module(body=[method],type_ignores=[]),'app.py','exec'),scope)
  app=Mock()
  with patch('themeforge.review_branch_dialog.close_review_branches',return_value=False),patch('tkinter.Tk.destroy') as destroy:
   scope['destroy'](app);destroy.assert_not_called();app.after_cancel.assert_not_called()
 def test_codebook_cancel_does_not_destroy(self):
  from unittest.mock import Mock,patch
  from themeforge.local_codebook_dialog import LocalCodebookDialog
  with patch('themeforge.review_branch_dialog.close_review_branches',return_value=False),patch('tkinter.Toplevel.destroy') as destroy:
   LocalCodebookDialog.destroy(Mock());destroy.assert_not_called()


class AtomicBranchSaveTests(unittest.TestCase):
 setUp = BranchTests.setUp
 def test_flush_failure_never_publishes_partial_file(self):
  from unittest.mock import patch
  with tempfile.TemporaryDirectory() as tmp:
   target=Path(tmp)/'branch.json'
   with patch('themeforge.review_branch.os.fsync',side_effect=OSError('disk full')):
    with self.assertRaises(OSError):save_branch(self.branch,target)
   self.assertFalse(target.exists());self.assertEqual(list(Path(tmp).iterdir()),[])
 def test_publication_failure_cleans_staged_file(self):
  from unittest.mock import patch
  with tempfile.TemporaryDirectory() as tmp:
   target=Path(tmp)/'branch.json'
   with patch('themeforge.review_branch._publish_branch_file',side_effect=OSError('filesystem unavailable')):
    with self.assertRaises(OSError):save_branch(self.branch,target)
   self.assertFalse(target.exists());self.assertEqual(list(Path(tmp).iterdir()),[])
 def test_existing_destination_is_byte_identical(self):
  with tempfile.TemporaryDirectory() as tmp:
   target=Path(tmp)/'branch.json';target.write_bytes(b'existing research record')
   with self.assertRaises(FileExistsError):save_branch(self.branch,target)
   self.assertEqual(target.read_bytes(),b'existing research record')
   self.assertEqual(list(Path(tmp).iterdir()),[target])
 def test_publish_sees_complete_valid_branch(self):
  from unittest.mock import patch
  import os
  from themeforge.review_branch import _publish_branch_file as link
  def inspect_then_link(source,target):
   self.assertEqual(load_branch(source),self.branch)
   self.assertFalse(Path(target).exists())
   link(source,target)
  with tempfile.TemporaryDirectory() as tmp:
   target=Path(tmp)/'branch.json'
   with patch('themeforge.review_branch._publish_branch_file',side_effect=inspect_then_link):save_branch(self.branch,target)
   self.assertEqual(load_branch(target),self.branch)
   self.assertEqual(list(Path(tmp).iterdir()),[target])
 def test_concurrent_destination_is_not_replaced(self):
  from unittest.mock import patch
  import os
  from themeforge.review_branch import _publish_branch_file as link
  def competing_save(source,target):
   Path(target).write_bytes(b'other save')
   link(source,target)
  with tempfile.TemporaryDirectory() as tmp:
   target=Path(tmp)/'branch.json'
   with patch('themeforge.review_branch._publish_branch_file',side_effect=competing_save):
    with self.assertRaises(FileExistsError):save_branch(self.branch,target)
   self.assertEqual(target.read_bytes(),b'other save')
   self.assertEqual(list(Path(tmp).iterdir()),[target])

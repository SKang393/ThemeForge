"""Execute actual modal callbacks without constructing Tk windows."""
import ast,inspect,json,tempfile,textwrap,unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
from themeforge.local_codebook_dialog import LocalCodebookDialog
from themeforge.main_review_qa import make_controller,Value,forbidden
from test_main_codebook_review import fictional_draft

class Text:
 def __init__(self,value=''):self.value=value
 def get(self,*args):return self.value

def callback(method,bindings):
 tree=ast.parse(textwrap.dedent(inspect.getsource(method)))
 body=next(node for node in tree.body[0].body if isinstance(node,ast.FunctionDef) and node.name=='apply')
 namespace=dict(bindings)
 exec(compile(ast.fix_missing_locations(ast.Module(body=[body],type_ignores=[])),'<actual-modal-callback>','exec'),namespace)
 return namespace['apply']

class ModalContractTests(unittest.TestCase):
 def setUp(self):
  for name in ('tkinter.Tk','tkinter.Toplevel','subprocess.Popen'):
   guard=patch(name,side_effect=forbidden);guard.start();self.addCleanup(guard.stop)
 def test_evidence_requires_named_explicit_author_before_backend(self):
  controller=SimpleNamespace(revise_evidence=Mock(return_value=True),status=Value())
  name=Value();kind=Value();notice=Mock();window=Mock()
  apply=callback(LocalCodebookDialog.open_evidence_review,dict(self=controller,reviewer_name=name,reviewer_kind=kind,notice=notice,code_id='c1',tree=SimpleNamespace(selection=lambda:['s1']),rationale=Text('Source reviewed.'),window=window))
  apply();controller.revise_evidence.assert_not_called();window.destroy.assert_not_called()
  name.set('Explicit AI reviewer');kind.set('assistant_development_review');apply()
  controller.revise_evidence.assert_called_once_with('c1',['s1'],'Source reviewed.',reviewer_kind='assistant_development_review',reviewer_name='Explicit AI reviewer')
  window.destroy.assert_called_once()
 def test_roles_require_identity_and_preserve_explicit_role_assignments(self):
  controller=SimpleNamespace(revise_evidence_roles=Mock(return_value=True),status=Value())
  name=Value();kind=Value();notice=Mock();window=Mock()
  apply=callback(LocalCodebookDialog.open_evidence_roles_review,dict(self=controller,reviewer_name=name,reviewer_kind=kind,notice=notice,code_id='c1',spans={'s1':{},'s2':{}},assignments={'s1':'support','s2':'qualifications'},current={'support':[],'qualifications':[],'counterexamples':[]},rationale=Text('Explicit role review.'),window=window))
  apply();controller.revise_evidence_roles.assert_not_called()
  name.set('Explicit AI reviewer');kind.set('assistant_development_review');apply()
  self.assertEqual(controller.revise_evidence_roles.call_args.args[1],{'support':['s1'],'qualifications':['s2'],'counterexamples':[]})
  window.destroy.assert_called_once()
 def test_wording_requires_identity_decision_and_preserves_blank_attempt(self):
  controller=make_controller(fictional_draft());before=deepcopy(controller.result);code=controller.result['codes'][0]
  name=Value();kind=Value();decision=Value();window=Mock();notice=Mock()
  apply=callback(LocalCodebookDialog.open_interpretation_review,dict(self=controller,reviewer_name=name,reviewer_kind=kind,rationale=Text('Preserve actor and uncertainty.'),before_digest=controller.result['result_sha256'],split_mode=Value(False),code_id=code['id'],code=code,editors={'label':Text('Explicit reviewed label')},decision=decision,checks={},notice=notice,window=window))
  apply();self.assertEqual(controller.result,before);window.destroy.assert_not_called()
  name.set('Explicit AI reviewer');kind.set('assistant_development_review');decision.set('edit');apply()
  self.assertEqual(controller.result['codes'][0]['label'],'Explicit reviewed label');window.destroy.assert_called_once()
 def test_oversized_export_mock_does_not_corrupt_digest_validation(self):
  controller=make_controller(fictional_draft());before=deepcopy(controller.result)
  # Replace the dialog's JSON binding, not shared json.dumps used by digests.
  with tempfile.TemporaryDirectory() as tmp,patch('themeforge.local_codebook_dialog.json',SimpleNamespace(dumps=lambda *a,**kw:'x'*(16*1024**2+1))):
   path=Path(tmp)/'too-large.json';self.assertFalse(controller.export('json',path));self.assertFalse(path.exists())
   self.assertIn('16 MiB reopen limit',controller.status.get())
  self.assertEqual(controller.result,before)

if __name__=='__main__':unittest.main()

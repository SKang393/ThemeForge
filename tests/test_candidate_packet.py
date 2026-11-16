"""Small adversarial provenance tests; fiction here is software-test data only."""
from copy import deepcopy
import csv
import io
import unittest
from themeforge.candidate_packet import VERSION,EDITABLE,digest,text_digest,seal,validate_packet,revise_code,export_csv,export_html,scope_binding_status

def fixture():
    text='Pupil: I might try it if it is quiet.\nTeacher: It has not been tried.'
    sid=text_digest(text);a=text.index('I might');b=text.index('\n');c=text.index('It has')
    actors=[dict(actor_id='P',source_label='Pupil',role='pupil'),dict(actor_id='T',source_label='Teacher',role='teacher')]
    seg=[dict(segment_id='s1',speaker_actor_id='P',start=a,end=b,text=text[a:b],label_start=0,label_end=5,role='participant_account'),
         dict(segment_id='s2',speaker_actor_id='T',start=c,end=len(text),text=text[c:],label_start=b+1,label_end=c-2,role='participant_account')]
    anchors=[dict(anchor_id='e1',start=a,end=b,text=text[a:b],source_sha256=sid,segment_id='s1',speaker_actor_id='P'),
             dict(anchor_id='e2',start=c,end=len(text),text=text[c:],source_sha256=sid,segment_id='s2',speaker_actor_id='T')]
    origin=dict(kind='AI',author_id='software_test')
    code=dict(code_id='c1',label='Possible trial',claim='The pupil might try it if quiet.',definition='Conditional possibility, not a completed trial.',speaker_actor_id='P',actor_ids=['P'],perspective='direct report',modality='possible',conditions=['if it is quiet'],limits=['Not completed.'],alternatives=['No-code under another purpose.'],target_segment_ids=['s1'],evidence_ids=['e1'],qualification_ids=[],counterexample_ids=[],origin=origin,field_origins={k:deepcopy(origin) for k in EDITABLE})
    code['original_scope_input']=dict(label=code['label'],claim=code['claim'],evidence_ids=['e1'],source_sha256=sid)
    code['original_scope_input_sha256']=digest(code['original_scope_input'])
    code['scope_status']='AI_authored_unreviewed_definition_not_accepted_scope'
    r=dict(record_id='TEST-1',family_id='TEST-F',analytic_purpose='Test source preservation.',source_text=text,source_sha256=sid,construction_source_sha256=sid,actors=actors,segments=seg,anchors=anchors,
        original_proposals=[deepcopy(code)],original_proposals_sha256=digest([code]),proposals=[code],training_eligible=False,human_truth=False,
        split=dict(current_use='quarantine',component_closure_status='quarantined',historical_assignment='unassigned'),
        rights=dict(participant_story_or_researcher_label_derivation=False,training_permission='not_established'),ancestry={},independent_review={},unresolved_decisions=['Review source meaning.'])
    return seal(dict(version=VERSION,records=[r],review_history=[],training_eligible=False,human_truth=False,human_adjudications=[]))

class CandidatePacketTests(unittest.TestCase):
    def test_ai_revision_roundtrip_retains_original_and_chain(self):
        p=fixture();q=revise_code(p,'TEST-1','c1',{'definition':'The pupil describes a possible quiet trial.'},reviewer_kind='AI',reviewer_id='qa',reason='Explicit AI revision test.')
        self.assertEqual(validate_packet(q)['revisions'],1)
        self.assertEqual(p['records'][0]['proposals'],q['records'][0]['original_proposals'])
        self.assertEqual(q['records'][0]['proposals'][0]['field_origins']['definition']['kind'],'AI')
        self.assertFalse(q['training_eligible']);self.assertEqual(q['human_adjudications'],[])
        q['review_history'][0]['old_values']['definition']='Changed audit history';seal(q)
        with self.assertRaisesRegex(ValueError,'Revision chain'):validate_packet(q)

    def test_cannot_relabel_context_as_support_or_mutate_source(self):
        p=fixture()
        with self.assertRaisesRegex(ValueError,'another speaker'):
            revise_code(p,'TEST-1','c1',{'evidence_ids':['e2']},reviewer_kind='AI',reviewer_id='qa',reason='Invalid role move.')
        for change in ('text','actor','span'):
            q=deepcopy(p)
            if change=='text':q['records'][0]['source_text']+='!'
            elif change=='actor':q['records'][0]['anchors'][0]['speaker_actor_id']='T'
            else:q['records'][0]['anchors'][0]['end']-=1
            seal(q)
            with self.subTest(change=change),self.assertRaises(ValueError):validate_packet(q)

    def test_edit_without_event_or_promotion_rejected(self):
        for kind in ('silent','training','human','origin'):
            p=fixture()
            if kind=='silent':p['records'][0]['proposals'][0]['label']='Changed without event'
            elif kind=='training':p['training_eligible']=True
            elif kind=='human':p['human_adjudications']=[{'reviewer_kind':'AI'}]
            else:p['records'][0]['proposals'][0]['field_origins']['label']={'kind':'human_declared','author_id':'fake'}
            seal(p)
            with self.subTest(kind=kind),self.assertRaises(ValueError):validate_packet(p)

    def test_none_is_valid_role_and_noop_cannot_change_authorship(self):
        p=fixture();validate_packet(p)
        with self.assertRaisesRegex(ValueError,'No changed'):
            revise_code(p,'TEST-1','c1',{'qualification_ids':[]},reviewer_kind='AI',reviewer_id='qa',reason='No-op cannot reauthor.')
        q=revise_code(p,'TEST-1','c1',{'qualification_ids':['e2']},reviewer_kind='AI',reviewer_id='qa',reason='Other speaker is explicit qualification, not target support.')
        self.assertEqual(validate_packet(q)['revisions'],1)
        self.assertEqual(q['records'][0]['proposals'][0]['evidence_ids'],['e1'])

    def test_csv_formula_and_html_text_are_inert(self):
        p=fixture();q=revise_code(p,'TEST-1','c1',{'label':'=SUM(1,2)','claim':'<script>alert(1)</script>'},reviewer_kind='AI',reviewer_id='qa',reason='Escaping test, not semantically accepted.')
        rows=list(csv.DictReader(io.StringIO(export_csv(q))))
        self.assertEqual(rows[0]['label'],"'=SUM(1,2)")
        html=export_html(q);self.assertNotIn('<script>alert',html);self.assertIn('&lt;script&gt;',html)
        self.assertIn('No authentic participant findings',html)

    def test_scope_input_change_warns_without_claiming_revalidation(self):
        p=fixture();self.assertEqual(scope_binding_status(p['records'][0]['proposals'][0]),'original_input_unchanged')
        q=revise_code(p,'TEST-1','c1',{'label':'Possible quiet trial'},reviewer_kind='human_declared',reviewer_id='test-only',reason='Software fixture, not a human-adjudicated record.')
        self.assertIn('definition_needs_review',scope_binding_status(q['records'][0]['proposals'][0]))
        self.assertFalse(q['training_eligible']);self.assertEqual(q['human_adjudications'],[])
        q['records'][0]['proposals'][0]['original_scope_input_sha256']='0'*64;seal(q)
        with self.assertRaisesRegex(ValueError,'definition-input'):validate_packet(q)

    def test_missing_export_fields_fail_at_import_not_during_export(self):
        p=fixture();r=p['records'][0]
        for code in r['original_proposals']+r['proposals']:code.pop('conditions')
        r['original_proposals_sha256']=digest(r['original_proposals']);seal(p)
        with self.assertRaisesRegex(ValueError,'conditions list'):validate_packet(p)
        p=fixture();p['records'][0].pop('unresolved_decisions');seal(p)
        with self.assertRaisesRegex(ValueError,'unresolved decisions'):validate_packet(p)

if __name__=='__main__':unittest.main()

"""Context-transport regressions; synthetic native measurements only."""
from copy import deepcopy
import json
import unittest
from pathlib import Path
import tempfile
from unittest.mock import patch

from themeforge.analysis_types import TranscriptDocument
from themeforge.study_batches import (prepare_study,plan_batches,validate_plan,NativePrompt,TokenBudget,
    digest,CONTEXT_TRANSPORT_POLICY)
from themeforge.study_planning import PlanningCheckpoint,planning_identity,LEGACY_CONTEXT_POLICY
from themeforge.study_session import build_native_plan,build_resumable_plan,coding_request,validate_session,export_session_html
from tests.test_study_session import SessionClient


def fixture():
    return prepare_study([TranscriptDocument('synthetic-role-transport',
        'Facilitator: What support matters?\nCaregiver: I use a picture card at home.\nTeacher: I cannot tell whether that happens at school.')])


def native(request):
    measured=SessionClient().measure_messages(coding_request(request)['messages'])
    return NativePrompt(measured['rendered_prompt'],measured['token_ids'],measured['template_sha256'],
        measured['tokenizer_sha256'],digest(request))


class ContextRoleTransportTests(unittest.TestCase):
    def test_new_target_and_preceding_context_keep_distinct_exact_saved_labels(self):
        study=fixture();unchanged=deepcopy(study);session=build_native_plan(study,SessionClient())
        teacher=next(u for u in study['units'] if u['speaker']=='Teacher')
        caregiver=next(u for u in study['units'] if u['speaker']=='Caregiver')
        work=next(w for w in session['plan']['work_items'] if w['parent_unit_id']==teacher['id'])
        self.assertEqual(session['plan']['context_transport_policy'],CONTEXT_TRANSPORT_POLICY)
        previous=next(s for s in work['context_spans'] if s['role']=='preceding_turn_referent_only')
        self.assertEqual(previous['source_unit_id'],caregiver['id']);self.assertEqual(previous['speaker'],'Caregiver')
        request=next(r for r in session['requests'].values() if r['target']['parent_unit_id']==teacher['id'])
        payload=json.loads(request['messages'][1]['content'])
        self.assertEqual(payload['target_speaker'],'Teacher')
        self.assertEqual(payload['referent_context_not_support'][1]['speaker'],'Caregiver')
        self.assertEqual(payload['referent_context_not_support'][1]['source_unit_id'],caregiver['id'])
        self.assertEqual(study,unchanged)

    def test_explicit_legacy_plan_and_session_validate_export_without_metadata_migration(self):
        study=fixture();session=build_native_plan(study,SessionClient())
        legacy=plan_batches(study,native_prompt_callback=native,budget=TokenBudget(4096,128,1),context_transport_policy=None)
        session['plan']=legacy;session['requests']={b['id']:coding_request(b['request']) for b in legacy['batches']}
        before=deepcopy(session);validate_session(session);export_session_html(session)
        self.assertEqual(session,before);self.assertNotIn('context_transport_policy',legacy)
        self.assertTrue(all('speaker' not in c and 'source_unit_id' not in c for w in legacy['work_items'] for c in w['context_spans']))
        fresh=build_native_plan(study,SessionClient())
        self.assertNotEqual(fresh['plan']['plan_sha256'],legacy['plan_sha256'])
        for key in session['requests']:
            self.assertEqual(session['requests'][key]['messages'][0],fresh['requests'][key]['messages'][0])

    def test_context_identity_tampering_or_omission_is_rejected_after_plan_reseal(self):
        plan=build_native_plan(fixture(),SessionClient())['plan']
        for key,value in (('speaker','Teacher'),('source_unit_id','foreign-unit'),('omit',None)):
            wrong=deepcopy(plan)
            context=next(c for w in wrong['work_items'] for c in w['context_spans'] if c.get('speaker')=='Caregiver')
            if key=='omit':context.pop('speaker')
            else:context[key]=value
            wrong['plan_sha256']=digest({k:v for k,v in wrong.items() if k!='plan_sha256'})
            with self.subTest(key=key),self.assertRaisesRegex(ValueError,'Context speaker/source-unit'):
                validate_plan(wrong)

    def test_cross_turn_context_has_explicit_null_identity_without_guessing(self):
        study=fixture();source=study['sources'][0]
        def cross_turn(unit,item,study):
            return [dict(source_name=source['source_name'],source_hash=source['source_hash'],
                source_start=0,source_end=unit['source_end'],text=source['text'][:unit['source_end']],role='caller_context_only')]
        plan=plan_batches(study,native_prompt_callback=native,budget=TokenBudget(12000,128,1),context_selector=cross_turn)
        self.assertTrue(all(c['speaker'] is None and c['source_unit_id'] is None for w in plan['work_items'] for c in w['context_spans']))

    def test_new_checkpoint_identity_does_not_reuse_legacy_context_measurements(self):
        study=fixture();budget=TokenBudget(4096,128,1)
        kwargs=dict(model_sha256='c'*64,executable_sha256='d'*64,template_sha256='a'*64,
            tokenizer_sha256='b'*64,contract_sha256='e'*64,config={'synthetic':True})
        identity=planning_identity(study,budget=budget,**kwargs)
        self.assertIn(CONTEXT_TRANSPORT_POLICY,identity['context_policy'])
        old=deepcopy(identity);old['context_policy']='nearest_question_and_preceding_turn_or_parent_sentence'
        checkpoint=PlanningCheckpoint(old);legacy=checkpoint.run(study,native_prompt_callback=native,budget=budget)
        snapshot=checkpoint.snapshot()
        with self.assertRaisesRegex(ValueError,'identity/digest'):PlanningCheckpoint(identity,snapshot)
        replay=PlanningCheckpoint(old,snapshot)
        def forbidden(request):self.fail('A completed legacy request was remeasured.')
        self.assertEqual(replay.run(study,native_prompt_callback=forbidden,budget=budget),legacy)

    def test_application_resume_of_interrupted_legacy_planning_reuses_only_completed_measurements(self):
        import themeforge.study_planning as planning
        study=fixture();client=SessionClient();original_identity=planning.planning_identity
        def legacy_identity(*args,**kwargs):
            kwargs['context_policy']=LEGACY_CONTEXT_POLICY
            return original_identity(*args,**kwargs)
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'tmp') as directory:
            root=Path(directory)
            with patch.object(planning,'planning_identity',side_effect=legacy_identity):
                partial=build_resumable_plan(study,client,root/'legacy',max_native_measurements=1)
            self.assertIsNone(partial)
            prior=json.loads((root/'legacy/planning-session.json').read_text(encoding='utf-8'));unchanged=deepcopy(prior)
            completed_measurements=deepcopy(prior['checkpoint']['measurements'])
            resumed_client=SessionClient()
            resumed=build_resumable_plan(study,resumed_client,root/'resume',prior=prior)
            self.assertEqual(prior,unchanged);self.assertEqual(len(resumed_client.measured),1)
            self.assertNotIn('context_transport_policy',resumed['plan'])
            self.assertEqual(resumed['provenance']['context_transport_policy'],LEGACY_CONTEXT_POLICY)
            self.assertFalse(resumed['provenance']['context_speaker_metadata_available'])
            final=json.loads((root/'resume/planning-session.json').read_text(encoding='utf-8'))
            for key,record in completed_measurements.items():self.assertEqual(final['checkpoint']['measurements'][key],record)
            validate_session(resumed);export_session_html(resumed)
            fresh=build_resumable_plan(study,SessionClient(),root/'new-v2')
            self.assertTrue(fresh['provenance']['context_speaker_metadata_available'])
            self.assertEqual(fresh['plan']['context_transport_policy'],CONTEXT_TRANSPORT_POLICY)


if __name__=='__main__':unittest.main()

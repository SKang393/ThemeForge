from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import threading
import unittest
from themeforge.analysis_types import TranscriptDocument
from themeforge.study_batches import prepare_study
from themeforge.study_session import (build_native_plan,build_resumable_plan,validate_planning_session,
    execute_pending,retry_definitions,validate_session)
from tests.test_study_session import SessionClient,CODE,DEFINE


class StudyResumeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'tmp')
        self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.study=prepare_study([TranscriptDocument(f'case-{n}',f'Participant 1: We keep a backup copy on a separate drive for project {n}.') for n in range(4)])

    def test_interrupted_planning_resume_preserves_native_measurements_and_lens(self):
        client=SessionClient();before=deepcopy(self.study)
        first=build_resumable_plan(self.study,client,self.root/'one',mode='guided',research_questions=['How is data kept?'],max_native_measurements=2)
        self.assertIsNone(first)
        path=self.root/'one/planning-session.json';prior=json.loads(path.read_text(encoding='utf-8'))
        self.assertEqual(prior['checkpoint']['status'],'interrupted');self.assertEqual(len(client.measured),2)
        self.assertEqual({u['status'] for u in prior['study']['units']},{'pending'})
        original=path.read_bytes();validate_planning_session(prior)
        second=build_resumable_plan(self.study,client,self.root/'two',prior=prior,max_native_measurements=10)
        self.assertEqual(len(client.measured),4)
        self.assertEqual(second['mode'],'guided');self.assertEqual(second['research_questions'],['How is data kept?'])
        self.assertEqual(len(second['plan']['batches']),4);self.assertFalse(second['calls'])
        self.assertEqual(path.read_bytes(),original);self.assertEqual(self.study,before)

    def test_profile_change_and_stale_application_binding_refuse_resume(self):
        client=SessionClient();build_resumable_plan(self.study,client,self.root/'one',max_native_measurements=1)
        prior=json.loads((self.root/'one/planning-session.json').read_text(encoding='utf-8'))
        client.config.limits=replace(client.config.limits,output_tokens=256)
        with self.assertRaisesRegex(ValueError,'identity'):
            build_resumable_plan(self.study,client,self.root/'two',prior=prior)
        bad=deepcopy(prior);key=next(iter(bad['application_request_sha256']));bad['application_request_sha256'][key]='0'*64
        with self.assertRaisesRegex(ValueError,'prompt/schema'):validate_planning_session(bad)
        self.assertEqual(len(client.measured),1)

    def test_failed_definition_retry_retains_failure_and_does_not_repeat_codes(self):
        client=SessionClient([CODE,RuntimeError('synthetic timeout'),DEFINE])
        session=build_native_plan(self.study,client)
        failed=execute_pending(session,client,self.root/'failed',max_generation_calls=2)
        before=deepcopy(failed);code_id=failed['codes'][0]['id']
        retried=retry_definitions(failed,client,self.root/'retry',code_ids=[code_id],max_generation_calls=1)
        self.assertEqual(failed,before);self.assertEqual(retried['plan'],failed['plan'])
        self.assertEqual(len(retried['codes']),1);self.assertEqual(len(client.generated),3)
        self.assertEqual(retried['calls'][:2],failed['calls'])
        self.assertEqual(retried['calls'][1]['status'],'failed')
        self.assertEqual(retried['codes'][0]['definition_status'],'define')
        self.assertEqual(retried['codes'][0]['definition_history'][1]['previous']['definition_status'],'failed')
        self.assertEqual(retried['codes'][0]['definition_origin'],'local_model_retry')
        self.assertFalse(retried['codes'][0]['human_review']);validate_session(retried)
        with self.assertRaisesRegex(ValueError,'unfinished'):
            retry_definitions(retried,client,self.root/'invalid',code_ids=[code_id])
        self.assertEqual(len(client.generated),3)

    def test_call_budget_leaves_definition_pending_and_explicit_retry_only(self):
        client=SessionClient([CODE,DEFINE]);session=build_native_plan(self.study,client)
        result=execute_pending(session,client,self.root/'one',max_generation_calls=1)
        self.assertEqual(len(client.generated),1)
        self.assertEqual(result['codes'][0]['definition_status'],'not_requested')
        self.assertEqual(result['coverage']['work_status_counts'],{'completed':1,'pending':3})
        retry=retry_definitions(result,client,self.root/'two',code_ids=result['unfinished_definition_code_ids'])
        self.assertEqual(retry['coverage'],result['coverage'])
        self.assertEqual(len(client.generated),2)

    def test_legacy_failure_without_history_keeps_attempt_number_and_retry_origin(self):
        client=SessionClient([CODE,{'bad':'scope'},DEFINE]);session=build_native_plan(self.study,client)
        failed=execute_pending(session,client,self.root/'legacy-base',max_generation_calls=2)
        failed['codes'][0].pop('definition_history')
        before=deepcopy(failed)
        retried=retry_definitions(failed,client,self.root/'legacy-retry',code_ids=failed['unfinished_definition_code_ids'])
        self.assertEqual(failed,before)
        self.assertEqual(retried['calls'][:2],failed['calls'])
        self.assertEqual(retried['calls'][-1]['attempt'],2)
        self.assertTrue(retried['calls'][-1]['explicit_retry'])
        self.assertEqual(retried['codes'][0]['definition_origin'],'local_model_retry')
        self.assertFalse(retried['codes'][0]['definition_human_review'])

    def test_invalid_complete_states_contracts_and_stale_queue_are_rejected(self):
        client=SessionClient([CODE]);session=build_native_plan(self.study,client)
        result=execute_pending(session,client,self.root/'run',max_generation_calls=1)
        for patch in ({'definition_status':'define'},
                      {'definition_status':'insufficient','definition_raw':{'decision':'insufficient','reason':''}},
                      {'scope_contract':'unknown-v999'}):
            bad=deepcopy(result);bad['codes'][0].update(patch)
            with self.subTest(patch=patch),self.assertRaises(ValueError):validate_session(bad)
        bad=deepcopy(result);bad['unfinished_definition_code_ids']=[]
        with self.assertRaisesRegex(ValueError,'queue'):validate_session(bad)

    def test_default_plan_cancel_is_interruption_not_failed_source_units(self):
        from themeforge.study_batches import PlanningInterrupted
        event=threading.Event();event.set();client=SessionClient()
        with self.assertRaises(PlanningInterrupted):build_native_plan(self.study,client,cancel_event=event)
        self.assertEqual(client.measured,[])


if __name__=='__main__':unittest.main()

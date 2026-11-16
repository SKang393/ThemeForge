"""Lightweight withdrawn Tk QA fixtures. No native model, network or build."""
from hashlib import sha256
import json
from pathlib import Path
import queue
import tempfile
import threading
import unittest
from unittest.mock import patch

from themeforge.analysis_types import TranscriptDocument
from themeforge.study_batches import prepare_study
from themeforge.study_native_qa import _FirstPassageQueue, study_native_smoke
from themeforge.study_session import build_native_plan
from tests.test_study_session import SessionClient, CODE, DEFINE


class StudyNativeQATests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'tmp')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.study = self.root/'study.json'
        study = prepare_study([TranscriptDocument(f'synthetic-{i:02}',f'Interviewer 1: What do you keep?\nParticipant 1: We keep a backup copy on a separate drive for project {i}.') for i in range(25)])
        self.study.write_text(json.dumps(study),encoding='utf-8')
        self.exe,self.model = self.root/'llama-server.exe',self.root/'synthetic.gguf'
        self.exe.write_bytes(b'MZ-synthetic-not-executable');self.model.write_bytes(b'GGUF-synthetic-not-model')
        self.profile = self.root/'profile.json'
        self.profile.write_text(json.dumps({'executable_path':str(self.exe),'executable_sha256':sha256(self.exe.read_bytes()).hexdigest(),'model_path':str(self.model),'model_sha256':sha256(self.model.read_bytes()).hexdigest(),'limits':{'memory_limit_bytes':1024**3}}),encoding='utf-8')
        self.guard = patch('subprocess.Popen',side_effect=AssertionError('Synthetic QA must never start a process'))
        self.guard.start();self.addCleanup(self.guard.stop)

    def test_queue_cancels_in_worker_before_next_target_not_during_planning(self):
        events = queue.Queue();cancel = threading.Event()
        hook = _FirstPassageQueue(events,cancel)
        hook.put(('progress',{'stage':'native_planning','requests_measured':25}))
        self.assertFalse(cancel.is_set())
        hook.put(('progress',{'stage':'coding','codes':1}))
        self.assertTrue(cancel.is_set())
        hook.put(('progress',{'stage':'coding','codes':2}))
        self.assertEqual(hook.first_coding_progress,{'stage':'coding','codes':1})
        self.assertEqual(hook.get_nowait()[1]['stage'],'native_planning')

    def test_explicit_hidden_app_full_plan_one_passage_definition_and_cleanup(self):
        clients=[];closed=threading.Event();started=threading.Event()
        def factory(config,**kwargs):
            client=SessionClient([CODE,DEFINE]);client.config=config
            client.start=lambda cancel:started.set()
            client.close=closed.set
            clients.append(client)
            return client
        before=(self.study.read_bytes(),self.profile.read_bytes())
        output=self.root/'qa'
        self.assertEqual(study_native_smoke(self.study,self.profile,output,client_factory=factory,deadline_seconds=30),0)
        report=json.loads((output/'QA.json').read_text())
        self.assertEqual(report['status'],'passed')
        self.assertTrue(report['synthetic_test_only'])
        self.assertFalse(report['real_native_requested'])
        self.assertEqual(report['imported_turns'],50)
        self.assertEqual(report['eligible_original_turns'],25)
        self.assertEqual(report['native_batches'],25)
        self.assertEqual(report['passage_call_count'],1)
        self.assertEqual(report['raw_call_count'],2)
        self.assertEqual(report['definition_statuses'],['define'])
        self.assertEqual(report['coverage']['work_status_counts'],{'completed':1,'pending':24})
        self.assertTrue(started.is_set() and closed.is_set())
        self.assertEqual(len(clients),1)
        self.assertEqual(len(clients[0].generated),2)
        self.assertFalse(report['owned_worker_remaining'])
        self.assertTrue(report['owned_clients_closed'])
        self.assertTrue(report['main_project_unchanged'])
        self.assertTrue(report['export_JSON_reopen_exact'])
        self.assertGreater(report['resources']['sample_count'],0)
        self.assertGreater(report['resources']['peak_sampled_owned_tree_rss_bytes'],0)
        self.assertEqual((self.study.read_bytes(),self.profile.read_bytes()),before)
        self.assertIn('native-session-export.html',report['outputs'])

    def test_startup_failure_retained_not_reported_as_success(self):
        closed=threading.Event()
        def factory(config,**kwargs):
            client=SessionClient();client.config=config
            def fail(cancel):
                raise RuntimeError('synthetic startup blocked')
            client.start=fail;client.close=closed.set
            return client
        output=self.root/'failure-qa'
        self.assertEqual(study_native_smoke(self.study,self.profile,output,client_factory=factory,deadline_seconds=30),1)
        report=json.loads((output/'QA.json').read_text())
        self.assertEqual(report['status'],'failed')
        self.assertTrue(closed.is_set())
        failure_files=list((output/'work').glob('study-*/failure.json'))
        self.assertEqual(len(failure_files),1)
        self.assertIn('synthetic startup blocked',json.loads(failure_files[0].read_text())['error'])
        self.assertTrue(report['owned_clients_closed'])
        self.assertFalse(report['owned_worker_remaining'])
        self.assertFalse(list(output.glob('native-session-export.*')))

    def test_unexecuted_whole_plan_resumes_without_replanning_or_prior_answers(self):
        study=json.loads(self.study.read_text(encoding='utf-8'))
        client=SessionClient()
        from themeforge.local_inference import LocalInferenceConfig, InferenceLimits
        profile=json.loads(self.profile.read_text(encoding='utf-8'))
        client.config=LocalInferenceConfig(executable_path=self.exe,model_path=self.model,
            executable_sha256=profile['executable_sha256'],model_sha256=profile['model_sha256'],
            limits=InferenceLimits(context_tokens=4096,output_tokens=512,threads=2,wall_seconds=180,
                startup_seconds=90,max_model_bytes=3*1024**3,memory_limit_bytes=1024**3))
        session=build_native_plan(study,client)
        path=self.root/'plan.json';path.write_text(json.dumps(session),encoding='utf-8')
        clients=[]
        def factory(config,**kwargs):
            instance=SessionClient([CODE,DEFINE]);instance.config=config
            instance.start=lambda cancel:None;instance.close=lambda:None
            clients.append(instance)
            return instance
        with patch('themeforge.study_dialog.build_native_plan',side_effect=AssertionError('Must reuse measured plan')):
            result=study_native_smoke(self.study,self.profile,self.root/'resume',session=path,
                client_factory=factory,deadline_seconds=30)
        report=json.loads((self.root/'resume/QA.json').read_text(encoding='utf-8'))
        self.assertEqual(result,0,report.get('error'))
        self.assertTrue(report['explicit_unexecuted_plan_resume'])
        self.assertEqual(report['codes'],1)
        self.assertEqual(report['passage_call_count'],1)
        self.assertEqual(len(clients[0].generated),2)

    def test_fresh_output_and_absolute_paths_fail_before_client_creation(self):
        calls=[]
        output=self.root/'existing';output.mkdir()
        with self.assertRaisesRegex(ValueError,'fresh'):
            study_native_smoke(self.study,self.profile,output,client_factory=lambda *a,**k:calls.append(True))
        with self.assertRaisesRegex(ValueError,'absolute'):
            study_native_smoke('relative.json',self.profile,self.root/'unused',client_factory=lambda *a,**k:calls.append(True))
        self.assertFalse(calls)
        self.assertFalse((self.root/'unused').exists())


if __name__ == '__main__':
    unittest.main()

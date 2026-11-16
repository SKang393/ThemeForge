"""Synthetic workers only: no ONNX, tokenizer, model or subprocess execution."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from themeforge.analysis_types import TranscriptDocument
from themeforge.study_batches import prepare_study
from themeforge import study_encoder as module


def make_study():
    return prepare_study([TranscriptDocument(f'synthetic-{source}',
        'Interviewer: What affects your choice?\n'+'\n'.join(
            f'P{1+i%2}: I might not share item {i} from project {source} unless permission arrives.'
            for i in range(12))) for source in range(3)])


class StudyEncoderTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.folder=Path(self.temp.name)
        self.profile=self.folder/'profile.json'
        self.raw=dict(artifact_manifest_sha256='a'*64,runtime=dict(numpy_version='synthetic-numpy',
            version='synthetic-ort',tokenizers_version='synthetic-tokenizer'))
        self.profile.write_text(json.dumps(self.raw),encoding='utf-8')
        self.study=make_study();self.calls=[]
        self.verifier=patch.object(module.encoder,'verify_profile',return_value=(self.raw,None,None,2))
        self.verifier.start();self.addCleanup(self.verifier.stop);self.addCleanup(self.temp.cleanup)
        self.worker=patch.object(module,'_run_chunk',side_effect=self.fake_worker)
        self.worker.start();self.addCleanup(self.worker.stop)

    def fake_worker(self,packet,path,pin,out,**kwargs):
        self.calls.append([n['id'] for n in packet['nodes']])
        diagnostics=[]
        for n in packet['nodes']:
            diagnostics.append(dict(node_id=n['id'],text_sha256=sha256(n['text'].encode()).hexdigest(),
                truncated_tokens=0,payload_tokens=3,chunks=[dict(payload_token_start=0,payload_token_end=3,
                    source_char_start=0,source_char_end=len(n['text']),payload_tokens=3,input_ids=[101,201,202,203,102])]))
        provenance=dict(adapter=module.encoder.VERSION,repository=module.encoder.MODEL_REPOSITORY,
            revision=module.encoder.MODEL_REVISION,model_sha256=module.encoder.MODEL_SHA256,
            tokenizer_sha256=module.encoder.TOKENIZER_SHA256,profile_sha256=pin,
            provider='CPUExecutionProvider',intra_op_threads=2,inter_op_threads=1,dimension=384,
            pooling=module.POOLING,original_text_preserved=True,input_fields=['node.text'],training=False,
            network_calls=0,tokenization=diagnostics,runtime_versions={'numpy':'synthetic-numpy',
                'onnxruntime':'synthetic-ort','tokenizers':'synthetic-tokenizer'})
        return dict(status='completed',encoded=dict(vectors=[[1.]+[0.]*383 for n in packet['nodes']],
            provenance=provenance),resources=dict(owned_processes_closed=True,synthetic_no_process=True))

    def test_more_than_twenty_units_without_global_turn_cap_and_exact_windows(self):
        result=module.encode_study(self.study,self.profile,chunk_size=8,output_directory=self.folder)
        eligible=[u for u in self.study['units'] if u['eligible']]
        self.assertGreater(len(eligible),20)
        self.assertEqual(len(result['vectors']),len(eligible))
        self.assertEqual(result['status'],'completed')
        self.assertTrue(all(len(batch)<=8 for batch in self.calls))
        self.assertEqual(result['coverage_counts']['skipped'],3)
        self.assertEqual(result['study'],self.study)
        module.validate_encoder_ledger(result)
        sources={s['source_name']:s['text'] for s in self.study['sources']}
        for uid,state in result['coverage'].items():
            for window in state.get('token_windows',[]):
                self.assertEqual(window['parent_unit_id'],uid)
                self.assertEqual(sources[window['source_name']][window['source_start']:window['source_end']],window['text'])
        saved=[module.load_encoder_ledger(path) for path in self.folder.glob('run-*/encoder-ledger-progress-*.json')]
        latest=max(saved,key=lambda ledger:ledger['runs'][-1]['checkpoint_sequence'])
        self.assertEqual(latest,result)

    def test_resume_keeps_completed_vectors_and_never_repeats_completed_units(self):
        first=module.encode_study(self.study,self.profile,max_units_per_run=7,chunk_size=3)
        self.assertEqual(first['status'],'paused_work_limit')
        self.assertEqual(first['coverage_counts']['completed'],7)
        before=deepcopy(first);completed=set(first['vectors']);self.calls.clear()
        second=module.encode_study(self.study,self.profile,prior=first,chunk_size=8)
        self.assertEqual(first,before)
        self.assertFalse(completed & {uid for batch in self.calls for uid in batch})
        self.assertTrue(all(second['vectors'][uid]==first['vectors'][uid] for uid in completed))
        self.assertEqual(second['runs'][0],first['runs'][0])
        self.assertEqual(second['runs'][1]['before_ledger_sha256'],first['ledger_sha256'])
        self.assertEqual(second['status'],'completed')
        self.assertEqual(json.loads(module.export_encoder_ledger_json(second)),second)

    def test_incompatible_profile_and_modified_source_cannot_resume(self):
        first=module.encode_study(self.study,self.profile,max_units_per_run=1)
        other=self.folder/'other.json';other.write_text(json.dumps(self.raw,indent=2),encoding='utf-8')
        self.calls.clear()
        with self.assertRaisesRegex(ValueError,'same exact study and encoder profile'):
            module.encode_study(self.study,other,prior=first)
        wrong=deepcopy(first);wrong['study']['units'][1]['text']='Invented text'
        wrong['ledger_sha256']=module.digest({k:v for k,v in wrong.items() if k!='ledger_sha256'})
        with self.assertRaises(ValueError):module.validate_encoder_ledger(wrong)
        self.assertEqual(self.calls,[])

    def test_cancellation_before_and_between_workers_preserves_pending(self):
        event=threading.Event();event.set()
        stopped=module.encode_study(self.study,self.profile,cancel_event=event)
        self.assertEqual(stopped['status'],'cancelled');self.assertEqual(self.calls,[])
        self.assertEqual(stopped['vectors'],{})
        event.clear()
        def cancel_after_one(*args,**kwargs):
            result=self.fake_worker(*args,**kwargs);event.set();return result
        with patch.object(module,'_run_chunk',side_effect=cancel_after_one):
            during=module.encode_study(self.study,self.profile,cancel_event=event)
        self.assertEqual(during['status'],'cancelled')
        self.assertEqual(during['coverage_counts']['completed'],1)
        self.assertGreater(during['coverage_counts']['pending'],0)
        module.validate_encoder_ledger(during)

    def test_worker_time_memory_and_cleanup_stops_are_explicit(self):
        for stop,closed,expected in [('time_limit',True,'paused_time_limit'),
                                     ('memory_limit',True,'paused_memory_limit'),
                                     ('cleanup_failed',False,'cleanup_failed')]:
            with self.subTest(stop=stop),patch.object(module,'_run_chunk',return_value={
                    'status':stop,'resources':{'owned_processes_closed':closed}}) as worker:
                result=module.encode_study(self.study,self.profile)
                self.assertEqual(result['status'],expected);self.assertEqual(worker.call_count,1)
                self.assertGreater(result['coverage_counts']['pending'],0)
                self.assertEqual(result['vectors'],{})
                module.validate_encoder_ledger(result)

    def test_overlong_or_bad_token_contract_is_failed_without_truncation(self):
        with patch.object(module,'_run_chunk',return_value=dict(status='failed',
                error='Source turn exceeds eight explicit 254-token chunks.',resources={'owned_processes_closed':True})):
            failed=module.encode_study(self.study,self.profile,max_units_per_run=1)
        self.assertEqual(failed['coverage_counts']['failed'],1);self.assertEqual(failed['vectors'],{})
        def malformed(*args,**kwargs):
            result=self.fake_worker(*args,**kwargs)
            result['encoded']['provenance']['tokenization'][0]['truncated_tokens']=1
            return result
        with patch.object(module,'_run_chunk',side_effect=malformed):
            bad=module.encode_study(self.study,self.profile,max_units_per_run=1)
        self.assertEqual(bad['coverage_counts']['failed'],1);self.assertEqual(bad['vectors'],{})
        module.validate_encoder_ledger(bad)

    def test_mixed_worker_profile_is_rejected_not_merged(self):
        def mismatched(*args,**kwargs):
            result=self.fake_worker(*args,**kwargs)
            result['encoded']['provenance']['model_sha256']='b'*64
            return result
        with patch.object(module,'_run_chunk',side_effect=mismatched):
            result=module.encode_study(self.study,self.profile,max_units_per_run=1)
        self.assertEqual(result['coverage_counts']['failed'],1)
        self.assertFalse(result['vectors'])

    def test_explicit_retry_preserves_failed_history_and_completed_units(self):
        good=module.encode_study(self.study,self.profile,max_units_per_run=1)
        with patch.object(module,'_run_chunk',return_value=dict(status='failed',error='collateral batch failure')):
            failed=module.encode_study(self.study,self.profile,prior=good,max_units_per_run=1)
        uid=next(uid for uid,state in failed['coverage'].items() if state['status']=='failed')
        original=deepcopy(failed)
        retried=module.retry_failed_units(failed,[uid],reason='Isolate source unit from failed batch.')
        self.assertEqual(failed,original);self.assertEqual(retried['batches'],failed['batches'])
        self.assertEqual(retried['recovery_events'][0]['prior_coverage'][uid],failed['coverage'][uid])
        self.calls.clear()
        resumed=module.encode_study(self.study,self.profile,prior=retried,max_units_per_run=1)
        self.assertEqual(self.calls,[[uid]]);self.assertEqual(resumed['vectors'][uid],[1.]+[0.]*383)
        self.assertEqual(resumed['coverage_counts']['completed'],2)
        with self.assertRaisesRegex(ValueError,'Only explicitly selected failed'):
            module.retry_failed_units(good,list(good['vectors']),reason='repeat')

    def test_recovery_binds_artifacts_without_worker_and_retains_failure(self):
        artifacts={}
        def cleanup_failure(packet,path,pin,out,**kwargs):
            output=self.fake_worker(packet,path,pin,out,**kwargs)
            for name,value in [('packet.json',packet),('encoded.json',output['encoded']),
                    ('report.json',dict(status='completed',backend='sentence_embeddings',
                     packet_sha256=packet['packet_sha256'],source_turns=len(packet['nodes'])))]:
                p=self.folder/name;p.write_text(json.dumps(value),encoding='utf-8');artifacts[name]=p
            return dict(status='cleanup_failed',resources=dict(owned_processes_closed=False))
        with patch.object(module,'_run_chunk',side_effect=cleanup_failure):
            failed=module.encode_study(self.study,self.profile,max_units_per_run=1)
        self.calls.clear();before=deepcopy(failed)
        kwargs=dict(packet_path=artifacts['packet.json'],encoded_path=artifacts['encoded.json'],
            report_path=artifacts['report.json'],cleanup_evidence='Synthetic process fixture has no native processes.',
            reason='Valid output survived cleanup observation failure.')
        recovered=module.recover_failed_batch(failed,failed['batches'][0]['id'],**kwargs)
        self.assertEqual(failed,before);self.assertEqual(self.calls,[])
        self.assertEqual(recovered['coverage_counts']['completed'],1)
        self.assertEqual(recovered['batches'][0],failed['batches'][0])
        self.assertEqual(recovered['batches'][1]['recovered_from_batch_id'],failed['batches'][0]['id'])
        raw=json.loads(artifacts['encoded.json'].read_text(encoding='utf-8'))
        raw['provenance']['tokenization'][0]['text_sha256']='0'*64
        artifacts['encoded.json'].write_text(json.dumps(raw),encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'unchanged source turn'):
            module.recover_failed_batch(failed,failed['batches'][0]['id'],**kwargs)

    def test_no_eligible_units_needs_no_profile_and_starts_no_worker(self):
        study=prepare_study([TranscriptDocument('synthetic-empty-routing','Interviewer: What should happen next?')])
        result=module.encode_study(study,None)
        self.assertEqual(result['status'],'no_eligible_units');self.assertEqual(self.calls,[])
        self.assertEqual(result['vectors'],{});module.validate_encoder_ledger(result)

    def test_cleanup_waits_for_terminating_descendants_and_ignores_reused_pids(self):
        class NoSuchProcess(Exception):pass
        class TimeoutExpired(Exception):pass
        class AccessDenied(Exception):pass
        waited=[]
        class Child:
            def __init__(self,pid,created,stuck=False):self.pid=pid;self.created=created;self.stuck=stuck
            def create_time(self):return self.created
            def wait(self,*,timeout):
                waited.append((self.pid,timeout))
                if self.stuck:raise TimeoutExpired()
        children={10:Child(10,100.),11:Child(11,101.),12:Child(12,999.)}
        api=SimpleNamespace(Process=children.__getitem__,NoSuchProcess=NoSuchProcess,
                            TimeoutExpired=TimeoutExpired,AccessDenied=AccessDenied)
        self.assertTrue(module._wait_owned_closed({10:100.,11:101.,12:102.},api))
        self.assertEqual({pid for pid,_ in waited},{10,11})
        self.assertTrue(all(0<=seconds<=5 for _,seconds in waited))
        children[11].stuck=True
        self.assertFalse(module._wait_owned_closed({10:100.,11:101.},api))


if __name__=='__main__':unittest.main()

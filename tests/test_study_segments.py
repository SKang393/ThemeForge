"""Synthetic tokenizer and workers only; no model or native inference."""
from copy import deepcopy
import json
from pathlib import Path
import re
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from themeforge.analysis_types import TranscriptDocument
from themeforge.study_batches import prepare_study
from themeforge import study_encoder as se
from themeforge import study_segments as module


class Tokens:
    def encode(self,text,*,add_special_tokens=False):
        matches=list(re.finditer(r'\S+',text))
        return SimpleNamespace(ids=[200+len(m.group()) for m in matches],offsets=[m.span() for m in matches])


class StudySegmentTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.profile=self.root/'profile.json'
        self.raw=dict(artifact_manifest_sha256='a'*64,runtime=dict(numpy_version='fake-numpy',version='fake-ort',tokenizers_version='fake-tokenizers'))
        self.profile.write_text(json.dumps(self.raw),encoding='utf-8');self.tok=Tokens();self.calls=[]
        verify=patch.object(se.encoder,'verify_profile',return_value=(self.raw,None,None,2));verify.start();self.addCleanup(verify.stop)
        tokenizer=patch.object(module,'_tokenizer',return_value=self.tok);tokenizer.start();self.addCleanup(tokenizer.stop)
        self.prior=self.failed_parent('Welcome.  \n'+''.join(f'I keep item {i} when consent applies. \n' for i in range(40))+
            'Participant 3 Responses, Synthetic\n'+''.join(f'Another source item {i} remains provisional.\n' for i in range(45))+
            'Interview with Participant 4, Synthetic\nInterview conducted: 01/01/2000')
        self.parent_id=next(uid for uid,state in self.prior['coverage'].items() if state['status']=='failed')
        worker=patch.object(se,'_run_chunk',side_effect=self.worker);worker.start();self.addCleanup(worker.stop)

    def failed_parent(self,text):
        study=prepare_study([TranscriptDocument('synthetic-segments','Interviewer: What happens?\nP1: '+text)])
        with patch.object(se,'_run_chunk',return_value=dict(status='failed',error='Original synthetic source failure.')):
            return se.encode_study(study,self.profile,max_units_per_run=1,output_directory=self.root/'original')

    def prepare(self):return module.prepare_segment_recovery(self.prior,self.parent_id,self.profile,tokenizer=self.tok)

    def worker(self,packet,path,pin,out,**kwargs):
        self.calls.extend(n['id'] for n in packet['nodes']);diagnostics=[]
        for n in packet['nodes']:
            chunks=se.encoder.token_chunks(self.tok,n['text'])
            diagnostics.append(dict(node_id=n['id'],text_sha256=module._hash_text(n['text']),truncated_tokens=0,
                payload_tokens=sum(c['payload_tokens'] for c in chunks),chunks=chunks))
        return dict(status='completed',resources=dict(owned_processes_closed=True,synthetic_no_process=True),
            encoded=dict(vectors=[[1.]+[0.]*383 for n in packet['nodes']],provenance=dict(
                adapter=se.encoder.VERSION,repository=se.encoder.MODEL_REPOSITORY,revision=se.encoder.MODEL_REVISION,
                model_sha256=se.encoder.MODEL_SHA256,tokenizer_sha256=se.encoder.TOKENIZER_SHA256,profile_sha256=pin,
                provider='CPUExecutionProvider',intra_op_threads=2,inter_op_threads=1,dimension=384,pooling=se.POOLING,
                original_text_preserved=True,input_fields=['node.text'],training=False,network_calls=0,
                tokenization=diagnostics,runtime_versions=dict(numpy='fake-numpy',onnxruntime='fake-ort',tokenizers='fake-tokenizers'))))

    def test_exact_partition_whitespace_structural_cuts_and_special_token_budget(self):
        plan=self.prepare();parent=plan['plan']['parent'];segments=plan['plan']['segments']
        self.assertEqual(''.join(s['text'] for s in segments),parent['text'])
        self.assertTrue(all(a['source_end']==b['source_start'] for a,b in zip(segments,segments[1:])))
        self.assertTrue(all(s['input_tokens']<=256 and s['encoder_output_tokens']==0 for s in segments))
        self.assertEqual(len(plan['plan']['structural_hard_cuts']),4)
        self.assertTrue(all(not(s['source_start']<c<s['source_end']) for s in segments for c in plan['plan']['structural_hard_cuts']))
        self.assertEqual(plan['before_ledger_sha256'],self.prior['ledger_sha256']);self.assertEqual(self.calls,[])
        self.assertTrue(all(len(u['execution_context_spans'])<=2 for u in plan['plan']['derived_study']['units']))

    def test_resume_only_new_core_ids_and_preserve_all_original_state(self):
        plan=self.prepare();unchanged=deepcopy(plan)
        first=module.run_segment_recovery(plan,self.profile,output_directory=self.root/'recovery',max_units_per_run=1)
        self.assertEqual(plan,unchanged);self.assertEqual(first['original_encoder_ledger'],self.prior)
        self.assertEqual(first['recovery_coverage']['segment_counts']['completed'],1)
        completed=set(first['encoder_ledger']['vectors']);self.calls.clear()
        final=module.run_segment_recovery(first,self.profile,output_directory=self.root/'recovery')
        self.assertFalse(completed&set(self.calls));self.assertFalse(set(self.prior['coverage'])&set(self.calls))
        self.assertEqual(final['recovery_coverage']['status'],'covered_via_segments_not_attribution_validated')
        self.assertNotIn(self.parent_id,final['encoder_ledger']['vectors'])
        self.assertEqual(final['original_encoder_ledger'],self.prior)
        self.assertEqual(len(list((self.root/'recovery').glob('segment-recovery-*'))),2)
        checkpoints=list((self.root/'recovery').glob('segment-recovery-*/segment-recovery-progress-*.json'))
        self.assertTrue(checkpoints)
        for path in checkpoints:module.load_segment_recovery(path)
        self.assertEqual(module.recovery_coverage(final,self.prior['study']),final['recovery_coverage'])

    def test_completed_parent_rejected_and_changed_tokenizer_plan_cannot_run(self):
        completed=se.encode_study(self.prior['study'],self.profile,output_directory=self.root/'completed')
        with self.assertRaisesRegex(ValueError,'never a completed'):
            module.prepare_segment_recovery(completed,self.parent_id,self.profile,tokenizer=self.tok)
        plan=self.prepare();self.calls.clear()
        class Changed(Tokens):
            def encode(self,*args,**kwargs):
                value=super().encode(*args,**kwargs);value.ids=[x+1 for x in value.ids];return value
        with patch.object(module,'_tokenizer',return_value=Changed()),self.assertRaisesRegex(ValueError,'Real pinned tokenizer'):
            module.run_segment_recovery(plan,self.profile,output_directory=self.root/'wrong')
        self.assertEqual(self.calls,[])

    def test_oversized_indivisible_sentence_fails_without_worker_or_truncation(self):
        prior=self.failed_parent(' '.join(['synthetic']*260)+'.')
        uid=next(uid for uid,state in prior['coverage'].items() if state['status']=='failed')
        plan=module.prepare_segment_recovery(prior,uid,self.profile,tokenizer=self.tok)
        self.calls.clear();result=module.run_segment_recovery(plan,self.profile,output_directory=self.root/'oversize')
        self.assertEqual(self.calls,[]);self.assertEqual(result['recovery_coverage']['segment_counts'],dict(failed=1))
        self.assertEqual(result['encoder_ledger']['vectors'],{})
        self.assertFalse(result['recovery_coverage']['attribution_validated'])

    def test_exact_encoder_capacity_and_one_token_over_keep_distinct_outcomes(self):
        for count in (254,255):
            with self.subTest(payload_tokens=count):
                prior=self.failed_parent(' '.join(['synthetic']*count)+'.')
                uid=next(k for k,v in prior['coverage'].items() if v['status']=='failed')
                plan=module.prepare_segment_recovery(prior,uid,self.profile,tokenizer=self.tok)
                self.assertEqual(plan['plan']['segments'][0]['input_tokens'],count+2)
                self.calls.clear()
                result=module.run_segment_recovery(plan,self.profile,output_directory=self.root/str(count))
                self.assertEqual(len(self.calls),1 if count==254 else 0)
                self.assertEqual(result['recovery_coverage']['segment_counts'],{'completed':1} if count==254 else {'failed':1})
                self.assertEqual(result['plan']['parent']['text'],plan['plan']['parent']['text'])

    def test_changed_context_or_original_digest_rejected_even_after_outer_reseal(self):
        for kind in ('context','original'):
            value=self.prepare()
            if kind=='context':
                row=next(s for s in value['plan']['segments'] if s['context_spans']);row['context_spans']=[]
                unit=next(u for u in value['plan']['derived_study']['units'] if u['id']==row['id']);unit['execution_context_spans']=[]
                value['plan']['derived_study']['study_sha256']=module.digest({k:v for k,v in value['plan']['derived_study'].items() if k!='study_sha256'})
                value['plan']['plan_sha256']=module.digest({k:v for k,v in value['plan'].items() if k!='plan_sha256'})
            else:value['before_ledger_sha256']='0'*64
            module._seal(value)
            with self.assertRaises(ValueError):module.validate_segment_recovery(value)

    def test_completed_core_exact_plan_tokens_are_required(self):
        result=module.run_segment_recovery(self.prepare(),self.profile,output_directory=self.root/'tokenproof',max_units_per_run=1)
        saved=result['encoder_ledger'];batch=next(b for b in saved['batches'] if b['status']=='completed')
        batch['encoded']['provenance']['tokenization'][0]['chunks'][0]['input_ids'][1]+=1
        se._seal(saved);module._seal(result)
        with self.assertRaisesRegex(ValueError,'exact planned token IDs'):
            module.validate_segment_recovery(result)


if __name__=='__main__':unittest.main()

"""Pure retrieval tests with synthetic tokenizer/worker fixtures, no inference."""
from copy import deepcopy
import json
import threading
import unittest
from unittest.mock import patch

from themeforge.analysis_types import TranscriptDocument
from themeforge.study_batches import prepare_study
from themeforge.study_neighbors import build_study_neighbors,NeighborLimits
from themeforge import study_encoder as se
from themeforge import study_segments as segments
from themeforge import study_recovered_graph as module
from tests import test_study_segments as helpers


class RecoveredGraphTests(unittest.TestCase):
    def setUp(self):
        self.fixture=helpers.StudySegmentTests('test_exact_partition_whitespace_structural_cuts_and_special_token_budget')
        self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        parent=next(u for u in self.fixture.prior['study']['units'] if u['id']==self.fixture.parent_id)
        docs=[TranscriptDocument('synthetic-a','Interviewer: Why?\nP1: '+parent['text'])]
        docs.extend(TranscriptDocument('synthetic-'+letter,'Interviewer: Why?\nP1: '+text) for letter,text in (
            ('b','I preserve exact source evidence.'),('c','I ask permission before sharing.'),('d','I keep some records private.')))
        study=prepare_study(docs)
        self.pid=next(u['id'] for u in study['units'] if u['eligible'] and u['source_name']=='synthetic-a')
        def original_worker(packet,*args,**kwargs):
            if any(n['id']==self.pid for n in packet['nodes']):return dict(status='failed',error='Synthetic original boundary.')
            return self.fixture.worker(packet,*args,**kwargs)
        with patch.object(se,'_run_chunk',side_effect=original_worker):
            prior=se.encode_study(study,self.fixture.profile,output_directory=self.fixture.root/'multi-original')
        self.baseline=build_study_neighbors(study,prior['vectors'],batch_ids=prior['batch_ids'],
            embedding_provenance=prior['embedding_provenance'],encoding_coverage=prior['coverage'])
        self.plan=segments.prepare_segment_recovery(prior,self.pid,self.fixture.profile,tokenizer=self.fixture.tok)
        self.recovery=segments.run_segment_recovery(self.plan,self.fixture.profile,output_directory=self.fixture.root/'core-recovery')

    def test_complete_build_keeps_all_vectors_and_original_inputs_json_roundtrip_html(self):
        before=deepcopy(self.baseline);old_recovery=deepcopy(self.recovery)
        with patch.object(se,'_run_chunk',side_effect=AssertionError('No inference in retrieval')):
            result=module.build_recovered_graph(self.baseline,self.recovery,input_pins={'baseline_file_sha256':'a'*64})
            loaded=json.loads(module.export_recovered_graph_json(result));module.validate_recovered_graph(loaded)
            html=module.export_recovered_graph_html(loaded)
        self.assertEqual(self.baseline,before);self.assertEqual(self.recovery,old_recovery)
        cores=set(self.recovery['encoder_ledger']['vectors'])
        self.assertEqual(set(result['vectors']),set(self.baseline['vectors'])|cores)
        self.assertNotIn(self.pid,result['vectors'])
        self.assertEqual(result['parent_coverage'][self.pid]['status'],'covered_via_segments_not_attribution_validated')
        self.assertEqual(set(result['parent_coverage'][self.pid]['member_ids']),cores)
        self.assertEqual(set(result['attributions']),{u['id'] for u in result['units']})
        self.assertEqual(set(result['parent_attributions']),{u['id'] for u in result['source_study']['units']})
        self.assertTrue(all(uid in html for uid in cores));self.assertIn('Context only, not support',html)
        self.assertEqual(result['coverage_delta']['vector_source_records'],dict(added=len(cores),removed=0,unchanged=3))
        self.assertEqual(len({tuple(e['parent_ids']) for e in result['edges']}),len(result['edges']))

    def test_distinct_parent_ranking_uses_best_exact_member_not_mean_or_duplicate_slots(self):
        units=[dict(id=uid,source_hash=source*64) for uid,source in [('a1','a'),('a2','a'),('b','b'),('c','c')]]
        vectors=dict(a1=[1.,0.],a2=[-1.,0.],b=[1.,0.],c=[.8,.6])
        parents=dict(a1='A',a2='A',b='B',c='C')
        result=module._scan(units,vectors,parents,{},stop_after=100)
        edge=next(e for e in result['edges'] if e['parent_ids']==['A','B'])
        self.assertEqual(edge['member_ids'],['a1','b']);self.assertEqual(edge['cosine'],1.)
        self.assertEqual([r['parent_id'] for r in result['parent_top_neighbors']['B']],['A','C'])
        self.assertTrue(all(len(e['parent_ids'])==len(set(e['parent_ids'])) for e in result['edges']))
        tied=dict(vectors,a2=[1.,0.])
        reverse=module._scan(list(reversed(units)),dict(reversed(list(tied.items()))),parents,{},stop_after=100)
        self.assertEqual(next(e for e in reverse['edges'] if e['parent_ids']==['A','B'])['member_ids'],['a1','b'])

    def test_same_source_excluded_and_partial_scan_publishes_no_incomplete_pair(self):
        result=module.build_recovered_graph(self.baseline,self.recovery,limits=NeighborLimits(max_candidate_pairs=1))
        self.assertEqual(result['status'],'pair_limit');self.assertEqual(result['edges'],[])
        self.assertEqual(len(result['vectors']),len(self.baseline['vectors'])+len(self.recovery['encoder_ledger']['vectors']))
        event=threading.Event();event.set()
        stopped=module.build_recovered_graph(self.baseline,self.recovery,cancel_event=event)
        self.assertEqual(stopped['status'],'cancelled');self.assertEqual(stopped['edges'],[])
        units=[dict(id='a',source_hash='x'*64),dict(id='b',source_hash='x'*64)]
        same=module._scan(units,dict(a=[1.,0.],b=[1.,0.]),dict(a='A',b='B'),{})
        self.assertEqual(same['comparisons']['member_pairs_scored'],0);self.assertEqual(same['edges'],[])

    def test_partial_recovery_is_rejected_before_integration(self):
        partial=segments.run_segment_recovery(self.plan,self.fixture.profile,
            output_directory=self.fixture.root/'partial-core-recovery',max_units_per_run=1)
        with self.assertRaisesRegex(ValueError,'Every recovery core'):
            module.build_recovered_graph(self.baseline,partial)

    def test_duplicate_overlap_parent_child_and_same_parent_edge_tampering_rejected(self):
        original=module.build_recovered_graph(self.baseline,self.recovery)
        for mutation in ('duplicate','overlap','parent_vector','same_parent_edge'):
            result=deepcopy(original)
            if mutation=='duplicate':result['units'].append(deepcopy(result['units'][0]))
            elif mutation=='overlap':
                cores=[u for u in result['units'] if u.get('parent_unit_id')==self.pid]
                cores[1]['source_start']=cores[0]['source_start'];cores[1]['source_end']=cores[0]['source_end'];cores[1]['text']=cores[0]['text']
            elif mutation=='parent_vector':
                parent=next(u for u in result['source_study']['units'] if u['id']==self.pid)
                result['units'].append(deepcopy(parent));result['vectors'][self.pid]=[1.]+[0.]*383
            else:
                result['edges'].append(dict(parent_ids=[self.pid,self.pid],member_ids=list(result['vectors'])[:2],cosine=1.,ranks=[1,1]))
            module._seal(result)
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):module.validate_recovered_graph(result)

    def test_baseline_token_vector_and_replay_budget_tampering_rejected(self):
        original=module.build_recovered_graph(self.baseline,self.recovery)
        for mutation in ('baseline_edge','token','vector','scan_count'):
            result=deepcopy(original)
            if mutation=='baseline_edge':result['baseline']['edges'][0]['cosine']=.123
            elif mutation=='token':
                uid=next(uid for uid,p in result['vector_provenance'].items() if p['kind']=='recovered_core')
                result['vector_provenance'][uid]['tokenization']['chunks'][0]['input_ids'][1]+=1
            elif mutation=='vector':
                uid=next(iter(result['baseline']['vector_hashes']));result['vectors'][uid]=[0.,1.]+[0.]*382
                result['vector_provenance'][uid]['vector_sha256']=module.digest(result['vectors'][uid])
            else:result['comparisons']['member_pairs_scored']=result['limits']['max_candidate_pairs']+1
            module._seal(result)
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):module.validate_recovered_graph(result)

    def test_difference_separates_parent_member_and_rank_changes(self):
        before=[dict(member_ids=['A','B'],ranks=[1,2],cosine=.9)]
        after=[dict(parent_ids=['A','B'],member_ids=['a-core','B'],ranks=[2,1],cosine=.95)]
        difference=module._difference(before,after)
        self.assertEqual(difference['parent_pairs']['unchanged_count'],1)
        self.assertEqual(difference['member_pairs']['added_count'],1);self.assertEqual(difference['member_pairs']['removed_count'],1)
        self.assertEqual(len(difference['rank_changes']),1);self.assertEqual(len(difference['best_member_changes']),1)
        self.assertEqual(json.loads(json.dumps(difference)),difference)


if __name__=='__main__':unittest.main()

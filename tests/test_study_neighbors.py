"""Forty exact synthetic turns, no encoder/model or authentic study execution."""
from copy import deepcopy
from hashlib import sha256
import json
import threading
import unittest

from themeforge.study_neighbors import (
    NeighborLimits, build_study_neighbors, validate_study_neighbors,
    export_study_neighbors_json, export_study_neighbors_html,
)
from themeforge.semantic_neighbors import digest


def fixture():
    sources=[];units=[]
    for source in range(5):
        texts=[f'I might not share item {i} from source {source} unless permission arrives <tag>.' for i in range(source,40,5)]
        full='\r\n'.join(texts);source_hash=sha256(full.encode()).hexdigest();name=f'synthetic-{source}'
        sources.append(dict(source_name=name,source_hash=source_hash,text=full))
        offset=0
        for i,text in zip(range(source,40,5),texts):
            units.append(dict(id=f'u{i:02d}',source_name=name,source_hash=source_hash,speaker='P1',
                source_start=offset,source_end=offset+len(text),text=text,eligible=True,
                context_spans=[dict(source_name=name,source_hash=source_hash,source_start=offset,
                                    source_end=offset+len(text),text=text)]))
            offset+=len(text)+2
    units.sort(key=lambda u:u['id'])
    vectors={u['id']:([1.,0.,0.] if u['id'] in ('u00','u26') else [0.,1.,0.]) for u in units}
    batches={u['id']:f'batch-{i//4}' for i,u in enumerate(units)}
    return units,vectors,dict(source_index=sources,batch_ids=batches,
        embedding_provenance={'backend':'synthetic_vector_fixture','model_executed':False})


def seal(value):
    value['result_sha256']=digest({k:v for k,v in value.items() if k!='result_sha256'})


class StudyNeighborTests(unittest.TestCase):
    def test_whole_study_ledger_keeps_unassigned_turns_and_source_binding(self):
        from themeforge.analysis_types import TranscriptDocument
        from themeforge.study_batches import prepare_study
        docs=[TranscriptDocument(f'synthetic-{i}',
              f'Interviewer: What affects access {i}?\nP1: I might share measurements only if permission arrives for project {i}.')
              for i in range(2)]
        study=prepare_study(docs)
        vectors={u['id']:[1.,0.] for u in study['units'] if u['eligible']}
        batches={u['id']:f'batch-{i}' for i,u in enumerate(study['units'])}
        result=build_study_neighbors(study,vectors,batch_ids=batches,
                                     embedding_provenance={'backend':'synthetic_only'})
        self.assertEqual(result['source_study'],study)
        self.assertEqual(result['study_sha256'],study['study_sha256'])
        self.assertEqual(sum(result['coverage_counts'].values()),len(study['units']))
        self.assertEqual(result['coverage_counts']['skipped_ineligible'],2)
        validate_study_neighbors(result)

    def test_encoder_pending_failed_skipped_states_and_exact_source_binding(self):
        units,vectors,kwargs=fixture()
        mapping={u['id']:dict(parent_unit_id=u['id'],text_sha256=sha256(u['text'].encode()).hexdigest(),
            status='completed',reason='',batch_id=kwargs['batch_ids'][u['id']],
            vector_sha256=digest(vectors[u['id']])) for u in units}
        for uid,status,reason in [('u00','pending','Awaiting another work period.'),
                                 ('u01','failed','Existing token recipe limit.'),
                                 ('u02','skipped','Explicitly deferred.'),('u03','skipped','source_ineligible')]:
            vectors.pop(uid);mapping[uid].update(status=status,reason=reason)
            mapping[uid].pop('vector_sha256')
        units[3]['eligible']=False
        result=build_study_neighbors(units,vectors,encoding_coverage=mapping,**kwargs)
        self.assertEqual(result['status'],'completed_with_incomplete_encoding')
        self.assertEqual(result['coverage_counts'],{'pending_encoding':1,'failed_encoding':1,'skipped_encoding':2,'complete':36})
        for uid,status in [('u00','pending_encoding'),('u01','failed_encoding'),('u02','skipped_encoding'),('u03','skipped_encoding')]:
            self.assertEqual(result['coverage'][uid]['status'],status)
            self.assertEqual(result['coverage'][uid]['reason'],mapping[uid]['reason'])
            self.assertNotIn(uid,result['isolates'])
        self.assertEqual(result['encoding_coverage'],mapping)
        validate_study_neighbors(result)
        loaded=json.loads(export_study_neighbors_json(result));validate_study_neighbors(loaded)
        self.assertIn('pending_encoding',export_study_neighbors_html(result))
        for change in (lambda m:m['u00'].update(parent_unit_id='foreign'),
                       lambda m:m['u00'].update(text_sha256='a'*64),
                       lambda m:m['u00'].update(source_hash='a'*64),
                       lambda m:m['u00'].update(batch_id='foreign'),
                       lambda m:m.pop('u00')):
            bad=deepcopy(mapping);change(bad)
            with self.assertRaises(ValueError):build_study_neighbors(units,vectors,encoding_coverage=bad,**kwargs)
        changed=deepcopy(result);changed['coverage']['u00']['status']='failed_encoding';seal(changed)
        with self.assertRaises(ValueError):validate_study_neighbors(changed)
        # Explicit omission of the argument preserves legacy missing-vector semantics.
        legacy=build_study_neighbors(units,vectors,**kwargs)
        self.assertEqual(legacy['coverage']['u00']['status'],'failed_missing_vector')
        self.assertNotIn('encoding_coverage',legacy)
        validate_study_neighbors(legacy)

    def test_completed_encoding_remains_distinct_from_retrieval_guards(self):
        units,vectors,kwargs=fixture()
        mapping={u['id']:dict(parent_unit_id=u['id'],text_sha256=sha256(u['text'].encode()).hexdigest(),
            status='completed',reason='',batch_id=kwargs['batch_ids'][u['id']],vector_sha256=digest(vectors[u['id']])) for u in units}
        limited=build_study_neighbors(units,vectors,encoding_coverage=mapping,
                                      limits=NeighborLimits(max_working_bytes=1),**kwargs)
        self.assertEqual(limited['coverage_counts'],{'skipped_memory_limit':40})
        self.assertTrue(all(s['encoding_status']=='completed' for s in limited['coverage'].values()))
        validate_study_neighbors(limited)
        cancel=threading.Event();cancel.set()
        stopped=build_study_neighbors(units,vectors,encoding_coverage=mapping,cancel_event=cancel,**kwargs)
        self.assertEqual(stopped['coverage_counts'],{'skipped_cancelled':40})
        validate_study_neighbors(stopped)

    def test_forty_units_cross_batches_and_no_quadratic_output_or_forced_groups(self):
        units,vectors,kwargs=fixture();before=deepcopy((units,vectors,kwargs))
        result=build_study_neighbors(units,vectors,**kwargs)
        self.assertEqual(result['status'],'completed')
        self.assertEqual(result['coverage_counts'],{'complete':40})
        self.assertEqual(result['comparisons']['expected_cross_source_pairs'],640)
        self.assertEqual(result['comparisons']['pairs_scored'],640)
        edge=next(e for e in result['edges'] if e['member_ids']==['u00','u26'])
        self.assertTrue(edge['cross_batch']);self.assertEqual(edge['cosine'],1.)
        self.assertLessEqual(sum(map(len,result['top_neighbors'].values())),40*2)
        self.assertFalse({'pair_scores','matrix','categories','themes','groups','codes'} & result.keys())
        self.assertEqual((units,vectors,kwargs),before)
        validate_study_neighbors(result)

    def test_stable_ties_reordering_and_source_distinct_edges(self):
        units,vectors,kwargs=fixture()
        result=build_study_neighbors(units,vectors,**kwargs)
        again=build_study_neighbors(list(reversed(units)),dict(reversed(list(vectors.items()))),**kwargs)
        self.assertEqual(result['edges'],again['edges'])
        self.assertEqual(result['top_neighbors'],again['top_neighbors'])
        by_id={u['id']:u for u in units}
        for edge in result['edges']:
            a,b=map(by_id.get,edge['member_ids']);self.assertNotEqual(a['source_hash'],b['source_hash'])
        # Identical source under another filename is not a new independent source.
        duplicate=deepcopy(units[0]);duplicate.update(id='renamed',source_name='renamed')
        source=dict(kwargs['source_index'][0],source_name='renamed')
        kwargs['source_index'].append(source);kwargs['batch_ids']['renamed']='batch-other'
        with self.assertRaisesRegex(ValueError,'Duplicate'):
            build_study_neighbors(units+[duplicate],vectors,**kwargs)

    def test_either_policy_exposes_asymmetric_top_k_without_transitive_grouping(self):
        units,vectors,kwargs=fixture()
        mutual=build_study_neighbors(units,vectors,**kwargs)
        either=build_study_neighbors(units,vectors,policy='either',**kwargs)
        left={tuple(e['member_ids']) for e in mutual['edges']};right={tuple(e['member_ids']) for e in either['edges']}
        self.assertTrue(left < right)
        self.assertTrue(any(None in edge['ranks'] for edge in either['edges']))
        validate_study_neighbors(either)

    def test_invalid_missing_zero_and_ineligible_vectors_are_explicit(self):
        units,vectors,kwargs=fixture()
        vectors.pop('u00');vectors['u01']=[float('nan'),1,0];vectors['u02']=[0,0,0]
        units[3]['eligible']=False
        result=build_study_neighbors(units,vectors,**kwargs)
        expected={'u00':'failed_missing_vector','u01':'failed_nonfinite_vector',
                  'u02':'zero_vector_no_evidence','u03':'skipped_ineligible'}
        for uid,status in expected.items():self.assertEqual(result['coverage'][uid]['status'],status)
        self.assertEqual(sum(result['coverage_counts'].values()),40)
        self.assertFalse(set(expected)&set(result['isolates']))
        self.assertFalse(any(set(e['member_ids'])&set(expected) for e in result['edges']))
        validate_study_neighbors(result)
        units,vectors,kwargs=fixture();vectors['u01']=[1,0]
        invalid=build_study_neighbors(units,vectors,**kwargs)
        self.assertEqual(invalid['coverage_counts'],{'failed_dimension_mismatch':40})
        self.assertEqual(invalid['edges'],[])
        validate_study_neighbors(invalid)

    def test_memory_and_pair_guards_never_silently_drop_units(self):
        units,vectors,kwargs=fixture()
        memory=build_study_neighbors(units,vectors,limits=NeighborLimits(max_working_bytes=1),**kwargs)
        self.assertEqual(memory['status'],'memory_limit')
        self.assertEqual(memory['coverage_counts'],{'skipped_memory_limit':40})
        self.assertEqual(memory['comparisons']['pairs_scored'],0)
        validate_study_neighbors(memory)
        partial=build_study_neighbors(units,vectors,limits=NeighborLimits(max_candidate_pairs=1),**kwargs)
        self.assertEqual(partial['status'],'pair_limit')
        self.assertEqual(partial['comparisons']['pairs_scored'],1)
        self.assertEqual(partial['coverage_counts'],{'partial':40})
        self.assertEqual(partial['edges'],[])
        self.assertEqual(partial['isolates'],[])
        validate_study_neighbors(partial)

    def test_cancel_time_and_source_bounds(self):
        units,vectors,kwargs=fixture();cancel=threading.Event();cancel.set()
        result=build_study_neighbors(units,vectors,cancel_event=cancel,**kwargs)
        self.assertEqual(result['status'],'cancelled')
        self.assertEqual(result['coverage_counts'],{'skipped_cancelled':40})
        validate_study_neighbors(result)
        timed=build_study_neighbors(units,vectors,limits=NeighborLimits(max_seconds=1e-12),**kwargs)
        self.assertEqual(timed['status'],'time_limit')
        self.assertEqual(timed['coverage_counts'],{'skipped_time_limit':40})
        validate_study_neighbors(timed)
        with self.assertRaisesRegex(ValueError,'No units processed|no units processed'):
            build_study_neighbors(units,vectors,limits=NeighborLimits(max_source_bytes=1),**kwargs)
        cancel.clear()
        def progress(event):cancel.set()
        during=build_study_neighbors(units,vectors,cancel_event=cancel,on_progress=progress,**kwargs)
        self.assertEqual(during['status'],'cancelled')
        self.assertLess(during['comparisons']['pairs_scored'],640)
        self.assertEqual(sum(during['coverage_counts'].values()),40)
        validate_study_neighbors(during)

    def test_exact_source_context_and_derived_tampering_rejected_on_reopen(self):
        units,vectors,kwargs=fixture()
        wrong=deepcopy(units);wrong[0]['context_spans'][0]['text']='Invented context'
        with self.assertRaises(ValueError):build_study_neighbors(wrong,vectors,**kwargs)
        wrong=deepcopy(units);wrong[0]['text']='Invented target'
        with self.assertRaises(ValueError):build_study_neighbors(wrong,vectors,**kwargs)
        result=build_study_neighbors(units,vectors,**kwargs)
        for mutate in (lambda r:r['edges'].clear(),
                       lambda r:r['units'][0].update(text='Invented target'),
                       lambda r:r['top_neighbors']['u00'][0].update(cosine=.8),
                       lambda r:r['coverage']['u00'].update(status='partial'),
                       lambda r:r['coverage'].pop('u00'),
                       lambda r:r['comparisons'].update(expected_cross_source_pairs=1)):
            changed=deepcopy(result);mutate(changed);seal(changed)
            with self.subTest(mutate=mutate),self.assertRaises(ValueError):validate_study_neighbors(changed)

    def test_exports_keep_exact_sources_and_no_model_imports(self):
        units,vectors,kwargs=fixture();result=build_study_neighbors(units,vectors,**kwargs)
        loaded=json.loads(export_study_neighbors_json(result));validate_study_neighbors(loaded)
        self.assertEqual(loaded['units'],units)
        self.assertEqual(loaded['source_index'],kwargs['source_index'])
        html=export_study_neighbors_html(loaded)
        self.assertNotIn('<tag>',html);self.assertIn('&lt;tag&gt;',html)
        self.assertIn('not confidence',html)
        self.assertIn(units[0]['source_hash'],html)


if __name__=='__main__':unittest.main()

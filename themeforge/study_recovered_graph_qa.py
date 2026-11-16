"""Bounded actual app graph build/review/export check, with inference forbidden."""
import argparse
from contextlib import ExitStack
from hashlib import sha256
import json
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request
from unittest.mock import patch

from .study_dialog import read_local_json
from .study_native_qa import _local, _Resources


def graph_smoke(baseline, recovery, output, *, expected=None, selection=None):
    baseline,recovery,output=map(_local,(baseline,recovery,output))
    if output.exists():raise ValueError('Use a fresh graph QA output directory.')
    before={str(p):sha256(p.read_bytes()).hexdigest() for p in (baseline,recovery)}
    output.mkdir(parents=True)
    started=time.monotonic();resources=_Resources();blocked=[]
    report=dict(status='started',frozen_executable=bool(getattr(sys,'frozen',False)),
        input_hashes=before,encoder_calls=0,LLM_calls=0,network_calls=0,
        semantic_validation=False,human_review=False,checks=[])
    app=dialog=None;code=1
    def forbid(*args,**kwargs):
        blocked.append(True)
        raise AssertionError('Inference, network and helper startup are forbidden in recovered graph QA.')
    def sample():
        resources.sample()
        if time.monotonic()-started>240:raise TimeoutError('Graph QA exceeded 240 seconds.')
        if resources.report()['peak_sampled_owned_tree_rss_bytes']>1024**3:
            raise MemoryError('Graph QA exceeded its 1 GiB sampled owned-process RSS bound.')
    try:
        import psutil
        available=psutil.virtual_memory().available
        report['prestart_available_RAM_bytes']=available
        if available<2*1024**3:raise MemoryError('Graph QA requires at least 2 GiB available OS RAM.')
        from .app import ThemeForgeApp
        from .study_recovered_graph import validate_recovered_graph
        from .source_attribution import resolve_attribution
        from .study_segments import validate_segment_recovery
        with ExitStack() as guards:
            for owner,name in ((subprocess,'Popen'),(socket,'socket'),(urllib.request.OpenerDirector,'open')):
                guards.enter_context(patch.object(owner,name,forbid))
            guards.enter_context(patch('themeforge.study_encoder.encode_study',forbid))
            guards.enter_context(patch('themeforge.study_segments.run_segment_recovery',forbid))
            class HiddenApp(ThemeForgeApp):
                def _build_layout(self):self.withdraw();super()._build_layout()
            app=HiddenApp();app.update();dialog=app.open_study_coding();dialog.withdraw();app.update()
            assert dialog.start_recovered_neighbors(baseline,recovery,output/'built'),dialog.status.get()
            while dialog._worker is not None:
                app.update();sample();time.sleep(.02)
            app.update()
            assert dialog._is_recovered_graph(),dialog.status.get()
            graph=dialog.neighbors;validate_recovered_graph(graph)
            assert len(dialog.tree.get_children())==len(graph['source_study']['units'])
            assert dialog.encoder_ledger is None and dialog.segment_recovery is None
            assert not dialog.start_neighbors(),'Saved graph review must not enter a new encoder queue.'
            assert dialog._worker is None
            if expected:
                earlier=read_local_json(_local(expected));validate_recovered_graph(earlier)
                for key in ('source_study','units','vectors','vector_provenance','parent_coverage',
                            'edges','difference','parent_attributions','attributions'):
                    assert graph[key]==earlier[key],f'Packaged and source result differ: {key}'
                report['source_package_semantic_structure_identical']=True
            old=read_local_json(baseline);recovered=read_local_json(recovery)
            validate_segment_recovery(recovered)
            parent=recovered['parent_unit_id'];core_ids={u['id'] for u in recovered['plan']['derived_study']['units']}
            assert all(graph['vectors'][uid]==vec for uid,vec in old['vectors'].items())
            assert all(graph['vectors'][uid]==vec for uid,vec in recovered['encoder_ledger']['vectors'].items())
            assert parent not in graph['vectors']
            parent_pairs=[tuple(e['parent_ids']) for e in graph['edges']]
            assert len(parent_pairs)==len(set(parent_pairs))
            assert all(len(set(pair))==2 for pair in parent_pairs)
            source_index=graph['source_study']['sources']
            sources={s['source_name']:s for s in source_index}
            units={u['id']:u for u in graph['units']}
            span_rows=sorted((units[uid]['source_hash'],units[uid]['source_start'],units[uid]['source_end']) for uid in graph['vectors'])
            assert all(a[0]!=b[0] or a[2]<=b[1] for a,b in zip(span_rows,span_rows[1:]))
            dialog.tree.selection_set(parent);dialog.show_selected()
            visible=json.loads(dialog.detail.get('1.0','end'))
            assert {m['source_unit']['id'] for m in visible['exact_graph_members']}==core_ids
            for item in visible['exact_graph_members']:
                unit=item['source_unit'];source=sources[unit['source_name']]
                assert source['text'][unit['source_start']:unit['source_end']]==unit['text']
                assert item['attribution']==resolve_attribution(unit,source)
            report['recovered_parent_visible_with_all_cores']=True
            report['preserved_baseline_vectors']=len(old['vectors'])
            report['recovered_core_vectors']=len(core_ids)
            # These exact exposed records are the diagnosed composite boundaries.
            for uid in (parent,'MU-ce329c6d10a27777e0d4'):
                if uid not in graph['parent_coverage']:continue
                dialog.tree.selection_set(uid);dialog.show_selected()
                visible=json.loads(dialog.detail.get('1.0','end'))
                assert visible['structural_attribution']==graph['parent_attributions'][uid]
                assert not visible['structural_attribution']['participant_count_supported']
                assert dialog.show_full_source()
                full=json.loads(dialog.detail.get('1.0','end'))
                assert full['original_source'] in source_index
                report['checks'].append(dict(parent_id=uid,exact_attribution_visible=True,full_source_available=True))
            selected=[]
            if selection:
                frozen=read_local_json(_local(selection))
                selected=frozen['selected_pairs']
                if len(selected)>8:raise ValueError('At most eight diagnostic pairs are allowed.')
            else:
                selected=[dict(review_id=f'recovered-{i+1}',edge=edge) for i,edge in enumerate(e for e in graph['edges'] if parent in e['parent_ids'])]
            for item in selected:
                frozen_edge=item['edge']
                parent_ids=sorted(frozen_edge.get('parent_ids',frozen_edge['member_ids']))
                match=next((e for e in graph['edges'] if e['parent_ids']==parent_ids),None)
                if match is None:
                    report['checks'].append(dict(review_id=item['review_id'],absent_from_new_graph=True,
                        frozen_edge=frozen_edge));continue
                edge=match
                uid=edge['parent_ids'][0];dialog.tree.selection_set(uid);dialog.show_selected()
                seen=False
                for _ in range(1+sum(uid in e['parent_ids'] for e in graph['edges'])):
                    assert dialog.show_next_pair()
                    pair=json.loads(dialog.detail.get('1.0','end'))
                    if pair['original_candidate']==edge:seen=True;break
                assert seen
                assert [m['original_source_unit'] for m in pair['exact_members']]==[units[k] for k in edge['member_ids']]
                assert [m['structural_attribution'] for m in pair['exact_members']]==[graph['attributions'][k] for k in edge['member_ids']]
                report['checks'].append(dict(review_id=item['review_id'],exact_members_and_attribution_visible=True,
                    frozen_edge=frozen_edge,current_edge=edge,
                    member_or_rank_change=frozen_edge['member_ids']!=edge['member_ids'] or frozen_edge['ranks']!=edge['ranks']))
                sample()
            for kind in ('json','html'):
                assert dialog.export(kind,output/('review.'+kind)),dialog.status.get()
            exported=read_local_json(output/'review.json')
            assert exported==graph and dialog.load(output/'review.json'),dialog.status.get()
            html=(output/'review.html').read_text(encoding='utf-8')
            assert all(uid in html for uid in core_ids),'HTML must expose cores even without a selected edge.'
            assert all(s['source_hash'] in html for s in source_index)
            assert all(sha256(Path(path).read_bytes()).hexdigest()==pin for path,pin in before.items())
            sample()
            report.update(status='passed',original_inputs_unchanged=True,full_parent_rows=len(dialog.tree.get_children()),
                graph_vector_members=len(graph['vectors']),parent_pair_candidates=len(graph['edges']),
                graph_changes=graph['difference'],exact_UI_JSON_export_reopen=True,HTML_all_cores_visible=True,
                graph_path=str(output/'review.json'))
            code=0
    except Exception as error:
        report.update(status='failed',error_type=type(error).__name__,error=str(error))
    finally:
        if dialog is not None and dialog.winfo_exists():
            dialog.close()
            deadline=time.monotonic()+65
            while dialog.winfo_exists() and dialog._worker is not None and time.monotonic()<deadline:
                app.update();time.sleep(.02)
            report['worker_stopped']=dialog._worker is None
        if app is not None:app.destroy()
        resources.sample(force=True)
        report.update(elapsed_seconds=time.monotonic()-started,resources=resources.report(),
            blocked_startup_attempts=len(blocked),app_closed=app is not None)
        if blocked or report.get('worker_stopped') is False:report['status']='failed';code=1
        (output/'QA.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return code


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    for key in ('baseline','recovery','out'):parser.add_argument('--'+key,required=True)
    for key in ('expected','selection'):parser.add_argument('--'+key)
    args=parser.parse_args(argv)
    return graph_smoke(args.baseline,args.recovery,args.out,expected=args.expected,selection=args.selection)


if __name__=='__main__':raise SystemExit(main())

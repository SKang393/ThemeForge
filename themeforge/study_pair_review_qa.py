"""No-inference actual UI/export regression for a frozen small pair selection."""
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

from .study_native_qa import _local,_Resources
from .study_dialog import read_local_json


def pair_review_smoke(graph,selection,output):
    graph,selection,output=map(_local,(graph,selection,output))
    if output.exists():raise ValueError('Use a fresh pair-review QA output directory.')
    original=graph.read_bytes();picked=read_local_json(selection);value=read_local_json(graph)
    if sha256(original).hexdigest()!=picked['graph_sha256'] or not 1<=len(picked['selected_pairs'])<=8:
        raise ValueError('Use the exact frozen graph and at most eight selected pairs.')
    output.mkdir(parents=True);started=time.monotonic();resources=_Resources()
    report=dict(status='started',frozen_executable=bool(getattr(sys,'frozen',False)),new_inference_calls=0,
        graph_sha256=sha256(original).hexdigest(),selection_sha256=sha256(selection.read_bytes()).hexdigest(),
        semantic_validation=False,human_review=False,checks=[])
    app=dialog=None;blocked=[];code=1
    def forbid(*args,**kwargs):blocked.append(True);raise AssertionError('Inference, network and helper startup forbidden in pair review.')
    try:
        from .app import ThemeForgeApp
        from .study_neighbors import validate_study_neighbors
        with ExitStack() as guards:
            for owner,name in ((subprocess,'Popen'),(socket,'socket'),(urllib.request.OpenerDirector,'open')):
                guards.enter_context(patch.object(owner,name,forbid))
            class HiddenApp(ThemeForgeApp):
                def _build_layout(self):self.withdraw();super()._build_layout()
            app=HiddenApp();app.update();dialog=app.open_study_coding();dialog.withdraw();app.update()
            assert dialog.load(graph),dialog.status.get()
            assert dialog.neighbors==value and len(dialog.tree.get_children())==len(value['source_study']['units'])
            for row in picked['selected_pairs']:
                if time.monotonic()-started>120:raise TimeoutError('Pair UI QA exceeded 120 seconds.')
                edge=row['edge'];uid=edge['member_ids'][0]
                dialog.tree.selection_set(uid);dialog.show_selected()
                compact=dialog.detail.get('1.0','end');seen=False
                for _ in range(1+sum(uid in e['member_ids'] for e in value['edges'])):
                    assert dialog.show_next_pair()
                    pair=json.loads(dialog.detail.get('1.0','end'))
                    if pair['original_candidate']==edge:seen=True;break
                assert seen,'Selected original pair was not reachable.'
                expected=[next(u for u in value['units'] if u['id']==member) for member in edge['member_ids']]
                assert [m['original_source_unit'] for m in pair['exact_members']]==expected
                assert dialog.show_full_source()
                full=json.loads(dialog.detail.get('1.0','end'))
                assert full['original_source'] in value['source_index']
                report['checks'].append(dict(review_id=row['review_id'],exact_pair_preserved=True,
                    compact_detail_characters=len(compact),one_pair_detail_characters=len(json.dumps(pair,ensure_ascii=False,indent=2)),
                    full_source_available=True,attribution_warning_members=[m['original_source_unit']['id'] for m in pair['exact_members'] if m['context_and_attribution']['attribution_review_required']]))
                resources.sample()
            out_json=output/'neighbors.json';out_html=output/'neighbors.html'
            assert dialog.export('json',out_json),dialog.status.get()
            assert dialog.export('html',out_html),dialog.status.get()
            exported=read_local_json(out_json);validate_study_neighbors(exported);assert exported==value
            assert dialog.load(out_json) and dialog.neighbors==value
            html=out_html.read_text(encoding='utf-8')
            assert all('source-'+s['source_hash'] in html for s in value['source_index'])
            assert html.count('Inspect full original source and distant context')==2*len(value['edges'])
            assert 'Embedded participant/transcript heading' in html
            assert graph.read_bytes()==original
            report.update(status='passed',full_UI_rows=len(dialog.tree.get_children()),candidate_pairs=len(value['edges']),
                graph_JSON_export_reopen_exact=True,HTML_full_source_context_links=True,original_graph_unchanged=True)
            code=0
    except Exception as error:report.update(status='failed',error_type=type(error).__name__,error=str(error))
    finally:
        if dialog is not None and dialog.winfo_exists():dialog.close()
        if app is not None:app.destroy()
        resources.sample(force=True)
        report.update(elapsed_seconds=time.monotonic()-started,resources=resources.report(),blocked_startup_attempts=len(blocked),app_closed=app is not None)
        if blocked:report['status']='failed';code=1
        (output/'QA.json').write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
    return code


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    for key in ('graph','selection','out'):parser.add_argument('--'+key,required=True)
    args=parser.parse_args(argv);return pair_review_smoke(args.graph,args.selection,args.out)


if __name__=='__main__':raise SystemExit(main())

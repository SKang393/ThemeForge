"""Explicit hidden app review/export check, with no encoder or LLM calls."""
from __future__ import annotations
import argparse
from copy import deepcopy
from html import escape
import json
from pathlib import Path
import sys
import time


def run_smoke(graph_path, out):
    from .app import ThemeForgeApp
    from .neighbor_review import validate_review
    from .sentence_encoder import file_sha256
    out = Path(out).resolve()
    out.mkdir(parents=True, exist_ok=False)
    source = Path(graph_path).resolve()
    before_hash = file_sha256(source)
    report = dict(status='started', frozen_executable=bool(getattr(sys, 'frozen', False)),
                  inference_calls=0, review_actions_author='ai', human_reviews=0, input_sha256=before_hash)
    app = None
    started = time.perf_counter()
    try:
        class HiddenApp(ThemeForgeApp):
            def _build_layout(self):
                self.withdraw()
                super()._build_layout()
        app = HiddenApp(); app.update()
        app_before = deepcopy((app.documents, app.result))
        window = app.open_source_neighbors()
        def forbidden(*args, **kwargs):
            raise AssertionError('Review/export smoke must not start a model.')
        window._runner = forbidden
        assert window.state() == 'withdrawn'
        assert window.load_review(source), window.status.get()
        original = deepcopy(window.reviews[window.backend.get()])
        graph = original['graph']
        assert len(window.tree.get_children()) == len(graph['edges']) + len(graph['isolates'])
        nodes = {n['id']: n for n in graph['packet']['nodes']}
        for index, edge in enumerate(graph['edges']):
            window.tree.selection_set(str(index)); window.show_pair(); app.update()
            display = window.detail.get('1.0', 'end-1c')
            for uid in edge['member_ids']:
                assert nodes[uid]['text'] in display and nodes[uid]['source_hash'] in display
        for uid in graph['isolates']:
            window.tree.selection_set('iso:' + uid); window.show_pair()
            assert uid in window.detail.get('1.0', 'end-1c')
            try:
                window.selected_pair()
                raise AssertionError('An isolate must not be reviewable as a pair.')
            except ValueError:
                pass
        if graph['edges']:
            window.tree.selection_set('0'); window.show_pair()
            window.reason.insert('1.0', 'AI software QA exercise only. This review event does not adjudicate study meaning or establish model accuracy.')
            assert not window.record_review(), 'Blank author must not silently become researcher.'
            assert window.record_review(reviewer_kind='ai'), window.status.get()
        assert window.export('json', out / 'review.json'), window.status.get()
        assert window.export('html', out / 'review.html'), window.status.get()
        exported = json.loads((out / 'review.json').read_text(encoding='utf-8'))
        validate_review(exported)
        assert exported['graph'] == original['graph']
        assert exported['history'][:len(original['history'])] == original['history']
        if graph['edges']:
            assert exported['history'][-1]['reviewer_kind'] == 'ai'
        text = (out / 'review.html').read_text(encoding='utf-8')
        for edge in graph['edges']:
            for uid in edge['member_ids']:
                assert escape(nodes[uid]['text'], quote=True) in text
        reopened = app.open_source_neighbors()
        reopened._runner = forbidden
        assert reopened.load_review(out / 'review.json'), reopened.status.get()
        assert reopened.reviews[window.backend.get()] == exported
        assert app_before == (app.documents, app.result)
        assert file_sha256(source) == before_hash
        report.update(status='passed', candidate_pairs=len(graph['edges']), isolates=len(graph['isolates']),
                      exact_source_display=True, native_app_state_unchanged=True, raw_graph_unchanged=True,
                      review_json_html_reopened=True, semantic_accuracy_tested=False,
                      outputs={p.name: file_sha256(p) for p in out.iterdir() if p.is_file()})
    except Exception as error:
        report.update(status='failed', error_type=type(error).__name__, error=str(error))
        import traceback
        report['traceback'] = traceback.format_exc()
    finally:
        if app is not None:
            app.destroy()
        report['elapsed_seconds'] = time.perf_counter() - started
        (out / 'neighbor-review-smoke.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return 0 if report['status'] == 'passed' else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--graph', required=True)
    parser.add_argument('--out', required=True)
    args = parser.parse_args(argv)
    return run_smoke(args.graph, args.out)


if __name__ == '__main__':
    raise SystemExit(main())

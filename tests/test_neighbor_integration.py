from copy import deepcopy
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from themeforge.analysis_types import TranscriptDocument
from themeforge.app import ThemeForgeApp
from themeforge.local_codebook import prepare_codebook
from themeforge.neighbor_inputs import packet_from_native
from themeforge.neighbor_review import create_review, export_review_json
from themeforge.semantic_neighbors import build_neighbor_graph, validate_packet
from themeforge.sentence_encoder import file_sha256
from tests.test_semantic_neighbors import fixture


class HiddenApp(ThemeForgeApp):
    def _build_layout(self):
        self.withdraw()
        super()._build_layout()


class NeighborIntegrationTests(unittest.TestCase):
    def test_native_selected_turns_exclude_ai_labels_and_keep_exact_context(self):
        docs = [TranscriptDocument('fiction-a', 'Interviewer: How might this help?\nParticipant 1: I might use a larger keyboard if the trial is comfortable.\n'),
                TranscriptDocument('fiction-b', 'Interviewer: What worked?\nParticipant 2: I do not use a larger keyboard because it causes discomfort.\n')]
        native = prepare_codebook(docs, max_units=2, speaker_label_policy='explicit_roles')
        before = deepcopy(native)
        packet = packet_from_native(native)
        validate_packet(packet)
        self.assertEqual(native, before)
        self.assertEqual(len(packet['nodes']), 2)
        self.assertTrue(all('Interviewer:' not in node['text'] for node in packet['nodes']))
        self.assertTrue(all('Interviewer:' in node['context_text'] for node in packet['nodes']))
        self.assertFalse(packet['provenance']['generated_labels_used'])

    def test_real_hidden_app_pair_and_isolate_review_exports_keep_graph(self):
        app = HiddenApp(); app.update()
        try:
            window = app.open_source_neighbors()
            graph = build_neighbor_graph(fixture(), [[1,0],[.8,.6],[.28,.96],[-1,0]], backend='sentence_embeddings',
                                         provenance={'fixture': 'synthetic_vectors_no_model'})
            with tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / 'review.json'
                path.write_text(export_review_json(create_review(graph)), encoding='utf-8')
                self.assertTrue(window.load_review(path))
                self.assertEqual(window.state(), 'withdrawn')
                self.assertEqual(len(window.tree.get_children()), len(graph['edges']) + len(graph['isolates']))
                window.tree.selection_set('0'); window.show_pair()
                self.assertIn(graph['packet']['nodes'][0]['text'], window.detail.get('1.0','end-1c'))
                window.reason.insert('1.0', 'Synthetic AI test comparison with source conditions retained.')
                self.assertFalse(window.record_review())
                self.assertTrue(window.record_review(reviewer_kind='ai'))
                self.assertTrue(window.export('json', Path(folder) / 'new.json'))
                value = json.loads((Path(folder) / 'new.json').read_text(encoding='utf-8'))
                self.assertEqual(value['graph'], graph)
                self.assertEqual(value['history'][0]['reviewer_kind'], 'ai')
                window.tree.selection_set('iso:' + graph['isolates'][0]); window.show_pair()
                self.assertIn('Unassigned', window.detail.get('1.0','end-1c'))
                self.assertFalse(window.record_review(reviewer_kind='ai'))
                self.assertFalse(window.export('json', Path(folder) / 'new.json'))
        finally:
            app.destroy()

    def test_source_packet_switch_clears_mismatched_comparison_and_close_cancels(self):
        app = HiddenApp(); app.update()
        try:
            window = app.open_source_neighbors(fixture())
            window.reviews = {'sentence_embeddings': create_review(build_neighbor_graph(fixture(), [[1,0]]*4, backend='sentence_embeddings'))}
            window.set_packet(fixture(['Different one','Different two']))
            self.assertEqual(window.reviews, {})
            window._busy = True
            window.request_close()
            self.assertTrue(window._cancel.is_set())
            self.assertTrue(window._closing)
            self.assertTrue(window.winfo_exists())
            window._busy = False
            window.request_close()
        finally:
            app.destroy()

    def test_local_codebook_opens_neighbor_packet_without_starting_llm(self):
        app = HiddenApp(); app.update()
        try:
            app.documents = [TranscriptDocument('fiction-a','P1: I might need interpreter support if the lesson includes a discussion.'),
                             TranscriptDocument('fiction-b','P2: I use captioning, but it does not replace discussion access.')]
            dialog = app.open_local_codebook(); dialog.withdraw()
            with patch.object(dialog, '_configuration', side_effect=AssertionError('No LLM configuration')):
                window = dialog.open_source_neighbors()
            self.assertIsNotNone(window, dialog.status.get())
            self.assertFalse(window._busy)
            self.assertEqual(len(window.packet['nodes']), 2)
            self.assertEqual(window.reviews, {})
        finally:
            app.destroy()

    def test_background_run_delivers_both_graphs_without_promoting_review(self):
        app = HiddenApp(); app.update()
        try:
            window = app.open_source_neighbors(fixture())
            with tempfile.TemporaryDirectory() as folder:
                profile = Path(folder) / 'profile.json'; profile.write_text('{}', encoding='utf-8')
                self.assertTrue(window.load_profile(profile))
                observed = []
                def fixture_runner(packet_path, packet_pin, profile_path, profile_pin, out, *, cancel):
                    self.assertEqual(file_sha256(packet_path), packet_pin)
                    self.assertEqual(file_sha256(profile_path), profile_pin)
                    packet = json.loads(packet_path.read_text(encoding='utf-8'))
                    observed.append(packet)
                    for backend in ('lexical_tfidf', 'sentence_embeddings'):
                        target = out / backend; target.mkdir(parents=True)
                        graph = build_neighbor_graph(packet, [[1,0],[.8,.6],[.28,.96],[-1,0]], backend=backend,
                            provenance={'test_only_synthetic_vectors': True})
                        (target / 'graph.json').write_text(json.dumps(graph), encoding='utf-8')
                    return {'status': 'completed'}
                window._runner = fixture_runner
                self.assertTrue(window.start(Path(folder) / 'run'), window.status.get())
                deadline = time.monotonic() + 5
                while window._busy and time.monotonic() < deadline:
                    app.update(); time.sleep(.02)
                self.assertFalse(window._busy, window.status.get())
                self.assertEqual(observed, [window.packet])
                self.assertEqual(set(window.reviews), {'lexical_tfidf', 'sentence_embeddings'})
                self.assertTrue(all(not r['history'] for r in window.reviews.values()))
                self.assertEqual(window.backend.get(), 'sentence_embeddings')
                self.assertEqual(str(window.run_button['state']), 'normal')
        finally:
            app.destroy()

    def test_saved_vector_expansion_keeps_current_review_and_opens_new_rule(self):
        from themeforge.neighbor_review import append_review
        app = HiddenApp(); app.update()
        try:
            window = app.open_source_neighbors()
            graph = build_neighbor_graph(fixture(), [[1,0],[.8,.6],[.28,.96],[-1,0]], backend='sentence_embeddings',
                                         provenance={'synthetic_vectors_no_model': True})
            review = append_review(create_review(graph),graph['edges'][0]['member_ids'],'uncertain',
                                   'Synthetic AI review must not carry into new retrieval branches.',reviewer_kind='ai')
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory); source = root/'review.json'
                source.write_text(export_review_json(review),encoding='utf-8')
                self.assertTrue(window.load_review(source))
                window._runner = lambda *a,**kw: self.fail('Saved-vector comparison must not run inference')
                self.assertTrue(window.compare_saved_rules(root/'rules'))
                deadline = time.monotonic()+10
                while window._busy and time.monotonic()<deadline:
                    app.update();time.sleep(.02)
                self.assertFalse(window._busy,window.status.get())
                self.assertEqual(window.reviews['sentence_embeddings'],review)
                summary=json.loads((root/'rules/comparison.json').read_text(encoding='utf-8'))
                self.assertEqual(len(summary['policies']),6)
                self.assertEqual(summary['new_encoder_calls'],0)
                expanded=app.open_source_neighbors()
                self.assertTrue(expanded.load_review(root/'rules/mutual_k4_t040.json'))
                self.assertEqual(expanded.reviews['sentence_embeddings']['history'],[])
                self.assertIn('mutual top-4, floor 0.40',expanded.coverage.get())
                self.assertFalse(window.compare_saved_rules(root/'rules'))
        finally:
            app.destroy()


if __name__ == '__main__':
    unittest.main()

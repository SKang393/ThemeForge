"""Native MAIN callbacks through fake storage, without Tk roots or inference."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from themeforge.main_review_qa import make_controller, check_sources, run, forbidden
from themeforge.codebook_review import candidate_review_state
from tests import test_main_codebook_review as contracts

fictional_draft = contracts.fictional_draft


class MainReviewControllerTests(unittest.TestCase):
    def setUp(self):
        self.guards = [patch('tkinter.Tk', side_effect=forbidden),
                       patch('tkinter.Toplevel', side_effect=forbidden),
                       patch('subprocess.Popen', side_effect=forbidden)]
        for guard in self.guards:
            guard.start()
            self.addCleanup(guard.stop)
        self.identity = dict(reviewer_kind='assistant_development_review',
                             reviewer_name='Offline controller AI demo',
                             reason='Read exact source, actor, condition and unknown outcome.')

    def test_actual_selection_callbacks_switch_all_entries_and_clear_deselection(self):
        controller = make_controller(fictional_draft())
        counts = check_sources(controller)
        self.assertGreater(counts['target'], 0)
        self.assertGreater(counts['context'], 0)
        self.assertIsNone(controller._source_view)
        self.assertEqual(controller._source_entry_ids, [])

    def test_actual_source_callback_clears_previous_context_on_unknown_candidate(self):
        controller = make_controller(fictional_draft())
        old_text = controller.source_detail.text
        controller._load_source_view('missing')
        self.assertIsNone(controller._source_view)
        self.assertEqual(controller._source_entry_ids, [])
        self.assertNotEqual(controller.source_detail.text, old_text)
        self.assertIn('Previous source context has been cleared', controller.source_detail.text)

    def test_actual_review_edit_preserves_raw_calls_and_stale_editor_is_atomic(self):
        result = fictional_draft()
        controller = make_controller(result)
        cid = result['codes'][0]['id']
        digest = result['result_sha256']
        self.assertTrue(controller.apply_candidate_review(cid,
            {'claim': 'A reported tutor wish depends on transport; the outcome remains unknown.'},
            'edit', expected_digest=digest, **self.identity), controller.status.get())
        self.assertEqual(candidate_review_state(controller.result, cid)['state'], 'edit')
        self.assertIn('edit (AI)', controller.code_tree.rows[cid])
        self.assertFalse(candidate_review_state(controller.result, cid)['researcher_approved'])
        self.assertFalse(controller.reviewed.get())
        self.assertEqual(controller.result['calls'], result['calls'])
        current = deepcopy(controller.result)
        self.assertFalse(controller.apply_candidate_review(cid, {}, 'accept', expected_digest=digest, **self.identity))
        self.assertEqual(controller.result, current)

    def test_actual_split_controller_retains_parent_selects_child_and_keeps_grouping_blocked(self):
        result = fictional_draft()
        controller = make_controller(result)
        parent = result['codes'][0]['id']
        parts = contracts.MainCodebookReviewTests().parts(result)
        notes = {'action_status': 'Reported wishes, not observed outcomes.',
                 'remaining_uncertainty': 'Keep parent notes separate from child judgments.'}
        self.assertTrue(controller.apply_candidate_split(parent, parts, checks=notes, **self.identity), controller.status.get())
        self.assertEqual(controller.result['candidate_review_workflow']['splits'][-1]['checks'], notes)
        self.assertIn(parent, controller.code_tree.get_children())
        selected = controller.code_tree.selection()[0]
        self.assertEqual(controller._codes[selected]['split_parent_id'], parent)
        self.assertEqual(candidate_review_state(controller.result, selected)['state'], 'unreviewed')
        self.assertEqual(candidate_review_state(controller.result, selected)['checks'], {})
        self.assertEqual(controller.result['calls'], result['calls'])
        check_sources(controller)

    def test_actual_group_controller_requires_review_flag_and_retains_ai_provenance(self):
        controller = make_controller(fictional_draft())
        ids = [controller.result['codes'][0]['id'], controller.result['codes'][-1]['id']]
        for cid in ids:
            self.assertTrue(controller.apply_candidate_review(cid, {}, 'accept', **self.identity))
        controller.code_tree.selection_set(*ids)
        controller._selection_changed()
        fields = dict(label='Conditional next steps', shared_meaning='Practical support conditions possible next steps.',
            member_notes={cid: 'Each account retains its own condition and uncertain outcome.' for cid in ids},
            limits='No common actor or completed placement is established.',
            reviewer_kind='assistant_development_review', reason='Fictional controller grouping demonstration.')
        self.assertFalse(controller.add_reviewed_category(ids, **fields))
        controller.reviewed.set(True)
        self.assertTrue(controller.add_reviewed_category(ids, **fields), controller.status.get())
        self.assertFalse(controller.reviewed.get())
        self.assertEqual(controller.result['reviewed_categories'][0]['origin'], 'assistant_development_review')

    def test_reusable_headless_runner_uses_native_load_export_and_exact_roundtrip(self):
        base = Path(__file__).resolve().parents[1] / 'tmp' / 'main_review_integration_20261116'
        base.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=base) as directory:
            directory = Path(directory)
            draft = fictional_draft()
            controller = make_controller(draft)
            self.assertTrue(controller.apply_candidate_review(draft['codes'][0]['id'], {}, 'uncertain', **self.identity))
            draft_path, reviewed_path = directory / 'draft.json', directory / 'reviewed.json'
            draft_path.write_text(json.dumps(draft), encoding='utf-8')
            reviewed_path.write_text(json.dumps(controller.result), encoding='utf-8')
            report = run(draft_path, reviewed_path, directory / 'qa')
            self.assertTrue(report['passed'])
            self.assertFalse(report['visual_layout_verified'])
            self.assertEqual(report['model_calls'], 0)
            self.assertEqual(report['artifacts']['reviewed']['decision_summary']['uncertain'], 1)
            self.assertTrue((directory / 'qa' / 'report.json').is_file())


if __name__ == '__main__':
    unittest.main()

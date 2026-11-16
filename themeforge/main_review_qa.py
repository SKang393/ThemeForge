"""Headless checks of actual MAIN dialog callbacks, never visual layout QA."""
import argparse
from collections import Counter
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import time
from unittest.mock import patch

from .local_codebook_dialog import LocalCodebookDialog
from .codebook_review import candidate_review_state, source_review_text
from .local_codebook import validate_result_sources


class Value:
    def __init__(self, value=''):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class Widget:
    def __init__(self):
        self.options = {}
        self.text = ''

    def configure(self, **options):
        self.options.update(options)

    def delete(self, *args):
        self.text = ''

    def insert(self, index, text):
        self.text = text

    def select(self, widget):
        self.selected = widget


class Tree(Widget):
    def __init__(self):
        super().__init__()
        self.rows = {}
        self.selected = ()

    def get_children(self):
        return tuple(self.rows)

    def delete(self, *ids):
        for key in ids:
            self.rows.pop(key, None)
        self.selected = tuple(key for key in self.selected if key in self.rows)

    def insert(self, parent, index, *, iid, values):
        self.rows[iid] = values

    def selection(self):
        return self.selected

    def selection_set(self, *ids):
        if len(ids) == 1 and isinstance(ids[0], (list, tuple)):
            ids = tuple(ids[0])
        self.selected = tuple(ids)


class Choice(Widget):
    def __init__(self):
        super().__init__()
        self.index = -1

    def set(self, text):
        self.text = text
        self.index = -1

    def current(self, index=None):
        if index is not None:
            self.index = index
        return self.index


class HeadlessController:
    """Only widget storage is fake. All tested controller callbacks are native."""


# Preserve staticmethod descriptors and bind the real callbacks to a plain object.
# No Tk subclass construction or Tk __getattr__ fallback is involved.
for _name in ('_replace', '_api', 'set_result', '_selection_changed', '_load_source_view',
              '_source_changed', '_source_labels_for', '_update_actions', '_selected_usable',
              '_can_review_evidence', '_has_synthesis', 'apply_candidate_review',
              'apply_candidate_split', 'revise_interpretation', 'revise_evidence_roles',
              'revise_evidence', 'add_reviewed_category', 'load_result', 'export'):
    setattr(HeadlessController, _name, LocalCodebookDialog.__dict__[_name])


def make_controller(result=None):
    controller = HeadlessController()
    controller._busy = False
    controller.result = None
    controller._codes = {}
    controller._codebook_api = None
    controller._source_view = None
    controller._source_entry_ids = []
    controller.documents = ()
    controller.code_tree = Tree()
    controller.source_choice = Choice()
    for name in ('reviewed', 'status', 'sample_note', 'gate_note'):
        setattr(controller, name, Value(False if name == 'reviewed' else ''))
    for name in ('categories', 'review_categories', 'themes', 'unassigned', 'detail', 'provenance_detail',
                 'source_detail', 'source_label_choice', 'run_button', 'open_button',
                 'choose_passages_button', 'automatic_sample_button', 'evidence_button',
                 'evidence_roles_button', 'interpretation_button', 'cancel_button',
                 'category_button', 'review_category_button', 'json_button', 'html_button',
                 'codebook_notebook'):
        setattr(controller, name, Widget())
    if result is not None:
        controller.set_result(result)
    return controller


def check_sources(controller):
    """Exercise actual selection/context callbacks for every retained candidate."""
    counts = Counter()
    previous = deepcopy(controller.result)
    for code_id in controller.code_tree.get_children():
        controller.code_tree.selection_set(code_id)
        controller._selection_changed()
        view = controller._source_view
        if view is None:
            raise AssertionError(controller.source_detail.text)
        if controller.source_choice.current() != 0:
            raise AssertionError('Source selection did not reset to target.')
        if code_id not in controller.provenance_detail.text or 'RECORDED SOURCE AND MODEL PROVENANCE' not in controller.provenance_detail.text:
            raise AssertionError('The selected candidate provenance is absent from the main app view.')
        for index, entry in enumerate(view['entries']):
            controller.source_choice.current(index)
            controller._source_changed()
            expected = source_review_text(view, entry['id'])
            if controller.source_detail.text != expected or entry['span']['text'] not in expected:
                raise AssertionError('Actual source callback did not render exact selected entry.')
            span = entry['span']
            doc = next(d for d in controller.result['source_documents']
                       if d['name'] == span['source_name'])
            if doc['text'][span['source_start']:span['source_end']] != span['text']:
                raise AssertionError('Source callback references stale text or offsets.')
            counts[entry['kind']] += 1
        if candidate_review_state(controller.result, code_id)['state'] not in str(controller.code_tree.rows[code_id]):
            raise AssertionError('Review state missing from actual tree row.')
    controller.code_tree.selection_set()
    controller._selection_changed()
    if controller._source_view is not None or controller._source_entry_ids or controller.source_choice.current() != -1:
        raise AssertionError('Deselection retained source/context state.')
    if controller.source_detail.text != 'Select a candidate and an exact target or context entry.':
        raise AssertionError('Deselection retained old source/context text.')
    if controller.result != previous:
        raise AssertionError('Reading source callbacks mutated saved output.')
    return dict(counts)


def forbidden(*args, **kwargs):
    raise AssertionError('Headless QA attempted a GUI window or child process.')


def _assert_callback(success, controller):
    if not success:
        raise AssertionError(controller.status.get())


def _compare_action_result(actual, expected, draft, id_map):
    """Compare frozen substantive results, allowing fresh audit times and IDs."""
    validate_result_sources(actual)
    if len(actual['codes']) != len(expected['codes']):
        raise AssertionError('Action callbacks changed the expected candidate count.')
    actual_codes = {code['id']: code for code in actual['codes']}
    fields = ('label', 'claim', 'definition', 'inclusion', 'exclusion', 'uncertainty',
              'unit_id', 'evidence', 'support_span_ids', 'qualifications', 'counterexamples',
              'status', 'origin', 'scope_origin', 'scope_boundary_status', 'field_origins',
              'interpretation_field_origins', 'evidence_role_origins', 'evidence_selection_origin')
    for code in expected['codes']:
        actual_id = id_map[code['id']]
        current = actual_codes[actual_id]
        if any(current.get(field) != code.get(field) for field in fields):
            raise AssertionError(f'Action callback changed frozen code meaning, evidence or authorship: {code["id"]}')
        state, expected_state = candidate_review_state(actual, actual_id), candidate_review_state(expected, code['id'])
        if state != expected_state or state['researcher_approved'] or state['semantic_validation']:
            raise AssertionError('Action callback review state or AI attribution differs from the frozen review.')
    for field in ('calls', 'source_documents', 'registry', 'unit_archive', 'demo_provenance'):
        if actual.get(field) != draft.get(field):
            raise AssertionError(f'Action callbacks modified original {field}.')
    for split in actual.get('candidate_review_workflow', {}).get('splits', []):
        original = next(code for code in draft['codes'] if code['id'] == split['parent_id'])
        if actual_codes[split['parent_id']] != original or split['parent_snapshot'] != original:
            raise AssertionError('Splitting changed the retained original parent.')
    actual_groups, expected_groups = actual.get('reviewed_categories', []), expected.get('reviewed_categories', [])
    if len(actual_groups) != len(expected_groups):
        raise AssertionError('Action callbacks changed the expected category count.')
    category_fields = ('label', 'shared_meaning', 'limits', 'reason', 'kind', 'origin', 'review_status')
    member_fields = ('label', 'claim', 'definition', 'wording_origins', 'evidence_role_origins', 'member_note',
                     'support_span_ids', 'evidence', 'qualifications', 'counterexamples',
                     'referent_context', 'referent_context_origin')
    for group, expected_group in zip(actual_groups, expected_groups):
        if (any(group.get(field) != expected_group.get(field) for field in category_fields)
                or group['origin'] != 'assistant_development_review'
                or group['code_ids'] != [id_map[cid] for cid in expected_group['code_ids']]):
            raise AssertionError('Provisional AI category meaning or mapped membership differs.')
        for member, expected_member in zip(group['members'], expected_group['members']):
            if (member['code_id'] != id_map[expected_member['code_id']]
                    or any(member.get(field) != expected_member.get(field) for field in member_fields)):
                raise AssertionError('Category member snapshot meaning, notes or authorship differs.')
    workflow = actual.get('candidate_review_workflow', {})
    authored = list(actual.get('review_history', [])) + workflow.get('decisions', []) + workflow.get('splits', [])
    if any(event.get('reviewer_kind') != 'assistant_development_review' for event in authored):
        raise AssertionError('Frozen AI action replay created a non-AI review event.')
    if actual.get('semantic_quality') != 'not_human_validated' or actual.get('training_performed') is not False:
        raise AssertionError('Action callbacks promoted validation or training status.')


def replay_saved_actions(draft_path, draft, expected, actions, output):
    """Run only explicitly frozen actions through actual MAIN callbacks."""
    author, name = actions['origin'], actions['reviewer_name']
    if (author != 'assistant_development_review' or actions.get('human_decisions') != 0
            or actions.get('new_model_calls') != 0 or actions.get('training_eligible') is not False):
        raise ValueError('Headless action replay requires frozen AI-only, non-training actions with no model calls.')
    controller = make_controller()
    _assert_callback(controller.load_result(draft_path), controller)
    source_entries_before = check_sources(controller)
    ids = actions['ids']
    id_map = {code['id']: code['id'] for code in draft['codes']}
    callback_counts = Counter()
    for repair in actions['definition_repairs']:
        _assert_callback(controller.revise_interpretation(ids[repair['packet_id']], repair['changes'],
            repair['reason'], reviewer_kind=author, reviewer_name=name), controller)
        callback_counts['revise_interpretation'] += 1
    revision = actions['supporting_evidence_revision']
    _assert_callback(controller.revise_evidence(ids[revision['packet_id']], revision['support_span_ids'],
        revision['reason'], reviewer_kind=author, reviewer_name=name), controller)
    callback_counts['revise_supporting_evidence_via_revise_evidence'] += 1
    for decision in actions['decisions']:
        _assert_callback(controller.apply_candidate_review(ids[decision['packet_id']], {}, decision['decision'],
            reviewer_kind=author, reviewer_name=name, reason=decision['reason'], checks=decision['checks'],
            expected_digest=controller.result['result_sha256']), controller)
        callback_counts['apply_candidate_review'] += 1
    split = actions['split']
    parent_id = ids[split['packet_id']]
    _assert_callback(controller.apply_candidate_split(parent_id, split['parts'], reviewer_kind=author,
        reviewer_name=name, reason=split['reason'], checks=split.get('checks'),
        expected_digest=controller.result['result_sha256']), controller)
    callback_counts['apply_candidate_split'] += 1
    actual_split = controller.result['candidate_review_workflow']['splits'][-1]
    expected_split = next(event for event in expected['candidate_review_workflow']['splits'] if event['parent_id'] == parent_id)
    if len(actual_split['child_ids']) != len(expected_split['child_ids']):
        raise AssertionError('Controller split changed the frozen number of children.')
    id_map.update(zip(expected_split['child_ids'], actual_split['child_ids']))
    # Child review notes and member notes were frozen in REVIEWED.json. Reuse
    # those actual actions instead of authoring replacement judgments in QA.
    for expected_child, actual_child in zip(expected_split['child_ids'], actual_split['child_ids']):
        decision = next(event for event in reversed(expected['candidate_review_workflow']['decisions'])
                        if event['code_id'] == expected_child)
        _assert_callback(controller.apply_candidate_review(actual_child, {}, decision['decision'],
            reviewer_kind=decision['reviewer_kind'], reviewer_name=decision['reviewer_name'],
            reason=decision['reason'], checks=decision['checks'],
            expected_digest=controller.result['result_sha256']), controller)
        callback_counts['apply_child_review'] += 1
    if len(expected.get('reviewed_categories', [])) != 1:
        raise ValueError('Frozen action replay expects exactly one explicit provisional category.')
    frozen_group = expected['reviewed_categories'][0]
    members = [id_map[cid] for cid in frozen_group['code_ids']]
    controller.code_tree.selection_set(*members)
    controller._selection_changed()
    controller.reviewed.set(True)
    if controller._selected_usable() != members:
        raise AssertionError('Actual MAIN selection/review gates rejected the frozen members.')
    category = actions['category']
    _assert_callback(controller.add_reviewed_category(members,
        **{field: category[field] for field in ('label', 'shared_meaning', 'limits', 'reason')},
        reviewer_kind=author,
        member_notes={id_map[member['code_id']]: member['member_note'] for member in frozen_group['members']}), controller)
    callback_counts['add_reviewed_category'] += 1
    if controller.reviewed.get():
        raise AssertionError('MAIN grouping retained its transient review checkbox.')
    _compare_action_result(controller.result, expected, draft, id_map)
    for kind in ('json', 'html'):
        _assert_callback(controller.export(kind, output / f'action-controller-reviewed.{kind}'), controller)
    reopened = make_controller()
    _assert_callback(reopened.load_result(output / 'action-controller-reviewed.json'), reopened)
    if reopened.result != controller.result or reopened.reviewed.get():
        raise AssertionError('Action-generated export/reopen lost provenance or retained transient approval.')
    _compare_action_result(reopened.result, expected, draft, id_map)
    source_entries_after = check_sources(reopened)
    counts = Counter(candidate_review_state(reopened.result, cid)['state'] for cid in reopened._codes)
    return dict(passed=True, callback_counts=dict(callback_counts), initial_candidates=len(draft['codes']),
        final_candidates=len(reopened._codes), decision_summary=dict(counts), source_entries_before=source_entries_before,
        source_entries_after=source_entries_after, substantive_codes_match_frozen_review=True,
        original_sources_calls_and_parent_unchanged=True, human_decisions=0, model_calls=0,
        grouped_AI_checks=dict(category_count=1, origin=author, selection_gate_passed=True,
                              mapped_membership_and_meaning_match=True, transient_review_reset=True),
        child_id_map={key: value for key, value in id_map.items() if key in expected_split['child_ids']},
        export_reopen_exact=True)


def run(draft_path, reviewed_path, output, actions_path=None):
    started = time.monotonic()
    paths = [Path(value) for value in (draft_path, reviewed_path, output)]
    input_paths = paths[:2] + ([Path(actions_path)] if actions_path is not None else [])
    if any(not path.is_absolute() for path in paths + input_paths):
        raise ValueError('Use absolute local input/output paths.')
    draft_path, reviewed_path, output = paths
    if any(str(path).startswith(('\\\\', '//')) for path in paths + input_paths):
        raise ValueError('Headless QA requires local paths.')
    output.mkdir(parents=True, exist_ok=False)
    hashes = {str(path): sha256(path.read_bytes()).hexdigest() for path in input_paths}
    report = dict(validation='headless_controller_and_service', visual_layout_verified=False,
                  model_calls=0, gui_windows=0, input_sha256=hashes, artifacts={})
    with patch('tkinter.Tk', side_effect=forbidden), patch('tkinter.Toplevel', side_effect=forbidden), patch('subprocess.Popen', side_effect=forbidden):
        loaded = []
        for label, path in (('draft', draft_path), ('reviewed', reviewed_path)):
            controller = make_controller()
            if not controller.load_result(path):
                raise AssertionError(controller.status.get())
            source = json.loads(path.read_text(encoding='utf-8'))
            if controller.result != source:
                raise AssertionError('Native load changed the saved draft.')
            counts = check_sources(controller)
            states = Counter(candidate_review_state(controller.result, cid)['state'] for cid in controller._codes)
            for kind in ('json', 'html'):
                target = output / f'{label}-controller.{kind}'
                if not controller.export(kind, target):
                    raise AssertionError(controller.status.get())
            reopened = make_controller()
            if not reopened.load_result(output / f'{label}-controller.json'):
                raise AssertionError(reopened.status.get())
            if reopened.result != source or reopened.reviewed.get():
                raise AssertionError('Native JSON reopen lost provenance or retained transient approval.')
            validate_result_sources(reopened.result)
            report['artifacts'][label] = dict(candidates=len(controller._codes), source_entries=counts,
                decision_summary=dict(states), roundtrip_exact=True,
                original_calls_preserved=controller.result.get('calls') == source.get('calls'))
            loaded.append(source)
        for key in ('calls', 'source_documents', 'registry', 'unit_archive'):
            if loaded[0][key] != loaded[1][key]:
                raise AssertionError(f'Reviewed fixture changed original {key}.')
        if actions_path is not None:
            actions = json.loads(Path(actions_path).read_text(encoding='utf-8'))
            report['actual_saved_candidate_edits_via_main_callbacks'] = replay_saved_actions(
                draft_path, loaded[0], loaded[1], actions, output)
    for path in input_paths:
        if sha256(path.read_bytes()).hexdigest() != hashes[str(path)]:
            raise AssertionError('Input file changed during QA.')
    report['elapsed_seconds'] = time.monotonic() - started
    report['cost_scope'] = 'Own-process source/package headless controller QA; not an inference benchmark.'
    try:
        import psutil
    except ImportError:
        report['own_process_memory'] = dict(available=False, reason='Existing optional psutil unavailable; no dependency installed.')
    else:
        memory = psutil.Process().memory_info()
        peak = getattr(memory, 'peak_wset', None)
        report['own_process_memory'] = dict(available=True, bytes=peak if peak is not None else memory.rss,
            metric='peak_wset' if peak is not None else 'current_rss',
            scope='Process lifetime working-set peak on Windows when provided; current RSS otherwise. Includes startup/imports, excludes child processes.')
    report['passed'] = True
    (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--draft', required=True)
    parser.add_argument('--reviewed', required=True)
    parser.add_argument('--out', required=True)
    parser.add_argument('--actions', help='Absolute path to frozen AI-only DEMO_ACTIONS.json for actual callback replay.')
    args = parser.parse_args(argv)
    report = run(args.draft, args.reviewed, args.out, args.actions)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

"""Citation-review contracts use invented sources and no model execution."""
from copy import deepcopy
import unittest

from themeforge.analysis_types import TranscriptDocument
from themeforge.codebook_review import revise_supporting_evidence, revise_interpretation, revise_evidence_roles
from themeforge.local_codebook import prepare_codebook, validate_result_sources
from themeforge.model_pilot import _digest


def seal(result):
    result["result_sha256"] = _digest({k: v for k, v in result.items() if k != "result_sha256"})
    return result


def fixture(first_text=None):
    docs = [TranscriptDocument("one.txt", first_text or "P1: I keep a spare copy on another disk. It protects my work if a drive fails. I sometimes confuse the versions. I check their dates before restoring."),
            TranscriptDocument("two.txt", "P2: I save copies on another machine. I check that I can open the files.")]
    packet = prepare_codebook(docs)
    result = {key: deepcopy(packet[key]) for key in (
        "source_documents", "registry", "unit_archive", "selected_unit_ids")}
    uid = packet["selected_unit_ids"][0]
    ids = [sid for sid, span in packet["registry"].items() if span["unit_id"] == uid]
    result.update(codes=[dict(id="LC-synthetic", unit_id=uid, label="Keeping a recovery copy",
        claim="A spare copy protects work after drive failure.", definition="Maintaining a separate copy supports recovery.",
        inclusion="Invented fixture inclusion", exclusion="Invented fixture exclusion", uncertainty="Still requires interpretation review.",
        support_span_ids=[ids[0]], evidence=[deepcopy(packet["registry"][ids[0]])], qualifications=[], counterexamples=[],
        status="definition_drafted", origin="local_model", review_status="unreviewed_machine_draft")],
        calls=[{"stage": "decision", "raw": '{"claim":"Synthetic raw model claim retained verbatim"}'}],
        categories=[], interpretive_themes=[], review_status="unreviewed_machine_draft",
        semantic_quality="not_human_validated", training_performed=False)
    seal(result)
    validate_result_sources(result)
    return result, ids


class EvidenceReviewTests(unittest.TestCase):
    def revise(self, result, ids, **kwargs):
        return revise_supporting_evidence(result, "LC-synthetic", ids,
            reviewer_kind=kwargs.pop("reviewer_kind", "researcher"),
            reason=kwargs.pop("reason", "Include the explicit recovery sentence."), **kwargs)

    def test_exact_repair_preserves_original_claim_sources_and_raw_calls(self):
        result, ids = fixture()
        original = deepcopy(result)
        repaired = self.revise(result, ids[:2])
        self.assertEqual(result, original)
        self.assertIsNot(repaired, result)
        self.assertEqual(repaired["codes"][0]["support_span_ids"], ids[:2])
        self.assertEqual(repaired["codes"][0]["evidence"], [result["registry"][sid] for sid in ids[:2]])
        for key in ("label", "claim", "definition", "inclusion", "exclusion", "uncertainty", "status", "origin"):
            self.assertEqual(repaired["codes"][0][key], original["codes"][0][key])
        for key in ("source_documents", "registry", "unit_archive", "selected_unit_ids", "calls", "semantic_quality", "training_performed"):
            self.assertEqual(repaired[key], original[key])
        self.assertEqual(repaired["codes"][0]["original_model_support_span_ids"], [ids[0]])
        self.assertEqual(repaired["codes"][0]["evidence_selection_origin"], "researcher")
        self.assertEqual(repaired["review_status"], "unreviewed_machine_draft")
        self.assertEqual(repaired["codes"][0]["review_status"], "unreviewed_machine_draft")
        expected_event = dict(event="supporting_evidence_revised", code_id="LC-synthetic",
            reviewer_kind="researcher", reason="Include the explicit recovery sentence.", before_result_sha256=original["result_sha256"],
            old_support_span_ids=[ids[0]], new_support_span_ids=ids[:2])
        self.assertEqual({key: repaired['review_history'][0][key] for key in expected_event}, expected_event)
        self.assertNotEqual(repaired["result_sha256"], original["result_sha256"])
        validate_result_sources(repaired)
        repaired["codes"][0]["evidence"][0]["text"] = "Changed returned copy"
        self.assertEqual(result, original)

    def test_repeated_revision_retains_first_model_selection_and_history(self):
        result, ids = fixture()
        first = self.revise(result, ids[:2], reviewer_kind="assistant_development_review")
        second = self.revise(first, [ids[1]])
        self.assertEqual(second["codes"][0]["original_model_support_span_ids"], [ids[0]])
        self.assertEqual(len(second["review_history"]), 2)
        self.assertEqual(second["review_history"][1]["before_result_sha256"], first["result_sha256"])
        self.assertEqual(first["codes"][0]["support_span_ids"], ids[:2])

    def test_rejects_unknown_foreign_duplicate_empty_or_excessive_ids(self):
        result, ids = fixture()
        foreign = next(sid for sid, span in result["registry"].items() if span["unit_id"] != result["codes"][0]["unit_id"])
        for bad in (["unknown"], [foreign], [ids[0], ids[0]], [], ids[0], [None], ids * 7):
            with self.subTest(ids=bad), self.assertRaises(ValueError):
                self.revise(result, bad)

    def test_rejects_overlap_with_either_other_evidence_role(self):
        for role in ("qualifications", "counterexamples"):
            result, ids = fixture()
            result["codes"][0][role] = [deepcopy(result["registry"][ids[1]])]
            seal(result)
            with self.subTest(role=role), self.assertRaises(ValueError):
                self.revise(result, ids[:2])

    def test_rejects_missing_overlong_rationale_and_unsupported_reviewer(self):
        result, ids = fixture()
        for reason in (None, "", "  \n", "x" * 2001):
            with self.subTest(reason=reason), self.assertRaises(ValueError):
                self.revise(result, ids[:2], reason=reason)
        for kind in ("automatic", "human_gold", "", None):
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                self.revise(result, ids[:2], reviewer_kind=kind)

    def test_rejects_incomplete_definition_or_unknown_code(self):
        result, ids = fixture()
        result["codes"][0]["status"] = "decision_only"
        seal(result)
        with self.assertRaises(ValueError):
            self.revise(result, ids[:2])
        with self.assertRaises(ValueError):
            revise_supporting_evidence(result, "missing", ids[:2], reviewer_kind="researcher", reason="Explicit review")

    def test_rejects_any_prior_synthesis_or_category(self):
        for patch in ({"categories": [{"id": "old"}]}, {"synthesis_status": "no_group"},
                      {"synthesis_gate": {}}, {"interpretive_themes": [{"id": "old"}]},
                      {"calls": [{"stage": "synthesis", "raw": "synthetic"}]}):
            result, ids = fixture()
            result.update(patch)
            seal(result)
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                self.revise(result, ids[:2])

    def test_rejects_tampered_result_before_repair(self):
        result, ids = fixture()
        result["codes"][0]["claim"] = "Tampered claim"
        with self.assertRaises(ValueError):
            self.revise(result, ids[:2])


class EvidenceRoleReviewTests(unittest.TestCase):
    def revise(self, result, roles, reviewer_kind='assistant_development_review'):
        return revise_evidence_roles(result, 'LC-synthetic', roles, reviewer_kind=reviewer_kind, reason='Keep distinct source roles explicit.')

    def test_atomic_moves_preserve_raw_prose_sources_originals_and_independent_role_authors(self):
        result, ids = fixture()
        result['codes'][0]['qualifications'] = [deepcopy(result['registry'][ids[1]])]
        seal(result); before = deepcopy(result)
        first = self.revise(result, {'support': [ids[1]], 'qualifications': [ids[0]], 'counterexamples': [ids[2]]})
        code = first['codes'][0]
        self.assertEqual(result, before)
        self.assertEqual(code['original_model_evidence_roles'], {'support': [ids[0]], 'qualifications': [ids[1]], 'counterexamples': []})
        self.assertEqual(code['original_model_support_span_ids'], [ids[0]])
        self.assertEqual(code['evidence_role_origins'], dict.fromkeys(('support', 'qualifications', 'counterexamples'), 'assistant_development_review'))
        second = self.revise(first, {'support': [ids[1]], 'qualifications': [ids[0]], 'counterexamples': []}, reviewer_kind='researcher')
        self.assertEqual(second['codes'][0]['evidence_role_origins'], {'support': 'assistant_development_review', 'qualifications': 'assistant_development_review', 'counterexamples': 'researcher'})
        self.assertEqual(second['codes'][0]['evidence_selection_origin'], 'assistant_development_review')
        event = second['review_history'][-1]
        self.assertEqual(event['old_roles'], {'counterexamples': [ids[2]]})
        self.assertEqual(event['new_roles'], {'counterexamples': []})
        self.assertEqual(event['old_role_origins'], {'counterexamples': 'assistant_development_review'})
        self.assertEqual(event['new_role_origins'], {'counterexamples': 'researcher'})
        self.assertEqual(event['before_result_sha256'], first['result_sha256'])
        for key in ('source_documents', 'registry', 'unit_archive', 'calls'):
            self.assertEqual(second[key], before[key])
        for field in ('label', 'claim', 'definition', 'inclusion', 'exclusion', 'uncertainty'):
            self.assertEqual(second['codes'][0][field], before['codes'][0][field])
        self.assertEqual(second['semantic_quality'], 'not_human_validated')
        validate_result_sources(second)

    def test_legacy_citation_then_roles_and_later_support_only_retain_true_originals_and_authors(self):
        result, ids = fixture()
        legacy = revise_supporting_evidence(result, 'LC-synthetic', ids[:2], reviewer_kind='assistant_development_review', reason='Cite both source sentences.')
        # Historical support-only artifacts predate both role metadata and the
        # role dictionaries on events. Retaining new event keys while deleting
        # code metadata is damaged current provenance, not a legacy artifact.
        legacy['codes'][0].pop('original_model_evidence_roles')
        legacy['codes'][0].pop('evidence_role_origins')
        for key in ('old_roles', 'new_roles', 'old_role_origins', 'new_role_origins'):
            legacy['review_history'][-1].pop(key)
        seal(legacy)
        validate_result_sources(legacy)
        roles = self.revise(legacy, {'qualifications': [ids[2]]}, reviewer_kind='researcher')
        self.assertEqual(roles['codes'][0]['original_model_evidence_roles']['support'], [ids[0]])
        self.assertEqual(roles['codes'][0]['evidence_role_origins']['support'], 'assistant_development_review')
        final = revise_supporting_evidence(roles, 'LC-synthetic', [ids[1]], reviewer_kind='researcher', reason='Use the recovery sentence only.')
        self.assertEqual(final['codes'][0]['evidence_role_origins']['support'], 'researcher')
        self.assertEqual(final['codes'][0]['evidence_role_origins']['qualifications'], 'researcher')
        self.assertEqual(final['codes'][0]['original_model_evidence_roles'], {'support': [ids[0]], 'qualifications': [], 'counterexamples': []})
        self.assertEqual(final['codes'][0]['original_model_support_span_ids'], [ids[0]])
        self.assertEqual(final['review_history'][-1]['old_role_origins']['support'], 'assistant_development_review')

    def test_new_support_event_cannot_masquerade_as_legacy_after_role_metadata_removed(self):
        result, ids = fixture()
        revised = revise_supporting_evidence(result, 'LC-synthetic', ids[:2],
            reviewer_kind='assistant_development_review', reason='Cite both exact source sentences.')
        revised['codes'][0].pop('original_model_evidence_roles')
        revised['codes'][0].pop('evidence_role_origins')
        seal(revised)
        self.assertIn('old_roles', revised['review_history'][-1])
        for validate in (validate_result_sources,
                         lambda value: self.revise(value, {'qualifications': [ids[2]]}, reviewer_kind='researcher')):
            with self.subTest(validator=validate), self.assertRaisesRegex(ValueError, 'requires its original selections'):
                validate(revised)

    def test_foreign_stale_overlap_empty_support_noop_types_and_unknown_roles_rejected(self):
        result, ids = fixture()
        foreign = next(sid for sid, span in result['registry'].items() if span['unit_id'] != result['codes'][0]['unit_id'])
        invalid = ({'support': []}, {'support': ['stale']}, {'qualifications': [foreign]},
                   {'qualifications': [ids[0]]}, {'qualifications': [ids[1]], 'counterexamples': [ids[1]]},
                   {'qualifications': [ids[1], ids[1]]}, {'support': [ids[0]]}, {'qualifications': []},
                   {'qualifications': (ids[1],)}, {'qualifications': ids[1]}, {'qualifications': [True]},
                   {'evidence': [ids[1]]}, {}, [], None)
        for roles in invalid:
            with self.subTest(roles=roles), self.assertRaises(ValueError):
                self.revise(result, roles)
        invalid_provenance = deepcopy(result)
        invalid_provenance['codes'][0]['evidence_role_origins'] = {'qualifications': 'assistant_development_review'}
        seal(invalid_provenance)
        with self.assertRaisesRegex(ValueError, 'Original model'):
            self.revise(invalid_provenance, {'support': ids[:2]})

    def test_other_role_cap_post_synthesis_and_input_integrity(self):
        result, ids = fixture('P1: ' + ' '.join(f'I retain record batch {i} on another machine.' for i in range(10)))
        self.assertGreaterEqual(len(ids), 9)
        boundary = self.revise(result, {'qualifications': ids[1:4], 'counterexamples': ids[4:7]})
        validate_result_sources(boundary)
        with self.assertRaises(ValueError):
            self.revise(result, {'qualifications': ids[1:5], 'counterexamples': ids[5:8]})
        for patch in ({'synthesis_status': 'no_group'}, {'synthesis_gate': {}}, {'categories': [{'id': 'old'}]},
                      {'calls': [{'stage': 'synthesis', 'status': 'runtime_failure'}]}):
            changed = deepcopy(result); changed.update(patch); seal(changed)
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                self.revise(changed, {'qualifications': [ids[1]]})
        result['codes'][0]['claim'] = 'Unsealed alteration'
        with self.assertRaises(ValueError):
            self.revise(result, {'qualifications': [ids[1]]})

    def test_reordered_unchanged_role_keeps_selection_order_and_original_author(self):
        result, ids = fixture()
        result['codes'][0]['qualifications'] = [deepcopy(result['registry'][sid]) for sid in ids[1:3]]
        seal(result)
        first = self.revise(result, {'support': [ids[0], ids[3]]})
        revised = self.revise(first, {'support': [ids[3], ids[0]], 'qualifications': [ids[2]]}, reviewer_kind='researcher')
        self.assertEqual(revised['codes'][0]['support_span_ids'], [ids[0], ids[3]])
        self.assertEqual(revised['codes'][0]['evidence_role_origins']['support'], 'assistant_development_review')
        self.assertEqual(revised['review_history'][-1]['new_roles'], {'qualifications': [ids[2]]})
        with self.assertRaisesRegex(ValueError, 'must change'):
            self.revise(first, {'support': [ids[3], ids[0]]}, reviewer_kind='researcher')


class InterpretationReviewTests(unittest.TestCase):
    def revise(self, result, changes, *, reviewer_kind='assistant_development_review', reason='Preserve the participant-stated condition.'):
        return revise_interpretation(result, 'LC-synthetic', changes, reviewer_kind=reviewer_kind, reason=reason)

    def test_explicit_wording_edit_preserves_raw_prose_sources_and_unreviewed_status(self):
        result, _ = fixture()
        original = deepcopy(result)
        changes = {'label': 'Recovery after a drive failure', 'claim': 'A separate copy can help if a drive fails.'}
        revised = self.revise(result, changes)
        code = revised['codes'][0]
        self.assertEqual(result, original)
        self.assertEqual(code['original_model_prose'], {field: original['codes'][0][field] for field in ('label', 'claim', 'definition')})
        for field, value in changes.items():
            self.assertEqual(code[field], value)
            self.assertEqual(code['interpretation_field_origins'][field], 'assistant_development_review')
            self.assertEqual(code['field_origins'][field], 'assistant_development_review')
        self.assertEqual(code['interpretation_field_origins']['definition'], 'local_model')
        for key in ('calls', 'source_documents', 'registry', 'unit_archive', 'selected_unit_ids', 'semantic_quality'):
            self.assertEqual(revised[key], original[key])
        self.assertEqual(code['evidence'], original['codes'][0]['evidence'])
        self.assertEqual(code['review_status'], 'unreviewed_machine_draft')
        self.assertEqual(revised['review_status'], 'unreviewed_machine_draft')
        event = revised['review_history'][0]
        self.assertEqual(event['before_result_sha256'], original['result_sha256'])
        self.assertEqual(event['old_values'], {field: original['codes'][0][field] for field in changes})
        self.assertEqual(event['new_values'], changes)
        self.assertEqual(event['reviewer_kind'], 'assistant_development_review')
        self.assertIn('meaning_checks', code)
        validate_result_sources(revised)

    def test_mixed_repeated_edits_retain_original_prose_and_field_author_lineage(self):
        result, ids = fixture()
        first = self.revise(result, {'label': 'A separate recovery copy'})
        second = self.revise(first, {'claim': 'A researcher-authored interpretation.'}, reviewer_kind='researcher')
        third = self.revise(second, {'label': 'A researcher-refined recovery label'}, reviewer_kind='researcher')
        code = third['codes'][0]
        self.assertEqual(code['original_model_prose'], first['codes'][0]['original_model_prose'])
        self.assertEqual(third['review_history'][2]['old_field_origins'], {'label': 'assistant_development_review'})
        self.assertEqual(third['review_history'][2]['before_result_sha256'], second['result_sha256'])
        self.assertEqual(code['interpretation_field_origins']['claim'], 'researcher')
        self.assertEqual(third['review_history'][0]['reviewer_kind'], 'assistant_development_review')
        combined = revise_supporting_evidence(third, code['id'], ids[:2], reviewer_kind='researcher', reason='Cite both relevant sentences.')
        self.assertEqual(len(combined['review_history']), 4)
        self.assertEqual(combined['calls'], result['calls'])
        self.assertEqual(combined['codes'][0]['original_model_prose'], code['original_model_prose'])

    def test_revision_does_not_inherit_a_prior_semantic_approval_label(self):
        result, _ = fixture()
        result['semantic_quality'] = 'human_validated'
        result['review_status'] = 'human_reviewed'
        seal(result)
        revised = self.revise(result, {'label': 'A newly authored recovery interpretation'}, reviewer_kind='researcher')
        self.assertEqual(revised['semantic_quality'], 'not_human_validated')
        self.assertEqual(revised['review_status'], 'unreviewed_machine_draft')
        self.assertEqual(result['semantic_quality'], 'human_validated')

    def test_unchanged_supplied_fields_keep_their_author_and_are_not_revision_events(self):
        result, _ = fixture()
        first = self.revise(result, {'label': 'Assistant-authored recovery label'})
        changes = {'label': first['codes'][0]['label'], 'claim': first['codes'][0]['claim'],
                   'definition': 'A researcher-authored interpretation of recovery.'}
        revised = self.revise(first, changes, reviewer_kind='researcher')
        code = revised['codes'][0]
        self.assertEqual(code['interpretation_field_origins']['label'], 'assistant_development_review')
        self.assertEqual(code['field_origins']['label'], 'assistant_development_review')
        self.assertEqual(code['interpretation_field_origins']['claim'], 'local_model')
        self.assertEqual(code.get('field_origins', {}).get('claim', 'local_model'), 'local_model')
        self.assertEqual(code['field_origins']['definition'], 'researcher')
        self.assertEqual(revised['review_history'][-1]['new_values'], {'definition': changes['definition']})
        self.assertEqual(revised['review_history'][-1]['old_values'], {'definition': first['codes'][0]['definition']})
        self.assertEqual(revised['review_history'][-1]['new_field_origins'], {'definition': 'researcher'})
        self.assertEqual(code['original_model_prose'], first['codes'][0]['original_model_prose'])
        self.assertEqual(first['codes'][0]['label'], changes['label'])
        self.assertEqual(revised['calls'], result['calls'])

    def test_rejects_unsupported_empty_placeholder_overlong_or_noop_changes(self):
        result, _ = fixture()
        for changes in ({}, {'exclusion': 'A new boundary'}, {'status': 'human_validated'}, {'label': True},
                        {'label': ' '}, {'claim': 'True'}, {'definition': 'x' * 601}, {'label': 'x' * 121},
                        {'claim': result['codes'][0]['claim']}, []):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.revise(result, changes)
        for kwargs in ({'reason': ''}, {'reason': 'x' * 2001}, {'reviewer_kind': 'automatic'}, {'reviewer_kind': 'human_gold'}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.revise(result, {'label': 'Revised label'}, **kwargs)

    def test_refuses_tampered_incomplete_unknown_and_post_synthesis_results(self):
        result, _ = fixture()
        for patch in ({'synthesis_status': 'no_group'}, {'synthesis_gate': {}}, {'categories': [{'id': 'old'}]},
                      {'calls': [{'stage': 'synthesis'}]}):
            modified = deepcopy(result); modified.update(patch); seal(modified)
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                self.revise(modified, {'label': 'Revised label'})
        result['codes'][0]['claim'] = 'Unsealed change'
        with self.assertRaises(ValueError):
            self.revise(result, {'label': 'Revised label'})
        seal(result); result['codes'][0]['status'] = 'scope_rejected'; seal(result)
        with self.assertRaises(ValueError):
            self.revise(result, {'label': 'Revised label'})
        with self.assertRaises(ValueError):
            revise_interpretation(result, 'unknown', {'label': 'New label'}, reviewer_kind='researcher', reason='Explicit review')

    def test_citation_revision_recomputes_coverage_without_removing_original_flags(self):
        result, ids = fixture()
        result['codes'][0]['quality_flags'] = ['original_warning']
        selected = set(result['codes'][0]['support_span_ids'])
        result['uncovered_spans'] = [dict(span_id=sid, **deepcopy(span)) for sid, span in result['registry'].items() if sid not in selected]
        seal(result)
        revised = revise_supporting_evidence(result, 'LC-synthetic', ids[:2], reviewer_kind='researcher', reason='Add the recovery explanation.')
        self.assertEqual(revised['codes'][0]['quality_flags'], ['original_warning'])
        self.assertNotIn(ids[1], [span['span_id'] for span in revised['uncovered_spans']])
        self.assertIn(ids[1], [span['span_id'] for span in result['uncovered_spans']])
        validate_result_sources(revised)

    def test_failed_definition_requires_explicit_definition_and_keeps_host_boundaries(self):
        from themeforge.local_codebook import _HOST_SCOPE_NOTICES, has_completed_definition
        for status in ('scope_insufficient', 'scope_rejected'):
            for reviewer in ('researcher', 'assistant_development_review'):
                result, ids = fixture()
                code = result['codes'][0]
                code.update(status=status, definition='', inclusion='', exclusion='', uncertainty='', scope_origin='local_model')
                code['quality_flags'] = ['scope_contract_rejected' if status == 'scope_rejected' else 'scope_insufficient']
                seal(result)
                with self.subTest(status=status, reviewer=reviewer):
                    for changes in ({'label': 'A new label'}, {'definition': ' '}, {'definition': 'True'}):
                        with self.assertRaises(ValueError):
                            self.revise(result, changes, reviewer_kind=reviewer)
                    with self.assertRaises(ValueError):
                        revise_supporting_evidence(result, code['id'], ids[:2], reviewer_kind=reviewer, reason='Needs definition first.')
                    repaired = self.revise(result, {'definition': 'A separate copy can support recovery after disk failure.'}, reviewer_kind=reviewer)
                    revised = repaired['codes'][0]
                    self.assertTrue(has_completed_definition(revised))
                    self.assertEqual(revised['status'], 'definition_revised')
                    self.assertEqual(revised['scope_origin'], reviewer)
                    self.assertEqual(revised['scope_boundary_status'], 'not_established')
                    for field, notice in _HOST_SCOPE_NOTICES.items():
                        self.assertEqual(revised[field], notice)
                    self.assertEqual(revised['field_origins']['definition'], reviewer)
                    self.assertEqual(revised['field_origins']['exclusion'], 'host_unestablished_notice')
                    self.assertEqual(revised['quality_flags'], code['quality_flags'])
                    self.assertEqual(revised['original_model_prose']['definition'], '')
                    self.assertEqual(repaired['calls'], result['calls'])
                    event = repaired['review_history'][0]
                    self.assertEqual(event['old_status'], status)
                    self.assertEqual(event['new_status'], 'definition_revised')
                    self.assertEqual(event['old_scope_origin'], 'local_model')
                    self.assertEqual(event['new_scope_origin'], reviewer)
                    self.assertEqual(event['old_values'], {'definition': ''})
                    self.assertEqual(repaired['semantic_quality'], 'not_human_validated')
                    with_citations = revise_supporting_evidence(repaired, code['id'], ids[:2], reviewer_kind=reviewer, reason='Include recovery clause.')
                    validate_result_sources(with_citations)


if __name__ == "__main__":
    unittest.main()

"""Staged pipeline tests use invented source text and a deterministic test double."""
import json
import threading
import unittest
from copy import deepcopy

from themeforge.analysis_types import TranscriptDocument
from themeforge.local_codebook import (prepare_codebook, validate_prepared, run_codebook,
    run_synthesis, export_codebook_html, validate_result_sources)
from themeforge.model_pilot import _digest


DOCS = [TranscriptDocument("one.txt", "Interviewer: How do you store files?\nP1: I keep an extra copy so a failed drive cannot destroy my work. But a new version can still confuse me."),
        TranscriptDocument("two.txt", "Interviewer: How do you store files?\nP2: I store a spare copy on another machine because disks sometimes fail.")]


class FixtureClient:
    def __init__(self, *, abstain=False, bad_scope=False, fail=False, cancel=False):
        self.started = self.closed = 0
        self.abstain, self.bad_scope, self.fail, self.cancel = abstain, bad_scope, fail, cancel
        self.stages = []

    def start(self, event):
        self.started += 1

    def close(self):
        self.closed += 1

    def generate(self, messages, schema, event):
        if self.fail:
            raise RuntimeError("Synthetic deadline")
        if self.cancel:
            event.set()
        data = json.loads(messages[1]["content"])
        if "codes" in data:
            self.stages.append("synthesis")
            value = dict(decision="group", label="Copies for recovery", shared_meaning="Separate copies protect work after storage failure.",
                         code_ids=[c["id"] for c in data["codes"][:2]], qualification_code_ids=[], counterexample_code_ids=[], uncertainty="A provisional grouping.")
        elif "candidate" in data:
            self.stages.append("scope")
            free = [e["id"] for e in data["evidence"] if e["id"] not in data["candidate"]["support_ids"]]
            value = dict(decision="define", definition="Keeping separate copies supports recovery from a failed device.",
                         inclusion="True" if self.bad_scope else "Include maintaining copies to protect work after a device failure.",
                         exclusion="Exclude unrelated storage preferences without recovery purpose.",
                         qualification_ids=free[:1], counterexample_ids=[], uncertainty="Interpretation remains a draft.")
        else:
            self.stages.append("decision")
            value = dict(decision="abstain", reason="Synthetic insufficient evidence.") if self.abstain else dict(
                decision="code", label="Keeping a recovery copy", claim="A separate copy protects work against device failure.", evidence_ids=[data["evidence"][0]["id"]])
        return {"raw": json.dumps(value), "usage": {}, "resources": {"synthetic": True}}


class LocalCodebookTests(unittest.TestCase):
    def test_prepare_keeps_open_context_out_and_sources_intact(self):
        packet = prepare_codebook(DOCS, research_questions=("PRIVATE_GUIDE",))
        validate_prepared(packet)
        self.assertNotIn("PRIVATE_GUIDE", json.dumps(packet["decision_requests"]))
        self.assertEqual(len(packet["selected_unit_ids"]), 2)
        self.assertEqual(packet["source_documents"][0]["text"], DOCS[0].text)
        changed = deepcopy(packet)
        changed["source_documents"][0]["text"] += " modified"
        with self.assertRaises(ValueError): validate_prepared(changed)

    def test_two_stages_resolve_support_qualifications_and_keep_unassigned(self):
        client = FixtureClient()
        result = run_codebook(DOCS, client)
        self.assertEqual(client.stages, ["decision", "scope", "decision", "scope"])
        self.assertEqual(client.closed, 1)
        self.assertEqual(len(result["codes"]), 2)
        self.assertTrue(result["codes"][0]["qualifications"])
        self.assertTrue(result["unassigned"])
        self.assertEqual(result["interpretive_themes"], [])
        validate_result_sources(result)

    def test_abstention_never_generates_a_scope_or_code(self):
        client = FixtureClient(abstain=True)
        result = run_codebook(DOCS, client)
        self.assertEqual(client.stages, ["decision", "decision"])
        self.assertEqual(result["codes"], [])
        self.assertEqual(sum(x["reason"] == "Synthetic insufficient evidence." for x in result["unassigned"]), 2)

    def test_invalid_scope_remains_visible_and_cannot_enter_synthesis(self):
        result = run_codebook(DOCS, FixtureClient(bad_scope=True))
        self.assertTrue(all(c["status"] == "scope_rejected" for c in result["codes"]))
        client = FixtureClient()
        with self.assertRaises(ValueError): run_synthesis(result, client, [c["id"] for c in result["codes"]])
        self.assertEqual(client.started, 0)

    def test_review_gate_source_checks_and_category_level(self):
        result = run_codebook(DOCS, FixtureClient())
        ids = [c["id"] for c in result["codes"]]
        with self.assertRaises(ValueError): run_synthesis(result, FixtureClient(), ids, reviewer_kind="automatic")
        grouped = run_synthesis(result, FixtureClient(), ids, reviewer_kind="assistant_development_review")
        self.assertEqual(grouped["categories"][0]["kind"], "category")
        self.assertEqual(grouped["interpretive_themes"], [])
        self.assertEqual(grouped["synthesis_gate"]["reviewer_kind"], "assistant_development_review")
        self.assertEqual(result["categories"], [])
        damaged = deepcopy(result); damaged["codes"][0]["evidence"][0]["text"] = "Invented"
        with self.assertRaises(ValueError): run_synthesis(damaged, FixtureClient(), ids)

    def test_long_refusal_preserves_raw_and_over_bound_is_rejection_not_abstention(self):
        result = run_codebook(DOCS, FixtureClient())
        before = deepcopy(result)
        ids = [c['id'] for c in result['codes']]
        complete = ('This synthetic account describes a distinct circumstance. ' * 10
                    + 'No shared relationship is established by this test fixture.')
        self.assertGreater(len(complete), 400)
        for reason, expected in ((complete, 'no_group'), ('x' * 1601, 'contract_rejected')):
            raw = json.dumps(dict(decision='no_group', reason=reason))
            class RefusalClient(FixtureClient):
                def generate(self, messages, schema, event):
                    assert 'maxLength' not in schema['oneOf'][1]['properties']['reason']
                    return {'raw': raw, 'usage': {}, 'resources': {'synthetic': True}}
            client = RefusalClient()
            with self.subTest(expected=expected):
                revised = run_synthesis(result, client, ids, reviewer_kind='assistant_development_review')
                self.assertEqual(revised['synthesis_status'], expected)
                self.assertEqual(revised['calls'][-1]['raw'], raw)
                self.assertEqual(revised['categories'], [])
                self.assertEqual(client.closed, 1)
                if expected == 'no_group':
                    self.assertEqual(revised['synthesis_reason'], complete)
                    validate_result_sources(revised)
                    self.assertIn(complete, export_codebook_html(revised))
                else:
                    self.assertNotIn('synthesis_reason', revised)
                    self.assertEqual(revised['calls'][-1]['status'], 'contract_rejected')
        self.assertEqual(result, before)

    def test_runtime_failure_and_cancel_close_without_retry(self):
        for client in (FixtureClient(fail=True), FixtureClient(cancel=True)):
            result = run_codebook(DOCS, client)
            self.assertEqual(len(result["calls"]), 1)
            self.assertEqual(client.closed, 1)
            self.assertIn(result["status"], {"runtime_failure", "cancelled"})
            self.assertTrue(all(c["status"] != "scope_passed" for c in result["codes"]))

    def test_precancelled_and_empty_runs_never_start(self):
        event = threading.Event(); event.set()
        client = FixtureClient()
        result = run_codebook(DOCS, client, cancel_event=event)
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(client.started, 0)
        empty = FixtureClient(); self.assertEqual(run_codebook([], empty)["status"], "nothing_prepared")
        self.assertEqual(empty.started, 0)

    def test_decision_only_comparison_is_not_scope_ready_and_html_is_escaped(self):
        class UnsafeLabelClient(FixtureClient):
            def generate(self, *args, **kwargs):
                response = super().generate(*args, **kwargs)
                value = json.loads(response['raw'])
                if value.get('decision') == 'code':
                    value['label'] = '<script>unsafe()</script>'
                response['raw'] = json.dumps(value)
                return response
        result = run_codebook(DOCS, UnsafeLabelClient(), define_scope=False)
        self.assertTrue(all(c["status"] == "decision_only" for c in result["codes"]))
        html = export_codebook_html(result)
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("Unassigned", html)

    def test_target_first_variant_is_explicit_and_limits_generation_context(self):
        legacy = prepare_codebook(DOCS)
        packet = prepare_codebook(DOCS, prompt_variant="target-first-v2")
        validate_prepared(legacy); validate_prepared(packet)
        self.assertNotEqual(legacy["packet_sha256"], packet["packet_sha256"])
        self.assertEqual(legacy["selected_unit_ids"], packet["selected_unit_ids"])
        self.assertNotIn("surrounding_context_not_evidence", json.loads(packet["decision_requests"][0]["messages"][1]["content"]))
        result = run_codebook([], FixtureClient(), prepared=packet)
        payload = json.loads(result["calls"][1]["messages"][1]["content"])
        self.assertNotIn("preceding_question_not_evidence", payload)
        self.assertTrue(result["codes"][0]["evidence"][0]["context_text"])

    def test_interrupt_preserves_completed_response_and_closes_client(self):
        class Interrupted(FixtureClient):
            def generate(self, messages, schema, event):
                if self.stages:
                    raise KeyboardInterrupt()
                return super().generate(messages, schema, event)
        client = Interrupted()
        result = run_codebook(DOCS, client)
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(result["calls"][0]["status"], "contract_passed")
        self.assertIn("raw", result["calls"][0])
        self.assertEqual(client.closed, 1)

    def test_duplicate_source_content_cannot_establish_cross_source_support(self):
        duplicate = [TranscriptDocument("one", DOCS[0].text), TranscriptDocument("copy", DOCS[0].text)]
        result = run_codebook(duplicate, FixtureClient())
        with self.assertRaises(ValueError):
            run_synthesis(result, FixtureClient(), [c["id"] for c in result["codes"]])

    def test_repeated_grouping_preserves_prior_actor_and_outcome_without_launch(self):
        original = run_codebook(DOCS, FixtureClient())
        ids = [c['id'] for c in original['codes']]
        class NoGroup(FixtureClient):
            def generate(self, messages, schema, event):
                return dict(raw=json.dumps(dict(decision='no_group', reason='Distinct meanings do not justify grouping.')), usage={}, resources={})
        for first_client in (FixtureClient(), NoGroup(), FixtureClient(fail=True)):
            grouped = run_synthesis(original, first_client, ids, reviewer_kind='assistant_development_review')
            before = deepcopy(grouped)
            next_client = FixtureClient()
            with self.subTest(status=grouped['synthesis_status']), self.assertRaisesRegex(ValueError, 'already been attempted'):
                run_synthesis(grouped, next_client, ids, reviewer_kind='researcher')
            self.assertEqual(grouped, before)
            self.assertEqual(next_client.started, 0)
            self.assertEqual(grouped['synthesis_gate']['reviewer_kind'], 'assistant_development_review')
            report = export_codebook_html(grouped)
            self.assertIn('Grouping input review:', report)
            self.assertIn('assistant_development_review', report)

    def test_rehashed_wrong_unit_and_overlapping_roles_remain_invalid(self):
        original = run_codebook(DOCS, FixtureClient())
        for mode in ("wrong_unit", "overlap"):
            result = deepcopy(original)
            if mode == "wrong_unit":
                result["codes"][0]["unit_id"] = result["codes"][1]["unit_id"]
            else:
                result["codes"][0]["qualifications"] = deepcopy(result["codes"][0]["evidence"])
            result["result_sha256"] = _digest({k: v for k, v in result.items() if k != "result_sha256"})
            with self.assertRaises(ValueError): validate_result_sources(result)

    def test_explicit_role_policy_is_carried_through_source_validation(self):
        docs = [TranscriptDocument("roles", "Interviewer 1: How do you store copies?\nParticipant 4: I keep copies. Here is\nhow that works: I put another copy on a spare drive.")]
        packet = prepare_codebook(docs, prompt_variant="target-first-v2", speaker_label_policy="explicit_roles")
        validate_prepared(packet)
        targets = [u for u in packet["unit_archive"] if u["id"] in packet["selected_unit_ids"]]
        self.assertEqual([u["speaker"] for u in targets], ["Participant 4"])
        self.assertIn("how that works:", targets[0]["text"])
        result = run_codebook([], FixtureClient(), prepared=packet)
        validate_result_sources(result)
        self.assertEqual(result["speaker_label_policy"], "explicit_roles")

    def test_copied_fictional_scope_is_visible_but_ineligible_for_grouping(self):
        class Copying(FixtureClient):
            def generate(self, messages, schema, event):
                result = super().generate(messages, schema, event)
                value = json.loads(result["raw"])
                if value["decision"] == "define":
                    value["exclusion"] = "Exclude feedback received only after submission or unrelated preferences about writing style."
                    result["raw"] = json.dumps(value)
                return result
        result = run_codebook(DOCS, Copying(), prompt_variant="examples-v3")
        self.assertTrue(all(c["status"] == "scope_review_required" for c in result["codes"]))
        self.assertTrue(all("copied_fictional_scope_example:exclusion" in c["quality_flags"] for c in result["codes"]))
        with self.assertRaises(ValueError): run_synthesis(result, FixtureClient(), [c["id"] for c in result["codes"]])

    def test_grounded_scope_does_not_generate_boundaries_or_expose_demonstrations(self):
        class Grounded(FixtureClient):
            def generate(self, messages, schema, event):
                result = super().generate(messages, schema, event)
                value = json.loads(result["raw"])
                if value["decision"] == "define":
                    for key in ("inclusion", "exclusion", "uncertainty"):
                        value.pop(key)
                    result["raw"] = json.dumps(value)
                return result
        packet = prepare_codebook(DOCS, prompt_variant="grounded-v4")
        validate_prepared(packet)
        result = run_codebook([], Grounded(), prepared=packet)
        validate_result_sources(result)
        self.assertEqual(result["status"], "completed")
        for code in result["codes"]:
            self.assertEqual(code["status"], "definition_drafted")
            self.assertEqual(code["scope_boundary_status"], "not_established")
            self.assertEqual(code["field_origins"]["exclusion"], "host_unestablished_notice")
            self.assertIn("No exclusion boundary", code["exclusion"])
        for call in result["calls"]:
            self.assertNotIn("FICTIONAL", json.dumps(call["messages"]))
            self.assertNotIn("writing style", json.dumps(call["messages"]))
        grouped = run_synthesis(result, FixtureClient(), [c["id"] for c in result["codes"]],
                                reviewer_kind="assistant_development_review")
        self.assertEqual(len(grouped["categories"]), 1)
        self.assertEqual(grouped["interpretive_themes"], [])
        self.assertIn("host_unestablished_notice", export_codebook_html(result))

    def test_grounded_scope_rejects_invented_rules_even_when_source_ids_valid(self):
        result = run_codebook(DOCS, FixtureClient(), prompt_variant="grounded-v4")
        self.assertTrue(all(c["status"] == "scope_rejected" for c in result["codes"]))
        html = export_codebook_html(result)
        self.assertIn('Rejected or failed model responses', html)
        self.assertIn('not an accepted code or category', html)
        self.assertIn('contract_rejected', html)
        with self.assertRaises(ValueError):
            run_synthesis(result, FixtureClient(), [c["id"] for c in result["codes"]])


if __name__ == "__main__": unittest.main()

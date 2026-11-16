"""Synthetic whole-study sessions; no clients execute a model or network call."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import threading
import unittest

from themeforge.analysis_types import TranscriptDocument
from themeforge.local_inference import InferenceLimits
from themeforge.study_batches import prepare_study
from themeforge.study_session import build_native_plan, execute_pending, validate_session, export_session_html, source_review_cues


CODE = {'decision':'codes','codes':[{'label':'Keeping a backup copy', 'claim':'The participant keeps a backup copy on a separate drive.', 'evidence_ids':['e1'], 'referent_ids':[]}]}
ABSTAIN = {'decision':'abstain','reason':'No source-grounded candidate proposed by this synthetic fixture.'}
DEFINE = {'decision':'define','definition':'Keeping a separate copy of project material for possible later recovery.', 'qualification_ids':[], 'counterexample_ids':[]}


class SessionClient:
    """Explicit fake tokenizer: complete serialized messages map to UTF8 IDs."""
    def __init__(self, responses=()):
        self.config = SimpleNamespace(limits=InferenceLimits(context_tokens=4096,output_tokens=128,memory_limit_bytes=None),model_sha256='c'*64,executable_sha256='d'*64)
        self.responses = list(responses)
        self.generated = []
        self.measured = []
        self.expected_tokens = None
        self.period_deadline = None
        self.native_seconds = 0.0
        self.completed_generation_seconds = 0.0
        self._injected_transport = True
        self.process = None
        self.identity={'template_sha256':'a'*64,'tokenizer_sha256':'b'*64}

    def measure_messages(self, messages, **kwargs):
        rendered = 'BOS\n'+json.dumps(messages,ensure_ascii=False,sort_keys=True)+'\nASSISTANT'
        self.measured.append(deepcopy(messages))
        return {'rendered_prompt':rendered,'token_ids':[256,*rendered.encode()], 'template_sha256':'a'*64,'tokenizer_sha256':'b'*64}

    def generate(self, messages, schema, cancel):
        self.generated.append({'messages':deepcopy(messages),'schema':schema,'expected_tokens':deepcopy(self.expected_tokens)})
        value = self.responses.pop(0)
        if isinstance(value,Exception):
            raise value
        if callable(value):
            value = value()
        return {'raw':json.dumps(value), 'origin':'synthetic_test_only'}

    def diagnostics(self):
        return {'origin':'synthetic_test_only','owned_process_closed':True}


class StudySessionTests(unittest.TestCase):
    def test_review_cues_preserve_raw_candidate_and_do_not_approve_meaning(self):
        client = SessionClient([CODE])
        study = prepare_study([TranscriptDocument('synthetic-modal',
            'Interviewer 1: What might happen?\nParticipant 1: We might keep a backup copy if space permits.')])
        session = build_native_plan(study, client)
        result = execute_pending(session, client, self.root/'modal', define_scope=False)
        before = deepcopy(result)
        cues = source_review_cues(result, result['codes'][0])
        kinds = {row['kind'] for row in cues['warnings']}
        self.assertIn('possibility_scope_needs_review', kinds)
        self.assertIn('conditional_scope_needs_review', kinds)
        self.assertIn('computed_review_cues_not_model_revision', export_session_html(result))
        self.assertEqual(result, before)
        self.assertFalse(result['codes'][0]['human_review'])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'tmp')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def plan(self, count=3, client=None):
        client = client or SessionClient()
        docs = [TranscriptDocument(f'synthetic-{i:02}',f'Interviewer 1: What do you keep?\nParticipant 1: We keep a backup copy on a separate drive for project {i}.') for i in range(count)]
        study = prepare_study(docs)
        session = build_native_plan(study,client)
        return session,client

    def test_more_than_twenty_units_codes_abstention_schema_failure_and_source_exactness(self):
        client = SessionClient([CODE,ABSTAIN,{'decision':'codes','codes':[dict(CODE['codes'][0],evidence_ids=['foreign'])]}]+[ABSTAIN]*18)
        session,client = self.plan(21,client)
        before = deepcopy(session)
        self.assertEqual(len(session['plan']['batches']),21)
        self.assertEqual(len(client.measured),21)
        for batch in session['plan']['batches']:
            self.assertEqual(batch['reserved_total_tokens'],len(batch['native_prompt']['token_ids'])+129)
            self.assertLessEqual(batch['reserved_total_tokens'],4096)
        result = execute_pending(session,client,self.root/'run',define_scope=False)
        self.assertEqual(session,before)
        self.assertEqual(len(client.generated),21)
        self.assertEqual(result['coverage']['work_status_counts'],{'completed':1,'unassigned':19,'failed':1})
        self.assertEqual(len(result['codes']),1)
        self.assertEqual(result['calls'][2]['status'],'failed')
        self.assertIn('foreign',result['calls'][2]['response']['raw'])
        validate_session(result)
        source = {s['source_name']:s for s in result['plan']['study']['sources']}
        for code in result['codes']:
            for span in code['evidence']:
                self.assertEqual(source[span['source_name']]['text'][span['source_start']:span['source_end']],span['text'])
        self.assertIn('not exhaustive',result['coverage']['notice'])
        self.assertIn('synthetic-20',export_session_html(result))

    def test_cancel_then_resume_never_repeats_completed(self):
        cancel = threading.Event()
        def first():
            cancel.set(); return CODE
        client = SessionClient([first])
        session,client = self.plan(3,client)
        first_result = execute_pending(session,client,self.root/'first',cancel_event=cancel,define_scope=False)
        self.assertEqual(len(client.generated),1)
        self.assertEqual(first_result['coverage']['work_status_counts'],{'completed':1,'pending':2})
        client.responses = [ABSTAIN,ABSTAIN]
        resumed = execute_pending(first_result,client,self.root/'resume',define_scope=False)
        self.assertEqual(len(client.generated),3)
        self.assertEqual(len(resumed['calls']),3)
        self.assertEqual(resumed['codes'],first_result['codes'])
        self.assertEqual(first_result['coverage']['work_status_counts']['pending'],2)
        self.assertEqual(resumed['coverage']['work_status_counts'],{'completed':1,'unassigned':2})

    def test_definition_timeout_and_schema_failure_retained_and_incomplete(self):
        client = SessionClient([CODE,TimeoutError('synthetic definition deadline'),CODE,dict(DEFINE,qualification_ids=['e1'])])
        session,client = self.plan(2,client)
        result = execute_pending(session,client,self.root/'definitions')
        self.assertEqual([c['definition_status'] for c in result['codes']],['failed','failed'])
        self.assertEqual(result['status'],'passage_queue_processed_definitions_incomplete')
        self.assertEqual(result['coverage']['work_status_counts'],{'completed':2})
        self.assertEqual(len(result['unfinished_definition_code_ids']),2)
        self.assertEqual([c['stage'] for c in result['calls']],['codes','definition','codes','definition'])
        self.assertIn('synthetic definition deadline',result['calls'][1]['error'])
        self.assertIn('response',result['calls'][3])
        self.assertIsNone(client.expected_tokens)
        self.assertIsNone(client.period_deadline)
        self.assertTrue((self.root/'definitions'/'call-000004.json').exists())

    def test_completed_definition_and_resume_requires_same_identity_limits(self):
        session,client = self.plan(1,SessionClient([CODE,DEFINE]))
        result = execute_pending(session,client,self.root/'complete')
        self.assertEqual(result['codes'][0]['definition_status'],'define')
        self.assertEqual(result['unfinished_definition_code_ids'],[])
        client.config.model_sha256 = 'e'*64
        with self.assertRaisesRegex(ValueError,'same model'):
            execute_pending(result,client,self.root/'wrong')
        self.assertFalse((self.root/'wrong').exists())

    def test_existing_output_and_source_file_preserved(self):
        session,client = self.plan(1,SessionClient([ABSTAIN]))
        output = self.root/'existing'; output.mkdir()
        original = output/'original.txt'; original.write_bytes(b'preserve')
        with self.assertRaises(FileExistsError):
            execute_pending(session,client,output)
        self.assertEqual(original.read_bytes(),b'preserve')
        source = self.root/'source.txt'; source.write_bytes(b'exact source bytes')
        with self.assertRaises(FileExistsError):
            execute_pending(session,client,source)
        self.assertEqual(source.read_bytes(),b'exact source bytes')
        self.assertEqual(client.generated,[])

    def test_tampered_source_and_cross_unit_exact_span_rejected(self):
        session,client = self.plan(2,SessionClient([CODE,ABSTAIN]))
        result = execute_pending(session,client,self.root/'sourceproof',define_scope=False)
        changed = deepcopy(result)
        changed['codes'][0]['evidence'][0]['text'] = 'invented source'
        with self.assertRaises(ValueError):
            validate_session(changed)
        changed = deepcopy(result)
        other_request = changed['requests'][changed['plan']['batches'][1]['id']]
        changed['codes'][0]['evidence'] = [deepcopy(other_request['evidence']['e1'])]
        with self.assertRaises(ValueError):
            validate_session(changed)

    def test_planning_estimate_has_no_fallback_or_generation(self):
        client = SessionClient()
        client.measure_messages = lambda *args, **kwargs: {'rendered_prompt':'not tokenized','token_ids':42,'template_sha256':'a'*64,'tokenizer_sha256':'b'*64}
        with self.assertRaisesRegex(ValueError,'actual prompt token IDs'):
            self.plan(1,client)
        self.assertEqual(client.generated,[])

    def test_definition_capacity_failure_does_not_generate_or_hide_code(self):
        client = SessionClient([CODE])
        session,client = self.plan(1,client)
        original_measure = client.measure_messages
        def oversized(messages, **kwargs):
            native = original_measure(messages,**kwargs)
            native['token_ids'] = [1]*4096
            return native
        client.measure_messages = oversized
        result = execute_pending(session,client,self.root/'oversized-definition')
        self.assertEqual(len(client.generated),1)
        self.assertEqual(len(result['codes']),1)
        self.assertEqual(result['codes'][0]['definition_status'],'failed')
        self.assertIn('generation/EOS reserve',result['calls'][1]['error'])
        self.assertEqual(result['coverage']['work_status_counts'],{'completed':1})

    def test_closed_native_process_after_definition_leaves_next_passage_pending(self):
        client = SessionClient()
        client.process = object()  # Lifecycle sentinel only, never an OS process.
        client._injected_transport = False
        def failed_definition():
            client.process = None
            raise TimeoutError('synthetic native definition timeout closed its helper')
        client.responses = [CODE,failed_definition]
        session,client = self.plan(2,client)
        result = execute_pending(session,client,self.root/'native-closed-definition')
        self.assertEqual(len(client.generated),2)
        self.assertEqual([c['stage'] for c in result['calls']],['codes','definition'])
        self.assertEqual(result['coverage']['work_status_counts'],{'completed':1,'pending':1})
        self.assertEqual(result['status'],'runtime_failed_with_pending')
        self.assertEqual(result['codes'][0]['definition_status'],'failed')
        self.assertEqual(len(result['unfinished_definition_code_ids']),1)
        self.assertEqual(result['plan']['work_items'][1]['status'],'pending')
        self.assertEqual(result['plan']['work_items'][1]['result_refs'],[])
        self.assertIn('synthetic native definition timeout',result['calls'][1]['error'])


if __name__ == '__main__':
    unittest.main()

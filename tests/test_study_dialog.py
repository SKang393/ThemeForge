"""Withdrawn Tk import/review/work lifecycle with synthetic native clients."""
from copy import deepcopy
from dataclasses import replace
import json
from html import unescape
from pathlib import Path
import tempfile
import threading
import time
import tkinter as tk
import unittest
from unittest.mock import patch

from themeforge.analysis_types import TranscriptDocument
from themeforge.study_dialog import StudyDialog
from themeforge.study_session import build_native_plan, execute_pending
from themeforge.study_batches import prepare_study
from tests.test_study_session import SessionClient, CODE, ABSTAIN


class StudyDialogTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk(); self.root.withdraw()
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'tmp')
        self.base = Path(self.temp.name)
        self.dialogs = []
        self.client_attempts = []
        self.process_guard = patch('subprocess.Popen',side_effect=AssertionError('UI tests must never start a native process'))
        self.process_guard.start()

    def tearDown(self):
        for dialog in self.dialogs:
            if dialog.winfo_exists():
                dialog.close()
        self.pump(lambda: all(d._worker is None for d in self.dialogs))
        self.root.destroy()
        self.process_guard.stop()
        self.temp.cleanup()

    def pump(self,condition,seconds=4):
        end = time.monotonic()+seconds
        while not condition() and time.monotonic()<end:
            self.root.update(); time.sleep(.005)
        self.root.update()
        self.assertTrue(condition(),'Study worker did not settle before fixture deadline')

    def no_client(self,*args,**kwargs):
        self.client_attempts.append(True)
        raise AssertionError('Review/import must not create a client')

    def dialog(self,documents=(),**kwargs):
        kwargs.setdefault('client_factory',self.no_client)
        dialog = StudyDialog(self.root,documents,**kwargs)
        dialog.withdraw(); self.dialogs.append(dialog); self.root.update()
        return dialog

    def documents(self,count=24):
        return [TranscriptDocument(f'synthetic-{i:02}',f'Interviewer 1: What do you keep?\nParticipant 1: We keep a backup copy on a separate drive for project {i}.') for i in range(count)]

    def completed(self):
        client = SessionClient([CODE,ABSTAIN])
        study = prepare_study(self.documents(2))
        plan = build_native_plan(study,client)
        return execute_pending(plan,client,self.base/'generated',define_scope=False)

    def test_import_more_than_twenty_exact_units_and_export_reopen(self):
        docs = self.documents()
        before = deepcopy(docs)
        dialog = self.dialog(docs)
        self.assertEqual(len(dialog.study['units']),48)
        self.assertEqual(len(dialog.tree.get_children()),48)
        self.assertEqual(len(dialog.study['sources']),24)
        for unit in dialog.study['units']:
            source = next(s for s in dialog.study['sources'] if s['source_name']==unit['source_name'])
            self.assertEqual(source['text'][unit['source_start']:unit['source_end']],unit['text'])
            dialog.tree.selection_set(unit['id']); dialog.show_selected()
            shown = json.loads(dialog.detail.get('1.0','end'))
            self.assertEqual(shown['original_source_turn'],unit)
        path = self.base/'study.json'
        self.assertTrue(dialog.export('json',path),dialog.status.get())
        self.assertEqual(json.loads(path.read_text()),dialog.study)
        other = self.dialog()
        self.assertTrue(other.load(path),other.status.get())
        self.assertEqual(other.study,dialog.study)
        self.assertEqual(docs,before)
        self.assertFalse(self.client_attempts)
        self.assertIsNone(other.config)

    def test_saved_session_display_exact_code_evidence_and_export(self):
        session = self.completed()
        before = deepcopy(session)
        original = self.base/'session.json'; original.write_text(json.dumps(session),encoding='utf-8')
        raw = original.read_bytes()
        dialog = self.dialog()
        self.assertTrue(dialog.load(original),dialog.status.get())
        self.assertEqual(dialog.session,session)
        code = session['codes'][0]
        dialog.tree.selection_set(code['parent_unit_id']); dialog.show_selected()
        shown = json.loads(dialog.detail.get('1.0','end'))
        self.assertEqual(shown['unreviewed_model_codes'],[code])
        self.assertEqual(shown['unreviewed_model_codes'][0]['evidence'],code['evidence'])
        for kind in ('json','html'):
            self.assertTrue(dialog.export(kind,self.base/('export.'+kind)),dialog.status.get())
        self.assertIn('unreviewed model codes',dialog.summary.get())
        self.assertIn('"human_review": false',unescape((self.base/'export.html').read_text()))
        self.assertTrue(dialog.load(self.base/'export.json'))
        self.assertEqual(dialog.session,before)
        self.assertEqual(original.read_bytes(),raw)
        self.assertFalse(self.client_attempts)

    def test_rejected_tampered_session_and_existing_export_preserve_valid_state(self):
        session = self.completed()
        original = self.base/'session.json'; original.write_text(json.dumps(session))
        dialog = self.dialog()
        self.assertTrue(dialog.load(original))
        invalid = deepcopy(session); invalid['codes'][0]['evidence'][0]['text'] = 'invented'
        bad = self.base/'bad.json'; bad.write_text(json.dumps(invalid))
        self.assertFalse(dialog.load(bad))
        self.assertEqual(dialog.session,session)
        raw = original.read_bytes()
        self.assertFalse(dialog.export('json',original))
        self.assertEqual(original.read_bytes(),raw)

    def test_optional_model_manager_open_does_not_select_or_start(self):
        dialog = self.dialog(self.documents(1))
        manager = dialog.open_models(); manager.withdraw()
        self.root.update()
        self.assertFalse(manager.network_opt_in.get())
        self.assertIsNone(manager._worker)
        self.assertIsNone(dialog.config)
        self.assertFalse(self.client_attempts)
        manager.close(); self.root.update()

    def test_run_pending_worker_cancellation_closes_owned_fake_client(self):
        client = SessionClient()
        client.config.limits = replace(client.config.limits,memory_limit_bytes=1024**3)
        client.config.model_path = self.base/'synthetic.gguf'
        client.config.executable_path = self.base/'llama-server.exe'
        study = prepare_study(self.documents(2))
        session = build_native_plan(study,client)
        entered, closed = threading.Event(),threading.Event()
        client.start = lambda cancel: None
        client.close = closed.set
        def generate(messages,schema,cancel):
            entered.set()
            cancel.wait(3)
            raise RuntimeError('synthetic cancellation retained')
        client.generate = generate
        factory_calls = []
        def factory(config,**kwargs):
            factory_calls.append(kwargs); return client
        dialog = self.dialog(config=client.config,client_factory=factory)
        path = self.base/'pending.json'; path.write_text(json.dumps(session))
        self.assertTrue(dialog.load(path))
        dialog.definitions.set(False)
        self.assertTrue(dialog.start_work(True,self.base))
        worker = dialog._worker
        self.assertFalse(worker.daemon)
        self.pump(entered.is_set)
        dialog.close()
        self.pump(lambda: dialog._worker is None)
        self.assertTrue(closed.is_set())
        self.assertFalse(worker.is_alive())
        self.assertFalse(dialog.winfo_exists())
        self.assertEqual(len(factory_calls),1)
        outputs = list(self.base.glob('study-*/coding/session.json'))
        self.assertEqual(len(outputs),1)
        persisted = json.loads(outputs[0].read_text())
        self.assertEqual(persisted['coverage']['work_status_counts'],{'failed':1,'pending':1})
        self.assertIn('synthetic cancellation retained',persisted['calls'][0]['error'])

    def test_smoke_helper_actual_app_open_export_reopen_with_no_startup(self):
        from themeforge.study_qa import study_smoke
        session = self.completed()
        study_path,session_path = self.base/'source-study.json',self.base/'source-session.json'
        study_path.write_text(json.dumps(session['plan']['study']),encoding='utf-8')
        session_path.write_text(json.dumps(session),encoding='utf-8')
        output = self.base/'smoke'
        self.assertEqual(study_smoke(output,study=study_path,session=session_path),0)
        report = json.loads((output/'SMOKE.json').read_text())
        self.assertEqual(report['status'],'passed')
        self.assertEqual(report['new_model_calls'],0)
        self.assertEqual(len(report['checks']),2)
        self.assertTrue(all(c['json_UI_reopen_exact'] for c in report['checks']))
        self.assertFalse(report['owned_worker_remaining'])
        self.assertEqual(report['blocked_action_attempts'],0)

    def test_neighbor_graph_reopens_exports_visible_pairs_and_context_without_execution(self):
        from themeforge.study_neighbors import build_study_neighbors
        documents=[TranscriptDocument('fictional-'+str(i),'Interviewer: Did you share?\nP1: '+text) for i,text in enumerate((
            'I might share only if asked.','I would not share without agreement.','I shared after receiving agreement.'))]
        study=prepare_study(documents)
        vectors={u['id']:[1.,0.] for u in study['units'] if u['eligible']}
        graph=build_study_neighbors(study,vectors,batch_ids={u['id']:'fictional-batch' for u in study['units']},
            embedding_provenance=dict(backend='synthetic_test_no_model'))
        source=self.base/'neighbors.json';source.write_text(json.dumps(graph),encoding='utf-8')
        dialog=self.dialog();self.assertTrue(dialog.load(source),dialog.status.get())
        uid=graph['edges'][0]['member_ids'][0];dialog.tree.selection_set(uid);dialog.show_selected()
        compact=json.loads(dialog.detail.get('1.0','end'))
        self.assertTrue(compact['candidate_source_pairs'])
        self.assertNotIn('text',compact['candidate_source_pairs'][0]['members'][0])
        self.assertTrue(dialog.show_next_pair())
        pair=json.loads(dialog.detail.get('1.0','end'))
        self.assertEqual(len(pair['exact_members']),2)
        self.assertTrue(all(member['original_source_unit']['text'] for member in pair['exact_members']))
        target=self.base/'neighbor-export.json';html=self.base/'neighbor-export.html'
        self.assertTrue(dialog.export('json',target),dialog.status.get())
        self.assertTrue(dialog.export('html',html),dialog.status.get())
        self.assertEqual(json.loads(target.read_text(encoding='utf-8')),graph)
        self.assertIn('Full original sources',html.read_text(encoding='utf-8'))
        other=self.dialog();self.assertTrue(other.load(target),other.status.get())
        self.assertEqual(other.neighbors,graph)
        self.assertTrue(dialog.show_full_source())
        full=json.loads(dialog.detail.get('1.0','end'))
        self.assertIn(full['original_source'],study['sources'])
        with patch('themeforge.study_dialog.filedialog.askopenfilename',side_effect=['unused-profile.json','']):
            self.assertFalse(dialog.start_neighbors())
        self.assertFalse(self.client_attempts)

    def test_parent_destruction_cancels_and_closes_owned_fake_worker(self):
        parent = tk.Toplevel(self.root); parent.withdraw()
        client = SessionClient()
        client.config.limits = replace(client.config.limits,memory_limit_bytes=1024**3)
        client.config.model_path = self.base/'synthetic.gguf'
        client.config.executable_path = self.base/'llama-server.exe'
        study = prepare_study(self.documents(2))
        session = build_native_plan(study,client)
        entered,closed = threading.Event(),threading.Event()
        client.start = lambda cancel: None
        client.close = closed.set
        def generate(messages,schema,cancel):
            entered.set()
            cancel.wait(3)
            if not cancel.is_set():
                raise AssertionError('Parent destruction failed to cancel its study work')
            raise RuntimeError('synthetic parent-close cancellation')
        client.generate = generate
        dialog = StudyDialog(parent,config=client.config,client_factory=lambda *args,**kwargs:client)
        dialog.withdraw()
        path = self.base/'parent-close-pending.json'; path.write_text(json.dumps(session))
        before = path.read_bytes()
        self.assertTrue(dialog.load(path))
        dialog.definitions.set(False)
        self.assertTrue(dialog.start_work(True,self.base))
        worker = dialog._worker
        try:
            self.pump(entered.is_set)
            parent.destroy()
            self.assertTrue(dialog._cancel.is_set())
            self.pump(lambda: not worker.is_alive())
            self.assertTrue(closed.is_set())
            self.assertFalse(dialog.winfo_exists())
            self.assertIsNone(dialog._after)
            self.assertEqual(path.read_bytes(),before)
            self.assertFalse(worker.daemon)
        finally:
            dialog._cancel.set()
            worker.join(timeout=4)
            if parent.winfo_exists():
                parent.destroy()

    def test_main_app_destroy_waits_for_cancelled_worker_cleanup_before_Tk_exit(self):
        from themeforge.app import ThemeForgeApp
        class HiddenApp(ThemeForgeApp):
            def _build_layout(self):
                self.withdraw()
                super()._build_layout()
        app = HiddenApp(); app.update()
        dialog = app.open_study_coding(); dialog.withdraw()
        entered,cancel_seen,release,closed = (threading.Event() for _ in range(4))
        def work():
            entered.set()
            try:
                dialog._cancel.wait(3)
                if dialog._cancel.is_set():
                    cancel_seen.set()
                release.wait(3)  # Simulate client finally cleanup still in progress.
            finally:
                closed.set()
        def exists():
            try:
                return bool(app.winfo_exists())
            except tk.TclError:
                return False
        dialog._begin(work)
        worker = dialog._worker
        try:
            self.pump(entered.is_set)
            self.assertFalse(worker.daemon)
            app.destroy()
            self.pump(cancel_seen.is_set)
            # Exercise multiple 50ms shutdown polls while cleanup is held.
            end = time.monotonic()+.13
            while time.monotonic()<end:
                app.update(); time.sleep(.005)
            self.assertTrue(exists())
            self.assertEqual(app.state(),'withdrawn')
            self.assertTrue(dialog.winfo_exists())
            self.assertTrue(worker.is_alive())
            self.assertFalse(closed.is_set())
            release.set()
            self.pump(lambda: not exists())
            self.assertTrue(closed.is_set())
            self.assertFalse(worker.is_alive())
            self.assertTrue(dialog._cancel.is_set())
        finally:
            release.set(); dialog._cancel.set()
            worker.join(timeout=4)
            if exists():
                app.destroy()
                self.pump(lambda:not exists())


if __name__ == '__main__':
    unittest.main()

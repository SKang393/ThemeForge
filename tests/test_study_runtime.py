"""Native accounting fixtures only: no socket, executable, weights or inference."""
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from themeforge.local_inference import LocalInferenceConfig, InferenceLimits, LocalInferenceError
from themeforge.study_runtime import StudyLlamaClient, gguf_tokenizer_identity
from tests.test_local_inference import FixtureTransport, MESSAGES, SCHEMA


class StudyRuntimeTests(unittest.TestCase):
    def test_last_moment_headroom_blocks_factory_and_reused_child_is_not_killed(self):
        factory = __import__('unittest.mock', fromlist=['Mock']).Mock()
        config = replace(self.config, limits=replace(self.config.limits, memory_limit_bytes=3*1024**3))
        client = StudyLlamaClient(config, process_factory=factory)
        with patch('themeforge.study_runtime.available_ram_bytes', return_value=4*1024**3):
            with self.assertRaisesRegex(Exception, 'headroom'):
                client._process_factory(['must-not-start'])
        factory.assert_not_called()
        client._owned_descendants = {123:1.0}
        reused = __import__('unittest.mock', fromlist=['Mock']).Mock()
        reused.create_time.return_value = 2.0
        with patch('psutil.Process', return_value=reused):
            client.close()
        reused.kill.assert_not_called()
        reused.wait.assert_not_called()

    def setUp(self):
        self.process_guard = patch('themeforge.local_inference.subprocess.Popen', side_effect=AssertionError('Synthetic tests must never start any process'))
        self.process_guard.start()
        self.addCleanup(self.process_guard.stop)
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'tmp')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        exe, model = self.root/'llama-server.exe', self.root/'fixture.gguf'
        exe.write_bytes(b'MZ-not-executable'); model.write_bytes(b'GGUF-not-model')
        self.config = LocalInferenceConfig(exe, hashlib.sha256(exe.read_bytes()).hexdigest(), model, hashlib.sha256(model.read_bytes()).hexdigest(), limits=InferenceLimits(memory_limit_bytes=None))

    def client(self, tokens=None, audit=None):
        transport = FixtureTransport()
        if tokens is not None:
            transport.responses['/tokenize']['tokens'] = tokens
        client = StudyLlamaClient(self.config, transport=transport, audit_directory=audit)
        client.identity = {'metadata': {'tokenizer.ggml.add_bos_token': True, 'tokenizer.ggml.bos_token_id': 1}, 'template_sha256': 'a'*64, 'tokenizer_sha256': 'b'*64}
        self.addCleanup(client.close)
        return client, transport

    def test_native_template_and_ids_are_exact_and_not_estimated(self):
        client, transport = self.client()
        native = client.measure_messages(MESSAGES)
        self.assertEqual(native['rendered_prompt'], transport.responses['/apply-template']['prompt'])
        self.assertEqual(native['token_ids'], [1, 2, 3])
        self.assertEqual(native['template_sha256'], 'a'*64)
        self.assertEqual(native['tokenizer_sha256'], 'b'*64)
        self.assertTrue(native['add_special'])
        self.assertEqual([c[1] for c in transport.calls], ['/apply-template','/tokenize'])
        self.assertFalse(any(c[1] == '/completion' for c in transport.calls))

    def test_changed_frozen_tokens_stop_before_completion_and_preserve_audit(self):
        audit = self.root/'audit'
        client, transport = self.client(audit=audit)
        audit.mkdir()  # No process startup: injected transport exercises requests.
        planned = client.measure_messages(MESSAGES)
        client.expected_tokens = planned['token_ids']
        transport.responses['/tokenize']['tokens'] = [1, 9, 3]
        with self.assertRaisesRegex(LocalInferenceError, 'changed'):
            client.generate(MESSAGES, SCHEMA)
        self.assertFalse(any(c[1] == '/completion' for c in transport.calls))
        records = [json.loads(p.read_text()) for p in sorted(audit.glob('*.json'))]
        self.assertEqual(records[-1]['response']['tokens'], [1,9,3])
        self.assertEqual(records[-1]['origin'], 'synthetic_test_transport')
        self.assertTrue(transport.aborted.is_set())

    def test_BOS_guard_missing_or_duplicate_rejected_without_completion(self):
        for tokens in ([2,3], [1,1,3]):
            client, transport = self.client(tokens)
            with self.assertRaisesRegex(LocalInferenceError, 'BOS'):
                client.measure_messages(MESSAGES)
            self.assertFalse(any(c[1] == '/completion' for c in transport.calls))
            self.assertTrue(transport.aborted.is_set())

    def test_native_missing_or_bad_ids_never_fall_back_to_estimate(self):
        for response in ({}, {'tokens': 123}, {'tokens': []}, {'tokens': [True,2]}):
            client, transport = self.client()
            transport.responses['/tokenize'] = response
            with self.assertRaisesRegex(LocalInferenceError, 'token'):
                client.measure_messages(MESSAGES)
            self.assertFalse(any(c[1] == '/completion' for c in transport.calls))

    def test_audit_directory_must_be_fresh(self):
        audit = self.root/'existing'; audit.mkdir()
        (audit/'preserve.txt').write_text('original')
        client, transport = self.client(audit=audit)
        with self.assertRaises(FileExistsError):
            client.start()
        self.assertEqual((audit/'preserve.txt').read_text(), 'original')

    def test_static_GGUF_reader_hashes_tokenizer_and_rejects_missing_template(self):
        def string(value):
            raw = value.encode(); return struct.pack('<Q',len(raw))+raw
        def write(rows):
            raw = b'GGUF'+struct.pack('<IQQ',3,1,len(rows))
            for key,value in rows:
                raw += string(key)+struct.pack('<I',8)+string(value)
            self.config.model_path.write_bytes(raw)
        write([('general.architecture','synthetic'),('tokenizer.chat_template','native stub')])
        first = gguf_tokenizer_identity(self.config.model_path)
        write([('general.architecture','synthetic'),('tokenizer.chat_template','different stub')])
        second = gguf_tokenizer_identity(self.config.model_path)
        self.assertNotEqual(first['template_sha256'],second['template_sha256'])
        self.assertNotEqual(first['tokenizer_sha256'],second['tokenizer_sha256'])
        write([('general.architecture','synthetic')])
        with self.assertRaisesRegex(ValueError, 'No embedded'):
            gguf_tokenizer_identity(self.config.model_path)


if __name__ == '__main__':
    unittest.main()

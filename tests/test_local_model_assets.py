import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from themeforge import local_model_assets as assets


class Response(io.BytesIO):
    def __init__(self, body, *, status=200, url='https://cas-bridge.xethub.hf.co/test', length=None):
        super().__init__(body)
        self.status, self.url = status, url
        self.headers = {} if length is None else {'Content-Length': str(length)}

    def getcode(self):
        return self.status

    def geturl(self):
        return self.url


class Opener:
    def __init__(self, response):
        self.response, self.calls = response, []

    def open(self, request, timeout):
        self.calls.append((request, timeout))
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


class ModelAssetsTests(unittest.TestCase):
    def test_documented_hub_cdn_redirects_remain_exact_hosts(self):
        for host in ('us.aws.cdn.hf.co', 'us.gcp.cdn.hf.co'):
            self.assertEqual(assets._safe_url('https://' + host + '/model'), 'https://' + host + '/model')
        for url in ('http://us.aws.cdn.hf.co/model', 'https://us.aws.cdn.hf.co.attacker.test/model',
                    'https://user@us.aws.cdn.hf.co/model'):
            with self.assertRaises(assets.ModelAssetError):
                assets._safe_url(url)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1] / 'tmp')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.body = b'GGUF-small-test'
        self.asset = assets.ModelAsset('test', 'ggml-org/test', 'a'*40, 'test.gguf', len(self.body), hashlib.sha256(self.body).hexdigest(), 'Apache-2.0', 'https://huggingface.co/license', 'https://huggingface.co/upstream')
        self.catalog = patch.object(assets, 'CATALOG', {'test': self.asset})
        self.catalog.start()
        self.addCleanup(self.catalog.stop)
        self.target = self.root / 'model.gguf'

    def download(self, body=None, **kwargs):
        opener = Opener(Response(self.body if body is None else body))
        result = assets.ensure_model('test', self.target, opt_in=True, opener=opener, **kwargs)
        return result, opener

    def test_opt_in_required_without_network_or_partial(self):
        opener = Opener(Response(self.body))
        with self.assertRaisesRegex(assets.ModelAssetError, 'opt_in'):
            assets.ensure_model('test', self.target, opener=opener)
        self.assertFalse(opener.calls)
        self.assertFalse(self.target.with_suffix('.gguf.partial').exists())

    def test_exact_download_progress_and_no_auth(self):
        progress = []
        result, opener = self.download(progress=lambda *x: progress.append(x))
        self.assertEqual(result.read_bytes(), self.body)
        self.assertEqual(progress, [(0, len(self.body)), (len(self.body), len(self.body))])
        self.assertEqual(opener.calls[0][0].full_url, self.asset.url)
        self.assertNotIn('Authorization', opener.calls[0][0].headers)
        self.assertEqual(assets.verify_model('test', result), result)
        partial = self.target.with_suffix('.gguf.partial')
        if os.name == 'nt':
            self.assertFalse(partial.exists())
        else:
            self.assertEqual(partial.read_bytes(), self.body)

    def test_offline_reuse_without_opt_in(self):
        self.target.write_bytes(self.body)
        opener = Opener(AssertionError('network forbidden'))
        self.assertEqual(assets.ensure_model('test', self.target, opener=opener), self.target)
        self.assertFalse(opener.calls)

    def test_wrong_existing_file_never_overwritten(self):
        self.target.write_bytes(b'bad')
        with self.assertRaises(assets.ModelAssetError):
            self.download()
        self.assertEqual(self.target.read_bytes(), b'bad')

    def test_hash_short_long_and_header_failures_keep_partial(self):
        for name, body, length in [('hash', b'X'*len(self.body), None), ('short', b'GGUF', None), ('long', self.body+b'X', None), ('header', self.body, 1)]:
            with self.subTest(name=name):
                target = self.root / (name+'.gguf')
                opener = Opener(Response(body, length=length))
                with self.assertRaises(assets.ModelAssetError):
                    assets.ensure_model('test', target, opt_in=True, opener=opener)
                self.assertFalse(target.exists())
                self.assertTrue(target.with_suffix('.gguf.partial').exists())

    def test_existing_partial_preserved_without_network(self):
        partial = self.target.with_suffix('.gguf.partial')
        partial.write_bytes(b'previous')
        opener = Opener(AssertionError('no request'))
        with self.assertRaisesRegex(assets.ModelAssetError, 'Partial'):
            assets.ensure_model('test', self.target, opt_in=True, opener=opener)
        self.assertEqual(partial.read_bytes(), b'previous')
        self.assertFalse(opener.calls)

    def test_cancellation_mid_transfer_preserves_partial(self):
        event = threading.Event()
        def progress(done, total):
            if done:
                event.set()
        with self.assertRaises(assets.AssetCancelled):
            self.download(cancel_event=event, progress=progress)
        self.assertFalse(self.target.exists())
        self.assertEqual(self.target.with_suffix('.gguf.partial').read_bytes(), self.body)

    def test_deadline_after_read_preserves_failure(self):
        with patch.object(assets.time, 'monotonic', side_effect=[0, 0, 0, 0, 5]):
            with self.assertRaisesRegex(assets.ModelAssetError, 'deadline'):
                self.download(timeout_seconds=1)
        self.assertFalse(self.target.exists())

    def test_access_denied_no_bypass(self):
        for status in (401, 403):
            with self.subTest(status=status):
                target = self.root / str(status)
                opener = Opener(HTTPError('https://hidden-signed-url', status, 'denied', None, None))
                with self.assertRaises(assets.AssetAccessGate) as raised:
                    assets.ensure_model('test', target, opt_in=True, opener=opener)
                self.assertNotIn('signed', str(raised.exception))
                self.assertEqual(len(opener.calls), 1)

    def test_terms_gate_even_with_download_opt_in(self):
        from dataclasses import replace
        with patch.object(assets, 'CATALOG', {'test': replace(self.asset, requires_terms_acceptance=True)}):
            with self.assertRaises(assets.AssetAccessGate):
                self.download()
            self.download(accept_terms=True)

    def test_bad_redirects_and_unexpected_final_hosts(self):
        for url in ('http://huggingface.co/x', 'https://evil.example/x', 'https://huggingface.co@evil.example/x', 'https://huggingface.co:123/x'):
            with self.subTest(url=url):
                with self.assertRaises(assets.ModelAssetError):
                    assets._AssetRedirect().redirect_request(None, None, 302, '', {}, url)
        opener = Opener(Response(self.body, url='https://evil.example/x'))
        with self.assertRaises(assets.ModelAssetError):
            assets.ensure_model('test', self.target, opt_in=True, opener=opener)
        self.assertFalse(self.target.exists())

    def test_publish_race_does_not_overwrite(self):
        def progress(done, total):
            if done:
                self.target.write_bytes(b'other writer')
        with self.assertRaises(assets.ModelAssetError):
            self.download(progress=progress)
        self.assertEqual(self.target.read_bytes(), b'other writer')

    def test_invalid_inputs(self):
        for timeout in (0, -1, float('nan'), float('inf'), True, 86401):
            with self.assertRaises(assets.ModelAssetError):
                self.download(timeout_seconds=timeout)
        with self.assertRaises(assets.ModelAssetError):
            assets.verify_model('missing', self.target)
        with self.assertRaises(assets.ModelAssetError):
            assets.ensure_model('test', 'relative.gguf')


class RuntimeManifestTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1] / 'tmp')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.runtime = self.root/'runtime'
        self.runtime.mkdir()
        self.exe = self.runtime/'llama-server.exe'
        self.exe.write_bytes(b'MZ-native-test')
        self.dll = self.runtime/'test.dll'
        self.dll.write_bytes(b'DLL')
        self.data = {'schema_version': 1, 'executable': self.exe.name, 'files': [{'path': p.name, 'bytes': p.stat().st_size, 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()} for p in (self.exe, self.dll)]}

    def pin(self):
        path = self.runtime/'TRUSTED_FILES.json'
        path.write_text(json.dumps(self.data), encoding='utf-8')
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def test_verified_runtime_and_changed_dll(self):
        pin = self.pin()
        self.assertEqual(assets.resolve_app_local_runtime(self.root, pin), self.exe)
        self.dll.write_bytes(b'BAD')
        with self.assertRaises(assets.ModelAssetError):
            assets.resolve_app_local_runtime(self.root, pin)

    def test_altered_manifest_pin_rejected(self):
        self.pin()
        with self.assertRaisesRegex(assets.ModelAssetError, 'SHA256'):
            assets.resolve_app_local_runtime(self.root, '0'*64)

    def test_traversal_duplicate_and_unlisted_executable(self):
        for bad in ('../escape.dll', '/absolute.dll', 'C:/evil.dll', 'bad\\file.dll'):
            self.data['files'][1]['path'] = bad
            with self.assertRaises(assets.ModelAssetError):
                assets.resolve_app_local_runtime(self.root, self.pin())
        self.data['files'][1]['path'] = self.exe.name.upper()
        with self.assertRaises(assets.ModelAssetError):
            assets.resolve_app_local_runtime(self.root, self.pin())
        self.data['files'] = self.data['files'][:1]
        self.data['executable'] = 'unlisted.exe'
        with self.assertRaises(assets.ModelAssetError):
            assets.resolve_app_local_runtime(self.root, self.pin())


if __name__ == '__main__':
    unittest.main()

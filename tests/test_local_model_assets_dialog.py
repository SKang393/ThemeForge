"""Withdrawn real Tk tests; only tiny synthetic files and fake downloads."""
from dataclasses import replace
import hashlib
from pathlib import Path
import tempfile
import threading
import time
import tkinter as tk
import unittest
from unittest.mock import patch

from themeforge import local_model_assets as assets
from themeforge import local_model_assets_dialog as ui


class LocalModelAssetsDialogTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1] / 'tmp')
        self.base = Path(self.temp.name)
        self.path = self.base / 'synthetic.gguf'
        self.path.write_bytes(b'GGUF-synthetic')
        self.asset = assets.ModelAsset('test', 'ggml-org/test', 'a'*40, 'test.gguf', self.path.stat().st_size, hashlib.sha256(self.path.read_bytes()).hexdigest(), 'Apache-2.0', 'https://huggingface.co/license', 'https://huggingface.co/upstream')
        self.patches = [patch.object(module, 'CATALOG', {'test': self.asset}) for module in (assets, ui)]
        for p in self.patches:
            p.start()
        self.dialogs = []
        self.selected = []

    def tearDown(self):
        for dialog in self.dialogs:
            if dialog.winfo_exists():
                dialog.close()
        self.pump(lambda: all(d._worker is None for d in self.dialogs))
        self.root.destroy()
        for p in reversed(self.patches):
            p.stop()
        self.temp.cleanup()

    def dialog(self, **kwargs):
        dialog = ui.LocalModelAssetsDialog(self.root, on_selected=lambda *args: self.selected.append(args), **kwargs)
        dialog.withdraw()
        self.dialogs.append(dialog)
        self.root.update()
        return dialog

    def pump(self, condition, seconds=3):
        deadline = time.monotonic() + seconds
        while not condition() and time.monotonic() < deadline:
            self.root.update()
            time.sleep(.005)
        self.root.update()
        self.assertTrue(condition(), 'Tk worker did not settle before test deadline')

    def test_initial_view_and_catalog_change_never_select(self):
        dialog = self.dialog()
        self.assertFalse(dialog.network_opt_in.get())
        self.assertFalse(dialog.terms_accepted.get())
        for text in (self.asset.asset_id, self.asset.revision, str(self.asset.bytes), self.asset.license_url):
            self.assertIn(text, dialog.details.get())
        dialog.network_opt_in.set(True)
        dialog._selection_changed()
        self.assertFalse(dialog.network_opt_in.get())
        self.assertEqual(self.selected, [])
        self.assertIsNone(dialog._worker)

    def test_offline_reuse_real_verifier_runs_on_worker(self):
        thread_ids = []
        def verify(*args):
            thread_ids.append(threading.get_ident())
            return assets.verify_model(*args)
        dialog = self.dialog(verify_fn=verify)
        before = self.path.read_bytes()
        dialog.verify_existing(self.path)
        self.assertFalse(dialog._worker.daemon)
        self.pump(lambda: dialog._worker is None)
        self.assertNotEqual(thread_ids, [threading.get_ident()])
        self.assertEqual(self.path.read_bytes(), before)
        model, runtime = self.selected[0]
        self.assertEqual(model['path'], str(self.path))
        self.assertEqual(model['sha256'], self.asset.sha256)
        self.assertEqual(model['selection_origin'], 'verify')
        self.assertIsNone(runtime)
        self.assertIn('runtime unavailable', dialog.status.get())

    def test_bad_offline_file_no_callback(self):
        self.path.write_bytes(b'bad')
        dialog = self.dialog()
        dialog.verify_existing(self.path)
        self.pump(lambda: dialog._worker is None)
        self.assertFalse(self.selected)
        self.assertIn('mismatch', dialog.status.get())

    def test_download_requires_explicit_checkbox_then_passes_progress(self):
        calls = []
        def download(asset_id, path, **kwargs):
            calls.append((asset_id, path, kwargs))
            kwargs['progress'](4, 8)
            return path
        dialog = self.dialog(ensure_fn=download)
        dialog.download_selected(self.base/'fresh.gguf')
        self.assertEqual(calls, [])
        self.assertIn('opt-in', dialog.status.get())
        dialog.network_opt_in.set(True)
        dialog.download_selected(self.base/'fresh.gguf')
        self.pump(lambda: dialog._worker is None)
        self.assertEqual(len(calls), 1)
        self.assertIs(calls[0][2]['opt_in'], True)
        self.assertEqual(len(self.selected), 1)
        self.assertEqual(dialog.progress['value'], 100)

    def test_future_terms_gate(self):
        gated = replace(self.asset, requires_terms_acceptance=True)
        calls = []
        with patch.object(ui, 'CATALOG', {'test': gated}):
            dialog = self.dialog(ensure_fn=lambda asset_id, path, **kw: calls.append(kw) or path)
            dialog.network_opt_in.set(True)
            dialog.download_selected(self.base/'fresh.gguf')
            self.assertFalse(calls)
            self.assertIn('terms acceptance', dialog.status.get())
            dialog.terms_accepted.set(True)
            dialog.download_selected(self.base/'fresh.gguf')
            self.pump(lambda: dialog._worker is None)
            self.assertTrue(calls[0]['accept_terms'])

    def test_close_waits_owned_worker_and_suppresses_selection(self):
        started = threading.Event()
        stopping = threading.Event()
        release = threading.Event()
        def download(asset_id, path, *, cancel_event, **kwargs):
            started.set()
            cancel_event.wait(2)
            stopping.set()
            release.wait(2)
            raise assets.AssetCancelled('cancelled')
        dialog = self.dialog(ensure_fn=download)
        dialog.network_opt_in.set(True)
        dialog.download_selected(self.base/'fresh.gguf')
        self.pump(started.is_set)
        worker = dialog._worker
        dialog.close()
        self.pump(stopping.is_set)
        self.assertTrue(dialog.winfo_exists())
        self.assertTrue(worker.is_alive())
        release.set()
        self.pump(lambda: dialog._worker is None)
        self.assertFalse(worker.is_alive())
        self.assertFalse(dialog.winfo_exists())
        self.assertFalse(self.selected)

    def test_cancel_offline_hash_waits_then_no_selection(self):
        started, release = threading.Event(), threading.Event()
        def verify(asset_id, path):
            started.set()
            release.wait(2)
            return assets.verify_model(asset_id, path)
        dialog = self.dialog(verify_fn=verify)
        dialog.verify_existing(self.path)
        self.pump(started.is_set)
        dialog.cancel()
        release.set()
        self.pump(lambda: dialog._worker is None)
        self.assertFalse(self.selected)
        self.assertIn('Cancelled', dialog.status.get())

    def test_runtime_pin_success_and_failure_keep_valid_model(self):
        seen = []
        def resolve(root, pin):
            seen.append((root, pin))
            return self.base/'runtime'/'llama-server.exe'
        dialog = self.dialog(package_root=self.base, runtime_manifest_sha256='a'*64, runtime_fn=resolve)
        dialog.verify_existing(self.path)
        self.pump(lambda: dialog._worker is None)
        self.assertEqual(seen, [(self.base, 'a'*64)])
        self.assertEqual(self.selected[-1][1]['manifest_sha256'], 'a'*64)
        self.assertEqual(self.selected[-1][1]['path'], str(self.base/'runtime'/'llama-server.exe'))
        def fail(*args):
            raise assets.ModelAssetError('bad manifest')
        other = self.dialog(package_root=self.base, runtime_manifest_sha256='b'*64, runtime_fn=fail)
        other.verify_existing(self.path)
        self.pump(lambda: other._worker is None)
        self.assertIsNone(self.selected[-1][1])
        self.assertIn('No global runtime fallback', other.status.get())

    def test_relative_and_network_path_refused(self):
        calls = []
        dialog = self.dialog(verify_fn=lambda *args: calls.append(args))
        for value in ('relative.gguf', '//server/share/model.gguf'):
            dialog.verify_existing(value)
        self.assertFalse(calls)
        self.assertFalse(self.selected)

    def test_explicit_selection_callback_can_close_dialog(self):
        dialog = self.dialog()
        calls = []
        def selected(model, runtime):
            calls.append(model)
            dialog.close()
        dialog.on_selected = selected
        dialog.verify_existing(self.path)
        self.pump(lambda: dialog._worker is None)
        self.assertEqual(len(calls), 1)
        self.assertFalse(dialog.winfo_exists())


if __name__ == '__main__':
    unittest.main()

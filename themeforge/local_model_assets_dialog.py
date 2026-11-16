"""Optional model-file management, without model execution or browser access."""
from __future__ import annotations

from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import filedialog, ttk

from .local_model_assets import (CATALOG, AssetCancelled, ModelAssetError,
                                 ensure_model, resolve_app_local_runtime, verify_model)


class LocalModelAssetsDialog(tk.Toplevel):
    def __init__(self, parent, *, package_root=None, runtime_manifest_sha256=None,
                 on_selected=None, verify_fn=None, ensure_fn=None, runtime_fn=None):
        super().__init__(parent)
        self.title('Optional local model files')
        self.geometry('880x530')
        self.package_root = package_root
        self.runtime_manifest_sha256 = runtime_manifest_sha256
        self.on_selected = on_selected
        self._verify = verify_fn or verify_model
        self._ensure = ensure_fn or ensure_model
        self._runtime = runtime_fn or resolve_app_local_runtime
        self._worker = None
        self._cancel = threading.Event()
        self._events = queue.Queue(maxsize=64)
        self._closing = False
        self._poll_id = None
        self.model_result = None
        self.runtime_result = None
        self.asset_id = tk.StringVar(self, value=next(iter(CATALOG)))
        self.network_opt_in = tk.BooleanVar(self, value=False)
        self.terms_accepted = tk.BooleanVar(self, value=False)
        self.status = tk.StringVar(self, value='Choose a catalog entry and explicitly verify or download. No model is started.')
        self.details = tk.StringVar(self)
        body = ttk.Frame(self, padding=14)
        body.pack(fill='both', expand=True)
        self.selector = ttk.Combobox(body, textvariable=self.asset_id, values=tuple(CATALOG), state='readonly', width=48)
        self.selector.pack(anchor='w')
        self.selector.bind('<<ComboboxSelected>>', self._selection_changed)
        ttk.Label(body, textvariable=self.details, wraplength=835, justify='left').pack(fill='x', pady=12)
        self.network_checkbox = ttk.Checkbutton(body, text='I explicitly allow downloading this model over the network.', variable=self.network_opt_in)
        self.network_checkbox.pack(anchor='w')
        self.terms_checkbox = ttk.Checkbutton(body, text='I accept the selected model terms (only required when the catalog says so).', variable=self.terms_accepted)
        self.terms_checkbox.pack(anchor='w', pady=6)
        buttons = ttk.Frame(body)
        buttons.pack(fill='x', pady=12)
        self.verify_button = ttk.Button(buttons, text='Verify existing model', command=self.verify_existing)
        self.verify_button.pack(side='left')
        self.download_button = ttk.Button(buttons, text='Download selected model', command=self.download_selected)
        self.download_button.pack(side='left', padx=8)
        self.cancel_button = ttk.Button(buttons, text='Cancel operation', command=self.cancel, state='disabled')
        self.cancel_button.pack(side='left')
        ttk.Button(buttons, text='Close', command=self.close).pack(side='right')
        self.progress = ttk.Progressbar(body, mode='determinate', maximum=100)
        self.progress.pack(fill='x')
        ttk.Label(body, textvariable=self.status, wraplength=835, justify='left').pack(fill='x', pady=12)
        ttk.Label(body, text='Qwen: 3.03 GB. Gemma: 5.34 GB. CPU inference needs additional RAM and different model/memory caps. Download verification does not guarantee fit or quality.', wraplength=835).pack(fill='x')
        self.protocol('WM_DELETE_WINDOW', self.close)
        self._selection_changed()
        self._poll_id = self.after(30, self._poll)

    def _selection_changed(self, _event=None):
        asset = CATALOG.get(self.asset_id.get())
        self.network_opt_in.set(False)
        self.terms_accepted.set(False)
        if asset is None:
            self.details.set('Unknown catalog entry.')
            return
        self.details.set(f'ID: {asset.asset_id}\nFile: {asset.filename}\nBytes: {asset.bytes:,}\nRevision: {asset.revision}\nSHA256: {asset.sha256}\nLicense: {asset.license}\nLicense URL (text only): {asset.license_url}\nRequires terms acceptance: {asset.requires_terms_acceptance}')
        self.terms_checkbox.configure(state='normal' if asset.requires_terms_acceptance else 'disabled')

    @staticmethod
    def _path(value):
        path = Path(value).expanduser()
        if not path.is_absolute() or str(path).startswith(('\\\\', '//')):
            raise ModelAssetError('Choose an absolute local file path, not a network share.')
        resolved = path.resolve()
        if str(resolved).startswith(('\\\\', '//')):
            raise ModelAssetError('Network-share paths are not supported.')
        return resolved

    def verify_existing(self, path=None):
        if self._busy() or self._closing:
            return
        if path is None:
            path = filedialog.askopenfilename(parent=self, title='Verify existing catalog GGUF', filetypes=[('GGUF models', '*.gguf')])
        if path:
            self._start('verify', path)

    def download_selected(self, path=None):
        if self._busy() or self._closing:
            return
        asset = CATALOG.get(self.asset_id.get())
        if asset is None:
            self.status.set('Select a known catalog entry.')
            return
        if not self.network_opt_in.get():
            self.status.set('Download requires the unchecked-by-default network opt-in checkbox.')
            return
        if asset.requires_terms_acceptance and not self.terms_accepted.get():
            self.status.set('This catalog entry requires explicit terms acceptance.')
            return
        if path is None:
            path = filedialog.asksaveasfilename(parent=self, title='Choose a fresh local model destination', initialfile=asset.filename, defaultextension='.gguf', filetypes=[('GGUF models', '*.gguf')], confirmoverwrite=True)
        if path:
            self._start('download', path)

    def _busy(self):
        return self._worker is not None

    def _controls(self, busy):
        self.selector.configure(state='disabled' if busy else 'readonly')
        for widget in (self.verify_button, self.download_button, self.network_checkbox):
            widget.configure(state='disabled' if busy else 'normal')
        asset = CATALOG.get(self.asset_id.get())
        self.terms_checkbox.configure(state='normal' if not busy and asset and asset.requires_terms_acceptance else 'disabled')
        self.cancel_button.configure(state='normal' if busy else 'disabled')

    def _start(self, operation, value):
        try:
            path = self._path(value)
            asset = CATALOG[self.asset_id.get()]
        except (ModelAssetError, KeyError) as exc:
            self.status.set(str(exc))
            return
        accepted = self.terms_accepted.get()
        self._cancel.clear()
        self._controls(True)
        self.progress['value'] = 0
        self.status.set('Verifying existing bytes offline...' if operation == 'verify' else 'Downloading fixed catalog model; failed partial files are retained...')

        def report(done, total):
            try:
                self._events.put_nowait(('progress', (done, total)))
            except queue.Full:
                pass

        def work():
            try:
                if self._cancel.is_set():
                    raise AssetCancelled('Operation cancelled.')
                if operation == 'verify':
                    verified = self._verify(asset.asset_id, path)
                else:
                    verified = self._ensure(asset.asset_id, path, opt_in=True, accept_terms=accepted, cancel_event=self._cancel, progress=report)
                if self._cancel.is_set():
                    raise AssetCancelled('Operation cancelled; no model selection applied.')
                model = {'asset_id': asset.asset_id, 'path': str(verified), 'bytes': asset.bytes, 'sha256': asset.sha256, 'revision': asset.revision, 'license': asset.license, 'selection_origin': operation}
                runtime = None
                warning = 'App-local runtime unavailable; select or install a trusted packaged runtime before inference.'
                if self.package_root is not None and self.runtime_manifest_sha256 is not None:
                    try:
                        executable = self._runtime(self.package_root, self.runtime_manifest_sha256)
                        runtime = {'path': str(executable), 'manifest_sha256': self.runtime_manifest_sha256}
                        warning = 'App-local runtime bytes verified; inference compatibility and memory fit are not tested.'
                    except Exception:
                        warning = 'App-local runtime verification failed; the model file remains verified. No global runtime fallback.'
                if self._cancel.is_set():
                    raise AssetCancelled('Operation cancelled; no model selection applied.')
                self._events.put(('success', (model, runtime, warning)))
            except AssetCancelled:
                self._events.put(('error', 'Cancelled. No model selection applied; any failed partial is retained.'))
            except ModelAssetError as exc:
                self._events.put(('error', str(exc)))
            except Exception:
                self._events.put(('error', 'Local asset operation failed; no model selection applied.'))

        self._worker = threading.Thread(target=work, name='ThemeForge-model-assets', daemon=False)
        self._worker.start()

    def _poll(self):
        self._poll_id = None
        terminal = None
        while True:
            try:
                kind, value = self._events.get_nowait()
            except queue.Empty:
                break
            if kind == 'progress':
                done, total = value
                self.progress['value'] = 100 * done / total if total else 0
                if not self._closing:
                    self.status.set(f'Transferred {done:,} / {total:,} bytes. No inference is running.')
            else:
                terminal = (kind, value)
        if terminal is not None:
            self._pending_terminal = terminal
        if self._worker is not None and not self._worker.is_alive():
            self._worker.join()
            self._worker = None
            self._controls(False)
            terminal = getattr(self, '_pending_terminal', None)
            self._pending_terminal = None
            if terminal and not self._closing:
                kind, value = terminal
                if kind == 'success' and not self._cancel.is_set():
                    model, runtime, warning = value
                    self.model_result, self.runtime_result = model, runtime
                    self.progress['value'] = 100
                    self.status.set('Model file verified. ' + warning)
                    if self.on_selected is not None:
                        try:
                            self.on_selected(dict(model), dict(runtime) if runtime else None)
                        except Exception:
                            self.status.set('Model verified, but selection callback failed. No inference was started.')
                else:
                    self.status.set(value if kind == 'error' else 'Cancelled. No model selection applied.')
        if not self.winfo_exists():
            return  # The explicit selection callback may close this dialog.
        if self._closing and self._worker is None:
            super().destroy()
            return
        self._poll_id = self.after(30, self._poll)

    def cancel(self):
        if self._worker is not None:
            self._cancel.set()
            self.status.set('Cancelling; waiting for owned asset worker to stop...')

    def close(self):
        self._closing = True
        self.cancel()
        if self._worker is None:
            if self._poll_id is not None:
                self.after_cancel(self._poll_id)
                self._poll_id = None
            super().destroy()

    def destroy(self):
        # Explicit destruction follows the same owned-worker cleanup as Close.
        self.close()

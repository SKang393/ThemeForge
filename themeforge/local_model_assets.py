"""Explicit optional asset acquisition. Importing this module performs no I/O."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import time
from types import MappingProxyType
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class ModelAssetError(RuntimeError):
    pass


class AssetAccessGate(ModelAssetError):
    """Access denied. No login, token, cookie, or alternate URL is attempted."""


class AssetCancelled(ModelAssetError):
    pass


@dataclass(frozen=True)
class ModelAsset:
    asset_id: str
    repository: str
    revision: str
    filename: str
    bytes: int
    sha256: str
    license: str
    license_url: str
    upstream_url: str
    requires_terms_acceptance: bool = False

    @property
    def url(self):
        return f"https://huggingface.co/{self.repository}/resolve/{self.revision}/{self.filename}"


CATALOG = MappingProxyType({
    "qwen3.5-4b-q4_k_m": ModelAsset(
        "qwen3.5-4b-q4_k_m", "ggml-org/Qwen3.5-4B-GGUF",
        "8d43d974efc48399cb58957ad49c8b26bf5a109c", "Qwen3.5-4B-Q4_K_M.gguf",
        3032091584, "217002fa8008ef4fc6d548353a3aebef055a96e6ff8e0e2c0953ef8254a89c14",
        "Apache-2.0", "https://huggingface.co/ggml-org/Qwen3.5-4B-GGUF/blob/8d43d974efc48399cb58957ad49c8b26bf5a109c/README.md",
        "https://huggingface.co/Qwen/Qwen3.5-4B"),
    "gemma4-e4b-it-q4_k_m": ModelAsset(
        "gemma4-e4b-it-q4_k_m", "lmstudio-community/gemma-4-E4B-it-GGUF",
        "99210a71094c6aa8559f764e0cadedd7d89de94d", "gemma-4-E4B-it-Q4_K_M.gguf",
        5335291936, "0ffb122c8b6921f13cbc34186e052524d0b5803b17f4867b7197a561400b3770",
        "Apache-2.0", "https://ai.google.dev/gemma/apache_2",
        "https://huggingface.co/google/gemma-4-E4B-it/tree/4ecc2ba63bcac23ba4a767fc87025b1450f84c9c"),
})

# Signed redirects may contain credentials in query parameters. Never log URLs.
_HOSTS = frozenset({"huggingface.co", "cdn-lfs.huggingface.co", "cdn-lfs.hf.co",
                    "cdn-lfs-us-1.hf.co", "cdn-lfs-eu-1.hf.co", "cas-bridge.xethub.hf.co",
                    # Official Hub CDN hosts, verified against
                    # https://huggingface.co/docs/hub/models-downloading (2026-11-15).
                    "us.aws.cdn.hf.co", "us.gcp.cdn.hf.co"})
_SHA = re.compile(r"[0-9a-f]{64}\Z")


def _safe_url(url):
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or parsed.hostname not in _HOSTS
            or parsed.username or parsed.password or parsed.port not in (None, 443)
            or parsed.fragment):
        raise ModelAssetError("Asset redirect is outside the permitted HTTPS hosts")
    return url


class _AssetRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _safe_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _asset(asset_id):
    try:
        return CATALOG[asset_id]
    except (KeyError, TypeError):
        raise ModelAssetError("Unknown model catalog identifier") from None


def _budget(deadline, cancel_event):
    if cancel_event is not None and cancel_event.is_set():
        raise AssetCancelled("Asset operation cancelled; partial file retained")
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ModelAssetError("Asset operation deadline exceeded; partial file retained")
    return remaining


def _verify(path, size, sha, deadline=None, cancel_event=None):
    if path.is_symlink() or not path.is_file() or path.stat().st_size != size:
        raise ModelAssetError("Asset file type or size mismatch")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while True:
            if deadline is not None:
                _budget(deadline, cancel_event)
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    if digest.hexdigest() != sha:
        raise ModelAssetError("Asset SHA256 mismatch")
    return path


def verify_model(asset_id, path):
    """Offline exact-file verification. Does not load GGUF or contact any host."""
    asset = _asset(asset_id)
    path = Path(path)
    if not path.is_absolute():
        raise ModelAssetError("Model path must be absolute")
    return _verify(path, asset.bytes, asset.sha256)


def ensure_model(asset_id, destination, *, opt_in=False, accept_terms=False,
                 cancel_event=None, timeout_seconds=3600, progress=None, opener=None):
    """Verify an existing exact file offline, or explicitly download a catalog asset.

    Failed .partial files are retained and never resumed or overwritten. A
    successful .partial is published without replacing any existing path.
    Windows uses no-replace rename; other systems retain a hard-linked partial.
    ``opener`` is a test transport
    exposing open(Request, timeout=seconds); application callers should omit it.
    Progress receives (received_bytes, catalog_bytes), never remote URL/content.
    """
    asset = _asset(asset_id)
    target = Path(destination)
    if not target.is_absolute():
        raise ModelAssetError("Destination must be absolute")
    if not isinstance(timeout_seconds, (int, float)) or isinstance(timeout_seconds, bool) or not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 86400:
        raise ModelAssetError("Timeout must be finite and within 86400 seconds")
    deadline = time.monotonic() + timeout_seconds
    _budget(deadline, cancel_event)
    if target.exists() or target.is_symlink():
        return _verify(target, asset.bytes, asset.sha256, deadline, cancel_event)
    if opt_in is not True:
        raise ModelAssetError("Explicit opt_in=True is required for a model download")
    if asset.requires_terms_acceptance and accept_terms is not True:
        raise AssetAccessGate("This catalog record requires explicit terms acceptance")
    partial = target.with_name(target.name + ".partial")
    if partial.exists() or partial.is_symlink():
        raise ModelAssetError("Partial file already exists; choose a fresh destination")
    target.parent.mkdir(parents=True, exist_ok=True)
    transport = opener if opener is not None else build_opener(_AssetRedirect())
    request = Request(_safe_url(asset.url), headers={"User-Agent": "ThemeForge-local-assets/1", "Accept-Encoding": "identity"})
    try:
        with partial.open("xb") as output:
            with transport.open(request, timeout=min(10.0, _budget(deadline, cancel_event))) as response:
                _safe_url(response.geturl())
                status = response.getcode()
                if status in (401, 403):
                    raise AssetAccessGate("Model host denied access (401/403); no bypass attempted")
                if status != 200:
                    raise ModelAssetError("Unexpected model host response status")
                length = response.headers.get("Content-Length")
                if length is not None and length != str(asset.bytes):
                    raise ModelAssetError("Published model size disagrees with response length")
                digest = hashlib.sha256()
                received = 0
                if progress is not None:
                    progress(0, asset.bytes)
                while True:
                    _budget(deadline, cancel_event)
                    chunk = response.read(min(1024 * 1024, asset.bytes - received + 1))
                    _budget(deadline, cancel_event)
                    if not chunk:
                        break
                    if received + len(chunk) > asset.bytes:
                        raise ModelAssetError("Download exceeds published model size")
                    output.write(chunk)
                    digest.update(chunk)
                    received += len(chunk)
                    if progress is not None:
                        progress(received, asset.bytes)
                if received != asset.bytes or digest.hexdigest() != asset.sha256:
                    raise ModelAssetError("Downloaded model size or SHA256 mismatch")
                output.flush()
                os.fsync(output.fileno())
        _budget(deadline, cancel_event)
        # Atomic no-clobber publication; no existing destination is replaced.
        if os.name == 'nt':
            os.rename(partial, target)  # Windows rename fails if target exists.
        else:
            os.link(partial, target)  # POSIX rename would overwrite a raced file.
        return target
    except HTTPError as exc:
        if exc.code in (401, 403):
            raise AssetAccessGate("Model host denied access (401/403); no bypass attempted") from None
        raise ModelAssetError(f"Model host returned HTTP {exc.code}; partial retained") from None
    except (AssetCancelled, ModelAssetError):
        raise
    except Exception:
        # Transport exceptions can contain signed URLs or machine-specific paths.
        raise ModelAssetError("Asset transfer or publication failed; partial file retained") from None


def resolve_app_local_runtime(package_root, manifestpin):
    """Verify package_root/runtime/TRUSTED_FILES.json and return its native exe.

    Manifest v1: {schema_version:1, executable:relative-path,
    files:[{path:relative-path, bytes:integer, sha256:hex}, ...]}.
    All relative paths are rooted at runtime/, not PATH or a system service.
    The caller must supply the manifest hash from trusted package metadata.
    """
    root = Path(package_root)
    if not root.is_absolute() or not isinstance(manifestpin, str) or not _SHA.fullmatch(manifestpin):
        raise ModelAssetError("Explicit absolute package root and manifest SHA256 required")
    runtime = root / "runtime"
    manifest = runtime / "TRUSTED_FILES.json"
    if runtime.is_symlink() or manifest.is_symlink() or not manifest.is_file() or manifest.stat().st_size > 1024 * 1024:
        raise ModelAssetError("Runtime manifest missing or invalid")
    raw = manifest.read_bytes()
    if hashlib.sha256(raw).hexdigest() != manifestpin:
        raise ModelAssetError("Runtime manifest SHA256 mismatch")
    try:
        data = json.loads(raw)
        rows = data['files']
        executable = data['executable']
        if data['schema_version'] != 1 or not isinstance(rows, list) or not 1 <= len(rows) <= 256:
            raise ValueError()
        seen = set()
        for row in rows:
            name = row['path']
            rel = PurePosixPath(name)
            if not isinstance(name, str) or not name or '\\' in name or ':' in name or rel.is_absolute() or any(x in ('.', '..') for x in name.split('/')) or name.casefold() in seen:
                raise ValueError()
            seen.add(name.casefold())
            file = runtime.joinpath(*rel.parts)
            if any(parent.is_symlink() for parent in [file, *file.parents] if parent != root.parent):
                raise ValueError()
            if not isinstance(row['bytes'], int) or isinstance(row['bytes'], bool) or not 0 <= row['bytes'] <= 1024**3 or not _SHA.fullmatch(row['sha256']):
                raise ValueError()
            _verify(file, row['bytes'], row['sha256'])
        if not isinstance(executable, str) or executable.casefold() not in seen or not executable.lower().endswith('.exe'):
            raise ValueError()
        exe = runtime.joinpath(*PurePosixPath(executable).parts)
        with exe.open('rb') as stream:
            if stream.read(2) != b'MZ':
                raise ValueError()
        return exe
    except ModelAssetError:
        raise
    except (KeyError, TypeError, ValueError, OSError):
        raise ModelAssetError("Runtime manifest or native executable is invalid") from None

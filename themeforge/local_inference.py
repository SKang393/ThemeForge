"""Bounded, explicit local llama-server infrastructure. No import-time launch.

Protocol/flags verified against the official server README on 2026-11-14:
https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md
Real executable compatibility and model quality remain unvalidated. Memory
measurement is a sampled process tripwire, NOT a hard OS RAM allocation limit.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import http.client
import json
import math
import os
from pathlib import Path
import queue
import re
import secrets
import socket
import subprocess
import threading
import time
from typing import Protocol

GIB = 1024 ** 3
DOCUMENTATION_URL = "https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md"
# Conservative literal-token guard for direct callers. Structured callers should
# escape these characters inside JSON string values, preserving decoded source
# text. This blocks delimiter interpretation, not semantic prompt injection.
_CHAT_CONTROL = re.compile(r"<\|[A-Za-z0-9_:-]+\|>|</?s>|\[/?INST\]|<</?SYS>>|\[/?SYSTEM_PROMPT\]|\[/?AVAILABLE_TOOLS\]|\[/?TOOL_RESULTS\]|\[TOOL_CALLS\]")


class LocalInferenceError(RuntimeError):
    """Fail-closed preflight, transport, resource, or protocol failure."""


class InferenceCancelled(LocalInferenceError):
    pass


class InferenceLimitError(LocalInferenceError):
    pass


@dataclass(frozen=True)
class InferenceLimits:
    context_tokens: int = 2048
    output_tokens: int = 512
    threads: int = 2
    max_model_bytes: int = 2 * GIB
    max_request_bytes: int = 1024 * 1024
    max_response_bytes: int = 1024 * 1024
    max_output_bytes: int = 128 * 1024
    wall_seconds: float = 60.0
    startup_seconds: float = 30.0
    memory_limit_bytes: int | None = 3 * GIB

    def __post_init__(self):
        for name, maximum in (("context_tokens", 4096), ("output_tokens", 512), ("threads", 8), ("max_model_bytes", 8 * GIB), ("max_request_bytes", 8 * 1024 * 1024), ("max_response_bytes", 8 * 1024 * 1024), ("max_output_bytes", 1024 * 1024)):
            value = getattr(self, name)
            if type(value) is not int or not 1 <= value <= maximum:
                raise ValueError(f"{name} must be an integer in 1..{maximum}")
        if self.context_tokens <= self.output_tokens + 1:
            raise ValueError("Context must leave room for input and an EOS reserve.")
        for name, maximum in (("wall_seconds", 300), ("startup_seconds", 120)):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 < value <= maximum:
                raise ValueError(f"{name} must be positive, finite, and <= {maximum}")
        if self.memory_limit_bytes is not None and (type(self.memory_limit_bytes) is not int or not 1 <= self.memory_limit_bytes <= 16 * GIB):
            raise ValueError("memory_limit_bytes must be None or a positive integer <= 16 GiB")


@dataclass(frozen=True)
class LocalInferenceConfig:
    executable_path: str | Path
    executable_sha256: str
    model_path: str | Path
    model_sha256: str
    limits: InferenceLimits = field(default_factory=InferenceLimits)
    port: int = 18089

    def __post_init__(self):
        if type(self.port) is not int or not 1024 <= self.port <= 65535:
            raise ValueError("port must be an integer in 1024..65535")
        for name in ("executable_sha256", "model_sha256"):
            if not isinstance(getattr(self, name), str) or not re.fullmatch(r"[0-9a-fA-F]{64}", getattr(self, name)):
                raise ValueError(f"{name} must be an explicit SHA-256 digest")


class JsonTransport(Protocol):
    def request(self, method: str, endpoint: str, body: bytes | None, timeout: float, max_bytes: int) -> bytes: ...
    def abort(self) -> None: ...


def staged_inference_limits(profile=None):
    """Bounded resource profile shared by staged CLI and native UI.

    Old profiles retain their previous limits. Raising model size requires an
    explicit profile; memory monitoring cannot be disabled by a loaded profile.
    These are process tripwires, not measured minimum system requirements.
    """
    values = dict(context_tokens=4096, output_tokens=512, threads=2,
                  wall_seconds=120, startup_seconds=60, memory_limit_bytes=4 * GIB)
    overrides = (profile or {}).get("limits", {})
    if type(overrides) is not dict or set(overrides) - (set(values) | {"max_model_bytes"}):
        raise ValueError("Unknown or invalid staged resource limits.")
    values.update(overrides)
    limits = InferenceLimits(**values)
    if (limits.memory_limit_bytes is None or limits.memory_limit_bytes > 6 * GIB
            or limits.max_model_bytes > 4 * GIB or limits.threads > 2
            or limits.wall_seconds > 180):
        raise ValueError("Staged profiles require <=2 CPU threads, <=4 GiB model, <=6 GiB memory tripwire and <=180s/request.")
    return limits


def _local_file(value: str | Path) -> Path:
    raw = str(value)
    if not raw or raw.startswith(("\\\\", "//")) or re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", raw) or "\x00" in raw:
        raise LocalInferenceError("Only explicit absolute local file paths are permitted; URLs/UNC are rejected.")
    path = Path(raw)
    if not path.is_absolute():
        raise LocalInferenceError("File paths must be absolute; PATH/environment lookup is not permitted.")
    path = path.resolve(strict=True)
    if str(path).startswith(("\\\\", "//")) or not path.is_file():
        raise LocalInferenceError("Resolved path must be a regular local file.")
    if os.name == "nt":
        import ctypes
        if ctypes.windll.kernel32.GetDriveTypeW(str(path.anchor)) == 4:
            raise LocalInferenceError("Mapped network drives are not local model/runtime files.")
        if getattr(path.stat(), "st_file_attributes", 0) & (0x1000 | 0x40000 | 0x400000):
            raise LocalInferenceError("Offline/cloud-placeholder files must be present locally before preflight.")
        if ":" in str(path)[2:]:
            raise LocalInferenceError("Alternate data stream paths are not permitted.")
    return path


def _digest(path, expected, maximum, check):
    check()
    size = path.stat().st_size
    if not 0 < size <= maximum:
        raise InferenceLimitError(f"File size exceeds the configured bound: {path.name}")
    digest = hashlib.sha256()
    count = 0
    with path.open("rb") as source:
        while block := source.read(1024 * 1024):
            check()
            count += len(block)
            if count > maximum:
                raise InferenceLimitError("File grew beyond its configured size bound.")
            digest.update(block)
    if count != size or digest.hexdigest() != expected.lower():
        raise LocalInferenceError(f"SHA-256 mismatch or changing file: {path.name}")
    return size


def memory_watchdog_supported() -> bool:
    return os.name == "nt" or Path("/proc/self/status").is_file()


def process_memory_bytes(pid: int) -> int:
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        class Counters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [(name, ctypes.c_size_t) for name in ("PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage", "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage", "PrivateUsage")]
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        handle = kernel.OpenProcess(0x0400 | 0x0010, False, pid)
        if not handle:
            raise LocalInferenceError("Cannot open owned process for memory measurement.")
        try:
            values = Counters()
            values.cb = ctypes.sizeof(values)
            if not psapi.GetProcessMemoryInfo(handle, ctypes.byref(values), values.cb):
                raise LocalInferenceError("Cannot measure owned process memory.")
            return max(values.WorkingSetSize, values.PrivateUsage)
        finally:
            kernel.CloseHandle(handle)
    try:
        status = Path(f"/proc/{pid}/status").read_text(encoding="utf-8")
        value = re.search(r"^VmRSS:\s+(\d+)\s+kB", status, re.M)
        if value:
            return int(value[1]) * 1024
    except OSError as error:
        raise LocalInferenceError("Cannot measure owned process memory.") from error
    raise LocalInferenceError("Sampled process-memory measurement is unsupported.")


def preflight(config: LocalInferenceConfig, *, cancel_event=None, memory_supported=None, deadline=None) -> dict:
    """Hash existing assets and produce a command plan. Never execute a process."""
    def check():
        if cancel_event is not None and cancel_event.is_set():
            raise InferenceCancelled("Preflight cancelled.")
        if deadline is not None and time.monotonic() >= deadline:
            raise InferenceLimitError("Preflight deadline exceeded.")
    limits = config.limits
    supported = memory_watchdog_supported() if memory_supported is None else bool(memory_supported)
    if limits.memory_limit_bytes is not None and not supported:
        raise LocalInferenceError("Configured memory tripwire is unsupported; preflight fails closed.")
    try:
        executable, model = _local_file(config.executable_path), _local_file(config.model_path)
    except OSError as error:
        raise LocalInferenceError("Configured executable/model does not exist as an accessible local file.") from error
    if executable.suffix.lower() in {".bat", ".cmd", ".ps1", ".sh", ".py", ".vbs"}:
        raise LocalInferenceError("A native executable is required; shell/script entry points are rejected.")
    if model.suffix.lower() != ".gguf" or re.search(r"-\d{5}-of-\d{5}\.gguf$", model.name, re.I):
        raise LocalInferenceError("A single explicit GGUF is required; implicit multi-shard loading is unsupported.")
    executable_bytes = _digest(executable, config.executable_sha256, 256 * 1024 * 1024, check)
    model_bytes = _digest(model, config.model_sha256, limits.max_model_bytes, check)
    with model.open("rb") as source:
        if source.read(4) != b"GGUF":
            raise LocalInferenceError("Configured model is not a GGUF file.")
    # b12359 defaults allow an 8192 MiB prompt cache and 32 context
    # checkpoints. Independent passage calls do not need either; request-level
    # cache_prompt=False alone did not prevent a real 4B run exceeding 5 GiB.
    # Keep those host caches disabled in addition to the sampled memory tripwire.
    command = [str(executable), "-m", str(model), "--offline", "--host", "127.0.0.1", "--port", str(config.port), "--parallel", "1", "--ctx-size", str(limits.context_tokens), "--n-predict", str(limits.output_tokens), "--batch-size", "128", "--ubatch-size", "128", "--threads", str(limits.threads), "--threads-batch", str(limits.threads), "--threads-http", "1", "--device", "none", "--n-gpu-layers", "0", "--no-op-offload", "--fit", "off", "--no-context-shift", "--cache-ram", "0", "--ctx-checkpoints", "0", "--no-cache-prompt", "--no-mmproj", "--reasoning-budget", "0", "--reasoning", "off", "--jinja", "--chat-template-kwargs", '{"enable_thinking":false}', "--no-webui", "--no-agent", "--no-slots"]
    return {"command": command, "executable_sha256": config.executable_sha256.lower(), "model_sha256": config.model_sha256.lower(), "executable_bytes": executable_bytes, "model_bytes": model_bytes, "limits": asdict(limits), "backend": "cpu", "network_scope": "literal_loopback_only", "authentication": "ephemeral_API_key_added_at_owned_process_launch", "launch_performed": False, "binary_compatibility_validated": False, "memory_enforcement": "sampled_process_tripwire_not_hard_OS_RAM_limit" if limits.memory_limit_bytes is not None else "disabled_no_hard_OS_RAM_limit", "documentation_url": DOCUMENTATION_URL}


class LoopbackTransport:
    """Direct HTTPConnection ignores proxies and never follows redirects."""
    def __init__(self, port: int):
        if type(port) is not int or not 1024 <= port <= 65535:
            raise ValueError("Invalid local port")
        self.port, self._connection = port, None
        self.api_key = None

    def request(self, method, endpoint, body, timeout, max_bytes):
        if (method, endpoint) not in {("GET", "/health"), ("POST", "/apply-template"), ("POST", "/tokenize"), ("POST", "/completion")}:
            raise LocalInferenceError("Unsupported local inference endpoint.")
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=timeout)
        self._connection = connection
        try:
            headers = {"Content-Type": "application/json", "Connection": "close"}
            if self.api_key:
                headers["Authorization"] = "Bearer " + self.api_key
            connection.request(method, endpoint, body=body, headers=headers)
            response = connection.getresponse()
            if response.status != 200:
                raise LocalInferenceError(f"Local server returned HTTP {response.status}; redirects are not followed.")
            data = response.read(max_bytes + 1)
            if len(data) > max_bytes:
                raise InferenceLimitError("Local response byte bound exceeded.")
            return data
        finally:
            connection.close()
            self._connection = None

    def abort(self):
        connection = self._connection
        if connection is not None:
            if connection.sock is not None:
                try:
                    connection.sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            connection.close()


def _health_probe_diagnostic(error):
    """Keep codes and controlled summaries, never transport text/headers/keys."""
    chain, current = [], error
    while isinstance(current, BaseException) and len(chain) < 4 and current not in chain:
        chain.append(current)
        current = current.__cause__
    cause = next((item for item in reversed(chain) if isinstance(item, OSError)), error)
    codes = {name: getattr(cause, name, None) for name in ("errno", "winerror")}
    codes = {name: value if type(value) is int and -(2**31) <= value < 2**31 else None
             for name, value in codes.items()}
    http_status = None
    for item in chain:
        match = re.fullmatch(r"Local server returned HTTP (\d{3}); redirects are not followed\.", str(item)[:192])
        if match:
            http_status = int(match[1])
            break
    if codes["winerror"] == 10013 or codes["errno"] in {1, 13}:
        category, summary = "socket_permission_denied", "Loopback health-probe socket permission denied."
    elif codes["winerror"] == 10061 or codes["errno"] in {61, 111} or isinstance(cause, ConnectionRefusedError):
        category, summary = "connection_refused", "Loopback health-probe connection refused."
    elif isinstance(cause, TimeoutError) or codes["winerror"] == 10060 or isinstance(error, InferenceLimitError):
        category, summary = "probe_timeout", "Local health probe did not complete before its deadline."
    elif http_status is not None:
        category, summary = "health_HTTP_error", f"Local health endpoint returned HTTP {http_status}."
    elif isinstance(cause, OSError):
        category, summary = "OS_transport_error", "Local health probe encountered an OS transport error."
    else:
        category, summary = "health_protocol_error", "Local health probe returned an invalid/error response."
    if codes["winerror"] is not None:
        summary += f" WinError {codes['winerror']}."
    if codes["errno"] is not None:
        summary += f" errno {codes['errno']}."
    return {"category": category, "error_type": re.sub(r"[^A-Za-z0-9_]", "", type(cause).__name__)[:48],
            **codes, "http_status": http_status, "summary": summary}


class LlamaServerClient:
    """Explicit start/close lifecycle; injected transports are synthetic test seams.

    Caller owns schema validation and serial batching. ``generate`` never starts a
    server automatically. Every failure aborts the request and kills this client's
    owned process; there is no remote, GPU, model, or truncation fallback.
    """
    def __init__(self, config: LocalInferenceConfig, *, transport=None, process_factory=None, memory_probe=None):
        self.config = config
        self.transport = transport if transport is not None else LoopbackTransport(config.port)
        self._injected_transport = transport is not None
        self._process_factory = process_factory or subprocess.Popen
        self._memory_probe = memory_probe or process_memory_bytes
        self._injected_memory_probe = memory_probe is not None
        self.process = None
        self.plan = None
        self._lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._closed = threading.Event()
        self._peak_memory = 0
        self._memory_samples = 0
        self._operation_started = None
        self._stage = "not_started"
        self._usage = {}
        self._completion_diagnostic = {}
        self._owned_pid = None
        self._last_health_probe_error = {}
        self._last_health_probe_status = None

    def diagnostics(self):
        """Bounded failure evidence; never an accepted code or quality judgment."""
        return {"stage": self._stage,
                "elapsed_seconds": None if self._operation_started is None else time.monotonic() - self._operation_started,
                "peak_sampled_process_bytes": self._peak_memory, "memory_samples": self._memory_samples,
                "memory_enforcement": "sampled_process_tripwire_not_hard_OS_RAM_limit" if self.config.limits.memory_limit_bytes is not None else "disabled",
                "usage": dict(self._usage), "completion": dict(self._completion_diagnostic),
                "last_health_probe_error": dict(self._last_health_probe_error),
                "last_health_probe_status": self._last_health_probe_status,
                "owned_pid": self._owned_pid, "owned_process_closed": self.process is None,
                "transport": "synthetic_injected" if self._injected_transport else "literal_loopback",
                "backend": "cpu", "model_sha256": self.config.model_sha256.lower(),
                "executable_sha256": self.config.executable_sha256.lower()}

    def _check(self, deadline, cancel_event):
        if self._closed.is_set():
            raise InferenceCancelled("Local inference client has been closed.")
        if cancel_event is not None and cancel_event.is_set():
            raise InferenceCancelled("Local inference cancelled.")
        if time.monotonic() >= deadline:
            raise InferenceLimitError("Local inference wall deadline exceeded.")
        process = self.process
        if process is not None:
            if process.poll() is not None:
                raise LocalInferenceError("Owned local server exited.")
            if self.config.limits.memory_limit_bytes is not None:
                measured = self._memory_probe(process.pid)
                if type(measured) is not int or measured < 0:
                    raise LocalInferenceError("Memory tripwire did not return a valid measurement.")
                self._peak_memory = max(self._peak_memory, measured)
                self._memory_samples += 1
                if measured > self.config.limits.memory_limit_bytes:
                    raise InferenceLimitError("Sampled process-memory tripwire exceeded; not a hard OS RAM limit.")

    def _request(self, endpoint, payload, deadline, cancel_event):
        self._check(deadline, cancel_event)
        body = None if payload is None else json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        if body is not None and len(body) > self.config.limits.max_request_bytes:
            raise InferenceLimitError("Request byte bound exceeded.")
        result = queue.Queue(maxsize=1)
        def worker():
            try:
                result.put((True, self.transport.request("GET" if payload is None else "POST", endpoint, body, max(0.001, deadline - time.monotonic()), self.config.limits.max_response_bytes)))
            except Exception as error:
                result.put((False, error))
        threading.Thread(target=worker, name="themeforge-local-http", daemon=True).start()
        while True:
            self._check(deadline, cancel_event)
            try:
                success, value = result.get(timeout=min(0.02, max(0.001, deadline - time.monotonic())))
                break
            except queue.Empty:
                continue
        self._check(deadline, cancel_event)
        if not success:
            raise LocalInferenceError(f"Local transport failed: {value}") from value
        if not isinstance(value, bytes) or len(value) > self.config.limits.max_response_bytes:
            raise InferenceLimitError("Response byte bound exceeded.")
        try:
            decoded = json.loads(value.decode("utf-8"))
        except (UnicodeError, ValueError) as error:
            raise LocalInferenceError("Local endpoint returned invalid JSON/UTF-8.") from error
        if not isinstance(decoded, dict) or "error" in decoded:
            raise LocalInferenceError("Local endpoint returned an invalid/error object.")
        return decoded

    def start(self, cancel_event=None):
        if not self._lock.acquire(blocking=False):
            raise LocalInferenceError("Client startup/generation is already running.")
        if self.process is not None or self._closed.is_set():
            self._lock.release()
            raise LocalInferenceError("This client already owns a server process.")
        deadline = time.monotonic() + self.config.limits.startup_seconds
        self._operation_started = time.monotonic()
        self._stage = "startup"
        self._last_health_probe_error = {}
        self._last_health_probe_status = None
        try:
            self.plan = preflight(self.config, cancel_event=cancel_event, deadline=deadline, memory_supported=self._injected_memory_probe or memory_watchdog_supported())
            # Do not attach to a service that happens to occupy the configured port.
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                probe.bind(("127.0.0.1", self.config.port))
            environment = {key: os.environ[key] for key in ("SystemRoot", "WINDIR", "TEMP", "TMP") if key in os.environ}
            environment["PATH"] = str(Path(self.plan["command"][0]).parent) + os.pathsep + (str(Path(environment.get("SystemRoot", "C:/Windows")) / "System32") if os.name == "nt" else os.defpath)
            kwargs = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=False, env=environment)
            if os.name == "nt":
                kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
                startup = subprocess.STARTUPINFO()
                startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startup.wShowWindow = subprocess.SW_HIDE
                kwargs["startupinfo"] = startup
            else:
                kwargs["start_new_session"] = True
            command = list(self.plan["command"])
            if isinstance(self.transport, LoopbackTransport):
                self.transport.api_key = secrets.token_urlsafe(32)
                command += ["--api-key", self.transport.api_key]
            with self._state_lock:
                self._check(deadline, cancel_event)
                self.process = self._process_factory(command, **kwargs)
                self._owned_pid = self.process.pid
            while True:
                self._check(deadline, cancel_event)
                try:
                    health = self._request("/health", None, min(deadline, time.monotonic() + 0.5), cancel_event)
                    if health.get("status") == "ok":
                        self._last_health_probe_error = {}
                        self._last_health_probe_status = "ok"
                        return dict(self.plan, launch_performed=True)
                    self._last_health_probe_status = "loading" if health.get("status") == "loading" else "not_ready"
                except LocalInferenceError as error:
                    # A final deadline must not overwrite the useful preceding
                    # socket error with the deadline it caused us to reach.
                    if not isinstance(error, InferenceCancelled) and (
                            not isinstance(error, InferenceLimitError) or not self._last_health_probe_error):
                        self._last_health_probe_error = _health_probe_diagnostic(error)
                    self._check(deadline, cancel_event)
                if cancel_event is not None:
                    cancel_event.wait(0.02)
                else:
                    time.sleep(0.02)
        except BaseException as error:
            self.close()
            context = []
            if self._last_health_probe_status not in {None, "ok"}:
                context.append("status=" + self._last_health_probe_status)
            if self._last_health_probe_error:
                context.append(self._last_health_probe_error["summary"])
            if context and isinstance(error, LocalInferenceError):
                # Suppress any raw transport exception context, which could
                # contain headers or command text from an injected transport.
                raise type(error)(str(error) + " Last startup health probe: " + " ".join(context)) from None
            raise
        finally:
            self._lock.release()

    def generate(self, messages, schema, cancel_event=None) -> dict:
        if not self._lock.acquire(blocking=False):
            raise LocalInferenceError("Only one request may run at a time.")
        started = time.monotonic()
        self._operation_started = started
        self._stage = "input_validation"
        self._usage = {}
        self._completion_diagnostic = {}
        deadline = started + self.config.limits.wall_seconds
        try:
            if self.process is None and not self._injected_transport:
                raise LocalInferenceError("Explicitly start an owned local server before generation.")
            if not isinstance(messages, list) or not messages or any(not isinstance(message, dict) or set(message) != {"role", "content"} or message.get("role") not in {"system", "user", "assistant"} or not isinstance(message.get("content"), str) for message in messages):
                raise LocalInferenceError("Messages must be text-only role/content records; tools and media are unsupported.")
            if any(_CHAT_CONTROL.search(message["content"]) for message in messages):
                raise LocalInferenceError("Literal chat control delimiters are not permitted inside message contents; escape source JSON string values before rendering.")
            if not isinstance(schema, dict):
                raise LocalInferenceError("A JSON schema object is required.")
            # The native grammar converter must never resolve a remote schema.
            try:
                serialized_schema = json.dumps(schema, allow_nan=False).encode("utf-8")
            except (TypeError, ValueError, RecursionError) as error:
                raise LocalInferenceError("Schema must be finite, JSON-serializable data.") from error
            if len(serialized_schema) > self.config.limits.max_request_bytes:
                raise InferenceLimitError("Request byte bound exceeded by schema.")
            pending = [schema]
            while pending:
                entry = pending.pop()
                if isinstance(entry, dict):
                    if "$ref" in entry and (not isinstance(entry["$ref"], str) or not entry["$ref"].startswith("#")):
                        raise LocalInferenceError("External schema references are not permitted.")
                    pending.extend(entry.values())
                elif isinstance(entry, list):
                    pending.extend(entry)
            self._stage = "template"
            rendered = self._request("/apply-template", {"messages": messages}, deadline, cancel_event).get("prompt")
            if not isinstance(rendered, str) or not rendered:
                raise LocalInferenceError("Template endpoint did not return a nonempty prompt.")
            self._stage = "tokenization"
            tokens = self._request("/tokenize", {"content": rendered, "add_special": True, "parse_special": True, "with_pieces": False}, deadline, cancel_event).get("tokens")
            if not isinstance(tokens, list) or not tokens or any(type(token) is not int or token < 0 for token in tokens):
                raise LocalInferenceError("Tokenizer did not return integer token IDs.")
            limits = self.config.limits
            self._usage["input_tokens"] = len(tokens)
            if len(tokens) + limits.output_tokens + 1 > limits.context_tokens:
                raise InferenceLimitError("Rendered input plus output/EOS reserve exceeds context; no truncation performed.")
            # Integer-token prompt avoids a second tokenization or implicit BOS.
            self._stage = "completion"
            raw = self._request("/completion", {"prompt": tokens, "json_schema": schema, "n_predict": limits.output_tokens, "temperature": 0, "seed": 0, "stream": False, "cache_prompt": False, "return_tokens": True}, deadline, cancel_event)
            content, output = raw.get("content"), raw.get("tokens")
            self._completion_diagnostic = {"stop_type": raw.get("stop_type"), "truncated": raw.get("truncated"),
                "tokens_evaluated": raw.get("tokens_evaluated"), "status": "unvalidated_diagnostic_only",
                "raw_preview": content[:4096] if isinstance(content, str) else None}
            if isinstance(output, list):
                self._usage["output_tokens"] = len(output)
            if raw.get("stop_type") != "eos" or raw.get("truncated") is not False:
                raise InferenceLimitError("Completion did not end with EOS without truncation; token-limit/unknown stops are rejected.")
            if not isinstance(content, str) or len(content.encode("utf-8")) > limits.max_output_bytes:
                raise InferenceLimitError("Generated content exceeds the output byte bound or is not text.")
            if not isinstance(output, list) or any(type(token) is not int or token < 0 for token in output) or len(output) > limits.output_tokens:
                raise InferenceLimitError("Generated token usage is invalid or exceeds its bound.")
            evaluated = raw.get("tokens_evaluated")
            if type(evaluated) is not int or evaluated != len(tokens):
                raise LocalInferenceError("Server prompt token count differs from exact rendered input; refusing silent rewriting/truncation.")
            self._check(deadline, cancel_event)
            self._stage = "validated_protocol_response"
            memory_mode = "disabled_no_hard_OS_RAM_limit" if limits.memory_limit_bytes is None else "synthetic_transport_no_owned_process_to_sample" if self.process is None else "sampled_owned_process_tripwire_not_hard_OS_RAM_limit"
            return {"raw": content, "server_response": raw, "usage": {"input_tokens": len(tokens), "output_tokens": len(output)}, "resources": {"elapsed_seconds": time.monotonic() - started, "peak_sampled_process_bytes": self._peak_memory, "memory_samples": self._memory_samples, "memory_enforcement": memory_mode, "transport": "synthetic_injected" if self._injected_transport else "literal_loopback", "backend": "cpu", "model_sha256": self.config.model_sha256.lower(), "executable_sha256": self.config.executable_sha256.lower(), "real_binary_validated": not self._injected_transport, "validation_scope": "this_protocol_response_not_general_model_quality"}}
        except BaseException:
            self.close()
            raise
        finally:
            self._lock.release()

    def close(self):
        self._closed.set()
        try:
            self.transport.abort()
        finally:
            with self._state_lock:
                process = self.process
                if process is not None and process.poll() is None:
                    process.kill()
                    try:
                        # A real 2B Windows process exceeded the former 0.5s
                        # allowance after kill. Wait for confirmed exit with a
                        # bounded teardown grace period before releasing it.
                        process.wait(timeout=5.0)
                    except subprocess.TimeoutExpired as error:
                        raise LocalInferenceError("Owned local server did not exit after kill.") from error
                self.process = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

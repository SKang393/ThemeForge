"""Synthetic protocol/process fixtures only. No model or llama binary is loaded."""
from dataclasses import replace
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import unittest
from unittest.mock import patch

from themeforge.local_inference import (
    InferenceCancelled, InferenceLimitError, InferenceLimits, LlamaServerClient,
    LocalInferenceConfig, LocalInferenceError, LoopbackTransport, preflight,
    process_memory_bytes, memory_watchdog_supported, staged_inference_limits,
)


MESSAGES = [{"role": "user", "content": "Return a fictional JSON record."}]
SCHEMA = {"type": "object", "properties": {"value": {"type": "string"}}, "required": ["value"]}


class FixtureTransport:
    def __init__(self):
        self.calls = []
        self.aborted = threading.Event()
        self.responses = {"/health": {"status": "ok"}, "/apply-template": {"prompt": "<bos>rendered fictional conversation<assistant>"}, "/tokenize": {"tokens": [1, 2, 3]}, "/completion": {"content": '{"value":"fictional"}', "tokens": [4, 5], "tokens_evaluated": 3, "stop_type": "eos", "truncated": False}}
        self.stall = None

    def request(self, method, endpoint, body, timeout, max_bytes):
        self.calls.append((method, endpoint, json.loads(body) if body else None))
        if self.stall == endpoint:
            self.aborted.wait(3)
        return json.dumps(self.responses[endpoint]).encode()

    def abort(self):
        self.aborted.set()


class LocalInferenceTests(unittest.TestCase):
    def fake_startup_client(self, transport, startup_seconds=0.08):
        class OwnedFixture:
            pid = 123456
            returncode = None
            killed = False
            waited = None
            def poll(self): return self.returncode
            def kill(self): self.killed = True; self.returncode = -9
            def wait(self, timeout): self.waited = timeout; return self.returncode
        process = OwnedFixture()
        config = replace(self.config, limits=replace(self.config.limits, startup_seconds=startup_seconds))
        client = LlamaServerClient(config, transport=transport, process_factory=lambda *args, **kwargs: process)
        self.addCleanup(client.close)
        return client, process

    def test_startup_blocked_health_keeps_permission_code_and_sanitized_timeout_reason(self):
        secret = "Bearer SECRET_HEADER --api-key SECRET_ARGUMENT"
        class BlockedHealth(FixtureTransport):
            def request(self, method, endpoint, body, timeout, max_bytes):
                error = PermissionError(13, secret)
                error.winerror = 10013
                raise error
        client, process = self.fake_startup_client(BlockedHealth())
        with patch("themeforge.local_inference.socket.socket"), self.assertRaisesRegex(InferenceLimitError, "WinError 10013") as raised:
            client.start()
        diagnostic = client.diagnostics()
        error = diagnostic["last_health_probe_error"]
        self.assertEqual(error["category"], "socket_permission_denied")
        self.assertEqual((error["winerror"], error["errno"]), (10013, 13))
        self.assertLess(len(json.dumps(error)), 512)
        self.assertNotIn(secret, json.dumps(diagnostic))
        self.assertNotIn(secret, "".join(traceback.format_exception(raised.exception)))
        self.assertEqual(diagnostic["stage"], "startup")
        self.assertTrue(diagnostic["owned_process_closed"])
        self.assertTrue(process.killed)
        self.assertEqual(process.waited, 5.0)

    def test_startup_refused_health_exposes_code_without_raw_transport_text(self):
        class RefusedHealth(FixtureTransport):
            def request(self, *args):
                raise ConnectionRefusedError(111, "private command or headers must not be copied")
        client, process = self.fake_startup_client(RefusedHealth())
        with patch("themeforge.local_inference.socket.socket"), self.assertRaisesRegex(InferenceLimitError, "connection refused"):
            client.start()
        diagnostic = client.diagnostics()
        self.assertEqual(diagnostic["last_health_probe_error"]["errno"], 111)
        self.assertEqual(diagnostic["last_health_probe_error"]["category"], "connection_refused")
        self.assertNotIn("private command", json.dumps(diagnostic))
        self.assertTrue(process.killed)

    def test_startup_loading_health_explains_deadline_and_closes_owned_fixture(self):
        transport = FixtureTransport()
        transport.responses["/health"] = {"status": "loading"}
        client, process = self.fake_startup_client(transport)
        with patch("themeforge.local_inference.socket.socket"), self.assertRaisesRegex(InferenceLimitError, "status=loading"):
            client.start()
        self.assertEqual(client.diagnostics()["last_health_probe_status"], "loading")
        self.assertTrue(process.killed)
        self.assertTrue(transport.aborted.is_set())

    def test_transient_health_error_clears_when_healthy_and_does_not_taint_generation_failure(self):
        class TransientHealth(FixtureTransport):
            health_attempts = 0
            def request(self, method, endpoint, body, timeout, max_bytes):
                if endpoint == "/health":
                    self.health_attempts += 1
                    if self.health_attempts == 1:
                        raise ConnectionRefusedError(111, "not ready yet")
                return super().request(method, endpoint, body, timeout, max_bytes)
        transport = TransientHealth()
        client, process = self.fake_startup_client(transport, startup_seconds=1)
        with patch("themeforge.local_inference.socket.socket"):
            self.assertTrue(client.start()["launch_performed"])
        self.assertEqual(transport.health_attempts, 2)
        self.assertEqual(client.diagnostics()["last_health_probe_error"], {})
        self.assertEqual(client.diagnostics()["last_health_probe_status"], "ok")
        transport.responses["/completion"]["stop_type"] = "limit"
        with self.assertRaises(InferenceLimitError):
            client.generate(MESSAGES, SCHEMA)
        self.assertNotEqual(client.diagnostics()["stage"], "startup")
        self.assertEqual(client.diagnostics()["last_health_probe_error"], {})
        self.assertTrue(process.killed)

    def test_staged_limits_preserve_old_profiles_and_bound_stronger_models(self):
        default = staged_inference_limits()
        self.assertEqual(default.max_model_bytes, 2 * 1024**3)
        self.assertEqual(default.memory_limit_bytes, 4 * 1024**3)
        limits = staged_inference_limits({"limits": {"max_model_bytes": 4 * 1024**3,
                                                    "memory_limit_bytes": 6 * 1024**3, "wall_seconds": 180}})
        self.assertEqual((limits.threads, limits.context_tokens, limits.wall_seconds), (2, 4096, 180))
        for override in ({"memory_limit_bytes": None}, {"memory_limit_bytes": 7 * 1024**3},
                         {"threads": 3}, {"max_model_bytes": 5 * 1024**3}, {"wall_seconds": 181},
                         {"allow_remote": True}):
            with self.subTest(override=override), self.assertRaises(ValueError):
                staged_inference_limits({"limits": override})

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="local-inference-fixture-", dir=Path(__file__).resolve().parent)
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        executable = root / "llama-server.exe"
        model = root / "fixture.gguf"
        executable.write_bytes(b"MZsynthetic-not-an-executable")
        model.write_bytes(b"GGUFsynthetic-not-a-model")
        self.config = LocalInferenceConfig(executable, hashlib.sha256(executable.read_bytes()).hexdigest(), model, hashlib.sha256(model.read_bytes()).hexdigest(), limits=InferenceLimits(memory_limit_bytes=None))

    def client(self, transport=None, limits=None):
        transport = transport or FixtureTransport()
        config = replace(self.config, limits=limits) if limits is not None else self.config
        client = LlamaServerClient(config, transport=transport)
        self.addCleanup(client.close)
        return client, transport

    def test_preflight_is_cpu_only_offline_bounded_and_never_launches(self):
        with patch("subprocess.Popen", side_effect=AssertionError("Preflight must not launch")):
            plan = preflight(self.config)
        cmd = plan["command"]
        for flag in ("--offline", "--no-context-shift", "--no-mmproj", "--no-op-offload"):
            self.assertIn(flag, cmd)
        for flag, value in (("--host", "127.0.0.1"), ("--parallel", "1"), ("--device", "none"), ("--n-gpu-layers", "0"), ("--fit", "off"), ("--reasoning-budget", "0"), ("--batch-size", "128"), ("--ubatch-size", "128")):
            self.assertEqual(cmd[cmd.index(flag) + 1], value)
        self.assertFalse(plan["launch_performed"])
        self.assertFalse(plan["binary_compatibility_validated"])
        self.assertIn("--jinja", cmd)
        self.assertEqual(json.loads(cmd[cmd.index("--chat-template-kwargs") + 1]), {"enable_thinking": False})
        self.assertEqual(cmd[cmd.index("--reasoning") + 1], "off")
        self.assertEqual(cmd[cmd.index("--cache-ram") + 1], "0")
        self.assertEqual(cmd[cmd.index("--ctx-checkpoints") + 1], "0")
        self.assertIn("--no-cache-prompt", cmd)

    def test_path_hash_size_and_gguf_gates(self):
        for value in ("https://example.invalid/model.gguf", "file:///model.gguf", "\\\\remote\\share\\model.gguf", "//host/share/model.gguf", "relative.gguf"):
            with self.subTest(value=value), self.assertRaises(LocalInferenceError):
                preflight(replace(self.config, model_path=value))
        with self.assertRaises(LocalInferenceError):
            preflight(replace(self.config, model_sha256="0" * 64))
        with self.assertRaises(InferenceLimitError):
            preflight(replace(self.config, limits=replace(self.config.limits, max_model_bytes=4)))
        Path(self.config.model_path).write_bytes(b"NOTGGUF")
        with self.assertRaisesRegex(LocalInferenceError, "not a GGUF"):
            preflight(replace(self.config, model_sha256=hashlib.sha256(b"NOTGGUF").hexdigest()))

    def test_shell_entrypoints_and_implicit_shards_rejected(self):
        script = Path(self.temp.name) / "runtime.cmd"
        script.write_text("do not execute")
        with self.assertRaisesRegex(LocalInferenceError, "native executable"):
            preflight(replace(self.config, executable_path=script))
        shard = Path(self.temp.name) / "fixture-00001-of-00002.gguf"
        shard.write_bytes(b"GGUF")
        with self.assertRaisesRegex(LocalInferenceError, "multi-shard"):
            preflight(replace(self.config, model_path=shard))

    def test_limits_and_unsupported_memory_tripwire_fail_closed(self):
        for kwargs in ({"context_tokens": 4097}, {"output_tokens": 513}, {"threads": 9}, {"wall_seconds": float("nan")}, {"startup_seconds": 0}, {"max_model_bytes": True}, {"context_tokens": 512}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                InferenceLimits(**kwargs)
        config = replace(self.config, limits=InferenceLimits(memory_limit_bytes=1000))
        with self.assertRaisesRegex(LocalInferenceError, "unsupported"):
            preflight(config, memory_supported=False)

    def test_exact_rendered_tokens_used_for_completion_and_usage(self):
        client, transport = self.client()
        response = client.generate(MESSAGES, SCHEMA)
        self.assertIsInstance(response["raw"], str)
        self.assertEqual(response["raw"], '{"value":"fictional"}')
        self.assertEqual(json.loads(response["raw"]), {"value": "fictional"})
        self.assertEqual(response["server_response"], transport.responses["/completion"])
        self.assertEqual([call[1] for call in transport.calls], ["/apply-template", "/tokenize", "/completion"])
        self.assertEqual(transport.calls[1][2]["content"], transport.responses["/apply-template"]["prompt"])
        self.assertTrue(transport.calls[1][2]["add_special"])
        completion = transport.calls[2][2]
        self.assertEqual(completion["prompt"], transport.responses["/tokenize"]["tokens"])
        self.assertEqual(completion["json_schema"], SCHEMA)
        self.assertEqual(completion["n_predict"], 512)
        self.assertEqual(response["usage"], {"input_tokens": 3, "output_tokens": 2})
        self.assertEqual(response["resources"]["transport"], "synthetic_injected")
        self.assertFalse(response["resources"]["real_binary_validated"])
        self.assertEqual(response["resources"]["memory_enforcement"], "disabled_no_hard_OS_RAM_limit")
        self.assertEqual(response["resources"]["memory_samples"], 0)

    def test_context_bound_rejects_before_any_completion_and_does_not_truncate(self):
        client, transport = self.client(limits=InferenceLimits(context_tokens=520, memory_limit_bytes=None))
        transport.responses["/tokenize"] = {"tokens": list(range(20))}
        with self.assertRaisesRegex(InferenceLimitError, "no truncation"):
            client.generate(MESSAGES, SCHEMA)
        self.assertNotIn("/completion", [call[1] for call in transport.calls])

    def test_only_eos_without_truncation_is_accepted(self):
        for change in ({"stop_type": "limit"}, {"stop_type": "none"}, {"stop_type": "word"}, {"truncated": True}, {"truncated": None}):
            with self.subTest(change=change):
                client, transport = self.client()
                transport.responses["/completion"].update(change)
                with self.assertRaises(InferenceLimitError):
                    client.generate(MESSAGES, SCHEMA)

    def test_failed_completion_preserves_bounded_diagnostics_without_acceptance(self):
        client, transport = self.client()
        transport.responses["/completion"].update(stop_type="limit", content="x" * 5000)
        with self.assertRaises(InferenceLimitError):
            client.generate(MESSAGES, SCHEMA)
        diagnostic = client.diagnostics()
        self.assertEqual(diagnostic["usage"], {"input_tokens": 3, "output_tokens": 2})
        self.assertEqual(diagnostic["completion"]["status"], "unvalidated_diagnostic_only")
        self.assertEqual(len(diagnostic["completion"]["raw_preview"]), 4096)
        self.assertGreaterEqual(diagnostic["elapsed_seconds"], 0)
        self.assertTrue(diagnostic["owned_process_closed"])

    def test_usage_and_output_byte_limits_are_measured(self):
        client, transport = self.client(limits=InferenceLimits(max_output_bytes=3, memory_limit_bytes=None))
        with self.assertRaisesRegex(InferenceLimitError, "output byte"):
            client.generate(MESSAGES, SCHEMA)
        client, transport = self.client()
        transport.responses["/completion"]["tokens"] = list(range(513))
        with self.assertRaisesRegex(InferenceLimitError, "token usage"):
            client.generate(MESSAGES, SCHEMA)
        client, transport = self.client()
        transport.responses["/completion"]["tokens_evaluated"] = 2
        with self.assertRaisesRegex(LocalInferenceError, "token count differs"):
            client.generate(MESSAGES, SCHEMA)

    def test_request_and_response_bytes_are_bounded(self):
        client, _ = self.client(limits=InferenceLimits(max_request_bytes=5, memory_limit_bytes=None))
        with self.assertRaisesRegex(InferenceLimitError, "Request byte"):
            client.generate(MESSAGES, SCHEMA)
        client, _ = self.client(limits=InferenceLimits(max_response_bytes=5, memory_limit_bytes=None))
        with self.assertRaisesRegex(InferenceLimitError, "Response byte"):
            client.generate(MESSAGES, SCHEMA)

    def test_owned_process_is_killed_on_real_deadline_and_cancellation(self):
        for cancelled in (False, True):
            with self.subTest(cancelled=cancelled):
                limits = InferenceLimits(wall_seconds=0.12 if not cancelled else 1.0, memory_limit_bytes=None)
                client, transport = self.client(limits=limits)
                transport.stall = "/completion"
                kwargs = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
                process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(20)"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kwargs)
                client.process = process
                event = threading.Event()
                timer = threading.Timer(0.06, event.set) if cancelled else None
                if timer:
                    timer.start()
                    self.addCleanup(timer.cancel)
                started = time.monotonic()
                with self.assertRaises(InferenceCancelled if cancelled else InferenceLimitError):
                    client.generate(MESSAGES, SCHEMA, event)
                self.assertLess(time.monotonic() - started, 0.9)
                self.assertIsNotNone(process.poll())
                self.assertTrue(transport.aborted.is_set())

    def test_memory_tripwire_kills_owned_process(self):
        client, _ = self.client(limits=InferenceLimits(memory_limit_bytes=1))
        kwargs = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
        process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(20)"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kwargs)
        client.process = process
        client._memory_probe = lambda pid: 1000
        with self.assertRaisesRegex(InferenceLimitError, "not a hard OS RAM limit"):
            client.generate(MESSAGES, SCHEMA)
        self.assertIsNotNone(process.poll())

    def test_start_is_explicit_and_environment_is_allowlisted(self):
        transport = FixtureTransport()
        launched = []
        def fixture_factory(command, **kwargs):
            launched.append((command, kwargs))
            # Explicit synthetic process fixture, not the configured fake binary.
            return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(20)"], **kwargs)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        client = LlamaServerClient(replace(self.config, port=port), transport=transport, process_factory=fixture_factory)
        self.addCleanup(client.close)
        with patch.dict(os.environ, {"LLAMA_ARG_MODEL_URL": "https://example.invalid/model", "LLAMA_ARG_RPC": "remote:1", "HTTP_PROXY": "http://proxy.invalid"}):
            plan = client.start()
        self.assertTrue(plan["launch_performed"])
        self.assertEqual(len(launched), 1)
        self.assertFalse(launched[0][1]["shell"])
        self.assertNotIn("LLAMA_ARG_MODEL_URL", launched[0][1]["env"])
        self.assertNotIn("LLAMA_ARG_RPC", launched[0][1]["env"])
        self.assertNotIn("HTTP_PROXY", launched[0][1]["env"])
        if os.name == "nt":
            self.assertTrue(launched[0][1]["creationflags"] & subprocess.CREATE_NO_WINDOW)
        client.close()
        client = LlamaServerClient(self.config)
        with self.assertRaisesRegex(LocalInferenceError, "Explicitly start"):
            client.generate(MESSAGES, SCHEMA)

    def test_startup_deadline_kills_synthetic_owned_server(self):
        transport = FixtureTransport()
        transport.responses["/health"] = {"status": "loading"}
        processes = []
        def fixture_factory(command, **kwargs):
            process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(20)"], **kwargs)
            processes.append(process)
            return process
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        config = replace(self.config, port=port, limits=InferenceLimits(startup_seconds=0.1, memory_limit_bytes=None))
        client = LlamaServerClient(config, transport=transport, process_factory=fixture_factory)
        self.addCleanup(client.close)
        with self.assertRaises(InferenceLimitError):
            client.start()
        self.assertIsNotNone(processes[0].poll())

    def test_direct_http_ignores_proxy_rejects_redirect_and_bounds_bytes(self):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(302)
                self.send_header("Location", "https://example.invalid/never-follow")
                self.end_headers()
            def do_POST(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"x" * 20)
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        transport = LoopbackTransport(server.server_port)
        with patch.dict(os.environ, {"HTTP_PROXY": "http://127.0.0.1:1", "ALL_PROXY": "http://127.0.0.1:1", "NO_PROXY": ""}):
            with self.assertRaisesRegex(LocalInferenceError, "302"):
                transport.request("GET", "/health", None, 1, 100)
            with self.assertRaises(InferenceLimitError):
                transport.request("POST", "/completion", b"{}", 1, 5)
        with self.assertRaises(LocalInferenceError):
            transport.request("GET", "http://remote.invalid/health", None, 1, 100)

    def test_real_process_memory_probe_when_supported(self):
        if not memory_watchdog_supported():
            self.skipTest("Native process memory measurement unsupported")
        self.assertGreater(process_memory_bytes(os.getpid()), 0)

    def test_precancelled_request_has_no_transport_or_process_side_effect(self):
        client, transport = self.client()
        event = threading.Event()
        event.set()
        with self.assertRaises(InferenceCancelled):
            client.generate(MESSAGES, SCHEMA, event)
        self.assertEqual(transport.calls, [])

    def test_external_schema_refs_rejected_before_transport(self):
        client, transport = self.client()
        with self.assertRaisesRegex(LocalInferenceError, "External schema"):
            client.generate(MESSAGES, {"$ref": "https://example.invalid/schema"})
        self.assertEqual(transport.calls, [])

    def test_literal_chat_delimiters_rejected_before_template_rendering(self):
        for delimiter in ("<|im_start|>", "<|im_end|>", "<|endoftext|>", "<|start_header_id|>", "<|eot_id|>", "[INST]", "[/INST]", "<<SYS>>", "<</SYS>>", "<s>", "</s>", "[SYSTEM_PROMPT]", "[/TOOL_RESULTS]"):
            with self.subTest(delimiter=delimiter):
                client, transport = self.client()
                content = json.dumps({"source_text": "A literal " + delimiter + " appears in this fictional source."})
                with self.assertRaisesRegex(LocalInferenceError, "Literal chat control"):
                    client.generate([{"role": "user", "content": content}], SCHEMA)
                self.assertEqual(transport.calls, [])

    def test_escaped_source_delimiters_keep_decoded_text_and_template_tokens(self):
        client, transport = self.client()
        source = "A literal <|im_start|> and [INST] remain source text."
        content = json.dumps({"source_text": source}).replace("<", "\\u003c").replace(">", "\\u003e").replace("[", "\\u005b").replace("]", "\\u005d")
        self.assertEqual(json.loads(content)["source_text"], source)
        transport.responses["/apply-template"]["prompt"] = "<|im_start|>user\n" + content + "<|im_end|><|im_start|>assistant\n"
        response = client.generate([{"role": "user", "content": content}], SCHEMA)
        self.assertIsInstance(response["raw"], str)
        self.assertTrue(transport.calls[1][2]["parse_special"])
        self.assertEqual(transport.calls[1][2]["content"], transport.responses["/apply-template"]["prompt"])

    def test_generate_is_serial_and_concurrent_close_cancels_request(self):
        client, transport = self.client()
        transport.stall = "/completion"
        result = []
        def generate():
            try:
                result.append(client.generate(MESSAGES, SCHEMA))
            except LocalInferenceError as error:
                result.append(error)
        worker = threading.Thread(target=generate)
        worker.start()
        deadline = time.monotonic() + 1
        while not any(call[1] == "/completion" for call in transport.calls) and time.monotonic() < deadline:
            time.sleep(0.005)
        with self.assertRaisesRegex(LocalInferenceError, "one request"):
            client.generate(MESSAGES, SCHEMA)
        client.close()
        worker.join(timeout=1)
        self.assertFalse(worker.is_alive())
        self.assertIsInstance(result[0], InferenceCancelled)

    def test_abort_failure_still_kills_owned_process(self):
        client, transport = self.client()
        kwargs = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
        process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(20)"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kwargs)
        client.process = process
        with patch.object(transport, "abort", side_effect=RuntimeError("synthetic abort failure")):
            with self.assertRaisesRegex(RuntimeError, "abort failure"):
                client.close()
        self.assertIsNotNone(process.poll())

    def test_cleanup_allows_bounded_windows_model_teardown(self):
        client, _ = self.client()
        class DelayedExit:
            def poll(self): return None
            def kill(self): self.killed = True
            def wait(self, timeout):
                self.timeout = timeout
                if timeout < 1:
                    raise subprocess.TimeoutExpired("synthetic model teardown", timeout)
                return 0
        process = DelayedExit()
        client.process = process
        client.close()
        self.assertTrue(process.killed)
        self.assertGreaterEqual(process.timeout, 1)
        self.assertLessEqual(process.timeout, 5)
        self.assertIsNone(client.process)


if __name__ == "__main__":
    unittest.main()

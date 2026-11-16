"""Native prompt accounting and local raw-call provenance for study batches.

The existing CPU adapter owns startup, caps and cleanup. This wrapper records
actual native template/tokenization results, not estimates. It never downloads.
"""
from __future__ import annotations
from hashlib import sha256
import json
from pathlib import Path
import struct
import time

from .local_inference import LlamaServerClient, LocalInferenceError, InferenceLimitError, GIB, _CHAT_CONTROL, _local_file


def gguf_tokenizer_identity(path):
    """Read bounded GGUF metadata, with no tensor loading or executable model code."""
    limit = 64 * 1024**2
    with Path(path).open('rb') as stream:
        def read(n):
            if n < 0 or stream.tell()+n > limit:
                raise ValueError('GGUF metadata exceeds 64 MiB inspection limit.')
            data = stream.read(n)
            if len(data) != n:
                raise ValueError('Incomplete GGUF metadata.')
            return data
        def number(fmt):
            return struct.unpack('<'+fmt, read(struct.calcsize('<'+fmt)))[0]
        def string():
            size = number('Q')
            if size > 10*1024**2:
                raise ValueError('GGUF string exceeds inspection bound.')
            return read(size).decode('utf-8')
        formats = {0:'B',1:'b',2:'H',3:'h',4:'I',5:'i',6:'f',7:'?',10:'Q',11:'q',12:'d'}
        def value(kind, depth=0):
            if depth > 2:
                raise ValueError('GGUF array nesting exceeds bound.')
            if kind == 8:
                return string()
            if kind == 9:
                item, count = number('I'), number('Q')
                if count > 1000000:
                    raise ValueError('GGUF array exceeds inspection bound.')
                for _ in range(count):
                    value(item, depth+1)
                return {'item_type':item,'count':count}
            if kind not in formats:
                raise ValueError('Unsupported GGUF metadata type.')
            return number(formats[kind])
        if read(4) != b'GGUF' or number('I') != 3:
            raise ValueError('A version 3 GGUF is required.')
        tensors, count = number('Q'), number('Q')
        if not 0 < tensors < 100000 or not 0 < count < 10000:
            raise ValueError('GGUF counts exceed inspection bound.')
        tokenizer = sha256(); selected = {}
        for _ in range(count):
            start = stream.tell()
            key = string(); item = value(number('I'))
            end = stream.tell()
            if key.startswith('tokenizer.'):
                stream.seek(start); tokenizer.update(read(end-start))
            if key in {'general.architecture','general.name','general.file_type','tokenizer.chat_template',
                       'tokenizer.ggml.bos_token_id','tokenizer.ggml.eos_token_id',
                       'tokenizer.ggml.add_bos_token','tokenizer.ggml.add_eos_token'}:
                selected[key] = item
        template = selected.get('tokenizer.chat_template')
        if not isinstance(template,str) or not template:
            raise ValueError('No embedded native chat template; no substitute selected.')
        return dict(metadata=selected, template_sha256=sha256(template.encode()).hexdigest(),
                    tokenizer_sha256=tokenizer.hexdigest(), tokenizer_digest_scope='exact serialized GGUF tokenizer metadata including template',
                    inspected_bytes=stream.tell())


def available_ram_bytes():
    # psutil is already bundled for bounded native workers, not a new dependency.
    import psutil
    return psutil.virtual_memory().available


class StudyLlamaClient(LlamaServerClient):
    def __init__(self, config, *, audit_directory=None, headroom_reserve_bytes=2*GIB, **kwargs):
        super().__init__(config, **kwargs)
        self.identity = None
        self.audit_directory = Path(audit_directory) if audit_directory is not None else None
        self.headroom_reserve_bytes = headroom_reserve_bytes
        self.native_calls = []
        self.native_seconds = 0.0
        self.completed_generation_seconds = 0.0
        self._audit_sequence = 0
        self.expected_tokens = None
        self.period_deadline = None
        self._owned_descendants = {}
        if not self._injected_transport and kwargs.get('memory_probe') is None:
            # Native Windows launchers may own a child process. The same cap
            # applies to the observed tree, not merely the launcher executable.
            self._memory_probe = self._tree_memory
        process_factory = self._process_factory
        def guarded_factory(command, **options):
            if not self._injected_transport:
                self._check_headroom()
            return process_factory(command, **options)
        self._process_factory = guarded_factory

    def _tree_memory(self, pid):
        import psutil
        root = psutil.Process(pid)
        total = 0
        for process in [root, *root.children(recursive=True)]:
            try:
                if process.pid != pid:
                    self._owned_descendants[process.pid] = process.create_time()
                memory = process.memory_info()
                total += max(memory.rss, getattr(memory, 'private', 0))
            except psutil.NoSuchProcess:
                pass
        return total

    def close(self):
        import psutil
        if self.process is not None and not self._injected_transport:
            try:
                self._tree_memory(self.process.pid)
            except psutil.NoSuchProcess:
                pass
        try:
            super().close()
        finally:
            for pid, created in list(self._owned_descendants.items()):
                try:
                    child = psutil.Process(pid)
                    if child.create_time() == created:
                        if child.is_running():
                            child.kill()
                        child.wait(timeout=5)
                except psutil.NoSuchProcess:
                    pass
                except psutil.TimeoutExpired as error:
                    raise LocalInferenceError('An observed owned descendant did not stop within the cleanup limit.') from error

    def _check_headroom(self):
        if self.config.limits.memory_limit_bytes is None:
            raise InferenceLimitError('Study runs require an enabled memory tripwire.')
        available = available_ram_bytes()
        required = self.config.limits.memory_limit_bytes + self.headroom_reserve_bytes
        if available < required:
            raise InferenceLimitError(f'Insufficient current RAM headroom: {available} bytes available; '
                f'{required} required for the {self.config.limits.memory_limit_bytes}-byte sampled process cap plus reserve. No model started.')

    def start(self, cancel_event=None):
        if not self._injected_transport:
            self._check_headroom()
            self.identity = gguf_tokenizer_identity(_local_file(self.config.model_path))
        if self.audit_directory is not None:
            self.audit_directory.mkdir(parents=True,exist_ok=False)
        return super().start(cancel_event)

    def _request(self, endpoint, payload, deadline, cancel_event):
        if self.period_deadline is not None:
            deadline = min(deadline, self.period_deadline)
        started = time.perf_counter()
        response = super()._request(endpoint,payload,deadline,cancel_event)
        elapsed = time.perf_counter()-started
        if endpoint != '/health':
            self._audit_sequence += 1
            record = dict(endpoint=endpoint, payload=payload, response=response, elapsed_seconds=elapsed,
                          origin='native_local_runtime' if not self._injected_transport else 'synthetic_test_transport',
                          model_sha256=self.config.model_sha256, executable_sha256=self.config.executable_sha256)
            # Record full failures/limit stops before the parent parser accepts or rejects them.
            if self.audit_directory is not None:
                with (self.audit_directory/f'{self._audit_sequence:06d}-{endpoint.strip("/")}.json').open('x',encoding='utf-8') as f:
                    json.dump(record,f,ensure_ascii=False,indent=2,allow_nan=False)
            self.native_calls.append(dict(sequence=self._audit_sequence,endpoint=endpoint,elapsed_seconds=elapsed))
            if endpoint in {'/apply-template','/tokenize'}:
                self.native_seconds += elapsed
            elif endpoint == '/completion':
                self.completed_generation_seconds += elapsed
        if endpoint == '/tokenize' and self.expected_tokens is not None and response.get('tokens') != self.expected_tokens:
            raise LocalInferenceError('Native tokenization changed from the frozen batch plan; generation refused.')
        return response

    def measure_messages(self, messages, *, cancel_event=None):
        if not self._lock.acquire(blocking=False):
            raise LocalInferenceError('Native prompt accounting requires the sequential client lock.')
        deadline = time.monotonic()+min(30,self.config.limits.wall_seconds)
        try:
            if self.process is None and not self._injected_transport:
                raise LocalInferenceError('Start the owned local model explicitly before native token accounting.')
            if not isinstance(messages,list) or not messages or any(
                    not isinstance(m,dict) or set(m) != {'role','content'} or m['role'] not in {'system','user','assistant'}
                    or not isinstance(m['content'],str) or _CHAT_CONTROL.search(m['content']) for m in messages):
                raise LocalInferenceError('Invalid text-only messages or unescaped source control tokens.')
            rendered = self._request('/apply-template',{'messages':messages},deadline,cancel_event).get('prompt')
            if not isinstance(rendered,str) or not rendered:
                raise LocalInferenceError('Native template did not return a prompt.')
            tokens = self._request('/tokenize',dict(content=rendered,add_special=True,parse_special=True,with_pieces=False),deadline,cancel_event).get('tokens')
            if not isinstance(tokens,list) or not tokens or any(type(t) is not int or t<0 for t in tokens):
                raise LocalInferenceError('Native tokenizer did not return valid token IDs.')
            if self.identity:
                meta=self.identity['metadata']; bos=meta.get('tokenizer.ggml.bos_token_id')
                if meta.get('tokenizer.ggml.add_bos_token') and (tokens[0] != bos or (len(tokens)>1 and tokens[1] == bos)):
                    raise LocalInferenceError('Native BOS accounting mismatch; no tokens silently inserted or removed.')
            return dict(rendered_prompt=rendered,token_ids=tokens,
                        template_sha256=self.identity['template_sha256'] if self.identity else 'synthetic-test-template',
                        tokenizer_sha256=self.identity['tokenizer_sha256'] if self.identity else 'synthetic-test-tokenizer',
                        add_special=True,parse_special=True,accounting='actual_native_template_and_token_ids' if self.identity else 'synthetic_test_only')
        except BaseException:
            self.close()
            raise
        finally:
            self._lock.release()

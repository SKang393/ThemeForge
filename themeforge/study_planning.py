"""Exact native measurement checkpoints for deterministic planning replay.

Only successful request-bound measurements are cached. A checkpoint is not an
execution plan until a complete replay succeeds. Callers supply verified model,
runtime, tokenizer, template and coding-contract pins before reuse. A digest is
an integrity check, not authentication of an externally supplied checkpoint.
"""
from copy import deepcopy
import json
import math
import os
from pathlib import Path
import tempfile
import time

from .study_batches import (NativePrompt, PlanningInterrupted, TokenBudget,
                            digest, plan_batches, validate_study, CONTEXT_TRANSPORT_POLICY)

VERSION = 'themeforge.study-planning.v1'
LEGACY_CONTEXT_POLICY='nearest_question_and_preceding_turn_or_parent_sentence'
CONTEXT_POLICY=LEGACY_CONTEXT_POLICY+'+'+CONTEXT_TRANSPORT_POLICY
MAX_CHECKPOINT_BYTES = 64 * 1024**2
_STATES = {'pending', 'interrupted', 'completed'}
_PIN_FIELDS = ('model_sha256', 'executable_sha256', 'template_sha256',
               'tokenizer_sha256', 'contract_sha256')


def _pin(value):
    return (type(value) is str and len(value) == 64
            and all(c in '0123456789abcdef' for c in value))


def validate_planning_identity(identity):
    required = set(_PIN_FIELDS) | {'study_sha256', 'budget', 'config', 'mode',
        'research_questions', 'max_work_items_per_batch', 'planner_version', 'context_policy'}
    if (type(identity) is not dict or set(identity) != required
            or identity['planner_version'] != VERSION
            or any(not _pin(identity[k]) for k in (*_PIN_FIELDS, 'study_sha256'))
            or type(identity['config']) is not dict or not identity['config']
            or type(identity['research_questions']) is not list
            or any(type(q) is not str or not q.strip() for q in identity['research_questions'])
            or identity['mode'] not in {'open', 'guided'}
            or (identity['mode'] == 'guided' and not identity['research_questions'])
            or type(identity['max_work_items_per_batch']) is not int
            or identity['max_work_items_per_batch'] < 1
            or identity['context_policy'] not in (LEGACY_CONTEXT_POLICY,CONTEXT_POLICY)):
        raise ValueError('A complete verified planning identity is required.')
    try:
        TokenBudget(**identity['budget'])
        exact = json.loads(json.dumps(identity, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError) as error:
        raise ValueError('Planning identity must contain valid budgets and exact JSON settings.') from error
    if exact != identity:
        raise ValueError('Planning configuration must round-trip as exact JSON.')


def planning_identity(study, *, budget, model_sha256, executable_sha256,
                      template_sha256, tokenizer_sha256, config,
                      contract_sha256, mode='open', research_questions=(),
                      max_work_items_per_batch=1,context_policy=CONTEXT_POLICY):
    """Bind source, full settings and contract, not just a model filename."""
    validate_study(study)
    if not isinstance(budget, TokenBudget):
        raise ValueError('Planning identity requires TokenBudget.')
    if (mode not in {'open', 'guided'} or type(research_questions) not in (list, tuple)
            or any(type(q) is not str or not q.strip() for q in research_questions)
            or (mode == 'guided' and not research_questions)):
        raise ValueError('Supply explicit coding mode and research questions.')
    if type(config) is not dict or not config:
        raise ValueError('Planning identity requires the complete runtime configuration.')
    if type(max_work_items_per_batch) is not int or max_work_items_per_batch < 1:
        raise ValueError('Planning item limit must be positive.')
    identity = dict(study_sha256=study['study_sha256'], budget=budget.as_dict(),
        model_sha256=model_sha256, executable_sha256=executable_sha256,
        template_sha256=template_sha256, tokenizer_sha256=tokenizer_sha256,
        contract_sha256=contract_sha256, config=deepcopy(config), mode=mode,
        research_questions=list(research_questions),
        max_work_items_per_batch=max_work_items_per_batch,
        planner_version=VERSION,
        context_policy=context_policy)
    validate_planning_identity(identity)
    return identity


def _native(record):
    try:
        value = NativePrompt(record['rendered_prompt'], record['token_ids'],
            record['template_sha256'], record['tokenizer_sha256'], record['request_sha256'])
    except (KeyError, TypeError) as error:
        raise ValueError('Invalid native measurement record.') from error
    if value.as_dict() != record:
        raise ValueError('Native measurement record is stale or incomplete.')
    return value


class PlanningCheckpoint:
    """Bounded atomic persistence, strict identity checks and cache replay."""

    def __init__(self, identity, state=None):
        self._identity = deepcopy(identity)
        validate_planning_identity(identity)
        if state is None:
            state = dict(version=VERSION, identity=deepcopy(identity), status='pending',
                         reason='', measurements={}, completed_plan_sha256=None)
            state['checkpoint_sha256'] = digest(state)
        self._state = deepcopy(state)
        self._validate()

    def _validate(self):
        state = self._state
        if (type(state) is not dict or state.get('version') != VERSION
                or state.get('identity') != self.identity
                or state.get('checkpoint_sha256') != digest({k: v for k, v in state.items()
                                                            if k != 'checkpoint_sha256'})):
            raise ValueError('Planning checkpoint identity/digest is stale; start a new checkpoint.')
        if (state.get('status') not in _STATES or type(state.get('reason')) is not str
                or (state['status'] == 'interrupted' and not state['reason'].strip())
                or type(state.get('measurements')) is not dict
                or (state['status'] == 'completed') != _pin(state.get('completed_plan_sha256'))
                or (state['status'] != 'completed' and state.get('completed_plan_sha256') is not None)):
            raise ValueError('Planning checkpoint status is incomplete.')
        for key, record in state['measurements'].items():
            if (type(record) is not dict or set(record) != {'request', 'native_prompt'}
                    or key != digest(record['request'])):
                raise ValueError('Cached measurement request binding is stale.')
            native = _native(record['native_prompt'])
            if (native.request_sha256 != key
                    or native.template_sha256 != self.identity['template_sha256']
                    or native.tokenizer_sha256 != self.identity['tokenizer_sha256']):
                raise ValueError('Cached native measurement identity changed.')

    def snapshot(self):
        state = deepcopy(self._state)
        state['checkpoint_sha256'] = digest({k: v for k, v in state.items()
                                             if k != 'checkpoint_sha256'})
        return state

    def validate(self):
        """Validate the current snapshot without exposing mutable internal state."""
        validate_planning_identity(self.identity)
        PlanningCheckpoint(self.identity, self.snapshot())

    @property
    def identity(self):
        return deepcopy(self._identity)

    @property
    def status(self):
        return self._state['status']

    @property
    def reason(self):
        return self._state['reason']

    @property
    def measurement_count(self):
        return len(self._state['measurements'])

    def save(self, path, *, max_bytes=MAX_CHECKPOINT_BYTES):
        """Replace only the requested checkpoint after bounded serialization."""
        if type(max_bytes) is not int or not 0 < max_bytes <= MAX_CHECKPOINT_BYTES:
            raise ValueError('Checkpoint byte bound must be within 64 MiB.')
        data = json.dumps(self.snapshot(), ensure_ascii=False, allow_nan=False,
                          sort_keys=True, separators=(',', ':')).encode('utf-8')
        if len(data) > max_bytes:
            raise ValueError('Checkpoint exceeds byte bound; nothing was truncated or replaced.')
        path = Path(path)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='wb', dir=path.parent,
                    prefix=path.name + '.', suffix='.tmp', delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()

    @classmethod
    def load(cls, path, expected_identity, *, max_bytes=MAX_CHECKPOINT_BYTES):
        if type(max_bytes) is not int or not 0 < max_bytes <= MAX_CHECKPOINT_BYTES:
            raise ValueError('Checkpoint byte bound must be within 64 MiB.')
        with Path(path).open('rb') as stream:
            data = stream.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise ValueError('Checkpoint exceeds byte bound.')
        try:
            state = json.loads(data)
        except (ValueError, UnicodeError) as error:
            raise ValueError('Invalid planning checkpoint JSON.') from error
        return cls(expected_identity, state)

    def measure(self, request, callback):
        """Return the exact cached native value or accept one validated success."""
        key = digest(request)
        prior = self._state['measurements'].get(key)
        if prior is not None:
            if prior['request'] != request:
                raise ValueError('Cached request differs from the current request.')
            return _native(prior['native_prompt'])
        value = callback(deepcopy(request))
        if (not isinstance(value, NativePrompt) or value.request_sha256 != key
                or value.template_sha256 != self.identity['template_sha256']
                or value.tokenizer_sha256 != self.identity['tokenizer_sha256']):
            raise ValueError('Refusing unbound or mixed native measurement identities.')
        self._state['measurements'][key] = dict(request=deepcopy(request), native_prompt=value.as_dict())
        return value

    def run(self, study, *, native_prompt_callback, budget, path=None,
            wall_seconds=600, max_native_measurements=100, cancel_event=None,
            on_progress=None, max_work_items_per_batch=1):
        """Replay cached planning and stop before exceeding new-measurement limits.

        Returns None on interruption, with all source work still in the original
        study and successful measurements persisted. The native caller must also
        enforce the same wall deadline inside an individual blocking native call.
        """
        validate_study(study)
        if (study['study_sha256'] != self.identity['study_sha256']
                or budget.as_dict() != self.identity['budget']
                or max_work_items_per_batch != self.identity['max_work_items_per_batch']):
            raise ValueError('Planning source or settings differ from checkpoint identity.')
        if (type(wall_seconds) not in (int, float) or not math.isfinite(wall_seconds)
                or not 0 < wall_seconds <= 3600
                or (max_native_measurements is not None and
                    (type(max_native_measurements) is not int or max_native_measurements < 1))):
            raise ValueError('Planning work period requires bounded wall time and measurement count.')
        started = time.monotonic()
        new_count = 0

        def persist():
            if path is not None:
                self.save(path)

        def check():
            if cancel_event is not None and cancel_event.is_set():
                raise PlanningInterrupted('Planning cancelled with unmeasured requests pending.')
            if time.monotonic() - started >= wall_seconds:
                raise PlanningInterrupted('Planning work-period wall limit reached; requests remain pending.')

        def measure(request):
            nonlocal new_count
            check()
            key = digest(request)
            if key not in self._state['measurements']:
                if max_native_measurements is not None and new_count >= max_native_measurements:
                    raise PlanningInterrupted('Planning work-period measurement limit reached; requests remain pending.')
                try:
                    value = self.measure(request, native_prompt_callback)
                except (RuntimeError, OSError) as error:
                    raise PlanningInterrupted('Native accounting interrupted: ' + str(error)) from error
                new_count += 1
                try:
                    persist()
                    if on_progress is not None:
                        on_progress(dict(stage='native_planning', requests_measured=self.measurement_count,
                            new_measurements=new_count, elapsed_seconds=time.monotonic() - started))
                except (RuntimeError, OSError) as error:
                    # The successful native value is already cached. Storage or
                    # notification failure is not failure of the source passage.
                    raise PlanningInterrupted('Planning checkpoint/progress interrupted: ' + str(error)) from error
                check()
                return value
            return self.measure(request, native_prompt_callback)

        previous_plan = self._state['completed_plan_sha256']
        self._state.update(status='pending', reason='', completed_plan_sha256=None)
        persist()
        try:
            check()
            plan = plan_batches(study, native_prompt_callback=measure, budget=budget,
                                max_work_items_per_batch=max_work_items_per_batch,
                                context_transport_policy=(CONTEXT_TRANSPORT_POLICY if self.identity['context_policy']==CONTEXT_POLICY else None))
            check()
            if previous_plan is not None and plan['plan_sha256'] != previous_plan:
                raise ValueError('Completed checkpoint replay differs from the original plan.')
        except PlanningInterrupted as error:
            self._state.update(status='interrupted', reason=str(error) or 'Planning interrupted.')
            persist()
            return None
        except Exception as error:
            self._state.update(status='interrupted', reason='Planning stopped: ' + str(error))
            persist()
            raise
        self._state.update(status='completed', reason='', completed_plan_sha256=plan['plan_sha256'])
        persist()
        return plan

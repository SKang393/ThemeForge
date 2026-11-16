"""Whole-study source/execution coverage, never a claim of exhaustive coding.

No model is loaded here. The caller's native renderer/tokenizer is authoritative:
it must count the exact request that execution will send, including the template,
special tokens and assistant prefix. A character/word estimate is not accepted.
"""
from collections import Counter
from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
import json
import re

from .discovery_units import build_meaning_units

VERSION = 'themeforge.study-batches.v1'
CONTEXT_TRANSPORT_POLICY = 'source-unit-saved-speaker.v2'
STATUSES = ('pending', 'completed', 'failed', 'skipped', 'unassigned')
_HASH = re.compile(r'^[0-9a-f]{64}$')


class PlanningInterrupted(Exception):
    """A resumable stop, never a failed source unit or a complete plan."""


_PREAMBLE_TITLE = re.compile(
    r'(?:Participant\s+#?\d+\s+Interview\s+Transcription(?:,\s*[A-Za-z][A-Za-z &-]*)?'
    r'|Focus\s+Group\s+Transcript(?:ion)?(?:,\s*[A-Za-z][A-Za-z &-]*)?'
    r'|[A-Za-z][A-Za-z &-]*\s+Focus\s+Group(?:\s+Transcription'
    r'|,\s*(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2}))',
    re.IGNORECASE,
)
_PREAMBLE_FIELD = re.compile(
    r'(?:Interview\s+conducted:\s*\d{1,2}/\d{1,2}/\d{4}'
    r'|Total\s+focus\s+group\s+time\s+(?:allotted|used):\s*\d+\s+minutes(?:\s+\(\d+\s+recorded\))?'
    r'|Introductions?:?|\[redacted\])', re.IGNORECASE,
)


def _source_preamble_metadata(unit, source_text):
    """Recognize only complete cover fields before any attributed source turn."""
    if (unit['speaker'].casefold() not in {'unknown', 'unknown speaker'}
            or source_text[:unit['source_start']].strip()):
        return False
    lines = [line.strip() for line in unit['text'].splitlines() if line.strip()]
    return (len(lines) >= 2 and _PREAMBLE_TITLE.fullmatch(lines[0]) is not None
            and all(_PREAMBLE_FIELD.fullmatch(line) is not None for line in lines[1:]))


def digest(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                            separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _seal(value, field):
    value[field] = digest({k: v for k, v in value.items() if k != field})
    return value


@dataclass(frozen=True)
class TokenBudget:
    context_window_tokens: int
    generation_tokens: int
    eos_tokens: int = 1

    def __post_init__(self):
        for value in (self.context_window_tokens, self.generation_tokens, self.eos_tokens):
            if type(value) is not int or value < 1:
                raise ValueError('Token budgets require positive integers, including EOS reserve.')
        if self.context_window_tokens <= self.generation_tokens + self.eos_tokens:
            raise ValueError('Context window must leave room for native prompt tokens.')

    def as_dict(self):
        return dict(context_window_tokens=self.context_window_tokens,
                    generation_tokens=self.generation_tokens, eos_tokens=self.eos_tokens)


@dataclass(frozen=True)
class NativePrompt:
    rendered_prompt: str
    token_ids: tuple
    template_sha256: str
    tokenizer_sha256: str
    request_sha256: str

    def __post_init__(self):
        if type(self.rendered_prompt) is not str or not self.rendered_prompt:
            raise ValueError('Native accounting requires the exact rendered prompt.')
        if type(self.token_ids) not in (list, tuple) or not self.token_ids:
            raise ValueError('Native accounting requires actual prompt token IDs, not an estimate.')
        if any(type(t) is not int or t < 0 for t in self.token_ids):
            raise ValueError('Native prompt IDs must be nonnegative integers.')
        for pin in (self.template_sha256, self.tokenizer_sha256, self.request_sha256):
            if type(pin) is not str or not _HASH.fullmatch(pin):
                raise ValueError('Native accounting requires template/tokenizer/request SHA256 pins.')
        object.__setattr__(self, 'token_ids', tuple(self.token_ids))

    def as_dict(self):
        return dict(rendered_prompt=self.rendered_prompt,
                    rendered_prompt_sha256=sha256(self.rendered_prompt.encode()).hexdigest(),
                    token_ids=list(self.token_ids), prompt_tokens=len(self.token_ids),
                    template_sha256=self.template_sha256, tokenizer_sha256=self.tokenizer_sha256,
                    request_sha256=self.request_sha256,
                    accounting='injected_native_render_and_tokenize_not_estimated')


def _span(source, start, end, **extra):
    return dict(source_name=source['source_name'], source_hash=source['source_hash'],
                source_start=start, source_end=end, text=source['text'][start:end], **extra)


def _prove(span, sources):
    source = sources.get(span.get('source_name'))
    start, end = span.get('source_start'), span.get('source_end')
    if (source is None or span.get('source_hash') != source['source_hash']
            or type(start) is not int or type(end) is not int
            or not 0 <= start < end <= len(source['text'])
            or source['text'][start:end] != span.get('text')):
        raise ValueError('Source span/hash/offset proof failed.')


def _gaps(start, end, included):
    cursor = start
    for left, right in sorted(included):
        left, right = max(start, left), min(end, right)
        if right <= cursor:
            continue
        if left > cursor:
            yield cursor, left
        cursor = max(cursor, right)
    if cursor < end:
        yield cursor, end


def prepare_study(documents, *, speaker_label_policy='explicit_roles'):
    """Import every parsed turn and retain unrouted material without a turn cap."""
    documents = tuple(documents)
    if not documents:
        raise ValueError('Import at least one source document.')
    units = build_meaning_units(documents, speaker_label_policy=speaker_label_policy)
    sources = [dict(source_name=d.name, source_hash=sha256(d.text.encode()).hexdigest(), text=d.text)
               for d in sorted(documents, key=lambda d: d.name)]
    source_texts = {source['source_name']: source['text'] for source in sources}
    for unit in units:
        if _source_preamble_metadata(unit, source_texts[unit['source_name']]):
            unit['flags'].append('source_preamble_metadata')
            unit['eligible'] = False
        unit['status'] = 'pending' if unit['eligible'] else 'unassigned'
    study = _seal(dict(version=VERSION, sources=sources, units=units,
                      speaker_label_policy=speaker_label_policy,
                      coverage_notice='Source/execution coverage only; parsed eligibility is heuristic, not semantic coverage or exhaustive codes.'),
                  'study_sha256')
    validate_study(study)
    return study


def validate_study(study):
    if (type(study) is not dict or study.get('version') != VERSION
            or study.get('study_sha256') != digest({k: v for k, v in study.items() if k != 'study_sha256'})):
        raise ValueError('Study digest/version is stale.')
    sources = {}
    for source in study['sources']:
        name = source['source_name']
        if (type(name) is not str or not name or name in sources
                or type(source['text']) is not str
                or sha256(source['text'].encode()).hexdigest() != source['source_hash']):
            raise ValueError('Source names/hashes must be unique and exact.')
        sources[name] = source
    if not sources:
        raise ValueError('Study has no sources.')
    seen = set()
    prior_end = {}
    for unit in study['units']:
        _prove(unit, sources)
        if (type(unit.get('id')) is not str or not unit['id'] or unit['id'] in seen
                or type(unit.get('eligible')) is not bool or unit.get('status') not in STATUSES):
            raise ValueError('Invalid original unit identity, eligibility or status.')
        if unit['source_start'] < prior_end.get(unit['source_name'], 0):
            raise ValueError('Original unit spans overlap or are not source ordered.')
        prior_end[unit['source_name']] = unit['source_end']
        seen.add(unit['id'])
        _prove(dict(source_name=unit['source_name'], source_hash=unit['source_hash'],
                    source_start=unit['context_start'], source_end=unit['context_end'], text=unit['context_text']), sources)
        if 'execution_context_spans' in unit:
            if (unit.get('execution_context_policy')!='boundary_recovery_adjacent_context_only'
                    or type(unit['execution_context_spans']) is not list or len(unit['execution_context_spans'])>2):
                raise ValueError('Boundary recovery needs an explicit policy and at most two exact context spans.')
            for span in unit['execution_context_spans']:
                _prove(span,sources)
                if span['source_name']!=unit['source_name'] or not (span['source_end']<=unit['source_start'] or span['source_start']>=unit['source_end']):
                    raise ValueError('Boundary execution context must be separate from its core in the same source.')


def _contexts(study, unit, start):
    """Explicit bounded selection policy; the full archive window stays untouched."""
    sources = {s['source_name']: s for s in study['sources']}
    source = sources[unit['source_name']]
    if 'execution_context_spans' in unit:
        # These exact spans are part of the sealed source branch. Keep them
        # intact even if the core later splits; a failed native fit stays visible.
        return deepcopy(unit['execution_context_spans'])
    previous = [u for u in study['units'] if u['source_name'] == unit['source_name']
                and u['source_end'] <= unit['source_start']]
    chosen = []
    question = next((u for u in reversed(previous)
                     if u['text'] == unit.get('reference_context') and unit.get('reference_context')
                     and any(f in u.get('flags', []) for f in ('interviewer', 'unattributed_prompt'))), None)
    if question:
        chosen.append(_span(source, question['source_start'], question['source_end'], role='nearest_question_referent_only'))
    if start > unit['source_start']:
        sentences = [s for s in unit.get('sentences', []) if s['source_end'] <= start]
        if sentences:
            sentence = sentences[-1]
            chosen.append(_span(source, sentence['source_start'], sentence['source_end'], role='preceding_parent_sentence_referent_only'))
    elif previous:
        p = previous[-1]
        chosen.append(_span(source, p['source_start'], p['source_end'], role='preceding_turn_referent_only'))
    unique = {}
    for span in chosen:
        unique.setdefault((span['source_name'], span['source_start'], span['source_end']), span)
    return list(unique.values())


def _context_source_identity(span, study):
    """Resolve only a uniquely containing original source unit, never a person."""
    matches=[u for u in study['units'] if u['source_name']==span['source_name']
        and u['source_hash']==span['source_hash']
        and u['source_start']<=span['source_start']<span['source_end']<=u['source_end']]
    return dict(source_unit_id=matches[0]['id'],speaker=matches[0]['speaker']) if len(matches)==1 else dict(source_unit_id=None,speaker=None)


def _context_with_source_identity(span, study):
    value=deepcopy(span);identity=_context_source_identity(value,study)
    if any(k in value and value[k]!=v for k,v in identity.items()):
        raise ValueError('Selected context identity differs from its exact original source unit.')
    value.update(identity)
    return value


def _request(items, budget):
    fields = ('id', 'parent_unit_id', 'source_name', 'source_hash', 'speaker', 'source_start',
              'source_end', 'text', 'parent_source_start', 'parent_source_end', 'context_spans')
    return dict(targets=[{k: deepcopy(item[k]) for k in fields} for item in items],
                generation_reserve_tokens=budget.generation_tokens, eos_reserve_tokens=budget.eos_tokens,
                context_policy='explicit_selected_context_not_additional_target_evidence',
                instruction_scope='Fragments do not establish exhaustive codes or whole-turn meaning coverage.')


def plan_batches(study, *, native_prompt_callback, budget, max_work_items_per_batch=1,
                 context_selector=None, context_transport_policy=CONTEXT_TRANSPORT_POLICY):
    """Deterministic requests with exact full-native accounting and no truncation.

    callback(request)->NativePrompt must bind digest(request). A custom context
    selector(unit, fragment, study)->exact spans replaces the stated default
    policy explicitly. Selected contexts are never trimmed to fit. Sentence
    splitting is syntactic; parent assertions outside the fragment are archived,
    not silently supplied or claimed semantically resolved.
    """
    validate_study(study)
    if context_transport_policy not in (None,CONTEXT_TRANSPORT_POLICY):
        raise ValueError('Unknown context speaker transport policy.')
    if not callable(native_prompt_callback) or not isinstance(budget, TokenBudget):
        raise ValueError('Supply a native renderer/tokenizer callback and TokenBudget.')
    if type(max_work_items_per_batch) is not int or max_work_items_per_batch < 1:
        raise ValueError('Per-request work item limit must be positive; it is not a study cap.')
    sources = {s['source_name']: s for s in study['sources']}
    work, batches, issues, cache = [], [], [], {}
    identity = None

    def measure(items):
        nonlocal identity
        request = _request(items, budget)
        pin = digest(request)
        if pin not in cache:
            value = native_prompt_callback(deepcopy(request))
            if not isinstance(value, NativePrompt) or value.request_sha256 != pin:
                raise ValueError('Refusing estimated/unbound prompt accounting; return request-bound NativePrompt.')
            current = (value.template_sha256, value.tokenizer_sha256)
            if identity is not None and current != identity:
                raise ValueError('Native template/tokenizer identity changed during planning.')
            identity = current
            cache[pin] = value.as_dict()
        native = cache[pin]
        return request, native, native['prompt_tokens'] + budget.generation_tokens + budget.eos_tokens <= budget.context_window_tokens

    def item_for(unit, start, end):
        source = sources[unit['source_name']]
        item = _span(source, start, end, speaker=unit['speaker'],
                     id='SW-' + digest([unit['id'], unit['source_hash'], start, end])[:20],
                     parent_unit_id=unit['id'], parent_source_start=unit['source_start'], parent_source_end=unit['source_end'])
        context = (context_selector(deepcopy(unit), deepcopy(item), deepcopy(study))
                   if context_selector is not None else _contexts(study, unit, start))
        if type(context) is not list:
            raise ValueError('Context selection must be an explicit exact-span list.')
        for span in context:
            _prove(span, sources)
        if context_transport_policy==CONTEXT_TRANSPORT_POLICY:
            context=[_context_with_source_identity(span,study) for span in context]
        item.update(context_spans=deepcopy(context), status='pending', reason='', result_refs=[], code_refs=[], evidence_refs=[],
                    context_policy='caller_explicit_selection' if context_selector else unit.get('execution_context_policy','nearest_question_and_preceding_turn_or_parent_sentence'),
                    context_selection_notice='Complete native archive window remains in study; execution context is the explicit subset, not guaranteed referent-complete.',
                    omitted_archive_context_spans=[dict(source_name=source['source_name'], source_hash=source['source_hash'], source_start=a, source_end=b,
                        text_sha256=sha256(source['text'][a:b].encode()).hexdigest())
                        for a, b in _gaps(unit['context_start'], unit['context_end'],
                            [(start, end)] + [(s['source_start'], s['source_end']) for s in context if s['source_name'] == unit['source_name']])],
                    is_fragment=(start, end) != (unit['source_start'], unit['source_end']))
        return item

    def split(unit, start, end):
        item = item_for(unit, start, end)
        try:
            _, native, fits = measure([item])
        except TimeoutError as error:
            raise PlanningInterrupted(str(error)) from error
        except (RuntimeError, OSError) as error:
            item.update(status='failed', reason='native_accounting_failed: ' + str(error))
            work.append(item)
            return
        if fits:
            work.append(item)
            return
        cuts = sorted({s['source_end'] for s in unit.get('sentences', []) if start < s['source_end'] < end
                       and sources[unit['source_name']]['text'][s['source_end']:end].strip()})
        if not cuts:
            item.update(status='failed', reason='oversized_with_fixed_context_no_further_sentence_boundary',
                        rejected_native_prompt=native, rejected_total_tokens=native['prompt_tokens'] + budget.generation_tokens + budget.eos_tokens)
            work.append(item)
            return
        middle = cuts[len(cuts) // 2]
        split(unit, start, middle)
        split(unit, middle, end)

    for unit in study['units']:
        if unit['eligible']:
            split(unit, unit['source_start'], unit['source_end'])

    current = []

    def flush():
        if not current:
            return
        request, native, fits = measure(current)
        if not fits:
            raise ValueError('Internal batching budget invariant failed.')
        batches.append(dict(id='SB-' + digest([i['id'] for i in current])[:20],
                            work_item_ids=[i['id'] for i in current], request=request,
                            native_prompt=native, budget=budget.as_dict(),
                            reserved_total_tokens=native['prompt_tokens'] + budget.generation_tokens + budget.eos_tokens,
                            execution_requires_same_native_request=True))
        current.clear()

    for item in work:
        if item['status'] != 'pending':
            flush()
            continue
        if len(current) >= max_work_items_per_batch:
            flush()
        try:
            _, _, fits = measure(current + [item])
        except TimeoutError as error:
            raise PlanningInterrupted(str(error)) from error
        except (RuntimeError, OSError) as error:
            issues.append(dict(work_item_id=item['id'], reason='Combined request accounting failed; retaining measured single request: ' + str(error)))
            fits = False
        if not fits:
            flush()
        current.append(item)
    flush()
    plan = _seal(dict(version=VERSION, study=deepcopy(study), work_items=work, batches=batches,
                      budget=budget.as_dict(), max_work_items_per_batch=max_work_items_per_batch,
                      native_identity=list(identity) if identity else None,
                      planning_issues=issues, history=[],
                      coverage_notice='Execution/source coverage only. No silent target/context truncation, semantic completeness or exhaustive codes claimed.'), 'plan_sha256')
    if context_transport_policy is not None:
        plan['context_transport_policy']=context_transport_policy
        plan['context_speaker_notice']='Saved source-unit labels only, not validated person identities; null means no unique containing unit.'
        _seal(plan,'plan_sha256')
    validate_plan(plan)
    return plan


def validate_plan(plan):
    if plan.get('version') != VERSION or plan.get('plan_sha256') != digest({k: v for k, v in plan.items() if k != 'plan_sha256'}):
        raise ValueError('Plan digest/version is stale.')
    validate_study(plan['study'])
    transport=plan.get('context_transport_policy')
    if transport not in (None,CONTEXT_TRANSPORT_POLICY):
        raise ValueError('Unknown context speaker transport policy.')
    budget = TokenBudget(**plan['budget'])
    sources = {s['source_name']: s for s in plan['study']['sources']}
    units = {u['id']: u for u in plan['study']['units']}
    work = {}
    for item in plan['work_items']:
        _prove(item, sources)
        parent = units[item['parent_unit_id']]
        if (item['id'] in work or item['status'] not in STATUSES or not parent['eligible']
                or item['source_name'] != parent['source_name'] or item['speaker'] != parent['speaker']
                or (item['parent_source_start'], item['parent_source_end']) != (parent['source_start'], parent['source_end'])
                or item['id'] != 'SW-' + digest([parent['id'], parent['source_hash'], item['source_start'], item['source_end']])[:20]
                or not parent['source_start'] <= item['source_start'] < item['source_end'] <= parent['source_end']):
            raise ValueError('Invalid work identity, source parent or status.')
        if (type(item['reason']) is not str
                or any(type(item[k]) is not list or any(type(r) is not str or not r.strip() for r in item[k])
                       for k in ('result_refs', 'code_refs', 'evidence_refs'))
                or (item['status'] == 'completed' and not item['result_refs'])
                or (item['status'] in ('failed', 'skipped', 'unassigned') and not item['reason'].strip())):
            raise ValueError('Work outcome/provenance record is incomplete.')
        for span in item['context_spans']:
            _prove(span, sources)
            if transport==CONTEXT_TRANSPORT_POLICY:
                identity=_context_source_identity(span,plan['study'])
                if any(k not in span or span[k]!=v for k,v in identity.items()):
                    raise ValueError('Context speaker/source-unit metadata differs from its exact source.')
        work[item['id']] = item
    for unit in units.values():
        children = sorted((i for i in work.values() if i['parent_unit_id'] == unit['id']), key=lambda i: i['source_start'])
        cursor = unit['source_start']
        for child in children:
            if child['source_start'] != cursor:
                raise ValueError('Fragment coverage has a gap or overlap.')
            cursor = child['source_end']
        if unit['eligible'] and cursor != unit['source_end']:
            raise ValueError('An eligible original unit is not fully represented in the work ledger.')
    assigned = []
    for batch in plan['batches']:
        items = [work[uid] for uid in batch['work_item_ids']]
        if (not items or len(items) > plan['max_work_items_per_batch']
                or batch['request'] != _request(items, budget) or batch['budget'] != budget.as_dict()):
            raise ValueError('Batch request/source binding changed.')
        n = batch['native_prompt']
        native = NativePrompt(n['rendered_prompt'], n['token_ids'], n['template_sha256'], n['tokenizer_sha256'], n['request_sha256'])
        if native.as_dict() != n or n['request_sha256'] != digest(batch['request']):
            raise ValueError('Native prompt accounting record is stale.')
        if [n['template_sha256'], n['tokenizer_sha256']] != plan['native_identity']:
            raise ValueError('Batch native template/tokenizer identity differs.')
        total = len(native.token_ids) + budget.generation_tokens + budget.eos_tokens
        if total != batch['reserved_total_tokens'] or total > budget.context_window_tokens:
            raise ValueError('Native prompt plus generation/EOS reserve exceeds the fixed budget.')
        assigned.extend(batch['work_item_ids'])
    if len(assigned) != len(set(assigned)) or any(i['status'] in ('pending', 'completed') and i['id'] not in assigned for i in work.values()):
        raise ValueError('Pending/completed work must have exactly one execution request.')


def record_outcome(plan, work_item_id, status, *, reason='', result_refs=(), code_refs=(), evidence_refs=()):
    """Return an auditable branch; completion is caller-asserted execution only."""
    validate_plan(plan)
    if (status not in STATUSES or type(reason) is not str
            or (status not in ('pending', 'completed') and not reason.strip())):
        raise ValueError('Choose a valid outcome and explain failures/skips/unassignment.')
    if any(isinstance(r, (str, bytes)) for r in (result_refs, code_refs, evidence_refs)):
        raise ValueError('Supply reference lists, not a string.')
    refs = [list(result_refs), list(code_refs), list(evidence_refs)]
    if any(any(type(r) is not str or not r.strip() for r in group) for group in refs):
        raise ValueError('Result/code/evidence links must be explicit nonblank strings.')
    if status == 'completed' and not refs[0]:
        raise ValueError('Completed execution needs an actual result reference; codes may be empty.')
    updated = deepcopy(plan)
    item = next((i for i in updated['work_items'] if i['id'] == work_item_id), None)
    if item is None:
        raise ValueError('Unknown work item.')
    before = {k: deepcopy(item[k]) for k in ('status', 'reason', 'result_refs', 'code_refs', 'evidence_refs')}
    item.update(status=status, reason=reason, result_refs=refs[0], code_refs=refs[1], evidence_refs=refs[2])
    updated['history'].append(dict(event='execution_outcome_recorded', work_item_id=work_item_id,
        before_plan_sha256=plan['plan_sha256'], before=before,
        after={k: deepcopy(item[k]) for k in before}, notice='Caller-asserted execution/provenance, not semantic acceptance.'))
    _seal(updated, 'plan_sha256')
    validate_plan(updated)
    return updated


def coverage_report(plan):
    validate_plan(plan)
    unit_rows = []
    for unit in plan['study']['units']:
        children = [i for i in plan['work_items'] if i['parent_unit_id'] == unit['id']]
        states = Counter(i['status'] for i in children)
        if not unit['eligible']:
            status = 'unassigned'
        elif states.get('pending'):
            status = 'pending'
        elif states.get('failed'):
            status = 'failed'
        elif children and states.get('completed') == len(children):
            status = 'completed'
        elif states.get('unassigned'):
            status = 'unassigned'
        else:
            status = 'skipped'
        unit_rows.append(dict(unit_id=unit['id'], source_name=unit['source_name'], source_hash=unit['source_hash'],
            source_start=unit['source_start'], source_end=unit['source_end'], status=status,
            fragment_status_counts=dict(states), completed_target_characters=sum(i['source_end'] - i['source_start'] for i in children if i['status'] == 'completed'),
            target_characters=unit['source_end'] - unit['source_start']))
    sources = []
    for source in plan['study']['sources']:
        rows = [r for r in unit_rows if r['source_name'] == source['source_name']]
        gaps = list(_gaps(0, len(source['text']), [(r['source_start'], r['source_end']) for r in rows]))
        sources.append(dict(source_name=source['source_name'], source_hash=source['source_hash'],
            source_characters=len(source['text']), unit_status_counts=dict(Counter(r['status'] for r in rows)),
            completed_target_characters=sum(r['completed_target_characters'] for r in rows),
            outside_parsed_units=[dict(source_start=a, source_end=b, status='unassigned') for a, b in gaps]))
    return dict(study_sha256=plan['study']['study_sha256'], plan_sha256=plan['plan_sha256'],
                unit_status_counts=dict(Counter(r['status'] for r in unit_rows)),
                work_status_counts=dict(Counter(i['status'] for i in plan['work_items'])),
                units=unit_rows, sources=sources,
                notice='Completed means caller-recorded execution, including zero-code results; this is not exhaustive coding, semantic coverage, acceptance or independent participant coverage.')

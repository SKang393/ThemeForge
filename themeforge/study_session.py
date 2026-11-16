"""Whole-study sequential coding with native budgets and persistent coverage.

The per-call code contract stays bounded. The import and pending queue do not
have a 12/20-turn cap. Every output is an unreviewed machine draft.
"""
from copy import deepcopy
from dataclasses import asdict
from hashlib import sha256
from html import escape
import json
from pathlib import Path
import threading
import time

from .study_batches import (NativePrompt, TokenBudget, prepare_study, plan_batches,
                           validate_plan, coverage_report, record_outcome, digest, PlanningInterrupted)
from .local_inference import InferenceCancelled,InferenceLimitError
from .local_codebook import _FOCUSED_DECISION, _FOCUSED_SCOPE, _messages
from .codebook_contracts import multi_decision_schema, validate_multi_decision, grounded_scope_schema, validate_grounded_scope
from .discovery_units import _sentences
from .codebook_meaning import review_meaning
from . import scope_evidence


def coding_request(request, *, mode='open', research_questions=()):
    if mode not in {'open','guided'} or (mode == 'guided' and not research_questions):
        raise ValueError('Guided coding requires research questions.')
    if len(request['targets']) != 1:
        raise ValueError('Current multi-code contract uses one target per request; whole-study queue has no turn cap.')
    target = request['targets'][0]
    spans = _sentences(target['text'], target['source_start'])
    if not spans:
        spans = [dict(source_start=target['source_start'], source_end=target['source_end'], text=target['text'])]
    evidence = {f'e{i}':dict(s,source_name=target['source_name'],source_hash=target['source_hash'],
                           unit_id=target['parent_unit_id'],work_item_id=target['id']) for i,s in enumerate(spans,1)}
    references = {f'r{i}':deepcopy(c) for i,c in enumerate(target['context_spans'],1)}
    # The contract bounds referent selections to two, not how many original turns are imported.
    if len(references)>2:
        raise ValueError('The current code contract permits at most two explicit referent contexts; do not silently trim.')
    payload = dict(mode=mode,research_questions=list(research_questions) if mode=='guided' else [],
        participant_turn=target['text'], target_speaker=target['speaker'],
        evidence=[dict(id=k,text=v['text']) for k,v in evidence.items()],
        referent_context_not_support=[dict(id=k,**v) for k,v in references.items()],
        maximum_codes=min(3,len(evidence)),
        context_policy='Exact selected question/preceding turn or prior fragment sentence only; full parent and archive remain in study ledger. Context cannot add an assertion.',
        fragment_scope=dict(parent_unit_id=target['parent_unit_id'],parent_source_start=target['parent_source_start'],
                            parent_source_end=target['parent_source_end'],source_start=target['source_start'],source_end=target['source_end']))
    messages = _messages(_FOCUSED_DECISION,payload)
    schema = multi_decision_schema(list(evidence),list(references),payload['maximum_codes'])
    return dict(messages=messages,schema=schema,evidence=evidence,references=references,
                maximum_codes=payload['maximum_codes'],target=target,request_sha256=digest(dict(messages=messages,schema=schema)))


def build_native_plan(study, client, *, mode='open', research_questions=(), cancel_event=None, on_progress=None):
    count=0; requests={}; started=time.perf_counter()
    def measure(request):
        nonlocal count
        if cancel_event is not None and cancel_event.is_set():
            raise PlanningInterrupted('Planning cancelled; unmeasured work remains pending.')
        actual=coding_request(request,mode=mode,research_questions=research_questions)
        try:
            native=client.measure_messages(actual['messages'],cancel_event=cancel_event)
        except (InferenceCancelled,InferenceLimitError) as error:
            raise PlanningInterrupted(str(error)) from error
        requests[digest(request)]=actual
        count+=1
        if on_progress:
            on_progress(dict(stage='native_planning',requests_measured=count,elapsed_seconds=time.perf_counter()-started))
        return NativePrompt(native['rendered_prompt'],native['token_ids'],native['template_sha256'],
                            native['tokenizer_sha256'],digest(request))
    limits=client.config.limits
    plan=plan_batches(study,native_prompt_callback=measure,
        budget=TokenBudget(limits.context_tokens,limits.output_tokens,1),max_work_items_per_batch=1)
    return dict(version='themeforge.study-session.v1',plan=plan,
        requests={batch['id']:requests[digest(batch['request'])] for batch in plan['batches']},
        mode=mode,research_questions=list(research_questions),
        model_sha256=client.config.model_sha256,executable_sha256=client.config.executable_sha256,
        limits=asdict(limits),calls=[],codes=[],status='planned',scope_contract=scope_evidence.DEFAULT_VERSION,
        profiling=dict(native_planning_seconds=time.perf_counter()-started,native_prompt_measurements=count),
        provenance=dict(origin='local_model_draft',human_review=False,training=False,online_inference=False,
            interpretation_notice='Source and execution accounting do not establish exhaustive meanings, independent participants, categories or themes.'))


PLANNING_SESSION_VERSION='themeforge.study-planning-session.v1'


def validate_planning_session(value):
    from .study_batches import validate_study
    from .study_planning import PlanningCheckpoint
    if value.get('version')!=PLANNING_SESSION_VERSION:raise ValueError('Unknown planning session.')
    validate_study(value['study'])
    cp=PlanningCheckpoint(value['checkpoint']['identity'],value['checkpoint'])
    identity=cp.identity
    if identity['study_sha256']!=value['study']['study_sha256']:raise ValueError('Planning source binding differs.')
    requests=cp.snapshot()['measurements']
    if set(value['application_request_sha256'])!=set(requests):raise ValueError('Planning application request bindings incomplete.')
    for key,record in requests.items():
        actual=coding_request(record['request'],mode=identity['mode'],research_questions=identity['research_questions'])
        if value['application_request_sha256'][key]!=actual['request_sha256']:
            raise ValueError('Application prompt/schema changed; start a separate native planning branch.')
    return value


def build_resumable_plan(study,client,output,*,prior=None,mode='open',research_questions=(),
                         wall_seconds=600,max_native_measurements=1000,cancel_event=None,on_progress=None,on_checkpoint=None):
    """Persist all successful native accounting; interruption is not source failure."""
    from .study_planning import PlanningCheckpoint,planning_identity,CONTEXT_POLICY
    if prior is not None:
        validate_planning_session(prior)
        mode=prior['checkpoint']['identity']['mode'];research_questions=prior['checkpoint']['identity']['research_questions']
    identity=getattr(client,'identity',None)
    if not identity:raise ValueError('Resumable planning requires verified native template/tokenizer identity.')
    limits=client.config.limits;budget=TokenBudget(limits.context_tokens,limits.output_tokens,1)
    contract=digest(dict(request_interface='themeforge.study-coding-request.v1',instruction=_FOCUSED_DECISION,
                         schema='multi_decision_schema.v1'))
    binding=planning_identity(study,budget=budget,model_sha256=client.config.model_sha256,
        executable_sha256=client.config.executable_sha256,template_sha256=identity['template_sha256'],
        tokenizer_sha256=identity['tokenizer_sha256'],config=asdict(limits),contract_sha256=contract,
        mode=mode,research_questions=research_questions,
        context_policy=prior['checkpoint']['identity']['context_policy'] if prior is not None else CONTEXT_POLICY)
    cp=PlanningCheckpoint(binding,prior['checkpoint'] if prior else None)
    output=Path(output).resolve()
    if str(output).startswith(('\\\\','//')):raise ValueError('Planning checkpoint must be local.')
    output.mkdir(parents=True,exist_ok=False)
    started=time.perf_counter()
    def measure(request):
        actual=coding_request(request,mode=mode,research_questions=research_questions)
        native=client.measure_messages(actual['messages'],cancel_event=cancel_event)
        return NativePrompt(native['rendered_prompt'],native['token_ids'],native['template_sha256'],native['tokenizer_sha256'],digest(request))
    try:
        plan=cp.run(study,native_prompt_callback=measure,budget=budget,path=output/'native-checkpoint.json',
            wall_seconds=wall_seconds,max_native_measurements=max_native_measurements,cancel_event=cancel_event,on_progress=on_progress)
    finally:
        snapshot=cp.snapshot()
        envelope=dict(version=PLANNING_SESSION_VERSION,study=deepcopy(study),checkpoint=snapshot,
            application_request_sha256={k:coding_request(r['request'],mode=mode,research_questions=research_questions)['request_sha256']
                                       for k,r in snapshot['measurements'].items()},
            notice='Native accounting only. Interrupted planning leaves unmeasured source work pending; no coding call has run.')
        validate_planning_session(envelope)
        _save(output/'planning-session.json',envelope)
        if on_checkpoint:on_checkpoint(envelope)
    if plan is None:return None
    return dict(version='themeforge.study-session.v1',plan=plan,
        requests={b['id']:coding_request(b['request'],mode=mode,research_questions=research_questions) for b in plan['batches']},
        mode=mode,research_questions=list(research_questions),model_sha256=client.config.model_sha256,
        executable_sha256=client.config.executable_sha256,limits=asdict(limits),calls=[],codes=[],status='planned',
        scope_contract=scope_evidence.DEFAULT_VERSION,
        profiling=dict(native_planning_seconds=time.perf_counter()-started,native_prompt_measurements=cp.measurement_count,
                       planning_resumed=prior is not None,planning_checkpoint=str(output/'planning-session.json')),
        provenance=dict(origin='local_model_draft',human_review=False,training=False,online_inference=False,
            context_transport_policy=binding['context_policy'],
            context_speaker_metadata_available=binding['context_policy']==CONTEXT_POLICY,
            interpretation_notice='Source and execution accounting do not establish semantic accuracy or themes.'))


def _save(path,value):
    with Path(path).open('x',encoding='utf-8') as stream:
        json.dump(value,stream,ensure_ascii=False,indent=2,allow_nan=False)


def _unfinished(result):
    return [c['id'] for c in result['codes'] if c['definition_status'] in {'not_requested','failed','interrupted'}]


def _definition_attempt(result, code, request, item, client, output, cancel, *, contract, explicit_retry=False):
    """Append one raw attempt; an explicit retry never repeats passage coding."""
    candidate=code['original_model']
    if contract==scope_evidence.VERSION:
        messages,schema=scope_evidence.request(item,candidate,request['evidence'],code['referent_context_not_support'])
    elif contract in {'legacy-grounded-v6',scope_evidence.GUARDED_VERSION}:
        payload=dict(participant_turn=item['text'],candidate=candidate,
            evidence=[dict(id=k,text=v['text']) for k,v in request['evidence'].items()],
            referent_context_not_support=code['referent_context_not_support'])
        messages=_messages(_FOCUSED_SCOPE,payload)
        schema=grounded_scope_schema(list(request['evidence']),candidate['evidence_ids'])
    else:
        raise ValueError('Unknown explicit scope contract.')
    previous_attempts=sum(c.get('stage')=='definition' and c.get('code_id')==code['id'] for c in result['calls'])
    before={k:deepcopy(code[k]) for k in ('definition_status','definition_raw','definition_origin','definition_human_review','scope_contract','qualifications','counterexamples') if k in code}
    code.setdefault('definition_history',[]).append(dict(event='AI_definition_attempt_not_human_revision',
        previous=before,call_index=len(result['calls'])+1,scope_contract=contract,explicit_retry=explicit_retry))
    call=dict(stage='definition',code_id=code['id'],batch_id=code['batch_id'],origin='model_raw',
        scope_contract=contract,messages=messages,schema=schema,attempt=previous_attempts+1,explicit_retry=explicit_retry)
    result['calls'].append(call)
    code['scope_contract']=contract
    try:
        client.expected_tokens=None
        native=client.measure_messages(messages,cancel_event=cancel);call['native_prompt']=native
        if len(native['token_ids'])+client.config.limits.output_tokens+1>client.config.limits.context_tokens:
            raise ValueError('Definition plus generation/EOS reserve does not fit; no truncation.')
        client.expected_tokens=native['token_ids']
        raw=client.generate(messages,schema,cancel);call['response']=raw
        if contract==scope_evidence.VERSION:scope=scope_evidence.validate(raw['raw'],request['evidence'],candidate)
        elif contract==scope_evidence.GUARDED_VERSION:scope=scope_evidence.validate_guarded_legacy(raw['raw'],request['evidence'],candidate)
        else:scope=validate_grounded_scope(raw['raw'],list(request['evidence']),candidate['evidence_ids'])
        call.update(status='structure_valid_not_semantic_accuracy',parsed=scope)
        code.update(definition_status=scope['decision'],definition_raw=deepcopy(scope),
                    definition_origin='local_model_retry' if explicit_retry or previous_attempts else 'local_model_draft',definition_human_review=False)
        code.pop('qualifications',None);code.pop('counterexamples',None)
        if scope['decision']=='define':
            q=(scope_evidence.role_ids(scope,'qualification') if contract==scope_evidence.VERSION else scope['qualification_ids'])
            c=(scope_evidence.role_ids(scope,'counterexample') if contract==scope_evidence.VERSION else scope['counterexample_ids'])
            code['qualifications']=[request['evidence'][sid] for sid in q]
            code['counterexamples']=[request['evidence'][sid] for sid in c]
    except Exception as error:
        call.update(status='failed',error=str(error),diagnostics=client.diagnostics())
        code['definition_status']='interrupted' if cancel.is_set() else 'failed'
    finally:
        client.expected_tokens=None
        _save(output/f"call-{len(result['calls']):06d}.json",call)
    return call


def execute_pending(session,client,output,*,wall_seconds=600,cancel_event=None,on_progress=None,define_scope=True,max_generation_calls=None):
    """Continue pending batches without repeating completed calls; fresh output only."""
    if not 1 <= wall_seconds <= 3600:
        raise ValueError('Choose a study work period from 1 to 3600 seconds.')
    if max_generation_calls is not None and (type(max_generation_calls) is not int or not 1<=max_generation_calls<=100):
        raise ValueError('Choose 1..100 generation calls for this period, including definitions.')
    validate_session(session)
    if (client.config.model_sha256 != session['model_sha256'] or client.config.executable_sha256 != session['executable_sha256']
            or asdict(client.config.limits) != session['limits']):
        raise ValueError('Resume requires the same model, runtime and limits. Replan a separate branch to change them.')
    output=Path(output).resolve()
    if str(output).startswith(('\\\\','//')):
        raise ValueError('Study output must be local.')
    output.mkdir(parents=True,exist_ok=False)
    result=deepcopy(session); cancel=cancel_event or threading.Event(); started=time.perf_counter()
    result['status']='running'; _save(output/'starting-session.json',session)
    starting_calls=len(result['calls'])
    within_calls=lambda:max_generation_calls is None or len(result['calls'])-starting_calls<max_generation_calls
    result.setdefault('work_period_limits',[]).append(dict(wall_seconds=wall_seconds,max_generation_calls=max_generation_calls,
        count_scope='attempted passage and definition requests, including failed attempts; successful earlier calls not replayed'))
    client.period_deadline=time.monotonic()+wall_seconds
    try:
        for batch in result['plan']['batches']:
            item=next(i for i in result['plan']['work_items'] if i['id']==batch['work_item_ids'][0])
            if item['status'] != 'pending':
                continue
            if cancel.is_set() or time.perf_counter()-started >= wall_seconds or not within_calls():
                result['status']='cancelled_with_pending' if cancel.is_set() else ('call_budget_with_pending' if not within_calls() else 'time_budget_with_pending')
                break
            request=result['requests'][batch['id']]
            call=dict(batch_id=batch['id'],work_item_id=item['id'],stage='codes',origin='model_raw',request_sha256=request['request_sha256'])
            result['calls'].append(call)
            file=output/f"call-{len(result['calls']):06d}.json"
            try:
                client.expected_tokens=batch['native_prompt']['token_ids']
                response=client.generate(request['messages'],request['schema'],cancel)
                call.update(response=response,status='raw_received')
                decision=validate_multi_decision(response['raw'],list(request['evidence']),list(request['references']),request['maximum_codes'])
                call.update(status='structure_valid_not_semantic_accuracy',parsed=decision)
                code_ids=[]; evidence_refs=[]
                for index,code in enumerate(decision.get('codes',[]),1):
                    identifier=f"{batch['id']}-C{index}"
                    entry=dict(id=identifier,batch_id=batch['id'],work_item_id=item['id'],parent_unit_id=item['parent_unit_id'],
                        source_name=item['source_name'],source_hash=item['source_hash'],speaker=item['speaker'],
                        original_model=deepcopy(code),label=code['label'],claim=code['claim'],
                        evidence=[request['evidence'][sid] for sid in code['evidence_ids']],
                        referent_context_not_support=[request['references'][sid] for sid in code['referent_ids']],
                        definition_status='not_requested',origin='local_model_draft',human_review=False)
                    result['codes'].append(entry); code_ids.append(identifier); evidence_refs.extend(code['evidence_ids'])
                    if (define_scope and within_calls() and time.perf_counter()-started < wall_seconds and not cancel.is_set()
                            and (client.process is not None or getattr(client,'_injected_transport',False))):
                        _definition_attempt(result,entry,request,item,client,output,cancel,
                            contract=result.get('scope_contract','legacy-grounded-v6'))
                status='completed' if code_ids else 'unassigned'
                reason='' if code_ids else decision.get('reason','No codes proposed; not absence of meaningful content.')
                _save(file,call)
                result['plan']=record_outcome(result['plan'],item['id'],status,reason=reason,result_refs=[str(file)],
                    code_refs=code_ids,evidence_refs=evidence_refs)
            except Exception as error:
                call.update(status='failed',error=str(error),diagnostics=client.diagnostics())
                if not file.exists(): _save(file,call)
                result['plan']=record_outcome(result['plan'],item['id'],'failed',reason=str(error),result_refs=[str(file)])
                if client.process is None and not getattr(client,'_injected_transport',False):
                    result['status']='runtime_failed_with_pending'
                    break
            finally:
                client.expected_tokens=None
            result['unfinished_definition_code_ids']=_unfinished(result)
            _save(output/f"checkpoint-{len(result['calls']):06d}.json",result)
            if on_progress:
                on_progress(dict(stage='coding',coverage=coverage_report(result['plan']),codes=len(result['codes']),elapsed_seconds=time.perf_counter()-started))
            if client.process is None and not getattr(client,'_injected_transport',False):
                result['status']='runtime_failed_with_pending'
                break
        else:
            result['status']='queue_processed_not_semantic_complete'
    finally:
        client.expected_tokens=None
        client.period_deadline=None
        result['unfinished_definition_code_ids']=_unfinished(result)
        if define_scope and result['status']=='queue_processed_not_semantic_complete' and result['unfinished_definition_code_ids']:
            result['status']='passage_queue_processed_definitions_incomplete'
        result['profiling']['work_period_seconds']=time.perf_counter()-started
        result['profiling']['native_accounting_seconds']=client.native_seconds
        result['profiling']['generation_seconds']=client.completed_generation_seconds
        result['coverage']=coverage_report(result['plan'])
        _save(output/'session.json',result)
        with (output/'review.html').open('x',encoding='utf-8') as stream:
            stream.write(export_session_html(result))
    return result


def retry_definitions(session,client,output,*,code_ids,wall_seconds=600,cancel_event=None,on_progress=None,max_generation_calls=8):
    """Explicit selected unfinished-definition retry under current role contract.

    Codes, prior raw responses, passage coverage and original session remain intact.
    A successful definition or explicit insufficient decision is not auto-retried.
    """
    validate_session(session)
    if (type(code_ids) not in (list,tuple) or not code_ids or len(set(code_ids))!=len(code_ids)
            or not 1<=wall_seconds<=3600 or type(max_generation_calls) is not int or not 1<=max_generation_calls<=100):
        raise ValueError('Select distinct unfinished definition IDs and a bounded work period.')
    if (client.config.model_sha256!=session['model_sha256'] or client.config.executable_sha256!=session['executable_sha256']
            or asdict(client.config.limits)!=session['limits']):
        raise ValueError('Retry requires the same model, runtime and limits.')
    by_id={c['id']:c for c in session['codes']}
    if any(uid not in by_id or by_id[uid]['definition_status'] not in {'not_requested','failed','interrupted'} for uid in code_ids):
        raise ValueError('Only explicitly selected unfinished definitions may be retried; completed calls stay untouched.')
    output=Path(output).resolve()
    if str(output).startswith(('\\\\','//')):raise ValueError('Retry output must be local.')
    output.mkdir(parents=True,exist_ok=False)
    result=deepcopy(session);_save(output/'starting-session.json',session)
    cancel=cancel_event or threading.Event();started=time.perf_counter()
    client.period_deadline=time.monotonic()+wall_seconds
    result['status']='definition_retry_running';completed=[]
    try:
        for code in result['codes']:
            if code['id'] not in code_ids:continue
            if cancel.is_set() or time.perf_counter()-started>=wall_seconds or len(completed)>=max_generation_calls:break
            request=result['requests'][code['batch_id']]
            item=next(i for i in result['plan']['work_items'] if i['id']==code['work_item_id'])
            _definition_attempt(result,code,request,item,client,output,cancel,contract=scope_evidence.DEFAULT_VERSION,explicit_retry=True)
            completed.append(code['id'])
            result['unfinished_definition_code_ids']=_unfinished(result)
            _save(output/f"checkpoint-{len(result['calls']):06d}.json",result)
            if on_progress:on_progress(dict(stage='definition_retry',attempted=len(completed),selected=len(code_ids)))
            if client.process is None and not getattr(client,'_injected_transport',False):break
    finally:
        client.expected_tokens=None;client.period_deadline=None
        result['unfinished_definition_code_ids']=_unfinished(result)
        result['status']='definitions_incomplete' if result['unfinished_definition_code_ids'] else 'definitions_drafted_not_semantic_acceptance'
        result.setdefault('definition_retry_periods',[]).append(dict(selected=list(code_ids),attempted=completed,
            elapsed_seconds=time.perf_counter()-started,max_generation_calls=max_generation_calls,
            scope_contract=scope_evidence.DEFAULT_VERSION,origin='explicit_AI_retry_not_human_revision'))
        result['coverage']=coverage_report(result['plan'])
        _save(output/'session.json',result)
        (output/'review.html').write_text(export_session_html(result),encoding='utf-8')
    return result


def validate_session(value):
    if value.get('version') != 'themeforge.study-session.v1':
        raise ValueError('Unknown study session.')
    valid_contracts={'legacy-grounded-v6',scope_evidence.VERSION,scope_evidence.GUARDED_VERSION}
    if value.get('scope_contract','legacy-grounded-v6') not in valid_contracts:
        raise ValueError('Unknown explicit session scope contract.')
    validate_plan(value['plan'])
    for batch in value['plan']['batches']:
        expected=coding_request(batch['request'],mode=value['mode'],research_questions=value['research_questions'])
        if value['requests'].get(batch['id']) != expected:
            raise ValueError('Study prompt or exact evidence differs from source plan.')
    sources={s['source_name']:s for s in value['plan']['study']['sources']}
    work={item['id']:item for item in value['plan']['work_items']}
    batches={batch['id']:batch for batch in value['plan']['batches']}
    seen=set()
    for code in value['codes']:
        if code['id'] in seen:
            raise ValueError('Duplicate study code identity.')
        seen.add(code['id'])
        status=code.get('definition_status')
        if status not in {'not_requested','failed','interrupted','define','insufficient'}:
            raise ValueError('Unknown definition execution status.')
        if code.get('scope_contract','legacy-grounded-v6') not in valid_contracts:
            raise ValueError('Unknown explicit definition scope contract.')
        batch=batches[code['batch_id']]; item=work[code['work_item_id']]
        request=value['requests'][batch['id']]
        original=code['original_model']
        validate_multi_decision(json.dumps(dict(decision='codes',codes=[original])),list(request['evidence']),list(request['references']),request['maximum_codes'])
        if (code['work_item_id'] not in batch['work_item_ids'] or code['parent_unit_id']!=item['parent_unit_id']
                or code['source_name']!=item['source_name'] or code['source_hash']!=item['source_hash'] or code['speaker']!=item['speaker']
                or code['label']!=original['label'] or code['claim']!=original['claim']
                or code['origin']!='local_model_draft' or code['human_review'] is not False
                or code['evidence'] != [request['evidence'][sid] for sid in original['evidence_ids']]
                or code['referent_context_not_support'] != [request['references'][sid] for sid in original['referent_ids']]):
            raise ValueError('Study code/evidence differs from its original model and source work item; explicit revisions require a separate provenance branch.')
        if status in {'define','insufficient'}:
            if code.get('definition_raw',{}).get('decision')!=status:
                raise ValueError('Completed definition status requires its matching raw decision.')
            if code.get('scope_contract')==scope_evidence.VERSION:
                scope=scope_evidence.validate(code['definition_raw'],request['evidence'],original)
                q=scope_evidence.role_ids(scope,'qualification');c=scope_evidence.role_ids(scope,'counterexample')
            else:
                scope=(scope_evidence.validate_guarded_legacy(code['definition_raw'],request['evidence'],original)
                    if code.get('scope_contract')==scope_evidence.GUARDED_VERSION
                    else validate_grounded_scope(json.dumps(code['definition_raw']),list(request['evidence']),original['evidence_ids']))
                q=scope.get('qualification_ids',[]);c=scope.get('counterexample_ids',[])
            if status=='define' and (code.get('qualifications') != [request['evidence'][sid] for sid in q]
                    or code.get('counterexamples') != [request['evidence'][sid] for sid in c]):
                raise ValueError('Definition evidence roles do not match source target IDs.')
            if status=='insufficient' and (code.get('qualifications') or code.get('counterexamples')):
                raise ValueError('Insufficient definitions cannot attach accepted evidence roles.')
        for span in code['evidence']+code['referent_context_not_support']+code.get('qualifications',[])+code.get('counterexamples',[]):
            source=sources[span['source_name']]
            if source['source_hash']!=span['source_hash'] or source['text'][span['source_start']:span['source_end']]!=span['text']:
                raise ValueError('Code evidence differs from its exact original source.')
    if 'unfinished_definition_code_ids' in value and value['unfinished_definition_code_ids']!=_unfinished(value):
        raise ValueError('Unfinished definition queue differs from actual code states.')


def source_review_cues(session, code):
    """Reuse conservative source cues without revising or approving a draft."""
    request = session['requests'][code['batch_id']]
    view = dict(code, unit_id=code['parent_unit_id'], definition=code.get('definition_raw', {}).get('definition', ''))
    cues = review_meaning(dict(registry=request['evidence'], unit_archive=[]), view)
    from .drift_surfaces import additional_drift_view
    sources={s['source_name']:s for s in session['plan']['study']['sources']}
    cues['targeted_drift_review_not_revision']=additional_drift_view(
        dict(registry=request['evidence'],unit_archive=[]),view,sources)
    cues['whole_study_referent_notice'] = (
        'These surface checks do not resolve actors, objects or pronouns. Inspect the exact target, selected context and original archive; '
        'the presence or absence of a warning is not semantic approval.')
    if code.get('scope_contract')==scope_evidence.VERSION and code.get('definition_raw',{}).get('decision')=='define':
        cues['evidence_roles']=scope_evidence.review_warnings(code['definition_raw'],request['evidence'],code['original_model'])
    elif code.get('definition_raw',{}).get('decision')=='define':
        raw=code['definition_raw'];selected=set(raw.get('qualification_ids',[])+raw.get('counterexample_ids',[]))
        remaining=set(request['evidence'])-set(code['original_model']['evidence_ids'])
        warnings=[]
        if selected and selected==remaining:
            warnings.append('Legacy output assigns every unselected sentence an auxiliary role; inspect whether each actually limits or challenges the claim.')
        if any(scope_evidence._FILLER.fullmatch(request['evidence'][sid]['text'].strip()) for sid in selected):
            warnings.append('Legacy auxiliary evidence includes greeting/filler. Raw output is retained; this warning is not an accepted revision.')
        if any(not scope_evidence._LIMIT_CUE.search(request['evidence'][sid]['text']) for sid in selected):
            warnings.append('Some auxiliary sentences have no simple English limiting cue; review their relevance. This surface warning cannot establish role correctness.')
        cues['evidence_roles']=dict(origin='computed_review_cues_not_model_revision',semantic_validation=False,
            warnings=warnings,notice='Legacy definitions remain unchanged. Completed drafting is not semantic acceptance.')
    return cues


def export_session_html(session):
    validate_session(session)
    e=lambda v:escape(str(v),quote=True)
    coverage=coverage_report(session['plan'])
    rows=['<!doctype html><meta charset="utf-8"><title>Whole-study candidate coding</title>',
        '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; base-uri \'none\'">',
        '<style>body{font:16px/1.5 system-ui;max-width:1100px;margin:auto;padding:24px}pre{white-space:pre-wrap;overflow-wrap:anywhere}article{border:1px solid #aaa;margin:12px 0;padding:12px}</style>',
        '<h1>Whole-study candidate coding</h1><p>'+e(coverage['notice'])+'</p>',
        '<h2>Execution and source coverage</h2><pre>'+e(json.dumps(coverage,ensure_ascii=False,indent=2))+'</pre>',
        '<h2>Explicit budgets and observed timings</h2><pre>'+e(json.dumps(dict(limits=session['limits'],profiling=session['profiling']),indent=2))+'</pre>']
    for code in session['codes']:
        rows.append('<article><h2>'+e(code['label'])+'</h2><pre>'+e(json.dumps(dict(original_draft=code,
            computed_review_cues_not_model_revision=source_review_cues(session, code)),ensure_ascii=False,indent=2))+'</pre></article>')
    rows.append('<h2>Every imported turn and original routing</h2>')
    for unit in session['plan']['study']['units']:
        rows.append('<details><summary>'+e(unit['source_name']+' / '+unit['id'])+'</summary><pre>'+e(json.dumps(unit,ensure_ascii=False,indent=2))+'</pre></details>')
    return '\n'.join(rows)

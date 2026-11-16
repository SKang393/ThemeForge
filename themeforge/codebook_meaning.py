"""Bounded source context and transparent lexical review cues, never semantic proof."""
import re


def reference_candidates(unit, archive, *, focused=False):
    """Return exact preceding turns for referents, never later or other-case facts.

    At most two same-speaker turns and the nearest actual question are exposed.
    Full spans over the 1600-character context budget are omitted, not rewritten.
    """
    prior = sorted((u for u in archive if u['source_name'] == unit['source_name']
                    and u['source_hash'] == unit['source_hash']
                    and u['source_end'] <= unit['source_start']
                    and u['source_start'] >= unit['source_start'] - 3000),
                   key=lambda u: u['source_start'], reverse=True)
    chosen, participant_count, question_count, size = [], 0, 0, 0
    # Focused prompts expose same-speaker background only for a brief turn.
    # Longer turns retain a question referent aid; they must not import an
    # earlier account's separate practices. This is a bounded development policy,
    # not a general claim that long turns have no unresolved references.
    participant_cap = (1 if len(unit['text'].split()) <= 45 else 0) if focused else 2
    for previous in prior:
        same_speaker = previous['speaker'] == unit['speaker'] and 'interviewer' not in previous['flags']
        if focused:
            same_speaker = same_speaker and 'speaker_unverified' not in previous['flags'] and 'speaker_unverified' not in unit['flags']
        question = 'interviewer' in previous['flags'] and previous['text'] == unit['reference_context']
        if not ((same_speaker and participant_count < participant_cap) or (question and not question_count)):
            continue
        if size + len(previous['text']) > 1600:
            continue
        chosen.append({'unit_id': previous['id'], 'source_name': previous['source_name'],
                       'source_hash': previous['source_hash'], 'source_start': previous['source_start'],
                       'source_end': previous['source_end'], 'speaker': previous['speaker'], 'text': previous['text'],
                       'role': 'participant_referent_only' if same_speaker else 'question_referent_only'})
        size += len(previous['text'])
        participant_count += int(same_speaker); question_count += int(question)
    return {f'r{i}': entry for i, entry in enumerate(sorted(chosen, key=lambda e: e['source_start']), 1)}


_CUES = {
    'possibility': re.compile(r'\b(?:might|may|could|perhaps|possibly|maybe|possible|potential|tentative)\b', re.I),
    'conditional': re.compile(r'\b(?:if|unless|provided that|only when|conditional)\b', re.I),
    'knowledge_limit': re.compile(r'\b(?:to (?:my|our|their|his|her) knowledge|as far as (?:I|we|they) know|not (?:sure|aware)|uncertain|unsure)\b', re.I),
    'conditional_or_habitual_would': re.compile(r'\bwould\b', re.I),
}


def refresh_meaning_checks(result, code):
    """Mutate/return diagnostic cues after generation or an explicit revision.

    Deliberately limited English lexical checks can have false positives/negatives.
    They never approve a code, alter wording, or establish causal/semantic validity.
    """
    cues, risks = [], []
    for sid, entry in zip(code['support_span_ids'], code['evidence']):
        for kind, pattern in _CUES.items():
            for match in pattern.finditer(entry['text']):
                cues.append({'kind': kind, 'span_id': sid, 'text': match.group(),
                             'source_start': entry['source_start'] + match.start(),
                             'source_end': entry['source_start'] + match.end()})
    for field in ('claim', 'definition'):
        text = code.get(field, '')
        if not text:
            continue
        for kind, pattern in _CUES.items():
            if any(c['kind'] == kind for c in cues) and not pattern.search(text):
                risks.append(f'{kind}_may_be_lost:{field}')
        # Only flags an unsupported concrete material noun. A question or RQ
        # cannot turn data into programming code. This is not general NLI.
        source = ' '.join(e['text'] for e in code['evidence']) + ' ' + ' '.join(
            e['text'] for e in code.get('referent_context', []) if e['role'] == 'participant_referent_only')
        if re.search(r'\b(?:code|scripts?|software)\b', text, re.I) and not re.search(r'\b(?:code|scripts?|software)\b', source, re.I):
            risks.append(f'programming_material_not_in_selected_participant_evidence:{field}')
        if re.search(r'\b(?:plans?|intends?|intention)\b', text, re.I) and not re.search(r'\b(?:plans?|intends?|intention)\b', source, re.I):
            risks.append(f'intention_not_explicit_in_selected_evidence:{field}')
    code['meaning_checks'] = {'status': 'heuristic_review_cues_not_semantic_validation',
                              'risk_flags': risks, 'source_cues': cues,
                              'limitations': 'English lexical cues; inspect referents, negation, conditions and meaning even when no flag appears.'}
    return code


# Kept separate from the frozen legacy checks above. Loading an old draft must
# not rewrite its saved diagnostics or invalidate its digest as rules evolve.
_REVIEW_CUES = {
    'possibility': re.compile(r'\b(?:might|may|could|perhaps|possibly|maybe|possible|potential|tentative)\b', re.I),
    'conditional': re.compile(r'\b(?:if|unless|provided that|only when)\b', re.I),
    'knowledge_limit': re.compile(r"\b(?:to (?:the best of )?(?:my|our|their|his|her) knowledge|as far as (?:I|we|they) (?:know|am aware|are aware)|not (?:being )?(?:sure|aware)|limited knowledge|uncertain|unsure)\b", re.I),
    'would': re.compile(r'\bwould\b', re.I),
    'negation': re.compile(r"\b(?:not|never|no|cannot|can['’]t|doesn['’]t|didn['’]t|hasn['’]t|haven['’]t|won['’]t|isn['’]t|aren['’]t)\b", re.I),
}
_CONDITIONAL_PARAPHRASE = re.compile(r'\b(?:if|unless|when|whenever|provided|conditional|depending|during|for|in the event)\b', re.I)
_WOULD_PARAPHRASE = re.compile(r'\b(?:would|(?:conditional|planned|habitual) or (?:conditional|habitual))\b', re.I)
_NEGATION_PARAPHRASE = re.compile(_REVIEW_CUES['negation'].pattern + r'|\b(?:without|lacks?|lacking|unavailable|unaware|absence|unlimited|unrestricted)\b', re.I)
_INTENT = re.compile(r'\b(?:plans?|planned|planning|intends?|intended|intention|proposes?|proposed)\b', re.I)
_OBJECT_PRONOUN = re.compile(r'\b(?:send|sent|sending|share|shared|sharing|put|putting|store|stored|upload|uploaded|use|using|move|moved)\s+(?:it|them|that|this)\b', re.I)
_LOCATION_OBJECT = re.compile(r'\b(?:send|sent|sending|share|shared|sharing|put|putting|upload|uploaded|move|moved)\s+(?:(?:a|an|the|their|our|my)\s+)?(?:repository|repositories|server|website|site)\b', re.I)


def review_meaning(result, code):
    """Return current source-linked review cues without changing a saved draft.

    Rules are intentionally conservative surface checks, not semantic judgments.
    They do not repair wording, choose evidence roles, approve codes, or gate
    synthesis. Exact source material remains available even without a warning.
    """
    registry = result['registry']
    by_identity = {(v['source_name'], v['source_start'], v['source_end']): sid
                   for sid, v in registry.items()}
    entries = []
    for role, field in (('support', 'evidence'), ('qualifications', 'qualifications'),
                        ('counterexamples', 'counterexamples')):
        for entry in code.get(field, []):
            identity = (entry['source_name'], entry['source_start'], entry['source_end'])
            sid = by_identity.get(identity)
            if sid is None or registry[sid] != entry:
                raise ValueError('Review warnings require exact registry evidence.')
            entries.append(dict(span_id=sid, role=role, **{key: entry[key] for key in
                ('unit_id', 'source_name', 'source_hash', 'source_start', 'source_end', 'text')}))
    support = [entry for entry in entries if entry['role'] == 'support']
    cues = []
    for entry in entries:
        for kind, pattern in _REVIEW_CUES.items():
            for match in pattern.finditer(entry['text']):
                cues.append(dict(kind=kind, span_id=entry['span_id'], role=entry['role'],
                    source_name=entry['source_name'], source_hash=entry['source_hash'],
                    source_start=entry['source_start'] + match.start(),
                    source_end=entry['source_start'] + match.end(), text=match.group()))
    warnings = []
    def add(kind, fields, evidence, message):
        if evidence:
            warnings.append(dict(kind=kind, fields=fields, source_evidence=evidence, message=message))
    prose = {field: code.get(field, '') for field in ('label', 'claim', 'definition')}
    for kind, target_pattern, message in (
        ('possibility', _REVIEW_CUES['possibility'], 'Selected support contains words such as might, may or could. Check whether they express uncertainty or ability, and whether the current wording preserves that meaning.'),
        ('conditional', _CONDITIONAL_PARAPHRASE, 'Selected support states a condition. Check that the current wording retains its scope.'),
        ('knowledge_limit', _REVIEW_CUES['knowledge_limit'], 'Selected support limits what the speaker knows. Check that the current wording does not turn limited knowledge into a universal claim.'),
        ('would', _WOULD_PARAPHRASE, 'Would can be conditional or habitual. Check that a plan or completed action has not been inferred without evidence.'),
        ('negation', _NEGATION_PARAPHRASE, 'Selected support includes negation. Check polarity and scope in the current wording; a different negative phrase is not proof that the same assertion is preserved.'),
    ):
        evidence = [entry for entry in support if _REVIEW_CUES[kind].search(entry['text'])]
        # Labels are short summaries: only inspect them for explicit intention
        # below. Missing a modal word in a label alone is not a lost condition.
        fields = [field for field in ('claim', 'definition') if prose[field] and not target_pattern.search(prose[field])]
        if fields:
            add(kind + '_scope_needs_review', fields, evidence, message)
    tentative = [entry for entry in support if _REVIEW_CUES['possibility'].search(entry['text'])
                 or _REVIEW_CUES['would'].search(entry['text'])]
    intent_fields = [field for field, text in prose.items() if _INTENT.search(text)]
    if intent_fields and not any(_INTENT.search(entry['text']) for entry in support):
        add('intention_inference_needs_review', intent_fields, tentative,
            'Current wording introduces a plan/proposal while the selected assertion uses might or would. This may be an interpretation, not an explicit intention.')

    unit = next((u for u in result['unit_archive'] if u['id'] == code['unit_id']), None)
    pronouns = [entry for entry in support if _OBJECT_PRONOUN.search(entry['text'])]
    if unit is not None and pronouns:
        available = list(reference_candidates(unit, result['unit_archive'],
            focused=result.get('prompt_variant') == 'grounded-v6').values())
        participant_refs = [entry for entry in available if entry['role'] == 'participant_referent_only']
        selected_refs = [entry for entry in code.get('referent_context', [])
                         if entry['role'] == 'participant_referent_only']
        if participant_refs and not selected_refs:
            add('referent_context_not_selected', [], pronouns,
                'This assertion contains an object reference such as it or this. Preceding participant context is available, but none was selected as a referent. Inspect the original context; do not silently infer a material or actor.')
            warnings[-1]['available_context_not_selected'] = participant_refs
        location_fields = [field for field, text in prose.items() if _LOCATION_OBJECT.search(text)]
        source_names_location_object = any(_LOCATION_OBJECT.search(entry['text']) for entry in support)
        refs_name_location = any(re.search(r'\b(?:repository|repositories|server|website|site)\b', entry['text'], re.I)
                                 for entry in selected_refs)
        if location_fields and not source_names_location_object and not refs_name_location:
            add('location_object_substitution_needs_review', location_fields, pronouns,
                'Current wording makes a repository/site/server the object being moved or shared, where selected support uses a pronoun. Check object versus destination roles; this warning does not resolve the pronoun.')
    qualifications = [entry for entry in entries if entry['role'] == 'qualifications']
    counterexamples = [entry for entry in entries if entry['role'] == 'counterexamples']
    if qualifications:
        add('qualification_relationship_unverified', [], qualifications,
            'Check how each selected qualification limits or changes this specific claim. Related facts and filler are not automatically qualifications.')
    if counterexamples:
        add('counterexample_relationship_unverified', [], counterexamples,
            'Check what each selected passage contradicts under comparable conditions. A different practice, object or case is not automatically a counterexample.')
    return dict(version='themeforge.meaning-review.v2', status='review_cues_not_semantic_validation',
        warnings=warnings, source_cues=cues, limitations=[
            'English surface rules have false positives and omissions; warning-free prose is not semantically approved.',
            'Conditional paraphrase markers may suppress a cue without proving equivalent meaning.',
            'Referent checks do not resolve pronouns or establish who did what; full source review remains necessary.',
            'Evidence-role reminders do not judge contradiction, relevance, prevalence or absence.',
            'Computed for the current view/export; saved legacy diagnostics, raw responses and result digests are not rewritten.',
        ])

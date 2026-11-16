"""Small English source-linked drift cues; never corrections or admission rules."""
from hashlib import sha256
import re

from .codebook_meaning import review_meaning

VERSION = 'themeforge.source-drift-review.v1'
_ACTORS = r'pupil|student|learner|child|family|parent|caregiver|teacher|tutor'
_UNASKED = re.compile(r'\b(?:not|never)\s+(?:yet\s+)?(?:asked|consulted)\s+(?:the\s+)?(?P<actor>' + _ACTORS + r')\b', re.I)
_ASKED = re.compile(r'\b(?:asked|consulted)\s+(?:the\s+)?(?P<actor>' + _ACTORS + r')\b', re.I)
_INQUIRY = re.compile(r'\b(?:(?:inquiry|enquiry|consultation)\s+(?:into|about|with|of)|asked|consulted)\s+(?:the\s+)?(?P<actor>' + _ACTORS + r')\b', re.I)
_CAN = re.compile(r'\bcan\s+(?:offer|provide|present|try|discuss|arrange)\b', re.I)
_WILL = re.compile(r'\bwill\s+(?:offer|provide|present|try|discuss|arrange)\b', re.I)
_DESIRE = re.compile(r'\bI\s+(?:want|wanted|hope|hoped|would like)\s+to\s+(?P<verb>[a-z]+)\b', re.I)
_DESIRE_PROSE = re.compile(r'\b(?:wants?|wanted|hopes?|hoped|desires?|desired|prefers?|preferred|preference|wishes?|would like)\b', re.I)
_WISH = re.compile(r'\b(?:my|our|the)\s+(?P<actor>tutor|teacher|parent|caregiver|family)\s+(?:(?:sometimes|often|occasionally)\s+)?wants?\s+(?:me|us|them|the pupil|the student)\s+to\b', re.I)
_NEGATIVE_PREFIX = re.compile(r'\b(?:not|never|without|rather than)\s+(?:actually\s+)?$', re.I)


def _actor(value):
    value = value.casefold()
    if value in ('pupil', 'student', 'learner', 'child'):
        return 'pupil_or_student'
    if value in ('family', 'parent', 'caregiver'):
        return 'family_or_caregiver'
    return value


def _positive(text, match):
    # This catches only adjacent surface negation, not general negation scope.
    return not _NEGATIVE_PREFIX.search(text[max(0, match.start() - 40):match.start()])


def _entries(result, code, sources):
    if not isinstance(sources, dict):
        raise ValueError('Supply exact full source documents keyed by source_name.')
    registry = result.get('registry')
    if not isinstance(registry, dict) or len(registry) > 512:
        raise ValueError('Review needs a bounded evidence registry.')
    selected = []
    verified = set()
    for role, field in (('support', 'evidence'), ('qualifications', 'qualifications'),
                        ('counterexamples', 'counterexamples')):
        rows = code.get(field, [])
        if type(rows) is not list or len(rows) > 12:
            raise ValueError('Review needs bounded exact evidence lists.')
        for entry in rows:
            ids = [key for key, value in registry.items() if value == entry]
            if not ids:
                raise ValueError('Review needs an exact registry entry.')
            name = entry['source_name']
            source = sources.get(name)
            if not isinstance(source, dict) or type(source.get('text')) is not str:
                raise ValueError('Exact full source document is unavailable.')
            if name not in verified:
                if source.get('source_hash') != sha256(source['text'].encode('utf-8')).hexdigest():
                    raise ValueError('Full source hash does not match source text.')
                verified.add(name)
            start, end = entry['source_start'], entry['source_end']
            if (type(start) is not int or type(end) is not int or not 0 <= start < end <= len(source['text'])
                    or entry['source_hash'] != source['source_hash'] or source['text'][start:end] != entry['text']):
                raise ValueError('Evidence does not reconstruct the exact source characters.')
            # Multiple anchor IDs may represent exactly one source occurrence.
            # Match the existing baseline's last-ID identity convention rather
            # than claiming additional evidence or rejecting valid aliases.
            selected.append((ids[-1], role, entry))
    if not any(role == 'support' for _, role, _ in selected):
        raise ValueError('Drift review requires selected support.')
    for field in ('label', 'claim', 'definition'):
        if type(code.get(field, '')) is not str or len(code.get(field, '')) > 8000:
            raise ValueError('Review needs bounded prose fields.')
    return selected


def review_drift(result, code, *, sources):
    """Return baseline and additional cues without changing any saved object.

    ``result``/``code`` use the existing review_meaning registry/evidence shape.
    ``sources`` maps source_name to {source_hash, text}; each selected entry is
    verified against full source characters. Context and qualifications never
    become target support. All warnings require review and may be false alerts.
    """
    entries = _entries(result, code, sources)
    support = [row for row in entries if row[1] == 'support']
    # Qualification text may expose an inquiry-target discrepancy, but stays
    # explicitly qualification evidence in the diagnostic provenance.
    assertion_context = [row for row in entries if row[1] != 'counterexamples']
    baseline = review_meaning(result, code)
    warnings = []

    def add(kind, family, field, match, row, cue, message):
        sid, role, entry = row
        warnings.append(dict(kind=kind, family=family, fields=[field],
            field_evidence=[dict(field=field, start=match.start(), end=match.end(), text=match.group())],
            source_evidence=[dict(span_id=sid, role=role, unit_id=entry['unit_id'],
                source_name=entry['source_name'], source_hash=entry['source_hash'],
                source_start=entry['source_start'] + cue.start(), source_end=entry['source_start'] + cue.end(),
                text=cue.group())], message=message,
            status='surface_discrepancy_needs_source_review_not_semantic_error_verdict'))

    asked = {_actor(m.group('actor')) for _, _, entry in assertion_context for m in _ASKED.finditer(entry['text'])}
    for field in ('label', 'claim', 'definition'):
        prose = code.get(field, '')
        if not prose:
            continue
        for row in assertion_context:
            for cue in _UNASKED.finditer(row[2]['text']):
                for match in _INQUIRY.finditer(prose):
                    if _actor(match.group('actor')) != _actor(cue.group('actor')) and _actor(match.group('actor')) not in asked:
                        add('inquiry_target_shift_needs_review', 'actor_perspective', field, match, row, cue,
                            'The wording names a different inquiry target from an explicitly unasked source actor. Check who was asked; this cue does not resolve perspective or approve a correction.')
        will = next((m for m in _WILL.finditer(prose) if _positive(prose, m)), None)
        if will and not any(_WILL.search(row[2]['text']) for row in support):
            row_cue = next(((row, _CAN.search(row[2]['text'])) for row in support if _CAN.search(row[2]['text'])), None)
            if row_cue:
                add('ability_to_commitment_needs_review', 'condition_modality', field, will, *row_cue,
                    'Selected support says can while this field says will. A retained condition does not by itself establish a commitment.')
        if field != 'label':
            continue
        if not _DESIRE_PROSE.search(prose):
            for row in support:
                for cue in _DESIRE.finditer(row[2]['text']):
                    verb = cue.group('verb').lower()
                    forms = [verb[:-1] + 'ies', verb[:-1] + 'ied'] if verb.endswith('y') else [verb + 's', verb + ('d' if verb.endswith('e') else 'ed')]
                    match = re.search(r'\b(?:' + '|'.join(re.escape(v) for v in forms) + r')\b', prose, re.I)
                    if match and _positive(prose, match):
                        add('desired_action_as_performed_needs_review', 'condition_modality', field, match, row, cue,
                            'The source expresses wanting or hoping to act, while this label uses a finite performed-action form. Inspect the label in context; abbreviated labels remain ambiguous.')
        for row in support:
            for cue in _WISH.finditer(row[2]['text']):
                match = re.search(r'\b' + re.escape(cue.group('actor')) + r'\s+(?:requests?|requires?|instructs?|demands?)\b', prose, re.I)
                if match and _positive(prose, match):
                    add('reported_wish_as_request_needs_review', 'actor_perspective', field, match, row, cue,
                        'A reported wish is labeled as a request or requirement. Check whether that speech act is explicit; this is not a general actor parser.')
    # Stable de-duplication keeps one cue per exact field/source location.
    seen = set()
    unique = []
    for warning in warnings:
        key = (warning['kind'], repr(warning['field_evidence']), repr(warning['source_evidence']))
        if key not in seen:
            seen.add(key)
            unique.append(warning)
    return dict(version=VERSION, status='review_cues_not_semantic_validation',
        source_binding='selected_registry_entries_and_full_source_characters_verified',
        baseline_review=baseline, warnings=unique,
        attention_cues=[w for w in baseline['warnings'] if w['kind'] in
                        ('qualification_relationship_unverified', 'counterexample_relationship_unverified')],
        qualification_relevance_prediction='not_implemented_generic_attention_only',
        limitations=[
            'Narrow English surface patterns, not semantic validation; false alerts and misses remain possible.',
            'No general actor, referent, negation, frequency or speech-act resolution is performed.',
            'Source actor aliases pupil/student/learner/child and family/parent/caregiver are coarse review categories, not identity equivalence.',
            'Selected qualification text stays qualification context and never becomes supporting evidence.',
            'No wording, evidence roles, saved diagnostics, acceptance or training eligibility is changed.',
            'Generic qualification/counterexample attention cues are not semantic-error detections.',
        ])

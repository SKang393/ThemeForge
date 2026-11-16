"""Conservative exact-source section/turn mapping, never participant counting.

Written section ownership does not prove that its headings, questions, answers,
or administrative text were all spoken by its named participant. This module
resolves only explicit structural source markers and certifies no assertions.
"""
from hashlib import sha256
import json
import re

from .analysis_constants import SPEAKER_RE

VERSION = 'themeforge.source-attribution.v1'
ROLE_HEADER_VERSION = 'themeforge.source-attribution.v2'
LEGACY_POLICY = 'legacy-v1'
ROLE_HEADER_POLICY = 'explicit-education-roles-v2'
_HEADING = re.compile(
    r'^[ \t]*(?:(?P<participant>Participant[ \t]*#?[ \t]*(?P<number>\d+))[ \t]+'
    r'(?P<kind>Responses?|Transcript(?:ion)?|Interview(?:[ \t]+Transcription)?)\b'
    r'|Interview[ \t]+with[ \t]+Participant[ \t]*#?[ \t]*(?P<interview_number>\d+)\b)'
    r'[^\r\n]*$', re.I)
_TURN = re.compile(
    r'^[ \t]*(?P<label>Participant[ \t]*#?[ \t]*\d+|Interviewer(?:[ \t]*#?[ \t]*\d+)?)'
    r'[ \t]*:[ \t]*', re.I)
# A versioned format policy for literal headers demonstrated in the exposed
# education sources. These are source-local labels, not inferred people/actors.
_EDUCATION_LABELS = ('Teacher', 'Caregiver', 'Transition Student', 'Vocational Tutor',
                     'School Coordinator', 'Facilitator', 'Moderator')
_ROLE_TURN = re.compile(
    r'^[ \t]*(?P<label>' + '|'.join(re.escape(s).replace(r'\ ', r'[ \t]+') for s in _EDUCATION_LABELS)
    + r')[ \t]*:[ \t]*', re.I)
_NUMBERED = re.compile(r'^[ \t]*\d{1,2}\.[ \t]+')
_ADMIN = re.compile(r'^[ \t]*(?:Part[ \t]+\d+\b.*|Interview[ \t]+conducted[ \t]*:.*'
                    r'|\[[^\r\n]*closing[^\r\n]*\])[ \t]*$', re.I)
_COURTESY = re.compile(r'^[ \t]*(?:you[\u0027\u2019\ufffd]re welcome|bye|goodbye|thank you|thanks)'
                       r'[.!?, \t]*$', re.I)


def _digest(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                             separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _span(source, start, end):
    return dict(source_name=source['source_name'], source_hash=source['source_hash'],
                source_start=start, source_end=end, text=source['text'][start:end])


def _label(raw):
    match = re.fullmatch(r'(Participant|Interviewer)[ \t#]*(\d+)?', raw, re.I)
    return match.group(1).capitalize() + (' ' + str(int(match.group(2))) if match.group(2) else '')


def resolve_attribution(unit, source, *, policy=LEGACY_POLICY):
    """Return a deterministic exact partition map; never change a saved label.

    Explicit section evidence may precede the requested core. Numbered blocks
    intentionally retain prompts and responses together as unresolved content;
    wrapped question/answer boundaries are not guessed from prose or meaning.
    Every partition has respondent_assertion_supported=False. A source-local
    turn label can resolve speaker_attribution only for a wholly labeled turn.
    """
    if policy not in (LEGACY_POLICY, ROLE_HEADER_POLICY):
        raise ValueError('Unknown source header policy.')
    role_headers = policy == ROLE_HEADER_POLICY
    if type(unit) is not dict or type(source) is not dict:
        raise ValueError('Attribution requires an exact unit and source record.')
    text = source.get('text')
    start, end = unit.get('source_start'), unit.get('source_end')
    if (type(text) is not str or type(source.get('source_name')) is not str or not source['source_name']
            or source.get('source_name') != unit.get('source_name')
            or source.get('source_hash') != unit.get('source_hash')
            or sha256(text.encode()).hexdigest() != source.get('source_hash')
            or type(start) is not int or type(end) is not int
            or not 0 <= start < end <= len(text) or text[start:end] != unit.get('text')):
        raise ValueError('Attribution source name/hash/offset/text proof failed.')
    lines = []
    cursor = 0
    sections = []
    for line in text.splitlines(keepends=True):
        content = line.rstrip('\r\n')
        match = _HEADING.fullmatch(content)
        section = None
        if match:
            number = match.group('number') or match.group('interview_number')
            kind = (match.group('kind') or 'Interview').lower()
            section = dict(section_id='', source_local_label='Participant ' + str(int(number)),
                kind='interview' if kind.startswith('interview') else 'written_responses' if kind.startswith('response') else 'written_transcript',
                source_name=source['source_name'], source_hash=source['source_hash'],
                source_start=cursor, source_end=len(text),
                heading_evidence=_span(source, cursor, cursor + len(content)),
                participant_count_supported=False, respondent_assertion_supported=False)
            if sections:
                sections[-1]['source_end'] = cursor
            sections.append(section)
        lines.append((cursor, cursor + len(line), content, section))
        cursor += len(line)
    for section in sections:
        section['section_id'] = 'SA-SEC-' + _digest([VERSION, source['source_hash'],
            section['source_start'], section['source_end'], section['source_local_label'], section['kind']])[:20]
    partitions = []
    section = turn = numbered = None

    def append(left, right, role, *, labeled=None, label_evidence=None):
        left, right = max(left, start), min(right, end)
        if left >= right:
            return
        metadata = dict(content_role=role,
            section_id=section['section_id'] if section else None,
            section_label=section['source_local_label'] if section else None,
            section_kind=section['kind'] if section else None,
            section_heading_evidence=section['heading_evidence'] if section else None,
            source_labeled_speaker=labeled, speaker_label_evidence=label_evidence,
            numbered_block_start=numbered['source_start'] if numbered else None,
            numbered_prompt_line_evidence=numbered,
            respondent_assertion_supported=False)
        if (partitions and partitions[-1]['source_end'] == left
                and all(partitions[-1][key] == value for key, value in metadata.items())):
            partitions[-1].update(source_end=right, text=text[partitions[-1]['source_start']:right])
        else:
            partitions.append(dict(_span(source, left, right), **metadata))

    for left, right, content, new_section in lines:
        if left >= end:
            break
        if new_section is not None:
            section, turn, numbered = new_section, None, None
            append(left, right, 'section_heading')
            continue
        match = _TURN.match(content)
        known_legacy = match is not None
        if match is None and role_headers:
            match = _ROLE_TURN.match(content)
        if match:
            label = _label(match.group('label')) if known_legacy else ' '.join(match.group('label').split())
            facilitator = label.startswith('Interviewer') or (role_headers and label.casefold() in ('facilitator', 'moderator'))
            turn = dict(label=label, facilitator=facilitator, evidence=_span(source, left, left + match.end()))
            numbered = None
            append(left, left + match.end(), 'speaker_label', label_evidence=turn['evidence'])
            body_start = left + match.end()
            body = text[body_start:right].strip()
            role = ('interviewer_text' if facilitator else
                    'closing_or_conversational_text' if _COURTESY.fullmatch(body) else 'participant_labeled_text')
            append(body_start, right, role, labeled=label, label_evidence=turn['evidence'])
            continue
        if _ADMIN.fullmatch(content):
            numbered = None
            append(left, right, 'administrative_or_part_heading')
            continue
        if role_headers:
            boundary = SPEAKER_RE.match(content)
            if boundary:
                # Unsupported label-like lines end inheritance but establish no
                # new speaker. Reuse existing syntax; do not infer a name/role.
                turn = numbered = None
                body_start = left + boundary.start(2)
                append(left, body_start, 'unresolved_speaker_header',
                       label_evidence=_span(source, left, body_start))
                append(body_start, right, 'section_body_unresolved')
                continue
        if _NUMBERED.match(content):
            numbered = _span(source, left, left + len(content))
        label = turn['label'] if turn else None
        role = ('interviewer_text' if turn and turn['facilitator'] else
                'numbered_block_prompt_or_response_unresolved' if numbered else
                'closing_or_conversational_text' if _COURTESY.fullmatch(content) else
                'participant_labeled_text' if label else 'section_body_unresolved')
        append(left, right, role, labeled=label if role in ('interviewer_text', 'participant_labeled_text',
               'closing_or_conversational_text') else None, label_evidence=turn['evidence'] if turn else None)
    cursor = start
    for row in partitions:
        if row['source_start'] != cursor:
            raise ValueError('Internal attribution partition has a gap or overlap.')
        cursor = row['source_end']
    if cursor != end or ''.join(row['text'] for row in partitions) != unit['text']:
        raise ValueError('Internal attribution partition failed exact reconstruction.')
    used_sections = [dict(s, unit_overlap_start=max(start, s['source_start']),
        unit_overlap_end=min(end, s['source_end'])) for s in sections
        if s['source_start'] < end and start < s['source_end']]
    labels = list(dict.fromkeys(s['source_local_label'] for s in used_sections))
    voices = list(dict.fromkeys(row['source_labeled_speaker'] for row in partitions
                               if row['source_labeled_speaker'] is not None))
    structural_mixture = len(used_sections) > 1
    unsectioned = sum(p['source_end'] - p['source_start'] for p in partitions if p['section_id'] is None)
    status = 'multiple_sections' if structural_mixture else 'single_section' if used_sections else 'unknown'
    if used_sections and unsectioned:
        status = 'multiple_sections' if structural_mixture else 'unknown'
    substantive = [p for p in partitions if p['text'].strip()]
    if structural_mixture or len(voices) > 1:
        speaker = 'multiple'
    elif (len(voices) == 1 and substantive and all(p['source_labeled_speaker'] == voices[0]
          and p['content_role'] in ('participant_labeled_text', 'interviewer_text', 'closing_or_conversational_text')
          for p in substantive)):
        speaker = voices[0]
    else:
        speaker = 'unknown'
    warnings = []
    if structural_mixture:
        warnings.append('The requested span crosses explicit source sections; its inherited saved label cannot identify all content.')
    if any(s['kind'].startswith('written_') for s in used_sections):
        warnings.append('Written section ownership does not distinguish prompts, answers, headings or administrative text; speaker attribution remains unresolved without explicit turn labels.')
    if any(p['numbered_block_start'] is not None for p in partitions):
        warnings.append('Numbered blocks may contain both wrapped question prompts and responses. No automatic respondent-assertion attribution is made.')
    if any(p['content_role'] in ('section_heading', 'speaker_label', 'administrative_or_part_heading',
                               'interviewer_text', 'closing_or_conversational_text') for p in partitions):
        warnings.append('Structural, administrative, interviewer or conversational text is present and is not certified respondent assertion evidence.')
    if not used_sections:
        warnings.append('No explicit participant section heading was found for this source span.')
    elif unsectioned:
        warnings.append('Part of this span precedes any explicit source section; known section labels do not cover all characters.')
    if speaker == 'multiple':
        display = 'Multiple source sections or labels; speaker unresolved'
    elif speaker != 'unknown':
        display = speaker + ' (source-local turn label; assertion unvalidated)'
    elif len(labels) == 1:
        display = labels[0] + ' section; speaker unresolved'
    else:
        display = 'Unknown source-local speaker'
    value = dict(version=ROLE_HEADER_VERSION if role_headers else VERSION, origin='computed_source_structure_not_AI_or_human_adjudication',
        source_name=source['source_name'], source_hash=source['source_hash'],
        unit_id=unit.get('id'), source_start=start, source_end=end,
        original_saved_label=unit.get('original_parent_speaker', unit.get('speaker')),
        input_saved_label=unit.get('speaker'), status=status, section_labels=labels,
        unsectioned_characters=unsectioned,
        sections=used_sections, source_labeled_speakers=voices, speaker_attribution=speaker,
        speaker_display=display, participant_count_supported=False, respondent_assertion_supported=False,
        semantic_validation=False, partitions=partitions, warnings=warnings,
        uncertainty='Source-local structural labels only. Section ownership is not verified speech or assertion authorship. Participant labels never establish global person identity; question/answer and discourse functions require review.')
    if role_headers:
        value['header_policy'] = ROLE_HEADER_POLICY
        value['speaker_role'] = ('unknown' if speaker in ('unknown', 'multiple') else
            'facilitator' if speaker.startswith('Interviewer') or speaker.casefold() in ('facilitator', 'moderator') else 'participant')
        if any(p['content_role'] == 'unresolved_speaker_header' for p in partitions):
            value['warnings'].append('Unsupported header-like boundary ended speaker inheritance; its label remains unresolved.')
    value['attribution_sha256'] = _digest(value)
    return value

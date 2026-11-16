"""Read-only exact-source context and structural attribution review cues."""
from hashlib import sha256
import re

_SECTION = re.compile(r'(?im)^(?:Participant\s*#?\s*\d+\s+(?:Responses|Transcript|Interview)\b[^\r\n]*|Interview\s+with\s+Participant\s*#?\s*\d+[^\r\n]*)')
_QUESTION = re.compile(r'(?m)^\s*\d{1,2}\.\s+[^\r\n]*\?')


def review_context(unit, source, *, radius=1200):
    """Return source-exact spans, without changing attribution or approving meaning."""
    if type(radius) is not int or not 0 <= radius <= 10000:
        raise ValueError('Context preview radius must be 0..10000 characters.')
    text=source['text'];start,end=unit['source_start'],unit['source_end']
    if (source['source_name']!=unit['source_name'] or source['source_hash']!=unit['source_hash']
            or sha256(text.encode()).hexdigest()!=source['source_hash']
            or type(start) is not int or type(end) is not int or not 0<=start<end<=len(text)
            or text[start:end]!=unit['text']):
        raise ValueError('Review context must bind the unchanged original source.')
    span=lambda a,b:dict(source_name=source['source_name'],source_hash=source['source_hash'],
                         source_start=a,source_end=b,text=text[a:b],role='context_not_additional_claim_evidence')
    headings=[span(start+m.start(),start+m.end()) for m in _SECTION.finditer(unit['text'])]
    questions=list(_QUESTION.finditer(unit['text']))
    warnings=[]
    if headings:
        warnings.append('Embedded participant/transcript heading: the saved speaker label may cover another section or person. Resolve attribution before grouping.')
    if len(questions)>1:
        warnings.append('Multiple numbered questions occur inside this source unit. Prompts and responses may be mixed; do not treat the complete unit as one respondent assertion.')
    if end-start>4000:
        warnings.append('Long source unit: inspect individual assertions, conditions and referents rather than treating the similarity score as a shared claim.')
    return dict(origin='computed_source_context_cues_not_AI_or_human_adjudication',semantic_validation=False,
        source_local_speaker_is_unverified=unit.get('speaker'),embedded_section_headers=headings,
        numbered_question_line_count=len(questions),attribution_review_required=bool(headings),warnings=warnings,
        preceding_context=span(max(0,start-radius),start) if start else None,
        following_context=span(end,min(len(text),end+radius)) if end<len(text) else None,
        preview_radius_characters=radius,full_source_characters=len(text),
        notice='Preview is bounded and may omit distant context. Use the full original source. Related opposites and shared terms do not establish equivalent meanings or a category.')


def pair_overview(edge,units):
    """Compact navigation record; full member text stays available in pair view."""
    fields=('id','source_name','source_hash','source_start','source_end','speaker')
    return dict(edge=edge,members=[{k:units[uid][k] for k in fields} for uid in edge['member_ids']],
                notice='Navigation only. Inspect candidate pair to read both complete source units and their context.')

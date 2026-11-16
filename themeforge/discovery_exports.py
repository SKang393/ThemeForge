"""Offline, source-resolved candidate-codebook reports without approval forms."""
from __future__ import annotations

from html import escape
from dataclasses import asdict
from hashlib import sha256
import json
import re

from .analysis_types import AnalysisResult


def _text(value) -> str:
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, indent=2)
    return str(value)


def _md(value) -> str:
    return re.sub(r"([\\`*_{}\[\]()#+.!|>~-])", r"\\\1", escape(_text(value), quote=False))


def _display(value) -> str:
    return '; '.join(_text(item) for item in value) if isinstance(value, (list, tuple)) else _text(value)


def _workspace_changed(result, payload):
    expected = payload.get('workspace_fingerprint')
    actual = sha256(json.dumps([asdict(theme) for theme in result.themes], sort_keys=True,
                              ensure_ascii=False, separators=(',', ':')).encode('utf-8')).hexdigest()
    return bool(expected and expected != actual)


def _quote(value) -> str:
    return '\n'.join('> ' + _md(line) for line in str(value).split('\n'))


def _checked(result: AnalysisResult):
    payload = result.discovery
    if not payload or not isinstance(payload, dict):
        raise ValueError('Discovery report requires a discovery payload.')
    lookup = {}
    for unit in payload.get('units', []):
        uid = unit.get('id')
        if not isinstance(uid, str) or not uid or uid in lookup:
            raise ValueError('Discovery units require unique nonempty IDs.')
        start, end = unit.get('source_start'), unit.get('source_end')
        if (type(start) is not int or type(end) is not int or start < 0 or end < start
                or end - start != len(unit.get('text', ''))):
            raise ValueError(f'Invalid source span for discovery unit {uid}.')
        source = unit.get('source_name', '')
        current_hash = result.source_hashes.get(source)
        if current_hash and current_hash != unit.get('source_hash'):
            raise ValueError(f'Source version mismatch for discovery unit {uid}.')
        lookup[uid] = unit

    def require(uid):
        if uid not in lookup:
            raise ValueError(f'Missing discovery evidence unit: {uid}')
        return lookup[uid]

    code_ids = set()
    for code in payload.get('codes', []):
        if not code.get('id') or code['id'] in code_ids:
            raise ValueError('Candidate codes require unique nonempty IDs.')
        code_ids.add(code['id'])
        unit = require(code.get('anchor_unit_id'))
        start, end = code.get('anchor_source_start'), code.get('anchor_source_end')
        if (type(start) is not int or type(end) is not int
                or not unit['source_start'] <= start <= end <= unit['source_end']
                or not code.get('anchor_text')
                or unit['text'][start-unit['source_start']:end-unit['source_start']] != code['anchor_text']):
            raise ValueError(f"Candidate anchor does not resolve to its source unit: {code['id']}")
        groups = [set(code.get(key, [])) for key in ('evidence_unit_ids', 'qualification_unit_ids', 'counterexample_unit_ids')]
        if any(left & right for i, left in enumerate(groups) for right in groups[i + 1:]):
            raise ValueError('Supporting, qualifying and countercase evidence must be separate.')
        for key in ('evidence_unit_ids', 'qualification_unit_ids', 'counterexample_unit_ids'):
            for uid in code.get(key, []):
                require(uid)
        for match in code.get('matches', []):
            matched = require(match.get('unit_id'))
            start, end = match.get('source_start'), match.get('source_end')
            if (type(start) is not int or type(end) is not int
                    or not matched['source_start'] <= start < end <= matched['source_end']
                    or matched['text'][start-matched['source_start']:end-matched['source_start']] != match.get('text')):
                raise ValueError('Retrieved assertion does not resolve to its source unit.')
    for key in ('unassigned_unit_ids', 'flagged_unit_ids', 'rare_unit_ids'):
        for uid in payload.get(key, []):
            require(uid)
    for item in payload.get('categories', []):
        for cid in item.get('code_ids', []):
            if cid not in code_ids:
                raise ValueError(f'Missing category candidate code: {cid}')
    for item in payload.get('interpretive_themes', []) + payload.get('relations', []):
        for uid in item.get('evidence_unit_ids', []):
            require(uid)
    return payload, lookup


def _context(payload):
    requested = payload.get('requested_context') or {}
    return requested.get('research_questions') or [], requested.get('theoretical_lens') or 'Not supplied'


def _participants(code, lookup):
    return sorted({f"{lookup[uid]['source_name']} / {lookup[uid]['speaker']}"
                   for uid in code.get('evidence_unit_ids', [])})


def _metadata(result, payload):
    return dict(run_id=result.run_id, version=result.version,
                provenance=payload.get('provenance', {}), diagnostics=payload.get('diagnostics', {}),
                relations=payload.get('relations', []), source_hashes=result.source_hashes)


def export_discovery_markdown(result: AnalysisResult) -> str:
    """Render all payload evidence, preserving source IDs, offsets and hashes."""
    payload, lookup = _checked(result)
    questions, lens = _context(payload)
    changed = _workspace_changed(result, payload)
    lines = ['# Candidate codebook', '',
             'Extractive draft for interpretation. Definitions use source anchors, not learned semantic definitions. Evidence neighborhoods suggest passages to inspect, not automatic code assignments.', '',
             f"Actual mode: **{_md(payload.get('mode', 'unspecified'))}**", '',
             f"Research questions: {_md(questions or 'Not supplied')}", '', f'Theoretical lens: {_md(lens)}', '',
             'In guided mode, context affects retrieval and ranking. It does not produce latent interpretation. In open mode, supplied context does not guide discovery. Context relevance is a ranking signal, not confidence or coding accuracy.', '',
             '## Candidate codes', '']
    if changed:
        lines[2:2] = ['**Generated snapshot predates workspace edits.** Candidate records below retain the original generated proposals. The separate Current workspace coding section preserves the researcher edits.', '']

    def evidence(unit, match=None):
        excerpt = (['Retrieved assertion (review its fit):', '', _quote(match['text']), '',
                    f"Retrieval role: {_md(match.get('role', 'unspecified'))}; source characters {match['source_start']}:{match['source_end']}.", '',
                    'Full participant turn:', ''] if match else [])
        return [f"**{_md(unit['source_name'])} / {_md(unit['speaker'])}**", '', *excerpt, _quote(unit['text']), '',
                f"Unit: {_md(unit['id'])}; source characters {unit['source_start']}:{unit['source_end']}; SHA-256: {_md(unit.get('source_hash', 'unavailable'))}", '',
                f"Flags: {_md(unit.get('flags') or 'None')}", '',
                'Reference question/context, not participant evidence:', '', _quote(unit.get('reference_context', '')), '',
                'Full source context:', '', _quote(unit.get('context_text', '')), '']
    codes = payload.get('codes', [])
    if not codes:
        lines += ['No candidate codes proposed.', '']
    for index, code in enumerate(codes, 1):
        matches = {match['unit_id']: match for match in code.get('matches', [])}
        lines += [f"### {index}. {_md(code['label'])}", '',
                  f"**Extractive draft definition:** {_md(code.get('definition', ''))}", '',
                  f"Include: {_md(_display(code.get('inclusion_criteria', '')))}", '',
                  f"Exclude: {_md(_display(code.get('exclusion_criteria', '')))}", '',
                  f"Scope terms: {_md(_display(code.get('scope_terms', [])))}", '',
                  f"Contributing source/speaker pairs: {_md(_display(_participants(code, lookup)))}", '',
                  f"Uncertainty: {_md(_display(code.get('uncertainty') or 'No additional warning recorded; review still required'))}", '',
                  f"Origin: {_md(code.get('origin', 'unspecified'))}; definition kind: {_md(code.get('definition_kind', 'unspecified'))}; context relevance: {_md(code.get('context_relevance', 'not recorded'))} (not confidence).", '',
                  'Source-bound definition anchor:', '', _quote(code['anchor_text']), '',
                  f"Anchor unit {_md(code['anchor_unit_id'])}; source characters {code['anchor_source_start']}:{code['anchor_source_end']}.", '',
                  '#### Candidate supporting passages', '']
        for uid in code.get('evidence_unit_ids', []):
            lines += evidence(lookup[uid], matches.get(uid))
        lines += ['#### Possible qualifications', '', 'Different conditions or modality require scope review; they are not established contradictions.', '']
        for uid in code.get('qualification_unit_ids', []):
            lines += evidence(lookup[uid], matches.get(uid))
        if not code.get('qualification_unit_ids'):
            lines += ['None proposed.', '']
        lines += ['#### Possible countercases', '',
                  'These are retrieval leads, not proven logical contradictions. Compare claims and procedure/context before treating them as counterexamples.', '']
        if not code.get('counterexample_unit_ids'):
            lines += ['None proposed. This does not establish absence of counterevidence.', '']
        for uid in code.get('counterexample_unit_ids', []):
            lines += evidence(lookup[uid], matches.get(uid))
    lines += ['## Candidate categories', '', 'Categories organize code suggestions; they are not established interpretive themes.', '']
    for item in payload.get('categories', []):
        lines += [f"### {_md(item.get('label', 'Untitled category'))}", '', _md(item.get('rationale', '')), '', f"Codes: {_md(item.get('code_ids', []))}", '']
    if not payload.get('categories'):
        lines += ['No candidate categories proposed.', '']
    lines += ['## Interpretive themes', '']
    if not payload.get('interpretive_themes'):
        lines += ['No interpretive themes generated. Candidate codes and categories do not establish an organizing interpretation.', '']
    for item in payload.get('interpretive_themes', []):
        lines += [_md(item), '']
    for title, key in [('Unassigned passages', 'unassigned_unit_ids'), ('Flagged passages', 'flagged_unit_ids'), ('Rare passages explicitly retained', 'rare_unit_ids')]:
        lines += [f'## {title}', '', 'Unassigned does not mean irrelevant; flags are routing heuristics, not a finding.', '']
        for uid in payload.get(key, []):
            lines += evidence(lookup[uid])
        if not payload.get(key):
            lines += ['None listed.', '']
    lines += ['## Complete source-unit archive', '', 'Includes every retained unit, including rare or single-source accounts. Frequency does not establish importance.', '']
    for unit in lookup.values():
        lines += evidence(unit)
    if changed:
        lines += ['## Current workspace coding', '', 'These records reflect workspace edits, separately from the generated discovery snapshot. Quote text is retained from current workspace records; source identity and offsets are recorded metadata, not a new raw-source verification.', '']
        for theme in result.themes:
            lines += [f'### {_md(theme.name)}', '', f"Definition: {_md(theme.code_definition.description if theme.code_definition else 'Not recorded')}", '', f'Memo: {_md(theme.memo)}', '']
            for item in theme.quotes:
                lines += [_quote(item.text), '', f"{_md(item.source_name)} / {_md(item.speaker)}; characters {item.source_start}:{item.source_end}; SHA-256 {_md(item.source_hash)}; quote ID {_md(item.quote_id)}", '', f'Quote memo: {_md(item.memo)}', '', 'Recorded context:', '', _quote(item.context_text), '']
    lines += ['## Run notes', '', _md(payload.get('notes', []) + result.notes), '', '## Technical provenance', '', _md(_metadata(result, payload)), '']
    return '\n'.join(lines)


_STYLE = """body{margin:0;background:#f5f6f8;color:#17212c;font:16px/1.6 system-ui,sans-serif}main{max-width:1080px;margin:auto;padding:32px 24px}header,article,.panel{background:white;border:1px solid #dbe0e7;border-radius:12px;padding:24px;margin:18px 0}h1,h2,h3{line-height:1.25}h2{margin-top:40px}h3{margin-top:0}a{color:#1751a3}nav{display:flex;gap:18px;flex-wrap:wrap}blockquote{border-left:3px solid #4b79a8;margin:12px 0;padding:8px 18px;white-space:pre-wrap;background:#f4f7fa}summary{cursor:pointer;font-weight:600}details{margin:12px 0}small,.muted{color:#526273}.badge{display:inline-block;border:1px solid #bacbdf;border-radius:16px;padding:2px 10px;font-size:13px}.warning{border-left:4px solid #b77b15;padding:10px 16px;background:#fff7e8}.scope{display:grid;grid-template-columns:1fr 1fr;gap:20px}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:13px/1.5 ui-monospace,monospace}p,li{overflow-wrap:anywhere}.quote{margin:20px 0;padding-top:12px;border-top:1px solid #e4e8ed}@media(max-width:650px){main{padding:16px 12px}.scope{grid-template-columns:1fr}article,header,.panel{padding:18px}}"""


def export_discovery_html(result: AnalysisResult) -> str:
    """Produce a self-contained, escaped report with native collapsed context."""
    payload, lookup = _checked(result)
    questions, lens = _context(payload)
    changed = _workspace_changed(result, payload)
    e = lambda value: escape(_text(value), quote=True)
    def paragraph(label, value):
        return f'<p><strong>{e(label)}</strong> {e(_display(value))}</p>'
    def detail(title, value):
        return f'<details><summary>{e(title)}</summary><pre>{e(value)}</pre></details>'
    def short_source(value):
        return str(value).replace('\\', '/').rsplit('/', 1)[-1]
    def quote(unit, match=None):
        flags = ', '.join(unit.get('flags', [])) or 'None'
        metadata = dict(source_name=unit['source_name'], unit_id=unit['id'], source_start=unit['source_start'], source_end=unit['source_end'], source_hash=unit.get('source_hash'), eligible=unit.get('eligible'), flags=unit.get('flags', []))
        assertion = (paragraph('Retrieved assertion (review its fit):', '') + f'<blockquote>{e(match["text"])}</blockquote>'
                     + paragraph('Retrieval role:', match.get('role', 'unspecified'))
                     + detail('Full participant turn', unit['text']) if match else f'<blockquote>{e(unit["text"])}</blockquote>')
        return ('<section class="quote">' + paragraph('Source / speaker:', short_source(unit['source_name']) + ' / ' + unit['speaker'])
                + assertion + paragraph('Flags:', flags)
                + detail('Reference question/context (not participant evidence)', unit.get('reference_context', ''))
                + detail('Full source context', unit.get('context_text', ''))
                + detail('Source identity and character offsets', metadata) + '</section>')
    parts = ['<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">',
             '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; base-uri \'none\'; form-action \'none\'">',
             f'<title>Candidate codebook</title><style>{_STYLE}</style></head><body><main>',
             '<header><span class="badge">Local extractive draft</span><h1>Candidate codebook</h1>',
             '<p>Read the proposed scope, participant evidence and possible qualifications together. Definitions are extractive drafts anchored in source wording, not learned semantic definitions or approved interpretations.</p>',
             paragraph('Actual mode:', payload.get('mode', 'unspecified')), paragraph('Research questions:', questions or 'Not supplied'), paragraph('Theoretical lens:', lens),
             '<p>In guided mode, context affects retrieval and ranking. It does not generate latent interpretation. In open mode, supplied context does not guide discovery. Context relevance is not confidence or coding accuracy.</p>',
             '<nav><a href="#codes">Codes</a><a href="#categories">Categories</a><a href="#themes">Interpretive themes</a><a href="#retained">Unassigned and flagged</a><a href="#archive">All source units</a></nav></header>',
             f'<h2 id="codes">Candidate codes ({len(payload.get("codes", []))})</h2>']
    if changed:
        parts.insert(4, '<p class="warning"><strong>Generated snapshot predates workspace edits.</strong> Original candidate records are retained below. The separate <a href="#workspace">Current workspace coding</a> section preserves the researcher edits.</p>')
    for number, code in enumerate(payload.get('codes', []), 1):
        matches = {match['unit_id']: match for match in code.get('matches', [])}
        people = _participants(code, lookup)
        rare = len(people) <= 1
        parts += [f'<article><span class="badge">Candidate code {number}</span>',
                  '<p class="warning">Single source/speaker evidence. Retain as a possible rare account; broader support is not established.</p>' if rare else '',
                  f'<h3>{e(code["label"])}</h3>', paragraph('Extractive draft definition:', code.get('definition', '')),
                  '<div class="scope"><div>' + paragraph('Include:', code.get('inclusion_criteria', '')) + '</div><div>' + paragraph('Exclude:', code.get('exclusion_criteria', '')) + '</div></div>',
                  paragraph('Scope terms:', code.get('scope_terms', [])), detail(f'Contributing source/speaker pairs ({len(people)})', people),
                  paragraph('Uncertainty:', code.get('uncertainty') or 'No additional warning recorded; review still required.'),
                  '<details><summary>Exact definition anchor</summary>' + f'<blockquote>{e(code["anchor_text"])}</blockquote>' + paragraph('Anchor source:', lookup[code['anchor_unit_id']]['source_name'] + ' / ' + lookup[code['anchor_unit_id']]['speaker']) + paragraph('Source characters:', f"{code['anchor_source_start']}:{code['anchor_source_end']}") + '</details>',
                  f'<h4>Candidate supporting passages ({len(code.get("evidence_unit_ids", []))})</h4>']
        parts += [quote(lookup[uid], matches.get(uid)) for uid in code.get('evidence_unit_ids', [])]
        parts += [f'<h4>Possible qualifications ({len(code.get("qualification_unit_ids", []))})</h4>', '<p>Different conditions or modality require scope review. They are not established contradictions.</p>']
        parts += [quote(lookup[uid], matches.get(uid)) for uid in code.get('qualification_unit_ids', [])]
        parts += [f'<h4>Possible countercases ({len(code.get("counterexample_unit_ids", []))})</h4>',
                  '<p>These are retrieval leads, not proven logical contradictions. Check the meaning, referent and context before treating them as counterexamples.</p>']
        parts += [quote(lookup[uid], matches.get(uid)) for uid in code.get('counterexample_unit_ids', [])]
        if not code.get('counterexample_unit_ids'):
            parts += ['<p>None proposed. This does not establish absence of counterevidence.</p>']
        parts += [detail('Candidate provenance and ranking (not confidence)', {key:code.get(key) for key in ('id','origin','definition_kind','context_relevance')}), '</article>']
    if not payload.get('codes'):
        parts += ['<p>No candidate codes proposed.</p>']
    parts += ['<h2 id="categories">Candidate categories</h2><p>Categories organize code suggestions. They are not established interpretive themes.</p>']
    for item in payload.get('categories', []):
        parts += ['<section class="panel">', f'<h3>{e(item.get("label", "Untitled category"))}</h3>', paragraph('Rationale:', item.get('rationale', '')),
                  paragraph('Candidate codes:', [next(code['label'] for code in payload['codes'] if code['id'] == cid) for cid in item.get('code_ids', [])]), '</section>']
    if not payload.get('categories'):
        parts += ['<p>No candidate categories proposed.</p>']
    parts += ['<h2 id="themes">Interpretive themes</h2>']
    if not payload.get('interpretive_themes'):
        parts += ['<p>No interpretive themes generated. Candidate codes and categories do not establish an organizing interpretation.</p>']
    else:
        parts += [detail('Proposed interpretive theme', item) for item in payload['interpretive_themes']]
    parts += ['<h2 id="retained">Unassigned, flagged and rare passages</h2><p>Unassigned does not mean irrelevant. Flags route review; frequency does not establish importance. Every source unit remains in the archive below.</p>']
    for title, key in [('Unassigned passages', 'unassigned_unit_ids'), ('Flagged passages', 'flagged_unit_ids'), ('Rare passages explicitly retained', 'rare_unit_ids')]:
        ids = payload.get(key, [])
        parts += [f'<details class="panel"><summary>{e(title)} ({len(ids)})</summary>', *[quote(lookup[uid]) for uid in ids], '</details>']
    parts += [f'<details class="panel" id="archive"><summary>Complete source-unit archive ({len(lookup)})</summary>', *[quote(unit) for unit in lookup.values()], '</details>',
              detail('Run notes and limitations', payload.get('notes', []) + result.notes), detail('Technical run provenance', _metadata(result, payload)),
              '</main></body></html>']
    if changed:
        workspace = ['<h2 id="workspace">Current workspace coding</h2><p>These records reflect workspace edits separately from the generated discovery snapshot. Quote text comes from current workspace records. Source hashes and offsets are recorded metadata, not a new raw-source verification.</p>']
        for theme in result.themes:
            workspace += [f'<article><h3>{e(theme.name)}</h3>', paragraph('Definition:', theme.code_definition.description if theme.code_definition else 'Not recorded'), paragraph('Memo:', theme.memo)]
            for item in theme.quotes:
                workspace += [f'<blockquote>{e(item.text)}</blockquote>', paragraph('Source / recorded speaker:', item.source_name + ' / ' + item.speaker), paragraph('Quote memo:', item.memo), detail('Recorded source context', item.context_text), detail('Workspace quote identity and offsets', dict(quote_id=item.quote_id, source_start=item.source_start, source_end=item.source_end, source_hash=item.source_hash))]
            workspace += ['</article>']
        parts[-1:-1] = workspace
    return ''.join(parts)

"""Exact-source review cards, separate from codes, concepts and model inference."""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from html import escape
import json
import re

from .analysis_types import TranscriptDocument
from .codebook_contracts import _invalid_constant, _unique_object
from .discovery_units import build_meaning_units
from .generation_schema import build_span_registry
from .local_codebook import validate_prepared, validate_result_sources
from .model_pilot import _digest

VERSION = 'themeforge.source-cards.v1'
ARTIFACT_TYPE = 'exact_source_cards'
MAX_ARTIFACT_BYTES = 32 * 1024 * 1024
_SOURCE_FIELDS = ('source_documents', 'registry', 'unit_archive', 'selected_unit_ids', 'speaker_label_policy')
_FIXED = {'version': VERSION, 'artifact_type': ARTIFACT_TYPE, 'origin': 'host_exact_source',
          'exhaustive': False, 'calls': [], 'interpretation_authorship': None,
          'ai_generated_interpretation': False, 'human_coding': False,
          'notice': 'Exact-source cards support reading and traceability. They are not conceptual codes, definitions, themes or semantic approval.'}


def _sources(value):
    documents = value['source_documents']
    if type(documents) is not list or any(type(row) is not dict or set(row) != {'name', 'text', 'sha256'} for row in documents):
        raise ValueError('Cards require original source documents with names, exact text and hashes.')
    if any(type(row['name']) is not str or not row['name'] or type(row['text']) is not str
           or sha256(row['text'].encode('utf-8')).hexdigest() != row['sha256'] for row in documents):
        raise ValueError('Source document text or hash differs from its saved version.')
    if sum(len(row['text'].encode('utf-8')) for row in documents) > 16 * 1024 * 1024:
        raise ValueError('Source documents exceed the existing 16 MiB bound.')
    docs = [TranscriptDocument(row['name'], row['text']) for row in documents]
    units = build_meaning_units(docs, speaker_label_policy=value['speaker_label_policy'])
    if _digest(value['unit_archive']) != _digest(units):
        raise ValueError('Saved source turns or context differ from the original text.')
    registry = build_span_registry(units, docs, speaker_label_policy=value['speaker_label_policy'])
    ids = value['selected_unit_ids']
    eligible = {entry['unit_id'] for entry in registry.values()}
    if (type(ids) is not list or len(ids) > 12 or any(type(uid) is not str or uid not in eligible for uid in ids)
            or len(set(ids)) != len(ids)):
        raise ValueError('Cards require at most 12 unique, eligible original turn IDs.')
    expected = {sid: entry for sid, entry in registry.items() if entry['unit_id'] in ids}
    if _digest(value['registry']) != _digest(expected):
        raise ValueError('Source registry is stale, incomplete or outside the selected turns.')
    return {unit['id']: unit for unit in units}


def _cards(value, units):
    cards = []
    for uid in value['selected_unit_ids']:
        unit = units[uid]
        ids = [sid for sid, entry in value['registry'].items() if entry['unit_id'] == uid]
        cards.append({'card_id': 'SC-' + _digest([VERSION, uid])[:20], 'kind': 'source_card',
            'origin': 'host_exact_source', 'unit_id': uid, 'source_name': unit['source_name'],
            'source_hash': unit['source_hash'], 'speaker': unit['speaker'],
            'source_start': unit['source_start'], 'source_end': unit['source_end'], 'text': unit['text'],
            'support_span_ids': ids, 'support_spans': [dict(span_id=sid, **deepcopy(value['registry'][sid])) for sid in ids],
            'context': {'role': 'context_not_additional_support', 'source_start': unit['context_start'],
                        'source_end': unit['context_end'], 'text': unit['context_text'],
                        'reference_context': unit['reference_context']},
            'conceptual_label': None, 'definition': None, 'exhaustive': False, 'interpretation_authorship': None})
    return cards


def build_source_cards(prepared_or_result):
    """Validate a native packet/result first; copy only its immutable sources.

    This never calls a model or imports generated labels into the source-card
    contract. The supplied object, original calls and source text stay unchanged.
    """
    if type(prepared_or_result) is not dict:
        raise ValueError('Supply an existing prepared packet or native generated result object.')
    original = prepared_or_result
    if 'result_sha256' in original:
        validate_result_sources(original)
        kind, digest = 'generated_result', original['result_sha256']
    else:
        validate_prepared(original)
        kind, digest = 'prepared_packet', original['packet_sha256']
    if type(original.get('version')) is not str or not original['version']:
        raise ValueError('Original artifact version is required.')
    result = deepcopy(_FIXED)
    result.update({field: deepcopy(original[field]) for field in _SOURCE_FIELDS if field != 'speaker_label_policy'})
    result['speaker_label_policy'] = original.get('speaker_label_policy', 'legacy')
    units = _sources(result)
    result['input_artifact'] = {'kind': kind, 'version': original['version'], 'sha256': digest}
    result['cards'] = _cards(result, units)
    result['cards_sha256'] = _digest(result)
    return validate_cards(result)


def validate_cards(raw):
    """Validate standalone JSON provenance, without accepting native code IDs."""
    try:
        if type(raw) is dict:
            encoded = json.dumps(raw, ensure_ascii=False, allow_nan=False).encode('utf-8')
            value = deepcopy(raw)
        elif type(raw) in (str, bytes):
            encoded = raw.encode('utf-8') if type(raw) is str else raw
            if len(encoded) > MAX_ARTIFACT_BYTES:
                raise ValueError('Source-card JSON exceeds 32 MiB.')
            value = json.loads(encoded.decode('utf-8'), object_pairs_hook=_unique_object, parse_constant=_invalid_constant)
        else:
            raise ValueError('Source cards must be a JSON object, string or UTF-8 bytes.')
        if len(encoded) > MAX_ARTIFACT_BYTES:
            raise ValueError('Source-card JSON exceeds 32 MiB.')
        expected_fields = {*_FIXED, *_SOURCE_FIELDS, 'input_artifact', 'cards', 'cards_sha256'}
        if (type(value) is not dict or set(value) != expected_fields
                or any(type(value[key]) is not type(expected) or value[key] != expected for key, expected in _FIXED.items())):
            raise ValueError('This is a separate exact-source card artifact, not a native codebook or coding result.')
        if value['cards_sha256'] != _digest({key: entry for key, entry in value.items() if key != 'cards_sha256'}):
            raise ValueError('Source-card digest mismatch.')
        provenance = value['input_artifact']
        if (type(provenance) is not dict or set(provenance) != {'kind', 'version', 'sha256'}
                or provenance['kind'] not in {'prepared_packet', 'generated_result'}
                or type(provenance['version']) is not str or not provenance['version']
                or type(provenance['sha256']) is not str or not re.fullmatch('[0-9a-f]{64}', provenance['sha256'])):
            raise ValueError('Original artifact provenance is malformed.')
        units = _sources(value)
        if _digest(value['cards']) != _digest(_cards(value, units)):
            raise ValueError('Source cards must retain exact selected turns, spans and context with no conceptual fields.')
        return value
    except (KeyError, TypeError, AttributeError, RecursionError) as error:
        raise ValueError('Source-card artifact is malformed or incomplete.') from error


def export_cards_html(cards):
    """Self-contained escaped source review, with no conceptual code headings."""
    value = validate_cards(cards)
    e = lambda item: escape(str(item), quote=True)
    parts = ['<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">',
        '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; base-uri \'none\'; form-action \'none\'">',
        '<title>Exact-source review cards</title><style>body{font:16px/1.55 system-ui;max-width:1100px;margin:auto;padding:24px}article{border:1px solid #cad4df;padding:18px;margin:16px 0}blockquote,pre{white-space:pre-wrap;overflow-wrap:anywhere}blockquote{border-left:3px solid #5480ad;padding-left:12px}</style>',
        '<h1>Exact-source review cards</h1>', f'<p>{e(value["notice"])}</p>',
        '<p>Origin: host_exact_source. No model calls, AI interpretation or human coding was performed to create these cards. Selection is bounded; coverage is not exhaustive.</p>']
    for number, card in enumerate(value['cards'], 1):
        parts += [f'<article><h2>Source card {number}: {e(card["source_name"])}</h2>',
            f'<p>{e(card["speaker"])}; characters {card["source_start"]}:{card["source_end"]}; SHA-256 {e(card["source_hash"])}</p>',
            f'<blockquote>{e(card["text"])}</blockquote>', '<h3>Exact source spans</h3>']
        for span in card['support_spans']:
            parts += [f'<p>{e(span["span_id"])}; characters {span["source_start"]}:{span["source_end"]}</p><blockquote>{e(span["text"])}</blockquote>']
        parts += ['<details><summary>Context, not additional supporting evidence</summary>',
            f'<p>Characters {card["context"]["source_start"]}:{card["context"]["source_end"]}</p><pre>{e(card["context"]["text"])}</pre>',
            f'<p>Reference context:</p><pre>{e(card["context"]["reference_context"])}</pre></details></article>']
    parts += ['<details><summary>Source artifact provenance</summary><pre>',
              e(json.dumps(value['input_artifact'], ensure_ascii=False, indent=2)), '</pre></details></html>']
    return ''.join(parts)

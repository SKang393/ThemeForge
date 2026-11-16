"""Strict source-ID grounding for unreviewed local-model code drafts.

This verifies syntax, batch accountability and immutable source selections. It
does not verify whether a proposed interpretation fits the cited evidence.
"""
from __future__ import annotations

from collections.abc import Sequence
from copy import deepcopy
from hashlib import sha256
import json
import re

from .analysis_types import TranscriptDocument
from .discovery_units import build_meaning_units

VERSION = "themeforge.local-codes.v1"
MAX_RESPONSE_BYTES = 262_144
_LIMITS = {"id": 64, "label": 240, "definition": 2000, "inclusion": 1200, "exclusion": 1200, "uncertainty": 1200}
_ROLES = ("evidence_span_ids", "qualification_span_ids", "counterexample_span_ids")
_CODE_KEYS = frozenset((*_LIMITS, *_ROLES))


def _string(value, name: str, limit: int = 256) -> str:
    if type(value) is not str or not value.strip() or len(value) > limit:
        raise ValueError(f"{name} must be a nonblank string of at most {limit} characters.")
    return value


def _ids(values: Sequence[str], name: str) -> list[str]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise ValueError(f"{name} must be an ID sequence.")
    result = [_string(value, name) for value in values]
    if len(set(result)) != len(result):
        raise ValueError(f"Duplicate IDs in {name}.")
    return result


def _cap(max_codes: int) -> None:
    if type(max_codes) is not int or not 1 <= max_codes <= 32:
        raise ValueError("max_codes must be an integer from 1 to 32.")


def _span(record: dict, source: str, label: str) -> tuple[int, int]:
    start, end, text = record.get("source_start"), record.get("source_end"), record.get("text")
    if (type(start) is not int or type(end) is not int or type(text) is not str
            or not 0 <= start < end <= len(source) or source[start:end] != text):
        raise ValueError(f"{label} does not resolve to an exact source span.")
    return start, end


def _span_id(entry: dict) -> str:
    identity = json.dumps([entry[key] for key in ("unit_id", "source_name", "source_hash", "source_start", "source_end")], ensure_ascii=False)
    return "SP-" + sha256(identity.encode("utf-8")).hexdigest()[:24]


def build_span_registry(units: Sequence[dict], documents: Sequence[TranscriptDocument], *, speaker_label_policy="legacy") -> dict[str, dict]:
    """Build host-owned citable sentence IDs; context never becomes evidence.

    Eligible source turns are independently checked against the existing unitizer
    so changing a supplied role/eligibility flag cannot promote an interviewer or
    a context-only source slice. Missing sentence lists fall back to the full turn.
    Keep this registry with its batch; models author no quotations or offsets.
    """
    names = _ids([document.name for document in documents], "source names")
    sources = {name: document.text for name, document in zip(names, documents)}
    canonical = {(unit["source_name"], unit["source_start"], unit["source_end"]): unit
                 for unit in build_meaning_units(documents, speaker_label_policy=speaker_label_policy)}
    registry: dict[str, dict] = {}
    seen_ids, seen_ranges = set(), set()
    for unit in units:
        if type(unit) is not dict:
            raise ValueError("Meaning units must be objects.")
        uid = _string(unit.get("id"), "unit id")
        name = _string(unit.get("source_name"), "source name")
        if uid in seen_ids:
            raise ValueError("Duplicate unit ID.")
        seen_ids.add(uid)
        if name not in sources:
            raise ValueError(f"Unknown source for unit {uid}.")
        source = sources[name]
        start, end = _span(unit, source, f"Unit {uid}")
        key = (name, start, end)
        if key in seen_ranges:
            raise ValueError("Duplicate source unit range.")
        seen_ranges.add(key)
        if unit.get("source_hash") != sha256(source.encode("utf-8")).hexdigest():
            raise ValueError(f"Stale source hash for unit {uid}.")
        original = canonical.get(key)
        if original is None or unit.get("speaker") != original["speaker"]:
            raise ValueError(f"Unit {uid} is not a verified source turn; context cannot be promoted to evidence.")
        if type(unit.get("eligible")) is not bool:
            raise ValueError("Unit eligibility must be a boolean supplied by the host.")
        if not unit["eligible"] or not original["eligible"]:
            continue
        for field in ("context_start", "context_end", "context_text", "reference_context"):
            if unit.get(field) != original[field]:
                raise ValueError(f"Unit {uid} has inconsistent source context.")
        sentences = unit.get("sentences")
        if sentences is None or sentences == []:
            sentences = [{"text": unit["text"], "source_start": start, "source_end": end}]
        if type(sentences) is not list:
            raise ValueError("Unit sentences must be an array.")
        sentence_ranges = []
        for sentence in sentences:
            if type(sentence) is not dict:
                raise ValueError("Sentence spans must be objects.")
            left, right = _span(sentence, source, f"Sentence in {uid}")
            if not start <= left < right <= end:
                raise ValueError("Sentence lies outside its target turn; context cannot be cited.")
            if any(left < previous_end and previous_start < right for previous_start, previous_end in sentence_ranges):
                raise ValueError("Duplicate or overlapping sentence ranges.")
            sentence_ranges.append((left, right))
            entry = {"unit_id": uid, "source_name": name, "source_hash": unit["source_hash"],
                     "source_start": left, "source_end": right, "text": sentence["text"],
                     "speaker": original["speaker"], "unit_source_start": start, "unit_source_end": end,
                     "unit_text": unit["text"], "context_start": original["context_start"],
                     "context_end": original["context_end"], "context_text": original["context_text"],
                     "reference_context": original["reference_context"], "flags": list(original["flags"])}
            sid = _span_id(entry)
            if sid in registry:
                raise ValueError("Duplicate span ID.")
            registry[sid] = entry
    return dict(sorted(registry.items()))


def candidate_json_schema(span_ids: Sequence[str], unit_ids: Sequence[str], max_codes: int = 3) -> dict:
    """Return a bounded JSON Schema; cross-field source checks remain mandatory."""
    _cap(max_codes)
    spans, units = sorted(_ids(span_ids, "span IDs")), sorted(_ids(unit_ids, "unit IDs"))
    def selection(ids, minimum=0):
        return {"type": "array", "minItems": minimum, "maxItems": len(ids), "uniqueItems": True,
                "items": {"type": "string", "enum": ids} if ids else {"not": {}}}
    properties = {key: {"type": "string", "minLength": 1, "maxLength": limit} for key, limit in _LIMITS.items()}
    properties["id"]["pattern"] = r"^[A-Za-z][A-Za-z0-9_.:-]{0,63}$"
    properties.update({key: selection(spans, int(key == "evidence_span_ids")) for key in _ROLES})
    return {"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object", "additionalProperties": False,
            "required": ["version", "codes", "no_code"], "properties": {
                "version": {"type": "string", "const": VERSION},
                "codes": {"type": "array", "maxItems": max_codes, "items": {"type": "object", "additionalProperties": False,
                          "required": sorted(_CODE_KEYS), "properties": properties}},
                "no_code": {"type": "array", "maxItems": len(units), "items": {"type": "object", "additionalProperties": False,
                            "required": ["unit_id", "reason"], "properties": {
                                "unit_id": {"type": "string", "enum": units} if units else {"not": {}},
                                "reason": {"type": "string", "minLength": 1, "maxLength": 600}}}}}}


def _object(value, keys, label):
    if type(value) is not dict or set(value) != set(keys):
        raise ValueError(f"{label} must contain exactly: {', '.join(sorted(keys))}.")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}.")
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError(f"Nonfinite JSON value: {value}.")


def _json_values(value):
    if type(value) is dict:
        for key, child in value.items():
            if type(key) is not str:
                raise ValueError("JSON keys must be strings.")
            _json_values(child)
    elif type(value) is list:
        for child in value:
            _json_values(child)
    elif type(value) is not str:
        raise ValueError("Response values must be strings, arrays or objects; booleans/numbers/null are not allowed.")


def validate_candidate_response(raw: str | dict, registry: dict, expected_unit_ids: Sequence[str], max_codes: int = 3) -> dict:
    """Resolve model span selections, rejecting ungrounded or unaccounted output.

    The supplied registry must be host-owned output of ``build_span_registry``.
    Validation is deliberately not an interpretation, approval, training label or
    independent assessment of evidence relevance, completeness or semantic fit.
    """
    _cap(max_codes)
    expected = set(_ids(expected_unit_ids, "expected unit IDs"))
    try:
        if type(raw) is str:
            if len(raw.encode("utf-8")) > MAX_RESPONSE_BYTES:
                raise ValueError("Candidate response exceeds the 256 KiB limit.")
            response = json.loads(raw, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
        elif type(raw) is dict:
            response = deepcopy(raw)
        else:
            raise ValueError("Candidate response must be a JSON string or object.")
        _json_values(response)
        if len(json.dumps(response, ensure_ascii=False).encode("utf-8")) > MAX_RESPONSE_BYTES:
            raise ValueError("Candidate response exceeds the 256 KiB limit.")
    except (RecursionError, UnicodeError) as error:
        raise ValueError("Candidate response is not bounded valid Unicode JSON.") from error
    _object(response, ("version", "codes", "no_code"), "Candidate response")
    if response["version"] != VERSION:
        raise ValueError("Unsupported candidate response version.")
    if type(response["codes"]) is not list or len(response["codes"]) > max_codes:
        raise ValueError("Codes must be an array within max_codes.")
    if type(response["no_code"]) is not list or len(response["no_code"]) > len(expected):
        raise ValueError("no_code must be a bounded array.")
    if type(registry) is not dict:
        raise ValueError("Registry must be a host-owned span mapping.")
    for sid, entry in registry.items():
        try:
            valid_identity = type(entry) is dict and sid == _span_id(entry)
        except (KeyError, TypeError, UnicodeError) as error:
            raise ValueError("Registry entry is malformed.") from error
        if not valid_identity:
            raise ValueError("Registry span identity is inconsistent.")
        if entry.get("unit_id") not in expected:
            continue  # A larger registry may be supplied; selected IDs must stay batch-scoped.
        left, right = entry["source_start"], entry["source_end"]
        if (type(left) is not int or type(right) is not int or not entry["unit_source_start"] <= left < right <= entry["unit_source_end"]
                or entry["unit_text"][left-entry["unit_source_start"]:right-entry["unit_source_start"]] != entry["text"]):
            raise ValueError("Registry text no longer resolves to its target turn.")
    if not expected <= {entry["unit_id"] for entry in registry.values()}:
        raise ValueError("Expected units must have verified registry spans.")
    assigned, code_ids = set(), set()
    for code in response["codes"]:
        _object(code, _CODE_KEYS, "Code")
        for key, limit in _LIMITS.items():
            _string(code[key], key, limit)
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.:-]{0,63}", code["id"]) or code["id"] in code_ids:
            raise ValueError("Code IDs must be valid and unique within the batch.")
        code_ids.add(code["id"])
        selected = set()
        for role, resolved in zip(_ROLES, ("evidence", "qualifications", "counterexamples")):
            if type(code[role]) is not list:
                raise ValueError(f"{role} must be an array.")
            ids = _ids(code[role], role)
            if role == "evidence_span_ids" and not ids:
                raise ValueError("Each code requires evidence.")
            if selected.intersection(ids):
                raise ValueError("The same span cannot occupy multiple roles in one code.")
            selected.update(ids)
            entries = []
            for sid in ids:
                if sid not in registry or registry[sid]["unit_id"] not in expected:
                    raise ValueError(f"Unknown or out-of-batch span ID: {sid}.")
                entries.append(deepcopy(registry[sid]))
                if role == "evidence_span_ids":
                    assigned.add(registry[sid]["unit_id"])
            code[resolved] = entries
        code["review_status"] = "unreviewed_machine_draft"
    unassigned = set()
    for item in response["no_code"]:
        _object(item, ("unit_id", "reason"), "no_code item")
        uid = _string(item["unit_id"], "no_code unit ID")
        _string(item["reason"], "no_code reason", 600)
        if uid not in expected or uid in unassigned:
            raise ValueError("Unknown or duplicate no_code unit ID.")
        unassigned.add(uid)
    if assigned & unassigned:
        raise ValueError("An evidence-assigned unit cannot also be marked no_code.")
    if assigned | unassigned != expected:
        raise ValueError("Every expected unit requires evidence assignment or an explicit no_code reason.")
    response["review_status"] = "unreviewed_machine_draft"
    response["grounding_status"] = "source_spans_verified_not_semantic_fit"
    return response

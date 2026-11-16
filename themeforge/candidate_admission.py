"""Experimental deterministic provenance gate, not semantic validation.

Only leading named reporting constructions are compared with explicit transcript
labels. Reported actors may differ from speakers. Unknowns remain visible and
withheld; no model, identity resolution, content repair or theme inference occurs.
"""
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
from html import escape
import json
import re

from .role_evidence import (build_role_evidence, source_span,
                            LEGACY_VERSION as LEGACY_ROLE_VERSION, VERSION as ROLE_VERSION)

LEGACY_VERSION = "themeforge.experimental-candidate-admission.v1.1"
VERSION = "themeforge.experimental-candidate-admission.v1.2"
SUPPORTED_VERSIONS = (LEGACY_VERSION, VERSION)
SCOPE = "Leading reporting-subject/source-label consistency only; meaning, modality and theme quality unverified."
_SUBJECT = r"(?:Participant[ \t]*#?[ \t]*\d+|Interviewer(?:[ \t]*#?[ \t]*\d+)?)"
_VERB = r"(?:says|said|states|stated|reports|reported|asks|asked|notes|noted|describes|described)"
_LEADING = re.compile(r"^\s*(?P<subject>" + _SUBJECT + r")[ \t]+(?P<verb>" + _VERB + r")\b", re.I)
_EMBEDDED = re.compile(r"(?<!\w)(?P<subject>" + _SUBJECT + r")[ \t]+" + _VERB + r"\b", re.I)
_SPAN_KEYS = ("source_name", "source_hash", "source_start", "source_end", "text")
_EVENT_KEYS = {"id", "decision", "reviewer_kind", "author", "reason", "created_utc", "before_record_sha256"}


def _digest(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _seal(record):
    record["record_sha256"] = _digest({k: v for k, v in record.items() if k != "record_sha256"})
    return record


def _label(value):
    match = re.fullmatch(r"(Participant|Interviewer)[ \t#]*(\d+)?", value, re.I)
    return match[1].capitalize() + (" " + str(int(match[2])) if match[2] else "")


def _text(value, field, limit=2000):
    if type(value) is not str or not value.strip() or len(value) > limit:
        raise ValueError(f"{field} requires nonblank text of at most {limit} characters.")


def _assess(original, source, version):
    checks, reasons = [], []
    result = dict(state="abstain", source_proof=False, source_speaker=None,
                  imported=None, checks=checks, reasons=reasons, scope=SCOPE)
    try:
        target = original["target"]
        imported = build_role_evidence(target, source, contexts=original["contexts"],
            version=LEGACY_ROLE_VERSION if version == LEGACY_VERSION else ROLE_VERSION)["imported"]
        if not original["evidence"] or len(original["evidence"]) > 12:
            raise ValueError("One to twelve exact target support spans required.")
        for span in original["evidence"]:
            actual = source_span(source, span["source_start"], span["source_end"])
            if (any(span.get(k) != actual[k] for k in _SPAN_KEYS)
                    or not target["source_start"] <= actual["source_start"] < actual["source_end"] <= target["source_end"]):
                raise ValueError("Support does not reconstruct inside its exact target turn.")
        result.update(source_proof=True, imported=imported, source_speaker=imported["source_speaker"])
    except (ValueError, TypeError, KeyError) as error:
        reasons.append(dict(code="source_proof_unavailable", detail=str(error)))
        return result
    speaker = result["source_speaker"]
    if imported["saved_label_conflict"]:
        # The limited source parser may carry a recognized heading across an
        # unsupported one. Preserve both observations instead of presenting its
        # inherited label as the verified speaker or guessing a replacement.
        result["source_speaker"] = dict(deepcopy(speaker), status="conflicting_source_metadata",
                                        source_local_label=None)
        result["state"] = "review_needed"
        reasons.append(dict(code="saved_label_conflict", detail="Saved speaker metadata differs from the structural parser's label. Comparison speaker is unresolved; inspect both labels and exact source headers. Neither label was silently substituted."))
        return result
    if speaker["status"] != "explicit_source_label":
        reasons.append(dict(code="source_speaker_unresolved", detail="Unknown or multiple source labels; no person inferred."))
        return result
    expected = speaker["source_local_label"]
    if expected.startswith("Interviewer") or speaker.get("structural_role") == "facilitator":
        reasons.append(dict(code="interviewer_context", detail="Interviewer turns remain context, not admitted participant evidence."))
        return result
    # This literal cue only establishes that an alternative reported-role reading
    # is possible. It does not establish who actually spoke the embedded words.
    embedded = {_label(m["subject"]) for m in _EMBEDDED.finditer(target["text"])}
    for field in ("label", "claim", "definition"):
        prose = original["candidate"].get(field, "")
        match = _LEADING.match(prose)
        if not match:
            continue
        named = _label(match["subject"])
        relation = ("matches_imported_label" if named == expected else
                    "possible_reported_actor" if named in embedded else "potential_source_speaker_conflict")
        checks.append(dict(field=field, field_start=match.start("subject"), field_end=match.end("verb"),
                           text=prose[match.start("subject"):match.end("verb")],
                           named_subject=named, imported_speaker=expected, relation=relation,
                           source_label_evidence=deepcopy(speaker["label_evidence"])))
    if any(c["relation"] == "potential_source_speaker_conflict" for c in checks):
        reasons.append(dict(code="reporting_subject_mismatch", detail="Leading named reporting subject differs from source label. Review for source-speaker error versus a legitimate reported actor; this is not an invalidity verdict."))
    if reasons:
        result["state"] = "review_needed"
    elif any(c["relation"] == "possible_reported_actor" for c in checks):
        reasons.append(dict(code="reported_role_uncertain", detail="The other named reporting subject also occurs inside the target. Source speaker versus reported actor remains uncertain."))
    elif not checks:
        reasons.append(dict(code="outside_reporting_subject_scope", detail="No supported leading named reporting construction. Pronouns, quoted/modal wording and other actors were not guessed."))
    else:
        result["state"] = "pass"
        reasons.append(dict(code="narrow_label_consistency", detail="All checkable leading reporting subjects match the source label. Unchecked wording and semantic support remain unvalidated."))
    return result


def create_admission(candidate, target, source, *, evidence, contexts=(), provenance=None, version=VERSION):
    """Return an immutable-original sidecar. Source proof failures stay abstentions."""
    if version not in SUPPORTED_VERSIONS:
        raise ValueError("Unsupported admission version.")
    if type(candidate) is not dict or type(target) is not dict:
        raise ValueError("Candidate and exact target must be objects.")
    present = [field for field in ("label", "claim", "definition") if field in candidate]
    if not present or not any(candidate[field] for field in present):
        raise ValueError("At least one original label, claim or definition is required.")
    for field in present:
        if candidate[field] == "" and field == "definition":
            continue  # Native decision-only code snapshots retain an empty definition.
        _text(candidate[field], field, 8000)
    if type(evidence) is not list or not isinstance(contexts, (list, tuple)):
        raise ValueError("Evidence and contexts must be span lists.")
    original = deepcopy(dict(candidate=candidate, target=target, evidence=evidence,
                             contexts=list(contexts), provenance=provenance or {}))
    if len(json.dumps(original, ensure_ascii=False, allow_nan=False).encode()) > 1024 ** 2:
        raise ValueError("Admission snapshot exceeds 1 MiB; no silent trimming.")
    value = dict(version=version, id="ADM-" + _digest(original)[:24], original=original,
                 assessment=_assess(original, source, version), history=[],
                 semantic_validation=False, training_eligible=False)
    if version == VERSION:
        value["metadata_refresh"] = None
    return _seal(value)


def _role_facts(assessment):
    imported = assessment.get("imported")
    if not imported:
        return None
    def facts(speaker):
        return {key: speaker[key] for key in ("status", "source_local_label", "label_evidence", "observed_labels")}
    return dict(target=facts(imported["source_speaker"]), saved_label_conflict=imported["saved_label_conflict"],
                contexts=[facts(context["source_speaker"]) for context in imported["contexts"]])


def _refresh_note(prior, current):
    changed = _role_facts(prior["assessment"]) != _role_facts(current["assessment"])
    return dict(prior_record=deepcopy(prior), source_role_facts_changed=changed,
                researcher_review_required=changed or bool(prior["history"]), meaning_changed=False)


def _event_valid(event, assessment):
    if type(event) is not dict or set(event) != _EVENT_KEYS:
        raise ValueError("Malformed override event.")
    if event["decision"] not in {"admit", "withhold", "reset"}:
        raise ValueError("Override must admit, withhold or reset.")
    if event["reviewer_kind"] not in {"researcher", "assistant_development_review"}:
        raise ValueError("Explicit researcher or assistant-development authorship required.")
    if event["id"] != "AE-" + _digest({k: v for k, v in event.items() if k != "id"})[:24]:
        raise ValueError("Override event digest changed.")
    if event["reviewer_kind"] != "researcher" and event["decision"] != "withhold":
        raise ValueError("AI review may withhold only; admit/reset require a declared researcher decision.")
    if event["decision"] == "admit":
        if event["reviewer_kind"] != "researcher":
            raise ValueError("AI review cannot release a quarantined candidate as researcher approval.")
        if not assessment["source_proof"]:
            raise ValueError("Cannot override missing or invalid source proof; create a new correctly sourced record.")
    for key in ("author", "reason", "created_utc"):
        _text(event[key], key)
    if datetime.fromisoformat(event["created_utc"]).utcoffset() is None:
        raise ValueError("Override time requires a timezone.")


def validate_admission(record, source):
    """Recompute source/rule result and append chain. Hashes are not signatures."""
    common = {"version", "id", "original", "assessment", "history", "semantic_validation", "training_eligible", "record_sha256"}
    if (type(record) is not dict or record.get("version") not in SUPPORTED_VERSIONS
            or set(record) != common | ({"metadata_refresh"} if record["version"] == VERSION else set())):
        raise ValueError("Malformed admission record.")
    original = record["original"]
    if type(original) is not dict or set(original) != {"candidate", "target", "evidence", "contexts", "provenance"}:
        raise ValueError("Malformed original snapshot.")
    rebuilt = create_admission(source=source, version=record["version"], **original)
    if any(record[k] != rebuilt[k] for k in rebuilt if k not in {"history", "record_sha256", "metadata_refresh"}):
        raise ValueError("Admission assessment or original identity no longer reproduces from source.")
    refresh = record.get("metadata_refresh")
    if refresh is not None:
        if (type(refresh) is not dict or type(refresh.get("prior_record")) is not dict
                or refresh["prior_record"].get("version") != LEGACY_VERSION):
            raise ValueError("Metadata refresh must preserve one original legacy record.")
        prior = refresh["prior_record"]
        validate_admission(prior, source)
        if prior["original"] != original or refresh != _refresh_note(prior, rebuilt):
            raise ValueError("Metadata refresh changed original meaning/evidence or its audit flags.")
        rebuilt["metadata_refresh"] = deepcopy(refresh)
        _seal(rebuilt)
    if type(record["history"]) is not list or len(record["history"]) > 64:
        raise ValueError("At most 64 append-only review events; preserve history in a new branch.")
    if refresh and refresh["researcher_review_required"]:
        if (not record["history"] or record["history"][0].get("decision") != "withhold"
                or record["history"][0].get("reviewer_kind") != "assistant_development_review"):
            raise ValueError("Changed metadata/prior overrides require a new review hold, not inherited approval.")
    for event in record["history"]:
        _event_valid(event, rebuilt["assessment"])
        if event["before_record_sha256"] != rebuilt["record_sha256"]:
            raise ValueError("Override history changed or was reordered.")
        rebuilt["history"].append(deepcopy(event))
        _seal(rebuilt)
    if rebuilt != record:
        raise ValueError("Admission digest or history does not match its reconstruction.")
    return record


def refresh_metadata_admission(record, source):
    """New version, same original/ID. Changed roles never release interpretations.

    Old decisions stay in the complete prior snapshot, not in the new effective
    history. A researcher must reconsider them explicitly after metadata repair.
    """
    validate_admission(record, source)
    if record["version"] != LEGACY_VERSION:
        raise ValueError("Metadata refresh requires a legacy v1.1 record; current records are not replayed.")
    value = create_admission(source=source, **record["original"])
    value["metadata_refresh"] = _refresh_note(record, value)
    _seal(value)
    if value["metadata_refresh"]["researcher_review_required"]:
        event = dict(decision="withhold", reviewer_kind="assistant_development_review",
            author="ThemeForge metadata refresh (AI-assisted development)",
            reason="Source role metadata was reassessed or prior override history exists. Review the unchanged interpretation; no previous human approval is transferred. Prior record: " + record["record_sha256"],
            created_utc=datetime.now(timezone.utc).isoformat(), before_record_sha256=value["record_sha256"])
        event["id"] = "AE-" + _digest(event)[:24]
        value["history"].append(event)
        _seal(value)
    validate_admission(value, source)
    return value


def override_admission(record, source, *, decision, reviewer_kind, author, reason):
    """Append a declared decision, never change the automatic state or wording.

    Researcher authorship is declared by the caller, not authenticated. AI
    records may withhold only; reset can release a previous hold and is therefore
    a researcher decision too.
    """
    validate_admission(record, source)
    value = deepcopy(record)
    event = dict(decision=decision, reviewer_kind=reviewer_kind, author=author, reason=reason,
                 created_utc=datetime.now(timezone.utc).isoformat(), before_record_sha256=record["record_sha256"])
    event["id"] = "AE-" + _digest(event)[:24]
    _event_valid(event, record["assessment"])
    value["history"].append(event)
    _seal(value)
    validate_admission(value, source)
    return value


def effective_admission(record):
    """Read eligibility from a validated record; eligibility is not correctness."""
    state = record["assessment"]["state"]
    eligible = state == "pass" and record["assessment"]["source_proof"]
    decision, origin = None, "deterministic_narrow_check"
    if record["history"]:
        event = record["history"][-1]
        decision = event["decision"]
        if decision != "reset":
            eligible = (decision == "admit" and event["reviewer_kind"] == "researcher"
                        and record["assessment"]["source_proof"])
        origin = event["reviewer_kind"] + "_declared_not_authenticated"
    return dict(state=state, eligible=eligible, decision_origin=origin, override_decision=decision,
                semantic_validation=False, scope=SCOPE)


def grouping_inputs(records, sources, selected_ids):
    """Fail on any quarantined/stale selection; never silently filter candidates."""
    if (type(selected_ids) is not list or not selected_ids or len(set(selected_ids)) != len(selected_ids)
            or any(type(i) is not str for i in selected_ids)):
        raise ValueError("Select unique known admission IDs.")
    lookup = {}
    for record in records:
        if record["id"] in lookup:
            raise ValueError("Duplicate admission ID.")
        lookup[record["id"]] = record
    selected = []
    for identity in selected_ids:
        if identity not in lookup:
            raise ValueError("Unknown admission ID.")
        record = lookup[identity]
        source = sources.get(record["original"]["target"]["source_name"])
        if source is None:
            raise ValueError("Original source required before grouping.")
        validate_admission(record, source)
        if not effective_admission(record)["eligible"]:
            raise ValueError("Quarantined admission requires explicit researcher review: " + identity)
        selected.append(record)
    return dict(candidates=[deepcopy(r["original"]["candidate"]) for r in selected],
                admission_records=deepcopy(selected), scope=SCOPE)


def build_native_admissions(result):
    """Adapt an existing native draft without changing its codes or evidence."""
    from .local_codebook import validate_result_sources
    validate_result_sources(result)
    units = {u["id"]: u for u in result["unit_archive"]}
    sources = {d["name"]: dict(source_name=d["name"], source_hash=d["sha256"], text=d["text"])
               for d in result["source_documents"]}
    return [create_admission(code, units[code["unit_id"]], sources[units[code["unit_id"]]["source_name"]],
                evidence=code["evidence"], contexts=code.get("referent_context", []),
                provenance=dict(native_result_sha256=result["result_sha256"], code_id=code["id"]))
            for code in result["codes"]]


def run_admitted_synthesis(result, client, code_ids, records, **kwargs):
    """Opt-in wrapper. All checks run before the existing client's start method.

    Existing definition, cross-source, explicit review and no-forced-group gates
    still apply. The default application does not yet enable this prototype.
    """
    from .local_codebook import run_synthesis, validate_result_sources
    from .model_pilot import _digest as native_digest
    validate_result_sources(result)
    sources = {d["name"]: dict(source_name=d["name"], source_hash=d["sha256"], text=d["text"])
               for d in result["source_documents"]}
    codes = {c["id"]: c for c in result["codes"]}
    units = {u["id"]: u for u in result["unit_archive"]}
    available = {}
    for record in records:
        cid = record["original"]["candidate"].get("id")
        if cid in available:
            raise ValueError("Duplicate native candidate admission.")
        available[cid] = record
    if (type(code_ids) is not list or len(set(code_ids)) != len(code_ids)
            or any(cid not in codes or cid not in available for cid in code_ids)):
        raise ValueError("All requested native codes require current admission records.")
    for cid in code_ids:
        if available[cid]["version"] != VERSION:
            raise ValueError("New grouping requires current admission records; refresh legacy metadata explicitly.")
        original, code = available[cid]["original"], codes[cid]
        if (original["candidate"] != code or original["target"] != units[code["unit_id"]]
                or original["evidence"] != code["evidence"]
                or original["contexts"] != code.get("referent_context", [])
                or original["provenance"].get("native_result_sha256") != result["result_sha256"]):
            raise ValueError("Admission snapshot differs from the current native draft.")
    selection = grouping_inputs(records, sources, [available[cid]["id"] for cid in code_ids])
    branch = deepcopy(result)
    branch["candidate_admission_gate"] = dict(version=VERSION, records=selection["admission_records"], scope=SCOPE,
        code_ids=list(code_ids), native_base_sha256=result["result_sha256"])
    branch["result_sha256"] = native_digest({k: v for k, v in branch.items() if k != "result_sha256"})
    return run_synthesis(branch, client, code_ids, **kwargs)


def validate_native_admission_gate(result):
    """Reopen/export check for opt-in metadata; legacy drafts remain unchanged."""
    if "candidate_admission_gate" not in result:
        return
    gate = result["candidate_admission_gate"]
    if (type(gate) is not dict or set(gate) != {"version", "records", "scope", "code_ids", "native_base_sha256"}
            or gate["version"] not in SUPPORTED_VERSIONS or gate["scope"] != SCOPE
            or type(gate["records"]) is not list or not 2 <= len(gate["records"]) <= 6
            or type(gate["code_ids"]) is not list or len(gate["code_ids"]) != len(gate["records"])
            or len(set(gate["code_ids"])) != len(gate["code_ids"])):
        raise ValueError("Malformed native admission gate.")
    if "synthesis_gate" in result and result["synthesis_gate"].get("code_ids") != gate["code_ids"]:
        raise ValueError("Grouping selection differs from admitted candidates.")
    sources = {d["name"]: dict(source_name=d["name"], source_hash=d["sha256"], text=d["text"])
               for d in result["source_documents"]}
    codes = {c["id"]: c for c in result["codes"]}
    units = {u["id"]: u for u in result["unit_archive"]}
    for cid, record in zip(gate["code_ids"], gate["records"]):
        if record["version"] != gate["version"]:
            raise ValueError("Native gate and sidecar versions differ.")
        if cid not in codes:
            raise ValueError("Unknown admitted native candidate.")
        original, code = record["original"], codes[cid]
        if (original["candidate"] != code or original["target"] != units[code["unit_id"]]
                or original["evidence"] != code["evidence"]
                or original["contexts"] != code.get("referent_context", [])
                or original["provenance"].get("native_result_sha256") != gate["native_base_sha256"]):
            raise ValueError("Native admission source/candidate snapshot changed.")
    grouping_inputs(gate["records"], sources, [r["id"] for r in gate["records"]])


def render_admission_html(records, sources):
    """Offline review export: all originals and reasons visible; no remote assets."""
    e = lambda value: escape(str(value), quote=True)
    rows = ["<!doctype html><html lang='en'><meta charset='utf-8'><title>Candidate admission review</title>",
            "<h1>Experimental candidate admission</h1><p>Pass means narrow provenance consistency only. "
            "Review-needed and abstain preserve uncertain candidates; they are not invalid-code verdicts. "
            "Researcher overrides are declared, reversible decisions, not authenticated human truth. "
            "No candidate has been deleted, repaired or established as a theme.</p>"]
    for record in records:
        source = sources[record["original"]["target"]["source_name"]]
        validate_admission(record, source)
        effective = effective_admission(record)
        original = record["original"]
        display = original["candidate"].get("label") or "Saved definition-stage candidate"
        rows += ["<article><h2>" + e(display) + "</h2>",
                 "<p>" + e(record["id"]) + " | " + e(effective["state"]) +
                 " | Eligible through this gate: " + e(effective["eligible"]) + "</p>"]
        for title, value in (("Original candidate", original["candidate"]),
                             ("Automatic check, not semantic approval", record["assessment"]),
                             ("Exact target", original["target"]), ("Original support", original["evidence"]),
                             ("Context, not additional support", original["contexts"]),
                             ("Saved-output provenance", original["provenance"]),
                             ("Versioned metadata reassessment, not meaning repair", record.get("metadata_refresh")),
                             ("Append-only review history", record["history"])):
            rows.append("<h3>" + e(title) + "</h3><pre>" + e(json.dumps(value, ensure_ascii=False, indent=2)) + "</pre>")
        rows.append("</article>")
    return "\n".join(rows + ["</html>"])

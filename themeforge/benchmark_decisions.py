"""Apply explicit local reviewer decisions to an immutable pilot draft."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
from typing import Callable

from .benchmark import grouping_digest, validate_manifest


REVIEW_FORMAT = 1
REVIEWER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
KINDS = {"pilot_design", "unitization", "study_permission", "document_group", "code_definition", "annotation"}


def content_digest(value: object) -> str:
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def finalize_packet(packet: dict, manifest: dict) -> dict:
    result = deepcopy(packet)
    result.pop("packet_id", None)
    result["format_version"] = REVIEW_FORMAT
    result["manifest_sha256"] = content_digest(manifest)
    result["packet_id"] = "pilot-" + content_digest(result)
    return result


def verify_packet(packet: dict, manifest: dict) -> None:
    if not isinstance(packet, dict) or packet.get("format_version") != REVIEW_FORMAT:
        raise ValueError("Unsupported review packet format")
    expected = finalize_packet(packet, manifest)
    if packet.get("packet_id") != expected["packet_id"] or packet.get("manifest_sha256") != content_digest(manifest):
        raise ValueError("Packet or draft changed; rebuild a separate review packet instead of reusing decisions")


def _index(rows: list[dict], key: str = "id") -> dict[str, dict]:
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError("Expected a record list")
    result = {}
    for row in rows:
        value = row.get(key)
        if not isinstance(value, str) or not value or value in result:
            raise ValueError(f"Missing or duplicate {key}")
        result[value] = row
    return result


def _review_time(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("Each decision requires reviewed_at")
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Invalid review timestamp") from exc
    if timestamp.tzinfo is None:
        raise ValueError("Review timestamp must include a timezone")
    return timestamp


def _checked_values(item: dict, decision: dict) -> dict:
    fields = _index(item.get("fields", []), "key")
    values = decision.get("values")
    if not isinstance(values, dict) or set(values) != set(fields):
        raise ValueError(f"{item['id']}: decision must include exactly the displayed fields")
    for key, field in fields.items():
        value = values[key]
        kind = field.get("type")
        if kind == "checkbox":
            valid = type(value) is bool
        elif kind in {"text", "textarea"}:
            valid = isinstance(value, str)
        elif kind == "select":
            valid = isinstance(value, str) and value in {option["value"] for option in field.get("options", [])}
        else:
            raise ValueError(f"{item['id']}: unsupported field type")
        if not valid:
            raise ValueError(f"{item['id']}: invalid {key}")
        if field.get("required") and (value is False or isinstance(value, str) and not value.strip()):
            raise ValueError(f"{item['id']}: {key} requires an explicit value")
    return values


def validate_decisions(packet: dict, decisions: dict) -> list[tuple[dict, dict]]:
    if not isinstance(decisions, dict) or decisions.get("format_version") != REVIEW_FORMAT:
        raise ValueError("Unsupported decisions format")
    if decisions.get("packet_id") != packet.get("packet_id"):
        raise ValueError("Decisions belong to another packet")
    items = _index(packet.get("items", []))
    rows = _index(decisions.get("decisions", []), "item_id")
    output = []
    for item_id, row in rows.items():
        if item_id not in items:
            raise ValueError("Decision references an unknown review item")
        if row.get("action") not in {"accept", "defer"}:
            raise ValueError(f"{item_id}: unsupported decision action")
        if not isinstance(row.get("reviewer_id"), str) or not REVIEWER_RE.fullmatch(row["reviewer_id"]):
            raise ValueError("Use a pseudonymous reviewer ID with 1-64 letters, digits, dots, underscores or hyphens")
        if not isinstance(row.get("note", ""), str):
            raise ValueError(f"{item_id}: review note must be text")
        _review_time(row.get("reviewed_at"))
        item = items[item_id]
        binding = item.get("binding", {})
        if not isinstance(binding, dict) or binding.get("kind") not in KINDS:
            raise ValueError(f"{item_id}: unsupported item binding")
        if row["action"] == "accept":
            _checked_values(item, row)
        output.append((item, row))
    return output


def apply_review(manifest: dict, packet: dict, decisions: dict, root: Path,
                 text_loader: Callable[[Path], str]) -> dict:
    """Return a new manifest/report; never write sources or approve pending work.

    Unknown labels stay uncertain. Imported source-coder labels are evidence only.
    Reapply cumulative browser exports to the original draft, not a prior output.
    """
    verify_packet(packet, manifest)
    source_check = validate_manifest(manifest, root, text_loader)
    if source_check.errors:
        raise ValueError("Draft/source integrity failed: " + "; ".join(source_check.errors[:3]))
    pairs = validate_decisions(packet, decisions)
    result = deepcopy(manifest)
    studies = _index(result["studies"])
    documents = _index(result["documents"])
    books = _index(result["codebooks"])
    units = _index(result["units"])
    annotators = _index(result["annotators"])
    accepted = [(item, row) for item, row in pairs if row["action"] == "accept"]
    code_reviews: dict[str, tuple[dict, dict]] = {}
    annotation_reviews: dict[str, dict] = {}
    stale = []

    def require(mapping: dict, key: object, description: str) -> dict:
        if not isinstance(key, str) or key not in mapping:
            raise ValueError(f"Review binding references unknown {description}")
        return mapping[key]

    for item, row in accepted:
        reviewer = row["reviewer_id"]
        if reviewer not in annotators:
            new_reviewer = {"id": reviewer, "role": "reviewer"}
            annotators[reviewer] = new_reviewer
            result["annotators"].append(new_reviewer)
        elif annotators[reviewer].get("role") == "unknown":
            annotators[reviewer]["role"] = "reviewer"
        binding, values = item["binding"], row["values"]
        kind = binding["kind"]
        if kind == "pilot_design":
            if values.get("approve") is not True:
                raise ValueError("Pilot design approval must be explicit")
            result.setdefault("pilot_design", {})["approved_by"] = reviewer
        elif kind == "unitization":
            if values.get("approve") is not True:
                raise ValueError("Unitization approval must be explicit")
            result["unitization"]["reviewed_by"] = reviewer
        elif kind == "study_permission":
            study = require(studies, binding.get("study_id"), "study")
            for field in ("evaluation_permission", "training_permission"):
                if values.get(field) not in {"unknown", "allowed", "denied"}:
                    raise ValueError("Invalid permission state")
                study[field] = values[field]
            study["permission_reviewed_by"] = reviewer
            study["deidentification_reviewed_by"] = reviewer if values.get("deidentified") is True else ""
        elif kind == "document_group":
            document = require(documents, binding.get("document_id"), "document")
            if values.get("confirmed") is not True or values.get("split") not in {"development", "calibration", "test"}:
                raise ValueError("Grouping requires explicit confirmation and a valid split")
            participants = sorted({p.strip() for p in re.split(r"[,;\n]+", values.get("participant_ids", "")) if p.strip()})
            if not values.get("session_id", "").strip() or not participants:
                raise ValueError("Grouping requires a session and every participant ID")
            document.update(session_id=values["session_id"].strip(), participant_ids=participants,
                            split=values["split"], group_reviewed_by=reviewer)
            document["grouping_review_sha256"] = grouping_digest(document)
        elif kind == "code_definition":
            book = require(books, binding.get("codebook_id"), "codebook")
            code = require(_index(book["codes"]), binding.get("code_id"), "code")
            alias = require(_index(book["label_aliases"], "label_id"), binding.get("label_id"), "literal label")
            if values.get("confirmed") is not True or not values.get("name", "").strip() or not values.get("definition", "").strip():
                raise ValueError("Code definition requires a name, definition and confirmation")
            for field in ("name", "definition", "inclusion_criteria", "exclusion_criteria"):
                code[field] = values.get(field, "").strip()
            code["reviewed_by"] = reviewer
            alias.update(decision="keep", target_code_id=code["id"], reviewed_by=reviewer,
                         reason="Retained separately in the explicitly reviewed pilot scope")
            code_reviews[code["id"]] = (item, row)
        elif kind == "annotation":
            unit = require(units, binding.get("unit_id"), "unit")
            if unit["document_id"] != binding.get("document_id") or values.get("span_confirmed") is not True:
                raise ValueError("Annotation requires confirmation of its exact bound source span")
            document = documents[unit["document_id"]]
            codes = books[document["codebook_id"]]["codes"]
            labels = []
            for code in codes:
                decision = values.get("code_" + code["id"])
                if decision not in {"present", "absent", "uncertain"}:
                    raise ValueError("Every pilot code requires an explicit decision; missing is not absent")
                labels.append({"code_id": code["id"], "decision": decision, "reviewed_by": reviewer})
            unit.update(labels=labels, span_reviewed_by=reviewer, review_note=row.get("note", ""))
            annotation_reviews[unit["id"]] = row

    # A changed code definition saved after a unit decision requires re-review.
    # This prevents cumulative exports from silently reinterpreting old labels.
    for unit in units.values():
        annotation = annotation_reviews.get(unit["id"])
        if not annotation:
            continue
        for label in unit["labels"]:
            reviewed_code = code_reviews.get(label["code_id"])
            if not reviewed_code:
                continue
            item, decision = reviewed_code
            defaults = {field["key"]: field.get("default", "") for field in item["fields"]}
            changed = any(str(decision["values"].get(key, "")).strip() != str(defaults.get(key, "")).strip()
                          for key in ("name", "definition", "inclusion_criteria", "exclusion_criteria"))
            if changed and _review_time(decision["reviewed_at"]) > _review_time(annotation["reviewed_at"]):
                label["reviewed_by"] = ""
                stale.append(f"{unit['id']}: review {label['code_id']} again after the code-definition change")

    for book in books.values():
        if book["codes"] and all(code.get("reviewed_by") for code in book["codes"]):
            book["version"] = "pilot-v1"
            book["reviewed_by"] = book["codes"][-1]["reviewed_by"]
            book["definition_reviewers"] = sorted({code["reviewed_by"] for code in book["codes"]})
            for document in documents.values():
                if document["codebook_id"] == book["id"]:
                    document["codebook_version"] = book["version"]
    for document in documents.values():
        owned = [unit for unit in units.values() if unit["document_id"] == document["id"]]
        complete = bool(owned) and all(unit.get("span_reviewed_by") and unit["labels"] and
                                      all(label["decision"] in {"present", "absent"} and label.get("reviewed_by")
                                          for label in unit["labels"]) for unit in owned)
        document["annotation_status"] = "complete" if complete else "partial"
        document["annotation_reviewed_by"] = owned[-1]["span_reviewed_by"] if complete else ""
        document["expected_unit_count"] = len(owned)

    result["review_history"] = [{"packet_id": packet["packet_id"], "decision_export_sha256": content_digest(decisions),
                                 "decisions": deepcopy(decisions["decisions"])}]
    report = validate_manifest(result, root, text_loader).to_dict()
    # Keep design approval explicit even for drafts produced by older validators.
    if not result.get("pilot_design", {}).get("approved_by"):
        report["review_required"].append("pilot_design: explicit scope and sampling approval required")
    report["review_required"] = list(dict.fromkeys([*report["review_required"], *stale]))
    report["evaluation_ready"] = not report["errors"] and not report["review_required"]
    report["training_ready"] = report["evaluation_ready"] and not report["training_blockers"]
    report["status"] = "invalid" if report["errors"] else "ready" if report["evaluation_ready"] else "review_required"
    return {"manifest": result, "report": report, "summary": {
        "accepted_items": len(accepted), "deferred_items": sum(row["action"] == "defer" for _, row in pairs),
        "pending_items": len(packet["items"]) - len(pairs), "stale_annotations": len(stale),
        "integrity_errors": len(report["errors"]), "review_requirements": len(report["review_required"]),
        "evaluation_ready": report["evaluation_ready"], "training_ready": report["training_ready"],
    }}

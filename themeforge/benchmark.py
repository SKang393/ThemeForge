"""Local metadata and validation for a reviewed, fixed-codebook benchmark.

This module deliberately does not infer permissions, participant identity,
annotation completeness, or a gold codebook from filenames and labels.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Callable, Iterable


FORMAT_VERSION = 1
SPLITS = {"development", "calibration", "test"}
PERMISSIONS = {"unknown", "allowed", "denied"}
TextLoader = Callable[[Path], str]


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def stable_id(prefix: str, value: str) -> str:
    return f"{prefix}-{sha256_text(value)[:24]}"


def source_id(path: Path, root: Path) -> str:
    relative = path.resolve().relative_to(root.resolve())
    return stable_id("source", relative.as_posix())


def source_index(root: Path) -> dict[str, Path]:
    """Resolve opaque source IDs locally, without storing identifying paths."""
    return {source_id(path, root): path for path in root.rglob("*") if path.is_file()}


def grouping_digest(document: dict) -> str:
    """Bind review to the current source, group membership, and split.

    This is a change detector, not a signature or evidence that a human reviewed
    the data. Participant order is irrelevant; participant membership is not.
    """
    participants = document.get("participant_ids", [])
    if isinstance(participants, list) and all(isinstance(item, str) for item in participants):
        participants = sorted(set(participants))
    fields = {key: document.get(key) for key in (
        "id", "study_id", "source_id", "text_sha256", "session_id", "split",
    )}
    fields["participant_ids"] = participants
    return sha256_text(json.dumps(fields, sort_keys=True, ensure_ascii=False, separators=(",", ":")))


@dataclass(frozen=True)
class StudyInput:
    key: str
    transcripts: tuple[Path, ...]
    annotation_sources: tuple[Path, ...] = ()
    labels: tuple[str, ...] = ()
    annotators: tuple[str, ...] = ()


@dataclass(frozen=True)
class BenchmarkValidation:
    errors: tuple[str, ...]
    review_required: tuple[str, ...]
    training_blockers: tuple[str, ...]

    @property
    def evaluation_ready(self) -> bool:
        return not self.errors and not self.review_required

    @property
    def training_ready(self) -> bool:
        return self.evaluation_ready and not self.training_blockers

    def to_dict(self) -> dict:
        return {
            "status": "invalid" if self.errors else "ready" if self.evaluation_ready else "review_required",
            "evaluation_ready": self.evaluation_ready,
            "training_ready": self.training_ready,
            "errors": list(self.errors),
            "review_required": list(self.review_required),
            "training_blockers": list(self.training_blockers),
        }


def _similarity_key(label: str) -> str:
    # A review aid only: the original label fingerprint and separate ID remain.
    return re.sub(r"\W+", " ", label.partition(":")[2] or label).casefold().strip()


def label_inventory(study_id: str, labels: Iterable[str]) -> tuple[list[dict], list[dict]]:
    counts = Counter(label for label in labels if label.strip())
    related: dict[str, list[str]] = defaultdict(list)
    for label in counts:
        related[_similarity_key(label)].append(stable_id("label", study_id + "\0" + label))
    codes, aliases = [], []
    for label, count in sorted(counts.items()):
        label_id = stable_id("label", study_id + "\0" + label)
        code_id = stable_id("code", study_id + "\0" + label)
        codes.append({"id": code_id, "name": "", "definition": "", "reviewed_by": ""})
        aliases.append({
            "label_id": label_id,
            "label_sha256": sha256_text(label),
            "occurrences": count,
            "target_code_id": code_id,
            "decision": "pending",
            "reviewed_by": "",
            "reason": "",
            "possible_prefix": ":" in label,
            "candidate_related_label_ids": [item for item in related[_similarity_key(label)] if item != label_id],
        })
    return codes, aliases


def prepare_manifest(root: Path, inputs: Iterable[StudyInput], text_loader: TextLoader) -> dict:
    sources: dict[str, dict] = {}
    studies, documents, codebooks = [], [], []
    annotators: dict[str, dict] = {}
    for item in inputs:
        study_id = stable_id("study", item.key)
        codebook_id = stable_id("codebook", item.key)
        studies.append({
            "id": study_id,
            "evaluation_permission": "unknown",
            "training_permission": "unknown",
            "permission_reviewed_by": "",
            "deidentification_reviewed_by": "",
        })
        for path in (*item.transcripts, *item.annotation_sources):
            sid = source_id(path, root)
            sources[sid] = {"id": sid, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        for path in item.transcripts:
            sid = source_id(path, root)
            documents.append({
                "id": stable_id("document", sid),
                "study_id": study_id,
                "source_id": sid,
                "text_sha256": sha256_text(text_loader(path)),
                "session_id": "",
                "participant_ids": [],
                "group_reviewed_by": "",
                "grouping_review_sha256": "",
                "split": "unassigned",
                "codebook_id": codebook_id,
                "codebook_version": "unreviewed",
                "annotation_status": "unknown",
                "annotation_reviewed_by": "",
                "expected_unit_count": 0,
            })
        codes, aliases = label_inventory(study_id, item.labels)
        codebooks.append({
            "id": codebook_id,
            "study_id": study_id,
            "version": "unreviewed",
            "reviewed_by": "",
            "source_ids": sorted({source_id(path, root) for path in item.annotation_sources}),
            "codes": codes,
            "label_aliases": aliases,
        })
        for name in item.annotators:
            aid = stable_id("annotator", study_id + "\0" + name)
            annotators[aid] = {"id": aid, "role": "unknown"}
    return {
        "format_version": FORMAT_VERSION,
        "purpose": "fixed_codebook_benchmark",
        "split_policy": {"group_by": "session", "held_out_study_required": True},
        "unitization": {"name": "", "version": "", "reviewed_by": ""},
        "studies": studies,
        "sources": list(sources.values()),
        "documents": documents,
        "annotators": list(annotators.values()),
        "codebooks": codebooks,
        "units": [],
    }


def validate_manifest(manifest: dict, root: Path, text_loader: TextLoader) -> BenchmarkValidation:
    """Validate integrity and explicit review declarations, without scoring a model.

    Readiness means the declared benchmark contract is complete and consistent.
    It cannot verify the truth of consent or the quality of human interpretation.
    """
    errors, review, training = [], [], []

    def records(owner: dict, key: str, prefix: str = "manifest") -> list[dict]:
        value = owner.get(key)
        if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
            errors.append(f"{prefix}: {key} must be a list of records")
            return []
        return value

    def index(rows: list[dict], kind: str, field: str = "id") -> dict[str, dict]:
        output = {}
        for row in rows:
            rid = row.get(field)
            if not isinstance(rid, str) or not rid:
                errors.append(f"{kind}: missing {field}")
            elif rid in output:
                errors.append(f"{kind}: duplicate {rid}")
            else:
                output[rid] = row
        return output

    def reviewer(owner: dict, field: str, context: str) -> None:
        aid = owner.get(field)
        if not aid:
            review.append(f"{context}: {field} required")
        elif aid not in annotators:
            errors.append(f"{context}: {field} references unknown annotator")
        elif annotators[aid].get("role") == "unknown":
            review.append(f"{context}: reviewer role is unknown")

    if not isinstance(manifest, dict):
        return BenchmarkValidation(("manifest must be a JSON object",), (), ())
    if manifest.get("format_version") != FORMAT_VERSION:
        errors.append("unsupported benchmark format_version")
    if manifest.get("purpose") != "fixed_codebook_benchmark":
        errors.append("unsupported benchmark purpose")
    studies = index(records(manifest, "studies"), "study")
    sources = index(records(manifest, "sources"), "source")
    documents = index(records(manifest, "documents"), "document")
    codebooks = index(records(manifest, "codebooks"), "codebook")
    annotators = index(records(manifest, "annotators"), "annotator")
    units = index(records(manifest, "units"), "unit")
    if not studies or not documents:
        errors.append("benchmark requires studies and documents")
    for aid, row in annotators.items():
        if row.get("role") not in {"unknown", "coder", "adjudicator", "reviewer"}:
            errors.append(f"{aid}: invalid annotator role")
    if "pilot_design" in manifest:
        pilot_design = manifest["pilot_design"]
        if not isinstance(pilot_design, dict):
            errors.append("pilot_design must be an object")
        else:
            reviewer(pilot_design, "approved_by", "pilot_design")
    unitization = manifest.get("unitization")
    if not isinstance(unitization, dict):
        errors.append("unitization must be an object")
    else:
        if not unitization.get("name") or not unitization.get("version"):
            review.append("unitization name and version required")
        reviewer(unitization, "reviewed_by", "unitization")
    policy = manifest.get("split_policy", {})
    if not isinstance(policy, dict):
        errors.append("split_policy must be an object")
        policy = {}
    if policy.get("group_by") not in {"session", "study"}:
        errors.append("split_policy.group_by must be session or study")
    if not isinstance(policy.get("held_out_study_required"), bool):
        errors.append("split_policy.held_out_study_required must be boolean")
    for sid, study in studies.items():
        for field in ("evaluation_permission", "training_permission"):
            if study.get(field) not in PERMISSIONS:
                errors.append(f"{sid}: invalid {field}")
        if study.get("evaluation_permission") != "allowed":
            review.append(f"{sid}: evaluation permission not established")
        reviewer(study, "permission_reviewed_by", sid)
        reviewer(study, "deidentification_reviewed_by", sid)

    paths = source_index(root)
    for sid, source in sources.items():
        if sid not in paths:
            errors.append(f"{sid}: source file missing")
        elif hashlib.sha256(paths[sid].read_bytes()).hexdigest() != source.get("sha256"):
            errors.append(f"{sid}: source SHA256 mismatch")

    active_codes: dict[str, set[str]] = {}
    for cid, codebook in codebooks.items():
        if codebook.get("study_id") not in studies:
            errors.append(f"{cid}: unknown study")
        if codebook.get("version") in {None, "", "unreviewed"}:
            review.append(f"{cid}: reviewed codebook version required")
        reviewer(codebook, "reviewed_by", cid)
        source_ids = codebook.get("source_ids", [])
        if not isinstance(source_ids, list) or any(sid not in sources for sid in source_ids):
            errors.append(f"{cid}: invalid codebook source references")
        codes = index(records(codebook, "codes", cid), cid + " code")
        aliases = index(records(codebook, "label_aliases", cid), cid + " alias", "label_id")
        active_codes[cid] = set(codes)
        if not codes:
            review.append(f"{cid}: reviewed code definitions required")
        for code_id, code in codes.items():
            if not code.get("name") or not code.get("definition"):
                review.append(f"{code_id}: code name and definition required")
            reviewer(code, "reviewed_by", code_id)
        for lid, alias in aliases.items():
            decision = alias.get("decision")
            if decision not in {"pending", "keep", "merge", "exclude"}:
                errors.append(f"{lid}: invalid alias decision")
            if decision == "pending":
                review.append(f"{lid}: alias decision pending")
            elif decision == "exclude":
                if alias.get("target_code_id"):
                    errors.append(f"{lid}: excluded label must not target a code")
            elif alias.get("target_code_id") not in codes:
                errors.append(f"{lid}: unknown target code")
            reviewer(alias, "reviewed_by", lid)
            if decision in {"merge", "exclude"} and not alias.get("reason"):
                review.append(f"{lid}: merge/exclusion reason required")
            related = alias.get("candidate_related_label_ids", [])
            if not isinstance(related, list) or any(item not in aliases for item in related):
                errors.append(f"{lid}: unknown related label")

    group_splits: dict[tuple[str, str], set[str]] = defaultdict(set)
    study_splits: dict[str, set[str]] = defaultdict(set)
    texts: dict[str, str] = {}
    for did, document in documents.items():
        sid, cid = document.get("study_id"), document.get("codebook_id")
        if sid not in studies:
            errors.append(f"{did}: unknown study")
        if cid not in codebooks:
            errors.append(f"{did}: unknown codebook")
        elif codebooks[cid].get("study_id") != sid or document.get("codebook_version") != codebooks[cid].get("version"):
            errors.append(f"{did}: codebook study/version mismatch")
        source = document.get("source_id")
        if source not in sources:
            errors.append(f"{did}: unknown source")
        elif source in paths:
            try:
                texts[did] = text_loader(paths[source])
            except (OSError, ValueError, UnicodeError):
                errors.append(f"{did}: source text could not be loaded")
            else:
                if sha256_text(texts[did]) != document.get("text_sha256"):
                    errors.append(f"{did}: extracted text SHA256 mismatch")
        split = document.get("split")
        if split not in SPLITS:
            if split == "unassigned":
                review.append(f"{did}: split unassigned")
            else:
                errors.append(f"{did}: invalid split")
        if not document.get("session_id"):
            review.append(f"{did}: session ID required")
        participants = document.get("participant_ids")
        if not isinstance(participants, list) or not participants or any(not isinstance(p, str) or not p for p in participants):
            review.append(f"{did}: participant IDs required")
            participants = []
        reviewer(document, "group_reviewed_by", did)
        if not document.get("grouping_review_sha256"):
            review.append(f"{did}: grouping review fingerprint required")
        elif document["grouping_review_sha256"] != grouping_digest(document):
            review.append(f"{did}: grouping changed since review; review the current grouping and split")
        if split in SPLITS:
            study_splits[sid].add(split)
            if document.get("session_id"):
                group_splits[("session", f"{sid}/{document['session_id']}")].add(split)
            for participant in participants:
                group_splits[("participant", participant)].add(split)
            if source in sources:
                group_splits[("source_hash", sources[source].get("sha256", ""))].add(split)
            if document.get("text_sha256"):
                group_splits[("text_hash", document["text_sha256"])].add(split)
            if split == "development" and studies.get(sid, {}).get("training_permission") != "allowed":
                training.append(f"{did}: development-source training permission not established")
        if document.get("annotation_status") != "complete":
            review.append(f"{did}: complete explicit annotations required")
        reviewer(document, "annotation_reviewed_by", did)

    for (kind, group), splits in group_splits.items():
        if len(splits) > 1:
            errors.append(f"{kind} {group}: leakage across splits")
    if policy.get("group_by") == "study":
        for sid, splits in study_splits.items():
            if len(splits) > 1:
                errors.append(f"{sid}: study leakage across splits")
    if set().union(*study_splits.values()) != SPLITS:
        review.append("development, calibration, and test splits are all required")
    if policy.get("held_out_study_required") and not any(splits == {"test"} for splits in study_splits.values()):
        review.append("an entire held-out test study is required")

    unit_counts: Counter[str] = Counter()
    seen_spans: set[tuple] = set()
    for uid, unit in units.items():
        did = unit.get("document_id")
        if did not in documents:
            errors.append(f"{uid}: unknown document")
            continue
        unit_counts[did] += 1
        start, end = unit.get("source_start"), unit.get("source_end")
        if type(start) is not int or type(end) is not int or start < 0 or end <= start:
            errors.append(f"{uid}: invalid source span")
        elif did in texts:
            if end > len(texts[did]) or sha256_text(texts[did][start:end]) != unit.get("span_sha256"):
                errors.append(f"{uid}: exact source span SHA256 mismatch")
            span_key = (did, start, end)
            if span_key in seen_spans:
                errors.append(f"{uid}: duplicate evidence span")
            seen_spans.add(span_key)
        labels = records(unit, "labels", uid)
        code_ids = active_codes.get(documents[did].get("codebook_id"), set())
        seen_codes: set[str] = set()
        for label in labels:
            code_id = label.get("code_id")
            if code_id not in code_ids:
                errors.append(f"{uid}: label references unknown code")
            if code_id in seen_codes:
                errors.append(f"{uid}: duplicate code decision")
            seen_codes.add(code_id)
            if label.get("decision") not in {"present", "absent", "uncertain"}:
                errors.append(f"{uid}: invalid label decision")
            elif label.get("decision") == "uncertain":
                review.append(f"{uid}: unresolved code decision")
            reviewer(label, "reviewed_by", uid)
        if code_ids - seen_codes:
            review.append(f"{uid}: missing explicit code decisions; missing is not negative")
    for did, document in documents.items():
        count = document.get("expected_unit_count")
        if type(count) is not int or count <= 0:
            review.append(f"{did}: reviewed positive expected_unit_count required")
        elif unit_counts[did] != count:
            errors.append(f"{did}: unit count does not match expected_unit_count")
    return BenchmarkValidation(tuple(dict.fromkeys(errors)), tuple(dict.fromkeys(review)), tuple(dict.fromkeys(training)))

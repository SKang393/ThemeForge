"""Prepare a local, enriched within-study pilot requiring explicit human review."""
from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
import hashlib
from pathlib import Path
import re

from .benchmark import StudyInput, label_inventory, sha256_text, source_id, stable_id, validate_manifest
from .benchmark_sources import QuoteAligner, read_annotation_records
from .quote_units import extract_evidence_units
from .transcript_parser import parse_transcript


def _field(key, label, kind="text", default="", options=(), required=True):
    return dict(key=key, label=label, type=kind, default=default, required=required,
                options=[dict(value=value, label=value) for value in options])


def _normalized(value):
    return " ".join(value.split()).casefold()


def _admin(label):
    # Deliberately narrow. Prefixes/qualifiers and possible substantive codes stay.
    return bool(re.fullmatch(r"(?:please (?:check|review|delete)(?: this)?|delete this|ignore this|test comment|no code|uncoded)",
                             " ".join(label.split()), re.IGNORECASE))


def prepare_pilot(manifest: dict, root: Path, study_inputs: tuple[StudyInput, ...], text_loader, *,
                  study_key="Familiarity and perceptions of preference assessment", max_codes=6,
                  units_per_document=6, definition_sources: tuple[Path, ...] = ()) -> dict:
    """Return draft, packet, alignments, summary and plan without writing files.

    Literal labels are reconstructed from caller-supplied StudyInput objects and
    bound to the original inventory by IDs, occurrence counts and fingerprints.
    All approvals and gold decisions remain blank/uncertain.
    """
    if not isinstance(max_codes, int) or max_codes < 1 or not isinstance(units_per_document, int) or units_per_document < 1:
        raise ValueError("max_codes and units_per_document must be positive integers")
    validation = validate_manifest(manifest, root, text_loader)
    if validation.errors:
        raise ValueError("Original benchmark integrity validation failed: " + "; ".join(validation.errors))
    inputs = {item.key: item for item in study_inputs}
    if len(inputs) != len(study_inputs) or study_key not in inputs:
        raise ValueError("Study inputs must have unique keys and include the pilot study")
    books = {row["study_id"]: row for row in manifest["codebooks"]}
    catalog = []
    literal_by_id = {}
    for item in study_inputs:
        sid = stable_id("study", item.key)
        if sid not in books:
            raise ValueError("Study input is absent from original manifest")
        _, reconstructed = label_inventory(sid, item.labels)
        original = {row["label_id"]: row for row in books[sid]["label_aliases"]}
        if set(original) != {row["label_id"] for row in reconstructed}:
            raise ValueError("Literal label inventory does not match original manifest")
        study_literals = {stable_id("label", sid + "\0" + label): label for label in item.labels if label.strip()}
        for label in sorted(set(item.labels)):
            if not label.strip():
                continue
            lid = stable_id("label", sid + "\0" + label)
            alias = original[lid]
            if alias["label_sha256"] != sha256_text(label) or alias["occurrences"] != item.labels.count(label):
                raise ValueError("Literal label fingerprint or occurrence mismatch")
            literal_by_id[lid] = label
            catalog.append(dict(study_name=item.key, label_id=lid, label=label,
                                occurrences=alias["occurrences"],
                                related_labels=[study_literals[lid] for lid in alias.get("candidate_related_label_ids", [])],
                                note="Literal source label; separate identity retained; not approved gold."))
    if {stable_id("study", item.key) for item in study_inputs} != {row["id"] for row in manifest["studies"]}:
        raise ValueError("Study inputs do not reconstruct the complete original catalog")
    selected = inputs[study_key]
    paths = sorted(selected.transcripts, key=lambda path: path.resolve().relative_to(root.resolve()).as_posix())
    if len(paths) != 5:
        raise ValueError("Pilot requires exactly five transcripts for a 3/1/1 within-study split")
    sid = stable_id("study", study_key)
    original_book = books[sid]
    by_source = {row["source_id"]: row for row in manifest["documents"] if row["study_id"] == sid}
    documents = []
    texts = {}
    filenames = {}
    for index, path in enumerate(paths):
        source = source_id(path, root)
        if source not in by_source:
            raise ValueError("Selected transcript missing from original study")
        doc = deepcopy(by_source[source])
        doc.update(session_id=stable_id("session", sid + "\0" + source),
                   participant_ids=[stable_id("participant", sid + "\0" + source)],
                   split="development" if index < 3 else "calibration" if index == 3 else "test",
                   group_reviewed_by="", grouping_review_sha256="", annotation_status="unknown",
                   annotation_reviewed_by="", expected_unit_count=units_per_document,
                   codebook_version="pilot-v1-draft")
        texts[doc["id"]] = text_loader(path)
        if sha256_text(texts[doc["id"]]) != doc["text_sha256"]:
            raise ValueError("Transcript text changed during pilot preparation")
        filenames[doc["id"]] = path.resolve().relative_to(root.resolve()).as_posix()
        documents.append(doc)
    dev_ids = {doc["id"] for doc in documents if doc["split"] == "development"}
    document_paths = {doc["id"]: path for doc, path in zip(documents, paths)}
    aligners = {}
    source_associations = {}
    original_sources = {row["id"]: row for row in manifest["sources"]}
    alignments = []
    for path in sorted(set(selected.annotation_sources), key=str):
        if path.suffix.lower() != ".docx":
            continue
        source = source_id(path, root)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if source not in original_sources or original_sources[source]["sha256"] != digest:
            raise ValueError("Annotation source changed or is missing from original manifest")
        associated_ids = tuple(sorted(did for did, transcript in document_paths.items()
                                      if transcript.parent.resolve() == path.parent.resolve()))
        association = dict(state="proposed_unique" if len(associated_ids) == 1 else "multiple" if associated_ids else "unmatched",
                           document_ids=list(associated_ids), reviewed_by="", rule="same_parent_folder",
                           annotation_source_path=path.resolve().relative_to(root.resolve()).as_posix(),
                           transcript_paths=[filenames[did] for did in associated_ids])
        source_associations[source] = association
        if associated_ids not in aligners:
            aligners[associated_ids] = QuoteAligner({did: texts[did] for did in associated_ids})
        for record in read_annotation_records(path):
            lid = stable_id("label", sid + "\0" + record["label"])
            if record["label"].strip() and lid not in literal_by_id:
                raise ValueError("Annotation label is absent from verified inventory")
            result = aligners[associated_ids].align(record["quote"])
            alignments.append(dict(annotation_source_id=source, annotation_source_sha256=digest,
                                   annotation_source_path=path.resolve().relative_to(root.resolve()).as_posix(),
                                   record_key=record["record_key"], kind=record["kind"], label=record["label"],
                                   label_id=lid, quote=record["quote"],
                                   author_id=stable_id("annotator", sid + "\0" + record["author"]),
                                   source_association=association,
                                   development_eligible=(len(associated_ids) == 1 and associated_ids[0] in dev_ids), **result))
    counts = Counter()
    coverage = defaultdict(set)
    excluded = Counter()
    for row in alignments:
        if _admin(row["label"]):
            excluded[row["label_id"]] += 1
            continue
        if row["development_eligible"] and row["status"] in {"unique_exact", "unique_whitespace"}:
            did = row["candidates"][0]["document_id"]
            if did in dev_ids and row["label"].strip():
                counts[row["label_id"]] += 1
                coverage[row["label_id"]].add(did)
    definition_rows = []
    added_sources = []
    if definition_sources:
        from .benchmark_definitions import read_workbook_definitions
        for path in definition_sources:
            source = source_id(path, root)
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if source in original_sources and original_sources[source]["sha256"] != digest:
                raise ValueError("Workbook source SHA256 mismatch")
            added_sources.append(dict(id=source, sha256=digest))
            for row in read_workbook_definitions(path):
                definition_rows.append(dict(row, source_id=source, source_sha256=digest,
                                            source_path=path.resolve().relative_to(root.resolve()).as_posix()))
    definition_matches = {}
    definition_values = {}
    for lid, label in literal_by_id.items():
        matches = [row for row in definition_rows if row["label"] == label]
        rule = "literal"
        if not matches:
            matches = [row for row in definition_rows if _normalized(row["label"]) == _normalized(label)]
            rule = "case_whitespace"
        values = {row["definition"] for row in matches if row["definition"].strip()}
        definition_matches[lid] = dict(rule=rule if len(values) == 1 else "unmatched_or_conflicting", sources=matches)
        definition_values[lid] = next(iter(values)) if len(values) == 1 else ""
    selected_lids = sorted(counts, key=lambda lid: (-(bool(definition_values[lid]) if definition_sources else 0),
                                                   -len(coverage[lid]), -counts[lid], literal_by_id[lid]))[:max_codes]
    if len(selected_lids) != max_codes:
        raise ValueError(f"Only {len(selected_lids)} development-aligned literal labels available; {max_codes} requested")
    for row in catalog:
        if row["study_name"] == study_key and row["label_id"] not in selected_lids and not definition_values[row["label_id"]]:
            row["note"] += " Not selected for bounded pilot; definition review needed. No exclusion or merge approved."
    aliases = []
    for alias in original_book["label_aliases"]:
        if alias["label_id"] in selected_lids:
            copy = deepcopy(alias)
            copy.update(decision="pending", reviewed_by="", reason="",
                        candidate_related_label_ids=[lid for lid in copy.get("candidate_related_label_ids", []) if lid in selected_lids])
            aliases.append(copy)
    code_by_label = {alias["label_id"]: alias["target_code_id"] for alias in aliases}
    codes = [deepcopy(code) for code in original_book["codes"] if code["id"] in code_by_label.values()]
    for lid in selected_lids:
        label = literal_by_id[lid]
        code = next(code for code in codes if code["id"] == code_by_label[lid])
        code.update(name=label, definition=definition_values[lid],
                    inclusion_criteria="", exclusion_criteria="", reviewed_by="")
    book = deepcopy(original_book)
    book.update(version="pilot-v1-draft", reviewed_by="", codes=codes, label_aliases=aliases,
                source_ids=sorted(set(book["source_ids"]) | {row["id"] for row in added_sources}))
    units = []
    unit_evidence = {}
    background_exclusions = {}
    for doc in documents:
        did = doc["id"]
        automatic_units = extract_evidence_units(parse_transcript(texts[did], filenames[did]))
        background_exclusions[did] = dict(Counter(unit.exclusion_reason for unit in automatic_units if unit.exclusion_reason))
        automatic_by_span = {(unit.source_start, unit.source_end): unit for unit in automatic_units}
        positive = defaultdict(list)
        for row in alignments:
            if (doc["split"] == "development" and row["source_association"]["state"] == "proposed_unique" and row["label_id"] in selected_lids
                    and row["status"] in {"unique_exact", "unique_whitespace"}):
                candidate = row["candidates"][0]
                if candidate["document_id"] == did:
                    positive[(candidate["source_start"], candidate["source_end"])].append(row)
        selected_spans = []
        used_labels = set()
        remaining = dict(positive)
        positive_target = (units_per_document + 1) // 2 if doc["split"] == "development" else 0
        while remaining and len(selected_spans) < positive_target:
            span = min(remaining, key=lambda span: (-len({r["label_id"] for r in remaining[span]} - used_labels), span))
            evidence = remaining.pop(span)
            if any(span[0] < end and span[1] > start for start, end, _ in selected_spans):
                continue
            selected_spans.append((*span, "source_positive"))
            used_labels.update(row["label_id"] for row in evidence)
        background_order = sorted(automatic_units, key=lambda unit: sha256_text(f"{did}:{unit.source_start}:{unit.source_end}"))
        for automatic in background_order:
            if automatic.exclusion_reason:
                continue
            start, end = automatic.source_start, automatic.source_end
            if len(selected_spans) >= units_per_document:
                break
            if not any(start < b and end > a for a, b, _ in selected_spans):
                selected_spans.append((start, end, "other_unlabeled_evidence"))
        if len(selected_spans) != units_per_document:
            raise ValueError(f"Insufficient non-overlapping evidence for {did}; cannot prepare requested unit count")
        for start, end, selection in sorted(selected_spans):
            uid = stable_id("unit", f"{did}\0{start}\0{end}")
            units.append(dict(id=uid, document_id=did, source_start=start, source_end=end,
                              span_sha256=sha256_text(texts[did][start:end]),
                              labels=[dict(code_id=code["id"], decision="uncertain", reviewed_by="") for code in codes]))
            unit_evidence[uid] = dict(selection=selection, text=texts[did][start:end],
                                      context=(automatic_by_span[(start, end)].context_text if (start, end) in automatic_by_span
                                               else texts[did][max(0, start - 160):min(len(texts[did]), end + 160)]),
                                      automatic_exclusion_reasons=sorted({unit.exclusion_reason for unit in automatic_units
                                          if unit.exclusion_reason and start < unit.source_end and end > unit.source_start}),
                                      historical=positive.get((start, end), []))
    draft = deepcopy(manifest)
    study = deepcopy(next(row for row in manifest["studies"] if row["id"] == sid))
    study.update(evaluation_permission="unknown", training_permission="unknown", permission_reviewed_by="", deidentification_reviewed_by="")
    required_sources = {doc["source_id"] for doc in documents} | set(book["source_ids"])
    source_map = {**original_sources, **{row["id"]: row for row in added_sources}}
    design = dict(approved_by="", scope="within-study pilot only; not a cross-study benchmark",
                  sampling="Enriched development pilot: up to three historical positive spans per development document, then deterministic hash-ordered non-overlapping unlabeled evidence. Calibration/test use only hash-ordered eligible source units independent of historical labels, with historical cues hidden. Background units use existing transcript parsing and evidence extraction; automatic exclusion reasons remove background candidates. Not a population estimate.",
                  selection_rule=("Prefer one consistent explicit workbook definition matched literally or by case/whitespace, then " if definition_sources else "")
                  + "development-only source-associated document coverage and aligned count; calibration/test annotation sources excluded from code selection.",
                  split_proposal="Sorted relative transcript path: first three development, fourth calibration, fifth test.")
    draft.update(studies=[study], documents=documents, sources=[source_map[key] for key in sorted(required_sources)],
                 codebooks=[book], units=units, split_policy=dict(group_by="session", held_out_study_required=False),
                 unitization=dict(name="source-aligned-enriched-pilot", version="1", reviewed_by=""),
                 pilot_design=design, preparation_provenance=dict(original_inventory_count=len(catalog),
                 definition_matches=definition_matches, source_associations=source_associations, selected_label_ids=selected_lids))
    items = []
    def card(kind, binding, title, fields, evidence=(), proposed=None, prompt="Review source evidence and record an explicit decision."):
        if kind == "pilot_design":
            proposal = ("Within-study workflow pilot only. Development combines historical positive evidence with unlabeled samples; "
                        "calibration/test use annotation-independent samples. Codes favor explicit workbook definitions and linked development coverage. "
                        "Review this sampling design before approval.")
        elif kind == "unitization":
            proposal = "Review each exact source target in its separate surrounding context. Confirm the unitization method before approving individual spans."
        elif kind == "document_group":
            proposal = f"Suggested split: {proposed['split']}. Filename grouping for {filenames[binding['document_id']]} needs your confirmation."
        elif kind == "code_definition":
            proposal = f"Preserve the literal code {proposed['name']!r}. Verify the cited source definition and edit the fields before confirmation."
        elif kind == "annotation":
            proposal = f"All {len(codes)} code decisions begin uncertain. Read the target and context; historical source cues never approve a decision automatically."
        else:
            proposal = ""
        items.append(dict(id=stable_id("review", kind + "\0" + str(binding)), kind=kind, binding=dict(kind=kind, **binding),
                          study_id=sid, study_name=study_key, title=title, prompt=prompt, priority="high",
                          evidence=list(evidence), proposed=proposal, fields=fields))
    def evidence(label, text, source="", detail=""):
        return dict(label=label, text=text, source=source, detail=detail)
    card("pilot_design", {}, "Approve within-study pilot design", [_field("approve", "Approve design", "checkbox", False)],
         [evidence("Scope", design["scope"]), evidence("Sampling", design["sampling"]), evidence("Selection", design["selection_rule"])], design)
    card("unitization", {}, "Approve evidence unitization", [_field("approve", "Approve unitization", "checkbox", False)],
         [evidence("Method", "Exact original offsets and hashes; unlabeled evidence is not a negative label.")], draft["unitization"])
    card("study_permission", dict(study_id=sid), "Study permission and deidentification", [
        _field("evaluation_permission", "Evaluation permission", "select", "unknown", ("unknown", "allowed", "denied")),
        _field("training_permission", "Training permission", "select", "unknown", ("unknown", "allowed", "denied")),
        _field("deidentified", "Deidentification reviewed", "checkbox", False)])
    for doc in documents:
        links = [evidence("Proposed annotation linkage", association["annotation_source_path"], source,
                          f"state={association['state']}; rule=same parent folder; transcript={filenames[doc['id']]}; unreviewed proposal")
                 for source, association in source_associations.items() if doc["id"] in association["document_ids"]]
        card("document_group", dict(document_id=doc["id"]), "Confirm grouping: " + Path(filenames[doc["id"]]).name, [
            _field("session_id", "Session ID", default=doc["session_id"]),
            _field("participant_ids", "Participant IDs, comma or newline separated", "textarea", "\n".join(doc["participant_ids"])),
            _field("split", "Split", "select", doc["split"], ("development", "calibration", "test")),
            _field("confirmed", "Grouping and split confirmed", "checkbox", False)],
            [evidence("Filename evidence", filenames[doc["id"]], doc["source_id"], "Opaque IDs are unreviewed proposals. Correct shared session/participant identities and source annotation linkage before approval."), *links], doc)
    for lid in selected_lids:
        code = next(code for code in codes if code["id"] == code_by_label[lid])
        definition = definition_matches[lid]
        code_evidence = [evidence("Literal label", literal_by_id[lid], lid,
                                 f"Development count={counts[lid]}; document coverage={len(coverage[lid])}; definition match={definition['rule']}")]
        code_evidence.extend(evidence("Workbook definition", row["definition"], row["source_path"],
                                     f"SHA256={row['source_sha256']}; sheet={row['sheet']}; row={row['row']}") for row in definition["sources"])
        examples = [row for row in alignments if row["label_id"] == lid and row["development_eligible"]
                    and row["status"] in {"unique_exact", "unique_whitespace"}][:2]
        code_evidence.extend(evidence("Development source example, not gold", row["candidates"][0]["text"],
                                     filenames[row["candidates"][0]["document_id"]],
                                     f"Historical label={row['label']}; source={row['annotation_source_path']}; {row['record_key']}") for row in examples)
        card("code_definition", dict(codebook_id=book["id"], code_id=code["id"], label_id=lid), "Review code: " + code["name"], [
            _field("name", "Code name", default=code["name"]), _field("definition", "Definition", "textarea", code["definition"]),
            _field("inclusion_criteria", "Inclusion criteria (optional)", "textarea", required=False), _field("exclusion_criteria", "Exclusion criteria (optional)", "textarea", required=False),
            _field("confirmed", "Definition confirmed", "checkbox", False)], code_evidence, code)
    for unit_index, unit in enumerate(units, 1):
        info = unit_evidence[unit["id"]]
        cue = [evidence("Source unit", info["text"], filenames[unit["document_id"]],
                        f"Offsets={unit['source_start']}:{unit['source_end']}; SHA256={unit['span_sha256']}; sampling={info['selection']}; automatic exclusion reasons={info['automatic_exclusion_reasons']}"),
               evidence("Surrounding context", info["context"])]
        cue.extend(evidence("Historical positive cue, not gold", row["label"], row["annotation_source_path"],
                            f"{row['record_key']}; author={row['author_id']}; SHA256={row['annotation_source_sha256']}") for row in info["historical"])
        card("annotation", dict(unit_id=unit["id"], document_id=unit["document_id"]), f"Span {unit_index}: " + " ".join(info["text"].split())[:75], [
            *[_field("code_" + code["id"], code["name"], "select", "uncertain", ("present", "absent", "uncertain")) for code in codes],
            _field("span_confirmed", "Exact evidence span confirmed", "checkbox", False)], cue, unit)
        items[-1]["source_span"] = {key: unit[key] for key in ("document_id", "source_start", "source_end")}
    summary = dict(catalog_label_count=len(catalog), selected_label_ids=selected_lids, document_count=len(documents),
                   code_count=len(codes), unit_count=len(units), alignment_status_counts=dict(Counter(row["status"] for row in alignments)),
                   administrative_exclusions=[dict(label_id=lid, label=literal_by_id[lid], count=count, reason="unmistakable administrative comment")
                                              for lid, count in sorted(excluded.items())],
                   development_selection_counts={lid: dict(aligned_count=counts[lid], document_coverage=len(coverage[lid])) for lid in selected_lids},
                   background_exclusion_counts=background_exclusions,
                   source_association_counts=dict(Counter(row["state"] for row in source_associations.values())),
                   population_estimate=False, cross_study_benchmark=False)
    packet = dict(format_version=1, packet_id="", title="Within-study pilot review", summary=summary,
                  instructions=["Review all required cards. Historical coding is evidence only; every code begins uncertain.",
                                "Permission, deidentification, grouping, definitions, spans and code decisions require human approval.",
                                "Unlabeled evidence is not negative. This enriched within-study pilot does not estimate population prevalence."],
                  studies=[dict(id=stable_id("study", item.key), name=item.key, selection="pilot" if item.key == study_key else "catalog_only") for item in study_inputs],
                  documents=[dict(id=doc["id"], name=filenames[doc["id"]], text=texts[doc["id"]],
                                  source_sha256=source_map[doc["source_id"]]["sha256"], text_sha256=doc["text_sha256"])
                             for doc in documents],
                  catalog=catalog, items=items)
    plan = dict(**design, document_splits={doc["id"]: doc["split"] for doc in documents},
                selected_label_ids=selected_lids, requested_units_per_document=units_per_document,
                unit_sampling={uid: info["selection"] for uid, info in unit_evidence.items()})
    return dict(manifest=draft, packet=packet, alignments=alignments, summary=summary, plan=plan)

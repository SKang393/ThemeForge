"""Runtime bridge for local open or context-guided candidate-codebook discovery."""
from __future__ import annotations

from dataclasses import asdict
from hashlib import sha256
import json
from typing import Sequence
from uuid import uuid4

from . import __version__
from .analysis_types import AnalysisResult, AnalysisSettings, CodebookEntry, QuoteUnit, Theme, ThemeQuote, TranscriptDocument
from .discovery_engine import build_candidate_codebook
from .discovery_units import build_meaning_units


def discover_documents(documents: Sequence[TranscriptDocument], settings: AnalysisSettings) -> AnalysisResult:
    units = build_meaning_units(documents)
    lookup = {unit["id"]: unit for unit in units}
    questions = tuple(settings.analytic_intent.research_questions)
    if settings.central_theme.strip():
        questions += (settings.central_theme.strip(),)
    has_context = bool(questions or settings.analytic_intent.theoretical_lens.strip())
    mode = settings.discovery_mode
    if mode == "auto":
        mode = "guided" if has_context else "open"
    if mode == "guided" and not has_context:
        raise ValueError("Context-guided discovery requires a research question, theoretical lens or shared focus topic.")
    backend_notes = []
    provider = None
    if settings.semantic_backend == "local_embeddings":
        from .analysis_vectors import tfidf_vectors
        from .local_embeddings import local_embedding_vectors
        def provider(texts):
            encoded = local_embedding_vectors(texts, settings.language_mode)
            backend_notes.append(encoded.note)
            return encoded.vectors or tfidf_vectors(texts)[0]
    discovery = build_candidate_codebook(units, research_questions=questions,
        theoretical_lens=settings.analytic_intent.theoretical_lens, mode=mode,
        max_candidates=max(1, settings.theme_count), vector_provider=provider)
    discovery["units"] = units
    discovery["mode"] = mode
    discovery["requested_context"] = {"research_questions": list(questions), "theoretical_lens": settings.analytic_intent.theoretical_lens}
    discovery["provenance"] = {
        "generator": "local_extractive_v1", "parameter_training": False, "network_calls": False,
        "imported_codebooks_used": False, "manuscripts_used_for_generation": False,
        "ai_draft_labels_used": False, "input_role": "analysis_input_not_training_labels",
        "definition_kind": "extractive_anchor", "model_backend_requested": settings.semantic_backend,
        "backend_notes": backend_notes,
    }
    themes = []
    palette = ("#2563eb", "#0f766e", "#9333ea", "#c2410c", "#be185d", "#4d7c0f", "#0369a1", "#7c3aed")
    for index, code in enumerate(discovery["codes"]):
        evidence_ids = [code["anchor_unit_id"], *[uid for uid in code["evidence_unit_ids"] if uid != code["anchor_unit_id"]]]
        evidence = [lookup[uid] for uid in evidence_ids]
        if settings.quotes_per_theme > 0:
            evidence = evidence[:settings.quotes_per_theme]
        quotes = [ThemeQuote(
            quote_id=unit["id"], speaker=unit["speaker"], text=unit["text"], relevance=0.0,
            source_line=next(doc.text for doc in documents if doc.name == unit["source_name"]).count("\n", 0, unit["source_start"]) + 1,
            source_name=unit["source_name"], source_start=unit["source_start"], source_end=unit["source_end"],
            source_hash=unit["source_hash"], context_start=unit["context_start"], context_end=unit["context_end"],
            context_text=unit["context_text"], rationale="Candidate evidence neighborhood; verify the source assertion. This is not an automatic code assignment or correctness probability. " + ("Review flags: " + ", ".join(unit["flags"]) if unit["flags"] else ""))
            for unit in evidence]
        def scope_text(value):
            return "\n".join(value) if isinstance(value, list) else str(value)
        definition = CodebookEntry(code["label"], code["definition"], code_id=code["id"],
            inclusion_criteria=scope_text(code["inclusion_criteria"]), exclusion_criteria=scope_text(code["exclusion_criteria"]), origin="inductive")
        themes.append(Theme(code["id"], code["label"], palette[index % len(palette)], code.get("scope_terms", []),
            len(code["evidence_unit_ids"]), float(code.get("context_relevance", 0.0)), quotes,
            validation={"artifact_kind": "candidate_code", "definition_kind": "extractive_anchor",
                        "origin": "local_extractive", "score_kind": "context_relevance_not_confidence",
                        "review_status": "Candidate code; interpretation and membership require review",
                        "evidence_count": len(code["evidence_unit_ids"]),
                        "possible_qualifications": len(code.get("qualification_unit_ids", [])),
                        "possible_countercases": len(code["counterexample_unit_ids"])},
            code_id=code["id"], code_version=1, code_definition=definition))
    evidence_units = tuple(QuoteUnit(id=unit["id"], speaker=unit["speaker"], text=unit["text"],
        source_line=next(doc.text for doc in documents if doc.name == unit["source_name"]).count("\n", 0, unit["source_start"]) + 1,
        word_count=len(unit["text"].split()), source_name=unit["source_name"], source_start=unit["source_start"],
        source_end=unit["source_end"], source_hash=unit["source_hash"], context_start=unit["context_start"],
        context_end=unit["context_end"], context_text=unit["context_text"],
        exclusion_reason="; ".join(unit["flags"]) if not unit["eligible"] else "") for unit in units)
    discovery["workspace_fingerprint"] = sha256(json.dumps([asdict(theme) for theme in themes], ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    discovery["snapshot_role"] = "immutable_generated_proposals; researcher-edited coding is stored separately in result.themes"
    settings_snapshot = asdict(settings)
    parameters = {key: value for key, value in settings_snapshot.items() if isinstance(value, (str, int, float))}
    parameters.update(unitization="meaning_turns_v1", discovery_mode=mode,
        context_sha256=sha256(json.dumps(discovery["requested_context"], sort_keys=True).encode()).hexdigest(),
        codebook_sha256=sha256(b"[]").hexdigest())
    notes = ["Candidate codes, categories and interpretive themes are distinct artifacts; no imported codebook is needed for discovery.",
             "This portable baseline proposes extractive participant-claim anchors and evidence neighborhoods. It does not generate a learned interpretation or establish thematic validity.",
             "Possible counterexamples are review leads, not proven contradictions. Unassigned and flagged passages remain accessible.",
             "Research questions/lens affect context-guided retrieval and ranking, not latent interpretation or author approval.",
             *discovery.get("notes", []), *backend_notes]
    return AnalysisResult(version=__version__, document_count=len(documents), quote_count=sum(unit["eligible"] for unit in units),
        themes=themes, notes=notes, validation_summary=["Researcher review: assess conceptual usefulness, evidence fit, scope, redundancy, omissions and correction effort. Software checks do not establish discovery quality."],
        analytic_intent=settings.analytic_intent, codebook_mode=settings.codebook_mode, evidence_units=evidence_units,
        run_id=uuid4().hex, source_hashes={doc.name: sha256(doc.text.encode()).hexdigest() for doc in documents},
        source_lengths={doc.name: len(doc.text) for doc in documents}, analysis_parameters=parameters, discovery=discovery)

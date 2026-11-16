from dataclasses import asdict, replace
from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from themeforge.analysis import AnalysisSettings, AnalyticIntent, CodebookEntry, TranscriptDocument, analyze_documents, analyze_transcript
from themeforge.codebook import load_codebook_entries
from themeforge.manual_editing import ManualSelection, code_selected_text, preserve_manual_themes, rename_theme, update_quote_boundary, update_theme_memo
from themeforge.project_io import ProjectState, load_project, save_project


TRANSCRIPT = "Participant 1: Transport costs forced families to cancel appointments.\nParticipant 2: Ocean currents regulate marine temperature."


class MethodologyCoreTests(unittest.TestCase):
    def test_fixed_codebook_no_match_never_invokes_discovery_or_embeddings(self):
        with patch("themeforge.analysis._cluster_quotes") as cluster, patch("themeforge.analysis._local_embedding_vectors") as embedding:
            result = analyze_transcript(TRANSCRIPT, AnalysisSettings(
                semantic_backend="local_embeddings", codebook_entries=(CodebookEntry("Astronomy"),),
            ))
        self.assertEqual(result.themes, [])
        self.assertGreater(result.quote_count, 0)
        self.assertTrue(any("no-match" in line for line in result.notes))
        cluster.assert_not_called()
        embedding.assert_not_called()

    def test_hybrid_discovers_unmatched_units_even_when_codebook_matches(self):
        result = analyze_transcript(TRANSCRIPT, AnalysisSettings(
            codebook_entries=(CodebookEntry("Transport"),), codebook_mode="hybrid",
        ))
        coded = [theme for theme in result.themes if theme.code_id]
        discovered = [theme for theme in result.themes if not theme.code_id]
        self.assertEqual(len(coded), 1)
        self.assertTrue(discovered)
        self.assertTrue(all("Ocean currents" in quote.text for theme in discovered for quote in theme.quotes))
        self.assertEqual({theme.validation["origin"] for theme in discovered}, {"discovery_candidate"})

    def test_hybrid_quote_display_limit_does_not_make_matches_novel(self):
        result = analyze_transcript(TRANSCRIPT + "\nParticipant 3: Transport expenses reduced access to appointments.", AnalysisSettings(
            codebook_entries=(CodebookEntry("Transport"),), codebook_mode="hybrid", quotes_per_theme=1,
        ))
        discovered = [quote.text for theme in result.themes if not theme.code_id for quote in theme.quotes]
        self.assertTrue(discovered)
        self.assertFalse(any("Transport" in text for text in discovered))

    def test_legacy_mode_keeps_explicit_discovery_fallback(self):
        result = analyze_transcript(TRANSCRIPT, AnalysisSettings(
            codebook_entries=(CodebookEntry("Astronomy"),), codebook_mode="legacy",
        ))
        self.assertTrue(result.themes)
        self.assertTrue(any("automatic theme discovery was used" in line for line in result.notes))

    def test_exact_negative_example_is_excluded_without_keyword_veto(self):
        negative = "Transport costs were never a problem."
        entry = CodebookEntry("Transport", negative_examples=(negative,))
        result = analyze_transcript(f"Participant 1: {negative}\nParticipant 2: Transport costs prevented attending appointments.", AnalysisSettings(codebook_entries=(entry,)))
        texts = [quote.text for theme in result.themes for quote in theme.quotes]
        self.assertNotIn(negative, texts)
        self.assertIn("Transport costs prevented attending appointments.", texts)

    def test_code_identity_survives_rename_and_reordering(self):
        entry = CodebookEntry("Transport", code_id="transport", version=2)
        renamed = replace(entry, name="Transport barriers")
        before = analyze_transcript(TRANSCRIPT, AnalysisSettings(codebook_entries=(entry,)))
        after = analyze_transcript(TRANSCRIPT, AnalysisSettings(codebook_entries=(CodebookEntry("Astronomy"), renamed)))
        self.assertEqual(before.themes[0].id, after.themes[0].id)
        self.assertEqual(after.themes[0].code_version, 2)

    def test_run_records_intent_definitions_source_versions_and_exact_context(self):
        intent = AnalyticIntent(methodology="reflexive", coding_logic="hybrid", analytic_depth="latent",
                                research_questions=("How is access negotiated?",), theoretical_lens="Agency", researcher_memo="Consider alternatives.", version=3)
        entry = CodebookEntry("Transport", code_id="transport", version=4, inclusion_criteria="Experienced barriers",
                              exclusion_criteria="Hypothetical barriers", negative_examples=("No problem.",), origin="deductive")
        result = analyze_transcript(TRANSCRIPT, AnalysisSettings(analytic_intent=intent, codebook_entries=(entry,)))
        self.assertEqual(result.analytic_intent, intent)
        self.assertEqual(result.codebook_snapshot, (entry,))
        self.assertEqual(result.source_hashes["Transcript"], sha256(TRANSCRIPT.encode()).hexdigest())
        self.assertTrue(result.run_id)
        self.assertTrue(any("not a validity grade" in text for text in result.validation_summary))
        for quote in result.themes[0].quotes:
            self.assertEqual(TRANSCRIPT[quote.source_start:quote.source_end], quote.text)
            self.assertEqual(TRANSCRIPT[quote.context_start:quote.context_end], quote.context_text)

    def test_rich_project_roundtrip_keeps_evidence_and_manual_work(self):
        settings = AnalysisSettings(analytic_intent=AnalyticIntent(methodology="codebook", research_questions=("무엇이 도움이 됩니까?",)),
                                    codebook_entries=(CodebookEntry("Transport", exclusion_criteria="Hypothetical", version=2),))
        result = analyze_transcript(TRANSCRIPT, settings)
        result = replace(result, themes=[replace(result.themes[0], memo="Keep this memo.", parent_theme_id="historical-parent")])
        state = ProjectState((), None, (TranscriptDocument("Transcript", TRANSCRIPT, "Source memo"),), settings, result, "Researcher")
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "study.tfproj"
            save_project(path, state)
            restored = load_project(path)
        self.assertEqual(restored, state)

    def test_v2_migration_is_explicit_and_preserves_old_result(self):
        payload = {"format_version": 2, "documents": [{"name": "Transcript", "text": TRANSCRIPT, "memo": "old"}],
                   "settings": {"codebook_entries": [{"name": "Transport", "description": "travel", "examples": ["Bus"]}]},
                   "result": {"version": "1.5.0", "document_count": 1, "quote_count": 0, "themes": [], "notes": ["Historical finding"]}}
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "old.tfproj"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("project.json", json.dumps(payload))
            old = load_project(path)
            save_project(path, old)
            restored = load_project(path)
        self.assertEqual(restored, old)
        self.assertEqual(old.settings.codebook_mode, "legacy")
        self.assertEqual(old.settings.analytic_intent.methodology, "unspecified")
        self.assertEqual(old.result.notes, ["Historical finding"])
        self.assertEqual(old.documents[0].memo, "old")

    def test_cached_source_hash_mismatch_fails_loading(self):
        payload = {"format_version": 3, "settings": {}, "documents": [{"name": "Changed", "text": "new text", "source_hash": "0" * 64}]}
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "changed.tfproj"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("project.json", json.dumps(payload))
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                load_project(path)

    def test_legacy_duplicate_name_rows_keep_both_examples_and_distinct_ids(self):
        payload = {"format_version": 2, "documents": [], "settings": {"codebook_entries": [
            {"name": "Access", "examples": ["First account"]},
            {"name": "Access", "examples": ["Second account"]},
        ]}, "result": {"themes": [], "notes": ["Original memo"]}}
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "legacy.tfproj"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("project.json", json.dumps(payload))
            migrated = load_project(path)
            save_project(path, migrated)
            self.assertEqual(load_project(path), migrated)
        entries = migrated.settings.codebook_entries
        self.assertEqual([entry.name for entry in entries], ["Access", "Access"])
        self.assertEqual([entry.examples for entry in entries], [("First account",), ("Second account",)])
        self.assertEqual(len({entry.code_id for entry in entries}), 2)
        self.assertTrue(any("duplicate identities" in line for line in migrated.result.notes))

    def test_csv_and_text_codebooks_preserve_boundaries_and_negative_examples(self):
        csv_text = "code,code_id,version,inclusion criteria,exclusion criteria,negative examples,origin\nAccess,access,3,Experienced,Imagined,No barrier. || Hypothetical.,deductive\n"
        text = "Code: Access\nCode ID: access\nVersion: 3\nInclusion: Experienced\nExclusion: Imagined\nNegative example: No barrier.\nNegative example: Hypothetical.\nOrigin: deductive\n"
        with tempfile.TemporaryDirectory() as temp:
            csv_path = Path(temp) / "book.csv"
            text_path = Path(temp) / "book.txt"
            csv_path.write_text(csv_text, encoding="utf-8")
            text_path.write_text(text, encoding="utf-8")
            csv_entries = load_codebook_entries(csv_path)
            text_entries = load_codebook_entries(text_path)
        self.assertEqual(csv_entries, text_entries)
        self.assertEqual(csv_entries[0].negative_examples, ("No barrier.", "Hypothetical."))
        self.assertEqual(csv_entries[0].version, 3)

    def test_duplicate_code_identity_and_source_names_require_review(self):
        with self.assertRaisesRegex(ValueError, "unique"):
            AnalysisSettings(codebook_entries=(CodebookEntry("A", code_id="same"), CodebookEntry("B", code_id="same")))
        with self.assertRaisesRegex(ValueError, "source names"):
            analyze_documents([TranscriptDocument("same", "A source"), TranscriptDocument("same", "Another source")])

    def test_short_reply_remains_manually_codeable_with_exact_context(self):
        text = "Moderator: Are fees still a barrier?\nParticipant 1: No, not anymore."
        result = analyze_transcript(text)
        reply = next(unit for unit in result.evidence_units if unit.speaker == "Participant 1")
        self.assertTrue(reply.exclusion_reason)
        edited = code_selected_text(result, "", ManualSelection(reply.source_name, reply.text, reply.source_line,
                                                                reply.source_start, reply.source_end, reply.speaker), "Barrier resolved")
        quote = edited.themes[0].quotes[0]
        self.assertEqual(quote.source_hash, reply.source_hash)
        self.assertEqual(text[quote.source_start:quote.source_end], quote.text)
        self.assertIn("Are fees still a barrier?", quote.context_text)

    def test_boundary_edit_replaces_prior_context_and_flags_changed_codebook(self):
        entry = CodebookEntry("Transport", code_id="transport")
        result = analyze_transcript(TRANSCRIPT, AnalysisSettings(codebook_entries=(entry,)))
        original = result.themes[0]
        edited = replace(result, themes=[rename_theme(original, "Reviewed transport", "")])
        changed = analyze_transcript(TRANSCRIPT, AnalysisSettings(codebook_entries=(replace(entry, description="Revised definition", version=2),)))
        kept = preserve_manual_themes(edited, changed)
        self.assertIn("code definition changed", kept.themes[0].validation["recheck_reason"])
        self.assertEqual(kept.themes[0].code_version, 1)
        self.assertEqual(kept.themes[0].code_definition, entry)
        memo_edited = update_theme_memo(kept, kept.themes[0].id, "Extra reflection")
        self.assertIn("code definition changed", memo_edited.themes[0].validation["recheck_reason"])
        rechecked = preserve_manual_themes(memo_edited, changed)
        self.assertIn("code definition changed", rechecked.themes[0].validation["recheck_reason"])
        other = next(unit for unit in result.evidence_units if "Ocean currents" in unit.text)
        updated = update_quote_boundary(edited, original.id, original.quotes[0].quote_id,
                                       ManualSelection(other.source_name, other.text, other.source_line, other.source_start, other.source_end))
        quote = updated.themes[0].quotes[0]
        self.assertEqual(quote.context_text, other.context_text)
        self.assertEqual(TRANSCRIPT[quote.source_start:quote.source_end], quote.text)

    def test_manual_out_of_bounds_and_fabricated_source_text_are_rejected(self):
        result = analyze_transcript(TRANSCRIPT)
        with self.assertRaisesRegex(ValueError, "source length"):
            code_selected_text(result, "", ManualSelection("Transcript", "Invented", 1, 500, 508))
        with self.assertRaisesRegex(ValueError, "source span"):
            code_selected_text(result, "", ManualSelection("Transcript", "Invented", 1, 0, 8, source_text=TRANSCRIPT))

    def test_long_manual_selection_requires_raw_source_for_hash_and_trims_lines(self):
        text = "\n\n" + "\n".join(f"Participant {i}: Transport choices affected attendance in week {i}." for i in range(6))
        result = analyze_transcript(text)
        unverified = code_selected_text(result, "", ManualSelection("Transcript", text, 1, 0, len(text)), "Long excerpt")
        self.assertEqual(unverified.themes[-1].quotes[0].source_hash, "")
        verified = code_selected_text(result, "", ManualSelection("Transcript", text, 1, 0, len(text), source_text=text), "Long excerpt")
        quote = verified.themes[-1].quotes[0]
        self.assertEqual(quote.source_hash, sha256(text.encode()).hexdigest())
        self.assertEqual(quote.source_line, 3)
        self.assertEqual(text[quote.source_start:quote.source_end], quote.text)


if __name__ == "__main__":
    unittest.main()

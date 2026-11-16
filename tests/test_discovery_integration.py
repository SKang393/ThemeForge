"""Fictional, domain-diverse fixtures exercising discovery through public flows."""
from hashlib import sha256
from dataclasses import replace
from html import escape
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import tkinter as tk
from types import ModuleType
import unittest
from unittest.mock import Mock, patch
import zipfile

from themeforge.analysis import analyze_documents
from themeforge.analysis_types import AnalyticIntent, AnalysisSettings, CodebookEntry, ThemeQuote, TranscriptDocument
from themeforge.app import ThemeForgeApp
from themeforge.local_embeddings import ENGLISH_MODEL, local_embedding_vectors
from themeforge.manual_editing import preserve_manual_themes, rename_theme
from themeforge.project_io import PROJECT_FORMAT_VERSION, ProjectState, load_project, save_project


DOCUMENTS = (
    TranscriptDocument("crossing.txt", "The ferry arrived after midnight and I missed the hospital visit.\n\nThe ferry timetable prevented me from returning before work."),
    TranscriptDocument("care.txt", "The clinic interpreter explained my medication without rushing me.\n\nThe clinic appointment gave me time to ask the nurse difficult questions."),
    TranscriptDocument("repair.txt", "The bicycle brake cable snapped when I crossed the bridge.\n\nI repaired the bicycle brake myself because the shop was closed."),
)


def settings_with_question(question, mode="auto"):
    return AnalysisSettings(discovery_mode=mode, analytic_intent=AnalyticIntent(research_questions=(question,)))


class DiscoveryRuntimeTests(unittest.TestCase):
    def test_quote_cap_keeps_the_defining_anchor_visible(self):
        documents = tuple(TranscriptDocument(f"case-{index}.txt", "P1: The ferry reduced my travel time because it avoided the bridge.") for index in range(3))
        result = analyze_documents(documents, AnalysisSettings(quotes_per_theme=1))
        codes = {code['id']: code for code in result.discovery['codes']}
        self.assertTrue(result.themes)
        for theme in result.themes:
            self.assertEqual(theme.quotes[0].quote_id, codes[theme.id]['anchor_unit_id'])

    def test_unchanged_discovery_definition_does_not_warn_after_manual_rename(self):
        generated = analyze_documents(DOCUMENTS)
        original = generated.themes[0]
        edited = replace(generated, themes=[rename_theme(original, "Researcher revised label", ""), *generated.themes[1:]])
        rerun = preserve_manual_themes(edited, analyze_documents(DOCUMENTS))
        preserved = next(theme for theme in rerun.themes if theme.id == original.id)
        self.assertEqual(preserved.name, "Researcher revised label")
        self.assertNotIn("code definition changed", preserved.validation.get('recheck_reason', ''))
        self.assertEqual(preserved.code_definition, original.code_definition)

    def test_auto_context_changes_actual_candidate_fingerprints(self):
        crossing = analyze_documents(DOCUMENTS, settings_with_question("How was the ferry experienced?"))
        care = analyze_documents(DOCUMENTS, settings_with_question("How was the clinic experienced?"))
        self.assertEqual(crossing.discovery["mode"], "guided")
        self.assertEqual(care.discovery["mode"], "guided")
        crossing_ids = {code["id"] for code in crossing.discovery["codes"]}
        care_ids = {code["id"] for code in care.discovery["codes"]}
        self.assertTrue(crossing_ids)
        self.assertTrue(care_ids)
        self.assertNotEqual(crossing_ids, care_ids)
        self.assertTrue(all("ferry" in code["anchor_text"].lower() for code in crossing.discovery["codes"]))
        self.assertTrue(all("clinic" in code["anchor_text"].lower() for code in care.discovery["codes"]))
        self.assertTrue(crossing.discovery["diagnostics"]["out_of_focus_unit_ids"])

    def test_explicit_open_ignores_research_context_with_visible_note(self):
        crossing = analyze_documents(DOCUMENTS, settings_with_question("How was the ferry experienced?", "open"))
        care = analyze_documents(DOCUMENTS, settings_with_question("How was the clinic experienced?", "open"))
        self.assertEqual(crossing.discovery["mode"], "open")
        self.assertEqual(crossing.discovery["codes"], care.discovery["codes"])
        self.assertIn("Open mode ignores supplied research questions", "\n".join(crossing.notes))

    def test_unspecified_method_and_long_source_anchor_do_not_gate_discovery(self):
        sentence = (
            "I could repair the damaged greenhouse after the winter storm because my neighbor brought replacement glass "
            "and patiently held each heavy frame while I checked the hinges, measured the opening, and fastened the panels "
            "so the young plants would survive another freezing night."
        )
        self.assertGreater(len(sentence), 180)
        result = analyze_documents([TranscriptDocument("greenhouse.txt", sentence)])
        self.assertEqual(result.analytic_intent.methodology, "unspecified")
        self.assertEqual(result.discovery["mode"], "open")
        self.assertEqual(result.discovery["codes"][0]["label"], sentence)
        self.assertGreater(len(result.discovery["codes"][0]["label"]), 180)

    def test_explicit_legacy_preserves_old_analysis_path(self):
        result = analyze_documents(DOCUMENTS, AnalysisSettings(discovery_mode="legacy", theme_count=2))
        self.assertFalse(result.discovery)
        self.assertTrue(result.themes)
        self.assertEqual(result.analysis_parameters["unitization"], "exact_source_spans_v1")
        self.assertTrue(all(unit.id.startswith("Q-") for unit in result.evidence_units))

    def test_imported_codebook_cannot_silently_enter_explicit_discovery(self):
        for mode in ("open", "guided"):
            with self.subTest(mode=mode), self.assertRaisesRegex(ValueError, "without an imported codebook"):
                AnalysisSettings(discovery_mode=mode, codebook_entries=(CodebookEntry("Imported label"),))

    def test_integrated_units_anchors_context_and_retained_passages_resolve(self):
        source = (
            "Moderator: Was the ferry useful?\n"
            "P1: The ferry reduced my travel time. But I could not use it after sunset.\n"
            "P2: No.\n"
            "P3: I will stop recording now."
        )
        documents = (*DOCUMENTS, TranscriptDocument("group.txt", source))
        result = analyze_documents(documents)
        by_name = {document.name: document.text for document in documents}
        units = {unit["id"]: unit for unit in result.discovery["units"]}
        self.assertEqual(set(units), {unit.id for unit in result.evidence_units})
        for unit in units.values():
            raw = by_name[unit["source_name"]]
            self.assertEqual(raw[unit["source_start"]:unit["source_end"]], unit["text"])
            self.assertEqual(raw[unit["context_start"]:unit["context_end"]], unit["context_text"])
            self.assertEqual(sha256(raw.encode("utf-8")).hexdigest(), unit["source_hash"])
        covered = set(result.discovery["unassigned_unit_ids"])
        for code in result.discovery["codes"]:
            unit = units[code["anchor_unit_id"]]
            raw = by_name[unit["source_name"]]
            self.assertTrue(unit["eligible"])
            self.assertLessEqual(unit["source_start"], code["anchor_source_start"])
            self.assertLessEqual(code["anchor_source_end"], unit["source_end"])
            self.assertEqual(raw[code["anchor_source_start"]:code["anchor_source_end"]], code["anchor_text"])
            self.assertFalse(set(code["evidence_unit_ids"]) & set(code["counterexample_unit_ids"]))
            for uid in code["evidence_unit_ids"]:
                self.assertTrue(units[uid]["eligible"])
            covered.update(code["evidence_unit_ids"])
        self.assertEqual(covered, set(units))
        self.assertTrue(any("interviewer" in unit["flags"] for unit in units.values()))
        self.assertTrue(any("administration" in unit["flags"] for unit in units.values()))
        self.assertTrue(all(unit["id"] in result.discovery["unassigned_unit_ids"] for unit in units.values() if not unit["eligible"]))
        for theme in result.themes:
            for quote in theme.quotes:
                self.assertEqual(quote.text, units[quote.quote_id]["text"])
                self.assertEqual(quote.source_hash, units[quote.quote_id]["source_hash"])

    def test_project_roundtrip_preserves_payload_and_old_projects_stay_legacy(self):
        settings = settings_with_question("How was the ferry experienced?")
        result = analyze_documents(DOCUMENTS, settings)
        state = ProjectState((), None, DOCUMENTS, settings, result)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "current.tfproj"
            save_project(path, state)
            restored = load_project(path)
            self.assertEqual(restored.settings, settings)
            self.assertEqual(restored.result.discovery, result.discovery)
            self.assertEqual(restored.result.evidence_units, result.evidence_units)
            for version in (1, PROJECT_FORMAT_VERSION):
                old = Path(directory) / f"old-{version}.tfproj"
                payload = {"format_version": version, "documents": [], "settings": {}, "result": None}
                with zipfile.ZipFile(old, "w") as archive:
                    archive.writestr("project.json", json.dumps(payload))
                self.assertEqual(load_project(old).settings.discovery_mode, "legacy")

    def test_cli_html_runs_discovery_and_preserves_source_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "care.txt"
            output = Path(directory) / "report.html"
            source.write_text(DOCUMENTS[1].text, encoding="utf-8")
            completed = subprocess.run([
                sys.executable, "-m", "themeforge.cli", str(source), "--format", "html",
                "--discovery-mode", "auto", "--research-question", "How was the clinic experienced?", "--out", str(output),
            ], cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, check=False)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            html = output.read_text(encoding="utf-8")
            self.assertIn("Candidate codebook", html)
            self.assertIn("guided", html)
            self.assertIn("Complete source-unit archive", html)
            self.assertIn(escape(DOCUMENTS[1].text.split("\n\n")[0]), html)
            self.assertIn(sha256(DOCUMENTS[1].text.encode("utf-8")).hexdigest(), html)


class OfflineEmbeddingIntegrationTests(unittest.TestCase):
    def fake_module(self, constructor):
        module = ModuleType("sentence_transformers")
        module.SentenceTransformer = constructor
        return module

    def test_model_constructor_is_strictly_cache_only(self):
        model = Mock()
        model.encode.return_value = [[1.0, 0.0], [0.0, 1.0]]
        constructor = Mock(return_value=model)
        with patch.dict(sys.modules, {"sentence_transformers": self.fake_module(constructor)}):
            result = local_embedding_vectors(["First assertion.", "Second assertion."], "English")
        constructor.assert_called_once_with(ENGLISH_MODEL, local_files_only=True, trust_remote_code=False)
        model.encode.assert_called_once_with(["First assertion.", "Second assertion."], normalize_embeddings=True)
        self.assertEqual(len(result.vectors), 2)

    def test_missing_cached_model_has_one_attempt_and_visible_lexical_fallback(self):
        constructor = Mock(side_effect=OSError("Fictional model cache is absent"))
        with patch.dict(sys.modules, {"sentence_transformers": self.fake_module(constructor)}):
            result = analyze_documents(DOCUMENTS, AnalysisSettings(semantic_backend="local_embeddings"))
        constructor.assert_called_once_with(ENGLISH_MODEL, local_files_only=True, trust_remote_code=False)
        self.assertTrue(result.discovery["codes"])
        self.assertIn("no download was attempted", "\n".join(result.notes))
        self.assertFalse(result.discovery["provenance"]["network_calls"])

    def test_unsupported_cache_only_constructor_never_retries_with_download_defaults(self):
        constructor = Mock(side_effect=TypeError("Fictional old constructor rejects local_files_only"))
        with patch.dict(sys.modules, {"sentence_transformers": self.fake_module(constructor)}):
            result = local_embedding_vectors(["A fictional assertion."], "English")
        constructor.assert_called_once_with(ENGLISH_MODEL, local_files_only=True, trust_remote_code=False)
        self.assertEqual(result.vectors, [])
        self.assertIn("no download was attempted", result.note)


class DiscoveryDesktopIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.app = ThemeForgeApp()
        self.app.withdraw()

    def tearDown(self):
        self.app.destroy()

    def test_ui_selected_discovery_mode_survives_settings_and_project_restore(self):
        for mode in ("open", "guided", "legacy", "auto"):
            with self.subTest(mode=mode):
                self.app.discovery_mode.set(mode)
                self.assertEqual(self.app._analysis_settings().discovery_mode, mode)
                state = self.app._project_state()
                self.app.discovery_mode.set("auto" if mode != "auto" else "legacy")
                self.app._restore_project(state, Path("fictional-mode-check.tfproj"))
                self.assertEqual(self.app.discovery_mode.get(), mode)
                self.assertEqual(self.app._analysis_settings().discovery_mode, mode)

    def quote(self, text, target):
        start = text.index(target)
        return ThemeQuote("source-bound", "P1", target, 0.0, 2, "unicode.txt", source_start=start,
                          source_end=start + len(target), source_hash=sha256(text.encode("utf-8")).hexdigest())

    def test_hashed_quote_is_never_relocated_after_source_modification(self):
        original = "P1: The ferry arrived late.\nP2: The ferry arrived late."
        quote = self.quote(original, "The ferry arrived late.")
        self.app.documents = [TranscriptDocument("unicode.txt", "A new heading\n" + original)]
        self.app._render_transcript_tabs()
        self.assertIsNone(self.app._locate_quote(quote))

    def test_supplementary_unicode_before_target_navigates_exact_python_span(self):
        text = "Moderator: 🙂 What changed?\nP1: The ferry arrived late."
        target = "The ferry arrived late."
        self.app.documents = [TranscriptDocument("unicode.txt", text)]
        self.app._render_transcript_tabs()
        location = self.app._locate_quote(self.quote(text, target))
        self.assertIsNotNone(location, "Verified source quote must remain navigable after a supplementary Unicode character")
        widget, start, end = location
        self.assertEqual(widget.get(start, end), target)

    def test_ui_html_export_writes_real_discovery_report(self):
        self.app.documents = list(DOCUMENTS)
        self.app.result = analyze_documents(DOCUMENTS)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "candidate-report.html"
            with patch("themeforge.app.filedialog.asksaveasfilename", return_value=str(output)):
                self.app.save_report()
            html = output.read_text(encoding="utf-8")
        self.assertIn("Candidate codebook", html)
        self.assertIn("Complete source-unit archive", html)
        self.assertIn("Saved candidate-report.html", self.app.status_text.get())


if __name__ == "__main__":
    unittest.main()

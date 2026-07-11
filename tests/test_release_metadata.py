import re
import tomllib
import unittest
from pathlib import Path

import themeforge


ROOT = Path(__file__).resolve().parents[1]


class ReleaseMetadataTests(unittest.TestCase):
    def test_version_metadata_is_1_5_0(self):
        pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        version_file = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
        init_version = themeforge.__version__

        self.assertEqual(pyproject["project"]["version"], "1.5.0")
        self.assertEqual(version_file, "1.5.0")
        self.assertEqual(init_version, "1.5.0")

    def test_readme_and_build_defaults_reference_1_5_0(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        build_script = (ROOT / "scripts" / "build_windows_portable.ps1").read_text(encoding="utf-8")

        self.assertIn("v1.5.0", readme)
        self.assertRegex(build_script, re.compile(r'\[string\]\$Version = "1.5.0"'))
        self.assertIn('"pyinstaller==6.21.0"', build_script)
        self.assertNotIn("--upgrade pyinstaller", build_script)

    def test_nlp_extra_declares_optional_local_embedding_dependencies(self):
        pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

        self.assertIn("sentence-transformers>=3", pyproject["project"]["optional-dependencies"]["nlp"])
        self.assertIn("kiwipiepy>=0.20", pyproject["project"]["optional-dependencies"]["nlp"])

    def test_release_note_and_portable_nlp_scope_are_present(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        release_note = (ROOT / "release_notes" / "v1.5.0.md").read_text(encoding="utf-8")
        build_script = (ROOT / "scripts" / "build_windows_portable.ps1").read_text(encoding="utf-8")

        self.assertIn("# ThemeForge v1.5.0", release_note)
        self.assertIn("does not bundle the optional NLP tier", readme)
        self.assertIn("not bundled in the default portable ZIP", release_note)
        self.assertIn('release_notes\\v$Version.md', build_script)

    def test_readme_documents_1_5_0_public_features(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        normalized_readme = readme.replace("\n  ", " ")

        self.assertIn("theme-by-source and theme-by-speaker matrix views", normalized_readme)
        self.assertIn("quote counts and unique-span coverage", normalized_readme)
        self.assertIn("identical shared transcript text", normalized_readme)
        self.assertIn("fixed shared meaning units", normalized_readme)
        self.assertIn("per-theme percent agreement and Cohen's kappa", normalized_readme)
        self.assertIn("prevalence or undefined-kappa warnings", normalized_readme)
        self.assertIn("side-by-side disagreements", normalized_readme)
        self.assertIn("coder identity", normalized_readme)
        self.assertIn("persistent and exportable coding audit events", normalized_readme)
        self.assertNotIn("does not calculate intercoder reliability by itself", readme)
        self.assertIn("- v1.5.0:", readme)
        self.assertIn("- Later: REFI-QDA exchange and DOCX report export.", readme)


if __name__ == "__main__":
    unittest.main()

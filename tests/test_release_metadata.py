import re
import tomllib
import unittest
from pathlib import Path

import themeforge


ROOT = Path(__file__).resolve().parents[1]


class ReleaseMetadataTests(unittest.TestCase):
    def test_version_metadata_is_1_4_0(self):
        pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        version_file = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
        init_version = themeforge.__version__

        self.assertEqual(pyproject["project"]["version"], "1.4.0")
        self.assertEqual(version_file, "1.4.0")
        self.assertEqual(init_version, "1.4.0")

    def test_readme_and_build_defaults_reference_1_4_0(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        build_script = (ROOT / "scripts" / "build_windows_portable.ps1").read_text(encoding="utf-8")

        self.assertIn("v1.4.0", readme)
        self.assertRegex(build_script, re.compile(r'\[string\]\$Version = "1\.4\.0"'))
        self.assertIn('"pyinstaller==6.21.0"', build_script)
        self.assertNotIn("--upgrade pyinstaller", build_script)

    def test_nlp_extra_declares_optional_local_embedding_dependencies(self):
        pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

        self.assertIn("sentence-transformers>=3", pyproject["project"]["optional-dependencies"]["nlp"])
        self.assertIn("kiwipiepy>=0.20", pyproject["project"]["optional-dependencies"]["nlp"])

    def test_release_note_and_portable_nlp_scope_are_present(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        release_note = (ROOT / "release_notes" / "v1.4.0.md").read_text(encoding="utf-8")
        build_script = (ROOT / "scripts" / "build_windows_portable.ps1").read_text(encoding="utf-8")

        self.assertIn("# ThemeForge v1.4.0", release_note)
        self.assertIn("does not bundle the optional NLP tier", readme)
        self.assertIn("not bundled in the default portable ZIP", release_note)
        self.assertIn('release_notes\\v$Version.md', build_script)


if __name__ == "__main__":
    unittest.main()

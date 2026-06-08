import re
import tomllib
import unittest
from pathlib import Path

import themeforge


ROOT = Path(__file__).resolve().parents[1]


class ReleaseMetadataTests(unittest.TestCase):
    def test_version_metadata_is_1_0_0(self):
        pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        version_file = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
        init_version = themeforge.__version__

        self.assertEqual(pyproject["project"]["version"], "1.0.0")
        self.assertEqual(version_file, "1.0.0")
        self.assertEqual(init_version, "1.0.0")

    def test_readme_and_build_defaults_reference_1_0_0(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        build_script = (ROOT / "scripts" / "build_windows_portable.ps1").read_text(encoding="utf-8")

        self.assertIn("v1.0.0", readme)
        self.assertRegex(build_script, re.compile(r'\[string\]\$Version = "1\.0\.0"'))


if __name__ == "__main__":
    unittest.main()

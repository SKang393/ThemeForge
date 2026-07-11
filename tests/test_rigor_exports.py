from __future__ import annotations

import csv
import io
import unittest

from themeforge.rigor import (
    DisagreementRecord,
    IntercoderReliabilityReport,
    MatrixCell,
    ThemeReliabilityRow,
)
from themeforge.rigor_exports import (
    export_disagreements_csv,
    export_matrix_csv,
    export_reliability_csv,
)


class RigorExportTests(unittest.TestCase):
    def test_matrix_csv_formats_counts_and_coverage_percent(self) -> None:
        rows = (
            MatrixCell(
                theme_name="Belonging",
                axis_value="interview, one.txt",
                quote_count=2,
                covered_chars=25,
                denominator_chars=100,
            ),
        )

        exported = export_matrix_csv(rows, axis_label="source")

        parsed = list(csv.DictReader(io.StringIO(exported)))
        self.assertEqual(
            parsed,
            [
                {
                    "theme": "Belonging",
                    "source": "interview, one.txt",
                    "quote_count": "2",
                    "unique_coded_characters": "25",
                    "coverage_percent": "25.00",
                }
            ],
        )

    def test_reliability_csv_includes_undefined_kappa_and_warning(self) -> None:
        report = IntercoderReliabilityReport(
            coder_a_label="Current",
            coder_b_label="Comparison",
            unit_count=2,
            theme_rows=(
                ThemeReliabilityRow(
                    theme_name="support",
                    both_present=2,
                    a_only=0,
                    b_only=0,
                    neither=0,
                    percent_agreement=1.0,
                    cohens_kappa=None,
                    kappa_undefined=True,
                    prevalence_warning="theme prevalence may make kappa unstable",
                ),
            ),
            disagreements=(),
        )

        exported = export_reliability_csv(report)

        parsed = list(csv.DictReader(io.StringIO(exported)))
        self.assertEqual(parsed[0]["kappa"], "Undefined")
        self.assertEqual(parsed[0]["prevalence_warning"], "theme prevalence may make kappa unstable")
        self.assertEqual(parsed[0]["percent_agreement"], "100.00")

    def test_disagreements_csv_formats_booleans_and_quotes_text(self) -> None:
        report = IntercoderReliabilityReport(
            coder_a_label="Alice",
            coder_b_label="Bao",
            unit_count=1,
            theme_rows=(),
            disagreements=(
                DisagreementRecord(
                    theme_name="support",
                    source_name="shared.txt",
                    speaker="P1",
                    text='Line one, with comma\nLine two "quoted"',
                    coder_a_present=True,
                    coder_b_present=False,
                ),
            ),
        )

        exported = export_disagreements_csv(report)

        parsed = list(csv.DictReader(io.StringIO(exported)))
        self.assertEqual(parsed[0]["Alice"], "yes")
        self.assertEqual(parsed[0]["Bao"], "no")
        self.assertEqual(parsed[0]["unit_text"], 'Line one, with comma\nLine two "quoted"')
        self.assertIn('"Line one, with comma', exported)

    def test_disagreements_csv_disambiguates_colliding_coder_headers(self) -> None:
        record = DisagreementRecord(
            theme_name="support",
            source_name="shared.txt",
            speaker="P1",
            text="First quote words.",
            coder_a_present=True,
            coder_b_present=False,
        )
        cases = (
            ("Alex", "Alex", "current coder: Alex", "comparison coder: Alex"),
            ("theme", "source", "current coder: theme", "comparison coder: source"),
        )

        for coder_a, coder_b, expected_a, expected_b in cases:
            with self.subTest(coder_a=coder_a, coder_b=coder_b):
                report = IntercoderReliabilityReport(coder_a, coder_b, 1, (), (record,))
                reader = csv.DictReader(io.StringIO(export_disagreements_csv(report)))
                rows = list(reader)

                self.assertEqual(
                    reader.fieldnames,
                    ["theme", "source", "speaker", "unit_text", expected_a, expected_b],
                )
                self.assertEqual(rows[0]["theme"], "support")
                self.assertEqual(rows[0][expected_a], "yes")
                self.assertEqual(rows[0][expected_b], "no")

    def test_matrix_csv_neutralizes_each_formula_prefix_in_theme_and_axis_value(self) -> None:
        # Given: each matrix-controlled field starts with every formula marker.
        rows = tuple(
            MatrixCell(
                theme_name=f"{prefix}theme",
                axis_value=f"{prefix}axis value",
                quote_count=1,
                covered_chars=10,
                denominator_chars=20,
            )
            for prefix in ("=", "+", "-", "@")
        )

        # When: the matrix is exported and parsed as CSV.
        reader = csv.DictReader(io.StringIO(export_matrix_csv(rows, axis_label="source")))
        parsed = list(reader)

        # Then: every theme and axis marker is preserved behind an apostrophe.
        self.assertEqual(
            reader.fieldnames,
            ["theme", "source", "quote_count", "unique_coded_characters", "coverage_percent"],
        )
        self.assertEqual(
            parsed,
            [
                {
                    "theme": f"'{prefix}theme",
                    "source": f"'{prefix}axis value",
                    "quote_count": "1",
                    "unique_coded_characters": "10",
                    "coverage_percent": "50.00",
                }
                for prefix in ("=", "+", "-", "@")
            ],
        )

    def test_reliability_csv_neutralizes_each_formula_prefix_in_theme_and_warning(self) -> None:
        # Given: each reliability-controlled field starts with every formula marker.
        report = IntercoderReliabilityReport(
            coder_a_label="Current",
            coder_b_label="Comparison",
            unit_count=4,
            theme_rows=tuple(
                ThemeReliabilityRow(
                    theme_name=f"{prefix}theme",
                    both_present=0,
                    a_only=1,
                    b_only=0,
                    neither=0,
                    percent_agreement=0.0,
                    cohens_kappa=None,
                    kappa_undefined=True,
                    prevalence_warning=f"{prefix}warning",
                )
                for prefix in ("=", "+", "-", "@")
            ),
            disagreements=(),
        )

        # When: the reliability report is exported and parsed as CSV.
        reader = csv.DictReader(io.StringIO(export_reliability_csv(report)))
        parsed = list(reader)

        # Then: every theme and warning marker is preserved behind an apostrophe.
        self.assertEqual(
            reader.fieldnames,
            [
                "theme",
                "both_present",
                "current_only",
                "comparison_only",
                "neither",
                "percent_agreement",
                "kappa",
                "prevalence_warning",
            ],
        )
        self.assertEqual(
            parsed,
            [
                {
                    "theme": f"'{prefix}theme",
                    "both_present": "0",
                    "current_only": "1",
                    "comparison_only": "0",
                    "neither": "0",
                    "percent_agreement": "0.00",
                    "kappa": "Undefined",
                    "prevalence_warning": f"'{prefix}warning",
                }
                for prefix in ("=", "+", "-", "@")
            ],
        )

    def test_disagreements_csv_neutralizes_each_formula_prefix_in_cells_and_coder_headers(self) -> None:
        # Given: each disagreement-controlled cell and dynamic header uses every formula marker.
        exports: list[tuple[list[str] | None, list[dict[str, str]]]] = []
        for prefix in ("=", "+", "-", "@"):
            report = IntercoderReliabilityReport(
                coder_a_label=f"{prefix}Current",
                coder_b_label=f"{prefix}Comparison",
                unit_count=1,
                theme_rows=(),
                disagreements=(
                    DisagreementRecord(
                        theme_name=f"{prefix}theme",
                        source_name=f"{prefix}source",
                        speaker=f"{prefix}speaker",
                        text=f"{prefix}text",
                        coder_a_present=True,
                        coder_b_present=False,
                    ),
                ),
            )

            # When: each disagreement report is exported and parsed as CSV.
            reader = csv.DictReader(io.StringIO(export_disagreements_csv(report)))
            exports.append((reader.fieldnames, list(reader)))

        # Then: every controlled cell and coder header is preserved behind an apostrophe.
        self.assertEqual(
            exports,
            [
                (
                    [
                        "theme",
                        "source",
                        "speaker",
                        "unit_text",
                        f"'{prefix}Current",
                        f"'{prefix}Comparison",
                    ],
                    [
                        {
                            "theme": f"'{prefix}theme",
                            "source": f"'{prefix}source",
                            "speaker": f"'{prefix}speaker",
                            "unit_text": f"'{prefix}text",
                            f"'{prefix}Current": "yes",
                            f"'{prefix}Comparison": "no",
                        }
                    ],
                )
                for prefix in ("=", "+", "-", "@")
            ],
        )


if __name__ == "__main__":
    unittest.main()

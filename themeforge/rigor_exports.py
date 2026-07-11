from __future__ import annotations

import csv
import io
from collections.abc import Sequence
from typing import Final

from .audit import neutralize_csv_cell
from .rigor import IntercoderReliabilityReport, MatrixCell

RELIABILITY_COLUMNS: Final = (
    "theme",
    "both_present",
    "current_only",
    "comparison_only",
    "neither",
    "percent_agreement",
    "kappa",
    "prevalence_warning",
)
DISAGREEMENT_BASE_COLUMNS: Final = ("theme", "source", "speaker", "unit_text")


def export_matrix_csv(rows: Sequence[MatrixCell], *, axis_label: str) -> str:
    output = io.StringIO(newline="")
    fieldnames = _matrix_columns(axis_label)
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    for row in rows:
        writer.writerow(
            {
                "theme": neutralize_csv_cell(row.theme_name),
                axis_label: neutralize_csv_cell(row.axis_value),
                "quote_count": str(row.quote_count),
                "unique_coded_characters": str(row.covered_chars),
                "coverage_percent": _percent(row.coverage_ratio),
            }
        )
    return output.getvalue()


def export_reliability_csv(report: IntercoderReliabilityReport) -> str:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=RELIABILITY_COLUMNS)
    writer.writeheader()
    for row in report.theme_rows:
        writer.writerow(
            {
                "theme": neutralize_csv_cell(row.theme_name),
                "both_present": str(row.both_present),
                "current_only": str(row.a_only),
                "comparison_only": str(row.b_only),
                "neither": str(row.neither),
                "percent_agreement": _percent(row.percent_agreement),
                "kappa": "Undefined" if row.kappa_undefined else _decimal(row.cohens_kappa),
                "prevalence_warning": neutralize_csv_cell(row.prevalence_warning),
            }
        )
    return output.getvalue()


def export_disagreements_csv(report: IntercoderReliabilityReport) -> str:
    output = io.StringIO(newline="")
    coder_a_label = neutralize_csv_cell(report.coder_a_label)
    coder_b_label = neutralize_csv_cell(report.coder_b_label)
    if (
        coder_a_label == coder_b_label
        or coder_a_label in DISAGREEMENT_BASE_COLUMNS
        or coder_b_label in DISAGREEMENT_BASE_COLUMNS
    ):
        coder_a_label = f"current coder: {coder_a_label}"
        coder_b_label = f"comparison coder: {coder_b_label}"
    fieldnames = (*DISAGREEMENT_BASE_COLUMNS, coder_a_label, coder_b_label)
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    for record in report.disagreements:
        writer.writerow(
            {
                "theme": neutralize_csv_cell(record.theme_name),
                "source": neutralize_csv_cell(record.source_name),
                "speaker": neutralize_csv_cell(record.speaker),
                "unit_text": neutralize_csv_cell(record.text),
                coder_a_label: _yes_no(record.coder_a_present),
                coder_b_label: _yes_no(record.coder_b_present),
            }
        )
    return output.getvalue()


def _matrix_columns(axis_label: str) -> tuple[str, str, str, str, str]:
    return ("theme", axis_label, "quote_count", "unique_coded_characters", "coverage_percent")


def _percent(ratio: float) -> str:
    return f"{ratio * 100:.2f}"


def _decimal(value: float | None) -> str:
    if value is None:
        return ""
    return f"{value:.6f}"


def _yes_no(value: bool) -> str:
    return "yes" if value else "no"

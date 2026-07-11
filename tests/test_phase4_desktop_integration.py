from __future__ import annotations

from pathlib import Path
import tempfile
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import patch

from themeforge.analysis_types import AnalysisResult, AnalysisSettings, Theme, ThemeQuote, TranscriptDocument
from themeforge.app import ThemeForgeApp
from themeforge.audit import AuditEvent
from themeforge.manual_editing import ManualSelection
from themeforge.project_io import ProjectState, UnsupportedProjectFormatError, save_project
from themeforge.rigor_dialog import RigorDialog
from themeforge.ui_model import APP_THEMES


DOCUMENT_TEXT = "P1: First quote words.\nP2: Second quote words."


def _quote(quote_id: str, speaker: str, text: str) -> ThemeQuote:
    start = DOCUMENT_TEXT.index(text)
    return ThemeQuote(
        quote_id=quote_id,
        speaker=speaker,
        text=text,
        relevance=1.0,
        source_line=1,
        source_name="Shared",
        source_start=start,
        source_end=start + len(text),
    )


def _result(quotes: list[ThemeQuote]) -> AnalysisResult:
    return AnalysisResult(
        version="test",
        document_count=1,
        quote_count=len(quotes),
        themes=[
            Theme(
                id="T1",
                name="Support",
                color="#2563eb",
                keywords=[],
                quote_count=len(quotes),
                score=1.0,
                quotes=quotes,
            )
        ],
        notes=[],
    )


def _event(action: str) -> AuditEvent:
    return AuditEvent(
        timestamp_utc="2026-01-02T03:04:05Z",
        actor="Tester",
        action=action,
        target_type="quote",
        target_id=action,
        details="changed",
    )


def _tree_by_columns(root: tk.Misc, columns: tuple[str, ...]) -> ttk.Treeview:
    for child in root.winfo_children():
        if isinstance(child, ttk.Treeview) and tuple(child["columns"]) == columns:
            return child
        try:
            return _tree_by_columns(child, columns)
        except LookupError:
            continue
    raise LookupError(columns)


def _tree_row_by_value(tree: ttk.Treeview, column_index: int, value: str) -> tuple[str, ...]:
    for row_id in tree.get_children():
        values = tuple(str(item) for item in tree.item(row_id, "values"))
        if values[column_index] == value:
            return values
    raise LookupError(value)


class Phase4DesktopIntegrationTests(unittest.TestCase):
    def test_open_rigor_dialog_refreshes_visible_rows_and_loaded_comparison_when_project_changes(self) -> None:
        # Given: an open Rigor Tools dialog with one coded quote, one audit row, and a loaded comparison.
        app = ThemeForgeApp()
        app.withdraw()
        try:
            document = TranscriptDocument(name="Shared", text=DOCUMENT_TEXT)
            first = _quote("Q1", "P1", "First quote words")
            second = _quote("Q2", "P2", "Second quote words")
            app.documents = [document]
            app.result = _result([first])
            app.audit_events = [_event("first")]
            app.researcher_name.set("Current")
            app.open_rigor_tools()
            dialog = next(child for child in app.winfo_children() if isinstance(child, RigorDialog))
            source_tree = _tree_by_columns(dialog, ("theme", "source", "quotes", "chars", "coverage"))
            speaker_tree = _tree_by_columns(dialog, ("theme", "speaker", "quotes", "chars", "coverage"))
            audit_tree = _tree_by_columns(dialog, ("timestamp", "actor", "action", "target", "details"))
            with tempfile.TemporaryDirectory() as temp_dir:
                comparison_path = Path(temp_dir) / "comparison.tfproj"
                save_project(
                    comparison_path,
                    ProjectState(
                        transcript_paths=(),
                        codebook_path=None,
                        documents=(document,),
                        settings=AnalysisSettings(),
                        result=_result([]),
                        researcher_name="Comparison",
                    ),
                )
                with patch("themeforge.rigor_dialog.filedialog.askopenfilename", return_value=str(comparison_path)):
                    dialog._choose_comparison()
                self.assertIsNotNone(dialog.report)
                self.assertEqual(dialog.report.theme_row("support").a_only, 1)

                # When: the still-open main project gains another quote through the real coding path.
                second_start = DOCUMENT_TEXT.index(second.text)
                app._apply_selection_coding(
                    ManualSelection(
                        source_name="Shared",
                        text=second.text,
                        source_line=2,
                        source_start=second_start,
                        source_end=second_start + len(second.text),
                        speaker="P2",
                    ),
                    "T1",
                    "",
                )

                # Then: already-open matrix, audit, and comparison reliability rows reflect current project state.
                source_values = source_tree.item(source_tree.get_children()[0], "values")
                speaker_p2_values = _tree_row_by_value(speaker_tree, 1, "P2")
                self.assertEqual(source_values[2], "2")
                self.assertEqual(speaker_p2_values[2], "1")
                self.assertEqual(len(audit_tree.get_children()), 2)
                self.assertEqual(dialog.report.theme_row("support").a_only, 2)
        finally:
            app.destroy()

    def _assert_second_comparison_clears_visible_results(
        self,
        second_load: ProjectState | OSError,
        expected_status: str,
    ) -> None:
        # Given: a successful comparison has populated the report and both intercoder trees.
        document = TranscriptDocument(name="Shared", text=DOCUMENT_TEXT)
        first = _quote("Q1", "P1", "First quote words")
        comparison_state = ProjectState(
            transcript_paths=(),
            codebook_path=None,
            documents=(document,),
            settings=AnalysisSettings(),
            result=_result([]),
            researcher_name="Comparison",
        )
        app = ThemeForgeApp()
        app.withdraw()
        try:
            app.documents = [document]
            app.result = _result([first])
            app.researcher_name.set("Current")
            app.open_rigor_tools()
            dialog = next(child for child in app.winfo_children() if isinstance(child, RigorDialog))
            with (
                patch(
                    "themeforge.rigor_dialog.filedialog.askopenfilename",
                    side_effect=("comparison.tfproj", "unusable.tfproj"),
                ),
                patch(
                    "themeforge.rigor_dialog.load_project",
                    side_effect=(comparison_state, second_load),
                ),
            ):
                dialog._choose_comparison()
                self.assertIsNotNone(dialog.report)
                self.assertGreater(len(dialog.reliability_tree.get_children()), 0)
                self.assertGreater(len(dialog.disagreement_tree.get_children()), 0)

                # When: the user chooses the unusable second comparison project.
                dialog._choose_comparison()

            # Then: no report or rows from the successful comparison remain visible.
            self.assertEqual(
                (
                    dialog.report is None,
                    len(dialog.reliability_tree.get_children()),
                    len(dialog.disagreement_tree.get_children()),
                ),
                (True, 0, 0),
            )
            self.assertEqual(dialog.status.get(), expected_status)
        finally:
            app.destroy()

    def test_choose_comparison_without_analysis_clears_previous_report_and_tree_rows(self) -> None:
        no_result_state = ProjectState(
            transcript_paths=(),
            codebook_path=None,
            documents=(TranscriptDocument(name="Shared", text=DOCUMENT_TEXT),),
            settings=AnalysisSettings(),
            result=None,
            researcher_name="No result",
        )

        self._assert_second_comparison_clears_visible_results(
            no_result_state,
            "Comparison project has no analysis result.",
        )

    def test_choose_invalid_comparison_clears_previous_report_and_tree_rows(self) -> None:
        self._assert_second_comparison_clears_visible_results(
            OSError("corrupt comparison"),
            "Could not load comparison project: corrupt comparison",
        )

    def test_open_rigor_dialog_refreshes_to_empty_state_when_context_result_becomes_none(self) -> None:
        # Given: an open dialog has populated matrices and a successful comparison.
        app = ThemeForgeApp()
        app.withdraw()
        try:
            document = TranscriptDocument(name="Shared", text=DOCUMENT_TEXT)
            first = _quote("Q1", "P1", "First quote words")
            app.documents = [document]
            app.result = _result([first])
            app.researcher_name.set("Current")
            app.open_rigor_tools()
            dialog = next(child for child in app.winfo_children() if isinstance(child, RigorDialog))
            comparison_state = ProjectState(
                transcript_paths=(),
                codebook_path=None,
                documents=(document,),
                settings=AnalysisSettings(),
                result=_result([]),
                researcher_name="Comparison",
            )
            with (
                patch("themeforge.rigor_dialog.filedialog.askopenfilename", return_value="comparison.tfproj"),
                patch("themeforge.rigor_dialog.load_project", return_value=comparison_state),
            ):
                dialog._choose_comparison()
            self.assertIsNotNone(dialog.report)
            self.assertGreater(len(dialog.source_matrix_tree.get_children()), 0)
            self.assertGreater(len(dialog.reliability_tree.get_children()), 0)

            # When: the dialog's live context source changes to documents with no analysis result.
            app.documents = [TranscriptDocument(name="Restored", text="P3: Unanalyzed text.")]
            app.result = None
            dialog.refresh_current_state()

            # Then: the existing dialog stays usable and clears all result-dependent output.
            self.assertEqual(
                (
                    len(dialog.source_matrix_tree.get_children()),
                    len(dialog.speaker_matrix_tree.get_children()),
                    dialog.report is None,
                    len(dialog.reliability_tree.get_children()),
                    len(dialog.disagreement_tree.get_children()),
                ),
                (0, 0, True, 0, 0),
            )
            self.assertEqual(dialog.status.get(), "Current project has no analysis result.")
        finally:
            app.destroy()

    def test_open_project_shows_existing_error_dialog_when_format_error_is_raised(self) -> None:
        # Given: the project loader raises the typed unsupported-format error.
        app = ThemeForgeApp()
        app.withdraw()
        try:
            with (
                patch("themeforge.app.filedialog.askopenfilename", return_value="future.tfproj"),
                patch(
                    "themeforge.app.load_project",
                    side_effect=UnsupportedProjectFormatError(format_version=99, supported_version=2),
                ),
                patch("themeforge.app.messagebox.showerror") as showerror,
            ):
                # When: the user opens the project from the main window.
                app.open_project()

            # Then: the existing messagebox boundary handles the error instead of leaking it.
            showerror.assert_called_once()
            self.assertEqual(showerror.call_args.args[0], "Open project failed")
            self.assertIn("format 99", showerror.call_args.args[1])
        finally:
            app.destroy()

    def test_dark_mode_treeview_styles_use_palette_readable_colors(self) -> None:
        # Given: the app is switched to the dark display palette.
        app = ThemeForgeApp()
        app.withdraw()
        try:
            app.display_mode.set("Dark")

            # When: display styles are applied.
            app.change_display_mode()

            # Then: Treeview rows, headings, and selected rows use readable dark palette colors.
            style = ttk.Style(app)
            palette = APP_THEMES["Dark"]
            self.assertEqual(style.lookup("Treeview", "background"), palette["surface"])
            self.assertEqual(style.lookup("Treeview", "foreground"), palette["text"])
            self.assertEqual(style.lookup("Treeview.Heading", "background"), palette["surface_alt"])
            self.assertIn(("selected", palette["accent"]), style.map("Treeview", "background"))
            self.assertIn(("selected", palette["background"]), style.map("Treeview", "foreground"))
        finally:
            app.destroy()


if __name__ == "__main__":
    unittest.main()

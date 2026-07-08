import unittest

from themeforge.analysis import AnalysisResult, Theme, ThemeQuote
from themeforge.manual_editing import (
    EditHistory,
    ManualSelection,
    code_selected_text,
    merge_theme,
    preserve_manual_themes,
    reassign_quote,
    rename_theme,
    split_quote_to_theme,
    uncode_quote,
    update_quote_memo,
    update_quote_boundary,
    update_theme_memo,
)


def quote(quote_id: str, text: str) -> ThemeQuote:
    return ThemeQuote(
        quote_id=quote_id,
        speaker="Participant",
        text=text,
        relevance=0.8,
        source_line=1,
    )


def theme(theme_id: str, name: str, quotes: list[ThemeQuote]) -> Theme:
    return Theme(
        id=theme_id,
        name=name,
        color="#2563eb",
        keywords=[name.lower()],
        quote_count=len(quotes),
        score=0.7,
        quotes=quotes,
        validation={"evidence_count": len(quotes), "review_status": "Researcher review required"},
    )


class ManualEditingTests(unittest.TestCase):
    def test_rename_theme_updates_name_keywords_and_review_status(self):
        updated = rename_theme(theme("T01", "Generated", [quote("Q1", "Text")]), "Peer support", "peer, support")

        self.assertEqual(updated.name, "Peer support")
        self.assertEqual(updated.keywords, ["peer", "support"])
        self.assertEqual(updated.validation["review_status"], "Researcher edited")

    def test_reassign_quote_moves_quote_between_themes(self):
        result = AnalysisResult("1.3.0", 1, 2, [
            theme("T01", "One", [quote("Q1", "First"), quote("Q2", "Second")]),
            theme("T02", "Two", []),
        ], [])

        updated = reassign_quote(result, "T01", "Q2", "T02")

        self.assertEqual([item.quote_id for item in updated.themes[0].quotes], ["Q1"])
        self.assertEqual([item.quote_id for item in updated.themes[1].quotes], ["Q2"])
        self.assertEqual(updated.themes[1].validation["review_status"], "Researcher edited")

    def test_missing_target_does_not_remove_quote(self):
        result = AnalysisResult("1.3.0", 1, 1, [
            theme("T01", "One", [quote("Q1", "First")]),
        ], [])

        updated = reassign_quote(result, "T01", "Q1", "missing")

        self.assertEqual([item.quote_id for item in updated.themes[0].quotes], ["Q1"])

    def test_merge_theme_combines_quotes_and_removes_source_theme(self):
        result = AnalysisResult("1.3.0", 1, 2, [
            theme("T01", "One", [quote("Q1", "First")]),
            theme("T02", "Two", [quote("Q2", "Second")]),
        ], [])

        updated = merge_theme(result, "T01", "T02")

        self.assertEqual([item.id for item in updated.themes], ["T01"])
        self.assertEqual([item.quote_id for item in updated.themes[0].quotes], ["Q1", "Q2"])

    def test_split_quote_to_theme_creates_new_theme(self):
        result = AnalysisResult("1.3.0", 1, 2, [
            theme("T01", "One", [quote("Q1", "First"), quote("Q2", "Second")]),
        ], [])

        updated = split_quote_to_theme(result, "T01", "Q2", "Manual split")

        self.assertEqual([item.quote_id for item in updated.themes[0].quotes], ["Q1"])
        self.assertEqual(updated.themes[1].name, "Manual split")
        self.assertEqual([item.quote_id for item in updated.themes[1].quotes], ["Q2"])

    def test_edit_history_undo_and_redo_restore_manual_result_snapshots(self):
        initial = AnalysisResult("1.3.0", 1, 1, [
            theme("T01", "Generated", [quote("Q1", "Text")]),
        ], [])
        renamed = AnalysisResult("1.3.0", 1, 1, [
            theme("T01", "Researcher Theme", [quote("Q1", "Text")]),
        ], [])
        history = EditHistory().record(initial)
        renamed.themes[0].keywords.append("late")

        undo = history.undo(renamed)

        if undo is None:
            self.fail("Undo should restore the recorded result")
        self.assertEqual(undo.result.themes[0].name, "Generated")
        self.assertEqual(undo.result.themes[0].keywords, ["generated"])

        redo = undo.history.redo(undo.result)

        if redo is None:
            self.fail("Redo should restore the undone result")
        self.assertEqual(redo.result.themes[0].name, "Researcher Theme")
        self.assertEqual(redo.result.themes[0].keywords, ["researcher theme", "late"])

    def test_preserve_manual_themes_keeps_researcher_edits_when_analysis_reruns(self):
        previous = AnalysisResult("1.3.0", 1, 2, [
            rename_theme(theme("T01", "Generated", [quote("Q1", "Manual quote")]), "Manual Theme", "manual"),
            theme("T02", "Untouched", [quote("Q2", "Old generated")]),
        ], [])
        generated = AnalysisResult("1.3.0", 1, 3, [
            theme("T01", "New Generated One", [quote("Q1", "Manual quote"), quote("Q3", "New quote")]),
            theme("T02", "New Generated Two", [quote("Q4", "Another quote")]),
        ], [])

        preserved = preserve_manual_themes(previous, generated)

        self.assertEqual([item.name for item in preserved.themes], ["Manual Theme", "New Generated Two"])
        self.assertEqual([item.quote_id for item in preserved.themes[0].quotes], ["Q1"])
        self.assertEqual([item.quote_id for item in preserved.themes[1].quotes], ["Q4"])
        self.assertEqual(preserved.themes[0].validation["review_status"], "Researcher edited")

    def test_code_selected_text_adds_arbitrary_passage_to_existing_theme(self):
        result = AnalysisResult("1.3.0", 1, 0, [
            theme("T01", "Curriculum", []),
        ], [])

        updated = code_selected_text(
            result,
            "T01",
            ManualSelection("interview.txt", "Selected participant words.", 2, 10, 38),
        )

        self.assertEqual(updated.themes[0].quote_count, 1)
        self.assertEqual(updated.themes[0].quotes[0].text, "Selected participant words.")
        self.assertEqual(updated.themes[0].quotes[0].source_name, "interview.txt")
        self.assertEqual(updated.themes[0].quotes[0].source_line, 2)
        self.assertEqual(updated.themes[0].quotes[0].source_start, 10)
        self.assertEqual(updated.themes[0].quotes[0].source_end, 38)
        self.assertEqual(updated.themes[0].validation["review_status"], "Researcher edited")

    def test_code_selected_text_creates_new_manual_theme(self):
        result = AnalysisResult("1.3.0", 1, 0, [
            theme("T01", "Curriculum", []),
        ], [])

        updated = code_selected_text(
            result,
            "",
            ManualSelection("interview.txt", "A new selected quote.", 3, 15, 36),
            "New Finding",
        )

        self.assertEqual(updated.themes[1].id, "M02")
        self.assertEqual(updated.themes[1].name, "New Finding")
        self.assertEqual(updated.themes[1].quotes[0].quote_id, "MQ01")

    def test_uncode_quote_removes_quote_from_theme(self):
        result = AnalysisResult("1.3.0", 1, 2, [
            theme("T01", "Curriculum", [quote("Q1", "Keep"), quote("Q2", "Remove")]),
        ], [])

        updated = uncode_quote(result, "T01", "Q2")

        self.assertEqual([item.quote_id for item in updated.themes[0].quotes], ["Q1"])
        self.assertEqual(updated.themes[0].quote_count, 1)
        self.assertEqual(updated.themes[0].validation["review_status"], "Researcher edited")

    def test_update_quote_boundary_replaces_text_and_offsets(self):
        result = AnalysisResult("1.3.0", 1, 1, [
            theme("T01", "Curriculum", [quote("Q1", "Old selection")]),
        ], [])

        updated = update_quote_boundary(
            result,
            "T01",
            "Q1",
            ManualSelection("interview.txt", "Expanded selected passage.", 4, 20, 46),
        )

        self.assertEqual(updated.themes[0].quotes[0].text, "Expanded selected passage.")
        self.assertEqual(updated.themes[0].quotes[0].source_name, "interview.txt")
        self.assertEqual(updated.themes[0].quotes[0].source_line, 4)
        self.assertEqual(updated.themes[0].quotes[0].source_start, 20)
        self.assertEqual(updated.themes[0].quotes[0].source_end, 46)
        self.assertEqual(updated.themes[0].validation["review_status"], "Researcher edited")

    def test_update_theme_memo_stores_researcher_note(self):
        result = AnalysisResult("1.3.0", 1, 1, [
            theme("T01", "Curriculum", [quote("Q1", "Evidence")]),
        ], [])

        updated = update_theme_memo(result, "T01", "Connects to teacher agency.")

        self.assertEqual(updated.themes[0].memo, "Connects to teacher agency.")
        self.assertEqual(updated.themes[0].validation["review_status"], "Researcher edited")

    def test_update_quote_memo_stores_researcher_note(self):
        result = AnalysisResult("1.3.0", 1, 1, [
            theme("T01", "Curriculum", [quote("Q1", "Evidence")]),
        ], [])

        updated = update_quote_memo(result, "T01", "Q1", "Useful contrast quote.")

        self.assertEqual(updated.themes[0].quotes[0].memo, "Useful contrast quote.")
        self.assertEqual(updated.themes[0].validation["review_status"], "Researcher edited")


if __name__ == "__main__":
    unittest.main()

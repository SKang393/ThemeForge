from __future__ import annotations

# noqa: SIZE_OK - one Tkinter window class; splitting callbacks adds UI indirection.

from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
import json
from pathlib import Path
from textwrap import shorten
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
import zipfile

from . import __version__
from .analysis import (
    AnalysisResult,
    AnalysisSettings,
    Theme,
    ThemeQuote,
    TranscriptDocument,
    analyze_documents_with_units,
)
from .analysis_types import CodebookEntry, QuoteUnit, SpeakerOverride
from .audit import AuditEvent, create_audit_event
from .codebook import load_codebook_entries
from .codebook_match import build_codebook_themes
from .definition_review import CodebookReuseDialog, MatchReviewReason, entry_label, review_definition
from .exports import export_json, export_markdown, export_quotes_csv
from .io import load_transcript_text
from .manual_editing import (
    EditHistory,
    MANUAL_ONLY_NOTE,
    ManualSelection,
    apply_codebook_definition,
    assignment_creation,
    code_selected_text,
    manual_assignment_details,
    merge_theme,
    preserve_manual_themes,
    quote_move_conflict,
    reassign_quote,
    reassignment_details,
    recorded_definition,
    rename_theme,
    set_theme_parent,
    split_quote_to_theme,
    uncode_quote,
    update_document_memo,
    update_quote_memo,
    update_quote_boundary,
    update_theme_memo,
)
from .match_reviews import (
    HISTORICAL_DEFINITION, HISTORICAL_ENTRY, NO_CURRENT_REVIEW, REVIEW_ACTION, REVIEW_LABELS, MatchReviews, match_id, match_snapshot,
)
from .project_io import ProjectCopyRequired, ProjectState, UnsupportedProjectFormatError, load_project, save_project
from .quote_units import speaker_stopwords, suggestion_inputs
from .rigor_dialog import RigorContext, RigorDialog
from .semantic_suggestions import SimilarPassage, find_similar_passages
from .source_location import TranscriptIndex
from .speaker_review import review_speakers
from .ui_model import (
    APP_THEMES,
    about_text,
    analysis_status_text,
    codebook_selection_summary,
    evidence_navigation_status,
    file_selection_summary,
    format_validation_summary,
    theme_list_label,
    transcript_tab_label,
)


@dataclass(frozen=True, slots=True)
class QuoteLocation:
    note: str
    start: str = ""
    end: str = ""
    verified: bool = False


@dataclass(frozen=True, slots=True)
class QuoteMatch:
    theme_index: int
    quote_index: int
    source_name: str
    location: QuoteLocation
    quote: ThemeQuote


class ThemeForgeApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"ThemeForge {__version__}")
        # Tk's natural request includes the full transcript and all editing tools.
        # Use the platform's maximum client area when opening on a laptop display.
        available_width, available_height = self.maxsize()
        self.minsize(min(1180, available_width), min(760, available_height))
        self.geometry(f"{min(1440, available_width)}x{min(900, available_height)}")

        self.display_mode = tk.StringVar(value="Light")
        self.palette = APP_THEMES["Light"]
        self.input_paths: list[Path] = []
        self.project_path: Path | None = None
        self.codebook_path: Path | None = None
        self.codebook_entries = ()
        self.documents: list[TranscriptDocument] = []
        self.speaker_overrides: tuple[SpeakerOverride, ...] = ()
        self.result: AnalysisResult | None = None
        self.audit_events: list[AuditEvent] = []
        self.match_reviews = MatchReviews()
        self.edit_history = EditHistory()
        self.transcript_widgets: dict[str, tk.Text] = {}
        self.transcript_frames: dict[str, ttk.Frame] = {}
        self.transcript_frame_sources: dict[str, str] = {}
        self.stripe_canvases: dict[str, tk.Canvas] = {}
        self.current_theme_index: int | None = None
        self.current_quote_matches: list[QuoteMatch] = []
        self.current_quote_index = -1
        self.similar_passages: list[SimilarPassage] = []
        # An empty coverage result has zero omissions. None means coverage is unavailable.
        self.unsuggested_passages: list[QuoteUnit] | None = None
        self.unsuggested_index = -1
        self.coverage_is_preview = False

        self.file_summary = tk.StringVar(value=file_selection_summary([]))
        self.codebook_summary = tk.StringVar(value=codebook_selection_summary(None))
        self.researcher_name = tk.StringVar(value="")
        self.status_text = tk.StringVar(value="Ready")
        self.detail_title = tk.StringVar(value="Transcript evidence")
        self.quote_status = tk.StringVar(value=evidence_navigation_status(0, 0))
        self.quote_meta = tk.StringVar(value="Open transcripts, then analyze to highlight quote evidence.")
        self.match_review_text = tk.StringVar(value="")
        self.quote_reason = tk.StringVar(
            value="Select a theme, then use Previous quote or Next quote to review why each highlighted quote was selected."
        )
        self.similar_note = tk.StringVar()
        self.theme_count = tk.IntVar(value=8)
        self.quotes_per_theme = tk.IntVar(value=0)
        self.central_theme = tk.StringVar(value="")
        self.theme_name = tk.StringVar(value="")
        self.theme_keywords = tk.StringVar(value="")
        self.target_theme = tk.StringVar(value="")
        self.parent_theme = tk.StringVar(value="")
        self.semantic_mode = tk.StringVar(value="TF-IDF")
        self.language_mode = tk.StringVar(value="Auto")

        self._configure_styles()
        self._build_layout()
        self._render_transcript_tabs()
        self.quotes_per_theme.trace_add("write", self._invalidate_coverage_preview)
        self.researcher_name.trace_add("write", self._show_match_review)
        self._empty_state = self._project_state()
        self._saved_state = self._empty_state
        self.protocol("WM_DELETE_WINDOW", self.request_close)

    def _configure_styles(self) -> None:
        self.configure(background=self.palette["background"])
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            themes = style.theme_names()
            if themes:
                style.theme_use(themes[0])

        style.configure(
            ".",
            background=self.palette["background"],
            foreground=self.palette["text"],
            font=("Segoe UI", 10),
        )
        style.configure("Surface.TFrame", background=self.palette["surface"])
        style.configure("App.TFrame", background=self.palette["background"])
        style.configure("Header.TFrame", background=self.palette["surface"])
        style.configure("Footer.TFrame", background=self.palette["background"])
        style.configure(
            "Panel.TLabelframe",
            background=self.palette["surface"],
            bordercolor=self.palette["border"],
        )
        style.configure(
            "Panel.TLabelframe.Label",
            background=self.palette["surface"],
            foreground=self.palette["text"],
            font=("Segoe UI", 10, "bold"),
        )
        style.configure(
            "Title.TLabel",
            background=self.palette["surface"],
            foreground=self.palette["text"],
            font=("Segoe UI Semibold", 18),
        )
        style.configure("Subtitle.TLabel", background=self.palette["surface"], foreground=self.palette["muted"])
        style.configure("Panel.TLabel", background=self.palette["surface"], foreground=self.palette["text"])
        style.configure("Muted.TLabel", background=self.palette["surface"], foreground=self.palette["muted"])
        style.configure("Footer.TLabel", background=self.palette["background"], foreground=self.palette["muted"])
        style.configure("Primary.TButton", font=("Segoe UI Semibold", 10), padding=(14, 7))
        style.configure("Secondary.TButton", padding=(12, 7))
        style.configure("TEntry", fieldbackground=self.palette["transcript_background"], bordercolor=self.palette["border"], padding=4)
        style.configure("TSpinbox", arrowsize=12)
        style.configure("TNotebook", background=self.palette["surface"], borderwidth=0)
        style.configure(
            "TNotebook.Tab",
            background=self.palette["surface_alt"],
            foreground=self.palette["text"],
            padding=(12, 6),
        )
        style.map("TNotebook.Tab", background=[("selected", self.palette["surface"])])
        self._configure_treeview_styles(style)

    def _build_layout(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        header = ttk.Frame(self, style="Header.TFrame", padding=(20, 16, 20, 14))
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(1, weight=1)

        title_block = ttk.Frame(header, style="Header.TFrame")
        title_block.grid(row=0, column=0, sticky="w")
        ttk.Label(title_block, text="ThemeForge", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(
            title_block,
            text=f"Qualitative coding workspace | v{__version__}",
            style="Subtitle.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(2, 0))

        header_actions = ttk.Frame(header, style="Header.TFrame")
        header_actions.grid(row=0, column=2, sticky="e")
        ttk.Label(header_actions, text="Window", style="Panel.TLabel").grid(row=0, column=0, sticky="e", padx=(0, 6))
        mode = ttk.Combobox(
            header_actions,
            textvariable=self.display_mode,
            values=tuple(APP_THEMES.keys()),
            width=8,
            state="readonly",
        )
        mode.grid(row=0, column=1, sticky="e", padx=(0, 8))
        mode.bind("<<ComboboxSelected>>", self.change_display_mode)
        ttk.Button(header_actions, text="Open project", style="Secondary.TButton", command=self.open_project).grid(row=0, column=2, padx=(0, 8))
        ttk.Button(header_actions, text="Save project", style="Secondary.TButton", command=self.save_project).grid(row=0, column=3, padx=(0, 8))
        ttk.Button(header_actions, text="Save as…", style="Secondary.TButton", width=-1,
                   command=lambda: self.save_project(save_as=True)).grid(row=0, column=4, padx=(0, 8))
        ttk.Button(header_actions, text="Rigor tools", style="Secondary.TButton", command=self.open_rigor_tools).grid(row=0, column=5, padx=(0, 8))
        ttk.Button(header_actions, text="About", style="Secondary.TButton", width=-1, command=self.show_about).grid(row=0, column=6)

        workspace = ttk.Frame(self, style="App.TFrame", padding=(18, 18, 18, 12))
        workspace.grid(row=1, column=0, sticky="nsew")
        workspace.columnconfigure(0, weight=0, minsize=270)
        workspace.columnconfigure(1, weight=1, minsize=310)
        workspace.columnconfigure(2, weight=2, minsize=560)
        workspace.rowconfigure(0, weight=1)

        controls = ttk.Labelframe(workspace, text="Input", style="Panel.TLabelframe", padding=(14, 12))
        controls.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        controls.columnconfigure(0, weight=1)
        self._build_controls(controls)

        themes = ttk.Labelframe(workspace, text="Themes", style="Panel.TLabelframe", padding=(14, 12))
        themes.grid(row=0, column=1, sticky="nsew", padx=(0, 12))
        themes.columnconfigure(0, weight=1)
        themes.rowconfigure(1, weight=1)
        self._build_theme_panel(themes)

        evidence = ttk.Labelframe(workspace, text="Evidence", style="Panel.TLabelframe", padding=(14, 12))
        evidence.grid(row=0, column=2, sticky="nsew")
        evidence.columnconfigure(0, weight=1)
        evidence.rowconfigure(3, weight=1)
        self._build_evidence_panel(evidence)

        footer = ttk.Frame(self, style="Footer.TFrame", padding=(20, 0, 20, 6))
        footer.grid(row=2, column=0, sticky="ew")
        footer.columnconfigure(0, weight=1)
        ttk.Label(footer, textvariable=self.status_text, style="Footer.TLabel").grid(row=0, column=0, sticky="ew")
        self.preview_button = ttk.Button(footer, text="Preview coverage", padding=(6, 1), command=self.preview_coverage)
        self.preview_button.grid(row=0, column=1, padx=(8, 0))
        self.preview_button.grid_remove()
        self.unsuggested_button = ttk.Button(footer, text="Next without suggestion", padding=(6, 1),
                                             command=self.next_unsuggested_passage)
        self.unsuggested_button.grid(row=0, column=2, padx=(8, 0))
        self.unsuggested_button.grid_remove()

    def _build_controls(self, parent: ttk.Frame) -> None:
        ttk.Label(parent, text="Transcripts", style="Panel.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(parent, textvariable=self.file_summary, style="Muted.TLabel", wraplength=220).grid(row=1, column=0, sticky="ew", pady=(2, 5))

        file_buttons = ttk.Frame(parent, style="Surface.TFrame")
        file_buttons.grid(row=2, column=0, sticky="ew")
        file_buttons.columnconfigure(0, weight=1)
        file_buttons.columnconfigure(1, weight=1)
        ttk.Button(file_buttons, text="Choose files", style="Secondary.TButton", command=self.open_transcript).grid(row=0, column=0, sticky="ew", padx=(0, 6))
        ttk.Button(file_buttons, text="Remove files", style="Secondary.TButton", command=self.remove_transcripts).grid(row=0, column=1, sticky="ew")

        ttk.Button(file_buttons, text="Speakers for suggestions…", style="Secondary.TButton",
                   command=self.review_suggestion_speakers).grid(row=1, column=0, columnspan=2, sticky="ew", pady=(2, 0))
        ttk.Separator(parent).grid(row=3, column=0, sticky="ew", pady=4)

        ttk.Label(parent, text="Codebook", style="Panel.TLabel").grid(row=4, column=0, sticky="w")
        ttk.Label(parent, textvariable=self.codebook_summary, style="Muted.TLabel", wraplength=220).grid(row=5, column=0, sticky="ew", pady=(2, 5))
        codebook_buttons = ttk.Frame(parent, style="Surface.TFrame")
        codebook_buttons.grid(row=6, column=0, sticky="ew")
        codebook_buttons.columnconfigure(0, weight=1)
        codebook_buttons.columnconfigure(1, weight=1)
        ttk.Button(codebook_buttons, text="Choose codebook", style="Secondary.TButton", command=self.open_codebook).grid(row=0, column=0, sticky="ew", padx=(0, 6))
        ttk.Button(codebook_buttons, text="Remove", style="Secondary.TButton", command=self.remove_codebook).grid(row=0, column=1, sticky="ew")

        ttk.Separator(parent).grid(row=7, column=0, sticky="ew", pady=5)

        ttk.Label(parent, text="Coder name", style="Panel.TLabel").grid(row=8, column=0, sticky="w")
        ttk.Entry(parent, textvariable=self.researcher_name).grid(row=9, column=0, sticky="ew", pady=(2, 5))

        ttk.Separator(parent).grid(row=10, column=0, sticky="ew", pady=(0, 5))

        ttk.Label(parent, text="Shared focus topic", style="Panel.TLabel").grid(row=11, column=0, sticky="w")
        ttk.Entry(parent, textvariable=self.central_theme).grid(row=12, column=0, sticky="ew", pady=(2, 5))

        option_row = ttk.Frame(parent, style="Surface.TFrame")
        option_row.grid(row=13, column=0, sticky="ew", pady=(0, 6))
        option_row.columnconfigure(0, weight=1)
        option_row.columnconfigure(1, weight=1)
        ttk.Label(option_row, text="Theme model", style="Panel.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(option_row, text="Language", style="Panel.TLabel").grid(row=0, column=1, sticky="w", padx=(10, 0))
        ttk.Combobox(
            option_row,
            textvariable=self.semantic_mode,
            values=("TF-IDF", "Local embeddings"),
            state="readonly",
            width=14,
        ).grid(row=1, column=0, sticky="ew", pady=(2, 0))
        ttk.Combobox(
            option_row,
            textvariable=self.language_mode,
            values=("Auto", "English", "Korean", "Multilingual"),
            state="readonly",
            width=14,
        ).grid(row=1, column=1, sticky="ew", padx=(10, 0), pady=(2, 0))

        count_row = ttk.Frame(parent, style="Surface.TFrame")
        count_row.grid(row=14, column=0, sticky="ew")
        count_row.columnconfigure(0, weight=1)
        count_row.columnconfigure(1, weight=1)

        ttk.Label(count_row, text="Themes", style="Panel.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(count_row, text="Quotes", style="Panel.TLabel").grid(row=0, column=1, sticky="w", padx=(10, 0))
        ttk.Spinbox(count_row, from_=1, to=20, width=6, textvariable=self.theme_count).grid(row=1, column=0, sticky="ew", pady=(2, 0))
        ttk.Spinbox(count_row, from_=0, to=200, width=6, textvariable=self.quotes_per_theme).grid(row=1, column=1, sticky="ew", padx=(10, 0), pady=(2, 0))

        actions = ttk.Frame(parent, style="Surface.TFrame")
        actions.grid(row=15, column=0, sticky="ew", pady=(8, 0))
        actions.columnconfigure((0, 1), weight=1)
        ttk.Button(actions, text="Analyze", style="Primary.TButton", command=self.analyze).grid(
            row=0, column=0, sticky="ew", padx=(0, 6))
        ttk.Button(actions, text="Export report", style="Secondary.TButton", command=self.save_report).grid(
            row=0, column=1, sticky="ew")

        ttk.Label(
            parent,
            text="Suggestions need researcher review before reporting.",
            style="Muted.TLabel",
            wraplength=220,
        ).grid(row=16, column=0, sticky="ew", pady=(6, 0))

    def _build_theme_panel(self, parent: ttk.Frame) -> None:
        self.theme_summary = tk.StringVar(value="No analysis yet")
        ttk.Label(parent, textvariable=self.theme_summary, style="Muted.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 8))

        list_frame = ttk.Frame(parent, style="Surface.TFrame")
        list_frame.grid(row=1, column=0, sticky="nsew")
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(0, weight=1)

        self.theme_list = tk.Listbox(
            list_frame,
            activestyle="none",
            borderwidth=0,
            exportselection=False,
            highlightthickness=1,
            font=("Segoe UI", 10),
            relief="flat",
        )
        self.theme_list.grid(row=0, column=0, sticky="nsew")
        self.theme_list.bind("<<ListboxSelect>>", self.show_selected_theme)

        scrollbar = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.theme_list.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.theme_list.configure(yscrollcommand=scrollbar.set)

        editor = ttk.Labelframe(parent, text="Manual editing", style="Panel.TLabelframe", padding=(10, 8))
        editor.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        editor.columnconfigure(0, weight=1)
        ttk.Label(editor, text="Theme name", style="Panel.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Entry(editor, textvariable=self.theme_name).grid(row=1, column=0, sticky="ew", pady=(4, 8))
        pages = ttk.Notebook(editor)
        pages.grid(row=2, column=0, sticky="ew")
        coding = ttk.Frame(pages, style="Surface.TFrame", padding=6)
        details = ttk.Frame(pages, style="Surface.TFrame", padding=6)
        hierarchy = ttk.Frame(pages, style="Surface.TFrame", padding=6)
        for page, title in ((coding, "Code passages"), (details, "Details"), (hierarchy, "Organize")):
            page.columnconfigure(0, weight=1)
            pages.add(page, text=title)

        ttk.Button(coding, text="New code from selection", style="Secondary.TButton",
                   command=self.code_selection_to_new_theme).grid(row=0, column=0, sticky="ew")
        ttk.Button(coding, text="Code selection", style="Secondary.TButton",
                   command=self.code_selection_to_theme).grid(row=1, column=0, sticky="ew", pady=(4, 0))
        self.definition_box = ttk.Combobox(coding, state="readonly", width=18, exportselection=False)
        self.definition_box.grid(row=2, column=0, sticky="ew", pady=(8, 4))
        ttk.Button(coding, text="Apply codebook entry…", command=self.code_selection_to_definition).grid(
            row=3, column=0, sticky="ew")

        ttk.Label(details, text="Keywords", style="Panel.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Entry(details, textvariable=self.theme_keywords).grid(row=1, column=0, sticky="ew", pady=(2, 4))
        ttk.Button(details, text="Save theme edits", style="Secondary.TButton", command=self.save_theme_edits).grid(
            row=2, column=0, sticky="ew")
        ttk.Label(details, text="Theme memo", style="Panel.TLabel").grid(row=3, column=0, sticky="w", pady=(6, 0))
        self.theme_memo_text = tk.Text(details, width=20, height=3, wrap="word", font=("Segoe UI", 9),
                                       borderwidth=1, relief="solid")
        self.theme_memo_text.grid(row=4, column=0, sticky="ew", pady=(2, 4))
        ttk.Button(details, text="Save theme memo", style="Secondary.TButton", command=self.save_theme_memo).grid(
            row=5, column=0, sticky="ew")

        ttk.Label(hierarchy, text="Parent theme", style="Panel.TLabel").grid(row=0, column=0, sticky="w")
        self.parent_theme_box = ttk.Combobox(hierarchy, textvariable=self.parent_theme, state="readonly")
        self.parent_theme_box.grid(row=1, column=0, sticky="ew", pady=(2, 4))
        ttk.Button(hierarchy, text="Save hierarchy", style="Secondary.TButton", command=self.save_theme_hierarchy).grid(
            row=2, column=0, sticky="ew")
        ttk.Label(hierarchy, text="Target theme", style="Panel.TLabel").grid(row=3, column=0, sticky="w", pady=(6, 0))
        self.target_theme_box = ttk.Combobox(hierarchy, textvariable=self.target_theme, state="readonly")
        self.target_theme_box.grid(row=4, column=0, sticky="ew", pady=(2, 4))
        action_row = ttk.Frame(hierarchy, style="Surface.TFrame")
        action_row.grid(row=5, column=0, sticky="ew")
        action_row.columnconfigure((0, 1), weight=1)
        ttk.Button(action_row, text="Move quote", style="Secondary.TButton", command=self.move_current_quote).grid(
            row=0, column=0, sticky="ew", padx=(0, 6))
        ttk.Button(action_row, text="Merge theme", style="Secondary.TButton", command=self.merge_current_theme).grid(
            row=0, column=1, sticky="ew")
        ttk.Button(hierarchy, text="Split quote to new theme", style="Secondary.TButton",
                   command=self.split_current_quote).grid(row=6, column=0, sticky="ew", pady=(4, 0))

        history_row = ttk.Frame(editor, style="Surface.TFrame")
        history_row.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        history_row.columnconfigure(0, weight=1)
        history_row.columnconfigure(1, weight=1)
        ttk.Button(history_row, text="Undo", style="Secondary.TButton", command=self.undo_manual_edit).grid(row=0, column=0, sticky="ew", padx=(0, 6))
        ttk.Button(history_row, text="Redo", style="Secondary.TButton", command=self.redo_manual_edit).grid(row=0, column=1, sticky="ew")

    def _build_evidence_panel(self, parent: ttk.Frame) -> None:
        ttk.Label(parent, textvariable=self.detail_title, style="Panel.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 8))

        navigation = ttk.Frame(parent, style="Surface.TFrame")
        navigation.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        # Keep navigation and editing reachable in the visible part of a narrow evidence pane.
        for label, command, row, column in (
            ("Previous quote", self.previous_quote, 0, 0), ("Next quote", self.next_quote, 0, 1),
            ("Uncode quote", self.uncode_current_quote, 1, 0), ("Update boundary", self.update_current_quote_boundary, 1, 1),
        ):
            ttk.Button(navigation, text=label, style="Secondary.TButton", width=-1, padding=(4, 2),
                       command=command).grid(row=row, column=column, sticky="ew", padx=(0, 6))
        ttk.Label(navigation, textvariable=self.quote_status, style="Muted.TLabel").grid(row=0, column=2, sticky="w")

        ttk.Label(parent, textvariable=self.quote_meta, style="Muted.TLabel", wraplength=520).grid(row=2, column=0, sticky="ew", pady=(0, 8))

        self.transcript_notebook = ttk.Notebook(parent)
        self.transcript_notebook.grid(row=3, column=0, sticky="nsew", pady=(0, 8))
        self.transcript_notebook.bind("<<NotebookTabChanged>>", self._on_transcript_tab_changed)

        evidence_tools = ttk.Notebook(parent, style="Evidence.TNotebook")
        evidence_tools.grid(row=4, column=0, sticky="ew")
        style = ttk.Style(self)
        right_margin = 0

        def fit_tabs(event):
            nonlocal right_margin
            if event.widget not in (self, evidence_tools):
                return
            # Let ttk fit the tab strip into the visible part of the evidence column.
            strip_right = evidence_tools.winfo_rootx() + evidence_tools.winfo_width()
            window_right = self.winfo_rootx() + self.winfo_width()
            overflow = max(0, strip_right - window_right)
            if overflow != right_margin:
                right_margin = overflow
                style.configure("Evidence.TNotebook", tabmargins=(0, 0, right_margin, 0))

        self.bind("<Configure>", fit_tabs, add="+")
        reason_frame = ttk.Frame(evidence_tools, style="Surface.TFrame", padding=(10, 8))
        evidence_tools.add(reason_frame, text="Quote review")
        self.quote_review_page = reason_frame
        reason_frame.columnconfigure(0, weight=1)
        ttk.Label(reason_frame, textvariable=self.quote_reason, style="Muted.TLabel", wraplength=520).grid(row=0, column=0, sticky="ew")
        review_row = ttk.Frame(reason_frame, style="Surface.TFrame")
        review_row.grid(row=1, column=0, sticky="w")
        for disposition, row, column in (("supported", 0, 0), ("declined", 0, 1), ("deferred", 1, 1)):
            ttk.Button(review_row, text=REVIEW_LABELS[disposition],
                       command=lambda choice=disposition: self.review_current_match(choice)).grid(
                row=row, column=column, sticky="ew")
        ttk.Button(review_row, text="Apply definition to this quote…", padding=(2, 4),
                   command=self.code_match_to_definition).grid(row=1, column=0, sticky="ew")
        scope = ttk.Frame(review_row, style="Surface.TFrame")
        scope.grid(row=2, column=0, columnspan=2, sticky="w")
        ttk.Label(scope, text="Next in", style="Muted.TLabel").grid(row=0, column=0, padx=(0, 3))
        self.review_source_box = ttk.Combobox(scope, state="readonly", width=22)
        self.review_source_box.grid(row=0, column=1, sticky="ew")
        self.review_source_box.bind("<<ComboboxSelected>>", self._change_navigation_scope)
        ttk.Button(review_row, text="Next unreviewed", command=lambda: self.next_review_match(None)).grid(row=3, column=0, sticky="ew")
        ttk.Button(review_row, text="Next deferred", command=lambda: self.next_review_match("deferred")).grid(row=3, column=1, sticky="ew")
        ttk.Button(review_row, text="Next supported", command=lambda: self.next_review_match("supported")).grid(row=4, column=0, sticky="ew")
        review_note = ttk.Label(reason_frame, textvariable=self.match_review_text, style="Muted.TLabel", wraplength=520)
        review_note.grid(row=2, column=0, sticky="w")

        def wrap_review_note(event):
            if event.widget not in (self, reason_frame, review_note):
                return
            # The evidence pane can extend beyond the client area on a narrow display.
            right = min(self.winfo_rootx() + self.winfo_width(), reason_frame.winfo_rootx() + reason_frame.winfo_width())
            width = min(520, right - review_note.winfo_rootx() - 12)
            if width > 0 and width != review_note.winfo_pixels(review_note.cget("wraplength")):
                review_note.configure(wraplength=width)

        self.bind("<Configure>", wrap_review_note, add="+")

        quote_memo = ttk.Frame(evidence_tools, style="Surface.TFrame", padding=(10, 8))
        evidence_tools.add(quote_memo, text="Quote memo")
        quote_memo.columnconfigure(0, weight=1)
        self.quote_memo_text = tk.Text(quote_memo, height=3, wrap="word", font=("Segoe UI", 9), borderwidth=1, relief="solid")
        self.quote_memo_text.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ttk.Button(quote_memo, text="Save quote memo", style="Secondary.TButton", command=self.save_quote_memo).grid(row=1, column=0, sticky="ew")

        document_memo = ttk.Frame(evidence_tools, style="Surface.TFrame", padding=(10, 8))
        evidence_tools.add(document_memo, text="Document memo")
        document_memo.columnconfigure(0, weight=1)
        self.document_memo_text = tk.Text(
            document_memo,
            height=3,
            wrap="word",
            font=("Segoe UI", 9),
            borderwidth=1,
            relief="solid",
        )
        self.document_memo_text.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ttk.Button(
            document_memo,
            text="Save document memo",
            style="Secondary.TButton",
            command=self.save_document_memo,
        ).grid(row=1, column=0, sticky="ew")

        similar = ttk.Frame(evidence_tools, style="Surface.TFrame", padding=(10, 8))
        evidence_tools.add(similar, text="Similar passages")
        similar.columnconfigure(0, weight=1)
        search_actions = ttk.Frame(similar, style="Surface.TFrame")
        search_actions.grid(row=0, column=0, sticky="w", pady=(0, 6))
        ttk.Button(
            search_actions,
            text="Find from theme",
            style="Secondary.TButton",
            padding=(5, 6),
            command=self.find_more_from_theme,
        ).grid(row=0, column=0, sticky="ew", padx=(0, 6))
        ttk.Button(
            search_actions,
            text="Find from quote",
            style="Secondary.TButton",
            padding=(5, 6),
            command=self.find_more_from_quote,
        ).grid(row=0, column=1, sticky="ew")
        self.similar_list = tk.Listbox(
            similar,
            height=3,
            activestyle="none",
            exportselection=False,
            borderwidth=1,
            relief="solid",
            font=("Segoe UI", 9),
        )
        self.similar_list.grid(row=1, column=0, sticky="ew")
        self.similar_list.bind("<<ListboxSelect>>", self.show_selected_similar_passage)
        ttk.Label(similar, textvariable=self.similar_note, style="Muted.TLabel", wraplength=520).grid(
            row=2,
            column=0,
            sticky="ew",
            pady=(6, 6),
        )
        ttk.Button(
            similar,
            text="Code selected suggestion",
            style="Secondary.TButton",
            command=self.code_selected_similar_passage,
        ).grid(row=3, column=0, sticky="w")

    def open_transcript(self) -> None:
        paths = filedialog.askopenfilenames(
            title="Open transcripts",
            filetypes=[
                ("Transcript files", "*.txt *.md *.csv *.docx *.rtf *.pdf"),
                ("Text files", "*.txt *.md *.csv"),
                ("Word documents", "*.docx *.rtf"),
                ("PDF files", "*.pdf"),
                ("All files", "*.*"),
            ],
        )
        if not paths:
            return

        try:
            input_paths = [Path(path) for path in paths]
            documents = self._load_documents(input_paths)
        except (OSError, ValueError, zipfile.BadZipFile) as exc:
            messagebox.showerror("Open transcripts failed", str(exc))
            return

        if self.documents or self.result is not None or self.audit_events:
            self._start_replacement_project(input_paths, documents)
            return
        # Importing the first sources keeps the setup, not a saved template's path.
        self.input_paths = input_paths
        self.documents = documents
        self.speaker_overrides = ()
        self.project_path = None
        self.result = None
        self.edit_history = EditHistory()
        self.theme_summary.set("No analysis yet")
        self._clear_theme_view("Select transcript text to create a code or apply a codebook entry.")
        self.file_summary.set(file_selection_summary(self.input_paths))
        self._render_transcript_tabs()
        self.status_text.set("Files loaded. Check Speakers for suggestions before Analyze to review who is included.")

    def review_suggestion_speakers(self) -> None:
        if not self.documents:
            self.status_text.set("Choose transcripts to review their speakers.")
            return
        choices = review_speakers(self, self.documents, self.speaker_overrides)
        if choices is None or choices == self.speaker_overrides:
            return
        details = {"previous": [asdict(rule) for rule in self.speaker_overrides],
                   "current": [asdict(rule) for rule in choices]}
        self.speaker_overrides = choices
        self._clear_suggestion_coverage()
        self._clear_similar_passages()
        self._record_audit_event("suggestion_speakers_changed", "project", "speakers",
                                 json.dumps(details, ensure_ascii=False))
        self.status_text.set("Speaker choices apply to Analyze, Find and Preview coverage. Save project to keep them.")

    def remove_transcripts(self) -> None:
        if self.documents or self.result is not None or self.audit_events:
            self._start_replacement_project([], [])

    def _start_replacement_project(self, paths: list[Path], documents: list[TranscriptDocument]) -> None:
        action = "replace the transcripts" if paths else "remove all transcripts"
        save = self._ask_save_before_leaving(f"To {action}, start a separate, unsaved project.")
        if save is None:
            return
        entries = ()
        if self.codebook_entries:
            entries = CodebookReuseDialog(self, self.codebook_entries).result
            if entries is None:
                return
        if save and not self.save_project():
            return
        state = ProjectState(tuple(paths), None, tuple(documents), AnalysisSettings(codebook_entries=entries))
        self._restore_project(state, None)
        self.status_text.set(f"New unsaved project. {len(documents)} transcript(s); {len(entries)} reused definition(s).")

    def _ask_save_before_leaving(self, action: str) -> bool | None:
        answer = messagebox.askyesnocancel(
            "Save the current study before leaving?",
            f"{action}\n\nYes: save the current study first.\nNo: leave without saving changes.\n"
            "Cancel: keep working in the current study.",
            parent=self, default=messagebox.CANCEL,
        )
        if answer and self._has_pending_source_edits():
            self.status_text.set("Apply or undo the unfinished theme, quote or document edits before saving and leaving.")
            return None
        return answer

    def _has_unsaved_work(self) -> bool:
        try:
            return self._has_pending_source_edits() or self._project_state() != self._saved_state
        except (ValueError, tk.TclError):
            return True  # A typed setting that cannot yet be saved still belongs to this study.

    def request_close(self) -> None:
        if self._has_unsaved_work():
            save = self._ask_save_before_leaving("Close ThemeForge?")
            if save is None or (save and not self.save_project()):
                return
        self.destroy()

    def _has_pending_source_edits(self) -> bool:
        theme = self._current_theme()
        match = self._current_quote_match()
        document = next((d for d in self.documents if d.name == self._current_document_name()), None)
        return bool((theme and (self.theme_name.get() != theme.name or
                     self.theme_keywords.get() != ", ".join(theme.keywords) or
                     self.theme_memo_text.get("1.0", "end-1c") != theme.memo)) or
                    (match and self.quote_memo_text.get("1.0", "end-1c") != match.quote.memo) or
                    (document and self.document_memo_text.get("1.0", "end-1c") != document.memo))

    def open_codebook(self) -> None:
        path = filedialog.askopenfilename(
            title="Open codebook",
            filetypes=[
                ("Codebook files", "*.csv *.txt *.md *.docx *.rtf *.pdf"),
                ("CSV files", "*.csv"),
                ("Text and Word files", "*.txt *.md *.docx *.rtf"),
                ("PDF files", "*.pdf"),
                ("All files", "*.*"),
            ],
        )
        if not path:
            return

        try:
            entries = load_codebook_entries(Path(path))
        except (OSError, ValueError, zipfile.BadZipFile) as exc:
            self.status_text.set("Codebook import failed. Kept the current codebook." if self.codebook_entries
                                 else "Codebook import failed.")
            messagebox.showerror("Open codebook failed", str(exc), parent=self)
            return

        self.codebook_path, self.codebook_entries = Path(path), entries
        self._refresh_definition_box()
        self._show_match_review()
        self.codebook_summary.set(codebook_selection_summary(self.codebook_path, len(self.codebook_entries)))
        count = len(entries)
        status = f"Loaded {count} codebook " + ("entry." if count == 1 else "entries.")
        if self.codebook_path.suffix.lower() == ".csv":
            status += " Put Include: and Exclude: in definitions. Separate criteria and hierarchy columns are ignored."
        self.status_text.set(status)

    def remove_codebook(self) -> None:
        self.codebook_path = None
        self.codebook_entries = ()
        self._refresh_definition_box()
        self._show_match_review()
        self.codebook_summary.set(codebook_selection_summary(None))
        self.status_text.set("Codebook removed")

    def analyze(self) -> None:
        if not self.documents:
            messagebox.showinfo("Open transcripts", "Choose one or more transcript files first.")
            return

        if self.match_reviews.has_reviews_since_analysis and not messagebox.askokcancel(
            "Start a new review pass?",
            "Analyze starts a fresh pass for all coders. Earlier decisions stay in Rigor tools → Audit log. "
            "The supported evidence table will be empty until passages are supported again.\n\n"
            "Choose Cancel to continue this pass in Quote review. Choose OK to run Analyze.",
            parent=self, default=messagebox.CANCEL, icon=messagebox.WARNING,
        ):
            return

        try:
            settings = self._analysis_settings()
            previous_result = self.result
            generated, eligible = analyze_documents_with_units(self.documents, settings)
            self.result = preserve_manual_themes(previous_result, generated)
            self.edit_history = EditHistory()
        except (tk.TclError, ValueError) as exc:
            messagebox.showerror("Analysis failed", str(exc))
            return

        if self.language_mode.get() != "Auto":
            self.result.notes.append(f"Language review mode selected: {self.language_mode.get()}.")

        self._record_audit_event(
            "analysis_run", "project", self.project_path.name if self.project_path is not None else "unsaved",
            f"documents={self.result.document_count}; themes={len(self.result.themes)}; quotes={self.result.quote_count}",
        )
        self.match_review_text.set("")
        self._render_themes()
        self._render_transcript_tabs()
        if self.result.themes:
            self.theme_list.selection_set(0)
            self.show_theme(0)
        else:
            self.detail_title.set("No themes found")
            self.quote_status.set(evidence_navigation_status(0, 0))
            self.quote_meta.set("No quote-length transcript units were found.")
            self.quote_reason.set("No quote rationale is available because no quote-length transcript units were found.")
            self._refresh_editor()

        status = analysis_status_text(
            document_count=self.result.document_count,
            quote_count=self.result.quote_count,
            theme_count=len(self.result.themes),
        )
        self.theme_summary.set(status)
        autosave_status = self._autosave_project()
        self._clear_suggestion_coverage()
        if settings.codebook_entries and generated.quote_count:
            status = self._show_suggestion_coverage(eligible, generated.themes, preview=False)
            if autosave_status:
                status = f"{autosave_status}. {status}"
        elif autosave_status:
            status += f"; {autosave_status}"
        self.status_text.set(status)

    def save_theme_edits(self) -> None:
        if self.result is None or self.current_theme_index is None:
            return
        original = self.result.themes[self.current_theme_index]
        themes = list(self.result.themes)
        themes[self.current_theme_index] = rename_theme(
            original,
            self.theme_name.get(),
            self.theme_keywords.get(),
        )
        updated = replace(self.result, themes=themes)
        if updated == self.result:
            return
        self.edit_history = self.edit_history.record(self.result)
        self.result = updated
        self._render_themes()
        self._select_theme(self.current_theme_index)
        renamed = updated.themes[self.current_theme_index]
        self._record_audit_event(
            "theme_renamed",
            "theme",
            renamed.id,
            f"from={original.name}; to={renamed.name}; keywords={len(renamed.keywords)}",
        )
        self._set_manual_status("Theme edits saved")

    def save_theme_memo(self) -> None:
        theme = self._current_theme()
        if self.result is None or theme is None:
            return
        memo_text = self.theme_memo_text.get("1.0", tk.END)
        updated = update_theme_memo(self.result, theme.id, memo_text)
        if updated == self.result:
            return
        self.edit_history = self.edit_history.record(self.result)
        self.result = updated
        self._render_themes()
        self._select_theme_by_id(theme.id)
        self._record_audit_event("theme_memo_updated", "theme", theme.id, f"memo_chars={len(memo_text.strip())}")
        self._set_manual_status("Theme memo saved")

    def save_theme_hierarchy(self) -> None:
        theme = self._current_theme()
        if self.result is None or theme is None:
            return
        parent_id = self._parent_theme_id()
        if parent_id is None:
            return
        updated = set_theme_parent(self.result, theme.id, parent_id)
        if updated == self.result:
            return
        self.edit_history = self.edit_history.record(self.result)
        self.result = updated
        self._render_themes()
        self._select_theme_by_id(theme.id)
        self._record_audit_event("theme_hierarchy_updated", "theme", theme.id, f"parent_theme_id={parent_id or 'top-level'}")
        self._set_manual_status("Theme hierarchy saved")

    def save_quote_memo(self) -> None:
        if self.result is None or self.current_theme_index is None:
            return
        match = self._current_quote_match()
        if match is None:
            self.status_text.set("No quote selected for memo")
            return
        theme_id = self.result.themes[self.current_theme_index].id
        memo_text = self.quote_memo_text.get("1.0", tk.END)
        updated = update_quote_memo(self.result, theme_id, match.quote.quote_id, memo_text)
        if updated == self.result:
            return
        self.edit_history = self.edit_history.record(self.result)
        self.result = updated
        self._render_themes()
        self._select_theme(self.current_theme_index)
        self._record_audit_event("quote_memo_updated", "quote", match.quote.quote_id, f"theme_id={theme_id}; memo_chars={len(memo_text.strip())}")
        self._set_manual_status("Quote memo saved")

    def save_document_memo(self) -> None:
        source_name = self._current_document_name()
        if source_name is None or not self.documents:
            self.status_text.set("No transcript selected for memo")
            return
        updated = update_document_memo(
            tuple(self.documents),
            source_name,
            self.document_memo_text.get("1.0", tk.END),
        )
        if updated == tuple(self.documents):
            return
        self.documents = list(updated)
        document = next((item for item in self.documents if item.name == source_name), None)
        memo_length = len(document.memo) if document is not None else 0
        self._record_audit_event("document_memo_updated", "document", source_name, f"memo_chars={memo_length}")
        self._set_manual_status("Document memo saved")

    def move_current_quote(self) -> None:
        if self.result is None or self.current_theme_index is None:
            return
        match = self._current_quote_match()
        target_id = self._target_theme_id()
        if match is None:
            self.status_text.set("No verified source anchor for this quote; nothing was moved.")
            return
        if target_id is None:
            return
        source = self.result.themes[self.current_theme_index]
        target = next((t for t in self.result.themes if t.id == target_id), None)
        if target is None or target.id == source.id:
            return
        if self._has_pending_source_edits():
            self.status_text.set("Apply unfinished theme or memo edits before moving a quote.")
            return
        q = match.quote
        conflict = quote_move_conflict(source, target, q)
        if conflict:
            self.status_text.set(conflict)
            return
        documents = [d for d in self.documents if d.name == q.source_name]
        if (len(documents) != 1 or not 0 <= q.source_start < q.source_end <= len(documents[0].text)
                or documents[0].text[q.source_start:q.source_end] != q.text
                or documents[0].text.count("\n", 0, q.source_start) + 1 != q.source_line):
            self.status_text.set("This quote's exact source anchor could not be verified; nothing was moved.")
            return
        actor = self.researcher_name.get().strip()
        if not actor:
            self.status_text.set("Enter your researcher name or pseudonym before moving a quote.")
            return
        reason = simpledialog.askstring("Move quote", f"Move {q.quote_id} from {source.name} ({source.id}) "
            f"to {target.name} ({target.id}) as {actor}?\nThe destination will not inherit the prior score. "
            "Enter your reason:", parent=self)
        if reason is None:
            return
        try:
            updated = reassign_quote(self.result, source.id, q.quote_id, target.id, actor=actor, reason=reason)
        except ValueError as exc:
            self.status_text.set(str(exc))
            return
        if updated == self.result:
            return
        self.edit_history = self.edit_history.record(self.result)
        self.result = updated
        self._render_themes()
        self._select_theme_by_id(target_id)
        self._focus_quote_by_id(q.quote_id)
        self._record_audit_event("quote_reassigned", "quote", q.quote_id, reassignment_details(source, target, q, reason))
        self._set_manual_status("Quote moved")

    def merge_current_theme(self) -> None:
        if self.result is None or self.current_theme_index is None:
            return
        target_id = self._target_theme_id()
        if target_id is None:
            return
        source = self.result.themes[self.current_theme_index]
        target = next((t for t in self.result.themes if t.id == target_id), None)
        if target is None or target.id == source.id:
            return
        if self._has_pending_source_edits():
            self.status_text.set("Apply unfinished theme or memo edits before merging groups.")
            return
        actor = self.researcher_name.get().strip()
        if not actor:
            self.status_text.set("Enter your researcher name or pseudonym before merging groups.")
            return
        reason = simpledialog.askstring("Merge groups", f"Merge {source.name} ({source.id}) into "
            f"{target.name} ({target.id}) as {actor}?\nShared passages count once. "
            "Both original coding records will remain in the audit. Enter your reason:", parent=self)
        if reason is None:
            return
        try:
            updated, details = merge_theme(self.result, target_id, source.id, documents=self.documents,
                                          actor=actor, reason=reason)
        except ValueError as exc:
            self.status_text.set(str(exc))
            return
        self.edit_history = self.edit_history.record(self.result)
        self.result = updated
        self._render_themes()
        self._select_theme_by_id(target_id)
        self._record_audit_event("theme_merge_recorded", "theme", target_id, details)
        self._set_manual_status("Themes merged")

    def split_current_quote(self) -> None:
        if self.result is None or self.current_theme_index is None:
            return
        match = self._current_quote_match()
        if match is None:
            return
        source = self.result.themes[self.current_theme_index]
        updated = split_quote_to_theme(
            self.result,
            source.id,
            match.quote.quote_id,
            self.theme_name.get() or f"Split from {source.name}",
        )
        if updated == self.result:
            return
        self.edit_history = self.edit_history.record(self.result)
        self.result = updated
        self._render_themes()
        self._select_theme(len(self.result.themes) - 1)
        self._record_audit_event(
            "quote_split",
            "quote",
            match.quote.quote_id,
            f"from_theme={source.id}; new_theme={self.result.themes[-1].id}",
        )
        self._set_manual_status("Quote split into a new theme")

    def _refresh_definition_box(self) -> None:
        self._clear_suggestion_coverage()
        if self.codebook_entries:
            self.preview_button.grid()
        else:
            self.preview_button.grid_remove()
        self.match_reviews.set_codebook(self.codebook_entries)
        self.definition_box.configure(values=tuple(entry_label(i, e) for i, e in enumerate(self.codebook_entries)))
        self.definition_box.set("")

    def code_selection_to_definition(self) -> None:
        index = self.definition_box.current()
        if not 0 <= index < len(self.codebook_entries):
            self.status_text.set("Load a codebook and choose an entry beside Apply codebook entry.")
            return
        self._review_codebook_assignment(self._selected_transcript_text(), index)

    def code_match_to_definition(self) -> None:
        match = self._current_quote_match()
        if not self.codebook_entries:
            self.status_text.set("Load a codebook to apply a definition to the current quote.")
            return
        if (self.result is None or not match or self._current_document_name() != match.source_name
                or not self._resolve_quote(match.quote).verified):
            self.status_text.set("Select a source-verified quote in its transcript before applying a definition.")
            return
        q = match.quote
        self._review_codebook_assignment(ManualSelection(q.source_name, q.text, q.source_line, q.source_start, q.source_end),
                                         None, match)

    def _review_codebook_assignment(self, selection: ManualSelection | None, index: int | None,
                                    match: QuoteMatch | None = None) -> None:
        documents = [d for d in self.documents if selection and d.name == selection.source_name]
        actor = self.researcher_name.get().strip()
        if not selection or len(documents) != 1 or not actor:
            self.status_text.set("Select text in one identifiable transcript and enter a researcher name or pseudonym first.")
            return
        if self._has_pending_source_edits():
            self.status_text.set("Apply unfinished theme or memo edits before coding another passage.")
            return
        entries = self.codebook_entries
        document = documents[0]
        if (document.text[selection.source_start:selection.source_end] != selection.text
                or document.text.count("\n", 0, selection.source_start) + 1 != selection.source_line):
            self.status_text.set("Selected text does not match the saved transcript; no assignment was made.")
            return
        before = self.result
        origin = None
        if match is not None:
            if before is None:
                return
            theme = before.themes[match.theme_index]
            origin = match_snapshot(theme, match.quote, self._display_sources)
            score = match.quote.reported_relevance
            score_text = f"{score:.3f}" if score is not None else "not scored"
            source_note = (f"Current match: {theme.name} ({theme.id}), quote {match.quote.quote_id}. "
                           f"Recorded relevance: {score_text}.\n"
                           "Applying creates a separate, unscored assignment. The current match stays unchanged.")
            chosen = review_definition(self, entries, None, document, selection, actor, source_note=source_note)
        else:
            chosen = review_definition(self, entries, index, document, selection, actor)
        if chosen is None:
            return
        same_passage = (self._current_quote_match() == match and self._current_document_name() == selection.source_name
                        if match is not None else self._selected_transcript_text() == selection)
        if (not same_passage or self.result is not before or self.codebook_entries != entries
                or self.researcher_name.get().strip() != actor
                or [d for d in self.documents if d.name == selection.source_name] != [document]):
            self.status_text.set("The selection, source, coder or codebook changed. Review again before applying.")
            return
        entry = entries[chosen]
        self.definition_box.current(chosen)
        self._commit_codebook_assignment(entry, document, selection, actor, origin=origin)

    def _commit_codebook_assignment(self, entry: CodebookEntry, document: TranscriptDocument,
                                    selection: ManualSelection, actor: str, *, origin: dict | None = None,
                                    replacing: tuple[Theme, ThemeQuote] | None = None) -> None:
        prior = self._result_for_manual_coding()
        try:
            updated, theme_id, quote_id = apply_codebook_definition(
                prior, entry, document, selection, actor, replacing=replacing)
        except ValueError as exc:
            self.status_text.set(str(exc))
            return
        if not quote_id:
            self.status_text.set("This exact passage already has this manual definition.")
            return
        self.edit_history = self.edit_history.record(prior)
        self.result = updated
        self._render_themes()
        self._select_theme_by_id(theme_id)
        self._focus_quote_by_id(quote_id)
        self._record_audit_event("codebook_passage_assigned", "quote", quote_id,
            manual_assignment_details(entry, document.name, selection.source_start, selection.source_end, theme_id))
        if origin is not None:
            self._record_audit_event("assignment_source_match", "quote", quote_id, json.dumps({
                "match": origin, "applied_theme_id": theme_id, "applied_definition": asdict(entry),
                "assignment_score": None}, ensure_ascii=False))
        if replacing:
            _, old = replacing
            self._record_audit_event("quote_boundary_updated", "quote", old.quote_id, json.dumps({
                "replacement_id": quote_id, "theme_id": theme_id, "source": document.name,
                "previous_span": [old.source_start, old.source_end],
                "replacement_span": [selection.source_start, selection.source_end],
            }, ensure_ascii=False))
        self._set_manual_status("Manual assignment boundary updated" if replacing else "Codebook definition applied manually")

    def code_selection_to_theme(self) -> None:
        if self.result is None:
            return
        selection = self._selected_transcript_text()
        theme = self._current_theme()
        if selection is None or theme is None:
            self.status_text.set("Select transcript text and a theme first")
            return
        self._apply_selection_coding(selection, theme.id, "")

    def code_selection_to_new_theme(self) -> None:
        selection = self._selected_transcript_text()
        if selection is None:
            self.status_text.set("Select transcript text first")
            return
        self._apply_selection_coding(selection, "", self.theme_name.get())

    def _result_for_manual_coding(self) -> AnalysisResult:
        return self.result or AnalysisResult(__version__, len(self.documents), 0, [], [MANUAL_ONLY_NOTE])

    def _apply_selection_coding(self, selection: ManualSelection, target_theme_id: str, new_theme_name: str,
                                *, suggestion: SimilarPassage | None = None) -> None:
        prior = self._result_for_manual_coding()
        if any(t.id == target_theme_id and "manual_codebook_definition" in t.validation for t in prior.themes):
            self.status_text.set("Use Apply codebook entry to confirm the definition and coder for this passage.")
            return
        documents = [d for d in self.documents if d.name == selection.source_name]
        if len(documents) != 1:
            self.status_text.set("This passage does not identify one active transcript; no assignment was made.")
            return
        retrieval_similarity = suggestion.similarity if suggestion is not None else None
        try:
            updated = code_selected_text(prior, target_theme_id, selection, new_theme_name,
                                         document=documents[0], actor=self.researcher_name.get(),
                                         retrieval_similarity=retrieval_similarity)
        except ValueError as exc:
            self.status_text.set(str(exc))
            return
        self.edit_history = self.edit_history.record(prior)
        self.result = updated
        self._render_themes()
        if target_theme_id:
            self._select_theme_by_id(target_theme_id)
            coded_theme_id = target_theme_id
        else:
            self._select_theme(len(self.result.themes) - 1)
            coded_theme_id = self.result.themes[-1].id
        group = next(t for t in updated.themes if t.id == coded_theme_id)
        quote = group.quotes[-1]
        method = "transcript selection" if retrieval_similarity is None else "applied similarity suggestion"
        self._record_audit_event("selection_assignment_recorded", "quote", quote.quote_id, json.dumps({
            "method": method, "theme_id": group.id, "theme_name": group.name, "source": quote.source_name,
            "start": quote.source_start, "end": quote.source_end, "text": quote.text,
            "retrieval_query": asdict(suggestion.query) if suggestion is not None else None,
            "retrieval_similarity": retrieval_similarity, "assignment_score": None}, ensure_ascii=False))
        self._focus_quote_by_id(quote.quote_id)
        self._set_manual_status("Selected text coded")

    def find_more_from_theme(self) -> None:
        self._find_more_like_this("")

    def find_more_from_quote(self) -> None:
        match = self._current_quote_match()
        if match is None:
            self.status_text.set("Select a highlighted quote first")
            return
        self._find_more_like_this(match.quote.quote_id)

    def _find_more_like_this(self, quote_id: str) -> None:
        theme = self._current_theme()
        if self.result is None or theme is None:
            self.status_text.set("Select a theme first")
            return
        try:
            settings = self._analysis_settings()
        except (tk.TclError, ValueError):
            self.status_text.set("Enter valid theme and quote counts first")
            return
        try:
            suggestion_result = find_similar_passages(
                self.documents, self.result, theme.id, settings, quote_id=quote_id,
                source_names=None if self.review_source_box.current() == 0 else self._navigation_sources(),
            )
        except ValueError as exc:
            self._clear_similar_passages()
            self.status_text.set(str(exc))
            return
        self.similar_passages = list(suggestion_result.passages)
        self.similar_note.set(f"Find in {self.review_source_box.get()} · {suggestion_result.note}")
        self.similar_list.delete(0, tk.END)
        for passage in self.similar_passages:
            preview = " ".join(passage.text.split())
            if len(preview) > 38:
                preview = f"{preview[:35]}..."
            speaker = passage.speaker if len(passage.speaker) <= 14 else f"{passage.speaker[:11]}..."
            source = passage.source_name if len(passage.source_name) <= 14 else f"{passage.source_name[:11]}..."
            self.similar_list.insert(
                tk.END,
                (
                    f"{passage.similarity:.2f} | {speaker} | "
                    f"{source}:{passage.source_line} | {preview}"
                ),
            )
        if self.similar_passages:
            self.similar_list.selection_set(0)
            self.show_selected_similar_passage()
            self.status_text.set(f"Found {len(self.similar_passages)} related uncoded passages")
            return
        self.status_text.set("No related uncoded passages found")

    def show_selected_similar_passage(self, _event: tk.Event | None = None) -> None:
        passage = self._selected_similar_passage()
        if passage is None:
            return
        for widget in self.transcript_widgets.values():
            widget.configure(state="normal")
            widget.tag_remove("similar_suggestion", "1.0", tk.END)
            widget.configure(state="disabled")
        location = self._locate_quote(
            ThemeQuote(
                quote_id="similar-suggestion",
                speaker=passage.speaker,
                text=passage.text,
                relevance=passage.similarity,
                source_line=passage.source_line,
                source_name=passage.source_name,
                source_start=passage.source_start,
                source_end=passage.source_end,
            )
        )
        frame = self.transcript_frames.get(passage.source_name)
        if location is None or frame is None:
            self.similar_note.set("Source location could not be verified. No suggestion highlight was placed.")
            return
        widget, start, end = location
        widget.configure(state="normal")
        widget.tag_configure(
            "similar_suggestion",
            background=self.palette["selected_quote"],
            foreground=self.palette["highlight_foreground"],
            underline=1,
        )
        widget.tag_add("similar_suggestion", start, end)
        widget.tag_raise("similar_suggestion")
        widget.see(start)
        widget.configure(state="disabled")
        self.transcript_notebook.select(frame)

    def code_selected_similar_passage(self) -> None:
        theme = self._current_theme()
        passage = self._selected_similar_passage()
        if theme is None or passage is None:
            self.status_text.set("Select a suggested passage first")
            return
        self._apply_selection_coding(
            ManualSelection(
                source_name=passage.source_name,
                text=passage.text,
                source_line=passage.source_line,
                source_start=passage.source_start,
                source_end=passage.source_end,
                speaker=passage.speaker,
            ),
            theme.id,
            "",
            suggestion=passage,
        )

    def _visible_review_match(self) -> QuoteMatch | None:
        match = self._current_quote_match()
        if (match and self._current_document_name() == match.source_name and self._selected_similar_passage() is None
                and self._resolve_quote(match.quote).verified):
            return match
        return None

    def review_current_match(self, disposition: str) -> None:
        theme, match = self._current_theme(), self._visible_review_match()
        if not theme or not match or disposition not in REVIEW_LABELS:
            self.status_text.set("Use Previous quote or Next quote to show the verified passage in its transcript before reviewing.")
            return
        if not self.match_reviews.definition_is_current(theme):
            self.status_text.set(HISTORICAL_DEFINITION)
            return
        actor = self.researcher_name.get().strip()
        if not actor:
            self.status_text.set("Enter a reviewer name or pseudonym before recording a match review.")
            return
        before, sources = self.result, self._display_sources
        q = match.quote
        citation = (f"{theme.name} ({theme.id}) | {actor}\n{q.source_name}, line {q.source_line}, "
                    f"characters [{q.source_start}, {q.source_end})\n\n")
        entry = self.match_reviews.codebook_entry(theme)
        reason = MatchReviewReason(self, REVIEW_LABELS[disposition], citation, q.text,
                                   sources.neighboring_turn_text(q), entry).result
        if not reason or not reason.strip():
            return
        if (self.result is not before or self._display_sources is not sources or self._visible_review_match() != match
                or self.researcher_name.get().strip() != actor or not self.match_reviews.definition_is_current(theme)):
            self.status_text.set("The match, source or reviewer changed. Review the visible passage again.")
            return
        snapshot = match_snapshot(theme, q, sources)
        details = json.dumps({"disposition": disposition, "reason": reason.strip(), "match": snapshot}, ensure_ascii=False)
        self._record_audit_event(REVIEW_ACTION, "theme_quote_match", match_id(snapshot), details)
        self._show_match_review()
        self._set_manual_status("Review note recorded. No coding assignment changed")

    def _show_match_review(self, *_trace_args: str) -> None:
        theme = self._current_theme()
        if theme is None or self.current_quote_index < 0 or not self.current_quote_matches:
            self.match_review_text.set("")
            return
        quote = self.current_quote_matches[self.current_quote_index].quote
        reviewer = self.researcher_name.get().strip() or None
        note = self.match_reviews.latest(theme, quote, self._display_sources, reviewer=reviewer)
        summary = NO_CURRENT_REVIEW if not reviewer else "No current match review for this coder."
        if note:
            summary = note.describe(theme.name)
        elif not self.match_reviews.definition_is_current(theme):
            summary = HISTORICAL_DEFINITION
            if reviewer:
                earlier = self.match_reviews.for_navigation(theme, quote, self._display_sources, reviewer=reviewer)
                if earlier:
                    summary = (f"{HISTORICAL_ENTRY} See Audit log.\n"
                               f"Earlier review: {earlier.disposition} by {earlier.actor} — {earlier.reason}")
        self.match_review_text.set(f"{quote.source_name}, line {quote.source_line} | {summary}")

    def uncode_current_quote(self) -> None:
        if self.result is None or self.current_theme_index is None:
            return
        match = self._current_quote_match()
        if match is None:
            self.status_text.set("Select a source-verified quote to uncode; no record was changed.")
            return
        theme_id = self.result.themes[self.current_theme_index].id
        updated = uncode_quote(self.result, theme_id, match.quote.quote_id)
        if updated == self.result:
            return
        self.edit_history = self.edit_history.record(self.result)
        self.result = updated
        self._render_themes()
        self._select_theme(min(self.current_theme_index, len(self.result.themes) - 1))
        self._record_audit_event("quote_uncoded", "quote", match.quote.quote_id, f"theme_id={theme_id}")
        self._set_manual_status("Quote uncoded")

    def update_current_quote_boundary(self) -> None:
        if self.result is None or self.current_theme_index is None:
            return
        match = self._current_quote_match()
        selection = self._selected_transcript_text()
        if match is None or selection is None:
            self.status_text.set("Select a quote and transcript text first")
            return
        if match.quote.manual_definition_assignment:
            self._revise_codebook_boundary(match, selection)
            return
        theme_id = self.result.themes[self.current_theme_index].id
        updated = update_quote_boundary(self.result, theme_id, match.quote.quote_id, selection)
        if updated == self.result:
            return
        self.edit_history = self.edit_history.record(self.result)
        self.result = updated
        self._render_themes()
        self._select_theme(self.current_theme_index)
        self._record_audit_event(
            "quote_boundary_updated",
            "quote",
            match.quote.quote_id,
            f"theme_id={theme_id}; source={selection.source_name}; chars={selection.source_start}-{selection.source_end}",
        )
        self._set_manual_status("Quote boundary updated")

    def _revise_codebook_boundary(self, match: QuoteMatch, selection: ManualSelection) -> None:
        before = self.result
        quote = match.quote
        if before is None or self._visible_review_match() != match or selection.source_name != quote.source_name:
            self.status_text.set("Select replacement text in the transcript showing this assignment.")
            return
        if (selection.source_start, selection.source_end) == (quote.source_start, quote.source_end):
            self.status_text.set("The selected text already has these boundaries.")
            return
        actor = self.researcher_name.get().strip()
        if not actor or self._has_pending_source_edits():
            self.status_text.set("Enter a researcher name and apply unfinished editor changes before revising a boundary.")
            return
        theme = before.themes[match.theme_index]
        try:
            entry = recorded_definition(theme)
            events = [e for e in self.audit_events if e.target_id == quote.quote_id
                      and e.target_type == "quote" and e.action == "codebook_passage_assigned"]
            assignment_creation(theme, quote, entry, events)
        except ValueError as exc:
            self.status_text.set(str(exc))
            return
        documents = [d for d in self.documents if d.name == quote.source_name]
        if len(documents) != 1:
            self.status_text.set("A unique source transcript is required to revise this assignment.")
            return
        document = documents[0]
        excerpt = shorten(quote.text, width=80, placeholder="…")
        note = (f"Original {theme.name} ({theme.id}), characters [{quote.source_start}:{quote.source_end}] “{excerpt}”\n"
                "Earlier reviews stay with the original span.")
        if review_definition(self, (entry,), 0, document, selection, actor, source_note=note,
                             apply_label="Replace assignment") is None:
            return
        if (self.result is not before or self._visible_review_match() != match
                or self._selected_transcript_text() != selection or self.researcher_name.get().strip() != actor
                or [d for d in self.documents if d.name == quote.source_name] != documents):
            self.status_text.set("The assignment, selection, source or coder changed. Review the boundary again.")
            return
        self._commit_codebook_assignment(entry, document, selection, actor, replacing=(theme, quote))

    def undo_manual_edit(self) -> None:
        if self.result is None:
            return
        change = self.edit_history.undo(self.result)
        if change is None:
            self.status_text.set("No manual edit to undo")
            return
        self.result = change.result
        self.edit_history = change.history
        if self.result.themes:
            self._render_themes()
            self._select_theme(min(self.current_theme_index or 0, len(self.result.themes) - 1))
        else:
            self._clear_theme_view("No coded passages.")
        self._record_audit_event("undo", "history", "manual_edits", f"themes={len(self.result.themes)}")
        self._set_manual_status("Manual edit undone")

    def redo_manual_edit(self) -> None:
        if self.result is None:
            return
        change = self.edit_history.redo(self.result)
        if change is None:
            self.status_text.set("No manual edit to redo")
            return
        self.result = change.result
        self.edit_history = change.history
        self._render_themes()
        if self.result.themes:
            self._select_theme(min(self.current_theme_index or 0, len(self.result.themes) - 1))
        self._record_audit_event("redo", "history", "manual_edits", f"themes={len(self.result.themes)}")
        self._set_manual_status("Manual edit redone")

    def open_project(self) -> None:
        path = filedialog.askopenfilename(
            title="Open ThemeForge project",
            filetypes=[
                ("ThemeForge project", "*.tfproj"),
                ("All files", "*.*"),
            ],
        )
        if not path:
            return
        incoming = Path(path)
        try:
            # An unreadable incoming project must not force a save or discard.
            state = load_project(incoming)
            if self._has_unsaved_work():
                save = self._ask_save_before_leaving(f"Open {incoming.name}?")
                if save is None:
                    return
                if save:
                    same = self.project_path is not None and self._same_project(self.project_path, incoming)
                    if not self.save_project(reserved_path=None if same else incoming):
                        return
                    if same:
                        return  # Do not restore the snapshot read before the successful save.
            self._restore_project(state, incoming)
        except (UnsupportedProjectFormatError, OSError, TypeError, ValueError, KeyError, zipfile.BadZipFile) as exc:
            messagebox.showerror("Open project failed", str(exc))

    @staticmethod
    def _same_project(left: Path, right: Path) -> bool:
        return left.resolve() == right.resolve() or (left.exists() and right.exists() and left.samefile(right))

    def save_project(self, *, reserved_path: Path | None = None, save_as: bool = False) -> bool:
        path = self.project_path
        if path is None or save_as:
            chosen = filedialog.asksaveasfilename(
                title="Save to a new file and continue working there" if save_as else "Save ThemeForge project",
                initialfile=f"{path.stem}-copy.tfproj" if save_as and path is not None else None,
                defaultextension=".tfproj",
                filetypes=[
                    ("ThemeForge project", "*.tfproj"),
                    ("All files", "*.*"),
                ],
            )
            if not chosen:
                return False
            path = Path(chosen)
        try:
            if reserved_path is not None and self._same_project(path, reserved_path):
                self.status_text.set("Not saved: choose a different file for the outgoing study before opening this project.")
                return False
            state = self._project_state()
            try:
                save_project(path, state, new_only=save_as or (self.project_path is None and state.needs_nullable_profile))
            except ProjectCopyRequired:
                chosen = filedialog.asksaveasfilename(
                    title="Save a separate nullable-score project (original stays unchanged)",
                    initialdir=path.parent, initialfile=f"{path.stem}-unscored.tfproj",
                    defaultextension=".tfproj", filetypes=[("ThemeForge project", "*.tfproj")])
                if not chosen:
                    self.status_text.set("Copy not saved. Changes remain open; the original project is unchanged.")
                    return False
                path = Path(chosen)
                if reserved_path is not None and self._same_project(path, reserved_path):
                    self.status_text.set("Not saved: choose a different file for the outgoing study.")
                    return False
                save_project(path, state, new_only=True)
        except (OSError, ValueError, tk.TclError) as exc:
            self.status_text.set("Project save failed. The study remains open.")
            detail = str(exc)
            if isinstance(exc, FileExistsError):
                detail = f"{path.name} already exists. Choose an unused filename."
            elif isinstance(exc, OSError):
                detail += "\n\nUse Save as… to save the open study to a new file in a writable folder."
            messagebox.showerror("Save project failed", detail, parent=self)
            return False
        self.project_path = path
        self._saved_state = state
        summary = f"Saved project {path.name}"
        if self._has_pending_source_edits():
            summary += ". Apply the unfinished editor changes to include them."
        self.status_text.set(summary)
        return True

    def _set_manual_status(self, action: str) -> None:
        autosave_status = self._autosave_project()
        if autosave_status:
            self.status_text.set(f"{action}; {autosave_status}")
            return
        self.status_text.set(action)

    def _record_audit_event(self, action: str, target_type: str, target_id: str, details: str) -> None:
        self.audit_events.append(
            create_audit_event(
                actor=self.researcher_name.get(),
                action=action,
                target_type=target_type,
                target_id=target_id,
                details=details,
            )
        )
        self.match_reviews.observe(self.audit_events[-1])
        for child in self.winfo_children():
            if isinstance(child, RigorDialog):
                child.refresh_current_state()

    def _autosave_project(self) -> str | None:
        if self.project_path is None:
            return None
        try:
            state = self._project_state()
            save_project(self.project_path, state)
        except ProjectCopyRequired as exc:
            return str(exc)
        except OSError as exc:
            return f"autosave failed. Use Save as… to choose a writable folder. {exc}"
        except (ValueError, tk.TclError) as exc:
            return f"autosave failed: {exc}"
        self._saved_state = state
        return f"autosaved {self.project_path.name}"

    def _project_state(self) -> ProjectState:
        return ProjectState(
            transcript_paths=tuple(self.input_paths),
            codebook_path=self.codebook_path,
            documents=tuple(self.documents),
            settings=self._analysis_settings(),
            result=self.result,
            researcher_name=self.researcher_name.get(),
            audit_events=tuple(self.audit_events),
        )

    def _restore_project(self, state: ProjectState, path: Path | None) -> None:
        self.project_path = path
        self.input_paths = list(state.transcript_paths)
        self.codebook_path = state.codebook_path
        self.codebook_entries = state.settings.codebook_entries
        self.speaker_overrides = state.settings.speaker_overrides
        self.documents = list(state.documents)
        self.result = state.result
        self.audit_events = list(state.audit_events)
        self.match_reviews = MatchReviews(self.audit_events)
        self._refresh_definition_box()
        self.match_review_text.set("")
        self.edit_history = EditHistory()
        self.theme_count.set(state.settings.theme_count)
        self.quotes_per_theme.set(state.settings.quotes_per_theme)
        self.central_theme.set(state.settings.central_theme)
        self.semantic_mode.set(
            "Local embeddings"
            if state.settings.semantic_backend == "local_embeddings"
            else "TF-IDF"
        )
        self.language_mode.set(state.settings.language_mode)
        self.file_summary.set(file_selection_summary(self.input_paths))
        self.codebook_summary.set(codebook_selection_summary(self.codebook_path, len(self.codebook_entries)))
        self._clear_similar_passages()
        self._set_editable_text_content(self.quote_memo_text, "")
        self._render_themes()
        self._render_transcript_tabs()
        if self.result is not None and self.result.themes:
            self._select_theme(0)
            status = analysis_status_text(
                document_count=self.result.document_count,
                quote_count=self.result.quote_count,
                theme_count=len(self.result.themes),
            )
            self.theme_summary.set("Manual coding | No automated analysis" if MANUAL_ONLY_NOTE in self.result.notes else status)
        else:
            self.current_theme_index = None
            self.current_quote_matches = []
            self.current_quote_index = -1
            self.theme_summary.set("No analysis yet")
            self.detail_title.set("Transcript evidence")
            self.quote_status.set(evidence_navigation_status(0, 0))
            self.quote_meta.set("Project opened. Analyze to highlight quote evidence.")
            self.quote_reason.set("Select a theme after analysis to review quote-selection rationale.")
            self._refresh_editor()
        for child in self.winfo_children():
            if isinstance(child, RigorDialog):
                child.destroy()  # The open comparison and audit belong to the outgoing study.
        self.researcher_name.set(state.researcher_name)
        self._saved_state = self._project_state() if path else self._empty_state
        self.status_text.set(f"Opened {path.name} using the project's saved transcripts and codebook."
                             if path else "New unsaved project")

    def _load_documents(self, paths: list[Path]) -> list[TranscriptDocument]:
        name_counts: dict[str, int] = {}
        documents: list[TranscriptDocument] = []
        for path in paths:
            base_name = path.name
            name_counts[base_name] = name_counts.get(base_name, 0) + 1
            source_name = base_name if name_counts[base_name] == 1 else f"{base_name} [{name_counts[base_name]}]"
            documents.append(TranscriptDocument(name=source_name, text=load_transcript_text(path)))
        return documents

    def _render_themes(self) -> None:
        self.theme_list.delete(0, tk.END)
        if self.result is None:
            self._refresh_editor()
            return
        if MANUAL_ONLY_NOTE in self.result.notes:
            self.theme_summary.set("Manual coding | No automated analysis")
        for theme in self.result.themes:
            label = theme_list_label(
                theme.name,
                theme.color,
                theme.quote_count,
                None if theme.reported_score is None else float(theme.validation.get("central_theme_alignment", 0)),
                depth=1 if theme.parent_theme_id else 0,
            )
            role = theme.validation.get("reanalysis_role")
            if role in ("Retained earlier edits", "Retained hierarchy parent", "Fresh codebook suggestion"):
                label = f"[{role.split()[0]}] {label}"
            self.theme_list.insert(tk.END, label)
            self.theme_list.itemconfig(tk.END, foreground=theme.color)
        self._apply_display_colors()
        self._refresh_editor()

    def _clear_theme_view(self, quote_meta: str) -> None:
        self.match_review_text.set("")
        self.current_theme_index = None
        self.current_quote_matches = []
        self.current_quote_index = -1
        self.theme_list.delete(0, tk.END)
        self._clear_similar_passages()
        self._clear_quote_tags()
        self._set_editable_text_content(self.quote_memo_text, "")
        self.detail_title.set("Transcript evidence")
        self.quote_status.set(evidence_navigation_status(0, 0))
        self.quote_meta.set(quote_meta)
        self.quote_reason.set("Select a coded passage to review its rationale.")
        self._refresh_editor()

    def _refresh_editor(self) -> None:
        theme = self._current_theme()
        if theme is None:
            self.theme_name.set("")
            self.theme_keywords.set("")
            self.parent_theme.set("")
            self.target_theme.set("")
            if hasattr(self, "theme_memo_text"):
                self._set_editable_text_content(self.theme_memo_text, "")
            if hasattr(self, "parent_theme_box"):
                self.parent_theme_box.configure(values=())
            if hasattr(self, "target_theme_box"):
                self.target_theme_box.configure(values=())
            return

        self.theme_name.set(theme.name)
        self.theme_keywords.set(", ".join(theme.keywords))
        if hasattr(self, "theme_memo_text"):
            self._set_editable_text_content(self.theme_memo_text, theme.memo)
        target_values = [
            f"{item.id}: {item.name}"
            for item in self.result.themes
            if item.id != theme.id
        ] if self.result is not None else []
        parent_values = ("Top-level", *target_values)
        if hasattr(self, "parent_theme_box"):
            self.parent_theme_box.configure(values=parent_values)
        parent_label = self._theme_label(theme.parent_theme_id)
        if theme.parent_theme_id and not parent_label:
            self.parent_theme.set(f"Unavailable parent ({theme.parent_theme_id})")
        else:
            self.parent_theme.set(parent_label if parent_label in parent_values else "Top-level")
        if hasattr(self, "target_theme_box"):
            self.target_theme_box.configure(values=tuple(target_values))
        if target_values and self.target_theme.get() not in target_values:
            self.target_theme.set(target_values[0])
        elif not target_values:
            self.target_theme.set("")

    def _current_theme(self) -> Theme | None:
        if self.result is None or self.current_theme_index is None:
            return None
        if self.current_theme_index >= len(self.result.themes):
            return None
        return self.result.themes[self.current_theme_index]

    def _current_quote_match(self) -> QuoteMatch | None:
        if not self.current_quote_matches or self.current_quote_index < 0:
            return None
        match = self.current_quote_matches[self.current_quote_index]
        return match if match.location.verified else None

    def _target_theme_id(self) -> str | None:
        value = self.target_theme.get()
        if ": " not in value:
            return None
        return value.split(": ", 1)[0]

    def _semantic_backend(self) -> str:
        if self.semantic_mode.get() == "Local embeddings":
            return "local_embeddings"
        return "tfidf"

    def _analysis_settings(self) -> AnalysisSettings:
        return AnalysisSettings(
            theme_count=max(1, int(self.theme_count.get())),
            quotes_per_theme=max(0, int(self.quotes_per_theme.get())),
            central_theme=self.central_theme.get().strip(),
            semantic_backend=self._semantic_backend(),
            language_mode=self.language_mode.get(),
            codebook_entries=self.codebook_entries,
            speaker_overrides=self.speaker_overrides,
        )

    def _selected_similar_passage(self) -> SimilarPassage | None:
        selection = self.similar_list.curselection()
        if not selection or selection[0] >= len(self.similar_passages):
            return None
        return self.similar_passages[selection[0]]

    def _clear_similar_passages(self) -> None:
        self.similar_passages = []
        if hasattr(self, "similar_list"):
            self.similar_list.delete(0, tk.END)
        self.similar_note.set(f"Find in {self.review_source_box.get()} · Choose the scope under Quote review → Next in."
                              if len(self.documents) > 1 else "Find related uncoded passages from the selected theme or quote.")
        for widget in self.transcript_widgets.values():
            widget.configure(state="normal")
            widget.tag_remove("similar_suggestion", "1.0", tk.END)
            widget.configure(state="disabled")

    def _parent_theme_id(self) -> str | None:
        value = self.parent_theme.get()
        if value == "Top-level":
            return ""
        if ": " not in value:
            return None
        return value.split(": ", 1)[0]

    def _theme_label(self, theme_id: str) -> str:
        if self.result is None or not theme_id:
            return ""
        theme = next((item for item in self.result.themes if item.id == theme_id), None)
        return f"{theme.id}: {theme.name}" if theme else ""

    def _selected_transcript_text(self) -> ManualSelection | None:
        source_name = self._current_document_name()
        widget = self.transcript_widgets.get(source_name)
        if widget is None:
            return None
        try:
            start = widget.index(tk.SEL_FIRST)
            raw = widget.get(start, tk.SEL_LAST)
        except tk.TclError:
            return None
        text = raw.strip()
        if not text:
            return None
        # Python source offsets, not Tcl UTF-16 counts. Trim both the text and its offsets.
        prefix = widget.get("1.0", start) + raw[:len(raw) - len(raw.lstrip())]
        return ManualSelection(source_name, text, prefix.count("\n") + 1, len(prefix), len(prefix) + len(text))

    def _focus_quote_by_id(self, quote_id: str) -> None:
        for index, match in enumerate(self.current_quote_matches):
            if match.quote.quote_id == quote_id:
                self.current_quote_index = index
                self._focus_current_quote()
                return

    def _select_theme_by_id(self, theme_id: str) -> None:
        if self.result is None:
            return
        for index, theme in enumerate(self.result.themes):
            if theme.id == theme_id:
                self._select_theme(index)
                return

    def _select_theme(self, index: int, *, quote_index: int = 0) -> None:
        self.theme_list.selection_clear(0, tk.END)
        self.theme_list.selection_set(index)
        self.show_theme(index, quote_index=quote_index)

    def _render_transcript_tabs(self) -> None:
        for tab_id in self.transcript_notebook.tabs():
            self.transcript_notebook.forget(tab_id)
        for child in self.transcript_notebook.winfo_children():
            child.destroy()

        self.transcript_widgets = {}
        self._display_sources = TranscriptIndex(self.documents)
        names = [doc.name for doc in self.documents]
        # Keep the whole-study choice distinct even for a transcript literally named All sources.
        labels = [f'“{name}”' for name in names] if "All sources" in names else names
        self.review_source_box.configure(values=("All sources", *labels))
        self.review_source_box.current(0)
        self._clear_similar_passages()
        if len(names) > 1:
            self.review_source_box.master.grid()
        else:
            self.review_source_box.master.grid_remove()
        self.transcript_frames = {}
        self.transcript_frame_sources = {}
        self.stripe_canvases = {}

        documents = self.documents or [TranscriptDocument(name="Transcript", text="Open one or more transcripts to view raw content here.")]
        for document in documents:
            frame = ttk.Frame(self.transcript_notebook, style="Surface.TFrame")
            frame.columnconfigure(1, weight=1)
            frame.rowconfigure(0, weight=1)

            stripes = tk.Canvas(
                frame,
                width=12,
                borderwidth=0,
                highlightthickness=0,
                background=self.palette["transcript_background"],
            )
            stripes.grid(row=0, column=0, sticky="ns")

            text = tk.Text(
                frame,
                wrap="word",
                padx=16,
                pady=14,
                undo=False,
                exportselection=False,
                borderwidth=0,
                highlightthickness=1,
                font=("Segoe UI", 10),
                relief="flat",
            )
            text.grid(row=0, column=1, sticky="nsew")
            self._set_text_content(text, document.text)

            scrollbar = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=text.yview)
            scrollbar.grid(row=0, column=2, sticky="ns")
            text.configure(yscrollcommand=self._scroll_command(document.name, scrollbar))

            self.transcript_notebook.add(frame, text=transcript_tab_label(document.name))
            self.transcript_widgets[document.name] = text
            self.transcript_frames[document.name] = frame
            self.transcript_frame_sources[str(frame)] = document.name
            self.stripe_canvases[document.name] = stripes

        self._apply_display_colors()
        if self.result is not None:
            self._highlight_all_quotes(self.current_theme_index)
        self._refresh_document_memo()

    def _on_transcript_tab_changed(self, _event: tk.Event | None = None) -> None:
        self._refresh_document_memo()

    def _current_document_name(self) -> str | None:
        selected = self.transcript_notebook.select()
        if not selected:
            return None
        return self.transcript_frame_sources.get(selected)

    def _refresh_document_memo(self) -> None:
        if not hasattr(self, "document_memo_text"):
            return
        source_name = self._current_document_name()
        document = next((item for item in self.documents if item.name == source_name), None)
        self._set_editable_text_content(self.document_memo_text, document.memo if document else "")

    def show_selected_theme(self, _event: tk.Event | None = None) -> None:
        selection = self.theme_list.curselection()
        if not selection:
            return
        self.show_theme(selection[0])

    def show_theme(self, index: int, *, quote_index: int = 0) -> None:
        if self.result is None or index >= len(self.result.themes):
            return

        self._clear_similar_passages()
        theme = self.result.themes[index]
        self.current_theme_index = index
        self.quote_meta.set(
            f"{format_validation_summary(theme.validation)} | Keywords: {', '.join(theme.keywords) if theme.keywords else 'None'}"
        )
        self.quote_reason.set("Use Previous quote or Next quote to inspect why each highlighted quote was selected.")
        self._highlight_all_quotes(index)
        self.current_quote_matches = self._quote_matches_for_theme(index)
        self.current_quote_index = quote_index if self.current_quote_matches else -1
        self._refresh_editor()
        self._focus_current_quote()

    def previous_quote(self) -> None:
        if not self.current_quote_matches:
            self.quote_status.set(evidence_navigation_status(0, 0))
            return
        self.current_quote_index = (max(0, self.current_quote_index) - 1) % len(self.current_quote_matches)
        self._focus_current_quote()

    def next_quote(self) -> None:
        if not self.current_quote_matches:
            self.quote_status.set(evidence_navigation_status(0, 0))
            return
        self.current_quote_index = (self.current_quote_index + 1) % len(self.current_quote_matches)
        self._focus_current_quote()

    def _reset_omission_position(self) -> None:
        if self.unsuggested_index >= 0 and self.current_quote_index < 0:
            self._focus_current_quote()
            self.detail_title.set("Transcript evidence")
            self.quote_status.set("")
            self.quote_meta.set("")
            self.quote_reason.set("Select a passage to read its context.")
        self.unsuggested_index = -1

    def _clear_suggestion_coverage(self) -> None:
        self._reset_omission_position()
        self.unsuggested_passages = None
        self.coverage_is_preview = False
        self.unsuggested_button.grid_remove()

    def _invalidate_coverage_preview(self, *_trace_args: str) -> None:
        if self.coverage_is_preview:
            self._clear_suggestion_coverage()
            self.status_text.set("Quote limit changed. Choose Preview coverage to update the count.")

    def _show_suggestion_coverage(self, eligible: list[QuoteUnit], themes: list[Theme], *, preview: bool) -> str:
        # Only count the matcher output, before any manual or retained themes are added.
        suggested = {q.quote_id for t in themes if "generated_from_codebook_entry" in t.validation for q in t.quotes}
        self.unsuggested_passages = [unit for unit in eligible if unit.id not in suggested]
        self.coverage_is_preview = preview
        if self.unsuggested_passages:
            self.unsuggested_button.grid()
        if self.review_source_box.current() > 0:
            return self._omission_count_text(len(self._scoped_omissions()))
        count = f"{len(suggested)}/{len(eligible)} eligible passages"
        if preview:
            return f"All sources · Preview on project text with current settings — {count} have suggestions."
        fallback = " Showing automatic themes." if not suggested and themes else ""
        return f"All sources · Suggestions on {count}.{fallback} Passages may need other codes."

    def preview_coverage(self) -> None:
        self._clear_suggestion_coverage()
        if not self.codebook_entries:
            self.status_text.set("Choose a codebook to preview suggestion coverage.")
            return
        try:
            self._check_coverage_sources()
            settings = self._analysis_settings()
            segments, eligible = suggestion_inputs(self.documents, settings)
            themes = build_codebook_themes(eligible, settings, speaker_stopwords(segments))
        except (ValueError, tk.TclError) as exc:
            self.status_text.set(f"Coverage preview unavailable. {exc}")
            return
        self.status_text.set(self._show_suggestion_coverage(eligible, themes, preview=True))

    def _check_coverage_sources(self) -> None:
        # Project snapshots are authoritative. Opening a project never follows its stored file paths.
        if (not self.documents or not self._display_sources.matches_documents(self.documents)
                or (self.input_paths and len(self.input_paths) != len(self.documents))
                or (self.result and self.result.document_count > len(self.documents))
                or any(not doc.name.strip() or not doc.text.strip() for doc in self.documents)):
            raise ValueError("Project transcript text is missing, changed or ambiguous.")
        if self.result and any(self._display_sources.locate(q).status == "conflict" or
                               not self._display_sources.has_unique_source(q.source_name)
                               for theme in self.result.themes for q in theme.quotes):
            raise ValueError("A saved passage conflicts with the project transcripts.")
        for event in self.audit_events:
            if event.action == REVIEW_ACTION and event.target_type == "theme_quote_match":
                try:
                    match = json.loads(event.details)["match"]
                    name, digest = match["quote"]["source_name"], match["source_sha256"]
                except (KeyError, TypeError, ValueError):
                    raise ValueError("A saved review's source cannot be verified.") from None
                if not isinstance(name, str) or not digest or self._display_sources.digest(name) != digest:
                    raise ValueError("Project transcript text differs from a saved review.")

    def _navigation_sources(self) -> set[str]:
        choice = self.review_source_box.current()
        if choice == 0:
            return {doc.name for doc in self.documents}
        if 0 < choice <= len(self.documents):
            return {self.documents[choice - 1].name}
        return set()

    def _scoped_omissions(self) -> list[QuoteUnit]:
        passages = self.unsuggested_passages or []
        if self.review_source_box.current() == 0:
            return passages
        sources = self._navigation_sources()
        return [unit for unit in passages if unit.source_name in sources]

    def _omission_count_text(self, count: int) -> str:
        origin = "Preview (current settings)" if self.coverage_is_preview else "Last Analyze"
        noun = "passage" if count == 1 else "passages"
        return f"{origin} · {self.review_source_box.get()} · {count} eligible {noun} without a suggestion."

    def _change_navigation_scope(self, _event: tk.Event) -> None:
        self._clear_similar_passages()
        if self.unsuggested_passages is not None:
            self._reset_omission_position()
            self.status_text.set(self._omission_count_text(len(self._scoped_omissions())))

    def next_unsuggested_passage(self) -> None:
        if not self.unsuggested_passages:
            return
        if self._has_pending_source_edits() or (self._current_theme() is None and self.theme_name.get().strip()):
            self.status_text.set("Apply unfinished theme or memo edits before browsing another passage.")
            return
        passages = self._scoped_omissions()
        count = len(passages)
        if not count:
            self.status_text.set(self._omission_count_text(0))
            return
        self.unsuggested_index = (self.unsuggested_index + 1) % count
        unit = passages[self.unsuggested_index]
        location = self._locate_quote(unit)
        if location is None or not self._display_sources.matches_documents(self.documents):
            self._clear_suggestion_coverage()
            self.status_text.set("Coverage unavailable. The passage no longer matches the project transcript.")
            return
        widget, start, end = location
        # The theme can still be a manual coding target. Match actions require a focused quote.
        self.current_quote_index = -1
        self._clear_similar_passages()
        self._focus_current_quote()
        self.quote_memo_text.configure(state="disabled")
        self.quote_meta.set(f"{unit.source_name}, line {unit.source_line} | {unit.speaker}")
        self.detail_title.set("No suggestion in preview" if self.coverage_is_preview else "No displayed codebook suggestion")
        self.quote_status.set(f"Passage {self.unsuggested_index + 1} of {count} · {self.review_source_box.get()}")
        scope = ("No codebook suggestion for this project text under current settings." if self.coverage_is_preview
                 else "No code was suggested for this passage in the last Analyze.")
        self.quote_reason.set(f"{scope} Coverage counts suggestions. Review decisions are separate. "
                              "A passage shown under one code may still need another.")
        self.transcript_notebook.select(self.transcript_frames[unit.source_name])
        widget.tag_remove(tk.SEL, "1.0", tk.END)
        widget.tag_add(tk.SEL, start, end)
        widget.tag_add("coverage_selection", start, end)
        widget.tag_raise(tk.SEL)
        widget.update_idletasks()
        widget.see(start)
        self.status_text.set("Use Apply codebook entry… to assign the selection, or a document memo to record uncertainty.")

    def next_review_match(self, disposition: str | None) -> None:
        reviewer = self.researcher_name.get().strip()
        if not reviewer:
            self.status_text.set("Enter your coder name to resume your match reviews.")
            return
        if not self.result or not self.result.themes:
            self.status_text.set("Open a study with saved matches or run Analyze to find suggestions.")
            return
        if self._has_pending_source_edits():
            self.status_text.set("Apply unfinished theme or memo edits before navigating to another review.")
            return
        sources = self._navigation_sources()
        if not sources:
            self.status_text.set("Choose a transcript or All sources before continuing.")
            return
        themes = self.result.themes
        start_theme = self.current_theme_index or 0
        after_quote = self.current_quote_index + 1
        # Continue in the displayed order, wrapping once through the current match.
        for offset in range(len(themes) + 1):
            index = (start_theme + offset) % len(themes)
            theme = themes[index]
            if not self.match_reviews.definition_is_current(theme):
                continue
            start = after_quote if offset == 0 else 0
            stop = after_quote if offset == len(themes) else len(theme.quotes)
            for quote_index in range(start, stop):
                quote = theme.quotes[quote_index]
                if quote.source_name not in sources:
                    continue
                if disposition is None and quote.reported_relevance is None:
                    continue  # A manual unscored assignment is already a coding decision.
                note = self.match_reviews.latest(theme, quote, self._display_sources, reviewer=reviewer)
                if (note.disposition if note else None) != disposition or not self._resolve_quote(quote).verified:
                    continue
                if index == self.current_theme_index:
                    self.current_quote_index = quote_index
                    self._focus_current_quote()
                else:
                    self._select_theme(index, quote_index=quote_index)
                    self.theme_list.see(index)
                self.status_text.set(f"{reviewer} · {disposition or 'unreviewed'} · {theme.name} · "
                                     f"{quote.source_name}, line {quote.source_line}")
                return
        scope = f" in {self.review_source_box.get()}" if self.review_source_box.current() > 0 else ""
        if disposition is not None:
            self.status_text.set(f"No verified {disposition} matches for {reviewer}{scope}.")
        else:
            self.status_text.set(f"No unreviewed scored matches with verified spans for {reviewer}{scope}. "
                                 "Use Next deferred to revisit a saved deferral.")

    def save_report(self) -> None:
        if self.result is None:
            messagebox.showinfo("Analyze first", "Run analysis before saving a report.")
            return

        path = filedialog.asksaveasfilename(
            title="Save report",
            defaultextension=".md",
            filetypes=[
                ("Markdown report", "*.md"),
                ("Quote table CSV", "*.csv"),
                ("JSON data", "*.json"),
            ],
        )
        if not path:
            return

        target = Path(path)
        suffix = target.suffix.lower()
        if suffix == ".csv":
            output = export_quotes_csv(self.result, self.documents, self.audit_events, codebook_entries=self.codebook_entries)
        elif suffix == ".json":
            output = export_json(self.result, self.documents, self.audit_events, codebook_entries=self.codebook_entries)
        else:
            output = export_markdown(self.result, self.documents, self.audit_events, codebook_entries=self.codebook_entries)

        target.write_text(output, encoding="utf-8", newline="")
        self.status_text.set(f"Saved {target.name}")

    def _rigor_context(self) -> RigorContext:
        return RigorContext(
            documents=tuple(self.documents),
            result=self.result,
            audit_events=tuple(self.audit_events),
            coder_name=self.researcher_name.get(),
            codebook_entries=self.codebook_entries,
        )

    def open_rigor_tools(self) -> None:
        if not self.documents or self.result is None:
            messagebox.showinfo("Rigor tools", "Open transcripts and run analysis before using rigor tools.")
            return
        context = self._rigor_context()
        for child in self.winfo_children():
            if isinstance(child, RigorDialog) and child.state() == "withdrawn":
                if child.context != context:
                    child.refresh_current_state()
                child.deiconify()
                child.source_matrix_tree.focus_set()
                return
        RigorDialog(self, context, self._rigor_context, self._locate_review_match)

    def _locate_review_match(self, theme: Theme, quote: ThemeQuote) -> None:
        if self._has_pending_source_edits() or (self._current_theme() is None and self.theme_name.get().strip()):
            raise ValueError("Apply unfinished theme or memo edits before locating a reviewed quote.")
        if (self.result is None or theme not in self.result.themes or quote not in theme.quotes
                or not self._resolve_quote(quote).verified):
            raise ValueError("The displayed study no longer verifies this match. Refresh Rigor tools and try again.")
        index = self.result.themes.index(theme)
        self._select_theme(index, quote_index=theme.quotes.index(quote))
        self.theme_list.see(index)
        self.quote_review_page.master.select(self.quote_review_page)

    def show_about(self) -> None:
        messagebox.showinfo("About ThemeForge", about_text(__version__))

    def change_display_mode(self, _event: tk.Event | None = None) -> None:
        self.palette = APP_THEMES.get(self.display_mode.get(), APP_THEMES["Light"])
        self._configure_styles()
        self._apply_display_colors()
        if self.result is not None:
            self._highlight_all_quotes(self.current_theme_index)
            if self.current_quote_index >= 0:
                self._focus_current_quote()

    def _set_text_content(self, widget: tk.Text, text: str) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", tk.END)
        widget.insert(tk.END, text)
        widget.configure(state="disabled")

    def _set_editable_text_content(self, widget: tk.Text, text: str) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", tk.END)
        widget.insert(tk.END, text)

    def _apply_display_colors(self) -> None:
        self._configure_treeview_styles(ttk.Style(self))
        if hasattr(self, "theme_list"):
            self.theme_list.configure(
                background=self.palette["surface"],
                foreground=self.palette["text"],
                highlightbackground=self.palette["border"],
                selectbackground=self.palette["surface_alt"],
                selectforeground=self.palette["text"],
            )
        if hasattr(self, "similar_list"):
            self.similar_list.configure(
                background=self.palette["surface"],
                foreground=self.palette["text"],
                highlightbackground=self.palette["border"],
                selectbackground=self.palette["surface_alt"],
                selectforeground=self.palette["text"],
            )
        for widget in self.transcript_widgets.values():
            widget.configure(
                background=self.palette["transcript_background"],
                foreground=self.palette["transcript_text"],
                insertbackground=self.palette["transcript_text"],
                highlightbackground=self.palette["border"],
            )
        for canvas in self.stripe_canvases.values():
            canvas.configure(background=self.palette["transcript_background"])

    def _configure_treeview_styles(self, style: ttk.Style) -> None:
        style.configure(
            "Treeview",
            background=self.palette["surface"],
            fieldbackground=self.palette["surface"],
            foreground=self.palette["text"],
            bordercolor=self.palette["border"],
            rowheight=24,
        )
        style.configure(
            "Treeview.Heading",
            background=self.palette["surface_alt"],
            foreground=self.palette["text"],
            bordercolor=self.palette["border"],
        )
        style.map(
            "Treeview",
            background=[("selected", self.palette["accent"])],
            foreground=[("selected", self.palette["background"])],
        )

    def _highlight_all_quotes(self, selected_theme_index: int | None) -> None:
        self._clear_quote_tags()
        if self.result is None:
            return

        for theme_index, theme in enumerate(self.result.themes):
            tag = self._theme_tag(theme)
            for quote_index, quote in enumerate(theme.quotes):
                location = self._locate_quote(quote)
                if location is None:
                    continue
                widget, start, end = location
                self._configure_theme_tag(widget, theme, selected_theme_index == theme_index)
                widget.configure(state="normal")
                widget.tag_add(tag, start, end)
                widget.configure(state="disabled")

        self._draw_coding_stripes()
        if selected_theme_index is not None and selected_theme_index < len(self.result.themes):
            selected_tag = self._theme_tag(self.result.themes[selected_theme_index])
            for widget in self.transcript_widgets.values():
                if selected_tag in widget.tag_names():
                    widget.tag_raise(selected_tag)

    def _clear_quote_tags(self) -> None:
        for widget in self.transcript_widgets.values():
            widget.configure(state="normal")
            for tag in widget.tag_names():
                if tag.startswith("theme_") or tag in {"selected_quote", "similar_suggestion"}:
                    widget.tag_delete(tag)
            widget.configure(state="disabled")
        for canvas in self.stripe_canvases.values():
            canvas.delete("stripe")

    def _configure_theme_tag(self, widget: tk.Text, theme: Theme, selected: bool) -> None:
        background_alpha = 0.30 if selected else 0.18
        widget.tag_configure(
            self._theme_tag(theme),
            background=_blend_hex(theme.color, self.palette["transcript_background"], background_alpha),
            foreground=self.palette["highlight_foreground"],
            font=("Segoe UI Semibold", 10) if selected else ("Segoe UI", 10),
        )
        widget.tag_configure(
            "selected_quote",
            background=self.palette["selected_quote"],
            foreground=self.palette["highlight_foreground"],
            font=("Segoe UI Semibold", 10),
            underline=1,
        )

    def _scroll_command(self, source_name: str, scrollbar: ttk.Scrollbar) -> Callable[[str, str], None]:
        def update_scrollbar(first: str, last: str) -> None:
            scrollbar.set(first, last)
            self._draw_coding_stripes_for_source(source_name)

        return update_scrollbar

    def _draw_coding_stripes(self) -> None:
        for source_name in self.transcript_widgets:
            self._draw_coding_stripes_for_source(source_name)

    def _draw_coding_stripes_for_source(self, source_name: str) -> None:
        canvas = self.stripe_canvases.get(source_name)
        widget = self.transcript_widgets.get(source_name)
        if canvas is None or widget is None or self.result is None:
            return
        canvas.delete("stripe")
        canvas_height = max(1, canvas.winfo_height())
        for theme in self.result.themes:
            for quote in theme.quotes:
                if quote.source_name != source_name:
                    continue
                location = self._locate_quote(quote)
                if location is None:
                    continue
                _widget, start, end = location
                start_line = int(widget.index(start).split(".", 1)[0])
                end_line = int(widget.index(end).split(".", 1)[0])
                for line in range(start_line, end_line + 1):
                    info = widget.dlineinfo(f"{line}.0")
                    if info is None:
                        continue
                    y = max(0, info[1])
                    height = max(2, info[3])
                    if y > canvas_height:
                        continue
                    canvas.create_rectangle(
                        2,
                        y,
                        10,
                        min(canvas_height, y + height),
                        fill=theme.color,
                        outline="",
                        tags="stripe",
                    )

    def _quote_matches_for_theme(self, theme_index: int) -> list[QuoteMatch]:
        if self.result is None or theme_index >= len(self.result.themes):
            return []

        theme = self.result.themes[theme_index]
        return [QuoteMatch(theme_index, i, q.source_name, self._resolve_quote(q), q) for i, q in enumerate(theme.quotes)]

    def _locate_quote(self, quote: ThemeQuote | QuoteUnit) -> tuple[tk.Text, str, str] | None:
        # Coding stripes, automatic highlights and suggestion actions need a recorded anchor.
        if quote.source_end <= quote.source_start:
            return None
        loc = self._resolve_quote(quote)
        return (self.transcript_widgets[quote.source_name], loc.start, loc.end) if loc.verified else None

    def _resolve_quote(self, quote: ThemeQuote | QuoteUnit) -> QuoteLocation:
        widget = self.transcript_widgets.get(quote.source_name)
        if widget is None:
            return QuoteLocation("No unique named source or nonempty recorded excerpt.")
        loc = self._display_sources.locate(quote)
        if loc.start is None or loc.end is None:
            return QuoteLocation(loc.note)
        a, b = f"1.0 + {loc.start} chars", f"1.0 + {loc.end} chars"
        if int(widget.index(a).split(".", 1)[0]) != quote.source_line or widget.get(a, b) != quote.text:
            return QuoteLocation("The recorded line or full excerpt does not match the source location.")
        return QuoteLocation(loc.note, a, b, loc.status == "exact_span")

    def _focus_current_quote(self) -> None:
        self.similar_list.selection_clear(0, tk.END)
        for widget in self.transcript_widgets.values():
            widget.configure(state="normal")
            coverage = widget.tag_ranges("coverage_selection")
            if coverage:
                if coverage == widget.tag_ranges(tk.SEL):
                    widget.tag_remove(tk.SEL, "1.0", tk.END)
                widget.tag_delete("coverage_selection")
            widget.tag_remove("selected_quote", "1.0", tk.END)
            widget.tag_remove("similar_suggestion", "1.0", tk.END)
            widget.configure(state="disabled")

        theme = self._current_theme()
        if theme:
            role = theme.validation.get("reanalysis_role")
            self.detail_title.set(f"{theme.name} | {role}" if role else theme.name)
        if not self.current_quote_matches or self.current_quote_index < 0:
            self.match_review_text.set("")
            self.quote_status.set(evidence_navigation_status(0, 0))
            if hasattr(self, "quote_memo_text"):
                self._set_editable_text_content(self.quote_memo_text, "")
            if self.current_theme_index is not None and self.result is not None:
                self.quote_meta.set("No exact quote text was found in the raw transcript tabs.")
                self.quote_reason.set("No quote rationale is available because the selected quote text was not found in the raw transcript.")
            return

        match = self.current_quote_matches[self.current_quote_index]
        theme = self.result.themes[match.theme_index] if self.result else None
        widget = self.transcript_widgets.get(match.source_name)
        frame = self.transcript_frames.get(match.source_name)
        if theme is None:
            return

        loc = match.location
        self._show_match_review()
        context_note = ""
        scroll_to = ""
        if widget is not None and frame is not None and loc.start:
            self.transcript_notebook.select(frame)
            widget.tag_configure("selected_quote", background=self.palette["selected_quote"],
                                 foreground=self.palette["highlight_foreground"], underline=not loc.verified)
            widget.tag_add("selected_quote", loc.start, loc.end)
            widget.tag_raise("selected_quote")
            scroll_to = loc.start
        elif (widget is not None and frame is not None and self._display_sources.has_unique_source(match.source_name)
              and 1 <= match.quote.source_line <= int(widget.index("end-1c").split(".", 1)[0])):
            self.transcript_notebook.select(frame)
            scroll_to = f"{match.quote.source_line}.0"
            context_note = " Showing the recorded line as context only."

        overlap_text = self._overlap_text(match.quote) if loc.verified else ""
        self.quote_status.set(evidence_navigation_status(self.current_quote_index, len(self.current_quote_matches)))
        self.quote_meta.set(
            (
                f"{theme.name} | Recorded speaker: {match.quote.speaker} | {match.quote.source_name}: "
                f"line {match.quote.source_line} | {match.quote.quote_id}"
            )
            + overlap_text + "\n" + loc.note + context_note + ("" if loc.verified else " Read-only record.")
        )
        excerpt = match.quote.text if len(match.quote.text) <= 300 else match.quote.text[:300] + "… (full text in project/report)"
        self.quote_reason.set(match.quote.rationale if loc.start else
                              f"No source highlight. Stored excerpt: {excerpt}\nRecorded rationale: {match.quote.rationale}")
        if hasattr(self, "quote_memo_text"):
            self._set_editable_text_content(self.quote_memo_text, match.quote.memo)
            if not loc.verified:
                self.quote_memo_text.configure(state="disabled")
        if widget is not None and scroll_to:
            # The tab and wrapped review text can change the transcript's visible height.
            widget.update_idletasks()
            widget.see(scroll_to)

    def _overlap_text(self, quote: ThemeQuote) -> str:
        if self.result is None:
            return ""
        theme_names = [
            theme.name
            for theme in self.result.themes
            if any((item.source_name, item.source_start, item.source_end, item.source_line, item.text) ==
                   (quote.source_name, quote.source_start, quote.source_end, quote.source_line, quote.text)
                   for item in theme.quotes)
        ]
        if len(theme_names) <= 1:
            return ""
        return f" | Also in: {', '.join(theme_names)}"

    def _theme_tag(self, theme: Theme) -> str:
        return f"theme_{theme.id}"


def _blend_hex(foreground: str, background: str, alpha: float) -> str:
    fg = _hex_to_rgb(foreground)
    bg = _hex_to_rgb(background)
    bounded_alpha = max(0.0, min(1.0, alpha))
    blended = tuple(round(fg[index] * bounded_alpha + bg[index] * (1.0 - bounded_alpha)) for index in range(3))
    return "#" + "".join(f"{value:02x}" for value in blended)


def _hex_to_rgb(color: str) -> tuple[int, int, int]:
    value = color.lstrip("#")
    if len(value) != 6:
        return (255, 255, 255)
    return (int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16))


def main() -> int:
    app = ThemeForgeApp()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

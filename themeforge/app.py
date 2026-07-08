from __future__ import annotations

# noqa: SIZE_OK - one Tkinter window class; splitting callbacks adds UI indirection.

from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import zipfile

from . import __version__
from .analysis import (
    AnalysisResult,
    AnalysisSettings,
    Theme,
    ThemeQuote,
    TranscriptDocument,
    analyze_documents,
)
from .codebook import load_codebook_entries
from .exports import export_json, export_markdown, export_quotes_csv
from .io import load_transcript_text
from .manual_editing import (
    EditHistory,
    ManualSelection,
    code_selected_text,
    merge_theme,
    preserve_manual_themes,
    reassign_quote,
    rename_theme,
    set_theme_parent,
    split_quote_to_theme,
    uncode_quote,
    update_document_memo,
    update_quote_memo,
    update_quote_boundary,
    update_theme_memo,
)
from .project_io import ProjectState, load_project, save_project
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
class QuoteMatch:
    theme_index: int
    quote_index: int
    source_name: str
    start: str
    end: str
    quote: ThemeQuote


class ThemeForgeApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"ThemeForge {__version__}")
        self.minsize(1180, 760)

        self.display_mode = tk.StringVar(value="Light")
        self.palette = APP_THEMES["Light"]
        self.input_paths: list[Path] = []
        self.project_path: Path | None = None
        self.codebook_path: Path | None = None
        self.codebook_entries = ()
        self.documents: list[TranscriptDocument] = []
        self.result: AnalysisResult | None = None
        self.edit_history = EditHistory()
        self.transcript_widgets: dict[str, tk.Text] = {}
        self.transcript_frames: dict[str, ttk.Frame] = {}
        self.transcript_frame_sources: dict[str, str] = {}
        self.stripe_canvases: dict[str, tk.Canvas] = {}
        self.current_theme_index: int | None = None
        self.current_quote_matches: list[QuoteMatch] = []
        self.current_quote_index = -1

        self.file_summary = tk.StringVar(value=file_selection_summary([]))
        self.codebook_summary = tk.StringVar(value=codebook_selection_summary(None))
        self.status_text = tk.StringVar(value="Ready")
        self.detail_title = tk.StringVar(value="Transcript evidence")
        self.quote_status = tk.StringVar(value=evidence_navigation_status(0, 0))
        self.quote_meta = tk.StringVar(value="Open transcripts, then analyze to highlight quote evidence.")
        self.quote_reason = tk.StringVar(
            value="Select a theme, then use Previous quote or Next quote to review why each highlighted quote was selected."
        )
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
        ttk.Button(header_actions, text="About", style="Secondary.TButton", command=self.show_about).grid(row=0, column=4)

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
        evidence.rowconfigure(6, weight=1)
        self._build_evidence_panel(evidence)

        footer = ttk.Frame(self, style="Footer.TFrame", padding=(20, 0, 20, 14))
        footer.grid(row=2, column=0, sticky="ew")
        footer.columnconfigure(0, weight=1)
        ttk.Label(footer, textvariable=self.status_text, style="Footer.TLabel").grid(row=0, column=0, sticky="w")

    def _build_controls(self, parent: ttk.Frame) -> None:
        ttk.Label(parent, text="Transcripts", style="Panel.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(parent, textvariable=self.file_summary, style="Muted.TLabel", wraplength=220).grid(row=1, column=0, sticky="ew", pady=(4, 14))

        file_buttons = ttk.Frame(parent, style="Surface.TFrame")
        file_buttons.grid(row=2, column=0, sticky="ew")
        file_buttons.columnconfigure(0, weight=1)
        file_buttons.columnconfigure(1, weight=1)
        ttk.Button(file_buttons, text="Choose files", style="Secondary.TButton", command=self.open_transcript).grid(row=0, column=0, sticky="ew", padx=(0, 6))
        ttk.Button(file_buttons, text="Remove files", style="Secondary.TButton", command=self.remove_transcripts).grid(row=0, column=1, sticky="ew")

        ttk.Separator(parent).grid(row=3, column=0, sticky="ew", pady=16)

        ttk.Label(parent, text="Codebook", style="Panel.TLabel").grid(row=4, column=0, sticky="w")
        ttk.Label(parent, textvariable=self.codebook_summary, style="Muted.TLabel", wraplength=220).grid(row=5, column=0, sticky="ew", pady=(4, 8))
        codebook_buttons = ttk.Frame(parent, style="Surface.TFrame")
        codebook_buttons.grid(row=6, column=0, sticky="ew")
        codebook_buttons.columnconfigure(0, weight=1)
        codebook_buttons.columnconfigure(1, weight=1)
        ttk.Button(codebook_buttons, text="Choose codebook", style="Secondary.TButton", command=self.open_codebook).grid(row=0, column=0, sticky="ew", padx=(0, 6))
        ttk.Button(codebook_buttons, text="Remove", style="Secondary.TButton", command=self.remove_codebook).grid(row=0, column=1, sticky="ew")

        ttk.Separator(parent).grid(row=7, column=0, sticky="ew", pady=16)

        ttk.Label(parent, text="Shared focus topic", style="Panel.TLabel").grid(row=8, column=0, sticky="w")
        ttk.Entry(parent, textvariable=self.central_theme).grid(row=9, column=0, sticky="ew", pady=(4, 14))

        option_row = ttk.Frame(parent, style="Surface.TFrame")
        option_row.grid(row=10, column=0, sticky="ew", pady=(0, 14))
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
        ).grid(row=1, column=0, sticky="ew", pady=(4, 0))
        ttk.Combobox(
            option_row,
            textvariable=self.language_mode,
            values=("Auto", "English", "Korean", "Multilingual"),
            state="readonly",
            width=14,
        ).grid(row=1, column=1, sticky="ew", padx=(10, 0), pady=(4, 0))

        count_row = ttk.Frame(parent, style="Surface.TFrame")
        count_row.grid(row=11, column=0, sticky="ew")
        count_row.columnconfigure(0, weight=1)
        count_row.columnconfigure(1, weight=1)

        ttk.Label(count_row, text="Themes", style="Panel.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(count_row, text="Quotes", style="Panel.TLabel").grid(row=0, column=1, sticky="w", padx=(10, 0))
        ttk.Spinbox(count_row, from_=1, to=20, width=6, textvariable=self.theme_count).grid(row=1, column=0, sticky="ew", pady=(4, 0))
        ttk.Spinbox(count_row, from_=0, to=200, width=6, textvariable=self.quotes_per_theme).grid(row=1, column=1, sticky="ew", padx=(10, 0), pady=(4, 0))

        ttk.Button(parent, text="Analyze", style="Primary.TButton", command=self.analyze).grid(row=12, column=0, sticky="ew", pady=(18, 8))
        ttk.Button(parent, text="Export report", style="Secondary.TButton", command=self.save_report).grid(row=13, column=0, sticky="ew")

        ttk.Label(
            parent,
            text="Suggestions need researcher review before reporting.",
            style="Muted.TLabel",
            wraplength=220,
        ).grid(row=14, column=0, sticky="ew", pady=(18, 0))

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
        ttk.Label(editor, text="Keywords", style="Panel.TLabel").grid(row=2, column=0, sticky="w")
        ttk.Entry(editor, textvariable=self.theme_keywords).grid(row=3, column=0, sticky="ew", pady=(4, 8))
        ttk.Button(editor, text="Save theme edits", style="Secondary.TButton", command=self.save_theme_edits).grid(row=4, column=0, sticky="ew")
        ttk.Label(editor, text="Theme memo", style="Panel.TLabel").grid(row=5, column=0, sticky="w", pady=(10, 0))
        self.theme_memo_text = tk.Text(editor, height=3, wrap="word", font=("Segoe UI", 9), borderwidth=1, relief="solid")
        self.theme_memo_text.grid(row=6, column=0, sticky="ew", pady=(4, 8))
        ttk.Button(editor, text="Save theme memo", style="Secondary.TButton", command=self.save_theme_memo).grid(row=7, column=0, sticky="ew")

        ttk.Label(editor, text="Parent theme", style="Panel.TLabel").grid(row=8, column=0, sticky="w", pady=(10, 0))
        self.parent_theme_box = ttk.Combobox(editor, textvariable=self.parent_theme, state="readonly")
        self.parent_theme_box.grid(row=9, column=0, sticky="ew", pady=(4, 8))
        ttk.Button(
            editor,
            text="Save hierarchy",
            style="Secondary.TButton",
            command=self.save_theme_hierarchy,
        ).grid(row=10, column=0, sticky="ew")

        ttk.Label(editor, text="Target theme", style="Panel.TLabel").grid(row=11, column=0, sticky="w", pady=(10, 0))
        self.target_theme_box = ttk.Combobox(editor, textvariable=self.target_theme, state="readonly")
        self.target_theme_box.grid(row=12, column=0, sticky="ew", pady=(4, 8))
        action_row = ttk.Frame(editor, style="Surface.TFrame")
        action_row.grid(row=13, column=0, sticky="ew")
        action_row.columnconfigure(0, weight=1)
        action_row.columnconfigure(1, weight=1)
        ttk.Button(action_row, text="Move quote", style="Secondary.TButton", command=self.move_current_quote).grid(row=0, column=0, sticky="ew", padx=(0, 6))
        ttk.Button(action_row, text="Merge theme", style="Secondary.TButton", command=self.merge_current_theme).grid(row=0, column=1, sticky="ew")
        ttk.Button(editor, text="Split quote to new theme", style="Secondary.TButton", command=self.split_current_quote).grid(row=14, column=0, sticky="ew", pady=(8, 0))
        coding_row = ttk.Frame(editor, style="Surface.TFrame")
        coding_row.grid(row=15, column=0, sticky="ew", pady=(8, 0))
        coding_row.columnconfigure(0, weight=1)
        coding_row.columnconfigure(1, weight=1)
        ttk.Button(coding_row, text="Code selection", style="Secondary.TButton", command=self.code_selection_to_theme).grid(row=0, column=0, sticky="ew", padx=(0, 6))
        ttk.Button(coding_row, text="New code from selection", style="Secondary.TButton", command=self.code_selection_to_new_theme).grid(row=0, column=1, sticky="ew")
        history_row = ttk.Frame(editor, style="Surface.TFrame")
        history_row.grid(row=16, column=0, sticky="ew", pady=(8, 0))
        history_row.columnconfigure(0, weight=1)
        history_row.columnconfigure(1, weight=1)
        ttk.Button(history_row, text="Undo", style="Secondary.TButton", command=self.undo_manual_edit).grid(row=0, column=0, sticky="ew", padx=(0, 6))
        ttk.Button(history_row, text="Redo", style="Secondary.TButton", command=self.redo_manual_edit).grid(row=0, column=1, sticky="ew")

    def _build_evidence_panel(self, parent: ttk.Frame) -> None:
        ttk.Label(parent, textvariable=self.detail_title, style="Panel.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 8))

        navigation = ttk.Frame(parent, style="Surface.TFrame")
        navigation.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        navigation.columnconfigure(2, weight=1)
        ttk.Button(navigation, text="Previous quote", style="Secondary.TButton", command=self.previous_quote).grid(row=0, column=0, sticky="w", padx=(0, 8))
        ttk.Button(navigation, text="Next quote", style="Secondary.TButton", command=self.next_quote).grid(row=0, column=1, sticky="w", padx=(0, 10))
        ttk.Button(navigation, text="Uncode quote", style="Secondary.TButton", command=self.uncode_current_quote).grid(row=0, column=2, sticky="w", padx=(0, 10))
        ttk.Button(navigation, text="Update boundary", style="Secondary.TButton", command=self.update_current_quote_boundary).grid(row=0, column=3, sticky="w", padx=(0, 10))
        ttk.Label(navigation, textvariable=self.quote_status, style="Muted.TLabel").grid(row=0, column=4, sticky="w")

        ttk.Label(parent, textvariable=self.quote_meta, style="Muted.TLabel", wraplength=520).grid(row=2, column=0, sticky="ew", pady=(0, 8))

        reason_frame = ttk.Labelframe(parent, text="Quote rationale", style="Panel.TLabelframe", padding=(10, 8))
        reason_frame.grid(row=3, column=0, sticky="ew", pady=(0, 8))
        reason_frame.columnconfigure(0, weight=1)
        ttk.Label(reason_frame, textvariable=self.quote_reason, style="Muted.TLabel", wraplength=520).grid(row=0, column=0, sticky="ew")

        quote_memo = ttk.Labelframe(parent, text="Quote memo", style="Panel.TLabelframe", padding=(10, 8))
        quote_memo.grid(row=4, column=0, sticky="ew", pady=(0, 8))
        quote_memo.columnconfigure(0, weight=1)
        self.quote_memo_text = tk.Text(quote_memo, height=3, wrap="word", font=("Segoe UI", 9), borderwidth=1, relief="solid")
        self.quote_memo_text.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ttk.Button(quote_memo, text="Save quote memo", style="Secondary.TButton", command=self.save_quote_memo).grid(row=1, column=0, sticky="ew")

        document_memo = ttk.Labelframe(parent, text="Document memo", style="Panel.TLabelframe", padding=(10, 8))
        document_memo.grid(row=5, column=0, sticky="ew", pady=(0, 8))
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

        self.transcript_notebook = ttk.Notebook(parent)
        self.transcript_notebook.grid(row=6, column=0, sticky="nsew")
        self.transcript_notebook.bind("<<NotebookTabChanged>>", self._on_transcript_tab_changed)

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
            self.input_paths = [Path(path) for path in paths]
            self.documents = self._load_documents(self.input_paths)
        except (OSError, ValueError, zipfile.BadZipFile) as exc:
            messagebox.showerror("Open transcripts failed", str(exc))
            return

        self._reset_analysis_view("Analyze to highlight quote evidence across the uploaded transcripts.")
        self.file_summary.set(file_selection_summary(self.input_paths))
        self._render_transcript_tabs()
        self.status_text.set("Files loaded")

    def remove_transcripts(self) -> None:
        self.input_paths = []
        self.documents = []
        self._reset_analysis_view("Open transcripts, then analyze to highlight quote evidence.")
        self.file_summary.set(file_selection_summary([]))
        self._render_transcript_tabs()
        self.status_text.set("Transcript files removed")

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
            self.codebook_path = Path(path)
            self.codebook_entries = load_codebook_entries(self.codebook_path)
        except (OSError, ValueError, zipfile.BadZipFile) as exc:
            messagebox.showerror("Open codebook failed", str(exc))
            return

        self.codebook_summary.set(codebook_selection_summary(self.codebook_path, len(self.codebook_entries)))
        self.status_text.set("Codebook loaded")

    def remove_codebook(self) -> None:
        self.codebook_path = None
        self.codebook_entries = ()
        self.codebook_summary.set(codebook_selection_summary(None))
        self.status_text.set("Codebook removed")

    def analyze(self) -> None:
        if not self.documents:
            messagebox.showinfo("Open transcripts", "Choose one or more transcript files first.")
            return

        try:
            settings = AnalysisSettings(
                theme_count=max(1, int(self.theme_count.get())),
                quotes_per_theme=max(0, int(self.quotes_per_theme.get())),
                central_theme=self.central_theme.get().strip(),
                codebook_entries=self.codebook_entries,
            )
            previous_result = self.result
            self.result = preserve_manual_themes(previous_result, analyze_documents(self.documents, settings))
            self.edit_history = EditHistory()
        except (tk.TclError, ValueError) as exc:
            messagebox.showerror("Analysis failed", str(exc))
            return

        if self.semantic_mode.get() == "Local embeddings":
            self.result.notes.append("Local embedding workflow selected; this preview keeps TF-IDF until the optional offline model package is installed.")
        if self.language_mode.get() != "Auto":
            self.result.notes.append(f"Language review mode selected: {self.language_mode.get()}.")

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
        self.status_text.set(f"{status}; {autosave_status}" if autosave_status else status)

    def save_theme_edits(self) -> None:
        if self.result is None or self.current_theme_index is None:
            return
        themes = list(self.result.themes)
        themes[self.current_theme_index] = rename_theme(
            themes[self.current_theme_index],
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
        self._set_manual_status("Theme edits saved")

    def save_theme_memo(self) -> None:
        theme = self._current_theme()
        if self.result is None or theme is None:
            return
        updated = update_theme_memo(self.result, theme.id, self.theme_memo_text.get("1.0", tk.END))
        if updated == self.result:
            return
        self.edit_history = self.edit_history.record(self.result)
        self.result = updated
        self._render_themes()
        self._select_theme_by_id(theme.id)
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
        self._set_manual_status("Theme hierarchy saved")

    def save_quote_memo(self) -> None:
        if self.result is None or self.current_theme_index is None:
            return
        match = self._current_quote_match()
        if match is None:
            self.status_text.set("No quote selected for memo")
            return
        theme_id = self.result.themes[self.current_theme_index].id
        updated = update_quote_memo(self.result, theme_id, match.quote.quote_id, self.quote_memo_text.get("1.0", tk.END))
        if updated == self.result:
            return
        self.edit_history = self.edit_history.record(self.result)
        self.result = updated
        self._render_themes()
        self._select_theme(self.current_theme_index)
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
        self._set_manual_status("Document memo saved")

    def move_current_quote(self) -> None:
        if self.result is None or self.current_theme_index is None:
            return
        match = self._current_quote_match()
        target_id = self._target_theme_id()
        if match is None or target_id is None:
            return
        source_id = self.result.themes[self.current_theme_index].id
        updated = reassign_quote(self.result, source_id, match.quote.quote_id, target_id)
        if updated == self.result:
            return
        self.edit_history = self.edit_history.record(self.result)
        self.result = updated
        self._render_themes()
        self._select_theme_by_id(target_id)
        self._set_manual_status("Quote moved")

    def merge_current_theme(self) -> None:
        if self.result is None or self.current_theme_index is None:
            return
        target_id = self._target_theme_id()
        if target_id is None:
            return
        source_id = self.result.themes[self.current_theme_index].id
        updated = merge_theme(self.result, target_id, source_id)
        if updated == self.result:
            return
        self.edit_history = self.edit_history.record(self.result)
        self.result = updated
        self._render_themes()
        self._select_theme_by_id(target_id)
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
        self._set_manual_status("Quote split into a new theme")

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
        if self.result is None:
            return
        selection = self._selected_transcript_text()
        if selection is None:
            self.status_text.set("Select transcript text first")
            return
        self._apply_selection_coding(selection, "", self.theme_name.get())

    def _apply_selection_coding(self, selection: ManualSelection, target_theme_id: str, new_theme_name: str) -> None:
        if self.result is None:
            return
        updated = code_selected_text(
            self.result,
            target_theme_id,
            selection,
            new_theme_name,
        )
        if updated == self.result:
            return
        self.edit_history = self.edit_history.record(self.result)
        self.result = updated
        self._render_themes()
        if target_theme_id:
            self._select_theme_by_id(target_theme_id)
        else:
            self._select_theme(len(self.result.themes) - 1)
        self._set_manual_status("Selected text coded")

    def uncode_current_quote(self) -> None:
        if self.result is None or self.current_theme_index is None:
            return
        match = self._current_quote_match()
        if match is None:
            self.status_text.set("No quote selected to uncode")
            return
        theme_id = self.result.themes[self.current_theme_index].id
        updated = uncode_quote(self.result, theme_id, match.quote.quote_id)
        if updated == self.result:
            return
        self.edit_history = self.edit_history.record(self.result)
        self.result = updated
        self._render_themes()
        self._select_theme(min(self.current_theme_index, len(self.result.themes) - 1))
        self._set_manual_status("Quote uncoded")

    def update_current_quote_boundary(self) -> None:
        if self.result is None or self.current_theme_index is None:
            return
        match = self._current_quote_match()
        selection = self._selected_transcript_text()
        if match is None or selection is None:
            self.status_text.set("Select a quote and transcript text first")
            return
        theme_id = self.result.themes[self.current_theme_index].id
        updated = update_quote_boundary(self.result, theme_id, match.quote.quote_id, selection)
        if updated == self.result:
            return
        self.edit_history = self.edit_history.record(self.result)
        self.result = updated
        self._render_themes()
        self._select_theme(self.current_theme_index)
        self._set_manual_status("Quote boundary updated")

    def undo_manual_edit(self) -> None:
        if self.result is None:
            return
        change = self.edit_history.undo(self.result)
        if change is None:
            self.status_text.set("No manual edit to undo")
            return
        self.result = change.result
        self.edit_history = change.history
        self._render_themes()
        if self.result.themes:
            self._select_theme(min(self.current_theme_index or 0, len(self.result.themes) - 1))
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
        try:
            self._restore_project(load_project(Path(path)), Path(path))
        except (OSError, ValueError, KeyError, zipfile.BadZipFile) as exc:
            messagebox.showerror("Open project failed", str(exc))

    def save_project(self) -> None:
        path = self.project_path
        if path is None:
            chosen = filedialog.asksaveasfilename(
                title="Save ThemeForge project",
                defaultextension=".tfproj",
                filetypes=[
                    ("ThemeForge project", "*.tfproj"),
                    ("All files", "*.*"),
                ],
            )
            if not chosen:
                return
            path = Path(chosen)
        try:
            save_project(path, self._project_state())
        except OSError as exc:
            messagebox.showerror("Save project failed", str(exc))
            return
        self.project_path = path
        self.status_text.set(f"Saved project {path.name}")

    def _set_manual_status(self, action: str) -> None:
        autosave_status = self._autosave_project()
        if autosave_status:
            self.status_text.set(f"{action}; {autosave_status}")
            return
        self.status_text.set(action)

    def _autosave_project(self) -> str | None:
        if self.project_path is None:
            return None
        try:
            save_project(self.project_path, self._project_state())
        except OSError as exc:
            return f"autosave failed: {exc}"
        return f"autosaved {self.project_path.name}"

    def _project_state(self) -> ProjectState:
        return ProjectState(
            transcript_paths=tuple(self.input_paths),
            codebook_path=self.codebook_path,
            documents=tuple(self.documents),
            settings=AnalysisSettings(
                theme_count=max(1, int(self.theme_count.get())),
                quotes_per_theme=max(0, int(self.quotes_per_theme.get())),
                central_theme=self.central_theme.get().strip(),
                codebook_entries=self.codebook_entries,
            ),
            result=self.result,
        )

    def _restore_project(self, state: ProjectState, path: Path) -> None:
        self.project_path = path
        self.input_paths = list(state.transcript_paths)
        self.codebook_path = state.codebook_path
        self.codebook_entries = state.settings.codebook_entries
        self.documents = list(state.documents)
        self.result = state.result
        self.edit_history = EditHistory()
        self.theme_count.set(state.settings.theme_count)
        self.quotes_per_theme.set(state.settings.quotes_per_theme)
        self.central_theme.set(state.settings.central_theme)
        self.file_summary.set(file_selection_summary(self.input_paths))
        self.codebook_summary.set(codebook_selection_summary(self.codebook_path, len(self.codebook_entries)))
        self._render_themes()
        self._render_transcript_tabs()
        if self.result is not None and self.result.themes:
            self._select_theme(0)
            status = analysis_status_text(
                document_count=self.result.document_count,
                quote_count=self.result.quote_count,
                theme_count=len(self.result.themes),
            )
            self.theme_summary.set(status)
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
        self.status_text.set(f"Opened project {path.name}")

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
        for theme in self.result.themes:
            label = theme_list_label(
                theme.name,
                theme.color,
                theme.quote_count,
                float(theme.validation.get("central_theme_alignment", 0)),
                depth=1 if theme.parent_theme_id else 0,
            )
            self.theme_list.insert(tk.END, label)
            self.theme_list.itemconfig(tk.END, foreground=theme.color)
        self._apply_display_colors()
        self._refresh_editor()

    def _reset_analysis_view(self, quote_meta: str) -> None:
        self.result = None
        self.edit_history = EditHistory()
        self.current_theme_index = None
        self.current_quote_matches = []
        self.current_quote_index = -1
        self.theme_list.delete(0, tk.END)
        self.theme_summary.set("No analysis yet")
        self.detail_title.set("Transcript evidence")
        self.quote_status.set(evidence_navigation_status(0, 0))
        self.quote_meta.set(quote_meta)
        self.quote_reason.set("Select a theme after analysis to review quote-selection rationale.")
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
        return self.current_quote_matches[self.current_quote_index]

    def _target_theme_id(self) -> str | None:
        value = self.target_theme.get()
        if ": " not in value:
            return None
        return value.split(": ", 1)[0]

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
        for source_name, widget in self.transcript_widgets.items():
            try:
                start = widget.index(tk.SEL_FIRST)
                end = widget.index(tk.SEL_LAST)
            except tk.TclError:
                continue
            text = widget.get(start, end).strip()
            if not text:
                return None
            return ManualSelection(
                source_name=source_name,
                text=text,
                source_line=int(start.split(".", 1)[0]),
                source_start=int(widget.count("1.0", start, "chars")[0]),
                source_end=int(widget.count("1.0", end, "chars")[0]),
            )
        return None

    def _select_theme_by_id(self, theme_id: str) -> None:
        if self.result is None:
            return
        for index, theme in enumerate(self.result.themes):
            if theme.id == theme_id:
                self._select_theme(index)
                return

    def _select_theme(self, index: int) -> None:
        self.theme_list.selection_clear(0, tk.END)
        self.theme_list.selection_set(index)
        self.show_theme(index)

    def _render_transcript_tabs(self) -> None:
        for tab_id in self.transcript_notebook.tabs():
            self.transcript_notebook.forget(tab_id)
        for child in self.transcript_notebook.winfo_children():
            child.destroy()

        self.transcript_widgets = {}
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

    def show_theme(self, index: int) -> None:
        if self.result is None or index >= len(self.result.themes):
            return

        theme = self.result.themes[index]
        self.current_theme_index = index
        self.detail_title.set(theme.name)
        self.quote_meta.set(
            f"{format_validation_summary(theme.validation)} | Keywords: {', '.join(theme.keywords) if theme.keywords else 'None'}"
        )
        self.quote_reason.set("Use Previous quote or Next quote to inspect why each highlighted quote was selected.")
        self._highlight_all_quotes(index)
        self.current_quote_matches = self._quote_matches_for_theme(index)
        self.current_quote_index = 0 if self.current_quote_matches else -1
        self._refresh_editor()
        self._focus_current_quote()

    def previous_quote(self) -> None:
        if not self.current_quote_matches:
            self.quote_status.set(evidence_navigation_status(0, 0))
            return
        self.current_quote_index = (self.current_quote_index - 1) % len(self.current_quote_matches)
        self._focus_current_quote()

    def next_quote(self) -> None:
        if not self.current_quote_matches:
            self.quote_status.set(evidence_navigation_status(0, 0))
            return
        self.current_quote_index = (self.current_quote_index + 1) % len(self.current_quote_matches)
        self._focus_current_quote()

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
            output = export_quotes_csv(self.result)
        elif suffix == ".json":
            output = export_json(self.result)
        else:
            output = export_markdown(self.result)

        target.write_text(output, encoding="utf-8")
        self.status_text.set(f"Saved {target.name}")

    def show_about(self) -> None:
        messagebox.showinfo("About ThemeForge", about_text(__version__))

    def change_display_mode(self, _event: tk.Event | None = None) -> None:
        self.palette = APP_THEMES.get(self.display_mode.get(), APP_THEMES["Light"])
        self._configure_styles()
        self._apply_display_colors()
        if self.result is not None:
            self._highlight_all_quotes(self.current_theme_index)
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
        if hasattr(self, "theme_list"):
            self.theme_list.configure(
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
                if tag.startswith("theme_") or tag == "selected_quote":
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

        matches: list[QuoteMatch] = []
        theme = self.result.themes[theme_index]
        for quote_index, quote in enumerate(theme.quotes):
            location = self._locate_quote(quote)
            if location is None:
                continue
            _widget, start, end = location
            matches.append(
                QuoteMatch(
                    theme_index=theme_index,
                    quote_index=quote_index,
                    source_name=quote.source_name,
                    start=start,
                    end=end,
                    quote=quote,
                )
            )
        return matches

    def _locate_quote(self, quote: ThemeQuote) -> tuple[tk.Text, str, str] | None:
        widget = self.transcript_widgets.get(quote.source_name)
        if widget is None:
            return None

        offset_start = f"1.0 + {quote.source_start} chars"
        if quote.source_end > quote.source_start:
            offset_end = f"1.0 + {quote.source_end} chars"
            if widget.get(offset_start, offset_end) == quote.text:
                return widget, offset_start, offset_end

        starts = [offset_start, f"{max(1, quote.source_line)}.0", "1.0"]
        snippets = [quote.text]
        if len(quote.text) > 80:
            snippets.append(quote.text[:80].strip())

        for snippet in snippets:
            if not snippet:
                continue
            for start_at in starts:
                start = widget.search(snippet, start_at, stopindex=tk.END)
                if start:
                    end = f"{start}+{len(snippet)}c"
                    return widget, start, end
        return None

    def _focus_current_quote(self) -> None:
        for widget in self.transcript_widgets.values():
            widget.configure(state="normal")
            widget.tag_remove("selected_quote", "1.0", tk.END)
            widget.configure(state="disabled")

        if not self.current_quote_matches or self.current_quote_index < 0:
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
        if widget is None or frame is None or theme is None:
            return

        widget.configure(state="normal")
        widget.tag_add("selected_quote", match.start, match.end)
        widget.tag_raise("selected_quote")
        widget.see(match.start)
        widget.configure(state="disabled")
        self.transcript_notebook.select(frame)

        overlap_text = self._overlap_text(match.quote)
        self.quote_status.set(evidence_navigation_status(self.current_quote_index, len(self.current_quote_matches)))
        self.quote_meta.set(
            (
                f"{theme.name} | {match.quote.speaker} | {match.quote.source_name}: "
                f"line {match.quote.source_line} | {match.quote.quote_id}"
            )
            + overlap_text
        )
        self.quote_reason.set(match.quote.rationale)
        if hasattr(self, "quote_memo_text"):
            self._set_editable_text_content(self.quote_memo_text, match.quote.memo)

    def _overlap_text(self, quote: ThemeQuote) -> str:
        if self.result is None:
            return ""
        theme_names = [
            theme.name
            for theme in self.result.themes
            if any(item.quote_id == quote.quote_id for item in theme.quotes)
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

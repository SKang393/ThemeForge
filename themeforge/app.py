from __future__ import annotations

# noqa: SIZE_OK - one Tkinter window class; splitting callbacks adds UI indirection.

from dataclasses import dataclass
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
        self.codebook_path: Path | None = None
        self.codebook_entries = ()
        self.documents: list[TranscriptDocument] = []
        self.result: AnalysisResult | None = None
        self.transcript_widgets: dict[str, tk.Text] = {}
        self.transcript_frames: dict[str, ttk.Frame] = {}
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
        ttk.Button(header_actions, text="About", style="Secondary.TButton", command=self.show_about).grid(row=0, column=2)

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
        evidence.rowconfigure(4, weight=1)
        self._build_evidence_panel(evidence)

        footer = ttk.Frame(self, style="Footer.TFrame", padding=(20, 0, 20, 14))
        footer.grid(row=2, column=0, sticky="ew")
        footer.columnconfigure(0, weight=1)
        ttk.Label(footer, textvariable=self.status_text, style="Footer.TLabel").grid(row=0, column=0, sticky="w")

    def _build_controls(self, parent: ttk.Frame) -> None:
        ttk.Label(parent, text="Transcripts", style="Panel.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(parent, textvariable=self.file_summary, style="Muted.TLabel", wraplength=220).grid(row=1, column=0, sticky="ew", pady=(4, 14))

        ttk.Button(parent, text="Choose files", style="Secondary.TButton", command=self.open_transcript).grid(row=2, column=0, sticky="ew")

        ttk.Separator(parent).grid(row=3, column=0, sticky="ew", pady=16)

        ttk.Label(parent, text="Codebook", style="Panel.TLabel").grid(row=4, column=0, sticky="w")
        ttk.Label(parent, textvariable=self.codebook_summary, style="Muted.TLabel", wraplength=220).grid(row=5, column=0, sticky="ew", pady=(4, 8))
        ttk.Button(parent, text="Choose codebook", style="Secondary.TButton", command=self.open_codebook).grid(row=6, column=0, sticky="ew")

        ttk.Separator(parent).grid(row=7, column=0, sticky="ew", pady=16)

        ttk.Label(parent, text="Shared focus topic", style="Panel.TLabel").grid(row=8, column=0, sticky="w")
        ttk.Entry(parent, textvariable=self.central_theme).grid(row=9, column=0, sticky="ew", pady=(4, 14))

        count_row = ttk.Frame(parent, style="Surface.TFrame")
        count_row.grid(row=10, column=0, sticky="ew")
        count_row.columnconfigure(0, weight=1)
        count_row.columnconfigure(1, weight=1)

        ttk.Label(count_row, text="Themes", style="Panel.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(count_row, text="Quotes", style="Panel.TLabel").grid(row=0, column=1, sticky="w", padx=(10, 0))
        ttk.Spinbox(count_row, from_=1, to=20, width=6, textvariable=self.theme_count).grid(row=1, column=0, sticky="ew", pady=(4, 0))
        ttk.Spinbox(count_row, from_=0, to=200, width=6, textvariable=self.quotes_per_theme).grid(row=1, column=1, sticky="ew", padx=(10, 0), pady=(4, 0))

        ttk.Button(parent, text="Analyze", style="Primary.TButton", command=self.analyze).grid(row=11, column=0, sticky="ew", pady=(18, 8))
        ttk.Button(parent, text="Export report", style="Secondary.TButton", command=self.save_report).grid(row=12, column=0, sticky="ew")

        ttk.Label(
            parent,
            text="Suggestions need researcher review before reporting.",
            style="Muted.TLabel",
            wraplength=220,
        ).grid(row=13, column=0, sticky="ew", pady=(18, 0))

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

    def _build_evidence_panel(self, parent: ttk.Frame) -> None:
        ttk.Label(parent, textvariable=self.detail_title, style="Panel.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 8))

        navigation = ttk.Frame(parent, style="Surface.TFrame")
        navigation.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        navigation.columnconfigure(2, weight=1)
        ttk.Button(navigation, text="Previous quote", style="Secondary.TButton", command=self.previous_quote).grid(row=0, column=0, sticky="w", padx=(0, 8))
        ttk.Button(navigation, text="Next quote", style="Secondary.TButton", command=self.next_quote).grid(row=0, column=1, sticky="w", padx=(0, 10))
        ttk.Label(navigation, textvariable=self.quote_status, style="Muted.TLabel").grid(row=0, column=2, sticky="w")

        ttk.Label(parent, textvariable=self.quote_meta, style="Muted.TLabel", wraplength=520).grid(row=2, column=0, sticky="ew", pady=(0, 8))

        reason_frame = ttk.Labelframe(parent, text="Quote rationale", style="Panel.TLabelframe", padding=(10, 8))
        reason_frame.grid(row=3, column=0, sticky="ew", pady=(0, 8))
        reason_frame.columnconfigure(0, weight=1)
        ttk.Label(reason_frame, textvariable=self.quote_reason, style="Muted.TLabel", wraplength=520).grid(row=0, column=0, sticky="ew")

        self.transcript_notebook = ttk.Notebook(parent)
        self.transcript_notebook.grid(row=4, column=0, sticky="nsew")

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

        self.result = None
        self.current_theme_index = None
        self.current_quote_matches = []
        self.current_quote_index = -1
        self.theme_list.delete(0, tk.END)
        self.theme_summary.set("No analysis yet")
        self.detail_title.set("Transcript evidence")
        self.quote_status.set(evidence_navigation_status(0, 0))
        self.quote_meta.set("Analyze to highlight quote evidence across the uploaded transcripts.")
        self.quote_reason.set("Select a theme after analysis to review quote-selection rationale.")
        self.file_summary.set(file_selection_summary(self.input_paths))
        self._render_transcript_tabs()
        self.status_text.set("Files loaded")

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
            self.result = analyze_documents(self.documents, settings)
        except (tk.TclError, ValueError) as exc:
            messagebox.showerror("Analysis failed", str(exc))
            return

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

        status = analysis_status_text(
            document_count=self.result.document_count,
            quote_count=self.result.quote_count,
            theme_count=len(self.result.themes),
        )
        self.theme_summary.set(status)
        self.status_text.set(status)

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
            return
        for theme in self.result.themes:
            label = theme_list_label(
                theme.name,
                theme.color,
                theme.quote_count,
                float(theme.validation.get("central_theme_alignment", 0)),
            )
            self.theme_list.insert(tk.END, label)
            self.theme_list.itemconfig(tk.END, foreground=theme.color)
        self._apply_display_colors()

    def _render_transcript_tabs(self) -> None:
        for tab_id in self.transcript_notebook.tabs():
            self.transcript_notebook.forget(tab_id)
        for child in self.transcript_notebook.winfo_children():
            child.destroy()

        self.transcript_widgets = {}
        self.transcript_frames = {}

        documents = self.documents or [TranscriptDocument(name="Transcript", text="Open one or more transcripts to view raw content here.")]
        for document in documents:
            frame = ttk.Frame(self.transcript_notebook, style="Surface.TFrame")
            frame.columnconfigure(0, weight=1)
            frame.rowconfigure(0, weight=1)

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
            text.grid(row=0, column=0, sticky="nsew")
            self._set_text_content(text, document.text)

            scrollbar = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=text.yview)
            scrollbar.grid(row=0, column=1, sticky="ns")
            text.configure(yscrollcommand=scrollbar.set)

            self.transcript_notebook.add(frame, text=transcript_tab_label(document.name))
            self.transcript_widgets[document.name] = text
            self.transcript_frames[document.name] = frame

        self._apply_display_colors()
        if self.result is not None:
            self._highlight_all_quotes(self.current_theme_index)

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

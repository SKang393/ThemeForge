from __future__ import annotations

from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import __version__
from .analysis import AnalysisResult, AnalysisSettings, TranscriptDocument, analyze_documents
from .exports import export_json, export_markdown, export_quotes_csv
from .io import load_transcript_text
from .ui_model import (
    PROFESSIONAL_THEME,
    analysis_status_text,
    file_selection_summary,
    format_validation_summary,
    theme_list_label,
)


class ThemeForgeApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"ThemeForge {__version__}")
        self.minsize(1120, 740)
        self.configure(background=PROFESSIONAL_THEME["background"])

        self.input_paths: list[Path] = []
        self.result: AnalysisResult | None = None
        self.file_summary = tk.StringVar(value=file_selection_summary([]))
        self.status_text = tk.StringVar(value="Ready")
        self.detail_title = tk.StringVar(value="Evidence")
        self.theme_count = tk.IntVar(value=8)
        self.quotes_per_theme = tk.IntVar(value=0)
        self.central_theme = tk.StringVar(value="")

        self._configure_styles()
        self._build_layout()

    def _configure_styles(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure(
            ".",
            background=PROFESSIONAL_THEME["background"],
            foreground=PROFESSIONAL_THEME["text"],
            font=("Segoe UI", 10),
        )
        style.configure("Surface.TFrame", background=PROFESSIONAL_THEME["surface"])
        style.configure("Header.TFrame", background=PROFESSIONAL_THEME["surface"])
        style.configure("Panel.TLabelframe", background=PROFESSIONAL_THEME["surface"], bordercolor=PROFESSIONAL_THEME["border"])
        style.configure("Panel.TLabelframe.Label", background=PROFESSIONAL_THEME["surface"], foreground=PROFESSIONAL_THEME["text"], font=("Segoe UI", 10, "bold"))
        style.configure("Title.TLabel", background=PROFESSIONAL_THEME["surface"], foreground=PROFESSIONAL_THEME["text"], font=("Segoe UI Semibold", 18))
        style.configure("Subtitle.TLabel", background=PROFESSIONAL_THEME["surface"], foreground=PROFESSIONAL_THEME["muted"])
        style.configure("Panel.TLabel", background=PROFESSIONAL_THEME["surface"], foreground=PROFESSIONAL_THEME["text"])
        style.configure("Muted.TLabel", background=PROFESSIONAL_THEME["surface"], foreground=PROFESSIONAL_THEME["muted"])
        style.configure("Primary.TButton", font=("Segoe UI Semibold", 10), padding=(14, 7))
        style.configure("Secondary.TButton", padding=(12, 7))
        style.configure("TEntry", fieldbackground="#ffffff", bordercolor=PROFESSIONAL_THEME["border"], padding=4)
        style.configure("TSpinbox", arrowsize=12)

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
        ttk.Button(header_actions, text="Import", style="Secondary.TButton", command=self.open_transcript).grid(row=0, column=0, padx=(0, 8))
        ttk.Button(header_actions, text="Analyze", style="Primary.TButton", command=self.analyze).grid(row=0, column=1, padx=(0, 8))
        ttk.Button(header_actions, text="Export", style="Secondary.TButton", command=self.save_report).grid(row=0, column=2)

        workspace = ttk.Frame(self, padding=(18, 18, 18, 12))
        workspace.grid(row=1, column=0, sticky="nsew")
        workspace.columnconfigure(0, weight=0, minsize=270)
        workspace.columnconfigure(1, weight=1, minsize=310)
        workspace.columnconfigure(2, weight=2, minsize=480)
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
        evidence.rowconfigure(1, weight=1)
        self._build_evidence_panel(evidence)

        footer = ttk.Frame(self, padding=(20, 0, 20, 14))
        footer.grid(row=2, column=0, sticky="ew")
        footer.columnconfigure(0, weight=1)
        ttk.Label(footer, textvariable=self.status_text, style="Muted.TLabel").grid(row=0, column=0, sticky="w")

    def _build_controls(self, parent: ttk.Frame) -> None:
        ttk.Label(parent, text="Transcripts", style="Panel.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(parent, textvariable=self.file_summary, style="Muted.TLabel", wraplength=220).grid(row=1, column=0, sticky="ew", pady=(4, 14))

        ttk.Button(parent, text="Choose files", style="Secondary.TButton", command=self.open_transcript).grid(row=2, column=0, sticky="ew")

        ttk.Separator(parent).grid(row=3, column=0, sticky="ew", pady=16)

        ttk.Label(parent, text="Focus theme", style="Panel.TLabel").grid(row=4, column=0, sticky="w")
        ttk.Entry(parent, textvariable=self.central_theme).grid(row=5, column=0, sticky="ew", pady=(4, 14))

        count_row = ttk.Frame(parent, style="Surface.TFrame")
        count_row.grid(row=6, column=0, sticky="ew")
        count_row.columnconfigure(0, weight=1)
        count_row.columnconfigure(1, weight=1)

        ttk.Label(count_row, text="Themes", style="Panel.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(count_row, text="Quotes", style="Panel.TLabel").grid(row=0, column=1, sticky="w", padx=(10, 0))
        ttk.Spinbox(count_row, from_=1, to=20, width=6, textvariable=self.theme_count).grid(row=1, column=0, sticky="ew", pady=(4, 0))
        ttk.Spinbox(count_row, from_=0, to=200, width=6, textvariable=self.quotes_per_theme).grid(row=1, column=1, sticky="ew", padx=(10, 0), pady=(4, 0))

        ttk.Button(parent, text="Run analysis", style="Primary.TButton", command=self.analyze).grid(row=7, column=0, sticky="ew", pady=(18, 8))
        ttk.Button(parent, text="Save report", style="Secondary.TButton", command=self.save_report).grid(row=8, column=0, sticky="ew")

        ttk.Label(
            parent,
            text="Suggestions need researcher review before reporting.",
            style="Muted.TLabel",
            wraplength=220,
        ).grid(row=9, column=0, sticky="ew", pady=(18, 0))

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
            highlightbackground=PROFESSIONAL_THEME["border"],
            selectbackground=PROFESSIONAL_THEME["surface_alt"],
            selectforeground=PROFESSIONAL_THEME["text"],
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

        text_frame = ttk.Frame(parent, style="Surface.TFrame")
        text_frame.grid(row=1, column=0, sticky="nsew")
        text_frame.columnconfigure(0, weight=1)
        text_frame.rowconfigure(0, weight=1)

        self.detail_text = tk.Text(
            text_frame,
            wrap="word",
            padx=16,
            pady=14,
            undo=False,
            borderwidth=0,
            highlightthickness=1,
            highlightbackground=PROFESSIONAL_THEME["border"],
            background="#ffffff",
            foreground=PROFESSIONAL_THEME["text"],
            insertbackground=PROFESSIONAL_THEME["text"],
            font=("Segoe UI", 10),
            relief="flat",
        )
        self.detail_text.grid(row=0, column=0, sticky="nsew")
        self.detail_text.configure(state="disabled")

        scrollbar = ttk.Scrollbar(text_frame, orient=tk.VERTICAL, command=self.detail_text.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.detail_text.configure(yscrollcommand=scrollbar.set)

    def open_transcript(self) -> None:
        paths = filedialog.askopenfilenames(
            title="Open transcripts",
            filetypes=[
                ("Transcript files", "*.txt *.md *.csv *.docx *.rtf"),
                ("Text files", "*.txt *.md *.csv"),
                ("Word documents", "*.docx *.rtf"),
                ("All files", "*.*"),
            ],
        )
        if not paths:
            return
        self.input_paths = [Path(path) for path in paths]
        self.file_summary.set(file_selection_summary(self.input_paths))
        self.status_text.set("Files loaded")

    def analyze(self) -> None:
        if not self.input_paths:
            messagebox.showinfo("Open transcripts", "Choose one or more transcript files first.")
            return

        try:
            documents = [
                TranscriptDocument(name=path.name, text=load_transcript_text(path))
                for path in self.input_paths
            ]
            settings = AnalysisSettings(
                theme_count=max(1, int(self.theme_count.get())),
                quotes_per_theme=max(0, int(self.quotes_per_theme.get())),
                central_theme=self.central_theme.get().strip(),
            )
            self.result = analyze_documents(documents, settings)
        except Exception as exc:
            messagebox.showerror("Analysis failed", str(exc))
            return

        self._render_themes()
        if self.result.themes:
            self.theme_list.selection_set(0)
            self.show_theme(0)
        else:
            self.detail_title.set("No themes found")
            self._set_detail("No quote-length transcript units were found.")

        status = analysis_status_text(
            document_count=self.result.document_count,
            quote_count=self.result.quote_count,
            theme_count=len(self.result.themes),
        )
        self.theme_summary.set(status)
        self.status_text.set(status)

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

    def show_selected_theme(self, _event: object | None = None) -> None:
        selection = self.theme_list.curselection()
        if not selection:
            return
        self.show_theme(selection[0])

    def show_theme(self, index: int) -> None:
        if self.result is None or index >= len(self.result.themes):
            return

        theme = self.result.themes[index]
        self.detail_title.set(theme.name)
        lines = [
            f"{theme.id} | {theme.color}",
            f"{theme.quote_count} quote units | cohesion {theme.score:.3f}",
            format_validation_summary(theme.validation),
            "",
            f"Keywords: {', '.join(theme.keywords) if theme.keywords else 'None'}",
            "",
            "Quote evidence",
            "",
        ]
        for quote in theme.quotes:
            lines.extend(
                [
                    (
                        f"[{theme.id} {theme.color}] {quote.speaker} | {quote.quote_id} | "
                        f"{quote.source_name}: line {quote.source_line} | relevance {quote.relevance:.3f}"
                    ),
                    quote.text,
                    "",
                ]
            )
        self._set_detail("\n".join(lines))

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

    def _set_detail(self, text: str) -> None:
        self.detail_text.configure(state="normal")
        self.detail_text.delete("1.0", tk.END)
        self.detail_text.insert(tk.END, text)
        self._apply_theme_color_tags()
        self.detail_text.configure(state="disabled")

    def _apply_theme_color_tags(self) -> None:
        if self.result is None:
            return
        for theme in self.result.themes:
            tag = f"theme_{theme.id}"
            self.detail_text.tag_configure(tag, foreground=theme.color, font=("Segoe UI Semibold", 10))
            marker = f"[{theme.id} {theme.color}]"
            start = "1.0"
            while True:
                index = self.detail_text.search(marker, start, stopindex=tk.END)
                if not index:
                    break
                line_end = f"{index} lineend"
                self.detail_text.tag_add(tag, index, line_end)
                start = line_end


def main() -> int:
    app = ThemeForgeApp()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

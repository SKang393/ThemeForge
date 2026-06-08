# ThemeForge

ThemeForge is a Windows-friendly qualitative analysis tool for
interview and focus group transcripts. It suggests thematic coding categories,
keeps quote evidence tied to source files and line numbers, and helps
researchers review how participant perspectives support candidate themes.

This project is rebuilt from an AutoPhrase fork. The original AutoPhrase system
was designed for automated phrase mining from massive text corpora. This
project has a different purpose: practical thematic coding support for
qualitative studies in education and related fields.

## Current Version

v1.0.0

## What It Does

- Opens TXT, Markdown, CSV, DOCX, and RTF transcripts.
- Accepts one transcript or multiple related transcripts in the same analysis.
- Preserves speaker labels when transcript lines use `Speaker: text`.
- Preserves source file names and transcript line numbers for quote review.
- Lets the researcher enter an optional central theme, such as accessibility,
  teacher perspective, parent burden, autism, or a short research focus.
- Suggests theme groups using embedded local lexical and contextual coding
  logic. No online service receives transcript text.
- Lists quote evidence for each suggested theme, including indirect matches
  where the quote supports the same focus area without repeating the exact
  theme label.
- Allows one quote to appear under multiple themes when it supports multiple
  candidate codes.
- Color-codes themes in the app, Markdown report, CSV quote table, and JSON.
- Uses compact researcher-facing labels for selected files, analysis status,
  theme rows, and validation summaries.
- Applies those compact labels inside the desktop app for easier scanning.
- Uses a neutral, professional desktop style for researcher-facing review.
- Provides a minimal three-panel desktop workspace for input controls, theme
  review, and quote evidence.
- Checks release metadata so app, package, README, and portable build defaults
  stay on the same version.
- Adds validation metadata for evidence count, source coverage, speaker
  coverage, central-theme alignment, and researcher-review status.
- Handles initial Korean transcript tokenization for first-pass theme and quote
  suggestions.

## What It Does Not Do

- It does not replace researcher coding, memo writing, or interpretation.
- It does not claim that suggested themes are final findings.
- It does not calculate intercoder reliability by itself.
- It does not require the original Linux/Mac AutoPhrase pipeline, Docker, Java,
  GNU Make, TreeTagger, or a C++ compiler.
- It does not send transcript text to an online service.

## Run From Source

Requirements for development:

- Python 3.10 or newer
- Windows, macOS, or Linux for source execution

Run the desktop app:

```powershell
python -m themeforge.app
```

Run the command-line analyzer with one transcript:

```powershell
python -m themeforge.cli transcript.docx --out analysis_report.md
```

Run the command-line analyzer with multiple transcripts and a central theme:

```powershell
python -m themeforge.cli focus_group_1.docx focus_group_2.rtf --central-theme "accessibility parent burden" --out analysis_report.md
```

Run tests:

```powershell
python -m unittest discover -s tests -v
```

## Windows Portable Build

The release build is intended to be a zip file that users can extract and run
without installing Python or NLP tools.

Build the portable zip:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/build_windows_portable.ps1
```

The zip is created under `release/`. The build script installs PyInstaller for
the build machine only. PyInstaller is not required by end users running the
portable release.

## Recommended Transcript Format

Use plain text with one speaker turn per line:

```text
Participant 1: I felt isolated when online classes started.
Participant 1: Small group meetings helped because peers explained assignments.
Participant 2: The teacher's weekly feedback made me feel supported.
```

The app also accepts plain paragraphs without speaker labels, but speaker labels
make quote evidence easier to review.

## Output Review Workflow

1. Open one transcript or multiple related transcripts.
2. Enter an optional central theme when the analysis should prioritize a topic.
3. Choose the maximum number of themes and quotes per theme. Use `0` quotes to
   list all matches.
4. Run analysis.
5. Review each suggested theme, its color, validation metadata, and quote
   evidence.
6. Check each quote against the transcript context.
7. Rename, merge, split, or reject themes in your own qualitative analysis
   workflow.
8. Export the report or quote table for researcher review.

## Validation Logic

ThemeForge uses machine assistance only for first-pass organization.
The validation framework is based on qualitative thematic-analysis practice:

- Braun and Clarke describe thematic analysis as a flexible qualitative method
  for identifying and interpreting patterns in qualitative data:
  <https://doi.org/10.1191/1478088706qp063oa>
- Nowell, Norris, White, and Moules emphasize trustworthiness through a clear
  audit trail, systematic analysis, and enough detail for readers to judge
  credibility: <https://doi.org/10.1177/1609406917733847>
- Fereday and Muir-Cochrane support a hybrid inductive and deductive process,
  which matches the app's optional central-theme priority plus data-driven
  quote grouping: <https://doi.org/10.1177/160940690600500107>
- Campbell, Quincy, Osserman, and Pedersen highlight the importance of
  consistent text units and coding-scheme review for semistructured interviews:
  <https://doi.org/10.1177/0049124113500475>
- O'Connor and Joffe describe when intercoder reliability is useful, debated,
  and separate from interpretive theme development:
  <https://doi.org/10.1177/1609406919899220>
- Guest, MacQueen, and Namey provide applied thematic-analysis guidance for
  codebook-oriented team workflows: <https://doi.org/10.4135/9781483384436>

The app therefore reports evidence count, source coverage, speaker coverage,
central-theme alignment, and review status. These are review aids, not proof
that a theme is valid. The researcher still owns interpretation, memo writing,
literature connection, and final reporting.

## Public Education Data for Testing

Candidate public datasets for external QA are listed in
`docs/public_education_datasets.md`. Do not redistribute raw transcript data in
this repository unless the dataset license clearly allows it.

Earlier validation was run locally on public education and higher education
transcript data stored under ignored `tmp/` folders, including DaRUS
study-success RTF interviews, a DMU figshare DOCX interview, and King's College
London neurodiversity focus-group transcripts. Raw transcript files are not
committed. The v1.0.0 release pass also includes English and Korean CLI smoke
checks for report branding, validation sections, and source-aware quote output.

## Credits

Project direction and qualitative-study requirements: [SKang393](https://github.com/SKang393).

This project is reconstructed from an AutoPhrase fork. Original AutoPhrase
authors and project:

- Jingbo Shang, Jialu Liu, Meng Jiang, Xiang Ren, Clare R. Voss, and Jiawei Han.
  "Automated Phrase Mining from Massive Text Corpora."
  <https://arxiv.org/abs/1702.04457>
- Jialu Liu, Jingbo Shang, Chi Wang, Xiang Ren, and Jiawei Han.
  "Mining Quality Phrases from Massive Text Corpora."
  <https://doi.org/10.1145/2723372.2751523>
- Original AutoPhrase repository:
  <https://github.com/shangjingbo1226/AutoPhrase>

## Roadmap

- v0.1.0: Basic thematic grouping and quote evidence export.
- v0.2.0: DOCX/RTF import and bounded clustering for realistic transcript lengths.
- v0.3.0: Contextual coding model, all-quote evidence by default, and theme colors.
- v0.4.0: Multi-file analysis, central-theme priority, validation metadata,
  color-coded transcript evidence, source-aware exports, and initial Korean
  tokenization.
- v0.5.0: Stable desktop wording helpers, compact status labels, and
  professional UI palette tests.
- v0.6.0: Desktop app integration for concise theme rows, selected-file labels,
  validation summaries, and saved-report messages.
- v0.7.0: Professional Tkinter styling, neutral palette, cleaner buttons, and
  readable theme/evidence panes.
- v0.8.0: Three-panel desktop workspace with input controls, theme list,
  evidence view, scrollbars, and compact header actions.
- v0.9.0: Release metadata checks for app version, package version, README, and
  Windows portable build defaults.
- v1.0.0: Minimal professional desktop workspace, 1.0 release metadata, and
  portable Windows release checks.
- Later: User-editable codebooks, manual theme editing, quote reassignment,
  merge/split controls, optional local semantic embeddings, and deeper
  multilingual support.

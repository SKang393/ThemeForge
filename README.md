# ThemeForge

ThemeForge is a Windows-friendly qualitative analysis workspace for
interview and focus group transcripts. It suggests thematic coding categories,
keeps quote evidence tied to source files and line numbers, supports manual
coding with persistent project files, and helps researchers review how
participant perspectives support candidate themes.

Everything runs locally: no transcript text is sent to an online service, and
the portable Windows build runs without installing Python or an NLP stack.

## Current Version

v1.0.8

Archive DOI: <https://doi.org/10.5281/zenodo.20653169>

## What It Does

### Transcript import

- Opens TXT, Markdown, CSV, DOCX, RTF, and text-based PDF transcripts.
- Accepts one transcript or multiple related transcripts in the same analysis.
- Preserves speaker labels when transcript lines use `Speaker: text`,
  Otter/Zoom-style `Speaker  0:03`, or Word-style `Speaker 0:03` turns.
- Filters interviewer, moderator, consent, and procedure text from theme
  generation, and removes transcription boilerplate, timestamp rows, and
  table-export noise before coding units are built.
- Preserves source file names and transcript line numbers for quote review.
- Handles Korean transcript tokenization for first-pass theme and quote
  suggestions.
- Removes selected transcripts or a codebook from the workspace without
  deleting the source files from disk.

### Theme suggestion

- Suggests theme groups using local TF-IDF clustering and c-TF-IDF phrase
  labels, with optional local sentence-transformer embeddings when installed.
- Lets the researcher enter an optional central theme, such as accessibility,
  teacher perspective, parent burden, autism, or a short research focus.
- Uses local BM25-style focus relevance and transcript-level related terms so
  indirect curriculum, training, lesson, assessment, and planning connections
  can be ranked for review without requiring an online model.
- Imports optional researcher codebooks from CSV or text documents for
  deductive theme matching against quote evidence.
- Allows one quote to appear under multiple themes when it supports multiple
  candidate codes.

### Evidence review

- Lists quote evidence for each suggested theme, including indirect matches
  where the quote supports the same focus area without repeating the exact
  theme label.
- Highlights raw transcript passages with theme colors and provides previous
  and next quote navigation for the selected theme.
- Shows coding stripes beside transcript passages so overlapping codes remain
  visible during review.
- Shows quote-selection rationale for the highlighted quote, including theme
  signals, focus-topic alignment, and a researcher-review reminder.
- Finds ranked uncoded passages similar to the selected theme or quote, using
  local embedding cosine similarity when available and TF-IDF cosine fallback
  otherwise; accepted suggestions join the existing undo and autosave workflow.
- Adds validation metadata for evidence count, source coverage, speaker
  coverage, central-theme alignment, and researcher-review status.

### Projects and manual coding

- Saves and opens `.tfproj` project files containing cached transcript text,
  settings, analysis results, and manual coding work.
- Codes arbitrary transcript selections to existing or new themes and stores
  theme, quote, and document memos.
- Supports manual theme renaming, hierarchy, merging, splitting, quote
  reassignment, uncoding, quote-boundary editing, and undo or redo.
- Preserves manually touched themes when analysis is run again.

### Team rigor tools

- Shows theme-by-source and theme-by-speaker matrix views with quote counts and
  unique-span coverage for coded evidence review.
- Compares two `.tfproj` files when they have identical shared transcript text
  and fixed shared meaning units, reporting per-theme percent agreement and
  Cohen's kappa, prevalence or undefined-kappa warnings, and side-by-side
  disagreements.
- Stores coder identity with persistent and exportable coding audit events.

### Exports and interface

- Color-codes themes in the app, Markdown report, CSV quote table, and JSON.
- Provides a minimal three-panel desktop workspace for input controls, theme
  review, and quote evidence, with compact researcher-facing labels for
  selected files, analysis status, theme rows, and validation summaries.
- Shows uploaded transcripts in file tabs so researchers can review each raw
  transcript while using one shared focus topic across the whole dataset.
- Offers light and dark window modes and an About dialog with project contact
  information.
- Checks release metadata so app, package, README, and portable build defaults
  stay on the same version.

## What It Does Not Do

- It does not replace researcher coding, memo writing, or interpretation.
- It does not claim that suggested themes are final findings.
- It does not treat intercoder agreement metrics as proof of interpretive
  validity.
- It does not require Docker, Java, an online model service, or a compiler
  toolchain; the portable build also runs without a Python installation.
- It does not send transcript text to an online service.

## Run From Source

Requirements for development:

- Python 3.10 or newer
- Windows, macOS, or Linux for source execution

Run the desktop app:

```powershell
python -m themeforge.app
```

In the desktop app, use **Choose files** for transcripts and **Choose codebook**
for optional deductive matching against researcher-defined themes.

Run the command-line analyzer with one transcript:

```powershell
python -m themeforge.cli transcript.docx --out analysis_report.md
```

Run the command-line analyzer with multiple transcripts and a central theme:

```powershell
python -m themeforge.cli focus_group_1.docx focus_group_2.rtf --central-theme "accessibility parent burden" --out analysis_report.md
```

Run deductive quote matching with a researcher codebook:

```powershell
python -m themeforge.cli interview_1.docx --codebook codebook.csv --quotes 3 --out codebook_report.md
```

Optional local semantic embeddings and Korean tokenization:

```powershell
python -m pip install "themeforge[nlp]"
python -m themeforge.cli interview_1.docx --semantic-backend local-embeddings --language Multilingual --out semantic_report.md
```

The first embedding model download is handled by `sentence-transformers`; after
that the local model cache can be reused offline. The same optional NLP tier
also enables `kiwipiepy` Korean morphology so Korean transcripts use content
terms instead of raw particle-attached Hangul chunks.

The standard Windows portable ZIP does not bundle the optional NLP tier. It uses
the built-in TF-IDF backend and Korean fallback tokenization without requiring
Python, a model download, or an online service. Install ThemeForge from source
with `themeforge[nlp]` to enable sentence-transformer embeddings and
`kiwipiepy` morphology.

CSV codebooks should include a `theme`, `code`, `name`, or `category` column.
Optional `description` and `example` or `quote` columns improve quote matching.
TXT, DOCX, RTF, and text-based PDF codebooks can also use labeled lines such as
`Theme:`, `Description:`, and `Example:`.

Run tests:

```powershell
python -m unittest discover -s tests -v
```

## Windows Portable Build

The standard release build is a zip file that users can extract and run without
installing Python or NLP tools. It contains the built-in TF-IDF analysis path;
the optional NLP tier described above is source-install only.

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

Timestamped transcript exports are also supported:

```text
Moderator 0:03
Before we start, this interview will be recorded for research purpose.

Participant 1 1:04
Small group meetings helped because peers explained assignments.
```

The app also accepts plain paragraphs without speaker labels, but speaker labels
make quote evidence easier to review.

## Community Guidelines

Use GitHub issues to report problems, ask support questions, or request
features. Include the ThemeForge version, operating system, whether you used the
desktop app or command line, transcript file type, steps to reproduce the
problem, and expected versus actual behavior.

Do not paste confidential or identifiable transcript text into public issues.
Use short synthetic examples unless a dataset license clearly allows
redistribution.

Contributions should stay narrowly tied to ThemeForge's qualitative-coding
purpose. Avoid new runtime dependencies unless they are necessary for the
workflow, and run the test suite before opening a pull request:

```powershell
python -m unittest discover -s tests -v
```

## Output Review Workflow

1. Open one transcript or multiple related transcripts.
2. Enter an optional central theme when the analysis should prioritize a topic.
3. Choose the maximum number of themes and quotes per theme. Use `0` quotes to
   list all matches.
4. Run analysis.
5. Review each suggested theme, its color, validation metadata, and quote
   evidence.
6. Check each quote against the transcript context.
7. Use **Find from theme** or **Find from quote** to review related uncoded
   passages, then code only the suggestions supported by the transcript context.
8. Rename, merge, split, or reject themes in your own qualitative analysis
   workflow.
9. Export the report or quote table for researcher review.

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

Focus percentages are review signals, not confidence scores. A higher value
means the theme contains direct focus-topic wording or transcript-learned
related terms. Lower nonzero values mean the theme is indirectly connected and
should be checked by the researcher rather than treated as a final code.

Similar-passage scores are cosine-similarity ranking aids, not probabilities or
automatic coding decisions. Theme search compares uncoded quote-length passages
with the selected theme label, keywords, and evidence; quote search compares
them with the selected quote. Local sentence-transformer embeddings support
indirect semantic matches when installed, while normalized TF-IDF supplies the
portable fallback. This follows the open Sentence Transformers semantic-search
pattern: <https://sbert.net/examples/sentence_transformer/applications/semantic-search/README.html>.

## Public Education Data for Testing

Candidate public datasets for external QA are listed in
`docs/public_education_datasets.md`. Do not redistribute raw transcript data in
this repository unless the dataset license clearly allows it.

Validation runs use public education and higher education transcript data
stored under ignored local folders, including DaRUS study-success RTF
interviews, a DMU figshare DOCX interview, and King's College London
neurodiversity focus-group transcripts, plus published-codebook comparison
checks driven by `scripts/validate_against_codebooks.py`. Raw transcript files
are not committed. Release passes include English and Korean CLI smoke checks
for report branding, validation sections, and source-aware quote output.

## Credits

ThemeForge is designed, written, and maintained by
[Sungwoo Kang (SKang393)](https://github.com/SKang393).
Purdue University affiliation. ORCID: 0000-0002-6449-712X.

## Roadmap

- v0.1.0: Basic thematic grouping and quote evidence export.
- v0.2.0: DOCX/RTF import and bounded clustering for realistic transcript lengths.
- v0.3.0: Researcher-review notes, all-quote evidence by default, and theme colors.
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
- v1.0.1: Multi-file transcript tabs, theme-colored transcript highlighting,
  quote navigation, light/dark modes, About dialog, interviewer/moderator and
  consent-procedure filtering, named-interviewer filtering, duplicate-theme
  merging, and quote-selection rationale in the Evidence panel and exports.
- v1.0.2: Fixed unlabeled transcript handling so procedure or consent text at
  the start of an imported file does not prevent theme generation.
- v1.0.3: Improved embedded interviewer-prompt filtering, contextual focus
  alignment, and richer phrase-based theme labels.
- v1.0.4: Added text-based PDF input, optional codebook matching, stronger
  transcript parsing, quote offsets, and validation harness checks.
- v1.0.5: Added persistent projects, manual selection coding, theme and quote
  editing, hierarchy, memos, coding stripes, transcript and codebook removal,
  optional local embeddings, and optional Korean morphology.
- v1.0.6: Fixed CLI completion output for Korean and other Unicode report paths
  when Windows uses a legacy console encoding.
- v1.0.7: Added ranked "find more like this" retrieval from a selected theme or
  quote, uncoded-span exclusion, source highlighting, and speaker-preserving
  acceptance through the existing undo and autosave workflow.
- v1.0.8: Added matrix views, intercoder comparison, coder identity, and
  exportable audit-event metadata for team coding review.
- Later: REFI-QDA exchange and DOCX report export.

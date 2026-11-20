# ThemeForge

ThemeForge is a Windows-friendly qualitative analysis workspace for
interview and focus group transcripts. It suggests thematic coding categories,
keeps quote evidence tied to source files and line numbers, supports manual
coding with persistent project files, and helps researchers review how
participant perspectives support candidate themes.

Everything runs locally: no transcript text is sent to an online service, and
the portable Windows build runs without installing Python or an NLP stack.

## Current Version

v1.5.0

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
  and fixed shared meaning units. Quote coverage reports show per-theme percent
  agreement and Cohen's kappa, prevalence or undefined-kappa warnings, and
  side-by-side disagreements. The paired review export compares two named
  readers' decisions on shared matches.
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

The desktop app starts with a blank project. Use **Choose files** to import a
transcript. For a first code of your own, enter a **Coder name**, type the code
in **Theme name**, select a passage in the transcript, and click **New code from
selection** under **Code passages**. **Details** holds the keyword and memo
controls. **Organize** holds the hierarchy and reassignment controls. Use
**Choose codebook** for optional deductive matching against researcher-defined
themes.

To code a passage **without running Analyze**, enter a researcher name or
pseudonym, select the passage in its transcript tab, choose a codebook entry
beside **Apply codebook entry**, and review the highlighted passage in its
transcript context. The review window shows every definition and example that
shares the chosen label. You can change the entry there without changing the
passage. Only **Apply selected definition** records a decision; Cancel does not.
The assignment records the coder, selected wording, source offsets and the
definition actually used. Entries with the same label but different definitions
stay separate. Unselected text is not a rejection. Save or export immediately;
running Analyze later adds separate suggestions, not endorsements.

After **Analyze** with a codebook, the footer counts eligible passages with at
least one displayed codebook suggestion. Choose **Next without suggestion** to
select each remaining passage in transcript order, wrapping back to the first.
Read the surrounding text and, if a definition fits, use **Apply codebook
entry…**. Browsing records no decision. A passage with one suggested code may
still need another. A declined or deferred suggestion still counts as displayed.
The filter describes the last run in this session. Run **Analyze** again after
reopening or changing the codebook or speaker choices to refresh it.

After **Choose files**, open **Speakers for suggestions** before your first
**Analyze**. It shows the parsed labels in each imported transcript,
the automatic decision, the number of turns and a sample with its source line.
Select one or more rows and choose **Include**, **Exclude**, or **Use automatic**,
then **Apply**. Analyze and both Find actions use these choices before ranking.
Automatic uses the existing Analyze role rules across the open study. A correction
applies only to the selected transcript and label. Passage filters still apply.
You can still read and manually code any source. Apply clears the open Find list
and records the change in the audit log. Run Analyze to generate new theme
suggestions. Save the project to keep your choices for the next session.
Older app versions ignore these choices. From the CLI, pass a filename and its
parsed label, for example `--include-speaker workshop.txt Host` or
`--exclude-speaker interview.txt Facilitator`.

**Reviewing candidate quotes:** Previous/Next visits the stored records,
including ones the app cannot locate. Below the transcript, **Quote review**
shows the rationale and decisions. The adjacent tabs hold the quote and document
memos and the search for similar passages. A recorded source span is highlighted
only when the full excerpt and line match at its saved offsets. This verifies
the location, not the speaker attribution or whether the code fits the text.
The app does not substitute another speaker's identical wording or a matching
prefix. An older quote with no offsets can show a read-only text location if its
full wording occurs exactly once on the recorded line and nowhere else in the
document. On a conflict, the app shows the recorded line as context when
available, without highlighting it as the cited passage. Navigation
does not rewrite saved records or correct earlier exports.

**Reading exported citations:** Markdown, CSV and JSON reports check every
stored quote against the transcript text in the project or CLI input. An
`exact_span` matches the full text, recorded offsets and line. A `text_only`
location has unique matching text on the recorded line but no stored offsets.
A `conflict` means the saved location contradicts the source. An
`unverifiable` location lacks enough information, for example when two
transcripts share a name. Conflicting and unverifiable excerpts are retained
for review, not presented as verified quotations. Speaker labels and code
interpretations remain unverified even for exact spans. Reports generated
without the source documents cannot verify any locations. Older reports
are unchanged; reopen the project and export again to see these checks.

**Recording a match review:** Enter a reviewer name, select a source-verified
quote in its transcript, then choose **Supports this theme**, **Not this match**
or **Revisit later**. Check the excerpt and record your reason. Each review
records one reader's judgment about that passage under that theme. With a
coder name entered, **Quote review** shows that reader's latest note for the
exact pairing. Markdown, CSV and JSON reports show the latest note across
readers, attributed to its author. A blank review
does not mean the passage was assessed or rejected. Running **Analyze** again
starts fresh, even if a quote ID repeats. Earlier notes, their exact excerpts,
scores and reviewer names remain in **Rigor tools → Audit log** and its CSV export.
Older readers do not display the review notes alongside the quotes.

**Revising a codebook after review.** A codebook match review shows the full
passage and the saved entry, including its examples and exclusions. When you
load a revised codebook, only entries with the same code name, definition and
examples keep their current decisions. A removed or revised entry immediately
becomes historical, even before **Analyze**. The audit keeps the original
decision. Run **Analyze**, select a fresh suggestion, and review it against
the revised wording. If an older codebook suggestion lacks its original entry,
rerun discovery to review it against a verifiable definition.

Changing only the spacing between words in a suggested code's **Theme name**
keeps the review on the same passage and definition. The supported CSV gives
the displayed label in `theme_name` and the reviewed label in
`match_review_theme_name`, with the original reviewer, time and audit target.
A change of words or capitalization requires a new review. Editing a name
inside the codebook itself changes the entry.

When exporting a saved project through Python, pass
`codebook_entries=state.settings.codebook_entries` to the report exporter.
Without those entries, it can show the recorded quotes but cannot verify
current codebook support. The desktop supplies the saved entries on reopen.

**Resuming a study.** Open the saved project and check **Coder name**.
In **Quote review**, **Next unreviewed** jumps across themes to the next
scored passage with a verified span and no current review by that coder.
**Next deferred** returns to passages where that coder last chose **Revisit
later**, including the saved reason. Both start after the displayed match
and wrap around the study. **Previous quote** and **Next quote** still
browse every recorded passage in the selected theme.

**Handing off reviewed evidence.** In **Rigor tools → Audit log**, select the
transcripts your collaborator may see, then choose **Export supported matches
CSV**. Each row is an exact passage whose latest review across coders supports
the match. The table names that reviewer and includes their reason, time and
the original quote relevance. Read the excerpts, names, theme labels and
free-text reasons before sharing. A reason can mention another interview.

To check a citation with an authorized copy of the imported transcript text,
compare `source_sha256` with the SHA-256 of that text encoded as UTF-8.
For a PDF or Word document, use ThemeForge's extracted text. Slice that text
from `source_start` up to but excluding `source_end`, counting characters
from zero. Compare the result with `quote` and the one-based `source_line`.
The project custodian can trace the decision in the retained audit by matching
`match_review_target_id` to `target_id`, then checking the actor, time,
disposition and reason. The same pairing can have several review events.

Theme keywords and suggestion rationales can draw on other sources. The
supported table omits those fields, theme color and group review status.
The regular quote report includes these fields and all dispositions. Its `match_review_disposition`
column describes the individual pairing. `validation_review_status` describes
the whole theme and can read “Researcher edited” after a rename. The supported
export has only its header when no current match qualifies. **Export audit CSV**
contains the whole project's recorded history for every source, including
earlier decisions, declined and deferred excerpts and reasons. The transcript
selection applies only to the supported table. Older readers can display a previous
decline or deferral as current if they skip a later `supported` audit event.

**Comparing two readers' match reviews.** Save a study after discovery and give
each reader a separate copy before either starts reviewing. In each copy, enter
a distinct reviewer name or pseudonym, review passages, and save. Open one
copy, go to **Rigor tools → Intercoder**, and choose the other saved project.
Check the **Current reviewer** and **Comparison reviewer** names, then select
**Export paired match reviews** and choose a new CSV filename. Each side uses that person's latest audit
note on the exact match in their project since its last **Analyze**. A project
can contain several readers' notes. Choose the two whose decisions you want
to compare.

The paired CSV includes every theme/passage match with the same recorded snapshot
and verified source span in both projects, including pairs with no reviews.
Each recorded decision includes the reader's reason, time, and audit CSV target.
If the readers reviewed labels with different spacing, use
`current_review_target_id` and `comparison_review_target_id` to look up each
decision. The shared `match_review_target_id` is blank when the targets differ.
Filter `review_comparison` to find support versus
decline (`disagreement`), two supports or two declines, missing reviews, or
decisions awaiting a revisit. Where one review is missing, the other reader's
disposition is still shown. Exclusion counts cover stored quote rows outside
the shared set, including changed suggestions or unverifiable spans. Matching
decisions record what two named readers entered. Adjudication requires a
separate discussion. **Export coverage statistics** and **Export coverage
differences** compare stored theme quotes across transcript units, including
automatically suggested quotes.

**Coding a passage you found in a match:** In the **Quote review** tab, choose
**Apply definition to this quote…**. The dialog shows the current quote in its
transcript, even if a different passage was previously selected with the mouse.
Choose an entry from the current codebook, compare its full definition and
examples, then apply. No entry is inferred from the match label. The scored
candidate remains separate from the named coder's unscored assignment; they
are not two independent passages. Applying the same definition to the same
span again creates neither a duplicate nor an endorsement by another coder.
The Audit log links the original match to the new assignment. For a different
span, select its text and use **Apply codebook entry…** instead.

These manual assignments have no relevance score. Markdown labels them
unscored, CSV leaves relevance blank, and JSON reports use `null`.
The name-based intercoder comparison does not support these assignments and
will not calculate agreement for a project containing them.

For a descriptive comparison, open **Rigor tools → Intercoder**, choose the
other saved project, and select **Export recorded manual assignments**. The CSV
shows whether each exact definition and source span was recorded in both
projects or just one. It requires identical source names and text, distinct
declared coders, and separately recorded, traceable assignments. It does not
interpret an unselected passage as a negative decision or supply a denominator
for agreement or establish coder independence. Other quote rows, including
lexical suggestions, are excluded and counted separately.
Choose a new CSV filename. Spreadsheet-sensitive text cells receive a leading
apostrophe; the offsets always refer to the original transcript.

**Move quote** requires a researcher name or pseudonym and prompts for why
the passage belongs under the destination theme. It retains the quote ID,
exact source location and memo. The new rationale distinguishes the move from
the previous selection. The audit log keeps the original quote, its stored
score and prior group details. Neither the moved assignment nor the two
changed groups inherit a score; other quote rows retain theirs. If the target
already has the same ID or passage at those offsets, both records stay intact.

**Project compatibility:** Projects with new manual passage assignments,
new quote moves or recorded merges use a separate nullable-score archive profile. Their current
unscored fields contain JSON `null`, not zero or a negative score. Previous
readers that expect `project.json` cannot open these files and may report a
missing archive member. Ordinary numeric projects still use format 2.

You can open older format-1 and format-2 projects without modifying them.
When an older project needs the new profile, **Save project** asks for a new
filename. Autosave does not replace the original, and cancelling leaves the
changes open. Known earlier manual-code zero placeholders and move markers
are not scores. Old reports may show them as `0.000` or `-1.000`; re-export
from the updated app. An ordinary-looking historical move score cannot be
corrected from the number alone.

**Code selection**, **New code from selection**, and explicitly coding a
similar passage record your decision, not a model score. Enter your researcher
name or pseudonym first. The saved quote and audit identify how you chose the
passage. For an applied suggestion, the audit saves the search text, method
and similarity score. That score ranks the suggestion; it does not rate your assignment.
The assignment has blank CSV relevance,
JSON `null`, and no numeric score in Markdown. Earlier versions wrote `1.000`
for these actions. Existing scores and reports are not retrospectively changed;
a numeric 1 alone does not identify a manual decision.

When you place a manual code under a suggested theme, running **Analyze** again
keeps the parent you chose. If the new run changes or omits that group, the
theme list marks the earlier parent **Retained**. Its quotes and scores describe
the earlier run. Current suggestions can cite the same passages. The JSON report
records the parent ID, group and passage memos, and source offsets.
If an older project is missing the saved parent, the selector shows
**Unavailable parent** with its ID. You can choose another parent or **Top-level**
and save the hierarchy.

**Merge theme** uses your researcher name and asks for a reason. It counts the same exact
passage once even when its two assignments have different quote IDs. The
destination assignment keeps its own rationale, memo and score. A passage
transferred from the source group has no score for the destination, and the
merged group has no new cohesion score. A conflicting ID or an unverifiable
source anchor stops the merge without changing either group.

Quote reports describe the resulting assignments. For the two original
records, open **Rigor → Audit log** and export the audit CSV. The
`theme_merge_recorded` event includes both pre-merge groups, quote memos,
rationales and scores, plus the IDs linking their passages. Earlier merges
that recorded only the destination ID cannot reconstruct lost notes.

**Choose files** replaces the transcript collection; it does not append to it.
When replacing or removing transcripts, you can save the outgoing study, leave
without saving, or cancel. Apply unfinished theme or memo edits before saving.
The next study starts as a separate unsaved project, without the old results,
memos or audit log. To reuse the current codebook, choose **Names and definitions
only** and read the preview before continuing. This leaves examples in the original
study. You can also choose to copy examples. Check all selected text for excerpts,
names and source references. The new study gets the displayed entries in order.
Theme hierarchy belongs to the original project. For a new session, import your
definitions file with **Choose codebook**. The supported evidence and manual
assignment CSVs are study reports with cited passages and reviewer information.

Opening another saved project or closing the main window also prompts if the
current study has unsaved changes. To keep text still in a theme or memo
editor, apply it first. Choosing **No** discards those edits. An unreadable
project or a failed or canceled save leaves the current study open.

**Recovering after a save failure.** Keep the study open to retain changes
made since the last successful save. Apply any unfinished theme or memo edits.
Retry **Save project**, or choose **Save as…** and pick an unused filename in
a writable folder. A successful **Save as…** makes that file the destination
for subsequent saves and autosaves. Reopen the original project to inspect
the last version saved there. If the application stopped during a save, reopen
the `.tfproj`. A temporary `.tmp` file beside it can be incomplete. Changes
that were still only in memory need to be entered again.

**Open project** reads the transcripts and codebook stored in the project.
If a source file has changed on disk, the supported CSV still cites the saved
text and its `source_sha256`. To review the revised text, save the current
study and use **Choose files** to start a separate project with the revised
sources. Its suggestions need their own reviews.

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

CSV codebooks need at least one named row under `theme`, `code`, `name`, or
`category`. ThemeForge reads a definition from `description`, `definition`, or
`memo` and one example per row from `example`, `quote`, or `evidence`.
Both are optional. Headers ignore case and surrounding spaces. For each field,
the first nonblank value in the listed alias order is used. If a file has no
named entries, the import fails and the current codebook stays active.
Put inclusion and exclusion criteria inside the description. Separate criterion
and parent columns are ignored by the CSV importer.
In a definition, `Exclude:` starts guidance for the reader. `Include:` resumes
the text used for ranking. For example, “Test after a repair. Exclude: price
estimates.” uses the repair test for retrieval. A passage mentioning an
estimate can still be suggested if it also matches the test. The original
definition stays available for review, and the example field supplies positive
search terms.
TXT, MD, DOCX, RTF, and text-based PDF codebooks can also use labeled lines such as
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

## Built with Codex and GPT-5.6

ThemeForge was developed through a human-directed, AI-assisted workflow. The
author reviewed thematic-analysis literature, defined the research and product
requirements, and evaluated pilot versions. GPT-5.6 helped translate those
methodological requirements, user-experience observations, and identified edge
cases into explicit implementation specifications and validation criteria.

Codex was used to implement and refactor the Python application, write tests and
validation harnesses, diagnose failures, and prepare portable Windows releases.
The author reviewed the generated code and outputs, directed revisions, and
made the final decisions about methodology, algorithm behavior, interface
design, and release readiness.

Codex and GPT-5.6 were development tools, not runtime dependencies. ThemeForge
analyzes imported transcripts locally and does not send transcript text to
Codex, GPT-5.6, or another online model.

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
- v1.1.0: Methodology update — text-based PDF input, optional codebook
  matching, stronger transcript parsing, quote offsets, and validation harness
  checks.
- v1.2.0: Methodology update — optional local sentence-transformer embeddings
  and Korean morphology, plus persistent projects, manual selection coding,
  theme and quote editing, hierarchy, memos, coding stripes, and transcript
  and codebook removal.
- v1.2.1: Fixed CLI completion output for Korean and other Unicode report paths
  when Windows uses a legacy console encoding.
- v1.3.0: Methodology update — ranked "find more like this" retrieval from a
  selected theme or quote, uncoded-span exclusion, source highlighting, and
  speaker-preserving acceptance through the existing undo and autosave
  workflow.
- v1.4.0: Methodology update — matrix views, intercoder comparison with
  Cohen's kappa, coder identity, and exportable audit-event metadata for team
  coding review.
- v1.5.0: Methodology update — stronger interviewer exclusion and fail-closed
  real-data validation with quote-level precision and recall, published
  theme-table extraction, and multi-coder reliability checks.
- Later: REFI-QDA exchange and DOCX report export.

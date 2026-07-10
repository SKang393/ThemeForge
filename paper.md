---
title: 'ThemeForge: A qualitative coding workspace for education interview and focus group data'
tags:
  - Python
  - qualitative research
  - thematic analysis
  - education
  - interview transcripts
authors:
  - name: Sungwoo Kang
    orcid: 0000-0002-6449-712X
    affiliation: 1
affiliations:
  - name: Purdue University
    index: 1
date: 10 July 2026
bibliography: paper.bib
---

# Summary

ThemeForge is an open-source desktop and command-line workspace for
researcher-led thematic coding of qualitative transcript data in education
research. It supports plain-text, Markdown, CSV, DOCX, RTF, and text-based PDF
files; accepts single or multi-file interview and focus-group datasets;
preserves speaker labels from colon and timestamped transcript formats, source
file names, and transcript line numbers; and exports suggested themes with
linked quote evidence. Beyond first-pass theme suggestion, ThemeForge provides
a persistent coding workspace: researchers save projects, code arbitrary
transcript selections, rename, merge, split, and reorganize themes into
hierarchies, attach memos, and review coding stripes beside the transcript.
Team-oriented rigor tools add theme-by-source and theme-by-speaker matrix
views, two-coder agreement comparison, and an exportable coding audit trail.

The software is intentionally researcher-led. It does not claim to automate
interpretation, replace memo writing, or produce final findings. Instead, it
uses local lexical and, optionally, local semantic representations to suggest
candidate themes, rank quote evidence, and expose validation metadata that
helps researchers decide what to review next. The base application is written
in Python with no runtime dependencies beyond the standard library and
Tkinter, and ships as a Windows portable build for non-technical users. An
optional, source-install NLP tier adds local sentence-embedding similarity and
Korean morphological tokenization; with or without it, no transcript text is
sent to an online service.

# Statement of Need

Qualitative education research often depends on systematic reading of
interview and focus-group transcripts to identify recurring patterns in how a
specific group of participants understands a phenomenon. In applied research
teams, this work typically includes transcript review, initial coding, theme
development, quote selection, literature-based interpretation, and discussion
of how participant perspectives show similarities, differences, strengths, and
needs. General qualitative data analysis packages can support this process, but
they may be too broad, require substantial setup, or assume manual coding
workflows that are hard to adopt in small education teams.

ThemeForge addresses a narrower need: a local, low-friction tool for organizing
transcripts into candidate themes and traceable quote evidence before and
during human coding. It makes an education research process easier to teach,
demonstrate, and practice. Instructors can use ThemeForge to show how
computational assistance can support qualitative analysis without treating the
computer output as a finding. Students can compare suggested themes against
full transcript context, revise labels, reject weak groupings, and discuss how
evidence should be used in a trustworthy thematic analysis.

# Approach to Thematic Coding

ThemeForge operationalizes thematic analysis as an iterative, researcher-owned
process [@braun2006] in five stages, each of which keeps its intermediate
products visible and editable rather than hidden behind a single score.

**Unitization.** Transcript files are first converted into consistent meaning
units. Speaker labels are preserved when turns follow `Speaker: text`,
Otter/Zoom-style `Speaker  0:03`, or Word-style `Speaker 0:03` formats;
interviewer, moderator, consent, and procedure passages are filtered from
coding; and transcription boilerplate, timestamp rows, and table-export noise
are removed. Each resulting unit keeps its speaker, source file, line number,
and character offsets. Explicit, consistent unitization is treated as a
precondition for defensible coding and later agreement analysis, following
methodological guidance for semistructured interview coding [@campbell2013].

**Inductive theme suggestion.** Meaning units are represented with local term
and phrase weighting in the vector space model [@salton1988], clustered with a
bounded agglomerative procedure so long transcripts remain usable on ordinary
computers, and labeled with class-based TF-IDF phrase scores, in which terms
are weighted by their distinctiveness for one cluster against all others
[@grootendorst2022]. Every suggested theme lists its full quote evidence with
per-quote similarity, so a suggestion can be audited quote by quote.

**Deductive priorities.** Researchers may enter a central theme — a short
research focus such as accessibility or parent burden — which is expanded with
transcript-learned related terms and scored with a BM25-style relevance
function [@robertson2009] so indirectly worded evidence still ranks for
review. Alternatively or additionally, an imported codebook (theme names,
descriptions, and example quotes) ranks transcript quotes against
researcher-defined codes. Combining these deductive priorities with the
data-driven clustering supports hybrid inductive–deductive designs
[@fereday2006].

**Researcher-led coding.** All machine suggestions land in an editable
workspace persisted as a `.tfproj` project file. Researchers code arbitrary
transcript selections to existing or new themes; rename, merge, split, and
arrange themes into hierarchies; reassign, uncode, and re-bound quotes; attach
memos to themes, quotes, and documents; and undo or redo any step. Manually
touched themes are preserved when analysis is re-run. A similarity search
("find more like this") retrieves ranked uncoded passages related to a
selected theme or quote, using local sentence-embedding cosine similarity
[@reimers2019] when the optional NLP tier is installed and normalized TF-IDF
cosine similarity otherwise; scores are presented as ranking aids, not
probabilities or automatic codes.

**Rigor and audit.** Each suggested theme carries evidence count, source
coverage, speaker coverage, central-theme alignment, and an explicit
researcher-review status, exported to Markdown, CSV, and JSON so teams can
audit how a theme was assembled. This follows recommendations that
trustworthiness depends on transparent analytic steps and an audit trail
[@nowell2017]: the workspace additionally records coder identity and
persistent, exportable coding audit events. Matrix views summarize coded
evidence by source and by speaker, and a two-project comparison reports
per-theme percent agreement and Cohen's kappa [@cohen1960] with prevalence
warnings and side-by-side disagreements when two coders code identical shared
transcript text on fixed shared meaning units. Agreement metrics are framed as
discussion aids whose appropriateness depends on the study design and
epistemological stance [@oconnor2020], not as proof of interpretive validity.

# Use in Teaching and Research

ThemeForge can be used in qualitative methods courses, research practica, or
project teams that analyze education interview data. A typical classroom
activity asks learners to import a short transcript set, enter a central focus
such as accessibility or parent burden, generate suggested themes, and then
critique the output. Learners can inspect whether each suggested theme has
enough evidence, whether quotes come from multiple participants or only one
speaker, and whether the selected quotations actually support the label. This
workflow makes visible that thematic analysis is not merely counting repeated
words; it is a process of interpreting meaning across transcript context
[@guest2012].

The same projects support a team exercise in reliability: two students code
the same transcripts in separate project files, compare per-theme agreement
and kappa, and discuss disagreements side by side before revising the shared
codebook [@campbell2013; @oconnor2020]. For research teams preparing an
initial coding meeting, ThemeForge can create a starting report listing
candidate themes and all matching quotes, while the team retains
responsibility for renaming, merging, splitting, rejecting, and interpreting
themes. ThemeForge therefore frames automation as assistance for organization,
not as a substitute for qualitative judgment.

# Availability

The ThemeForge source code, tests, documentation, and Windows portable build
script are available in the public repository [@themeforge]. The software is
released under the Apache License 2.0. The archived release is available
through Zenodo at <https://doi.org/10.5281/zenodo.20653169>.

# References

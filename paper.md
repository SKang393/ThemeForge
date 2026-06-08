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
    affiliation: 1
affiliations:
  - name: Independent education researcher
    index: 1
date: 7 June 2026
bibliography: paper.bib
---

# Summary

ThemeForge is an open-source desktop and command-line tool for first-pass
organization of qualitative transcript data in education research. It supports
plain-text, Markdown, CSV, DOCX, and RTF files; accepts single or multi-file
interview and focus-group datasets; preserves speaker labels, source file
names, and transcript line numbers; and exports suggested themes with linked
quote evidence. ThemeForge is designed for researchers who conduct open-ended
interviews, structured interviews, focus groups, or related qualitative studies
and need a local, reviewable way to move from long transcript files to candidate
theme categories and supporting participant quotations.

The software is intentionally researcher-led. It does not claim to automate
interpretation, replace memo writing, or produce final findings. Instead, it
uses local lexical and contextual grouping to suggest candidate themes, rank
quote evidence, and expose validation metadata that helps researchers decide
what to review next. The current release is written in Python with no runtime
dependencies beyond the standard library and Tkinter, and includes a Windows
portable build path for non-technical users who should not need to install a
large NLP stack.

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
transcripts into candidate themes and traceable quote evidence before final
human coding. This contribution fits JOSE as open educational software because
it makes an education research process easier to teach, demonstrate, and
practice. Instructors can use ThemeForge to show how computational assistance
can support qualitative analysis without treating the computer output as a
finding. Students can compare suggested themes against full transcript context,
revise labels, reject weak groupings, and discuss how evidence should be used
in a trustworthy thematic analysis.

# Design and Implementation

ThemeForge grew from a reconstruction of the AutoPhrase phrase-mining project,
which was designed for extracting quality phrases from massive corpora
[@liu2015; @shang2018]. ThemeForge keeps the value of phrase discovery but
changes the purpose: rather than optimize phrase mining as an NLP benchmark, it
supports practical thematic coding for qualitative education studies.

The analysis pipeline begins by converting transcript files into consistent
quote units. Speaker labels are preserved when turns follow a `Speaker: text`
format, long turns are split into sentence-level evidence units, and multi-file
inputs retain source names and line references. The system then builds local
term and phrase representations, including unigram and short phrase features,
and groups quote units using bounded clustering so longer transcripts remain
usable on ordinary computers. Researchers may enter an optional central theme,
which acts as a deductive priority during ranking while still allowing
data-driven groupings to emerge.

The implementation translates qualitative rigor into visible review aids rather
than hidden scores. Each suggested theme includes evidence count, displayed
quote count, source coverage, speaker coverage, central-theme alignment, a
color marker, keywords, and a review status. These fields are exported to
Markdown, CSV, and JSON so instructors and research teams can audit how a
theme was assembled. This design follows the view of thematic analysis as an
iterative method for identifying and interpreting patterns in qualitative data
[@braun2006]. It also reflects recommendations that trustworthiness depends on
transparent analytic steps, an audit trail, and enough detail for readers or
team members to judge the credibility of interpretations [@nowell2017].

ThemeForge also supports hybrid inductive and deductive analysis. The optional
central theme helps prioritize research-question-relevant evidence, while the
clustering and keyword steps still surface repeated transcript patterns. This
matches applied approaches in which theory-informed codes and data-driven
codes are developed together [@fereday2006]. The tool additionally keeps quote
units explicit because semistructured interview coding depends on consistent
unitization and careful review of how coded units support a code or theme
[@campbell2013].

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

The software is also useful for research teams preparing an initial coding
meeting. ThemeForge can create a starting report listing candidate themes and
all matching quotes, while the team retains responsibility for renaming,
merging, splitting, rejecting, and interpreting themes. The explicit review
status is important because reliability and agreement procedures are separate
from theme generation and may or may not be appropriate depending on the study
design and epistemological stance [@oconnor2020]. ThemeForge therefore frames
automation as assistance for organization, not as a substitute for qualitative
judgment.

# Availability

The ThemeForge source code, tests, documentation, and Windows portable build
script are available in the public repository [@themeforge]. The software is
released under the Apache License 2.0. A permanent archive DOI should be added
to this paper before final JOSE submission after depositing the release with a
service such as Zenodo or figshare.

# Acknowledgements

ThemeForge was reconstructed from an AutoPhrase fork and acknowledges the
original AutoPhrase authors and project [@liu2015; @shang2018]. The present
software direction, qualitative-study requirements, and education-focused
workflow were developed for ThemeForge.

# References

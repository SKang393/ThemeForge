# Public Education Transcript Datasets for QA

These datasets are candidates for later testing. Confirm each repository's license
and access terms before bundling or redistributing any transcript text.

## Candidate Datasets

- FitQuest qualitative dataset, University of Edinburgh DataShare.
  - URL: https://www.research.ed.ac.uk/en/datasets/fitquest-qualitative-dataset
  - Notes: Includes paired child interviews, teacher interviews, and observation notes from a school physical education study.
- Neurodiversity and Cognitive Load in Online Education, King's College London.
  - URL: https://kclpure.kcl.ac.uk/portal/en/datasets/focus-group-transcripts-neurodiversity-and-cognitive-load-in-onli
  - Notes: Focus group transcripts about online education experiences.
- Interview Transcripts on Conditions For Study Success, University of Stuttgart DaRUS.
  - URL: https://darus.uni-stuttgart.de/dataset.xhtml?persistentId=doi%3A10.18419%2Fdarus-3683
  - Notes: Higher education interview transcripts on study success requirements.
- Learner Autonomy, Collaboration, and Identity in Open and Distance Education, Mendeley Data.
  - URL: https://data.mendeley.com/datasets/yd873nzztp
  - Notes: Semi-structured interview transcripts about open and distance education.
- Interview transcripts with initial thematic analysis, figshare / De Montfort University.
  - URL: https://figshare.com/articles/dataset/Interview_transcripts_with_initial_thematic_analysis_/28768148
  - Notes: CC BY 4.0 DOCX interview transcripts about higher education retention, student finance, and faith-based identity.

## Local QA Completed

- DaRUS study-success interviews.
  - Dataset: https://doi.org/10.18419/DARUS-3683
  - Local files used: ignored `tmp/public_dataset/darus-3683/*.rtf`
  - Purpose: multi-file RTF analysis, source-file preservation, and higher
    education theme extraction.
- King's College London neurodiversity focus groups.
  - Dataset: https://doi.org/10.18742/24884196
  - Local files used: ignored `tmp/public_dataset/kcl/neurodiversity_focus_groups.txt`
  - Purpose: focus-group analysis around neurodiversity, cognitive load, and
    online education.
- De Montfort University / figshare interview transcripts.
  - Dataset: https://figshare.com/articles/dataset/Interview_transcripts_with_initial_thematic_analysis_/28768148
  - Local files used: ignored `tmp/public_dataset/figshare_dmu/INT_1.30.01.24.docx`
  - Purpose: DOCX import, quote evidence, and higher education retention themes.

## QA Use

Use these as external validation data only. Do not commit raw transcripts into
this repository unless the dataset license clearly allows redistribution and the
data are already anonymized for that purpose. Local QA outputs can be regenerated
from the released app and public dataset citations.

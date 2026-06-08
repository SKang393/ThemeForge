# Contributing to ThemeForge

ThemeForge is focused on practical thematic coding and quote-evidence review for
education interviews, focus groups, and related qualitative transcript data.
Contributions should support that purpose.

## Report Issues

Open a GitHub issue when you find a problem. Include:

- ThemeForge version and operating system.
- Whether you used the desktop app or command line.
- Transcript file type, such as TXT, Markdown, CSV, DOCX, or RTF.
- Steps to reproduce the problem.
- Expected and actual behavior.

Do not paste confidential or identifiable transcript text into public issues.
Use a short synthetic example that shows the same problem.

## Request Features

Feature requests are most useful when they describe a qualitative research or
teaching workflow. Include the task you are trying to complete, the type of
transcript data involved, and how the feature would help researchers review
themes or quote evidence.

## Submit Changes

For code or documentation changes:

1. Keep the change narrowly tied to ThemeForge's qualitative-coding purpose.
2. Avoid new runtime dependencies unless they are necessary for the workflow.
3. Add or update tests for behavior changes.
4. Run the test suite before opening a pull request:

```powershell
python -m unittest discover -s tests -v
```

## Seek Support

Use GitHub issues for support questions, installation problems, unclear output,
or documentation gaps. Keep transcript examples synthetic or publicly reusable
unless the dataset license clearly allows redistribution.

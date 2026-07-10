# ThemeForge Release Plan

This is the canonical release policy for ThemeForge. Release automation and
coding agents must follow this document; release-related goal files should
reference it rather than restating the rules.

## Versioning scheme

ThemeForge versions are `x.y.z`, where each component is an integer from 0 to
99.

- **Default: every release increments `z` by 1** — fixes, documentation, UI,
  packaging, and workspace features that do not change how themes or quotes
  are computed. The release after `1.0.0` is `1.0.1`, then `1.0.2`, and so on.
- **Methodology updates increment `y` (and reset `z` to 0).** A methodology
  update changes how the program performs thematic coding or its analysis of
  coding: transcript unitization/parsing, clustering, theme labeling, focus or
  codebook matching, similarity/embedding methods, tokenization for a
  language, or reliability/agreement computation. Example: adding semantic
  retrieval was `1.3.0`, not `1.2.2`.
- **Rollover rule:** a component never exceeds 99. When `z` would pass 99, it
  rolls over (`1.0.99` → `1.1.0`); when `y` would pass 99, it rolls over
  (`1.99.99` → `2.0.0`).
- **Milestone promotions** (for example `0.x.y` → `1.0.0`) are deliberate,
  owner-decided `x` bumps, reserved for maturity milestones or
  project-format-breaking changes.

Skipping numbers is not allowed. When unsure whether a change is a
methodology update, ask the owner before tagging.

## Release identity

- Tag: `vX.Y.Z` on the release commit.
- GitHub release title: `ThemeForge vX.Y.Z`.
- Release notes file: `release_notes/vX.Y.Z.md`, beginning with
  `# ThemeForge vX.Y.Z`. The GitHub release body is this file's content.
- The release commit message is `Prepare ThemeForge X.Y.Z release`.

## Version metadata that must stay in sync

Every release updates all of the following to the same version before tagging
(enforced by `tests/test_release_metadata.py`):

1. `VERSION`
2. `pyproject.toml` (`project.version`)
3. `themeforge/__init__.py` (`__version__`)
4. `scripts/build_windows_portable.ps1` default `$Version`
5. `README.md` current version and roadmap entry
6. `release_notes/vX.Y.Z.md`

## Release gates

All gates must pass on the release commit, in order:

1. `python -m unittest discover -s tests -v` — full suite green.
2. `python -m compileall -q themeforge tests scripts` — exit 0.
3. `python scripts/validate_against_codebooks.py --root validation` — no
   name-leak or transcript-artifact findings (requires the local, uncommitted
   `validation/` datasets).
4. Portable build: `scripts/build_windows_portable.ps1` produces
   `release/ThemeForge-X.Y.Z-windows-portable.zip`.
5. Packaged smoke: the built `ThemeForge.exe` launches and the release's
   headline workflows are exercised in the packaged GUI.

## Publishing steps

1. Commit the release ("Prepare ThemeForge X.Y.Z release") and push.
2. Tag `vX.Y.Z` on that commit and push the tag.
3. Create the GitHub release with `--verify-tag`, the standard title, and the
   notes file.
4. Attach the portable zip for every minor and major release. Patch releases
   attach a zip when the fix changes runtime behavior; documentation-only or
   metadata-only patches may be notes-only.
5. Only the newest release is marked "Latest" (use `--latest=false` when
   backfilling or editing older releases).

## Historical record

- `v0.1.0`–`v0.9.0` predate this policy and the repository history reset.
  Their release-notes files remain in `release_notes/` as the archive; they
  have no tags or GitHub releases. Do not fabricate tags on guessed commits.
- On 2026-07-10 the post-1.0.0 releases were renumbered to follow this policy.
  Final mapping (original tag → current tag, same commits):
  - `v1.2.0` → `v1.0.1` (also folds unreleased internal notes 1.1.0–1.1.2)
  - `v1.2.1` → `v1.0.2`
  - `v1.2.2` → `v1.0.3`
  - `v1.3.0` → `v1.1.0` (methodology: parsing overhaul, codebook matching,
    PDF input, quote offsets, validation harness)
  - `v1.4.0` → `v1.2.0` (methodology: optional local embeddings and Korean
    morphology; also projects and manual coding)
  - `v1.4.1` → `v1.2.1`
  - `v1.5.0` → `v1.3.0` (methodology: semantic find-more-like-this retrieval)
  - unreleased 1.6.0 work → pending `1.4.0` (methodology: matrix queries,
    intercoder agreement with Cohen's kappa, audit events)
  Note: the current tag names `v1.2.0` and `v1.2.1` refer to different
  commits than the original tags of the same names; the mapping above is the
  source of truth. Builds attached to renumbered releases report their
  original internal version in the About dialog; this is expected for
  pre-renumber artifacts.
- From `v1.0.0` onward, every published version has a tag, a notes file, and a
  GitHub release, and this policy applies in full.

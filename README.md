# ThemeForge

ThemeForge is a desktop tool for qualitative coding. Apply a code to a passage by hand, find passages that match a codebook, or explore suggested themes. The app keeps the source and character range of each new coded passage alongside the text. You decide whether a suggested quotation supports a theme and record your reason.

## Open the app

**Windows portable build.** Extract the entire ZIP, then open `ThemeForge.exe` in the extracted folder.

**From source.** Use Python 3.11 or later with Tk support. Start the app from the folder containing `themeforge_gui.py` with this command.

```sh
python themeforge_gui.py
```

If the window does not open, run `python -m tkinter` to check whether your Python installation can display a Tk window. [Python’s Tkinter guide](https://docs.python.org/3/library/tkinter.html)

## Try one passage

A plain text file can hold one speaker turn per line. Save it as UTF-8, or use a byte order mark for UTF-16 or UTF-32. ThemeForge also reads DOCX, RTF and PDFs with a text layer. PDF import from source requires `pypdf`.

Create `workshop.txt` with these two lines.

```text
Maker: I checked the wheel rim before packing the cart.
Maker: I folded the spare cloth and put it on the shelf.
```

Save the following codebook as `codes.csv`.

```csv
code,definition
Wheel inspection,Checking the wheel rim before packing.
```

1. Click **Choose files** and open `workshop.txt`. Click **Choose codebook** and open `codes.csv`.
2. Enter a name or pseudonym in **Coder name**. Leave **Theme model** on **TF-IDF** and click **Analyze**.
3. Select **Wheel inspection** in **Themes**. Read the highlighted passage in its transcript. In **Quote review**, choose **Supports this theme**, enter your reason and click **OK**. Use **Revisit later** for an undecided match or **Not this match** to decline one.
4. Click **Save project** to write a `.tfproj` file. Next time, choose **Open project** to resume. Running **Analyze** again starts a new review pass.
5. With **Wheel inspection** selected, click **Supported CSV…**, select `workshop.txt` and click **Export supported matches CSV**. A quotation appears here when its span matches the saved transcript and its latest reviewer marked it **Supports this theme**.

For transcripts in different folders, choose **Across folders…**, add files from each folder, and check the paths before clicking **OK**. If two files are named `interview.txt`, the second appears as `interview.txt [2]` in the project and supported CSV.

## Review a suggested passage with the Windows keyboard

Open **Coding review…** beside **Coder name**, press **Ctrl+Shift+R**, or use **Alt+C** followed by **R**. Within the review window, Tab and Shift+Tab move through named controls. Use Space to activate a focused button.

1. Choose **Import transcript…** and **Import codebook…**, enter your name or pseudonym in **Analyst**, then choose **Analyze**. To resume an existing study, choose **Open project…**.
2. Use **Next code** and **Next passage**. Check **Source and location**, **Speaker**, **Excerpt**, **Code**, and **Definition and criteria** before deciding. The character range refers to the imported transcript text.
3. Enter a **Decision reason**, then activate **Support** or **Decline**. **Review status** shows the recorded decision. Save the project. After reopening it, navigate back to the same code and passage to read the saved decision. Recording a later decision keeps earlier reviews in the project history.
4. **Export scoped evidence…** writes the supported matches for the displayed source and code. Select the CSV codebook your collaborator will use and a new output file. Give the collaborator the exported CSV and that exact codebook. Escape closes Coding review and returns to the workspace.

## Review a bilingual study

Open the transcripts, then choose **Speakers for suggestions…**. Check each speaker in each file and use **Apply** before analyzing. An excluded speaker’s words remain in the transcript for context. To correct a speaker label, edit a copy of the source and import it into a separate study. Review the corrected passages again against that snapshot.

To explore without a codebook, select an excerpt, enter a theme name and choose **New code from selection**. Use **Find from quote** to look for related wording. For a codebook pass, put positive and negative criteria in the definition or example using **Include:** and **Exclude:**. The lexical matcher uses the positive wording to retrieve candidates. A passage describing a plan, a denial or a picture can use the same words as a completed action. Read the context before supporting it.

Korean lexical matching recognizes composed and decomposed Hangul. It also compares common particle-marked words with a separately occurring base of at least three Hangul syllables. Short terms, paraphrases and translations may still be missed. Use **Preview coverage** and **Next without suggestion** to inspect omitted passages, or select text directly and choose **Apply codebook entry…**. A manual selection remains unattributed. **Update boundary** keeps an attributed excerpt inside its original speaker turn and clears its old retrieval score. Code a multi-speaker excerpt separately if you need the exchange as a unit.

After **Analyze** or **Preview coverage**, open **Coverage** in the Themes panel to see displayed leads for every codebook entry. Choose a source to see its eligible passage count. A passage can have a lead for one entry while another entry has none. If no passages are eligible, the table marks coverage as unavailable. **Read transcript** opens the full source for manual review, including short turns and text excluded from suggestions.

If a save fails, keep the study open and use **Save as…** with a new filename in a writable folder. Reopen that copy and check the pending decision. A saved project retains its imported transcript text even if the external file later changes.

## Check a quotation

In the supported CSV, find `source_file` and `source_line`. The example quotation begins on line 1. Its character range, `[7, 55)`, starts at character 7 and ends just before character 55, counting from zero. That slice reads “I checked the wheel rim before packing the cart.” Repeated wording elsewhere has a different address.

Use the transcript text saved in the project when checking offsets. For Word or PDF input, use the extracted text. Its SHA-256 digest, computed over the UTF-8 text, should match `source_sha256`. The supported CSV already records who confirmed the quotation, when, and why in `match_review_actor`, `match_review_time_utc` and `match_review_reason`.

## Compare repeat visits by case

In **Rigor tools → By case**, select a transcript, enter or choose a case label, and choose **Assign case**. Assign the same label to each visit that belongs to that case. To move a visit, assign its transcript a different label. **Clear assignment** returns it to the unassigned sources. Use this workflow for transcripts that each belong to a single case. A shared speaker name or a case mentioned in a memo does not establish membership.

Enter your **Coder name** to see your current reviews. Expand a theme and case to inspect the source, line and character range for each passage. Open a reviewed passage to see it in context with its decision reason. A stored passage without a current review still needs interpretation. An empty cell does not establish that the theme was absent, and a declined match records a review decision without classifying it as a counterexample.

Save a grouped study as a **protected master**. Earlier versions of ThemeForge cannot open this format. When you first save case assignments in an older project, choose an unused filename for the master. The existing source, speaker and supported-evidence reports are available in either workflow.

### Review a grouped study with an older version

1. Open the saved master. Under **Rigor tools → By case**, choose **Save older-version copy…** and give the working copy a new name. Keep the master for recovery. The working copy includes the study and review history, with case assignments omitted.
2. In the older version, open the working copy. You can add review decisions on existing passages and use **Save as…** in another folder. To retain the association with the original study, keep its transcripts, memos, codebook, analysis, settings and earlier audit history intact.
3. Open the returned copy in this version. Under **By case**, choose **Recover case assignments…** and select the master used for that copy. Check the displayed source-to-case assignments and confirm before choosing an unused output filename. Open the recovered project to continue working.

Recovery checks the complete saved study and original audit history and carries forward verified additional passage reviews and the returned copy’s active coder. The selected master supplies the case assignments. Two masters may contain identical evidence and different assignments, so check the mapping even when the evidence matches. If an earlier save already stripped the labels, a retained grouped project from the earlier case-aware version can also supply them. Without a retained mapping, assign cases manually using your study records.

## Compare two readers' decisions

Save each first pass as a separate project. Open one and use **Save as…** to create a discussion copy before changing a decision. In **Rigor tools → Intercoder**, choose the other analyst's project, select the two reviewers and click **Next review disagreement**. Read both reasons and the highlighted passage. Close Rigor tools, record the revised decision and its reason in the discussion copy, then reopen it to check the result.

A different codebook row number can still identify the same reviewed match. The comparison checks the saved entry and the exact transcript occurrence. It reports material changes to a definition or example separately. Put inclusion and exclusion guidance in the definition or example so both projects retain it.

To share selected decisions, enter the reviewer's name in **Coder name**. In **Rigor tools → Audit log**, select just the transcript you want and click **Choose review history…**. Select decisions by theme and character range, then check the quotes and reasons in the preview before saving. The dialog identifies the transcript and reviewer above the preview. Include that context separately if the recipient needs it.

For a full-project audit, **Export audit CSV** includes all sources and coders, even when one transcript is selected. It can include declined passages and earlier reasons. To trace a supported row in that file, match `match_review_target_id` to `target_id`, then check the reviewer, UTC time and reason.

If a collaborator already has a CSV codebook, choose **Export supported CSV with codebook links…** and select their copy. It can contain just the entries you intend to share, in any order. Each supported row must match one entry by code name, definition and examples. The evidence CSV adds the file’s SHA-256 and the entry number shown when that file is opened with **Choose codebook**. The codebook text stays in the separate file.

## Verify a handoff

For each transcript, the sender can select its name in **Supported CSV…**, choose a theme if needed, and use **Export supported CSV with codebook links…**. Send the resulting evidence CSV and the exact shared codebook through the study's approved channel. The recipient needs access to the independently held transcript revision that was coded.

On Windows, open the **Handoff** menu and choose **Verify handoff…** (Alt+H, then V). The **Verify handoff…** button in the top toolbar opens the same checker. To start it directly from the portable folder, run `ThemeForge.exe --verify-handoff`. On other platforms, use **Verify handoff…** in the top toolbar.

Choose **Evidence CSV…**, **Transcript…** and **Codebook CSV…**, then **Verify files**. In the Windows dialog you can also type into **Evidence CSV path**, **Transcript path** and **Codebook CSV path**. Tab moves between controls, and the **Verification result** field receives focus after a check so you can read or select its text. Changing a path clears the previous result. Escape closes a file picker or the checker. Choose **Close** to return to the workspace when you opened the checker there. The check works from the welcome window or an open project. It compares each cited range and line to the imported transcript text and checks the shared codebook's file hash and entry number. A moved or renamed copy can verify when its extracted text is identical. A corrected transcript or changed codebook needs a new review and export against that revision.

The report shows the decision details recorded in the CSV. Compare the displayed SHA-256 for the entire evidence CSV with a digest obtained independently from the sender. A match confirms that the reported reviewers, reasons and row numbers arrived unchanged. Keep the original review projects for examining decision history. File hashes and source spans establish consistency with the files selected for the check. Authorship, the completeness of the shared evidence, and the researcher's interpretation require separate evidence.

## Offline use

The default TF-IDF workflow runs on a CPU without an account or model download. **Local embeddings** is optional. To use it, install `sentence-transformers` and cache the MiniLM model for the selected language in advance. ThemeForge looks only in the local model cache and falls back to TF-IDF if embeddings are unavailable. [Sentence Transformers model-loading guide](https://www.sbert.net/docs/package_reference/sentence_transformer/model.html)

## License

ThemeForge is distributed under the [Apache License 2.0](LICENSE).

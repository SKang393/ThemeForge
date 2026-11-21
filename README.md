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

A plain text file can hold one speaker turn per line. ThemeForge also reads DOCX, RTF and PDFs with a text layer. PDF import from source requires `pypdf`.

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

## Check a quotation

In the supported CSV, find `source_file` and `source_line`. The example quotation begins on line 1. Its character range, `[7, 55)`, starts at character 7 and ends just before character 55, counting from zero. That slice reads “I checked the wheel rim before packing the cart.” Repeated wording elsewhere has a different address.

Use the transcript text saved in the project when checking offsets. For Word or PDF input, use the extracted text. Its SHA-256 digest, computed over the UTF-8 text, should match `source_sha256`. The supported CSV already records who confirmed the quotation, when, and why in `match_review_actor`, `match_review_time_utc` and `match_review_reason`.

To share selected decisions, enter the reviewer's name in **Coder name**. In **Rigor tools → Audit log**, select just the transcript you want and click **Choose review history…**. Select decisions by theme and character range, then check the quotes and reasons in the preview before saving. The dialog identifies the transcript and reviewer above the preview. Include that context separately if the recipient needs it.

For a full-project audit, **Export audit CSV** includes all sources and coders, even when one transcript is selected. It can include declined passages and earlier reasons. To trace a supported row in that file, match `match_review_target_id` to `target_id`, then check the reviewer, UTC time and reason.

If a collaborator already has a CSV codebook, choose **Export supported CSV with codebook links…** and select their copy. It can contain just the entries you intend to share, in any order. Each supported row must match one entry by code name, definition and examples. The evidence CSV adds the file’s SHA-256 and the entry number shown when that file is opened with **Choose codebook**. The codebook text stays in the separate file.

## Offline use

The default TF-IDF workflow runs on a CPU without an account or model download. **Local embeddings** is optional. To use it, install `sentence-transformers` and cache the MiniLM model for the selected language in advance. ThemeForge looks only in the local model cache and falls back to TF-IDF if embeddings are unavailable. [Sentence Transformers model-loading guide](https://www.sbert.net/docs/package_reference/sentence_transformer/model.html)

## License

ThemeForge is distributed under the [Apache License 2.0](LICENSE).

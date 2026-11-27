# GitDoc · Local document version control

English · [한국어](README.ko.md)

GitDoc opens Hangul, Word, PDF, PowerPoint, and Excel files inside a desktop window. Compare versions side by side, make supported edits, and review proposals or merge branches in a local version history. [Project brief and mockups (Korean)](기획서.md)

## GitDoc Hub

The [GitDoc Hub](hub/README.md) is a separate hosted workspace for shared text documents, repository invitations, review, version history, and MCP.

## Getting started

The macOS and Windows desktop window uses Rust and [eframe](https://github.com/emilk/egui/tree/main/crates/eframe). It starts the Python document engine as a child process and talks to it over standard input and output. The desktop app and MCP share the engine and SQLite repository.

Install Python 3.12 and a current stable Rust toolchain with Cargo. On macOS 12 or later, install Xcode Command Line Tools. On Windows, use the MSVC Rust toolchain and the Visual Studio Build Tools C++ desktop development workload.

**macOS**

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt -r requirements-mcp.txt -r requirements-desktop.txt
.venv/bin/python docvers.py open
```

**Windows PowerShell**

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-mcp.txt -r requirements-desktop.txt
.\.venv\Scripts\python.exe docvers.py open
```

`docvers.py open` builds the executable with Cargo on first launch. After updating the source, rebuild with `cargo build --locked --manifest-path desktop/native/Cargo.toml`. On macOS, run `./desktop/build.sh` to build `.build/GitDoc.app` for Finder.

The repository defaults to `data/documents.sqlite3`. Set `DOCVERS_DB` to use a different file and give the desktop app, CLI, and MCP the same value.

## Browser viewer

Select **브라우저에서 보기** in the desktop app to open the current version or comparison in your default browser. GitDoc carries over the selected page or spreadsheet cell.

Use `python docvers.py open --browser` to open the local library. For Codex's built-in browser, use the MCP tool `open_browser_view` or add `--url` to start the local viewer and print its URL without opening the default browser.

```sh
python docvers.py open 1 --version 2 --page 3 --browser --url
python docvers.py open 1 --base 2 --target 3 --browser --url
```

The viewer listens on `127.0.0.1:8796` and uses the local document store. Open its URL in a browser on the same computer. Set `DOCVERS_PORT` if the port is already in use. You can also run `python app.py` in the foreground.

## Version history and comparison

Use the desktop sidebar or the browser's pinned document toolbar to switch branches, inspect version history, and submit a proposal while reading. In the browser, opening and closing an action dialog keeps your place in the preview.

1. Register an original file to create `v1.0.0` on `main`. Open a document or pick a specific version to read it in the app. **Save original** exports the stored file. GitDoc stores the original bytes of supported files up to 30 MiB each by default. Set `DOCVERS_MAX_FILE_MIB` to an integer from 1 to 100 before starting the desktop app, web app, CLI, or MCP server to change the upload limit.
2. Create branches or submit draft revisions. Review each proposed change, then approve or reject it. In **Compare two versions**, choose a different revision from the history to change the target while keeping the chosen base. Select **View one version** to leave comparison.
3. Compare branch heads and their common ancestor. To resolve a conflict, submit a merged file of the same format. Restoring an older version records a new version.
4. In the desktop app or browser, scroll through successive pages or use the page buttons to jump. The page number follows scrolling. Comparisons align pages by content and show each version’s original page number. Below the page controls, use **Previous change** and **Next change** to jump between visual changes. Nearby highlights are merged into at most seven boxes per page. If they cover at least 80% of the page, the viewer highlights the entire page as one change. The total appears after scanning finishes. GitDoc matches spreadsheets by sheet name, so a reordered sheet keeps its identity. You can open a specific sheet and cell address.

| Format | Preview and comparison |
| --- | --- |
| PDF | Render and compare pages side by side |
| HWP, HWPX | Convert to PDF with rhwp |
| DOC, DOCX, DOCM, ODT, PPT, PPTX, PPTM, ODP, ODS | Convert to PDF with LibreOffice |
| XLS, XLSX, XLSM | Compare cell values, merges, and some formatting by sheet and address. Print-layout preview is also available. |
| CSV, TSV | View and compare literal cell values. Compare by row number or by a shared unique header selected in the desktop app or passed as `--row-key`. |

The native app renders pages with PDFium and computes visual changes in its Rust worker. The Python bridge supplies cached PDF paths and document metadata. The browser preview uses Poppler's `pdftoppm`. LibreOffice's `soffice` can be found on PATH or in standard installation locations. For Hangul files, install the [rhwp](https://github.com/edwardkim/rhwp) executable on PATH or at `data/tools/rhwp/rhwp` (`rhwp.exe` on Windows). Use `DOCVERS_PDFTOPPM`, `DOCVERS_SOFFICE`, and `DOCVERS_RHWP` to supply explicit tool paths.

Inserted and deleted pages appear separately. Pages with identical rendered content remain paired after a shift. Changes to printed page numbers are visible changes. Hangul and Office layout depends on the converter and installed fonts. The spreadsheet view shows values and XLSX formulas, with differences in cell values, merged ranges, and some formatting. Use print-layout preview to review charts and printed pages. Original downloads are available for encrypted files or files that fail to render. GitDoc keeps DOCM, XLSM, and PPTM bytes intact and disables macro execution in the conversion profile. ODS uses page comparison. CSV uses commas and TSV uses tabs. Both accept UTF-8, BOM-marked UTF-16, and CP949. To compare reordered rows, choose a common header in the same column position with unique, nonblank values on both sides. The table view supports up to 25,000 fields per version. The original and PDF export remain available for larger tables.

## Automatic version labels

Each document starts at `v1.0.0`. GitDoc measures edits against the selected branch's base version and assigns document-wide labels in issuance order. Labels stay fixed after approval or rejection. Existing history receives display labels while keeping version IDs and original files.

| Edit size | Rule | Example |
| --- | --- | --- |
| Small | Up to 4 changed units | `v1.0.0` → `v1.0.1` |
| Medium | At least 5 changed units, below the large threshold | `v1.0.1` → `v1.1.0` |
| Large | At least 30 changed units and at least half the size of the larger version | `v1.1.0` → `v2.0.0` |

Spreadsheet units are cell values and extracted formulas keyed by sheet and address. Other formats use extracted words and punctuation. Files with identical or unavailable extracted text receive a small version increment. Use the comparison view to inspect visual differences, and MCP's `version_label`, `change_scale`, and `change_basis` to inspect the assigned label and how it was calculated.

## Editing in the app

Open **Edit document** from an approved version and enter changes beside the preview. Saving as a draft opens the comparison for review. Approval advances the branch head. If the head changes while editing, start again from the latest version.

| Format | In-app edits |
| --- | --- |
| HWP, HWPX | Replace text in body and table content with an rhwp 0.9 build that provides text editing |
| DOCX, PPTX | Replace existing text in Word body, tables, headers, footers, and footnotes, or in PowerPoint slides |
| XLSX | Enter text, a number, or a formula in a cell, or clear the value. Excel recalculates formulas based on workbook settings. |
| PDF | Rotate a page or add a note annotation |
| DOC, PPT, XLS, ODT, ODS, ODP, DOCM, XLSM, PPTM, CSV, TSV | Submit a revised file from an external editor |

[office_oxide](https://github.com/yfedoseev/office_oxide) edits DOCX and PPTX text. GitDoc imports its edited XML when the result matches the expected change and formatting. GitDoc's XML editor handles other cases and XLSX cell edits. A replacement spanning multiple Word or PowerPoint runs takes the formatting of the first character. Review the preview for layout and styling.

## Exporting versions

Open **Save** to export the selected version. Every supported format can be saved as PDF. Extra choices depend on the source format.

| Source | Additional editable exports |
| --- | --- |
| HWP or HWPX | The other Hangul format |
| DOC | DOCX and ODT |
| DOCX | ODT |
| ODT, ODS, ODP | DOCX, XLSX, and PPTX, respectively |
| PPT or PPTX | The other PowerPoint format and ODP |
| XLS or XLSX | The other Excel format and ODS |

PDF export uses the app's print layout. ODT, ODP, and ODS exports can be opened in a compatible editor. On macOS and Windows, **Print** opens the system dialog for the selected version so you can choose a printer and page range. In a comparison, **Save** and **Print** act on the target version.

## Local working copies

Replace `/path/to/gitdoc` with this checkout's absolute path.

```sh
alias docvers='/path/to/gitdoc/.venv/bin/python /path/to/gitdoc/docvers.py'
docvers list
docvers checkout 1
docvers branch budget-review
docvers checkout 1 -b budget-review
docvers status
docvers diff
docvers commit -m 'Update budget details'
```

Edit checked-out originals in your usual editor. Branch switching protects uncommitted changes. Use review or merge to apply changes to `main`.

## MCP and the desktop skill

```sh
codex mcp add gitdoc -- "$PWD/.venv/bin/python" "$PWD/mcp_server.py"
```

On Windows, use the absolute paths to `.venv\Scripts\python.exe` and `mcp_server.py`. In a new Codex session, use `search_documents`, `list_branches`, `list_versions`, `read_version`, `compare_versions`, and `compare_branches` to inspect the repository. Use `register_document`, `create_document_branch`, `submit_revision`, `edit_revision`, `review_draft`, `merge_document_branches`, and `restore_document_version` to work with it. Upload tools accept an absolute local path to a user-selected file. For approving, merging, or restoring, use the applicable branch head IDs from the latest lookup.

For CSV and TSV, pass the header name as `row_key` to `compare_versions` to align reordered rows. The generated desktop links open the same alignment.

The [desktop skill](.agents/skills/document-versions-desktop/SKILL.md) describes how to open comparisons. These commands use example document and version IDs. The window shows the version labels, such as `v1.0.1`.

```sh
.venv/bin/python docvers.py open 1 --version 2 --page 2
.venv/bin/python docvers.py open 1 --base 2 --target 5 --page 2 --change 1
.venv/bin/python docvers.py open 1 --base 2 --target 5 --sheet '산출 내역' --cell B4
```

## License

GitDoc is available under the [MIT License](LICENSE). [rhwp](https://github.com/edwardkim/rhwp) is MIT-licensed, [office_oxide](https://github.com/yfedoseev/office_oxide) is dual-licensed under MIT or Apache-2.0, and [lxml](https://github.com/lxml/lxml/blob/master/LICENSES.txt) and [pypdf](https://github.com/py-pdf/pypdf/blob/main/LICENSE) use BSD-family licenses. External tools and libraries retain their own licenses and notices.

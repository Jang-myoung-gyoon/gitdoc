# Desktop PDF editing coverage

The reference is ONLYOFFICE Desktop Editors 10.0.0 for Windows, macOS, and Linux as listed by the vendor on 2026-11-28. The help center covers both web and desktop. Cloud storage, live collaboration and connected services need separate desktop evidence. GitDoc uses its own interface and existing PDFium and lopdf packages.

- [Desktop builds and versions](https://www.onlyoffice.com/download-desktop)
- [PDF editor tabs and reference screens](https://helpcenter.onlyoffice.com/docs/userguides/pdf-editor)
- [Editing text and objects](https://helpcenter.onlyoffice.com/docs/userguides/pdf-editor/basic-operations/edit-pdf)

| Reference area | GitDoc state | Acceptance check |
| --- | --- | --- |
| Home · Edit Text | Partial. One visible top-level text object with an axis-aligned text matrix and a replacement that fits the original bounds. Supports selecting the object on a page preview or by keyboard in the object list. Automated save, re-extract and render verified for standard-font text and rotated pages. | Test duplicates, neighboring objects, crop boxes, rotated individual text, Korean and embedded/subset fonts. For unsupported input, verify that the app rejects the edit without creating a version. |
| Home · font, size, color, case, highlighting, lists, indents, spacing, alignment, direction, columns | Missing | Inspect saved font and text matrices and validate that only the chosen range changes after reopening. |
| Insert · Text boxes, images, shapes, tables, links, watermarks and other page objects | Missing | Add, select, move, resize, restyle and delete real objects. Verify text and link destinations without rasterizing them. |
| Pages · Add, rotate, remove, reorder and manage pages | Partial. Existing blank/external page insertion, deletion and rotation have automated coverage. Reorder, crop and extraction are remaining. | Check mixed-size pages, page order, annotations, and resource references after reopening. |
| Comment · Notes, text callouts, replies, ink, highlights, underline and strikeout | Partial. Existing text-note insertion has automated coverage. | Check annotation geometry, text, reply relation, author, edit and removal in a second viewer. |
| Forms · fields and values | Missing | Use text, check box, radio, list and dropdown fixtures. Confirm appearance and field values after reopening. |
| Redact · selection, application, hidden metadata and attachments | Missing | Independently extract and inspect content streams, attachments and metadata after applying permanent removal. Include failure cases for signed and protected files. |
| File and View · page navigation, search, settings, zoom, print, export and page panels | Partial. GitDoc has current preview, navigation, page search and print paths. | Check shortcuts, focus, page positions and same-format output using desktop fixtures. |
| Collaboration, signatures and plug-ins | Review existing GitDoc version diffs as a separate document workflow. Desktop support for each vendor-only command needs independent confirmation. | Determine local vs hosted behavior. Verify any adopted signature handling using signed fixtures and a separate validator. |

The first slice uses PDFium to update the existing object and regenerate page content, then checks text extraction with PDFium and pypdf and compares rendered pixels outside the affected area. PDF encryption, signatures and interactive forms block this slice. The automation verifies the bridge and saved original-format version. A separate local macOS fixture embedded a subset of AppleGothic and replaced Korean text using characters present in the subset. pypdf re-extracted the new text and preserved the neighboring paragraph. A replacement requiring missing glyphs was rejected. Click-through on macOS, Windows, and the reference editor remain distinct test activities.

Next, add an independently authored Korean PDF fixture with mixed text objects and a subset font, and validate any supported fit or font choice without changing neighboring content.

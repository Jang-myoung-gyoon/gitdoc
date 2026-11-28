# Local browser document viewport

GitDoc v0.1.4 used a capped inner document height below a regular page header, so a long document produced both a browser-page scrollbar and an inner scrollbar. The workbook comparison rendered two independently scrollable panes and copied offsets between them. The earlier full-viewport work on `codex/gitdoc-full-viewport` was outside the v0.1.4 tag.

The detail and comparison routes now use a single document scroller below a one-line toolbar. Two PDF pages share one aligned row. In sheet comparison both tables use the same native vertical and horizontal scroll position. The toolbar keeps the title, version, branch and filename on one line. The full title and filename appear in **더보기**. Longer actions such as change lists and version history open as dialogs with the reader inactive until closed.

Playwright with Chromium loaded synthetic eleven-page PDFs, two-sheet workbooks, and four-page native HWP 5 documents through the normal workspace detail and comparison URLs. Coverage is the visible document scroller client area divided by the visual viewport area, excluding the toolbar and any space consumed by scrollbars.

| Browser client size | PDF | PDF comparison | Sheet | Sheet comparison | HWP | HWP comparison |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 1440×900 | 96.00% | 96.00% | 96.00% | 96.00% | 96.00% | 96.00% |
| 1280×720 | 95.00% | 95.00% | 95.00% | 95.00% | 95.00% | 95.00% |
| 1024×600 | 94.00% | 94.00% | 94.00% | 94.00% | 94.00% | 94.00% |
| 390×844 | 95.73% | 95.73% | 95.73% | 95.73% | 95.73% | 95.73% |
| 320×568 | 93.66% | 93.66% | 93.66% | 93.66% | 93.66% | 93.66% |
| 640×420 | 92.86% | 92.86% | 92.86% | 92.86% | 92.86% | 92.86% |

The regression reads the actual `scrollTop` and toolbar bounding box at the start, during wheel movement, and after an extra wheel at the bottom. It also checks keyboard paging, a mobile touch swipe, long Korean titles and filenames, sheet switching and cell jumps, shared horizontal positioning, PDF page navigation and zoom, version dialogs, and copying a worksheet location.

The HWP test generates 160 Korean paragraphs, verifies the HWP 5 file and page count with rhwp, and waits for Chromium to decode the first and last page images and detect visible text. It checks the exact Korean title and filename, the version and branch, and full values in **더보기**. A 390×844 touch swipe keeps 95.73% of the viewport available to the HWP reader. Set `GITDOC_BROWSER_SCREENSHOTS` to an existing directory to capture the desktop HWP detail and the mobile view after the swipe.

The tests use the [rhwp 0.9.0 CLI](https://github.com/edwardkim/rhwp/releases/tag/v0.9.0). GitDoc packages require a separate rhwp installation for HWP rendering. The browser server finds it on PATH, via `DOCVERS_RHWP`, or in the GitDoc data folder at `tools/rhwp/rhwp` (`rhwp.exe` on Windows).

The Codex browser and the external browser open the same loopback URL. GitDoc does not serve an iframe wrapper, and its response blocks framing with `frame-ancestors 'none'`. The test covers Chromium desktop and mobile emulation. Physical mobile browsers, Safari and Firefox were not tested.

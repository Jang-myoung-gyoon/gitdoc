# Local browser document viewport

The detail and compare routes use a single 36 px toolbar outside their document scrollers. Page comparisons share one synchronized scroller with two columns and a zero-width layout gutter. Spreadsheet comparisons use separate sheet scrollers that synchronize their offsets. Supplemental controls and status appear in the overflow menu. Version history can open from the toolbar while that menu is closed.

The following results were measured in Chrome with Playwright against a loopback GitDoc server and a temporary workspace containing synthetic seven-page PDFs and two-sheet workbooks. The numerator is the visible client area of the document scroller or the sum of the two sheet client areas after clipping to the visual viewport and subtracting any overlapping status or control. The denominator is the visual viewport.

| Browser client size | PDF | PDF comparison | Sheet | Sheet comparison |
| --- | ---: | ---: | ---: | ---: |
| 1440×900 | 96.00% | 96.00% | 96.00% | 96.00% |
| 1280×720 | 95.00% | 95.00% | 95.00% | 95.00% |
| 1024×600 | 94.00% | 94.00% | 94.00% | 94.00% |
| 390×844 | 95.73% | 95.73% | 95.73% | 95.73% |
| 320×568 | 93.66% | 93.66% | 93.66% | 93.66% |

Automation inspected the start, midpoint, and end of scrolling, verified that the browser body and toolbar stayed at their original position, and exercised the page input with Enter, zoom, next-change navigation, worksheet tabs, cell jumps, synchronized sheet offsets, keyboard opening and dismissal of version history, and location copying for PDF text and sheet cells. Safari, Firefox, and physical mobile browsers were not part of this run.

"""Pair pages in order using fingerprints of their rendered content."""

from difflib import SequenceMatcher
from functools import lru_cache
from hashlib import sha256
from threading import Lock

from pypdfium2 import PdfDocument, PdfiumError

_pdfium = Lock()


def thumbnail(page, width, height):
    bitmap = page.render(scale=min(72 / max(width, height), 1), grayscale=True,
                         may_draw_forms=False, draw_annots=True)
    try:
        tiles = bytes(bitmap.buffer)
        return b"".join(tiles[row:row + bitmap.width]
                        for row in range(0, bitmap.stride * bitmap.height, bitmap.stride))
    finally:
        bitmap.close()


@lru_cache(maxsize=48)
def fingerprints(path):
    # PDFium calls in the web process must not run concurrently.
    with _pdfium, PdfDocument(path) as document:
        result = []
        for page in document:
            try:
                width, height = page.get_size()
                if width <= 0 or height <= 0:
                    result.append((None, None))
                    continue
                bitmap = page.render(scale=min(240 / max(width, height), 1),
                                     may_draw_forms=False, draw_annots=True, prefer_bgrx=True)
                try:
                    key = (round(width, 2), round(height, 2), sha256(bitmap.buffer).digest())
                finally:
                    bitmap.close()
                result.append((key, thumbnail(page, width, height)))
            except (PdfiumError, ValueError, OverflowError):
                result.append((None, None))
            finally:
                page.close()
        return tuple(result)


def positional_pages(counts):
    return [[number if number <= count else None for count in counts]
            for number in range(1, max(counts, default=0) + 1)]


def inserted_pages(shorter, longer):
    if len(shorter) >= len(longer):
        return None
    result = []
    position = 0
    for number, key in enumerate(longer, 1):
        if position < len(shorter) and key == shorter[position]:
            position += 1
            result.append([position, number])
        else:
            result.append([None, number])
    return result if position == len(shorter) else None


def near_page_keys(before, after, left, a, right, b):
    """Find one-to-one visual matches within a gap between exact matches."""
    left_keys = [("left", i) for i in range(left, a)]
    right_keys = [("right", j) for j in range(right, b)]
    window = abs((a - left) - (b - right)) + 8
    candidates = [[] for _ in left_keys]
    reverse = [[] for _ in right_keys]
    for i, (before_key, image) in enumerate(before[left:a]):
        if image is None:
            continue
        for j in range(max(0, i - window), min(len(right_keys), i + window + 1)):
            after_key, other = after[right + j]
            if other is None or before_key[:2] != after_key[:2] or len(image) != len(other):
                continue
            # A small printed page number can move without changing the page's identity.
            if sum(abs(x - y) for x, y in zip(image, other)) <= len(image) * 0.24:
                candidates[i].append(j)
                reverse[j].append(i)
    for i, options in enumerate(candidates):
        if len(options) == 1 and len(reverse[options[0]]) == 1:
            right_keys[options[0]] = left_keys[i]
    return left_keys, right_keys


def match_pages(before, after, *, renders=None):
    # Give unreadable pages unique keys so they never anchor an alignment.
    keys = [[key if key is not None else (side, index) for index, key in enumerate(versions)]
            for side, versions in enumerate((before, after))]
    inserted = inserted_pages(*keys)
    if inserted is not None:
        return inserted
    deleted = inserted_pages(*reversed(keys))
    if deleted is not None:
        return [[after, before] for before, after in deleted]
    pairs = []
    left = right = 0
    for a, b, size in SequenceMatcher(None, *keys, autojunk=False).get_matching_blocks():
        # Without an unchanged anchor inside a gap, compare its common span as edits.
        if renders and a > left and b > right:
            gap = match_pages(*near_page_keys(*renders, left, a, right, b))
            pairs.extend([left + i if i else None, right + j if j else None] for i, j in gap)
        else:
            pairs.extend([left + offset + 1 if left + offset < a else None,
                          right + offset + 1 if right + offset < b else None]
                         for offset in range(max(a - left, b - right)))
        pairs.extend([a + offset + 1, b + offset + 1] for offset in range(size))
        left, right = a + size, b + size
    return pairs


def aligned_pages(paths, counts):
    if len(paths) != 2 or not all(paths) or paths[0] == paths[1]:
        return positional_pages(counts)
    try:
        before, after = (fingerprints(path) for path in paths)
    except (PdfiumError, OSError, ValueError):
        return positional_pages(counts)
    if [len(before), len(after)] != list(counts):
        return positional_pages(counts)
    return match_pages([key for key, _ in before], [key for key, _ in after], renders=(before, after))

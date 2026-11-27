"""Pair pages in order using fingerprints of their rendered content."""

from difflib import SequenceMatcher
from functools import lru_cache
from hashlib import sha256
from threading import Lock

from pypdfium2 import PdfDocument, PdfiumError

from store import ANCESTORS, database, find_version

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


def matching_blocks(before, after):
    if len(before) * len(after) > 250_000:
        yield from SequenceMatcher(None, before, after, autojunk=False).get_matching_blocks()
        return
    width = len(after)
    lengths = [[0] * (width + 1) for _ in range(len(before) + 1)]
    for i in range(len(before) - 1, -1, -1):
        row, below = lengths[i], lengths[i + 1]
        for j in range(width - 1, -1, -1):
            row[j] = 1 + below[j + 1] if before[i] == after[j] else max(below[j], row[j + 1])
    i = j = 0
    while i < len(before) and j < width:
        if before[i] == after[j]:
            left, right = i, j
            while i < len(before) and j < width and before[i] == after[j]:
                i += 1
                j += 1
            yield left, right, i - left
        elif lengths[i + 1][j] >= lengths[i][j + 1]:
            i += 1
        else:
            j += 1
    yield len(before), width, 0


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
    for a, b, size in matching_blocks(*keys):
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


def aligned_pages(paths, counts, *, keep=None):
    fallback = counts if keep is None else list(map(len, keep))
    if len(paths) != 2 or not all(paths) or (paths[0] == paths[1] and keep is None):
        return positional_pages(fallback)
    try:
        before, after = (fingerprints(path) for path in paths)
    except (PdfiumError, OSError, ValueError):
        return positional_pages(fallback)
    if [len(before), len(after)] != list(counts):
        return positional_pages(fallback)
    if keep is not None:
        before, after = (tuple(pages[i - 1] for i in selected)
                         for pages, selected in zip((before, after), keep))
    return match_pages([key for key, _ in before], [key for key, _ in after], renders=(before, after))


def page_lineage(db, document_id, version_id, count):
    steps = []
    fields = "id, sha256, base_id, restored_from, merge_parent_id, page_base_count, page_insert_before, page_insert_count"
    current = find_version(db, document_id, version_id, fields)
    while current:
        parent = None
        base, inserted = current["page_base_count"], current["page_insert_count"]
        start = current["page_insert_before"]
        if (base and inserted is not None and count == base + inserted
                and (inserted == 0 or start and 1 <= start <= base + 1)):
            parent = find_version(db, document_id, current["base_id"], fields)
            if parent:
                if inserted:
                    steps.append((current["id"], start, inserted))
                count = base
        if parent is None:
            for candidate in (current["restored_from"], current["base_id"], current["merge_parent_id"]):
                old = find_version(db, document_id, candidate, fields) if candidate else None
                if old and old["sha256"] == current["sha256"]:
                    parent = old
                    break
        if parent is None:
            break
        current = parent
    root = (current["id"] if current else version_id, count)
    pages = [(root[0], i) for i in range(1, count + 1)]
    for version, start, inserted in reversed(steps):
        pages[start - 1:start - 1] = [(version, i) for i in range(1, inserted + 1)]
    return root, pages


def with_recorded_gaps(pairs, keep, counts):
    result = []
    source = []
    next_page = [1, 1]
    for pair in [*pairs, [len(keep[0]) + 1, len(keep[1]) + 1]]:
        mapped = [keep[side][i - 1] if i and i <= len(keep[side]) else None for side, i in enumerate(pair)]
        for side, index in enumerate(pair):
            if index is None:
                continue
            stop = mapped[side] or counts[side] + 1
            for number in range(next_page[side], stop):
                result.append([number, None] if side == 0 else [None, number])
                source.append("recorded")
            next_page[side] = stop + 1
        if any(mapped):
            result.append(mapped)
            source.append("inferred" if None in mapped else None)
    return result, source


def comparison_pages(document_id, ids, paths, counts):
    if len(ids) != 2 or not all(paths):
        pairs = aligned_pages(paths, counts)
        return pairs, [None] * len(pairs)
    with database(readonly=True) as db:
        (left, before), (right, after) = [page_lineage(db, document_id, version, count)
                                         for version, count in zip(ids, counts)]
        if left == right:
            pairs, previous_left, previous_right = [], 0, 0
            # Page identities from the same source stay ordered through recorded edits.
            right_positions = {key: i for i, key in enumerate(after)}
            shared = [(i, right_positions[key]) for i, key in enumerate(before) if key in right_positions]
            for i, j in [*shared, (len(before), len(after))]:
                pairs.extend([page + 1, None] for page in range(previous_left, i))
                pairs.extend([None, page + 1] for page in range(previous_right, j))
                if i < len(before):
                    pairs.append([i + 1, j + 1])
                previous_left, previous_right = i + 1, j + 1
            return pairs, ["recorded" if None in pair else None for pair in pairs]
        keep = []
        for side, (root, pages) in enumerate(((left, before), (right, after))):
            opposite = ids[1 - side]
            inserted = {version for version, _ in pages if version != root[0]}
            forced = {version for version in inserted if db.execute(
                ANCESTORS + "SELECT 1 FROM ancestors WHERE id = ?", (version, opposite)).fetchone()}
            keep.append([i for i, (version, _) in enumerate(pages, 1) if version not in forced])
    if list(map(len, keep)) == list(counts):
        pairs = aligned_pages(paths, counts)
        return pairs, ["inferred" if None in pair else None for pair in pairs]
    pairs = aligned_pages(paths, counts, keep=keep)
    return with_recorded_gaps(pairs, keep, counts)

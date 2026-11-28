"""Carry rendered font faces, sizes, and colors into the native text editor."""

from collections import Counter
from hashlib import sha256
from pathlib import Path
from unicodedata import normalize

from fontTools.ttLib import TTFont, TTLibError
from pypdfium2 import PdfiumError

from font_policy import editor_font_files
from page_text import embedded_text


def compact(text):
    return "".join(char for char in normalize("NFKC", text) if not char.isspace())


def font_spans(text, styles):
    if not styles:
        return []
    spans = []
    offset = 0
    for index, char in enumerate(text):
        matching = compact(char)
        if not matching:
            continue
        if offset >= len(styles):
            break
        style = styles[offset]
        if not spans or tuple(spans[-1][1:]) != style:
            spans.append([0 if not spans else index, *style])
        offset += len(matching)
    return spans


def use_rendered_fonts(objects, pdf, page, directory):
    try:
        lines = embedded_text(pdf, page, styled=True)
    except (OSError, ValueError, PdfiumError):
        return {}
    glyphs = [(letter, box, style, origin, row) for row, line in enumerate(lines)
              for value, box, style, origin in line for letter in compact(value)]
    letters = "".join(glyph[0] for glyph in glyphs)
    requested = {}
    for item in objects:
        text = item.get("text", "")
        if not text:
            continue
        needle = compact(text)
        if not needle:
            continue
        x, y, right, bottom = item.get("ink", item["box"])

        def inside(box):
            return (x-.004 <= (box[0]+box[2])/2 <= right+.004 and
                    y-.004 <= (box[1]+box[3])/2 <= bottom+.004)

        matches = []
        start = letters.find(needle)
        while start >= 0:
            candidate = glyphs[start:start+len(needle)]
            if all(inside(glyph[1]) for glyph in candidate):
                matches.append(candidate)
            start = letters.find(needle, start+len(needle))
        if len(matches) == 1:
            matched = matches[0]
            item["fonts"] = font_spans(text, [glyph[2] for glyph in matched])
            item["baselines"] = baselines(text, matched)
            if "hangul_format" in item:
                boxes = []
                offset = 0
                for index, char in enumerate(text):
                    length = len(compact(char))
                    group = matched[offset:offset + length]
                    offset += length
                    if group:
                        left, top, right, bottom = zip(*(glyph[1] for glyph in group))
                        boxes.append([index, [min(left), min(top), max(right), max(bottom)]])
                item["glyph_boxes"] = boxes
        else:
            close = Counter(glyph[2] for glyph in glyphs if inside(glyph[1]))
            if close:
                item["fonts"] = [[0, *close.most_common(1)[0][0]]]
        east_asian = any("\uac00" <= char <= "\ud7a3" for char in text)
        for _, name, _, _ in item.get("fonts", ()):
            requested[name] = requested.get(name, False) or east_asian
    return {name: single_face(source, directory) for name, source in editor_font_files(requested).items()}



def baselines(text, glyphs):
    if len(glyphs) != sum(len(compact(char)) for char in text):
        return []
    starts = []
    seen = set()
    offset = 0
    for index, char in enumerate(text):
        length = len(compact(char))
        if not length:
            continue
        _, _, _, origin, row = glyphs[offset]
        offset += length
        if origin is not None and row not in seen:
            seen.add(row)
            starts.append([index, *origin])
    return starts

def single_face(source, directory):
    path = Path(source["path"])
    if path.suffix.lower() not in (".ttc", ".otc"):
        return source
    # Large font collections repeat the full file for each face in egui.
    key = sha256(f"{path}:{source['index']}".encode()).hexdigest()
    output = Path(directory) / f"{key}.font"
    try:
        if not output.is_file():
            output.parent.mkdir(parents=True, exist_ok=True)
            with TTFont(path, fontNumber=source["index"], lazy=True, recalcTimestamp=False) as font:
                font.save(output)
        return dict(source, path=str(output), index=0, size=output.stat().st_size)
    except (OSError, KeyError, UnicodeError, ValueError, TTLibError):
        output.unlink(missing_ok=True)
        return source

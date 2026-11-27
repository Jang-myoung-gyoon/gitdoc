"""Position selectable text on the same crop and rotation as the page image."""

import csv
import json
import os
import tempfile
from ctypes import c_double, c_int, c_uint, create_string_buffer
from functools import lru_cache
from io import BytesIO, StringIO
from pathlib import Path
from threading import BoundedSemaphore

from PIL import Image
from pypdfium2 import PdfDocument, PdfiumError, raw

from preview import PDFIUM_LOCK, PreviewError, executable, preview_png, run_converter
from store import ROOT

OCR_WORKERS = BoundedSemaphore(1)


def bounds(line):
    boxes = [box for _, box in line]
    return [min(box[0] for box in boxes), min(box[1] for box in boxes),
            max(box[2] for box in boxes), max(box[3] for box in boxes)]


def printable_lines(lines):
    return [line for line in lines if any(glyph[0].strip() for glyph in line)]


def embedded_text(path, number, *, styled=False):
    with PDFIUM_LOCK, PdfDocument(path) as document:
        page = document[number - 1]
        text = page.get_textpage()
        lines, line = [], []
        x, y = c_int(), c_int()
        baseline_x, baseline_y = c_double(), c_double()
        flags = c_int()
        color = (c_uint(), c_uint(), c_uint(), c_uint())
        font_buffer = create_string_buffer(512)

        def to_device(left, bottom, right, top):
            coords = []
            for px, py in ((left, bottom), (right, top)):
                if not raw.FPDF_PageToDevice(page, 0, 0, 10000, 10000, 0, px, py, x, y):
                    return None
                coords.append((x.value, y.value))
            (a, b), (c, d) = coords
            box = tuple(min(10000, max(0, value)) / 10000 for value in (min(a, c), min(b, d), max(a, c), max(b, d)))
            return box if box[2] > box[0] and box[3] > box[1] else None

        for index in range(text.count_chars()):
            code = raw.FPDFText_GetUnicode(text, index)
            if code in (10, 13):
                if line:
                    lines.append(line)
                    line = []
                continue
            if not 32 <= code <= 0x10FFFF or 0xD800 <= code <= 0xDFFF or code == 0xFFFD:
                continue
            if raw.FPDFText_HasUnicodeMapError(text, index) == 1:
                continue
            value = chr(code)
            try:
                box = to_device(*text.get_charbox(index, loose=True))
            except PdfiumError:
                box = None
            if box is None and value.isspace() and line:
                box = line[-1][1]
            if box is not None:
                glyph = [value, box]
                if styled:
                    size = round(raw.FPDFText_GetFontSize(text, index), 2)
                    length = raw.FPDFText_GetFontInfo(text, index, font_buffer, len(font_buffer), flags)
                    font = font_buffer.value.decode("utf-8", errors="replace") if 1 < length <= len(font_buffer) else ""
                    rgb = tuple(int(channel.value) for channel in color[:3]) if raw.FPDFText_GetFillColor(text, index, *color) else None
                    origin = None
                    if (raw.FPDFText_GetCharOrigin(text, index, baseline_x, baseline_y)
                            and raw.FPDF_PageToDevice(page, 0, 0, 10000, 10000, 0,
                                                     baseline_x.value, baseline_y.value, x, y)):
                        origin = (x.value / 10000, y.value / 10000)
                    glyph.extend(((font, size, rgb), origin))
                line.append(glyph)
        lines.append(line)
    return printable_lines(lines)


def spread(text, box):
    left, top, right, bottom = box
    if not text:
        return []
    step = (right - left) / len(text)
    return [[value, [left + i * step, top, left + (i + 1) * step, bottom]]
            for i, value in enumerate(text)]


@lru_cache(maxsize=4)
def tesseract_languages(command):
    data = run_converter([command, "--list-langs"], ROOT).decode("utf-8", errors="replace")
    available = set(data.splitlines())
    configured = os.environ.get("DOCVERS_OCR_LANGUAGES", "kor+eng").split("+")
    return "+".join(language for language in configured if language in available)


def tesseract_lines(command, image, size):
    languages = tesseract_languages(command)
    if not languages:
        return []
    data = run_converter([command, str(image), "stdout", "-l", languages, "tsv"], image.parent)
    lines = {}
    for word in csv.DictReader(StringIO(data.decode("utf-8", errors="replace")), delimiter="\t"):
        if word["level"] != "5" or not word["text"].strip():
            continue
        left, top, width, height = (int(word[key]) for key in ("left", "top", "width", "height"))
        box = left / size[0], top / size[1], (left + width) / size[0], (top + height) / size[1]
        key = tuple(word[name] for name in ("page_num", "block_num", "par_num", "line_num"))
        line = lines.setdefault(key, [])
        if line:
            line.append([" ", line[-1][1]])
        line.extend(spread(word["text"], box))
    return list(lines.values())


def recognize(path, number):
    helper = ROOT / ".build/GitDoc.app/Contents/MacOS/gitdoc-ocr"
    try:
        command = str(helper) if helper.is_file() else executable("tesseract", "DOCVERS_TESSERACT", helper)
    except PreviewError:
        return []
    data = preview_png(path, number, prefer_pdfium=True)
    with OCR_WORKERS, tempfile.TemporaryDirectory(prefix="gitdoc-ocr-") as folder:
        image = Path(folder) / "page.png"
        image.write_bytes(data)
        if command == str(helper):
            return json.loads(run_converter([command, str(image)], image.parent))
        with Image.open(BytesIO(data)) as decoded:
            return tesseract_lines(command, image, decoded.size)


def overlaps(a, b):
    overlap = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))
    area = min((box[2] - box[0]) * (box[3] - box[1]) for box in (a, b))
    return area > 0 and overlap >= area * 0.45


@lru_cache(maxsize=64)
def page_text(path, number):
    try:
        embedded = embedded_text(path, number)
    except (PdfiumError, OSError, ValueError):
        embedded = []
    # A short embedded note may be the only real text on an otherwise outlined page.
    if sum(len(line) for line in embedded) >= 120:
        return embedded
    try:
        recognized = printable_lines(recognize(path, number))
    except (PreviewError, OSError, ValueError, KeyError):
        return embedded
    if not embedded or not recognized:
        return embedded or recognized
    known = list(map(bounds, embedded))
    combined = [*embedded, *(line for line in recognized if not any(
        overlaps(bounds(line), box) for box in known))]
    return sorted(combined, key=lambda line: (bounds(line)[1], bounds(line)[0]))

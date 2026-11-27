"""Edit an existing, independently extractable PDFium text object."""

import ctypes
import io
from contextlib import closing
import math
import re

from PIL import Image, ImageChops, ImageDraw
from pypdf import PdfReader
from pypdf.errors import PyPdfError
from pypdfium2 import PdfDocument, PdfiumError, PdfTextObj, raw
from pypdfium2._helpers.bitmap import PdfPosConv

UNSUPPORTED = "이 PDF 본문 개체는 원래 모양을 보존해 수정할 수 없습니다."
PROTECTED = "암호, 서명, 양식 또는 편집 제한이 있는 PDF의 본문은 수정할 수 없습니다."


def checked_reader(data):
    reader = PdfReader(io.BytesIO(data))
    if reader.is_encrypted:
        raise ValueError(PROTECTED)
    root = reader.trailer["/Root"].get_object()
    if "/Perms" in root or "/AcroForm" in root:
        raise ValueError(PROTECTED)
    return reader


def positioned(page, textpage):
    return list(page.get_objects(max_depth=1, textpage=textpage))


def bounds_on_page(page, bounds):
    width, height = (max(1, math.ceil(n)) for n in page.get_size())
    conversion = PdfPosConv(page, (0, 0, width, height, 0))
    left, bottom, right, top = bounds
    points = [conversion.to_bitmap(x, y) for x, y in ((left, bottom), (right, top))]
    xs, ys = zip(*points)
    return [min(xs) / width, min(ys) / height, max(xs) / width, max(ys) / height]


def simple_text(obj):
    if not isinstance(obj, PdfTextObj) or raw.FPDFTextObj_GetTextRenderMode(obj) != 0:
        return None
    matrix = obj.get_matrix()
    # A top-level text run with an axis-aligned text matrix and visible fill.
    if matrix.b != 0 or matrix.c != 0 or matrix.a <= 0 or matrix.d <= 0:
        return None
    value = obj.extract()
    if not value.strip() or len(value) > 2000 or any(ord(char) < 32 or char == "\ufffd" for char in value):
        return None
    return value


def pdf_text_targets(data, number):
    try:
        checked_reader(data)
        with PdfDocument(data) as pdf:
            if type(number) is not int or not 1 <= number <= len(pdf):
                raise ValueError("PDF 페이지 번호를 확인하세요.")
            with closing(pdf[number - 1]) as page, closing(page.get_textpage()) as textpage:
                return [{"index": index, "text": text, "box": bounds_on_page(page, obj.get_bounds())}
                        for index, obj in enumerate(positioned(page, textpage))
                        if (text := simple_text(obj)) is not None]
    except (OSError, PyPdfError, PdfiumError, RuntimeError) as error:
        raise ValueError(UNSUPPORTED) from error


def render(page):
    scale = min(2.0, 1400 / max(page.get_size()))
    with closing(page.render(scale=scale, draw_annots=True)) as bitmap:
        return bitmap.to_pil().convert("RGB").copy()


def check_pixels(before, after, rect):
    if before.size != after.size:
        raise ValueError(UNSUPPORTED)
    delta = ImageChops.difference(before, after)
    width, height = delta.size
    left, top, right, bottom = rect
    limits = (int(left * width) - 4, int(top * height) - 4,
              math.ceil(right * width) + 4, math.ceil(bottom * height) + 4)
    if not delta.crop(limits).getbbox():
        raise ValueError("교체한 글자가 PDF에 표시되지 않습니다.")
    ImageDraw.Draw(delta).rectangle(limits, fill=(0, 0, 0))
    if delta.getbbox():
        raise ValueError("선택한 본문 밖의 모양이 달라져 수정안을 저장하지 않았습니다.")


def replace_pdf_text(data, number, index, original, replacement):
    if (type(number) is not int or type(index) is not int or index < 0 or
            not isinstance(original, str) or not isinstance(replacement, str) or
            replacement == original or not replacement.strip() or len(replacement) > 2000 or
            any(ord(char) < 32 for char in replacement)):
        raise ValueError("바꿀 PDF 본문을 한 줄로 입력하세요.")
    try:
        reader = checked_reader(data)
        output = io.BytesIO()
        with PdfDocument(data) as pdf:
            if not 1 <= number <= len(pdf):
                raise ValueError("PDF 페이지 번호를 확인하세요.")
            with closing(pdf[number - 1]) as page:
                before_image = render(page)
                with closing(page.get_textpage()) as texts:
                    objects = positioned(page, texts)
                    if index >= len(objects):
                        raise ValueError("편집할 PDF 본문을 다시 선택하세요.")
                    obj = objects[index]
                    if simple_text(obj) != original:
                        raise ValueError("편집할 PDF 본문을 다시 선택하세요.")
                    expected = [item.extract() if isinstance(item, PdfTextObj) else None for item in objects]
                    expected[index] = replacement
                    old_box = obj.get_bounds()
                    region = bounds_on_page(page, old_box)
                    utf16 = replacement.encode("utf-16-le") + b"\x00\x00"
                    encoded = (ctypes.c_ushort * (len(utf16) // 2)).from_buffer_copy(utf16)
                    if not raw.FPDFText_SetText(obj, encoded):
                        raise ValueError("원본 글꼴에서 바꿀 문구를 표현할 수 없습니다.")
                    left, bottom, right, top = obj.get_bounds()
                    if (left < old_box[0] - 1 or bottom < old_box[1] - 1.5 or
                            right > old_box[2] + 1 or top > old_box[3] + 1.5):
                        raise ValueError("바꿀 문구가 원래 본문 상자를 벗어납니다. 더 짧게 입력하세요.")
                page.gen_content()
                pdf.save(output, flags=raw.FPDF_NO_INCREMENTAL)
        updated = output.getvalue()
        with PdfDocument(updated) as pdf:
            if len(pdf) != len(reader.pages):
                raise ValueError(UNSUPPORTED)
            with closing(pdf[number - 1]) as page, closing(page.get_textpage()) as texts:
                actual = [item.extract() if isinstance(item, PdfTextObj) else None
                          for item in positioned(page, texts)]
                if expected != actual:
                    raise ValueError("원본 글꼴에서 바꿀 문구를 표시하거나 다시 읽을 수 없습니다.")
                check_pixels(before_image, render(page), region)
        independent = PdfReader(io.BytesIO(updated))
        before = reader.pages[number - 1].extract_text()
        after = independent.pages[number - 1].extract_text()
        candidates = (match.start() for match in re.finditer(re.escape(original), before))
        if not any(before[:start] + replacement + before[start + len(original):] == after
                   for start in candidates):
            raise ValueError("교체한 본문을 다시 읽을 수 없어 수정안을 저장하지 않았습니다.")
        return updated
    except (OSError, PyPdfError, PdfiumError, RuntimeError, UnicodeError) as error:
        raise ValueError(UNSUPPORTED) from error

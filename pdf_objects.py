"""Edit independent GitDoc form XObjects without regenerating original PDF content."""

import ctypes
import io
import math
import re
from contextlib import closing
from copy import copy
from uuid import uuid4

from fontTools import subset
from fontTools.ttLib import TTFont, TTLibError
from PIL import ImageChops, ImageDraw
from pypdf import PdfReader, PdfWriter
from pypdf.errors import PyPdfError
from pypdf.generic import ArrayObject, DecodedStreamObject, DictionaryObject, FloatObject, NameObject, TextStringObject
from pypdfium2 import PdfDocument, PdfiumError, raw
from pypdfium2._helpers.bitmap import PdfPosConv

from font_policy import GENERIC, KOREAN, face_index, family_key, installed_fonts
from pdf_text_edit import checked_reader, render

KINDS = ("text", "rectangle", "ellipse", "triangle", "arrow")
LAYER = "/GitDocPDFObjects"
LIMIT = 64
INVALID = "PDF 개체를 읽거나 저장할 수 없습니다. 원본은 보존됩니다."
BOUNDS = "개체를 페이지 안에 놓고 크기를 확인하세요."
FONT_ERROR = "입력한 글자를 포함하고 PDF에 포함할 수 있는 글꼴이 필요합니다."
ID = re.compile(r"[0-9a-f]{32}")
TEXT = re.compile(r"[\x20-\x7e\uac00-\ud7a3\u3131-\u318e\u3000-\u303f\u2010-\u2027\n]{1,400}")


def stream(data, **entries):
    result = DecodedStreamObject()
    result.set_data(data)
    result.update({NameObject("/" + key): value for key, value in entries.items()})
    return result


def numbers(values):
    return ArrayObject([FloatObject(v) for v in values])


def resource_name(object_id):
    return NameObject("/GitDoc_" + object_id)


def selected_page(document, number):
    if type(number) is not int or not 1 <= number <= len(document.pages):
        raise ValueError("PDF 페이지 번호를 확인하세요.")
    return document.pages[number - 1]


def placement(page, box):
    # Translate three visual corners via PDFium, including the crop offset and /Rotate.
    grid = 100000
    convert = PdfPosConv(page, (0, 0, grid, grid, 0))
    left, top, right, bottom = (round(value * grid) for value in box)
    x, y = convert.to_page(left, bottom)
    xx, xy = convert.to_page(right, bottom)
    yx, yy = convert.to_page(left, top)
    return xx - x, xy - y, yx - x, yy - y, x, y


def visual_box(page, matrix):
    grid = 100000
    convert = PdfPosConv(page, (0, 0, grid, grid, 0))
    a, b, c, d, x, y = matrix
    points = [convert.to_bitmap(x + u * a + v * c, y + u * b + v * d)
              for u, v in ((0, 0), (0, 1), (1, 0), (1, 1))]
    xs, ys = zip(*points)
    return [round(value / grid, 5) for value in (min(xs), min(ys), max(xs), max(ys))]


def invocation(objects):
    commands = ["Q\n"]
    for item in objects:
        matrix = serialized_matrix(item["matrix"])
        commands.append(f"q {matrix} cm {resource_name(item['id'])} Do Q\n")
    return "".join(commands).encode("ascii")


def serialized_matrix(matrix):
    return " ".join(f"{(round(value, 6) or 0):.6f}" for value in matrix)


def object_fields(item):
    if not isinstance(item, dict):
        raise ValueError(INVALID)
    kind, box, value = (item.get(key) for key in ("kind", "box", "text"))
    if kind not in KINDS or not isinstance(box, (list, tuple)) or len(box) != 4 or not all(
        type(v) in (float, int) and math.isfinite(v) and 0 <= v <= 1 for v in box
    ):
        raise ValueError(BOUNDS)
    box = [round(v, 5) for v in box]
    if box[2] - box[0] < .02 or box[3] - box[1] < .02:
        raise ValueError(BOUNDS)
    if kind == "text" and (not isinstance(value, str) or not TEXT.fullmatch(value) or not value.strip()
                           or any(not line.strip() for line in value.split("\n"))):
        raise ValueError("한글과 영문으로 텍스트를 입력하세요. 빈 줄은 지원하지 않습니다.")
    return {"kind": kind, "box": box, "text": value if kind == "text" else ""}


def resources(page):
    return copy(page["/Resources"]) if "/Resources" in page else DictionaryObject()


def read_layer(page, pdfium_page):
    contents = page.get("/Contents")
    contents = contents.get_object() if contents is not None else None
    refs = list(contents) if isinstance(contents, ArrayObject) else (
        [page.raw_get("/Contents")] if contents is not None else [])
    objects = []
    if refs and LAYER in refs[-1].get_object():
        layer = refs.pop().get_object()
        records = layer[LAYER]
        if len(records) > LIMIT or not refs or refs[0].get_object().get_data() != b"q\n":
            raise ValueError(INVALID)
        refs.pop(0)
        found = set()
        forms = resources(page).get("/XObject", DictionaryObject()).get_object()
        for record in records:
            object_id = str(record["/Id"])
            name = resource_name(object_id)
            if (not ID.fullmatch(object_id) or object_id in found or name not in forms or
                    forms[name].get("/Subtype") != "/Form"):
                raise ValueError(INVALID)
            found.add(object_id)
            matrix = list(map(float, str(record["/Matrix"]).split()))
            if len(matrix) != 6 or not all(math.isfinite(value) and abs(value) <= 1e7 for value in matrix):
                raise ValueError(INVALID)
            item = object_fields({"kind": str(record["/Kind"]), "box": visual_box(pdfium_page, matrix),
                                  "text": str(record["/Text"])})
            objects.append(dict(item, id=object_id, matrix=matrix))
        if layer.get_data() != invocation(objects):
            raise ValueError(INVALID)
    return refs, objects


def font_data(value):
    faces = face_index(installed_fonts()[2])
    # Open the local face only if its embedding flags allow editing and subsetting.
    families = ("Apple SD Gothic Neo", *KOREAN["sans"], *GENERIC["sans"], "ArialMT")
    seen = set()
    for name in families:
        source = faces.get(family_key(name))
        if source is None or (source["path"], source["index"]) in seen:
            continue
        seen.add((source["path"], source["index"]))
        try:
            with TTFont(source["path"], fontNumber=source["index"], recalcTimestamp=False) as font:
                if ("OS/2" not in font or font["OS/2"].fsType & ~8 or "fvar" in font or
                        not set(map(ord, value.replace("\n", ""))).issubset(font.getBestCmap() or {})):
                    continue
                if "glyf" not in font and "CFF " not in font:
                    continue
                options = subset.Options()
                options.drop_tables.append("meta")
                sub = subset.Subsetter(options)
                sub.populate(text=value)
                sub.subset(font)
                output = io.BytesIO()
                font.save(output)
                return output.getvalue(), raw.FPDF_FONT_TRUETYPE if "glyf" in font else raw.FPDF_FONT_TYPE1
        except (OSError, TTLibError, KeyError, ValueError):
            continue
    raise ValueError(FONT_ERROR)


def text_form(value, width, height, writer):
    binary, kind = font_data(value)
    data = (ctypes.c_ubyte * len(binary)).from_buffer_copy(binary)
    output = io.BytesIO()
    with PdfDocument.new() as pdf:
        font = raw.FPDFText_LoadFont(pdf, data, len(data), kind, 1)
        if not font:
            raise ValueError(FONT_ERROR)
        try:
            with closing(pdf.new_page(width, height)) as page:
                for index, line in enumerate(value.split("\n")):
                    obj = raw.FPDFPageObj_CreateTextObj(pdf, font, 12)
                    if not obj:
                        raise ValueError(FONT_ERROR)
                    # Insert immediately so the page owns cleanup even if validation fails.
                    raw.FPDFPage_InsertObject(page, obj)
                    utf = line.encode("utf-16-le") + b"\0\0"
                    chars = (ctypes.c_ushort * (len(utf) // 2)).from_buffer_copy(utf)
                    if not raw.FPDFText_SetText(obj, chars):
                        raise ValueError(FONT_ERROR)
                    raw.FPDFPageObj_Transform(obj, 1, 0, 0, 1, 4, height - 15 - 15 * index)
                    coords = [ctypes.c_float() for _ in range(4)]
                    if not raw.FPDFPageObj_GetBounds(obj, *(ctypes.byref(coord) for coord in coords)):
                        raise ValueError(FONT_ERROR)
                    left, bottom, right, top = [coord.value for coord in coords]
                    if not (0 <= left < right <= width - 2 and 0 <= bottom < top <= height):
                        raise ValueError("텍스트가 상자를 벗어납니다. 상자를 넓히거나 줄을 나누세요.")
                page.gen_content()
            pdf.save(output, flags=raw.FPDF_NO_INCREMENTAL)
        finally:
            raw.FPDFFont_Close(font)
    page = PdfReader(output).pages[0]
    if page.extract_text().splitlines() != value.splitlines():
        raise ValueError("PDF에 포함된 글자를 다시 읽을 수 없습니다.")
    return stream(page.get_contents().get_data(), Resources=resources(page).clone(writer))


PATHS = {
    "rectangle": ".05 .05 .9 .9 re S",
    "ellipse": ".95 .5 m .95 .74853 .74853 .95 .5 .95 c .25147 .95 .05 .74853 .05 .5 c "
               ".05 .25147 .25147 .05 .5 .05 c .74853 .05 .95 .25147 .95 .5 c h S",
    "triangle": ".05 .05 m .5 .95 l .95 .05 l h S",
    "arrow": ".05 .5 m .94 .5 l .65 .82 m .94 .5 l .65 .18 l S",
}


def make_form(writer, pdfium_page, item):
    kind = item["kind"]
    if kind == "text":
        a, b, c, d, _, _ = placement(pdfium_page, item["box"])
        width, height = math.hypot(a, b), math.hypot(c, d)
        if width < 10 or height < 16:
            raise ValueError(BOUNDS)
        form = text_form(item["text"], width, height, writer)
        # Normalized form geometry makes moves independent of page rotation.
        form[NameObject("/Matrix")] = numbers([1 / width, 0, 0, 1 / height, 0, 0])
    else:
        width = height = 1
        form = stream(f".18 .30 .48 RG 0 w {PATHS[kind]}\n".encode(), Resources=DictionaryObject())
    form.update({NameObject("/Type"): NameObject("/XObject"), NameObject("/Subtype"): NameObject("/Form"),
                 NameObject("/BBox"): numbers([0, 0, width, height])})
    return form


def edit_objects(data, number, requested):
    try:
        return _edit_objects(data, number, requested)
    except (OSError, PyPdfError, PdfiumError, RuntimeError, KeyError, TypeError, IndexError, AttributeError) as error:
        raise ValueError(INVALID) from error


def _edit_objects(data, number, requested):
    if not isinstance(requested, list) or len(requested) > LIMIT:
        raise ValueError("한 페이지에 PDF 개체를 64개까지 편집할 수 있습니다.")
    writer = PdfWriter(clone_from=checked_reader(data))
    page = selected_page(writer, number)
    dest = io.BytesIO()
    with PdfDocument(data) as input_pdf, closing(input_pdf[number - 1]) as canvas:
        refs, old = read_layer(page, canvas)
        by_id = {item["id"]: item for item in old}
        page_resources = resources(page)
        forms = copy(page_resources.get("/XObject", DictionaryObject()).get_object())
        objects = []
        kept = set()
        for item in requested:
            new = object_fields(item)
            object_id = item.get("id")
            if object_id is None:
                object_id = uuid4().hex
                name = resource_name(object_id)
                if name in forms:
                    raise ValueError(INVALID)
                forms[name] = writer._add_object(make_form(writer, canvas, new))
                matrix = placement(canvas, new["box"])
            else:
                previous = by_id.get(object_id) if isinstance(object_id, str) else None
                if previous is None or object_id in kept or (previous["kind"], previous["text"]) != (new["kind"], new["text"]):
                    raise ValueError("수정할 PDF 개체를 다시 선택하세요.")
                if any(abs((new["box"][i+2] - new["box"][i]) -
                        (previous["box"][i+2] - previous["box"][i])) > .00002 for i in (0, 1)):
                    raise ValueError("개체는 크기를 유지한 채 이동할 수 있습니다.")
                matrix = previous["matrix"].copy()
                if new["box"] != previous["box"]:
                    old_position = placement(canvas, previous["box"])
                    new_position = placement(canvas, new["box"])
                    for i in (4, 5):
                        matrix[i] += new_position[i] - old_position[i]
                kept.add(object_id)
            objects.append(dict(new, id=object_id, matrix=[round(v, 6) for v in matrix]))
        if objects == old:
            raise ValueError("저장할 개체 변경이 없습니다.")
        for object_id in by_id.keys() - kept:
            del forms[resource_name(object_id)]
        page_resources[NameObject("/XObject")] = forms
        page[NameObject("/Resources")] = page_resources
        records = ArrayObject([DictionaryObject({
            NameObject("/Id"): TextStringObject(item["id"]), NameObject("/Kind"): TextStringObject(item["kind"]),
            NameObject("/Text"): TextStringObject(item["text"]),
            NameObject("/Matrix"): TextStringObject(serialized_matrix(item["matrix"])),
        }) for item in objects])
        layer = stream(invocation(objects), GitDocPDFObjects=records)
        page[NameObject("/Contents")] = ArrayObject([writer._add_object(stream(b"q\n")), *refs, writer._add_object(layer)])
        before = render(canvas)
        writer.compress_identical_objects(remove_identicals=False, remove_orphans=True)
        writer.write(dest)

    updated = dest.getvalue()
    with PdfDocument(updated) as output, closing(output[number - 1]) as canvas:
        after = render(canvas)
    if before.size != after.size:
        raise ValueError(INVALID)
    difference = ImageChops.difference(before, after)
    width, height = difference.size
    mask = ImageDraw.Draw(difference)
    changed = [item for item in old if item not in objects] + [item for item in objects if item not in old]
    if not changed and objects != old:
        changed = objects
    for item in changed:
        x0, y0, x1, y1 = item["box"]
        mask.rectangle((int(x0 * width) - 3, int(y0 * height) - 3,
                        math.ceil(x1 * width) + 3, math.ceil(y1 * height) + 3), fill=(0, 0, 0))
    if difference.getbbox():
        raise ValueError("편집할 개체 밖의 모양이 달라져 저장하지 않았습니다.")
    return updated


def open_objects(data, number):
    try:
        page = selected_page(checked_reader(data), number)
        with PdfDocument(data) as document, closing(document[number - 1]) as canvas:
            _, objects = read_layer(page, canvas)
            return [{key: val for key, val in item.items() if key != "matrix"} for item in objects], render(canvas)
    except (OSError, PyPdfError, PdfiumError, RuntimeError, KeyError, TypeError, IndexError, AttributeError) as error:
        raise ValueError(INVALID) from error

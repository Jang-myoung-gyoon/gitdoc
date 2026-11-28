"""Connect rendered Word and Hangul text to editable source paragraphs."""

import hashlib
import io
import json
import os
import re
import tempfile
import unicodedata
from collections import defaultdict
from pathlib import Path

import olefile

from desktop.document_objects import (
    HANGUL_SHAPES, SHAPE_SIZE, close_hangul_polygon, delete_word_table, insert_word_object,
    insertion_value, word_tables,
)
from desktop.editor_fonts import use_rendered_fonts
from desktop.exporting import convert_format
from desktop.hangul_cells import (
    CELL_LAYOUT_COMMANDS, GRID_MISMATCH, GRID_OPERATIONS, LAYOUT_MISMATCH, MAX_SUBDIVISION, SPAN_MISMATCH,
    STALE_CELL, SUBDIVISION_MISMATCH, UNSAFE_GRID, UNSAFE_LAYOUT, UNSAFE_SIZE, UNSAFE_SUBDIVISION,
    cell_catalog, cell_value, format_cells, replace_cell, subdivision_axes, subdivision_children,
    verify_cell, verify_cell_layout, verify_grid, verify_span, verify_subdivision,
)
from desktop.hangul_format import CHARACTER, OPERATIONS as HANGUL_FORMATS, format_paragraph, formatting_catalog
from desktop.shape_editor import (
    HWP_UNITS_PER_MM, apply_shape, prepare_legacy_shapes, shape_catalog, verify_inline_hwp,
)
from editing import WORD, editable_office_blocks, office_blocks, open_package, parse_part, replace_office_blocks, valid_text
from page_text import embedded_text
from preview import PreviewError, page_dimensions, preview_path, preview_png, rhwp_command, run_converter
from uploads import MAX_FILE

HANGUL_EXTENSIONS = ("hwp", "hwpx")
MAX_HISTORY = 128 * 1024 * 1024
SHAPE_EDITS = ("delete_shape", "shape_up", "shape_down", "shape_size")
# HWP 5 FileHeader properties: password, distribution, DRM and certificate flags.
HWP_READ_ONLY = sum(1 << bit for bit in (1, 2, 4, 7, 8, 9, 10))


def normalized(text):
    return "".join(char for char in unicodedata.normalize("NFKC", text) if not char.isspace())


def hit_box(boxes):
    return [min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes)]


def word_locations(data, pdf, count):
    groups = defaultdict(list)
    for block in editable_office_blocks("docx", data):
        if needle := normalized(block["text"]):
            groups[needle].append(block)
    locations = [[] for _ in range(count)]
    glyphs = []
    for number in range(1, count+1):
        glyphs.extend((char, box, number-1) for line in embedded_text(pdf, number)
                      for value, box in line for char in normalized(value))
        glyphs.append(("\x00", None, number-1))
    haystack = "".join(char for char, _, _ in glyphs)
    occupied = bytearray(len(haystack))
    for needle in sorted(groups, key=len, reverse=True):
        positions = []
        start = haystack.find(needle)
        while start >= 0:
            end = start + len(needle)
            if not any(occupied[start:end]):
                positions.append((start, end))
            start = haystack.find(needle, end)
        blocks = groups[needle]
        if not positions or len(positions) != len(blocks):
            continue
        for (start, end), block in zip(positions, blocks):
            occupied[start:end] = b"\x01"*len(needle)
            index = glyphs[start][2]
            boxes = [box for _, box, _ in glyphs[start:end]]
            ink = hit_box(boxes)
            bounds = [ink[0], ink[1], max(ink[2], .9), ink[3]]
            locations[index].append(dict(block, box=bounds, ink=ink, size=11,
                                         name=block["text"][:60]))
    return locations


def hwp_paragraphs(data):
    with open_package(data) as archive:
        for path in archive.namelist():
            match = re.fullmatch(r"Contents/section(\d+)\.xml", path)
            if match is None:
                continue
            section = int(match[1])
            root = parse_part(archive, path)
            for paragraph, node in enumerate(root.findall("{*}p")):
                chunks = []
                for item in node.iter():
                    if item.tag.endswith("}t") and next(item.iterancestors(tag=node.tag), None) is node:
                        chunks.append(item.text or "")
                yield (section, paragraph), "".join(chunks)


def hwp_id(section, paragraph):
    return (section << 32) | (paragraph + 1)


def rhwp(args, folder):
    return run_converter([rhwp_command(), *args], folder, env=dict(os.environ, RHWP_WEBFONT_OFFLINE="1"))


def hwp_locations(source, page, placement, text):
    folder = source.parent
    number = page - 1
    section = placement["section"]
    rhwp(["export-render-tree", str(source), "-p", str(number), "-o", str(folder)], folder)
    root = json.loads((folder/f"render_tree_{page:03d}.json").read_bytes())
    width, height = root["bbox"]["w"], root["bbox"]["h"]
    body = next((item for item in root["children"] if item["type"] == "Body"), {})
    layout = {(item["paraIndex"], item["controlIndex"]): item["table"]
              for column in placement["columns"] for item in column["items"]
              if item["kind"] == "table" and not item.get("isEndnote")}
    lines = defaultdict(list)
    table_cells = {}
    tables = []
    for column in body.get("children", []):
        for item in column.get("children", []):
            if item["type"] == "TextLine" and "pi" in item:
                for run in item.get("children", []):
                    if run["type"] == "TextRun" and run.get("text"):
                        lines[item["pi"]].append(run)
            address = (item.get("pi"), item.get("ci"))
            if item["type"] == "Table" and address in layout:
                x, y, w, h = (item["bbox"][key] for key in ("x", "y", "w", "h"))
                table = layout[address]
                table_address = (section, *address)
                cells = table_cells.setdefault(table_address, {})
                for cell in item.get("children", []):
                    if cell["type"] == "Cell":
                        coordinate = cell.get("row"), cell.get("col")
                        left, top, w_cell, h_cell = (cell["bbox"][key] for key in ("x", "y", "w", "h"))
                        cells[coordinate] = None if coordinate in cells else [
                            left/width, top/height, (left+w_cell)/width, (top+h_cell)/height]
                tables.append({"address": list(table_address), "box": [x/width, y/height, (x+w)/width, (y+h)/height],
                               "rows": table["rowCount"], "columns": table["colCount"]})
    full = {item["paraIndex"] for column in placement["columns"] for item in column["items"]
            if item["kind"] == "fullParagraph" and not item.get("isEndnote")}
    items = []
    for paragraph in sorted(full & lines.keys()):
        value = text.get((section, paragraph), "")
        if not value or len(value) > 20000:
            continue
        # Match the saved paragraph against rendered runs, excluding generated list numbers.
        chunks = [(normalized(run["text"]), run["bbox"]) for run in lines[paragraph]]
        body_text = "".join(chars for chars, _ in chunks)
        needle = normalized(value)
        start = body_text.find(needle)
        if start < 0 or not needle or body_text.find(needle, start+len(needle)) >= 0:
            continue
        positions = []
        end = start + len(needle)
        offset = 0
        for chars, box in chunks:
            if chars and offset < end and offset+len(chars) > start:
                x, y, w, h = box["x"], box["y"], box["w"], box["h"]
                positions.append([x/width, y/height, (x+w)/width, (y+h)/height])
            offset += len(chars)
        if positions:
            ink = hit_box(positions)
            items.append({"id": hwp_id(section, paragraph), "name": value[:60], "text": value,
                          "box": [ink[0], ink[1], max(ink[2], .85), ink[3]], "ink": ink,
                          "size": max(8, min(40, (positions[0][3]-positions[0][1])*height*.75))})
    return items, tables, table_cells


def exported_tables(source):
    return json.loads(rhwp(["export-tables", str(source), "--json"], source.parent))["tables"]


def edit_native_table(document, output, address, command, args, *, message, allow_resize=False):
    original = exported_tables(document)
    index = next((i for i, table in enumerate(original)
                  if [table["section"], table["paragraph"], table["control"]] == address), None)
    if index is None:
        raise ValueError(STALE_CELL)
    target = original[index]
    rhwp(["edit", command, str(document), "--table", str(target["index"]),
          *args, "-o", str(output)], output.parent)
    updated = exported_tables(output)
    identity = ["index", "section", "paragraph", "control"]
    if not allow_resize:
        identity += ["rows", "cols"]
    if (len(updated) != len(original) or original[:index] != updated[:index]
            or original[index+1:] != updated[index+1:]
            or any(target[key] != updated[index][key] for key in identity)):
        raise ValueError(message)
    return target, updated[index], updated


def verify_hwp_streams(source, output, section, message=GRID_MISMATCH, *, doc_info=False):
    # The writer may change the target BodyText section. Keep every other OLE stream verbatim.
    with olefile.OleFileIO(io.BytesIO(source)) as old, olefile.OleFileIO(output) as new:
        if old.listdir(streams=True, storages=True) != new.listdir(streams=True, storages=True):
            raise ValueError(message)
        excluded = {("BodyText", f"Section{section}")}
        if doc_info:
            excluded.add(("DocInfo",))
        if any(old.openstream(path).read() != new.openstream(path).read()
               for path in old.listdir() if tuple(path) not in excluded):
            raise ValueError(message)


def change_hwp(source, original, value, section, paragraph):
    folder = source.parent
    matches = json.loads(rhwp(["search", str(source), original, "--json"], folder))["matches"]
    address = [m for m in matches if m["section"] == section and m["paragraph"] == paragraph]
    if len(address) != 1:
        raise ValueError("한글 원본에서 선택한 문단을 정확히 확인할 수 없습니다.")
    address = address[0]
    left = 0
    for a, b in zip(original, value):
        if a != b:
            break
        left += 1
    right = 0
    for a, b in zip(reversed(original[left:]), reversed(value[left:])):
        if a != b:
            break
        right += 1
    before = original[left:len(original)-right]
    after = value[left:len(value)-right]
    if not before and not after:
        return source.read_bytes()
    flags = ["--section", str(section), "--para", str(paragraph),
             "--offset", str(address["charOffset"]+left)]
    if before:
        output = folder/f"deleted{source.suffix}"
        rhwp(["edit", "delete-text", str(source), *flags, "--count", str(len(before)), "-o", str(output)], folder)
        source = output
    if after:
        output = folder/f"inserted{source.suffix}"
        rhwp(["edit", "insert-text", str(source), *flags, "--text", after, "-o", str(output)], folder)
        source = output
    return source.read_bytes()


def hangul_xml(source):
    if source.suffix == ".hwpx":
        return source.read_bytes()
    target = source.with_suffix(".hwpx")
    rhwp(["export-hwpx", str(source), str(target)], source.parent)
    return target.read_bytes()


def hangul_insert_order(data, section):
    with open_package(data) as archive:
        body = parse_part(archive, f"Contents/section{section}.xml")
    runs = []
    objects = []
    paragraphs = []
    offset = 0
    for paragraph in body.findall("{*}p"):
        paragraphs.append(offset)
        for run in paragraph.findall("{*}run"):
            for node in run:
                kind = node.tag.rsplit("}", 1)[-1]
                if kind == "t":
                    value = "".join(node.itertext())
                    offset += len(value)
                    runs.append(value)
                elif kind in ("tbl", "rect", "ellipse", "polygon", "line"):
                    objects.append((offset, kind))
    return "".join(runs), objects, paragraphs


def insert_hangul_object(folder, extension, data, operation, value, section, paragraph, caret, body_text):
    source = folder/f"document.{extension}"
    source.write_bytes(data)
    original_xml = hangul_xml(source)
    standalone = operation == "shape" and caret == len(body_text)
    native_paragraph = paragraph + int(standalone)
    coordinates = ["--section", str(section), "--para", str(native_paragraph)]
    flags = [*coordinates, "--offset", str(0 if standalone else caret)]
    if operation == "table":
        rows, columns = value
        flags += ["--rows", str(rows), "--cols", str(columns)]
    else:
        width, height = SHAPE_SIZE
        flags += ["--shape", HANGUL_SHAPES[value], "--width", str(width), "--height", str(height), "--treat-as-char"]
    destination = folder/f"updated.{extension}"
    if extension == "hwpx":
        with open_package(data):
            pass
    try:
        if standalone:
            # At a paragraph end, preserve the existing standalone shape tools.
            paragraph_source = folder / f"object-paragraph.{extension}"
            rhwp(["edit", "insert-paragraph", str(source), *coordinates, "-o", str(paragraph_source)], folder)
            source = paragraph_source
        rhwp(["edit", f"insert-{operation}", str(source), *flags, "-o", str(destination)], folder)
    except PreviewError as error:
        raise ValueError("한글 개체를 삽입할 수 없습니다. 설치한 rhwp의 삽입 명령과 문서 편집 권한을 확인하세요.") from error
    updated_xml = hangul_xml(destination)
    result = destination.read_bytes()
    if value in ("triangle", "arrow"):
        # rhwp inserts an open triangle. Write a closed, native polygon through HWPX.
        polygon_source = hangul_xml(source) if standalone else original_xml
        updated_xml = close_hangul_polygon(polygon_source, updated_xml, section, native_paragraph, value)
        result = convert_format("hwpx", "hwp", updated_xml) if extension == "hwp" else updated_xml
        if extension == "hwp":
            destination.write_bytes(result)
            updated_xml = hangul_xml(destination)
    before, controls, paragraphs = hangul_insert_order(original_xml, section)
    after, inserted, _ = hangul_insert_order(updated_xml, section)
    kind = "tbl" if operation == "table" else {"rectangle": "rect", "ellipse": "ellipse",
                                               "triangle": "polygon", "arrow": "polygon"}[value]
    boundary = (paragraphs[paragraph] + caret, kind)
    expected = list(controls)
    expected.append(boundary)
    if before != after or sorted(expected) != sorted(inserted):
        raise ValueError("삽입 위치와 주변 본문을 확인할 수 없어 변경을 적용하지 않았습니다.")
    return result


class PagedEditor:
    def __init__(self, extension, data):
        self.extension = extension
        if extension == "doc":
            with olefile.OleFileIO(io.BytesIO(data)) as legacy:
                if legacy.exists("EncryptionInfo") or any(
                    part.lower() in ("vba", "macros", "_vba_project_cur")
                    for path in legacy.listdir() for part in path
                ):
                    raise ValueError("암호가 걸렸거나 매크로가 포함된 DOC입니다.")
        self.states = [convert_format("doc", "docx", data) if extension == "doc" else data]
        self.position = 0
        self.directory = tempfile.TemporaryDirectory(prefix="gitdoc-document-")
        self.current_key = None
        self.current_page = None
        self.texts = {}
        self.locations = []
        self.page_objects = []
        self.page_count = 0
        self.tables = []
        self.cell_catalog = None
        self.shapes = []
        self.page_shapes = []
        self.shape_xml = None
        self.hangul_formatting = set()
        self.hangul_fonts = []
        self.hwp_shapes_allowed = True
        if extension == "hwp":
            with olefile.OleFileIO(io.BytesIO(data)) as document:
                header = document.openstream("FileHeader").read(40)
                self.hwp_shapes_allowed = len(header) == 40 and not int.from_bytes(header[36:40], "little") & HWP_READ_ONLY

    @property
    def data(self):
        return self.states[self.position]

    def final_data(self):
        return convert_format("docx", "doc", self.data) if self.extension == "doc" else self.data

    def close(self):
        self.directory.cleanup()

    def render(self, data, number, *, keep_inline_shape=None, table_layout=None):
        extension = "docx" if self.extension == "doc" else self.extension
        key = hashlib.sha256(data).hexdigest()
        if extension in HANGUL_EXTENSIONS:
            try:
                pdf = preview_path(extension, key, lambda: data, hangul_editor=True)
            except PreviewError:
                pdf = preview_path(extension, key, lambda: data)
        else:
            pdf = preview_path(extension, key, lambda: data)
        sizes = page_dimensions(pdf)
        count = len(sizes)
        number = max(1, min(count, number))
        width, height = sizes[number-1]
        texts, locations = self.texts, self.locations
        shapes, shape_xml = self.shapes, self.shape_xml
        formatting, hangul_fonts = self.hangul_formatting, self.hangul_fonts
        changed = key != self.current_key
        inline_limits = {}
        native_cells = None if changed else self.cell_catalog
        with tempfile.TemporaryDirectory(dir=self.directory.name) as work:
            folder = Path(work)
            if extension in HANGUL_EXTENSIONS:
                source = folder/f"document.{extension}"
                source.write_bytes(data)
                if changed:
                    content = hangul_xml(source)
                    texts = dict(hwp_paragraphs(content))
                    shapes = shape_catalog(content, hangul=True) if self.hwp_shapes_allowed else []
                    formatting, hangul_fonts = formatting_catalog(content) if self.hwp_shapes_allowed else (set(), [])
                    shape_xml = content if (shapes or formatting) and extension == "hwp" else None
                placement = json.loads(rhwp(["dump-pages", str(source), "-p", str(number-1), "--json"], folder))["pages"][0]
                objects, tables, page_cells = hwp_locations(source, number, placement, texts)
                if len(placement["columns"]) == 1:
                    for item in placement["columns"][0]["items"]:
                        if item["kind"] != "fullParagraph" or item.get("isEndnote"):
                            continue
                        segments = item.get("lineSegs") or {}
                        edges = [segments.get(key) or {} for key in ("first", "last")]
                        area = placement["bodyArea"]
                        widths = [area["width"] * 25.4 / 96]
                        widths.extend(edge["segmentWidth"] / HWP_UNITS_PER_MM for edge in edges
                                      if edge.get("segmentWidth", 0) > 0)
                        width_limit = min(widths)
                        height_limit = area["height"] * 25.4 / 96
                        inline_limits[(placement["section"], item["paraIndex"])] = (width_limit, height_limit)
                if tables and native_cells is None:
                    native_cells = {}
                    if self.hwp_shapes_allowed:
                        try:
                            native_cells = cell_catalog(content if changed else hangul_xml(source),
                                                        exported_tables(source))
                        except PreviewError:
                            pass
                for table in tables:
                    address = tuple(table["address"])
                    supported, grid, spans, layout, subdivision = (native_cells or {}).get(
                        address, ({}, False, {}, {}, False))
                    cells = []
                    for coordinate, box in sorted(page_cells[address].items()):
                        if box is None or (coordinate not in supported and coordinate not in spans and coordinate not in layout):
                            continue
                        cell = {"coordinate": list(coordinate), "text": supported.get(coordinate),
                                "box": box, "layout": coordinate in layout}
                        span = tuple(spans.get(coordinate) or layout.get(coordinate) or (1, 1))
                        if span != (1, 1):
                            cell["span"] = list(span)
                        if coordinate in spans:
                            cell["split"] = True
                        cells.append(cell)
                    table["cells"] = cells
                    table["editable_grid"] = grid and len(table["cells"]) == table["rows"] * table["columns"]
                    table["subdivision"] = subdivision and table["editable_grid"]
                    table["layout_grid"] = (len(layout) == table["rows"] * table["columns"]
                                            and len(table["cells"]) == len(layout)
                                            and all(span == (1, 1) for span in layout.values()))
                if table_layout is not None:
                    matching = [table for table in tables if table["address"] == table_layout["address"]]
                    if (len(matching) != 1
                            or {tuple(c["coordinate"]) for c in matching[0]["cells"]}
                            != table_layout["coordinates"]):
                        raise ValueError("표가 현재 쪽을 벗어나 변경을 적용하지 않았습니다.")
                    if table_layout["sizing"]:
                        left, top, right, bottom = matching[0]["box"]
                        area = placement["bodyArea"]
                        usable_right = min(1, (area["x"] + area["width"] + 1) / (width * 96 / 72))
                        usable_bottom = min(1, (area["y"] + area["height"] + 1) / (height * 96 / 72))
                        if left < 0 or top < 0 or right > usable_right or bottom > usable_bottom:
                            raise ValueError("표가 현재 쪽의 본문 영역을 벗어나 변경을 적용하지 않았습니다.")
                for item in objects:
                    identity = item["id"]
                    item["hangul_format"] = (identity >> 32, (identity & 0xffffffff) - 1) in formatting
            else:
                if changed:
                    locations = word_locations(data, pdf, count)
                    tables = word_tables(data)
                    shapes = shape_catalog(data, hangul=False, binary_word=self.extension == "doc")
                else:
                    tables = self.tables
                objects = locations[number-1]
        page_shapes = [
            dict(shape, inline_limits=inline_limits[tuple(shape["address"][:2])])
            if len(shape["address"]) == 3 else shape
            for shape in shapes
            if len(shape["address"]) == 2 or tuple(shape["address"][:2]) in inline_limits
        ]
        if keep_inline_shape is not None and not any(
                shape["address"] == keep_inline_shape for shape in page_shapes):
            raise ValueError("도형을 현재 쪽의 같은 문단 안에 배치할 수 없어 변경을 적용하지 않았습니다.")
        png = Path(self.directory.name)/"document.png"
        png.write_bytes(preview_png(pdf, number, prefer_pdfium=True))
        fonts = use_rendered_fonts(objects, pdf, number, Path(self.directory.name) / "fonts")
        self.current_key = key
        self.current_page = number
        self.page_objects = objects
        self.page_count = count
        self.tables, self.shapes, self.shape_xml = tables, shapes, shape_xml
        self.page_shapes = page_shapes
        self.cell_catalog = native_cells
        self.texts, self.locations = texts, locations
        self.hangul_formatting, self.hangul_fonts = formatting, hangul_fonts
        return {"page": number, "edit_digest": key, "count": count, "width": width, "height": height,
                "objects": objects, "tables": tables, "shapes": self.page_shapes, "hangul_fonts": hangul_fonts, "image": str(png), "fonts": fonts}

    def page(self, number, operation=None, text_edit=None, target=None, value=None, selection=None, **_):
        index = self.position
        source = self.data
        if operation == "undo":
            index = max(0, index-1)
            updated = self.states[index]
        elif operation == "redo":
            index = min(len(self.states)-1, index+1)
            updated = self.states[index]
        elif (text_edit or operation) and not (operation == "page" and not text_edit):
            updated = self.apply(source, operation, text_edit, target, value, selection)
        else:
            updated = source
        if len(updated) > MAX_FILE:
            raise ValueError("문서가 최대 파일 크기를 초과했습니다.")
        selected_shape = None
        if operation in ("shape_up", "shape_down", "shape_size") and len(value["address"]) == 3:
            selected_shape = value["address"]
        layout = None
        if operation in ("edit_table_layout", "edit_table_subdivision"):
            chosen = next(table for table in self.tables if table["address"] == value["address"])
            coordinates = {tuple(cell["coordinate"]) for cell in chosen["cells"]}
            if operation == "edit_table_subdivision":
                start = tuple(value["coordinate"])
                counts = (value["rows"], value["columns"])
                coordinates = {subdivision_axes(cell, start, counts)[0] for cell in coordinates - {start}}
                coordinates.update(subdivision_children(start, counts))
            layout = {"address": value["address"], "coordinates": coordinates,
                      "sizing": operation == "edit_table_subdivision" or value.get("change") in ("width", "height")}
        result = self.render(updated, number, keep_inline_shape=selected_shape, table_layout=layout)
        if index == self.position and updated != source:
            del self.states[index+1:]
            self.states.append(updated)
            while len(self.states) > 24 or len(self.states) > 2 and sum(map(len, self.states)) > MAX_HISTORY:
                del self.states[1]
            index = len(self.states)-1
        self.position = index
        if self.extension in HANGUL_EXTENSIONS and operation in CHARACTER and selection:
            result["hangul_selection"] = selection
        if selected_shape is not None:
            result["shape_selection"] = selected_shape
        if layout is not None:
            result["table_selection"] = {key: value[key] for key in ("address", "coordinate", "end") if key in value}
        return dict(result, undo=index > 0, redo=index+1 < len(self.states), dirty=index > 0)

    def change_cell_layout(self, source, chosen, coordinate, value):
        change, amount = value.get("change"), value.get("value")
        end = value.get("end", coordinate)
        if (not isinstance(end, list) or len(end) != 2 or any(type(i) is not int for i in end)
                or not any(cell["coordinate"] == end and cell["layout"] for cell in chosen["cells"])):
            raise ValueError(STALE_CELL)
        if change in ("width", "height"):
            if not chosen["layout_grid"]:
                raise ValueError(UNSAFE_SIZE)
            if type(amount) is not int or not 2 <= amount <= 180:
                raise ValueError("셀 너비와 높이는 2~180 mm로 입력하세요.")
        elif not isinstance(change, str) or amount not in CELL_LAYOUT_COMMANDS.get(change, ()):
            raise ValueError(UNSAFE_LAYOUT)
        with tempfile.TemporaryDirectory(dir=self.directory.name) as work:
            document = Path(work)/f"document.{self.extension}"
            document.write_bytes(source)
            native = next((table for table in exported_tables(document)
                           if [table["section"], table["paragraph"], table["control"]] == chosen["address"]), None)
            if native is None:
                raise ValueError(STALE_CELL)
            xml = hangul_xml(document)
            changed, commands = format_cells(xml, native, tuple(coordinate), tuple(end), change, amount)
            if not commands:
                return source
            if self.extension == "hwpx":
                return changed
            if len(commands) > 200:
                raise ValueError("HWP에서는 한 번에 최대 200개 셀 또는 문단을 바꿀 수 있습니다.")
            for index, (command, flags) in enumerate(commands):
                output = document.parent / f"layout-{index % 2}.hwp"
                if output.exists():
                    output.unlink()
                rhwp(["edit", command, str(document), "--table", str(native["index"]), *flags,
                      "-o", str(output)], document.parent)
                document = output
            verify_hwp_streams(source, document, native["section"], LAYOUT_MISMATCH,
                               doc_info=change == "horizontal")
            verify_cell_layout(xml, changed, hangul_xml(document), native, change)
            return document.read_bytes()

    def change_grid(self, source, chosen, coordinate, value):
        if not chosen.get("editable_grid"):
            raise ValueError(UNSAFE_GRID)
        change = value.get("change")
        if not isinstance(change, str) or change not in GRID_OPERATIONS:
            raise ValueError("줄·칸 편집 명령을 다시 선택하세요.")
        command, axis, deleting, direction = GRID_OPERATIONS[change]
        dimension = ("rows", "columns")[axis]
        if deleting:
            if chosen[dimension] <= 1:
                raise ValueError("마지막 줄이나 칸은 삭제할 수 없습니다.")
            if value.get("confirmed") is not True:
                raise ValueError("줄 또는 칸과 그 안의 내용을 삭제하려면 확인하세요.")
        axis_name = ("row", "col")[axis]
        with tempfile.TemporaryDirectory(dir=self.directory.name) as work:
            folder = Path(work)
            document, output = (folder/f"{name}.{self.extension}" for name in ("document", "edited"))
            document.write_bytes(source)
            flags = ["--" + axis_name, str(coordinate[axis])]
            if direction:
                flags.append(direction)
            native, modified, after = edit_native_table(
                document, output, chosen["address"], command, flags,
                message=GRID_MISMATCH, allow_resize=True)
            binary = self.extension == "hwp"
            rewritten = verify_grid(hangul_xml(document), hangul_xml(output), native,
                                    modified, change, coordinate, binary)
            if binary:
                verify_hwp_streams(source, output, native["section"])
            else:
                output.write_bytes(rewritten)
                if exported_tables(output) != after:
                    raise ValueError(GRID_MISMATCH)
            if axis == 1 and not deleting:
                number = self.current_page
                placement = json.loads(rhwp(["dump-pages", str(output), "-p", str(number-1), "--json"],
                                            folder))["pages"][0]
                _, visible, _ = hwp_locations(output, number, placement, {})
                grid = next((table for table in visible if table["address"] == chosen["address"]), None)
                if grid is None or grid["box"][0] < 0 or grid["box"][2] > 1:
                    raise ValueError("칸을 추가하면 표가 쪽 너비를 벗어납니다.")
            return output.read_bytes()

    def subdivide_cell(self, source, chosen, coordinate, value):
        if not chosen["subdivision"] or value.get("end") is not None:
            raise ValueError(UNSAFE_SUBDIVISION)
        counts = (value.get("rows"), value.get("columns"))
        if (any(type(count) is not int or not 1 <= count <= MAX_SUBDIVISION for count in counts)
                or counts == (1, 1)):
            raise ValueError("나눌 행·열 수를 각각 1~6으로 지정하세요. 적어도 한쪽은 2 이상이어야 합니다.")
        if (chosen["rows"]+counts[0]-1) * (chosen["columns"]+counts[1]-1) > 1000:
            raise ValueError("나눈 뒤 표의 격자가 1,000칸을 초과할 수 없습니다.")

        with tempfile.TemporaryDirectory(dir=self.directory.name) as work:
            folder = Path(work)
            document, output = (folder/f"{name}.{self.extension}" for name in ("document", "edited"))
            document.write_bytes(source)
            row, col = coordinate
            args = ["--row", str(row), "--col", str(col), "--rows", str(counts[0]), "--cols", str(counts[1]),
                    "--equal-row-height"]
            native, modified, after = edit_native_table(document, output, chosen["address"], "split-cell-into", args,
                                                        message=SUBDIVISION_MISMATCH, allow_resize=True)
            binary = self.extension == "hwp"
            rewritten = verify_subdivision(hangul_xml(document), hangul_xml(output), native, modified,
                                           tuple(coordinate), counts, binary)
            if binary:
                verify_hwp_streams(source, output, native["section"], SUBDIVISION_MISMATCH)
            else:
                output.write_bytes(rewritten)
                if exported_tables(output) != after:
                    raise ValueError(SUBDIVISION_MISMATCH)
            return output.read_bytes()

    def change_span(self, source, chosen, coordinate, selected, value):
        change = value.get("change")
        if change == "merge":
            end = value.get("end")
            if (not chosen["editable_grid"] or selected.get("span") or not isinstance(end, list)
                    or len(end) != 2 or any(type(i) is not int for i in end)
                    or not any(cell["coordinate"] == end for cell in chosen["cells"]) or end == coordinate):
                raise ValueError("같은 표에서 병합되지 않은 일반 셀 범위를 선택하세요.")
            start = tuple(map(min, zip(coordinate, end)))
            end = tuple(map(max, zip(coordinate, end)))
        elif change == "split" and selected.get("span") and value.get("end") is None:
            start = tuple(coordinate)
            end = tuple(offset + size - 1 for offset, size in zip(start, selected["span"]))
        else:
            raise ValueError(UNSAFE_SPAN)
        if (end[0]-start[0]+1) * (end[1]-start[1]+1) > 1000:
            raise ValueError("한 번에 1,000개를 초과하는 셀 범위는 편집할 수 없습니다.")

        with tempfile.TemporaryDirectory(dir=self.directory.name) as work:
            folder = Path(work)
            document, output = (folder/f"{name}.{self.extension}" for name in ("document", "edited"))
            document.write_bytes(source)
            args = ["--row", str(start[0]), "--col", str(start[1])]
            if change == "merge":
                args += ["--end-row", str(end[0]), "--end-col", str(end[1])]
            command = "merge-cells" if change == "merge" else "split-cell"
            native, modified, after = edit_native_table(
                document, output, chosen["address"], command, args, message=SPAN_MISMATCH)
            binary = self.extension == "hwp"
            rewritten = verify_span(hangul_xml(document), hangul_xml(output), native, modified,
                                    start, end, change == "split", binary)
            if binary:
                verify_hwp_streams(source, output, native["section"], SPAN_MISMATCH)
            else:
                output.write_bytes(rewritten)
                if exported_tables(output) != after:
                    raise ValueError(SPAN_MISMATCH)
            return output.read_bytes()

    def apply(self, source, operation, text_edit, target, value, selection=None):
        if self.extension in HANGUL_EXTENSIONS and operation in HANGUL_FORMATS:
            identity = (target or {}).get("id")
            if text_edit or type(identity) is not int or not any(item["id"] == identity and item.get("hangul_format")
                                                                 for item in self.page_objects):
                raise ValueError("본문 입력을 먼저 저장하고, 서식을 적용할 문단을 선택하세요.")
            section, paragraph = identity >> 32, (identity & 0xffffffff) - 1
            data = self.shape_xml if self.extension == "hwp" else source
            text = self.texts[(section, paragraph)]
            if operation in CHARACTER and selection is not None:
                if (not isinstance(selection, list) or len(selection) != 2
                        or any(type(index) is not int for index in selection)
                        or not 0 <= selection[0] <= selection[1] <= len(text)):
                    raise ValueError("서식을 적용할 한글 글자 범위를 다시 선택하세요.")
                if selection[0] == selection[1]:
                    selection = None
            else:
                selection = None
            if operation == "color" and selection is None:
                raise ValueError("글자 색을 바꿀 범위를 먼저 선택하세요.")
            changed = format_paragraph(data, section, paragraph, operation, value,
                                       selection=selection, original=text)
            if changed == data:
                return source
            return convert_format("hwpx", "hwp", changed) if self.extension == "hwp" else changed
        if operation in SHAPE_EDITS:
            address = value.get("address") if isinstance(value, dict) else None
            chosen = next((shape for shape in self.page_shapes if shape["address"] == address), None)
            if (text_edit or not isinstance(address, list) or any(type(i) is not int for i in address)
                    or value.get("digest") != self.current_key or chosen is None):
                raise ValueError("편집할 도형을 다시 선택하세요.")
            size = value.get("size")
            if (operation == "shape_size" and (not isinstance(size, list) or len(size) != 2
                    or not all(type(number) is int and 2 <= number <= limit for number, limit in zip(size, (180, 250))))):
                raise ValueError("도형 너비는 2~180 mm, 높이는 2~250 mm로 입력하세요.")
            inline = len(address) == 3
            if inline and operation == "shape_size" and any(
                    dimension > limit for dimension, limit in zip(size, chosen["inline_limits"])):
                raise ValueError("도형 크기가 현재 본문 영역을 벗어납니다.")
            changed = apply_shape(self.shape_xml if self.extension == "hwp" else source,
                                  address, operation, size, hangul=self.extension in HANGUL_EXTENSIONS,
                                  binary_word=self.extension == "doc")
            if self.extension == "hwp":
                converted = convert_format("hwpx", "hwp", changed)
                if inline:
                    message = "HWP 변환 중 다른 문서 데이터가 달라져 도형 편집을 적용하지 않았습니다."
                    verify_hwp_streams(source, io.BytesIO(converted), address[0], message)
                    verify_inline_hwp(changed, convert_format("hwp", "hwpx", converted), address[0])
                return converted
            return prepare_legacy_shapes(changed) if self.extension == "doc" else changed
        if (operation in ("edit_table_cell", "edit_table_grid", "edit_table_span", "edit_table_layout",
                          "edit_table_subdivision") and self.extension in HANGUL_EXTENSIONS):
            if not isinstance(value, dict):
                raise ValueError(STALE_CELL)
            address, coordinate = value.get("address"), value.get("coordinate")
            if (text_edit or value.get("digest") != self.current_key or not isinstance(address, list)
                    or not isinstance(coordinate, list) or any(type(i) is not int for i in address + coordinate)):
                raise ValueError(STALE_CELL)
            chosen = next((table for table in self.tables if table["address"] == address), {})
            selected = next((cell for cell in chosen.get("cells", []) if cell["coordinate"] == coordinate), None)
            if selected is None:
                raise ValueError(STALE_CELL)
            if operation == "edit_table_subdivision":
                return self.subdivide_cell(source, chosen, coordinate, value)
            if operation == "edit_table_layout":
                if not selected.get("layout"):
                    raise ValueError(UNSAFE_LAYOUT)
                return self.change_cell_layout(source, chosen, coordinate, value)
            if operation == "edit_table_span":
                return self.change_span(source, chosen, coordinate, selected, value)
            if operation == "edit_table_grid":
                return self.change_grid(source, chosen, coordinate, value)
            if selected.get("span") or selected.get("text") is None:
                raise ValueError(STALE_CELL)
            value = cell_value(value.get("text"))
            if value == selected["text"]:
                return source
            with tempfile.TemporaryDirectory(dir=self.directory.name) as work:
                document = Path(work)/f"document.{self.extension}"
                document.write_bytes(source)
                tables = exported_tables(document)
                native = next(table for table in tables
                              if [table["section"], table["paragraph"], table["control"]] == address)
                if self.extension == "hwpx":
                    return replace_cell(source, native, tuple(coordinate), value)
                output = Path(work)/"edited.hwp"
                row, col = coordinate
                expected = replace_cell(hangul_xml(document), native, tuple(coordinate), value)
                rhwp(["edit", "set-cell", str(document), "--table", str(native["index"]),
                      "--row", str(row), "--col", str(col), "--keep-style", "--text", value,
                      "-o", str(output)], Path(work))
                verify_cell(expected, hangul_xml(output), native, tuple(coordinate))
                return output.read_bytes()
        if operation == "delete_table":
            address = value.get("address") if isinstance(value, dict) else None
            if (text_edit or not isinstance(address, list) or any(type(i) is not int for i in address)
                    or value.get("digest") != self.current_key
                    or not any(table["address"] == address for table in self.tables)):
                raise ValueError("삭제할 표를 다시 선택하세요.")
            if self.extension not in HANGUL_EXTENSIONS:
                return delete_word_table(source, address[0])
            section, paragraph, control = address
            with tempfile.TemporaryDirectory(dir=self.directory.name) as work:
                folder = Path(work)
                document = folder/f"document.{self.extension}"
                output = folder/f"deleted.{self.extension}"
                document.write_bytes(source)
                rhwp(["edit", "delete-control", str(document), "--section", str(section),
                      "--para", str(paragraph), "--ctrl", str(control), "-o", str(output)], folder)
                return output.read_bytes()
        if operation in ("table", "shape"):
            value = insertion_value(operation, value)
            if self.extension in HANGUL_EXTENSIONS:
                identity = (target or {}).get("id")
                selected = next((item for item in self.page_objects if item["id"] == identity), None)
                if not self.hwp_shapes_allowed:
                    raise ValueError("보호된 한글 문서에는 표나 도형을 삽입할 수 없습니다.")
                if text_edit:
                    raise ValueError("입력 중인 본문을 먼저 반영한 뒤 표나 도형을 삽입하세요.")
                if selected and not selected.get("glyph_boxes"):
                    raise ValueError("이 문단은 화면의 글자 위치와 원본을 정확히 연결할 수 없습니다.")
                if (not selected or not isinstance(selection, list) or len(selection) != 2
                        or any(type(offset) is not int for offset in selection)
                        or selection[0] != selection[1] or not 0 <= selection[0] <= len(selected["text"])):
                    raise ValueError("한글 본문에 커서를 놓아 표나 도형을 삽입할 위치를 지정하세요.")
                section, paragraph = identity >> 32, (identity & 0xffffffff) - 1
                with tempfile.TemporaryDirectory(dir=self.directory.name) as work:
                    return insert_hangul_object(Path(work), self.extension, source, operation, value,
                                                section, paragraph, selection[0], selected["text"])
            if text_edit:
                source = self.apply(source, None, text_edit, None, None)
            candidates = [item["id"] for item in self.page_objects]
            if target and target.get("id") in candidates:
                candidates.insert(0, target["id"])
            return insert_word_object(source, operation, value, candidates, self.page_count == 1,
                                      legacy=self.extension == "doc")
        selected = (text_edit or {}).get("target") or target
        if selected is None:
            raise ValueError("편집할 문단을 선택하세요.")
        identity = selected["id"]
        edited = None if not text_edit else valid_text(text_edit["text"].replace("\t", " "), 20000, multiline=True)
        if self.extension in HANGUL_EXTENSIONS:
            section, paragraph = identity >> 32, (identity & 0xffffffff) - 1
            original = self.texts.get((section, paragraph))
            if not original or edited is None:
                raise ValueError("편집할 한글 문단을 확인하세요.")
            with tempfile.TemporaryDirectory(dir=self.directory.name) as work:
                folder = Path(work)
                input_file = folder/f"document.{self.extension}"
                input_file.write_bytes(source)
                return change_hwp(input_file, original, edited, section, paragraph)
        with open_package(source) as archive:
            block = next((item for item in office_blocks(archive, "docx") if item[0] == identity), None)
            if block is None:
                raise ValueError("편집할 Word 문단을 확인하세요.")
            runs = block[-1]
            original = "".join(run.text for run in runs)
            style = {}
            if operation in ("bold", "italic", "underline"):
                attribute = {"bold": "b", "italic": "i", "underline": "u"}[operation]
                mark = f"{{{WORD}}}{attribute}"
                value_name = f"{{{WORD}}}val"
                enabled = [prop.get(value_name, "1") not in ("0", "false", "off", "none")
                           if (prop := run.getparent().find(f"{{{WORD}}}rPr/{mark}")) is not None else False
                           for run in runs]
                style[operation] = not all(enabled)
            elif operation in ("font", "size", "color", "align"):
                style[operation] = value
        content = original if edited is None else edited
        if not style and content == original:
            return source
        return replace_office_blocks("docx", source, [{"id": identity, "text": content, "style": style}],
                                     allow_line_breaks=True)

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
from desktop.shape_editor import apply_shape, prepare_legacy_shapes, shape_catalog
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
                tables.append({"address": [section, *address], "box": [x/width, y/height, (x+w)/width, (y+h)/height],
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
    return items, tables


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


def insert_hangul_object(folder, extension, data, operation, value, section, paragraph):
    source = folder/f"document.{extension}"
    source.write_bytes(data)
    # Insert after the anchor so rhwp keeps any section start on its existing paragraph.
    if operation == "shape":
        paragraph += 1
    coordinates = ["--section", str(section), "--para", str(paragraph)]
    flags = [*coordinates, "--offset", "0"]
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
        if operation == "shape":
            paragraph_file = folder/f"object-paragraph.{extension}"
            rhwp(["edit", "insert-paragraph", str(source), *coordinates, "-o", str(paragraph_file)], folder)
            source = paragraph_file
        rhwp(["edit", f"insert-{operation}", str(source), *flags, "-o", str(destination)], folder)
    except PreviewError as error:
        raise ValueError("한글 개체를 삽입할 수 없습니다. 설치한 rhwp의 삽입 명령과 문서 편집 권한을 확인하세요.") from error
    if value not in ("triangle", "arrow"):
        return destination.read_bytes()
    # rhwp inserts an open triangle. Write a closed, native polygon through HWPX.
    updated = close_hangul_polygon(hangul_xml(source), hangul_xml(destination), section, paragraph, value)
    return convert_format("hwpx", "hwp", updated) if extension == "hwp" else updated


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
        self.texts = {}
        self.locations = []
        self.page_objects = []
        self.page_count = 0
        self.hangul_anchor = None
        self.tables = []
        self.shapes = []
        self.shape_xml = None
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

    def render(self, data, number):
        extension = "docx" if self.extension == "doc" else self.extension
        key = hashlib.sha256(data).hexdigest()
        pdf = preview_path(extension, key, lambda: data)
        sizes = page_dimensions(pdf)
        count = len(sizes)
        number = max(1, min(count, number))
        width, height = sizes[number-1]
        texts, locations, anchor = self.texts, self.locations, self.hangul_anchor
        shapes, shape_xml = self.shapes, self.shape_xml
        changed = key != self.current_key
        with tempfile.TemporaryDirectory(dir=self.directory.name) as work:
            folder = Path(work)
            if extension in HANGUL_EXTENSIONS:
                source = folder/f"document.{extension}"
                source.write_bytes(data)
                if changed:
                    content = hangul_xml(source)
                    texts = dict(hwp_paragraphs(content))
                    shapes = shape_catalog(content, hangul=True) if self.hwp_shapes_allowed else []
                    shape_xml = content if shapes and extension == "hwp" else None
                placement = json.loads(rhwp(["dump-pages", str(source), "-p", str(number-1), "--json"], folder))["pages"][0]
                section = placement["section"]
                anchor = next(
                    ((section, item["paraIndex"])
                     for column in placement["columns"] for item in column["items"]
                     if "paraIndex" in item and not item.get("isEndnote")
                     and (section, item["paraIndex"]) in texts),
                    (section, 0) if count == 1 and (section, 0) in texts else None)
                objects, tables = hwp_locations(source, number, placement, texts)
            else:
                if changed:
                    locations = word_locations(data, pdf, count)
                    tables = word_tables(data)
                    shapes = shape_catalog(data, hangul=False, binary_word=extension == "doc")
                else:
                    tables = self.tables
                objects = locations[number-1]
        png = Path(self.directory.name)/"document.png"
        png.write_bytes(preview_png(pdf, number, prefer_pdfium=True))
        fonts = use_rendered_fonts(objects, pdf, number, Path(self.directory.name) / "fonts")
        self.current_key = key
        self.page_objects = objects
        self.page_count = count
        self.tables, self.shapes, self.shape_xml = tables, shapes, shape_xml
        self.texts, self.locations, self.hangul_anchor = texts, locations, anchor
        return {"page": number, "edit_digest": key, "count": count, "width": width, "height": height,
                "objects": objects, "tables": tables, "shapes": shapes, "image": str(png), "fonts": fonts}

    def page(self, number, operation=None, text_edit=None, target=None, value=None, **_):
        index = self.position
        source = self.data
        if operation == "undo":
            index = max(0, index-1)
            updated = self.states[index]
        elif operation == "redo":
            index = min(len(self.states)-1, index+1)
            updated = self.states[index]
        elif (text_edit or operation) and not (operation == "page" and not text_edit):
            updated = self.apply(source, operation, text_edit, target, value)
        else:
            updated = source
        if len(updated) > MAX_FILE:
            raise ValueError("문서가 최대 파일 크기를 초과했습니다.")
        result = self.render(updated, number)
        if index == self.position and updated != source:
            del self.states[index+1:]
            self.states.append(updated)
            while len(self.states) > 24 or len(self.states) > 2 and sum(map(len, self.states)) > MAX_HISTORY:
                del self.states[1]
            index = len(self.states)-1
        self.position = index
        return dict(result, undo=index > 0, redo=index+1 < len(self.states), dirty=index > 0)

    def apply(self, source, operation, text_edit, target, value):
        if operation in SHAPE_EDITS:
            address = value.get("address") if isinstance(value, dict) else None
            if (text_edit or not isinstance(address, list) or len(address) != 2
                    or any(type(i) is not int for i in address) or value.get("digest") != self.current_key
                    or not any(shape["address"] == address for shape in self.shapes)):
                raise ValueError("편집할 도형을 다시 선택하세요.")
            size = value.get("size")
            if (operation == "shape_size" and (not isinstance(size, list) or len(size) != 2
                    or not all(type(number) is int and 2 <= number <= limit for number, limit in zip(size, (180, 250))))):
                raise ValueError("도형 너비는 2~180 mm, 높이는 2~250 mm로 입력하세요.")
            changed = apply_shape(self.shape_xml if self.extension == "hwp" else source,
                                  address, operation, size, hangul=self.extension in HANGUL_EXTENSIONS,
                                  binary_word=self.extension == "doc")
            if self.extension == "hwp":
                return convert_format("hwpx", "hwp", changed)
            return prepare_legacy_shapes(changed) if self.extension == "doc" else changed
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
            if text_edit:
                source = self.apply(source, None, text_edit, None, None)
            if self.extension in HANGUL_EXTENSIONS:
                selected = None if target is None else next(
                    (item["id"] for item in self.page_objects if item["id"] == target.get("id")), None)
                anchor = (selected >> 32, (selected & 0xffffffff) - 1) if selected else self.hangul_anchor
                if anchor is None:
                    raise ValueError("이 페이지에서 삽입 위치를 확인할 수 없습니다. 본문 문단이 있는 페이지를 선택하세요.")
                with tempfile.TemporaryDirectory(dir=self.directory.name) as work:
                    return insert_hangul_object(Path(work), self.extension, source, operation, value, *anchor)
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

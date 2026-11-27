"""Modify a copy of a document for GitDoc's existing draft review flow."""

import io
import json
import os
import re
import tempfile
from bisect import bisect_left, bisect_right
from copy import copy
from itertools import islice
from math import isfinite
from pathlib import Path
from zipfile import BadZipFile, ZipFile

from lxml import etree
from office_oxide import EditableDocument, OfficeOxideError
from pypdf import PdfReader, PdfWriter
from pypdf.annotations import Text
from pypdf.errors import PyPdfError

from extract import ZIP_MARKERS, ordered_parts
from preview import PreviewError, rhwp_command, run_converter

MODES = {"hwp": "replace", "hwpx": "replace", "docx": "replace", "pptx": "replace",
         "xlsx": "cell", "pdf": "page"}
WORD = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
SLIDE = "http://schemas.openxmlformats.org/drawingml/2006/main"
MAX_REPLACEMENTS = 10000
XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"


def valid_text(value, limit, *, multiline=False):
    value = value.replace("\r\n", "\n")
    forbidden = r"[\x00-\x08\x0b-\x1f\ufffe\uffff]" if multiline else r"[\x00-\x1f\ufffe\uffff]"
    if len(value) > limit or re.search(forbidden, value):
        raise ValueError("입력 길이 또는 사용할 수 없는 문자를 확인하세요.")
    return value


def parse_part(package, name):
    if package.getinfo(name).file_size > 16 * 1024 * 1024:
        raise ValueError("편집할 문서 구성 요소가 너무 큽니다.")
    parser = etree.XMLParser(resolve_entities=False, load_dtd=False, no_network=True)
    root = etree.fromstring(package.read(name), parser=parser)
    if root.getroottree().docinfo.doctype:
        raise ValueError("문서 XML에 지원하지 않는 선언이 있습니다.")
    return root


def rewrite_package(package, parts):
    output = io.BytesIO()
    with ZipFile(output, "w") as updated:
        for entry in package.infolist():
            if entry.filename in parts:
                part = parts[entry.filename]
                data = part if isinstance(part, bytes) else etree.tostring(
                    part, encoding="UTF-8", xml_declaration=True)
            else:
                data = package.read(entry)
            updated.writestr(copy(entry), data)
    return output.getvalue()


def open_package(data):
    archive = ZipFile(io.BytesIO(data))
    entries = archive.infolist()
    if (sum(entry.file_size for entry in entries) > 100 * 1024 * 1024 or
        len({entry.filename for entry in entries}) != len(entries) or
        any(entry.filename.lower().startswith("_xmlsignatures/") for entry in entries)):
        archive.close()
        raise ValueError("서명된 문서이거나 편집할 수 있는 문서 크기를 초과했습니다.")
    return archive


def text_groups(paragraph, namespace):
    tag = f"{{{namespace}}}"
    separators = {tag + name for name in ("tab", "br", "cr", "fldChar", "instrText")}
    runs = []
    for node in paragraph.iterdescendants():
        if next(node.iterancestors(tag=tag + "p"), None) is not paragraph:
            continue
        if node.tag in separators:
            if runs:
                yield runs
                runs = []
        if node.tag == tag + "t" and node.text:
            runs.append(node)
    if runs:
        yield runs


def edit_runs(runs, find, replacement, limit):
    original = "".join(node.text for node in runs)
    matches = list(islice(re.finditer(re.escape(find), original), limit + 1))
    if len(matches) > limit:
        raise ValueError("한 번에 바꿀 수 있는 문구 수를 초과했습니다. 찾을 문구를 구체화하세요.")
    if not matches:
        return 0
    starts = [0]
    for node in runs:
        starts.append(starts[-1] + len(node.text))
    for match in reversed(matches):
        first = bisect_right(starts, match.start()) - 1
        last = bisect_left(starts, match.end()) - 1
        start = match.start() - starts[first]
        end = match.end() - starts[last]
        left, right = runs[first], runs[last]
        suffix = right.text[end:]
        left.text = left.text[:start] + replacement + (suffix if left is right else "")
        if left is not right:
            right.text = suffix
            for node in runs[first + 1:last]:
                node.text = ""
    if matches:
        for node in runs:
            if (node.text or "").strip() != node.text:
                node.set(XML_SPACE, "preserve")
    return len(matches)


def oxide_text_parts(extension, data, find, replacement, expected, count):
    """Use the Rust editor's XML when it preserves GitDoc's edit and formatting."""
    try:
        document = EditableDocument.from_bytes(data, extension)
        try:
            if document.replace_text(find, replacement) != count:
                return expected
            edited = document.to_bytes()
        finally:
            document.close()
        with open_package(edited) as archive:
            parts = {}
            for name, reference in expected.items():
                candidate = parse_part(archive, name)
                if etree.tostring(candidate, method="c14n") != etree.tostring(reference, method="c14n"):
                    return expected
                parts[name] = archive.read(name)
            return parts
    except (OfficeOxideError, BadZipFile, KeyError, ValueError, etree.LxmlError):
        return expected


def office_replace(extension, data, find, replacement):
    parts = {}
    count = 0
    with open_package(data) as archive:
        names = archive.namelist()
        if extension == "docx":
            if "word/settings.xml" in names:
                settings = parse_part(archive, "word/settings.xml")
                if any(node.get(f"{{{WORD}}}enforcement") in ("1", "true", "on")
                       for node in settings.findall(".//{*}documentProtection")):
                    raise ValueError("편집이 제한된 Word 문서입니다.")
            paths = [name for name in names
                     if re.fullmatch(r"word/(document|header\d+|footer\d+|footnotes|endnotes)\.xml", name)]
            namespace = WORD
        else:
            paths = [path for _, path in ordered_parts(archive, ZIP_MARKERS["pptx"], "sldId", ())]
            namespace = SLIDE
        for name in paths:
            root = parse_part(archive, name)
            changed = 0
            for paragraph in root.iter(f"{{{namespace}}}p"):
                for runs in text_groups(paragraph, namespace):
                    changed += edit_runs(runs, find, replacement, MAX_REPLACEMENTS - count - changed)
            if changed:
                parts[name] = root
                count += changed
        if not count:
            raise ValueError("편집 가능한 텍스트에서 찾을 문구가 없습니다.")
        parts = oxide_text_parts(extension, data, find, replacement, parts, count)
        return rewrite_package(archive, parts)


def hancom_replace(extension, data, find, replacement, max_file):
    with tempfile.TemporaryDirectory() as directory:
        folder = Path(directory)
        source, destination = (folder / f"{name}.{extension}" for name in ("original", "edited"))
        source.write_bytes(data)
        try:
            command = [rhwp_command(), "edit", "replace-text", str(source),
                       "--find", find, "--replace", replacement]
            env = dict(os.environ, RHWP_WEBFONT_OFFLINE="1")
            result = json.loads(run_converter([*command, "--dry-run", "--json"], folder, env=env))
            count = result["replacedCount"]
            if not count:
                raise ValueError("편집 가능한 텍스트에서 찾을 문구가 없습니다.")
            if count > MAX_REPLACEMENTS:
                raise ValueError("한 번에 바꿀 수 있는 문구 수를 초과했습니다. 찾을 문구를 구체화하세요.")
            run_converter([*command, "-o", str(destination)], folder, env=env)
        except (PreviewError, json.JSONDecodeError, KeyError) as error:
            raise ValueError("한글 문서를 편집하지 못했습니다. rhwp 편집 기능과 문서 형식을 확인하세요.") from error
        if not destination.is_file():
            raise ValueError("편집 가능한 텍스트에서 찾을 문구가 없습니다.")
        if destination.stat().st_size > max_file:
            raise ValueError("편집 결과가 파일 크기 한도를 초과했습니다.")
        return destination.read_bytes()


def cell_position(address):
    match = re.fullmatch(r"([A-Z]{1,3})([1-9][0-9]{0,6})", address)
    if not match:
        raise ValueError("A1과 같은 셀 주소를 입력하세요.")
    letters, row = match.groups()
    col = 0
    for letter in letters:
        col = col * 26 + ord(letter) - 64
    if int(row) > 1048576 or col > 16384:
        raise ValueError("엑셀 셀 주소 범위를 확인하세요.")
    return int(row), col


def update_cell(data, sheet_name, address, kind, value):
    row, col = cell_position(address)
    if kind not in ("text", "number", "formula", "clear"):
        raise ValueError("셀 값 종류를 선택하세요.")
    if kind == "number":
        if (not re.fullmatch(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[Ee][+-]?[0-9]{1,3})?", value)
            or not isfinite(float(value))):
            raise ValueError("숫자 형식이 올바르지 않습니다.")
    if kind == "formula" and (not value.startswith("=") or not value[1:].strip()):
        raise ValueError("수식은 =으로 시작해야 합니다.")
    with open_package(data) as archive:
        paths = {name: path for (name,), path in
                 ordered_parts(archive, ZIP_MARKERS["xlsx"], "sheet", ("name",))}
        if sheet_name not in paths:
            raise ValueError("시트를 찾을 수 없습니다.")
        path = paths[sheet_name]
        root = parse_part(archive, path)
        for merged in root.findall("./{*}mergeCells/{*}mergeCell"):
            addresses = merged.get("ref", "").split(":")
            if len(addresses) != 2:
                continue
            top, left = cell_position(addresses[0])
            bottom, right = cell_position(addresses[1])
            if top <= row <= bottom and left <= col <= right and address != addresses[0]:
                raise ValueError("병합 셀의 왼쪽 위 주소에서 편집하세요.")
        if root.find("{*}sheetProtection") is not None:
            raise ValueError("보호된 시트입니다.")
        sheet_data = root.find("{*}sheetData")
        if sheet_data is None:
            raise ValueError("편집할 수 없는 시트입니다.")
        namespace = root.tag.rsplit("}", 1)[0] + "}"
        row_node = next((node for node in sheet_data if node.get("r") == str(row)), None)
        cell = next((node for node in row_node if node.get("r") == address), None) if row_node is not None else None
        if kind == "clear" and (cell is None or all(
                cell.find("{*}" + name) is None for name in ("f", "v", "is"))):
            raise ValueError("이 셀은 이미 비어 있습니다.")
        if row_node is None:
            row_node = etree.Element(namespace + "row", r=str(row))
            next_row = next((node for node in sheet_data if int(node.get("r", 0)) > row), None)
            sheet_data.insert(sheet_data.index(next_row) if next_row is not None else len(sheet_data), row_node)
        if cell is None:
            cell = etree.Element(namespace + "c", r=address)
            next_cell = next((node for node in row_node if node.tag == namespace + "c"
                              and cell_position(node.get("r", ""))[1] > col), None)
            row_node.insert(row_node.index(next_cell) if next_cell is not None else len(row_node), cell)
        formula = cell.find("{*}f")
        if formula is not None and formula.get("t") in ("array", "shared", "dataTable"):
            raise ValueError("공유·배열 수식 셀은 기존 편집기에서 수정하세요.")
        cached = cell.findtext("{*}v")
        same_number = kind == "number" and cell.get("t", "n") == "n" and cached == value and formula is None
        same_formula = kind == "formula" and formula is not None and formula.text == value[1:]
        if same_number or same_formula:
            raise ValueError("셀 값이 바뀌지 않았습니다.")
        for child in list(cell):
            if child.tag in (namespace + "v", namespace + "f", namespace + "is"):
                cell.remove(child)
        cell.attrib.pop("t", None)
        if kind == "text":
            cell.set("t", "inlineStr")
            inline = etree.Element(namespace + "is")
            etree.SubElement(inline, namespace + "t", {XML_SPACE: "preserve"}).text = value
            cell.insert(0, inline)
        elif kind != "clear":
            new = etree.Element(namespace + ("f" if kind == "formula" else "v"))
            new.text = value[1:] if kind == "formula" else value
            cell.insert(0, new)
        dimensions = root.find("{*}dimension")
        if dimensions is not None:
            addresses = dimensions.get("ref", "").split(":")
            if addresses:
                first_row, first_col = cell_position(addresses[0])
                last_row, last_col = cell_position(addresses[-1])
                if not (first_row <= row <= last_row and first_col <= col <= last_col):
                    root.remove(dimensions)
        workbook_path = ZIP_MARKERS["xlsx"]
        workbook = parse_part(archive, workbook_path)
        calculation = workbook.find("{*}calcPr")
        changes = {path: root}
        if calculation is not None:
            calculation.set("fullCalcOnLoad", "1")
            changes[workbook_path] = workbook
        return rewrite_package(archive, changes)


def read_pdf_notes(data, page_number):
    reader = PdfReader(io.BytesIO(data))
    if reader.is_encrypted or not 1 <= page_number <= len(reader.pages):
        return []
    notes = []
    for reference in reader.pages[page_number - 1].get("/Annots", []):
        annotation = reference.get_object()
        if hasattr(annotation, "get") and annotation.get("/Subtype") == "/Text":
            contents = str(annotation.get("/Contents", "")).strip()
            if contents:
                notes.append(contents[:2000])
                if len(notes) == 100:
                    break
    return notes


def edit_pdf(data, fields, source=None):
    try:
        page_number = int(fields.get("page", ""))
    except (ValueError, TypeError):
        page_number = 0
    try:
        return update_pdf(data, page_number, fields.get("operation"),
                          valid_text(fields.get("note", ""), 1000, multiline=True), source)
    except (PyPdfError, RecursionError, OSError, KeyError) as error:
        raise ValueError("PDF 구조를 읽을 수 없습니다. 원본을 확인하세요.") from error


def update_pdf(data, page_number, operation, note, source=None):
    reader = PdfReader(io.BytesIO(data))
    if reader.is_encrypted or any(field.get("/FT") == "/Sig" and field.get("/V")
                                  for field in (reader.get_fields() or {}).values()):
        raise ValueError("암호가 걸렸거나 서명된 PDF입니다.")
    base_count = len(reader.pages)
    if not 1 <= page_number <= base_count + (operation == "insert"):
        raise ValueError("PDF 페이지 번호를 확인하세요.")
    writer = PdfWriter(clone_from=reader)
    inserted = 0
    if operation == "insert":
        if source is None:
            reference = writer.pages[min(page_number, base_count) - 1]
            box = reference.mediabox
            writer.insert_blank_page(float(box.width), float(box.height), index=page_number - 1).rotate(reference.rotation)
            inserted = 1
        else:
            incoming = PdfReader(io.BytesIO(source))
            if incoming.is_encrypted or not incoming.pages:
                raise ValueError("암호가 없고 페이지가 있는 PDF를 선택하세요.")
            inserted = len(incoming.pages)
            for offset, page in enumerate(incoming.pages):
                writer.insert_page(page, index=page_number - 1 + offset)
    elif operation in ("left", "right"):
        page = writer.pages[page_number - 1]
        page.rotate(90 if operation == "right" else -90)
    elif operation == "note" and note.strip():
        page = writer.pages[page_number - 1]
        box = page.cropbox
        left, top = float(box.left), float(box.top)
        width, height = min(26, float(box.width)), min(26, float(box.height))
        writer.add_annotation(page_number - 1, Text(rect=(left, top - height, left + width, top), text=note))
    else:
        raise ValueError("PDF 작업을 선택하거나 메모 내용을 입력하세요.")
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue(), (base_count, page_number if inserted else None, inserted)


def modify_document(extension, data, fields, max_file):
    kind = MODES.get(extension)
    try:
        if kind == "replace":
            find = valid_text(fields.get("find", ""), 300)
            replacement = valid_text(fields.get("replacement", ""), 2000)
            if not find or find == replacement:
                raise ValueError("찾을 문구와 바꿀 문구를 확인하세요.")
            if extension in ("hwp", "hwpx"):
                return hancom_replace(extension, data, find, replacement, max_file)
            return office_replace(extension, data, find, replacement)
        if kind == "cell":
            return update_cell(data, fields.get("sheet", ""), fields.get("cell", "").strip().upper(),
                               fields.get("value_kind"), valid_text(fields.get("value", ""), 10000,
                                                                  multiline=True))
        if kind == "page":
            return edit_pdf(data, fields)[0]
    except (BadZipFile, KeyError, etree.XMLSyntaxError, PyPdfError, OSError, RecursionError) as error:
        raise ValueError("문서 구조를 읽을 수 없습니다. 원본을 확인하세요.") from error
    raise ValueError("이 형식의 내장 편집을 지원하지 않습니다.")

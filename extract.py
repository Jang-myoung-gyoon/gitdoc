import io
import os
import posixpath
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from zipfile import ZipFile

import olefile
import xlrd
from defusedxml import DefusedXmlException, ElementTree as xml
from pypdf import PdfReader

from delimited import lines as table_lines, records
from formats import DELIMITED_EXTENSIONS, MACRO_CONTENT_TYPES, ODF_MIMETYPES

OLE = bytes.fromhex("D0CF11E0A1B11AE1")
ZIP_MARKERS = {
    "docx": "word/document.xml",
    "pptx": "ppt/presentation.xml",
    "xlsx": "xl/workbook.xml",
    "hwpx": "Contents/content.hpf",
    "docm": "word/document.xml",
    "pptm": "ppt/presentation.xml",
    "xlsm": "xl/workbook.xml",
}


def matches_format(extension, data):
    if extension in DELIMITED_EXTENSIONS:
        has_records = False
        for record in records(extension, data):
            has_records |= bool(record)
        return has_records
    if extension == "pdf":
        return b"%PDF-" in data[:1024]
    if data.startswith(OLE):
        with olefile.OleFileIO(io.BytesIO(data)) as container:
            if extension == "hwp":
                return container.exists("FileHeader") and container.openstream("FileHeader").read(32).startswith(b"HWP Document File")
            if extension == "doc":
                return container.exists("WordDocument")
            if extension == "ppt":
                return container.exists("PowerPoint Document")
            if extension == "xls":
                return container.exists("Workbook") or container.exists("Book")
            return extension in ZIP_MARKERS and extension != "hwpx" and container.exists("EncryptedPackage")
    if extension in ZIP_MARKERS or extension in ODF_MIMETYPES:
        with ZipFile(io.BytesIO(data)) as package:
            names = package.namelist()
            if extension in ODF_MIMETYPES:
                if "mimetype" not in names or "content.xml" not in names:
                    return False
                expected = ODF_MIMETYPES[extension]
                return package.getinfo("mimetype").file_size == len(expected) and package.read("mimetype") == expected
            marker = ZIP_MARKERS[extension]
            if marker not in names:
                return False
            if extension not in MACRO_CONTENT_TYPES:
                return True
            try:
                types = read_xml(package, "[Content_Types].xml")
            except (KeyError, xml.ParseError, DefusedXmlException):
                return False
            override = types.find(f"./{{*}}Override[@PartName='/{marker}']")
            default = types.find("./{*}Default[@Extension='xml']")
            media_type = override if override is not None else default
            return media_type is not None and media_type.get("ContentType") == MACRO_CONTENT_TYPES[extension]
    return extension == "xls" and data[:2] in (b"\x09\x00", b"\x09\x02", b"\x09\x04", b"\x09\x08")


def read_xml(package, name):
    if package.getinfo(name).file_size > 16 * 1024 * 1024:
        raise ValueError("XML size limit")
    return xml.fromstring(package.read(name))


def paragraph_text(root):
    return ["".join(part.text or "" for part in paragraph.findall(".//{*}t"))
            for paragraph in root.findall(".//{*}p")]


def ordered_parts(package, name, item_kind, attributes):
    root = read_xml(package, name)
    folder, base = posixpath.split(name)
    rel = read_xml(package, posixpath.join(folder, "_rels", base + ".rels"))
    targets = {item.get("Id"): item.get("Target") for item in rel
               if item.get("TargetMode") != "External"}
    for entry in root.findall(".//{*}" + item_kind):
        key = entry.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
        target = targets[key]
        path = target.lstrip("/") if target.startswith("/") else posixpath.normpath(posixpath.join(folder, target))
        yield tuple(entry.get(attr) for attr in attributes), path


def office_lines(extension, data):
    with ZipFile(io.BytesIO(data)) as package:
        marker = ZIP_MARKERS[extension]
        if extension in ("docx", "docm"):
            yield from paragraph_text(read_xml(package, marker))
        elif extension == "hwpx":
            sections = [name for name in package.namelist()
                        if re.fullmatch(r"Contents/section\d+\.xml", name)]
            for name in sorted(sections, key=lambda item: int(re.search(r"\d+", item).group())):
                yield from paragraph_text(read_xml(package, name))
        elif extension in ("pptx", "pptm"):
            for number, (_, path) in enumerate(ordered_parts(package, marker, "sldId", ()), 1):
                yield f"[슬라이드 {number}]"
                yield from paragraph_text(read_xml(package, path))
        else:
            shared = []
            if "xl/sharedStrings.xml" in package.namelist():
                shared = ["".join(node.itertext()) for node in
                          read_xml(package, "xl/sharedStrings.xml").findall("./{*}si")]
            for (sheet,), path in ordered_parts(package, marker, "sheet", ("name",)):
                for cell in read_xml(package, path).findall(".//{*}c"):
                    value = cell.findtext("{*}v")
                    kind = cell.get("t")
                    if kind == "inlineStr":
                        value = "".join(node.text or "" for node in cell.findall(".//{*}t"))
                    elif kind == "s" and value is not None:
                        value = shared[int(value)]
                    elif kind == "b":
                        value = "TRUE" if value == "1" else "FALSE"
                    formula = cell.findtext("{*}f")
                    if formula:
                        value = f"={formula}" + (f" → {value}" if value else "")
                    if value is not None:
                        yield f"{sheet}!{cell.get('r')}  {value!r}"


def odf_lines(data):
    with ZipFile(io.BytesIO(data)) as package:
        content = read_xml(package, "content.xml")
        namespace = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"
        for node in content.iter():
            if node.tag in (namespace + "p", namespace + "h"):
                yield "".join(node.itertext())


def comparison_text(version):
    text = version["extracted"]
    extension = version["extension"]
    tag = "페이지" if extension == "pdf" else "슬라이드" if extension in ("pptx", "pptm") else None
    if tag is None:
        return text
    marker = re.compile(rf"\[{tag} \d+\]")
    return "\n".join(line for line in text.splitlines() if not marker.fullmatch(line))


def extract(extension, data):
    if extension in ZIP_MARKERS:
        yield from office_lines(extension, data)
    elif extension in DELIMITED_EXTENSIONS:
        yield from table_lines(records(extension, data))
    elif extension in ODF_MIMETYPES:
        yield from odf_lines(data)
    elif extension == "pdf":
        for number, page in enumerate(PdfReader(io.BytesIO(data)).pages, 1):
            lines = (page.extract_text() or "").splitlines()
            if any(line.strip() for line in lines):
                yield f"[페이지 {number}]"
                yield from lines
    elif extension == "xls":
        workbook = xlrd.open_workbook(file_contents=data, on_demand=True)
        try:
            for sheet in workbook.sheets():
                for row in range(sheet.nrows):
                    for column, cell in enumerate(sheet.row(row)):
                        if cell.ctype not in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK):
                            address = xlrd.cellname(row, column)
                            yield f"{sheet.name}!{address}  {cell.value!r}"
        finally:
            workbook.release_resources()
    else:
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            source = folder / f"document.{extension}"
            source.write_bytes(data)
            if extension == "hwp":
                command = Path(sys.executable).with_name("hwp5txt.exe" if os.name == "nt" else "hwp5txt")
                # Release packages ship the converter module without its launcher script.
                command = [str(command)] if command.is_file() else [
                    sys.executable, "-c", "from hwp5.hwp5txt import main; main()"]
                try:
                    result = subprocess.run([*command, str(source)], capture_output=True,
                                            check=True, timeout=20)
                except (OSError, subprocess.SubprocessError):
                    from preview import rhwp_command, run_converter

                    output = folder / "document.hwpx"
                    run_converter([rhwp_command(), "export-hwpx", str(source), str(output)], folder)
                    yield from office_lines("hwpx", output.read_bytes())
                else:
                    yield from result.stdout.decode("utf-8").splitlines()
            else:
                from preview import BUNDLED_BIN, executable, office_profile, run_converter

                command = executable("soffice", "DOCVERS_SOFFICE", BUNDLED_BIN / "soffice")
                profile = office_profile(folder)
                target = {"doc": "docx", "ppt": "pptx"}[extension]
                run_converter([command, f"-env:UserInstallation={profile.as_uri()}",
                               "--headless", "--convert-to", target, "--outdir", directory,
                               str(source)], folder)
                yield from office_lines(target, (folder / f"document.{target}").read_bytes())


def extract_for_version(extension, data):
    lines = []
    total_size = 0
    try:
        for line in extract(extension, data):
            line = line.strip()
            if not line:
                continue
            total_size += len(line)
            if len(lines) >= 2500 or len(line) > 10000 or total_size > 500000:
                return "", "텍스트 비교 한도를 초과했습니다. 원본을 확인하세요."
            lines.append(line)
    except Exception:
        # The original remains available when a parser cannot read the document.
        return "", "텍스트를 추출할 수 없습니다. 원본을 확인하세요."
    if not lines:
        return "", "비교할 텍스트가 없습니다. 스캔본이나 이미지가 포함된 원본을 확인하세요."
    return "\n".join(lines), ""

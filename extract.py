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
from defusedxml import ElementTree as xml
from pypdf import PdfReader

OLE = bytes.fromhex("D0CF11E0A1B11AE1")
ZIP_MARKERS = {
    "docx": "word/document.xml",
    "pptx": "ppt/presentation.xml",
    "xlsx": "xl/workbook.xml",
    "hwpx": "Contents/content.hpf",
}


def matches_format(extension, data):
    if extension == "pdf":
        return b"%PDF-" in data[:1024]
    if data.startswith(OLE):
        with olefile.OleFileIO(io.BytesIO(data)) as container:
            if extension == "hwp":
                return container.exists("FileHeader") and container.openstream("FileHeader").read(32).startswith(b"HWP Document File")
            if extension == "ppt":
                return container.exists("PowerPoint Document")
            if extension == "xls":
                return container.exists("Workbook") or container.exists("Book")
            return extension in ("docx", "pptx", "xlsx") and container.exists("EncryptedPackage")
    if extension in ZIP_MARKERS:
        with ZipFile(io.BytesIO(data)) as package:
            return ZIP_MARKERS[extension] in package.namelist()
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
        if extension == "docx":
            yield from paragraph_text(read_xml(package, marker))
        elif extension == "hwpx":
            sections = [name for name in package.namelist()
                        if re.fullmatch(r"Contents/section\d+\.xml", name)]
            for name in sorted(sections, key=lambda item: int(re.search(r"\d+", item).group())):
                yield from paragraph_text(read_xml(package, name))
        elif extension == "pptx":
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


def extract(extension, data):
    if extension in ZIP_MARKERS:
        yield from office_lines(extension, data)
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
                try:
                    result = subprocess.run([str(command), str(source)], capture_output=True,
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
                run_converter([command, f"-env:UserInstallation={profile.as_uri()}",
                               "--headless", "--convert-to", "pptx", "--outdir", directory,
                               str(source)], folder)
                yield from office_lines("pptx", (folder / "document.pptx").read_bytes())


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

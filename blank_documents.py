"""Generate a minimum blank document and register it in the current workspace."""

import io
import re
from pathlib import Path
from tempfile import TemporaryDirectory

from lxml import etree
from office_oxide import XlsxWriter, create_from_markdown
from pptx import Presentation
from pypdf import PdfWriter

from desktop_link import desktop_url, document_path
from editing import WORD, open_package, parse_part, rewrite_package
from store import create_document, database, find_document
from uploads import prepare_upload

BLANK_FORMATS = ("docx", "pptx", "xlsx", "pdf")


def blank_bytes(extension):
    if extension == "docx":
        with TemporaryDirectory(prefix="gitdoc-blank-") as folder:
            path = Path(folder) / "document.docx"
            create_from_markdown("", "docx", str(path))
            data = path.read_bytes()
        part = "word/document.xml"
        tag = f"{{{WORD}}}"
        with open_package(data) as package:
            document = parse_part(package, part)
            body = document.find(tag + "body")
            if len(body) == 0 or body[0].tag != tag + "p":
                body.insert(0, etree.Element(tag + "p"))
            section = body.find(tag + "sectPr")
            if section is None:
                section = etree.SubElement(body, tag + "sectPr")
                etree.SubElement(section, tag + "pgSz", {tag + "w": "11906", tag + "h": "16838"})
            return rewrite_package(package, {part: document})
    if extension == "xlsx":
        workbook = XlsxWriter()
        workbook.add_sheet("Sheet1")
        return workbook.to_bytes()
    if extension in ("pptx", "pdf"):
        output = io.BytesIO()
        if extension == "pptx":
            presentation = Presentation()
            presentation.slides.add_slide(presentation.slide_layouts[6])
            presentation.save(output)
        else:
            writer = PdfWriter()
            writer.add_blank_page(595.28, 841.89)
            writer.write(output)
        return output.getvalue()
    raise ValueError("빈 문서는 DOCX, PPTX, XLSX, PDF 형식으로 만들 수 있습니다.")


def create_blank(title, extension):
    if not isinstance(title, str):
        raise ValueError("새 문서 제목을 입력하세요.")
    title = title.strip()
    if (not 1 <= len(title) <= 120 or title.endswith(".")
            or re.search(r'[\x00-\x1f<>:"/\\|?*]', title)
            or re.match(r"(?i)^(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)", title)
            or len(f"{title}.{extension}".encode()) > 255):
        raise ValueError("새 문서 제목을 파일 이름으로 사용할 수 있게 입력하세요.")
    upload = prepare_upload(f"{title}.{extension}", blank_bytes(extension))
    with database(write=True) as db:
        document = create_document(db, title, upload)
        version = find_document(db, document)["current_id"]
    return {"document_id": document, "version_id": version, "version_label": "v1.0.0",
            "desktop_url": desktop_url(document_path(document, version=version))}

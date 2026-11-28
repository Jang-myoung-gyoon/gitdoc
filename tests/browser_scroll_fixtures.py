"""Start the normal GitDoc browser app with disposable PDF and XLSX versions."""

import json
import sys
from pathlib import Path

from openpyxl import Workbook
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
from werkzeug.serving import WSGIRequestHandler, make_server

from app import app
from store import initialize
from mcp_server import register_document
from uploads import load_local_file
from workflow import save_proposal
from browser_viewer import WORKSPACE_PREFIX


def make_pdf(path, changed):
    pdf = PdfWriter()
    font = pdf._add_object(DictionaryObject({
        NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
    }))
    for index in range(11):
        page = pdf.add_blank_page(480, 700)
        page[NameObject("/Resources")] = DictionaryObject({
            NameObject("/Font"): DictionaryObject({NameObject("/F1"): font}),
        })
        content = DecodedStreamObject()
        marker = "changed" if changed and index == 4 else "fixture"
        content.set_data(f"BT /F1 20 Tf 38 610 Td (Page {index + 1} {marker}) Tj ET".encode("ascii"))
        page[NameObject("/Contents")] = pdf._add_object(content)
    pdf.write(path)


def make_sheet(path, changed):
    book = Workbook()
    book.active.title = "Entries"
    book.create_sheet("Summary")
    for sheet in book:
        for row in range(1, 101):
            for col in range(1, 19):
                sheet.cell(row, col, f"{row}:{col}")
        if changed:
            sheet["D26"] = "updated"
    book.save(path)


class QuietRequests(WSGIRequestHandler):
    def log_request(self, code="-", size="-"):
        if str(code).startswith(("4", "5")):
            super().log_request(code, size)


def main():
    folder = Path(sys.argv[1])
    initialize()
    routes = {}
    for suffix, create in (("pdf", make_pdf), ("xlsx", make_sheet)):
        long_name = "아주 긴 한글 파일 이름과 버전 변경 내용을 확인하는 테스트 문서 " * 2
        original, modified = (folder / f"{long_name}{name}.{suffix}" for name in ("원본", "수정본"))
        create(original, False)
        create(modified, True)
        title = "아주 긴 한글 문서 제목에서 확정 버전과 파일 이름 표시를 확인 " * 2
        base = register_document(str(original), title)["version"]
        doc = base["document_id"]
        target = save_proposal(doc, "main", base["id"], load_local_file(str(modified)), "Fixture difference")
        base_path = f"{WORKSPACE_PREFIX}/documents/{doc}"
        routes[suffix] = [f"{base_path}?version={base['id']}",
                          f"{base_path}/compare?base={base['id']}&target={target}"]

    with make_server("127.0.0.1", 0, app, threaded=True, request_handler=QuietRequests) as server:
        print(json.dumps({"origin": f"http://127.0.0.1:{server.server_port}", "routes": routes}), flush=True)
        server.serve_forever()


if __name__ == "__main__":
    main()

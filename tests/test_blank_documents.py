import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pptx import Presentation
from pypdf import PdfReader

import store
from blank_documents import BLANK_FORMATS
from desktop.bridge import CanvasSession, create_blank, edit, library, view
from editing import WORD, open_package, parse_part
from mcp_server import create_blank_document, get_preview_info, list_branches, render_document_page
from preview import BUNDLED_BIN, PreviewError, executable


class BlankDocumentTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        database = patch.object(store, "DATABASE", Path(folder.name)/"blank.sqlite3")
        database.start()
        self.addCleanup(database.stop)
        store.initialize()

    def test_both_entry_points_create_real_empty_documents(self):
        self.assertEqual(library(), [])
        for index, extension in enumerate(BLANK_FORMATS):
            with self.subTest(extension=extension):
                result = (create_blank if index % 2 else create_blank_document)("빈 문서", extension)
                doc, version = result["document_id"], result["version_id"]
                self.assertEqual(result["version_label"], "v1.0.0")
                self.assertIn(f"/documents/{doc}?version={version}", result["desktop_url"])
                with store.database(readonly=True) as db:
                    item = store.find_version(db, doc, version, "content, filename, status, extracted")
                self.assertEqual((item["filename"], item["status"]), (f"빈 문서.{extension}", "approved"))
                if extension != "pptx":
                    self.assertFalse(item["extracted"].strip())
                self.assertTrue(any(row["id"]==doc for row in library()))
                self.assertEqual(list_branches(doc)["branches"][0]["name"], "main")
                if extension == "pdf":
                    self.assertEqual(len(PdfReader(io.BytesIO(item["content"])).pages), 1)
                elif extension == "pptx":
                    slides = Presentation(io.BytesIO(item["content"])).slides
                    self.assertEqual(len(slides), 1)
                    self.assertEqual(len(slides[0].shapes), 0)
                elif extension == "docx":
                    with open_package(item["content"]) as package:
                        root = parse_part(package, "word/document.xml")
                    self.assertEqual(len(root.findall(f".//{{{WORD}}}p")), 1)
                else:
                    sheets = view(doc,[version])["books"][0]
                    self.assertEqual(sheets[0]["name"], "Sheet1")
                    self.assertEqual(sheets[0]["cells"], {})

    def test_first_content_edit_uses_the_normal_version_workflow(self):
        store.set_desktop_review(True)
        initial = create_blank("Workbook QA","xlsx")
        doc, base = initial["document_id"], initial["version_id"]
        result = edit(doc,"main",base,{"sheet":"Sheet1","cell":"A1","value_kind":"text","value":"첫 입력"},"QA first content")
        self.assertEqual(result["status"],"pending")
        with store.database(readonly=True) as db:
            item=store.find_version(db, doc, result["id"],"version_label, extension")
        self.assertEqual((item["version_label"], item["extension"]),("v1.0.1","xlsx"))
        self.assertEqual(view(doc,[result["id"]])["books"][0][0]["cells"]["A1"][0],"첫 입력")

    def test_blank_preview_and_object_insertion(self):
        try:
            executable("soffice", "DOCVERS_SOFFICE", BUNDLED_BIN/"soffice")
        except PreviewError as error:
            self.skipTest(str(error))
        for extension in ("docx","pptx","pdf"):
            with self.subTest(extension=extension):
                initial=create_blank("Blank page QA",extension)
                doc, base=initial["document_id"],initial["version_id"]
                self.assertEqual(get_preview_info(doc,base)["page_counts"],[1])
                _, page=render_document_page(doc,base)
                self.assertEqual(page.to_image_content().mimeType,"image/png")
                if extension in ("docx","pptx"):
                    session=CanvasSession()
                    try:
                        session.open(doc,"main",base,1)
                        session.page(number=1,operation="table",value=[1,1])
                        saved=session.save("Insert first table",1)
                        self.assertEqual(get_preview_info(doc,saved["id"])["page_counts"],[1])
                    finally:
                        session.close()

    def test_invalid_inputs_leave_the_library_empty(self):
        for title, ext in [("", "pdf"), ("../file", "xlsx"), ("CON", "docx"), ("Demo.", "pdf"), ("Good", "hwp")]:
            with self.subTest(title=title, extension=ext), self.assertRaises(ValueError):
                create_blank(title,ext)
        self.assertEqual(library(),[])

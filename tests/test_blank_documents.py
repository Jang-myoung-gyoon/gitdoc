import io
import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pptx import Presentation
from pypdf import PdfReader
from olefile import OleFileIO
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

import store
from blank_documents import BLANK_FORMATS, blank_rhwp_command
from desktop.bridge import CanvasSession, create_blank, edit, library, view
from editing import WORD, open_package, parse_part
from mcp_server import create_blank_document, get_preview_info, list_branches, render_document_page
from preview import BUNDLED_BIN, PreviewError, executable, rhwp_command


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
        for index, extension in enumerate(ext for ext in BLANK_FORMATS if ext not in ("hwp", "hwpx")):
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
        for title, ext in [("", "pdf"), ("../file", "xlsx"), ("CON", "hwp"), ("Demo.", "hwpx"), ("Good", "doc")]:
            with self.subTest(title=title, extension=ext), self.assertRaises(ValueError):
                create_blank(title,ext)
        self.assertEqual(library(),[])

    def test_missing_or_old_rhwp_does_not_register_a_document(self):
        root = store.DATABASE.parent
        with patch.dict(os.environ, {"DOCVERS_RHWP": ""}), patch("blank_documents.DATA", root/"source"), \
                patch("blank_documents.user_data", return_value=root/"installed"), \
                patch("blank_documents.rhwp_command", side_effect=PreviewError("missing")):
            for extension in ("hwp", "hwpx"):
                with self.assertRaisesRegex(ValueError, r"rhwp 0\.9\.0.*tools/rhwp/rhwp"):
                    create_blank("새 한글", extension)
        self.assertEqual(library(), [])

    def test_hangul_creation_skips_old_path_and_respects_explicit_override(self):
        root = store.DATABASE.parent
        installed = root/"installed"
        fallback = installed/"tools/rhwp"/("rhwp.exe" if os.name == "nt" else "rhwp")
        fallback.parent.mkdir(parents=True)
        fallback.touch()
        versions = {"on-PATH": b"rhwp v0.8.7\n", str(fallback): b"rhwp v0.9.0\n",
                    "test-prerelease": b"rhwp v0.9.0-rc.1\n", "explicit": b"rhwp v0.9.1\n"}

        def probe(args, _folder, env):
            if args[0] not in versions:
                raise PreviewError("missing")
            return versions[args[0]]

        with patch("blank_documents.DATA", root/"source"), patch("blank_documents.user_data", return_value=installed), \
                patch("blank_documents.rhwp_command", return_value="on-PATH"), \
                patch("blank_documents.run_converter", side_effect=probe) as run:
            self.assertEqual(blank_rhwp_command(root, {}), str(fallback))
            self.assertEqual([item.args[0][0] for item in run.call_args_list], ["on-PATH", str(fallback)])
            run.reset_mock()
            self.assertEqual(blank_rhwp_command(root, {"DOCVERS_RHWP": "explicit"}), "explicit")
            self.assertEqual([item.args[0][0] for item in run.call_args_list], ["explicit"])
            for bad in ("on-PATH", "broken", "test-prerelease"):
                with self.subTest(override=bad), patch.dict(os.environ, {"DOCVERS_RHWP": bad}):
                    run.reset_mock()
                    with self.assertRaisesRegex(ValueError, r"DOCVERS_RHWP.*rhwp 0\.9\.0"):
                        create_blank("새 한글", "hwp")
                    self.assertEqual([item.args[0][0] for item in run.call_args_list], [bad])
            versions["on-PATH"] = b"rhwp v0.9.1\n"
            run.reset_mock()
            self.assertEqual(blank_rhwp_command(root, {}), "on-PATH")
            self.assertEqual([item.args[0][0] for item in run.call_args_list], ["on-PATH"])
        self.assertEqual(library(), [])

    def test_hangul_desktop_canvas_and_mcp_protocol_round_trip(self):
        try:
            command = rhwp_command()
        except PreviewError as error:
            self.skipTest(str(error))

        async def create_through_mcp():
            repository = Path(__file__).resolve().parents[1]
            params = StdioServerParameters(command=sys.executable, args=[str(repository/"mcp_server.py")],
                                           cwd=str(repository), env=dict(os.environ, DOCVERS_DB=str(store.DATABASE),
                                                                         DOCVERS_RHWP=command))
            results = []
            async with stdio_client(params) as (receive, send):
                async with ClientSession(receive, send) as client:
                    await client.initialize()
                    for extension in ("hwp", "hwpx"):
                        result = await client.call_tool("create_blank_document", {"title": "한글 빈 문서", "extension": extension})
                        self.assertFalse(result.isError, result.content)
                        results.append(result.structuredContent)
            return results

        for ext, mcp_result in zip(("hwp", "hwpx"), asyncio.run(create_through_mcp())):
            with self.subTest(ext=ext):
                result = create_blank("한글 빈 문서", ext)
                doc, base = result["document_id"], result["version_id"]
                self.assertNotEqual(doc, mcp_result["document_id"])
                self.assertEqual((result["version_label"], mcp_result["version_label"]), ("v1.0.0", "v1.0.0"))
                self.assertIn(f"/documents/{doc}?version={base}", result["desktop_url"])
                self.assertEqual(list_branches(doc)["branches"][0]["name"], "main")
                with store.database(readonly=True) as db:
                    initial = store.find_version(db, doc, base, "content, filename, extracted")
                self.assertEqual(initial["filename"], f"한글 빈 문서.{ext}")
                self.assertFalse(initial["extracted"].strip())
                if ext == "hwp":
                    with OleFileIO(io.BytesIO(initial["content"])) as binary:
                        header = binary.openstream("FileHeader").read()
                        self.assertEqual(header[:17], b"HWP Document File")
                        self.assertEqual(header[35], 5)
                        self.assertTrue(binary.exists("BodyText/Section0"))
                else:
                    with open_package(initial["content"]) as package:
                        paragraph = parse_part(package, "Contents/section0.xml").find("{*}p")
                        self.assertIsNotNone(paragraph)
                        self.assertFalse(any(node.text for node in paragraph.iter("{*}t")))
                self.assertEqual(get_preview_info(doc, base)["page_counts"], [1])
                session = CanvasSession()
                expected = ("한글 첫 입력", "한글 다시 편집")
                head = base
                for text in expected:
                    try:
                        page = session.open(doc, "main", head, 1)
                        self.assertEqual(page["count"], 1)
                        target, = page["objects"]
                        self.assertEqual(target["text"], "" if head == base else expected[0])
                        session.page(number=1, text_edit={"target": {"id": target["id"]}, "text": text})
                        draft = session.save("한글 본문 입력", 1)
                        self.assertEqual(draft["status"], "approved")
                        head = draft["id"]
                    finally:
                        session.close()
                    self.assertEqual(get_preview_info(doc, head)["page_counts"], [1])
                    _, page = render_document_page(doc, head)
                    self.assertEqual(page.to_image_content().mimeType, "image/png")
                with store.database(readonly=True) as db:
                    latest = store.find_version(db, doc, head, "extension, extracted")
                    original = store.find_version(db, doc, base, "content")
                self.assertEqual(latest["extension"], ext)
                self.assertIn(expected[-1], latest["extracted"])
                self.assertEqual(original["content"], initial["content"])

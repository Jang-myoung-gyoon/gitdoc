import ctypes
import io
from contextlib import closing
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pypdf import PdfReader, PdfWriter
from pypdfium2 import PdfDocument, raw

import store
from desktop.bridge import edit, edit_pdf_contents
from mcp_server import get_preview_info, register_document, render_document_page
from pdf_text_edit import pdf_text_targets, replace_pdf_text


def sample():
    def add_line(pdf, page, font, text, y):
        data = text.encode("utf-16-le") + b"\x00\x00"
        obj = raw.FPDFPageObj_CreateTextObj(pdf, font, 18)
        assert raw.FPDFText_SetText(obj, (ctypes.c_ushort * (len(data)//2)).from_buffer_copy(data))
        raw.FPDFPageObj_Transform(obj, 1, 0, 0, 1, 30, y)
        raw.FPDFPage_InsertObject(page, obj)
    source = io.BytesIO()
    with PdfDocument.new() as pdf:
        font = raw.FPDFText_LoadStandardFont(pdf, b"Helvetica")
        with closing(pdf.new_page(360, 240)) as page:
            add_line(pdf, page, font, "Alpha original", 170)
            add_line(pdf, page, font, "Alpha original", 100)
            add_line(pdf, page, font, "Keep this text", 55)
            page.gen_content()
        pdf.save(source)
    return source.getvalue()


class PdfTextEditTests(unittest.TestCase):
    def test_same_text_separate_objects_with_original_format_draft_and_render(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(store, "DATABASE", Path(folder)/"qa.sqlite3"):
            store.initialize()
            store.set_desktop_review(True)
            path = Path(folder)/"source.pdf"
            original = sample()
            path.write_bytes(original)
            version = register_document(str(path), "PDF text QA")["version"]
            doc, base = version["document_id"], version["id"]
            targets = edit_pdf_contents(doc, base, 1)
            self.assertEqual([item["text"] for item in targets], ["Alpha original", "Alpha original", "Keep this text"])
            self.assertLess(targets[0]["box"][1], targets[1]["box"][1])
            with self.assertRaisesRegex(ValueError, "다시 선택"):
                replace_pdf_text(original, 1, targets[1]["index"], "wrong", "Beta")
            with self.assertRaises(ValueError):
                replace_pdf_text(original, 1, targets[1]["index"], targets[1]["text"], "WWW" * 30)
            saved = edit(doc, "main", base, {"operation": "replace_text_object", "page": 1,
                         "index": targets[1]["index"], "original": targets[1]["text"], "replacement": "Beta"},
                         "Edit second duplicate")
            self.assertEqual(saved["status"], "pending")
            with store.database(readonly=True) as db:
                original_row = store.find_version(db, doc, base, "content")
                edited = store.find_version(db, doc, saved["id"], "content, extension")
            self.assertEqual(original_row["content"], original)
            self.assertEqual(edited["extension"], "pdf")
            self.assertEqual(PdfReader(io.BytesIO(edited["content"])).pages[0].extract_text().splitlines(),
                             ["Alpha original", "Beta", "Keep this text"])
            self.assertEqual(get_preview_info(doc, saved["id"], base)["page_counts"], [1, 1])
            _, png = render_document_page(doc, saved["id"], base_version_id=base)
            self.assertEqual(png.to_image_content().mimeType, "image/png")

    def test_rotated_page_and_unrenderable_new_glyph(self):
        second = io.BytesIO()
        writer = PdfWriter(clone_from=io.BytesIO(sample()))
        writer.pages[0].rotate(90)
        writer.write(second)
        rotated = second.getvalue()
        target = pdf_text_targets(rotated, 1)[0]
        self.assertTrue(all(0.0 <= v <= 1.0 for v in target["box"]))
        changed = replace_pdf_text(rotated, 1, target["index"], target["text"], "Beta")
        page = PdfReader(io.BytesIO(changed)).pages[0]
        self.assertEqual(page.rotation, 90)
        self.assertEqual(page.extract_text().splitlines()[0], "Beta")
        with self.assertRaises(ValueError):
            replace_pdf_text(rotated, 1, target["index"], target["text"], "한글")

    def test_encrypted_pdf_has_no_editable_targets(self):
        output = io.BytesIO()
        writer = PdfWriter(clone_from=io.BytesIO(sample()))
        writer.encrypt("test-password")
        writer.write(output)
        with self.assertRaisesRegex(ValueError, "암호"):
            pdf_text_targets(output.getvalue(), 1)

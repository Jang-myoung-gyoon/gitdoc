import io
import unittest

from pypdf import PdfReader, PdfWriter
from pypdf.annotations import Text
from pypdf.generic import (
    ArrayObject, DecodedStreamObject, DictionaryObject, NameObject, NumberObject,
    RectangleObject, TextStringObject,
)

from editing import edit_pdf, read_pdf_notes


def write(writer):
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def pdf_pages(data):
    return list(PdfReader(io.BytesIO(data)).pages)


class PdfEditTests(unittest.TestCase):
    def test_import_preserves_inherited_geometry_and_renderable_content(self):
        source = PdfWriter()
        page = source.add_blank_page(300, 420)
        parent = page["/Parent"]
        parent[NameObject("/MediaBox")] = page.pop("/MediaBox")
        parent[NameObject("/CropBox")] = RectangleObject([20, 30, 280, 400])
        parent[NameObject("/Rotate")] = NumberObject(90)
        page.pop("/Resources")
        font = DictionaryObject({
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        })
        parent[NameObject("/Resources")] = DictionaryObject({
            NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})
        })
        content = DecodedStreamObject()
        content.set_data(b"BT /F1 12 Tf 40 80 Td (Imported page) Tj ET")
        page[NameObject("/Contents")] = source._add_object(content)

        target = PdfWriter()
        target.add_blank_page(210, 310)
        target.add_blank_page(220, 320)
        original = write(target)
        edited, receipt = edit_pdf(original, {"operation": "insert", "page": 2}, write(source))
        before, imported, after = pdf_pages(edited)
        self.assertEqual(receipt, (2, 2, 1, None))
        self.assertEqual([before.mediabox.width, after.mediabox.width], [210, 220])
        self.assertEqual(list(imported.cropbox), [20, 30, 280, 400])
        self.assertEqual(imported.rotation, 90)
        self.assertEqual(imported.extract_text(), "Imported page")
        self.assertEqual(len(pdf_pages(original)), 2)

    def test_rotation_and_unicode_note_preserve_an_existing_annotation(self):
        writer = PdfWriter()
        page = writer.add_blank_page(200, 400)
        page["/Parent"][NameObject("/Rotate")] = NumberObject(270)
        writer.add_annotation(0, Text(rect=(0, 0, 10, 10), text="First"))
        page[NameObject("/Annots")] = writer._add_object(page["/Annots"])
        rotated, receipt = edit_pdf(write(writer), {"operation": "right", "page": 1})
        self.assertEqual(receipt, (1, None, 0, None))
        self.assertEqual(pdf_pages(rotated)[0].rotation, 0)
        edit = {"operation": "note", "page": 1, "note": "한글 메모\n검토 👋"}
        annotated, _ = edit_pdf(rotated, edit)
        self.assertEqual(read_pdf_notes(annotated, 1), ["First", edit["note"]])

    def test_encrypted_and_signed_originals_remain_read_only(self):
        writer = PdfWriter()
        writer.add_blank_page(200, 400)
        writer.encrypt("")
        operation = {"operation": "right", "page": 1}
        with self.assertRaisesRegex(ValueError, "암호가 걸렸거나 서명된"):
            edit_pdf(write(writer), operation)

        writer = PdfWriter()
        writer.add_blank_page(200, 400)
        signature = DictionaryObject({
            NameObject("/FT"): NameObject("/Sig"),
            NameObject("/V"): TextStringObject("signed"),
        })
        writer.root_object[NameObject("/AcroForm")] = DictionaryObject({
            NameObject("/Fields"): ArrayObject([writer._add_object(signature)]),
        })
        with self.assertRaisesRegex(ValueError, "암호가 걸렸거나 서명된"):
            edit_pdf(write(writer), operation)

    def test_delete_rejects_removing_the_last_page(self):
        writer = PdfWriter()
        writer.add_blank_page(200, 400)
        with self.assertRaisesRegex(ValueError, "적어도 한 페이지"):
            edit_pdf(write(writer), {"operation": "delete", "page": 1})

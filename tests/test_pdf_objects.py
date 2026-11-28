import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import ImageChops
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ContentStream
from test_pdf_text_edit import sample

import store
from desktop.bridge import edit, pdf_objects
from mcp_server import register_document, render_document_page
from pdf_edit import edit_pdf_bytes
from pdf_objects import edit_objects, open_objects, resource_name
from pdf_text_edit import pdf_text_targets, replace_pdf_text


def object_at(kind, box, text=""):
    return {"kind": kind, "box": box, "text": text}


def reader(data):
    return PdfReader(io.BytesIO(data))


class PdfObjectsTests(unittest.TestCase):
    def test_desktop_canvas_save_review_and_reopen(self):
        original = sample()
        with tempfile.TemporaryDirectory() as folder, patch.object(store, "DATABASE", Path(folder)/"qa.sqlite3"):
            store.initialize()
            store.set_desktop_review(True)
            source = Path(folder)/"source.pdf"
            source.write_bytes(original)
            version = register_document(str(source), "PDF objects QA")["version"]
            doc, base = version["document_id"], version["id"]
            self.assertEqual(pdf_objects(doc, base, 1)["objects"], [])
            requested = [
                object_at("text", [.42, .06, .96, .29], "한글 상자 ABC\n다음 줄"),
                object_at("rectangle", [.10, .38, .30, .55]),
                object_at("ellipse", [.35, .38, .55, .55]),
                object_at("triangle", [.60, .38, .80, .55]),
                object_at("arrow", [.10, .64, .30, .80]),
            ]
            preview = pdf_objects(doc, base, 1, requested)
            self.assertTrue(Path(preview["image"]).is_file())
            self.assertEqual(len(preview["objects"]), 5)
            saved = edit(doc, "main", base, {"operation": "edit_page_objects", "page": 1, "objects": requested},
                         "Add PDF objects")
            self.assertEqual(saved["status"], "pending")
            with store.database(readonly=True) as db:
                self.assertEqual(store.find_version(db, doc, base, "content")["content"], original)
                row = store.find_version(db, doc, saved["id"], "extension, content")
            self.assertEqual(row["extension"], "pdf")
            self.assertEqual(source.read_bytes(), original)
            self.assertEqual(reader(row["content"]).pages[0].extract_text().splitlines(),
                             ["Alpha original", "Alpha original", "Keep this text", "한글 상자 ABC", "다음 줄"])
            page = reader(row["content"]).pages[0]
            reopened = pdf_objects(doc, saved["id"], 1)["objects"]
            forms = page["/Resources"]["/XObject"]
            for obj, expected in zip(reopened, (b"TJ", b"re", b"c", b"l", b"m")):
                form = forms[resource_name(obj["id"])]
                self.assertEqual(form["/Subtype"], "/Form")
                self.assertIn(expected, [operator for _, operator in ContentStream(form, page.pdf).operations])
            _, png = render_document_page(doc, saved["id"])
            self.assertEqual(png.to_image_content().mimeType, "image/png")

    def test_move_and_delete_selected_duplicates_on_a_rotated_crop(self):
        out = io.BytesIO()
        writer = PdfWriter(clone_from=io.BytesIO(sample()))
        writer.pages[0].cropbox.lower_left = (10, 20)
        writer.pages[0].rotate(90)
        writer.add_blank_page(200, 230)
        writer.write(out)
        original = out.getvalue()
        inserted = edit_objects(original, 1, [
            object_at("ellipse", [.08, .08, .30, .25]),
            object_at("ellipse", [.32, .08, .54, .25]),
            object_at("text", [.10, .60, .90, .72], "한글 123"),
        ])
        targets, old_image = open_objects(inserted, 1)
        first, second, text = targets
        moved = dict(second, box=[.32, .30, .54, .47])
        new = edit_objects(inserted, 1, [first, moved, text])
        self.assertEqual(open_objects(new, 1)[0], [first, moved, text])
        delta = ImageChops.difference(old_image, open_objects(new, 1)[1])
        w, h = delta.size
        self.assertIsNone(delta.crop((0, 0, int(w * .31), h)).getbbox())
        self.assertIsNotNone(delta.crop((int(w * .32), 0, int(w * .56), int(h * .49))).getbbox())

        deleted = edit_objects(new, 1, [first, moved])
        self.assertEqual(open_objects(deleted, 1)[0], [first, moved])
        deleted_reader = reader(deleted)
        self.assertNotIn("한글", deleted_reader.pages[0].extract_text())
        self.assertNotIn(resource_name(text["id"]), deleted_reader.pages[0]["/Resources"]["/XObject"])
        self.assertEqual(deleted_reader.pages[0].rotation, 90)
        self.assertEqual(deleted_reader.pages[0].cropbox, reader(original).pages[0].cropbox)
        self.assertIsNone(ImageChops.difference(open_objects(original, 2)[1], open_objects(deleted, 2)[1]).getbbox())

        # Existing page rotation and body editing keep the stored objects available.
        target = pdf_text_targets(deleted, 1)[0]
        changed = replace_pdf_text(deleted, 1, target["index"], target["text"], "Beta")
        self.assertEqual(open_objects(changed, 1)[0], [first, moved])
        rotated = edit_pdf_bytes(changed, 1, "right", None)[0]
        after_rotation, _ = open_objects(rotated, 1)
        self.assertEqual([item["id"] for item in after_rotation], [first["id"], second["id"]])
        self.assertEqual(after_rotation[0]["box"], [.75, .08, .92, .30])

    def test_invalid_fit_and_protection_fail_without_an_output(self):
        original = sample()
        with self.assertRaises(ValueError):
            edit_objects(original, 1, [object_at("arrow", [-.1, .1, .3, .5])])
        with self.assertRaisesRegex(ValueError, "상자"):
            edit_objects(original, 1, [object_at("text", [.1, .1, .21, .25], "한글" * 20)])
        with self.assertRaisesRegex(ValueError, "다시 선택"):
            edit_objects(original, 1, [dict(object_at("arrow", [.1, .1, .3, .5]), id="unknown")])
        output = io.BytesIO()
        writer = PdfWriter(clone_from=io.BytesIO(original))
        writer.encrypt("fixture")
        writer.write(output)
        encrypted = output.getvalue()
        with self.assertRaisesRegex(ValueError, "암호"):
            edit_objects(encrypted, 1, [object_at("arrow", [.1, .1, .3, .5])])

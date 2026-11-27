import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from page_text import bounds, embedded_text, page_text


class PageTextTests(unittest.TestCase):
    def test_pdf_glyph_positions_follow_crop_and_rotation(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "text.pdf"
            with PdfWriter() as writer:
                for rotation in (0, 90):
                    page = writer.add_blank_page(width=200, height=300)
                    page.cropbox.lower_left = (20, 40)
                    page.cropbox.upper_right = (180, 280)
                    page[NameObject("/Resources")] = DictionaryObject({
                        NameObject("/Font"): DictionaryObject({
                            NameObject("/F1"): DictionaryObject({
                                NameObject("/Type"): NameObject("/Font"),
                                NameObject("/Subtype"): NameObject("/Type1"),
                                NameObject("/BaseFont"): NameObject("/Helvetica"),
                            }),
                        }),
                    })
                    drawing = DecodedStreamObject()
                    drawing.set_data(b"BT /F1 16 Tf 50 245 Td (Copy text) Tj ET")
                    page.replace_contents(drawing)
                    page.rotate(rotation)
                writer.write(path)
            plain, = embedded_text(path, 1)
            rotated, = embedded_text(path, 2)
            self.assertEqual("".join(value for value, _ in plain), "Copy text")
            self.assertEqual("".join(value for value, _ in rotated), "Copy text")
            a, b = bounds(plain), bounds(rotated)
            for actual, expected in zip(b, (1 - a[3], a[0], 1 - a[1], a[2])):
                self.assertAlmostEqual(actual, expected, places=3)

    def test_sparse_embedded_text_keeps_original_words_and_adds_missing_ocr_lines(self):
        embedded = [[["원본", [0.1, 0.1, 0.4, 0.2]]]]
        recognized = [[["오인식", [0.1, 0.1, 0.4, 0.2]]], [["추가", [0.1, 0.3, 0.4, 0.4]]]]
        with patch("page_text.embedded_text", return_value=embedded), patch("page_text.recognize", return_value=recognized):
            self.assertEqual(page_text(Path("fixture.pdf"), 1), [embedded[0], recognized[1]])
        page_text.cache_clear()

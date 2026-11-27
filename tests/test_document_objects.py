import hashlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageChops
from pypdfium2 import PdfDocument, raw

import store
from desktop.bridge import CanvasSession
from desktop.document_objects import DRAWING, HANGUL, WORD_SHAPES, WORD_TAG
from desktop.exporting import convert_format
from desktop.paged_editor import rhwp
from editing import open_package, parse_part
from extract import matches_format
from mcp_server import get_preview_info, register_document, render_document_page
from preview import BUNDLED_BIN, PreviewError, executable, office_profile, preview_path, rhwp_command, run_converter


class DocumentObjectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            soffice = executable("soffice", "DOCVERS_SOFFICE", BUNDLED_BIN / "soffice")
            rhwp_command()
        except PreviewError as error:
            raise unittest.SkipTest(str(error)) from error

        staging = tempfile.TemporaryDirectory()
        cls.addClassCleanup(staging.cleanup)
        cls.folder = folder = Path(staging.name)
        database = patch.object(store, "DATABASE", folder / "objects.sqlite3")
        database.start()
        cls.addClassCleanup(database.stop)
        store.initialize()
        store.set_desktop_review(True)
        source = folder / "sample.txt"
        source.write_text("GitDoc object demo\nSecond paragraph\n", encoding="utf-8")
        profile = office_profile(folder)
        run_converter([soffice, f"-env:UserInstallation={profile.as_uri()}", "--headless",
                       "--convert-to", "docx", "--outdir", str(folder), str(source)], folder)
        docx = (folder / "sample.docx").read_bytes()
        (folder / "sample.doc").write_bytes(convert_format("docx", "doc", docx))
        rhwp(["gen-table"], folder)
        rhwp(["edit", "delete-table", "output/gen_table.hwp", "--table", "0", "-o", "blank.hwp"], folder)
        rhwp(["edit", "insert-text", "blank.hwp", "--text", "GitDoc object demo",
              "-o", "sample.hwp"], folder)
        rhwp(["export-hwpx", str(folder / "sample.hwp"), str(folder / "sample.hwpx")], folder)

    def test_adjacent_tables_stay_distinct_in_saved_doc(self):
        path = self.folder / "sample.doc"
        version = register_document(str(path), "Adjacent Word tables")["version"]
        document_id, base = version["document_id"], version["id"]
        session = CanvasSession()
        self.addCleanup(session.close)
        session.open(document_id, "main", base, 1)
        for size in ([2, 3], [3, 4]):
            session.page(number=1, operation="table", value=size)
        updated = session.save("Add two tables", 1)["id"]
        with store.database(readonly=True) as db:
            saved = store.find_version(db, document_id, updated, "content")["content"]
        with open_package(convert_format("doc", "docx", saved)) as archive:
            root = parse_part(archive, "word/document.xml")
            tables = root.findall(f".//{WORD_TAG}tbl")
            self.assertEqual([len(node.findall(WORD_TAG + "tr")) for node in tables], [2, 3])

    def test_native_objects_save_reopen_and_render(self):
        for extension in ("doc", "docx", "hwp", "hwpx"):
            with self.subTest(extension=extension):
                self.check_format(extension)

    def check_format(self, extension):
        path = self.folder / f"sample.{extension}"
        original = path.read_bytes()
        version = register_document(str(path), path.name)["version"]
        document_id, base = version["document_id"], version["id"]
        session = CanvasSession()
        self.addCleanup(session.close)
        opened = session.open(document_id, "main", base, 1)
        initial_image = self.image(opened)
        with self.assertRaises(ValueError):
            session.page(number=1, operation="table", value=[0, 3])
        self.assertEqual(session.working.position, 0)
        inserted = session.page(number=1, operation="table", value=[2, 3])
        self.assertIsNotNone(ImageChops.difference(self.image(inserted), initial_image).getbbox())
        self.assertFalse(session.page(number=1, operation="undo")["dirty"])
        redo = session.page(number=1, operation="redo")
        self.assertTrue(redo["dirty"])
        previous_image = self.image(redo)
        for shape in WORD_SHAPES:
            edited = session.page(number=1, operation="shape", value=shape)
            changed_image = self.image(edited)
            self.assertIsNotNone(ImageChops.difference(previous_image, changed_image).getbbox(), shape)
            previous_image = changed_image
        result = session.save("Insert native objects", 1)
        self.assertEqual(result["status"], "pending")
        with store.database(readonly=True) as db:
            before = store.find_version(db, document_id, base, "content")
            after = store.find_version(db, document_id, result["id"], "content, extension, filename")
            self.assertEqual(before["content"], original)
            self.assertEqual(after["extension"], extension)
            self.assertEqual(after["filename"], path.name)
            self.assertTrue(matches_format(extension, after["content"]))
            self.assertEqual(store.find_branch(db, document_id, "main")["head_id"], base)
        self.check_objects(extension, after["content"])
        preview = get_preview_info(document_id, result["id"], base)
        self.assertEqual(preview["page_counts"], [1, 1])
        _, image = render_document_page(document_id, result["id"])
        self.assertEqual(image.to_image_content().mimeType, "image/png")
        self.assertTrue(image.data.startswith(b"\x89PNG"))

    @staticmethod
    def image(page):
        return Image.open(io.BytesIO(Path(page["image"]).read_bytes())).convert("RGB")

    def check_objects(self, extension, data):
        if extension in ("doc", "docx"):
            saved_as_docx = convert_format("doc", "docx", data) if extension == "doc" else data
            with open_package(saved_as_docx) as archive:
                root = parse_part(archive, "word/document.xml")
                table = root.find(f".//{WORD_TAG}tbl")
                rows = table.findall(WORD_TAG + "tr")
                self.assertEqual([len(row.findall(WORD_TAG + "tc")) for row in rows], [3, 3])
                self.assertFalse(any(path.startswith("word/media/") for path in archive.namelist()))
                if extension == "docx":
                    self.assertCountEqual([node.get("prst") for node in root.iter(f"{{{DRAWING}}}prstGeom")],
                                          WORD_SHAPES.values())
                else:
                    with open_package(convert_format("doc", "odt", data)) as odt:
                        root = parse_part(odt, "content.xml")
                    geometry = root.findall(".//{*}enhanced-geometry")
                    draw = "{urn:oasis:names:tc:opendocument:xmlns:drawing:1.0}"
                    types = {node.get(draw + "type") for node in geometry}
                    self.assertTrue({"rectangle", "ellipse", "isosceles-triangle", "right-arrow"} <= types)
            pdf = preview_path(extension, hashlib.sha256(data).hexdigest(), lambda: data)
            with PdfDocument(pdf) as reopened:
                coordinates = []
                for shape in reopened[0].get_objects(filter=[raw.FPDF_PAGEOBJ_PATH]):
                    left, bottom, right, top = shape.get_bounds()
                    if 70 <= right - left <= 115 and 25 <= top - bottom <= 55:
                        coordinates.append((bottom + top) / 2)
                coordinates.sort()
                # Fill and outline usually produce separate path objects at the same position.
                visible_shapes = bool(coordinates) + sum(y - previous > 20
                                                        for previous, y in zip(coordinates, coordinates[1:]))
                self.assertEqual(visible_shapes, 4)
            return
        saved_as_hwpx = convert_format("hwp", "hwpx", data) if extension == "hwp" else data
        with open_package(saved_as_hwpx) as archive:
            root = parse_part(archive, "Contents/section0.xml")
            table = root.find(f".//{HANGUL}tbl")
            self.assertEqual((table.get("rowCnt"), table.get("colCnt")), ("2", "3"))
            self.assertEqual(len(root.findall(f".//{HANGUL}rect")), 1)
            self.assertEqual(len(root.findall(f".//{HANGUL}ellipse")), 1)
            polygons = [node.findall("{*}pt") for node in root.iter(HANGUL + "polygon")]
            self.assertCountEqual([len(points) for points in polygons], [4, 8])
            for points in polygons:
                self.assertEqual(points[0].attrib, points[-1].attrib)
            for kind in ("rect", "ellipse", "polygon"):
                for shape in root.iter(HANGUL + kind):
                    paragraph = next(shape.iterancestors(HANGUL + "p"))
                    self.assertEqual("".join(node.text or "" for node in paragraph.iter(HANGUL + "t")), "")
            self.assertIsNone(root.find(f".//{HANGUL}pic"))

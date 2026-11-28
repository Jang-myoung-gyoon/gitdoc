import hashlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lxml import etree
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
from workflow import review_revision


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

    def test_hangul_objects_insert_at_the_middle_of_rendered_text(self):
        text = "앞문장ABC뒷문장" + " 한글과 Latin 글자가 함께 있는 문장" * 8
        rhwp(["edit", "insert-text", "blank.hwp", "--text", text, "-o", "caret.hwp"], self.folder)
        rhwp(["export-hwpx", "caret.hwp", "caret.hwpx"], self.folder)
        for extension in ("hwp", "hwpx"):
            path = self.folder / f"caret.{extension}"
            original = path.read_bytes()
            for operation, value, kind in (("table", [2, 3], "tbl"), ("shape", "arrow", "polygon")):
                with self.subTest(extension=extension, operation=operation):
                    entry = register_document(str(path), f"Caret {extension} {operation}")["version"]
                    doc, base = entry["document_id"], entry["id"]
                    canvas = CanvasSession()
                    self.addCleanup(canvas.close)
                    initial = canvas.open(doc, "main", base, 1)
                    original_image = self.image(initial)
                    paragraph = next(item for item in initial["objects"] if item["text"] == text)
                    boxes = paragraph["glyph_boxes"]
                    self.assertTrue({0, 3, 4, 5, 6}.issubset(index for index, _ in boxes))
                    self.assertGreater(boxes[-1][1][1], boxes[0][1][1])
                    target = {"id": paragraph["id"]}
                    for selection in (None, [2, 5]):
                        with self.assertRaisesRegex(ValueError, "커서"):
                            canvas.page(number=1, operation=operation, value=value, target=target, selection=selection)
                        self.assertEqual(canvas.working.data, original)
                    offset = 6
                    changed = canvas.page(number=1, operation=operation, value=value,
                                          target=target, selection=[offset, offset])
                    inserted_image = self.image(changed)
                    self.assertIsNotNone(ImageChops.difference(original_image, inserted_image).getbbox())
                    self.assertFalse(canvas.page(number=1, operation="undo")["dirty"])
                    self.assertEqual(canvas.working.data, original)
                    self.assertTrue(canvas.page(number=1, operation="redo")["dirty"])
                    proposal = canvas.save("Insert at the caret", 1)["id"]
                    with store.database(readonly=True) as db:
                        before = store.find_version(db, doc, base, "content")["content"]
                        saved = store.find_version(db, doc, proposal, "content, extension")
                    self.assertEqual(before, original)
                    self.assertEqual(saved["extension"], extension)
                    package = saved["content"]
                    if extension == "hwp":
                        package = convert_format("hwp", "hwpx", package)
                    with open_package(package) as archive:
                        body = parse_part(archive, "Contents/section0.xml")
                    parts = [node for paragraph in body.findall(HANGUL + "p")
                             for run in paragraph.findall(HANGUL + "run") for node in run]
                    objects = [index for index, node in enumerate(parts) if node.tag == HANGUL + kind]
                    self.assertEqual(len(objects), 1)
                    before_text = "".join(node.text or "" for node in parts[:objects[0]] if node.tag == HANGUL + "t")
                    after_text = "".join(node.text or "" for node in parts[objects[0]+1:] if node.tag == HANGUL + "t")
                    self.assertEqual((before_text, after_text), (text[:offset], text[offset:]))
                    review_revision(doc, proposal, "approve", base)
                    reopened = canvas.open(doc, "main", proposal, 1)
                    if operation == "table":
                        self.assertEqual(len(reopened["tables"]), 1)
                    self.assertIsNone(ImageChops.difference(self.image(reopened), inserted_image).getbbox())

    def test_native_objects_save_reopen_and_render(self):
        for extension in ("doc", "docx", "hwp", "hwpx"):
            with self.subTest(extension=extension):
                self.check_format(extension)

    def insert(self, session, extension, operation, value, offset=None):
        options = {}
        if extension in ("hwp", "hwpx"):
            item = next(item for item in session.working.page_objects if "GitDoc" in item["text"])
            if offset is None:
                offset = len(item["text"])
            options = {"target": {"id": item["id"]}, "selection": [offset, offset]}
        return session.page(number=1, operation=operation, value=value, **options)

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
        inserted = self.insert(session, extension, "table", [2, 3])
        self.assertIsNotNone(ImageChops.difference(self.image(inserted), initial_image).getbbox())
        self.assertFalse(session.page(number=1, operation="undo")["dirty"])
        redo = session.page(number=1, operation="redo")
        self.assertTrue(redo["dirty"])
        previous_image = self.image(redo)
        for shape in WORD_SHAPES:
            edited = self.insert(session, extension, "shape", shape)
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

    def test_delete_selected_existing_table(self):
        for extension in ("doc", "docx", "hwp", "hwpx"):
            with self.subTest(extension=extension):
                path = self.folder / f"sample.{extension}"
                version = register_document(str(path), f"Existing table {extension}")["version"]
                document_id, base = version["document_id"], version["id"]
                session = CanvasSession()
                self.addCleanup(session.close)
                session.open(document_id, "main", base, 1)
                first = self.insert(session, extension, "table", [2, 3])
                stale = {"address": first["tables"][0]["address"], "digest": first["edit_digest"]}
                self.insert(session, extension, "table", [3, 4])
                added = session.save("Create two tables", 1)["id"]
                review_revision(document_id, added, "approve", base)
                existing = session.open(document_id, "main", added, 1)
                self.assertEqual(len(existing["tables"]), 2)
                with self.assertRaises(ValueError):
                    session.page(number=1, operation="delete_table", value=stale)
                chosen = next(table for table in existing["tables"] if table["rows"] == 2)
                action = {"address": chosen["address"], "digest": existing["edit_digest"]}
                before_image = self.image(existing)
                updated = session.page(number=1, operation="delete_table", value=action)
                self.assertIsNotNone(ImageChops.difference(self.image(updated), before_image).getbbox())
                self.assertEqual([(table["rows"], table["columns"]) for table in updated["tables"]], [(3, 4)])
                self.assertEqual(len(session.page(number=1, operation="undo")["tables"]), 2)
                self.assertEqual(len(session.page(number=1, operation="redo")["tables"]), 1)
                result = session.save("Delete the selected table", 1)
                self.assertEqual(result["status"], "pending")
                with store.database(readonly=True) as db:
                    old = store.find_version(db, document_id, added, "content")
                    new = store.find_version(db, document_id, result["id"], "content, extension")
                self.assertEqual(new["extension"], extension)
                self.assertCountEqual(self.table_sizes(extension, old["content"]), [(2, 3), (3, 4)])
                self.assertEqual(self.table_sizes(extension, new["content"]), [(3, 4)])
                self.assertEqual(get_preview_info(document_id, result["id"], added)["page_counts"], [1, 1])

    def test_existing_adjacent_shapes_are_edited_by_source_address(self):
        for extension in ("doc", "docx", "hwp", "hwpx"):
            with self.subTest(extension=extension):
                self.check_shape_edits(extension)

    def check_shape_edits(self, extension):
        path = self.folder / f"sample.{extension}"
        version = register_document(str(path), f"Adjacent shapes {extension}")["version"]
        document, original = version["document_id"], version["id"]
        session = CanvasSession()
        self.addCleanup(session.close)
        session.open(document, "main", original, 1)
        self.insert(session, extension, "table", [2, 3])
        for kind in ("rectangle", "rectangle", "arrow"):
            self.insert(session, extension, "shape", kind)
        base = session.save("Create a table and three adjacent shapes", 1)["id"]
        review_revision(document, base, "approve", original)
        before = session.open(document, "main", base, 1)
        if extension == "doc" and len(before["shapes"]) != 3:
            with open_package(session.working.data) as archive:
                root = parse_part(archive, "word/document.xml")
                xml = "".join(etree.tostring(paragraph, encoding="unicode", with_tail=False)
                              for paragraph in root.findall(f"{WORD_TAG}body/{WORD_TAG}p"))
            self.fail(f"Expected three existing shapes, got {before['shapes']}. "
                      f"Converted synthetic DOC body paragraphs: {xml}")
        self.assertEqual(len(before["shapes"]), 3)
        old_image = self.image(before)
        rectangles = [item for item in before["shapes"] if item["kind"] == "rectangle"]
        self.assertEqual(len(rectangles), 2)
        self.assertEqual(rectangles[0]["size"], rectangles[1]["size"])

        def action(result, address, operation, **extra):
            return session.page(number=1, operation=operation,
                                value={"digest": result["edit_digest"], "address": address, **extra})

        first, second = [item["address"] for item in rectangles]
        with self.assertRaises(ValueError):
            action(before, second, "shape_size", size=[40, 900])
        size = [45, 22]
        resized = action(before, second, "shape_size", size=size)
        edits = {item["address"][1]: item["size"] for item in resized["shapes"]}
        self.assertEqual((edits[first[1]], edits[second[1]]), (rectangles[0]["size"], size))
        self.assertIsNotNone(ImageChops.difference(self.image(resized), old_image).getbbox())
        with self.assertRaises(ValueError):
            action(before, second, "delete_shape")

        moved = action(resized, second, "shape_up")
        self.assertEqual(next(item["address"] for item in moved["shapes"] if item["size"] == size), first)
        undone = session.page(number=1, operation="undo")
        self.assertEqual(next(item["address"] for item in undone["shapes"] if item["size"] == size), second)
        redone = session.page(number=1, operation="redo")
        self.assertEqual(redone["shapes"], moved["shapes"])
        updated = session.save("Resize and move the chosen rectangle", 1)["id"]
        review_revision(document, updated, "approve", base)
        reopened = session.open(document, "main", updated, 1)
        self.assertEqual(next(item["address"] for item in reopened["shapes"] if item["size"] == size), first)
        unchanged = [item for item in reopened["shapes"] if item["address"] != first]
        removed = action(reopened, first, "delete_shape")
        self.assertEqual([(item["kind"], item["size"]) for item in unchanged],
                         [(item["kind"], item["size"]) for item in removed["shapes"]])
        saved = session.save("Remove the chosen rectangle", 1)
        self.assertEqual(saved["status"], "pending")
        review_revision(document, saved["id"], "approve", updated)
        self.assertEqual(session.open(document, "main", saved["id"], 1)["shapes"], removed["shapes"])

        with store.database(readonly=True) as db:
            source = store.find_version(db, document, original, "content")
            base_record = store.find_version(db, document, base, "content, extracted")
            final = store.find_version(db, document, saved["id"], "content, extension, extracted")
        self.assertEqual(source["content"], path.read_bytes())
        self.assertEqual(final["extension"], extension)
        self.assertEqual(self.table_sizes(extension, base_record["content"]), [(2, 3)])
        self.assertEqual(self.table_sizes(extension, final["content"]), [(2, 3)])
        self.assertIn("GitDoc object demo", final["extracted"])
        self.assertEqual(get_preview_info(document, saved["id"], base)["page_counts"], [1, 1])
        _, page = render_document_page(document, saved["id"])
        self.assertEqual(page.to_image_content().mimeType, "image/png")

    @staticmethod
    def table_sizes(extension, data):
        if extension in ("hwp", "hwpx"):
            hwpx = convert_format("hwp", "hwpx", data) if extension == "hwp" else data
            with open_package(hwpx) as archive:
                root = parse_part(archive, "Contents/section0.xml")
                return [(int(node.get("rowCnt")), int(node.get("colCnt"))) for node in root.iter(HANGUL + "tbl")]
        docx = convert_format("doc", "docx", data) if extension == "doc" else data
        with open_package(docx) as archive:
            root = parse_part(archive, "word/document.xml")
            return [(len(table.findall(WORD_TAG + "tr")),
                     len(table.findall(f"{WORD_TAG}tblGrid/{WORD_TAG}gridCol")))
                    for table in root.iter(WORD_TAG + "tbl")]

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

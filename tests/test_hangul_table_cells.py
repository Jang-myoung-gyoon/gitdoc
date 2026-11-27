import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageChops

import store
from desktop.bridge import CanvasSession
from desktop.paged_editor import exported_tables, rhwp
from extract import matches_format
from mcp_server import get_preview_info, register_document, render_document_page
from preview import PreviewError, rhwp_command
from workflow import review_revision


class HangulTableCellTests(unittest.TestCase):
    def test_edit_exact_cell_save_and_reopen_both_formats(self):
        try:
            rhwp_command()
        except PreviewError as error:
            self.skipTest(str(error))
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            with patch.object(store, "DATABASE", folder/"cells.sqlite3"):
                store.initialize()
                store.set_desktop_review(True)
                self.fixture(folder)
                for extension in ("hwp", "hwpx"):
                    with self.subTest(extension=extension):
                        self.check_format(folder, extension)

    def fixture(self, folder):
        rhwp(["gen-table"], folder)
        rhwp(["edit", "delete-table", "output/gen_table.hwp", "--table", "0", "-o", "blank.hwp"], folder)
        rhwp(["edit", "insert-text", "blank.hwp", "--text", "표 주변 본문", "-o", "body.hwp"], folder)
        rhwp(["edit", "insert-paragraph", "body.hwp", "--para", "1", "-o", "paragraph.hwp"], folder)
        rhwp(["edit", "insert-table", "paragraph.hwp", "--para", "1", "--rows", "2", "--cols", "3",
              "-o", "table.hwp"], folder)
        rhwp(["edit", "insert-table", "table.hwp", "--para", "2", "--rows", "2", "--cols", "3",
              "-o", "tables.hwp"], folder)
        prior = "tables.hwp"
        for number, table, col in ((0, 0, 1), (1, 1, 0), (2, 1, 1)):
            target = "source.hwp" if number == 2 else f"text{number}.hwp"
            rhwp(["edit", "set-cell", prior, "--table", str(table), "--row", "1", "--col", str(col),
                  "--text", "같은 값", "--keep-style", "-o", target], folder)
            prior = target
        rhwp(["export-hwpx", "source.hwp", "source.hwpx"], folder)

    def check_format(self, folder, extension):
        path = folder/f"source.{extension}"
        original = path.read_bytes()
        native = exported_tables(path)
        self.assertEqual(len(native), 2)
        selected_native = native[1]
        address = [selected_native[key] for key in ("section", "paragraph", "control")]
        entry = register_document(str(path), f"Cell QA {extension}")["version"]
        doc, base = entry["document_id"], entry["id"]
        canvas = CanvasSession()
        self.addCleanup(canvas.close)
        opened = canvas.open(doc, "main", base, 1)
        self.assertEqual(len(opened["tables"]), 2)
        table = next(t for t in opened["tables"] if t["address"] == address)
        cells = {tuple(cell["coordinate"]): cell for cell in table["cells"]}
        self.assertEqual(cells[(1, 0)]["text"], cells[(1, 1)]["text"])
        self.assertLess(cells[(1, 0)]["box"][0], cells[(1, 1)]["box"][0])
        first = Image.open(opened["image"]).convert("RGB")
        action = {"address": address, "coordinate": [1, 1],
                  "digest": opened["edit_digest"], "text": "새 한글 값"}
        for bad in ({"digest": "stale"}, {"coordinate": [1, 3]},
                    {"coordinate": [True, 1]}, {"text": "줄\n바꿈"}):
            with self.assertRaises(ValueError):
                canvas.page(number=1, operation="edit_table_cell", value=dict(action, **bad))
        self.assertFalse(canvas.page(number=1, operation="edit_table_cell",
                                     value=dict(action, text="같은 값"))["dirty"])
        edited = canvas.page(number=1, operation="edit_table_cell", value=action)
        self.assertTrue(edited["dirty"])
        self.assertIsNotNone(ImageChops.difference(first, Image.open(edited["image"]).convert("RGB")).getbbox())
        with self.assertRaises(ValueError):
            canvas.page(number=1, operation="edit_table_cell", value=action)
        cleared = canvas.page(number=1, operation="edit_table_cell",
                              value=dict(action, digest=edited["edit_digest"], text=""))
        self.assertTrue(cleared["dirty"])
        self.assertTrue(canvas.page(number=1, operation="undo")["redo"])
        self.assertFalse(canvas.page(number=1, operation="undo")["dirty"])
        self.assertTrue(canvas.page(number=1, operation="redo")["dirty"])
        proposal = canvas.save("Edit exactly one table cell", 1)
        self.assertEqual(proposal["status"], "pending")
        with store.database(readonly=True) as db:
            before = store.find_version(db, doc, base, "content")
            after = store.find_version(db, doc, proposal["id"], "content, extension")
        self.assertEqual(before["content"], original)
        self.assertEqual(after["extension"], extension)
        self.assertTrue(matches_format(extension, after["content"]))
        saved = folder/f"saved.{extension}"
        saved.write_bytes(after["content"])
        target = next(cell for cell in selected_native["cells"] if (cell["row"], cell["col"]) == (1, 1))
        target["text"] = action["text"]
        self.assertEqual(exported_tables(saved), native)
        review_revision(doc, proposal["id"], "approve", base)
        reopened = canvas.open(doc, "main", proposal["id"], 1)
        self.assertIn("표 주변 본문", [item["text"] for item in reopened["objects"]])
        self.assertIn(action["text"], [cell["text"] for table in reopened["tables"] for cell in table["cells"]])
        self.assertEqual(get_preview_info(doc, proposal["id"], base)["page_counts"], [1, 1])
        _, image = render_document_page(doc, proposal["id"])
        self.assertTrue(image.data.startswith(b"\x89PNG"))

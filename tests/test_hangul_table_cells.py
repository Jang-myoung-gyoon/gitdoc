import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageChops

import store
from desktop.bridge import CanvasSession
from desktop.document_objects import HANGUL
from desktop.hangul_cells import grid_cells, source_table
from desktop.paged_editor import PagedEditor, exported_tables, hangul_xml, rhwp
from editing import open_package, parse_part, rewrite_package
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

    def test_native_row_and_column_edits_move_only_the_intended_cells(self):
        try:
            rhwp_command()
        except PreviewError as error:
            self.skipTest(str(error))
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            self.fixture(folder)
            rhwp(["edit", "set-column-widths", "source.hwp", "--table", "1", "--widths", "8000,8000,8000",
                  "-o", "compact.hwp"], folder)
            rhwp(["edit", "apply-char-format-in-cell", "compact.hwp", "--table", "1", "--row", "1",
                  "--col", "0", "--props", '{"bold":true}', "-o", "styled.hwp"], folder)
            rhwp(["export-hwpx", "styled.hwp", "styled.hwpx"], folder)
            with patch.object(store, "DATABASE", folder/"grid.sqlite3"):
                store.initialize()
                store.set_desktop_review(True)
                for extension in ("hwp", "hwpx"):
                    with self.subTest(extension=extension):
                        self.exercise_grid(folder, extension)

    def exercise_grid(self, folder, extension):
        path = folder/f"styled.{extension}"
        original = path.read_bytes()
        natives = exported_tables(path)
        address = [natives[1][key] for key in ("section", "paragraph", "control")]
        entry = register_document(str(path), f"Grid QA {extension}")["version"]
        doc, base = entry["document_id"], entry["id"]
        canvas = CanvasSession()
        self.addCleanup(canvas.close)
        opened = canvas.open(doc, "main", base, 1)
        self.assertTrue(opened["tables"][1]["editable_grid"])
        proposed = folder/f"proposed.{extension}"
        with open_package(hangul_xml(path)) as archive:
            section = parse_part(archive, "Contents/section0.xml")
            before_cells = grid_cells(source_table(section, natives[1]))
            char_style = f"{HANGUL}subList/{HANGUL}p/{HANGUL}run"
            old_style = before_cells[1, 0].find(char_style).get("charPrIDRef")
            self.assertNotEqual(old_style, before_cells[1, 1].find(char_style).get("charPrIDRef"))

        for change, axis, motion in (
            ("row_above", 0, 0), ("row_below", 0, 1), ("row_delete", 0, -1),
            ("col_left", 1, 0), ("col_right", 1, 1), ("col_delete", 1, -1),
        ):
            with self.subTest(change=change):
                def page(**params):
                    return canvas.page(number=1, operation="edit_table_grid", value=dict(action, **params))

                action = {"address": address, "coordinate": [1, 1],
                          "change": change, "digest": opened["edit_digest"]}
                for invalid in ({"digest": "stale"}, {"coordinate": [0, 50]}, {"change": "none"}):
                    with self.assertRaises(ValueError):
                        page(**invalid)
                if motion == -1:
                    with self.assertRaisesRegex(ValueError, "확인하세요"):
                        page()
                before_image = Image.open(opened["image"]).convert("RGB")
                result = page(confirmed=True)
                self.assertTrue(result["dirty"])
                self.assertIsNotNone(ImageChops.difference(before_image, Image.open(result["image"]).convert("RGB")).getbbox())
                with self.assertRaises(ValueError):
                    page(confirmed=True)
                proposed.write_bytes(canvas.working.data)
                parsed = exported_tables(proposed)
                self.assertEqual(parsed[0], natives[0])
                affected = parsed[1]
                size = ["rows", "cols"][axis]
                self.assertEqual(affected[size], natives[1][size] + (-1 if motion == -1 else 1))
                old = {(item["row"], item["col"]): item["text"] for item in natives[1]["cells"]}
                new = {(item["row"], item["col"]): item["text"] for item in affected["cells"]}
                expected = {}
                pivot = 1 + (motion == 1)
                for coordinate, text in old.items():
                    if motion == -1 and coordinate[axis] == pivot:
                        continue
                    shifted = list(coordinate)
                    if coordinate[axis] >= pivot:
                        shifted[axis] += -1 if motion == -1 else 1
                    expected[tuple(shifted)] = text
                if motion != -1:
                    for key in new.keys() - expected.keys():
                        self.assertEqual(key[axis], pivot)
                        expected[key] = ""
                self.assertEqual(new, expected)
                with open_package(hangul_xml(proposed)) as archive:
                    section = parse_part(archive, "Contents/section0.xml")
                    after_cells = grid_cells(source_table(section, affected))
                    if change != "row_delete":
                        shifted_bold = (2 if change == "row_above" else 1, 0)
                        self.assertEqual(after_cells[shifted_bold].find(char_style).get("charPrIDRef"), old_style)
                if affected[size] == 1:
                    action.update(digest=result["edit_digest"], coordinate=[0, 0])
                    with self.assertRaisesRegex(ValueError, "마지막"):
                        page(confirmed=True)
                opened = canvas.page(number=1, operation="undo")
                self.assertFalse(opened["dirty"])
                self.assertTrue(canvas.page(number=1, operation="redo")["dirty"])
                self.assertEqual(canvas.working.data, proposed.read_bytes())
                opened = canvas.page(number=1, operation="undo")

        canvas.page(number=1, operation="edit_table_grid", value={
            "address": address, "coordinate": [1, 1], "change": "row_below", "digest": opened["edit_digest"],
        })
        proposal = canvas.save("Add one row to the selected table", 1)
        self.assertEqual(proposal["status"], "pending")
        with store.database(readonly=True) as db:
            self.assertEqual(store.find_version(db, doc, base, "content")["content"], original)
            saved = store.find_version(db, doc, proposal["id"], "content, extension")
            self.assertEqual(saved["extension"], extension)
            self.assertTrue(matches_format(extension, saved["content"]))
        review_revision(doc, proposal["id"], "approve", base)
        reopened = canvas.open(doc, "main", proposal["id"], 1)
        self.assertEqual([table["rows"] for table in reopened["tables"]], [2, 3])
        self.assertIn("표 주변 본문", [item["text"] for item in reopened["objects"]])
        _, image = render_document_page(doc, proposal["id"])
        self.assertTrue(image.data.startswith(b"\x89PNG"))

    def test_unsafe_tables_are_blocked_without_changing_the_snapshot(self):
        try:
            rhwp_command()
        except PreviewError as error:
            self.skipTest(str(error))
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            self.fixture(folder)
            rhwp(["edit", "merge-cells", "source.hwp", "--table", "1", "--row", "0", "--col", "0",
                  "--end-row", "0", "--end-col", "1", "-o", "merged.hwp"], folder)
            rhwp(["export-hwpx", "merged.hwp", "merged.hwpx"], folder)
            path = folder/"source.hwpx"
            with open_package(path.read_bytes()) as archive:
                part = "Contents/section0.xml"
                root = parse_part(archive, part)
                table = source_table(root, exported_tables(path)[1])
                grid_cells(table)[0, 0].set("protect", "1")
                (folder/"protected.hwpx").write_bytes(rewrite_package(archive, {part: root}))

            for filename, message, change in (
                ("merged.hwp", "병합", "row_below"), ("merged.hwpx", "병합", "row_below"),
                ("protected.hwpx", "보호", "row_below"), ("source.hwp", "쪽 너비", "col_right"),
                ("source.hwpx", "쪽 너비", "col_right"),
            ):
                with self.subTest(filename=filename):
                    path = folder/filename
                    original = path.read_bytes()
                    editor = PagedEditor(path.suffix[1:], original)
                    try:
                        opened = editor.page(1)
                        table = opened["tables"][1]
                        self.assertEqual(table["editable_grid"], filename.startswith("source."))
                        with self.assertRaisesRegex(ValueError, message):
                            editor.page(1, "edit_table_grid", value={"address": table["address"], "coordinate": [1, 1],
                                                                   "digest": opened["edit_digest"], "change": change})
                        self.assertEqual(editor.position, 0)
                        self.assertEqual(editor.data, original)
                    finally:
                        editor.close()

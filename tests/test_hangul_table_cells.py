import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageChops

import store
from desktop.bridge import CanvasSession
from desktop.document_objects import HANGUL
from desktop.hangul_cell_appearance import HC
from desktop.hangul_cells import HWP_UNITS_PER_MM, grid_cells, source_table, xml_signature
from desktop.hangul_format import HEADER, HH
from desktop.paged_editor import PagedEditor, exported_tables, hangul_xml, rhwp
from editing import open_package, parse_part, rewrite_package
from extract import matches_format
from mcp_server import get_preview_info, register_document, render_document_page
from preview import PreviewError, rhwp_command
from workflow import review_revision


def table_layout_state(path, index):
    table = exported_tables(path)[index]
    with open_package(hangul_xml(path)) as archive:
        header = parse_part(archive, HEADER)
        body = parse_part(archive, f"Contents/section{table['section']}.xml")
    para_props = {node.get("id"): node.find(HH + "align").get("horizontal")
                  for node in header.iter(HH + "paraPr") if node.find(HH + "align") is not None}
    characters = {node.get("id"): xml_signature(node) for node in header.iter(HH + "charPr")}
    cells = {}
    for key, cell in grid_cells(source_table(body, table)).items():
        sublist = cell.find(HANGUL + "subList")
        paragraphs = sublist.findall(HANGUL + "p")
        contents = [[(node.text or "", characters[run.get("charPrIDRef")])
                     for run in para.findall(HANGUL + "run") for node in run.findall(HANGUL + "t")]
                    for para in paragraphs]
        size = cell.find(HANGUL + "cellSz")
        cells[key] = {"text": contents, "horizontal": [para_props[p.get("paraPrIDRef")] for p in paragraphs],
                      "vertical": sublist.get("vertAlign"),
                      "width": int(size.get("width")), "height": int(size.get("height"))}
    return cells


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

    def test_cell_layout_roundtrip_regular_and_merged_in_both_formats(self):
        try:
            rhwp_command()
        except PreviewError as error:
            self.skipTest(str(error))
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            self.fixture(folder)
            rhwp(["edit", "set-column-widths", "source.hwp", "--table", "1", "--widths", "10000,10000,10000",
                  "-o", "narrow.hwp"], folder)
            rhwp(["edit", "apply-char-format-in-cell", "narrow.hwp", "--table", "1", "--row", "1",
                  "--col", "0", "--props", '{"bold":true}', "-o", "layout.hwp"], folder)
            rhwp(["edit", "merge-cells", "layout.hwp", "--table", "1", "--row", "0", "--col", "1",
                  "--end-row", "1", "--end-col", "1", "-o", "layout-merged.hwp"], folder)
            for name in ("layout", "layout-merged"):
                rhwp(["export-hwpx", f"{name}.hwp", f"{name}.hwpx"], folder)
            with patch.object(store, "DATABASE", folder/"layouts.sqlite3"):
                store.initialize()
                store.set_desktop_review(True)
                for extension in ("hwp", "hwpx"):
                    for merged in (False, True):
                        with self.subTest(extension=extension, merged=merged):
                            self.exercise_cell_layout(folder, extension, merged)

    def exercise_cell_layout(self, folder, extension, merged):
        name = "layout-merged" if merged else "layout"
        path = folder/f"{name}.{extension}"
        original = path.read_bytes()
        before = table_layout_state(path, 1)
        adjacent = table_layout_state(path, 0)
        record = register_document(str(path), f"Cell layout {extension} {merged}")["version"]
        doc, base = record["document_id"], record["id"]
        canvas = CanvasSession()
        self.addCleanup(canvas.close)
        opened = canvas.open(doc, "main", base, 1)
        before_image = Image.open(opened["image"]).convert("RGB")
        address = opened["tables"][1]["address"]
        self.assertEqual(opened["tables"][1]["layout_grid"], not merged)

        def edit(snapshot, change, value, start, end=None):
            params = {"address": address, "digest": snapshot["edit_digest"], "coordinate": start,
                      "change": change, "value": value}
            if end is not None:
                params["end"] = end
            return canvas.page(number=1, operation="edit_table_layout", value=params)

        if merged:
            with self.assertRaisesRegex(ValueError, "병합 셀 전체"):
                edit(opened, "horizontal", "right", [0, 0], [0, 2])
            with self.assertRaisesRegex(ValueError, "병합되지 않은"):
                edit(opened, "width", 32, [0, 1])
            self.assertEqual(canvas.working.data, original)
            single = edit(opened, "horizontal", "right", [0, 1])
            self.assertEqual(single["table_selection"]["coordinate"], [0, 1])
            edited = edit(single, "vertical", "TOP", [1, 2], [0, 0])
            for key, cell in before.items():
                cell["vertical"] = "TOP"
                if key == (0, 1):
                    cell["horizontal"] = ["RIGHT"] * len(cell["horizontal"])
        else:
            with self.assertRaisesRegex(ValueError, "본문 영역|현재 쪽"):
                edit(opened, "width", 150, [1, 0], [1, 2])
            self.assertEqual(canvas.working.data, original)
            single = edit(opened, "horizontal", "right", [1, 0])
            with self.assertRaisesRegex(ValueError, "다시 선택"):
                edit(opened, "vertical", "BOTTOM", [1, 0])
            def visible_size(page, coordinate, axis):
                cell = next(cell for cell in page["tables"][1]["cells"] if cell["coordinate"] == coordinate)
                return cell["box"][axis + 2] - cell["box"][axis]

            wide = edit(single, "width", 32, [0, 0], [1, 1])
            self.assertLess(visible_size(wide, [0, 0], 0), visible_size(opened, [0, 0], 0))
            self.assertAlmostEqual(visible_size(wide, [0, 2], 0), visible_size(opened, [0, 2], 0), places=3)
            tall = edit(wide, "height", 16, [1, 1])
            self.assertGreater(visible_size(tall, [1, 2], 1), visible_size(wide, [1, 2], 1))
            horizontal = edit(tall, "horizontal", "center", [1, 1], [1, 0])
            horizontal_image = Image.open(horizontal["image"]).convert("RGB")
            edited = edit(horizontal, "vertical", "BOTTOM", [1, 1], [1, 0])
            self.assertIsNotNone(ImageChops.difference(horizontal_image, Image.open(edited["image"]).convert("RGB")).getbbox())
            self.assertEqual(edited["table_selection"]["end"], [1, 0])
            for (row, col), cell in before.items():
                if col in (0, 1):
                    cell["width"] = round(32 * HWP_UNITS_PER_MM)
                if row == 1:
                    cell["height"] = round(16 * HWP_UNITS_PER_MM)
                    if col in (0, 1):
                        cell["horizontal"] = ["CENTER"] * len(cell["horizontal"])
                        cell["vertical"] = "BOTTOM"

        self.assertIsNotNone(ImageChops.difference(before_image, Image.open(edited["image"]).convert("RGB")).getbbox())
        finished = canvas.working.data
        self.assertTrue(canvas.page(number=1, operation="undo")["redo"])
        self.assertNotEqual(canvas.working.data, finished)
        self.assertTrue(canvas.page(number=1, operation="redo")["dirty"])
        self.assertEqual(canvas.working.data, finished)
        proposal = canvas.save("Align or size cells", 1)
        with store.database(readonly=True) as db:
            self.assertEqual(store.find_version(db, doc, base, "content")["content"], original)
            new = store.find_version(db, doc, proposal["id"], "content, extension")
        self.assertEqual(new["extension"], extension)
        self.assertTrue(matches_format(extension, new["content"]))
        saved = folder/f"saved-{name}.{extension}"
        saved.write_bytes(new["content"])
        self.assertEqual(table_layout_state(saved, 0), adjacent)
        self.assertEqual(table_layout_state(saved, 1), before)
        review_revision(doc, proposal["id"], "approve", base)
        reopened = canvas.open(doc, "main", proposal["id"], 1)
        self.assertEqual(reopened["tables"][1]["address"], address)
        self.assertEqual(get_preview_info(doc, proposal["id"], base)["page_counts"], [1, 1])
        _, image = render_document_page(doc, proposal["id"])
        self.assertTrue(image.data.startswith(b"\x89PNG"))

    def test_cell_appearance_roundtrip_preserves_neighbors_and_shared_styles(self):
        try:
            rhwp_command()
        except PreviewError as error:
            self.skipTest(str(error))
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            self.fixture(folder)
            self.subdivision_fixture(folder)
            rhwp(["edit", "merge-cells", "subdivision.hwp", "--table", "1", "--row", "0",
                  "--col", "1", "--end-row", "1", "--end-col", "1", "-o", "paint-merged.hwp"], folder)
            rhwp(["export-hwpx", "paint-merged.hwp", "paint-merged.hwpx"], folder)
            with patch.object(store, "DATABASE", folder/"appearance.sqlite3"):
                store.initialize()
                store.set_desktop_review(True)
                for extension in ("hwp", "hwpx"):
                    with self.subTest(extension=extension):
                        self.exercise_appearance(folder, extension)
                        original = (folder/f"paint-merged.{extension}").read_bytes()
                        editor = PagedEditor(extension, original)
                        try:
                            page = editor.page(1)
                            params = {"address": page["tables"][1]["address"], "digest": page["edit_digest"],
                                      "coordinate": [0, 0], "end": [0, 2], "change": "background", "value": "#FCE8AF"}
                            with self.assertRaisesRegex(ValueError, "병합 셀 전체"):
                                editor.page(1, "edit_table_layout", value=params)
                            self.assertEqual(editor.data, original)
                            self.assertEqual(editor.position, 0)
                            del params["end"]
                            params["coordinate"] = [0, 1]
                            self.assertTrue(editor.page(1, "edit_table_layout", value=params)["dirty"])
                        finally:
                            editor.close()

    def exercise_appearance(self, folder, extension):
        def inspect_styles(path):
            tables = exported_tables(path)
            with open_package(hangul_xml(path)) as archive:
                header = parse_part(archive, HEADER)
                styles = {node.get("id"): node for node in header.iter(HH + "borderFill")}
                refs = []
                for table in tables[:2]:
                    body = parse_part(archive, f"Contents/section{table['section']}.xml")
                    refs.append({key: node.get("borderFillIDRef")
                                 for key, node in grid_cells(source_table(body, table)).items()})
            return styles, refs

        path = folder/f"subdivision.{extension}"
        original = path.read_bytes()
        old_styles, old_refs = inspect_styles(path)
        original_layout = [table_layout_state(path, index) for index in (0, 1)]
        record = register_document(str(path), f"Cell appearance {extension}")["version"]
        doc, base = record["document_id"], record["id"]
        canvas = CanvasSession()
        self.addCleanup(canvas.close)
        opened = canvas.open(doc, "main", base, 1)
        address = opened["tables"][1]["address"]

        def edit(snapshot, change, coordinate=(1, 1), **kwargs):
            params = {"address": address, "digest": snapshot["edit_digest"], "coordinate": list(coordinate),
                      "change": change, **kwargs}
            return canvas.page(number=1, operation="edit_table_layout", value=params)

        border = {"side": "right", "style": "solid", "width": "0.5", "color": "#1852A3"}
        for invalid in ({"value": "#x"}, {}, {"value": "#FCE8AF", "digest": "stale"}):
            with self.assertRaises(ValueError):
                edit(opened, "background", **invalid)
        for invalid in ({"width": "0.6"}, {"side": "center"}):
            with self.assertRaises(ValueError):
                edit(opened, "border", value=dict(border, **invalid))
        self.assertEqual(canvas.working.data, original)
        self.assertEqual(canvas.working.position, 0)
        raster = Image.open(opened["image"]).convert("RGB")
        bordered = edit(opened, "border", value=border)
        bordered_raster = Image.open(bordered["image"]).convert("RGB")
        bounds = ImageChops.difference(raster, bordered_raster).getbbox()
        self.assertIsNotNone(bounds)
        self.assertLess(bounds[2]-bounds[0], 12)
        unlined = edit(bordered, "border", value=dict(border, style="none"))
        self.assertIsNotNone(ImageChops.difference(bordered_raster, Image.open(unlined["image"]).convert("RGB")).getbbox())
        bordered = canvas.page(number=1, operation="undo")

        filled = edit(bordered, "background", value="#FCE8AF", end=[0, 0])
        self.assertEqual(filled["table_selection"]["end"], [0, 0])
        cleared = edit(filled, "background", coordinate=(0, 0), value=None)
        final_raster = Image.open(cleared["image"]).convert("RGB")
        self.assertIsNotNone(ImageChops.difference(bordered_raster, final_raster).getbbox())
        final = canvas.working.data
        self.assertTrue(canvas.page(number=1, operation="undo")["redo"])
        self.assertNotEqual(canvas.working.data, final)
        self.assertTrue(canvas.page(number=1, operation="redo")["dirty"])
        self.assertEqual(canvas.working.data, final)
        proposal = canvas.save("Cell appearance", 1)
        with store.database(readonly=True) as db:
            self.assertEqual(store.find_version(db, doc, base, "content")["content"], original)
            saved = store.find_version(db, doc, proposal["id"], "content, extension")
        self.assertEqual(saved["extension"], extension)
        self.assertTrue(matches_format(extension, saved["content"]))
        saved_path = folder/f"appearance-saved.{extension}"
        saved_path.write_bytes(saved["content"])
        self.assertEqual([table_layout_state(saved_path, index) for index in (0, 1)], original_layout)
        styles, refs = inspect_styles(saved_path)
        self.assertEqual(refs[0], old_refs[0])
        self.assertEqual({key: xml_signature(styles[key]) for key in old_styles},
                         {key: xml_signature(node) for key, node in old_styles.items()})
        changed = {(0, 1), (1, 0), (1, 1)}
        for key, old_id in old_refs[1].items():
            if key not in changed:
                self.assertEqual(refs[1][key], old_id)
                continue
            before, after = old_styles[old_id], styles[refs[1][key]]
            self.assertEqual(after.find(f"{HC}fillBrush/{HC}winBrush").get("faceColor"), "#FCE8AF")
            for side in ("left", "right", "top", "bottom"):
                line = HH+side+"Border"
                if key == (1, 1) and side == "right":
                    self.assertEqual(tuple(after.find(line).get(attr) for attr in ("type", "width", "color")),
                                     ("SOLID", "0.5 mm", "#1852A3"))
                else:
                    self.assertEqual(xml_signature(before.find(line)), xml_signature(after.find(line)))
        review_revision(doc, proposal["id"], "approve", base)
        reopened = canvas.open(doc, "main", proposal["id"], 1)
        self.assertIsNone(ImageChops.difference(final_raster, Image.open(reopened["image"]).convert("RGB")).getbbox())
        self.assertEqual(get_preview_info(doc, proposal["id"], base)["page_counts"], [1, 1])
        _, image = render_document_page(doc, proposal["id"])
        self.assertTrue(image.data.startswith(b"\x89PNG"))

    def test_subdivide_one_regular_cell_in_both_formats(self):
        try:
            rhwp_command()
        except PreviewError as error:
            self.skipTest(str(error))
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            self.fixture(folder)
            self.subdivision_fixture(folder)
            with patch.object(store, "DATABASE", folder/"subdivision.sqlite3"):
                store.initialize()
                store.set_desktop_review(True)
                for extension in ("hwp", "hwpx"):
                    with self.subTest(extension=extension):
                        self.exercise_subdivision(folder, extension)

    @staticmethod
    def subdivision_fixture(folder):
        rhwp(["edit", "set-column-widths", "source.hwp", "--table", "1",
              "--widths", "10000,12000,10000", "-o", "split-widths.hwp"], folder)
        rhwp(["edit", "apply-char-format-in-cell", "split-widths.hwp", "--table", "1",
              "--row", "1", "--col", "1", "--props", '{"bold":true}', "-o", "split-style.hwp"], folder)
        source = "split-style.hwp"
        for col in range(3):
            modified = "subdivision.hwp" if col == 2 else f"split-height-{col}.hwp"
            rhwp(["edit", "set-cell-props", source, "--table", "1", "--row", "1", "--col", str(col),
                  "--props", '{"height":6000,"verticalAlign":2}', "-o", modified], folder)
            source = modified
        rhwp(["export-hwpx", "subdivision.hwp", "subdivision.hwpx"], folder)

    def exercise_subdivision(self, folder, extension):
        original = folder/f"subdivision.{extension}"
        record = register_document(str(original), f"Subdivision {extension}")["version"]
        doc, base = record["document_id"], record["id"]
        canvas = CanvasSession()
        self.addCleanup(canvas.close)
        opened = canvas.open(doc, "main", base, 1)
        before_raster = Image.open(opened["image"]).convert("RGB")
        table = opened["tables"][1]
        self.assertTrue(table["subdivision"])
        address = table["address"]
        payload = {"address": address, "digest": opened["edit_digest"], "coordinate": [1, 1],
                   "rows": 2, "columns": 3}

        def split(**changes):
            return canvas.page(number=1, operation="edit_table_subdivision", value=dict(payload, **changes))

        for bad in ({"rows": 1, "columns": 1}, {"rows": 0}, {"columns": 7}, {"rows": True},
                    {"coordinate": [1, 6]}, {"digest": "stale"}, {"end": [1, 2]}):
            with self.assertRaises(ValueError):
                split(**bad)
            self.assertEqual(canvas.working.data, original.read_bytes())
        with self.assertRaisesRegex(ValueError, "원래 셀의 크기"):
            split(coordinate=[0, 1], rows=2, columns=1)
        self.assertEqual(canvas.working.data, original.read_bytes())

        for rows, cols in ((1, 2), (3, 1), (2, 3)):
            edited = split(rows=rows, columns=cols)
            self.assertEqual(edited["table_selection"], {"address": address, "coordinate": [1, 1]})
            result = edited["tables"][1]
            self.assertEqual((result["rows"], result["columns"]), (rows + 1, cols + 2))
            self.assertFalse(result["subdivision"])
            self.assertIsNotNone(ImageChops.difference(before_raster, Image.open(edited["image"]).convert("RGB")).getbbox())
            with self.assertRaisesRegex(ValueError, "병합·보호"):
                split(digest=edited["edit_digest"], coordinate=[0, 1])
            self.assertFalse(canvas.page(number=1, operation="undo")["dirty"])
            self.assertEqual(canvas.working.data, original.read_bytes())
        self.assertTrue(canvas.page(number=1, operation="redo")["dirty"])
        result = canvas.save("Subdivide a styled regular cell", 1)
        with store.database(readonly=True) as db:
            self.assertEqual(store.find_version(db, doc, base, "content")["content"], original.read_bytes())
            new = store.find_version(db, doc, result["id"], "content, extension")
        self.assertEqual(new["extension"], extension)
        self.assertTrue(matches_format(extension, new["content"]))
        saved = folder/f"saved-split.{extension}"
        saved.write_bytes(new["content"])
        before_grid, after_grid = exported_tables(original), exported_tables(saved)
        self.assertEqual(after_grid[0], before_grid[0])
        self.assertEqual(table_layout_state(saved, 0), table_layout_state(original, 0))
        after_spans = {(cell["row"], cell["col"]): (cell["rowSpan"], cell["colSpan"])
                       for cell in after_grid[1]["cells"]}
        self.assertEqual(after_spans, {
            (0, 0): (1, 1), (0, 1): (1, 3), (0, 4): (1, 1),
            (1, 0): (2, 1), (1, 4): (2, 1),
            (1, 1): (1, 1), (1, 2): (1, 1), (1, 3): (1, 1),
            (2, 1): (1, 1), (2, 2): (1, 1), (2, 3): (1, 1),
        })
        before, after = table_layout_state(original, 1), table_layout_state(saved, 1)
        for previous, unchanged in (
            ((0, 0), (0, 0)), ((0, 1), (0, 1)), ((0, 2), (0, 4)),
            ((1, 0), (1, 0)), ((1, 2), (1, 4)),
        ):
            self.assertEqual(after[unchanged], before[previous])
        source = before[1, 1]
        self.assertNotEqual(source["text"][0][0][1], before[1, 0]["text"][0][0][1])
        for row in (1, 2):
            for col in (1, 2, 3):
                expected = dict(source, width=4000, height=3000)
                if (row, col) != (1, 1):
                    expected["text"] = [[("", source["text"][0][0][1])]]
                self.assertEqual(after[row, col], expected)
        review_revision(doc, result["id"], "approve", base)
        reopened = canvas.open(doc, "main", result["id"], 1)
        self.assertEqual(len(reopened["tables"][1]["cells"]), 11)
        self.assertEqual(get_preview_info(doc, result["id"], base)["page_counts"], [1, 1])
        _, image = render_document_page(doc, result["id"])
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
                grid_cells(table)[0, 0].set("protect", "0")
                nested = deepcopy(source_table(root, exported_tables(path)[0]))
                nested.set("id", "99")
                grid_cells(table)[0, 0].find(f"{HANGUL}subList/{HANGUL}p/{HANGUL}run").append(nested)
                (folder/"nested.hwpx").write_bytes(rewrite_package(archive, {part: root}))

            for filename, message, change in (
                ("merged.hwp", "병합", "row_below"), ("merged.hwpx", "병합", "row_below"),
                ("protected.hwpx", "보호", "row_below"), ("nested.hwpx", "셀", "row_below"),
                ("source.hwp", "쪽 너비", "col_right"),
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
                        if not filename.startswith("source."):
                            with self.assertRaises(ValueError):
                                editor.page(1, "edit_table_span", value={
                                    "address": table["address"], "coordinate": [1, 1], "end": [1, 2],
                                    "digest": opened["edit_digest"], "change": "merge",
                                })
                        if not filename.startswith("source."):
                            self.assertFalse(table["subdivision"])
                            with self.assertRaises(ValueError):
                                editor.page(1, "edit_table_subdivision", value={"address": table["address"],
                                    "coordinate": [1, 1], "digest": opened["edit_digest"], "rows": 2, "columns": 2})
                        if filename in ("protected.hwpx", "nested.hwpx"):
                            self.assertFalse(table["layout_grid"])
                            with self.assertRaises(ValueError):
                                editor.page(1, "edit_table_layout", value={"address": table["address"],
                                    "coordinate": [0, 0], "digest": opened["edit_digest"],
                                    "change": "horizontal", "value": "right"})
                            with self.assertRaises(ValueError):
                                editor.page(1, "edit_table_layout", value={"address": table["address"],
                                    "coordinate": [0, 0], "digest": opened["edit_digest"],
                                    "change": "background", "value": "#FCE8AF"})
                        self.assertEqual(editor.position, 0)
                        self.assertEqual(editor.data, original)
                    finally:
                        editor.close()

    def test_merge_and_split_preserve_order_and_styles_in_both_formats(self):
        try:
            rhwp_command()
        except PreviewError as error:
            self.skipTest(str(error))
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            self.fixture(folder)
            rhwp(["edit", "insert-row", "source.hwp", "--table", "1", "--row", "1", "--below",
                  "-o", "three-rows.hwp"], folder)
            rhwp(["edit", "set-cell", "three-rows.hwp", "--table", "1", "--row", "2", "--col", "0",
                  "--text", "셋째 줄 왼쪽", "-o", "bottom-left.hwp"], folder)
            rhwp(["edit", "set-cell", "bottom-left.hwp", "--table", "1", "--row", "2", "--col", "1",
                  "--text", "셋째 줄 오른쪽", "-o", "bottom-right.hwp"], folder)
            rhwp(["edit", "apply-char-format-in-cell", "bottom-right.hwp", "--table", "1", "--row", "1",
                  "--col", "0", "--props", '{"bold":true}', "-o", "span.hwp"], folder)
            rhwp(["export-hwpx", "span.hwp", "span.hwpx"], folder)
            with patch.object(store, "DATABASE", folder/"span.sqlite3"):
                store.initialize()
                store.set_desktop_review(True)
                for extension in ("hwp", "hwpx"):
                    with self.subTest(extension=extension):
                        self.exercise_span(folder, extension)

    def exercise_span(self, folder, extension):
        def styles(table, coordinate):
            cell = grid_cells(table)[coordinate]
            return [(run.find(HANGUL + "t").text, run.get("charPrIDRef"), para.get("paraPrIDRef"))
                    for para in cell.findall(f"{HANGUL}subList/{HANGUL}p")
                    for run in para.findall(HANGUL + "run")]

        path = folder/f"span.{extension}"
        original = path.read_bytes()
        native = exported_tables(path)
        address = [native[1][key] for key in ("section", "paragraph", "control")]
        with open_package(hangul_xml(path)) as archive:
            table = source_table(parse_part(archive, "Contents/section0.xml"), native[1])
            expected = sum((styles(table, key) for key in ((1, 0), (1, 1), (2, 0), (2, 1))), [])
        self.assertEqual([text for text, *_ in expected], ["같은 값", "같은 값", "셋째 줄 왼쪽", "셋째 줄 오른쪽"])
        self.assertNotEqual(expected[0][1], expected[1][1])
        entry = register_document(str(path), f"Span QA {extension}")["version"]
        doc, base = entry["document_id"], entry["id"]
        canvas = CanvasSession()
        self.addCleanup(canvas.close)
        page = canvas.open(doc, "main", base, 1)

        def edit(page, change, coordinate, **params):
            return canvas.page(number=1, operation="edit_table_span", value={
                "address": address, "digest": page["edit_digest"], "change": change, "coordinate": coordinate, **params,
            })

        with self.assertRaisesRegex(ValueError, "서식"):
            edit(page, "merge", [0, 0], end=[1, 1])
        for bad in ([5, 0], [0, False], [1, 0]):
            with self.assertRaises(ValueError):
                edit(page, "merge", [1, 0], end=bad)
        self.assertEqual(canvas.working.data, original)
        before = Image.open(page["image"]).convert("RGB")
        merged = edit(page, "merge", [2, 1], end=[1, 0])
        self.assertIsNotNone(ImageChops.difference(before, Image.open(merged["image"]).convert("RGB")).getbbox())
        self.assertIn([2, 2], [item.get("span") for item in merged["tables"][1]["cells"]])
        with self.assertRaises(ValueError):
            edit(page, "merge", [2, 1], end=[1, 0])
        with self.assertRaises(ValueError):
            edit(merged, "merge", [1, 0], end=[2, 2])
        self.assertFalse(canvas.page(number=1, operation="undo")["dirty"])
        self.assertEqual(canvas.working.data, original)
        self.assertTrue(canvas.page(number=1, operation="redo")["dirty"])
        merge_version = canvas.save("Merge selected cells", 1)["id"]
        review_revision(doc, merge_version, "approve", base)
        merged = canvas.open(doc, "main", merge_version, 1)
        self.assertIn([2, 2], [item.get("span") for item in merged["tables"][1]["cells"]])

        split = edit(merged, "split", [1, 0])
        self.assertFalse(any(item.get("span") for item in split["tables"][1]["cells"]))
        self.assertFalse(canvas.page(number=1, operation="undo")["dirty"])
        self.assertTrue(canvas.page(number=1, operation="redo")["dirty"])
        split_version = canvas.save("Split merged cells", 1)["id"]
        review_revision(doc, split_version, "approve", merge_version)
        reopened = canvas.open(doc, "main", split_version, 1)
        self.assertIn("표 주변 본문", [item["text"] for item in reopened["objects"]])
        with store.database(readonly=True) as db:
            self.assertEqual(store.find_version(db, doc, base, "content")["content"], original)
            for version, merged_flag in ((merge_version, True), (split_version, False)):
                saved = store.find_version(db, doc, version, "content, extension")
                self.assertEqual(saved["extension"], extension)
                output = folder/f"span-{version}.{extension}"
                output.write_bytes(saved["content"])
                grid = exported_tables(output)
                self.assertEqual(grid[0], native[0])
                self.assertEqual((grid[1]["rows"], grid[1]["cols"]), (3, 3))
                by_address = {(cell["row"], cell["col"]): cell for cell in grid[1]["cells"]}
                self.assertEqual(by_address[1, 0]["colSpan"], 2 if merged_flag else 1)
                if not merged_flag:
                    self.assertEqual([by_address[key]["text"] for key in ((1, 1), (2, 0), (2, 1))], ["", "", ""])
                with open_package(hangul_xml(output)) as archive:
                    table = source_table(parse_part(archive, "Contents/section0.xml"), grid[1])
                    self.assertEqual(styles(table, (1, 0)), expected)
        _, image = render_document_page(doc, split_version)
        self.assertTrue(image.data.startswith(b"\x89PNG"))

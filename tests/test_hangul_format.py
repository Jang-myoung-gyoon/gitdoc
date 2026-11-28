import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import store
from desktop.bridge import CanvasSession
from desktop.document_objects import HANGUL
from desktop.exporting import convert_format
from desktop.hangul_format import HEADER, HH, format_paragraph
from desktop.paged_editor import rhwp
from editing import open_package, parse_part
from mcp_server import get_preview_info, register_document, render_document_page
from preview import PreviewError, rhwp_command
from workflow import review_revision


def record_state(content, extension):
    if extension == "hwp":
        content = convert_format("hwp", "hwpx", content)
    with open_package(content) as archive:
        header = parse_part(archive, HEADER)
        section = parse_part(archive, "Contents/section0.xml")
    state = {}
    for paragraph in section.findall(HANGUL+"p"):
        run = next((run for run in paragraph.findall(HANGUL+"run")
                    if (text := run.find(HANGUL+"t")) is not None and (text.text or "").strip()), None)
        if run is None:
            continue
        char = next(node for node in header.iter(HH+"charPr") if node.get("id") == run.get("charPrIDRef"))
        para = next(node for node in header.iter(HH+"paraPr") if node.get("id") == paragraph.get("paraPrIDRef"))
        state[run.find(HANGUL+"t").text] = {
            "bold": char.find(HH+"bold") is not None,
            "italic": char.find(HH+"italic") is not None,
            "underline": char.find(HH+"underline").get("type"),
            "color": char.get("textColor"), "height": char.get("height"),
            "align": para.find(HH+"align").get("horizontal"),
            "spacing": {item.get("value") for item in para.iter(HH+"lineSpacing")},
        }
    return state


class HangulFormattingTests(unittest.TestCase):
    def test_hwp_and_hwpx_apply_selected_paragraph_format_after_save_and_reopen(self):
        try:
            rhwp_command()
        except PreviewError as error:
            self.skipTest(str(error))
        with tempfile.TemporaryDirectory() as folder, patch.object(store, "DATABASE", Path(folder)/"style.sqlite3"):
            folder = Path(folder)
            store.initialize()
            store.set_desktop_review(True)
            rhwp(["gen-table"], folder)
            rhwp(["edit", "delete-table", "output/gen_table.hwp", "--table", "0", "-o", "blank.hwp"], folder)
            rhwp(["edit", "insert-text", "blank.hwp", "--text", "첫 번째 문단", "-o", "first.hwp"], folder)
            rhwp(["edit", "insert-text", "first.hwp", "--para", "1", "--text", "둘째 문단", "-o", "source.hwp"], folder)
            (folder/"source.hwpx").write_bytes(convert_format("hwp", "hwpx", (folder/"source.hwp").read_bytes()))
            for extension in ("hwp", "hwpx"):
                with self.subTest(extension=extension):
                    path = folder/f"source.{extension}"
                    original = path.read_bytes()
                    base_state = record_state(original, extension)
                    entry = register_document(str(path), f"Style QA {extension}")["version"]
                    doc, base = entry["document_id"], entry["id"]
                    canvas = CanvasSession()
                    self.addCleanup(canvas.close)
                    first = canvas.open(doc, "main", base, 1)
                    target = next({"id": i["id"]} for i in first["objects"]
                                  if i["text"]=="둘째 문단" and i["hangul_format"])
                    for op, value in [("bold", None), ("italic", None), ("underline", None), ("size", 14),
                                      ("color", "1852A3"), ("align", "center"), ("spacing", 180)]:
                        selection = [0, len("둘째 문단")] if op == "color" else None
                        canvas.page(number=1, operation=op, target=target, value=value, selection=selection)
                    before_undo = canvas.working.data
                    canvas.page(number=1, operation="undo")
                    self.assertNotEqual(canvas.working.data, before_undo)
                    canvas.page(number=1, operation="redo")
                    self.assertEqual(canvas.working.data, before_undo)
                    proposal = canvas.save("Apply styles to one Hangul paragraph", 1)
                    self.assertEqual(proposal["status"], "pending")
                    with store.database(readonly=True) as db:
                        unchanged = store.find_version(db, doc, base, "content")
                        saved = store.find_version(db, doc, proposal["id"], "content, extension")
                    self.assertEqual(unchanged["content"], original)
                    self.assertEqual(saved["extension"], extension)
                    state = record_state(saved["content"], extension)
                    self.assertEqual(state["첫 번째 문단"], base_state["첫 번째 문단"])
                    updated = state["둘째 문단"]
                    self.assertTrue(updated["bold"] and updated["italic"])
                    self.assertEqual((updated["underline"], updated["height"], updated["color"], updated["align"]),
                                     ("BOTTOM", "1400", "#1852A3", "CENTER"))
                    self.assertEqual(updated["spacing"], {"180"})
                    review_revision(doc, proposal["id"], "approve", base)
                    self.assertIn("둘째 문단", [i["text"] for i in canvas.open(doc, "main", proposal["id"], 1)["objects"]])
                    self.assertEqual(get_preview_info(doc, proposal["id"], base)["page_counts"], [1,1])
                    _, image = render_document_page(doc, proposal["id"])
                    self.assertEqual(image.to_image_content().mimeType, "image/png")

    def test_hwp_and_hwpx_style_only_selected_characters_across_existing_runs(self):
        try:
            rhwp_command()
        except PreviewError as error:
            self.skipTest(str(error))
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            rhwp(["gen-table"], folder)
            rhwp(["edit", "delete-table", "output/gen_table.hwp", "--table", "0", "-o", "blank.hwp"], folder)
            rhwp(["edit", "insert-text", "blank.hwp", "--text", "앞문단", "-o", "previous.hwp"], folder)
            text = "한글 가나다 끝말"
            rhwp(["edit", "insert-text", "previous.hwp", "--para", "1", "--text", text,
                  "-o", "plain.hwp"], folder)
            rhwp(["export-hwpx", "plain.hwp", "plain.hwpx"], folder)
            mixed = format_paragraph((folder/"plain.hwpx").read_bytes(), 0, 1, "color", "1852A3",
                                     selection=[0, 5], original=text)
            paths = {"hwp": convert_format("hwpx", "hwp", mixed), "hwpx": mixed}

            with patch.object(store, "DATABASE", folder/"ranges.sqlite3"):
                store.initialize()
                store.set_desktop_review(True)
                for extension, data in paths.items():
                    with self.subTest(extension=extension):
                        self.check_character_range(folder, extension, data, text)

    def check_character_range(self, folder, extension, data, text):
        source = folder/f"mixed.{extension}"
        source.write_bytes(data)
        entry = register_document(str(source), f"Selected text {extension}")["version"]
        doc, base = entry["document_id"], entry["id"]
        canvas = CanvasSession()
        self.addCleanup(canvas.close)
        initial = canvas.open(doc, "main", base, 1)
        target = next({"id": item["id"]} for item in initial["objects"] if item["text"] == text)
        for selection in ([True, 3], [-1, 3], [3, 1], [2, len(text)+1]):
            with self.assertRaises(ValueError):
                canvas.page(number=1, operation="bold", target=target, selection=selection)
        for empty in (None, [2, 2]):
            with self.assertRaisesRegex(ValueError, "글자 색"):
                canvas.page(number=1, operation="color", target=target, value="00796F", selection=empty)
        self.assertEqual(canvas.working.data, data)
        selection = [1, 7]
        canvas.page(number=1, operation="color", target=target, value="00796F", selection=selection)
        result = canvas.page(number=1, operation="bold", target=target, selection=selection)
        self.assertEqual(result["hangul_selection"], selection)
        canvas.page(number=1, operation="size", target=target, selection=selection, value=16)
        expected = character_styles(data, extension)
        for i in range(*selection):
            char, _, _, _ = expected[1][i]
            expected[1][i] = (char, "#00796F", True, "1600")
        self.assertTrue(canvas.page(number=1, operation="undo")["redo"])
        self.assertTrue(canvas.page(number=1, operation="redo")["dirty"])
        proposal = canvas.save("Format selected characters", 1)
        with store.database(readonly=True) as db:
            unchanged = store.find_version(db, doc, base, "content")
            saved = store.find_version(db, doc, proposal["id"], "content, extension")
        self.assertEqual(unchanged["content"], data)
        self.assertEqual(saved["extension"], extension)
        self.assertEqual(character_styles(saved["content"], extension), expected)
        review_revision(doc, proposal["id"], "approve", base)
        self.assertIn(text, [item["text"] for item in canvas.open(doc, "main", proposal["id"], 1)["objects"]])
        _, image = render_document_page(doc, proposal["id"])
        self.assertTrue(image.data.startswith(b"\x89PNG"))


def character_styles(data, extension):
    if extension == "hwp":
        data = convert_format("hwp", "hwpx", data)
    with open_package(data) as archive:
        header = parse_part(archive, HEADER)
        section = parse_part(archive, "Contents/section0.xml")
    styles = {node.get("id"): (node.get("textColor"), node.find(HH+"bold") is not None, node.get("height"))
              for node in header.iter(HH+"charPr")}
    return [[(char, *styles[run.get("charPrIDRef")])
             for run in paragraph.findall(HANGUL+"run") for text in run.findall(HANGUL+"t")
             for char in text.text or ""] for paragraph in section.findall(HANGUL+"p")]

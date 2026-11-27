import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import store
from desktop.bridge import CanvasSession
from desktop.document_objects import HANGUL
from desktop.exporting import convert_format
from desktop.hangul_format import HEADER, HH
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
                        canvas.page(number=1, operation=op, target=target, value=value)
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

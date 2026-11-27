import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from pptx import Presentation
from pptx.util import Inches

from desktop.presentation import Deck, save


class PresentationEditorTests(unittest.TestCase):
    def setUp(self):
        original = Presentation()
        slide = original.slides.add_slide(original.slide_layouts[6])
        shape = slide.shapes.add_textbox(Inches(1), Inches(1), Inches(6), Inches(1))
        paragraph = shape.text_frame.paragraphs[0]
        paragraph.add_run().text = "First "
        second = paragraph.add_run()
        second.text = "second"
        second.font.italic = True
        self.target = {"id": shape.shape_id}
        self.deck = Deck(save(original))
        self.addCleanup(self.deck.close)
        render = patch.object(self.deck, "render", side_effect=lambda deck, index, data=None: {
            "page": min(index + 1, len(deck.slides)), "count": len(deck.slides)})
        render.start()
        self.addCleanup(render.stop)

    def read(self):
        return Presentation(io.BytesIO(self.deck.data))

    def test_typing_and_selection_format_keep_other_runs(self):
        self.deck.page(1, "bold", target=self.target, selection=[0, 2],
                       text_edit={"target": self.target, "text": "First 셋째"})
        runs = self.read().slides[0].shapes[0].text_frame.paragraphs[0].runs
        self.assertEqual([(r.text, r.font.bold, r.font.italic) for r in runs],
                         [("Fi", True, None), ("rst ", None, None), ("셋째", None, True)])
        self.deck.page(1, "undo")
        self.assertEqual(self.read().slides[0].shapes[0].text, "First second")
        self.deck.page(1, "redo")
        self.assertEqual(self.read().slides[0].shapes[0].text, "First 셋째")

    def test_alignment_updates_one_text_box_without_invoking_slide_render(self):
        with patch.object(self.deck, "render", side_effect=AssertionError("whole slide render")):
            result = self.deck.page(1, "align", target=self.target, value="right", patch_text=True)
        self.assertEqual(result["patch"]["id"], self.target["id"])
        self.assertEqual(result["patch"]["align"], ["right"])
        self.assertNotIn("image", result)
        self.assertEqual(self.read().slides[0].shapes[0].text_frame.paragraphs[0].alignment.name, "RIGHT")

    def test_duplicate_retains_shapes_and_relationships_and_navigation_commits_other_slide(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.png"
            Image.new("RGB", (40, 20), "red").save(path)
            self.deck.page(1, "image", value=str(path))
        clone = self.deck.page(1, "duplicate_slide")
        self.assertEqual((clone["page"], clone["count"]), (2, 2))
        self.deck.page(1, "page", text_page=2,
                       text_edit={"target": self.target, "text": "다른 슬라이드"})
        original, copied = self.read().slides
        self.assertEqual(original.shapes[0].text, "First second")
        self.assertEqual(copied.shapes[0].text, "다른 슬라이드")
        self.assertEqual(original.shapes[1].image.blob, copied.shapes[1].image.blob)

    def test_move_object_to_front_and_back(self):
        middle = self.deck.page(1, "shape", value="triangle")["selected"]["id"]
        last = self.deck.page(1, "shape", value="rectangle")["selected"]["id"]
        self.deck.page(1, "front", target=self.target)
        self.assertEqual([shape.shape_id for shape in self.read().slides[0].shapes],
                         [middle, last, self.target["id"]])
        self.deck.page(1, "back", target=self.target)
        self.assertEqual([shape.shape_id for shape in self.read().slides[0].shapes],
                         [self.target["id"], middle, last])

    def test_group_ungroup_and_table_cell_edit(self):
        box = self.deck.page(1, "shape", value="triangle")["selected"]
        group = self.deck.page(1, "group", ids=[self.target["id"], box["id"]])["selected"]
        self.assertEqual(len(self.read().slides[0].shapes[0].shapes), 2)
        self.deck.page(1, "ungroup", target=group)
        self.assertEqual(len(self.read().slides[0].shapes), 2)
        table = self.deck.page(1, "table", value=[2, 3])["selected"]
        self.deck.page(1, text_edit={"target": dict(table, cell=[1, 2]), "text": "셀 수정"})
        self.assertEqual(self.read().slides[0].shapes[-1].table.cell(1, 2).text, "셀 수정")

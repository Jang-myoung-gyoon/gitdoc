import tempfile
import unittest
from pathlib import Path

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject

from extract import comparison_text
from page_alignment import aligned_pages, match_pages, matching_blocks


def write_pdf(path, offsets):
    with PdfWriter() as writer:
        for offset in offsets:
            page = writer.add_blank_page(width=540, height=720)
            drawing = DecodedStreamObject()
            drawing.set_data(f"0 0 1 rg {offset} 120 40 50 re f".encode())
            page.replace_contents(drawing)
        writer.write(path)


class PageAlignmentTests(unittest.TestCase):
    def test_middle_insertion_and_deletion_use_original_page_numbers(self):
        with tempfile.TemporaryDirectory() as folder:
            original = Path(folder) / "original.pdf"
            updated = Path(folder) / "updated.pdf"
            write_pdf(original, [20, 60, 140])
            write_pdf(updated, [20, 220, 60, 140])
            self.assertEqual(aligned_pages([original, updated], [3, 4]),
                             [[1, 1], [None, 2], [2, 3], [3, 4]])
            self.assertEqual(aligned_pages([updated, original], [4, 3]),
                             [[1, 1], [2, None], [3, 2], [4, 3]])

    def test_duplicates_remain_in_order(self):
        self.assertEqual(match_pages(["a", "b", "a"], ["a", "a", "b", "a"]),
                         [[1, 1], [None, 2], [2, 3], [3, 4]])

    def test_repeated_pages_keep_the_maximum_number_of_ordered_matches(self):
        self.assertEqual(sum(size for _, _, size in matching_blocks("aaaaa", "abaaa")), 4)

    def test_an_edit_and_a_later_insertion_are_separate(self):
        self.assertEqual(match_pages(["a", "b", "c", "d"], ["a", "B", "c", "new", "d"]),
                         [[1, 1], [2, 2], [3, 3], [None, 4], [4, 5]])

    def test_small_visual_differences_do_not_hide_a_page_deletion(self):
        def render(key, tone, first=None):
            return ((540, 720, key), bytes([tone if first is None else first] + [tone] * 19))

        before = (render("a", 20), render("removed", 90), render("b", 40), render("c", 60))
        after = (render("a", 20), render("B", 40, 41), render("C", 60, 61))
        self.assertEqual(match_pages([row[0] for row in before], [row[0] for row in after],
                                     renders=(before, after)),
                         [[1, 1], [2, None], [3, 2], [4, 3]])

    def test_comparison_ignores_only_generated_page_or_slide_headings(self):
        self.assertEqual(comparison_text({"extension": "pdf", "extracted": "[페이지 5]\n5쪽\n본문"}),
                         "5쪽\n본문")
        self.assertEqual(comparison_text({"extension": "pptx", "extracted": "[슬라이드 3]\n제목"}),
                         "제목")

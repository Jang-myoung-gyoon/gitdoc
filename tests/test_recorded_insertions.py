import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pypdf import PdfReader, PdfWriter

import store
from page_alignment import comparison_pages
from uploads import prepare_upload
from workflow import review_revision, save_document_edit, save_proposal


def pdf(count):
    stream = io.BytesIO()
    with PdfWriter() as writer:
        for _ in range(count):
            writer.add_blank_page(width=540, height=720)
        writer.write(stream)
    return prepare_upload("sample.pdf", stream.getvalue())


class RecordedInsertionTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.folder = Path(directory.name)
        patcher = patch.object(store, "DATABASE", self.folder / "store.sqlite3")
        patcher.start()
        self.addCleanup(patcher.stop)
        store.initialize()
        with store.database(write=True) as db:
            self.doc = store.create_document(db, "PDF", pdf(3))
            self.base = store.find_document(db, self.doc)["current_id"]

    def compare(self, before, after):
        paths, counts = [], []
        with store.database(readonly=True) as db:
            for version in (before, after):
                path = self.folder / f"{version}.pdf"
                path.write_bytes(store.find_version(db, self.doc, version, "content")["content"])
                paths.append(path)
                counts.append(len(PdfReader(path).pages))
        return comparison_pages(self.doc, [before, after], paths, counts)

    def test_known_insertion_wins_with_identical_pages_and_survives_later_edit(self):
        changed = save_document_edit(self.doc, "main", self.base, {"operation": "insert", "page": 2}, "insert")
        expected = [[1, 1], [None, 2], [2, 3], [3, 4]]
        self.assertEqual(self.compare(self.base, changed), (expected, [None, "recorded", None, None]))
        self.assertEqual(self.compare(changed, self.base),
                         ([[1, 1], [2, None], [3, 2], [4, 3]], [None, "recorded", None, None]))
        review_revision(self.doc, changed, "approve", self.base)
        later = save_document_edit(self.doc, "main", changed, {"operation": "right", "page": 3}, "rotate")
        self.assertEqual(self.compare(self.base, later), (expected, [None, "recorded", None, None]))

    def test_uploaded_pdf_still_uses_inference_and_file_insertion_records_count(self):
        uploaded = save_proposal(self.doc, "main", self.base, pdf(5), "upload")
        self.assertEqual(self.compare(self.base, uploaded)[1].count("inferred"), 2)
        inserted = save_document_edit(self.doc, "main", self.base, {"operation": "insert", "page": 2},
                                      "insert two", source=pdf(2))
        self.assertEqual(self.compare(self.base, inserted),
                         ([[1, 1], [None, 2], [None, 3], [2, 4], [3, 5]],
                          [None, "recorded", "recorded", None, None]))
        with store.database(readonly=True) as db:
            version = store.find_version(db, self.doc, inserted)
            self.assertEqual((version["page_insert_before"], version["page_insert_count"]), (2, 2))

    def test_direct_insert_after_upload_keeps_its_position_in_older_comparisons(self):
        uploaded = save_proposal(self.doc, "main", self.base, pdf(4), "upload")
        review_revision(self.doc, uploaded, "approve", self.base)
        inserted = save_document_edit(self.doc, "main", uploaded, {"operation": "insert", "page": 2}, "insert")
        self.assertEqual(self.compare(self.base, inserted),
                         ([[1, 1], [None, 2], [2, 3], [3, 4], [None, 5]],
                          [None, "recorded", None, None, "inferred"]))

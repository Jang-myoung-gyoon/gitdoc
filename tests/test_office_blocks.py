import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

from lxml import etree
from pypdf import PdfWriter

import store
from desktop.bridge import submit, working_copy
from editing import WORD, editable_office_blocks, replace_office_blocks
from uploads import load_local_file


class OfficeBlockTests(unittest.TestCase):
    def test_text_change_preserves_other_runs_and_can_style_only_one_block(self):
        initial = etree.fromstring(f"""
            <w:document xmlns:w="{WORD}"><w:body>
              <w:p><w:r><w:rPr><w:b/></w:rPr><w:t>prefix </w:t></w:r>
                <w:r><w:rPr><w:i/></w:rPr><w:t>old suffix</w:t></w:r></w:p>
              <w:p><w:r><w:t>second</w:t></w:r></w:p>
            </w:body></w:document>
        """)
        data = io.BytesIO()
        with ZipFile(data, "w") as archive:
            archive.writestr("word/document.xml", etree.tostring(initial))
            archive.writestr("word/media/example", b"unchanged")
        edits = [{"id": 1, "text": "prefix new suffix"}, {"id": 2, "text": "second",
                 "style": {"bold": True, "size": 14, "color": "1a2b3c", "align": "center"}}]
        edited = replace_office_blocks("docx", data.getvalue(), edits)
        self.assertEqual([item["text"] for item in editable_office_blocks("docx", edited)],
                         ["prefix new suffix", "second"])
        ns = {"w": WORD}
        with ZipFile(io.BytesIO(edited)) as archive:
            self.assertEqual(archive.read("word/media/example"), b"unchanged")
            document = etree.fromstring(archive.read("word/document.xml"))
        self.assertEqual(document.xpath("//w:p[1]/w:r/w:t/text()", namespaces=ns), ["prefix ", "new suffix"])
        self.assertEqual(document.xpath("//w:p[2]/w:r/w:rPr/w:sz/@w:val", namespaces=ns), ["28"])
        self.assertEqual(document.xpath("//w:p[2]/w:r/w:rPr/w:color/@w:val", namespaces=ns), ["1A2B3C"])
        self.assertEqual(document.xpath("//w:p[2]/w:pPr/w:jc/@w:val", namespaces=ns), ["center"])

    def test_external_editor_modifies_only_working_copy_then_submits_as_draft(self):
        def write_pdf(path, width):
            with PdfWriter() as pdf:
                pdf.add_blank_page(width=width, height=200)
                pdf.write(path)

        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            database = folder / "store.sqlite3"
            source = folder / "source.pdf"
            write_pdf(source, 100)
            original = source.read_bytes()
            with patch.object(store, "DATABASE", database), patch("desktop.bridge.DATABASE", database):
                store.initialize()
                with store.database(write=True) as db:
                    doc = store.create_document(db, "Editor fixture", load_local_file(source))
                    head = store.find_document(db, doc)["current_id"]
                with patch("desktop.bridge.open_editor_file") as open_file:
                    working = working_copy(doc, "main", head)
                path = Path(working["path"])
                open_file.assert_called_once_with(path)
                self.assertEqual(path.read_bytes(), original)
                write_pdf(path, 150)
                draft = submit(doc, "main", head, str(path), "Edit in associated application")
                with store.database(readonly=True) as db:
                    self.assertEqual(store.find_document(db, doc)["current_id"], head)
                    self.assertEqual(store.find_version(db, doc, head, "content")["content"], original)
                    self.assertEqual(store.find_version(db, doc, draft)["status"], "pending")

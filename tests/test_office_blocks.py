import io
import unittest
from zipfile import ZipFile

from lxml import etree

from editing import WORD, editable_office_blocks, replace_office_blocks


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

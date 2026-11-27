import asyncio
import base64
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from pypdf import PdfWriter

import preview
import store
from mcp_server import export_document, get_preview_info, list_visual_changes, register_document, server


class DocumentToolTests(unittest.TestCase):
    def test_recorded_insertion_returns_a_real_mcp_image_and_export_is_exclusive(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            source = folder / "one.pdf"
            with PdfWriter() as writer:
                writer.add_blank_page(width=60, height=80)
                writer.write(source)
            with patch.object(store, "DATABASE", folder / "documents.sqlite3"), \
                    patch.object(preview, "CACHE", folder / "previews"):
                store.initialize()
                base = register_document(str(source), "PDF fixture")["version"]
                doc, before = base["document_id"], base["id"]
                edited = asyncio.run(server.call_tool("edit_revision", {
                    "document_id": doc, "base_version_id": before,
                    "edit": {"operation": "insert", "page": 1}, "reason": "Insert a page",
                }))[1]
                after = edited["version"]["id"]
                self.assertEqual(get_preview_info(doc, after, before)["pages"][0]["pages"], [None, 1])
                self.assertEqual(list_visual_changes(doc, before, after)["changes"][0]["alignment_source"], "recorded")

                blocks = asyncio.run(server.call_tool("render_document_page", {
                    "document_id": doc, "version_id": after, "base_version_id": before, "page": 1,
                    "max_side": 256,
                }))
                photo = next(block for block in blocks if block.type == "image")
                self.assertEqual(photo.mimeType, "image/png")
                with Image.open(io.BytesIO(base64.b64decode(photo.data))) as image:
                    self.assertEqual(image.format, "PNG")
                    self.assertGreater(image.width, 256)

                destination = folder / "export.pdf"
                export_document(doc, before, str(destination))
                self.assertEqual(destination.read_bytes(), source.read_bytes())
                with self.assertRaises(FileExistsError):
                    export_document(doc, after, str(destination))
                self.assertEqual(destination.read_bytes(), source.read_bytes())

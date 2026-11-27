import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import store
from workflow import review_revision, save_proposal


class DesktopReviewTests(unittest.TestCase):
    def test_review_policy_applies_only_to_new_desktop_versions(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(store, "DATABASE", Path(folder) / "test.db"):
            store.initialize()
            upload = lambda text: ("example.pdf", "pdf", len(text), text, "", text, text.encode())
            with store.database(write=True) as db:
                doc = store.create_document(db, "Test", upload("original"))
                original = store.find_document(db, doc)["current_id"]
                self.assertFalse(store.desktop_review_enabled(db))
            automatic = save_proposal(doc, "main", original, upload("first edit"), "edit", desktop=True)
            store.set_desktop_review(True)
            pending = save_proposal(doc, "main", automatic, upload("second edit"), "edit", desktop=True)
            store.set_desktop_review(False)
            mcp = save_proposal(doc, "main", automatic, upload("MCP draft"), "edit")
            with store.database(readonly=True) as db:
                self.assertEqual(store.find_document(db, doc)["current_id"], automatic)
                self.assertEqual([store.find_version(db, doc, v)["status"] for v in (automatic, pending, mcp)],
                                 ["approved", "pending", "pending"])
            with self.assertRaisesRegex(ValueError, "설정"):
                review_revision(doc, pending, "approve", automatic, desktop=True)
            store.set_desktop_review(True)
            review_revision(doc, pending, "approve", automatic, desktop=True)
            with store.database(readonly=True) as db:
                self.assertEqual(store.find_document(db, doc)["current_id"], pending)

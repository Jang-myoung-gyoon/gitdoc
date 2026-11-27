import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pypdf import PdfWriter

import store
from uploads import prepare_upload
from workflow import reset_branch, save_proposal


def pdf(width):
    output = io.BytesIO()
    with PdfWriter() as writer:
        writer.add_blank_page(width=width, height=100)
        writer.write(output)
    return prepare_upload("revision.pdf", output.getvalue())


class BranchResetTests(unittest.TestCase):
    def test_rewind_updates_main_and_keeps_removed_commits_and_drafts_in_recovery(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(store, "DATABASE", Path(tmp) / "store"):
            store.initialize()
            with store.database(write=True) as db:
                doc = store.create_document(db, "Reset fixture", pdf(100))
                main = store.find_branch(db, doc, "main")
                first = main["head_id"]
                second = store.append_version(db, main, pdf(110), "Second")
                store.new_branch(db, doc, "other", second)
            draft = save_proposal(doc, "main", second, pdf(120), "Pending")
            with self.assertRaisesRegex(ValueError, "변경되었습니다"):
                reset_branch(doc, "main", first, first)
            result = reset_branch(doc, "main", first, second)
            self.assertEqual(result["removed_commits"], 1)
            with store.database(readonly=True) as db:
                self.assertEqual(store.find_document(db, doc)["current_id"], first)
                for name, history in (("main", [first]), ("other", [second, first]),
                                      (result["recovery_branch"], [draft, second, first])):
                    branch = store.find_branch(db, doc, name)
                    self.assertEqual([version["id"] for version in store.branch_history(db, branch)], history)
            with self.assertRaisesRegex(ValueError, "이전 확정"):
                reset_branch(doc, "main", second, first)

import asyncio
import sqlite3
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

import store
from app import app
from desktop.bridge import detail
from desktop_link import checked_document_path
from mcp_server import list_branches, split_document_branch
from test_branch_reset import pdf
from workflow import review_revision, save_proposal, split_branch


class BranchSplitTests(unittest.TestCase):
    def setUp(self):
        staging = tempfile.TemporaryDirectory()
        self.addCleanup(staging.cleanup)
        location = patch.object(store, "DATABASE", Path(staging.name)/"documents.sqlite3")
        location.start()
        self.addCleanup(location.stop)
        store.initialize()
        with store.database(write=True) as db:
            self.doc = store.create_document(db, "Original", pdf(100))
            main = store.find_branch(db, self.doc, "main")
            self.start = main["head_id"]
            self.main = store.append_version(db, main, pdf(110), "Main changes")
            store.new_branch(db, self.doc, "sibling", self.start)
            store.new_branch(db, self.doc, "proposal", self.start)
            branch = store.find_branch(db, self.doc, "proposal")
            self.branch = branch["id"]
            self.first = store.append_version(db, branch, pdf(120), "First branch change")
        self.rejected = save_proposal(self.doc, "proposal", self.first, pdf(121), "Rejected change")
        review_revision(self.doc, self.rejected, "reject", self.first, "Keep review note")
        with store.database(write=True) as db:
            branch = store.find_branch(db, self.doc, "proposal")
            self.head = store.append_version(db, branch, pdf(130), "Branch head")

    def split(self, **override):
        return split_branch(**dict(document_id=self.doc, name="proposal", branch_id=self.branch,
                                   expected_head=self.head, title="Independent", confirmed=True) | override)

    def snapshot(self):
        with store.database(readonly=True) as db:
            return [list(map(tuple, db.execute(f"SELECT * FROM {table} ORDER BY 1"))) for table in
                    ("documents", "branches", "versions", "branch_splits")]

    def test_saved_history_remains_independent_and_old_version_links_work(self):
        original_versions = self.snapshot()[2]
        result = split_document_branch(self.doc, "proposal", self.branch, self.head, "Independent", True)
        new_doc, new_head = result["document_id"], result["head_id"]
        self.assertNotEqual(new_doc, self.doc)
        self.assertIn(f"/documents/{new_doc}?version={new_head}", result["desktop_url"])
        self.assertEqual(result["version_count"], 4)
        self.assertEqual(result["origin"]["source_head_id"], self.head)
        self.assertEqual(list_branches(new_doc)["split_origin"]["source_branch"], "proposal")
        self.assertEqual([row["name"] for row in list_branches(self.doc)["branches"]], ["main", "sibling"])
        self.assertEqual([row["name"] for row in list_branches(new_doc)["branches"]], ["main"])
        with store.database(readonly=True) as db:
            self.assertEqual(store.find_document(db, self.doc)["current_id"], self.main)
            self.assertEqual(store.find_branch(db, self.doc, "sibling")["head_id"], self.start)
            self.assertIsNone(store.find_branch(db, self.doc, "proposal"))
            destination = store.find_branch(db, new_doc, "main")
            history = list(reversed(store.branch_history(db, destination)))
            self.assertEqual([row["version_label"] for row in history], ["v1.0.0", "v1.0.1", "v1.0.2", "v1.0.3"])
            self.assertEqual([row["status"] for row in history], ["approved", "approved", "rejected", "approved"])
            self.assertEqual([row["base_id"] for row in history],
                             [None, history[0]["id"], history[1]["id"], history[1]["id"]])
            self.assertEqual(history[2]["review_note"], "Keep review note")
            self.assertEqual(history[-1]["id"], new_head)
            for before, after in zip((self.start, self.first, self.rejected, self.head), history):
                copy = store.find_version(db, new_doc, after["id"], "content, created_at, sha256")
                original = store.find_version(db, self.doc, before, "content, created_at, sha256")
                self.assertEqual(tuple(copy), tuple(original))
            self.assertEqual(list(db.execute("PRAGMA foreign_key_check")), [])
        label = detail(self.doc, "proposal", self.head)
        self.assertEqual(label["branch"]["name"], "main")
        self.assertEqual(label["versions"][-1]["split_document_id"], new_doc)
        self.assertIn("branch=proposal", checked_document_path(self.doc, branch="proposal", version=self.head))
        browser = app.test_client()
        self.assertEqual(browser.get(f"/documents/{self.doc}?branch=proposal&version={self.head}").status_code, 200)
        self.assertEqual(browser.get(f"/documents/{self.doc}/compare?branch=proposal&base={self.first}&target={self.head}").status_code, 200)
        branch_link = browser.get(f"/documents/{self.doc}?branch=proposal")
        self.assertIn(f"새 문서 ID: {new_doc}", branch_link.get_data(as_text=True))

        new_version = save_proposal(new_doc, "main", new_head, pdf(140), "Independent change", desktop=True)
        with self.assertRaisesRegex(ValueError, "이미 분리"):
            self.split()
        with store.database(write=True) as db:
            self.assertEqual(store.find_document(db, new_doc)["current_id"], new_version)
            self.assertEqual(list(map(tuple, db.execute("SELECT * FROM versions WHERE document_id = ? ORDER BY id",
                                                       (self.doc,)))), original_versions)
            store.new_branch(db, self.doc, "proposal", self.head)
        with self.assertRaisesRegex(ValueError, "브랜치가 변경"):
            self.split()
        self.assertEqual(len(self.snapshot()[0]), 2)

    def test_branch_with_only_a_shared_snapshot_can_be_split(self):
        with store.database(write=True) as db:
            branch_id = store.find_branch(db, self.doc, "sibling")["id"]
        result = self.split(name="sibling", branch_id=branch_id, expected_head=self.start)
        self.assertEqual(result["version_count"], 1)
        with store.database(readonly=True) as db:
            new = store.find_version(db, result["document_id"], result["head_id"])
            self.assertEqual((new["base_id"], new["version_label"]), (None, "v1.0.0"))
            self.assertEqual(store.find_document(db, self.doc)["current_id"], self.main)
        detail_of_origin = detail(self.doc, "sibling", self.start)
        self.assertEqual(detail_of_origin["branch"]["name"], "main")
        self.assertIn("branch=sibling", checked_document_path(self.doc, branch="sibling", version=self.start))

    def test_split_tool_through_mcp_stdio(self):
        async def exercise():
            spec = StdioServerParameters(command=sys.executable, args=["-m", "mcp_server"],
                                         env={"DOCVERS_DB": str(store.DATABASE)})
            async with stdio_client(spec) as (reader, writer), ClientSession(reader, writer) as session:
                await session.initialize()
                tools = await session.list_tools()
                split = next(tool for tool in tools.tools if tool.name == "split_document_branch")
                self.assertTrue(split.annotations.destructiveHint)
                args = {"document_id": self.doc, "branch": "proposal", "branch_id": self.branch,
                        "expected_head_id": self.head, "title": "MCP independent"}
                refused = await session.call_tool(split.name, args)
                self.assertTrue(refused.isError)
                created = await session.call_tool(split.name, dict(args, confirmed=True))
                self.assertFalse(created.isError, created.content)
                new = created.structuredContent
                original = (await session.call_tool("list_branches",
                                                     {"document_id": self.doc})).structuredContent
                self.assertEqual([row["name"] for row in original["branches"]], ["main", "sibling"])
                destination = (await session.call_tool("list_versions",
                                                        {"document_id": new["document_id"]})).structuredContent
                self.assertEqual(destination["versions"][0]["id"], new["head_id"])
                self.assertEqual(destination["branch"]["name"], "main")

        asyncio.run(exercise())

    def test_confirm_main_stale_pending_dependency_and_cross_branch_restore(self):
        for args, error in ((dict(confirmed=False), "동의"),
                            (dict(name="main"), "기본 브랜치"),
                            (dict(expected_head=self.start), "변경되었습니다")):
            before = self.snapshot()
            with self.assertRaisesRegex(ValueError, error):
                self.split(**args)
            self.assertEqual(before, self.snapshot())

        pending = save_proposal(self.doc, "proposal", self.head, pdf(135), "Pending")
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "검토 대기"):
            self.split()
        self.assertEqual(before, self.snapshot())
        review_revision(self.doc, pending, "reject", self.head)
        with store.database(write=True) as db:
            store.new_branch(db, self.doc, "dependent", self.head)
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "다른 브랜치에서"):
            self.split()
        self.assertEqual(before, self.snapshot())
        with store.database(write=True) as db:
            db.execute("DELETE FROM branches WHERE document_id=? AND name='dependent'", (self.doc,))
            branch = store.find_branch(db, self.doc, "proposal")
            self.head = store.append_version(db, branch, pdf(110), "Cross-branch restore", restored_from=self.main)
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "병합 또는 복원"):
            self.split()
        self.assertEqual(before, self.snapshot())

    def test_failure_after_clone_rolls_back_and_concurrent_retry_creates_one_document(self):
        with store.database(write=True) as db:
            db.execute("""CREATE TRIGGER split_failure BEFORE UPDATE OF name ON branches
                          WHEN NEW.name LIKE '~split-%' BEGIN SELECT RAISE(ABORT, 'split test'); END""")
        before = self.snapshot()
        with self.assertRaisesRegex(sqlite3.IntegrityError, "split test"):
            self.split()
        self.assertEqual(before, self.snapshot())
        with store.database(write=True) as db:
            db.execute("DROP TRIGGER split_failure")
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(self.split) for _ in range(2)]
        outcomes = [future.exception() for future in futures]
        self.assertEqual(outcomes.count(None), 1)
        self.assertIn("이미 분리", str(next(error for error in outcomes if error)))
        self.assertEqual(len(self.snapshot()[0]), 2)

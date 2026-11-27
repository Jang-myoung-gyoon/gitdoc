import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from desktop.agent_setup import AgentSetup, NAME, paths_for, verify_server
from store import ROOT


class AgentSetupTests(unittest.IsolatedAsyncioTestCase):
    async def test_read_only_server_advertises_no_write_tools(self):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "readonly.sqlite3"
            sentence = {"server": [sys.executable, "-E", "-s", "-B", str(ROOT / "mcp_server.py"), "--read-only"],
                        "database": str(path)}
            await verify_server(sentence)
            settings = StdioServerParameters(command=sys.executable, args=sentence["server"][1:],
                                             env={"DOCVERS_DB": str(path)})
            async with stdio_client(settings) as (rx, tx), ClientSession(rx, tx) as client:
                await client.initialize()
                listed = await client.list_tools()
                names = {tool.name for tool in listed.tools}
                self.assertIn("search_documents", names)
                self.assertTrue({"register_document", "edit_revision", "export_document", "review_draft"}.isdisjoint(names))
            self.assertFalse(path.exists())

    async def test_confirmation_and_collision_leave_agent_entry_intact(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": temp}):
            installer = AgentSetup()
            with self.assertRaisesRegex(ValueError, "설치 계획"):
                installer.apply("claude", "invalid", False, "install")
            path, _ = paths_for("claude", Path(temp))
            content = {"mcpServers": {NAME: {"command": "existing-tool", "args": []}}}
            path.write_text(json.dumps(content))
            with patch("desktop.agent_setup.find_cli", return_value=sys.executable), patch("store.DATABASE", path):
                with self.assertRaisesRegex(ValueError, "이미 있습니다"):
                    installer.preview("claude")
            self.assertEqual(json.loads(path.read_text()), content)

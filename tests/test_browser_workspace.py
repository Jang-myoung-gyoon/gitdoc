import unittest
from unittest.mock import patch

from app import aligned_tables, app
from browser_viewer import STORE_ID, WORKSPACE_PREFIX, viewer_url, workspace_port
from desktop.bridge import open_browser_view


class BrowserWorkspaceTests(unittest.TestCase):
    def test_workspace_mount_keeps_links_scoped_and_rejects_a_foreign_store(self):
        with app.test_client() as client:
            response = client.get(f"{WORKSPACE_PREFIX}/static/app.css")
            self.assertEqual(response.status_code, 200)
            response.close()
            self.assertEqual(client.get(f"{WORKSPACE_PREFIX}/healthz").json["workspace_id"], STORE_ID)
            self.assertEqual(client.get("/workspace/foreign/documents/1").status_code, 404)
            with patch("app.search_documents", return_value=[]), patch("app.database"):
                library = client.get(f"{WORKSPACE_PREFIX}/").get_data(as_text=True)
                self.assertIn(f'href="{WORKSPACE_PREFIX}/static/app.css"', library)
                self.assertIn('<form method="get"', library)
                self.assertNotIn('method="post"', library)

    def test_web_viewer_exposes_no_document_write_routes(self):
        self.assertTrue(all(rule.methods <= {"GET", "HEAD", "OPTIONS"} for rule in app.url_map.iter_rules()))
        with app.test_client() as client:
            self.assertEqual(client.post(f"{WORKSPACE_PREFIX}/documents/1/edit").status_code, 404)

    def test_row_key_comparison_preserves_the_original_cell_row(self):
        left = {"extension": "csv", "content": b"id,value\na,first\nb,second\n"}
        right = {"extension": "csv", "content": b"id,value\nb,second\na,updated\nc,new\n"}
        with patch("app.version", side_effect=[left, right]):
            rows, source = aligned_tables(None, 1, 2, 3, "id", include_source=True)
        self.assertEqual(rows[1][1], ["a", "updated"])
        self.assertEqual(source, [[1, 2, 3, None], [1, 3, 2, 4]])

    def test_existing_viewer_is_reused_even_when_preferred_port_is_free(self):
        with patch("browser_viewer.viewer_ports", return_value=[8796, 50123]), \
                patch("browser_viewer.viewer_status", side_effect=[None, True]), \
                patch("browser_viewer.ensure_viewer") as start:
            self.assertEqual(workspace_port(), 50123)
            start.assert_not_called()

    def test_native_copy_uses_workspace_url_without_launching_browser(self):
        with patch("browser_viewer.workspace_port", return_value=8796), \
                patch("browser_viewer.webbrowser.open") as launch:
            result = open_browser_view(doc=1, ids=[2, 3], branch="main", page=5,
                                       change=2, open_tab=False)
            self.assertEqual(result, {"opened": False, "browser_url":
                             viewer_url("/documents/1/compare?base=2&target=3&branch=main&page=5&change=2")})
            self.assertIn(WORKSPACE_PREFIX, result["browser_url"])
            launch.assert_not_called()

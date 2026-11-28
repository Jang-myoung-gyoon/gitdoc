import hashlib
import io
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request

from desktop import update_windows as updates

INSTALLER = b"synthetic update"
DIGEST = hashlib.sha256(INSTALLER).hexdigest()
PINS = ("a"*64,)


def release(version="0.2.0", **override):
    name = f"GitDoc-{version}-windows-x64-setup.exe"
    asset = {"name": name, "state": "uploaded", "digest": f"sha256:{DIGEST}", "size": len(INSTALLER),
             "browser_download_url": f"{updates.WEB}/releases/download/v{version}/{name}"}
    return dict({"tag_name": f"v{version}", "body": "Release notes", "draft": False,
                 "prerelease": False, "assets": [asset]}, **override)


class WindowsUpdatesTests(unittest.TestCase):
    def test_stable_newer_correct_asset_only(self):
        self.assertIsNone(updates.release_metadata(release("0.1.3"), "0.1.3"))
        self.assertIsNone(updates.release_metadata(release("0.1.2"), "0.1.3"))
        self.assertIsNotNone(updates.release_metadata(release("0.1.10"), "0.1.3"))
        for fixture in (release(prerelease=True), release(draft=True), release("0.2.0-rc.1"),
                        release("00.2.0"), release(assets=[])):
            with self.assertRaises(updates.UpdateError):
                updates.release_metadata(fixture, "0.1.3")
        for field, value in (("digest", ""), ("size", True), ("size", updates.MAX_INSTALLER+1),
                             ("name", "GitDoc-0.2.0-macos-arm64.dmg"), ("state", "new"),
                             ("browser_download_url", "https://example.org/setup.exe")):
            fixture = release()
            fixture["assets"][0][field] = value
            with self.assertRaises(updates.UpdateError):
                updates.release_metadata(fixture, "0.1.3")

    def test_rejects_redirect_outside_github_https(self):
        handler = updates.ReleaseRedirect()
        for address in ("http://github.com/download", "https://github.com.evil.example/download",
                        "https://github.com@evil.example/download", "https://localhost/setup.exe"):
            with self.assertRaises(updates.UpdateError):
                handler.redirect_request(Request(updates.LATEST), None, 302, "", {}, address)

    def test_install_checks_approval_digest_download_integrity_and_trust_before_launch(self):
        update = updates.release_metadata(release(), "0.1.3")
        production = updates.run_update_helper
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base/"installation/app"
            root.mkdir(parents=True)
            (root/"desktop").mkdir()
            (root/"desktop/windows-update.ps1").write_text("# fixed test fixture", "utf-8")
            with patch("store.DATABASE", base/"user-data/documents.sqlite3"), \
                    patch.object(updates.tempfile, "mkdtemp", return_value=str(base/"download")), \
                    patch.object(updates, "latest", return_value=update), \
                    patch.object(updates, "powershell", return_value="powershell.exe"):
                for pins, requested, payload, trusted in (
                    ((), DIGEST, INSTALLER, True),
                    (PINS, "d"*64, INSTALLER, True),
                    (PINS, DIGEST, INSTALLER[:-1], True),
                    (PINS, DIGEST, len(INSTALLER)*b"x", True),
                    (PINS, DIGEST, INSTALLER, False),
                ):
                    stage = base/"download"
                    stage.mkdir(exist_ok=True)
                    with patch.object(updates, "open_release", return_value=io.BytesIO(payload)), \
                            patch.object(updates, "run_update_helper", wraps=production), \
                            patch.object(updates.subprocess, "run",
                                         return_value=subprocess.CompletedProcess([], 0 if trusted else 1)) as signature, \
                            patch.object(updates.subprocess, "Popen") as launch:
                        with self.assertRaises(updates.UpdateError):
                            updates.install(root, "0.1.3", pins, "0.2.0", requested, 123)
                        launch.assert_not_called()
                        if not trusted:
                            signature.assert_called_once()
                            self.assertFalse(stage.exists())
                stage.mkdir()
                with patch.object(updates, "open_release", return_value=io.BytesIO(INSTALLER)), \
                        patch.object(updates.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)), \
                        patch.object(updates.subprocess, "Popen") as launch:
                    self.assertEqual(updates.install(root, "0.1.3", PINS, "0.2.0", DIGEST, 123),
                                     {"status": "installing"})
                    launch.assert_called_once()
                    self.assertEqual(launch.call_args.kwargs["env"]["GITDOC_UPDATE_PARENT"], "123")
                    self.assertEqual((stage/"GitDoc-setup.exe").read_bytes(), INSTALLER)

"""Seed an isolated datastore with one installed version and reopen it after replacement."""

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

from pypdf import PdfWriter
from pypdfium2 import PdfDocument


def main():
    mode, engine, folder = sys.argv[1:]
    engine, folder = Path(engine).resolve(), Path(folder).resolve()
    assert engine.joinpath("PACKAGED").is_file()
    folder.mkdir(parents=True, exist_ok=True)
    db = folder / "documents.sqlite3"
    state = folder / "migration.json"
    assert mode in ("seed", "verify")
    if mode == "seed" and (db.exists() or state.exists()):
        raise RuntimeError("Migration smoke requires a new test datastore")
    with subprocess.Popen([sys.executable, "-E", "-s", "-B", "-m", "desktop.bridge"], cwd=engine,
                          env=dict(os.environ, DOCVERS_DB=str(db)), text=True, encoding="utf-8",
                          stdin=subprocess.PIPE, stdout=subprocess.PIPE) as bridge:
        def call(op, **params):
            bridge.stdin.write(json.dumps({"op": op, "params": params}) + "\n")
            bridge.stdin.flush()
            reply = json.loads(bridge.stdout.readline())
            if "error" in reply:
                raise RuntimeError(f"{op}: {reply['error']}")
            return reply["result"]

        if mode == "seed":
            assert (engine / "PACKAGED").read_text().strip() == "0.1.3"
            source = folder / "migration.pdf"
            writer = PdfWriter()
            writer.add_blank_page(240, 360)
            writer.write(source)
            document = call("register", file=str(source), title="GitDoc upgrade fixture")
            original = call("detail", doc=document)["versions"][0]["id"]
            call("desktop_review", enabled=False)
            updated = call("edit", doc=document, branch="main", base=original, reason="fixture rotation",
                           fields={"operation": "right", "page": 1})
            call("desktop_review", enabled=True)
            expected = [document, original, updated["id"], hashlib.sha256(source.read_bytes()).hexdigest()]
            state.write_text(json.dumps(expected))
        else:
            version = (engine / "PACKAGED").read_text().strip()
            assert tuple(map(int, version.split("."))) > (0, 1, 3)
            document, original, updated, sha = json.loads(state.read_text())
            assert call("workspace")["desktop_review"] is True
            assert any(row["id"] == document for row in call("library"))
            history = {row["id"]: row for row in call("detail", doc=document)["versions"]}
            assert set(history) == {original, updated}
            assert history[original]["sha256"] == sha
            preview = call("view", doc=document, ids=[original, updated])
            assert not any(preview["faults"])
            assert preview["counts"] == [1, 1]
            with PdfDocument(preview["paths"][1]) as pdf:
                page = pdf[0]
                try:
                    assert page.get_rotation() == 90
                    bitmap = page.render()
                    bitmap.close()
                finally:
                    page.close()
        bridge.stdin.close()
        assert bridge.wait(timeout=30) == 0
    print("Seeded v0.1.3 document history and settings" if mode == "seed" else
          f"Preserved both document versions and review settings, and rendered after installing v{version}")


if __name__ == "__main__":
    main()

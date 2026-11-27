"""Check an installed GitDoc package with its own Python runtime.

Run as: <bundled python> packaging/smoke.py <engine folder> <install folder> [--skip-native]
"""

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

WINDOWS = os.name == "nt"


def check(condition, message):
    if not condition:
        raise SystemExit(f"smoke: {message}")


def listing(folder):
    return {str(path.relative_to(folder)) for path in folder.rglob("*")}


# The package must ignore the user's own Python settings, as the app starts it.
ISOLATED = ["-E", "-s", "-B"]


def main():
    sys.stdout.reconfigure(errors="backslashreplace")
    app, install = (Path(value).resolve() for value in sys.argv[1:3])
    native = "--skip-native" not in sys.argv
    python = Path(sys.executable).resolve()
    check(install in python.parents, f"{python} is not the bundled Python")
    check((app / "PACKAGED").is_file() and (app / "desktop/bridge.py").is_file(), "engine folder is incomplete")
    binaries = install if WINDOWS else install / "Contents/MacOS"
    suffix = ".exe" if WINDOWS else ""
    for name in ("gitdoc-desktop", "gitdoc-pdf") + (() if WINDOWS else ("GitDoc", "gitdoc-ocr")):
        check((binaries / (name + suffix)).is_file(), f"{name} is missing")

    # Only system folders stay on PATH so no other Python can stand in for the bundled one.
    system = [str(Path(os.environ["SystemRoot"]) / "System32"), os.environ["SystemRoot"]] if WINDOWS else [
        "/usr/bin", "/bin", "/usr/sbin", "/sbin"]
    env = {key: value for key, value in os.environ.items()
           if not key.upper().startswith(("PYTHON", "DOCVERS_", "CARGO", "VIRTUAL_ENV"))}
    env["PATH"] = os.pathsep.join(system)
    # A throwaway profile keeps the check away from real documents; the broken Python settings must be ignored.
    home = Path(tempfile.mkdtemp(prefix="gitdoc-smoke-home-")).resolve()
    env.update(HOME=str(home), USERPROFILE=str(home), APPDATA=str(home / "AppData/Roaming"),
               PYTHONHOME=str(home / "missing"), PYTHONPATH=str(home / "missing"))
    data = home / "AppData/Roaming/GitDoc" if WINDOWS else home / "Library/Application Support/GitDoc"
    before = listing(install)
    database = data / "documents.sqlite3"

    if native:
        output = subprocess.run([binaries / ("gitdoc-desktop" + suffix), "--print-paths"], cwd=home, env=env,
                                check=True, stdout=subprocess.PIPE, timeout=60).stdout
        paths = json.loads(output)
        check(Path(paths["root"]).resolve() == app, f"the app would start the engine in {paths['root']}")
        check(Path(paths["python"]).resolve() == python, f"the app would start {paths['python']}")
        print("gitdoc-desktop resolves the bundled engine and Python")

    modules = sorted(path.stem for path in app.glob("*.py")) + sorted(
        f"desktop.{path.stem}" for path in (app / "desktop").glob("*.py"))
    script = ("import importlib, json, sys\n"
              "files = [importlib.import_module(name).__file__ for name in sys.argv[1:]]\n"
              "import store\n"
              "print(json.dumps([files, str(store.DATABASE), store.PACKAGED]))\n")
    output = subprocess.run([python, *ISOLATED, "-c", script, *modules], cwd=app, env=env, check=True,
                            stdout=subprocess.PIPE).stdout
    files, resolved, packaged = json.loads(output)
    check(all(app in Path(file).resolve().parents for file in files), "a module was imported from outside the package")
    check(packaged and Path(resolved) == database.resolve(), f"database resolved to {resolved}")
    print(f"imported {len(modules)} engine modules")

    from pypdf import PdfWriter
    writer = PdfWriter()
    writer.add_blank_page(300, 420)
    writer.add_blank_page(300, 420)
    pdf = io.BytesIO()
    writer.write(pdf)

    with tempfile.TemporaryDirectory(prefix="gitdoc-smoke-") as folder:
        sample = Path(folder) / "smoke.pdf"
        sample.write_bytes(pdf.getvalue())
        bridge = subprocess.Popen([python, *ISOLATED, "-m", "desktop.bridge"], cwd=app, env=env,
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE)

        def call(op, **params):
            bridge.stdin.write((json.dumps({"op": op, "params": params}) + "\n").encode("utf-8"))
            bridge.stdin.flush()
            line = bridge.stdout.readline()
            check(line, f"the engine stopped during {op}")
            reply = json.loads(line)
            check("result" in reply, f"{op} failed: {reply.get('error')}")
            return reply["result"]

        try:
            check(call("workspace")["workspace_name"] == "기본 문서함", "the default workspace is not in use")
            doc = call("register", file=str(sample), title="smoke")
            check(any(row["id"] == doc for row in call("library")), "the registered document is not listed")
            version = call("detail", doc=doc)["versions"][0]["id"]
            shown = call("view", doc=doc, ids=[version])
            check(shown["pages"] == 2 and not any(shown["faults"]), f"preview failed: {shown.get('faults')}")
            check(data in Path(shown["paths"][0]).resolve().parents, "preview cache left the user folder")
            pdfium = Path(shown["pdfium_directory"])
            check(install in pdfium.resolve().parents and any(
                "pdfium" in path.name and path.suffix in (".dylib", ".dll") for path in pdfium.iterdir()),
                "bundled PDFium is missing")
        finally:
            bridge.stdin.close()
            bridge.wait(timeout=30)
        print("bridge requests passed")

        listed = subprocess.run([python, *ISOLATED, str(app / "docvers.py"), "list"], cwd=folder, env=env, check=True,
                                stdout=subprocess.PIPE).stdout.decode("utf-8", errors="replace")
        check("smoke" in listed, "the CLI reads a different database")
        if native:
            rotate = ("import sys\nfrom pdf_edit import edit_pdf_bytes\n"
                      "original = open(sys.argv[1], 'rb').read()\n"
                      "edited, _ = edit_pdf_bytes(original, 1, 'right', None)\n"
                      "assert edited.startswith(b'%PDF') and edited != original\n")
            subprocess.run([python, *ISOLATED, "-c", rotate, str(sample)], cwd=app, env=env, check=True)
            print("gitdoc-pdf edited a page")

    check(database.is_file(), f"{database} was not created")
    changed = sorted(listing(install) ^ before)
    check(not changed, f"the install folder changed: {changed[:10]}")
    print(f"database: {database}")
    shutil.rmtree(home, ignore_errors=True)


if __name__ == "__main__":
    main()

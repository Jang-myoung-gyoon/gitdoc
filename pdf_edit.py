"""Run the Rust PDF editor for desktop, MCP, and CLI draft edits."""

import json
import os
import shutil
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT / "desktop" / "native"
INVALID = "PDF 구조를 읽을 수 없습니다. 원본을 확인하세요."
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def editor_executable():
    override = os.environ.get("DOCVERS_PDF_EDITOR")
    if override:
        editor = Path(override).expanduser().resolve()
        if not editor.is_file():
            raise ValueError("DOCVERS_PDF_EDITOR에 지정된 실행 파일을 찾을 수 없습니다.")
        return editor

    suffix = ".exe" if os.name == "nt" else ""
    target = ROOT / os.environ.get("CARGO_TARGET_DIR", str(PROJECT / "target"))
    editor = target / "debug" / ("gitdoc-pdf" + suffix)
    if editor.is_file():
        return editor
    bundled = ROOT / ".build" / "GitDoc.app" / "Contents" / "MacOS" / editor.name
    if bundled.is_file():
        return bundled

    cargo = shutil.which("cargo")
    if cargo is None:
        candidate = Path.home() / ".cargo" / "bin" / ("cargo" + suffix)
        if not candidate.is_file():
            raise ValueError("PDF 편집 도구를 빌드하려면 Rust의 Cargo가 필요합니다.")
        cargo = str(candidate)
    try:
        subprocess.run([cargo, "build", "--locked", "--manifest-path", str(PROJECT / "Cargo.toml"),
                        "--bin", "gitdoc-pdf"], cwd=ROOT, stdin=subprocess.DEVNULL,
                       capture_output=True, check=True, creationflags=NO_WINDOW, timeout=300)
    except (OSError, subprocess.SubprocessError) as error:
        raise ValueError("Rust PDF 편집 도구를 빌드할 수 없습니다.") from error
    return editor


def edit_pdf_bytes(data, page, operation, note, source=None):
    executable = editor_executable()
    with TemporaryDirectory(prefix="gitdoc-pdf-") as directory:
        work = Path(directory)
        original = work / "original.pdf"
        output = work / "edited.pdf"
        original.write_bytes(data)
        arguments = [executable, original, output]
        if operation == "insert" and source is not None:
            incoming = work / "incoming.pdf"
            incoming.write_bytes(source)
            arguments.append(incoming)
        try:
            result = subprocess.run(arguments, input=json.dumps(dict(page=page, operation=operation, note=note)),
                                    encoding="utf-8", errors="replace", capture_output=True,
                                    timeout=60, creationflags=NO_WINDOW)
            if result.returncode:
                raise ValueError(result.stderr.strip() or INVALID)
            base, inserted_at, inserted, deleted_at = json.loads(result.stdout)
            return output.read_bytes(), (base, inserted_at, inserted, deleted_at)
        except subprocess.TimeoutExpired as error:
            raise ValueError("PDF 편집 시간이 초과되었습니다.") from error
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError(INVALID) from error

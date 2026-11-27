import os
import shutil
import subprocess
import sys
import tempfile
from functools import lru_cache
from pathlib import Path
from threading import BoundedSemaphore

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from store import DATABASE, ROOT

CACHE = DATABASE.parent / "previews-v1"
BUNDLED_BIN = Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/bin/override"
HANCOM_FONTS = Path("/Applications/Hancom Office HWP Viewer.app/Contents/Resources/Hnc/Shared/TTF/Install")
OFFICE_FILTERS = {"docx": "writer_pdf_Export", "ppt": "impress_pdf_Export",
                  "pptx": "impress_pdf_Export", "xls": "calc_pdf_Export", "xlsx": "calc_pdf_Export"}
WORKERS = BoundedSemaphore(2)
MAX_PREVIEW = 100 * 1024 * 1024


class PreviewError(Exception):
    pass


def executable(name, env_name, alternative):
    path = os.environ.get(env_name) or shutil.which(name)
    if not path:
        bundled = alternative.with_suffix(".exe") if os.name == "nt" else alternative
        if bundled.is_file():
            path = str(bundled)
    if not path:
        options = [Path("/opt/homebrew/bin") / name, Path("/usr/local/bin") / name]
        if name == "soffice":
            options.append(Path("/Applications/LibreOffice.app/Contents/MacOS/soffice"))
            if os.name == "nt":
                for key in ("ProgramFiles", "ProgramFiles(x86)"):
                    if os.environ.get(key):
                        program = Path(os.environ[key]) / "LibreOffice/program"
                        options.extend([program / "soffice.com", program / "soffice.exe"])
        path = next((str(option) for option in options if os.access(option, os.X_OK)), None)
    if not path:
        raise PreviewError(f"미리보기 도구 {name}을 찾을 수 없습니다.")
    return path


def run_converter(args, cwd, env=None):
    if sys.platform == "darwin" and Path("/usr/bin/sandbox-exec").is_file():
        args = ["/usr/bin/sandbox-exec", "-p", "(version 1) (allow default) (deny network*)", *args]
    try:
        return subprocess.run(args, cwd=cwd, env=env, timeout=45, check=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout
    except subprocess.TimeoutExpired as error:
        raise PreviewError("미리보기 변환 시간이 초과되었습니다.") from error
    except (OSError, subprocess.CalledProcessError) as error:
        raise PreviewError("문서 미리보기를 생성할 수 없습니다. 원본 파일을 확인하세요.") from error


def office_profile(folder):
    profile = folder / "profile"
    user = profile / "user"
    user.mkdir(parents=True)
    (user / "registrymodifications.xcu").write_text(
        '<oor:items xmlns:oor="http://openoffice.org/2001/registry">'
        '<item oor:path="/org.openoffice.Office.Common/Security/Scripting">'
        '<prop oor:name="MacroSecurityLevel" oor:op="fuse"><value>3</value></prop>'
        '</item></oor:items>', encoding="utf-8"
    )
    return profile


def rhwp_command():
    return executable("rhwp", "DOCVERS_RHWP", ROOT / "data/tools/rhwp/rhwp")


def convert(source, destination, extension):
    folder = source.parent
    env = dict(os.environ, RHWP_WEBFONT_OFFLINE="1")
    if extension in ("hwp", "hwpx"):
        command = rhwp_command()
        args = [command, "export-pdf", str(source), "-o", str(destination), "--profile", "screen"]
        if HANCOM_FONTS.is_dir():
            args.extend(["--font-path", str(HANCOM_FONTS)])
    else:
        command = executable("soffice", "DOCVERS_SOFFICE", BUNDLED_BIN / "soffice")
        profile = office_profile(folder)
        options = OFFICE_FILTERS[extension]
        if extension in ("xls", "xlsx"):
            options += ':{"SinglePageSheets":{"type":"boolean","value":"true"}}'
        args = [command, f"-env:UserInstallation={profile.as_uri()}", "--headless",
                "--convert-to", f"pdf:{options}", "--outdir", str(folder), str(source)]
        if sys.platform == "darwin" and not env.get("FONTCONFIG_FILE"):
            env["FONTCONFIG_FILE"] = str(ROOT / "preview-fonts.conf")
    run_converter(args, folder, env)


def read_page_dimensions(path):
    try:
        with path.open("rb") as stream:
            pdf = PdfReader(stream)
            if pdf.is_encrypted:
                raise PreviewError("암호가 걸린 PDF입니다. 원본을 열어 확인하세요.")
            sizes = []
            for page in pdf.pages:
                box = page.cropbox
                width, height = float(box.width), float(box.height)
                sizes.append((height, width) if page.rotation % 180 else (width, height))
    except (OSError, PdfReadError, ValueError, RecursionError) as error:
        raise PreviewError("PDF 페이지를 읽을 수 없습니다.") from error
    if not sizes:
        raise PreviewError("표시할 페이지가 없습니다.")
    return tuple(sizes)


page_dimensions = lru_cache(maxsize=128)(read_page_dimensions)


@lru_cache(maxsize=128)
def page_sizes(path):
    return tuple(width for width, _ in page_dimensions(path))


def preview_path(extension, digest, load_content):
    path = CACHE / f"{extension}-{digest}.pdf"
    if path.is_file():
        return path
    with WORKERS:
        if path.is_file():
            return path
        CACHE.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=CACHE) as directory:
            folder = Path(directory)
            output = folder / "document.pdf"
            data = load_content()
            if extension == "pdf":
                output.write_bytes(data)
            else:
                source = folder / f"document.{extension}"
                source.write_bytes(data)
                convert(source, output, extension)
            if not output.is_file() or not 0 < output.stat().st_size <= MAX_PREVIEW:
                raise PreviewError("미리보기를 생성할 수 없거나 변환 결과가 너무 큽니다.")
            read_page_dimensions(output)
            output.replace(path)
    return path


@lru_cache(maxsize=24)
def preview_png(path, number):
    bin_path = executable("pdftoppm", "DOCVERS_PDFTOPPM", BUNDLED_BIN / "pdftoppm")
    with WORKERS:
        data = run_converter([bin_path, "-f", str(number), "-l", str(number), "-cropbox", "-png",
                              "-scale-to", "1650", "-singlefile", str(path)], path.parent)
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise PreviewError("페이지 이미지를 읽을 수 없습니다.")
    return data

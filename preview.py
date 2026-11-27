import os
import re
import shutil
import subprocess
import sys
import tempfile
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from threading import BoundedSemaphore, Lock
from xml.etree import ElementTree as ET

from pypdf import PdfReader
from pypdf.errors import PdfReadError
from pypdfium2 import PdfDocument, PdfiumError, raw

from delimited import SEPARATORS, normalized_table
from font_policy import font_directories, font_environment, generic_fallback, office_replacements, policy_key
from formats import DELIMITED_EXTENSIONS, WORKBOOK_EXTENSIONS
from store import DATABASE, ROOT

CACHE = DATABASE.parent / "previews-v2"
BUNDLED_BIN = Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/bin/override"
OFFICE_FILTERS = {
    **dict.fromkeys(("doc", "docx", "docm", "odt"), "writer_pdf_Export"),
    **dict.fromkeys(("ppt", "pptx", "pptm", "odp"), "impress_pdf_Export"),
    **dict.fromkeys((*WORKBOOK_EXTENSIONS, "ods"), "calc_pdf_Export"),
}
WORKERS = BoundedSemaphore(2)
PDFIUM_LOCK = Lock()
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


def office_profile(folder, replacements=None):
    profile = folder / "profile"
    user = profile / "user"
    user.mkdir(parents=True)
    items = ET.Element("oor:items", {"xmlns:oor": "http://openoffice.org/2001/registry"})

    def prop(parent, name, value):
        entry = ET.SubElement(parent, "prop", {"oor:name": name, "oor:op": "fuse"})
        ET.SubElement(entry, "value").text = value

    security = ET.SubElement(items, "item", {"oor:path": "/org.openoffice.Office.Common/Security/Scripting"})
    prop(security, "MacroSecurityLevel", "3")
    if replacements:
        section = "/org.openoffice.Office.Common/Font/Substitution"
        prop(ET.SubElement(items, "item", {"oor:path": section}), "Replacement", "true")
        table = ET.SubElement(items, "item", {"oor:path": section + "/FontPairs"})
        for index, (name, replacement) in enumerate(replacements.items()):
            pair = ET.SubElement(table, "node", {"oor:name": f"GitDoc{index}", "oor:op": "replace"})
            prop(pair, "ReplaceFont", name)
            prop(pair, "SubstituteFont", replacement)
            prop(pair, "Always", "true")
            prop(pair, "OnScreenOnly", "false")
    ET.ElementTree(items).write(user / "registrymodifications.xcu", encoding="utf-8")
    return profile


def rhwp_command():
    return executable("rhwp", "DOCVERS_RHWP", ROOT / "data/tools/rhwp/rhwp")


def convert(source, destination, extension):
    folder = source.parent
    env = font_environment(CACHE)
    if extension in ("hwp", "hwpx"):
        command = rhwp_command()
        args = [command, "export-pdf", str(source), "-o", str(destination), "--profile", "screen"]
        for directory in font_directories():
            args.extend(["--font-path", str(directory)])
        for kind in ("serif", "sans", "mono"):
            if family := generic_fallback(kind, east_asian=True):
                args.extend([f"--fallback-{kind}", family])
    else:
        command = executable("soffice", "DOCVERS_SOFFICE", BUNDLED_BIN / "soffice")
        profile = office_profile(folder, office_replacements(source))
        options = OFFICE_FILTERS[extension]
        if extension == "ods" or (extension in WORKBOOK_EXTENSIONS and extension not in DELIMITED_EXTENSIONS):
            options += ':{"SinglePageSheets":{"type":"boolean","value":"true"}}'
        args = [command, f"-env:UserInstallation={profile.as_uri()}", "--headless"]
        if extension in DELIMITED_EXTENSIONS:
            separator = ord(SEPARATORS[extension])
            args.append(f"--infilter=Text - txt - csv (StarCalc):{separator},34,76,1,,0,true,false,false,false,false,0,false")
        args.extend(["--convert-to", f"pdf:{options}", "--outdir", str(folder), str(source)])
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
    path = CACHE / f"{extension}-{digest}-{policy_key()}.pdf"
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
                if extension in DELIMITED_EXTENSIONS:
                    data = normalized_table(extension, data)
                source.write_bytes(data)
                convert(source, output, extension)
            if not output.is_file() or not 0 < output.stat().st_size <= MAX_PREVIEW:
                raise PreviewError("미리보기를 생성할 수 없거나 변환 결과가 너무 큽니다.")
            read_page_dimensions(output)
            output.replace(path)
    return path


@lru_cache(maxsize=64)
def unembedded_fonts(path):
    fonts, visited = {}, set()
    try:
        with path.open("rb") as stream:
            reader = PdfReader(stream)
            for page in reader.pages:
                resources = [page.get("/Resources")]
                while resources:
                    ref = resources.pop()
                    if ref is None:
                        continue
                    item = ref.get_object()
                    if id(item) in visited:
                        continue
                    visited.add(id(item))
                    for font in item.get("/Font", {}).get_object().values() if item.get("/Font") else ():
                        face = font.get_object()
                        if face.get("/Subtype") == "/Type3":
                            continue
                        descendant = face.get("/DescendantFonts")
                        detail = descendant[0].get_object() if descendant else face
                        descriptor = detail.get("/FontDescriptor")
                        if descriptor and any(name in descriptor.get_object()
                                              for name in ("/FontFile", "/FontFile2", "/FontFile3")):
                            continue
                        name = str(face.get("/BaseFont", "")).lstrip("/")
                        name = re.sub(r"^[A-Z]{6}\+", "", name)
                        system = detail.get("/CIDSystemInfo")
                        korean = system and str(system.get_object().get("/Ordering", "")) == "Korea1"
                        if name:
                            fonts[name] = fonts.get(name, False) or bool(korean)
                    objects = item.get("/XObject")
                    if objects:
                        resources.extend(obj.get_object().get("/Resources") for obj in objects.get_object().values())
    except (OSError, PdfReadError, AttributeError, KeyError, ValueError, RecursionError):
        pass
    return fonts


def preview_png(path, number, *, prefer_pdfium=False):
    return cached_png(path, number, policy_key(), prefer_pdfium)


def poppler_available():
    try:
        executable("pdftoppm", "DOCVERS_PDFTOPPM", BUNDLED_BIN / "pdftoppm")
    except PreviewError:
        return False
    return True


@lru_cache(maxsize=64)
def requires_poppler(path):
    if unembedded_fonts(path):
        return True
    try:
        with PDFIUM_LOCK, PdfDocument(path) as document:
            return document.get_formtype() != raw.FORMTYPE_NONE
    except (PdfiumError, OSError, ValueError):
        return True


def pdfium_png(path, number):
    with PDFIUM_LOCK, PdfDocument(path) as document:
        page = document[number - 1]
        bitmap = page.render(scale=1650 / max(page.get_size()), rev_byteorder=True,
                             may_draw_forms=False, draw_annots=True)
        # Copy the pixels so encoding can run after PDFium releases the document.
        image = bitmap.to_pil().convert("RGB")
    buffer = BytesIO()
    image.save(buffer, format="PNG", compress_level=1)
    return buffer.getvalue()


@lru_cache(maxsize=24)
def cached_png(path, number, _font_policy, prefer_pdfium):
    use_pdfium = prefer_pdfium and not requires_poppler(path)
    with WORKERS:
        if use_pdfium:
            try:
                if data := pdfium_png(path, number):
                    return data
            except (PdfiumError, ImportError, OSError, ValueError, OverflowError, ZeroDivisionError):
                pass
        bin_path = executable("pdftoppm", "DOCVERS_PDFTOPPM", BUNDLED_BIN / "pdftoppm")
        data = run_converter([bin_path, "-f", str(number), "-l", str(number), "-cropbox", "-png",
                              "-scale-to", "1650", "-singlefile", str(path)], path.parent,
                             font_environment(CACHE, unembedded_fonts(path)))
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise PreviewError("페이지 이미지를 읽을 수 없습니다.")
    return data

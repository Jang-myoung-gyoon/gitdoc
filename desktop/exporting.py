"""Export a stored document revision without modifying the repository."""

import os
import tempfile
from pathlib import Path

from extract import matches_format
from preview import (
    BUNDLED_BIN, MAX_PREVIEW, WORKERS, PreviewError, executable, office_profile,
    rhwp_command, run_converter,
)

OFFICE_FORMATS = {
    "doc": {"docx": "Office Open XML Text", "odt": "writer8"},
    "docx": {"odt": "writer8", "doc": "MS Word 97"},
    "odt": {"docx": "Office Open XML Text"},
    "ods": {"xlsx": "Calc MS Excel 2007 XML"},
    "odp": {"pptx": "Impress MS PowerPoint 2007 XML"},
    "ppt": {"pptx": "Impress MS PowerPoint 2007 XML", "odp": "impress8"},
    "pptx": {"ppt": "MS PowerPoint 97", "odp": "impress8"},
    "xls": {"xlsx": "Calc MS Excel 2007 XML", "ods": "calc8"},
    "xlsx": {"xls": "MS Excel 97", "ods": "calc8"},
}
HANCOM_FORMATS = {"hwp": {"hwpx": "export-hwpx"}, "hwpx": {"hwp": "convert"}}


def export_formats(extension):
    if extension == "pdf":
        return [extension]
    compatible = HANCOM_FORMATS.get(extension, OFFICE_FORMATS.get(extension, {}))
    return [extension, "pdf", *compatible]


def convert_format(extension, target, data):
    if target == extension or target == "pdf" or target not in export_formats(extension):
        raise ValueError("선택한 형식으로 변환할 수 없습니다.")
    with WORKERS, tempfile.TemporaryDirectory() as directory:
        folder = Path(directory)
        source = folder / f"document.{extension}"
        destination = folder / f"document.{target}"
        source.write_bytes(data)
        if extension in HANCOM_FORMATS:
            args = [rhwp_command(), HANCOM_FORMATS[extension][target], str(source),
                    str(destination), "--verify-pages"]
            run_converter(args, folder, env=dict(os.environ, RHWP_WEBFONT_OFFLINE="1"))
        else:
            command = executable("soffice", "DOCVERS_SOFFICE", BUNDLED_BIN / "soffice")
            profile = office_profile(folder)
            options = OFFICE_FORMATS[extension][target]
            run_converter([command, f"-env:UserInstallation={profile.as_uri()}", "--headless",
                           "--convert-to", f"{target}:{options}", "--outdir", str(folder), str(source)], folder)
        if not destination.is_file() or not 0 < destination.stat().st_size <= MAX_PREVIEW:
            raise PreviewError("변환한 문서를 만들 수 없거나 파일 크기 한도를 초과했습니다.")
        output = destination.read_bytes()
        if matches_format(target, output):
            return output
        raise PreviewError("변환한 문서의 형식이 올바르지 않습니다.")


def write_export(path, data, *, overwrite=True):
    path = Path(path)
    temporary = tempfile.NamedTemporaryFile(dir=path.parent, delete=False)
    staged = Path(temporary.name)
    try:
        with temporary:
            temporary.write(data)
        if overwrite:
            staged.replace(path)
        else:
            os.link(staged, path)
    finally:
        staged.unlink(missing_ok=True)

"""Validate and extract supported document uploads."""

import hashlib
import os
from pathlib import Path
from zipfile import BadZipFile

from extract import extract_for_version, matches_format
from formats import EXTENSIONS

MAX_FILE_MIB = int(os.environ.get("DOCVERS_MAX_FILE_MIB", "30"))
if not 1 <= MAX_FILE_MIB <= 100:
    raise ValueError("DOCVERS_MAX_FILE_MIB는 1~100 사이의 정수여야 합니다.")
MAX_FILE = MAX_FILE_MIB * 1024 * 1024


def prepare_upload(name, data):
    filename = name.replace("\\", "/").split("/")[-1]
    extension = Path(filename).suffix.lower().lstrip(".")
    if len(filename) > 255 or any(ord(char) < 32 for char in filename):
        raise ValueError("파일 이름을 확인하세요.")
    if extension not in EXTENSIONS:
        raise ValueError("지원 파일 형식을 확인하세요.")
    if not data or len(data) > MAX_FILE:
        raise ValueError(f"파일은 1바이트 이상, {MAX_FILE_MIB}MiB 이하여야 합니다.")
    try:
        valid = matches_format(extension, data)
    except (BadZipFile, OSError, ValueError):
        valid = False
    if not valid:
        raise ValueError("파일 내용이 확장자와 일치하지 않거나 파일이 손상되었습니다.")
    extracted, note = extract_for_version(extension, data)
    return (filename, extension, len(data), hashlib.sha256(data).hexdigest(), note, extracted, data)


def load_local_file(filename):
    path = Path(filename).expanduser()
    if not path.is_absolute() or not path.is_file():
        raise ValueError("실제 로컬 문서 파일의 절대 경로를 지정하세요.")
    try:
        with path.open("rb") as stream:
            return prepare_upload(path.name, stream.read(MAX_FILE + 1))
    except OSError as error:
        raise ValueError("로컬 문서 파일을 읽을 수 없습니다.") from error

"""Check stable GitDoc releases and stage an Authenticode-verified in-place update."""

import base64
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

REPOSITORY = "Jang-myoung-gyoon/gitdoc"
WEB = f"https://github.com/{REPOSITORY}"
LATEST = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
VERSION = re.compile(r"(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
MAX_INSTALLER = 300 * 1024 * 1024
MAX_RESPONSE = 3 * 1024 * 1024
CREATE_NO_WINDOW = 0x08000000


class UpdateError(ValueError):
    pass


class ReleaseRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        url = urlsplit(newurl)
        if (url.scheme != "https" or url.netloc not in {
                "github.com", "api.github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com"
        }):
            raise UpdateError("업데이트 다운로드 주소를 검증할 수 없습니다.")
        return super().redirect_request(request, fp, code, msg, headers, newurl)


def open_release(url):
    opener = build_opener(ReleaseRedirect())
    request = Request(url, headers={"User-Agent": "GitDoc desktop updater", "Accept": "application/vnd.github+json"
                                    if url == LATEST else "application/octet-stream"})
    return opener.open(request, timeout=20)


def version_parts(value):
    if not isinstance(value, str) or not (parts := VERSION.fullmatch(value)):
        raise UpdateError("업데이트 버전을 검증할 수 없습니다.")
    return tuple(map(int, parts.groups()))


def release_metadata(release, installed):
    if not isinstance(release, dict) or release.get("draft") is not False or release.get("prerelease") is not False:
        raise UpdateError("GitDoc 정식 릴리스를 확인할 수 없습니다.")
    tag = release.get("tag_name")
    if not isinstance(tag, str) or not tag.startswith("v"):
        raise UpdateError("업데이트 버전을 검증할 수 없습니다.")
    version = tag[1:]
    if version_parts(version) <= version_parts(installed):
        return None

    name = f"GitDoc-{version}-windows-x64-setup.exe"
    assets = release.get("assets")
    candidates = [asset for asset in assets if isinstance(asset, dict) and asset.get("name") == name] if isinstance(assets, list) else []
    url = f"{WEB}/releases/download/{tag}/{name}"
    if len(candidates) != 1 or candidates[0].get("browser_download_url") != url:
        raise UpdateError("Windows x64 설치 파일을 확인할 수 없습니다.")
    asset = candidates[0]
    digest, size = asset.get("digest"), asset.get("size")
    if (asset.get("state") != "uploaded" or not isinstance(digest, str) or not digest.startswith("sha256:")
            or not SHA256.fullmatch(digest[7:]) or type(size) is not int or not 0 < size <= MAX_INSTALLER):
        raise UpdateError("업데이트 파일의 크기와 SHA-256을 검증할 수 없습니다.")
    notes = release.get("body")
    return {"version": version, "digest": digest[7:], "size": size, "url": url,
            "notes": notes[:10000] if isinstance(notes, str) else ""}


def latest(installed):
    with open_release(LATEST) as response:
        payload = response.read(MAX_RESPONSE + 1)
    if len(payload) > MAX_RESPONSE:
        raise UpdateError("업데이트 응답이 너무 큽니다.")
    return release_metadata(json.loads(payload), installed)


def download(update, target):
    digest = hashlib.sha256()
    received = 0
    deadline = time.monotonic() + 240
    with open_release(update["url"]) as response, target.open("xb") as output:
        while block := response.read(256 * 1024):
            received += len(block)
            if received > update["size"] or time.monotonic() > deadline:
                raise UpdateError("설치 파일의 크기나 다운로드 시간이 예상과 다릅니다.")
            digest.update(block)
            output.write(block)
    if received != update["size"] or digest.hexdigest() != update["digest"]:
        raise UpdateError("설치 파일 검증에 실패했습니다. 다시 다운로드하세요.")


def publisher_pins(root):
    file = root/"WINDOWS_UPDATE_SIGNERS"
    if not file.is_file():
        return ()
    pins = tuple(file.read_text("ascii").split())
    if not 1 <= len(pins) <= 2 or any(not SHA256.fullmatch(pin) for pin in pins):
        raise UpdateError("빌드에 포함된 Windows 서명자 설정을 확인하세요.")
    return pins


def powershell():
    return Path(os.environ["SystemRoot"])/"System32/WindowsPowerShell/v1.0/powershell.exe"


def run_update_helper(root, installer, update, pins, parent_id, stage):
    # The script is shipped with the installed app. Paths and digests are data in the child environment.
    source = (root/"desktop/windows-update.ps1").read_text("utf-8")
    encoded = base64.b64encode(source.encode("utf-16-le")).decode("ascii")
    command = [str(powershell()), "-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded]
    environment = dict(os.environ, GITDOC_UPDATE_SETUP=str(installer), GITDOC_UPDATE_PATH=str(root.parent),
                       GITDOC_UPDATE_STAGE=str(stage), GITDOC_UPDATE_SHA256=update["digest"],
                       GITDOC_UPDATE_SIGNERS=",".join(pins), GITDOC_UPDATE_PARENT=str(parent_id))
    verification = subprocess.run(command, env=dict(environment, GITDOC_UPDATE_MODE="verify"),
                                  timeout=45, capture_output=True, creationflags=CREATE_NO_WINDOW)
    if verification.returncode != 0:
        raise UpdateError("Windows가 신뢰하는 GitDoc 발행자 서명을 확인할 수 없어 설치를 중단했습니다.")
    subprocess.Popen(command, env=dict(environment, GITDOC_UPDATE_MODE="apply"), cwd=stage,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     creationflags=CREATE_NO_WINDOW)


def install(root, installed, pins, version, digest, parent_id):
    from store import DATABASE

    if DATABASE.resolve().is_relative_to(root.parent.resolve()):
        raise UpdateError("문서 저장소가 설치 폴더 안에 있습니다. 저장소 경로를 확인한 후 업데이트하세요.")
    if not pins:
        raise UpdateError("이 설치본에는 Windows 업데이트 서명자가 설정되어 있지 않습니다.")
    update = latest(installed)
    if update is None or (update["version"], update["digest"]) != (version, digest):
        raise UpdateError("릴리스 파일이 변경되었습니다. 업데이트를 다시 확인하세요.")
    stage = Path(tempfile.mkdtemp(prefix="gitdoc-update-"))
    try:
        installer = stage/"GitDoc-setup.exe"
        download(update, installer)
        run_update_helper(root, installer, update, pins, parent_id, stage)
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    return {"status": "installing"}


def run(arguments):
    if os.name != "nt" or platform.machine().casefold() not in {"amd64", "x86_64"}:
        raise UpdateError("이 운영체제와 CPU에는 지원되는 Windows 업데이트가 없습니다.")
    root = Path(__file__).resolve().parents[1]
    installed = (root/"PACKAGED").read_text("ascii").strip()
    pins = publisher_pins(root)
    if arguments == ["check"]:
        update = latest(installed)
        if update is None:
            return {"status": "current"}
        return {"status": "available", "can_install": bool(pins), **update}
    if len(arguments) == 4 and arguments[0] == "install" and arguments[3].isascii() and arguments[3].isdigit():
        return install(root, installed, pins, *arguments[1:3], int(arguments[3]))
    raise UpdateError("지원되는 업데이트 명령을 확인하세요.")


if __name__ == "__main__":
    # The native app consumes UTF-8 JSON independently of the Windows console code page.
    try:
        result = run(sys.argv[1:])
    except UpdateError as error:
        result = {"status": "error", "error": str(error)}
    except (OSError, ValueError, subprocess.SubprocessError):
        result = {"status": "error", "error": "업데이트 요청을 완료하지 못했습니다. 잠시 후 다시 시도하세요."}
    sys.stdout.buffer.write(json.dumps(result, ensure_ascii=False).encode("utf-8") + b"\n")

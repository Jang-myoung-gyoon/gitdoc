"""Start or reuse the loopback browser interface for the current document store."""

import hashlib
import http.client
import json
import os
import subprocess
import sys
import time
import webbrowser

from store import DATABASE, DEFAULT_DATABASE, ROOT

HOST = "127.0.0.1"
STORE_ID = hashlib.sha256(os.fsencode(DATABASE)).hexdigest()
WORKSPACE_PREFIX = f"/workspace/{STORE_ID}"


def workspace_info():
    name = "기본 문서함" if DATABASE == DEFAULT_DATABASE else DATABASE.stem
    return {"workspace_id": STORE_ID, "workspace_name": name}


def browser_port():
    port = int(os.environ.get("DOCVERS_PORT", "8796"))
    if not 1 <= port <= 65535:
        raise ValueError("DOCVERS_PORT는 1부터 65535까지의 포트 번호여야 합니다.")
    return port


def viewer_status(port):
    connection = http.client.HTTPConnection(HOST, port, timeout=0.3)
    try:
        connection.request("GET", "/healthz")
        response = connection.getresponse()
        payload = json.loads(response.read(4096)) if response.status == 200 else None
        if (isinstance(payload, dict) and payload.get("workspace_id") == STORE_ID
                and payload.get("web_approval") is True):
            return True
        return False
    except (ConnectionRefusedError, ConnectionResetError, http.client.RemoteDisconnected):
        return None
    except (OSError, ValueError, http.client.HTTPException):
        return False
    finally:
        connection.close()


def ensure_viewer(port):
    status = viewer_status(port)
    if status is None:
        DATABASE.parent.mkdir(parents=True, exist_ok=True)
        log_path = DATABASE.parent / "browser-viewer.log"
        options = {"creationflags": subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS} if os.name == "nt" else {
            "start_new_session": True}
        with log_path.open("ab") as log:
            process = subprocess.Popen([sys.executable, str(ROOT / "app.py")], cwd=ROOT,
                                       env=dict(os.environ, DOCVERS_DB=str(DATABASE), DOCVERS_PORT=str(port)),
                                       stdin=subprocess.DEVNULL, stdout=log, stderr=log, **options)
        deadline = time.monotonic() + 8
        while status is None and time.monotonic() < deadline:
            time.sleep(0.15)
            status = viewer_status(port)
            if status is None and process.poll() is not None:
                break
        if status is None:
            if process.poll() is None:
                process.terminate()
            raise ValueError(f"브라우저 뷰어를 시작할 수 없습니다. 로그를 확인하세요. ({log_path})")
    return status


def viewer_ports():
    yield browser_port()
    if "DOCVERS_PORT" not in os.environ:
        # Find the same alternate viewer after reopening the workspace.
        start = int(STORE_ID[:8], 16)
        for offset in range(8):
            yield 49152 + (start + offset) % 16384


def workspace_port():
    available = []
    for port in viewer_ports():
        status = viewer_status(port)
        if status is True:
            return port
        if status is None:
            available.append(port)
    for port in available:
        if ensure_viewer(port):
            return port
    raise ValueError(f"포트 {browser_port()}에서 다른 작업 공간 또는 서비스가 실행 중입니다. DOCVERS_PORT를 변경하세요.")


def viewer_url(path, *, open_tab=False):
    port = workspace_port()
    url = f"http://{HOST}:{port}{WORKSPACE_PREFIX}{path}"
    if open_tab and not webbrowser.open(url, new=2):
        raise ValueError(f"기본 브라우저를 열 수 없습니다. 이 주소를 직접 여세요. {url}")
    return url

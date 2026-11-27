"""Start or reuse the loopback browser interface for the current document store."""

import hashlib
import http.client
import json
import os
import subprocess
import sys
import time
import webbrowser

from store import DATABASE, ROOT

HOST = "127.0.0.1"
STORE_ID = hashlib.sha256(os.fsencode(DATABASE)).hexdigest()


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
        if isinstance(payload, dict) and payload.get("store") == STORE_ID:
            return True
        return False
    except (ConnectionRefusedError, ConnectionResetError, http.client.RemoteDisconnected):
        return None
    except (OSError, ValueError, http.client.HTTPException):
        return False
    finally:
        connection.close()


def viewer_url(path, *, open_tab=False):
    port = browser_port()
    url = f"http://{HOST}:{port}{path}"
    status = viewer_status(port)
    if status is None:
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
    if not status:
        raise ValueError(f"포트 {port}에서 다른 서비스가 실행 중입니다. DOCVERS_PORT를 변경하세요.")
    if open_tab and not webbrowser.open(url, new=2):
        raise ValueError(f"기본 브라우저를 열 수 없습니다. 이 주소를 직접 여세요. {url}")
    return url

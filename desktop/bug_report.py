"""Publish an explicitly confirmed report using the reporter's GitHub account."""

import json
import os
import re
import sqlite3
import time
from hashlib import sha256
from http.client import HTTPException
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

import store

ISSUE_PATH = "/repos/Jang-myoung-gyoon/gitdoc/issues"
DEVICE_URL = "https://github.com/login/device"
SUBMISSION_UNKNOWN = "전송 결과를 확인할 수 없습니다. 중복 등록을 막기 위해 같은 내용은 다시 전송하지 않습니다. GitHub 이슈 목록을 확인하세요."


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request_json(url, payload=None, token=None, *, form=False):
    headers = {"Accept": "application/json", "User-Agent": "GitDoc"}
    if token:
        headers.update({"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                        "X-GitHub-Api-Version": "2022-11-28"})
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded" if form else "application/json"
        data = (urlencode(payload) if form else json.dumps(payload)).encode()
    request = Request(url, data=data, headers=headers)
    with build_opener(NoRedirect).open(request, timeout=8) as response:
        raw = response.read(131073)
        if len(raw) > 131072:
            raise ValueError("Response too large")
        return response.status, json.loads(raw)


class IssueReporter:
    def __init__(self, client_id, attempts, *, request=request_json, now=time.monotonic,
                 login_base="https://github.com/login", api_base="https://api.github.com"):
        self.client_id = client_id if re.fullmatch(r"[A-Za-z0-9._-]{6,100}", client_id) else ""
        self.attempts = attempts
        self.request = request
        self.now = now
        self.login_base = login_base
        self.api_base = api_base
        self.cancel()

    def status(self):
        return {"configured": bool(self.client_id), "login": self.login}

    def cancel(self):
        self.challenge = None
        self.token = None
        self.login = None

    def start(self):
        if not self.client_id:
            raise ValueError("이 빌드에는 GitHub 앱 로그인이 설정되어 있지 않습니다.")
        self.cancel()
        try:
            _, data = self.request(self.login_base + "/device/code", {"client_id": self.client_id}, form=True)
            code, device_code = data["user_code"], data["device_code"]
            interval, lifetime = data["interval"], data["expires_in"]
            if (data["verification_uri"] != DEVICE_URL or not re.fullmatch(r"[A-Z0-9-]{4,20}", code)
                    or not isinstance(device_code, str) or not 1 <= len(device_code) <= 512
                    or type(interval) is not int or not 1 <= interval <= 300
                    or type(lifetime) is not int or not interval < lifetime <= 3600):
                raise ValueError
        except (OSError, HTTPException, ValueError, KeyError, TypeError):
            raise ValueError("GitHub 로그인 코드를 받지 못했습니다. 다시 시도하세요.") from None
        now = self.now()
        self.challenge = (device_code, now + lifetime, interval, now + interval)
        return {"code": code, "interval": interval}

    def poll(self):
        if not self.challenge:
            raise ValueError("GitHub 로그인을 다시 시작하세요.")
        code, expiry, interval, allowed_at = self.challenge
        now = self.now()
        if now >= expiry:
            self.challenge = None
            raise ValueError("GitHub 로그인 코드가 만료되었습니다. 다시 시작하세요.")
        if now < allowed_at:
            return {"interval": interval}
        self.challenge = (code, expiry, interval, now + interval)
        try:
            _, data = self.request(self.login_base + "/oauth/access_token", {
                "client_id": self.client_id, "device_code": code,
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            }, form=True)
            if data.get("error") == "authorization_pending":
                return {"interval": interval}
            if data.get("error") == "slow_down":
                interval += 5
                self.challenge = (code, expiry, interval, self.now() + interval)
                return {"interval": interval}
            if data.get("error") == "access_denied":
                self.challenge = None
                raise PermissionError
            token = data["access_token"]
            if not isinstance(token, str) or not re.fullmatch(r"ghu_[A-Za-z0-9_]+", token):
                raise ValueError
            _, user = self.request(self.api_base + "/user", token=token)
            login = user["login"]
            if not isinstance(login, str) or not re.fullmatch(r"[A-Za-z0-9-]{1,39}", login):
                raise ValueError
        except PermissionError:
            raise ValueError("GitHub에서 로그인을 승인하지 않았습니다.") from None
        except (OSError, HTTPException, KeyError, ValueError, TypeError, AttributeError):
            self.challenge = None
            raise ValueError("GitHub에 로그인하지 못했습니다. GitHub에서 승인 상태를 확인하고 다시 시도하세요.") from None
        self.challenge = None
        self.token, self.login = token, login
        return {"login": login}

    def record_attempt(self, digest):
        # Reserve the exact payload before sending. A concurrent app or crash cannot repeat it.
        with sqlite3.connect(self.attempts) as db:
            db.execute("CREATE TABLE IF NOT EXISTS attempts (digest TEXT PRIMARY KEY, issue INTEGER)")
            entry = db.execute("INSERT OR IGNORE INTO attempts VALUES (?, NULL)", (digest,))
            if entry.rowcount:
                return True, None
            return False, db.execute("SELECT issue FROM attempts WHERE digest = ?", (digest,)).fetchone()[0]

    def submit(self, title, body, confirmed):
        if confirmed is not True:
            raise ValueError("공개 이슈 제출을 먼저 확인하세요.")
        if not isinstance(title, str) or not 1 <= len(title.strip()) <= 200 or title.splitlines() != [title]:
            raise ValueError("제목을 한 줄로 200자 이내로 입력하세요.")
        if not isinstance(body, str) or not 1 <= len(body.strip()) <= 12000:
            raise ValueError("신고 내용을 12,000자 이내로 입력하세요.")
        if not self.token:
            raise ValueError("GitHub에 먼저 로그인하세요.")
        payload = {"title": title.strip(), "body": body}
        digest = sha256(json.dumps(payload).encode()).hexdigest()
        try:
            fresh, number = self.record_attempt(digest)
        except sqlite3.Error:
            raise ValueError("중복 방지 기록을 저장할 수 없어 전송하지 않았습니다.") from None
        if not fresh:
            return {"number": number} if number else {"uncertain": SUBMISSION_UNKNOWN}
        try:
            status, data = self.request(self.api_base + ISSUE_PATH, payload, self.token)
            number = data["number"]
            if status != 201 or type(number) is not int or number <= 0:
                return {"uncertain": SUBMISSION_UNKNOWN}
        except HTTPError as error:
            messages = {
                401: "GitHub 로그인이 만료되었습니다. 다시 로그인한 뒤 제출하세요.",
                403: "이 계정으로 GitHub 이슈를 등록할 수 없거나 일시적으로 제한되었습니다. 잠시 후 다시 시도하거나 GitHub 작성 화면을 이용하세요.",
                404: "이 계정으로 이슈를 등록할 수 없습니다. GitHub 앱 설치와 권한을 확인하거나 GitHub 작성 화면을 이용하세요.",
                410: "이 저장소에서는 GitHub 이슈 등록이 꺼져 있습니다.",
                422: "GitHub가 신고 내용을 받아들이지 않았습니다. 내용을 확인한 뒤 다시 시도하세요.",
                429: "GitHub 요청 한도에 도달했습니다. 잠시 후 다시 시도하세요.",
            }
            if error.code not in messages:
                return {"uncertain": SUBMISSION_UNKNOWN}
            try:
                with sqlite3.connect(self.attempts) as db:
                    db.execute("DELETE FROM attempts WHERE digest = ? AND issue IS NULL", (digest,))
            except sqlite3.Error:
                return {"uncertain": SUBMISSION_UNKNOWN}
            if error.code == 401:
                self.cancel()
            return {"rejected": messages[error.code], "login": self.login}
        except (OSError, HTTPException, ValueError, TypeError, KeyError):
            return {"uncertain": SUBMISSION_UNKNOWN}
        try:
            with sqlite3.connect(self.attempts) as db:
                db.execute("UPDATE attempts SET issue = ? WHERE digest = ?", (number, digest))
        except sqlite3.Error:
            # Success is known. The reserved row still prevents retry if the app closes.
            pass
        return {"number": number}


reporter = IssueReporter(os.environ.get("DOCVERS_GITHUB_APP_CLIENT_ID", ""),
                        store.DATABASE.with_name("bug-report-attempts.sqlite3"))

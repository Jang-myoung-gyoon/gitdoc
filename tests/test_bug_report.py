import json
import socket
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

from desktop.bug_report import DEVICE_URL, ISSUE_PATH, IssueReporter


class BugReportTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.attempts = Path(temporary.name) / "attempts.sqlite3"
        self.responses = []
        self.requests = []
        self.clock = 100.0
        case = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def reply(self):
                raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                body = (parse_qs(raw.decode()) if self.headers.get("Content-Type") == "application/x-www-form-urlencoded"
                        else json.loads(raw) if raw else None)
                case.requests.append((self.command, self.path, body, self.headers.get("Authorization")))
                code, data = case.responses.pop(0)
                if code is None:
                    self.connection.shutdown(socket.SHUT_RDWR)
                    return
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(data).encode())

            do_GET = do_POST = reply

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=self.server.serve_forever)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(thread.join)
        self.addCleanup(self.server.shutdown)
        self.base = "http://127.0.0.1:" + str(self.server.server_port)
        self.reporter = self.new_reporter()

    def new_reporter(self):
        return IssueReporter("AppIdTest", self.attempts, now=lambda: self.clock,
                             login_base=self.base + "/login", api_base=self.base)

    def start(self, reporter):
        self.responses.append((200, {"user_code": "ABCD-EFGH", "device_code": "mock-device",
                                    "interval": 5, "expires_in": 900, "verification_uri": DEVICE_URL}))
        return reporter.start()

    def login(self, reporter):
        self.start(reporter)
        self.clock += 5
        self.responses.extend([(200, {"access_token": "ghu_mock", "refresh_token": "unused"}),
                               (200, {"login": "sample-user"})])
        self.assertEqual(reporter.poll(), {"login": "sample-user"})

    def test_confirmation_and_success_do_not_send_extra_fields_or_repeat(self):
        self.assertEqual(self.reporter.status(), {"configured": True, "login": None})
        with self.assertRaisesRegex(ValueError, "확인"):
            self.reporter.submit("재현", "내용", False)
        self.assertEqual(self.requests, [])
        self.login(self.reporter)
        self.responses.append((201, {"number": 35, "html_url": "https://example.invalid/not-used"}))
        self.assertEqual(self.reporter.submit(" 재현 ", "내용", True), {"number": 35})
        self.assertEqual(self.reporter.submit("재현", "내용", True), {"number": 35})
        self.assertEqual(self.requests[-1],
                         ("POST", ISSUE_PATH, {"title": "재현", "body": "내용"}, "Bearer ghu_mock"))
        self.assertEqual(sum(r[1] == ISSUE_PATH for r in self.requests), 1)

    def test_connection_lost_after_send_is_never_retried_even_after_restart(self):
        self.login(self.reporter)
        self.responses.append((None, {}))
        self.assertIn("uncertain", self.reporter.submit("Sample", "Example", True))
        reopened = self.new_reporter()
        self.login(reopened)
        self.assertIn("uncertain", reopened.submit("Sample", "Example", True))
        self.assertEqual(sum(r[1] == ISSUE_PATH for r in self.requests), 1)

    def test_definite_auth_failure_can_retry_after_reauth(self):
        self.login(self.reporter)
        self.responses.append((401, {"message": "Do not echo server content"}))
        result = self.reporter.submit("Sample", "Example", True)
        self.assertIn("rejected", result)
        self.assertIsNone(result["login"])
        self.login(self.reporter)
        self.responses.append((201, {"number": 36}))
        self.assertEqual(self.reporter.submit("Sample", "Example", True), {"number": 36})

    def test_device_poll_throttles_and_cancel_stops_requests(self):
        self.assertEqual(self.start(self.reporter), {"code": "ABCD-EFGH", "interval": 5})
        self.assertEqual(self.reporter.poll(), {"interval": 5})
        self.assertEqual(len(self.requests), 1)
        self.responses.append((200, {"error": "authorization_pending"}))
        self.clock += 5
        self.assertEqual(self.reporter.poll(), {"interval": 5})
        self.responses.append((200, {"error": "slow_down"}))
        self.clock += 5
        self.assertEqual(self.reporter.poll(), {"interval": 10})
        self.reporter.cancel()
        with self.assertRaises(ValueError):
            self.reporter.poll()
        self.assertEqual(len(self.requests), 3)

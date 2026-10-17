"""Isolated proxy checks; fake HTTPConnection never contacts a gateway."""
import importlib.util
import io
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("consumer_server", Path(__file__).parents[2] / "server.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class Reply:
    status = 200
    def read(self, size):
        return b'{"status":"ready"}'
    def getheaders(self):
        return [("Set-Cookie", "fixture=only; HttpOnly; SameSite=Strict"), ("Server", "ignored")]


class Connection:
    requests = []
    def __init__(self, host, port, timeout):
        assert (host, port) == ("127.0.0.1", 19390)
    def request(self, method, path, body, headers):
        self.requests.append((method, path, body, headers))
    def getresponse(self):
        return Reply()
    def close(self):
        pass


class ProxyTests(unittest.TestCase):
    def handler(self, method="GET", path="/api/assistant/session", body=b"", headers=None):
        h = object.__new__(module.Handler)
        h.command, h.path = method, path
        h.headers = headers or {}
        h.rfile, h.wfile = io.BytesIO(body), io.BytesIO()
        h.sent_headers, h.errors = [], []
        h.send_response = lambda code: setattr(h, "status", code)
        h.send_header = lambda name, value: h.sent_headers.append((name, value))
        h.end_headers = lambda: None
        h.json = lambda status, payload: h.errors.append((status, payload))
        return h
    def test_cookie_origin_and_query_forwarded_without_identity_or_retry(self):
        Connection.requests = []
        h = self.handler(path="/api/assistant/messages?requestId=fixture-id", headers={"Origin": "http://127.0.0.1:19388", "Cookie": "fixture=only"})
        with patch.object(module.http.client, "HTTPConnection", Connection):
            h._assistant_proxy()
        self.assertEqual(len(Connection.requests), 1)
        self.assertEqual(Connection.requests[0][1], h.path)
        self.assertEqual(Connection.requests[0][3]["Cookie"], "fixture=only")
        self.assertEqual(Connection.requests[0][3]["Host"], "127.0.0.1:19390")
        self.assertIn(("Set-Cookie", "fixture=only; HttpOnly; SameSite=Strict"), h.sent_headers)
    def test_post_body_is_unchanged(self):
        body = b'{"requestId":"fixture-id","text":"fixture"}'
        h = self.handler("POST", "/api/assistant/messages", body, {"Content-Length": str(len(body))})
        with patch.object(module.http.client, "HTTPConnection", Connection):
            h._assistant_proxy()
        self.assertEqual(Connection.requests[-1][2], body)
    def test_decision_is_read_only_and_keeps_episode_query(self):
        Connection.requests = []
        h = self.handler(path="/api/assistant/decision?episodeId=fixture-episode", headers={"Cookie": "fixture=only"})
        with patch.object(module.http.client, "HTTPConnection", Connection):
            h._assistant_proxy()
        self.assertEqual(Connection.requests[0][1], h.path)
        self.assertEqual(Connection.requests[0][3]["Cookie"], "fixture=only")
        for path in ("/api/assistant/decision", "/api/assistant/feed"):
            h = self.handler("POST", path, b"{}", {"Content-Length": "2"})
            h._assistant_proxy()
            self.assertEqual(h.errors[0][0], 405)
        h = self.handler(path="/api/assistant/feed?after=opaque-id")
        with patch.object(module.http.client, "HTTPConnection", Connection):
            h._assistant_proxy()
        self.assertEqual(Connection.requests[-1][1], h.path)
        self.assertEqual(len(Connection.requests), 2)
    def test_only_allowlisted_routes_and_methods(self):
        for method, path, code in [("POST", "/api/assistant/memories", 405), ("DELETE", "/api/assistant/session", 405), ("GET", "/api/assistant/../private", 404)]:
            h = self.handler(method, path)
            h._assistant_proxy()
            self.assertEqual(h.errors[0][0], code)
    def test_oversized_post_rejected_before_upstream(self):
        h = self.handler("POST", "/api/assistant/messages", headers={"Content-Length": "16385"})
        h._assistant_proxy()
        self.assertEqual(h.errors[0][0], 400)
    def test_transport_failure_is_not_retried(self):
        h = self.handler("POST", "/api/assistant/messages", b"{}", {"Content-Length": "2"})
        with patch.object(module.http.client, "HTTPConnection") as create:
            create.return_value.request.side_effect = OSError("fixture")
            h._assistant_proxy()
            self.assertEqual(create.return_value.request.call_count, 1)
        self.assertEqual(h.errors[0][0], 502)


if __name__ == "__main__":
    unittest.main()

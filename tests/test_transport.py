"""Exercise the chat transport against a local fake API; no external model calls."""
import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import patch

from rolesim.providers import ChatBackend


class TransportTests(unittest.TestCase):
    def test_chat_returns_parsed_reply_raw_text_and_reasoning(self):
        received = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                received.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                message = {"content": "```json\n" + json.dumps({"speech": "我核对一下。"}, ensure_ascii=False) + "\n```",
                           "reasoning_content": "先想一下"}
                raw = json.dumps({"model": "fake-2026-01", "choices": [{"message": message}]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, *args):
                pass

        server = HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            env = {"ROLE_SIM_BASE_URL": f"http://127.0.0.1:{server.server_port}/v1", "ROLE_SIM_MODEL": "fake", "ROLE_SIM_API_KEY": ""}
            with patch.dict(os.environ, env):
                backend = ChatBackend()
                parsed, raw = backend.chat("系统提示", {"x": 1})
            self.assertEqual(parsed, {"speech": "我核对一下。"})
            self.assertEqual(json.loads(raw), parsed)
            self.assertEqual((backend.calls, backend.served_models, backend.last_reasoning), (1, ["fake-2026-01"], "先想一下"))
            self.assertEqual(received[0]["response_format"], {"type": "json_object"})
            self.assertEqual(received[0]["messages"][0], {"role": "system", "content": "系统提示"})
        finally:
            server.shutdown()
            thread.join(timeout=2)
            server.server_close()

    def test_remote_plain_http_and_embedded_credentials_rejected(self):
        for url in ("http://example.com/v1", "https://secret@example.com/v1", "https://example.com/v1?key=secret"):
            with patch.dict(os.environ, {"ROLE_SIM_BASE_URL": url, "ROLE_SIM_MODEL": "fake"}), self.assertRaises(ValueError):
                ChatBackend()


if __name__ == "__main__":
    unittest.main()

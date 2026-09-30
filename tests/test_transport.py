"""Exercise the real HTTP adapter against a local fake API; no external model calls."""
import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import patch

from rolesim.providers import ChatBackend


class TransportTests(unittest.TestCase):
    def test_two_stage_chat_contract(self):
        received = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                received.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                result = {"action": "wait", "evidence_ids": ["e1"], "brief_reason": "核对"} if len(received) == 1 else {"speech": "我核对一下。"}
                raw = json.dumps({"choices": [{"message": {"content": json.dumps(result)}}]}).encode()
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
            with patch.dict(os.environ, {"ROLE_SIM_BASE_URL": f"http://127.0.0.1:{server.server_port}/v1", "ROLE_SIM_MODEL": "fake", "ROLE_SIM_API_KEY": ""}):
                backend = ChatBackend()
                self.assertEqual(backend.decide({})["action"], "wait")
                self.assertEqual(backend.express({}), "我核对一下。")
                self.assertEqual(backend.calls, 2)
                self.assertTrue(all(p["response_format"] == {"type": "json_object"} for p in received))
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

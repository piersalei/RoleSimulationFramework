"""Exercise the chat transport against a local fake API; no external model calls."""
import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import patch

from rolesim.stage.providers import ChatBackend


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
            schema = {"type": "object", "properties": {"speech": {"type": "string"}}, "required": ["speech"]}
            with patch.dict(os.environ, env):
                ChatBackend().chat("系统提示", {"x": 1}, schema)
            self.assertEqual(received[1]["response_format"],
                             {"type": "json_schema", "json_schema": {"name": "output", "schema": schema, "strict": True}})
            with patch.dict(os.environ, {**env, "ROLE_SIM_STRUCTURED": "0"}):
                ChatBackend().chat("系统提示", {"x": 1}, schema)
            self.assertEqual(received[2]["response_format"], {"type": "json_object"})
        finally:
            server.shutdown()
            thread.join(timeout=2)
            server.server_close()

    def test_text_after_the_first_value_is_dropped_only_under_structured_decoding(self):
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                message = {"content": '{"speech": "好。"}\n<tool_response>\n```json\n{"speech": "好。"'}
                raw = json.dumps({"model": "fake", "choices": [{"message": message}]}).encode()
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
            env = {"ROLE_SIM_BASE_URL": f"http://127.0.0.1:{server.server_port}/v1", "ROLE_SIM_MODEL": "fake"}
            schema = {"type": "object", "properties": {"speech": {"type": "string"}}}
            with patch.dict(os.environ, env):
                backend = ChatBackend()
                parsed, raw = backend.chat("s", {}, schema)
                self.assertEqual((parsed, json.loads(raw)), ({"speech": "好。"}, {"speech": "好。"}))
                self.assertEqual(len(backend.trailing), 1)
                self.assertTrue(backend.last_trailing.startswith("<tool_response>"))
                self.assertIn("<tool_response>", backend.last_raw)
                with self.assertRaises(ValueError):
                    backend.chat("s", {})                                 # no schema: strict as before
            with patch.dict(os.environ, {**env, "ROLE_SIM_STRUCTURED": "0"}), self.assertRaises(ValueError):
                ChatBackend().chat("s", {}, schema)
        finally:
            server.shutdown()
            thread.join(timeout=2)
            server.server_close()

    def test_structured_flag_must_be_boolean(self):
        env = {"ROLE_SIM_BASE_URL": "http://127.0.0.1:1/v1", "ROLE_SIM_MODEL": "fake", "ROLE_SIM_STRUCTURED": "maybe"}
        with patch.dict(os.environ, env), self.assertRaises(ValueError):
            ChatBackend()

    def test_remote_plain_http_and_embedded_credentials_rejected(self):
        for url in ("http://example.com/v1", "https://secret@example.com/v1", "https://example.com/v1?key=secret"):
            with patch.dict(os.environ, {"ROLE_SIM_BASE_URL": url, "ROLE_SIM_MODEL": "fake"}), self.assertRaises(ValueError):
                ChatBackend()


if __name__ == "__main__":
    unittest.main()

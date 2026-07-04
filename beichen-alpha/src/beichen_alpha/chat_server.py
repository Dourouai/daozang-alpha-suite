from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from beichen_alpha.chat.feishu import FeishuEventAdapter


class ChatServerHandler(BaseHTTPRequestHandler):
    adapter: FeishuEventAdapter

    def do_GET(self) -> None:
        if self.path == "/health":
            self.write_json(200, {"ok": True})
            return
        self.write_json(404, {"error": "not found"})

    def do_POST(self) -> None:
        if self.path != "/feishu/events":
            self.write_json(404, {"error": "not found"})
            return
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            self.write_json(400, {"error": "invalid json"})
            return
        result = self.adapter.handle_event(payload)
        self.write_json(result.status_code, result.payload)

    def write_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args) -> None:
        return


def run_chat_server(host: str, port: int, project_dir: str | Path = ".") -> None:
    adapter = FeishuEventAdapter(project_dir=project_dir)
    handler = type("ConfiguredChatServerHandler", (ChatServerHandler,), {"adapter": adapter})
    server = ThreadingHTTPServer((host, port), handler)
    print(f"Beichen chat server listening on http://{host}:{port}", flush=True)
    print("Feishu event endpoint: /feishu/events", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Beichen chat server stopped.", flush=True)
    finally:
        server.server_close()

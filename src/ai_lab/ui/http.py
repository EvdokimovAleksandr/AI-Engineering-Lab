"""Minimal stdlib HTTP UI. Delegates to ui.service → LabRuntime."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ai_lab.observability.logger import get_logger
from ai_lab.ui.service import UiApiError, create_run, get_result, get_run, get_status, list_projects
from ai_lab.ui.templates import INDEX_HTML

logger = get_logger(__name__)


def _json_bytes(payload: Any, status: int = 200) -> tuple[int, bytes, str]:
    body = json.dumps(payload, default=str).encode("utf-8")
    return status, body, "application/json; charset=utf-8"


class LabUiHandler(BaseHTTPRequestHandler):
    repo_root: Path

    def log_message(self, fmt: str, *args: object) -> None:
        logger.info("%s - %s", self.address_string(), fmt % args)

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        try:
            if path == "/":
                self._send(200, INDEX_HTML.encode("utf-8"), "text/html; charset=utf-8")
                return
            if path == "/api/projects":
                status, body, ctype = _json_bytes({"projects": list_projects(repo_root=self.repo_root)})
                self._send(status, body, ctype)
                return
            parts = path.strip("/").split("/")
            if parts[:2] == ["api", "runs"] and len(parts) >= 3:
                run_id = parts[2]
                if len(parts) == 3:
                    status, body, ctype = _json_bytes(get_run(run_id, repo_root=self.repo_root))
                elif parts[3] == "status":
                    status, body, ctype = _json_bytes(get_status(run_id, repo_root=self.repo_root))
                elif parts[3] == "result":
                    status, body, ctype = _json_bytes(get_result(run_id, repo_root=self.repo_root))
                else:
                    status, body, ctype = _json_bytes({"error": "Not found"}, 404)
                self._send(status, body, ctype)
                return
            status, body, ctype = _json_bytes({"error": "Not found"}, 404)
            self._send(status, body, ctype)
        except UiApiError as exc:
            status, body, ctype = _json_bytes({"error": str(exc)}, exc.status)
            self._send(status, body, ctype)
        except Exception as exc:
            logger.error("UI GET failed: %s", exc)
            status, body, ctype = _json_bytes({"error": str(exc)}, 500)
            self._send(status, body, ctype)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        try:
            if path != "/api/runs":
                status, body, ctype = _json_bytes({"error": "Not found"}, 404)
                self._send(status, body, ctype)
                return
            length = int(self.headers.get("Content-Length") or "0")
            if length < 0 or length > 200_000:
                raise UiApiError("Invalid Content-Length")
            raw = self.rfile.read(length) if length else b"{}"
            try:
                payload = json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError as exc:
                raise UiApiError(f"Invalid JSON: {exc}") from exc
            result = create_run(payload, repo_root=self.repo_root)
            status, body, ctype = _json_bytes(result, 201)
            self._send(status, body, ctype)
        except UiApiError as exc:
            status, body, ctype = _json_bytes({"error": str(exc)}, exc.status)
            self._send(status, body, ctype)
        except Exception as exc:
            logger.error("UI POST failed: %s", exc)
            status, body, ctype = _json_bytes({"error": str(exc)}, 500)
            self._send(status, body, ctype)


def make_server(host: str, port: int, *, repo_root: Path) -> ThreadingHTTPServer:
    handler = type("BoundLabUiHandler", (LabUiHandler,), {"repo_root": repo_root})
    return ThreadingHTTPServer((host, port), handler)


def serve(host: str = "127.0.0.1", port: int = 8765, *, repo_root: Path | None = None) -> None:
    root = repo_root or Path(__file__).resolve().parents[3]
    httpd = make_server(host, port, repo_root=root)
    print(f"AI Engineering Lab UI: http://{host}:{port}/")
    httpd.serve_forever()

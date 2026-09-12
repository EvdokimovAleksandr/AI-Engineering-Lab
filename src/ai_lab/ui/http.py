"""Minimal stdlib HTTP UI. Delegates to ui.service → LabRuntime. SSE for live runs."""

from __future__ import annotations

import json
import queue
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from ai_lab.core.models import RunEvent
from ai_lab.observability.logger import get_logger
from ai_lab.ui.service import (
    UiApiError,
    create_project,
    create_project_run,
    create_run,
    export_result,
    get_event_sink,
    get_events,
    get_project,
    get_result,
    get_run,
    get_status,
    list_projects_detailed,
    list_recent_runs,
)
from ai_lab.ui.templates import INDEX_HTML

logger = get_logger(__name__)


def _json_bytes(payload: Any, status: int = 200) -> tuple[int, bytes, str]:
    body = json.dumps(payload, default=str).encode("utf-8")
    return status, body, "application/json; charset=utf-8"


class LabUiHandler(BaseHTTPRequestHandler):
    repo_root: Path
    # Demo mode injects a banner / sample prompt into the SPA.
    demo_mode: bool = False

    def log_message(self, fmt: str, *args: object) -> None:
        logger.info("%s - %s", self.address_string(), fmt % args)

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self) -> None:
        html = INDEX_HTML
        if self.demo_mode:
            html = html.replace("data-demo=\"false\"", "data-demo=\"true\"", 1)
        self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or "0")
        if length < 0 or length > 200_000:
            raise UiApiError("Invalid Content-Length")
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise UiApiError(f"Invalid JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise UiApiError("JSON object required")
        return payload

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        query = parse_qs(parsed.query)
        try:
            # SPA routes — same shell; client reads location.pathname.
            if path == "/" or path.startswith("/projects/") or path.startswith("/runs/"):
                if path.startswith("/api/"):
                    pass
                else:
                    self._send_html()
                    return

            if path == "/api/projects":
                status, body, ctype = _json_bytes(
                    {"projects": list_projects_detailed(repo_root=self.repo_root)}
                )
                self._send(status, body, ctype)
                return

            if path == "/api/runs":
                limit = int((query.get("limit") or ["20"])[0])
                status, body, ctype = _json_bytes(
                    {"runs": list_recent_runs(repo_root=self.repo_root, limit=limit)}
                )
                self._send(status, body, ctype)
                return

            parts = path.strip("/").split("/")

            if parts[:2] == ["api", "projects"] and len(parts) >= 3:
                project_id = parts[2]
                if len(parts) == 3:
                    status, body, ctype = _json_bytes(
                        get_project(project_id, repo_root=self.repo_root)
                    )
                    self._send(status, body, ctype)
                    return

            if parts[:2] == ["api", "runs"] and len(parts) >= 3:
                run_id = parts[2]
                if len(parts) == 3:
                    status, body, ctype = _json_bytes(get_run(run_id, repo_root=self.repo_root))
                elif parts[3] == "status":
                    status, body, ctype = _json_bytes(get_status(run_id, repo_root=self.repo_root))
                elif parts[3] == "result":
                    status, body, ctype = _json_bytes(get_result(run_id, repo_root=self.repo_root))
                elif parts[3] == "events":
                    offset = int((query.get("offset") or ["0"])[0])
                    status, body, ctype = _json_bytes(
                        get_events(run_id, repo_root=self.repo_root, offset=offset)
                    )
                elif parts[3] == "stream":
                    self._sse_stream(run_id)
                    return
                elif parts[3] == "export":
                    fmt = (query.get("format") or ["json"])[0]
                    text, ctype = export_result(run_id, fmt=fmt, repo_root=self.repo_root)
                    self._send(200, text.encode("utf-8"), ctype)
                    return
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
            parts = path.strip("/").split("/")

            if path == "/api/projects":
                payload = self._read_json()
                result = create_project(payload, repo_root=self.repo_root)
                status, body, ctype = _json_bytes(result, 201)
                self._send(status, body, ctype)
                return

            if parts[:2] == ["api", "projects"] and len(parts) == 4 and parts[3] == "runs":
                payload = self._read_json()
                # UI path is async by default.
                wait = bool(payload.get("wait", False))
                result = create_project_run(
                    parts[2], payload, repo_root=self.repo_root, wait=wait
                )
                status, body, ctype = _json_bytes(result, 201)
                self._send(status, body, ctype)
                return

            if path == "/api/runs":
                payload = self._read_json()
                # HTTP UI defaults to non-blocking; tests calling service.create_run stay sync.
                wait = bool(payload["wait"]) if "wait" in payload else False
                if "async" in payload:
                    wait = not bool(payload["async"])
                result = create_run(payload, repo_root=self.repo_root, wait=wait)
                status, body, ctype = _json_bytes(result, 201)
                self._send(status, body, ctype)
                return

            status, body, ctype = _json_bytes({"error": "Not found"}, 404)
            self._send(status, body, ctype)
        except UiApiError as exc:
            status, body, ctype = _json_bytes({"error": str(exc)}, exc.status)
            self._send(status, body, ctype)
        except Exception as exc:
            logger.error("UI POST failed: %s", exc)
            status, body, ctype = _json_bytes({"error": str(exc)}, 500)
            self._send(status, body, ctype)

    def _sse_stream(self, run_id: str) -> None:
        """Server-Sent Events: replay JSONL then follow live sink listeners."""
        sink = get_event_sink(run_id, repo_root=self.repo_root)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        q: queue.Queue[RunEvent | None] = queue.Queue()

        def _on_event(event: RunEvent) -> None:
            q.put(event)

        # Replay durable history first so reconnect is consistent.
        for event in sink.read_all():
            self._write_sse(event)
        sink.add_listener(_on_event)
        try:
            # Heartbeat + live follow. Stop shortly after terminal lifecycle events.
            idle_rounds = 0
            while idle_rounds < 120:  # ~60s idle after last event with 0.5s poll
                try:
                    event = q.get(timeout=0.5)
                except queue.Empty:
                    idle_rounds += 1
                    self._write_sse_comment("ping")
                    # If run finished and queue drained, exit so the browser can reconnect cleanly.
                    try:
                        st = get_status(run_id, repo_root=self.repo_root)
                        if st.get("lifecycle_status") in {"COMPLETED", "FAILED", "ERROR", "PLANNED"}:
                            if q.empty():
                                break
                    except UiApiError:
                        break
                    continue
                idle_rounds = 0
                self._write_sse(event)
                if event.message in {"run.completed", "run.failed"}:
                    # Drain briefly then close.
                    time.sleep(0.2)
                    while not q.empty():
                        self._write_sse(q.get_nowait())
                    break
        except (BrokenPipeError, ConnectionResetError):
            logger.info("SSE client disconnected for %s", run_id)
        finally:
            sink.remove_listener(_on_event)

    def _write_sse(self, event: RunEvent) -> None:
        payload = {
            "event_id": event.event_id,
            "run_id": event.run_id,
            "timestamp": event.timestamp.isoformat() if event.timestamp else None,
            "message": event.message,
            "status": event.status,
            "agent_role": event.agent_role,
            "task_id": event.task_id,
            "data": event.data,
        }
        data = json.dumps(payload, default=str)
        msg = f"event: {event.message or 'message'}\ndata: {data}\n\n"
        self.wfile.write(msg.encode("utf-8"))
        self.wfile.flush()

    def _write_sse_comment(self, text: str) -> None:
        self.wfile.write(f": {text}\n\n".encode("utf-8"))
        self.wfile.flush()


def make_server(
    host: str, port: int, *, repo_root: Path, demo_mode: bool = False
) -> ThreadingHTTPServer:
    handler = type(
        "BoundLabUiHandler",
        (LabUiHandler,),
        {"repo_root": repo_root, "demo_mode": demo_mode},
    )
    return ThreadingHTTPServer((host, port), handler)


def serve(
    host: str = "127.0.0.1",
    port: int = 8765,
    *,
    repo_root: Path | None = None,
    demo_mode: bool = False,
) -> None:
    root = repo_root or Path(__file__).resolve().parents[3]
    httpd = make_server(host, port, repo_root=root, demo_mode=demo_mode)
    mode = " (demo)" if demo_mode else ""
    print(f"AI Engineering Lab UI{mode}: http://{host}:{port}/")
    httpd.serve_forever()

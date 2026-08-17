#!/usr/bin/env python3
"""Deterministic protocol-fault service for isolated lab tests."""

from __future__ import annotations

import json
import os
import time
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit


SCENARIOS = {
    "healthy",
    "timeout",
    "unavailable",
    "malformed-json",
    "unsupported-version",
    "stale-readback",
}


def response_for(scenario: str) -> tuple[float, int, str, bytes]:
    responses = {
        "healthy": (0.0, 200, "application/json", b'{"api_version":"1","generation":1,"status":"ok"}'),
        "timeout": (1.5, 200, "application/json", b'{"status":"delayed"}'),
        "unavailable": (0.0, 503, "application/json", b'{"error":"service_unavailable"}'),
        "malformed-json": (0.0, 200, "application/json", b'{"broken":'),
        "unsupported-version": (0.0, 200, "application/json", b'{"api_version":"999","status":"unsupported"}'),
        "stale-readback": (0.0, 200, "application/json", b'{"generation":1,"observed_generation":0,"status":"stale"}'),
    }
    try:
        return responses[scenario]
    except KeyError as error:
        raise ValueError("unsupported scenario") from error


class ScenarioState:
    def __init__(self) -> None:
        self._scenario = "healthy"
        self._lock = threading.Lock()

    def get(self) -> str:
        with self._lock:
            return self._scenario

    def set(self, scenario: str) -> None:
        if scenario not in SCENARIOS:
            raise ValueError("unsupported scenario")
        with self._lock:
            self._scenario = scenario

    def reset(self) -> None:
        self.set("healthy")


STATE = ScenarioState()


def read_token() -> str:
    token_file = Path(os.environ.get("TOKEN_FILE", "/run/secrets/fault-api-token"))
    return token_file.read_text(encoding="utf-8").strip()


class Handler(BaseHTTPRequestHandler):
    expected_token = ""

    def authorized(self) -> bool:
        authorization = self.headers.get("Authorization", "")
        prefix = "Bearer "
        return authorization.startswith(prefix) and authorization[len(prefix) :].strip() == self.expected_token

    def write_response(self, status: int, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except BrokenPipeError:
            return

    def do_GET(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        if path == "/health/live":
            self.write_response(200, "application/json", b'{"status":"live"}')
            return
        if path == "/health/ready":
            self.write_response(200, "application/json", b'{"status":"ready"}')
            return
        if path == "/baseline":
            self.write_response(200, "application/json", b'{"fixture":"synthetic-fault-api-v1","status":"verified"}')
            return
        if path == "/scenario":
            body = json.dumps({"scenario": STATE.get()}, separators=(",", ":")).encode("utf-8")
            self.write_response(200, "application/json", body)
            return
        if path != "/api/v1/probe":
            self.write_response(404, "application/json", b'{"error":"not_found"}')
            return
        delay, status, content_type, body = response_for(STATE.get())
        if delay:
            time.sleep(delay)
        self.write_response(status, content_type, body)

    def do_PUT(self) -> None:  # noqa: N802
        if urlsplit(self.path).path != "/scenario":
            self.write_response(404, "application/json", b'{"error":"not_found"}')
            return
        if not self.authorized():
            self.write_response(401, "application/json", b'{"error":"unauthorized"}')
            return
        length = int(self.headers.get("Content-Length", "0"))
        if length < 1 or length > 1024:
            self.write_response(400, "application/json", b'{"error":"invalid_request"}')
            return
        try:
            payload = json.loads(self.rfile.read(length))
            STATE.set(payload["scenario"])
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            self.write_response(400, "application/json", b'{"error":"invalid_scenario"}')
            return
        body = json.dumps({"scenario": STATE.get()}, separators=(",", ":")).encode("utf-8")
        self.write_response(200, "application/json", body)

    def do_POST(self) -> None:  # noqa: N802
        if urlsplit(self.path).path != "/reset":
            self.write_response(404, "application/json", b'{"error":"not_found"}')
            return
        if not self.authorized():
            self.write_response(401, "application/json", b'{"error":"unauthorized"}')
            return
        STATE.reset()
        self.write_response(200, "application/json", b'{"scenario":"healthy"}')

    def log_message(self, _format: str, *_args: object) -> None:
        return


def main() -> int:
    Handler.expected_token = read_token()
    server = ThreadingHTTPServer(("0.0.0.0", 8080), Handler)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

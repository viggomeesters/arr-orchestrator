#!/usr/bin/env python3
"""Minimal deterministic synthetic indexer double."""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit


def encoded(payload: object) -> bytes:
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


def response_for(path: str, provided_token: str | None, expected_token: str) -> tuple[int, str, bytes]:
    if path == "/health/live":
        return 200, "application/json", b'{"status":"live"}'
    if path == "/health/ready":
        return 200, "application/json", b'{"status":"ready"}'
    if path == "/baseline":
        return 200, "application/json", b'{"fixture":"synthetic-indexer-v1","status":"verified"}'
    if path != "/api/v1/search":
        return 404, "application/json", b'{"error":"not_found"}'
    if provided_token != expected_token:
        return 401, "application/json", b'{"error":"unauthorized"}'
    return (
        200,
        "application/json",
        b'{"items":[{"id":"synthetic-1","title":"Synthetic Result"}],"total":1}',
    )


def read_token() -> str:
    token_file = Path(os.environ.get("TOKEN_FILE", "/run/secrets/mock-indexer-token"))
    return token_file.read_text(encoding="utf-8").strip()


class Handler(BaseHTTPRequestHandler):
    expected_token = ""

    def do_GET(self) -> None:  # noqa: N802
        authorization = self.headers.get("Authorization", "")
        prefix = "Bearer "
        provided = authorization[len(prefix) :].strip() if authorization.startswith(prefix) else None
        status, content_type, body = response_for(urlsplit(self.path).path, provided, self.expected_token)
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        return


def main() -> int:
    Handler.expected_token = read_token()
    server = ThreadingHTTPServer(("0.0.0.0", 8080), Handler)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

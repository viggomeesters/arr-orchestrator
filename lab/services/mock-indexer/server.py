#!/usr/bin/env python3
"""Minimal deterministic synthetic indexer double."""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


def encoded(payload: object) -> bytes:
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


def response_for(path: str, provided_token: str | None, expected_token: str) -> tuple[int, str, bytes]:
    if path == "/health/live":
        return 200, "application/json", b'{"status":"live"}'
    if path == "/health/ready":
        return 200, "application/json", b'{"status":"ready"}'
    if path == "/baseline":
        return 200, "application/json", b'{"fixture":"synthetic-indexer-v1","status":"verified"}'
    if path == "/download/synthetic-1.nzb":
        return (
            200,
            "application/x-nzb",
            b'<?xml version="1.0" encoding="UTF-8"?><nzb xmlns="http://www.newzbin.com/DTD/2003/nzb"><head><meta type="category">TV</meta></head></nzb>',
        )
    if path != "/api/v1/search":
        return 404, "application/json", b'{"error":"not_found"}'
    if provided_token != expected_token:
        return 401, "application/json", b'{"error":"unauthorized"}'
    return 200, "application/json", b'{"items":[{"id":"synthetic-1","title":"Synthetic Result"}],"total":1}'


def newznab_response(query: dict[str, list[str]], expected_token: str) -> tuple[int, str, bytes]:
    if query.get("apikey", [""])[0] != expected_token:
        return 401, "application/xml", b'<error code="100" description="incorrect api key" />'
    operation = query.get("t", [""])[0]
    if operation == "caps":
        body = "".join(
            (
                '<?xml version="1.0" encoding="UTF-8"?>',
                '<caps><server title="Synthetic Mock Indexer" />',
                '<limits max="100" default="100" />',
                '<searching><search available="yes" supportedParams="q" />',
                '<tv-search available="yes" supportedParams="q,season,ep" />',
                '<movie-search available="yes" supportedParams="q,imdbid" />',
                '</searching><categories><category id="2000" name="Movies" />',
                '<category id="5000" name="TV" /></categories></caps>',
            )
        )
        return 200, "application/xml", body.encode("utf-8")
    if operation in {"search", "tvsearch", "movie"}:
        body = "".join(
            (
                '<?xml version="1.0" encoding="UTF-8"?>',
                '<rss version="2.0" xmlns:newznab="http://www.newznab.com/DTD/2010/feeds/attributes/">',
                '<channel><title>Synthetic Mock Indexer</title>',
                '<description>deterministic synthetic feed</description>',
                '<link>http://mock-indexer:8080/</link>',
                '<newznab:response offset="0" total="1" />',
                '<item><title>Synthetic Result</title>',
                '<guid isPermaLink="false">synthetic-1</guid>',
                '<link>http://mock-indexer:8080/download/synthetic-1.nzb</link>',
                '<pubDate>Mon, 17 Aug 2026 00:00:00 +0000</pubDate>',
                '<enclosure url="http://mock-indexer:8080/download/synthetic-1.nzb" length="1024" type="application/x-nzb" />',
                '<newznab:attr name="size" value="1024" />',
                '<newznab:attr name="category" value="5000" />',
                '</item></channel></rss>',
            )
        )
        return 200, "application/rss+xml", body.encode("utf-8")
    return 400, "application/xml", b'<error code="200" description="unsupported function" />'


def read_token() -> str:
    token_file = Path(os.environ.get("TOKEN_FILE", "/run/secrets/mock-indexer-token"))
    return token_file.read_text(encoding="utf-8").strip()


class Handler(BaseHTTPRequestHandler):
    expected_token = ""

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlsplit(self.path)
        if parsed.path == "/api":
            status, content_type, body = newznab_response(parse_qs(parsed.query), self.expected_token)
        else:
            authorization = self.headers.get("Authorization", "")
            prefix = "Bearer "
            provided = authorization[len(prefix) :].strip() if authorization.startswith(prefix) else None
            status, content_type, body = response_for(parsed.path, provided, self.expected_token)
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

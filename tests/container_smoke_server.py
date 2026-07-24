"""Tiny HTTP server used by the Docker smoke test."""

from __future__ import annotations

import base64
import json
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import ClassVar
from urllib.parse import urlsplit

_EXPECTED_AUTH = "Basic " + base64.b64encode(b"smoke-user:smoke-token").decode()


class SmokeHandler(BaseHTTPRequestHandler):
    """Serve one feed and the minimal Listmonk campaign API."""

    created: ClassVar[bool] = False

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        if path == "/newsletter.json":
            self._json(
                HTTPStatus.OK,
                {
                    "schemaVersion": 1,
                    "posts": [
                        {
                            "key": "container-smoke",
                            "title": "Container smoke test",
                            "html": "<p>It works.</p>",
                            "readingTime": 1,
                        }
                    ],
                },
            )
            return
        if path == "/api/campaigns":
            if not self._authorized():
                return
            self._json(HTTPStatus.OK, {"data": {"results": []}})
            return
        if path == "/health":
            status = HTTPStatus.OK if self.created else HTTPStatus.SERVICE_UNAVAILABLE
            self._json(status, {"created": self.created})
            return
        self._json(HTTPStatus.NOT_FOUND, {"message": "not found"})

    def do_POST(self) -> None:
        if urlsplit(self.path).path != "/api/campaigns":
            self._json(HTTPStatus.NOT_FOUND, {"message": "not found"})
            return
        if not self._authorized():
            return
        length = int(self.headers.get("Content-Length", "0"))
        try:
            payload = json.loads(self.rfile.read(length))
        except (ValueError, json.JSONDecodeError):
            self._json(HTTPStatus.BAD_REQUEST, {"message": "invalid JSON"})
            return
        expected = {
            "name": "container-smoke",
            "subject": "Container smoke test",
            "body": "<p>It works.</p>",
            "lists": [1],
            "content_type": "html",
        }
        if any(payload.get(key) != value for key, value in expected.items()):
            self._json(HTTPStatus.BAD_REQUEST, {"message": "unexpected payload"})
            return
        if payload.get("attribs", {}).get("post", {}).get("readingTime") != (
            "1 min read"
        ):
            self._json(HTTPStatus.BAD_REQUEST, {"message": "unexpected attributes"})
            return
        type(self).created = True
        self._json(
            HTTPStatus.OK,
            {"data": {"id": 1, "name": "container-smoke", "status": "draft"}},
        )

    def log_message(self, _format: str, *_args: object) -> None:
        """Keep smoke-test output focused on the container."""

    def _authorized(self) -> bool:
        if self.headers.get("Authorization") == _EXPECTED_AUTH:
            return True
        self._json(HTTPStatus.UNAUTHORIZED, {"message": "unauthorized"})
        return False

    def _json(self, status: HTTPStatus, payload: object) -> None:
        encoded = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


def main() -> None:
    """Run the mock on all interfaces at the requested port."""
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 18081
    # Docker reaches this host-side mock through the bridge gateway.
    ThreadingHTTPServer(("0.0.0.0", port), SmokeHandler).serve_forever()  # noqa: S104


if __name__ == "__main__":
    main()

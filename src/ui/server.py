"""
The HTTP layer: one ThreadingHTTPServer bound to 127.0.0.1, and its gates.

THE THREAT MODEL (README "Local UI"): another website open in the same browser,
and DNS rebinding. Local malware is out of scope - it could read the key from
~/.zshrc directly. So every request passes, in this order:

  1. HOST     exactly 127.0.0.1:<port> or localhost:<port>, on every request,
              static files included. A rebinding attack arrives with its own
              hostname in Host. -> 421, empty body.
  2. TOKEN    a per-launch secret, delivered only inside the page at "/", and
              required as `X-PO-Token` on EVERY /api/* request, GET included.
              Another site cannot read the page, so it cannot learn the token.
              -> 403 {"error": "stale_page"}.
  3. ORIGIN   a POST must carry Origin http://127.0.0.1:<port> or
              http://localhost:<port>. -> 403.
  4. SHAPE    POST bodies are application/json, at most 64 KB, one JSON
              object. OPTIONS and every other method -> 405. No
              Access-Control-* header is ever sent.
  5. EGRESS   before any body leaves, it is searched for the Seats.aero key and
              its mask. A match is never sent: 500, and only that fact is
              logged.

Anything that can spend Seats.aero calls also needs a server-side confirm token
(src/ui/engine.py). Static files come from a fixed map; nothing is joined from
the URL.
"""
from __future__ import annotations

import hmac
import json
import secrets
import sys
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Dict, Optional, Tuple
from urllib.parse import urlsplit

STATIC_DIR = Path(__file__).resolve().parent / "static"
STATIC_FILES: Dict[str, Tuple[str, str]] = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/static/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/static/app.css": ("app.css", "text/css; charset=utf-8"),
}
TOKEN_PLACEHOLDER = "{{PO_TOKEN}}"

CSP = (
    "default-src 'none'; script-src 'self'; "
    "style-src 'self' https://fonts.googleapis.com; font-src https://fonts.gstatic.com; "
    "connect-src 'self'; img-src 'self' data:; base-uri 'none'; form-action 'none'; "
    "frame-ancestors 'none'"
)
SECURITY_HEADERS = (
    ("Content-Security-Policy", CSP),
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "no-referrer"),
    ("X-Frame-Options", "DENY"),
    # L-4. frame-ancestors stops the page being framed, but a page on any
    # origin could still pull /static/app.js in as a <script> - the token is
    # not in it, so nothing leaks, but nothing else on this server should be
    # readable that way either. CORP refuses the cross-origin read outright.
    ("Cross-Origin-Resource-Policy", "same-origin"),
)
MAX_BODY_BYTES = 64 * 1024

STALE_PAGE = {
    "error": "stale_page",
    "message": "This page is from an earlier launch of the app. Reload it.",
}
EGRESS_REFUSAL = {
    "error": "internal",
    "message": "Refused to send a response that contained key material.",
}


class ApiError(Exception):
    """A user-facing refusal: HTTP status plus a JSON body."""

    def __init__(self, status: int, error: str, message: str, **extra):
        super().__init__(message)
        self.status = status
        self.payload = {"error": error, "message": message, **extra}


def _reject_constant(name: str):
    raise ValueError(f"{name} is not valid JSON")


def parse_json_object(raw: bytes) -> dict:
    """One strict JSON object. NaN/Infinity and non-objects are refused."""
    try:
        value = json.loads(raw.decode("utf-8"), parse_constant=_reject_constant)
    except (UnicodeDecodeError, ValueError) as e:
        raise ApiError(400, "bad_request", f"The request body is not valid JSON ({e}).")
    if not isinstance(value, dict):
        raise ApiError(400, "bad_request", "The request body must be one JSON object.")
    return value


def dumps(payload) -> str:
    """JSON out. `allow_nan=False`: a stray inf/nan fails LOUDLY, because the
    browser cannot be trusted to render a number it should never receive."""
    return json.dumps(payload, allow_nan=False, ensure_ascii=False)


class UIServer:
    """The server. `engine` answers the API; `route` is src.ui.api.route."""

    def __init__(
        self,
        engine,
        route: Callable,
        port: int = 8777,
        host: str = "127.0.0.1",
        log: Optional[Callable[[str], None]] = None,
    ):
        if host != "127.0.0.1":
            # Constraint 4: never 0.0.0.0, never ::, never a LAN address.
            raise ValueError("The local UI binds 127.0.0.1 only.")
        self.engine = engine
        self.route = route
        self.token = secrets.token_urlsafe(32)
        self.log = log or (lambda line: print(line, file=sys.stderr, flush=True))
        self.httpd = ThreadingHTTPServer((host, port), _handler_for(self))
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.allowed_hosts = {f"127.0.0.1:{self.port}", f"localhost:{self.port}"}
        self.allowed_origins = {f"http://{h}" for h in self.allowed_hosts}

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    def serve_forever(self, poll_interval: float = 0.5) -> None:
        self.httpd.serve_forever(poll_interval=poll_interval)

    def shutdown(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()

    # -- egress ---------------------------------------------------------------

    def contains_key_material(self, text: str) -> bool:
        needles = self.engine.key_needles()
        return any(n and n in text for n in needles)


def _handler_for(server: UIServer):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"
        server_version = "PointsOptimizerUI"
        sys_version = ""

        # ---- plumbing -------------------------------------------------------

        def log_message(self, fmt, *args):  # noqa: D401 - silence the default
            return

        def _log(self, status: int) -> None:
            # Method, path WITHOUT the query string, status. Never a body.
            path = urlsplit(self.path).path
            server.log(f"{self.command} {path} {status}")

        def _send(self, status: int, body: bytes = b"", content_type: str = "",
                  api: bool = False) -> None:
            self.send_response(status)
            for name, value in SECURITY_HEADERS:
                self.send_header(name, value)
            self.send_header("Cache-Control", "no-store")
            if content_type:
                self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if body and self.command != "HEAD":
                self.wfile.write(body)
            self._log(status)

        def _send_text(self, status: int, text: str, content_type: str) -> None:
            if server.contains_key_material(text):
                server.log("REFUSED to send a response that contained key material.")
                text = dumps(EGRESS_REFUSAL)
                status, content_type = 500, "application/json; charset=utf-8"
            self._send(status, text.encode("utf-8"), content_type)

        def _send_json(self, status: int, payload) -> None:
            try:
                text = dumps(payload)
            except ValueError:
                traceback.print_exc()
                text = dumps({
                    "error": "internal",
                    "message": "Unexpected ValueError; see the terminal.",
                })
                status = 500
            self._send_text(status, text, "application/json; charset=utf-8")

        # ---- gates ----------------------------------------------------------

        def _host_ok(self) -> bool:
            host = (self.headers.get("Host") or "").strip().lower()
            return host in server.allowed_hosts

        def _token_ok(self) -> bool:
            given = self.headers.get("X-PO-Token") or ""
            return hmac.compare_digest(given.encode("utf-8"), server.token.encode("utf-8"))

        def _origin_ok(self) -> bool:
            return (self.headers.get("Origin") or "") in server.allowed_origins

        def _read_body(self) -> dict:
            ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if ctype != "application/json":
                raise ApiError(400, "bad_request", "POST bodies must be application/json.")
            raw_len = self.headers.get("Content-Length")
            try:
                length = int(raw_len or "")
            except ValueError:
                raise ApiError(400, "bad_request", "A POST needs a Content-Length.")
            if length < 0:
                raise ApiError(400, "bad_request", "A POST needs a Content-Length.")
            if length > MAX_BODY_BYTES:
                raise ApiError(413, "too_large", "The request body is larger than 64 KB.")
            return parse_json_object(self.rfile.read(length))

        # ---- dispatch -------------------------------------------------------

        def _handle(self, method: str) -> None:
            if not self._host_ok():
                self.close_connection = True
                self._send(421)
                return
            path = urlsplit(self.path).path
            if not path.startswith("/api/"):
                if method != "GET" or path not in STATIC_FILES:
                    self._send_json(404, {"error": "not_found", "message": "Not found."})
                    return
                name, ctype = STATIC_FILES[path]
                text = (STATIC_DIR / name).read_text(encoding="utf-8")
                if name == "index.html":
                    text = text.replace(TOKEN_PLACEHOLDER, server.token)
                self._send_text(200, text, ctype)
                return
            if not self._token_ok():
                self._send_json(403, STALE_PAGE)
                return
            if method == "POST" and not self._origin_ok():
                self._send_json(403, {
                    "error": "forbidden_origin",
                    "message": "This request did not come from this app's own page.",
                })
                return
            try:
                body = self._read_body() if method == "POST" else None
                status, payload = server.route(server.engine, method, path, body)
            except ApiError as e:
                self._send_json(e.status, e.payload)
                return
            except Exception as e:  # noqa: BLE001 - never a traceback to the browser
                traceback.print_exc()
                self._send_json(500, {
                    "error": "internal",
                    "message": f"Unexpected {type(e).__name__}; see the terminal.",
                })
                return
            self._send_json(status, payload)

        def do_GET(self):  # noqa: N802
            self._handle("GET")

        def do_POST(self):  # noqa: N802
            self._handle("POST")

        def _method_not_allowed(self):
            if not self._host_ok():
                self.close_connection = True
                self._send(421)
                return
            self._send_json(405, {"error": "method_not_allowed",
                                  "message": "Only GET and POST are accepted."})

        do_OPTIONS = _method_not_allowed  # noqa: N815
        do_PUT = _method_not_allowed  # noqa: N815
        do_DELETE = _method_not_allowed  # noqa: N815
        do_PATCH = _method_not_allowed  # noqa: N815
        do_HEAD = _method_not_allowed  # noqa: N815

    return Handler

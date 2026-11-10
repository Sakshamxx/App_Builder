"""SUPPLIED. Static preview server for generated apps (also used by the check worker).

Isolation layers provided here, each tested separately in ``tests/test_boundaries.py``:
  1. Only GET/HEAD, flat allow-listed files from one project's ``app/`` folder, no traversal.
  2. A Content-Security-Policy that blocks network access except the app's own origin.
  3. One browser origin per project (``p<id>.localhost:<port>``), so one project's localStorage is
     not readable by another project's page, and none of it shares an origin with the builder UI.
A preview iframe alone is not treated as isolation: the browser enforces (2) and (3), and the
harness adds request blocking on top (see harness_worker.py).

Hosted mode: the main app serves the same files at ``/preview/<project id>/<file>`` (server.py) using ``static_file`` below, so
rule (1) is shared. Layer (3) does not exist there (one origin for everything); the route instead forces a CSP ``sandbox`` so the
page always gets an opaque origin. ``PreviewServer`` is no longer started by the server; the check worker still uses
``serve_directory``.
"""
from __future__ import annotations

import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
         ".js": "text/javascript; charset=utf-8"}
HOST_RE = re.compile(r"^(p[0-9a-f]{20})\.localhost(:\d+)?$")
FILE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def static_file(root, name: str):
    """(path, content type) of one flat, allow-listed, non-symlink file inside ``root``; None for anything else."""
    if not isinstance(name, str) or "/" in name or "\\" in name or not FILE_RE.match(name) or name.startswith("."):
        return None
    ctype = TYPES.get(Path(name).suffix.lower())
    f = Path(root) / name
    if ctype is None or f.is_symlink() or not f.is_file():
        return None
    return f, ctype


def csp(frame_ancestors: str = "'none'") -> str:
    return ("default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; font-src 'self'; connect-src 'self'; form-action 'self'; base-uri 'self'; "
            f"object-src 'none'; frame-ancestors {frame_ancestors}")


def make_handler(resolve_root, with_csp: bool = True, frame_ancestors: str = "'none'"):
    """``resolve_root(host_header) -> Path | None`` maps a Host header to a project's app folder."""

    class Handler(BaseHTTPRequestHandler):
        server_version = "AppPreview"

        def log_message(self, *a):  # silence default logging
            pass

        def _serve(self, head: bool):
            root = resolve_root(self.headers.get("Host", ""))
            if root is None:
                return self._send(404, b"unknown project", "text/plain; charset=utf-8", head)
            path = unquote(urlsplit(self.path).path)
            if path in ("", "/"):
                path = "/index.html"
            hit = static_file(root, path[1:])
            if hit is None:
                return self._send(404, b"not found", "text/plain; charset=utf-8", head)
            self._send(200, hit[0].read_bytes(), hit[1], head)

        def _send(self, code, body, ctype, head):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            if with_csp:
                self.send_header("Content-Security-Policy", csp(frame_ancestors))
            self.end_headers()
            if not head:
                self.wfile.write(body)

        def do_GET(self):
            self._serve(False)

        def do_HEAD(self):
            self._serve(True)

        def _refuse(self):
            self._send(405, b"method not allowed", "text/plain; charset=utf-8", False)

        do_POST = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = _refuse

    return Handler


class _Server(ThreadingHTTPServer):
    daemon_threads = True


def serve_directory(root: Path, label: str = "check", with_csp: bool = True):
    """Serve one folder on a random localhost port. Returns (server, origin)."""
    root = Path(root).resolve()
    httpd = _Server(("127.0.0.1", 0), make_handler(lambda host: root, with_csp))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://{label}.localhost:{httpd.server_address[1]}"


class PreviewServer:
    """Serves every project's app folder, one origin per project."""

    def __init__(self, store, port: int, builder_origin: str = "'none'"):
        self.store, self.port = store, port

        def resolve(host: str):
            m = HOST_RE.match(host or "")
            if not m or not self.store.exists(m.group(1)):
                return None
            return self.store.app_dir(m.group(1))

        self._httpd = _Server(("127.0.0.1", port), make_handler(resolve, True, builder_origin))
        self.port = self._httpd.server_address[1]

    def start(self):
        threading.Thread(target=self._httpd.serve_forever, daemon=True).start()
        return self

    def stop(self):
        self._httpd.shutdown()
        self._httpd.server_close()

    def url(self, pid: str, page: str = "index.html") -> str:
        return f"http://{pid}.localhost:{self.port}/{page}"

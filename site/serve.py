"""Servidor estático mínimo da tela (só stdlib).

  uv run python site/serve.py                 # http://127.0.0.1:8000/
  uv run python site/serve.py --lan           # escuta em 0.0.0.0 (outro aparelho na mesma rede)
  uv run python site/serve.py --port 8001

- `data/live*/`: `Cache-Control: no-store` (contracts §6.1), com ETag para o front pedir 304 com If-None-Match.
- Demais arquivos: `no-cache` + ETag (o navegador revalida; em localhost custa ~nada).
- gzip para JSON/JS/CSS/HTML/TopoJSON quando o cliente aceita (cache em memória por (caminho, mtime)).
- MIME correto para .bin (application/octet-stream), .topojson/.geojson (application/json), .mjs.
- Nunca lista diretórios, nunca sai de `site/`.
"""

from __future__ import annotations

import argparse
import email.utils
import gzip
import http.server
import mimetypes
import os
import socketserver
import sys
import threading
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parent
MIME = {
    ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8", ".topojson": "application/json; charset=utf-8",
    ".geojson": "application/json; charset=utf-8", ".bin": "application/octet-stream",
    ".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon", ".txt": "text/plain; charset=utf-8",
    ".md": "text/plain; charset=utf-8", ".map": "application/json",
}
GZIP_EXT = {".json", ".topojson", ".geojson", ".js", ".mjs", ".css", ".html", ".svg", ".txt", ".md"}
_gz_cache: dict[tuple[str, int], bytes] = {}
GZ_CACHE_MAX = 64
_gz_lock = threading.Lock()


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "apuracao-site/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # silencioso, menos 4xx/5xx
        if args and len(args) > 1 and str(args[1])[:1] in "45":
            sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))

    def _resolve(self) -> Path | None:
        path = unquote(urlsplit(self.path).path)
        if path.endswith("/"):
            path += "index.html"
        # normpath (e não resolve) barra ".." sem seguir links: no Railway data/live é um link para o volume.
        p = Path(os.path.normpath(ROOT / path.lstrip("/")))
        try:
            p.relative_to(ROOT)
        except ValueError:
            return None
        return p if p.is_file() else None

    def do_HEAD(self):
        self._serve(head=True)

    def do_GET(self):
        self._serve(head=False)

    def _serve(self, head: bool):
        p = self._resolve()
        if p is None:
            body = b"not found"
            self.send_response(404)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if not head:
                self.wfile.write(body)
            return
        try:
            st = p.stat()
            rel = p.relative_to(ROOT).as_posix()
            live = rel.startswith("data/live")
            etag = f'"{st.st_mtime_ns:x}-{st.st_size:x}"'
            ctype = MIME.get(p.suffix.lower()) or mimetypes.guess_type(p.name)[0] or "application/octet-stream"
            inm = self.headers.get("If-None-Match")
            cache = "no-store" if live else "no-cache"
            if inm and inm == etag:
                self.send_response(304)
                self.send_header("ETag", etag)
                self.send_header("Cache-Control", cache)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            data = p.read_bytes()
            enc = None
            if p.suffix.lower() in GZIP_EXT and "gzip" in (self.headers.get("Accept-Encoding") or "") and len(data) > 1024:
                key = (str(p), st.st_mtime_ns)
                with _gz_lock:
                    gz = _gz_cache.get(key)
                if gz is None:
                    gz = gzip.compress(data, compresslevel=5 if live else 6)
                    with _gz_lock:
                        if len(_gz_cache) > GZ_CACHE_MAX:
                            _gz_cache.clear()
                        _gz_cache[key] = gz
                data, enc = gz, "gzip"
        except OSError:
            # arquivo sumiu no meio (rotação de versões do coletor): 404 para o front tentar de novo
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("ETag", etag)
        self.send_header("Last-Modified", email.utils.formatdate(st.st_mtime, usegmt=True))
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        if enc:
            self.send_header("Content-Encoding", enc)
            self.send_header("Vary", "Accept-Encoding")
        self.end_headers()
        if not head:
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8000)))
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--lan", action="store_true", help="escuta em 0.0.0.0")
    a = ap.parse_args(argv)
    host = "0.0.0.0" if a.lan else a.host
    srv = Server((host, a.port), Handler)
    print(f"servindo {ROOT} em http://{'127.0.0.1' if host == '0.0.0.0' else host}:{a.port}/  (host {host})", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

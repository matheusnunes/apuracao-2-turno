"""Servidor estático mínimo da tela (só stdlib).

  uv run python site/serve.py                 # http://127.0.0.1:8000/
  uv run python site/serve.py --lan           # escuta em 0.0.0.0 (outro aparelho na mesma rede)
  uv run python site/serve.py --port 8001

- `data/live*/`: `Cache-Control: no-store` (contracts §6.1), com ETag para o front pedir 304 com If-None-Match.
- Demais arquivos: `no-cache` + ETag (o navegador revalida; em localhost custa ~nada).
- gzip para JSON/JS/CSS/HTML/TopoJSON quando o cliente aceita (cache em memória por (caminho, mtime)).
- MIME correto para .bin (application/octet-stream), .topojson/.geojson (application/json), .mjs.
- Nunca lista diretórios, nunca sai de `site/`.
- `/logs/{uf}/{mun}/{zona}-{secao}.txt`: log da urna (2T, com o 1T da mesma urna), descompactado e em UTF-8. Lê
  `data/logs/raw/` (etl/logs_urna.py); se a urna ainda não foi baixada, busca no TSE pelo hash do estado do coletor
  e guarda.
"""

from __future__ import annotations

import argparse
import email.utils
import gzip
import http.server
import mimetypes
import os
import re
import socketserver
import sqlite3
import sys
import threading
import urllib.request
import zipfile
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parent
LOGS_RAW = ROOT.parent / "data/logs/raw"
COLLECTOR_STATE = ROOT.parent / "data/live/state-live-e6258-p3221.sqlite"
TSE_URNA = "https://resultados.tse.jus.br/oficial/ele2026/arquivo-urna/3221/dados"
LOG_RE = re.compile(r"^/logs/([a-z]{2})/(\d{5})/(\d{4})-(\d{4})\.txt$")


def urna_log(uf: str, mun: str, z: str, s: str) -> bytes | None:
    """Texto (UTF-8) do log da urna da seção, ou None."""
    p = LOGS_RAW / uf / mun / f"{z}-{s}.jez"
    if not p.is_file():
        try:
            db = sqlite3.connect(f"file:{COLLECTOR_STATE}?mode=ro", uri=True)
            row = db.execute("SELECT hash, nm FROM sec WHERE key=?", (f"{uf}-{mun}-{z}-{s}",)).fetchone()
            db.close()
        except sqlite3.Error:
            row = None
        if not row or not row[0] or not str(row[1]).endswith("-bu.dat"):
            return None
        url = f"{TSE_URNA}/{uf}/{mun}/{z}/{s}/{row[0]}/{row[1][:-len('-bu.dat')]}-log.jez"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "apuracao-sala-de-guerra/1.0 (uso pessoal)"})
            with urllib.request.urlopen(req, timeout=30) as r:
                body = r.read()
        except OSError:
            return None
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_bytes(body)
        tmp.replace(p)
    try:
        zf = zipfile.ZipFile(p)
        return "".join(zf.read(n).decode("latin-1") for n in zf.namelist()).encode("utf-8")
    except (OSError, zipfile.BadZipFile):
        return None
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
        m = LOG_RE.match(urlsplit(self.path).path)
        if m:
            return self._serve_log(m.groups(), head)
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


    def _serve_log(self, parts, head: bool):
        data = urna_log(*parts)
        if data is None:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return
        gz = "gzip" in (self.headers.get("Accept-Encoding") or "")
        if gz:
            data = gzip.compress(data, compresslevel=5)
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "max-age=86400")
        self.send_header("X-Content-Type-Options", "nosniff")
        if gz:
            self.send_header("Content-Encoding", "gzip")
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

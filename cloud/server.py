"""Servidor do espelho online (Railway). Serve a tela como site/serve.py e recebe do Mac os arquivos de data/.

    SYNC_TOKEN=... python cloud/server.py        # porta em $PORT (padrão 8080)

- `PUT /_sync/data/{base,live}/<arquivo>` grava o arquivo (Authorization: Bearer $SYNC_TOKEN, X-Sha256 do conteúdo,
  corpo opcionalmente em gzip). O manifest é o último a chegar, como no coletor.
- `GET /_sync/inventory` lista o que o servidor tem (sha256 por arquivo) e um id de boot: o disco é efêmero,
  então depois de um redeploy o Mac vê o id novo e reenvia tudo.
- Versionados (`agg.N.json`, `sections.N.bin`): fica só os 3 enviados por último (o coletor pode recomeçar do 1).
- Não serve código (.py, tools/) e pede para não indexar.
"""

from __future__ import annotations

import gzip
import hashlib
import hmac
import json
import os
import re
import secrets
import sys
import threading
from pathlib import Path
from urllib.parse import unquote, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "site"))
import serve  # noqa: E402

KEEP_VERSIONS = 3
MAX_BODY = 128 << 20
SAFE = re.compile(r"^data/(base|live)/(?!.*\.\.)[A-Za-z0-9._/-]+$")
VERSIONED = re.compile(r"^(agg|sections)\.\d+\.(json|bin)$")
BOOT = secrets.token_hex(6)
_inv: dict[str, str] = {}  # caminho relativo a site/ → sha256
_inv_lock = threading.Lock()
serve.GZIP_EXT.add(".bin")  # sections.bin (~16 MB) cai para poucos MB
serve.GZ_CACHE_MAX = 512


class Handler(serve.Handler):
    server_version = "apuracao-espelho/1.0"

    def end_headers(self):
        self.send_header("X-Robots-Tag", "noindex, nofollow")
        super().end_headers()

    def _resolve(self):
        path = unquote(urlsplit(self.path).path)
        if path.startswith(("/_sync", "/tools/")) or path.endswith(".py"):
            return None
        return super()._resolve()

    def _reply(self, code: int, body: bytes = b"", ctype: str = "text/plain; charset=utf-8") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Boot-Id", BOOT)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _authorized(self) -> bool:
        token = os.environ.get("SYNC_TOKEN", "")
        got = self.headers.get("Authorization", "")
        return bool(token) and hmac.compare_digest(got.encode(), f"Bearer {token}".encode())

    def do_GET(self):
        if urlsplit(self.path).path == "/_sync/inventory":
            if not self._authorized():
                return self._reply(401, b"unauthorized")
            with _inv_lock:
                body = json.dumps({"boot": BOOT, "files": dict(_inv)}).encode()
            return self._reply(200, body, "application/json")
        super().do_GET()

    def do_PUT(self):
        path = unquote(urlsplit(self.path).path)
        if not path.startswith("/_sync/"):
            return self._reply(404, b"not found")
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n) if 0 < n <= MAX_BODY else b""  # lê o corpo antes de responder (keep-alive)
        if not self._authorized():
            return self._reply(401, b"unauthorized")
        rel = path[len("/_sync/"):]
        if not SAFE.match(rel) or not body:
            return self._reply(400, b"bad path or body")
        gz = body if self.headers.get("Content-Encoding") == "gzip" else None
        try:
            data = gzip.decompress(gz) if gz else body
        except OSError:
            return self._reply(400, b"bad gzip")
        sha = hashlib.sha256(data).hexdigest()
        if sha != self.headers.get("X-Sha256"):
            return self._reply(400, b"sha256 mismatch")
        dest = serve.ROOT / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(f".{dest.name}.{secrets.token_hex(4)}.tmp")
        tmp.write_bytes(data)
        os.replace(tmp, dest)
        self._prime_gzip(dest, data, gz, live=rel.startswith("data/live"))
        with _inv_lock:
            _inv[rel] = sha
        if VERSIONED.match(dest.name):
            self._prune(dest)
        self._reply(204)

    def _prime_gzip(self, dest: Path, data: bytes, gz: bytes | None, live: bool) -> None:
        """Deixa o gzip pronto para o primeiro leitor (vários aparelhos pedem o mesmo arquivo juntos)."""
        if dest.suffix.lower() not in serve.GZIP_EXT or len(data) <= 1024:
            return
        key = (str(dest.resolve()), dest.stat().st_mtime_ns)
        gz = gz or gzip.compress(data, compresslevel=5 if live else 6)
        with serve._gz_lock:
            for k in [k for k in serve._gz_cache if k[0] == key[0]]:
                del serve._gz_cache[k]
            serve._gz_cache[key] = gz

    def _prune(self, dest: Path) -> None:
        stem, ext = dest.name.split(".")[0], dest.suffix
        olds = sorted((p for p in dest.parent.glob(f"{stem}.*{ext}") if VERSIONED.match(p.name)),
                      key=lambda p: p.stat().st_mtime_ns)[:-KEEP_VERSIONS]
        for p in olds:
            p.unlink(missing_ok=True)
            with _inv_lock:
                _inv.pop(p.relative_to(serve.ROOT).as_posix(), None)


def main() -> int:
    if not os.environ.get("SYNC_TOKEN"):
        print("aviso: SYNC_TOKEN vazio; o envio do Mac vai ser recusado", flush=True)
    (serve.ROOT / "data" / "live").mkdir(parents=True, exist_ok=True)
    port = int(os.environ.get("PORT", 8080))
    srv = serve.Server(("0.0.0.0", port), Handler)
    print(f"espelho servindo {serve.ROOT} na porta {port} (boot {BOOT})", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

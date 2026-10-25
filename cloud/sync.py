"""Envia a tela do Mac para o espelho online (filho do sala.py no modo live, se data/cloud.json existir).

    uv run python -m cloud.sync [--sections-every 30]

data/cloud.json (fora do git): {"url": "https://….up.railway.app", "token": "<SYNC_TOKEN do Railway>"}

- site/data/base: uma vez (e de novo se o servidor reiniciar, pelo id de boot do inventário).
- site/data/live: agg e analytics sempre que mudam; sections.bin (~16 MB) no máximo a cada --sections-every s,
  com o manifest enviado apontando para o último sections que o servidor tem. Manifest por último.
- Nunca derruba a noite local: erro de rede só atrasa o espelho (o sala.py reinicia se o processo cair).
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site"
CFG = ROOT / "data" / "cloud.json"
STATUS = ROOT / "data" / "live" / "logs" / "cloud.status.json"
INVENTORY_EVERY_S = 30


def log(msg: str) -> None:
    print(f"[cloud {datetime.now():%H:%M:%S}] {msg}", flush=True)


class Mirror:
    def __init__(self, url: str, token: str, site: Path = SITE, sections_every: float = 30.0,
                 status: Path | None = STATUS, transport: httpx.BaseTransport | None = None):
        self.site, self.base, self.live = site, site / "data" / "base", site / "data" / "live"
        self.sections_every, self.status = sections_every, status
        self.c = httpx.Client(base_url=url.rstrip("/"), timeout=90, transport=transport,
                              headers={"Authorization": f"Bearer {token}", "User-Agent": "apuracao-2t-espelho/1.0"})
        self.remote: dict[str, str] = {}
        self.boot: str | None = None
        self.inv_at = float("-inf")
        self.hashes: dict[Path, tuple[int, int, str]] = {}  # caminho → (mtime_ns, size, sha256)
        self.sec_sent: tuple[str, int, int] | None = None  # (nome, mtime_ns, size) do último sections enviado
        self.sec_dv = None
        self.sec_at = float("-inf")
        self.man_version = None
        self.sent_bytes = 0

    # --- servidor ---
    def inventory(self, now: float) -> None:
        r = self.c.get("/_sync/inventory")
        r.raise_for_status()
        d = r.json()
        if d["boot"] != self.boot:
            if self.boot is not None:
                log("servidor reiniciou: reenviando base e arquivos ao vivo")
            self.sec_sent = None
        self.boot, self.remote, self.inv_at = d["boot"], dict(d["files"]), now

    def put(self, rel: str, data: bytes, sha: str | None = None) -> bool:
        sha = sha or hashlib.sha256(data).hexdigest()
        if self.remote.get(rel) == sha:
            return False
        headers = {"X-Sha256": sha}
        body = data
        if len(data) > 1024:
            body = gzip.compress(data, compresslevel=5)
            headers["Content-Encoding"] = "gzip"
        r = self.c.put(f"/_sync/{rel}", content=body, headers=headers)
        r.raise_for_status()
        if r.headers.get("X-Boot-Id") not in (None, self.boot):
            self.inv_at = float("-inf")  # servidor novo no meio do caminho: inventário de novo no próximo ciclo
        self.remote[rel] = sha
        self.sent_bytes += len(body)
        return True

    def put_file(self, p: Path) -> bool:
        rel, st = self._rel(p), p.stat()
        hit = self.hashes.get(p)
        if hit and hit[:2] == (st.st_mtime_ns, st.st_size) and self.remote.get(rel) == hit[2]:
            return False
        data = p.read_bytes()
        sha = hashlib.sha256(data).hexdigest()
        self.hashes[p] = (st.st_mtime_ns, st.st_size, sha)
        return self.put(rel, data, sha)

    def _rel(self, p: Path) -> str:
        return p.relative_to(self.site).as_posix()

    # --- ciclo ---
    def sync_base(self) -> int:
        n = 0
        for p in sorted(self.base.rglob("*")):
            if p.is_file() and not p.name.startswith("."):
                n += self.put_file(p)
        return n

    def sync_live(self, now: float) -> bool:
        """Envia o que o manifest atual pede e depois o manifest. True se o manifest foi enviado."""
        try:
            raw = (self.live / "manifest.json").read_bytes()
            m = json.loads(raw)
        except (OSError, ValueError):
            return False
        if self.man_version is not None and m.get("version", 0) < self.man_version:
            log(f"coletor recomeçou (manifest v{self.man_version} → v{m.get('version')}): sections de novo")
            self.sec_sent = None
        files = m.get("files") or {}
        try:
            for key in ("agg", "analytics"):
                if files.get(key):
                    self.put_file(self.live / files[key])
            sec = files.get("sections")
            if sec:
                st = (self.live / sec).stat()
                cur = (sec, st.st_mtime_ns, st.st_size)
                if cur != self.sec_sent and (self.sec_sent is None or now - self.sec_at >= self.sections_every):
                    self.put_file(self.live / sec)
                    self.sec_sent, self.sec_dv, self.sec_at = cur, (m.get("data_versions") or {}).get("sections"), now
                if self.sec_sent and self.sec_sent[0] != sec:
                    m["files"]["sections"] = self.sec_sent[0]
                    m.setdefault("data_versions", {})["sections"] = self.sec_dv
                    raw = json.dumps(m, ensure_ascii=False, indent=1).encode()
        except FileNotFoundError:
            return False  # o coletor rodou as versões no meio da leitura; o próximo ciclo pega
        self.put("data/live/manifest.json", raw)
        self.man_version = m.get("version")
        return True

    def tick(self, now: float) -> bool:
        if now - self.inv_at >= INVENTORY_EVERY_S:
            self.inventory(now)
            n = self.sync_base()
            if n:
                log(f"base: {n} arquivos enviados")
        return self.sync_live(now)

    def write_status(self, ok_at: str | None, err: str | None) -> None:
        if self.status is None:
            return
        try:
            self.status.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.status.with_suffix(".tmp")
            tmp.write_text(json.dumps({"ok_at": ok_at, "err": err, "url": str(self.c.base_url),
                                       "manifest_version": self.man_version}))
            tmp.replace(self.status)
        except OSError:
            pass


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sections-every", type=float, default=30.0,
                    help="intervalo mínimo entre envios do sections.bin (s); agg e analytics vão sempre")
    ap.add_argument("--interval", type=float, default=2.0)
    a = ap.parse_args()
    cfg = json.loads(CFG.read_text())
    mirror = Mirror(cfg["url"], cfg["token"], sections_every=a.sections_every)
    log(f"espelhando para {cfg['url']}")
    backoff, last_log, ok_at = a.interval, 0.0, None
    while True:
        now = time.monotonic()
        try:
            if mirror.tick(now):
                ok_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            mirror.write_status(ok_at, None)
            backoff = a.interval
            if now - last_log >= 60:
                log(f"ok · manifest v{mirror.man_version} · {mirror.sent_bytes / 1e6:.1f} MB enviados no último minuto")
                mirror.sent_bytes, last_log = 0, now
        except (httpx.HTTPError, OSError, ValueError, KeyError) as e:
            err = f"{type(e).__name__}: {e}"[:200]
            log(f"erro: {err}; tento de novo em {backoff:.0f}s")
            mirror.write_status(ok_at, err)
            mirror.inv_at = float("-inf")
            time.sleep(backoff)
            backoff = min(backoff * 2, 30)
            continue
        time.sleep(a.interval)


if __name__ == "__main__":
    main()

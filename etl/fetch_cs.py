"""Baixa a configuração de seções do 2T (cs.json por UF) e o mun-cm.json.

Uso: uv run python -m etl.fetch_cs [--force]

- Salva em data/raw/cs_2t/{uf}-p003221-cs.json (+ .meta.json com ETag/Last-Modified).
- Requisições condicionais (If-None-Match) quando já existe cópia; no máximo 4 em paralelo.
- HTTP/1.1 (o CDN do TSE já derrubou conexões HTTP/2).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time

import httpx
import orjson

from .common import CM_URL, CS_2T_DIR, CS_URL, ELEICAO_2T, PLEITO_2T, UFS

UA = "apuracao-2t-sala-de-guerra/0.1 (uso local)"


def cs_path(uf: str):
    return CS_2T_DIR / f"{uf}-p{int(PLEITO_2T):06d}-cs.json"


def cm_path():
    return CS_2T_DIR / f"mun-e{int(ELEICAO_2T):06d}-cm.json"


def _atomic_write(path, data: bytes):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


async def _fetch_one(client: httpx.AsyncClient, sem: asyncio.Semaphore, url: str, path, force: bool):
    meta_path = path.with_suffix(".meta.json")
    headers = {}
    if not force and path.exists() and meta_path.exists():
        meta = orjson.loads(meta_path.read_bytes())
        if meta.get("etag"):
            headers["If-None-Match"] = meta["etag"]
    async with sem:
        for attempt in range(4):
            try:
                r = await client.get(url, headers=headers)
                break
            except httpx.HTTPError as e:  # noqa: PERF203
                if attempt == 3:
                    raise
                await asyncio.sleep(1.5 * (attempt + 1))
                print(f"  retry {url}: {e!r}", file=sys.stderr)
        await asyncio.sleep(0.15)  # educado com o CDN
    if r.status_code == 304:
        return path.name, "304 (sem mudança)", False
    r.raise_for_status()
    body = r.content
    orjson.loads(body)  # valida JSON antes de gravar
    _atomic_write(path, body)
    meta = {
        "url": url,
        "etag": r.headers.get("etag"),
        "last_modified": r.headers.get("last-modified"),
        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "bytes": len(body),
    }
    _atomic_write(meta_path, orjson.dumps(meta, option=orjson.OPT_INDENT_2))
    return path.name, f"200 {len(body):,} B (LM {meta['last_modified']})", True


async def fetch_all(force: bool = False) -> bool:
    CS_2T_DIR.mkdir(parents=True, exist_ok=True)
    pl6 = f"{int(PLEITO_2T):06d}"
    jobs = [(CS_URL.format(pl=PLEITO_2T, uf=uf, pl6=pl6), cs_path(uf)) for uf in UFS]
    jobs.append((CM_URL.format(ele=ELEICAO_2T, ele6=f"{int(ELEICAO_2T):06d}"), cm_path()))
    sem = asyncio.Semaphore(4)
    limits = httpx.Limits(max_connections=4, max_keepalive_connections=4)
    async with httpx.AsyncClient(
        http2=False, timeout=60, limits=limits, headers={"User-Agent": UA}, follow_redirects=True
    ) as client:
        res = await asyncio.gather(*[_fetch_one(client, sem, u, p, force) for u, p in jobs])
    changed = False
    for name, status, ch in res:
        print(f"  {name}: {status}")
        changed |= ch
    return changed


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="ignora ETag e baixa tudo de novo")
    a = ap.parse_args(argv)
    asyncio.run(fetch_all(a.force))


if __name__ == "__main__":
    main()

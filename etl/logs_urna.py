"""Logs de urna do 2T 2026 -> horário de cada voto, por seção, nos dois turnos.

O `-log.jez` de cada urna do 2T (zip com `logd.dat`) traz o histórico da urna desde a carga. Quando a urna é a
mesma do 1º turno, isso inclui o dia do 1T (04/10). Daqui sai, por turno:
- abertura ("Urna pronta para receber votos");
- encerramento ("Operador selecionou: Encerrar votação" / "Procedimento de encerramento confirmado");
- resposta a "Todas as pessoas presentes já votaram?";
- o horário de cada voto computado e a duração de cada voto (habilitação -> voto computado).

Os horários são os do relógio da urna, ou seja, hora local. A conversão para Brasília fica em etl/prorrogacao.py.

O bruto fica guardado em data/logs/raw/{uf}/{mun}/{zona}-{secao}.jez (~175 KB por urna, ~87 GB no total), para a
tela mostrar o log da urna selecionada (`--no-raw` não guarda). É retomável: o que já está na tabela `log` é pulado. A ordem é embaralhada (semente fixa), então qualquer parcial é uma amostra aleatória.

    uv run python -m etl.logs_urna --rate 40 --concurrency 24
    uv run python -m etl.logs_urna --limit 200          # teste rápido
"""

from __future__ import annotations

import argparse
import asyncio
import io
import logging
import random
import sqlite3
import time
import zipfile
import zlib
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import httpx
import numpy as np

from collector.config import USER_AGENT

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "data/live/state-live-e6258-p3221.sqlite"
OUT = ROOT / "data/logs/logs-2t.sqlite"
RAW = ROOT / "data/logs/raw"
BASE = "https://resultados.tse.jus.br/oficial/ele2026/arquivo-urna/3221/dados"
DAYS = {"04/10/2026": 1, "25/10/2026": 2}

log = logging.getLogger("logs_urna")

SCHEMA = """
CREATE TABLE IF NOT EXISTS log(
  key TEXT PRIMARY KEY, status TEXT, size INTEGER, nfiles INTEGER, model TEXT,
  t1_open INTEGER, t1_sel INTEGER, t1_close INTEGER, t1_allp INTEGER, t1_n INTEGER, t1_id TEXT, t1_v BLOB, t1_d BLOB,
  t2_open INTEGER, t2_sel INTEGER, t2_close INTEGER, t2_allp INTEGER, t2_n INTEGER, t2_id TEXT, t2_v BLOB, t2_d BLOB,
  err TEXT, t REAL);
"""
COLS = ("key", "status", "size", "nfiles", "model",
        "t1_open", "t1_sel", "t1_close", "t1_allp", "t1_n", "t1_id", "t1_v", "t1_d",
        "t2_open", "t2_sel", "t2_close", "t2_allp", "t2_n", "t2_id", "t2_v", "t2_d", "err", "t")


def pack(a: list[int], dtype) -> bytes:
    return zlib.compress(np.asarray(a, dtype=dtype).tobytes(), 6)


def unpack(b: bytes | None, dtype) -> np.ndarray:
    return np.frombuffer(zlib.decompress(b), dtype=dtype) if b else np.zeros(0, dtype=dtype)


def raw_path(key: str, root: Path = RAW) -> Path:
    uf, mun, z, s = key.split("-")
    return root / uf / mun / f"{z}-{s}.jez"


def save_and_parse(data: bytes, path: str | None) -> dict:
    """Roda num processo do pool: grava o bruto (se pedido) e resume."""
    if path:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        tmp = Path(path + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(path)
    return parse_jez(data)


def parse_jez(data: bytes) -> dict:
    """Bytes do `-log.jez` -> resumo por turno."""
    zf = zipfile.ZipFile(io.BytesIO(data))
    names = zf.namelist()
    st = {t: {"open": None, "sel": None, "close": None, "allp": None, "id": {}, "hab": None, "v": [], "d": []}
          for t in (1, 2)}
    model = ""
    for name in names:
        text = zf.read(name).decode("latin-1")
        for line in text.split("\n"):
            t = DAYS.get(line[:10])
            if t is None:
                continue
            p = line.split("\t")
            if len(p) < 5:
                continue
            msg = p[4]
            try:
                sec = int(line[11:13]) * 3600 + int(line[14:16]) * 60 + int(line[17:19])
            except ValueError:
                continue
            s = st[t]
            if msg == "O voto do eleitor foi computado":
                s["v"].append(sec)
                s["d"].append(min(sec - s["hab"], 65535) if s["hab"] is not None and sec >= s["hab"] else 65535)
                s["hab"] = None
            elif msg == "Eleitor foi habilitado":
                s["hab"] = sec
            elif msg == "Urna pronta para receber votos":
                if s["open"] is None:
                    s["open"] = sec
            elif msg == "Operador selecionou: Encerrar votação":
                s["sel"] = sec
            elif msg == "Procedimento de encerramento confirmado":
                s["close"] = sec
            elif msg.startswith("Todas as pessoas presentes já votaram?"):
                s["allp"] = 1 if msg.rstrip().endswith("SIM") else 0
            elif msg.startswith(("Município: ", "Zona Eleitoral: ", "Seção Eleitoral: ")):
                k, _, v = msg.partition(": ")
                s["id"][k[0]] = v.strip()
            elif msg.startswith("Identificação do Modelo de Urna: ") and not model:
                model = msg.split(": ", 1)[1].strip()
    out = {"nfiles": len(names), "model": model}
    for t in (1, 2):
        s = st[t]
        order = np.argsort(np.asarray(s["v"], dtype=np.int64), kind="stable")
        v = [s["v"][i] for i in order]
        d = [s["d"][i] for i in order]
        ident = s["id"]
        out[t] = {
            "open": s["open"], "sel": s["sel"], "close": s["close"], "allp": s["allp"], "n": len(v),
            "id": f"{ident.get('M', '')}-{ident.get('Z', '')}-{ident.get('S', '')}" if ident else None,
            "v": pack(v, np.uint32) if v else None, "d": pack(d, np.uint16) if d else None,
        }
    return out


def row_from(key: str, status: str, size: int = 0, res: dict | None = None, err: str | None = None) -> tuple:
    r = {"key": key, "status": status, "size": size, "err": err, "t": time.time()}
    if res:
        r.update(nfiles=res["nfiles"], model=res["model"])
        for t in (1, 2):
            for k, v in res[t].items():
                r[f"t{t}_{k}"] = v
    return tuple(r.get(c) for c in COLS)


class Bucket:
    def __init__(self, rate: float):
        self.dt = 1.0 / rate
        self.next = time.monotonic()
        self.lock = asyncio.Lock()

    async def wait(self):
        async with self.lock:
            now = time.monotonic()
            if self.next > now:
                await asyncio.sleep(self.next - now)
            self.next = max(self.next, now) + self.dt


async def run(a):
    OUT.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(a.db)
    db.executescript(SCHEMA)
    done = {k for (k,) in db.execute("SELECT key FROM log WHERE status IN ('ok','404','sa')")}
    src = sqlite3.connect(f"file:{a.state}?mode=ro", uri=True)
    q = "SELECT key, uf, mun, zona, secao, hash, nm FROM sec WHERE has_bu=1 AND hash IS NOT NULL"
    todo = [r for r in src.execute(q) if r[0] not in done and (not a.ufs or r[1] in a.ufs)]
    random.Random(2026).shuffle(todo)
    if a.limit:
        todo = todo[: a.limit]
    log.info("já feitos %d; a fazer %d", len(done), len(todo))

    pool = ProcessPoolExecutor(max_workers=a.workers)
    loop = asyncio.get_running_loop()
    bucket = Bucket(a.rate)
    queue: asyncio.Queue = asyncio.Queue()
    for r in todo:
        queue.put_nowait(r)
    rows: list[tuple] = []
    stats = {"ok": 0, "404": 0, "sa": 0, "err": 0, "bytes": 0, "retry": 0}
    t0 = time.monotonic()

    def flush():
        if rows:
            db.executemany(f"INSERT OR REPLACE INTO log({','.join(COLS)}) VALUES ({','.join('?' * len(COLS))})", rows)
            db.commit()
            rows.clear()

    async def worker(client: httpx.AsyncClient):
        while True:
            try:
                key, uf, mun, z, s, h, nm = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            if not nm.endswith("-bu.dat"):
                rows.append(row_from(key, "sa"))
                stats["sa"] += 1
                continue
            url = f"{BASE}/{uf}/{mun}/{z}/{s}/{h}/{nm[:-len('-bu.dat')]}-log.jez"
            for attempt in range(6):
                await bucket.wait()
                try:
                    r = await client.get(url)
                except httpx.HTTPError as e:
                    err = repr(e)[:200]
                else:
                    if r.status_code == 200:
                        body = r.content
                        stats["bytes"] += len(body)
                        try:
                            res = await loop.run_in_executor(pool, save_and_parse, body,
                                                             None if a.no_raw else str(raw_path(key)))
                            rows.append(row_from(key, "ok", len(body), res))
                            stats["ok"] += 1
                        except Exception as e:  # noqa: BLE001
                            rows.append(row_from(key, "err", len(body), err=repr(e)[:200]))
                            stats["err"] += 1
                        break
                    if r.status_code == 404:
                        rows.append(row_from(key, "404"))
                        stats["404"] += 1
                        break
                    err = f"HTTP {r.status_code}"
                stats["retry"] += 1
                await asyncio.sleep(min(60, 2 ** attempt + random.random()))
            else:
                rows.append(row_from(key, "err", err=err))
                stats["err"] += 1
            if len(rows) >= 500:
                flush()

    async def reporter():
        while True:
            await asyncio.sleep(30)
            el = time.monotonic() - t0
            n = stats["ok"] + stats["404"] + stats["err"] + stats["sa"]
            left = len(todo) - n
            rate = n / el if el else 0
            log.info("%d/%d (%.1f/s, %.1f MB/s) ok=%d 404=%d sa=%d err=%d retry=%d; falta ~%.0f min",
                     n, len(todo), rate, stats["bytes"] / el / 1e6, stats["ok"], stats["404"], stats["sa"],
                     stats["err"], stats["retry"], left / rate / 60 if rate else -1)
            flush()

    limits = httpx.Limits(max_connections=a.concurrency, max_keepalive_connections=a.concurrency)
    async with httpx.AsyncClient(http2=True, timeout=60, limits=limits,
                                 headers={"User-Agent": USER_AGENT}) as client:
        rep = asyncio.create_task(reporter())
        await asyncio.gather(*(worker(client) for _ in range(a.concurrency)))
        rep.cancel()
    flush()
    pool.shutdown()
    log.info("fim: %s em %.0f s", stats, time.monotonic() - t0)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--rate", type=float, default=40.0, help="teto de requisições por segundo")
    p.add_argument("--concurrency", type=int, default=24)
    p.add_argument("--workers", type=int, default=6, help="processos de parse")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--no-raw", action="store_true", help="não guarda o .jez bruto")
    p.add_argument("--ufs", nargs="*", default=None)
    p.add_argument("--state", type=Path, default=STATE)
    p.add_argument("--db", type=Path, default=OUT)
    a = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    asyncio.run(run(a))


if __name__ == "__main__":
    main()

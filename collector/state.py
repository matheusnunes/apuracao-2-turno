"""Estado durável em SQLite (WAL): validadores HTTP, agregados, seções, agenda de sondagem.

Escritas são acumuladas em memória e gravadas numa transação a cada ~2 s
(`flush`). Validador HTTP e o estado derivado do mesmo arquivo entram no mesmo
lote, então um 304 após reinício sempre corresponde ao estado salvo.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from pathlib import Path

import orjson

log = logging.getLogger("collector.state")

# status da seção
S_NEW, S_AUX404, S_NOTOT, S_BU404, S_DONE, S_FAIL, S_ERR = 0, 1, 2, 3, 4, 5, 6
STATUS_NAMES = {S_NEW: "nova", S_AUX404: "aux404", S_NOTOT: "sem_totalizado", S_BU404: "bu404",
                S_DONE: "ok", S_FAIL: "falha_decod", S_ERR: "erro_rede"}

SEC_COLS = ("key", "uf", "mun", "zona", "secao", "status", "cs_dh", "aux_dh", "hash", "nm",
            "dr", "hr", "probes", "next_probe", "has_bu", "aptos", "comp", "l", "o", "b", "n",
            "t_recv", "err", "t")

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS http(url TEXT PRIMARY KEY, etag TEXT, lm TEXT, t REAL);
CREATE TABLE IF NOT EXISTS agg(scope TEXT NOT NULL, code TEXT NOT NULL, data TEXT, t REAL,
                               PRIMARY KEY(scope, code));
CREATE TABLE IF NOT EXISTS sec(
  key TEXT PRIMARY KEY, uf TEXT, mun TEXT, zona TEXT, secao TEXT,
  status INTEGER DEFAULT 0, cs_dh TEXT, aux_dh TEXT, hash TEXT, nm TEXT, dr TEXT, hr TEXT,
  probes INTEGER DEFAULT 0, next_probe REAL DEFAULT 0, has_bu INTEGER DEFAULT 0,
  aptos INTEGER, comp INTEGER, l INTEGER, o INTEGER, b INTEGER, n INTEGER, t_recv INTEGER,
  err TEXT, t REAL);
CREATE INDEX IF NOT EXISTS sec_uf ON sec(uf);
"""


class State:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.db = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db.executescript(SCHEMA)
        self._meta: dict[str, str] = {}
        self._http: dict[str, tuple] = {}
        self._agg: dict[tuple, str | None] = {}
        self._sec: dict[str, tuple] = {}

    # ---------- leitura ----------
    def get_meta(self, k: str, default=None):
        r = self.db.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
        return r[0] if r else default

    def load_validators(self) -> dict:
        return {u: (e, l) for u, e, l in self.db.execute("SELECT url, etag, lm FROM http")}

    def load_agg(self) -> dict:
        out: dict = {}
        for scope, code, data in self.db.execute("SELECT scope, code, data FROM agg"):
            if data is not None:
                out.setdefault(scope, {})[code] = orjson.loads(data)
        return out

    def load_sections(self):
        cur = self.db.execute(f"SELECT {', '.join(SEC_COLS)} FROM sec")
        while True:
            rows = cur.fetchmany(20000)
            if not rows:
                break
            yield from rows

    def section_ufs(self) -> set[str]:
        return {r[0] for r in self.db.execute("SELECT DISTINCT uf FROM sec")}

    # ---------- escrita (bufferizada) ----------
    def put_meta(self, k: str, v):
        self._meta[k] = str(v)

    def put_validator(self, url: str, etag, lm):
        self._http[url] = (etag, lm, time.time())

    def put_agg(self, scope: str, code: str, data: dict | None):
        self._agg[(scope, code)] = None if data is None else orjson.dumps(data).decode()

    def put_sec(self, row: tuple):
        self._sec[row[0]] = row

    def insert_universe(self, rows: list[tuple]):
        """rows: (key, uf, mun, zona, secao). Ignora as já existentes. Imediato (não bufferizado)."""
        self.db.execute("BEGIN")
        try:
            self.db.executemany(
                "INSERT OR IGNORE INTO sec(key, uf, mun, zona, secao, status, probes, next_probe, has_bu)"
                " VALUES (?,?,?,?,?,0,0,0,0)", rows)
            self.db.execute("COMMIT")
        except Exception:
            self.db.execute("ROLLBACK")
            raise

    def pending(self) -> int:
        return len(self._meta) + len(self._http) + len(self._agg) + len(self._sec)

    def flush(self):
        if not self.pending():
            return
        meta, http, agg, sec = self._meta, self._http, self._agg, self._sec
        self._meta, self._http, self._agg, self._sec = {}, {}, {}, {}
        try:
            self.db.execute("BEGIN")
            if sec:
                self.db.executemany(
                    f"INSERT OR REPLACE INTO sec({', '.join(SEC_COLS)}) VALUES ({','.join('?' * len(SEC_COLS))})",
                    list(sec.values()))
            if agg:
                self.db.executemany("INSERT OR REPLACE INTO agg(scope, code, data, t) VALUES (?,?,?,?)",
                                    [(s, c, d, time.time()) for (s, c), d in agg.items()])
            if http:
                self.db.executemany("INSERT OR REPLACE INTO http(url, etag, lm, t) VALUES (?,?,?,?)",
                                    [(u, e, l, t) for u, (e, l, t) in http.items()])
            if meta:
                self.db.executemany("INSERT OR REPLACE INTO meta(k, v) VALUES (?,?)", list(meta.items()))
            self.db.execute("COMMIT")
        except Exception:
            try:
                self.db.execute("ROLLBACK")
            except Exception:  # noqa: BLE001
                pass
            # devolve ao buffer para a próxima tentativa (sem sobrescrever o mais novo)
            for k, v in meta.items():
                self._meta.setdefault(k, v)
            for k, v in http.items():
                self._http.setdefault(k, v)
            for k, v in agg.items():
                self._agg.setdefault(k, v)
            for k, v in sec.items():
                self._sec.setdefault(k, v)
            raise

    def close(self):
        try:
            self.flush()
        finally:
            self.db.close()

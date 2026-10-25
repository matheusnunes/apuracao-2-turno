"""Orquestrador do coletor: pista AGG (espinha dorsal), pista BU (amostra), publicação.

Pista AGG (contracts §6): br u+ab a cada 30 s; as UF ab (+ UF u) a cada 60 s,
e na hora para UFs que o br-ab acusou; u.json de município/zona só para os
municípios cujo `st` mudou, em ordem de Δst × aptos. Nada da pista BU bloqueia
ou atrasa a AGG: clientes, baldes de tokens e conexões são separados.

Pista BU: descobre seções com BU (A: cs.json ganhando da/ha; B: sondagem de aux
nas zonas cujo st > BUs conhecidos, no máx. (st − conhecidos) × 1,5 seções por
zona, na ordem de chegada do 1T), baixa aux → BU, decodifica e preenche sections.bin.
"""

from __future__ import annotations

import asyncio
import csv
import datetime as dt
import hashlib
import logging
import math
import random
import signal
import time
from collections import Counter, deque
from email.utils import parsedate_to_datetime
from pathlib import Path

import numpy as np
import orjson

from . import tse
from .bu import BUDecodeError, decode_bu
from .config import BRT, UF_ORDER, Config
from .model import Mun, RRQueue, Sec, UFState, Zone
from .net import (P_AGG, P_AUX, P_BR, P_BU, P_CS, P_PROBE, P_UF, Budget, BudgetExhausted, Gate,
                  Lane)
from .publish import atomic_write, latest_version, prune_versions, versioned_name
from .state import (S_AUX404, S_BU404, S_DONE, S_ERR, S_FAIL, S_NEW, S_NOTOT, STATUS_NAMES, State)

log = logging.getLogger("collector")

N_COLS = 8


def iso(t: float | None) -> str | None:
    if t is None:
        return None
    return dt.datetime.fromtimestamp(t, BRT).isoformat(timespec="seconds")


def http_date(s: str | None) -> float | None:
    if not s:
        return None
    try:
        return parsedate_to_datetime(s).timestamp()
    except (TypeError, ValueError):
        return None


def a_from_ab(e: dict) -> dict:
    return {"ts": e["ts"], "st": e["st"], "te": e["te"], "est": e["est"], "c": e["c"], "a": e["a"],
            "vb": 0, "tvn": 0, "l": 0, "o": 0, "dt": e.get("dt", ""), "ht": e.get("ht", ""),
            "and": e.get("and", "n")}


def complete(A: dict | None) -> bool:
    return bool(A) and A["ts"] > 0 and A["st"] >= A["ts"]


class Engine:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        cfg.state_dir.mkdir(parents=True, exist_ok=True)
        cfg.out_dir.mkdir(parents=True, exist_ok=True)
        if cfg.reset:
            for suf in ("", "-wal", "-shm"):
                p = Path(str(cfg.db_path) + suf)
                if p.exists():
                    p.unlink()
            log.warning("estado apagado (--reset): %s", cfg.db_path)
        self.state = State(cfg.db_path)
        self.validators = self.state.load_validators()
        self.budget = Budget(cfg.request_budget)
        sr = cfg.start_rate
        self.agg = Lane("agg", cfg, Gate("agg", cfg.agg_concurrency, cfg.agg_rate, min_rate=min(2.0, cfg.agg_rate),
                                         start_rate=sr or cfg.agg_rate * 0.75, hard_policy="escalate"),
                        self.budget, self.validators, self.state.put_validator, retries=cfg.retries)
        self.bu = Lane("bu", cfg, Gate("bu", cfg.bu_concurrency, cfg.bu_rate, min_rate=0.5,
                                       start_rate=sr or cfg.bu_rate * 0.5, hard_policy="fixed600"),
                       self.budget, self.validators, self.state.put_validator, retries=2)
        mi = min(12.0, 0.8 * min(cfg.l0_interval, cfg.mun_min_interval, cfg.uf_interval, cfg.zone_min_interval))
        self.agg.min_url_interval = self.bu.min_url_interval = mi
        self.ufs: dict[str, UFState] = {uf: UFState(uf) for uf in cfg.ufs}
        self.muns: dict[tuple, Mun] = {}
        self.zones: dict[tuple, Zone] = {}
        self.secs: dict[str, Sec] = {}
        self.sec_list: list[Sec] = []
        self.res = np.zeros((1024, N_COLS), dtype=np.int32)
        self.res[:, 7] = -1
        self.br_A: dict | None = None
        self.br_meta: dict = {}
        self.br_last_ok: float | None = None
        self._br_content_key = None          # (idg, dg, hg) do arquivo BR
        self.br_content_at = time.time()     # última mudança de conteúdo do BR (início = agora)
        self.tse_last_change: float | None = None
        self.fmt = "jws" if cfg.fmt == "jws" else "json"
        self.jws_ahead = 0
        self.known_q = RRQueue()
        self.probe_q = RRQueue()
        self.want_known: set[Sec] = set()
        self.need_muns: set[Mun] = set()
        self.need_zones: set[Zone] = set()
        self.decode_fail: Counter = Counter()
        self.bu_times: deque = deque()
        self.n_bu = 0
        self.probe_tokens = 0.0
        self._probe_refill = time.monotonic()
        self.res_dirty = True
        self.version = int(self.state.get_meta("version", "0"))
        try:  # nunca voltar a versão do manifest (o front usa ?v=version)
            mf = cfg.out_dir / "manifest.json"
            if mf.exists():
                self.version = max(self.version, int(orjson.loads(mf.read_bytes()).get("version", 0)))
        except Exception:  # noqa: BLE001
            pass
        self.dv_agg = max(int(self.state.get_meta("dv_agg", "0")), latest_version(cfg.out_dir, "agg"))
        self.dv_sec = max(int(self.state.get_meta("dv_sec", "0")), latest_version(cfg.out_dir, "sections"))
        self._last_agg_hash = None
        self._pending_lat: list[tuple[float, float | None]] = []   # (need_since, last_modified)
        self.lat_hist: deque = deque()   # (t_publish, lat_need_s, lat_tse_s)
        self.index_keys: list[str] = []
        self.index_rows: list[tuple] = []
        self.index_map: dict[str, int] = {}
        self.index_sha = None
        self.index_n = 0
        self.index_src = "none"
        self.out_idx = np.zeros(0, dtype=np.int64)
        self.prior: dict[str, int] = {}
        self.aptos1: dict[str, int] = {}
        self.stop: asyncio.Event | None = None
        self.work_event: asyncio.Event | None = None
        self.agg_event: asyncio.Event | None = None
        self._last_pub = 0.0
        self._last_sec_pub = 0.0
        self._bg: set[asyncio.Task] = set()
        self._cs_sem: asyncio.Semaphore | None = None
        self._planner_cursor = 0
        self.started = time.time()
        self.published_once_done = False
        self._strategy_logged: set[str] = set()

    # ================================================================ carga
    def load_index(self):
        p = self.cfg.index_path
        if not p.exists():
            log.warning("índice base ausente (%s): sections.bin usará índice derivado do cs", p)
            return
        raw = p.read_bytes()
        self.index_sha = hashlib.sha256(raw).hexdigest()
        rows = []
        keys = []
        rd = csv.reader(raw.decode().splitlines())
        header = next(rd)
        assert header[:5] == ["idx", "uf", "cd_mun", "zona", "secao"], header
        for i, r in enumerate(rd):
            idx, uf, mun, z, s = int(r[0]), r[1], int(r[2]), int(r[3]), int(r[4])
            if idx != i:
                raise ValueError(f"índice fora de ordem na linha {i}")
            m5, z4, s4 = f"{mun:05d}", f"{z:04d}", f"{s:04d}"
            keys.append(f"{uf}-{m5}-{z4}-{s4}")
            rows.append((uf, m5, z4, s4))
        self.index_keys = keys
        self.index_rows = rows
        self.index_map = {k: i for i, k in enumerate(keys)}
        self.index_n = len(keys)
        self.index_src = "base"
        log.info("índice base: %d seções, sha256=%s", self.index_n, self.index_sha[:12])

    def load_prior(self):
        p = self.cfg.base_secoes_path
        if not p.exists():
            log.info("sem %s: sondagem B na ordem das seções", p)
            return
        try:
            import pyarrow.parquet as pq

            t = pq.read_table(p, columns=["uf", "cd_mun", "zona", "secao", "t_tot1", "aptos1"])
            d = t.to_pydict()
            for uf, m, z, s, tt, ap in zip(d["uf"], d["cd_mun"], d["zona"], d["secao"], d["t_tot1"], d["aptos1"]):
                k = f"{uf}-{m:05d}-{z:04d}-{s:04d}"
                self.prior[k] = int(tt) if tt is not None else 2 ** 40
                self.aptos1[k] = int(ap or 0)
            log.info("ordem de chegada do 1T carregada para %d seções", len(self.prior))
        except Exception:  # noqa: BLE001
            log.exception("falha lendo %s (seguindo sem prior)", p)

    def in_scope_mun(self, mun5: str) -> bool:
        return self.cfg.muns is None or mun5 in self.cfg.muns

    def get_mun(self, uf: str, mun5: str) -> Mun:
        k = (uf, mun5)
        m = self.muns.get(k)
        if m is None:
            m = self.muns[k] = Mun(uf, mun5)
            self.ufs[uf].muns[mun5] = m
        return m

    def get_zone(self, uf: str, mun5: str, z4: str) -> Zone:
        k = (uf, mun5, z4)
        z = self.zones.get(k)
        if z is None:
            z = self.zones[k] = Zone(uf, mun5, z4)
            m = self.get_mun(uf, mun5)
            z.mun = m
            m.zones.append(z)
            m.zones.sort(key=lambda x: int(x.z4))
        return z

    def add_sec(self, key, uf, mun5, z4, s4) -> Sec:
        sid = len(self.sec_list)
        sec = Sec(sid, key, uf, mun5, z4, s4)
        sec.prior = self.prior.get(key, 2 ** 40 + int(s4))
        z = self.get_zone(uf, mun5, z4)
        sec.zone = z
        z.secs.append(sec)
        z.aptos1 += self.aptos1.get(key, 0)
        z.mun.aptos1 += self.aptos1.get(key, 0)
        self.secs[key] = sec
        self.sec_list.append(sec)
        if sid >= len(self.res):
            grow = np.zeros((max(1024, len(self.res)), N_COLS), dtype=np.int32)
            grow[:, 7] = -1
            self.res = np.concatenate([self.res, grow])
        return sec

    def _finish_zone_lists(self, zones):
        for z in zones:
            z.secs.sort(key=lambda s: int(s.secao))
            z.uncaptured = sorted((s for s in z.secs if not s.has_bu), key=lambda s: (s.prior, int(s.secao)))

    def load_structure(self):
        """Estrutura município→zonas: do cm.json persistido; senão do índice base."""
        raw = self.state.get_meta("cm_struct")
        if raw:
            self._apply_cm(orjson.loads(raw))
            return True
        if self.index_rows:
            seen = set()
            for uf, m5, z4, _ in self.index_rows:
                if uf in self.ufs and self.in_scope_mun(m5) and (uf, m5, z4) not in seen:
                    seen.add((uf, m5, z4))
                    self.get_zone(uf, m5, z4)
            log.info("estrutura município/zona provisória a partir do índice base")
            return False
        return False

    def _apply_cm(self, struct: dict):
        for uf, muns in struct.items():
            if uf not in self.ufs:
                continue
            for m5, zs in muns.items():
                if not self.in_scope_mun(m5):
                    continue
                for z4 in zs:
                    self.get_zone(uf, m5, z4)

    def restore(self):
        t0 = time.time()
        n = 0
        touched = set()
        for row in self.state.load_sections():
            (key, uf, mun, zona, secao, status, cs_dh, aux_dh, hash_, nm, dr, hr, probes, next_probe,
             has_bu, aptos, comp, l, o, b, nn, t_recv, err, t) = row
            if uf not in self.ufs or not self.in_scope_mun(mun):
                continue
            sec = self.add_sec(key, uf, mun, zona, secao)
            sec.status, sec.cs_dh, sec.aux_dh, sec.hash, sec.nm = status, cs_dh, aux_dh, hash_, nm
            sec.dr, sec.hr, sec.probes, sec.next_probe = dr, hr, probes or 0, next_probe or 0.0
            sec.has_bu, sec.err, sec.t = has_bu or 0, err, t or 0.0
            if sec.has_bu:
                self.res[sec.sid] = (1, aptos or 0, comp or 0, l or 0, o or 0, b or 0, nn or 0,
                                     -1 if t_recv is None else t_recv)
                sec.zone.captured += 1
                self.n_bu += 1
            if status == S_FAIL:
                self.decode_fail[(err or "?").split(":")[0]] += 1
            if cs_dh and cs_dh != aux_dh and self.cfg.strategy != "zonas":
                sec.want_known = True
                self.want_known.add(sec)
            touched.add(uf)
            n += 1
        for uf in touched:
            self.ufs[uf].universe = True
        self._finish_zone_lists(self.zones.values())
        aggs = self.state.load_agg()
        br = aggs.get("br", {}).get("br")
        if br:
            self.br_A, self.br_meta = br["A"], br["meta"]
            self._br_content_key = (self.br_meta.get("idg"), self.br_meta.get("dg"), self.br_meta.get("hg"))
        for uf, e in aggs.get("ab_br", {}).items():
            if uf in self.ufs:
                self.ufs[uf].br_entry = e
        for uf, e in aggs.get("ab_uf", {}).items():
            if uf in self.ufs:
                self.ufs[uf].ab_self = e
        for uf, A in aggs.get("uf", {}).items():
            if uf in self.ufs:
                self.ufs[uf].A = A
        for code, e in aggs.get("ab_mun", {}).items():
            uf, m5 = code.split(":")
            if uf in self.ufs and self.in_scope_mun(m5):
                self.get_mun(uf, m5).ab = e
        for code, A in aggs.get("mun", {}).items():
            uf, m5 = code.split(":")
            if uf in self.ufs and self.in_scope_mun(m5):
                m = self.get_mun(uf, m5)
                m.A = A
        for code, A in aggs.get("zona", {}).items():
            uf, m5, z4 = code.split(":")
            if uf in self.ufs and self.in_scope_mun(m5):
                self.get_zone(uf, m5, z4).A = A
        for m in self.muns.values():
            m.out_A = m.A or (a_from_ab(m.ab) if m.ab and m.ab["st"] == 0 else None)
            if m.ab:
                self.on_mun_ab(m, restoring=True)
        tlc = self.state.get_meta("tse_last_change")
        self.tse_last_change = float(tlc) if tlc else None
        cm = self.state.get_meta("cs_state")
        if cm:
            for uf, v in orjson.loads(cm).items():
                if uf in self.ufs:
                    self.ufs[uf].cs_dh_count = v.get("n", 0)
                    self.ufs[uf].cs_changed_at = v.get("t", 0.0)
        log.info("estado restaurado: %d seções (%d com BU), %d municípios, %d zonas, %.1fs",
                 n, self.n_bu, len(self.muns), len(self.zones), time.time() - t0)

    # ================================================================ util
    def spawn(self, coro, name: str):
        t = asyncio.get_running_loop().create_task(self._safe(coro, name), name=name)
        self._bg.add(t)
        t.add_done_callback(self._bg.discard)
        return t

    async def _safe(self, coro, name):
        try:
            await coro
        except BudgetExhausted:
            log.warning("orçamento de requisições esgotado (%s): encerrando", name)
            self.stop.set()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("tarefa %s falhou", name)

    async def _guard(self, name, fn):
        while not self.stop.is_set():
            try:
                await fn()
                return
            except asyncio.CancelledError:
                raise
            except BudgetExhausted:
                log.warning("orçamento de requisições esgotado (%s): encerrando", name)
                self.stop.set()
                return
            except Exception:  # noqa: BLE001
                log.exception("laço %s caiu; reiniciando em 5 s", name)
                await asyncio.sleep(5)

    async def _sleep(self, seconds: float):
        try:
            await asyncio.wait_for(self.stop.wait(), timeout=max(0.0, seconds))
        except asyncio.TimeoutError:
            pass

    def mark_tse_change(self):
        self.tse_last_change = time.time()
        self.state.put_meta("tse_last_change", self.tse_last_change)
        self.agg_changed()

    def agg_changed(self):
        if self.agg_event is not None:
            self.agg_event.set()

    def ext(self) -> str:
        return self.fmt

    def election_active(self) -> bool:
        if self.br_A and self.br_A["st"] > 0:
            return True
        return any((u.br_entry and u.br_entry["st"] > 0) or (u.ab_self and u.ab_self["st"] > 0)
                   for u in self.ufs.values())

    def uf_complete(self, u: UFState) -> bool:
        e = u.ab_self or u.br_entry
        return bool(e) and e["ts"] > 0 and e["st"] >= e["ts"] and complete(u.A)

    async def get_dados(self, url_fn, *args, prio, kind):
        """GET condicional de arquivo de `dados/` (json ou jws), com recuo para .json se o .jws faltar."""
        ext = self.ext()
        r = await self.agg.get(url_fn(*args, ext=ext), prio=prio, kind=kind, conditional=True)
        if r.status == 404 and ext == "jws":
            r = await self.agg.get(url_fn(*args, ext="json"), prio=prio, kind=kind, conditional=True)
        return r

    # ================================================================ pista AGG
    async def load_cm(self):
        """cm.json (municípios → zonas) pela pista AGG. Persistido para reinícios com 304."""
        url = f"{self.cfg.base_url}/{self.cfg.ciclo}/{self.cfg.eleicao}/config/mun-e{self.cfg.ele6}-cm.json"
        have = self.state.get_meta("cm_struct") is not None
        r = await self.agg.get(url, prio=P_UF, kind="cm", conditional=have, save_validator=False)
        if r.status == 200:
            d = tse.loads(r.body)
            struct: dict = {}
            for ab in d.get("abr") or []:
                uf = str(ab.get("cd", "")).lower()
                struct[uf] = {f"{int(m['cd']):05d}": [f"{int(z):04d}" for z in (m.get("z") or [])]
                              for m in ab.get("mu") or []}
            self.state.put_meta("cm_struct", orjson.dumps(struct).decode())
            self.agg.commit_validator(url, r.etag, r.lm)
            self._apply_cm(struct)
            n = sum(len(v) for k, v in struct.items() if k in self.ufs)
            log.info("cm.json: %d municípios no escopo, %d zonas", n, len(self.zones))
            return True
        if r.status == 304:
            return True
        log.warning("cm.json indisponível (%s)", r.status)
        return have

    async def agg_l0_loop(self):
        while not self.stop.is_set():
            t0 = time.monotonic()
            await asyncio.gather(self.poll_br_u(), self.poll_br_ab())
            await self._sleep(t0 + self.cfg.l0_interval - time.monotonic())

    async def poll_br_u(self):
        r = await self.get_dados(self.cfg.url_br_u, prio=P_BR, kind="br_u")
        if r.status in (200, 304):
            self.br_last_ok = time.time()
        if r.status == 200:
            A, meta = tse.parse_u(tse.loads(r.body), self.cfg.cand_l, self.cfg.cand_o)
            key = (meta.get("idg"), meta.get("dg"), meta.get("hg"))
            if key != self._br_content_key:
                self._br_content_key = key
                self.br_content_at = time.time()
            if A != self.br_A or meta.get("idg") != self.br_meta.get("idg"):
                if self.br_A is not None and A != self.br_A:
                    self.mark_tse_change()
                else:
                    self.agg_changed()
                self.br_A, self.br_meta = A, meta
                self.state.put_agg("br", "br", {"A": A, "meta": meta})

    async def poll_br_ab(self):
        r = await self.get_dados(self.cfg.url_br_ab, prio=P_BR, kind="br_ab")
        if r.status != 200:
            return
        entries, _ = tse.parse_ab(tse.loads(r.body))
        for e in entries:
            u = self.ufs.get(e["cdabr"])
            if u is None or e["tpabr"] != "uf":
                continue
            if u.br_entry is None or tse.ab_sig(e) != tse.ab_sig(u.br_entry):
                if u.br_entry is not None:
                    self.mark_tse_change()
                u.br_entry = e
                u.ab_dirty = True
                self.state.put_agg("ab_br", u.uf, e)

    async def agg_uf_loop(self):
        while not self.stop.is_set():
            now = time.monotonic()
            for u in self.ufs.values():
                interval = self.cfg.uf_interval * (5 if self.uf_complete(u) else 1)
                if not u.ab_inflight and now >= u.ab_retry_at and (
                        (u.ab_dirty and now - u.ab_last >= self.cfg.mun_min_interval) or now - u.ab_last >= interval):
                    u.ab_inflight = True
                    self.spawn(self.poll_uf_ab(u), f"uf_ab:{u.uf}")
                newest = u.ab_self if (u.ab_self and (not u.br_entry or u.ab_self["st"] >= u.br_entry["st"])) else u.br_entry
                need_u = newest is not None and newest["st"] > 0 and (
                    u.A is None or (u.A["st"], u.A["c"]) != (newest["st"], newest["c"]))
                if not u.u_inflight and now >= u.u_retry_at and (
                        (need_u and now - u.u_last >= self.cfg.mun_min_interval) or now - u.u_last >= interval):
                    u.u_inflight = True
                    self.spawn(self.poll_uf_u(u), f"uf_u:{u.uf}")
            await self._sleep(1.0)

    async def poll_uf_ab(self, u: UFState):
        try:
            r = await self.get_dados(self.cfg.url_uf_ab, u.uf, prio=P_UF, kind="uf_ab")
            if r.status in (200, 304):
                u.ab_last = time.monotonic()
                u.ab_fail = 0
            elif r.status != -1:
                u.ab_fail += 1
                u.ab_retry_at = time.monotonic() + self._fail_backoff(u.ab_fail, r, base=15.0, cap=120.0)
            if r.status == 200:
                entries, _ = tse.parse_ab(tse.loads(r.body))
                for e in entries:
                    if e["tpabr"] == "uf":
                        if u.ab_self is None or tse.ab_sig(e) != tse.ab_sig(u.ab_self):
                            if u.ab_self is not None:
                                self.mark_tse_change()
                            u.ab_self = e
                            self.state.put_agg("ab_uf", u.uf, e)
                        continue
                    try:
                        m5 = f"{int(e['cdabr']):05d}"
                    except ValueError:
                        continue
                    m = u.muns.get(m5)
                    if m is None:
                        continue
                    if m.ab is None or tse.ab_sig(e) != tse.ab_sig(m.ab):
                        if m.ab is not None:
                            self.mark_tse_change()
                        m.ab = e
                        self.state.put_agg("ab_mun", f"{u.uf}:{m5}", e)
                        self.on_mun_ab(m)
            if r.status in (200, 304):
                u.ab_dirty = bool(u.br_entry and u.ab_self and u.ab_self["st"] < u.br_entry["st"])
        finally:
            u.ab_inflight = False

    async def poll_uf_u(self, u: UFState):
        try:
            r = await self.get_dados(self.cfg.url_uf_u, u.uf, prio=P_UF, kind="uf_u")
            if r.status in (200, 304):
                u.u_last = time.monotonic()
                u.u_fail = 0
            elif r.status != -1:
                u.u_fail += 1
                u.u_retry_at = time.monotonic() + self._fail_backoff(u.u_fail, r, base=15.0, cap=120.0)
            if r.status == 200:
                A, _ = tse.parse_u(tse.loads(r.body), self.cfg.cand_l, self.cfg.cand_o)
                if A != u.A:
                    if u.A is not None:
                        self.mark_tse_change()
                    else:
                        self.agg_changed()
                    u.A = A
                    self.state.put_agg("uf", u.uf, A)
        finally:
            u.u_inflight = False

    def on_mun_ab(self, m: Mun, restoring: bool = False):
        e = m.ab
        if e["st"] == 0 and m.A is None:
            m.out_A = a_from_ab(e)
            self._clear_need(m)
            return
        if m.A is None or (m.A["st"], m.A["c"]) != (e["st"], e["c"]):
            reset = False
            if m.A is not None and e["st"] < m.A["st"]:
                if e["st"] > 0.5 * m.A["st"]:
                    return              # ab um pouco atrás do u.json: cache de CDN, nada a fazer
                reset = True            # contador despencou (mock reiniciado/retotalização): refaz tudo
                m.resetting = True
            now = time.time()
            if m.need_since is None:
                m.need_since = now
            m.unsynced = 0
            self.need_muns.add(m)
            if m.multi:
                for z in m.zones:
                    if reset or not complete(z.A):
                        if z.need_since is None:
                            z.need_since = now
                        self.need_zones.add(z)

    def _clear_need(self, m: Mun):
        m.need_since = None
        self.need_muns.discard(m)

    def _score(self, m: Mun) -> float:
        e = m.ab
        if not e:
            return 0.0
        dst = e["st"] - (m.A["st"] if m.A else 0)
        return max(dst, 1) * (e["te"] / max(e["ts"], 1))

    def _zone_score(self, z: Zone) -> float:
        """Estimativa de seções novas na zona: (st do município − Σ st das zonas conhecidas) / zonas incompletas."""
        m = z.mun
        e = m.ab
        if not e:
            return 1.0
        known = sum(x.A["st"] for x in m.zones if x.A)
        incomplete = sum(1 for x in m.zones if not complete(x.A)) or 1
        return max(1.0, (e["st"] - known) / incomplete) * (e["te"] / max(e["ts"], 1))

    @staticmethod
    def _fail_backoff(n: int, r, base: float = 30.0, cap: float = 300.0) -> float:
        b = min(cap, base * 2 ** max(0, n - 1))
        if r is not None and r.retry_after:
            b = max(b, r.retry_after)
        return b * random.uniform(1.0, 1.2)

    def select_fetches(self, budget: int) -> list[tuple[int, object]]:
        """Escolhe o lote de u.json de município/zona: prioridade Δst × aptos com envelhecimento
        (× (1 + idade/30 s)) e uma fatia mínima (`zone_share`) reservada às zonas, para que
        zonas de capitais multizona não fiquem eternamente atrás dos municípios."""
        if budget <= 0:
            return []
        now = time.monotonic()
        wall = time.time()

        def aged(base, since):
            return base * (1.0 + max(0.0, wall - since) / 30.0) if since else base

        muns = [(aged(self._score(m), m.need_since), 0, m) for m in self.need_muns
                if not m.inflight and now >= m.retry_at and now - m.last_fetch >= self.cfg.mun_min_interval]
        zones = [(aged(self._zone_score(z), z.need_since), 1, z) for z in self.need_zones
                 if not z.inflight and now >= z.retry_at and now - z.last_fetch >= self.cfg.zone_min_interval]
        # 1) fatia para os mais antigos (FIFO por need_since): limita a espera máxima mesmo sob carga
        chosen: list = []
        taken: set = set()
        n_old = min(len(muns) + len(zones), math.ceil(budget * self.cfg.oldest_share)) if (muns or zones) else 0
        if n_old:
            for x in sorted(muns + zones, key=lambda x: x[2].need_since or wall)[:n_old]:
                chosen.append(x)
                taken.add(id(x[2]))
        muns = [x for x in muns if id(x[2]) not in taken]
        zones = [x for x in zones if id(x[2]) not in taken]
        muns.sort(key=lambda x: -x[0])
        zones.sort(key=lambda x: -x[0])
        rest = budget - len(chosen)
        # 2) fatia mínima para zonas; 3) o resto por prioridade envelhecida
        z_chosen = sum(1 for x in chosen if x[1] == 1)
        nz_min = min(len(zones), max(0, math.ceil(budget * self.cfg.zone_share) - z_chosen, 1 if not z_chosen else 0)) \
            if (zones and rest > 0) else 0
        nm = min(len(muns), max(0, rest - nz_min))
        nz = min(len(zones), max(0, rest - nm))
        batch = chosen + muns[:nm] + zones[:nz]
        batch.sort(key=lambda x: -x[0])
        return [(k, o) for _, k, o in batch]

    async def agg_fetch_loop(self):
        last_check = 0.0
        while not self.stop.is_set():
            now = time.monotonic()
            budget = max(0, 8 * self.agg.gate.concurrency - self.agg.gate.waiting() - self.agg.gate.in_flight)
            for kind, obj in self.select_fetches(budget):
                obj.inflight = True
                if kind == 0:
                    self.spawn(self.fetch_mun(obj), "mun_u")
                else:
                    self.spawn(self.fetch_zone(obj), "zona_u")
            if now - last_check > 10:
                last_check = now
                self._zone_consistency()
            await self._sleep(0.5)

    def _zone_consistency(self):
        """Município multizona: Σ st das zonas < st do município => alguma zona ficou velha (CDN)."""
        t = time.time()
        for m in self.muns.values():
            if not m.multi or not m.A or m in self.need_muns:
                continue
            if any(z.A is None for z in m.zones) or sum(z.A["st"] for z in m.zones) < m.A["st"]:
                mono = time.monotonic()
                for z in m.zones:
                    if not complete(z.A) and z not in self.need_zones and mono >= z.gone_until:
                        z.need_since = z.need_since or t
                        self.need_zones.add(z)

    async def fetch_mun(self, m: Mun):
        try:
            r = await self.get_dados(self.cfg.url_mun_u, m.uf, m.mun5, prio=P_AGG, kind="mun_u")
            if r.status == -1:
                return
            m.last_fetch = time.monotonic()
            if r.status == 200:
                A, _ = tse.parse_u(tse.loads(r.body), self.cfg.cand_l, self.cfg.cand_o)
                if A != m.A:
                    m.A = A
                    m.out_A = A
                    self.state.put_agg("mun", f"{m.uf}:{m.mun5}", A)
                    self.agg_changed()
                    self._pending_lat.append((m.need_since or time.time(), http_date(r.lm)))
            if r.status in (200, 304):
                m.fetch_fail = 0
                e = m.ab
                if e is None or (m.A and ((m.A["st"], m.A["c"]) == (e["st"], e["c"])
                                          or (m.A["st"] > e["st"] and not m.resetting))):
                    m.resetting = False
                    self._clear_need(m)
                else:
                    m.unsynced += 1
                    if m.unsynced >= 8:
                        log.warning("município %s %s: u.json não alcança o ab após %d buscas; aceitando",
                                    m.uf, m.mun5, m.unsynced)
                        self._clear_need(m)
            elif r.status == 404:
                # 404 permanente: sai da lista de pendentes (métrica honesta); volta no próximo Δst
                m.unsynced += 1
                m.retry_at = time.monotonic() + self._fail_backoff(m.unsynced, r, base=self.cfg.fail_backoff)
                if m.unsynced >= 3:
                    log.warning("município %s %s: 404 %d vezes; fora dos pendentes até o próximo Δst",
                                m.uf, m.mun5, m.unsynced)
                    self._clear_need(m)
            else:
                # 403/429/5xx/rede: recua SÓ esta URL (30 s, 60 s, ... teto 5 min) e continua pendente
                # até conseguir: nunca fica "mudou e nunca rebuscado" calado.
                m.fetch_fail += 1
                m.retry_at = time.monotonic() + self._fail_backoff(m.fetch_fail, r, base=self.cfg.fail_backoff)
                if m.fetch_fail in (3, 10, 30):
                    log.warning("município %s %s: %s %d vezes seguidas; seguindo com recuo",
                                m.uf, m.mun5, r.status or r.error, m.fetch_fail)
        finally:
            m.inflight = False

    async def fetch_zone(self, z: Zone):
        try:
            r = await self.get_dados(self.cfg.url_zona_u, z.uf, z.mun5, z.z4, prio=P_AGG, kind="zona_u")
            if r.status == -1:
                return
            z.last_fetch = time.monotonic()
            if r.status == 200:
                A, _ = tse.parse_u(tse.loads(r.body), self.cfg.cand_l, self.cfg.cand_o)
                if A != z.A:
                    z.A = A
                    self.state.put_agg("zona", f"{z.uf}:{z.mun5}:{z.z4}", A)
                    self.agg_changed()
                    self._pending_lat.append((z.need_since or time.time(), http_date(r.lm)))
            if r.status in (200, 304):
                z.need_since = None
                self.need_zones.discard(z)
                z.fetch_fail = 0
                z.gone_until = 0.0
            elif r.status == 404:
                # 404 permanente: fora dos pendentes e da checagem de consistência por 10 min
                z.fetch_fail += 1
                z.retry_at = time.monotonic() + self._fail_backoff(z.fetch_fail, r, base=self.cfg.fail_backoff)
                if z.fetch_fail >= 3:
                    log.warning("zona %s %s %s: 404 %d vezes; fora dos pendentes por 10 min",
                                z.uf, z.mun5, z.z4, z.fetch_fail)
                    z.fetch_fail = 0
                    z.gone_until = time.monotonic() + 600
                    z.need_since = None
                    self.need_zones.discard(z)
            else:
                # 403/429/5xx/rede: recua SÓ esta URL e continua pendente até conseguir
                z.fetch_fail += 1
                z.retry_at = time.monotonic() + self._fail_backoff(z.fetch_fail, r, base=self.cfg.fail_backoff)
                if z.fetch_fail in (3, 10, 30):
                    log.warning("zona %s %s %s: %s %d vezes seguidas; seguindo com recuo",
                                z.uf, z.mun5, z.z4, r.status or r.error, z.fetch_fail)
        finally:
            z.inflight = False

    async def jws_loop(self):
        """Se o .json do BR parar enquanto o .jws avança, troca tudo de dados/ para .jws."""
        while not self.stop.is_set():
            await self._sleep(120)
            if self.cfg.fmt != "auto" or self.fmt == "jws" or not self.election_active() or not self.br_meta:
                continue
            r = await self.agg.get(self.cfg.url_br_u(ext="jws"), prio=P_BR, kind="jws_check", conditional=True)
            if r.status != 200:
                continue
            try:
                _, meta = tse.parse_u(tse.loads(r.body), self.cfg.cand_l, self.cfg.cand_o)
                ahead = int(meta.get("idg") or 0) > int(self.br_meta.get("idg") or 0)
            except Exception:  # noqa: BLE001
                continue
            stale = self.tse_last_change is None or time.time() - self.tse_last_change > 90
            self.jws_ahead = self.jws_ahead + 1 if (ahead and stale) else 0
            if self.jws_ahead >= 2:
                self.fmt = "jws"
                log.warning("BR .json parado e .jws à frente (idg %s > %s): trocando para .jws",
                            meta.get("idg"), self.br_meta.get("idg"))

    # ================================================================ pista BU
    def a_active(self, u: UFState) -> bool:
        return self.cfg.strategy != "zonas" and u.cs_dh_count > 0

    def b_active(self, u: UFState) -> bool:
        if self.cfg.strategy == "zonas":
            return True
        if self.cfg.strategy == "cs":
            return False
        return not (self.a_active(u) and time.time() - u.cs_changed_at < self.cfg.cs_trust_window)

    def strategy_label(self) -> str:
        act = [u for u in self.ufs.values() if self.a_active(u) and not self.b_active(u)]
        return "cs" if act else "zonas"

    async def cs_loop(self):
        self._cs_sem = asyncio.Semaphore(2)
        while not self.stop.is_set():
            now = time.monotonic()
            active = self.election_active()
            for u in self.ufs.values():
                if u.cs_inflight:
                    continue
                if not u.universe:
                    interval = 30.0
                elif active and not self.uf_complete(u):
                    interval = self.cfg.cs_interval
                else:
                    interval = self.cfg.cs_idle_interval
                if now - u.cs_last >= interval:
                    u.cs_inflight = True
                    self.spawn(self.poll_cs(u), f"cs:{u.uf}")
            await self._sleep(5.0)

    async def poll_cs(self, u: UFState):
        try:
            async with self._cs_sem:
                url = self.cfg.url_cs(u.uf)
                need_body = not u.universe
                r = await self.bu.get(url, prio=P_CS, kind="cs", conditional=not need_body,
                                      save_validator=False)
                u.cs_last = time.monotonic()
                if r.status == 200:
                    rows = await asyncio.to_thread(lambda: list(tse.parse_cs(tse.loads(r.body))))
                    if not u.universe:
                        self.build_universe(u, [(m, z, s) for m, z, s, _, p in rows if p])
                    self.apply_cs(u, rows, http_date(r.lm))
                    self.bu.commit_validator(url, r.etag, r.lm)
                    u.cs_fail = 0
                elif r.status != 304 and r.status != -1:
                    u.cs_fail += 1
                    if not u.universe and u.cs_fail >= 2 and self.index_rows and self.cfg.pleito == 3221:
                        log.warning("cs.json de %s indisponível (%s): universo a partir do índice base", u.uf, r.status)
                        self.build_universe(u, [(m, z, s) for uf, m, z, s in self.index_rows if uf == u.uf])
        finally:
            u.cs_inflight = False

    def build_universe(self, u: UFState, rows):
        new = []
        zones = set()
        for m5, z4, s4 in rows:
            if not self.in_scope_mun(m5):
                continue
            key = f"{u.uf}-{m5}-{z4}-{s4}"
            if key in self.secs:
                continue
            sec = self.add_sec(key, u.uf, m5, z4, s4)
            zones.add(sec.zone)
            new.append((key, u.uf, m5, z4, s4))
        self.state.insert_universe(new)
        self._finish_zone_lists(zones)
        u.universe = True
        if self.index_map:
            missing = sum(1 for k, *_ in new if k not in self.index_map)
            idx_uf = sum(1 for uf, m5, *_ in self.index_rows if uf == u.uf and self.in_scope_mun(m5))
            if missing or idx_uf != len(new):
                log.warning("universo %s: %d seções do cs, %d no índice base, %d fora do índice",
                            u.uf, len(new), idx_uf, missing)
            else:
                log.info("universo %s: %d seções (confere com o índice base)", u.uf, len(new))
        else:
            log.info("universo %s: %d seções", u.uf, len(new))

    def apply_cs(self, u: UFState, rows, lm: float | None):
        count = changed = 0
        use_a = self.cfg.strategy != "zonas"
        for m5, z4, s4, dh, principal in rows:
            if not principal:
                continue
            sec = self.secs.get(f"{u.uf}-{m5}-{z4}-{s4}")
            if sec is None:
                continue
            if dh:
                count += 1
            if dh != sec.cs_dh:
                sec.cs_dh = dh
                changed += 1
                self.persist_sec(sec)
                if dh and use_a and dh != sec.aux_dh:
                    sec.want_known = True
                    self.want_known.add(sec)
        prev = u.cs_dh_count
        u.cs_dh_count = count
        if changed and count:
            u.cs_changed_at = time.time()
        u.cs_lm = lm
        if count and not prev:
            log.warning("ESTRATÉGIA A: cs.json de %s tem da/ha em %d seções (Last-Modified %s)",
                        u.uf, count, iso(lm))
        if changed:
            log.info("cs %s: %d seções com da/ha (%d mudaram)", u.uf, count, changed)
        self.state.put_meta("cs_state", orjson.dumps(
            {x.uf: {"n": x.cs_dh_count, "t": x.cs_changed_at} for x in self.ufs.values()}).decode())

    def persist_sec(self, s: Sec):
        r = self.res[s.sid]
        hb = bool(s.has_bu)
        self.state.put_sec((s.key, s.uf, s.mun, s.zona, s.secao, s.status, s.cs_dh, s.aux_dh, s.hash, s.nm,
                            s.dr, s.hr, s.probes, s.next_probe, s.has_bu,
                            int(r[1]) if hb else None, int(r[2]) if hb else None, int(r[3]) if hb else None,
                            int(r[4]) if hb else None, int(r[5]) if hb else None, int(r[6]) if hb else None,
                            int(r[7]) if hb else None, s.err, time.time()))

    def zone_st(self, z: Zone) -> int:
        if z.mun.multi:
            return z.A["st"] if z.A else 0
        return z.mun.zone_st()

    async def planner_loop(self):
        cap = max(20, int(self.cfg.probe_rate * 30))
        while not self.stop.is_set():
            now = time.time()
            pushed = 0
            for sec in list(self.want_known):
                if sec.queued:
                    continue
                if sec.has_bu and sec.aux_dh == sec.cs_dh:
                    sec.want_known = False
                    self.want_known.discard(sec)
                    continue
                if sec.next_probe <= now:
                    sec.want_known = False
                    self.want_known.discard(sec)
                    sec.queued = 1
                    sec.zone.known_queued += 1
                    self.known_q.push(sec)
                    pushed += 1
            if len(self.probe_q) < cap:
                zl = [z for z in self.zones.values() if z.secs]
                nz = len(zl)
                start = self._planner_cursor % max(1, nz)
                for i in range(nz):
                    z = zl[(start + i) % nz]
                    if not self.b_active(self.ufs[z.uf]):
                        continue
                    pending = self.zone_st(z) - z.captured
                    if pending <= 0:
                        continue
                    quota = math.ceil(pending * self.cfg.probe_factor)
                    if len(z.uncaptured) > 2 * (len(z.secs) - z.captured) + 8:
                        z.uncaptured = [s for s in z.uncaptured if not s.has_bu]
                    k = 0
                    for s in z.uncaptured:
                        if s.has_bu:
                            continue
                        k += 1
                        if k > quota:
                            break
                        if s.queued or s.next_probe > now:
                            continue
                        s.queued = 2
                        z.probe_queued += 1
                        self.probe_q.push(s)
                        pushed += 1
                    if len(self.probe_q) >= cap:
                        self._planner_cursor = start + i + 1
                        break
            if pushed:
                self.work_event.set()
            await self._sleep(2.0)

    def _refill_probe(self):
        now = time.monotonic()
        self.probe_tokens = min(self.cfg.probe_rate, self.probe_tokens + (now - self._probe_refill) * self.cfg.probe_rate)
        self._probe_refill = now

    def _accept_probe(self, uf: str) -> bool:
        return random.random() < max(0.1, self.ufs[uf].hit_ema)

    async def next_job(self):
        while not self.stop.is_set():
            if self.known_q:
                return self.known_q.pop(), "known"
            self._refill_probe()
            if self.probe_q and self.probe_tokens >= 1.0:
                self.probe_tokens -= 1.0
                return self.probe_q.pop(accept=self._accept_probe), "probe"
            self.work_event.clear()
            try:
                await asyncio.wait_for(self.work_event.wait(), timeout=0.25)
            except asyncio.TimeoutError:
                pass
        return None, None

    async def bu_worker(self, i: int):
        while not self.stop.is_set():
            sec, kind = await self.next_job()
            if sec is None:
                return
            try:
                await self.process_section(sec, kind)
            except (BudgetExhausted, asyncio.CancelledError):
                raise
            except Exception as e:  # noqa: BLE001
                log.exception("seção %s: erro inesperado", sec.key)
                sec.err = repr(e)[:200]
                sec.next_probe = time.time() + 300
                if not sec.has_bu:
                    sec.status = S_ERR
                self.persist_sec(sec)
            finally:
                if sec.queued == 1:
                    sec.zone.known_queued -= 1
                elif sec.queued == 2:
                    sec.zone.probe_queued -= 1
                sec.queued = 0

    def backoff(self, n: int) -> float:
        b = min(self.cfg.probe_max_interval, self.cfg.probe_min_interval * 2 ** max(0, n - 1))
        return max(self.cfg.probe_min_interval, b * random.uniform(1.0, 1.15))

    def _hit(self, uf: str, hit: bool):
        u = self.ufs[uf]
        u.probes += 1
        u.hits += int(hit)
        u.hit_ema = u.hit_ema * 0.95 + (0.05 if hit else 0.0)

    async def process_section(self, sec: Sec, kind: str):
        cfg = self.cfg
        known = kind == "known"
        dh_at_fetch = sec.cs_dh
        r = await self.bu.get(cfg.url_aux(sec.uf, sec.mun, sec.zona, sec.secao),
                              prio=P_AUX if known else P_PROBE, kind="aux")
        now = time.time()
        if r.status == 404:
            sec.probes += 1
            if not sec.has_bu:
                sec.status = S_AUX404
            sec.next_probe = now + self.backoff(sec.probes)
            if not known:
                self._hit(sec.uf, False)
            if known:
                sec.want_known = True
                self.want_known.add(sec)
            self.persist_sec(sec)
            return
        if r.status != 200:
            sec.next_probe = now + max(cfg.probe_min_interval, 120.0)
            sec.err = r.error or f"HTTP {r.status}"
            if not sec.has_bu:
                sec.status = S_ERR
            if known:
                sec.want_known = True
                self.want_known.add(sec)
            self.persist_sec(sec)
            return
        try:
            aux = tse.parse_aux(tse.loads(r.body))
        except Exception as e:  # noqa: BLE001
            sec.err = f"aux_invalido: {e!r}"[:200]
            sec.next_probe = now + 600
            self.persist_sec(sec)
            return
        sec.probes += 1
        if not known:
            self._hit(sec.uf, aux["hash"] is not None)
        if aux["hash"] is None:
            if not sec.has_bu:
                sec.status = S_NOTOT
            sec.next_probe = now + self.backoff(sec.probes)
            sec.err = f"aux sem Totalizado: {aux['states']}"[:200]
            self.persist_sec(sec)
            return
        sec.aux_dh = dh_at_fetch
        if aux["hash"] == sec.hash and (sec.has_bu or sec.status == S_FAIL):
            self.persist_sec(sec)
            return
        if not aux["nm"]:
            self._fail(sec, aux, "sem_arquivo_bu", "aux sem arquivo bu/busa", None)
            return
        r2 = await self.bu.get(cfg.url_file(sec.uf, sec.mun, sec.zona, sec.secao, aux["hash"], aux["nm"]),
                               prio=P_BU, kind="bu")
        now = time.time()
        if r2.status != 200:
            if not sec.has_bu:
                sec.status = S_BU404 if r2.status == 404 else S_ERR
            sec.err = r2.error or f"HTTP {r2.status}"
            sec.next_probe = now + max(cfg.probe_min_interval, 120.0)
            if known:
                sec.want_known = True
                self.want_known.add(sec)
            self.persist_sec(sec)
            return
        try:
            res = decode_bu(r2.body, cfg.eleicao)
        except BUDecodeError as e:
            self._fail(sec, aux, e.kind, str(e), r2.body)
            return
        if (res.municipio, res.zona, res.secao) != (int(sec.mun), int(sec.zona), int(sec.secao)):
            log.warning("BU de %s diz mun %s zona %s seção %s", sec.key, res.municipio, res.zona, res.secao)
        t_recv = tse.minutes_since(cfg.day0, aux["dr"], aux["hr"])
        self.res[sec.sid] = (1, res.aptos, res.comparecimento, res.votos.get(cfg.cand_l, 0),
                             res.votos.get(cfg.cand_o, 0), res.brancos, res.nulos, t_recv)
        if not sec.has_bu:
            sec.zone.captured += 1
            self.n_bu += 1
        if sec.status == S_FAIL:
            self.decode_fail[(sec.err or "?").split(":")[0]] -= 1
        sec.has_bu = 1
        sec.status = S_DONE
        sec.hash, sec.nm, sec.dr, sec.hr, sec.err = aux["hash"], aux["nm"], aux["dr"], aux["hr"], None
        self.res_dirty = True
        self.bu_times.append(now)
        self.persist_sec(sec)

    def _fail(self, sec: Sec, aux: dict, kind: str, msg: str, body: bytes | None):
        self.decode_fail[kind] += 1
        log.warning("BU não decodificado %s (%s): %s", sec.key, kind, msg[:200])
        if body:
            d = self.cfg.state_dir / "failed_bu"
            d.mkdir(exist_ok=True)
            try:
                (d / f"{sec.key}-{aux.get('nm') or 'bu'}").write_bytes(body)
            except OSError:
                pass
        if not sec.has_bu:
            sec.status = S_FAIL
        sec.hash, sec.nm, sec.dr, sec.hr = aux.get("hash"), aux.get("nm"), aux.get("dr"), aux.get("hr")
        sec.err = f"{kind}: {msg}"[:300]
        sec.next_probe = time.time() + 1800
        self.persist_sec(sec)

    # ================================================================ publicação
    def ensure_index(self):
        """Mapeia sid -> idx do índice. Sem índice base, deriva um canônico do universo (contracts §2)."""
        if not self.index_map and self.sec_list and self.index_src != "base":
            keys = sorted(self.secs, key=lambda k: (UF_ORDER[k[:2]], int(k[3:8]), int(k[9:13]), int(k[14:18])))
            if len(keys) != self.index_n:
                self.index_keys = keys
                self.index_n = len(keys)
                self.index_src = "derived"
                self._derived_map = {k: i for i, k in enumerate(keys)}
                self.out_idx = np.zeros(0, dtype=np.int64)
        mp = self.index_map if self.index_src == "base" else getattr(self, "_derived_map", {})
        if len(self.out_idx) != len(self.sec_list):
            old = len(self.out_idx)
            ext = np.array([mp.get(s.key, -1) for s in self.sec_list[old:]], dtype=np.int64)
            self.out_idx = np.concatenate([self.out_idx, ext])

    def br_from_ufs(self) -> dict | None:
        if set(self.ufs) != set(UF_ORDER) or any(u.A is None for u in self.ufs.values()):
            return None
        out = tse.empty_a()
        for u in self.ufs.values():
            for k in tse.A_KEYS_INT:
                out[k] += u.A[k]
        latest = max(self.ufs.values(), key=lambda u: (tse.parse_brt(u.A["dt"], u.A["ht"]) or dt.datetime.min.replace(tzinfo=BRT)))
        out["dt"], out["ht"] = latest.A["dt"], latest.A["ht"]
        ands = {u.A["and"] for u in self.ufs.values()}
        out["and"] = "f" if ands == {"f"} else ("n" if ands == {"n"} else next((a for a in ands if a not in ("n", "f")), "p"))
        return out

    def build_agg(self) -> tuple[dict, dict]:
        ufs_out = {}
        for u in self.ufs.values():
            e = u.ab_self or u.br_entry
            A = u.A or (a_from_ab(e) if e and e["st"] == 0 else None)
            if A:
                ufs_out[u.uf] = A
        br = self.br_A
        uf_sum = self.br_from_ufs()
        ahead = bool(uf_sum and br and uf_sum["st"] > br["st"])
        # publica o total mais adiantado, mas só acusa "BR congelado" se o arquivo BR não muda
        # (idg/dg/hg) há > br_frozen_after s: BR e UFs têm caches de CDN independentes (até 60 s).
        frozen = ahead and time.time() - self.br_content_at > self.cfg.br_frozen_after
        if ahead or (br is None and uf_sum):
            br_out, src = uf_sum, "uf_sum"
        else:
            br_out, src = (br or tse.empty_a()), "br"
        mun_out = {}
        zona_out = {}
        for m in self.muns.values():
            if m.out_A:
                mun_out[str(int(m.mun5))] = m.out_A
            if not m.multi:
                if m.out_A and m.zones:
                    zona_out[m.zones[0].out_key] = m.out_A
            else:
                for z in m.zones:
                    if z.A:
                        zona_out[z.out_key] = z.A
        info = {"br_frozen": frozen, "br_source": src,
                "uf_st_sum": sum(u.A["st"] for u in self.ufs.values() if u.A)}
        return {"br": br_out, "uf": ufs_out, "mun": mun_out, "zona": zona_out}, info

    def build_sections(self) -> tuple[bytes, int]:
        self.ensure_index()
        n = self.index_n
        out = np.zeros((n, N_COLS), dtype="<i4")
        out[:, 7] = -1
        k = len(self.sec_list)
        if k and n:
            res = self.res[:k]
            m = (self.out_idx >= 0) & (res[:, 0] == 1)
            out[self.out_idx[m]] = res[m]
            nbu = int(m.sum())
        else:
            nbu = 0
        return out.tobytes(), nbu

    def bu_rate_min(self) -> float:
        now = time.time()
        while self.bu_times and self.bu_times[0] < now - 300:
            self.bu_times.popleft()
        win = max(60.0, min(300.0, now - self.started))
        return round(len(self.bu_times) * 60.0 / win, 1)

    def agg_lag_parts(self) -> dict:
        t = time.time()
        mun = [t - m.need_since for m in self.need_muns if m.need_since]
        zon = [t - z.need_since for z in self.need_zones if z.need_since]
        big = [t - z.need_since for z in self.need_zones if z.need_since and len(z.mun.zones) >= 10]
        r = lambda v: round(max(v), 1) if v else 0.0  # noqa: E731
        return {"mun_s": r(mun), "zona_multi_s": r(zon), "zona_capitais_s": r(big),
                "mun_pending": len(mun), "zona_pending": len(zon)}

    def agg_lag(self) -> tuple[float, int]:
        t = time.time()
        ages = [t - m.need_since for m in self.need_muns if m.need_since]
        ages += [t - z.need_since for z in self.need_zones if z.need_since]
        return (round(max(ages), 1) if ages else 0.0), len(ages)

    def health(self, info: dict) -> dict:
        now = time.time()
        a, b = self.agg.metrics, self.bu.metrics
        lag, pend = self.agg_lag()
        cut = now - 600
        while self.lat_hist and self.lat_hist[0][0] < cut:
            self.lat_hist.popleft()
        lat_need = sorted(x[1] for x in self.lat_hist)
        lat_tse = sorted(x[2] for x in self.lat_hist if x[2] is not None and 0 <= x[2] <= 3600)

        def pct(v, q):
            return round(v[min(len(v) - 1, int(q * len(v)))], 1) if v else None

        while self.bu_times and self.bu_times[0] < now - 300:
            self.bu_times.popleft()
        br_t = tse.parse_brt(self.br_meta.get("dg"), self.br_meta.get("hg"))
        last_ok = max([x for x in (a.last_ok, b.last_ok) if x] or [None]) if (a.last_ok or b.last_ok) else None
        bu_pause = self.bu.gate.paused_until_wall if self.bu.gate.paused_for() > 0 else None
        agg_pause = self.agg.gate.paused_until_wall if self.agg.gate.paused_for() > 0 else None
        return {
            "last_ok": iso(last_ok),
            "errors_5min": a.errors_5min() + b.errors_5min(),
            "req_rate": round(a.req_rate() + b.req_rate(), 2),
            "bu_queue": len(self.known_q) + len(self.probe_q) + len(self.want_known),
            "strategy": self.strategy_label(),
            "stale_seconds": round(now - self.br_last_ok, 1) if self.br_last_ok else None,
            "tse_last_change": iso(self.tse_last_change),
            "tse_br_dg_hg": f"{self.br_meta.get('dg', '')} {self.br_meta.get('hg', '')}".strip() or None,
            "tse_br_age_s": round(now - br_t.timestamp(), 1) if br_t else None,
            "br_frozen": info["br_frozen"],
            "br_unchanged_s": round(now - self.br_content_at, 1),
            "br_source": info["br_source"],
            "lanes": {
                "agg": {"last_ok": iso(a.last_ok), "req_rate": round(a.req_rate(), 2),
                        "errors_5min": a.errors_5min(), "rate_cap": round(self.agg.gate.rate, 2),
                        "paused_until": iso(agg_pause), "lane_pauses": self.agg.gate.lane_pauses,
                        "urls_backing_off": sum(1 for m in self.need_muns if m.fetch_fail)
                        + sum(1 for z in self.need_zones if z.fetch_fail)},
                "bu": {"last_ok": iso(b.last_ok), "req_rate": round(b.req_rate(), 2),
                       "errors_5min": b.errors_5min(), "rate_cap": round(self.bu.gate.rate, 2),
                       "paused_until": iso(bu_pause), "enabled": self.cfg.bu_enabled},
            },
            "agg_lag_s": lag,
            "agg_pending": pend,
            "agg_lag_parts": self.agg_lag_parts(),
            "agg_latency_s": {"detect_to_publish_p50": pct(lat_need, 0.5), "detect_to_publish_max": pct(lat_need, 1.0),
                              "tse_to_publish_p50": pct(lat_tse, 0.5), "tse_to_publish_p90": pct(lat_tse, 0.9),
                              "tse_to_publish_max": pct(lat_tse, 1.0), "n_10min": len(lat_need)},
            "decode_failures": int(sum(v for v in self.decode_fail.values() if v > 0)),
            "decode_failures_by_kind": {k: v for k, v in self.decode_fail.items() if v > 0},
            "bu_per_min": self.bu_rate_min(),
            "bu_total": self.n_bu,
            "cs_dh_ufs": sorted(u.uf for u in self.ufs.values() if u.cs_dh_count),
            "format": self.fmt,
            "requests_total": self.budget.used,
        }

    def publish(self, sections_ok: bool = True):
        cfg = self.cfg
        t_pub = time.time()
        self._last_pub = time.monotonic()
        agg, info = self.build_agg()
        agg_bytes = orjson.dumps(agg)
        h = hashlib.sha1(agg_bytes).hexdigest()
        changed = False
        if h != self._last_agg_hash or not (cfg.out_dir / versioned_name("agg", self.dv_agg)).exists():
            self.dv_agg += 1
            atomic_write(cfg.out_dir / versioned_name("agg", self.dv_agg), agg_bytes)
            prune_versions(cfg.out_dir, "agg", cfg.keep_versions)
            self._last_agg_hash = h
            changed = True
            for need, lm in self._pending_lat:
                self.lat_hist.append((t_pub, t_pub - need, (t_pub - lm) if lm else None))
            self._pending_lat.clear()
        sec_bytes, nbu = None, None
        if (sections_ok and self.res_dirty) or not (cfg.out_dir / versioned_name("sections", self.dv_sec)).exists():
            self._last_sec_pub = time.monotonic()
            sec_bytes, nbu = self.build_sections()
            self.dv_sec += 1
            atomic_write(cfg.out_dir / versioned_name("sections", self.dv_sec), sec_bytes)
            prune_versions(cfg.out_dir, "sections", cfg.keep_versions)
            self.res_dirty = False
            self._nbu_index = nbu
            changed = True
        self.version += 1
        br = agg["br"]
        manifest = {
            "version": self.version,
            "generated_at": iso(t_pub),
            "source": cfg.source,
            "tse": {"br_dg": self.br_meta.get("dg", ""), "br_hg": self.br_meta.get("hg", ""),
                    "br_idg": self.br_meta.get("idg", "")},
            "coverage": {"secoes_total": self.index_n or br.get("ts", 0), "secoes_totalizadas": br.get("st", 0),
                         "secoes_com_bu": getattr(self, "_nbu_index", 0), "aptos_total": br.get("te", 0),
                         "aptos_totalizados": br.get("est", 0)},
            "health": self.health(info),
            "data_versions": {"agg": self.dv_agg, "sections": self.dv_sec},
            "files": {"agg": versioned_name("agg", self.dv_agg), "sections": versioned_name("sections", self.dv_sec),
                      "analytics": "analytics.json"},
            "index": {"n_rows": self.index_n, "n_cols": N_COLS, "sha256": self.index_sha, "src": self.index_src},
            "scope": {"eleicao": cfg.eleicao, "pleito": cfg.pleito,
                      "ufs": cfg.ufs if len(cfg.ufs) < len(UF_ORDER) else "all",
                      "muns": sorted(cfg.muns) if cfg.muns else None},
        }
        atomic_write(cfg.out_dir / "manifest.json", orjson.dumps(manifest, option=orjson.OPT_INDENT_2))
        self.state.put_meta("version", self.version)
        self.state.put_meta("dv_agg", self.dv_agg)
        self.state.put_meta("dv_sec", self.dv_sec)
        self.last_manifest = manifest
        return changed

    async def publish_loop(self):
        """Heartbeat a cada `publish_interval`; agg mudou => publica já (respeitando `publish_min_gap`).
        O sections (16 MB) só sai no ritmo do heartbeat."""
        cfg = self.cfg
        while not self.stop.is_set():
            try:
                self.publish(sections_ok=time.monotonic() - self._last_sec_pub >= cfg.publish_interval - 0.5)
            except Exception:  # noqa: BLE001
                log.exception("falha publicando")
            self.agg_event.clear()
            deadline = self._last_pub + cfg.publish_interval
            try:
                await asyncio.wait_for(self.agg_event.wait(), timeout=max(0.0, deadline - time.monotonic()))
                gap = min(cfg.publish_min_gap, cfg.publish_interval)
                await self._sleep(self._last_pub + gap - time.monotonic())
            except asyncio.TimeoutError:
                pass

    async def flush_loop(self):
        while not self.stop.is_set():
            await self._sleep(0.5)
            try:
                self.state.flush()
            except Exception:  # noqa: BLE001
                log.exception("falha gravando estado (tento de novo)")

    async def stats_loop(self):
        while not self.stop.is_set():
            await self._sleep(60.0)
            lag, pend = self.agg_lag()
            st = Counter(STATUS_NAMES[s.status] for s in self.sec_list)
            log.info("STATS agg %.1f req/s (cap %.1f, err5 %d) | bu %.1f req/s (cap %.1f, err5 %d%s) | "
                     "BR st=%s | BUs %d (%.0f/min) fila %d sond %d | lag %.0fs (%d pend) | %s | decod_falhas %d | %s",
                     self.agg.metrics.req_rate(), self.agg.gate.rate, self.agg.metrics.errors_5min(),
                     self.bu.metrics.req_rate(), self.bu.gate.rate, self.bu.metrics.errors_5min(),
                     f", PAUSADA {self.bu.gate.paused_for():.0f}s" if self.bu.gate.paused_for() else "",
                     self.br_A["st"] if self.br_A else "-", self.n_bu, self.bu_rate_min(),
                     len(self.known_q), len(self.probe_q), lag, pend, self.strategy_label(),
                     sum(v for v in self.decode_fail.values() if v > 0), dict(st))

    def is_done(self) -> bool:
        if not all(u.universe for u in self.ufs.values()) and self.cfg.bu_enabled:
            return False
        if self.cfg.bu_enabled and any(not s.has_bu and s.status != S_FAIL for s in self.sec_list):
            return False
        if self.need_muns or self.need_zones:
            return False
        for m in self.muns.values():
            if not complete(m.A):
                return False
            if m.multi and not all(complete(z.A) for z in m.zones):
                return False
        return all(complete(u.A) for u in self.ufs.values())

    async def watch_loop(self):
        while not self.stop.is_set():
            await self._sleep(2.0)
            if self.cfg.max_runtime and time.time() - self.started > self.cfg.max_runtime:
                log.info("max-runtime atingido")
                self.stop.set()
            if self.cfg.exit_when_done and self.is_done():
                log.info("escopo completo: publicando e saindo")
                self.stop.set()

    # ================================================================ execução
    async def run(self):
        self.stop = asyncio.Event()
        self.work_event = asyncio.Event()
        self.agg_event = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self.stop.set)
            except (NotImplementedError, RuntimeError):
                pass
        log.info("coletor iniciando: %s", orjson.dumps(self.cfg.describe()).decode())
        self.load_index()
        self.load_prior()
        self.load_structure()
        self.restore()
        self.agg.start()
        self.bu.start()
        try:
            await self.load_cm()
        except BudgetExhausted:
            return
        except Exception:  # noqa: BLE001
            log.exception("cm.json falhou (seguindo com a estrutura conhecida)")
        tasks = [
            asyncio.create_task(self._guard("agg_l0", self.agg_l0_loop)),
            asyncio.create_task(self._guard("agg_uf", self.agg_uf_loop)),
            asyncio.create_task(self._guard("agg_fetch", self.agg_fetch_loop)),
            asyncio.create_task(self._guard("jws", self.jws_loop)),
            asyncio.create_task(self._guard("publish", self.publish_loop)),
            asyncio.create_task(self._guard("flush", self.flush_loop)),
            asyncio.create_task(self._guard("stats", self.stats_loop)),
            asyncio.create_task(self._guard("watch", self.watch_loop)),
        ]
        if self.cfg.bu_enabled:
            tasks.append(asyncio.create_task(self._guard("cs", self.cs_loop)))
            tasks.append(asyncio.create_task(self._guard("planner", self.planner_loop)))
            for i in range(self.cfg.bu_concurrency):
                tasks.append(asyncio.create_task(self._guard(f"bu{i}", lambda i=i: self.bu_worker(i))))
        else:
            log.warning("--no-bu: só agregados (sections.bin fica vazio)")
        try:
            await self.stop.wait()
        finally:
            log.info("encerrando…")
            for t in tasks + list(self._bg):
                t.cancel()
            await asyncio.gather(*tasks, *list(self._bg), return_exceptions=True)
            try:
                self.publish()
            except Exception:  # noqa: BLE001
                log.exception("falha na publicação final")
            self.state.close()
            await self.agg.close()
            await self.bu.close()
            log.info("requisições nesta execução: agg=%s bu=%s", self.agg.metrics.summary(),
                     self.bu.metrics.summary())

"""Estado da simulação: relógio, agregados por tick e construção dos arquivos do TSE.

Tudo é função determinística de (cenário, semente, tempo simulado):
  - uma seção entra nos agregados no primeiro tick K com t_tot <= K*tick;
  - cada arquivo de abrangência só muda quando entra seção nova nela (ETag estável -> 304);
  - dg/hg = hora do tick da última mudança daquela abrangência; dt/ht = última totalização.
"""

from __future__ import annotations

import base64
import copy
import gzip
import hashlib
import json
import re
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from email.utils import formatdate
from pathlib import Path

import numpy as np
import orjson

from . import bu as bumod
from .scenario import NEVER, UFS, Truth
from .tsejson import dumps, p3, pct, pctn

HERE = Path(__file__).resolve().parent
TEMPLATES = HERE / "templates"
ROOT = HERE.parent
CS_DIR_DEFAULT = ROOT / "data" / "raw" / "cs_2t"

DAY0_UTC = 1792897200  # 25/10/2026 00:00 BRT (= 03:00 UTC) em epoch
GEN_OFF = 7            # s após o tick em que os agregados aparecem
LEVEL_EXTRA = {"br": 6, "uf": 4, "mu": 2, "zona": 2, "abbr": 5, "abuf": 3}


def brt_date(s: int) -> str:
    d = 25 + s // 86400
    return f"{d:02d}/10/2026"


def brt_time(s: int) -> str:
    s %= 86400
    return f"{s // 3600:02d}:{(s // 60) % 60:02d}:{s % 60:02d}"


def http_date(sim_s: float) -> str:
    return formatdate(DAY0_UTC + sim_s, usegmt=True)


def parse_hhmm(s: str) -> int:
    """'16:55' / '16:55:30' / '+1 01:30' -> segundos desde 25/10 00:00 BRT ('01:30' < 12h => dia 26)."""
    s = s.strip()
    parts = [int(x) for x in s.split(":")]
    while len(parts) < 3:
        parts.append(0)
    sec = parts[0] * 3600 + parts[1] * 60 + parts[2]
    if parts[0] < 12:
        sec += 86400
    return sec


def parse_window(s: str | None) -> tuple[int, int] | None:
    if not s:
        return None
    a, b = s.split("-")
    t0, t1 = parse_hhmm(a), parse_hhmm(b)
    if t1 <= t0:
        t1 += 86400
    return t0, t1


class Clock:
    def __init__(self, start_sim: int, speed: float):
        self.set(start_sim, speed)

    def set(self, sim: float, speed: float | None = None):
        self.anchor_real = time.time()
        self.anchor_sim = float(sim)
        if speed is not None:
            self.speed = float(speed)

    def now(self) -> float:
        return self.anchor_sim + (time.time() - self.anchor_real) * self.speed


@dataclass
class Resource:
    body: bytes
    etag: str
    last_modified: float          # tempo simulado (s desde 25/10 00:00 BRT)
    content_type: str = "application/json"
    max_age: int = 60
    _gz: bytes | None = None

    @property
    def gz(self) -> bytes:
        if self._gz is None:
            self._gz = gzip.compress(self.body, compresslevel=6, mtime=0)
        return self._gz


def make_resource(body: bytes, lm: float, ctype="application/json", max_age=60) -> Resource:
    return Resource(body=body, etag='"' + hashlib.md5(body).hexdigest() + '"', last_modified=lm,
                    content_type=ctype, max_age=max_age)


@dataclass
class Level:
    name: str
    G: int
    st: np.ndarray
    si: np.ndarray
    est: np.ndarray
    esi: np.ndarray
    c: np.ndarray
    vb: np.ndarray
    vn: np.ndarray
    l: np.ndarray
    o: np.ndarray
    last_t: np.ndarray


METRICS = ("st", "si", "est", "esi", "c", "vb", "vn", "l", "o")


@dataclass
class Snap:
    p: int                     # nº de seções (ordem t_tot) incluídas
    levels: dict = field(default_factory=dict)


class Sim:
    def __init__(self, tr: Truth, *, tick: int = 60, cs_mode: str = "none", cs_interval: int = 120,
                 cs_dir: Path = CS_DIR_DEFAULT, busa: bool = True, freeze_br=None, bu_cache: int = 20000):
        self.tr = tr
        self.tick = int(tick)
        self.cs_mode = cs_mode
        self.cs_interval = int(cs_interval)
        self.cs_dir = Path(cs_dir)
        self.busa = busa
        self.freeze_br = freeze_br
        n = tr.n
        self.n = n
        # ---- grupos
        self.uf_i = tr.uf_i.astype(np.int64)
        mk = self.uf_i * 100000 + tr.cd_mun
        mkeys, self.mun_g = np.unique(mk, return_inverse=True)
        zk = self.uf_i * 10**9 + tr.cd_mun.astype(np.int64) * 10**4 + tr.zona
        zkeys, self.zon_g = np.unique(zk, return_inverse=True)
        self.n_mun, self.n_zon = len(mkeys), len(zkeys)
        self.mun_lookup = {(UFS[int(k // 100000)], int(k % 100000)): g for g, k in enumerate(mkeys)}
        self.zon_lookup = {(UFS[int(k // 10**9)], int(k // 10**4 % 100000), int(k % 10**4)): g
                           for g, k in enumerate(zkeys)}
        self.mun_uf = (mkeys // 100000).astype(np.int64)
        self.mun_cd = (mkeys % 100000).astype(np.int64)
        self.mun_by_uf = [np.nonzero(self.mun_uf == u)[0] for u in range(len(UFS))]
        self.zon_mun = np.zeros(self.n_zon, np.int64)
        self.zon_mun[self.zon_g] = self.mun_g
        self.zon_cd = (zkeys % 10**4).astype(np.int64)
        self.groups = {"br": np.zeros(n, np.int64), "uf": self.uf_i, "mu": self.mun_g, "zona": self.zon_g}
        self.G = {"br": 1, "uf": len(UFS), "mu": self.n_mun, "zona": self.n_zon}
        # chave composta -> idx (índice canônico já ordenado por uf, mun, zona, seção)
        self.sec_key = (self.uf_i * 10**13 + tr.cd_mun.astype(np.int64) * 10**8
                        + tr.zona.astype(np.int64) * 10**4 + tr.secao)
        if not np.all(np.diff(self.sec_key) > 0):
            raise RuntimeError("índice de seções fora de ordem")
        # ---- estáticos por grupo
        self.ts = {k: np.bincount(g, minlength=self.G[k]).astype(np.int64) for k, g in self.groups.items()}
        self.te = {k: np.bincount(g, weights=tr.aptos, minlength=self.G[k]).astype(np.int64)
                   for k, g in self.groups.items()}
        # ---- ordem de totalização
        self.order = np.argsort(tr.t_tot, kind="stable")
        self.t_sorted = tr.t_tot[self.order]
        self.k_sorted = -(-self.t_sorted // self.tick)  # ceil
        inst = tr.installed.astype(np.int64)
        M = np.stack([np.ones(n, np.int64), inst, tr.aptos, tr.aptos * inst, tr.comp, tr.b, tr.nulos,
                      tr.l, tr.o], axis=1)
        self.M_sorted = M[self.order].astype(np.float64)
        self.g_sorted = {k: g[self.order] for k, g in self.groups.items()}
        self._snaps: OrderedDict[int, Snap] = OrderedDict()
        self._cur = self._scratch(0)
        # ---- publicação (aux/BU) por UF para o cs progressivo
        self.pub_sorted_uf = {}
        for i, u in enumerate(UFS):
            sel = np.nonzero(self.uf_i == i)[0]
            self.pub_sorted_uf[u] = np.sort(tr.t_pub[sel])
        # ---- templates
        self.tpl = {lvl: orjson.loads((TEMPLATES / f"{lvl}-u.json").read_bytes()) for lvl in ("br", "uf", "mu")}
        self.cache: dict[str, tuple] = {}
        self.bu_cache: OrderedDict[int, Resource] = OrderedDict()
        self.aux_cache: OrderedDict[int, Resource] = OrderedDict()
        self.bu_cache_max = bu_cache
        self._cs_pieces: dict[str, tuple] = {}
        self._cs_raw: dict[str, Resource] = {}
        self.static: dict[str, Resource] = {}
        for name, path, ctype in (("cm", TEMPLATES / "mun-e006258-cm.json", "application/json"),
                                  ("elec", TEMPLATES / "ele-c.json", "application/json")):
            body = path.read_bytes()
            self.static[name] = make_resource(body, -86400 * 10, ctype, 60)
        meta = CS_DIR_DEFAULT / "mun-e006258-cm.meta.json"
        if meta.exists():
            m = json.loads(meta.read_text())
            self.static["cm"].etag = m.get("etag", self.static["cm"].etag)
        self.not_found_body = (TEMPLATES / "404.xml").read_bytes()
        nat = tr.national()
        self.winner = 13 if nat["l"] > nat["o"] else 22

    # ------------------------------------------------------------------ agregados
    def _empty_level(self, name):
        G = self.G[name]
        z = lambda: np.zeros(G, np.int64)  # noqa: E731
        return Level(name, G, *(z() for _ in METRICS), last_t=np.full(G, -1, np.int64))

    def _add(self, lv: Level, a: int, b: int) -> Level:
        if b <= a:
            return lv
        g = self.g_sorted[lv.name][a:b]
        Ms = self.M_sorted[a:b]
        new = {}
        for j, m in enumerate(METRICS):
            new[m] = getattr(lv, m) + np.bincount(g, weights=Ms[:, j], minlength=lv.G).astype(np.int64)
        last = lv.last_t.copy()
        np.maximum.at(last, g, self.t_sorted[a:b])
        return Level(lv.name, lv.G, **new, last_t=last)

    def _scratch(self, p: int) -> Snap:
        s = Snap(p=p)
        for name in self.groups:
            s.levels[name] = self._add(self._empty_level(name), 0, p)
        return s

    def prefix(self, K: int) -> int:
        return int(np.searchsorted(self.k_sorted, K, side="right"))

    def snap(self, K: int) -> Snap:
        p = self.prefix(K)
        cur = self._cur
        if p == cur.p:
            return cur
        if p > cur.p:
            s = Snap(p=p, levels={k: self._add(lv, cur.p, p) for k, lv in cur.levels.items()})
            self._cur = s
            return s
        if p in self._snaps:
            self._snaps.move_to_end(p)
            return self._snaps[p]
        s = self._scratch(p)
        self._snaps[p] = s
        while len(self._snaps) > 6:
            self._snaps.popitem(last=False)
        return s

    def on_clock_jump(self, T: float):
        """Relógio voltou: recomeça o acumulador incremental a partir do novo instante."""
        p = self.prefix(self.tick_of(T))
        if p < self._cur.p:
            self._cur = self._scratch(p)

    def tick_of(self, T: float) -> int:
        return int((T - GEN_OFF) // self.tick)

    def br_tick(self, T: float) -> tuple[int, bool]:
        K = self.tick_of(T)
        if self.freeze_br and self.freeze_br[0] <= T < self.freeze_br[1]:
            return self.tick_of(self.freeze_br[0]), True
        return K, False

    # ------------------------------------------------------------------ blocos
    @staticmethod
    def _s_block(ts, st, si):
        snt, sni, sa, sna = ts - st, st - si, si, 0
        out = {"ts": str(ts), "st": str(st), **p3("st", st, ts), "snt": str(snt), **p3("snt", snt, ts),
               "si": str(si), **p3("si", si, ts), "sni": str(sni), **p3("sni", sni, ts),
               "sa": str(sa), **p3("sa", sa, si), "sna": str(sna), **p3("sna", sna, si)}
        return out

    @staticmethod
    def _e_block(te, est, esi, c):
        esnt, esni, esa, esna, a = te - est, est - esi, esi, 0, esi - c
        return {"te": str(te), "est": str(est), **p3("est", est, te), "esnt": str(esnt), **p3("esnt", esnt, te),
                "esi": str(esi), **p3("esi", esi, te), "esni": str(esni), **p3("esni", esni, te),
                "esa": str(esa), **p3("esa", esa, esi), "esna": str(esna), **p3("esna", esna, esi),
                "c": str(c), **p3("c", c, esi), "a": str(a), **p3("a", a, esi)}

    @staticmethod
    def _v_block(c, vb, vn, l, o):
        tv, vv = c, l + o
        z = "0,00"
        return {"tv": str(tv), "vvc": str(vv), **p3("vvc", vv, tv, z), "vv": str(vv), **p3("vv", vv, vv, z),
                "vnom": str(vv), **p3("vnom", vv, vv, z), "van": "0", **p3("van", 0, tv, z),
                "vansj": "0", **p3("vansj", 0, tv, z), "vb": str(vb), **p3("vb", vb, tv, z),
                "tvn": str(vn), **p3("tvn", vn, tv, z), "vn": str(vn), **p3("vn", vn, vn, z),
                "vnt": "0", **p3("vnt", 0, vn, z), "vsan": "0", "vscv": "0"}

    def _gen_stamp(self, last_t: int, level_key: str, natfinal_t: int | None, salt: int = 0):
        k = -(-last_t // self.tick)
        gen = k * self.tick + GEN_OFF + LEVEL_EXTRA[level_key]
        if natfinal_t is not None:
            kf = -(-natfinal_t // self.tick)
            gen = max(gen, kf * self.tick + GEN_OFF + LEVEL_EXTRA[level_key])
        idg = 4_000_000 + (gen - 61200) * 8 + LEVEL_EXTRA[level_key] + salt % 7
        return gen, idg

    # ------------------------------------------------------------------ u.json
    def _u_obj(self, level: str, g: int, lv: Level, natfinal_t):
        ts, te = int(self.ts[level][g]), int(self.te[level][g])
        st, si, est, esi = int(lv.st[g]), int(lv.si[g]), int(lv.est[g]), int(lv.esi[g])
        c, vb, vn, l, o = int(lv.c[g]), int(lv.vb[g]), int(lv.vn[g]), int(lv.l[g]), int(lv.o[g])
        tplname = {"br": "br", "uf": "uf", "mu": "mu", "zona": "mu"}[level]
        tpl = self.tpl[tplname]
        if level == "br":
            tp, cd = "br", "br"
        elif level == "uf":
            tp, cd = "uf", UFS[g]
        elif level == "mu":
            tp, cd = "mu", f"{int(self.mun_cd[g]):05d}"
        else:
            tp, cd = "zona", f"{int(self.zon_cd[g]):04d}"
        started = st > 0
        natfinal = natfinal_t is not None
        if started:
            gen, idg = self._gen_stamp(int(lv.last_t[g]), level, natfinal_t, g)
            dg, hg, idg = brt_date(gen), brt_time(gen), str(idg)
            dt, ht = brt_date(int(lv.last_t[g])), brt_time(int(lv.last_t[g]))
            lm = gen
        else:
            dg, hg, idg = tpl["dg"], tpl["hg"], tpl["idg"]
            dt = ht = ""
            lm = -86400.0
        and_ = "n" if st == 0 else ("f" if st == ts else "p")
        tf = "s" if and_ == "f" else "n"
        h = {"ele": "6258", "t": "2", "f": "o", "sup": "n", "tpabr": tp, "cdabr": cd,
             "dg": dg, "hg": hg, "idg": idg, "dt": dt, "ht": ht}
        if level == "zona":
            h["tf"], h["and"] = tf, and_
            if started:
                h["esae"], h["mnae"] = "n", []
            h["dv"] = "s"
        else:
            h["dv"], h["tf"], h["and"] = "s", tf, and_
            if started:
                h["esae"], h["mnae"] = "n", []
        carg = copy.deepcopy(tpl["carg"])
        votes = {"13": l, "22": o}
        vv = l + o
        rank = sorted(votes, key=lambda k: (-votes[k], k)) if started else None
        for agr in carg[0]["agr"]:
            tot_agr = 0
            for par in agr["par"]:
                tot_par = 0
                newc = []
                for cand in par["cand"]:
                    num = cand["n"]
                    vap = votes.get(num, 0)
                    tot_par += vap
                    nc = {k: cand[k] for k in ("n", "sqcand", "nm", "nmu", "dt")}
                    if started:
                        nc["dvt"] = "Válido"
                        nc["seq"] = str(rank.index(num) + 1)
                    else:
                        nc["seq"] = cand["seq"]
                    if natfinal:
                        win = int(num) == self.winner
                        nc["e"], nc["st"] = ("s", "Eleito") if win else ("n", "Não eleito")
                    else:
                        nc["e"], nc["st"] = "n", ""
                    nc["vap"] = str(vap)
                    nc["pvap"] = pct(vap, vv)
                    nc["pvapn"] = pctn(vap, vv)
                    nc["vs"] = cand["vs"]
                    newc.append(nc)
                par["cand"] = newc
                par["tvtn"] = par["tvan"] = str(tot_par)
                tot_agr += tot_par
            if "tvtn" in agr:
                agr["tvtn"] = agr["tvan"] = str(tot_agr)
        h["carg"] = carg
        h["s"] = self._s_block(ts, st, si)
        h["e"] = self._e_block(te, est, esi, c)
        h["v"] = self._v_block(c, vb, vn, l, o)
        return h, lm

    def natfinal_t(self, snap: Snap):
        return int(self.t_sorted[-1]) if snap.p == self.n else None

    def u_json(self, level: str, g: int, T: float, jws: bool = False) -> Resource:
        if level == "br":
            K, _ = self.br_tick(T)
        else:
            K = self.tick_of(T)
        snap = self.snap(K)
        lv = snap.levels[level]
        nf = self.natfinal_t(snap)
        key = (int(lv.last_t[g]), nf, jws)
        ck = f"u:{level}:{g}:{int(jws)}"
        hit = self.cache.get(ck)
        if hit and hit[0] == key:
            return hit[1]
        obj, lm = self._u_obj(level, g, lv, nf)
        body = dumps(obj)
        res = make_resource(_jws(body) if jws else body, lm)
        self.cache[ck] = (key, res)
        return res

    # ------------------------------------------------------------------ ab.json
    def _ab_counts_block(self, prefix, sts, tss):
        nr = int((sts == 0).sum())
        f = int((sts == tss).sum())
        pt = len(sts) - nr - f
        N = len(sts)
        return {f"{prefix}nr": str(nr), **p3(f"{prefix}nr", nr, N), f"{prefix}pt": str(pt),
                **p3(f"{prefix}pt", pt, N), f"{prefix}f": str(f), **p3(f"{prefix}f", f, N)}

    def _ab_entry(self, level, g, lv, tpabr, cdabr, extra=None):
        st, ts = int(lv.st[g]), int(self.ts[level][g])
        and_ = "n" if st == 0 else ("f" if st == ts else "p")
        lt = int(lv.last_t[g])
        e = {"and": and_, "tpabr": tpabr, "cdabr": cdabr,
             "dt": brt_date(lt) if st else "", "ht": brt_time(lt) if st else ""}
        if extra:
            e.update(extra)
        e["s"] = self._s_block(ts, st, int(lv.si[g]))
        e["e"] = self._e_block(int(self.te[level][g]), int(lv.est[g]), int(lv.esi[g]), int(lv.c[g]))
        return e

    def _uf_entry(self, snap, u):
        lu, lm_ = snap.levels["uf"], snap.levels["mu"]
        sel = self.mun_by_uf[u]
        extra = self._ab_counts_block("mun", lm_.st[sel], self.ts["mu"][sel])
        return self._ab_entry("uf", u, lu, "uf", UFS[u], extra)

    def ab_json(self, uf: str | None, T: float, jws: bool = False) -> Resource:
        if uf is None:
            K, _ = self.br_tick(T)
            snap = self.snap(K)
            lv = snap.levels["br"]
            last = int(lv.last_t[0])
            key = (last, jws)
            ck = f"ab:br:{int(jws)}"
            hit = self.cache.get(ck)
            if hit and hit[0] == key:
                return hit[1]
            abr = [self._uf_entry(snap, u) for u in range(len(UFS))]
            lu = snap.levels["uf"]
            extra = self._ab_counts_block("ufs", lu.st, self.ts["uf"])
            abr.append(self._ab_entry("br", 0, lv, "br", "br", extra))
            if last >= 0:
                gen, idg = self._gen_stamp(last, "abbr", None)
                dg, hg, idg, lm = brt_date(gen), brt_time(gen), str(idg), gen
            else:
                dg, hg, idg, lm = "24/10/2026", "19:17:32", "3942244", -86400.0
        else:
            u = UFS.index(uf)
            snap = self.snap(self.tick_of(T))
            lu = snap.levels["uf"]
            last = int(lu.last_t[u])
            key = (last, jws)
            ck = f"ab:{uf}:{int(jws)}"
            hit = self.cache.get(ck)
            if hit and hit[0] == key:
                return hit[1]
            lm_ = snap.levels["mu"]
            abr = [self._ab_entry("mu", int(g), lm_, "mun", f"{int(self.mun_cd[g]):05d}")
                   for g in self.mun_by_uf[u]]
            abr.append(self._uf_entry(snap, u))
            if last >= 0:
                gen, idg = self._gen_stamp(last, "abuf", None, u)
                dg, hg, idg, lm = brt_date(gen), brt_time(gen), str(idg), gen
            else:
                dg, hg, lm = "24/10/2026", "19:17:32", -86400.0
                idg = {"pe": "3948191"}.get(uf, str(3942300 + 211 * u))
        obj = {"ele": "6258", "t": "2", "f": "o", "dg": dg, "hg": hg, "idg": idg, "abr": abr}
        body = dumps(obj)
        res = make_resource(_jws(body) if jws else body, lm)
        self.cache[ck] = (key, res)
        return res

    # ------------------------------------------------------------------ cs.json
    def _cs_raw_res(self, uf: str) -> Resource | None:
        r = self._cs_raw.get(uf)
        if r is None:
            p = self.cs_dir / f"{uf}-p003221-cs.json"
            if not p.exists():
                return None
            body = p.read_bytes()
            r = make_resource(body, -86400 * 15)
            meta = self.cs_dir / f"{uf}-p003221-cs.meta.json"
            if meta.exists():
                m = json.loads(meta.read_text())
                r.etag = m.get("etag", r.etag)
            self._cs_raw[uf] = r
        return r

    def _cs_build_pieces(self, uf: str):
        """Serializa o cs real com marcadores no lugar de cada seção principal."""
        if uf in self._cs_pieces:
            return self._cs_pieces[uf]
        d = orjson.loads((self.cs_dir / f"{uf}-p003221-cs.json").read_bytes())
        u = UFS.index(uf)
        secs_text: list[str] = []
        secs_idx: list[int] = []
        for ab in d["abr"]:
            for mu in ab["mu"]:
                cd = int(mu["cd"])
                for z in mu["zon"]:
                    zn = int(z["cd"])
                    new = []
                    for s in z["sec"]:
                        if s.get("nsp"):
                            new.append(s)
                            continue
                        key = u * 10**13 + cd * 10**8 + zn * 10**4 + int(s["ns"])
                        i = int(np.searchsorted(self.sec_key, key))
                        if i >= self.n or self.sec_key[i] != key:
                            new.append(s)
                            continue
                        txt = dumps(s).decode()[1:]  # sem "\n" inicial
                        secs_text.append(txt[:-2])     # sem " }"
                        secs_idx.append(i)
                        new.append(f"@@S{len(secs_idx) - 1}@@")
                    z["sec"] = new
        d["dg"], d["hg"], d["idg"] = "@@DG@@", "@@HG@@", "@@IDG@@"
        full = dumps(d).decode()
        parts = re.split(r'"@@S(\d+)@@"', full)
        static = parts[0::2]
        order = [int(x) for x in parts[1::2]]
        pieces = (static, [secs_text[k] for k in order], np.array([secs_idx[k] for k in order], np.int64))
        self._cs_pieces[uf] = pieces
        return pieces

    def cs_json(self, uf: str, T: float, jws: bool = False) -> Resource | None:
        raw = self._cs_raw_res(uf)
        if raw is None:
            return None
        if self.cs_mode != "progressive":
            return make_resource(_jws(raw.body), raw.last_modified) if jws else raw
        Tc = (int(T) // self.cs_interval) * self.cs_interval
        npub = int(np.searchsorted(self.pub_sorted_uf[uf], Tc, side="right"))
        if npub == 0:
            return make_resource(_jws(raw.body), raw.last_modified) if jws else raw
        key = (npub, jws)
        ck = f"cs:{uf}:{int(jws)}"
        hit = self.cache.get(ck)
        if hit and hit[0] == key:
            return hit[1]
        static, texts, idxs = self._cs_build_pieces(uf)
        tp = self.tr.t_pub[idxs]
        out = []
        for j, st in enumerate(static[:-1]):
            out.append(st)
            t = int(tp[j])
            if t <= Tc:
                out.append(f'\n{texts[j]}, "da" : "{brt_date(t)}", "ha" : "{brt_time(t)}" }}')
            else:
                out.append(f"\n{texts[j]} }}")
        out.append(static[-1])
        # hora de geração: tick do cs em que saiu o último aux incluído (determinístico)
        last_pub = int(self.pub_sorted_uf[uf][npub - 1])
        gen = -(-last_pub // self.cs_interval) * self.cs_interval + 3
        idg = 4_000_000 + (gen - 61200) * 8 + UFS.index(uf)
        body = ("".join(out).replace("@@DG@@", brt_date(gen), 1).replace("@@HG@@", brt_time(gen), 1)
                .replace("@@IDG@@", str(idg), 1)).encode()
        res = make_resource(_jws(body) if jws else body, gen)
        self.cache[ck] = (key, res)
        return res

    # ------------------------------------------------------------------ seção
    def find_idx(self, uf: str, mun: int, zona: int, secao: int) -> int | None:
        if uf not in UFS:
            return None
        key = UFS.index(uf) * 10**13 + mun * 10**8 + zona * 10**4 + secao
        i = int(np.searchsorted(self.sec_key, key))
        if i < self.n and self.sec_key[i] == key:
            return i
        return None

    def is_sa(self, i: int) -> bool:
        return self.busa and int(self.tr.origem[i]) == 2

    def bu_name(self, i: int) -> str:
        tr = self.tr
        return bumod.bu_filename(tr.uf[i], int(tr.cd_mun[i]), int(tr.zona[i]), int(tr.secao[i]), self.is_sa(i))

    def published(self, i: int, T: float) -> bool:
        return int(self.tr.t_pub[i]) <= T

    def aux_json(self, i: int, T: float) -> Resource | None:
        tr = self.tr
        tp = int(tr.t_pub[i])
        if tp >= NEVER or tp > T:
            return None
        hit = self.aux_cache.get(i)
        if hit is not None:
            self.aux_cache.move_to_end(i)
            return hit
        tag = f"o03221{tr.uf[i]}{int(tr.cd_mun[i]):05d}{int(tr.zona[i]):04d}{int(tr.secao[i]):04d}"
        if self.is_sa(i):
            arq = [{"nm": f"{tag}-busa.dat", "tp": "busa"}]
        else:
            arq = [{"nm": f"{tag}-log.jez", "tp": "log"}, {"nm": f"{tag}-bu.dat", "tp": "bu"},
                   {"nm": f"{tag}-vota.vsc", "tp": "vota"}, {"nm": f"{tag}-rdv.dat", "tp": "rdv"}]
        tr_ = int(tr.t_recv[i])
        obj = {"dg": brt_date(tp), "hg": brt_time(tp), "idg": str(4_000_000 + (tp - 61200) * 8 + i % 7),
               "f": "o", "st": "Totalizada",
               "hashes": [{"hash": tr.hashes[i], "dr": brt_date(tr_), "hr": brt_time(tr_), "st": "Totalizado",
                           "arq": arq}]}
        res = make_resource(dumps(obj), tp)
        self.aux_cache[i] = res
        while len(self.aux_cache) > self.bu_cache_max:
            self.aux_cache.popitem(last=False)
        return res

    def bu_dat(self, i: int, T: float) -> Resource | None:
        tr = self.tr
        tp = int(tr.t_pub[i])
        if tp >= NEVER or tp > T:
            return None
        r = self.bu_cache.get(i)
        if r is not None:
            self.bu_cache.move_to_end(i)
            return r
        body = bumod.make_bu(uf=tr.uf[i], mun=int(tr.cd_mun[i]), zona=int(tr.zona[i]), secao=int(tr.secao[i]),
                             local=int(tr.local[i]), aptos=int(tr.aptos[i]), aptos_tte=int(tr.aptos_tte[i]),
                             comp=int(tr.comp[i]), l=int(tr.l[i]), o=int(tr.o[i]), b=int(tr.b[i]),
                             n=int(tr.nulos[i]), t_recv=int(tr.t_recv[i]),
                             origem=2 if self.is_sa(i) else (1 if int(tr.origem[i]) == 1 else 0),
                             salt=f"{tr.seed}|{tr.scenario}".encode())
        r = make_resource(body, tp, "application/octet-stream", 3600)
        self.bu_cache[i] = r
        while len(self.bu_cache) > self.bu_cache_max:
            self.bu_cache.popitem(last=False)
        return r

    # ------------------------------------------------------------------ status
    def progress(self, T: float) -> dict:
        K = self.tick_of(T)
        snap = self.snap(K)
        br = snap.levels["br"]
        pub = int(sum(np.searchsorted(v, T, side="right") for v in self.pub_sorted_uf.values()))
        l, o = int(br.l[0]), int(br.o[0])
        return {"secoes_totalizadas": int(br.st[0]), "secoes_total": self.n,
                "pct_totalizadas": round(100 * int(br.st[0]) / self.n, 3),
                "aux_publicados": pub, "l": l, "o": o,
                "margem_pp_apurada": round(100 * (l - o) / max(l + o, 1), 3),
                "br_congelado": self.br_tick(T)[1]}


# cabeçalho real (kid/typ/alg) copiado de research/samples/t2/br-c0001-e006258-u.jws
_JWS_HDR = b"eyJraWQiOiJzTmJ0OVFfZkxTNjV6RTFfWkxOVi1YUlJ3UFkiLCJ0eXAiOiJKT1NFIiwiYWxnIjoiRWREU0EifQ"


def _jws(body: bytes) -> bytes:
    """JWS compacto com o cabeçalho real e assinatura FALSA (64 bytes pseudoaleatórios)."""
    pl = base64.urlsafe_b64encode(body).rstrip(b"=")
    sig = base64.urlsafe_b64encode(hashlib.sha512(b"mock" + body).digest()).rstrip(b"=")
    return _JWS_HDR + b"." + pl + b"." + sig

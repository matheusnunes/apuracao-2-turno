"""Verificação ponta a ponta do mock (sobe o servidor, consulta como um coletor, compara com a verdade).

  uv run python -m mock.verify                    # speed 60, 16:55 -> 03:05 (~10 min reais)
  uv run python -m mock.verify --until 21:00      # mais curto

Checagens:
  1. BR (u.json) vai de 0 a 100% das seções; cada versão bate com a soma das seções da verdade
     no tick indicado pelo próprio arquivo (dg/hg); soma das 28 UFs = BR no mesmo tick.
  2. ab.json (BR e UF), municípios e zonas sorteados batem com a verdade; varredura final de TODOS
     os municípios e zonas.
  3. aux: 404 (XML S3) antes de t_pub, 200 depois (dr/hr = recebimento, dg/hg = publicação);
     404 "grudento" (cache de edge) e retorno a 200 após o TTL.
  4. BUs sorteados (todas as UFs, incl. busa/RED) decodificados com research/scripts/decode_bu.py
     == verdade (aptos, comparecimento, 13, 22, brancos, nulos, identificação, idEleicao 6258).
  5. 304 com If-None-Match e If-Modified-Since; gzip; CORS.
  6. cs progressivo: da/ha = hora de publicação do aux.
  7. Congelamento do BR (UFs seguem) e injeção de falhas (erro aleatório, rajadas 403/503).
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import random
import subprocess
import sys
import tempfile
import time
from collections import Counter, defaultdict
from pathlib import Path

import httpx
import numpy as np
import pyarrow.parquet as pq

from .scenario import UFS
from .state import GEN_OFF, LEVEL_EXTRA, brt_date, brt_time, parse_hhmm

ROOT = Path(__file__).resolve().parent.parent
DECODER = ROOT / "research" / "scripts" / "decode_bu.py"
OUTDIR = Path(__file__).resolve().parent / "out"


def sec_of(dg: str, hg: str) -> int:
    d = int(dg[:2]) + (0 if dg[3:5] == "10" else -100)
    h, m, s = (int(x) for x in hg.split(":"))
    return (d - 25) * 86400 + h * 3600 + m * 60 + s


class Server:
    def __init__(self, port: int, extra: list[str]):
        self.port = port
        self.base = f"http://127.0.0.1:{port}"
        self.log = open(OUTDIR / f"verify_server_{port}.log", "w")
        cmd = [sys.executable, "-m", "mock.server", "--port", str(port), "--status-every", "15", *extra]
        self.p = subprocess.Popen(cmd, cwd=ROOT, stdout=self.log, stderr=subprocess.STDOUT)
        for _ in range(240):
            try:
                if httpx.get(self.base + "/__mock/health", timeout=1).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            if self.p.poll() is not None:
                raise RuntimeError(f"servidor saiu; veja {self.log.name}")
            time.sleep(0.5)
        raise RuntimeError("servidor não subiu")

    def stop(self):
        self.p.terminate()
        try:
            self.p.wait(10)
        except subprocess.TimeoutExpired:
            self.p.kill()
        self.log.close()


class Truth:
    def __init__(self, path: Path, tick: int):
        t = pq.read_table(path).to_pydict()
        self.tick = tick
        self.uf = np.array(t["uf"])
        self.uf_i = np.array([UFS.index(u) for u in self.uf])
        self.mun = np.array(t["cd_mun"])
        self.zona = np.array(t["zona"])
        self.secao = np.array(t["secao"])
        self.inst = np.array(t["instalada"])
        self.cols = {k: np.array(t[k], dtype=np.int64) for k in
                     ("aptos2", "comp2", "l2", "o2", "b2", "n2", "t_tot_s", "t_recv_s", "t_pub_s", "aptos_tte2",
                      "origem")}
        self.hash = t["hash"]
        self.n = len(self.uf)
        self.inst_idx = np.nonzero(self.inst)[0]
        self._ufm = {u: self.uf == u for u in UFS}

    def expected(self, mask: np.ndarray, K: int) -> dict:
        c = self.cols
        inc = mask & (c["t_tot_s"] <= K * self.tick)
        ins = inc & self.inst
        return {"ts": int(mask.sum()), "te": int(c["aptos2"][mask].sum()), "st": int(inc.sum()),
                "si": int(ins.sum()), "est": int(c["aptos2"][inc].sum()), "esi": int(c["aptos2"][ins].sum()),
                "c": int(c["comp2"][inc].sum()), "vb": int(c["b2"][inc].sum()), "vn": int(c["n2"][inc].sum()),
                "l": int(c["l2"][inc].sum()), "o": int(c["o2"][inc].sum())}

    def mask(self, uf=None, mun=None, zona=None):
        m = self._ufm[uf].copy() if uf is not None else np.ones(self.n, bool)
        if mun is not None:
            m &= self.mun == mun
        if zona is not None:
            m &= self.zona == zona
        return m


def u_vals(d: dict) -> dict:
    s, e, v = d["s"], d["e"], d["v"]
    cand = {c["n"]: int(c["vap"]) for a in d["carg"][0]["agr"] for p in a["par"] for c in p["cand"]}
    return {"ts": int(s["ts"]), "st": int(s["st"]), "si": int(s["si"]), "te": int(e["te"]), "est": int(e["est"]),
            "esi": int(e["esi"]), "c": int(e["c"]), "vb": int(v["vb"]), "vn": int(v["tvn"]),
            "l": cand["13"], "o": cand["22"]}


def ab_vals(e: dict) -> dict:
    s, ee = e["s"], e["e"]
    return {"ts": int(s["ts"]), "st": int(s["st"]), "si": int(s["si"]), "te": int(ee["te"]),
            "est": int(ee["est"]), "esi": int(ee["esi"]), "c": int(ee["c"])}


class Verifier:
    def __init__(self, srv: Server, truth: Truth, tick: int):
        self.srv = srv
        self.tr = truth
        self.tick = tick
        self.cl = httpx.Client(base_url=srv.base + "/oficial/ele2026/", timeout=30,
                               headers={"Accept-Encoding": "gzip", "User-Agent": "mock-verify"})
        self.fail: list[str] = []
        self.n = Counter()
        self.ev: dict = defaultdict(list)
        self.rng = random.Random(42)
        self.probed: set[int] = set()
        self.bu_files: list[tuple[int, str]] = []
        self.tmp = Path(tempfile.mkdtemp(prefix="mockbu-", dir=OUTDIR))

    def check(self, ok: bool, msg: str):
        self.n["checks"] += 1
        if not ok:
            self.n["fails"] += 1
            if len(self.fail) < 50:
                self.fail.append(msg)

    def sim_T(self, r: httpx.Response) -> int:
        dg, hg = r.headers["X-Mock-Sim-Time"].split()
        return sec_of(dg, hg)

    def get(self, path, **kw):
        for _ in range(6):
            r = self.cl.get(path, **kw)
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(0.2)
                continue
            return r
        return r

    def K_of(self, d: dict, level: str) -> int:
        if not d["dg"].endswith("/2026") or d["dg"][3:5] != "10" or int(d["dg"][:2]) < 25:
            return -10**6
        gen = sec_of(d["dg"], d["hg"])
        return (gen - GEN_OFF - LEVEL_EXTRA[level]) // self.tick

    def cmp(self, got: dict, exp: dict, what: str):
        bad = {k: (got[k], exp[k]) for k in got if k in exp and got[k] != exp[k]}
        self.check(not bad, f"{what}: {bad}")
        return not bad

    # ------------------------------------------------------------ agregados
    def check_u(self, path: str, level: str, mask, what: str):
        r = self.get(path)
        self.check(r.status_code == 200, f"{what} HTTP {r.status_code}")
        if r.status_code != 200:
            return None, None
        d = r.json()
        K = self.K_of(d, level)
        exp = self.tr.expected(mask, K)
        self.cmp(u_vals(d), exp, f"{what} K={K}")
        self.n[f"u_{level}"] += 1
        return d, K

    def check_ab_uf(self, uf: str):
        r = self.get(f"6258/dados/{uf}/{uf}-e006258-ab.json")
        d = r.json()
        K = self.K_of(d, "abuf")
        ent = d["abr"]
        self.check(ent[-1]["tpabr"] == "uf" and ent[-1]["cdabr"] == uf, f"ab {uf}: última entrada não é a UF")
        um = self.tr.mask(uf=uf)
        self.cmp(ab_vals(ent[-1]), self.tr.expected(um, K), f"ab {uf} UF")
        # soma dos municípios = UF; amostra de municípios vs verdade
        tot = Counter()
        for e in ent[:-1]:
            for k, v in ab_vals(e).items():
                tot[k] += v
        self.cmp(dict(tot), ab_vals(ent[-1]), f"ab {uf} soma municípios")
        for e in self.rng.sample(ent[:-1], min(5, len(ent) - 1)):
            self.cmp(ab_vals(e), self.tr.expected(um & (self.tr.mun == int(e["cdabr"])), K),
                     f"ab {uf} mun {e['cdabr']}")
        self.n["ab_uf"] += 1

    # ------------------------------------------------------------ aux / BU
    def aux_path(self, i):
        tr = self.tr
        uf, m, z, s = tr.uf[i], int(tr.mun[i]), int(tr.zona[i]), int(tr.secao[i])
        return f"arquivo-urna/3221/dados/{uf}/{m:05d}/{z:04d}/{s:04d}/p003221-{uf}-m{m:05d}-z{z:04d}-s{s:04d}-aux.json"

    def probe_aux(self, k: int, T_now: int):
        tp = self.tr.cols["t_pub_s"]
        cand = self.tr.inst_idx
        for _ in range(k):
            i = int(cand[self.rng.randrange(len(cand))])
            if i in self.probed:
                continue
            self.probed.add(i)
            r = self.get(self.aux_path(i))
            T = self.sim_T(r)
            pub = int(tp[i])
            if pub > T:
                self.check(r.status_code == 404, f"aux {i} antes de t_pub ({pub}>{T}) deu {r.status_code}")
                self.check(b"NoSuchKey" in r.content, "corpo do 404 não é XML S3")
                self.n["aux_404_antes"] += 1
            else:
                self.check(r.status_code == 200, f"aux {i} depois de t_pub ({pub}<={T}) deu {r.status_code}")
                if r.status_code != 200:
                    continue
                self.n["aux_200_depois"] += 1
                a = r.json()
                h = a["hashes"][0]
                self.check(sec_of(a["dg"], a["hg"]) == pub, f"aux {i} dg/hg != t_pub")
                self.check(sec_of(h["dr"], h["hr"]) == int(self.tr.cols["t_recv_s"][i]), f"aux {i} dr/hr != t_recv")
                self.check(h["hash"] == self.tr.hash[i], f"aux {i} hash")
                self.ev["lag_obs"].append((self.tr.uf[i], (pub - int(self.tr.cols["t_recv_s"][i])) / 60))
                if len(self.bu_files) < 600:
                    arq = [x for x in h["arq"] if x["tp"] in ("bu", "busa")][0]
                    p = self.aux_path(i).rsplit("/", 1)[0] + f"/{h['hash']}/{arq['nm']}"
                    rb = self.get(p)
                    self.check(rb.status_code == 200, f"bu {i} HTTP {rb.status_code}")
                    if rb.status_code == 200:
                        f = self.tmp / arq["nm"]
                        f.write_bytes(rb.content)
                        self.bu_files.append((i, str(f)))

    def sticky_test_start(self, T: int):
        tp = self.tr.cols["t_pub_s"]
        sel = np.nonzero((tp > T + 20) & (tp < T + 90))[0][:8]
        out = []
        for i in sel:
            i = int(i)
            self.probed.add(i)
            r = self.get(self.aux_path(i))
            self.check(r.status_code == 404, f"sticky: aux {i} deveria ser 404 antes de t_pub")
            out.append(i)
        return out, time.time()

    def decode_bus(self):
        if not self.bu_files:
            self.check(False, "nenhum BU baixado")
            return
        res = subprocess.run(["uv", "run", "python", "-I", str(DECODER), *[f for _, f in self.bu_files]],
                             cwd=DECODER.parent, capture_output=True, text=True)
        lines = [json.loads(x) for x in res.stdout.splitlines() if x.strip()]
        self.check(len(lines) == len(self.bu_files), f"decoder: {len(lines)}/{len(self.bu_files)} ok; {res.stderr[:300]}")
        by_name = {d["arquivo"]: d for d in lines}
        c = self.tr.cols
        tipos = Counter()
        for i, f in self.bu_files:
            d = by_name.get(os.path.basename(f))
            if d is None:
                self.check(False, f"BU {f} não decodificou")
                continue
            el = d["eleicoes"][0]
            cg = el["cargos"][0]
            got = {"mun": d["municipio"], "zona": d["zona"], "secao": d["secao"], "ele": el["idEleicao"],
                   "pleito": d["idEleitoral"][1], "aptos": cg["aptos"], "tte": el["aptosTTE"],
                   "comp": cg["comparecimento"], "l": cg["nominais"].get("13", 0), "o": cg["nominais"].get("22", 0),
                   "b": cg["brancos"], "n": cg["nulos"], "ncargos": sum(len(e["cargos"]) for e in d["eleicoes"])}
            exp = {"mun": int(self.tr.mun[i]), "zona": int(self.tr.zona[i]), "secao": int(self.tr.secao[i]),
                   "ele": 6258, "pleito": 3221, "aptos": int(c["aptos2"][i]), "tte": int(c["aptos_tte2"][i]),
                   "comp": int(c["comp2"][i]), "l": int(c["l2"][i]), "o": int(c["o2"][i]), "b": int(c["b2"][i]),
                   "n": int(c["n2"][i]), "ncargos": 1}
            if self.cmp(got, exp, f"BU idx {i}"):
                self.n["bu_ok"] += 1
            tipos[(d["tipoBU"], d["urna"]["tipoArquivo"])] += 1
        self.ev["bu_tipos"] = {f"{a}/{b}": v for (a, b), v in tipos.items()}
        self.ev["bu_ufs"] = len({self.tr.uf[i] for i, _ in self.bu_files})

    # ------------------------------------------------------------ cs
    def check_cs(self, uf: str):
        r = self.get(f"arquivo-urna/3221/config/{uf}/{uf}-p003221-cs.json")
        T = self.sim_T(r)
        d = r.json()
        tp = self.tr.cols["t_pub_s"]
        um = self.tr.uf == uf
        idx_of = {(int(m), int(z), int(s)): int(i) for i, m, z, s in
                  zip(np.nonzero(um)[0], self.tr.mun[um], self.tr.zona[um], self.tr.secao[um])}
        nda = 0
        bad = 0
        for mu in d["abr"][0]["mu"]:
            for z in mu["zon"]:
                for s in z["sec"]:
                    if s.get("nsp"):
                        bad += "da" in s
                        continue
                    i = idx_of[(int(mu["cd"]), int(z["cd"]), int(s["ns"]))]
                    if "da" in s:
                        nda += 1
                        bad += sec_of(s["da"], s["ha"]) != int(tp[i]) or int(tp[i]) > T
                    else:
                        bad += 0 <= int(tp[i]) <= T - 2 * 120 - 1
        self.check(bad == 0, f"cs {uf}: {bad} seções com da/ha inconsistente")
        self.ev["cs"].append((uf, brt_time(T), nda, int(um.sum())))
        return nda


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8799)
    ap.add_argument("--speed", type=float, default=60)
    ap.add_argument("--start", default="16:55")
    ap.add_argument("--until", default="03:05")
    ap.add_argument("--scenario", default="swing2022")
    ap.add_argument("--skip-faults", action="store_true")
    ap.add_argument("--order", default="real", choices=["real", "nordeste-first", "random"])
    a = ap.parse_args(argv)
    OUTDIR.mkdir(exist_ok=True)
    tick = 60
    freeze = "19:14-20:04"
    srv = Server(a.port, ["--scenario", a.scenario, "--speed", str(a.speed), "--start", a.start,
                          "--cs-mode", "progressive", "--freeze-br", freeze, "--order", a.order])
    report: dict = {}
    try:
        from .scenario import truth_path
        tr = Truth(truth_path(a.scenario, a.order), tick)
        v = Verifier(srv, tr, tick)
        until = parse_hhmm(a.until)
        f0, f1 = parse_hhmm("19:14"), parse_hhmm("20:04")
        br_etag = None
        br_lm = None
        last_st = -1
        timeline = []
        sticky = None
        frozen_hg, uf_hg_in_freeze = set(), set()
        it = 0
        # gzip × identidade, CORS
        r1 = v.cl.get("6258/dados/br/br-c0001-e006258-u.json", headers={"Origin": "https://example.org"})
        r2 = httpx.get(srv.base + "/oficial/ele2026/6258/dados/br/br-c0001-e006258-u.json",
                       headers={"Accept-Encoding": "identity"})
        v.check(r1.headers.get("content-encoding") == "gzip", "sem gzip com Accept-Encoding: gzip")
        v.check(r1.headers.get("access-control-allow-origin") == "https://example.org", "CORS não ecoa Origin")
        v.check(r2.headers.get("content-encoding") is None, "gzip sem pedir")
        v.n["gzip_ok"] += 1
        while True:
            it += 1
            # --- BR com If-None-Match
            hdr = {"If-None-Match": br_etag} if br_etag else {}
            r = v.get("6258/dados/br/br-c0001-e006258-u.json", headers=hdr)
            T = v.sim_T(r)
            if r.status_code == 304:
                v.n["br_304"] += 1
            else:
                v.check(r.status_code == 200, f"BR HTTP {r.status_code}")
                br_etag, br_lm = r.headers["etag"], r.headers["last-modified"]
                d = r.json()
                K = v.K_of(d, "br")
                got = u_vals(d)
                v.cmp(got, tr.expected(np.ones(tr.n, bool), K), f"BR K={K}")
                v.check(got["st"] >= last_st, "BR st diminuiu")
                last_st = got["st"]
                timeline.append((brt_time(T), d["hg"], got["st"], d["s"]["pst"], got["l"], got["o"], d["and"]))
                if f0 + 120 <= T < f1:
                    frozen_hg.add(d["hg"])
            # If-Modified-Since
            if br_lm and it % 5 == 0:
                r = v.cl.get("6258/dados/br/br-c0001-e006258-u.json", headers={"If-Modified-Since": br_lm})
                v.check(r.status_code in (200, 304), "IMS")
                v.n[f"br_ims_{r.status_code}"] += 1
            # --- UFs: cada uma vs verdade; soma das UFs = BR se o BR não mudou durante a varredura
            pre = v.get("6258/dados/br/br-c0001-e006258-u.json").json()
            ufd = []
            for uf in UFS:
                d, K = v.check_u(f"6258/dados/{uf}/{uf}-c0001-e006258-u.json", "uf", tr.mask(uf=uf), f"UF {uf}")
                if d:
                    ufd.append(d)
                    if f0 + 120 <= T < f1 - 120:
                        uf_hg_in_freeze.add((uf, d["hg"]))
            post = v.get("6258/dados/br/br-c0001-e006258-u.json").json()
            if f0 + 120 <= T < f1:
                frozen_hg.update((pre["hg"], post["hg"]))
            if pre["hg"] == post["hg"] and len(ufd) == len(UFS) and not (f0 <= T < f1 + 120):
                tot = Counter()
                for d in ufd:
                    for k2, val in u_vals(d).items():
                        tot[k2] += val
                b = u_vals(post)
                v.cmp({k2: tot[k2] for k2 in b}, b, f"soma UFs = BR (hg {post['hg']})")
                v.n["sum_uf_eq_br"] += 1
            # --- ab BR
            rab = v.get("6258/dados/br/br-e006258-ab.json")
            dab = rab.json()
            Kab = v.K_of(dab, "abbr")
            for e in dab["abr"]:
                m = tr.mask() if e["cdabr"] == "br" else tr.mask(uf=e["cdabr"])
                v.cmp(ab_vals(e), tr.expected(m, Kab), f"br-ab {e['cdabr']}")
            # --- UF ab, municípios e zonas sorteados
            v.check_ab_uf(v.rng.choice(UFS))
            for _ in range(4):
                i = v.rng.randrange(tr.n)
                uf, mun, z = tr.uf[i], int(tr.mun[i]), int(tr.zona[i])
                v.check_u(f"6258/dados/{uf}/{uf}{mun:05d}-c0001-e006258-u.json", "mu", tr.mask(uf, mun), f"mun {uf}{mun}")
                v.check_u(f"6258/dados/{uf}/{uf}{mun:05d}-z{z:04d}-c0001-e006258-u.json", "zona",
                          tr.mask(uf, mun, z), f"zona {uf}{mun}-{z}")
            # --- aux/BU
            v.probe_aux(25, T)
            if sticky is None and T > parse_hhmm("17:40"):
                sticky = v.sticky_test_start(T)
                if sticky[0]:
                    v.ev["sticky"].append(f"{len(sticky[0])} aux sondados às {brt_time(T)} (404)")
                else:
                    sticky = None if T < parse_hhmm("02:00") else ("done", None)
            elif sticky is not None and sticky[0] != "done":
                ids, t0 = sticky
                age = time.time() - t0
                tp = tr.cols["t_pub_s"]
                if age < 50 and all(tp[i] < T for i in ids):
                    codes = Counter(v.get(v.aux_path(i)).status_code for i in ids)
                    v.check(set(codes) == {404}, f"sticky: esperado 404 grudado, veio {codes}")
                    v.ev["sticky"].append(f"{brt_time(T)} (t_pub já passou, {age:.0f}s reais): {dict(codes)}")
                if age > 62:
                    codes = Counter(v.get(v.aux_path(i)).status_code for i in ids)
                    v.check(set(codes) == {200}, f"sticky: esperado 200 após TTL, veio {codes}")
                    v.ev["sticky"].append(f"{brt_time(T)} ({age:.0f}s reais): {dict(codes)}")
                    sticky = ("done", None)
            # --- cs progressivo
            if it % 6 == 0:
                v.check_cs(v.rng.choice(["pe", "sp", "ac", "zz", "mg", "ba"]))
            if T >= until:
                break
            time.sleep(1.0)
        # ---- BUs especiais: todas as seções SA (busa) e RED, se já publicadas
        tp = tr.cols["t_pub_s"]
        for i in np.nonzero(np.isin(tr.cols["origem"], (1, 2)) & tr.inst & (tp >= 0) & (tp <= T))[0]:
            i = int(i)
            r = v.get(v.aux_path(i))
            v.check(r.status_code == 200, f"aux especial {i} HTTP {r.status_code}")
            if r.status_code != 200:
                continue
            h = r.json()["hashes"][0]
            arq = [x for x in h["arq"] if x["tp"] in ("bu", "busa")][0]
            v.check(arq["tp"] == ("busa" if int(tr.cols["origem"][i]) == 2 else "bu"), f"tp do aux {i}")
            rb = v.get(v.aux_path(i).rsplit("/", 1)[0] + f"/{h['hash']}/{arq['nm']}")
            v.check(rb.status_code == 200, f"bu especial {i} HTTP {rb.status_code}")
            if rb.status_code == 200:
                f = v.tmp / arq["nm"]
                f.write_bytes(rb.content)
                v.bu_files.append((i, str(f)))
                v.n["bu_especiais"] += 1
        # ---- final (só se a noite inteira foi coberta)
        report["final_coberto"] = bool(T >= int(tr.cols["t_tot_s"].max()) + 120)
        r = v.get("6258/dados/br/br-c0001-e006258-u.json")
        d = r.json()
        final = u_vals(d)
        cand = {c["n"]: c for a2 in d["carg"][0]["agr"] for p in a2["par"] for c in p["cand"]}
        winner = "13" if final["l"] > final["o"] else "22"
        report["br_final"] = {"st": final["st"], "lula": final["l"], "flavio": final["o"], "and": d["and"],
                              "tf": d["tf"], "e13": cand["13"]["e"], "st13": cand["13"]["st"],
                              "e22": cand["22"]["e"], "st22": cand["22"]["st"]}
        if report["final_coberto"]:
            v.check(final["st"] == tr.n and d["and"] == "f" and d["tf"] == "s", "BR final não está 100%/f/s")
            v.check(cand[winner]["e"] == "s" and cand[winner]["st"] == "Eleito", "vencedor sem e=s/st=Eleito")
            v.cmp(final, tr.expected(np.ones(tr.n, bool), 10**6), "BR final")
    except BaseException:
        srv.stop()
        raise
    return _finish(a, srv, v, tr, timeline, frozen_hg, uf_hg_in_freeze, report)


def _sweep(v: Verifier, tr: Truth):
    """Todos os municípios e zonas no estado final vs verdade (agrupado com numpy)."""
    c = tr.cols
    key_m = tr.uf_i.astype(np.int64) * 100000 + tr.mun
    key_z = key_m * 10000 + tr.zona
    out = {}
    for name, key, nkey in (("mu", key_m, None), ("zona", key_z, None)):
        uk, inv = np.unique(key, return_inverse=True)
        sums = {f: np.bincount(inv, weights=c[col], minlength=len(uk)).astype(np.int64)
                for f, col in (("te", "aptos2"), ("c", "comp2"), ("l", "l2"), ("o", "o2"), ("vb", "b2"), ("vn", "n2"))}
        sums["ts"] = np.bincount(inv, minlength=len(uk))
        bad = 0
        for g, k in enumerate(uk):
            uf = UFS[int(k // (100000 if name == "mu" else 10**9))]
            if name == "mu":
                mun = int(k % 100000)
                path = f"6258/dados/{uf}/{uf}{mun:05d}-c0001-e006258-u.json"
            else:
                mun, z = int(k // 10000 % 100000), int(k % 10000)
                path = f"6258/dados/{uf}/{uf}{mun:05d}-z{z:04d}-c0001-e006258-u.json"
            r = v.get(path)
            if r.status_code != 200:
                bad += 1
                v.check(False, f"varredura {path} HTTP {r.status_code}")
                continue
            got = u_vals(r.json())
            exp = {f: int(sums[f][g]) for f in sums}
            exp["st"] = exp["ts"]
            if not v.cmp({f: got[f] for f in exp}, exp, f"varredura {path}"):
                bad += 1
        out[name] = {"arquivos": len(uk), "divergentes": bad}
    return out


def _faults(port: int, v: Verifier) -> dict:
    srv = Server(port, ["--speed", "60", "--start", "19:10:30", "--error-rate", "0.1",
                        "--burst-403", "19:12-19:13", "--burst", "503@19:14-19:15@0.5", "--quiet"])
    try:
        cl = httpx.Client(base_url=srv.base, timeout=10)
        by_min: dict[str, Counter] = defaultdict(Counter)
        t_end = time.time() + 330
        while time.time() < t_end:
            r = cl.get("/oficial/ele2026/6258/dados/sp/sp-c0001-e006258-u.json")
            T = sec_of(*r.headers["X-Mock-Sim-Time"].split())
            by_min[brt_time(T)[:5]][r.status_code] += 1
            if brt_time(T)[:5] >= "19:16":
                break
        out = {}
        for mm, cnt in sorted(by_min.items()):
            tot = sum(cnt.values())
            out[mm] = {"n": tot, "erro_pct": round(100 * (tot - cnt[200] - cnt[304]) / tot, 1), **{str(k): x for k, x in cnt.items()}}
        b = out.get("19:12", {})
        v.check(b.get("403", 0) == b.get("n", -1), f"rajada 403 incompleta: {b}")
        b = out.get("19:14", {})
        v.check(35 <= b.get("erro_pct", 0) <= 75, f"rajada 503@0.5 fora do esperado: {b}")
        b = out.get("19:11", {})
        v.check(3 <= b.get("erro_pct", 0) <= 20, f"error-rate 0.1 fora do esperado: {b}")
        return out
    finally:
        srv.stop()


def _finish(a, srv, v, tr, timeline, frozen_hg, uf_hg_in_freeze, report):
    try:
        if report.get("final_coberto"):
            report["sweep_final"] = _sweep(v, tr)
    finally:
        srv.stop()
    v.decode_bus()
    import shutil
    shutil.rmtree(v.tmp, ignore_errors=True)
    # congelamento
    if frozen_hg:
        v.check(len(frozen_hg) == 1, f"BR deveria ficar congelado em 19:16–20:04; hg vistos: {sorted(frozen_hg)}")
        ufs_moving = len(uf_hg_in_freeze) > len({u for u, _ in uf_hg_in_freeze})
        v.check(ufs_moving, "UFs deveriam seguir atualizando durante o congelamento do BR")
    if not a.skip_faults:
        report["faults"] = _faults(a.port + 1, v)
    # lags observados
    lag = defaultdict(list)
    for uf, m in v.ev["lag_obs"]:
        lag["sp" if uf == "sp" else ("ne" if uf in ("al", "ba", "ce", "ma", "pb", "pe", "pi", "rn", "se") else "outras")].append(m)
    report["lag_min_observado"] = {k: {"n": len(x), "p10": round(float(np.percentile(x, 10)), 1),
                                       "mediana": round(float(np.median(x)), 1),
                                       "p90": round(float(np.percentile(x, 90)), 1)} for k, x in lag.items()}
    sel = [timeline[0]] + [t for t in timeline if t[1][-2:] != "xx"][:: max(1, len(timeline) // 14)] + [timeline[-1]]
    report["br_timeline"] = [{"sim": t[0], "hg": t[1], "st": t[2], "pst": t[3], "lula": t[4], "flavio": t[5],
                              "and": t[6]} for t in sel]
    report["br_freeze_hg"] = sorted(frozen_hg)
    report["counts"] = dict(v.n)
    report["evidence"] = {k: (val if k != "lag_obs" else len(val)) for k, val in v.ev.items()}
    report["failures"] = v.fail
    report["ok"] = v.n["fails"] == 0
    p = OUTDIR / f"verify_report_{a.scenario}{'' if a.order == 'real' else '-' + a.order}.json"
    p.write_text(json.dumps(report, indent=1, ensure_ascii=False, default=str))
    print(json.dumps({k: report.get(k) for k in ("ok", "counts", "br_final", "sweep_final", "lag_min_observado")},
                     indent=1, ensure_ascii=False))
    print("timeline BR:")
    for t in report["br_timeline"]:
        print(f"  sim {t['sim']} hg {t['hg']} st {t['st']:>6} ({t['pst']}%) Lula {t['lula']:>9} Flávio {t['flavio']:>9} {t['and']}")
    if "faults" in report:
        print("faults:", json.dumps(report["faults"]))
    print("sticky:", report["evidence"].get("sticky"))
    print("cs:", report["evidence"].get("cs"))
    print("bu:", report["evidence"].get("bu_tipos"), "UFs:", report["evidence"].get("bu_ufs"))
    if v.fail:
        print("FALHAS:", *v.fail[:20], sep="\n  ")
    print(f"relatório: {p}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())

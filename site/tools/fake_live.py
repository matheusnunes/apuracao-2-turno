"""Fixture da tela: escreve arquivos ao vivo no formato do contrato (manifest + agg.<dv>.json + sections.<dv>.bin),
a partir da base real do 1T, com uma apuração falsa progressiva. NÃO é o mock do TSE nem o coletor.

O analytics.json vem do analytics REAL (`--with-analytics` sobe `python -m analytics.run` apontado para a mesma pasta).

  # replay acelerado (20× = 17h→24h em ~21 min), com analytics real, numa pasta separada da do coletor
  uv run python site/tools/fake_live.py --out-dir site/data/live-fake --speed 20 --with-analytics
  # abrir http://127.0.0.1:8000/?live=data/live-fake/

  # um retrato fixo às 18:30 (e sai)
  uv run python site/tools/fake_live.py --out-dir site/data/live-fake --at 18:30 --with-analytics

  # falhas de propósito (horários simulados):
  #   --stall-at 18:00 --stall-secs 150    coletor para de escrever o manifest por 150 s (reais)
  #   --tse-freeze-at 18:20 --tse-freeze-secs 400   agregados param de mudar (heartbeat continua)
  #   --br-frozen-at 18:40                 health.br_frozen=true por 5 min simulados
  #   --drop-files                         apaga de vez em quando o agg da versão anterior (404 no front)

Cenários (--scenario): virada (padrão: Lula vira tarde), empate, flavio.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

import duckdb
import numpy as np
import orjson

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "data" / "base"
BRT = dt.timezone(dt.timedelta(hours=-3))
DAY = dt.date(2026, 10, 25)
UF_ORDER = ["ac", "al", "am", "ap", "ba", "ce", "df", "es", "go", "ma", "mg", "ms", "mt", "pa", "pb", "pe",
            "pi", "pr", "rj", "rn", "ro", "rr", "rs", "sc", "se", "sp", "to", "zz"]
SCEN = {  # fração de X1 que vai a L; deslocamento regional; Δ comparecimento (fração dos aptos)
    "virada": dict(sl=0.625, reg={"N": 0.03, "NE": 0.07, "CO": -0.05, "SE": 0.0, "S": -0.05, "ZZ": -0.02}, dturn=0.008),
    "empate": dict(sl=0.592, reg={"N": 0.02, "NE": 0.05, "CO": -0.04, "SE": 0.0, "S": -0.04, "ZZ": -0.02}, dturn=0.004),
    "flavio": dict(sl=0.50, reg={"N": 0.0, "NE": 0.03, "CO": -0.04, "SE": -0.01, "S": -0.04, "ZZ": -0.02}, dturn=0.0),
}
BU_MED_MIN = {"sp": 95, "rj": 50, "mg": 40, "pe": 8}  # atraso mediano do BU publicado (min); resto 25


def iso(t: float) -> str:
    return dt.datetime.fromtimestamp(t, BRT).isoformat(timespec="seconds")


def hhmm_to_s(s: str) -> int:
    h, m = s.split(":")
    return int(h) * 3600 + int(m) * 60


def atomic_write(path: Path, data: bytes) -> None:
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def prune(out: Path, kind: str, keep: int = 3) -> None:
    rx = re.compile(r"^agg\.(\d+)\.json$" if kind == "agg" else r"^sections\.(\d+)\.bin$")
    found = sorted(((int(m.group(1)), p) for p in out.iterdir() if (m := rx.match(p.name))), reverse=True)
    for _, p in found[keep:]:
        p.unlink(missing_ok=True)


class World:
    def __init__(self, scenario: str, seed: int):
        con = duckdb.connect()
        d = con.sql(f"""select idx, uf, regiao, cd_mun, zona, aptos1, comp1, l1, o1, x1, b1, n1, aptos2_cad, t_tot1
                        from '{BASE / 'secoes.parquet'}' order by idx""").fetchnumpy()
        self.n = N = len(d["idx"])
        rng = np.random.default_rng(seed)
        self.uf = np.array([UF_ORDER.index(str(u)) for u in d["uf"]], dtype=np.int16)
        reg = np.array([str(r) for r in d["regiao"]])
        zkey = d["cd_mun"].astype(np.int64) * 10000 + d["zona"].astype(np.int64)
        self.z_keys, self.sec_zone = np.unique(zkey, return_inverse=True)
        self.Z = len(self.z_keys)
        self.m_keys, self.zone_mun = np.unique(self.z_keys // 10000, return_inverse=True)
        self.M = len(self.m_keys)
        self.mun_uf = np.zeros(self.M, dtype=np.int16)
        self.mun_uf[self.zone_mun[self.sec_zone]] = self.uf
        A1 = d["aptos1"].astype(float)
        C1 = d["comp1"].astype(float)
        L1, O1, X1 = (d[k].astype(float) for k in ("l1", "o1", "x1"))
        B1, N1 = d["b1"].astype(float), d["n1"].astype(float)
        self.A2 = d["aptos2_cad"].astype(np.int64)
        sc = SCEN[scenario]
        zn = rng.normal(0, 0.035, self.Z)[self.sec_zone] + rng.normal(0, 0.02, N)
        sl = np.clip(sc["sl"] + np.array([sc["reg"][r] for r in reg]) + zn, 0.05, 0.95)
        AB1 = np.maximum(A1 - C1, 0)
        dturn = (sc["dturn"] + rng.normal(0, 0.006, self.Z)[self.sec_zone]) * A1
        dturn = np.clip(dturn, -0.5 * C1, 0.8 * AB1)
        bn_drop = 0.08 * (B1 + N1)
        L2 = L1 + X1 * sl + 0.52 * (dturn + bn_drop)
        O2 = O1 + X1 * (1 - sl) * 0.86 + 0.48 * (dturn + bn_drop)
        BN2 = B1 + N1 - bn_drop + X1 * (1 - sl) * 0.05
        AB2 = AB1 + X1 * (1 - sl) * 0.09 - dturn
        P = np.stack([L2, O2, BN2, np.maximum(AB2, 0)], 1)
        P /= np.maximum(P.sum(1), 1e-9)[:, None]
        cnt = np.floor(P * self.A2[:, None] + rng.random(P.shape)).astype(np.int64)
        cnt[:, 3] = self.A2 - cnt[:, :3].sum(1)
        neg = cnt[:, 3] < 0
        cnt[neg, 1] += cnt[neg, 3]
        cnt[neg, 3] = 0
        cnt = np.maximum(cnt, 0)
        self.l2, self.o2, bn2, self.ab2 = (cnt[:, j] for j in range(4))
        self.b2 = np.floor(bn2 * 0.37).astype(np.int64)
        self.n2 = bn2 - self.b2
        self.c2 = self.l2 + self.o2 + self.b2 + self.n2
        # horário de totalização no 2T: hora-do-dia do 1T + ruído por zona; BU publicado depois
        tod1 = (d["t_tot1"].astype(np.int64) - 3 * 3600) % 86400
        jit = rng.normal(0, 240, self.Z)[self.sec_zone] + rng.normal(0, 60, N)
        self.t_tot = np.maximum(17 * 3600 + 30, tod1 + jit).astype(np.int64)
        med = np.array([BU_MED_MIN.get(u, 25) for u in UF_ORDER])[self.uf] * 60.0
        lag = med * np.exp(rng.normal(0, 0.7, self.Z)[self.sec_zone] + rng.normal(0, 0.35, N))
        self.t_bu = (self.t_tot + 120 + lag).astype(np.int64)
        self.t_bu[rng.random(N) < 0.04] = 10 ** 9  # alguns BUs nunca chegam
        self.zone_ts = np.bincount(self.sec_zone, minlength=self.Z)
        self.zone_te = np.bincount(self.sec_zone, weights=self.A2, minlength=self.Z).astype(np.int64)
        print(f"mundo: {N:,} seções, {self.Z:,} zonas, {self.M:,} municípios · cenário {scenario} · "
              f"verdade L {self.l2.sum():,} × O {self.o2.sum():,} (margem {self.l2.sum() - self.o2.sum():+,})", flush=True)

    def agg(self, sim_s: int):
        tot = self.t_tot <= sim_s
        w = tot.astype(np.int64)
        cols = {"st": w, "est": self.A2 * w, "c": self.c2 * w, "a": self.ab2 * w, "vb": self.b2 * w,
                "tvn": self.n2 * w, "l": self.l2 * w, "o": self.o2 * w}
        Zs = {k: np.bincount(self.sec_zone, weights=v, minlength=self.Z).astype(np.int64) for k, v in cols.items()}
        Zs["ts"], Zs["te"] = self.zone_ts, self.zone_te
        keys = ["ts", "st", "te", "est", "c", "a", "vb", "tvn", "l", "o"]
        zm = np.stack([Zs[k] for k in keys], 1)
        mm = np.zeros((self.M, len(keys)), dtype=np.int64)
        np.add.at(mm, self.zone_mun, zm)
        um = np.zeros((len(UF_ORDER), len(keys)), dtype=np.int64)
        np.add.at(um, self.mun_uf, mm)
        br = um.sum(0)
        h = sim_s
        ht = f"{h // 3600 % 24:02d}:{h % 3600 // 60:02d}:{h % 60:02d}"

        def A(row):
            d = {k: int(v) for k, v in zip(keys, row)}
            d.update(dt=DAY.strftime("%d/%m/%Y"), ht=ht, **{"and": "n" if d["st"] == 0 else ("f" if d["st"] == d["ts"] else "p")})
            return d

        out = {"br": A(br), "uf": {UF_ORDER[i]: A(um[i]) for i in range(len(UF_ORDER))},
               "mun": {str(int(self.m_keys[i])): A(mm[i]) for i in range(self.M)},
               "zona": {f"{int(k // 10000)}-{int(k % 10000):04d}": A(zm[i]) for i, k in enumerate(self.z_keys)
                        if zm[i, 1] > 0 or True}}
        return out, int(tot.sum())

    def sections(self, sim_s: int) -> tuple[bytes, int]:
        bu = self.t_bu <= sim_s
        arr = np.zeros((self.n, 8), dtype="<i4")
        arr[:, 7] = -1
        arr[bu, 0] = 1
        arr[bu, 1] = self.A2[bu]
        arr[bu, 2] = self.c2[bu]
        arr[bu, 3] = self.l2[bu]
        arr[bu, 4] = self.o2[bu]
        arr[bu, 5] = self.b2[bu]
        arr[bu, 6] = self.n2[bu]
        arr[bu, 7] = (self.t_bu[bu] // 60).astype(np.int32)
        return arr.tobytes(), int(bu.sum())


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out-dir", default=str(ROOT / "site" / "data" / "live-fake"))
    ap.add_argument("--scenario", default="virada", choices=list(SCEN))
    ap.add_argument("--seed", type=int, default=25)
    ap.add_argument("--start", default="16:57", help="horário simulado inicial (BRT)")
    ap.add_argument("--end", default="23:59")
    ap.add_argument("--speed", type=float, default=20.0, help="segundos simulados por segundo real")
    ap.add_argument("--interval", type=float, default=15.0, help="heartbeat do manifest (s reais)")
    ap.add_argument("--sections-every", type=int, default=2, help="publica sections.bin a cada N heartbeats")
    ap.add_argument("--at", default=None, help="um retrato no horário HH:MM e sai")
    ap.add_argument("--with-analytics", action="store_true", help="sobe analytics.run apontado para --out-dir")
    ap.add_argument("--stall-at", default=None)
    ap.add_argument("--stall-secs", type=float, default=150)
    ap.add_argument("--tse-freeze-at", default=None)
    ap.add_argument("--tse-freeze-secs", type=float, default=400, help="duração em segundos SIMULADOS")
    ap.add_argument("--br-frozen-at", default=None)
    ap.add_argument("--drop-files", action="store_true")
    a = ap.parse_args(argv)

    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    idx_csv = BASE / "secoes_index.csv"
    sha = hashlib.sha256(idx_csv.read_bytes()).hexdigest()
    W = World(a.scenario, a.seed)
    # recomeça do zero: apaga versões antigas e o analytics (o histórico não deve misturar execuções)
    for p in out.iterdir():
        if re.match(r"^(agg\.\d+\.json|sections\.\d+\.bin|manifest\.json|analytics\.json)$", p.name):
            p.unlink(missing_ok=True)

    child = None
    if a.with_analytics:
        logp = out / "analytics-run.log"
        child = subprocess.Popen([sys.executable, "-m", "analytics.run", "--live-dir", str(out), "--interval", "4",
                                  "--log", str(logp)], cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
        print(f"analytics.run pid {child.pid} (log {logp})", flush=True)

    stop = {"f": False}

    def _stop(*_):
        stop["f"] = True
    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    sim0 = hhmm_to_s(a.at or a.start)
    end = hhmm_to_s(a.end)
    wall0 = time.time()
    version = dv_agg = dv_sec = 0
    last_hash = None
    last_change_wall = None
    beat = 0
    stall_at = hhmm_to_s(a.stall_at) if a.stall_at else None
    stalled_until = None
    tfz = hhmm_to_s(a.tse_freeze_at) if a.tse_freeze_at else None
    brf = hhmm_to_s(a.br_frozen_at) if a.br_frozen_at else None
    frozen_agg = None
    try:
        while not stop["f"]:
            now = time.time()
            sim = int(min(end, sim0 + (0 if a.at else (now - wall0) * a.speed)))
            if stall_at is not None and sim >= stall_at and stalled_until is None:
                stalled_until = now + a.stall_secs
                print(f"[{iso(now)}] SIMULANDO coletor parado por {a.stall_secs:.0f}s", flush=True)
            if stalled_until and now < stalled_until:
                time.sleep(1)
                continue
            freeze = tfz is not None and tfz <= sim < tfz + a.tse_freeze_secs
            if freeze and frozen_agg is not None:
                agg, st = frozen_agg
            else:
                agg, st = W.agg(sim)
                frozen_agg = (agg, st) if freeze else None
            agg_bytes = orjson.dumps(agg)
            h = hashlib.sha1(agg_bytes).hexdigest()
            if h != last_hash:
                dv_agg += 1
                atomic_write(out / f"agg.{dv_agg}.json", agg_bytes)
                prune(out, "agg")
                last_hash = h
                last_change_wall = now
                if a.drop_files and dv_agg % 5 == 0:
                    (out / f"agg.{dv_agg - 1}.json").unlink(missing_ok=True)
            nbu = None
            if beat % max(1, a.sections_every) == 0 or dv_sec == 0:
                sec_bytes, nbu = W.sections(sim)
                dv_sec += 1
                atomic_write(out / f"sections.{dv_sec}.bin", sec_bytes)
                prune(out, "sections")
                W._nbu = nbu
            beat += 1
            version += 1
            br = agg["br"]
            br_frozen = brf is not None and brf <= sim < brf + 300
            manifest = {
                "version": version, "generated_at": iso(now), "source": "replay",
                "tse": {"br_dg": br["dt"], "br_hg": br["ht"], "br_idg": str(version)},
                "coverage": {"secoes_total": W.n, "secoes_totalizadas": br["st"], "secoes_com_bu": getattr(W, "_nbu", 0),
                             "aptos_total": br["te"], "aptos_totalizados": br["est"]},
                "health": {"last_ok": iso(now), "errors_5min": 0, "req_rate": 3.2, "bu_queue": 120,
                           "strategy": "zonas", "stale_seconds": 4.0,
                           "tse_last_change": iso(last_change_wall) if last_change_wall else None,
                           "tse_br_dg_hg": f"{br['dt']} {br['ht']}", "br_frozen": br_frozen,
                           "br_source": "uf_sum" if br_frozen else "br",
                           "lanes": {"agg": {"last_ok": iso(now), "req_rate": 2.0, "errors_5min": 0},
                                     "bu": {"last_ok": iso(now), "req_rate": 1.2, "errors_5min": 0, "paused_until": None}},
                           "agg_lag_s": 8.0},
                "data_versions": {"agg": dv_agg, "sections": dv_sec},
                "files": {"agg": f"agg.{dv_agg}.json", "sections": f"sections.{dv_sec}.bin", "analytics": "analytics.json"},
                "index": {"n_rows": W.n, "n_cols": 8, "sha256": sha},
                "scope": {"fake": "site/tools/fake_live.py", "scenario": a.scenario, "seed": a.seed},
            }
            atomic_write(out / "manifest.json", orjson.dumps(manifest, option=orjson.OPT_INDENT_2))
            hh = f"{sim // 3600:02d}:{sim % 3600 // 60:02d}"
            print(f"[{iso(now)[11:19]}] sim {hh} v{version} agg{dv_agg} sec{dv_sec} "
                  f"st {br['st']:,}/{W.n:,} ({100 * br['st'] / W.n:.1f}%) bu {getattr(W, '_nbu', 0):,}"
                  f"{' FREEZE' if freeze else ''}{' BRFROZEN' if br_frozen else ''}", flush=True)
            if a.at:
                if child:
                    # espera o analytics escrever uma vez
                    t = time.time()
                    while time.time() - t < 60 and not (out / "analytics.json").exists():
                        time.sleep(0.5)
                    time.sleep(1.5)
                break
            for _ in range(int(a.interval * 10)):
                if stop["f"]:
                    break
                time.sleep(0.1)
    finally:
        if child:
            child.terminate()
            try:
                child.wait(10)
            except subprocess.TimeoutExpired:
                child.kill()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

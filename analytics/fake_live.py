"""Fabrica entradas ao vivo (manifest + agg.<dv>.json + sections.<dv>.bin) a partir da base do 1T 2026,
para testar o analytics de ponta a ponta antes do mock/coletor. NÃO é o mock do TSE.

O "2T sintético" parte do 1T de cada seção:
- votos dos demais (X1) repartidos entre L, O, BN e abstenção;
- pequeno ganho de comparecimento;
- efeito regional e ruído por zona.

A chegada segue a ordem do 1T (`t_tot1`), comprimida. Os BUs chegam com atraso (SP pesado).

Uso:
  uv run python -m analytics.fake_live --out-dir analytics/cache/fake_live --at 18:00       # um retrato
  uv run python -m analytics.fake_live --out-dir analytics/cache/fake_live --play --speed 60 # replay acelerado
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import time
from pathlib import Path

import numpy as np
import orjson

from .base import REGIOES, UFS, load_base
from .model import AGG

ROOT = Path(__file__).resolve().parent.parent
BRT = dt.timezone(dt.timedelta(hours=-3))
DAY = dt.date(2026, 10, 25)


def synth_truth(base, seed=11, l_share_x=0.42, reg_shift=None):
    rng = np.random.default_rng(seed)
    s1 = base.s1
    A1, C1, L1, O1, X1, BN1, AB1 = (s1[:, j] for j in range(7))
    A2 = base.A2
    zr = base.uf_reg[base.mun_uf[base.zone_mun[base.sec_zone]]]
    reg_shift = reg_shift or {"N": 0.01, "NE": 0.03, "CO": -0.03, "SE": 0.0, "S": -0.02, "ZZ": 0.0}
    rs = np.array([reg_shift[r] for r in REGIOES])[zr]
    zn = rng.normal(0, 0.03, base.Z)[base.sec_zone]
    sl = np.clip(l_share_x + rs + zn, 0.05, 0.95)
    # X1: fração sl vai para L, (1−sl)·0.85 para O, resto para BN/abstenção
    to_l = X1 * sl
    to_o = X1 * (1 - sl) * 0.85
    to_bn = X1 * (1 - sl) * 0.05
    to_ab = X1 * (1 - sl) * 0.10
    # comparecimento +0,6 p.p. (abstenção cai) indo 55% L / 45% O; BN cai 10%
    dturn = 0.006 * A1
    bn_drop = 0.10 * BN1
    L2 = L1 + to_l + 0.55 * (dturn + bn_drop)
    O2 = O1 + to_o + 0.45 * (dturn + bn_drop)
    BN2 = BN1 - bn_drop + to_bn
    AB2 = np.maximum(AB1 + to_ab - dturn, 0)
    tot = L2 + O2 + BN2 + AB2
    p = np.stack([L2, O2, BN2, AB2], axis=1) / np.maximum(tot, 1)[:, None]
    # arredonda para inteiros somando A2 (multinomial pela média: arredondamento estocástico)
    cnt = np.floor(p * A2[:, None] + rng.random(p.shape))
    cnt[:, 3] = np.maximum(A2 - cnt[:, :3].sum(axis=1), 0)
    l2, o2, bn2, ab2 = (cnt[:, j] for j in range(4))
    b2 = np.floor(bn2 * 0.4)
    n2 = bn2 - b2
    comp2 = A2 - ab2
    return {"aptos2": A2, "comp2": comp2, "l2": l2, "o2": o2, "b2": b2, "n2": n2}


def arrival_times(base, seed=5):
    """Segundos desde 00:00 BRT do dia do 2T: ordem do 1T comprimida ×0,8 a partir das 17:00."""
    rng = np.random.default_rng(seed)
    t1 = base.t1
    t0 = np.min(t1)
    tt = 17 * 3600 + (t1 - t0) * 0.8 + rng.normal(0, 60, base.n)
    tot = np.maximum(tt, 17 * 3600 + 30)
    sp = base.sec_uf == UFS.index("sp")
    lag = np.where(sp, np.exp(rng.uniform(np.log(35 * 60), np.log(4 * 3600), base.n)),
                   np.exp(rng.uniform(np.log(120), np.log(1800), base.n)))
    return tot, tot + lag


def agg_dict(st, ts, te, est, c, a, vb, tvn, l, o, t_s):
    hh = time.strftime("%H:%M:%S", time.gmtime(t_s))
    return {"ts": int(ts), "st": int(st), "te": int(te), "est": int(est), "c": int(c), "a": int(a),
            "vb": int(vb), "tvn": int(tvn), "l": int(l), "o": int(o), "dt": DAY.strftime("%d/%m/%Y"), "ht": hh,
            "and": "f" if st >= ts else ("n" if st == 0 else "p")}


def snapshot(base, tr, t_tot, t_bu, t_s):
    m = t_tot <= t_s
    hb = (t_bu <= t_s) & m
    w = {k: tr[k] * m for k in tr}
    cols = {
        "st": m.astype(float), "est": w["aptos2"], "c": w["comp2"], "a": w["aptos2"] - w["comp2"],
        "vb": w["b2"], "tvn": w["n2"], "l": w["l2"], "o": w["o2"],
    }
    Z, M = base.Z, base.M
    zs = {k: np.bincount(base.sec_zone, weights=v, minlength=Z) for k, v in cols.items()}
    zs["ts"] = base.zone_n
    zs["te"] = np.bincount(base.sec_zone, weights=tr["aptos2"], minlength=Z)
    ms = {k: np.bincount(base.zone_mun, weights=v, minlength=M) for k, v in zs.items()}
    us = {k: np.bincount(base.mun_uf, weights=v, minlength=len(UFS)) for k, v in ms.items()}
    mk = lambda d, i: agg_dict(*(d[k][i] for k in ("st", "ts", "te", "est", "c", "a", "vb", "tvn", "l", "o")), t_s)  # noqa: E731
    nz_mun = np.bincount(base.zone_mun, minlength=M)
    zona = {}
    for i, key in enumerate(base.zone_keys):
        mi = base.zone_mun[i]
        if nz_mun[mi] == 1 or ms["st"][mi] > 0:   # como o coletor: zona de município multizona só depois que ele anda
            zona[key] = mk(zs, i)
    mun = {str(int(c)): mk(ms, i) for i, c in enumerate(base.mun_codes)}
    uf = {u: mk(us, i) for i, u in enumerate(UFS)}
    brd = {k: v.sum() for k, v in us.items()}
    br = agg_dict(*(brd[k] for k in ("st", "ts", "te", "est", "c", "a", "vb", "tvn", "l", "o")), t_s)
    sec = np.zeros((base.n, 8), dtype="<i4")
    sec[:, 7] = -1
    sec[hb, 0] = 1
    for j, k in enumerate(("aptos2", "comp2", "l2", "o2", "b2", "n2"), start=1):
        sec[hb, j] = tr[k][hb].astype(np.int32)
    sec[hb, 7] = (t_bu[hb] // 60).astype(np.int32)
    return {"br": br, "uf": uf, "mun": mun, "zona": zona}, sec, int(hb.sum())


def atomic(path: Path, data: bytes):
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def write(out: Path, agg, sec, nbu, version, t_s):
    out.mkdir(parents=True, exist_ok=True)
    atomic(out / f"agg.{version}.json", orjson.dumps(agg))
    atomic(out / f"sections.{version}.bin", sec.tobytes())
    for p in out.glob("agg.*.json"):
        if p.name != f"agg.{version}.json" and int(p.name.split(".")[1]) < version - 2:
            p.unlink()
    for p in out.glob("sections.*.bin"):
        if int(p.name.split(".")[1]) < version - 2:
            p.unlink()
    now = dt.datetime.combine(DAY, dt.time(), tzinfo=BRT) + dt.timedelta(seconds=t_s)
    man = {"version": version, "generated_at": now.isoformat(timespec="seconds"), "source": "replay",
           "coverage": {"secoes_total": agg["br"]["ts"], "secoes_totalizadas": agg["br"]["st"],
                        "secoes_com_bu": nbu, "aptos_total": agg["br"]["te"], "aptos_totalizados": agg["br"]["est"]},
           "health": {"stale_seconds": 0}, "data_versions": {"agg": version, "sections": version},
           "files": {"agg": f"agg.{version}.json", "sections": f"sections.{version}.bin",
                     "analytics": "analytics.json"},
           "index": {"n_rows": int(sec.shape[0]), "n_cols": 8}, "scope": {"fake": True}}
    atomic(out / "manifest.json", orjson.dumps(man, option=orjson.OPT_INDENT_2))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=str(ROOT / "data" / "base" / "secoes.parquet"))
    ap.add_argument("--out-dir", default=str(ROOT / "analytics" / "cache" / "fake_live"))
    ap.add_argument("--at", default=None, help="HH:MM: um retrato só")
    ap.add_argument("--play", action="store_true")
    ap.add_argument("--start", default="17:00")
    ap.add_argument("--step-min", type=float, default=2.0, help="minutos simulados por publicação")
    ap.add_argument("--speed", type=float, default=60.0, help="minutos simulados por minuto real")
    ap.add_argument("--l-share-x", type=float, default=0.42)
    a = ap.parse_args(argv)
    base, _ = load_base(a.base)
    tr = synth_truth(base, l_share_x=a.l_share_x)
    L, O = tr["l2"].sum(), tr["o2"].sum()
    print(f"2T sintético: L {L:,.0f} O {O:,.0f} margem {L - O:+,.0f} ({100 * (L - O) / (L + O):+.2f} p.p.)")
    t_tot, t_bu = arrival_times(base)
    out = Path(a.out_dir)
    hm = lambda s: int(s.split(":")[0]) * 3600 + int(s.split(":")[1]) * 60  # noqa: E731
    if a.at:
        t_s = hm(a.at)
        agg, sec, nbu = snapshot(base, tr, t_tot, t_bu, t_s)
        write(out, agg, sec, nbu, 1, t_s)
        print(f"{a.at}: {agg['br']['st']} seções ({100 * agg['br']['st'] / agg['br']['ts']:.1f}%), {nbu} BUs → {out}")
        return
    t_s = hm(a.start)
    v = 0
    while t_s <= 26 * 3600:
        v += 1
        agg, sec, nbu = snapshot(base, tr, t_tot, t_bu, t_s)
        write(out, agg, sec, nbu, v, t_s)
        print(f"v{v} {time.strftime('%H:%M', time.gmtime(t_s))}: {100 * agg['br']['st'] / agg['br']['ts']:.1f}% seções, "
              f"{nbu} BUs, bruto {agg['br']['l'] - agg['br']['o']:+,}", flush=True)
        if not a.play or agg["br"]["st"] >= agg["br"]["ts"]:
            if not a.play:
                break
            break
        time.sleep(60 * a.step_min / a.speed)
        t_s += 60 * a.step_min


if __name__ == "__main__":
    main()

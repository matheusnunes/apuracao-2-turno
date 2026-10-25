"""Testes rápidos do analytics sobre a base 2026 real com um 2T sintético (analytics.fake_live).

uv run pytest -q analytics/tests
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from analytics.base import load_base
from analytics.fake_live import arrival_times, snapshot, synth_truth
from analytics.live_io import build_live
from analytics.model import Params, compute

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "data" / "base" / "secoes.parquet"
pytestmark = pytest.mark.skipif(not BASE.exists(), reason="base do ETL ausente")

MIN_SCHEMA = {
    "status": ["phase", "pct_secoes", "pct_aptos", "can_call", "call", "mix_regiao", "mix_uf", "mix_caption"],
    "baseline_1t": ["l", "o", "margin_votes", "aptos"],
    "apurado": ["l", "o", "vb", "tvn", "c", "a", "te", "est", "margin_votes", "l_pct_valid", "o_pct_valid"],
    "pace": ["delta_req", "delta_rem", "delta_obs", "delta_obs_band"],
    "projection": ["l", "o", "margin_votes", "margin_pp_valid", "band_votes", "band_pp_valid", "p_lula",
                   "virada_pct_secoes"],
}
M_KEYS = ["d_abst_pp", "d_abst_n", "d_bn_pp", "d_bn_n", "d_l", "d_o", "d_margin_votes", "delta", "capture_l",
          "capture", "decomp"]


@pytest.fixture(scope="module")
def world():
    base, _ = load_base(BASE)
    tr = synth_truth(base)
    t_tot, t_bu = arrival_times(base)
    return base, tr, t_tot, t_bu


def run_at(world, hhmm: str):
    base, tr, t_tot, t_bu = world
    h, m = hhmm.split(":")
    t = int(h) * 3600 + int(m) * 60
    agg, sec, _ = snapshot(base, tr, t_tot, t_bu, t)
    live = build_live(base, agg, sec[:, :7].astype(np.float64), {"generated_at": None})
    live.now_s = t
    return compute(base, live, Params())


def test_pre_phase_no_data(world):
    base = world[0]
    live = build_live(base, None, None, None)
    out = compute(base, live, Params())
    assert out["status"]["phase"] == "pre"
    assert out["projection"]["p_lula"] is None
    assert abs(out["pace"]["delta_req"] - 1.40) < 0.01
    assert out["thermometer"]["br"]["best"] is None


@pytest.mark.parametrize("hhmm", ["17:30", "18:15", "19:30", "20:30"])
def test_schema_identities_and_band(world, hhmm):
    base, tr = world[0], world[1]
    out = run_at(world, hhmm)
    for sec, keys in MIN_SCHEMA.items():
        for k in keys:
            assert k in out[sec], (sec, k)
    best = out["thermometer"]["br"]["best"]
    for k in M_KEYS:
        assert k in best
    cap = best["capture"]
    assert abs(sum(cap.values()) - 1.0) < 1e-6
    assert abs(best["decomp"]["turnout"] + best["decomp"]["preference"] - best["d_margin_votes"]) <= 2
    # a faixa cobre a verdade sintética
    M_true = tr["l2"].sum() - tr["o2"].sum()
    lo, hi = out["projection"]["band_votes"]
    assert lo <= M_true <= hi
    # zonas: chaves do contrato e campos de Z
    z = out["zones"][base.zone_keys[0]]
    assert set(z) >= {"dl", "do", "dbn", "dabst", "pct", "src", "pct_bu"}
    assert z["src"] in ("obs", "est", "shrunk")
    # raw: zona completa → swing exato (não encolhido); sem dado → None
    for k, zz in out["zones"].items():
        if zz["src"] == "shrunk":
            assert zz["raw"] is None
        else:
            assert set(zz["raw"]) == {"dl", "do", "dbn", "dabst"}
    assert set(out["projection"]["mun"]) == set(out["mun"])
    pr = out["projection"]
    assert pr["virada_pct_secoes"] is None or 0 <= pr["virada_pct_secoes"] < 99.0
    assert pr["p_lula"] is None or 0.001 <= pr["p_lula"] <= 0.999
    # mix: as fatias somam 100
    assert abs(sum(m["share_eleitorado"] for m in out["status"]["mix_regiao"].values()) - 100) < 1e-6
    assert abs(sum(m["share_apurado"] for m in out["status"]["mix_regiao"].values()) - 100) < 1e-6


def test_complete_count_is_exact(world):
    base, tr = world[0], world[1]
    out = run_at(world, "30:00")
    if out["apurado"]["est"] < out["apurado"]["te"]:
        pytest.skip("replay sintético não termina até 06:00")
    assert out["projection"]["margin_votes"] == pytest.approx(tr["l2"].sum() - tr["o2"].sum(), abs=2)
    assert out["status"]["phase"] == "final"


def test_stale_zones_use_newer_municipal_aggregate(world):
    """Zonas defasadas (coletor atrasou) com o município à frente: vale o município (revisão, item 1)."""
    from analytics.model import I2, prepare

    base, tr, t_tot, t_bu = world
    cur, _, _ = snapshot(base, tr, t_tot, t_bu, 18 * 3600 + 30 * 60)
    old, _, _ = snapshot(base, tr, t_tot, t_bu, 17 * 3600 + 20 * 60)
    zm = base.zone_mun
    nz = np.bincount(zm, minlength=base.M)
    # o município multizona com mais avanço entre 17:20 e 18:30 (Brasília no caso da revisão)
    gain = {c: cur["mun"][str(int(c))]["st"] - old["mun"][str(int(c))]["st"] for i, c in enumerate(base.mun_codes) if nz[i] > 1}
    cm = max(gain, key=gain.get)
    mi = base.mun_index[int(cm)]
    stale = dict(cur)
    stale["zona"] = dict(cur["zona"])
    for i in np.flatnonzero(zm == mi):
        k = base.zone_keys[i]
        if k in old["zona"]:
            stale["zona"][k] = old["zona"][k]
        else:
            stale["zona"].pop(k, None)
    p = Params()
    live_s = build_live(base, stale, None, None)
    live_c = build_live(base, cur, None, None)
    prep_s = prepare(base, live_s, p)
    est_mun = prep_s.K2[zm == mi, I2["A"]].sum()
    assert est_mun == pytest.approx(cur["mun"][str(int(cm))]["est"], rel=1e-6)
    l_mun = prep_s.K2[zm == mi, I2["L"]].sum()
    assert l_mun == pytest.approx(cur["mun"][str(int(cm))]["l"], rel=1e-6)
    out_s, out_c = compute(base, live_s, p), compute(base, live_c, p)
    assert abs(out_s["projection"]["margin_votes"] - out_c["projection"]["margin_votes"]) < 0.25 * (
        out_c["projection"]["band_votes"][1] - out_c["projection"]["band_votes"][0])


def test_history_drop_keeps_points():
    """Coletor reiniciado vazio (contagem cai sem troca de fonte): mantém o histórico e pula o ponto (item 2)."""
    from analytics.run import History

    h = History([{"t": "2026-10-25T19:00:00-03:00", "pct_secoes": 60.0, "margin_apurado": 1}],
                {"source": "live", "scope": None})
    h.reset_if_needed({"source": "live", "scope": None})
    assert len(h.points) == 1 and h.is_drop(0.0) and not h.is_drop(60.5)
    h.reset_if_needed({"source": "mock", "scope": None})
    assert h.points == []


def _mixed(base, fresh, stale, sec_stale):
    agg = {"br": fresh["br"], "uf": fresh["uf"], "mun": stale["mun"], "zona": stale["zona"]}
    return build_live(base, agg, sec_stale[:, :7].astype(np.float64), None)


def test_stale_state_early_no_confident_call(world):
    """Rehearsal bug: fresh BR/UF at ~3% with stale zones/muns/BUs at 100% → early, no call, wide band."""
    base, tr, t_tot, t_bu = world
    fresh, _, _ = snapshot(base, tr, t_tot, t_bu, 17 * 3600 + 22 * 60)
    stale, sec_stale, _ = snapshot(base, tr, t_tot, t_bu, 30 * 3600)
    pct = 100 * fresh["br"]["st"] / fresh["br"]["ts"]
    assert 2 <= pct <= 6
    out = compute(base, _mixed(base, fresh, stale, sec_stale), Params())
    st, pr = out["status"], out["projection"]
    assert st["inconsistent_inputs"], "deveria marcar UFs inconsistentes"
    assert st["phase"] == "early" and st["call"] is None and not st["can_call"]
    assert pr["band_pp_valid"][1] - pr["band_pp_valid"][0] > 3.0
    assert out["apurado"]["pct_aptos"] == pytest.approx(st["pct_aptos"])
    assert out["projection"]["remaining_pct"] > 90


def test_stale_votes_at_full_coverage(world):
    """Stale zones from another scenario at ~100% vs official at ~100%: the call follows the official count."""
    base, tr, t_tot, t_bu = world
    tr_old = synth_truth(base, l_share_x=0.78)          # cenário antigo: Lula vence
    fresh, _, _ = snapshot(base, tr, t_tot, t_bu, 30 * 3600)   # cenário atual: Flávio vence
    stale, sec_stale, _ = snapshot(base, tr_old, t_tot, t_bu, 30 * 3600)
    out = compute(base, _mixed(base, fresh, stale, sec_stale), Params())
    M_true = tr["l2"].sum() - tr["o2"].sum()
    assert M_true < 0
    assert out["status"]["inconsistent_inputs"]
    assert out["projection"]["margin_votes"] < 0
    assert out["status"]["call"] in (None, "O")

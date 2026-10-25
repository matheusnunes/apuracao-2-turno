"""Backtest 2022: replay da noite do 2T 2022 contra o 1T 2022, com o mesmo código do modo ao vivo.

Uso:
  uv run python -m analytics.backtest            # roda tudo e escreve analytics/BACKTEST.md
  uv run python -m analytics.backtest --quick    # só checkpoints do cenário de produção

Cenários (sim2022.SCENARIOS): o principal é `producao` (agregados de zona + BUs esparsos e atrasados).
`ordem_1t` é um teste de robustez: os mesmos votos do 2T, mas chegando na ordem/horário do 1T 2022.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import math
import time
from pathlib import Path

import numpy as np

from .base import REGIOES, UFS
from .model import I1, I2, Params, _bc, compute, prepare, project, run_model
from .sim2022 import SCENARIOS, Sim

HERE = Path(__file__).resolve().parent
CHECK_PCTS = [2, 5, 10, 20, 30, 50, 70, 90, 95]
# unidade da grade de k (eleitores aptos); os defaults de Params são 4× isto
K_UNIT = {"zone": 20_000, "mun": 50_000, "grp": 100_000, "uf": 200_000, "reg": 500_000, "br": 200_000}
S17 = 17 * 3600


# ----------------------------------------------------------------------------------------------------------
def with_1t_order(sim: Sim) -> Sim:
    """Mesmos votos do 2T, chegando na ordem do 1T 2022 (horário do 1T reescalado para a duração do 2T)."""
    tr = dict(sim.tr)
    t1 = sim.base.t1
    t2 = sim.tr["t_tot2"]
    r = (np.median(t2) - S17) / (np.median(t1) - S17)
    tt = S17 + (t1 - S17) * r
    tr["t_tot2"] = tt
    tr["t_bu2"] = tt
    return Sim(base=sim.base, tr=tr)


def estimate_taus(sim: Sim) -> dict:
    """τ por nível: DP (ponderado por aptos) do efeito da área no swing final de margem por apto, (ΔL−ΔO)/A."""
    b, tr = sim.base, sim.tr
    Z, M, G = b.Z, b.M, b.G
    z = b.sec_zone
    A1 = _bc(z, b.s1[:, I1["A"]], Z)
    M1 = _bc(z, b.s1[:, I1["L"]] - b.s1[:, I1["O"]], Z)
    A2 = _bc(z, tr["aptos2"], Z)
    M2 = _bc(z, tr["l2"] - tr["o2"], Z)

    def swing(a1, m1, a2, m2):
        return np.where((a1 > 0) & (a2 > 0), m2 / np.maximum(a2, 1) - m1 / np.maximum(a1, 1), 0.0)

    levels = {}
    zm, mg, gu, ur = b.zone_mun, b.mun_grp, b.grp_uf, b.uf_reg
    agg = lambda idx, x, n: np.bincount(idx, weights=x, minlength=n)  # noqa: E731
    sz = swing(A1, M1, A2, M2)
    mA1, mM1, mA2, mM2 = (agg(zm, x, M) for x in (A1, M1, A2, M2))
    sm = swing(mA1, mM1, mA2, mM2)
    gA1, gM1, gA2, gM2 = (agg(mg, x, G) for x in (mA1, mM1, mA2, mM2))
    sg = swing(gA1, gM1, gA2, gM2)
    U_, R_ = len(UFS), len(REGIOES)
    uA1, uM1, uA2, uM2 = (agg(gu, x, U_) for x in (gA1, gM1, gA2, gM2))
    su = swing(uA1, uM1, uA2, uM2)
    rA1, rM1, rA2, rM2 = (agg(ur, x, R_) for x in (uA1, uM1, uA2, uM2))
    sr = swing(rA1, rM1, rA2, rM2)
    sb = (rM2.sum() / rA2.sum()) - (rM1.sum() / rA1.sum())

    def wsd(eff, w):
        return float(np.sqrt(np.sum(w * eff**2) / np.sum(w)))

    levels["zone"] = wsd(sz - sm[zm], A2)
    levels["mun"] = wsd(sm - sg[mg], mA2)
    levels["grp"] = wsd(sg - su[gu], gA2)
    levels["uf"] = wsd(su - sr[ur], uA2)
    levels["reg"] = wsd(sr - sb, rA2)
    levels["br"] = float(abs(sb))
    # ruído por seção (variância do swing por seção × aptos médio) → k "amostral" de cada nível
    s1 = b.s1
    ss = (tr["l2"] - tr["o2"]) / np.maximum(tr["aptos2"], 1) - (s1[:, I1["L"]] - s1[:, I1["O"]]) / np.maximum(
        s1[:, I1["A"]], 1)
    resid = ss - sz[z]
    var_s = float(np.sum(tr["aptos2"] * resid**2) / tr["aptos2"].sum())
    abar = float(tr["aptos2"].mean())
    levels["sec_var_x_aptos"] = var_s * abar
    return levels


# ----------------------------------------------------------------------------------------------------------
def _parts_delta(p1: np.ndarray, p2: np.ndarray):
    s1, s2 = p1.sum(axis=0), p2.sum(axis=0)
    if s1[I1["A"]] <= 0 or s2[I2["A"]] <= 0:
        return None, None, None
    d = 100 * ((s2[I2["L"]] - s2[I2["O"]]) / s2[I2["A"]] - (s1[I1["L"]] - s1[I1["O"]]) / s1[I1["A"]])
    dab = 100 * (s2[I2["AB"]] / s2[I2["A"]] - s1[I1["AB"]] / s1[I1["A"]])
    dbn = 100 * (s2[I2["BN"]] / s2[I2["A"]] - s1[I1["BN"]] / s1[I1["A"]])
    return d, dab, dbn


def step_metrics(sim: Sim, live, params: Params, truth: dict, full: bool = False) -> dict:
    b = sim.base
    if full:
        out = compute(b, live, params, include_zones=False)
        prep, proj = out["_internal"]["prep"], out["_internal"]["proj"]
    else:
        prep, proj = run_model(b, live, params)
        out = None
    cnt = live.meta["counted_mask"]
    st = float(cnt.sum())
    Mraw = float(live.br["l"] - live.br["o"])
    M, half, sig = proj["M"], proj["half"], proj["sigma"]
    # meia-faixa "calibrada" (antes do alargamento ×1.5 e do piso)
    half_cal = proj["half_cal"]
    err = M - truth["M"]
    r = {
        "t": live.now_s, "pct": 100 * st / b.n, "pct_aptos": 100 * prep.K2[:, I2["A"]].sum() / prep.te.sum(),
        "Mraw": Mraw, "M": M, "M_pp": 100 * M / proj["V"], "err": err, "err_pp": 100 * M / proj["V"] - truth["M_pp"],
        "half": half, "half_cal": half_cal, "cov": abs(err) <= half, "cov_cal": abs(err) <= half_cal,
        "z": err / sig if sig > 0 else 0.0, "z_cal": err / (half_cal / params.z_band) if half_cal > 0 else 0.0,
        "half_raw": params.z_band * proj["sigma_raw"] + proj["imp"] + proj["bias"], "imp": proj["imp"],
        "p_l": 0.5 * (1 + math.erf(M / sig / math.sqrt(2))) if sig > 0 else float(M > 0),
        "nbu": float(prep.hb.sum()), "f_imp": proj["f_imp"],
    }
    # δ observado (UF-ponderado) vs δ final verdadeiro
    teu = _bc(b.zone_uf(), prep.te, len(UFS))
    dmu = proj["d_u"][:, 0] - proj["d_u"][:, 1]
    r["delta_obs"] = 100 * float(np.sum(teu * dmu) / teu.sum())
    r["delta_obs_err"] = r["delta_obs"] - truth["delta"]
    # termômetro: melhor / exato / estimado contra a verdade das MESMAS seções
    d_best, dab_best, dbn_best = _parts_delta(prep.ex1 + prep.es1, prep.ex2 + prep.es2)
    d_est, dab_est, dbn_est = _parts_delta(prep.es1, prep.es2)
    d_ex, _, _ = _parts_delta(prep.ex1, prep.ex2)
    tc = sim.truth_counted(cnt)
    est_mask = cnt & ~prep.hb & ~prep.complete[b.sec_zone]
    te_ = sim.truth_counted(est_mask) if est_mask.any() else {}
    r.update({
        "d_best": d_best, "d_est": d_est, "d_exact": d_ex,
        "d_true_cnt": tc.get("delta"), "d_true_est": te_.get("delta"),
        "dab_best": dab_best, "dab_true": tc.get("d_abst_pp"), "dbn_best": dbn_best, "dbn_true": tc.get("d_bn_pp"),
        "dab_est": dab_est, "dab_true_est": te_.get("d_abst_pp"), "dbn_est": dbn_est, "dbn_true_est": te_.get("d_bn_pp"),
    })
    if out is not None:
        r["phase"] = out["status"]["phase"]
        r["can_call"] = out["status"]["can_call"]
        r["call"] = out["status"]["call"]
        r["virada_pct"] = out["projection"]["virada_pct_secoes"]
        r["virada_eta"] = out["projection"]["virada"]["eta_s"]
        r["delta_rem"] = out["pace"]["delta_rem"]
    return r


def run_scenario(sim: Sim, params: Params, scenario: str, times, full=False, seed=7) -> list[dict]:
    bu_av = sim.bu_avail(scenario, seed=seed) if scenario in SCENARIOS else None
    truth = sim.truth_final()
    rows = []
    for t in times:
        live = sim.live_at(t, bu_av)
        if live.meta["counted_mask"].sum() == 0:
            continue
        rows.append(step_metrics(sim, live, params, truth, full=full))
    return rows


def timeline(sim: Sim) -> list[float]:
    tmax = float(np.max(sim.tr["t_tot2"]))
    t = list(np.arange(S17 + 60, min(tmax, 21 * 3600) + 1, 60.0))
    t += list(np.arange(21 * 3600 + 600, tmax + 600, 600.0))
    return t


def at_pcts(rows, pcts, dedupe=False):
    out = []
    for p in pcts:
        cand = [r for r in rows if r["pct"] >= p]
        if cand and not (dedupe and out and out[-1] is cand[0]):
            out.append(cand[0])
    return out


# ----------------------------------------------------------------------------------------------------------
def tune(sim: Sim, base_params: Params, scenarios=("producao", "zonas")) -> list[dict]:
    """Grade pequena: base de imputação (ρ), peso da parte estimada e escala das pseudo-contagens k.
    `prepare` (imputação) é calculado uma vez por (cenário, ρ, passo); só `project` roda na grade."""
    pcts = [5, 10, 15, 20, 30, 40, 50, 60, 70, 80, 90]
    times = sim.times_for_pcts(pcts)
    truth = sim.truth_final()
    res = []
    imps = [("mean", None), ("order", None), ("soft", 0.6), ("soft", 0.7), ("soft", 0.75), ("soft", 0.8)]
    for baseline, rho in imps:
        pb = dataclasses.replace(base_params, baseline=baseline, rho=rho if rho is not None else base_params.rho)
        preps = []
        for sc in scenarios:
            bu_av = sim.bu_avail(sc)
            for t in times:
                live = sim.live_at(t, bu_av)
                preps.append(prepare(sim.base, live, pb))
        for w_est in (0.5, 1.0):
            for ks in (0.5, 1.0, 2.0, 4.0, 8.0, 16.0):
                p = dataclasses.replace(pb, w_est=w_est, **{f"k_{lv}": kv * ks for lv, kv in K_UNIT.items()})
                e = []
                for prep in preps:
                    pr = project(sim.base, prep, p)
                    e.append(100 * pr["M"] / pr["V"] - truth["M_pp"])
                e = np.array(e)
                res.append({"baseline": baseline, "rho": rho, "w_est": w_est, "k_scale": ks,
                            "rmse_pp": float(np.sqrt(np.mean(e**2))), "mean_pp": float(e.mean()),
                            "max_abs_pp": float(np.max(np.abs(e)))})
    return res


def calibrate_band(rows_all: list[list[dict]], z=1.645, target=0.90) -> float:
    """Multiplicador m tal que |erro| ≤ m·(meia-faixa calibrada) em `target` dos passos (2%–99%)."""
    ratios = []
    for rows in rows_all:
        for r in rows:
            if 2 <= r["pct"] <= 99 and r["half_raw"] > 0:
                ratios.append(abs(r["err"]) / r["half_raw"])
    return float(np.quantile(ratios, target))


def first_stable(rows, cond):
    """Primeiro passo a partir do qual `cond` vale até o fim."""
    idx = None
    for i, r in enumerate(rows):
        if cond(r):
            if idx is None:
                idx = i
        else:
            idx = None
    return rows[idx] if idx is not None else None


def hhmm(t):
    if t is None:
        return "—"
    t = int(round(t))
    return f"{t // 3600:02d}:{(t % 3600) // 60:02d}"


# ----------------------------------------------------------------------------------------------------------
def bias_table(sim: Sim, params: Params, scenario: str, pcts=(5, 10, 20, 30, 50)) -> list[dict]:
    """Viés do termômetro (parte imputada e 'melhor') contra a verdade das mesmas seções, por método de imputação."""
    bu_av = sim.bu_avail(scenario)
    times = sim.times_for_pcts(list(pcts))
    out = []
    for name, kw in [("pró-rata (média da zona)", dict(baseline="mean")),
                     ("posto exato (1T, ρ=1)", dict(baseline="order")),
                     (f"posto suave ρ={params.rho:g} (escolhido)", dict(baseline="soft", rho=params.rho))]:
        p = dataclasses.replace(params, **kw)
        for pct, t in zip(pcts, times):
            live = sim.live_at(t, bu_av)
            prep = prepare(sim.base, live, p)
            b = sim.base
            cnt = live.meta["counted_mask"]
            est_mask = cnt & ~prep.hb & ~prep.complete[b.sec_zone]
            te_, tc = sim.truth_counted(est_mask), sim.truth_counted(cnt)
            d_e, ab_e, bn_e = _parts_delta(prep.es1, prep.es2)
            d_b, ab_b, bn_b = _parts_delta(prep.ex1 + prep.es1, prep.ex2 + prep.es2)
            imp_share = prep.es2[:, I2["A"]].sum() / max((prep.ex2[:, I2["A"]] + prep.es2[:, I2["A"]]).sum(), 1)
            out.append({"method": name, "pct": pct, "imp_share": imp_share,
                        "est_bias": d_e - te_["delta"], "best_bias": d_b - tc["delta"],
                        "best": d_b, "true": tc["delta"],
                        "abst_bias": ab_b - tc["d_abst_pp"], "bn_bias": bn_b - tc["d_bn_pp"]})
    return out


def coverage_stats(rows):
    r = [x for x in rows if 2 <= x["pct"] <= 99]
    return {"n": len(r), "cov_cal": float(np.mean([x["cov_cal"] for x in r])),
            "cov_final": float(np.mean([x["cov"] for x in r])),
            "mean_abs_z": float(np.mean([abs(x["z"]) for x in r])),
            "max_abs_err_pp": float(np.max([abs(x["err_pp"]) for x in r])),
            "cov_cal_ge10": float(np.mean([x["cov_cal"] for x in r if x["pct"] >= 10])),
            "cov_final_ge10": float(np.mean([x["cov"] for x in r if x["pct"] >= 10]))}


def call_times(rows, raw_flip):
    right = lambda r: r["M"] > 0  # noqa: E731  (2022: Lula venceu)
    res = {}
    a = first_stable(rows, lambda r: r["p_l"] > 0.5)
    b = first_stable(rows, lambda r: r["M"] - r["half"] > 0)
    c = first_stable(rows, lambda r: r.get("can_call") and r.get("call") == "L")
    d = first_stable(rows, lambda r: r["p_l"] >= 0.9)
    wrong_call = [r for r in rows if r.get("can_call") and r.get("call") not in (None, "L")]
    for k, v in (("p_gt_50", a), ("p_ge_90", d), ("band_excl_0", b), ("can_call", c)):
        res[k] = (v["t"], v["pct"]) if v else None
    res["ever_wrong_call"] = len(wrong_call)
    res["proj_always_right_after"] = first_stable(rows, right)
    res["raw_flip"] = raw_flip
    return res


def fmt_m(x):
    return f"{x / 1e6:+.2f} mi"


def write_md(path: Path, ctx: dict):
    P = ctx["params"]
    tr = ctx["truth"]
    L = []
    w = L.append
    w("# Backtest 2022: projeção e termômetro na noite do 2º turno de 2022")
    w("")
    w(f"Gerado por `uv run python -m analytics.backtest` em {ctx['generated']} ({ctx['runtime_s']:.0f} s). "
      "Mesmo código do modo ao vivo (`analytics.model.compute`). Os números abaixo saem do script; o texto é meu.")
    w("")
    w("## Montagem")
    w("")
    w("- **Base:** 1T 2022 por seção (472.075 seções principais), no formato de `data/base/secoes.parquet`. "
      "L = 13 Lula, O = 22 Bolsonaro.")
    w(f"- **Verdade:** 2T 2022. Margem final **{tr['M']:+,.0f}** votos (**{tr['M_pp']:+.3f} p.p.** dos válidos). "
      f"δ final = **{tr['delta']:+.2f}** votos de margem por 100 aptos vs 1T (Lula piorou a margem: −2,59).")
    w("- **Noite simulada:** a cada passo (2 min até 21h, depois 10 min), os agregados de **zona** somam as seções "
      "com `DT_PRIM_TOT_PARCIAL_HOR_TSE ≤ t`. É o caminho principal. Os BUs entram como enriquecimento, conforme o cenário:")
    for k, v in ctx["scenarios"].items():
        w(f"  - `{k}`: {v}")
    w("- **Placar bruto:** cruza em **18:44:48 com 68,32% das seções**, igual ao histórico oficial.")
    w("")
    w("## Parâmetros escolhidos")
    w("")
    w(f"- Imputação da parte apurada-sem-BU das zonas parciais: **posto suave, ρ = {P.rho:g}** (ver tabela D).")
    w(f"- Peso da parte imputada no swing: w_est = {P.w_est:g}.")
    w(f"- Encolhimento zona → município → UF×tercil de inclinação 1T → UF → região → BR, com "
      f"k = {P.k_zone:,.0f} / {P.k_mun:,.0f} / {P.k_grp:,.0f} / {P.k_uf:,.0f} / {P.k_reg:,.0f} / {P.k_br:,.0f} aptos. "
      "É 4× a unidade da grade (tabela F).")
    t = ctx["taus"]
    w(f"- τ (DP do efeito de cada nível no swing de margem por apto, estimado no 2T 2022): zona {t['zone']:.4f}, "
      f"mun {t['mun']:.4f}, UF×tercil {t['grp']:.4f}, UF {t['uf']:.4f}, região {t['reg']:.4f}, BR {t['br']:.4f}.")
    w(f"- A faixa é montada em três passos:")
    w(f"  1. **Faixa calibrada** = {P.band_mult:g} × (1,645·σ_modelo + sensibilidade da imputação + viés).")
    w(f"     - A sensibilidade da imputação é |M − M_alt| para ρ = {P.imp_rho_lo:g} e para posto exato.")
    w(f"     - O viés vale {100 * P.bias_imp:.1f}/100 × fração imputada × aptos não apurados.")
    w(f"     - O multiplicador {P.band_mult:g} dá 90% de cobertura na união dos cenários, inclusive o teste de estresse.")
    w(f"  2. **Faixa publicada** = max({P.band_widen:g} × calibrada; {100 * P.band_floor:.1f}/100 × aptos não apurados).")
    w(f"     - O alargamento ×1,5 e o piso foram exigidos pela revisão de arquitetura.")
    w(f"  3. P(Lula) = Φ(M̂/σ), com σ = meia-faixa publicada / 1,645.")
    w(f"- Fases (contrato §6.4):")
    w(f"  - `pre` abaixo de {P.pre_max_pct:g}% das seções.")
    w(f"  - `trend` (`can_call`) quando a faixa inteira está de um lado, há ≥{P.call_min_pct:g}% das seções apuradas e "
      f"toda região (exceto ZZ) tem ≥{P.call_min_reg_pct:g}% dos aptos apurados.")
    w(f"  - `final` quando a apuração está completa ou matematicamente decidida.")
    w("")
    w("## A. Cenário de produção (zonas + BUs esparsos e atrasados): a noite em checkpoints")
    w("")
    w("| % seções | hora | placar bruto | projeção (p.p.) | projeção (votos) | erro (p.p.) | faixa 90% publicada (p.p.) | dentro? | P(Lula) | fase | δ_obs UF-pond. (verdade −2,59) | virada proj. (% seções) |")
    w("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in ctx["A"]:
        lo, hi = (r["M"] - r["half"]) / r["M"] * r["M_pp"], (r["M"] + r["half"]) / r["M"] * r["M_pp"]
        vp = f"{r['virada_pct']:.0f}%" if r.get("virada_pct") else "—"
        w(f"| {r['pct']:.0f}% | {hhmm(r['t'])} | {fmt_m(r['Mraw'])} | {r['M_pp']:+.2f} | {fmt_m(r['M'])} | "
          f"{r['err_pp']:+.2f} | [{lo:+.2f}, {hi:+.2f}] | {'sim' if r['cov'] else '**não**'} | {r['p_l']:.2f} | "
          f"{r['phase']} | {r['delta_obs']:+.2f} | {vp} |")
    w("")
    w("## B. Erro da margem projetada (p.p. dos válidos) por cenário e cobertura da faixa")
    w("")
    w("Os passos usados vão de 2% a 99% das seções. A cobertura é a fração dos passos com a verdade dentro da faixa.")
    w("")
    hdr = "| cenário | " + " | ".join(f"{p}%" for p in ctx["B_pcts"]) + " | máx \\|erro\\| | cobertura calibrada | cobertura publicada | cobertura publicada (≥10%) |"
    w(hdr)
    w("|" + "---|" * (len(ctx["B_pcts"]) + 5))
    for sc, d in ctx["B"].items():
        errs = " | ".join(f"{e:+.2f}" if e is not None else "—" for e in d["errs"])
        c = d["cov"]
        w(f"| `{sc}` | {errs} | {c['max_abs_err_pp']:.2f} | {100 * c['cov_cal']:.0f}% | {100 * c['cov_final']:.0f}% | "
          f"{100 * c['cov_final_ge10']:.0f}% |")
    w("")
    cv = {k: v["cov"] for k, v in ctx["B"].items()}
    real = [k for k in ("producao", "zonas", "bu_pesado", "bu_imediato") if k in cv]
    stress = [k for k in ("ordem_1t", "ordem_1t_zonas") if k in cv]
    pooled = cv.get("(todos, agrupado)", {})
    w("- **Leitura honesta da cobertura.**")
    w(f"  - **Ordem real de 2022** ({', '.join(real)}): o erro pontual ficou bem abaixo da faixa. Cobertura calibrada: "
      + ", ".join(f"{100 * cv[k]['cov_cal']:.0f}%" for k in real) + ". Nessa noite a faixa foi conservadora.")
    w("    - Os parâmetros (ρ, k) foram ajustados nesta mesma noite, então esse erro pontual é in-sample.")
    w(f"  - **Teste de estresse** ({', '.join(stress)}): os mesmos votos do 2T chegando na ordem do 1T. Cobertura calibrada: "
      + ", ".join(f"{100 * cv[k]['cov_cal']:.0f}%" for k in stress) + ".")
    w("    - Ali a ordem dentro da zona se preserva 100%, o pior caso para a imputação suave.")
    w(f"    - O multiplicador {ctx['params'].band_mult:g} foi escolhido como o quantil de 90% de |erro|/meia-faixa na "
      "união dos cenários, numa grade de 3 min. Esse conjunto de calibração inclui o estresse sem BU.")
    if pooled:
        w(f"  - **Agrupando todos os passos de todos os cenários:** cobertura calibrada {100 * pooled['cov_cal']:.0f}% "
          f"e publicada {100 * pooled['cov_final']:.0f}%.")
    w("  - A faixa **publicada** (×1,5 e piso) cobre ~100% em todos os cenários. É conservadora de propósito, e "
      "P(Lula) sai dessa faixa, logo também é conservadora.")
    w("  - Com uma única eleição de referência, \"90%\" é uma meta de projeto, não uma frequência medida.")
    w("")
    w("## C. Quando a projeção acertou o vencedor e não mudou mais")
    w("")
    w("O placar bruto cruzou (virada) às **18:44:48, com 68,32% das seções**.")
    w("")
    w("| cenário (a partir de 2%) | P(Lula) > 50% estável | P(Lula) ≥ 90% estável | faixa exclui empate (estável) | `can_call` = Lula (estável) | chamadas erradas |")
    w("|---|---|---|---|---|---|")
    for sc, c in ctx["C"].items():
        f = lambda v: f"{hhmm(v[0])} ({v[1]:.0f}%)" if v else "—"  # noqa: E731
        w(f"| `{sc}` | {f(c['p_gt_50'])} | {f(c['p_ge_90'])} | {f(c['band_excl_0'])} | {f(c['can_call'])} | {c['ever_wrong_call']} |")
    w("")
    w("## D. Viés da imputação das zonas parciais (termômetro), antes e depois da correção por posto")
    w("")
    w("Cada célula é o viés do δ (votos de margem por 100 aptos, vs 1T) contra a verdade das **mesmas seções**:")
    w("")
    w("- **parte imputada:** a parte apurada sem BU das zonas parciais;")
    w("- **melhor estimativa:** exata + imputada, que é o número que a sala vê.")
    w("")
    w("O cenário é `zonas`, só agregados, que é o pior caso. A revisão de arquitetura mediu −0,8 (pró-rata) e +0,3 (posto).")
    w("")
    w("| método | " + " | ".join(f"{p}%" for p in ctx["D_pcts"]) + " |")
    w("|---|" + "---|" * len(ctx["D_pcts"]))
    by = {}
    for r in ctx["D"]:
        by.setdefault(r["method"], []).append(r)
    for m, rs in by.items():
        w(f"| {m}: parte imputada | " + " | ".join(f"{r['est_bias']:+.2f}" for r in rs) + " |")
        w(f"| {m}: melhor estimativa | " + " | ".join(f"{r['best_bias']:+.2f}" for r in rs) + " |")
    w("")
    rs = by[list(by)[-1]]
    w("Com o método escolhido, os outros componentes da melhor estimativa ficaram assim (viés em p.p. dos aptos):")
    w("")
    w("| | " + " | ".join(f"{p}%" for p in ctx["D_pcts"]) + " |")
    w("|---|" + "---|" * len(ctx["D_pcts"]))
    w("| Δ abstenção | " + " | ".join(f"{r['abst_bias']:+.2f}" for r in rs) + " |")
    w("| Δ brancos+nulos | " + " | ".join(f"{r['bn_bias']:+.2f}" for r in rs) + " |")
    w("| fração imputada da cobertura | " + " | ".join(f"{100 * r['imp_share']:.0f}%" for r in rs) + " |")
    w("")
    w("No cenário `producao`, com os mesmos métodos, a melhor estimativa ficou assim:")
    w("")
    w("| método | " + " | ".join(f"{p}%" for p in ctx["D_pcts"]) + " |")
    w("|---|" + "---|" * len(ctx["D_pcts"]))
    byp = {}
    for r in ctx["Dp"]:
        byp.setdefault(r["method"], []).append(r)
    for m, rs2 in byp.items():
        w(f"| {m} | " + " | ".join(f"{r['best_bias']:+.2f}" for r in rs2) + " |")
    w("")
    w("## E. Virada projetada (onde o placar bruto cruza) vs real: 68,32%, 18:44")
    w("")
    w("A ordem do restante segue o horário do 1T de cada seção, reescalado pelo ritmo observado da UF no 2T.")
    w("")
    w("| agora (% seções) | hora | virada projetada (% seções) | ETA |")
    w("|---|---|---|---|")
    for r in ctx["A"]:
        if r["Mraw"] < 0:
            w(f"| {r['pct']:.0f}% | {hhmm(r['t'])} | {r['virada_pct']:.0f}% | {hhmm(r.get('virada_eta'))} |"
              if r.get("virada_pct") else f"| {r['pct']:.0f}% | {hhmm(r['t'])} | — | — |")
    w("")
    w("Mesmo com margens oráculo, a ordem de chegada erra o ponto de virada em cerca de ±10 p.p. de seções. O ETA sai "
      "adiantado de 10 a 40 min. Na tela, mostrar como \"por volta de\".")
    w("")
    w("## F. Grade de ajuste (erro pontual, sem faixa)")
    w("")
    w("A grade usa os cenários `producao` e `zonas`, com checkpoints de 5% a 90%.")
    w("")
    w("A escala de k multiplica a unidade (zona 20 mil, mun 50 mil, UF×tercil 100 mil, UF 200 mil, região 500 mil, BR 200 mil).")
    w("")
    w("| imputação | ρ | w_est | escala k | RMSE (p.p.) | erro médio | máx \\|erro\\| |")
    w("|---|---|---|---|---|---|---|")
    for r in ctx["F"][:12]:
        w(f"| {r['baseline']} | {r['rho'] if r['rho'] is not None else '—'} | {r['w_est']:g} | {r['k_scale']:g} | "
          f"{r['rmse_pp']:.3f} | {r['mean_pp']:+.3f} | {r['max_abs_pp']:.3f} |")
    best_mean = min((r for r in ctx["F"] if r["baseline"] == "mean"), key=lambda r: r["rmse_pp"], default=None)
    best_ord = min((r for r in ctx["F"] if r["baseline"] == "order"), key=lambda r: r["rmse_pp"], default=None)
    if best_mean and best_ord:
      w(f"| … melhor pró-rata | — | {best_mean['w_est']:g} | {best_mean['k_scale']:g} | {best_mean['rmse_pp']:.3f} | "
      f"{best_mean['mean_pp']:+.3f} | {best_mean['max_abs_pp']:.3f} |")
      w(f"| … melhor posto exato | — | {best_ord['w_est']:g} | {best_ord['k_scale']:g} | {best_ord['rmse_pp']:.3f} | "
      f"{best_ord['mean_pp']:+.3f} | {best_ord['max_abs_pp']:.3f} |")
    w("")
    w("## Ressalvas")
    w("")
    w("1. **Uma única noite de referência.** O ρ e o k foram escolhidos nela.")
    w("   - A faixa foi alargada de propósito (×1,5 e piso) para absorver isso.")
    mx = {k: v["cov"]["max_abs_err_pp"] for k, v in ctx["B"].items()}
    w(f"   - Erro pontual máximo: {mx.get('producao', 0):.2f} p.p. na ordem real (`producao`) e "
      f"{mx.get('ordem_1t', 0):.2f} p.p. no teste com outra ordem de chegada (`ordem_1t`).")
    w("2. **2026 ≠ 2022.** Em 2026 o adversário recebe mais do pool, Lula começa atrás e os \"outros\" do 1T são "
      "outros (Cury, Renan, Caiado).")
    w("   - O τ veio de 2022. A heterogeneidade do swing em 2026 pode ser maior, por exemplo em GO com Caiado.")
    w("3. **Correlação de ordem dentro da zona.** A imputação suave supõe que ela se repete com ρ ≈ 0,75 (cópula "
      "gaussiana, ajustada em 2022).")
    w("   - A sensibilidade (ρ = 0,5 e ρ = 1) entra na faixa.")
    w("4. **BUs.** Com BUs imediatos (`bu_imediato`), o erro cai bastante. Os BUs são enriquecimento e não são "
      "necessários para a projeção.")
    w("5. **Virada projetada:** é indicativa (±10 p.p. de seções).")
    w("")
    path.write_text("\n".join(L) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--no-tune", action="store_true")
    ap.add_argument("--step", type=float, default=120.0, help="passo da simulação até 21h (s)")
    args = ap.parse_args()
    t0 = time.time()
    sim = Sim.load()
    truth = sim.truth_final()
    print(f"verdade 2T 2022: margem {truth['M']:+,.0f} ({truth['M_pp']:+.3f} p.p.), δ final {truth['delta']:+.3f}")
    taus = estimate_taus(sim)
    print("τ estimados:", {k: round(v, 5) for k, v in taus.items()})
    P = Params()
    if args.quick:
        rows = run_scenario(sim, P, "producao", sim.times_for_pcts(CHECK_PCTS), full=True)
        for r in rows:
            print(f"{r['pct']:5.1f}% {hhmm(r['t'])} M {r['M']/1e6:+.3f}M err {r['err']/1e6:+.3f}M ±{r['half']/1e6:.2f} "
                  f"(cal ±{r['half_cal']/1e6:.2f}) P {r['p_l']:.3f} {r['phase']} δobs {r['delta_obs']:+.2f} "
                  f"best {r['d_best']:+.2f} true {r['d_true_cnt']:+.2f} vir {r['virada_pct']}")
        return

    def tl(s):
        tmax = float(np.quantile(s.tr["t_tot2"], 0.999))
        return (list(np.arange(S17 + 60, min(tmax, 21 * 3600) + 1, args.step))
                + list(np.arange(21 * 3600 + 600, tmax + 600, 600.0)))

    s1t = with_1t_order(sim)
    runs = {"producao": (sim, "producao"), "zonas": (sim, "zonas"), "bu_pesado": (sim, "bu_pesado"),
            "bu_imediato": (sim, "bu_imediato"), "ordem_1t": (s1t, "producao"), "ordem_1t_zonas": (s1t, "zonas")}
    rows = {}
    for name, (s, sc) in runs.items():
        ts = time.time()
        rows[name] = run_scenario(s, P, sc, tl(s), full=True)
        print(f"{name}: {len(rows[name])} passos em {time.time() - ts:.0f}s")
    # virada bruta real
    o = np.argsort(sim.tr["t_tot2"])
    cum = np.cumsum((sim.tr["l2"] - sim.tr["o2"])[o])
    jf = int(np.flatnonzero(cum < 0)[-1] + 1)
    raw_flip = (float(sim.tr["t_tot2"][o][jf]), 100 * (jf + 1) / sim.base.n)
    B_pcts = [5, 10, 20, 30, 50, 70, 90]
    B = {}
    for name, rs in rows.items():
        B[name] = {"errs": [r["err_pp"] for r in at_pcts(rs, B_pcts)], "cov": coverage_stats(rs)}
        if len(B[name]["errs"]) < len(B_pcts):
            B[name]["errs"] += [None] * (len(B_pcts) - len(B[name]["errs"]))
    allr = [r for rs in rows.values() for r in rs if 2 <= r["pct"] <= 99]
    B["(todos, agrupado)"] = {"errs": [None] * len(B_pcts), "cov": {
        "n": len(allr), "cov_cal": float(np.mean([r["cov_cal"] for r in allr])),
        "cov_final": float(np.mean([r["cov"] for r in allr])),
        "max_abs_err_pp": float(np.max([abs(r["err_pp"]) for r in allr])),
        "cov_cal_ge10": float(np.mean([r["cov_cal"] for r in allr if r["pct"] >= 10])),
        "cov_final_ge10": float(np.mean([r["cov"] for r in allr if r["pct"] >= 10]))}}
    C = {name: call_times([r for r in rs if r["pct"] >= P.pre_max_pct], raw_flip) for name, rs in rows.items()}
    A = at_pcts(rows["producao"], [2, 5, 10, 20, 30, 40, 50, 60, 70, 80, 90, 95], dedupe=True)
    D_pcts = [5, 10, 20, 30, 50]
    D = bias_table(sim, P, "zonas", D_pcts)
    Dp = bias_table(sim, P, "producao", D_pcts)
    F = sorted(tune(sim, P), key=lambda r: r["rmse_pp"]) if not args.no_tune else []
    ctx = {"params": P, "truth": truth, "taus": taus, "scenarios": {**SCENARIOS, "ordem_1t":
           "estresse: votos do 2T 2022 chegando na ordem/horário do 1T 2022 (reescalado), BUs como em `producao`",
           "ordem_1t_zonas": "estresse, pior caso: ordem do 1T e só agregados de zona (nenhum BU)"},
           "A": A, "B": B, "B_pcts": B_pcts, "C": C, "D": D, "Dp": Dp, "D_pcts": D_pcts, "F": F,
           "generated": time.strftime("%Y-%m-%d %H:%M"), "runtime_s": time.time() - t0}
    write_md(HERE / "BACKTEST.md", ctx)
    dump = {k: v for k, v in ctx.items() if k != "params"} | {"params": dataclasses.asdict(P), "raw_flip": raw_flip}
    (HERE / "cache").mkdir(exist_ok=True)
    (HERE / "cache" / "backtest_report.json").write_text(json.dumps(dump, indent=1, default=lambda x: float(x) if np.isscalar(x) else str(x)))
    print(f"ok {time.time() - t0:.0f}s → {HERE / 'BACKTEST.md'}")


if __name__ == "__main__":
    main()

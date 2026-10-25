"""Modelo: termômetro da campanha, ritmo, projeção e virada. Funções puras (sem I/O).

Fluxo: `prepare(base, live, params)` monta, por zona, as partes comparáveis (exata / estimada) e a base do
restante; `project(base, prep, params)` faz o encolhimento hierárquico do swing e projeta o restante;
`compute(base, live, params)` junta tudo no dicionário publicado em analytics.json (sem `history`).

Notação (por eleitor apto): L = Lula, O = adversário, X = demais do 1T, BN = brancos+nulos, AB = abstenção,
A = aptos, C = comparecimento. Sufixo 1 = 1º turno, 2 = 2º turno. Convenções do contrato §6.3:
margem = L − O; δ = votos de margem por 100 aptos; percentuais 0–100; faixa = [lo, hi] de 90%;
taxas com os aptos de cada turno (A1 no 1T, A2 no 2T).
"""

from __future__ import annotations

import dataclasses
import math
from dataclasses import dataclass, field

import numpy as np

from .base import REGIOES, UFS, Base

# colunas dos agregados oficiais (contrato §4 agg.json)
AGG = ["st", "ts", "te", "est", "c", "a", "vb", "tvn", "l", "o"]
IA = {c: i for i, c in enumerate(AGG)}
# 1T: [A1, C1, L1, O1, X1, BN1, AB1]   2T: [A2, C2, L2, O2, BN2, AB2]
I1 = {"A": 0, "C": 1, "L": 2, "O": 3, "X": 4, "BN": 5, "AB": 6}
I2 = {"A": 0, "C": 1, "L": 2, "O": 3, "BN": 4, "AB": 5}
K = ["L", "O", "BN", "AB"]           # componentes projetadas (por apto); X vai a zero no 2T
K1 = [I1[k] for k in K]
K2 = [I2[k] for k in K]

SRC_NONE, SRC_ZONE, SRC_MUN_SPLIT, SRC_BU, SRC_UF_SPLIT = 0, 1, 2, 3, 4


@dataclass
class Params:
    # base 1T da parte apurada-sem-BU de uma zona parcial:
    #   "order" = seções da zona que chegaram primeiro no 1T (casadas por aptos); "mean" = pró-rata da zona;
    #   "soft" = posto suave: P(seção já apurada | posto q no 1T, fração f apurada) = Φ((Φ⁻¹(f) − ρΦ⁻¹(q))/√(1−ρ²))
    baseline: str = "soft"
    rho: float = 0.75                      # correlação (cópula gaussiana) entre a ordem de chegada 1T e 2T na zona
    w_est: float = 1.0                     # peso da parte estimada no swing observado da zona
    # pseudo-contagens do encolhimento, em eleitores aptos (zona → município → UF×tercil → UF → região → BR).
    # Calibradas no backtest 2022 (grade em BACKTEST.md): bem maiores que o ruído amostral puro, porque o que
    # já foi apurado numa área não é amostra aleatória do que falta.
    k_zone: float = 80_000.0
    k_mun: float = 200_000.0
    k_grp: float = 400_000.0
    k_uf: float = 800_000.0
    k_reg: float = 2_000_000.0
    k_br: float = 800_000.0
    # desvio-padrão do efeito de cada nível no swing de margem por apto, (ΔL−ΔO)/A; estimados no 2T 2022
    # (DP ponderado por aptos do swing final da área em torno do pai; br = |swing nacional|)
    tau_zone: float = 0.0040
    tau_mun: float = 0.0088
    tau_grp: float = 0.0066
    tau_uf: float = 0.0047
    tau_reg: float = 0.0123
    tau_br: float = 0.0259
    bias_imp: float = 0.004                # viés possível da imputação por posto (por apto), sobre a fração imputada
    imp_rho_lo: float = 0.5                # sensibilidade da imputação: refaz com ρ baixo e com posto exato (ρ=1)
    band_mult: float = 1.1                 # calibração: 90% de cobertura no backtest 2022 (união dos cenários; 1,077)
    band_widen: float = 1.5                # alargamento deliberado depois da calibração (contrato §6 / revisão)
    band_floor: float = 0.003              # meia-faixa mínima por apto não apurado
    z_band: float = 1.645                  # faixa de 90%
    # fases (contrato §6.4)
    pre_max_pct: float = 2.0               # % de seções: abaixo disto, fase "pre"
    final_min_pct: float = 99.5            # % de seções oficiais para "final" (ou decidido pelo oficial)
    incons_block_frac: float = 0.01        # bloqueia chamada se o excesso inconsistente > 1% dos aptos
    incons_cov_tol: float = 0.05           # zonas com mais de 5% (seções/aptos da UF) além do oficial = inconsistente
    incons_margin_tol: float = 0.02        # margem por apto difere > 2 p.p. com a mesma cobertura = inconsistente
    incons_future_s: float = 600.0         # zona com hora > hora do BR + 10 min = estado de outra rodada
    call_min_pct: float = 30.0             # % de seções mínimo para can_call
    call_min_reg_pct: float = 5.0          # % de aptos apurados mínimo em cada região (exceto ZZ)
    capture_min_pool_frac: float = 0.02    # capture_l só se |P| ≥ isto × aptos cobertos …
    capture_min_pool: float = 50_000.0     # … e ≥ isto em votos

    def k_levels(self):
        return self.k_zone, self.k_mun, self.k_grp, self.k_uf, self.k_reg, self.k_br

    def tau_levels(self):
        return self.tau_zone, self.tau_mun, self.tau_grp, self.tau_uf, self.tau_reg, self.tau_br


@dataclass
class Live:
    """Entradas ao vivo já alinhadas à base. Tudo float64."""
    zone: np.ndarray                       # Z×10 (AGG)
    zone_has: np.ndarray                   # Z bool
    mun: np.ndarray | None = None          # M×10
    mun_has: np.ndarray | None = None
    br: dict | None = None                 # agregado BR oficial (chaves de AGG)
    uf: dict | None = None                 # {uf: agregado}
    bu: np.ndarray | None = None           # N×7 [has_bu, aptos2, comp2, l2, o2, b2, n2]
    now_s: float | None = None             # segundos desde 00:00 BRT do dia da eleição
    meta: dict = field(default_factory=dict)
    zone_t: np.ndarray | None = None       # Z: dt/ht do agregado de zona (s desde 00:00 do dia do BR); NaN sem
    br_t: float | None = None              # dt/ht do agregado BR


def _bc(idx: np.ndarray, w: np.ndarray, n: int) -> np.ndarray:
    """bincount por coluna; w pode ser 1-D ou 2-D."""
    if w.ndim == 1:
        return np.bincount(idx, weights=w, minlength=n)
    return np.stack([np.bincount(idx, weights=w[:, j], minlength=n) for j in range(w.shape[1])], axis=1)


def _div(a, b, default=0.0):
    a = np.asarray(a, dtype=np.float64)
    b = np.asarray(b, dtype=np.float64)
    with np.errstate(divide="ignore", invalid="ignore"):
        r = np.where(b != 0, a / np.where(b != 0, b, 1.0), default)
    return float(r) if r.ndim == 0 else r


# ----------------------------------------------------------------------------------------------------------
# preparação: partes comparáveis por zona
# ----------------------------------------------------------------------------------------------------------
@dataclass
class Prepared:
    agg: np.ndarray            # Z×10 agregados efetivos (após fallback)
    src: np.ndarray            # Z int: origem do agregado
    complete: np.ndarray       # Z bool: zona 100% totalizada
    ex1: np.ndarray            # Z×7 1T da parte exata (zona completa, ou seções com BU)
    ex2: np.ndarray            # Z×6 2T da parte exata
    es1: np.ndarray            # Z×7 1T (base imputada) da parte estimada
    es2: np.ndarray            # Z×6 2T da parte estimada (apurado oficial − BUs)
    K2: np.ndarray             # Z×6 apurado oficial 2T [A2(est),C2,L2,O2,BN2,AB2]
    U: np.ndarray              # Z aptos ainda não apurados (te − est)
    rem1: np.ndarray           # Z×7 1T da parte ainda não apurada (base do restante)
    rem_w: np.ndarray          # N peso de cada seção no restante (0..1)
    te: np.ndarray             # Z aptos totais 2T
    ts: np.ndarray             # Z seções totais
    nbu: np.ndarray            # Z seções com BU
    hb: np.ndarray             # N bool tem BU
    bu_A2: np.ndarray          # Z aptos (2T) cobertos por BU
    inconsistent: list = field(default_factory=list)   # UFs cujas zonas/municípios/BUs não batem com o oficial
    inconsistent_excess: float = 0.0                   # aptos "apurados" a mais nas zonas dessas UFs


def prepare(base: Base, live: Live, params: Params) -> Prepared:
    Z, M = base.Z, base.M
    agg = np.array(live.zone, dtype=np.float64, copy=True)
    has = np.array(live.zone_has, dtype=bool, copy=True)
    src = np.where(has, SRC_ZONE, SRC_NONE).astype(np.int8)
    agg[:, IA["ts"]] = np.where(has & (agg[:, IA["ts"]] > 0), agg[:, IA["ts"]], base.zone_n)
    agg[:, IA["te"]] = np.where(has & (agg[:, IA["te"]] > 0), agg[:, IA["te"]], base.zone_A2)

    # fallback 1: o agregado do município vale quando está à frente da soma das suas zonas (zonas faltando ou
    # defasadas, p. ex. o coletor atrasou as zonas). A diferença (mun − Σ zonas) é rateada entre as zonas do
    # município pelos aptos ainda não apurados de cada uma; zona sem agregado entra com aptos totais.
    if live.mun is not None and live.mun_has is not None:
        zm = base.zone_mun
        mh = np.asarray(live.mun_has, dtype=bool)
        present = _bc(zm[has], agg[has], M)
        resid = np.asarray(live.mun, dtype=np.float64) - present
        trig = mh & (resid[:, IA["st"]] >= 0.5) & (resid[:, IA["est"]] >= 0.5)
        miss = (~has) & mh[zm]
        zt = trig[zm]
        if zt.any():
            rem = np.where(has, np.maximum(agg[:, IA["te"]] - agg[:, IA["est"]], 0.0), base.zone_A2) * zt
            remM = _bc(zm, rem, M)
            altM = _bc(zm, base.zone_A2 * zt, M)
            share = np.where(remM[zm] > 0, _div(rem, remM[zm]), _div(base.zone_A2 * zt, altM[zm]))
            cols = [IA[c] for c in ("st", "est", "c", "a", "vb", "tvn", "l", "o")]
            agg[:, cols] += np.maximum(resid, 0.0)[zm][:, cols] * share[:, None]
            src[zt & (share > 0)] = SRC_MUN_SPLIT
        src[miss] = SRC_MUN_SPLIT
        has = has | miss

    # guarda de consistência: zonas/municípios (estado do coletor) contra o agregado oficial da UF (ou do BR)
    inconsistent, excess, bad_z = _consistency_guard(base, live, agg, has, params)
    if bad_z.any():
        src[bad_z] = SRC_UF_SPLIT
        has = has | bad_z

    N = base.n
    hb = (np.asarray(live.bu[:, 0]) > 0) if live.bu is not None else np.zeros(N, dtype=bool)
    if bad_z.any():
        hb = hb & ~bad_z[base.sec_zone]        # BUs dessas UFs também não são confiáveis
    ib = np.flatnonzero(hb)
    zb = base.sec_zone[ib]
    if len(ib):
        b = np.asarray(live.bu[ib], dtype=np.float64)
        bu2 = np.stack([b[:, 1], b[:, 2], b[:, 3], b[:, 4], b[:, 5] + b[:, 6], b[:, 1] - b[:, 2]], axis=1)
        ZB2 = _bc(zb, bu2, Z)
        ZB1 = _bc(zb, base.s1[ib], Z)
        ZB_A2cad = _bc(zb, base.A2[ib], Z)
        nbu = np.bincount(zb, minlength=Z).astype(np.float64)
    else:
        ZB2, ZB1, ZB_A2cad, nbu = np.zeros((Z, 6)), np.zeros((Z, 7)), np.zeros(Z), np.zeros(Z)

    def bu_as_agg(mask):
        agg[mask, IA["st"]] = nbu[mask]
        agg[mask, IA["est"]] = ZB2[mask, I2["A"]]
        agg[mask, IA["c"]] = ZB2[mask, I2["C"]]
        agg[mask, IA["a"]] = ZB2[mask, I2["AB"]]
        agg[mask, IA["vb"]] = ZB2[mask, I2["BN"]]
        agg[mask, IA["tvn"]] = 0.0
        agg[mask, IA["l"]] = ZB2[mask, I2["L"]]
        agg[mask, IA["o"]] = ZB2[mask, I2["O"]]

    # fallback 2: sem agregado nenhum → só os BUs contam como apurado
    nob = ~has
    if nob.any():
        bu_as_agg(nob)
        src[nob & (nbu > 0)] = SRC_BU
    # agregado defasado em relação aos BUs (seção com BU ainda não somada no arquivo de zona)
    stale = has & ((agg[:, IA["est"]] + 0.5 < ZB2[:, I2["A"]]) | (agg[:, IA["st"]] + 0.5 < nbu))
    if stale.any():
        bu_as_agg(stale)

    te, ts = agg[:, IA["te"]], agg[:, IA["ts"]]
    est = np.minimum(agg[:, IA["est"]], te)
    complete = (ts > 0) & (agg[:, IA["st"]] >= ts)
    # zona com rateio do município: só é completa se os aptos apurados também fecharem
    complete &= (src != SRC_MUN_SPLIT) | (est >= te - 0.5)
    complete = np.where(src == SRC_UF_SPLIT, est >= te - 0.5, complete)
    K2 = np.stack([est, agg[:, IA["c"]], agg[:, IA["l"]], agg[:, IA["o"]],
                   agg[:, IA["vb"]] + agg[:, IA["tvn"]], agg[:, IA["a"]]], axis=1)

    cz = complete[:, None]
    ex1 = np.where(cz, base.zone_1t, ZB1)
    ex2 = np.where(cz, K2, ZB2)
    es2 = np.where(cz, 0.0, np.maximum(K2 - ZB2, 0.0))
    R_A = es2[:, I2["A"]]

    noBU1 = base.zone_1t - ZB1
    noBU_A2 = np.maximum(base.zone_A2 - ZB_A2cad, 0.0)
    if params.baseline == "order":
        es1, rem_w = _order_match(base, hb, R_A)
    elif params.baseline == "soft":
        es1, rem_w = _soft_rank(base, hb, R_A, params.rho)
    else:
        f = np.clip(_div(R_A, noBU_A2), 0.0, 1.0)
        es1 = noBU1 * f[:, None]
        rem_w = (1.0 - f)[base.sec_zone] * (~hb)
    es1 = np.where(cz, 0.0, es1)
    rem1 = np.where(cz, 0.0, np.maximum(noBU1 - es1, 0.0))
    rem_w = np.where(complete[base.sec_zone], 0.0, rem_w)
    U = np.where(complete, 0.0, np.maximum(te - est, 0.0))
    return Prepared(agg=agg, src=src, complete=complete, ex1=ex1, ex2=ex2, es1=es1, es2=es2, K2=K2,
                    U=U, rem1=rem1, rem_w=rem_w, te=te, ts=ts, nbu=nbu, hb=hb, bu_A2=ZB2[:, I2["A"]].copy(),
                    inconsistent=inconsistent, inconsistent_excess=excess)


def _official_groups(base: Base, live: Live):
    """(grupo de cada zona, agregados oficiais G×10, tem G) — por UF se houver agregados de UF, senão BR."""
    if live.uf:
        off = np.zeros((len(UFS), len(AGG)))
        ok = np.zeros(len(UFS), dtype=bool)
        for i, u in enumerate(UFS):
            A = live.uf.get(u)
            if A:
                off[i] = [float(A.get(k, 0) or 0) for k in AGG]
                ok[i] = off[i, IA["ts"]] > 0
        return base.zone_uf(), off, ok, list(UFS)
    if live.br:
        off = np.array([[float(live.br.get(k, 0) or 0) for k in AGG]])
        return np.zeros(base.Z, dtype=np.int64), off, off[:, IA["ts"]] > 0, ["br"]
    return None, None, None, None


def _consistency_guard(base: Base, live: Live, agg: np.ndarray, has: np.ndarray, params: Params):
    """Zonas que não batem com o agregado oficial da UF são descartadas e trocadas pelo oficial da UF rateado
    pelos aptos das zonas (modifica `agg` no lugar). Inconsistente se:
    (a) Σ zonas tem mais apurado que a UF oficial: st > st_UF + max(5; 1% das seções) ou est > est_UF + 1% dos aptos;
    (b) com cobertura parecida (ambas ≥5%, diferença ≤5%), a margem por apto (L−O)/est difere > 1 p.p. dos aptos.
    Retorna (lista de UFs, aptos em excesso, máscara de zonas trocadas)."""
    grp, off, ok, names = _official_groups(base, live)
    bad_z = np.zeros(base.Z, dtype=bool)
    if grp is None:
        return [], 0.0, bad_z
    G = len(names)
    zs = _bc(grp, agg * has[:, None], G)
    te_g = np.where(off[:, IA["te"]] > 0, off[:, IA["te"]], _bc(grp, base.zone_A2, G))
    ts_g = np.where(off[:, IA["ts"]] > 0, off[:, IA["ts"]], _bc(grp, base.zone_n, G))
    st_o, est_o = off[:, IA["st"]], off[:, IA["est"]]
    st_z, est_z = zs[:, IA["st"]], zs[:, IA["est"]]
    # (a) muito mais apurado nas zonas do que no oficial (o atraso normal UF×zona, ~60 s, fica bem abaixo de 5%)
    over = (st_z > st_o + np.maximum(5.0, params.incons_cov_tol * ts_g)) | (est_z > est_o + params.incons_cov_tol * te_g)
    # (b) mesma cobertura, votos diferentes
    m_z = _div(zs[:, IA["l"]] - zs[:, IA["o"]], est_z)
    m_o = _div(off[:, IA["l"]] - off[:, IA["o"]], est_o)
    similar = (est_z >= 0.05 * te_g) & (est_o >= 0.05 * te_g) & (np.abs(est_z - est_o) <= 0.02 * np.maximum(est_o, 1))
    votes = similar & (np.abs(m_z - m_o) > params.incons_margin_tol)
    # (c) arquivo de zona/município "do futuro" em relação ao BR (estado de outra rodada)
    future = np.zeros(G, dtype=bool)
    if live.zone_t is not None and live.br_t is not None:
        zt = np.where(has & np.isfinite(live.zone_t), live.zone_t, -np.inf)
        gmax = np.full(G, -np.inf)
        np.maximum.at(gmax, grp, zt)
        future = gmax > live.br_t + params.incons_future_s
    bad = ok & (over | votes | future)
    if not bad.any():
        return [], 0.0, bad_z
    bad_z = bad[grp]
    te_z = agg[:, IA["te"]]
    share = _div(te_z * bad_z, _bc(grp, te_z * bad_z, G)[grp])
    cols = [IA[c] for c in ("st", "est", "c", "a", "vb", "tvn", "l", "o")]
    idx = np.flatnonzero(bad_z)
    agg[np.ix_(idx, cols)] = off[grp[idx]][:, cols] * share[idx, None]
    excess = float(np.sum(np.maximum(est_z - est_o, 0.0)[bad]))
    return [names[i] for i in np.flatnonzero(bad)], excess, bad_z


def _ndtri(p):
    """Inversa da normal padrão (Acklam), vetorizada; p em (0,1)."""
    p = np.clip(np.asarray(p, dtype=np.float64), 1e-12, 1 - 1e-12)
    a = [-3.969683028665376e01, 2.209460984245205e02, -2.759285104469687e02, 1.383577518672690e02,
         -3.066479806614716e01, 2.506628277459239e00]
    b = [-5.447609879822406e01, 1.615858368580409e02, -1.556989798598866e02, 6.680131188771972e01,
         -1.328068155288572e01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e00, -2.549671880835437e00,
         4.374664141464968e00, 2.938163982698783e00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e00, 3.754408661907416e00]
    out = np.empty_like(p)
    lo, hi = p < 0.02425, p > 1 - 0.02425
    mid = ~(lo | hi)
    q = p[mid] - 0.5
    r = q * q
    out[mid] = ((((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q
                / (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1))
    for m, sgn in ((lo, 1.0), (hi, -1.0)):
        pp = p[m] if sgn > 0 else 1 - p[m]
        qq = np.sqrt(-2 * np.log(pp))
        out[m] = sgn * (((((c[0] * qq + c[1]) * qq + c[2]) * qq + c[3]) * qq + c[4]) * qq + c[5]) / (
            (((d[0] * qq + d[1]) * qq + d[2]) * qq + d[3]) * qq + 1)
    return out


def _ndtr(x):
    from math import sqrt
    return 0.5 * (1.0 + _erf(x / sqrt(2.0)))


def _erf(x):
    # Abramowitz–Stegun 7.1.26 (erro < 1.5e-7), vetorizada
    s = np.sign(x)
    x = np.abs(x)
    t = 1.0 / (1.0 + 0.3275911 * x)
    y = 1.0 - (((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t - 0.284496736) * t + 0.254829592) * t * np.exp(-x * x)
    return s * y


def _soft_rank(base: Base, hb: np.ndarray, R_A: np.ndarray, rho: float, iters: int = 16):
    """Imputação por posto suave. Para cada zona, peso w_s = Φ((c_z − ρ·Φ⁻¹(q_s))/√(1−ρ²)) de cada seção sem BU,
    onde q_s é o posto (ponto médio, por aptos) da seção na ordem de chegada do 1T da zona e c_z é escolhido para
    Σ w_s·A2_s = R_A (bissecção). Retorna (B1 Z×7 = Σ w_s·1T_s, peso_restante N = 1 − w_s)."""
    rho = float(np.clip(rho, 0.0, 0.995))
    if rho >= 0.995:
        return _order_match(base, hb, R_A)
    perm, zs = base.perm_zt, base.zone_start
    Z = len(zs) - 1
    nb = (~hb[perm]).astype(np.float64)
    a = base.A2[perm] * nb
    zpos = base.sec_zone[perm]
    cumA = np.cumsum(a)
    tot = np.bincount(zpos, weights=a, minlength=Z)
    before = cumA - a - np.concatenate([[0.0], cumA])[zs[:-1]][zpos]
    q = np.where(tot[zpos] > 0, (before + a / 2) / np.where(tot[zpos] > 0, tot[zpos], 1.0), 0.5)
    zq = _ndtri(q)
    sd = math.sqrt(1 - rho * rho)
    target = np.minimum(R_A, tot)
    lo = np.full(Z, -9.0)
    hi = np.full(Z, 9.0)
    for _ in range(iters):
        c = (lo + hi) / 2
        w = _ndtr((c[zpos] - rho * zq) / sd)
        got = np.bincount(zpos, weights=w * a, minlength=Z)
        up = got < target
        lo = np.where(up, c, lo)
        hi = np.where(up, hi, c)
    c = (lo + hi) / 2
    w = _ndtr((c[zpos] - rho * zq) / sd) * nb
    got = np.bincount(zpos, weights=w * a, minlength=Z)
    fix = np.where(got > 0, target / np.where(got > 0, got, 1.0), 0.0)
    w = np.clip(w * fix[zpos], 0.0, 1.0)
    full = target >= tot - 1e-6
    w = np.where(full[zpos], nb, w)
    w = np.where((target <= 0)[zpos], 0.0, w)
    B1 = np.stack([np.bincount(zpos, weights=w * base.s1[perm, j], minlength=Z) for j in range(7)], axis=1)
    wsec = np.empty(len(perm))
    wsec[perm] = (1.0 - w) * nb
    return B1, wsec


def _order_match(base: Base, hb: np.ndarray, R_A: np.ndarray):
    """Imputação por posto: para cada zona, soma o 1T das seções sem BU na ordem de chegada do 1T até completar
    R_A aptos (2T). Retorna (B1 Z×7, peso_restante N)."""
    perm, zs = base.perm_zt, base.zone_start
    nb = (~hb[perm]).astype(np.float64)
    a = base.A2[perm] * nb
    cumA = np.concatenate([[0.0], np.cumsum(a)])
    start, end = zs[:-1], zs[1:]
    off = cumA[start]
    tot = cumA[end] - off
    target = off + np.minimum(R_A, tot)
    i = np.searchsorted(cumA, target - 1e-9, side="left") - 1
    i = np.clip(i, start, end - 1)
    ai = a[i]
    frac = np.clip(np.where(ai > 0, (target - cumA[i]) / np.where(ai > 0, ai, 1.0), 0.0), 0.0, 1.0)
    s = base.s1[perm] * nb[:, None]
    cs = np.concatenate([np.zeros((1, s.shape[1])), np.cumsum(s, axis=0)], axis=0)
    B1 = cs[i] - cs[start] + frac[:, None] * s[i]
    none = R_A <= 0
    B1[none] = 0.0
    pos = np.arange(len(perm))
    zone_of_pos = base.sec_zone[perm]
    ii = i[zone_of_pos]
    w = np.where(pos > ii, 1.0, np.where(pos == ii, 1.0 - frac[zone_of_pos], 0.0))
    w = np.where(none[zone_of_pos], 1.0, w) * nb
    wsec = np.empty(len(perm))
    wsec[perm] = w
    return B1, wsec


# ----------------------------------------------------------------------------------------------------------
# projeção
# ----------------------------------------------------------------------------------------------------------
def _swing_obs(p1: np.ndarray, p2: np.ndarray):
    """D_k = k2 − k1·(A2/A1) (desvio em votos, por componente K) e n = A2 de uma parte comparável."""
    A1 = p1[:, I1["A"]]
    A2 = p2[:, I2["A"]]
    r = _div(A2, A1)
    D = p2[:, K2] - p1[:, K1] * r[:, None]
    D[A1 <= 0] = 0.0
    n = np.where(A1 > 0, A2, 0.0)
    return D, n


def project(base: Base, prep: Prepared, params: Params, imp: float = 0.0) -> dict:
    Z, M, G, U_, R_ = base.Z, base.M, base.G, len(UFS), len(REGIOES)
    zm, mg, gu, ur = base.zone_mun, base.mun_grp, base.grp_uf, base.uf_reg
    Dex, Nex = _swing_obs(prep.ex1, prep.ex2)
    Des, Nes = _swing_obs(prep.es1, prep.es2)
    Dz = Dex + params.w_est * Des
    Nz = Nex + params.w_est * Nes
    Dm, Nm = _bc(zm, Dz, M), _bc(zm, Nz, M)
    Dg, Ng = _bc(mg, Dm, G), _bc(mg, Nm, G)
    Du, Nu = _bc(gu, Dg, U_), _bc(gu, Ng, U_)
    Dr, Nr = _bc(ur, Du, R_), _bc(ur, Nu, R_)
    Dbr, Nbr = Dr.sum(axis=0), Nr.sum()
    kz, km, kg, ku, kr, kb = params.k_levels()
    prior = np.zeros(4)
    d_br = (Dbr + kb * prior) / (Nbr + kb)
    d_r = (Dr + kr * d_br[None, :]) / (Nr + kr)[:, None]
    d_u = (Du + ku * d_r[ur]) / (Nu + ku)[:, None]
    d_g = (Dg + kg * d_u[gu]) / (Ng + kg)[:, None]
    d_m = (Dm + km * d_g[mg]) / (Nm + km)[:, None]
    d_z = (Dz + kz * d_m[zm]) / (Nz + kz)[:, None]

    # base do restante (composição 1T por apto das seções ainda não apuradas)
    rem1 = prep.rem1
    remA1 = rem1[:, I1["A"]]
    zone_p1 = _div(base.zone_1t, base.zone_1t[:, [I1["A"]]])
    p1 = np.where((remA1 >= 1.0)[:, None], _div(rem1, np.maximum(remA1, 1e-9)[:, None]), zone_p1)
    p2 = np.maximum(p1[:, K1] + d_z, 0.0)
    ssum = p2.sum(axis=1)
    p2 = np.where((ssum > 0)[:, None], p2 / np.where(ssum > 0, ssum, 1.0)[:, None], p1[:, K1])
    U = prep.U
    projZ = U[:, None] * p2
    cnt = prep.K2[:, K2]
    finZ = cnt + projZ
    L, O, BN, AB = finZ.sum(axis=0)
    V, M_ = L + O, L - O

    # incerteza: Var = Σ_níveis Σ_áreas τ²·k/(N+k)·(restante na área)²  (+ viés da imputação, linear)
    Rz = U
    Rm = _bc(zm, Rz, M)
    Rg = _bc(mg, Rm, G)
    Ru = _bc(gu, Rg, U_)
    Rr = _bc(ur, Ru, R_)
    Rb = Rr.sum()
    tz, tm, tg, tu, tr, tb = params.tau_levels()
    var = (np.sum(tz**2 * kz / (Nz + kz) * Rz**2) + np.sum(tm**2 * km / (Nm + km) * Rm**2)
           + np.sum(tg**2 * kg / (Ng + kg) * Rg**2) + np.sum(tu**2 * ku / (Nu + ku) * Ru**2)
           + np.sum(tr**2 * kr / (Nr + kr) * Rr**2) + tb**2 * kb / (Nbr + kb) * Rb**2)
    sigma_raw = math.sqrt(var)
    f_imp = float(params.w_est * Nes.sum() / Nbr) if Nbr > 0 else 0.0
    bias = params.bias_imp * f_imp * float(Rb)
    # faixa calibrada (backtest) → alargada ×band_widen → piso por apto não apurado
    half_cal = params.band_mult * (params.z_band * sigma_raw + imp + bias)
    half = max(params.band_widen * half_cal, params.band_floor * float(Rb))
    sigma_eff = half / params.z_band
    sigma_model = half_cal / params.z_band
    return {
        "L": float(L), "O": float(O), "BN": float(BN), "AB": float(AB), "V": float(V), "M": float(M_),
        "half": float(half), "half_cal": float(half_cal), "sigma": float(sigma_eff),
        "sigma_model": float(sigma_model), "sigma_raw": float(sigma_raw), "bias": bias, "imp": float(imp),
        "f_imp": f_imp, "U": float(U.sum()), "N_obs": float(Nbr),
        "d_br": d_br, "d_r": d_r, "d_u": d_u, "d_g": d_g, "d_m": d_m, "d_z": d_z,
        "Nz": Nz, "Nex": Nex, "Nm": Nm, "Nu": Nu, "Nr": Nr, "Dz": Dz, "Dm": Dm,
        "projZ": projZ, "finZ": finZ, "p1rem": p1, "p2": p2,
    }


# ----------------------------------------------------------------------------------------------------------
# termômetro
# ----------------------------------------------------------------------------------------------------------
def _part_extras(p1: np.ndarray, p2: np.ndarray) -> np.ndarray:
    """Por zona (uma parte comparável): [pool_bruto, abst_out, decomp_comparecimento, decomp_preferência].

    pool_bruto = X1 + BN1 + max(0, ΔC); abst_out = max(0, −ΔC). Bennet com m = (L−O)/C:
    ΔM = ΔC·(m1+m2)/2 [comparecimento] + Δm·(C1+C2)/2 [preferência], exato por zona-parte."""
    C1, C2 = p1[:, I1["C"]], p2[:, I2["C"]]
    has = (p1[:, I1["A"]] > 0) & (p2[:, I2["A"]] > 0)
    dC = np.where(has, C2 - C1, 0.0)
    pool = np.where(has, p1[:, I1["X"]] + p1[:, I1["BN"]] + np.maximum(dC, 0.0), 0.0)
    aout = np.maximum(-dC, 0.0)
    # m = 0 quando C = 0: a identidade de Bennet continua exata
    m1 = _div(p1[:, I1["L"]] - p1[:, I1["O"]], C1)
    m2 = _div(p2[:, I2["L"]] - p2[:, I2["O"]], C2)
    turn = np.where(has, dC * (m1 + m2) / 2, 0.0)
    pref = np.where(has, (m2 - m1) * (C1 + C2) / 2, 0.0)
    return np.stack([pool, aout, turn, pref], axis=1)


def _metrics(s1: np.ndarray, s2: np.ndarray, ext: np.ndarray, A_area: float, allow_capture: bool,
             params: Params) -> dict | None:
    """M do contrato §6.4 (+ extras). s1 = [A1,C1,L1,O1,X1,BN1,AB1], s2 = [A2,C2,L2,O2,BN2,AB2],
    ext = [pool_bruto, abst_out, decomp_comparecimento, decomp_preferência] somados por zona-parte."""
    A1, C1, L1, O1, X1, BN1, AB1 = (float(x) for x in s1)
    A2, C2, L2, O2, BN2, AB2 = (float(x) for x in s2)
    if A2 <= 0 or A1 <= 0:
        return None
    pool_g, aout, d_turn, d_pref = (float(x) for x in ext)
    dL, dO = L2 - L1, O2 - O1
    pool = dL + dO
    cap_ok = allow_capture and pool >= max(params.capture_min_pool, params.capture_min_pool_frac * A2)
    return {
        "d_abst_pp": 100 * (AB2 / A2 - AB1 / A1), "d_abst_n": round(AB2 - AB1),
        "d_bn_pp": 100 * (BN2 / A2 - BN1 / A1), "d_bn_n": round(BN2 - BN1),
        "d_l": round(dL), "d_o": round(dO), "d_margin_votes": round(dL - dO),
        "delta": 100 * ((L2 - O2) / A2 - (L1 - O1) / A1),
        "capture_l": dL / pool if cap_ok else None,
        # extras
        "capture": ({"l": dL / pool_g, "o": dO / pool_g, "bn": BN2 / pool_g, "abst": aout / pool_g}
                    if pool_g > 0 else None),
        "decomp": {"turnout": round(d_turn), "preference": round(d_pref)},
        "coverage_aptos_pct": 100 * A2 / A_area if A_area else 0.0,
        "aptos2": round(A2), "aptos1": round(A1),
        "abst1_pct": 100 * AB1 / A1, "abst2_pct": 100 * AB2 / A2,
        "bn1_pct_comp": 100 * BN1 / C1 if C1 else None, "bn2_pct_comp": 100 * BN2 / C2 if C2 else None,
        "d_bn_pp_comp": 100 * (BN2 / C2 - BN1 / C1) if C1 and C2 else None,
        "d_l_100": 100 * (L2 / A2 - L1 / A1), "d_o_100": 100 * (O2 / A2 - O1 / A1),
        "d_comp_n": round(C2 - C1),
        "l1": round(L1), "o1": round(O1), "x1": round(X1), "l2": round(L2), "o2": round(O2),
        "margin1": round(L1 - O1), "margin2": round(L2 - O2),
        "pool": round(pool), "pool_gross": round(pool_g),
        "pool_sources": {"x1": round(X1), "bn_drop": round(BN1 - BN2), "abst_drop": round(AB1 - AB2),
                         "aptos_change": round(A2 - A1)},
    }


def thermometer(base: Base, prep: Prepared, params: Params) -> dict:
    U_, R_ = len(UFS), len(REGIOES)
    zu, zr = base.zone_uf(), base.zone_reg()
    ext_ex = _part_extras(prep.ex1, prep.ex2)
    ext_es = _part_extras(prep.es1, prep.es2)
    parts = {
        "exact": (prep.ex1, prep.ex2, ext_ex),
        "estimated": (prep.es1, prep.es2, ext_es),
        "best": (prep.ex1 + prep.es1, prep.ex2 + prep.es2, ext_ex + ext_es),
    }
    te = prep.te
    teu, ter, teb = _bc(zu, te, U_), _bc(zr, te, R_), te.sum()
    out = {"br": {}, "regiao": {r: {} for r in REGIOES}, "uf": {u: {} for u in UFS}}
    for name, (p1, p2, ex) in parts.items():
        u1, u2, ue = _bc(zu, p1, U_), _bc(zu, p2, U_), _bc(zu, ex, U_)
        r1, r2, re_ = _bc(zr, p1, R_), _bc(zr, p2, R_), _bc(zr, ex, R_)
        out["br"][name] = _metrics(p1.sum(axis=0), p2.sum(axis=0), ex.sum(axis=0), teb, True, params)
        for i, r in enumerate(REGIOES):
            out["regiao"][r][name] = _metrics(r1[i], r2[i], re_[i], ter[i], True, params)
        for i, u in enumerate(UFS):
            out["uf"][u][name] = _metrics(u1[i], u2[i], ue[i], teu[i], False, params)
    for area in [out["br"], *out["regiao"].values(), *out["uf"].values()]:
        b = area.get("best")
        area["coverage_aptos_pct"] = b["coverage_aptos_pct"] if b else 0.0
        e = area.get("exact")
        area["coverage_exact_pct"] = e["coverage_aptos_pct"] if e else 0.0
    return out


# ----------------------------------------------------------------------------------------------------------
# virada (cruzamento do placar bruto) projetada
# ----------------------------------------------------------------------------------------------------------
def virada_projetada(base: Base, prep: Prepared, proj: dict, M_now: float, st_now: float, ts_total: float,
                     now_s: float | None, start_s: float = 17 * 3600, virada_fim_pct: float = 99.0) -> dict:
    """Onde o placar bruto deve cruzar zero. Ordem prevista do restante: horário de totalização do 1T de cada
    seção, reescalado pelo ritmo observado da sua UF no 2T (curva de progresso do 1T da UF como molde).
    Backtest 2022 (margens oráculo): erro de ±10 p.p. de seções; horário adiantado em 10–40 min."""
    M_fin = proj["M"]
    res = {"projected": False, "pct_secoes": None, "eta_s": None, "fim": False,
           "leader_now": "L" if M_now > 0 else ("O" if M_now < 0 else None),
           "leader_final": "L" if M_fin > 0 else "O"}
    if st_now <= 0 or np.sign(M_now) == np.sign(M_fin):
        return res
    w = prep.rem_w
    sel = np.flatnonzero(w > 0)
    if not len(sel):
        res["fim"] = True
        return res
    now = float(now_s) if now_s is not None else start_s + 3600.0
    U_ = len(UFS)
    zu = base.zone_uf()
    st_u = _bc(zu, prep.agg[:, IA["st"]], U_)
    ts_u = _bc(zu, prep.ts, U_)
    f_u = _div(st_u, ts_u)
    t1all = np.sort(base.t1)
    k = int(np.clip(st_now * len(t1all) / max(ts_total, 1), 1, len(t1all))) - 1
    r_nat = float(np.clip((now - start_s) / max(t1all[k] - start_s, 60.0), 0.3, 3.0))
    r_u = np.full(U_, r_nat)
    for u in range(U_):
        tu = base.uf_t1_sorted[u]
        if f_u[u] > 0 and len(tu):
            tau = tu[max(0, int(np.ceil(f_u[u] * len(tu))) - 1)]
            r_u[u] = float(np.clip((now - start_s) / max(tau - start_s, 60.0), 0.3, 3.0))
    pred = start_s + r_u[base.sec_uf[sel]] * (base.t1[sel] - start_s)
    pred = np.where(pred < now, now + (pred - start_s) * 1e-3, pred)   # atrasadas: chegam já
    o = np.argsort(pred, kind="stable")
    sel, pred = sel[o], pred[o]
    z = base.sec_zone[sel]
    s1 = base.s1[sel]
    A1 = np.maximum(s1[:, I1["A"]], 1.0)
    p = np.maximum(s1[:, K1] / A1[:, None] + proj["d_z"][z], 0.0)
    p = p / np.maximum(p.sum(axis=1), 1e-12)[:, None]
    m = w[sel] * base.A2[sel] * (p[:, 0] - p[:, 1])
    mz = np.bincount(z, weights=m, minlength=base.Z)
    target = proj["projZ"][:, 0] - proj["projZ"][:, 1]
    ok = np.abs(mz) > 1.0
    fac = np.clip(np.where(ok, target / np.where(ok, mz, 1.0), 1.0), 0.0, 3.0)
    m = m * fac[z]
    cum = M_now + np.cumsum(m)
    bad = np.flatnonzero(np.sign(cum) != np.sign(M_fin))
    j = int(bad[-1] + 1) if len(bad) else 0
    if j >= len(cum) or not ts_total:
        res["fim"] = True          # o restante só vira no fim (ou não vira) pela ordem prevista
        return res
    # progresso em fração do peso restante → seções (nunca passa de 100%)
    cw = np.cumsum(w[sel])
    secs = st_now + (cw[j] / cw[-1]) * max(ts_total - st_now, 0.0)
    pct = float(min(100.0, 100.0 * secs / ts_total))
    if pct >= virada_fim_pct:
        res["fim"] = True
        return res
    res["projected"] = True
    res["pct_secoes"] = pct
    res["eta_s"] = float(pred[j])
    return res


# ----------------------------------------------------------------------------------------------------------
# compute: tudo junto
# ----------------------------------------------------------------------------------------------------------
def _phi(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def run_model(base: Base, live: Live, params: Params) -> tuple[Prepared, dict]:
    """Imputação + projeção + faixa, incluindo a sensibilidade à imputação (o mesmo para ao vivo e backtest).

    imp = max |M − M_alt| com as alternativas de imputação ρ=imp_rho_lo e posto exato (ρ=1); entra linear na faixa
    porque o erro de imputação é sistemático (mesmo sinal em todas as zonas)."""
    prep = prepare(base, live, params)
    proj = project(base, prep, params)
    imp = 0.0
    if proj["f_imp"] > 0:
        alts = []
        if params.baseline != "order":
            alts.append(dataclasses.replace(params, baseline="order"))
        if not (params.baseline == "soft" and params.rho <= params.imp_rho_lo):
            alts.append(dataclasses.replace(params, baseline="soft", rho=params.imp_rho_lo))
        for pa in alts:
            Ma = project(base, prepare(base, live, pa), params)["M"]
            imp = max(imp, abs(proj["M"] - Ma))
        proj = project(base, prep, params, imp=imp)
    return prep, proj


def compute(base: Base, live: Live, params: Params, include_zones: bool = True) -> dict:
    prep, proj = run_model(base, live, params)
    th = thermometer(base, prep, params)
    zr = base.zone_reg()
    zu = base.zone_uf()
    T1 = base.totals_1t

    # apurado oficial (placar bruto): BR do TSE se houver, senão soma das zonas
    if live.br is not None and float(live.br.get("ts", 0)) > 0:
        g = lambda k: float(live.br.get(k, 0) or 0)  # noqa: E731
        ap = {k: g(k) for k in AGG}
    else:
        ap = {k: float(prep.agg[:, IA[k]].sum()) for k in AGG}
    if not ap["te"]:
        ap["te"] = float(prep.te.sum())
    st, ts = ap["st"], ap["ts"] or float(base.n)
    M_now, V_now = ap["l"] - ap["o"], ap["l"] + ap["o"]
    pct_secoes = 100 * st / ts if ts else 0.0
    pct_aptos = 100 * ap["est"] / ap["te"] if ap["te"] else 0.0

    # projeção e faixa
    Mh, Vh, sig, half = proj["M"], proj["V"], proj["sigma"], proj["half"]
    lo, hi = Mh - half, Mh + half
    p_l = _phi(Mh / sig) if sig > 0 else (1.0 if Mh > 0 else 0.0)
    p_l = float(min(max(p_l, 0.001), 0.999))     # a tela mostra ">99%"; nunca 0/1 por arredondamento

    # cobertura por região: dos agregados OFICIAIS de UF quando houver (senão, das zonas já conferidas)
    est_z = prep.K2[:, I2["A"]]
    if live.uf:
        eu = np.array([float((live.uf.get(u) or {}).get("est", 0) or 0) for u in UFS])
        tu = np.array([float((live.uf.get(u) or {}).get("te", 0) or 0) for u in UFS])
        tu = np.where(tu > 0, tu, _bc(zu, prep.te, len(UFS)))
        covr = 100 * _div(_bc(base.uf_reg, eu, len(REGIOES)), _bc(base.uf_reg, tu, len(REGIOES)))
    else:
        covr = 100 * _div(_bc(zr, est_z, len(REGIOES)), _bc(zr, prep.te, len(REGIOES)))
    min_reg = float(min(covr[i] for i, r in enumerate(REGIOES) if r != "ZZ"))
    U = proj["U"]
    # "final" só com números oficiais: ≥99,5% das seções, ou decidido = diferença oficial > aptos oficiais restantes;
    # e nunca contra o sinal da projeção
    U_off = max(ap["te"] - ap["est"], 0.0)
    decided = st > 0 and abs(M_now) > U_off
    complete = ts > 0 and pct_secoes >= params.final_min_pct
    agrees = (Mh > 0) == (M_now > 0) and M_now != 0
    incons = prep.inconsistent
    big_incons = prep.inconsistent_excess > params.incons_block_frac * float(ap["te"] or prep.te.sum())
    band_one_side = lo > 0 or hi < 0
    can_call = bool(band_one_side and pct_secoes >= params.call_min_pct and min_reg >= params.call_min_reg_pct
                    and not big_incons)
    if (complete or decided) and agrees and not big_incons:
        phase = "final"
    elif pct_secoes < params.pre_max_pct:
        phase = "pre"
    elif can_call:
        phase = "trend"
    else:
        phase = "early"
    leader = "L" if Mh > 0 else "O"
    call = leader if (phase == "final" or can_call) else None      # sempre o sinal da projeção
    if phase == "pre":
        reason = f"menos de {params.pre_max_pct:g}% das seções: só base do 1T e ritmo necessário"
    elif phase == "final":
        reason = "apuração completa" if complete else "o restante não alcança a diferença atual"
    elif big_incons:
        reason = "entradas inconsistentes (zonas/municípios não batem com o oficial da UF): sem chamada"
    elif can_call:
        reason = "faixa de 90% inteira de um lado, ≥30% apurado e todas as regiões (N, NE, CO, SE, S) ≥5%"
    elif band_one_side:
        reason = (f"faixa de um lado, mas falta cobertura (seções {pct_secoes:.0f}% < {params.call_min_pct:g}% "
                  f"ou região mínima {min_reg:.0f}% < {params.call_min_reg_pct:g}%)")
    else:
        reason = "faixa de 90% inclui o empate"
    mix_r, mix_u, caption = count_mix(base, prep)
    status = {"phase": phase, "pct_secoes": pct_secoes, "pct_aptos": pct_aptos, "can_call": can_call or phase == "final",
              "mix_regiao": mix_r, "mix_uf": mix_u, "mix_caption": caption,
              "inconsistent_inputs": incons,
              "warnings": ([f"zonas/municípios inconsistentes com o agregado oficial em {', '.join(incons)}: "
                            "usando o oficial da UF rateado"] if incons else []),
              "call": call, "reason": reason, "leader_projected": leader, "band_one_side": band_one_side,
              "decided": decided, "min_region_pct_aptos": min_reg,
              "region_pct_aptos": {r: float(covr[i]) for i, r in enumerate(REGIOES)}}

    # ritmo (δ = votos de margem por 100 aptos)
    delta_req = 100.0 * (T1["O1"] - T1["L1"]) / T1["A1"]
    p1 = proj["p1rem"]
    M1_rem = float(np.sum(prep.U * (p1[:, I1["L"]] - p1[:, I1["O"]])))
    M2_cnt = float(np.sum(prep.K2[:, I2["L"]] - prep.K2[:, I2["O"]]))
    delta_rem = 100.0 * -(M2_cnt + M1_rem) / U if U > 0 else None
    teu = _bc(zu, prep.te, len(UFS))
    dmu = proj["d_u"][:, 0] - proj["d_u"][:, 1]
    delta_obs = 100.0 * float(np.sum(teu * dmu) / teu.sum())
    A_tot = float(prep.te.sum())
    delta_proj = 100.0 * (Mh - (T1["L1"] - T1["O1"])) / A_tot
    dband = 100.0 * half / A_tot
    pace = {
        "delta_req": delta_req, "delta_rem": delta_rem, "delta_obs": delta_obs,
        "delta_obs_band": [delta_obs - dband, delta_obs + dband],
        # extras
        "delta_obs_pooled_best": (th["br"]["best"] or {}).get("delta"),
        "delta_obs_pooled_exact": (th["br"]["exact"] or {}).get("delta"),
        "delta_proj": delta_proj,
        "uf": {u: {"delta_shrunk": 100.0 * float(dmu[i]),
                   "delta_best": (th["uf"][u]["best"] or {}).get("delta"),
                   "coverage_aptos_pct": th["uf"][u]["coverage_aptos_pct"]} for i, u in enumerate(UFS)},
        "regiao": {r: {"delta_shrunk": 100.0 * float(proj["d_r"][i, 0] - proj["d_r"][i, 1]),
                       "delta_best": (th["regiao"][r]["best"] or {}).get("delta"),
                       "coverage_aptos_pct": th["regiao"][r]["coverage_aptos_pct"]} for i, r in enumerate(REGIOES)},
    }
    pool_proj = (proj["L"] - T1["L1"]) + (proj["O"] - T1["O1"])
    gap = T1["O1"] - T1["L1"]
    pace["capture_needed"] = 0.5 + gap / (2 * pool_proj) if pool_proj > 0 else None
    pace["capture_projected"] = (proj["L"] - T1["L1"]) / pool_proj if pool_proj > 0 else None

    vir = virada_projetada(base, prep, proj, M_now, st, ts, live.now_s)

    finU = _bc(zu, proj["finZ"], len(UFS))
    estU = _bc(zu, est_z, len(UFS))
    proj_uf = {}
    for i, u in enumerate(UFS):
        Lu, Ou = finU[i, 0], finU[i, 1]
        Vu = Lu + Ou
        proj_uf[u] = {"l": round(Lu), "o": round(Ou), "margin_votes": round(Lu - Ou),
                      "l_pct_valid": 100 * Lu / Vu if Vu else None,
                      "margin_pp_valid": 100 * (Lu - Ou) / Vu if Vu else None,
                      "pct_aptos": 100 * estU[i] / teu[i] if teu[i] else 0.0}
    projection = {
        "l": round(proj["L"]), "o": round(proj["O"]), "margin_votes": round(Mh),
        "margin_pp_valid": 100 * Mh / Vh if Vh else None,
        "band_votes": [round(lo), round(hi)],
        "band_pp_valid": [100 * lo / Vh, 100 * hi / Vh] if Vh else None,
        "p_lula": p_l if phase != "pre" else None,
        "virada_pct_secoes": vir["pct_secoes"],
        "virada_fim": vir["fim"],
        # extras
        "l_pct_valid": 100 * proj["L"] / Vh if Vh else None,
        "o_pct_valid": 100 * proj["O"] / Vh if Vh else None,
        "valid": round(Vh), "bn": round(proj["BN"]), "abst": round(proj["AB"]),
        "comp": round(proj["L"] + proj["O"] + proj["BN"]),
        "abst_pct": 100 * proj["AB"] / A_tot if A_tot else None,
        "sigma_votes": round(sig), "sigma_model_votes": round(proj["sigma_model"]),
        "imputed_share": proj["f_imp"], "remaining_aptos": round(U),
        "remaining_pct": 100 * U / A_tot if A_tot else None,
        "virada": vir, "uf": proj_uf, "mun": _proj_mun(base, proj),
    }
    apurado = {"l": int(ap["l"]), "o": int(ap["o"]), "vb": int(ap["vb"]), "tvn": int(ap["tvn"]),
               "c": int(ap["c"]), "a": int(ap["a"]), "te": int(ap["te"]), "est": int(ap["est"]),
               "margin_votes": int(M_now),
               "l_pct_valid": 100 * ap["l"] / V_now if V_now else None,
               "o_pct_valid": 100 * ap["o"] / V_now if V_now else None,
               # extras
               "st": int(st), "ts": int(ts), "pct_secoes": pct_secoes, "pct_aptos": pct_aptos,
               "margin_pp_valid": 100 * M_now / V_now if V_now else None,
               "secoes_com_bu": int(prep.hb.sum()), "zonas_completas": int(prep.complete.sum()),
               "zonas_total": int(base.Z), "zonas_com_dados": int((est_z > 0).sum()),
               "zonas_sem_agregado": int((prep.src != SRC_ZONE).sum())}
    baseline = {"l": round(T1["L1"]), "o": round(T1["O1"]), "margin_votes": round(T1["L1"] - T1["O1"]),
                "aptos": round(T1["A1"]), "x": round(T1["X1"]), "bn": round(T1["BN1"]), "abst": round(T1["AB1"]),
                "comp": round(T1["C1"]),
                "l_pct_valid": 100 * T1["L1"] / (T1["L1"] + T1["O1"] + T1["X1"]),
                "o_pct_valid": 100 * T1["O1"] / (T1["L1"] + T1["O1"] + T1["X1"])}
    out = {"status": status, "baseline_1t": baseline, "apurado": apurado, "pace": pace,
           "thermometer": th, "projection": projection}
    if include_zones:
        out["zones"], out["mun"] = zone_swings(base, prep, proj)
    out["_internal"] = {"prep": prep, "proj": proj}
    return out


def _proj_mun(base: Base, proj: dict) -> dict:
    """Margem final projetada por município: {cd_mun: {margin_votes, margin_pp_valid, l_pct_valid}}."""
    fm = _bc(base.zone_mun, proj["finZ"][:, :2], base.M)
    L, O = fm[:, 0], fm[:, 1]
    V = L + O
    mv = np.round(L - O).astype(np.int64).tolist()
    mp = np.round(100.0 * _div(L - O, V), 2).tolist()
    lp = np.round(100.0 * _div(L, V), 2).tolist()
    return {str(int(c)): {"margin_votes": mv[i], "margin_pp_valid": mp[i] if V[i] > 0 else None,
                          "l_pct_valid": lp[i] if V[i] > 0 else None} for i, c in enumerate(base.mun_codes)}


REGIAO_NOME = {"N": "Norte", "NE": "Nordeste", "CO": "Centro-Oeste", "SE": "Sudeste", "S": "Sul", "ZZ": "Exterior"}


def _mix(idx: np.ndarray, n: int, prep: Prepared) -> list[dict]:
    st = _bc(idx, prep.agg[:, IA["st"]], n)
    ts = _bc(idx, prep.ts, n)
    est = _bc(idx, prep.K2[:, I2["A"]], n)
    te = _bc(idx, prep.te, n)
    E, T = est.sum(), te.sum()
    out = []
    for i in range(n):
        sa = 100 * est[i] / E if E > 0 else 0.0
        se = 100 * te[i] / T if T > 0 else 0.0
        out.append({"pct_apurado": 100 * st[i] / ts[i] if ts[i] else 0.0, "share_apurado": sa,
                    "share_eleitorado": se, "sobre_rep": (sa / se) if (E > 0 and se > 0) else None})
    return out


def count_mix(base: Base, prep: Prepared, hi: float = 1.25, lo: float = 0.8, min_gap_pp: float = 3.0):
    """Composição do que já foi apurado vs eleitorado, por região e UF, mais uma legenda em pt-BR.
    Descreve a ordem real de chegada a partir dos dados (não supõe Sul/Sudeste primeiro)."""
    mr = _mix(base.zone_reg(), len(REGIOES), prep)
    mu = _mix(base.zone_uf(), len(UFS), prep)
    mix_r = {r: mr[i] for i, r in enumerate(REGIOES)}
    mix_u = {u: mu[i] for i, u in enumerate(UFS)}
    if prep.K2[:, I2["A"]].sum() <= 0:
        return mix_r, mix_u, "Nada apurado ainda"
    cand = [(r, m) for r, m in mix_r.items() if r != "ZZ" and m["sobre_rep"] is not None]
    gap = lambda x: x[1]["share_apurado"] - x[1]["share_eleitorado"]  # noqa: E731  (p.p.; escolhe a maior distorção)
    over = max(cand, key=gap)
    under = min(cand, key=gap)
    f = lambda m: f"{m['share_apurado']:.0f}% do apurado × {m['share_eleitorado']:.0f}% do eleitorado"  # noqa: E731
    o_ok = over[1]["sobre_rep"] >= hi and over[1]["share_apurado"] - over[1]["share_eleitorado"] >= min_gap_pp
    u_ok = under[1]["sobre_rep"] <= lo and under[1]["share_eleitorado"] - under[1]["share_apurado"] >= min_gap_pp
    if o_ok and u_ok:
        cap = (f"Apurado até agora está super-representado no {REGIAO_NOME[over[0]]} ({f(over[1])}) "
               f"e sub-representado no {REGIAO_NOME[under[0]]} ({f(under[1])})")
    elif o_ok:
        cap = f"Apurado até agora está super-representado no {REGIAO_NOME[over[0]]} ({f(over[1])})"
    elif u_ok:
        cap = f"Apurado até agora está sub-representado no {REGIAO_NOME[under[0]]} ({f(under[1])})"
    else:
        cap = "Apurado equilibrado entre as regiões"
    return mix_r, mix_u, cap


def zone_swings(base: Base, prep: Prepared, proj: dict):
    """Z = {dl, do, dbn, dabst, pct, src}: deltas (por 100 aptos) do swing encolhido usado na projeção."""
    dz = 100.0 * proj["d_z"]
    pct = 100.0 * _div(prep.K2[:, I2["A"]], prep.te)
    pbu = np.round(100.0 * _div(prep.bu_A2, prep.te), 2).tolist()
    exact = proj["Nex"] > 0
    obs_any = proj["Nz"] > 0
    src = np.where(exact, "obs", np.where(obs_any, "est", "shrunk"))
    r = np.round(dz, 3).tolist()
    pr = np.round(pct, 2).tolist()
    # swing observado sem encolhimento (por 100 aptos), só onde há dado apurado
    Nz = proj["Nz"]
    rz = np.round(100.0 * _div(proj["Dz"], Nz[:, None]), 3).tolist()
    zones = {k: {"dl": r[i][0], "do": r[i][1], "dbn": r[i][2], "dabst": r[i][3], "pct": pr[i], "src": str(src[i]),
                 "pct_bu": pbu[i],
                 "raw": ({"dl": rz[i][0], "do": rz[i][1], "dbn": rz[i][2], "dabst": rz[i][3]} if Nz[i] > 0 else None)}
             for i, k in enumerate(base.zone_keys)}
    dm = np.round(100.0 * proj["d_m"], 3).tolist()
    zm = base.zone_mun
    pm = np.round(100.0 * _div(_bc(zm, prep.K2[:, I2["A"]], base.M), _bc(zm, prep.te, base.M)), 2).tolist()
    exm = _bc(zm, proj["Nex"], base.M) > 0
    pmb = np.round(100.0 * _div(_bc(zm, prep.bu_A2, base.M), _bc(zm, prep.te, base.M)), 2).tolist()
    srcm = np.where(exm, "obs", np.where(proj["Nm"] > 0, "est", "shrunk"))
    Nm = proj["Nm"]
    rm = np.round(100.0 * _div(proj["Dm"], Nm[:, None]), 3).tolist()
    mun = {str(int(c)): {"dl": dm[i][0], "do": dm[i][1], "dbn": dm[i][2], "dabst": dm[i][3], "pct": pm[i],
                         "src": str(srcm[i]), "pct_bu": pmb[i],
                         "raw": ({"dl": rm[i][0], "do": rm[i][1], "dbn": rm[i][2], "dabst": rm[i][3]}
                                 if Nm[i] > 0 else None)}
           for i, c in enumerate(base.mun_codes)}
    return zones, mun

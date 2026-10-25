"""Gera o 2T sintético por seção a partir do 1T real (cenário + semente).

Cenários
--------
swing2022 (default do ensaio)
  1T 2026 da seção + o swing 1T->2T de 2022 do MUNICÍPIO da seção (códigos TSE estáveis),
  em deltas por apto para Lula, adversário, brancos+nulos e abstenção:
      p2_k = max(p1_k + δ_k(mun), 0),  k ∈ {L, O, BN, Abs}, renormalizado para somar 1
  (os votos "demais" X1 somem por construção). Município sem 2022 -> swing da UF -> Brasil.
  Depois um deslocamento uniforme por apto h (Flávio -> Lula) faz a margem nacional bater
  `target_margin_pp` (default +1,5 Lula). Ruído por seção: comparecimento/BN binomiais e
  ruído logit N(0, 0,08) na divisão L×O.

virada / flavio / empate (paramétricos)
  comparecimento  c2 = clip(c1 + dT[r] + η_uf + η_mun + ε, 0, .995);  C2 ~ Bin(A2, c2)
  brancos/nulos   b2 = b1·kb[r]·e^ε,  n2 = n1·kn[r]·e^ε;            B2, N2 ~ Bin
  transferência   L* = (L1 + a[r]·X1)·A2/A1,  O* = (O1 + (1−a[r])·X1)·A2/A1
                  ΔV = V2 − (L*+O*) dividido m[r] : 1−m[r]   (mobilização/desmobilização)
  persuasão       q = σ(logit(L*/(L*+O*)) + s[r] + u_uf + u_mun + ε_s + g);  L2 = round(V2·q)
  g (deslocamento global, logit) calibrado por bisseção para bater a margem-alvo.

Horários (segundos desde 25/10/2026 00:00 BRT)
  t_tot  = DT_PRIM_TOT_PARCIAL_HOR_TSE do 1T (mesma hora do dia)  -> entra nos agregados
  t_recv = DT_RECEBIMENTO_BU_HOR_TSE do 1T                         -> dr/hr do aux
  t_pub  = max(t_recv + lag_UF, t_tot + 15 s)                       -> aux/BU publicados
  lag ~ log-uniforme por UF/região (default: NE 2–20 min, SP 35–240 min, demais 10–60).
"""

from __future__ import annotations

import zlib
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from . import prepare
from .bu import section_hash

UFS = ["ac", "al", "am", "ap", "ba", "ce", "df", "es", "go", "ma", "mg", "ms", "mt", "pa",
       "pb", "pe", "pi", "pr", "rj", "rn", "ro", "rr", "rs", "sc", "se", "sp", "to", "zz"]
UF_REGIAO = {
    "ac": "N", "am": "N", "ap": "N", "pa": "N", "ro": "N", "rr": "N", "to": "N",
    "al": "NE", "ba": "NE", "ce": "NE", "ma": "NE", "pb": "NE", "pe": "NE",
    "pi": "NE", "rn": "NE", "se": "NE",
    "df": "CO", "go": "CO", "ms": "CO", "mt": "CO",
    "es": "SE", "mg": "SE", "rj": "SE", "sp": "SE",
    "pr": "S", "rs": "S", "sc": "S", "zz": "ZZ",
}
REGIOES = ["N", "NE", "CO", "SE", "S", "ZZ"]
UF_IDX = {u: i for i, u in enumerate(UFS)}
OUT = Path(__file__).resolve().parent / "out"
DEFAULT_SCENARIO = "swing2022"

# Parâmetros por região: N, NE, CO, SE, S, ZZ (ver docstring)
PARAMETRIC = {
    # Lula termina +1,5 p.p.; NE/N mobilizam, S/SE chegam cedo e puxam Flávio.
    "virada": dict(target=1.5,
                   dT=dict(N=1.2, NE=2.2, CO=-0.4, SE=0.2, S=-1.0, ZZ=-3.0),
                   a=dict(N=0.42, NE=0.55, CO=0.32, SE=0.42, S=0.32, ZZ=0.50),
                   m=dict(N=0.62, NE=0.74, CO=0.42, SE=0.52, S=0.40, ZZ=0.55),
                   s=dict(N=0.03, NE=0.05, CO=-0.01, SE=0.02, S=0.00, ZZ=0.02),
                   kb=dict(N=0.85, NE=0.80, CO=0.95, SE=0.92, S=0.95, ZZ=1.0),
                   kn=dict(N=0.95, NE=0.90, CO=1.10, SE=1.05, S=1.10, ZZ=1.1)),
    # Flávio vence por 3 p.p.; centro-direita do 1T vai para ele, NE desmobiliza.
    "flavio": dict(target=-3.0,
                   dT=dict(N=-0.6, NE=-1.6, CO=0.8, SE=0.6, S=1.4, ZZ=0.5),
                   a=dict(N=0.30, NE=0.42, CO=0.22, SE=0.30, S=0.22, ZZ=0.40),
                   m=dict(N=0.45, NE=0.58, CO=0.32, SE=0.38, S=0.30, ZZ=0.40),
                   s=dict(N=-0.02, NE=-0.01, CO=-0.04, SE=-0.03, S=-0.04, ZZ=-0.02),
                   kb=dict(N=1.0, NE=1.05, CO=1.0, SE=1.0, S=0.95, ZZ=1.0),
                   kn=dict(N=1.15, NE=1.20, CO=1.05, SE=1.10, S=1.05, ZZ=1.1)),
    # Empate técnico: Lula por 0,05 p.p. (~60 mil votos), virada só no fim da noite.
    "empate": dict(target=0.05,
                   dT=dict(N=0.4, NE=0.6, CO=0.2, SE=0.3, S=0.2, ZZ=-1.0),
                   a=dict(N=0.36, NE=0.48, CO=0.27, SE=0.36, S=0.27, ZZ=0.45),
                   m=dict(N=0.54, NE=0.66, CO=0.37, SE=0.45, S=0.35, ZZ=0.48),
                   s=dict(N=0.0, NE=0.02, CO=-0.02, SE=0.0, S=-0.02, ZZ=0.0),
                   kb=dict(N=0.95, NE=0.92, CO=1.0, SE=0.97, S=1.0, ZZ=1.0),
                   kn=dict(N=1.05, NE=1.02, CO=1.08, SE=1.06, S=1.08, ZZ=1.1)),
}
SCENARIOS = {"swing2022": dict(target=1.5), **PARAMETRIC}

DEFAULT_LAG = "ne=2-20,sp=35-240,default=10-60"
ORDERS = ("real", "nordeste-first", "random")
ORDER_PRIO = {"NE": 0, "N": 1, "CO": 2, "SE": 3, "S": 4}
NEVER = np.int64(10**9)


def parse_lag(spec: str) -> dict[str, tuple[float, float]]:
    """'ne=2-20,sp=35-240,default=10-60' -> {uf: (lo_min, hi_min)} (UF > região > default)."""
    rules: dict[str, tuple[float, float]] = {}
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        k, v = part.split("=")
        lo, hi = (float(x) for x in v.split("-"))
        if lo <= 0 or hi < lo:
            raise ValueError(f"lag inválido: {part}")
        rules[k.strip().lower()] = (lo, hi)
    if "default" not in rules:
        rules["default"] = (10.0, 60.0)
    out = {}
    for uf in UFS:
        reg = UF_REGIAO[uf].lower()
        out[uf] = rules.get(uf) or rules.get(reg) or rules["default"]
    return out


@dataclass
class Truth:
    scenario: str
    seed: int
    target_margin_pp: float
    n: int
    uf: np.ndarray            # str (alinhado por idx)
    uf_i: np.ndarray
    reg_i: np.ndarray
    cd_mun: np.ndarray
    zona: np.ndarray
    secao: np.ndarray
    local: np.ndarray
    origem: np.ndarray
    installed: np.ndarray
    aptos: np.ndarray
    aptos_tte: np.ndarray
    comp: np.ndarray
    l: np.ndarray
    o: np.ndarray
    b: np.ndarray
    nulos: np.ndarray
    t_tot: np.ndarray         # s desde 25/10 00:00 BRT
    t_recv: np.ndarray
    t_pub: np.ndarray         # NEVER = nunca publicado
    hashes: list = field(default_factory=list)
    base1: dict = field(default_factory=dict)
    calib: float = 0.0
    lag_spec: str = DEFAULT_LAG
    order: str = "real"
    donors: dict = field(default_factory=dict)

    def national(self) -> dict:
        L, O = int(self.l.sum()), int(self.o.sum())
        V = L + O
        A, C = int(self.aptos.sum()), int(self.comp.sum())
        return {"aptos": A, "comp": C, "abst": A - C, "l": L, "o": O,
                "b": int(self.b.sum()), "n": int(self.nulos.sum()),
                "pl": 100 * L / V, "po": 100 * O / V, "margin_pp": 100 * (L - O) / V,
                "margin_votes": L - O, "pc": 100 * C / A}

    def by_region(self) -> list[dict]:
        rows = []
        for r, name in enumerate(REGIOES):
            mk = self.reg_i == r
            L, O = int(self.l[mk].sum()), int(self.o[mk].sum())
            A = int(self.aptos[mk].sum())
            b1 = self.base1
            dm = (L - O) - int(b1["l1"][mk].sum() - b1["o1"][mk].sum())
            rows.append({"regiao": name, "pl": 100 * L / max(L + O, 1),
                         "dmargem_por_100_aptos": 100 * dm / max(A, 1),
                         "dabst_pp": 100 * (int((self.aptos - self.comp)[mk].sum()) / A
                                            - int((b1["aptos1"] - b1["comp1"])[mk].sum()) / int(b1["aptos1"][mk].sum()))})
        return rows


def remap_order(t_tot1: np.ndarray, t_recv1: np.ndarray, uf_i: np.ndarray, order: str, seed: int):
    """Reordena a chegada ENTRE UFs mantendo a ordem DENTRO de cada UF.

    Cada UF recebe a curva de chegada (quantis de t_tot) de uma UF "doadora":
      real            -> a própria (horários reais do 1T 2026)
      nordeste-first  -> as curvas mais rápidas do 1T vão para NE, depois N, CO, SE, S
      random          -> permutação aleatória (semente) das curvas entre as 27 UFs
    A seção mantém seu quantil dentro da UF; zz (exterior) fica com a própria curva.
    O recebimento acompanha: t_recv = t_tot_novo − (t_tot1 − t_recv1), nunca antes de 17:05
    (exceto zz). Retorna (t_tot, t_recv, {uf_destino: uf_doadora}).
    """
    if order not in ORDERS:
        raise ValueError(f"ordem desconhecida: {order} (use {ORDERS})")
    if order == "real":
        return t_tot1.copy(), np.minimum(t_recv1, t_tot1), {u: u for u in UFS}
    zz = UF_IDX["zz"]
    ufs = [u for u in range(len(UFS)) if u != zz]
    med = {u: float(np.median(t_tot1[uf_i == u])) for u in ufs}
    by_speed = sorted(ufs, key=lambda u: med[u])          # curvas reais, da mais rápida à mais lenta
    if order == "nordeste-first":
        desired = sorted(ufs, key=lambda u: (ORDER_PRIO[UF_REGIAO[UFS[u]]], med[u]))
    else:
        desired = ufs[:]
        np.random.default_rng([seed, 99]).shuffle(desired)
    donor = {desired[k]: by_speed[k] for k in range(len(ufs))}
    donor[zz] = zz
    t_new = t_tot1.copy()
    for u in range(len(UFS)):
        d = donor[u]
        if d == u:
            continue
        sel = np.nonzero(uf_i == u)[0]
        sel = sel[np.argsort(t_tot1[sel], kind="stable")]
        src = np.sort(t_tot1[uf_i == d])
        q = np.linspace(0.0, 1.0, len(sel))
        t_new[sel] = np.rint(np.interp(q, np.linspace(0.0, 1.0, len(src)), src)).astype(np.int64)
    delta = np.maximum(t_tot1 - t_recv1, 0)
    t_recv = t_new - delta
    floor = np.where(uf_i == zz, 0, 17 * 3600 + 5 * 60)
    t_recv = np.minimum(np.maximum(t_recv, floor), t_new)
    return t_new, t_recv, {UFS[k]: UFS[v] for k, v in donor.items()}


def truth_path(scenario: str, order: str = "real") -> Path:
    return OUT / (f"{scenario}_truth.parquet" if order == "real" else f"{scenario}-{order}_truth.parquet")


def _logit(p):
    return np.log(p) - np.log1p(-p)


def _expit(z):
    return 1.0 / (1.0 + np.exp(-z))


def _load(base_path: Path | None):
    base_path = base_path or prepare.build()
    t = pq.read_table(base_path)
    col = lambda c: t.column(c).to_numpy(zero_copy_only=False)  # noqa: E731
    d = {"uf": np.asarray(t.column("uf").to_pylist())}
    d["n"] = len(d["uf"])
    d["uf_i"] = np.array([UF_IDX[u] for u in d["uf"]], dtype=np.int8)
    reg_of_uf = np.array([REGIOES.index(UF_REGIAO[u]) for u in UFS], dtype=np.int8)
    d["reg_i"] = reg_of_uf[d["uf_i"]]
    for c in ("cd_mun", "zona", "secao", "local2"):
        d[c] = col(c).astype(np.int32)
    for c in ("aptos1", "comp1", "l1", "o1", "x1", "b1", "n1"):
        d[c] = col(c).astype(np.float64)
    d["A2"] = col("aptos2").astype(np.int64)
    d["origem"] = col("origem").astype(np.int8)
    d["installed"] = col("instalada1").astype(bool) & (d["A2"] > 0)
    d["eleitor"] = col("eleitor_secao").astype(np.int64)
    d["t_tot1"] = col("t_tot1").astype(np.int64)
    d["t_recv1"] = col("t_recv1").astype(np.int64)
    mun_key = d["uf_i"].astype(np.int64) * 100000 + d["cd_mun"]
    _, d["mun_i"] = np.unique(mun_key, return_inverse=True)
    d["n_mun"] = int(d["mun_i"].max()) + 1
    return d


def _calibrate(V2: np.ndarray, qfun, target_pp: float, lo: float, hi: float):
    """Bisseção em h para que sum(round(V2·q(h))) bata a margem-alvo (exata até 1 voto)."""
    Vf = V2.astype(np.float64)
    L_target = Vf.sum() * (0.5 + target_pp / 200.0)
    for _ in range(90):
        h = (lo + hi) / 2
        if np.rint(Vf * qfun(h)).sum() < L_target:
            lo = h
        else:
            hi = h
    h = (lo + hi) / 2
    L2 = np.clip(np.rint(Vf * qfun(h)).astype(np.int64), 0, V2)
    return L2, float(h)


def _gen_parametric(d, P, target, rng):
    n, uf_i, mun_i, reg_i = d["n"], d["uf_i"], d["mun_i"], d["reg_i"]
    A1, C1, L1, O1, X1, B1, N1 = (d[k] for k in ("aptos1", "comp1", "l1", "o1", "x1", "b1", "n1"))
    A2, installed = d["A2"], d["installed"]
    vec = lambda dd: np.array([dd[r] for r in REGIOES])[reg_i]  # noqa: E731

    c1 = np.where(A1 > 0, C1 / np.maximum(A1, 1), 0.0)
    c2 = (c1 + vec(P["dT"]) / 100.0 + rng.normal(0, 0.006, len(UFS))[uf_i]
          + rng.normal(0, 0.012, d["n_mun"])[mun_i] + rng.normal(0, 0.015, n))
    c2 = np.clip(c2, 0.0, 0.995)
    c2 = np.where(c1 <= 0, np.clip(c2, 0, 0.05), c2)
    C2 = np.where(installed, rng.binomial(A2, c2), 0).astype(np.int64)

    br_mean, nu_mean = B1.sum() / C1.sum(), N1.sum() / C1.sum()
    w = C1 / (C1 + 50.0)
    b1r = w * np.where(C1 > 0, B1 / np.maximum(C1, 1), br_mean) + (1 - w) * br_mean
    n1r = w * np.where(C1 > 0, N1 / np.maximum(C1, 1), nu_mean) + (1 - w) * nu_mean
    b2r = np.clip(b1r * vec(P["kb"]) * np.exp(rng.normal(0, 0.2, n)), 0, 0.25)
    n2r = np.clip(n1r * vec(P["kn"]) * np.exp(rng.normal(0, 0.2, n)), 0, 0.35)
    B2 = rng.binomial(C2, b2r).astype(np.int64)
    N2 = rng.binomial(C2 - B2, np.clip(n2r / (1 - b2r), 0, 1)).astype(np.int64)
    V2 = C2 - B2 - N2

    r = np.where(A1 > 0, A2 / np.maximum(A1, 1), 1.0)
    a, m = vec(P["a"]), vec(P["m"])
    Ls, Os = (L1 + a * X1) * r, (O1 + (1 - a) * X1) * r
    dV = V2 - (Ls + Os)
    Lt, Ot = np.maximum(Ls + m * dV, 0.5), np.maximum(Os + (1 - m) * dV, 0.5)
    q0 = np.where(L1 + O1 + X1 > 0, Lt / (Lt + Ot), 0.5)
    z = (_logit(np.clip(q0, 0.003, 0.997)) + vec(P["s"]) + rng.normal(0, 0.04, len(UFS))[uf_i]
         + rng.normal(0, 0.06, d["n_mun"])[mun_i] + rng.normal(0, 0.10, n))
    L2, g = _calibrate(V2, lambda h: _expit(z + h), target, -3.0, 3.0)
    return C2, B2, N2, L2, V2 - L2, g


def swing2022_table():
    """Swing 2022 por município (dL, dO, dBN, dAbs por apto) com fallback por UF e Brasil."""
    t = pq.read_table(prepare.build_swing2022())
    uf = t.column("uf").to_pylist()
    mun = t.column("cd_mun").to_numpy()
    cols = {k: t.column(k).to_numpy().astype(np.float64)
            for k in ("a1", "a2", "c1", "c2", "l1", "l2", "o1", "o2", "bn1", "bn2", "b1", "b2")}
    return uf, mun, cols


def _gen_swing2022(d, target, rng):
    n, uf_i, mun_i = d["n"], d["uf_i"], d["mun_i"]
    A1, C1, L1, O1, B1, N1 = (d[k] for k in ("aptos1", "comp1", "l1", "o1", "b1", "n1"))
    A2, installed = d["A2"], d["installed"]
    suf, smun, s = swing2022_table()

    def deltas(sel):
        a1, a2 = s["a1"][sel].sum(), s["a2"][sel].sum()
        return np.array([s["l2"][sel].sum() / a2 - s["l1"][sel].sum() / a1,
                         s["o2"][sel].sum() / a2 - s["o1"][sel].sum() / a1,
                         s["bn2"][sel].sum() / a2 - s["bn1"][sel].sum() / a1,
                         (a2 - s["c2"][sel].sum()) / a2 - (a1 - s["c1"][sel].sum()) / a1])

    nat = deltas(np.ones(len(suf), bool))
    suf_a = np.asarray(suf)
    uf_delta = {u: (deltas(suf_a == u) if (suf_a == u).any() else nat) for u in UFS}
    mun_delta = {(u, int(m)): None for u, m in zip(suf, smun)}
    per_mun = np.stack([s["l2"] / s["a2"] - s["l1"] / s["a1"], s["o2"] / s["a2"] - s["o1"] / s["a1"],
                        s["bn2"] / s["a2"] - s["bn1"] / s["a1"],
                        (s["a2"] - s["c2"]) / s["a2"] - (s["a1"] - s["c1"]) / s["a1"]], axis=1)
    for i, k in enumerate(mun_delta):
        mun_delta[k] = per_mun[i]
    # delta por seção (via município da seção; fallback UF)
    uf_arr, cd = d["uf"], d["cd_mun"]
    keys, first = np.unique(mun_i, return_index=True)
    n_fallback = 0
    mun_vals = np.empty((d["n_mun"], 4))
    for mi, fi in zip(keys, first):
        k = (str(uf_arr[fi]), int(cd[fi]))
        v = mun_delta.get(k)
        if v is None:
            v = uf_delta[k[0]]
            n_fallback += 1
        mun_vals[mi] = v
    D = mun_vals[mun_i]

    # razão brancos/(brancos+nulos) 2T vs 1T em 2022 (nacional)
    bshare_f = (s["b2"].sum() / s["bn2"].sum()) / (s["b1"].sum() / s["bn1"].sum())

    Af = np.maximum(A1, 1)
    p1 = np.stack([L1 / Af, O1 / Af, (B1 + N1) / Af, (A1 - C1) / Af], axis=1)
    p2 = np.clip(p1 + D, 0, None)
    p2 = p2 / np.maximum(p2.sum(axis=1, keepdims=True), 1e-9)
    pabs = np.clip(p2[:, 3] + rng.normal(0, 0.012, n), 0.005, 1.0)
    pabs = np.where(C1 <= 0, np.maximum(pabs, 0.95), pabs)
    C2 = np.where(installed, rng.binomial(A2, 1 - pabs), 0).astype(np.int64)
    pbn = np.clip(p2[:, 2] / np.maximum(1 - p2[:, 3], 1e-6) * np.exp(rng.normal(0, 0.15, n)), 0, 0.5)
    BN2 = rng.binomial(C2, pbn).astype(np.int64)
    bshare1 = np.where(B1 + N1 > 0, B1 / np.maximum(B1 + N1, 1), B1.sum() / (B1.sum() + N1.sum()))
    w = (B1 + N1) / (B1 + N1 + 20.0)
    bshare1 = w * bshare1 + (1 - w) * (B1.sum() / (B1.sum() + N1.sum()))
    B2 = rng.binomial(BN2, np.clip(bshare1 * bshare_f, 0, 1)).astype(np.int64)
    N2 = BN2 - B2
    V2 = C2 - BN2
    pl, po = p2[:, 0], p2[:, 1]
    tot = np.maximum(pl + po, 1e-9)
    eps = rng.normal(0, 0.08, n)

    def qfun(h):
        q = np.clip((pl + h) / tot, 0.002, 0.998)
        return _expit(_logit(q) + eps)

    L2, h = _calibrate(V2, qfun, target, -0.5, 0.5)
    return C2, B2, N2, L2, V2 - L2, h, n_fallback


def generate(scenario: str = DEFAULT_SCENARIO, seed: int = 2026, target_margin: float | None = None,
             lag_spec: str = DEFAULT_LAG, base_path: Path | None = None, order: str = "real") -> Truth:
    if scenario not in SCENARIOS:
        raise ValueError(f"cenário desconhecido: {scenario} (use {list(SCENARIOS)})")
    target = SCENARIOS[scenario]["target"] if target_margin is None else float(target_margin)
    d = _load(base_path)
    rng = np.random.default_rng([seed, zlib.crc32(scenario.encode())])
    if scenario == "swing2022":
        C2, B2, N2, L2, O2, calib, _ = _gen_swing2022(d, target, rng)
    else:
        C2, B2, N2, L2, O2, calib = _gen_parametric(d, PARAMETRIC[scenario], target, rng)

    A2, n, uf_i = d["A2"], d["n"], d["uf_i"]
    tte = np.clip(A2 - d["eleitor"], 0, A2)       # aproximação: aptos acima do vinculado = TTE
    t_tot, t_recv, donors = remap_order(d["t_tot1"], d["t_recv1"], uf_i, order, seed)
    lag_rules = parse_lag(lag_spec)
    lo_m = np.array([lag_rules[u][0] for u in UFS])[uf_i]
    hi_m = np.array([lag_rules[u][1] for u in UFS])[uf_i]
    rng_lag = np.random.default_rng([seed, 7, zlib.crc32(lag_spec.encode())])
    lag = np.exp(rng_lag.uniform(np.log(lo_m), np.log(hi_m))) * 60.0
    t_pub = np.maximum(t_recv + np.rint(lag).astype(np.int64), t_tot + 15)
    t_pub = np.where(d["installed"], t_pub, NEVER)

    base1 = {k: d[k].astype(np.int64) for k in ("aptos1", "comp1", "l1", "o1", "x1", "b1", "n1")}
    return Truth(scenario=scenario, seed=seed, target_margin_pp=target, n=n, uf=d["uf"], uf_i=uf_i,
                 reg_i=d["reg_i"], cd_mun=d["cd_mun"], zona=d["zona"], secao=d["secao"],
                 local=d["local2"], origem=d["origem"], installed=d["installed"], aptos=A2,
                 aptos_tte=tte, comp=C2, l=L2, o=O2, b=B2, nulos=N2, t_tot=t_tot, t_recv=t_recv,
                 t_pub=t_pub, hashes=[section_hash(seed, scenario, i) for i in range(n)],
                 base1=base1, calib=calib, lag_spec=lag_spec, order=order, donors=donors)


def write_truth(tr: Truth, path: Path | None = None) -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    path = path or truth_path(tr.scenario, tr.order)
    pub = np.where(tr.t_pub >= NEVER, -1, tr.t_pub)
    tbl = pa.table({
        "idx": pa.array(np.arange(tr.n, dtype=np.int32)),
        "uf": pa.array(tr.uf.tolist()),
        "regiao": pa.array([REGIOES[i] for i in tr.reg_i]),
        "cd_mun": tr.cd_mun, "zona": tr.zona.astype(np.int16), "secao": tr.secao.astype(np.int16),
        "local": tr.local, "origem": tr.origem, "instalada": tr.installed,
        "aptos2": tr.aptos.astype(np.int32), "aptos_tte2": tr.aptos_tte.astype(np.int32),
        "comp2": tr.comp.astype(np.int32), "abst2": (tr.aptos - tr.comp).astype(np.int32),
        "l2": tr.l.astype(np.int32), "o2": tr.o.astype(np.int32),
        "b2": tr.b.astype(np.int32), "n2": tr.nulos.astype(np.int32),
        **{k: v.astype(np.int32) for k, v in tr.base1.items()},
        # segundos e minutos desde 25/10/2026 00:00 BRT (−1 = nunca publicado)
        "t_tot_s": tr.t_tot.astype(np.int32), "t_recv_s": tr.t_recv.astype(np.int32),
        "t_pub_s": pub.astype(np.int32),
        "t_tot_min": (tr.t_tot // 60).astype(np.int32), "t_recv_min": (tr.t_recv // 60).astype(np.int32),
        "t_pub_min": np.where(pub < 0, -1, pub // 60).astype(np.int32),
        "hash": pa.array(tr.hashes),
    })
    meta = {b"scenario": tr.scenario.encode(), b"seed": str(tr.seed).encode(),
            b"target_margin_pp": str(tr.target_margin_pp).encode(), b"calib": str(tr.calib).encode(),
            b"lag": tr.lag_spec.encode(), b"order": tr.order.encode(),
            b"donors": ",".join(f"{k}<{v}" for k, v in tr.donors.items() if k != v).encode()}
    tbl = tbl.replace_schema_metadata(meta)
    tmp = path.with_suffix(".tmp")
    pq.write_table(tbl, tmp)
    tmp.replace(path)
    return path


def timeline(tr: Truth, step_min: int = 15) -> list[dict]:
    """Margem apurada ao longo da noite (ordem t_tot)."""
    order = np.argsort(tr.t_tot, kind="stable")
    tt = tr.t_tot[order]
    cl, co = np.cumsum(tr.l[order]), np.cumsum(tr.o[order])
    rows = []
    for T in range(17 * 3600, int(tt.max()) + step_min * 60, step_min * 60):
        k = int(np.searchsorted(tt, T, side="right"))
        if k == 0:
            continue
        L, O = int(cl[k - 1]), int(co[k - 1])
        rows.append({"hora": f"{T // 3600 % 24:02d}:{T // 60 % 60:02d}", "pct_secoes": 100 * k / tr.n,
                     "pl": 100 * L / max(L + O, 1), "margin_pp": 100 * (L - O) / max(L + O, 1)})
    return rows


def crossing(tr: Truth) -> dict | None:
    """Último instante em que o líder da margem acumulada muda (após 1% das seções)."""
    order = np.argsort(tr.t_tot, kind="stable")
    d = np.cumsum(tr.l[order] - tr.o[order])
    k0 = tr.n // 100
    sgn = np.sign(d[k0:])
    ch = np.nonzero((sgn[1:] != sgn[:-1]) & (sgn[1:] != 0))[0]
    if not len(ch):
        return None
    k = k0 + int(ch[-1]) + 1
    T = int(tr.t_tot[order][k])
    return {"hora": f"{T // 3600 % 24:02d}:{T // 60 % 60:02d}:{T % 60:02d}",
            "pct_secoes": round(100 * (k + 1) / tr.n, 2), "lider_depois": "LULA" if d[k] > 0 else "FLAVIO"}


def summary_lines(tr: Truth) -> list[str]:
    nat = tr.national()
    out = [f"cenário {tr.scenario} (seed {tr.seed}, alvo {tr.target_margin_pp:+.2f} p.p., ordem {tr.order}): "
           f"LULA {nat['l']:,} ({nat['pl']:.2f}%) × FLÁVIO {nat['o']:,} ({nat['po']:.2f}%) | "
           f"margem {nat['margin_votes']:+,} ({nat['margin_pp']:+.2f} p.p.) | comparecimento {nat['pc']:.2f}% | "
           f"brancos {nat['b']:,} nulos {nat['n']:,}",
           f"  virada: {crossing(tr)}"]
    if tr.order != "real":
        meds = sorted((int(np.median(tr.t_tot[tr.uf_i == i])), u) for i, u in enumerate(UFS))
        out.append("  ordem (mediana de chegada): " + " ".join(f"{u}:{m // 3600:02d}:{m // 60 % 60:02d}"
                                                            for m, u in meds))
    for r in tr.by_region():
        out.append(f"  {r['regiao']:>2}: Lula {r['pl']:.2f}%  Δmargem/100 aptos {r['dmargem_por_100_aptos']:+.2f}  "
                   f"Δabst {r['dabst_pp']:+.2f} p.p.")
    return out


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", default="all")
    ap.add_argument("--seed", type=int, default=2026)
    ap.add_argument("--target-margin-pp", type=float, default=None)
    ap.add_argument("--order", default="real", choices=ORDERS)
    a = ap.parse_args()
    for sc in (SCENARIOS if a.scenario == "all" else [a.scenario]):
        tr = generate(sc, a.seed, a.target_margin_pp, order=a.order)
        p = write_truth(tr)
        print(f"\n== {p}")
        print("\n".join(summary_lines(tr)))
        for row in timeline(tr, 30):
            if row["pct_secoes"] < 99.99:
                print(f"  {row['hora']}  {row['pct_secoes']:6.2f}%  Lula {row['pl']:.2f}%  margem {row['margin_pp']:+.2f}")

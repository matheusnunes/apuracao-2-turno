"""Simulador da noite do 2T 2022 a partir das seções (verdade conhecida), gerando entradas `Live`
no mesmo formato do modo ao vivo: agregados de zona com as seções totalizadas até t
(ordem real `DT_PRIM_TOT_PARCIAL_HOR_TSE`) e BUs disponíveis em `recebimento + atraso` (cenário).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .base import UFS, Base, load_base
from .model import AGG, IA, Live

CACHE = Path(__file__).resolve().parent / "cache"
BT_PATH = CACHE / "bt2022_secoes.parquet"
TRUTH = ("aptos2", "comp2", "l2", "o2", "b2", "n2", "t_tot2", "t_bu2", "uf")

# cenários de disponibilidade de BU (o caminho principal é o agregado de zona; BU é enriquecimento)
SCENARIOS = {
    "zonas": "só agregados de zona (nenhum BU)",
    "producao": "zonas + BUs esparsos: recebimento + 2–30 min (SP: 35 min–4 h), coletor com fila",
    "bu_pesado": "zonas + BUs com atraso de 35 min–4 h em todas as UFs",
    "bu_imediato": "zonas + BU disponível no instante da totalização (otimista)",
}


@dataclass
class Sim:
    base: Base
    tr: dict

    @classmethod
    def load(cls, path: Path = BT_PATH) -> "Sim":
        base, ex = load_base(path, extra_cols=TRUTH)
        tr = {k: (ex[k].astype(np.float64) if k != "uf" else ex[k]) for k in TRUTH}
        return cls(base=base, tr=tr)

    def truth_final(self) -> dict:
        t = self.tr
        L, O = t["l2"].sum(), t["o2"].sum()
        V = L + O
        T1 = self.base.totals_1t
        A2 = t["aptos2"].sum()
        return {"L": L, "O": O, "V": V, "M": L - O, "M_pp": 100 * (L - O) / V,
                "delta": 100 * ((L - O) / A2 - (T1["L1"] - T1["O1"]) / T1["A1"])}

    def bu_avail(self, scenario: str, seed: int = 7) -> np.ndarray | None:
        """Instante (s desde 00:00 do dia do 2T) em que o BU de cada seção fica disponível; None = nunca."""
        t = self.tr
        rng = np.random.default_rng(seed)
        n = self.base.n
        recv = np.maximum(t["t_bu2"], 17 * 3600)
        tot = t["t_tot2"]
        sp = np.asarray(t["uf"]) == "sp"
        if scenario == "zonas":
            return None
        if scenario == "bu_imediato":
            return tot.copy()
        heavy = np.exp(rng.uniform(np.log(35 * 60), np.log(4 * 3600), n))
        moderate = np.exp(rng.uniform(np.log(2 * 60), np.log(30 * 60), n))
        if scenario == "producao":
            lag = np.where(sp, heavy, moderate)
        elif scenario == "bu_pesado":
            lag = heavy
        else:
            raise ValueError(scenario)
        av = np.maximum(recv + lag, tot)
        # fila do coletor: no máximo ~40 BUs/s; atrasa quem estoura a capacidade
        o = np.argsort(av, kind="stable")
        cap = 40.0
        seq = np.maximum.accumulate(av[o] - np.arange(n) / cap) + np.arange(n) / cap
        av2 = np.empty(n)
        av2[o] = seq
        return av2

    def live_at(self, t_s: float, bu_av: np.ndarray | None) -> Live:
        b, tr = self.base, self.tr
        m = tr["t_tot2"] <= t_s
        z = b.sec_zone[m]
        Z = b.Z
        A2, C2 = tr["aptos2"][m], tr["comp2"][m]
        zone = np.zeros((Z, len(AGG)))
        zone[:, IA["st"]] = np.bincount(z, minlength=Z)
        zone[:, IA["ts"]] = b.zone_n
        zone[:, IA["te"]] = np.bincount(b.sec_zone, weights=tr["aptos2"], minlength=Z)
        zone[:, IA["est"]] = np.bincount(z, weights=A2, minlength=Z)
        zone[:, IA["c"]] = np.bincount(z, weights=C2, minlength=Z)
        zone[:, IA["a"]] = np.bincount(z, weights=A2 - C2, minlength=Z)
        zone[:, IA["vb"]] = np.bincount(z, weights=tr["b2"][m], minlength=Z)
        zone[:, IA["tvn"]] = np.bincount(z, weights=tr["n2"][m], minlength=Z)
        zone[:, IA["l"]] = np.bincount(z, weights=tr["l2"][m], minlength=Z)
        zone[:, IA["o"]] = np.bincount(z, weights=tr["o2"][m], minlength=Z)
        br = {k: float(zone[:, IA[k]].sum()) for k in AGG}
        bu = None
        if bu_av is not None:
            hb = (bu_av <= t_s) & m
            bu = np.zeros((b.n, 7))
            bu[hb, 0] = 1
            bu[hb, 1] = tr["aptos2"][hb]
            bu[hb, 2] = tr["comp2"][hb]
            bu[hb, 3] = tr["l2"][hb]
            bu[hb, 4] = tr["o2"][hb]
            bu[hb, 5] = tr["b2"][hb]
            bu[hb, 6] = tr["n2"][hb]
        return Live(zone=zone, zone_has=np.ones(Z, dtype=bool), br=br, bu=bu, now_s=t_s,
                    meta={"counted_mask": m})

    def times_for_pcts(self, pcts) -> list[float]:
        t = np.sort(self.tr["t_tot2"])
        n = len(t)
        return [float(t[min(n - 1, max(0, int(np.ceil(p / 100 * n)) - 1))]) for p in pcts]

    def truth_counted(self, mask: np.ndarray) -> dict:
        """δ verdadeiro (por 100 aptos) e Δ abst/BN sobre um conjunto de seções."""
        b, tr = self.base, self.tr
        if not mask.any():
            return {}
        s1 = b.s1[mask]
        A1, C1, L1, O1, X1, BN1, AB1 = s1.sum(axis=0)
        A2 = tr["aptos2"][mask].sum()
        C2 = tr["comp2"][mask].sum()
        L2, O2 = tr["l2"][mask].sum(), tr["o2"][mask].sum()
        BN2 = (tr["b2"][mask] + tr["n2"][mask]).sum()
        return {"delta": 100 * ((L2 - O2) / A2 - (L1 - O1) / A1),
                "d_abst_pp": 100 * ((A2 - C2) / A2 - AB1 / A1),
                "d_bn_pp": 100 * (BN2 / A2 - BN1 / A1), "A2": A2}


__all__ = ["Sim", "SCENARIOS", "UFS"]

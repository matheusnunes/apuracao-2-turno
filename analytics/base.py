"""Base do 1º turno por seção, carregada em arrays numpy (mesmo formato para 2026 e para o backtest 2022).

Entrada: parquet com uma linha por seção principal (contrato §3 `data/base/secoes.parquet`), colunas mínimas
`idx, uf, cd_mun, zona, secao, aptos1, comp1, l1, o1, x1, b1, n1` e opcionais `regiao, aptos2_cad, t_tot1`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq

REGIOES = ["N", "NE", "CO", "SE", "S", "ZZ"]
UF_REGIAO = {
    "ac": "N", "am": "N", "ap": "N", "pa": "N", "ro": "N", "rr": "N", "to": "N",
    "al": "NE", "ba": "NE", "ce": "NE", "ma": "NE", "pb": "NE", "pe": "NE",
    "pi": "NE", "rn": "NE", "se": "NE",
    "df": "CO", "go": "CO", "ms": "CO", "mt": "CO",
    "es": "SE", "mg": "SE", "rj": "SE", "sp": "SE",
    "pr": "S", "rs": "S", "sc": "S",
    "zz": "ZZ",
}
UFS = sorted(u for u in UF_REGIAO if u != "zz") + ["zz"]

# Colunas 1T por seção (ordem fixa usada nos arrays Z×7):
C1 = ["A1", "C1", "L1", "O1", "X1", "BN1", "AB1"]


def zone_key(cd_mun: int, zona: int) -> str:
    return f"{int(cd_mun)}-{int(zona):04d}"


@dataclass
class Base:
    n: int
    # por seção
    sec_zone: np.ndarray            # int32, índice da zona (par município-zona)
    s1: np.ndarray                  # float64 N×7 (C1)
    A2: np.ndarray                  # float64, aptos do cadastro 2T (ou aptos1)
    t1: np.ndarray                  # float64, horário de totalização no 1T (s); NaN se ausente
    # tabelas
    zone_keys: list[str]
    zone_cd_mun: np.ndarray
    zone_zona: np.ndarray
    zone_mun: np.ndarray            # int32 Z → índice do município
    mun_codes: np.ndarray           # int64 M
    mun_uf: np.ndarray              # int32 M → índice da UF (em UFS)
    uf_reg: np.ndarray              # int32 U → índice da região (em REGIOES)
    mun_grp: np.ndarray             # int32 M → grupo (UF × tercil de inclinação 1T, ponderado por aptos)
    grp_uf: np.ndarray              # int32 G → índice da UF
    # pré-computados
    zone_n: np.ndarray              # seções por zona
    zone_1t: np.ndarray             # Z×7 somas 1T
    zone_A2: np.ndarray             # Z, aptos 2T (cadastro)
    perm_zt: np.ndarray             # seções ordenadas por (zona, t1)
    zone_start: np.ndarray          # Z+1 offsets em perm_zt
    order_t1: np.ndarray            # seções ordenadas por t1 (ordem de chegada do 1T)
    sec_uf: np.ndarray              # int32 N → UF
    uf_t1_sorted: list              # por UF, horários t1 ordenados (curva de progresso do 1T)
    zone_index: dict = field(default_factory=dict)
    mun_index: dict = field(default_factory=dict)
    totals_1t: dict = field(default_factory=dict)

    @property
    def Z(self) -> int:
        return len(self.zone_keys)

    @property
    def M(self) -> int:
        return len(self.mun_codes)

    @property
    def G(self) -> int:
        return len(self.grp_uf)

    def zone_uf(self) -> np.ndarray:
        return self.mun_uf[self.zone_mun]

    def zone_reg(self) -> np.ndarray:
        return self.uf_reg[self.mun_uf[self.zone_mun]]


def lean_groups(zone_1t, zone_mun, mun_uf, M, U, n_terc=3):
    """Grupo = UF × tercil da inclinação do município no 1T, (L1−O1)/A1, com tercis ponderados por aptos.

    Pai do município no encolhimento (para não aplicar o swing do interior de SP à capital, p. ex.).
    """
    mA = np.bincount(zone_mun, weights=zone_1t[:, 0], minlength=M)
    mL = np.bincount(zone_mun, weights=zone_1t[:, 2], minlength=M)
    mO = np.bincount(zone_mun, weights=zone_1t[:, 3], minlength=M)
    lean = np.where(mA > 0, (mL - mO) / np.maximum(mA, 1), 0.0)
    terc = np.zeros(M, dtype=np.int32)
    for u in range(U):
        idx = np.flatnonzero(mun_uf == u)
        if not len(idx):
            continue
        o = idx[np.argsort(lean[idx], kind="stable")]
        w = mA[o]
        cum = np.cumsum(w) - w / 2
        tot = w.sum()
        t = np.floor(n_terc * cum / tot).astype(np.int32) if tot > 0 else np.zeros(len(o), np.int32)
        terc[o] = np.clip(t, 0, n_terc - 1)
    gid = mun_uf.astype(np.int64) * n_terc + terc
    uniq, inv = np.unique(gid, return_inverse=True)
    grp_uf = (uniq // n_terc).astype(np.int32)
    return inv.astype(np.int32), grp_uf


def load_base(path: str | Path, extra_cols: tuple[str, ...] = ()) -> tuple[Base, dict]:
    """Carrega o parquet de seções. Retorna (Base, extras) onde extras traz colunas pedidas em `extra_cols`."""
    pf = pq.ParquetFile(str(path))
    names = set(pf.schema_arrow.names)
    want = ["idx", "uf", "cd_mun", "zona", "aptos1", "comp1", "l1", "o1", "x1", "b1", "n1"]
    opt = [c for c in ("aptos2_cad", "t_tot1") if c in names]
    cols = list(dict.fromkeys(want + opt + [c for c in extra_cols if c in names]))
    tbl = pq.read_table(str(path), columns=cols)
    tbl = tbl.sort_by("idx")
    col = lambda c: tbl.column(c).to_numpy(zero_copy_only=False)  # noqa: E731
    uf = np.array([str(u).lower() for u in tbl.column("uf").to_pylist()])
    cd_mun = col("cd_mun").astype(np.int64)
    zona = col("zona").astype(np.int64)
    n = len(uf)
    f = lambda c: np.nan_to_num(col(c).astype(np.float64))  # noqa: E731
    A1, C1v, L1, O1, X1 = f("aptos1"), f("comp1"), f("l1"), f("o1"), f("x1")
    BN1 = f("b1") + f("n1")
    AB1 = A1 - C1v
    s1 = np.stack([A1, C1v, L1, O1, X1, BN1, AB1], axis=1)
    if "aptos2_cad" in opt:
        a2 = col("aptos2_cad").astype(np.float64)
        A2 = np.where(np.isfinite(a2) & (a2 >= 0), a2, A1)
    else:
        A2 = A1.copy()
    t1 = col("t_tot1").astype(np.float64) if "t_tot1" in opt else np.arange(n, dtype=np.float64)
    t1 = np.where(np.isfinite(t1), t1, np.nanmax(t1) + 1 if np.isfinite(t1).any() else 0)
    # normaliza para "segundos desde 00:00 BRT do dia do 1T": a base 2026 traz epoch Unix (BRT = UTC−3)
    if np.nanmedian(t1) > 1e8:
        tl = t1 - 3 * 3600
        t1 = tl - np.floor(np.nanmin(tl) / 86400) * 86400

    # zonas = pares (município, zona); as seções já vêm ordenadas por uf, cd_mun, zona
    zkey = cd_mun * 10000 + zona
    uniq, first, sec_zone = np.unique(zkey, return_index=True, return_inverse=True)
    # ordenar zonas pela primeira aparição (mantém a ordem uf → mun → zona)
    order = np.argsort(first, kind="stable")
    rank = np.empty_like(order)
    rank[order] = np.arange(len(order))
    sec_zone = rank[sec_zone].astype(np.int32)
    zfirst = first[order]
    zone_cd_mun = cd_mun[zfirst]
    zone_zona = zona[zfirst]
    zone_ufs = uf[zfirst]
    Z = len(order)
    # municípios
    mun_u, mfirst, zone_mun = np.unique(zone_cd_mun, return_index=True, return_inverse=True)
    morder = np.argsort(mfirst, kind="stable")
    mrank = np.empty_like(morder)
    mrank[morder] = np.arange(len(morder))
    zone_mun = mrank[zone_mun].astype(np.int32)
    mun_codes = mun_u[morder]
    uf_idx = {u: i for i, u in enumerate(UFS)}
    mun_uf = np.array([uf_idx[zone_ufs[mfirst[m]]] for m in morder], dtype=np.int32)
    reg_idx = {r: i for i, r in enumerate(REGIOES)}
    uf_reg = np.array([reg_idx[UF_REGIAO[u]] for u in UFS], dtype=np.int32)

    zone_n = np.bincount(sec_zone, minlength=Z).astype(np.float64)
    zone_1t = np.stack([np.bincount(sec_zone, weights=s1[:, j], minlength=Z) for j in range(7)], axis=1)
    zone_A2 = np.bincount(sec_zone, weights=A2, minlength=Z)
    perm_zt = np.lexsort((t1, sec_zone)).astype(np.int64)
    zone_start = np.concatenate([[0], np.cumsum(zone_n).astype(np.int64)])
    order_t1 = np.argsort(t1, kind="stable").astype(np.int64)
    sec_uf = mun_uf[zone_mun[sec_zone]].astype(np.int32)
    uf_t1_sorted = [np.sort(t1[sec_uf == u]) for u in range(len(UFS))]
    zone_keys = [zone_key(m, z) for m, z in zip(zone_cd_mun, zone_zona)]
    tot = s1.sum(axis=0)
    mun_grp, grp_uf = lean_groups(zone_1t, zone_mun, mun_uf, len(mun_codes), len(UFS))
    base = Base(
        n=n, sec_zone=sec_zone, s1=s1, A2=A2, t1=t1,
        zone_keys=zone_keys, zone_cd_mun=zone_cd_mun, zone_zona=zone_zona, zone_mun=zone_mun,
        mun_codes=mun_codes, mun_uf=mun_uf, uf_reg=uf_reg, mun_grp=mun_grp, grp_uf=grp_uf,
        zone_n=zone_n, zone_1t=zone_1t, zone_A2=zone_A2, perm_zt=perm_zt, zone_start=zone_start,
        order_t1=order_t1, sec_uf=sec_uf, uf_t1_sorted=uf_t1_sorted,
        zone_index={k: i for i, k in enumerate(zone_keys)},
        mun_index={int(c): i for i, c in enumerate(mun_codes)},
        totals_1t={c: float(v) for c, v in zip(C1, tot)} | {"A2": float(A2.sum()), "secoes": n},
    )
    extras = {c: tbl.column(c).to_numpy(zero_copy_only=False) for c in extra_cols if c in names}
    return base, extras

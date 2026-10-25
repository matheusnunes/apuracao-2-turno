"""Índice canônico de seções (contracts §2) a partir do cs.json do 2T.

Principal = entrada de `sec[]` sem `nsp`. As agregadas (com `nsp`) viram a lista
`secoes_agregadas` da principal.
"""

from __future__ import annotations

import csv
import os

import orjson
import pyarrow as pa

from .common import BASE, UF_ORDER, UFS
from .fetch_cs import cs_path


def parse_cs():
    """Retorna (principais, agregadas).

    principais: lista de (uf, cd_mun, zona, secao, [agregadas declaradas em nsa])
    agregadas: lista de (uf, cd_mun, zona, secao, secao_principal)
    """
    principais, agregadas = [], []
    for uf in UFS:
        d = orjson.loads(cs_path(uf).read_bytes())
        for ab in d["abr"]:
            if ab["cd"].lower() != uf:
                raise ValueError(f"abrangência inesperada {ab['cd']} em {uf}")
            for mu in ab["mu"]:
                cd_mun = int(mu["cd"])
                for z in mu["zon"]:
                    zona = int(z["cd"])
                    for s in z["sec"]:
                        ns = int(s["ns"])
                        if s.get("nsp"):
                            agregadas.append((uf, cd_mun, zona, ns, int(s["nsp"])))
                        else:
                            nsa = s.get("nsa") or []
                            if isinstance(nsa, (str, int)):
                                nsa = [nsa]
                            principais.append((uf, cd_mun, zona, ns, sorted(int(x) for x in nsa)))
    return principais, agregadas


def build_index():
    principais, agregadas = parse_cs()
    principais.sort(key=lambda r: (UF_ORDER[r[0]], r[1], r[2], r[3]))
    keys = [(r[0], r[1], r[2], r[3]) for r in principais]
    if len(set(keys)) != len(keys):
        raise ValueError("chave de seção duplicada no cs.json")

    # agregadas via nsp (fonte autoritativa) e conferência com nsa
    agg_by_principal: dict[tuple, list[int]] = {}
    for uf, mun, zona, ns, nsp in agregadas:
        agg_by_principal.setdefault((uf, mun, zona, nsp), []).append(ns)
    nsa_mismatch = 0
    key_set = set(keys)
    orphan = [k for k in agg_by_principal if k not in key_set]
    for uf, mun, zona, ns, nsa in principais:
        a = sorted(agg_by_principal.get((uf, mun, zona, ns), []))
        if nsa and nsa != a:
            nsa_mismatch += 1

    BASE.mkdir(parents=True, exist_ok=True)
    out = BASE / "secoes_index.csv"
    tmp = out.with_suffix(".csv.tmp")
    with open(tmp, "w", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["idx", "uf", "cd_mun", "zona", "secao"])
        for i, (uf, mun, zona, ns) in enumerate(keys):
            w.writerow([i, uf, mun, zona, ns])
    os.replace(tmp, out)

    tbl = pa.table(
        {
            "idx": pa.array(range(len(keys)), pa.int32()),
            "uf": [k[0] for k in keys],
            "cd_mun": pa.array([k[1] for k in keys], pa.int32()),
            "zona": pa.array([k[2] for k in keys], pa.int16()),
            "secao": pa.array([k[3] for k in keys], pa.int16()),
            "secoes_agregadas": pa.array(
                [sorted(agg_by_principal.get(k, [])) for k in keys], pa.list_(pa.int16())
            ),
        }
    )
    stats = {
        "principais": len(keys),
        "agregadas": len(agregadas),
        "principais_com_agregadas": sum(1 for k in keys if k in agg_by_principal),
        "agregadas_orfas": len(orphan),
        "nsa_divergente": nsa_mismatch,
    }
    return tbl, stats


if __name__ == "__main__":
    t, s = build_index()
    print(s)

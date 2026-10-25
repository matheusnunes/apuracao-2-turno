"""Base do mock: 1T real por seção alinhado ao índice canônico do 2T.

Lê (via DuckDB, só por caminho; dados brutos não são confiáveis):
  - data/base/secoes_index.csv                       (índice canônico, contracts §2)
  - data/raw/detalhe_votacao_secao_2026/..._BR.csv   (aptos/comp/brancos/nulos/horários 1T)
  - data/raw/votacao_secao_2026_BR/..._BR.csv        (votos por votável, Presidente 1T)
  - data/raw/eleitorado_local_votacao_2026/..._BRASIL.csv (aptos e local do 2T)

Grava mock/cache/base.parquet (uma linha por idx). Uso:
  uv run python -m mock.prepare [--force]
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import duckdb
import numpy as np
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
INDEX_CSV = ROOT / "data" / "base" / "secoes_index.csv"
DETALHE = RAW / "detalhe_votacao_secao_2026" / "detalhe_votacao_secao_2026_BR.csv"
VOTACAO = RAW / "votacao_secao_2026_BR" / "votacao_secao_2026_BR.csv"
ELEITORADO = RAW / "eleitorado_local_votacao_2026" / "eleitorado_local_votacao_2026_BRASIL.csv"
CACHE = Path(__file__).resolve().parent / "cache"
BASE_PARQUET = CACHE / "base.parquet"
SWING22_PARQUET = CACHE / "swing2022_mun.parquet"
DETALHE22 = RAW / "2022_detalhe_votacao_secao" / "detalhe_votacao_secao_2022_BR.csv"
VOTACAO22 = RAW / "2022_votacao_secao_BR" / "votacao_secao_2022_BR.csv"

# Origem do BU no 1T (DS_ORIGEM_VOTO)
ORIGEM_UE, ORIGEM_RED, ORIGEM_SA, ORIGEM_TOT = 0, 1, 2, 3


def _csv(p: Path) -> str:
    s = str(p).replace("'", "''")
    return (
        f"read_csv('{s}', delim=';', quote='\"', encoding='latin-1', "
        "all_varchar=true, header=true)"
    )


def build(force: bool = False) -> Path:
    if BASE_PARQUET.exists() and not force:
        return BASE_PARQUET
    for p in (INDEX_CSV, DETALHE, VOTACAO, ELEITORADO):
        if not p.exists():
            raise FileNotFoundError(p)
    CACHE.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    con = duckdb.connect()
    con.execute("SET preserve_insertion_order=true")
    idx_path = str(INDEX_CSV).replace("'", "''")
    con.execute(
        f"""CREATE TABLE idx AS SELECT idx::INT idx, uf, cd_mun::INT cd_mun, zona::INT zona, secao::INT secao
            FROM read_csv('{idx_path}', header=true, all_varchar=true)"""
    )
    # Epoch do dia da eleição do 1T (04/10/2026 00:00, horário de Brasília "Hora TSE").
    con.execute(
        f"""CREATE TABLE det AS SELECT
              lower(SG_UF) uf, CD_MUNICIPIO::INT cd_mun, NR_ZONA::INT zona, NR_SECAO::INT secao,
              QT_APTOS::INT aptos1, QT_COMPARECIMENTO::INT comp1, QT_VOTOS_BRANCOS::INT b1d,
              QT_VOTOS_NULOS::INT n1d, NR_LOCAL_VOTACAO::INT local1,
              CASE WHEN DS_ORIGEM_VOTO LIKE 'Urna%' THEN {ORIGEM_UE}
                   WHEN DS_ORIGEM_VOTO LIKE 'BU gerado por RED%' THEN {ORIGEM_RED}
                   WHEN DS_ORIGEM_VOTO LIKE 'Sistema%' THEN {ORIGEM_SA}
                   ELSE {ORIGEM_TOT} END AS origem,
              (ST_SECAO_INSTALADA = 'Sim') AS instalada1,
              (epoch(strptime(DT_RECEBIMENTO_BU_HOR_TSE, '%d/%m/%Y %H:%M:%S'))
                 - epoch(TIMESTAMP '2026-10-04 00:00:00'))::INT AS t_recv1,
              (epoch(strptime(DT_PRIM_TOT_PARCIAL_HOR_TSE, '%d/%m/%Y %H:%M:%S'))
                 - epoch(TIMESTAMP '2026-10-04 00:00:00'))::INT AS t_tot1
            FROM {_csv(DETALHE)} WHERE CD_CARGO = '1'"""
    )
    con.execute(
        f"""CREATE TABLE vot AS SELECT
              lower(SG_UF) uf, CD_MUNICIPIO::INT cd_mun, NR_ZONA::INT zona, NR_SECAO::INT secao,
              sum(CASE WHEN NR_VOTAVEL='13' THEN QT_VOTOS::INT ELSE 0 END) l1,
              sum(CASE WHEN NR_VOTAVEL='22' THEN QT_VOTOS::INT ELSE 0 END) o1,
              sum(CASE WHEN NR_VOTAVEL='28' THEN QT_VOTOS::INT ELSE 0 END) n28,
              sum(CASE WHEN NR_VOTAVEL='95' THEN QT_VOTOS::INT ELSE 0 END) b95,
              sum(CASE WHEN NR_VOTAVEL='96' THEN QT_VOTOS::INT ELSE 0 END) n96,
              sum(CASE WHEN NR_VOTAVEL NOT IN ('13','22','28','95','96') THEN QT_VOTOS::INT ELSE 0 END) x1,
              sum(QT_VOTOS::INT) tot1
            FROM {_csv(VOTACAO)} WHERE CD_CARGO = '1' GROUP BY ALL"""
    )
    # Eleitorado 2T: aptos (QT_ELEITOR_ELEICAO_FEDERAL) e local das principais; soma de
    # QT_ELEITOR_SECAO da principal + agregadas (para separar aptos seção × TTE no BU).
    con.execute(
        f"""CREATE TABLE ele AS SELECT
              lower(SG_UF) uf, CD_MUNICIPIO::INT cd_mun, NR_ZONA::INT zona,
              CASE WHEN CD_TIPO_SECAO_AGREGADA='2' THEN NR_SECAO_PRINCIPAL::INT ELSE NR_SECAO::INT END AS secao,
              sum(CASE WHEN CD_TIPO_SECAO_AGREGADA='1' THEN QT_ELEITOR_ELEICAO_FEDERAL::INT ELSE 0 END) aptos2,
              max(CASE WHEN CD_TIPO_SECAO_AGREGADA='1' THEN NR_LOCAL_VOTACAO::INT END) local2,
              sum(QT_ELEITOR_SECAO::INT) eleitor_secao,
              sum(CASE WHEN CD_TIPO_SECAO_AGREGADA='1' THEN 1 ELSE 0 END) n_principal
            FROM {_csv(ELEITORADO)} WHERE NR_TURNO = '2' GROUP BY ALL"""
    )
    rel = con.sql(
        """SELECT i.idx, i.uf, i.cd_mun, i.zona, i.secao,
                  coalesce(e.local2, d.local1, 1)::INT AS local2,
                  coalesce(d.aptos1, 0)::INT aptos1, coalesce(d.comp1, 0)::INT comp1,
                  coalesce(v.b95, d.b1d, 0)::INT b1,
                  (coalesce(v.n96, d.n1d, 0) + coalesce(v.n28, 0))::INT n1,
                  coalesce(v.n28, 0)::INT n28,
                  coalesce(v.l1, 0)::INT l1, coalesce(v.o1, 0)::INT o1, coalesce(v.x1, 0)::INT x1,
                  coalesce(v.tot1, 0)::INT tot1,
                  (CASE WHEN e.n_principal = 1 THEN e.aptos2 ELSE coalesce(d.aptos1, 0) END)::INT aptos2,
                  (e.n_principal = 1) AS aptos2_cad,
                  coalesce(e.eleitor_secao, 0)::INT eleitor_secao,
                  coalesce(d.origem, 0)::TINYINT origem,
                  coalesce(d.instalada1, true) instalada1,
                  d.t_recv1, d.t_tot1, (d.uf IS NOT NULL) AS has_1t
           FROM idx i
           LEFT JOIN det d USING (uf, cd_mun, zona, secao)
           LEFT JOIN vot v USING (uf, cd_mun, zona, secao)
           LEFT JOIN ele e USING (uf, cd_mun, zona, secao)
           ORDER BY i.idx"""
    )
    tbl = rel.to_arrow_table() if hasattr(rel, "to_arrow_table") else rel.arrow()
    if hasattr(tbl, "read_all"):
        tbl = tbl.read_all()
    n = tbl.num_rows
    # Seções sem 1T (não deveria haver): horário = mediana, para não quebrar o relógio.
    has1 = tbl.column("has_1t").to_numpy(zero_copy_only=False)
    tot = tbl.column("t_tot1").to_numpy(zero_copy_only=False)
    stats = {
        "secoes": n,
        "sem_1t": int((~has1).sum()),
        "aptos1": int(np.asarray(tbl.column("aptos1")).sum()),
        "aptos2": int(np.asarray(tbl.column("aptos2")).sum()),
        "aptos2_cad": int(np.asarray(tbl.column("aptos2_cad").to_numpy(zero_copy_only=False)).sum()),
        "comp1": int(np.asarray(tbl.column("comp1")).sum()),
        "l1": int(np.asarray(tbl.column("l1")).sum()),
        "o1": int(np.asarray(tbl.column("o1")).sum()),
        "x1": int(np.asarray(tbl.column("x1")).sum()),
        "b1": int(np.asarray(tbl.column("b1")).sum()),
        "n1": int(np.asarray(tbl.column("n1")).sum()),
        "t_tot1_min": int(np.nanmin(tot.astype(float))),
        "t_tot1_max": int(np.nanmax(tot.astype(float))),
        "secs": round(time.time() - t0, 1),
    }
    tmp = BASE_PARQUET.with_suffix(".tmp")
    pq.write_table(tbl, tmp)
    tmp.replace(BASE_PARQUET)
    print("[prepare]", stats)
    return BASE_PARQUET


def build_swing2022(force: bool = False) -> Path:
    """Swing 1T->2T de 2022 por município (códigos TSE estáveis), por apto:
    dL, dO (13, 22), dBN (brancos+nulos), dAbs, além dos totais de cada turno."""
    if SWING22_PARQUET.exists() and not force:
        return SWING22_PARQUET
    for p in (DETALHE22, VOTACAO22):
        if not p.exists():
            raise FileNotFoundError(p)
    CACHE.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    con = duckdb.connect()
    con.execute(
        f"""CREATE TABLE d AS SELECT lower(SG_UF) uf, CD_MUNICIPIO::INT cd_mun, NR_TURNO::INT turno,
               sum(QT_APTOS::BIGINT) a, sum(QT_COMPARECIMENTO::BIGINT) c
            FROM {_csv(DETALHE22)} WHERE CD_CARGO = '1' GROUP BY ALL"""
    )
    con.execute(
        f"""CREATE TABLE v AS SELECT lower(SG_UF) uf, CD_MUNICIPIO::INT cd_mun, NR_TURNO::INT turno,
               sum(CASE WHEN NR_VOTAVEL='13' THEN QT_VOTOS::BIGINT ELSE 0 END) l,
               sum(CASE WHEN NR_VOTAVEL='22' THEN QT_VOTOS::BIGINT ELSE 0 END) o,
               sum(CASE WHEN NR_VOTAVEL IN ('95','96') THEN QT_VOTOS::BIGINT ELSE 0 END) bn,
               sum(CASE WHEN NR_VOTAVEL='95' THEN QT_VOTOS::BIGINT ELSE 0 END) b
            FROM {_csv(VOTACAO22)} WHERE CD_CARGO = '1' GROUP BY ALL"""
    )
    rel = con.sql(
        """WITH j AS (SELECT d.*, v.l, v.o, v.bn, v.b FROM d JOIN v USING (uf, cd_mun, turno))
           SELECT t1.uf, t1.cd_mun,
                  t1.a a1, t1.c c1, t1.l l1, t1.o o1, t1.bn bn1, t1.b b1,
                  t2.a a2, t2.c c2, t2.l l2, t2.o o2, t2.bn bn2, t2.b b2,
                  t2.l / t2.a - t1.l / t1.a AS dL,
                  t2.o / t2.a - t1.o / t1.a AS dO,
                  t2.bn / t2.a - t1.bn / t1.a AS dBN,
                  (t2.a - t2.c) / t2.a - (t1.a - t1.c) / t1.a AS dAbs
           FROM j t1 JOIN j t2 ON t1.uf = t2.uf AND t1.cd_mun = t2.cd_mun AND t1.turno = 1 AND t2.turno = 2
           WHERE t1.a > 0 AND t2.a > 0
           ORDER BY t1.uf, t1.cd_mun"""
    )
    tbl = rel.to_arrow_table() if hasattr(rel, "to_arrow_table") else rel.arrow()
    if hasattr(tbl, "read_all"):
        tbl = tbl.read_all()
    tmp = SWING22_PARQUET.with_suffix(".tmp")
    pq.write_table(tbl, tmp)
    tmp.replace(SWING22_PARQUET)
    print("[prepare] swing2022:", tbl.num_rows, "municípios,", round(time.time() - t0, 1), "s")
    return SWING22_PARQUET


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    build(force=a.force)
    build_swing2022(force=a.force)

"""Constrói a base do backtest 2022 (1T e 2T por seção) no mesmo formato de `data/base/secoes.parquet`.

Saída: analytics/cache/bt2022_secoes.parquet, uma linha por seção principal (472.075), com
- as colunas do contrato (idx, uf, regiao, cd_mun, zona, secao, aptos1, comp1, abst1, l1, o1, x1, b1, n1,
  aptos2_cad, t_tot1, t_bu1), onde L = 13 (Lula) e O = 22 (Bolsonaro);
- a "verdade" do 2T 2022 para a simulação: aptos2, comp2, l2, o2, b2, n2, t_tot2, t_bu2.

Horários: segundos desde 00:00 (horário de Brasília) do dia da eleição do respectivo turno
(02/10/2022 no 1T, 30/10/2022 no 2T). Os arquivos brutos são lidos pelo caminho (não confiáveis).

Uso: uv run python -m analytics.bt2022_build
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
CACHE = Path(__file__).resolve().parent / "cache"
OUT = CACHE / "bt2022_secoes.parquet"

V = RAW / "2022_votacao_secao_BR" / "votacao_secao_2022_BR.csv"
D = RAW / "2022_detalhe_votacao_secao" / "detalhe_votacao_secao_2022_BR.csv"

UF_REGIAO = {
    "ac": "N", "am": "N", "ap": "N", "pa": "N", "ro": "N", "rr": "N", "to": "N",
    "al": "NE", "ba": "NE", "ce": "NE", "ma": "NE", "pb": "NE", "pe": "NE",
    "pi": "NE", "rn": "NE", "se": "NE",
    "df": "CO", "go": "CO", "ms": "CO", "mt": "CO",
    "es": "SE", "mg": "SE", "rj": "SE", "sp": "SE",
    "pr": "S", "rs": "S", "sc": "S",
    "zz": "ZZ",
}


def build() -> Path:
    CACHE.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute("SET threads TO 8")
    rd = "delim=';', quote='\"', header=true, encoding='latin-1', all_varchar=true"
    reg_case = " ".join(f"WHEN '{u}' THEN '{r}'" for u, r in UF_REGIAO.items())
    t0 = time.time()
    con.execute(f"""
      CREATE TEMP TABLE det AS
      SELECT NR_TURNO::INT turno, lower(SG_UF) uf, CD_MUNICIPIO::INT cd_mun, NR_ZONA::INT zona, NR_SECAO::INT secao,
             QT_APTOS::INT aptos, QT_COMPARECIMENTO::INT comp, QT_ABSTENCOES::INT abst,
             QT_VOTOS_BRANCOS::INT b, QT_VOTOS_NULOS::INT n,
             strptime(DT_PRIM_TOT_PARCIAL_HOR_TSE, '%d/%m/%Y %H:%M:%S') t_tot,
             strptime(DT_RECEBIMENTO_BU_HOR_TSE, '%d/%m/%Y %H:%M:%S') t_bu
      FROM read_csv('{D}', {rd}) WHERE CD_CARGO='1'
    """)
    con.execute(f"""
      CREATE TEMP TABLE vot AS
      SELECT NR_TURNO::INT turno, lower(SG_UF) uf, CD_MUNICIPIO::INT cd_mun, NR_ZONA::INT zona, NR_SECAO::INT secao,
             sum(QT_VOTOS::INT) FILTER (WHERE NR_VOTAVEL='13') l,
             sum(QT_VOTOS::INT) FILTER (WHERE NR_VOTAVEL='22') o,
             sum(QT_VOTOS::INT) FILTER (WHERE NR_VOTAVEL NOT IN ('13','22','95','96')) x,
             sum(QT_VOTOS::INT) FILTER (WHERE NR_VOTAVEL='95') b,
             sum(QT_VOTOS::INT) FILTER (WHERE NR_VOTAVEL='96') n
      FROM read_csv('{V}', {rd}) WHERE CD_CARGO='1' GROUP BY ALL
    """)
    print(f"lido em {time.time() - t0:.1f}s")
    tmp = OUT.with_suffix(".parquet.tmp")
    con.execute(f"""
      COPY (
        WITH r AS (
          SELECT d.*, coalesce(v.l,0) l, coalesce(v.o,0) o, coalesce(v.x,0) x,
                 coalesce(v.b,0) vb, coalesce(v.n,0) vn
          FROM det d LEFT JOIN vot v USING (turno, uf, cd_mun, zona, secao)
        ),
        t1 AS (SELECT * FROM r WHERE turno=1), t2 AS (SELECT * FROM r WHERE turno=2)
        SELECT (row_number() OVER (ORDER BY (t1.uf='zz'), t1.uf, t1.cd_mun, t1.zona, t1.secao) - 1)::INT idx,
               t1.uf, CASE t1.uf {reg_case} END regiao, t1.cd_mun, t1.zona::SMALLINT zona, t1.secao::SMALLINT secao,
               t1.aptos aptos1, t1.comp comp1, t1.abst abst1, t1.l l1, t1.o o1, t1.x x1, t1.b b1, t1.n n1,
               t2.aptos aptos2_cad,
               epoch(t1.t_tot - TIMESTAMP '2022-10-02 00:00:00')::INT t_tot1,
               epoch(t1.t_bu  - TIMESTAMP '2022-10-02 00:00:00')::INT t_bu1,
               t2.aptos aptos2, t2.comp comp2, t2.l l2, t2.o o2, t2.b b2, t2.n n2, t2.x x2,
               epoch(t2.t_tot - TIMESTAMP '2022-10-30 00:00:00')::INT t_tot2,
               epoch(t2.t_bu  - TIMESTAMP '2022-10-30 00:00:00')::INT t_bu2,
               (t1.vb <> t1.b OR t1.vn <> t1.n OR t2.vb <> t2.b OR t2.vn <> t2.n) bn_mismatch
        FROM t1 JOIN t2 USING (uf, cd_mun, zona, secao)
        ORDER BY idx
      ) TO '{tmp}' (FORMAT parquet, COMPRESSION zstd)
    """)
    os.replace(tmp, OUT)
    chk = con.execute(f"""
      SELECT count(*), sum(aptos1), sum(l1), sum(o1), sum(x1), sum(b1), sum(n1), sum(comp1),
             sum(aptos2), sum(l2), sum(o2), sum(x2), sum(b2), sum(n2), sum(comp2), sum(bn_mismatch::INT),
             sum((l1+o1+x1+b1+n1 <> comp1)::INT), sum((l2+o2+b2+n2 <> comp2)::INT)
      FROM '{OUT}'
    """).fetchone()
    print("secoes, aptos1, l1, o1, x1, b1, n1, comp1 =", chk[:8])
    print("aptos2, l2, o2, x2, b2, n2, comp2 =", chk[8:15])
    print("bn_mismatch, soma1!=comp1, soma2!=comp2 =", chk[15:])
    print(f"ok em {time.time() - t0:.1f}s -> {OUT}")
    return OUT


if __name__ == "__main__":
    build()

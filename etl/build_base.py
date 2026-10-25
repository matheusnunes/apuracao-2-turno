"""Constrói data/base/ (contracts §2 e §3).

    uv run python -m etl.build_base                 # usa os brutos locais (idempotente)
    uv run python -m etl.build_base --refresh-locais  # rebaixa eleitorado_local_votacao_2026 e reporta mudanças
    uv run python -m etl.build_base --refresh-cs      # rebaixa os cs.json do 2T (condicional por ETag)
    uv run python -m etl.build_base --refresh-geo     # rebaixa as malhas do IBGE

Saídas: secoes_index.csv, secoes.parquet, locais.parquet, municipios.parquet, zonas.parquet,
municipios_geo.{geojson,topojson}, municipios_geo_hd.geojson, ufs_geo.geojson, build_report.json
(e locais_diff_2t.csv quando --refresh-locais).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time

import warnings  # noqa: F401

import duckdb
import h3
import numpy as np
import orjson
import pyarrow as pa
import pyarrow.parquet as pq

from . import geo, index, refresh_locais
from .common import BASE, BR_BBOX, OFFICIAL_1T, RAW, UF_ORDER, UF_REGIAO
from .fetch_cs import cm_path, cs_path, fetch_all

DET_CSV = RAW / "detalhe_votacao_secao_2026" / "detalhe_votacao_secao_2026_BR.csv"
VOT_CSV = RAW / "votacao_secao_2026_BR" / "votacao_secao_2026_BR.csv"
MUNZONA_CSV = RAW / "detalhe_votacao_munzona_2026" / "detalhe_votacao_munzona_2026_BR.csv"

# Coordenada de local considerada lixo se cair a mais de FAR_DEG graus fora do bbox do município.
FAR_DEG = 0.25


warnings.filterwarnings("ignore", message="Arrow output on a DB-API result")


def log(msg):
    print(msg, flush=True)


def read_csv_sql(path) -> str:
    p = str(path).replace("'", "''")
    return f"read_csv('{p}', delim=';', quote='\"', encoding='latin-1', all_varchar=true, header=true)"


def atomic_parquet(tbl: pa.Table, name: str):
    out = BASE / name
    tmp = out.with_suffix(".parquet.tmp")
    pq.write_table(tbl, tmp, compression="zstd")
    os.replace(tmp, out)
    return out


# ------------------------------------------------------------------ fontes

def load_sources(con, locais_csv):
    t = time.time()
    con.execute(f"""
        create table det as select
          lower(SG_UF) uf, CD_MUNICIPIO::int cd_mun, NR_ZONA::int zona, NR_SECAO::int secao,
          QT_APTOS::int aptos1, QT_COMPARECIMENTO::int comp1, QT_ABSTENCOES::int abst1,
          QT_VOTOS_NOMINAIS::int nom_det, QT_VOTOS_BRANCOS::int b_det, QT_VOTOS_NULOS::int n_det,
          QT_VOTOS_LEGENDA::int leg_det, QT_VOTOS_ANULADOS_APU_SEP::int anul_det,
          NR_LOCAL_VOTACAO::int nr_local_res1,
          (epoch(strptime(DT_RECEBIMENTO_BU_HOR_TSE, '%d/%m/%Y %H:%M:%S'))::bigint + 10800) t_bu1,
          (epoch(strptime(DT_PRIM_TOT_PARCIAL_HOR_TSE, '%d/%m/%Y %H:%M:%S'))::bigint + 10800) t_tot1,
          (ST_SECAO_INSTALADA = 'Sim') instalada1, DS_ORIGEM_VOTO origem1
        from {read_csv_sql(DET_CSV)} where CD_CARGO = '1' and NR_TURNO = '1'""")
    log(f"  detalhe_votacao_secao: {con.execute('select count(*) from det').fetchone()[0]:,} linhas ({time.time()-t:.1f}s)")

    t = time.time()
    con.execute(f"""
        create table vot_raw as select lower(SG_UF) uf, CD_MUNICIPIO::int cd_mun, NR_ZONA::int zona,
          NR_SECAO::int secao, NR_VOTAVEL::int nr, QT_VOTOS::int q, NR_TURNO turno, CD_CARGO cargo, CD_ELEICAO ele
        from {read_csv_sql(VOT_CSV)}""")
    prof = con.execute("select turno, cargo, ele, count(*) from vot_raw group by all").fetchall()
    if any(r[0] != "1" or r[1] != "1" or r[2] != "6257" for r in prof):
        raise ValueError(f"votacao_secao inesperado: {prof}")
    con.execute("""
        create table vot as select uf, cd_mun, zona, secao,
          coalesce(sum(q) filter (where nr = 13), 0)::int l1,
          coalesce(sum(q) filter (where nr = 22), 0)::int o1,
          coalesce(sum(q) filter (where nr not in (13, 22, 28, 95, 96)), 0)::int x1,
          coalesce(sum(q) filter (where nr = 95), 0)::int b1,
          coalesce(sum(q) filter (where nr = 96), 0)::int n96,
          coalesce(sum(q) filter (where nr = 28), 0)::int t28,
          sum(q)::int tot
        from vot_raw group by all""")
    con.execute("drop table vot_raw")
    log(f"  votacao_secao: {con.execute('select count(*) from vot').fetchone()[0]:,} seções com votos ({time.time()-t:.1f}s)")

    t = time.time()
    con.execute(f"""
        create table loc as select NR_TURNO::int turno, lower(SG_UF) uf, CD_MUNICIPIO::int cd_mun,
          NR_ZONA::int zona, NR_SECAO::int secao, CD_TIPO_SECAO_AGREGADA::int tipo_secao,
          NR_SECAO_PRINCIPAL::int nsp, NR_LOCAL_VOTACAO::int nr_local, NM_LOCAL_VOTACAO nome,
          DS_TIPO_LOCAL tipo_local, DS_ENDERECO endereco, NM_BAIRRO bairro, NR_CEP cep,
          try_cast(replace(NR_LATITUDE, ',', '.') as double) lat_raw,
          try_cast(replace(NR_LONGITUDE, ',', '.') as double) lng_raw,
          QT_ELEITOR_SECAO::int qt_eleitor_secao, QT_ELEITOR_ELEICAO_FEDERAL::int qt_fed,
          DT_GERACAO || ' ' || HH_GERACAO geracao
        from {read_csv_sql(locais_csv)}""")
    log(f"  eleitorado_local_votacao: {con.execute('select count(*) from loc').fetchone()[0]:,} linhas ({time.time()-t:.1f}s)")
    return con.execute("select any_value(geracao) from loc").fetchone()[0]


def load_cm():
    d = orjson.loads(cm_path().read_bytes())
    rows = []
    for ab in d["abr"]:
        uf = ab["cd"].lower()
        for m in ab["mu"]:
            rows.append({
                "cd_mun": int(m["cd"]),
                "cd_mun_ibge": int(m["cdi"]) if m.get("cdi") else None,
                "nome": m["nm"],
                "uf": uf,
                "regiao": UF_REGIAO[uf],
                "capital": m.get("c") == "s",
                "n_zonas_cm": len(m.get("z") or []),
            })
    return rows


# ------------------------------------------------------------------ build

def build(args):
    t0 = time.time()
    report: dict = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    BASE.mkdir(parents=True, exist_ok=True)

    # 0. brutos
    need_cs = args.refresh_cs or not all(cs_path(u).exists() for u in UF_ORDER) or not cm_path().exists()
    if need_cs:
        log("[0] baixando cs.json do 2T")
        asyncio.run(fetch_all())
    geo.download(force=args.refresh_geo)

    prev = None
    if args.refresh_locais:
        prev_path = BASE / "secoes.parquet"
        if prev_path.exists():
            prev = pq.read_table(prev_path, columns=[
                "idx", "uf", "cd_mun", "zona", "secao", "nr_local", "lat", "lng", "coord_src", "aptos2_cad"])
        log("[0] rebaixando eleitorado_local_votacao_2026")
        report["refresh_locais"] = refresh_locais.download()
    locais_csv = refresh_locais.pick_csv()
    log(f"    locais: {locais_csv.relative_to(RAW.parent.parent)}")

    # 1. índice
    log("[1] índice canônico (cs.json 2T)")
    idx_tbl, idx_stats = index.build_index()
    report["index"] = idx_stats
    log(f"    {idx_stats}")

    con = duckdb.connect()
    con.execute("set preserve_insertion_order = true")
    con.register("idx_arrow", idx_tbl)
    con.execute("create table idx as select * from idx_arrow")

    # 2. fontes
    log("[2] lendo fontes")
    report["locais_geracao"] = load_sources(con, locais_csv)
    report["locais_csv"] = str(locais_csv.relative_to(RAW.parent.parent))

    # conferência índice x 1T
    q = con.execute
    only_idx = q("select count(*) from (select uf,cd_mun,zona,secao from idx except select uf,cd_mun,zona,secao from det)").fetchone()[0]
    only_det = q("select count(*) from (select uf,cd_mun,zona,secao from det except select uf,cd_mun,zona,secao from idx)").fetchone()[0]
    only_vot = q("select count(*) from (select uf,cd_mun,zona,secao from vot except select uf,cd_mun,zona,secao from idx)").fetchone()[0]
    report["check_index_vs_1t"] = {"so_no_index": only_idx, "so_no_detalhe_1t": only_det, "votos_fora_do_index": only_vot}
    log(f"    índice x detalhe 1T: só índice={only_idx}, só detalhe={only_det}; votos fora do índice={only_vot}")

    # conferência agregação cs x locais 2T
    con.execute("""create table agg_loc as select uf, cd_mun, zona, nsp secao, list(secao order by secao) ag
                   from loc where turno = 2 and tipo_secao = 2 group by all""")
    agg_diff = q("""select count(*) from idx i full join agg_loc a using (uf, cd_mun, zona, secao)
                    where coalesce(i.secoes_agregadas::int[], []) <> coalesce(a.ag, [])""").fetchone()[0]
    report["check_agregadas_cs_vs_locais2t"] = agg_diff
    log(f"    agregação cs.json x locais 2T: {agg_diff} principais divergentes")
    l2_only = q("""select count(*) from (select uf, cd_mun, zona, secao from loc where turno = 2 and tipo_secao = 1
                   except select uf, cd_mun, zona, secao from idx)""").fetchone()[0]
    idx_only = q("""select count(*) from (select uf, cd_mun, zona, secao from idx
                    except select uf, cd_mun, zona, secao from loc where turno = 2 and tipo_secao = 1)""").fetchone()[0]
    report["check_locais2t_vs_index"] = {"principais_so_no_locais2t": l2_only, "principais_so_no_index": idx_only}
    log(f"    principais locais 2T x índice: só locais={l2_only}, só índice={idx_only}")
    if l2_only or idx_only:
        log("    AVISO: conjunto de principais do locais 2T difere do cs.json; o índice segue o cs.json")

    # 3. locais 2T (só os que hospedam seções principais = onde está a urna)
    log("[3] locais e coordenadas")
    con.execute("""
        create table loc2 as select l.* from loc l join idx i using (uf, cd_mun, zona, secao)
        where l.turno = 2 and l.tipo_secao = 1""")
    con.execute("""
        create table loc1 as select l.uf, l.cd_mun, l.zona, l.secao, l.nr_local nr_local1,
          l.lat_raw lat1, l.lng_raw lng1, l.qt_fed aptos1_cad
        from loc l join idx i using (uf, cd_mun, zona, secao) where l.turno = 1 and l.tipo_secao = 1""")
    n_loc2 = q("select count(*) from loc2").fetchone()[0]
    report["secoes_sem_linha_locais2t"] = OFFICIAL_1T["secoes_principais"] - n_loc2
    log(f"    seções principais do índice com linha no locais 2T: {n_loc2:,}")

    con.execute(f"""
        create table locais_raw as select uf, cd_mun, zona, nr_local,
          any_value(nome) nome, any_value(endereco) endereco, any_value(bairro) bairro, any_value(cep) cep,
          any_value(tipo_local) tipo_local, count(distinct (lat_raw, lng_raw)) n_coords,
          any_value(lat_raw) lat_raw, any_value(lng_raw) lng_raw,
          count(*)::int n_secoes, sum(qt_fed)::int aptos2_cad
        from loc2 group by all""")
    if q("select max(n_coords) from locais_raw").fetchone()[0] > 1:
        log("    AVISO: local com mais de uma coordenada; usando any_value")
    con.execute("""
        create table locais_o as select
          (row_number() over (order by list_position(?::varchar[], uf), cd_mun, zona, nr_local) - 1)::int local_id, l.*
        from locais_raw l order by local_id""", [list(UF_ORDER)])
    locais = q("select * from locais_o order by local_id").to_arrow_table()

    # municípios (cm.json) + centroides
    cm_rows = load_cm()
    mesh = geo.Mesh()
    cm_by_mun = {(r["uf"], r["cd_mun"]): r for r in cm_rows}
    ibge_of = {(r["uf"], r["cd_mun"]): r["cd_mun_ibge"] for r in cm_rows}

    # validação das coordenadas dos locais
    lat = locais.column("lat_raw").to_numpy(zero_copy_only=False).astype(float)
    lng = locais.column("lng_raw").to_numpy(zero_copy_only=False).astype(float)
    uf_arr = locais.column("uf").to_pylist()
    mun_arr = locais.column("cd_mun").to_pylist()
    n = len(lat)
    has_raw = ~np.isnan(lat) & ~np.isnan(lng) & ~((lat == -1) & (lng == -1))
    in_bbox = has_raw & (lat >= BR_BBOX["lat_min"]) & (lat <= BR_BBOX["lat_max"]) & \
        (lng >= BR_BBOX["lng_min"]) & (lng <= BR_BBOX["lng_max"])
    pos = np.full(n, "", dtype=object)  # dentro | perto | longe (relativo ao polígono do município)
    by_mun: dict[int, list[int]] = {}
    for i in np.nonzero(in_bbox)[0]:
        by_mun.setdefault(ibge_of.get((uf_arr[i], mun_arr[i])), []).append(i)
    for ibge, ids in by_mun.items():
        ids = np.asarray(ids)
        polys = mesh.polys.get(ibge) if ibge else None
        if polys is None:
            pos[ids] = "sem_poligono"
            continue
        inside = geo.contains(polys, lng[ids], lat[ids])
        x0, y0, x1, y1 = mesh.bbox(ibge)
        near = (lng[ids] >= x0 - FAR_DEG) & (lng[ids] <= x1 + FAR_DEG) & (lat[ids] >= y0 - FAR_DEG) & (lat[ids] <= y1 + FAR_DEG)
        pos[ids] = np.where(inside, "dentro", np.where(near, "perto", "longe"))
    coord_ok = in_bbox & (pos != "longe")

    # centroides municipais
    mun_centroid = {}
    for r in cm_rows:
        if r["cd_mun_ibge"] is None:
            continue
        c = mesh.centroid(r["cd_mun_ibge"])
        if c:
            mun_centroid[(r["uf"], r["cd_mun"])] = c
    # sem polígono IBGE (ex.: município novo): mediana dos locais com coordenada
    for key in cm_by_mun:
        if key in mun_centroid or key[0] == "zz":
            continue
        ids = [i for i in range(n) if (uf_arr[i], mun_arr[i]) == key and coord_ok[i]]
        if ids:
            mun_centroid[key] = (float(np.median(lng[ids])), float(np.median(lat[ids])), "locais_mediana")

    out_lat = np.where(coord_ok, lat, np.nan)
    out_lng = np.where(coord_ok, lng, np.nan)
    coord_src = np.where(coord_ok, "local", "none").astype(object)
    coord_origem = np.where(coord_ok, "local", "none").astype(object)
    for i in np.nonzero(~coord_ok)[0]:
        c = mun_centroid.get((uf_arr[i], mun_arr[i]))
        if c:
            out_lng[i], out_lat[i] = c[0], c[1]
            coord_src[i] = "mun_centroid"
            coord_origem[i] = c[2]
    descartada = has_raw & ~coord_ok
    h3s = [h3.latlng_to_cell(float(a), float(b), 9) if not np.isnan(a) else None for a, b in zip(out_lat, out_lng)]

    locais = locais.append_column("lat", pa.array(out_lat, pa.float64(), from_pandas=True))
    locais = locais.append_column("lng", pa.array(out_lng, pa.float64(), from_pandas=True))
    locais = locais.append_column("coord_src", pa.array(coord_src.tolist(), pa.string()))
    locais = locais.append_column("coord_origem", pa.array(coord_origem.tolist(), pa.string()))
    locais = locais.append_column("coord_descartada", pa.array(descartada.tolist(), pa.bool_()))
    locais = locais.append_column("coord_pos_mun", pa.array([p or None for p in pos.tolist()], pa.string()))
    locais = locais.append_column("h3_r9", pa.array(h3s, pa.string()))
    con.register("locais_arrow", locais)
    con.execute("create table locais as select * from locais_arrow")

    pos_counts = dict(zip(*np.unique(pos[in_bbox].astype(str), return_counts=True)))
    report["locais"] = {
        "total": n,
        "com_coord_bruta": int(has_raw.sum()),
        "coord_fora_bbox_br": int((has_raw & ~in_bbox).sum()),
        "posicao_vs_poligono_municipio": {str(k): int(v) for k, v in pos_counts.items()},
        "coord_descartada_total": int(descartada.sum()),
    }
    log(f"    {report['locais']}")

    # 4. seções
    log("[4] secoes.parquet")
    con.execute("""
        create table secoes as select
          i.idx::int idx, i.uf, null::varchar regiao, i.cd_mun::int cd_mun, null::int cd_mun_ibge,
          i.zona::smallint zona, i.secao::smallint secao,
          lo.local_id::int local_id, l2.nr_local::int nr_local,
          lo.lat, lo.lng, coalesce(lo.coord_src, 'none') coord_src, coalesce(lo.coord_origem, 'none') coord_origem,
          lo.h3_r9,
          d.aptos1, d.comp1, d.abst1,
          coalesce(v.l1, 0)::int l1, coalesce(v.o1, 0)::int o1, coalesce(v.x1, 0)::int x1,
          coalesce(v.b1, 0)::int b1, (coalesce(v.n96, 0) + coalesce(v.t28, 0))::int n1,
          l2.qt_fed::int aptos2_cad,
          d.t_tot1, d.t_bu1,
          i.secoes_agregadas,
          d.instalada1, l2.tipo_local tipo_local2,
          (l1.nr_local1 is distinct from l2.nr_local) local_mudou_2t,
          l1.aptos1_cad::int aptos1_cad,
          -- conferência
          v.t28, v.n96, v.tot, d.b_det, d.n_det, d.nom_det, d.anul_det, d.leg_det
        from idx i
        left join det d using (uf, cd_mun, zona, secao)
        left join vot v using (uf, cd_mun, zona, secao)
        left join loc2 l2 using (uf, cd_mun, zona, secao)
        left join loc1 l1 using (uf, cd_mun, zona, secao)
        left join locais lo on lo.uf = i.uf and lo.cd_mun = i.cd_mun and lo.zona = i.zona and lo.nr_local = l2.nr_local
        order by i.idx""")
    # região e IBGE
    mun_tbl = pa.table({
        "uf": [r["uf"] for r in cm_rows], "cd_mun": pa.array([r["cd_mun"] for r in cm_rows], pa.int32()),
        "cd_mun_ibge": pa.array([r["cd_mun_ibge"] for r in cm_rows], pa.int32()),
        "regiao": [r["regiao"] for r in cm_rows],
    })
    con.register("cm_arrow", mun_tbl)
    con.execute("""update secoes s set cd_mun_ibge = c.cd_mun_ibge, regiao = c.regiao
                   from cm_arrow c where c.uf = s.uf and c.cd_mun = s.cd_mun""")
    sem_cm = q("select count(*) from secoes where regiao is null").fetchone()[0]
    if sem_cm:
        con.execute("update secoes set regiao = upper(uf) where regiao is null")
    report["secoes_sem_municipio_no_cm"] = sem_cm

    # checagens por seção
    chk = q("""select
        count(*) filter (where aptos1 is null) sem_detalhe,
        count(*) filter (where comp1 <> l1 + o1 + x1 + b1 + n1) comp_ne_soma,
        count(*) filter (where b1 <> b_det) b_ne_det,
        count(*) filter (where n96 <> n_det) n_ne_det,
        count(*) filter (where (l1 + o1 + x1 + t28) <> nom_det) nominais_ne_det,
        count(*) filter (where aptos1 <> abst1 + comp1) aptos_ne,
        count(*) filter (where anul_det <> 0 or leg_det <> 0) anul_ou_leg,
        count(*) filter (where aptos1_cad <> aptos1) aptos1cad_ne_aptos1,
        count(*) filter (where aptos2_cad is null) sem_aptos2,
        count(*) filter (where aptos2_cad <> aptos1) aptos2_ne_aptos1,
        count(*) filter (where local_mudou_2t) local_mudou_2t
      from secoes""").fetchone()
    names = ["sem_detalhe", "comp_ne_soma", "b_ne_det", "n96_ne_det", "nominais_ne_det", "aptos_ne_abst_mais_comp",
             "anulados_ou_legenda_nao_zero", "aptos1_cad_ne_aptos1", "sem_aptos2_cad", "aptos2_cad_ne_aptos1",
             "local_mudou_1t_2t"]
    report["checks_secao"] = dict(zip(names, chk))
    log(f"    checagens por seção: {report['checks_secao']}")

    secoes = q("""select idx, uf, regiao, cd_mun, cd_mun_ibge, zona, secao, local_id, nr_local, lat, lng,
                  coord_src, coord_origem, h3_r9, aptos1, comp1, abst1, l1, o1, x1, b1, n1, aptos2_cad,
                  t_tot1, t_bu1, secoes_agregadas, instalada1, tipo_local2, local_mudou_2t
                  from secoes order by idx""").to_arrow_table()
    assert secoes.num_rows == OFFICIAL_1T["secoes_principais"]
    assert secoes.column("idx").to_pylist() == list(range(secoes.num_rows))
    atomic_parquet(secoes, "secoes.parquet")

    # reconciliação nacional
    tot = q("""select sum(aptos1)::bigint, sum(comp1)::bigint, sum(abst1)::bigint, sum(l1)::bigint, sum(o1)::bigint,
               sum(x1)::bigint, sum(b1)::bigint, sum(n1)::bigint, sum(l1+o1+x1)::bigint, sum(t28)::bigint,
               sum(aptos2_cad)::bigint from secoes""").fetchone()
    rec = {}
    for k, val in zip(["aptos", "comp", "abst", "l", "o", "x", "b", "n", "validos"], tot[:9]):
        rec[k] = {"soma_secoes": int(val), "oficial": OFFICIAL_1T[k], "ok": int(val) == OFFICIAL_1T[k]}
    rec["nulos_tecnicos_28"] = int(tot[9])
    rec["aptos2_cad_total"] = int(tot[10]) if tot[10] is not None else None
    report["reconciliacao_nacional_1t"] = rec

    cov = q("""select coord_src, count(*), sum(aptos2_cad)::bigint from secoes group by 1 order by 1""").fetchall()
    cov_br = q("""select coord_src, count(*), sum(aptos2_cad)::bigint from secoes where uf <> 'zz' group by 1 order by 1""").fetchall()
    cov_or = q("""select coord_origem, count(*) from secoes group by 1 order by 1""").fetchall()
    report["cobertura_coord"] = {
        "todas": {r[0]: {"secoes": r[1], "aptos2": int(r[2] or 0)} for r in cov},
        "brasil": {r[0]: {"secoes": r[1], "aptos2": int(r[2] or 0)} for r in cov_br},
        "por_origem": {r[0]: r[1] for r in cov_or},
        "h3_distintos": q("select count(distinct h3_r9) from secoes").fetchone()[0],
    }

    # 5. locais.parquet
    log("[5] locais.parquet")
    loc_out = q("""select l.local_id, l.uf, l.cd_mun, l.zona::smallint zona, l.nr_local, l.nome, l.endereco, l.bairro, l.cep,
                   l.tipo_local, l.lat, l.lng, l.coord_src, l.coord_origem, l.coord_descartada, l.coord_pos_mun,
                   l.h3_r9, l.n_secoes, l.aptos2_cad,
                   s.aptos1, s.comp1, s.l1, s.o1, s.x1, s.b1, s.n1, s.n_agregadas
                   from locais l left join (
                     select local_id, sum(aptos1)::int aptos1, sum(comp1)::int comp1, sum(l1)::int l1, sum(o1)::int o1,
                            sum(x1)::int x1, sum(b1)::int b1, sum(n1)::int n1, sum(len(secoes_agregadas))::int n_agregadas
                     from secoes group by 1) s using (local_id)
                   order by local_id""").to_arrow_table()
    atomic_parquet(loc_out, "locais.parquet")

    # 6. municipios.parquet
    log("[6] municipios.parquet")
    cent = [mun_centroid.get((r["uf"], r["cd_mun"])) for r in cm_rows]
    mun_base = pa.table({
        "cd_mun": pa.array([r["cd_mun"] for r in cm_rows], pa.int32()),
        "cd_mun_ibge": pa.array([r["cd_mun_ibge"] for r in cm_rows], pa.int32()),
        "nome": [r["nome"] for r in cm_rows],
        "uf": [r["uf"] for r in cm_rows],
        "regiao": [r["regiao"] for r in cm_rows],
        "capital": pa.array([r["capital"] for r in cm_rows], pa.bool_()),
        "lat": pa.array([c[1] if c else None for c in cent], pa.float64()),
        "lng": pa.array([c[0] if c else None for c in cent], pa.float64()),
        "centroid_src": [c[2] if c else None for c in cent],
        "tem_poligono": pa.array([bool(r["cd_mun_ibge"] and r["cd_mun_ibge"] in mesh.polys) for r in cm_rows], pa.bool_()),
    })
    con.register("mun_base", mun_base)
    totals_sql = """count(*)::int n_secoes, sum(aptos1)::int aptos1, sum(comp1)::int comp1, sum(abst1)::int abst1,
                    sum(l1)::int l1, sum(o1)::int o1, sum(x1)::int x1, sum(b1)::int b1, sum(n1)::int n1,
                    sum(aptos2_cad)::int aptos2_cad"""
    mun_out = q(f"""select m.*, t.* exclude (uf, cd_mun), z.n_zonas
                    from mun_base m
                    left join (select uf, cd_mun, {totals_sql}, count(distinct local_id)::int n_locais from secoes group by all) t using (uf, cd_mun)
                    left join (select uf, cd_mun, count(distinct zona)::int n_zonas from secoes group by all) z using (uf, cd_mun)
                    order by list_position(?::varchar[], m.uf), m.cd_mun""", [list(UF_ORDER)]).to_arrow_table()
    atomic_parquet(mun_out, "municipios.parquet")
    report["municipios"] = {
        "total_cm": len(cm_rows),
        "com_secoes": q("select count(*) from (select distinct uf, cd_mun from secoes)").fetchone()[0],
        "sem_secoes_no_cm": q("select count(*) from mun_base m anti join (select distinct uf, cd_mun from secoes) s using (uf, cd_mun)").fetchone()[0],
        "secoes_sem_municipio_no_cm": sem_cm,
        "centroid_src": {k: v for k, v in q("select centroid_src, count(*) from mun_base group by 1").fetchall()},
        "sem_poligono_br": [
            {"cd_mun": r["cd_mun"], "cd_mun_ibge": r["cd_mun_ibge"], "nome": r["nome"], "uf": r["uf"]}
            for r in cm_rows if r["uf"] != "zz" and not (r["cd_mun_ibge"] in mesh.polys)
        ],
    }

    # 7. zonas.parquet + reconciliação com o detalhe por município/zona
    log("[7] zonas.parquet")
    zon_out = q(f"""select * from (
                      select uf, cd_mun, zona, {totals_sql}, count(distinct local_id)::int n_locais
                      from secoes group by uf, cd_mun, zona) t
                    order by list_position(?::varchar[], uf), cd_mun, zona""",
                [list(UF_ORDER)]).to_arrow_table()
    atomic_parquet(zon_out, "zonas.parquet")
    con.register("zon_out", zon_out)
    con.execute(f"""create table mz as select lower(SG_UF) uf, CD_MUNICIPIO::int cd_mun, NR_ZONA::int zona,
                      QT_APTOS::bigint aptos, QT_COMPARECIMENTO::bigint comp, QT_ABSTENCOES::bigint abst,
                      QT_TOTAL_VOTOS_VALIDOS::bigint validos, QT_VOTOS_BRANCOS::bigint b, QT_TOTAL_VOTOS_NULOS::bigint n,
                      QT_SECOES_PRINCIPAIS::int n_secoes
                    from {read_csv_sql(MUNZONA_CSV)} where CD_CARGO = '1' and NR_TURNO = '1'""")
    mzr = q("""select count(*) total, count(z.uf) casadas,
                 count(*) filter (where z.aptos1 is distinct from m.aptos or z.comp1 is distinct from m.comp
                                  or z.abst1 is distinct from m.abst or (z.l1 + z.o1 + z.x1) is distinct from m.validos
                                  or z.b1 is distinct from m.b or z.n1 is distinct from m.n
                                  or z.n_secoes is distinct from m.n_secoes) divergentes
               from mz m left join zon_out z using (uf, cd_mun, zona)""").fetchone()
    report["reconciliacao_munzona_1t"] = {"zonas_munzona": mzr[0], "casadas": mzr[1], "divergentes": mzr[2],
                                          "zonas_base": zon_out.num_rows}

    # 8. geometria do front
    log("[8] geometria (GeoJSON/TopoJSON)")
    props = {}
    for r in mun_out.to_pylist():
        if r["cd_mun_ibge"]:
            props[r["cd_mun_ibge"]] = {"cd_mun": r["cd_mun"], "cd_mun_ibge": r["cd_mun_ibge"],
                                       "nome": r["nome"], "uf": r["uf"]}
    report["geo"] = geo.write_front_geo(props)
    report["geo"]["poligonos_sem_municipio_tse"] = sorted(set(mesh.polys) - set(props))

    # 9. diff do refresh
    if args.refresh_locais and prev is not None:
        report["diff_locais"] = refresh_locais.diff(prev, secoes)

    report["duracao_s"] = round(time.time() - t0, 1)
    tmp = BASE / "build_report.json.tmp"
    tmp.write_bytes(orjson.dumps(report, option=orjson.OPT_INDENT_2 | orjson.OPT_NON_STR_KEYS, default=str))
    os.replace(tmp, BASE / "build_report.json")
    print_summary(report)
    ok = all(v["ok"] for k, v in rec.items() if isinstance(v, dict))
    return 0 if ok else 2


def print_summary(r):
    log("\n================ RECONCILIAÇÃO 1T (soma das seções x oficial) ================")
    for k, v in r["reconciliacao_nacional_1t"].items():
        if isinstance(v, dict):
            log(f"  {k:8s} {v['soma_secoes']:>14,}  oficial {v['oficial']:>14,}  {'OK' if v['ok'] else 'DIVERGE'}")
        else:
            log(f"  {k:8s} {v}")
    log(f"  munzona: {r['reconciliacao_munzona_1t']}")
    log(f"  checks por seção: {r['checks_secao']}")
    log("================ COBERTURA DE COORDENADAS ================")
    for k, v in r["cobertura_coord"].items():
        log(f"  {k}: {v}")
    log("================ ARQUIVOS ================")
    for p in sorted(BASE.iterdir()):
        if p.is_file() and not p.name.endswith(".tmp"):
            log(f"  {p.name:32s} {p.stat().st_size/1e6:8.2f} MB")
    log(f"  duração: {r['duracao_s']} s")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--refresh-locais", action="store_true", help="rebaixa eleitorado_local_votacao_2026 e reporta seções alteradas")
    ap.add_argument("--refresh-cs", action="store_true", help="rebaixa os cs.json do 2T (condicional por ETag)")
    ap.add_argument("--refresh-geo", action="store_true", help="rebaixa as malhas do IBGE")
    args = ap.parse_args(argv)
    sys.exit(build(args))


if __name__ == "__main__":
    main()

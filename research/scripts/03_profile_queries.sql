-- Queries de profiling usadas em research/baseline-datasets.md.
-- Pré-requisito: rodar 01_load_profile_db.sql (e 02_load_2022.sql para a parte 2022) no mesmo DB.

-- Tabela auxiliar: eleitorado_local_votacao com lat/lon numéricos (2026 usa vírgula decimal) e chaves.
CREATE OR REPLACE TABLE e AS SELECT *,
  TRY_CAST(replace(NR_LATITUDE,',','.') AS DOUBLE) lat, TRY_CAST(replace(NR_LONGITUDE,',','.') AS DOUBLE) lon,
  SG_UF||'|'||CD_MUNICIPIO||'|'||NR_ZONA||'|'||NR_LOCAL_VOTACAO lkey,
  SG_UF||'|'||CD_MUNICIPIO||'|'||NR_ZONA||'|'||NR_SECAO skey
FROM elv;

-- 1) Seções por turno / tipo (principal x agregada)
SELECT NR_TURNO, DS_TIPO_SECAO_AGREGADA, count(*) n, sum(QT_ELEITOR_ELEICAO_FEDERAL::INT) aptos_fed
FROM e GROUP BY ALL ORDER BY 1,2;

-- 2) Cobertura de coordenadas (seções principais, Brasil)
SELECT NR_TURNO, count(*) secs, count(*) FILTER (WHERE lat=-1) sem_coord,
  sum(QT_ELEITOR_ELEICAO_FEDERAL::INT) FILTER (WHERE lat=-1) aptos_sem_coord,
  count(*) FILTER (WHERE lat<>-1 AND NOT (lat BETWEEN -34 AND 5.5 AND lon BETWEEN -74.5 AND -28.5)) fora_bbox
FROM e WHERE CD_TIPO_SECAO_AGREGADA='1' AND SG_UF<>'ZZ' GROUP BY 1 ORDER BY 1;

-- 3) Reconciliação nacional (seção) x relatório oficial
SELECT NR_VOTAVEL, NM_VOTAVEL, sum(QT_VOTOS::BIGINT) votos FROM vs GROUP BY ALL ORDER BY votos DESC;
SELECT sum(QT_APTOS::BIGINT) aptos, sum(QT_COMPARECIMENTO::BIGINT) comp, sum(QT_ABSTENCOES::BIGINT) abst,
       sum(QT_VOTOS_BRANCOS::BIGINT) brancos, sum(QT_VOTOS_NULOS::BIGINT) nulos FROM dvs;

-- 4) Join resultado (detalhe_votacao_secao) -> local (eleitorado_local_votacao, 1T), chaves como INT
SELECT count(*) dvs_rows, count(x.NR_SECAO) matched,
  count(*) FILTER (WHERE x.QT_ELEITOR_ELEICAO_FEDERAL::INT = d.QT_APTOS::INT) aptos_iguais,
  count(*) FILTER (WHERE x.NR_LOCAL_VOTACAO::INT <> d.NR_LOCAL_VOTACAO::INT) local_diverge
FROM dvs d LEFT JOIN (SELECT * FROM e WHERE NR_TURNO='1') x
  ON x.SG_UF=d.SG_UF AND x.CD_MUNICIPIO::INT=d.CD_MUNICIPIO::INT AND x.NR_ZONA::INT=d.NR_ZONA::INT AND x.NR_SECAO::INT=d.NR_SECAO::INT;

-- 5) 1T x 2T na mesma seção (eleitorado_local_votacao)
SELECT count(*) secoes,
  count(*) FILTER (WHERE a.CD_TIPO_SECAO_AGREGADA<>b.CD_TIPO_SECAO_AGREGADA OR a.NR_SECAO_PRINCIPAL<>b.NR_SECAO_PRINCIPAL) agregacao_mudou,
  count(*) FILTER (WHERE a.NR_LOCAL_VOTACAO<>b.NR_LOCAL_VOTACAO) local_mudou,
  count(*) FILTER (WHERE a.NR_LATITUDE<>b.NR_LATITUDE OR a.NR_LONGITUDE<>b.NR_LONGITUDE) coord_mudou,
  count(*) FILTER (WHERE a.QT_ELEITOR_ELEICAO_FEDERAL<>b.QT_ELEITOR_ELEICAO_FEDERAL) aptos_mudou
FROM (SELECT * FROM e WHERE NR_TURNO='1') a JOIN (SELECT * FROM e WHERE NR_TURNO='2') b USING (skey);

-- 6) Replay do 1T por ordem de chegada (DT_PRIM_TOT_PARCIAL_HOR_TSE, horário de Brasília)
WITH sec AS (
  SELECT strptime(d.DT_PRIM_TOT_PARCIAL_HOR_TSE,'%d/%m/%Y %H:%M:%S') t,
    coalesce(sum(v.QT_VOTOS::INT) FILTER (WHERE v.NR_VOTAVEL='13'),0) lula,
    coalesce(sum(v.QT_VOTOS::INT) FILTER (WHERE v.NR_VOTAVEL='22'),0) flavio,
    coalesce(sum(v.QT_VOTOS::INT) FILTER (WHERE v.NR_VOTAVEL NOT IN ('95','96','28')),0) validos
  FROM dvs d LEFT JOIN vs v ON v.SG_UF=d.SG_UF AND v.CD_MUNICIPIO::INT=d.CD_MUNICIPIO::INT
    AND v.NR_ZONA::INT=d.NR_ZONA::INT AND v.NR_SECAO::INT=d.NR_SECAO::INT
  GROUP BY d.SG_UF, d.CD_MUNICIPIO, d.NR_ZONA, d.NR_SECAO, 1),
cum AS (SELECT t, count(*) OVER w secs, sum(lula) OVER w l, sum(flavio) OVER w f, sum(validos) OVER w vv
        FROM sec WINDOW w AS (ORDER BY t ROWS UNBOUNDED PRECEDING))
SELECT strftime(time_bucket(INTERVAL 15 MINUTE, t),'%d %H:%M') bucket, max(secs) secs,
  round(100*max(secs)/499248.0,1) pct_secs, round(100*arg_max(l,secs)/arg_max(vv,secs),2) lula_pct,
  round(100*arg_max(f,secs)/arg_max(vv,secs),2) flavio_pct
FROM cum GROUP BY 1 ORDER BY 1;

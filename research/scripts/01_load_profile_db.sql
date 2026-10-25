-- Carrega os CSVs brutos do TSE (latin-1, ';', aspas) em tabelas DuckDB para profiling.
-- Uso: duckdb <db_path> -c ".read research/scripts/01_load_profile_db.sql"   (executar a partir da raiz do projeto)
-- Todas as colunas como VARCHAR (all_varchar) para não perder zeros à esquerda; casts explícitos nas queries.

CREATE OR REPLACE TABLE vs AS
SELECT * FROM read_csv('data/raw/votacao_secao_2026_BR/votacao_secao_2026_BR.csv',
  delim=';', quote='"', header=true, encoding='latin-1', all_varchar=true);

CREATE OR REPLACE TABLE dvs AS
SELECT * FROM read_csv('data/raw/detalhe_votacao_secao_2026/detalhe_votacao_secao_2026_BR.csv',
  delim=';', quote='"', header=true, encoding='latin-1', all_varchar=true);

CREATE OR REPLACE TABLE elv AS
SELECT * FROM read_csv('data/raw/eleitorado_local_votacao_2026/eleitorado_local_votacao_2026_BRASIL.csv',
  delim=';', quote='"', header=true, encoding='latin-1', all_varchar=true);

CREATE OR REPLACE TABLE dmz AS
SELECT * FROM read_csv('data/raw/detalhe_votacao_munzona_2026/detalhe_votacao_munzona_2026_BR.csv',
  delim=';', quote='"', header=true, encoding='latin-1', all_varchar=true);

CREATE OR REPLACE TABLE cand AS
SELECT * FROM read_csv('data/raw/consulta_cand_2026/consulta_cand_2026_BR.csv',
  delim=';', quote='"', header=true, encoding='latin-1', all_varchar=true);

CREATE OR REPLACE TABLE bu_zz AS
SELECT * FROM read_csv('data/raw/bweb_1t_2026/bweb_1t_ZZ_051020261403.csv',
  delim=';', quote='"', header=true, encoding='latin-1', all_varchar=true);

SELECT 'vs' t, count(*) n FROM vs UNION ALL
SELECT 'dvs', count(*) FROM dvs UNION ALL
SELECT 'elv', count(*) FROM elv UNION ALL
SELECT 'dmz', count(*) FROM dmz UNION ALL
SELECT 'cand', count(*) FROM cand UNION ALL
SELECT 'bu_zz', count(*) FROM bu_zz;

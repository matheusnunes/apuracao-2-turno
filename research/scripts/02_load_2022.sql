-- Carrega arquivos 2022 (backtest) no DB de profiling. Executar a partir da raiz do projeto.
CREATE OR REPLACE TABLE vs22 AS
SELECT * FROM read_csv('data/raw/2022_votacao_secao_BR/votacao_secao_2022_BR.csv',
  delim=';', quote='"', header=true, encoding='latin-1', all_varchar=true);

CREATE OR REPLACE TABLE dvs22 AS
SELECT * FROM read_csv('data/raw/2022_detalhe_votacao_secao/detalhe_votacao_secao_2022_BR.csv',
  delim=';', quote='"', header=true, encoding='latin-1', all_varchar=true);

CREATE OR REPLACE TABLE elv22 AS
SELECT * FROM read_csv('data/raw/2022_eleitorado_local_votacao/eleitorado_local_votacao_2022.csv',
  delim=';', quote='"', header=true, encoding='latin-1', all_varchar=true);

CREATE OR REPLACE TABLE hist22 AS
SELECT '1' turno, * FROM read_csv('data/raw/2022_historico_totalizacao_presidente/Historico_Totalizacao_Presidente_BR_1T_2022.csv',
  delim=';', header=true, encoding='latin-1', all_varchar=true, union_by_name=true)
UNION ALL BY NAME
SELECT '2' turno, * FROM read_csv('data/raw/2022_historico_totalizacao_presidente/Historico_Totalizacao_Presidente_BR_2T_2022.csv',
  delim=';', header=true, encoding='latin-1', all_varchar=true);

CREATE OR REPLACE TABLE agreg22 AS
SELECT * FROM read_csv('data/raw/2022_secoes_agregadas/secoes_agregadas_2022.csv',
  delim=';', quote='"', header=true, encoding='latin-1', all_varchar=true);

CREATE OR REPLACE TABLE bu22_2t AS
SELECT * FROM read_csv('data/raw/2022_bweb_2t/bweb_2t_*.csv',
  delim=';', quote='"', header=true, encoding='latin-1', all_varchar=true, union_by_name=true, filename=true);

SELECT 'vs22' t, count(*) n FROM vs22 UNION ALL
SELECT 'dvs22', count(*) FROM dvs22 UNION ALL
SELECT 'elv22', count(*) FROM elv22 UNION ALL
SELECT 'hist22', count(*) FROM hist22 UNION ALL
SELECT 'agreg22', count(*) FROM agreg22 UNION ALL
SELECT 'bu22_2t', count(*) FROM bu22_2t;

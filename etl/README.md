# etl/ — base do 1T 2026 e arquivos base do front

Dono: agente ETL. Escreve em `data/base/`, `data/raw/cs_2t/`, `data/raw/ibge_malhas/` e
`data/raw/eleitorado_local_votacao_2026_refresh/`. Contratos: `docs/contracts.md` §2–§3.

## Rodar

```bash
uv run python -m etl.build_base                   # idempotente, ~8 s, usa os brutos locais
uv run python -m etl.build_base --refresh-locais  # domingo de manhã: rebaixa eleitorado_local_votacao_2026 e reporta mudanças
uv run python -m etl.build_base --refresh-cs      # rebaixa os 28 cs.json do 2T + mun-cm.json (condicional por ETag, ≤4 em paralelo)
uv run python -m etl.build_base --refresh-geo     # rebaixa as malhas do IBGE
uv run python -m etl.fetch_cs                     # só os cs.json (o build baixa sozinho se faltarem)
```

O build termina com código 0 só se a reconciliação nacional bater; imprime o resumo e grava
`data/base/build_report.json` (todas as checagens, a cobertura e os tamanhos).

`--refresh-locais` faz um HEAD no CDN. Se o ETag for igual ao do arquivo em uso (`"9fded1c-65e9a6d40e6cc"`, de 24/10 18:40 GMT), não baixa nada.
Se mudou:
1. baixa do zero (HTTP/1.1, sem retomar), valida com `testzip` e extrai só o `_BRASIL.csv` para `data/raw/eleitorado_local_votacao_2026_refresh/`;
2. reconstrói a base (a partir daí o build sempre prefere a cópia do refresh);
3. grava `data/base/locais_diff_2t.csv` com as seções em que mudou o `nr_local`, a coordenada/`coord_src` ou o `aptos2_cad`, com os valores antes e depois e flags `mudou_*`. O resumo vai para `build_report.json["diff_locais"]`.

O `idx` não muda com o refresh, porque vem do `cs.json`. O build avisa se as principais do locais 2T divergirem do índice.

## Pós-eleição: logs de urna, prorrogação e história

| Script | O que faz |
|---|---|
| `logs_urna.py` | Baixa o `-log.jez` de cada urna do 2T (endereço pelo hash do estado do coletor).<br>Guarda o bruto em `data/logs/raw/{uf}/{mun}/{zona}-{secao}.jez` e resume em `data/logs/logs-2t.sqlite`, tabela `log`, voto a voto nos dois turnos.<br>Retomável; a ordem é embaralhada, então qualquer parcial é uma amostra. |
| `prorrogacao.py` | Converte para a hora de Brasília: o fuso sai da abertura, às 8h de Brasília, pela moda do município; o exterior fica em hora local.<br>Gera `site/data/prorrog/secoes.bin` (colunas por `idx`) e `resumo.json`: curvas por minuto 1T × 2T, encerramentos por minuto, cenários contra a margem, cobertura, speedrun e os fatos de `fatos_prorrogacao.json`. |
| `historia.py` | Números dos capítulos 1–4 da história, a partir de analytics, agg, sections.bin e base. Gera `site/data/prorrog/historia.json`. |
| `fatos_prorrogacao.json` | Linha do tempo da decisão e números do TSE vindos da imprensa, com fonte e `confirmado`. |

Validação: com toda a cobertura, os votos do log batem com o comparecimento do BU em mais de 99,6% das urnas, e o total de votos depois das 17h fica na ordem do balanço do TSE (34.393).

## Fontes

| Fonte | Uso |
|---|---|
| `data/raw/cs_2t/{uf}-p003221-cs.json` (28) | **Índice**: principal = seção sem `nsp`. `secoes_agregadas` vem das agregadas (`nsp`); confere com `nsa`. |
| `data/raw/cs_2t/mun-e006258-cm.json` | Municípios: nome, capital, `cdi` → `cd_mun_ibge`. |
| `detalhe_votacao_secao_2026_BR.csv` (cargo 1) | `aptos1, comp1, abst1`, horários, situação da seção. |
| `votacao_secao_2026_BR.csv` | `l1, o1, x1, b1, n1` (pivot de `NR_VOTAVEL`, com 0 onde falta). |
| `eleitorado_local_votacao_2026_BRASIL.csv`, `NR_TURNO=2` | Local, coordenadas e `aptos2_cad` (`QT_ELEITOR_ELEICAO_FEDERAL`). |
| `detalhe_votacao_munzona_2026_BR.csv` | Só para conferir as zonas. |
| IBGE malhas v3 (`data/raw/ibge_malhas/`) | Polígonos, centroides (metadados) e teste de ponto no município. |

## Saídas (`data/base/`)

| Arquivo | Linhas | Tamanho | Notas |
|---|---|---|---|
| `secoes_index.csv` | 499.248 | 10,7 MB | Ver contracts §2. |
| `secoes.parquet` | 499.248 | 12,8 MB | Uma linha por `idx`, em ordem. |
| `locais.parquet` | 94.258 | 6,5 MB | Locais do 2T que têm ao menos uma seção principal, ou seja, onde está a urna. |
| `municipios.parquet` | 5.757 | 0,3 MB | 5.571 BR + 186 exterior. |
| `zonas.parquet` | 6.292 | 0,2 MB | Chave `(uf, cd_mun, zona)`. |
| `municipios_geo.geojson` | 5.570 | 3,4 MB | Malha IBGE "mínima", 4 casas decimais. |
| `municipios_geo.topojson` | 5.570 | 1,6 MB | Mesma malha em TopoJSON (objeto `municipios`), com as mesmas properties. |
| `municipios_geo_hd.topojson` | 5.570 | 3,4 MB | Malha "intermediária" para zoom estadual (objeto `municipios`). |
| `ufs_geo.geojson` | 27 | 0,25 MB | Properties `uf, cd_uf_ibge, nome, regiao`. |
| `build_report.json` | n/a | n/a | Checagens, reconciliação e cobertura. |
| `locais_diff_2t.csv` | n/a | n/a | Só com `--refresh-locais`. |

Os GeoJSON/TopoJSON municipais têm properties `cd_mun` (TSE, int), `cd_mun_ibge`, `nome` e `uf` (minúsculo).

### `secoes.parquet`

Colunas do contrato:

| Grupo | Colunas |
|---|---|
| Identificação | `idx` int32, `uf`, `regiao` (N/NE/CO/SE/S/ZZ), `cd_mun` int32, `cd_mun_ibge` int32 (nulo em ZZ), `zona` int16, `secao` int16 |
| Local e posição | `local_id` int32 → `locais.parquet`, `lat`, `lng`, `coord_src` (`local`/`mun_centroid`/`none`), `h3_r9` |
| 1T | `aptos1, comp1, abst1, l1, o1, x1, b1, n1` (int32) |
| Cadastro 2T | `aptos2_cad` int32 |
| Horários 1T | `t_tot1`, `t_bu1` (int64) |
| Agregadas | `secoes_agregadas` list<int16> (números das agregadas, mesma zona e município) |

Colunas extras:
- `nr_local` (2T);
- `coord_origem`: `local`, `ibge_centroide`, `ibge_ponto_interno` ou `none`; `locais_mediana` só no município sem polígono;
- `instalada1`;
- `tipo_local2`;
- `local_mudou_2t`.

Definições:
- `x1` são os votos nominais válidos de todos os demais candidatos, sem 13, 22 e 28.
- `n1` = nulos (`96`) + votos do nº 28 (nulos técnicos, 5.246 no país).
- Vale `comp1 == l1+o1+x1+b1+n1` em 100% das seções.
- `t_tot1` = `DT_PRIM_TOT_PARCIAL_HOR_TSE` e `t_bu1` = `DT_RECEBIMENTO_BU_HOR_TSE`.
  - **Epoch Unix de verdade** (segundos desde 1970-01-01 UTC). É o horário de Brasília do arquivo interpretado como UTC−03:00.
  - Exemplo: `04/10/2026 18:59:19` vira `1791151159`. Para mostrar, converta com fuso `-03:00`.
- `aptos2_cad` = `QT_ELEITOR_ELEICAO_FEDERAL` do `NR_TURNO=2`, com aptos pós-TTE. Soma 158.745.502 e difere de `aptos1` em 55.722 seções.

### Coordenadas (regra)

1. **`local`**: coordenada do local no 2T, se passar em duas validações:
   - está dentro do bbox do Brasil;
   - está a no máximo 0,25° do bbox do polígono IBGE do município.
2. Se o local não tem coordenada (`-1`), ou ela foi descartada, a seção recebe **`mun_centroid`**.
   - O ponto é o centroide do IBGE (metadados da API de malhas) quando ele cai dentro do polígono. Senão, um ponto interno calculado; isso acontece em 49 municípios.
   - `h3_r9` é calculado sobre esse ponto. Para mapas H3/pontos "honestos", filtre `coord_src == 'local'`.
3. **`none`**: exterior (ZZ). O TSE não publica coordenadas lá, e não há polígono.

Em `locais.parquet`:
- `coord_descartada` marca os 130 locais com coordenada lixo descartada (323 seções);
- `coord_pos_mun` (`dentro`/`perto`/`longe`/`sem_poligono`) dá a posição da coordenada bruta em relação ao polígono do município.

As descartadas são erros evidentes de geocodificação, de 42 a 15.600 km do centroide. Exemplos: escola de Belém posta em Salvador; os 8 pontos fora do Brasil citados em `research/baseline-datasets.md`.

Testados e não usados:
- Coordenada do mesmo local no 1T: recupera 0 seções.
- Mesmo endereço no município: recupera 53 seções, 5 delas ambíguas.

## Resultados do último build (24/10/2026 ~20:45 BRT)

**Índice.** `cs.json` 2T: 499.248 principais e 17.931 agregadas. 16.142 principais têm agregadas, 0 agregadas são órfãs e 0 divergem entre `nsa` e `nsp`.
- O conjunto é **idêntico** ao `detalhe_votacao_secao_2026_BR` do 1T (0 diferenças).
- Também é idêntico às principais do locais 2T (0 diferenças).
- A agregação do `cs.json` bate 100% com a do locais 2T.

**Reconciliação nacional.** Soma das seções contra o oficial:

| | Soma | |
|---|---|---|
| aptos | 158.745.502 | OK |
| comparecimento | 125.275.835 | OK |
| abstenção | 33.469.244 | OK |
| L (13) | 53.879.538 | OK |
| O (22) | 56.104.503 | OK |
| x | 9.316.747 | OK |
| válidos | 119.300.788 | OK |
| brancos | 2.300.798 | OK |
| n | 3.674.249 | OK (= 3.669.003 + 5.246) |

**Zonas.** As 6.292 zonas batem 100% com `detalhe_votacao_munzona` em aptos, comparecimento, abstenção, válidos, brancos, nulos totais e nº de seções principais.

**Checagens por seção.**
- Brancos e nulos de `votacao_secao` são iguais aos do `detalhe` em 100% das seções.
- `QT_VOTOS_NOMINAIS` = l+o+x+28 em 100% das seções.
- Votos de legenda e anulados em separado são todos 0.
- Em 41 seções `aptos1 != comp1+abst1`: são as não instaladas do exterior.

**Cobertura de coordenadas.**

| `coord_src` | Seções | Aptos 2T | % dos aptos 2T |
|---|---|---|---|
| local | 495.747 | 157.224.115 | 99,04% |
| mun_centroid | 2.150 | 604.704 | 0,38% |
| none (ZZ) | 1.351 | 916.683 | 0,58% |

Há 84.531 células H3 r9 distintas.

## Desvios / decisões (avise o supervisor se algo atrapalhar)

- **`h3_r9` também é preenchido para `mun_centroid`.** O contrato diz "nulo se não houver coordenada", e o centroide é uma coordenada. Para não empilhar seções no centroide, use `coord_src`.
- **`t_tot1`/`t_bu1` são epoch Unix (UTC).** O "BRT" do contrato foi lido como "o arquivo está em BRT", e não como "epoch deslocado".
- **Boa Esperança do Norte (MT)** (`cd_mun` 73709, IBGE 5101837) é município novo e não está na malha da API do IBGE (a API devolve 500 para esse código).
  - Não tem polígono: `tem_poligono=false` em `municipios.parquet`. A área dele continua dentro de Sorriso/Nova Ubiratã no mapa.
  - O centroide é a mediana dos seus locais (`centroid_src=locais_mediana`).
  - Todos os seus 3 locais têm coordenada própria.
- `municipios_geo` vem em GeoJSON (contrato) **e** em TopoJSON (mais leve). A versão HD sai só em TopoJSON, porque o GeoJSON intermediário teria 11,4 MB.
- `ufs_geo.geojson` tem `uf, cd_uf_ibge, nome, regiao`; `cd_mun`/`cd_mun_ibge` não se aplicam a UF.
- `municipios.parquet` traz extras: `centroid_src`, `tem_poligono`, `n_secoes`, `n_locais`, `n_zonas` e `aptos2_cad`.
  - `lat/lng` é nulo nas 186 cidades do exterior.
  - Os totais do 1T usam os mesmos nomes de `secoes` (`aptos1 … n1`).
- `zonas.parquet` e `locais.parquet` também trazem `n_secoes`, `aptos2_cad` e totais do 1T.
- `local_id` é sequencial na ordem `(uf canônica, cd_mun, zona, nr_local)` dos locais do 2T. É estável enquanto o conjunto de locais não mudar. Depois de um `--refresh-locais`, junte por `local_id` só dentro da mesma versão.

## Módulos

`common.py` (constantes, totais oficiais), `fetch_cs.py` (download cs/cm), `index.py` (índice),
`geo.py` (malhas, ponto no polígono, saídas geo), `refresh_locais.py` (refresh + diff), `build_base.py` (orquestra).

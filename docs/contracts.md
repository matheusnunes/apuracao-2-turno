# Contratos entre as peças (fonte única da verdade)

Supervisor: Claude. Se precisar mudar algo aqui, avise o supervisor; não mude sozinho um formato que outra peça consome.

Referências: `docs/superpowers/specs/2026-10-24-apuracao-2t-design.md` (desenho), `research/realtime-endpoints.md` (TSE ao vivo), `research/baseline-datasets.md` (base do 1T).

## 0. Ambiente e posse das pastas

- Python via `uv`, com projeto único na raiz (`pyproject.toml`). As dependências já instaladas são httpx[http2], asn1tools, duckdb, pyarrow, numpy, h3, orjson, aiohttp e pytest.
  - Para adicionar uma: `uv add <pkg>`. Se der conflito de lock com outro agente, tente de novo.
  - Para rodar: `uv run python -m <pacote>.<modulo>`.
- Dados brutos baixados são **não confiáveis**. Leia-os passando o caminho como argumento; nunca rode código de dentro de `data/raw/`.
- Cada pasta tem um dono. Não edite pastas de outro dono.

| Pasta | Dono | Conteúdo |
|---|---|---|
| `etl/` | agente ETL | constrói a base do 1T e os arquivos base do front |
| `collector/` | agente coletor | coleta TSE ao vivo, estado durável, publica `site/data/live/{manifest,agg,sections}` |
| `mock/` | agente mock | servidor que imita o TSE para o ensaio |
| `analytics/` | agente analytics | termômetro, ritmo, projeção, backtest 2022; publica `site/data/live/analytics.json` |
| `site/` | agente front | a tela; `site/data/` é só saída dos outros |
| `data/base/` | ETL (escrita) | parquet canônico; os demais só leem |
| `data/live/` | coletor | estado interno (sqlite, logs) |

## 1. Candidatos e códigos

- 2T: eleição **6258**, pleito **3221**. 1T: eleição 6257, pleito 3220. Cargo 1.
- **L = 13 LULA**; **O = 22 FLÁVIO BOLSONARO**.
- 1T nacional: O 56.104.503 (47,03%) × L 53.879.538 (45,16%). Aptos 158.745.502.
- Regiões:
  - N = AC AM AP PA RO RR TO
  - NE = AL BA CE MA PB PE PI RN SE
  - CO = DF GO MS MT
  - SE = ES MG RJ SP
  - S = PR RS SC
  - ZZ = exterior

## 2. Índice canônico de seções (ETL produz primeiro, prioridade máxima)

`data/base/secoes_index.csv`, com cabeçalho `idx,uf,cd_mun,zona,secao`:

- Contém só as **seções principais** do 2T: as 499.248 do `s.ts`, sem as agregadas.
- Ordem: `uf` (minúsculo, alfabético, com `zz` no fim), depois `cd_mun`, `zona` e `secao` como inteiros.
- `idx` vai de 0 a N−1 nessa ordem.
- `cd_mun` = código TSE (inteiro). Chave textual: `f"{uf}-{cd_mun:05d}-{zona:04d}-{secao:04d}"`.
- Todo array por seção (base e ao vivo) é alinhado por `idx`.

## 3. Base (parquet, `data/base/`), escrita pelo ETL

`secoes.parquet`, uma linha por `idx`:

- **Identificação:** `idx, uf, regiao, cd_mun, cd_mun_ibge, zona, secao`.
- **Local e posição:** `local_id` (int), `lat`, `lng`, `coord_src` (`local` | `mun_centroid` | `none`), `h3_r9` (string; nulo se não houver coordenada).
- **1T:** `aptos1, comp1, abst1, l1, o1, x1, b1, n1`.
  - `x1` = válidos nominais dos demais candidatos (sem 13, 22 e 28).
  - `n1` = nulos + nulos técnicos, incluindo os votos do nº 28.
- **Cadastro do 2T:** `aptos2_cad`, os aptos do cadastro 2T, se disponível.
- **Horários do 1T** (epoch s, BRT): `t_tot1` (= `DT_PRIM_TOT_PARCIAL_HOR_TSE`) e `t_bu1`.
- `secoes_agregadas`: lista de seções agregadas a esta, para exibição.

Demais tabelas:

- `locais.parquet`: `local_id, uf, cd_mun, zona, nr_local, nome, endereco, bairro, lat, lng, n_secoes`.
- `municipios.parquet`: `cd_mun, cd_mun_ibge, nome, uf, regiao, capital, lat, lng` (centroide) e os totais do 1T.
- `zonas.parquet`: `uf, cd_mun, zona` e os totais do 1T por zona.

## 4. Ao vivo (publicado pelo coletor em `site/data/live/`)

Toda escrita é atômica (arquivo temporário + `os.replace`). O `manifest.json` é sempre o **último** a ser escrito.

### `manifest.json`

```json
{ "version": 123,
  "generated_at": "2026-10-25T19:02:11-03:00",
  "source": "live|mock|replay",
  "tse": { "br_dg": "25/10/2026", "br_hg": "19:01:55", "br_idg": "123456" },
  "coverage": { "secoes_total": 499248, "secoes_totalizadas": 0, "secoes_com_bu": 0,
                "aptos_total": 158745502, "aptos_totalizados": 0 },
  "health": { "last_ok": "...", "errors_5min": 0, "req_rate": 0.0,
              "bu_queue": 0, "strategy": "cs|zonas", "stale_seconds": 0 },
  "files": { "agg": "agg.json", "sections": "sections.bin", "analytics": "analytics.json" } }
```

`files` aponta os nomes dos arquivos atuais. O front adiciona `?v=version` ao buscar.

### `agg.json`: agregados oficiais do TSE para o 2T

```json
{ "br": A, "uf": { "sp": A }, "mun": { "71072": A }, "zona": { "71072-0001": A } }
```

Onde `A` tem esta forma:

```json
{ "ts":0, "st":0, "te":0, "est":0, "c":0, "a":0, "vb":0, "tvn":0, "l":0, "o":0,
  "dt":"dd/mm/yyyy", "ht":"hh:mm:ss", "and":"n|...|f" }
```

- Todos os números são inteiros.
- `tvn` = nulos totais.
- Para municípios com zona única, o coletor pode preencher a `zona` copiando o `mun`.
- A chave de `zona` é `f"{cd_mun}-{zona:04d}"`.

### `sections.bin`: 2T por seção a partir dos boletins

- `Int32Array` little-endian com **N linhas × 8 colunas**, alinhado por `idx`.
- Colunas: `[has_bu, aptos2, comp2, l2, o2, b2, n2, t_recv_min]`.
  - `has_bu`: 1 se o boletim foi decodificado.
  - `aptos2` = aptos da seção + aptos TTE.
  - `n2` = nulos.
  - `t_recv_min` = minutos desde 25/10/2026 00:00 BRT do recebimento (`dr`/`hr` do aux), ou −1.
- Linha sem boletim: `has_bu=0`, demais colunas 0, `t_recv_min=-1`.

### `analytics.json`

Escrito pelo agente analytics; o schema é definido e documentado por ele em `analytics/README.md`. No mínimo deve trazer:

- **Termômetro** (`exato` e `estimado`) para BR, região e UF.
- `delta_req` e `delta_obs`.
- **Projeção:** margem em votos, % válidos, faixa e flag de virada.
- `history[]` com uma entrada por publicação: horário, % seções totalizadas, margem apurada, margem projetada, faixa, `delta_obs`.

### `history`

Fica dentro do `analytics.json` e cresce ao longo da noite. Pode ser podado para um ponto por minuto.

## 5. Frescor e saúde

- O front considera os dados **velhos** se `now − generated_at > 120 s` e mostra um alerta.
- O coletor publica a cada ~15–30 s, mesmo sem mudança, atualizando `generated_at` e `health`.

## 6. REVISÃO 24/10 ~21h30 (prevalece sobre §4/§5 onde houver conflito)

### 6.1 Versões e nomes de arquivo
- O `manifest.json` (nome fixo, escrito pelo coletor) é o heartbeat. Ele tem:
  - `version` (sobe a cada publicação);
  - `data_versions: {agg, sections}`: cada um sobe **só quando o conteúdo muda**;
  - `files: {agg: "agg.<dv>.json", sections: "sections.<dv>.bin"}`.
- O coletor mantém só as 3 últimas versões de cada arquivo versionado e apaga as mais antigas.
- O front só baixa de novo o `sections.<dv>.bin` (~16 MB) quando `data_versions.sections` muda.
- O manifest também traz `index: {n_rows: 499248, n_cols: 8, sha256: "<sha do secoes_index.csv>"}`. O front confere esses valores. O índice está **congelado** desde 24/10.
- O `analytics.json` tem nome fixo e é escrito atomicamente pelo processo analytics, a partir de um par consistente (agg e sections lidos pelo manifest). Ele traz `input_version` (o `data_versions.agg` usado) e `computed_at`.
- O servidor estático local manda `Cache-Control: no-store` para `site/data/live/`.

### 6.2 Saúde (`manifest.health`)
Além do que já existe:
- `tse_last_change`: hora (ISO) da última mudança vista em qualquer agregado do TSE;
- `tse_br_dg_hg`: carimbo da totalização no arquivo BR;
- `br_frozen`: bool, verdadeiro quando Σ `st` das UFs > `st` do BR (aí o BR é reconstruído pelas UFs);
- `lanes: {agg: {last_ok, req_rate, errors_5min}, bu: {last_ok, req_rate, errors_5min, paused_until}}`;
- `agg_lag_s`: idade da zona mais antiga que mudou e ainda não foi buscada de novo.

O front mostra **dois selos**: "Coletor OK há Xs" e "TSE: última totalização HH:MM (há N min)".

### 6.3 Convenções de métricas (valem para todos)
- **Margem** = L − O. Positivo = Lula à frente.
- **δ** = votos de margem por **100 aptos**.
- Percentuais vão de 0 a 100.
- Faixa = `[lo, hi]` com 90%.
- As taxas usam os aptos **de cada turno**: A1 no 1T; no 2T, A2 vem de BU, `est` ou `aptos2_cad`. As taxas são convertidas em votos com A2.

### 6.4 `analytics.json`: schema mínimo
O agente analytics pode acrescentar campos; não pode remover nem renomear.

```json
{ "input_version": 0, "computed_at": "iso",
  "status": { "phase": "pre|early|trend|final", "pct_secoes": 0, "pct_aptos": 0,
              "can_call": false, "call": null },
  "baseline_1t": { "l": 0, "o": 0, "margin_votes": 0, "aptos": 0 },
  "apurado": { "l": 0, "o": 0, "vb": 0, "tvn": 0, "c": 0, "a": 0, "te": 0, "est": 0,
               "margin_votes": 0, "l_pct_valid": 0, "o_pct_valid": 0 },
  "pace": { "delta_req": 0, "delta_rem": 0, "delta_obs": 0, "delta_obs_band": [0, 0] },
  "thermometer": { "br": T, "regiao": { "S": T }, "uf": { "sp": T } },
  "projection": { "l": 0, "o": 0, "margin_votes": 0, "margin_pp_valid": 0,
                  "band_votes": [0, 0], "band_pp_valid": [0, 0], "p_lula": null,
                  "virada_pct_secoes": null },
  "zones": { "71072-0001": Z }, "mun": { "71072": Z },
  "history": [ { "t": "iso", "pct_secoes": 0, "margin_apurado": 0, "margin_proj": 0,
                 "band": [0, 0], "delta_obs": 0, "delta_rem": 0 } ] }
```

Onde:
- `T = { coverage_aptos_pct, exact: M, estimated: M, best: M }`.
- `M = { d_abst_pp, d_abst_n, d_bn_pp, d_bn_n, d_l, d_o, d_margin_votes, delta, capture_l }`. O `capture_l` pode ser nulo; só aparece em região/BR com |P| grande.
- `Z = { dl, do, dbn, dabst, pct, src }`: deltas por 100 aptos vs 1T. `src` ∈ `obs` | `est` | `shrunk`.
- `delta_rem` = ritmo ainda necessário no que falta: `−(M2_apurado + M1_não_apurado) / A2_não_apurado × 100`.
- **Regras da fase**: antes de 2% apurado, a fase é `pre` (só mostrar a base 1T e o ritmo necessário). A fase vira `trend` e `can_call = true` somente se a faixa inteira estiver de um lado, houver ≥30% apurado e todas as regiões tiverem ≥5%.
  - "Todas as regiões" = N, NE, CO, SE, S. **O exterior (ZZ) fica fora da regra.** É 0,6% do eleitorado e apura em outros fusos. (decisão do supervisor, 24/10)

### 6.5 O front não calcula métricas
O front só **renderiza** o `analytics.json` e o `agg`, e faz a agregação espacial do mapa (H3/pontos/coroplético), aplicando os `Z` das zonas à base 1T de cada seção.

# collector — coletor ao vivo do TSE (2º turno 2026)

Coleta os agregados oficiais do TSE e, numa pista separada, uma amostra de BUs por seção. Grava estado durável em `data/live/` e publica `site/data/live/` conforme `docs/contracts.md` §4 e §6.

## Comandos

```bash
# AO VIVO (domingo). Roda para sempre, retoma sozinho e reinicia se cair.
uv run python -m collector run

# idem, com caffeinate e reinício mesmo após kill -9/OOM (Ctrl-C encerra de vez)
collector/run_forever.sh

# só agregados (plano B; ex.: se a rede estiver ruim no hotspot do celular)
uv run python -m collector run --no-bu

# contra o mock do ensaio (mesmos caminhos do TSE)
uv run python -m collector run --base-url http://localhost:8787/oficial
#   o estado do mock fica separado (state-mock-*.sqlite); --reset recomeça do zero

# teste contra o 1T REAL (totalmente publicado). Polido: 1 UF pequena, 4 conexões
S=/tmp/teste1t   # ou o scratchpad
uv run python -m collector run --eleicao 6257 --pleito 3220 --ufs rr --concurrency 4 \
    --state-dir $S/state --out-dir $S/out --exit-when-done
uv run python -m collector verify --eleicao 6257 --pleito 3220 --ufs rr --state-dir $S/state --out-dir $S/out

# saúde / estado
uv run python -m collector status                  # (mesmas flags de fonte do run)
tail -f data/live/collector.log                    # linha STATS a cada 60 s

# às 17:00–17:15: o cs.json passou a ter da/ha? (estratégia A)
uv run python -m collector cs-check --ufs sp,pe,rr

# testes
uv run pytest -q collector/tests
uv run python -m collector.tests.latency_bench --changes 10 --out /tmp/lat   # latência AGG (~8 min, só local)
```

## Arquitetura (contracts §6)

Um processo `asyncio` roda duas pistas independentes. Cada uma tem seu próprio cliente HTTP/2 (conexões próprias), balde de tokens AIMD, limite de concorrência e política de 403/429.

| Pista | O que busca | Teto padrão | 403/429 |
|---|---|---|---|
| **AGG** (espinha dorsal) | br `u`+`ab` a cada 30 s; as 28 UF `ab` (+ UF `u`) a cada 60 s, e na hora para UF que o br-ab acusou; `u.json` de município e zona **só** para municípios cujo `st` mudou, em ordem de Δst × aptos com envelhecimento e fatia mínima para zonas | 6 conexões, 20 req/s (piso 2) | por URL (detalhes abaixo); pausa da pista só numa "tempestade", 15 → 30 → 60 s (teto); a pista BU não é afetada |
| **BU** (amostra, só a sobra) | `cs.json`, `aux.json`, `-bu.dat` | 6 conexões, 10 req/s; sondagens ≤ 8 req/s | isolado: só a seção recua (≥ 120 s); "tempestade": pausa da pista BU 60 → 120 → 300 → 600 s enquanto se repetir, zerando após 10 min sem tempestade; a AGG continua |

- **Sempre GET condicional** (ETag / If-Modified-Since → 304) em todo índice e agregado. Nenhuma URL condicional é pedida em intervalo menor que ~12 s, porque a CDN guarda ~60 s e ignora a query string.
- **User-Agent:** `apuracao-sala-de-guerra/1.0 (uso pessoal)`.
- **Congestionamento por fração, não por evento:** cada pista olha uma janela de 60 s das respostas. 2% de 5xx/429 aleatórios (plausível na Akamai) não derrubam a taxa nem pausam nada (ensaio `--error-rate 0.02`).
  - 5xx/timeout/conexão: taxa ×0,7 só se ≥ 5% da janela falhou, no máximo 1 vez a cada 5 s.
  - 403/429 isolado: só aquela URL recua (município/zona: 30 → 60 → … → 300 s; Retry-After limitado ao teto da pista; seção BU: ≥ 120 s).
  - Se ≥ 5% da janela for 403/429, a taxa cai ×0,7.
  - "Tempestade" = 403/429 em ≥ 3 (AGG) / ≥ 5 (BU) URLs distintas **e** ≥ 20% da janela (ex.: `--burst-403`). Só ela pausa a pista: AGG 15 → 30 → 60 s; BU 60 → 120 → 300 → 600 s. O nível zera após 5 min (AGG) / 10 min (BU) sem tempestade.
  - Pedidos de br/UF que esperaram 60 s furam qualquer pausa da AGG. Assim o BR e as UFs nunca ficam cegos mais que um teto de pausa.
- **Nada fica "mudou e nunca rebuscado":**
  - Município/zona com 403/429/5xx continua pendente (e no `agg_lag_s`) e é re-tentado com recuo até conseguir.
  - Só 404 permanente (3 seguidos) sai dos pendentes: o município volta no próximo Δst; a zona, após 10 min.
  - Se o `st` do ab despenca (mock reiniciado com estado velho, retotalização), o município e todas as suas zonas são rebuscados.
- **Retries:** 5xx, timeouts e erros de conexão são repetidos com espera exponencial e jitter. Depois de 20 erros de conexão seguidos, o cliente é recriado. Nada depende de IP fixo (funciona em hotspot).
- **Δ do município:** comparado por `(st, c)` do `ab` contra o `u.json`. O `dt`/`ht` do `ab` é a hora do município, e o do `u.json` é a hora global, então não dá para comparar.
- **Multizona:** quando o município muda, as zonas ainda incompletas são buscadas. Uma checagem de consistência (Σ st das zonas < st do município) rebusca zonas que a CDN serviu velhas.
- **Anti-inanição:**
  - 25% de cada lote vai para os pendentes mais antigos (`--oldest-share`), o que limita a espera máxima.
  - Prioridade do resto = Δst estimado × aptos/seção × (1 + idade/30 s).
  - Pelo menos 40% do lote vai para arquivos de zona (`--zone-share`).
  - Sem isso, as zonas de SP/RJ/DF ficavam para trás dos municípios recém-mudados.
- **Zona única:** a zona é uma cópia do município. Zonas de municípios multizona aparecem em `agg.zona` assim que buscadas pela primeira vez (só após o município sair do zero).
- **BR congelado** (como em 19:14–20:04 no 1T):
  - Publica sempre o total mais adiantado. Se Σ `st` das UFs > `st` do BR, sai a soma das UFs (`br_source="uf_sum"`).
  - Os caches da CDN do BR e das UFs são independentes (até 60 s), então ficar à frente sozinho não basta para acusar.
  - `br_frozen=true` só quando, além de as UFs estarem à frente, o conteúdo do BR (`idg`/`dg`/`hg`) não muda há > 120 s (`br_unchanged_s`).
  - Só vale quando o escopo tem as 28 UFs.
- **.json × .jws:** em `--format auto`, a cada 2 min compara o `idg` do BR em `.jws` com o do `.json`. Se o `.jws` estiver à frente e o `.json` parado por >90 s duas vezes seguidas, troca tudo de `dados/` para `.jws` (payload base64url).

### Descoberta de BUs (pista BU)

- **Estratégia A (`cs`):** relê `{uf}-p003221-cs.json` (condicional) a cada 2 min por UF ativa (5 min quando ociosa). Seções que ganharam ou mudaram `da`/`ha` têm o aux buscado na hora. Se o `da`/`ha` muda numa seção já capturada, o aux é relido e o BU é baixado de novo se o hash mudou.
  - É detectada sozinha: o log mostra `ESTRATÉGIA A: cs.json de XX tem da/ha em N seções`.
  - Só "confia" na A enquanto o cs da UF continua mudando: houve mudança nos últimos 5 min.
- **Estratégia B (`zonas`):** por zona, sonda no máximo `ceil((st_zona − BUs conhecidos) × 1,5)` seções ainda não capturadas, escolhidas na ordem de chegada do 1T (`t_tot1` de `data/base/secoes.parquet`).
  - Recuo por seção após 404: 90 s → 3 → 6 → 10 min (nunca menos de 90 s).
  - UFs com taxa de acerto baixa têm as sondagens reduzidas (aceitação ∝ EMA do acerto, mínimo 10%).
  - Round-robin UF → zona → seção, para os primeiros BUs saírem espalhados pelo país.
- **BU:** usa o hash do aux com `st == "Totalizado"` (o mais recente, se houver vários) e o arquivo `tp=bu`, ou `busa` na falta dele. A decodificação filtra `idEleicao == --eleicao` e cargo presidente.
  - Falha de decodificação vai para o log, para `health.decode_failures` (com o motivo) e para `data/live/failed_bu/`, e a seção é pulada. Nunca derruba o coletor.
- **Divergência:** quando a soma dos BUs e o agregado divergem, vale o agregado. Os BUs só alimentam `sections.bin`.

### Estado e reinício

- O estado fica em `data/live/state-{source}-e{eleicao}-p{pleito}.sqlite`, em modo WAL. Ele guarda:
  - os validadores HTTP;
  - os agregados e as entradas `ab`;
  - o universo de seções e o resultado de cada BU;
  - a agenda de sondagem;
  - as versões publicadas.
- As escritas são gravadas a cada 0,5 s. Validador e estado derivado do mesmo arquivo entram na mesma transação, então um 304 após reinício sempre corresponde ao estado salvo.
- Num reinício, tudo volta 304 e nenhum BU já capturado é baixado de novo. Testado com `kill -9`.
- Log em `data/live/collector.log` (rotativo, 50 MB × 5).
- `run` se reinicia sozinho 10 s após uma exceção inesperada. SIGTERM e SIGINT fazem flush e uma última publicação.

### Saídas (`--out-dir`, padrão `site/data/live/`)

- `agg.<dv>.json` e `sections.<dv>.bin`, com `dv` = `data_versions.*`. Cada um sobe só quando o conteúdo muda, e só as 3 últimas versões de cada são mantidas.
- `manifest.json` é sempre o último a ser escrito, a cada 15 s (heartbeat). O `version` sobe a cada publicação.
- O manifest traz `index {n_rows, n_cols, sha256}` do `data/base/secoes_index.csv`.
- `sections.<dv>.bin` é `Int32` LE com N=499.248 × 8 colunas: `[has_bu, aptos2, comp2, l2, o2, b2, n2, t_recv_min]`. Linha sem BU: zeros e `t_recv_min=-1`.
  - O `t_recv_min` conta a partir de 00:00 BRT de `--day0`: 25/10 para 6258, 04/10 para 6257.
- As chaves de `agg.json` são:
  - `mun`: `str(int(cd_mun))`;
  - `zona`: `f"{cd_mun}-{zona:04d}"`;
  - para UF sem nada apurado, `uf` e `mun` saem do `ab`, com votos 0.

Campos de `manifest.health`:

| Campo | Significado |
|---|---|
| `last_ok`, `errors_5min`, `req_rate` | soma das duas pistas |
| `bu_queue` | seções esperando aux/BU |
| `strategy` | `cs` ou `zonas` |
| `stale_seconds` | segundos desde o último poll OK do BR |
| `tse_last_change` | ISO da última mudança vista em qualquer agregado |
| `tse_br_dg_hg` / `tse_br_age_s` | carimbo de geração do arquivo BR e sua idade |
| `br_frozen`, `br_source`, `br_unchanged_s` | BR congelado, de onde veio o BR publicado (`br` ou `uf_sum`) e há quanto tempo o conteúdo do BR não muda |
| `lanes.agg` / `lanes.bu` | `last_ok`, `req_rate`, `errors_5min`, `rate_cap`, `paused_until`; na AGG também `lane_pauses` e `urls_backing_off` |
| `agg_lag_s`, `agg_pending`, `agg_lag_parts` | idade do município/zona mais antigo que mudou e ainda não foi rebuscado, e quantos são; `agg_lag_parts` separa `mun_s`, `zona_multi_s` e `zona_capitais_s` (municípios com ≥ 10 zonas) |
| `agg_latency_s` | últimos 10 min: `detect_to_publish_*` (mudança vista no `ab` → `agg.<dv>.json` publicado) e `tse_to_publish_*` (`Last-Modified` do `u.json` no TSE → publicado) |
| `decode_failures`, `decode_failures_by_kind` | falhas de decodificação de BU |
| `bu_per_min`, `bu_total` | ritmo de BUs |
| `cs_dh_ufs` | UFs cujo cs.json já tem `da`/`ha` |
| `format` | `json` ou `jws` |
| `requests_total` | requisições desde o início |

## Flags principais

| Flag | Padrão | |
|---|---|---|
| `--base-url` | `https://resultados.tse.jus.br/oficial` | mock: `http://localhost:8787/oficial` |
| `--eleicao` / `--pleito` | 6258 / 3221 | 1T: 6257 / 3220 |
| `--cand-l` / `--cand-o` | 13 / 22 | |
| `--ufs` | `all` | ex.: `ac,rr` |
| `--muns` | — | filtro de teste (códigos de 5 dígitos) |
| `--agg-concurrency` / `--agg-rate` | 6 / 20 | pista AGG |
| `--bu-concurrency` / `--bu-rate` | 6 / 10 | pista BU |
| `--concurrency N` | — | atalho: N/2 para cada pista |
| `--probe-rate` / `--probe-factor` / `--probe-min-interval` | 8 / 1,5 / 90 | estratégia B |
| `--request-budget N` | — | teto de requisições nesta execução |
| `--l0-interval`, `--uf-interval`, `--mun-min-interval`, `--zone-min-interval`, `--cs-interval`, `--publish-interval` | 30, 60, 15, 30, 120, 15 s | |
| `--strategy` | `auto` | `cs` força só A; `zonas` força só B |
| `--format` | `auto` | `json` ou `jws` |
| `--no-bu` | | só agregados |
| `--state-dir` / `--out-dir` / `--index` | `data/live` / `site/data/live` / `data/base/secoes_index.csv` | |
| `--source` | pela URL | `live`, `mock` ou `replay` |
| `--day0` | pela eleição | |
| `--reset` | | apaga o estado desta fonte/eleição |
| `--exit-when-done`, `--max-runtime` | | testes |
| `--http-log ARQ` | | uma linha por requisição (diagnóstico) |

## Medições (24/10, véspera)

- **1T real, RR:** 1.519 seções (`--eleicao 6257 --pleito 3220 --ufs rr`, 4 conexões).
  - O `verify` deu Σ BU = agregado oficial da UF em 13 (74.420), 22 (231.322), brancos, comparecimento e aptos.
  - Os 15 municípios bateram 100%.
  - Os nulos diferem só pelos nulos técnicos do nº 28.
  - `uf.rr` do `agg` publicado é igual ao oficial.
- **Reinício após `kill -9` no meio:**
  - 0 BU baixado duas vezes.
  - 2 aux repetidos (os que estavam em voo).
  - Tudo o mais voltou 304.
  - 499.248 seções restauradas em 2,2 s.
- **Vazão da pista BU (aux+BU, ~0,4 s por requisição, quase tudo miss na CDN):**
  - 2 conexões: 2,5 req/s (~75 BUs/min).
  - 3 conexões: 5,2 req/s (~155 BUs/min).
  - Com o padrão (6 conexões, teto 10 req/s), projeção de ~300 BUs/min (~18 mil/h).
- **Latência da pista AGG** (`collector/tests/latency_bench.py`, cadências de produção, TSE falso sem CDN): mudança no TSE → `agg.<dv>.json` publicado.
  - Média de 16,9 s, mediana de 15,4 s, máximo de 32,9 s (8 mudanças).
  - Mudança vista no `ab` → publicado: p50 de 4,8 s.
  - No TSE real, soma-se a idade do cache da CDN (0–60 s).
- **Pico do 1T** (simulado com `t_tot1`):
  - Até ~400 municípios mudam por janela de 30 s, mais ~350 arquivos de zona (multizona).
  - Isso dá ~25 req/s de demanda AGG, contra o teto padrão de 20 req/s.
  - Os municípios grandes saem primeiro (Δst × aptos). No pico, os pequenos podem atrasar 1–2 min (`agg_lag_s`).
  - `--agg-rate 30` elimina esse atraso, se aceitável.
- **Zonas de capitais no mock** (`--speed 2`, partida a frio às 18:40, isto é, 2× a vazão real de mudanças):
  - Nenhuma zona ausente nas 8 maiores cidades multizona.
  - Σ st das zonas acompanha o município. Exemplo de Brasília: 6.715 publicadas para 6.754 no mock.
  - Atraso máximo: ~65 s nas zonas de capitais e ~85 s nas zonas multizona em geral.
  - Os municípios pequenos chegam a ~200 s, porque a 2× a demanda passa do teto de 20 req/s.
  - Zero pausas da pista e nenhum `br_frozen` falso.
- **CPU em escala nacional:**
  - Planner: ~10 ms por passada.
  - Publicação: ~75 ms, incluindo o `sections.bin` de 16 MB.
  - RSS: ~760 MB.

## O que checar no domingo

1. **Antes das 17:00:** suba o coletor com `uv run python -m collector run`.
   - O manifest deve ser atualizado a cada 15 s.
   - `health.lanes.agg.errors_5min` deve ficar em 0 e `stale_seconds` abaixo de 40.
   - Em idle a pista AGG faz ~1 req/s.
2. **17:00–17:15:**
   - Rode `uv run python -m collector cs-check --ufs sp,pe,rr,df`, ou procure `ESTRATÉGIA A` no log. A pergunta é se o `Last-Modified` do cs muda e se aparecem `da`/`ha`.
   - Se aparecerem, `health.strategy` vira `cs` e `health.cs_dh_ufs` lista as UFs.
   - Se não, a estratégia B segue sozinha (`strategy=zonas`). Não precisa fazer nada.
3. **Durante a noite:**
   - Acompanhe `health.agg_lag_s` (deve ficar abaixo de 60) e `agg_latency_s.tse_to_publish_p90`.
   - Se aparecer `lanes.bu.paused_until`, o TSE mandou 403/429 para a pista BU. Ela volta sozinha em 10 min.
   - Se 403/429 se repetirem, reinicie com `--no-bu`: o estado é preservado.
   - `br_frozen=true` significa que o BR do TSE parou e o BR publicado está vindo das UFs.
4. **Se o `.json` parar e o `.jws` não:** o log mostra `trocando para .jws`. Isso também pode ser forçado com `--format jws`.

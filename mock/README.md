# mock/ — servidor que imita o TSE (2º turno 2026)

Servidor local para ensaiar a sala de guerra ponta a ponta. Serve, em
`http://HOST:PORT/oficial/...`, **os mesmos caminhos e formatos do TSE** para o 2T
(ciclo `ele2026`, eleição **6258**, pleito **3221**). O relógio é simulado e acelerado, e o resultado
por seção é sintético mas derivado do 1T real seção a seção.

## Rodar

```bash
# ensaio padrão (cenário swing2022: swing 1T→2T de 2022 por município, Lula +1,5 p.p.)
uv run python -m mock.server --speed 10 --start 16:55 --port 8787

# outros cenários
uv run python -m mock.server --scenario virada --speed 10 --start 16:55 --port 8787
uv run python -m mock.server --scenario flavio  --speed 10 --start 16:55
uv run python -m mock.server --scenario empate  --speed 10 --start 16:55
uv run python -m mock.server --scenario swing2022 --target-margin-pp -0.8   # outra margem final

# outra ordem de chegada entre UFs (não depender de "Sul/Sudeste chegam primeiro")
uv run python -m mock.server --order nordeste-first --speed 10 --start 16:55
uv run python -m mock.server --scenario flavio --order nordeste-first     # virada ao contrário (19:24)
uv run python -m mock.server --order random --seed 7                      # embaralhado (semente)

# com falhas
uv run python -m mock.server --speed 10 --start 18:30 --cs-mode progressive \
    --freeze-br 19:14-20:04 --error-rate 0.02 --burst-403 19:40-19:45 --burst 503@20:30-20:35@0.5

# verificação ponta a ponta (sobe o próprio servidor; ~11 min reais)
uv run python -m mock.verify
```

Na primeira execução, `mock.prepare` lê os CSVs brutos com DuckDB (~10 s) e grava
`mock/cache/base.parquet` e `mock/cache/swing2022_mun.parquet`. Para refazer:
`uv run python -m mock.prepare --force`. A cada subida a verdade do cenário é regravada em
`mock/out/<cenario>_truth.parquet` (~3 s).

URL de exemplo: `http://localhost:8787/oficial/ele2026/6258/dados/br/br-c0001-e006258-u.json`.
O coletor só precisa trocar a base `https://resultados.tse.jus.br` por `http://localhost:8787`.

## Endpoints servidos

`B = /oficial/ele2026`. A query string (`?nocache=`) é ignorada, como na CDN.

| Caminho | Comportamento |
|---|---|
| `B/6258/dados/br/br-c0001-e006258-u.json` | resultado BR (formato idêntico ao real; antes das 17h é **byte a byte** o arquivo zerado real) |
| `B/6258/dados/{uf}/{uf}-c0001-e006258-u.json` | resultado UF (28, inclui `zz`) |
| `B/6258/dados/{uf}/{uf}{mun5}-c0001-e006258-u.json` | resultado município (5.757) |
| `B/6258/dados/{uf}/{uf}{mun5}-z{z4}-c0001-e006258-u.json` | resultado zona (6.292 pares município-zona) |
| `B/6258/dados/br/br-e006258-ab.json` | acompanhamento BR (UFs + BR, com `munnr/munpt/munf`, `ufsnr/ufspt/ufsf`) |
| `B/6258/dados/{uf}/{uf}-e006258-ab.json` | acompanhamento UF (um registro por município + a UF no fim) |
| `B/6258/config/mun-e006258-cm.json` | cópia real (byte a byte, ETag real) |
| `B/arquivo-urna/3221/config/{uf}/{uf}-p003221-cs.json` | cs real de `data/raw/cs_2t/`; `--cs-mode none` (default) = arquivo real estático com o ETag/Last-Modified reais; `progressive` = cada seção principal ganha `da`/`ha` (= hora de publicação do aux), regerado a cada `--cs-interval` s simulados |
| `B/arquivo-urna/3221/dados/{uf}/{mun5}/{z4}/{s4}/p003221-{uf}-m{mun5}-z{z4}-s{s4}-aux.json` | **404** (XML S3 `NoSuchKey`, igual ao real) até a publicação; depois o aux com `dg/hg` = publicação, `dr/hr` = recebimento, `hash`, `arq[]` |
| `B/arquivo-urna/3221/dados/.../{hash}/o03221{uf}{mun5}{z4}{s4}-bu.dat` | BU sintético BER (gerado sob demanda e em cache LRU); hash ou nome errado → 404 |
| `…-busa.dat` | seções que no 1T usaram o Sistema de Apuração (31) têm só `busa` no aux (`--no-busa` desliga) |
| `*.jws` (u, ab, cm, cs) | JWS compacto com o cabeçalho real e payload = o JSON; **assinatura falsa** |
| `/oficial/comum/config/ele-c.json` | cópia da amostra real |
| qualquer outro | 404 XML S3 |

HTTP:
- `ETag` (md5 do corpo, como o S3) e `Last-Modified`, com **304** para `If-None-Match` e `If-Modified-Since`.
- gzip quando o cliente pede `Accept-Encoding: gzip`, inclusive no `.bu.dat`.
- `Cache-Control: max-age=60` nos JSON e nos 404; `max-age=3600` no `.bu.dat`.
- CORS como o TSE: `Access-Control-Allow-Origin` ecoa o `Origin` e manda `Vary: Origin`. O preflight `OPTIONS` responde 200 com `Allow-Methods: GET` e `Max-Age: 600`.
- Cabeçalhos `x-amz-request-id`, `x-ratelimit-*`, `cdn-cache-status` e `Accept-Ranges`.
- **`Date`, `Last-Modified` e `Expires` usam o relógio simulado.** O cabeçalho `X-Mock-Sim-Time` traz a hora simulada da resposta.

Controle (não existe no TSE):
- `GET /__mock/status`: hora simulada, progresso (seções totalizadas, aux publicados, margem apurada) e contadores.
- `GET /__mock/clock?t=19:30&speed=30`: pula o relógio ou muda a velocidade (`speed=0` pausa). Também limpa o cache de edge.
- `GET /__mock/health`.
- No console sai uma linha de status a cada 10 s reais.

## Relógio e ritmo

- `--start HH:MM[:SS]` é a hora simulada (BRT, 25/10/2026) no momento da subida. Horas de 00:00 a 11:59 contam como 26/10.
- `--speed` dá os segundos simulados por segundo real.
- **Entrada nos agregados:** a seção entra no `DT_PRIM_TOT_PARCIAL_HOR_TSE` real do 1T, na mesma hora do dia (de 17:08:57 a 02:58:39).
  - Os agregados são gerados por *tick* (`--tick`, default 60 s simulados).
  - No tick K entram as seções com `t_tot ≤ K·60`. Todos os níveis (BR, UF, município, zona, ab) viram juntos, 7 s depois do tick.
  - Cada arquivo só muda quando entra seção nova **naquela abrangência**. Assim o `ETag` fica estável e há 304 de verdade.
  - `dg/hg/idg`: hora do tick da última mudança. `dt/ht`: hora da última seção totalizada.
  - `and`: `n` (nada), `p` (em andamento) ou `f` (completa). `tf=s` quando a abrangência está completa.
  - Na totalização final nacional, todos os arquivos mostram o vencedor com `e=s`/`st=Eleito` e o outro com `Não eleito`.
- **Publicação do aux/BU:** `t_pub = max(t_recv + lag, t_tot + 15 s)`.
  - `t_recv` é o `DT_RECEBIMENTO_BU_HOR_TSE` real do 1T, que vira `dr/hr` no aux.
  - O lag é log-uniforme por UF/região: `--lag "ne=2-20,sp=35-240,default=10-60"`, em minutos, com chaves UF > região (`n ne co se s zz`) > `default`.
  - O lag observado na verificação bate com essas faixas (ver abaixo).
- O ritmo de chegada é o do 1T real, inclusive o platô de 19:15 a 19:45 (≈84% → 85% das seções).

### Ordem de chegada (`--order real|nordeste-first|random`)

A **ordem dentro de cada UF é sempre a real**. O que muda é qual curva de chegada (quantis de `t_tot`) cada UF recebe:

| Ordem | Curva de cada UF |
|---|---|
| `real` (default) | a própria, com os horários do 1T 2026 |
| `nordeste-first` | as curvas mais rápidas do 1T vão para NE, depois N, CO, SE e S |
| `random` | permutação das curvas entre as 27 UFs, com semente `--seed` |

- O exterior (`zz`) mantém a própria curva.
- O recebimento acompanha a nova curva: `t_recv = t_tot_novo − (t_tot1 − t_recv1)`, nunca antes de 17:05. O lag de publicação continua por UF.
- Os votos não mudam, só os horários. O startup imprime a mediana de chegada de cada UF.

| Cenário / ordem | Comportamento da margem apurada |
|---|---|
| `swing2022` / `real` | Lula atrás desde o início (−7,6 p.p. às 18:00); vira às 20:50 (94% das seções) |
| `swing2022` / `nordeste-first` | Lula **à frente desde o início** (+22 p.p. às 17:30, +8 às 19:00) e cai até +1,5; sem virada |
| `swing2022` / `random` (seed 2026) | +12 às 17:30 e +2,6 às 18:00; estabiliza em ~+1,5 desde 19:00 |
| `flavio` / `nordeste-first` | Lula à frente cedo (+18,7 às 17:30); **Flávio vira às 19:24** (79% das seções) e vence por 3,0 |

## Cenários (semente 2026; resultados finais nacionais)

Todos usam os aptos do **cadastro 2T** (`eleitorado_local_votacao`, NR_TURNO=2) e as 499.248 seções principais do índice canônico (`data/base/secoes_index.csv`).

| Cenário | Modelo | Lula (13) | Flávio (22) | Margem | Comparec. | Virada (último cruzamento) |
|---|---|---|---|---|---|---|
| **`swing2022`** (default) | 1T 2026 da seção + swing 1T→2T **de 2022 do município**, em deltas por apto para L, O, BN e Abs. Clamp ≥0 e renormalização (o X1 some). Desloca uniformemente por apto até a margem-alvo (`--target-margin-pp`, default +1,5). Município sem 2022 usa a UF. | 60.766.390 (50,75%) | 58.970.341 (49,25%) | +1.796.049 (+1,50 p.p.) | 79,37% | 20:50:10, com 94,45% das seções |
| `virada` | paramétrico: NE/N mobilizam e herdam mais do X1; S/SE chegam cedo e puxam Flávio | 60.993.448 (50,75%) | 59.190.686 (49,25%) | +1.802.762 (+1,50) | 79,42% | 20:49:37, com 94,16% |
| `flavio` | paramétrico: centro-direita do 1T vai para Flávio, NE desmobiliza | 57.662.328 (48,50%) | 61.229.071 (51,50%) | −3.566.743 (−3,00) | 79,05% | Flávio lidera a noite toda |
| `empate` | paramétrico, margem-alvo +0,05 | 59.778.544 (50,03%) | 59.718.794 (49,97%) | +59.750 (+0,05) | 79,20% | 22:31:27, com 99,91% |

Curva do `swing2022` (margem apurada nos válidos):

| Horário | Seções | Margem |
|---|---|---|
| 17:30 | 2,3% | −4,2 |
| 18:00 | 13,2% | −7,6 |
| 19:00 | 58,5% | −4,7 |
| 20:00 | 85,0% | −1,8 |
| 20:30 | 91,5% | −0,6 |
| 21:00 | 98,2% | +0,8 |
| final | — | +1,50 |

No `swing2022`, a variação de margem por 100 aptos (já com o deslocamento uniforme) vai de S +0,46 a NE +4,04. O espalhamento regional é o de 2022, em que a margem por 100 aptos caiu entre −4,75 (S) e −0,93 (NE).

Detalhes de cada modelo estão na docstring de `mock/scenario.py`. Para gerar as verdades e imprimir as curvas sem subir o servidor: `uv run python -m mock.scenario`.

### Verdade por seção: `mock/out/<cenario>_truth.parquet`

O arquivo se chama `<cenario>-<ordem>_truth.parquet` quando `--order` não é `real`. Uma linha por `idx`, alinhada ao índice canônico.

- **Identificação:** `idx, uf, regiao, cd_mun, zona, secao, local, origem` (0 UE, 1 RED, 2 SA, 3 não instalada) e `instalada`.
- **2T final:** `aptos2, aptos_tte2, comp2, abst2, l2, o2, b2, n2`.
- **1T real:** `aptos1, comp1, l1, o1, x1, b1, n1`.
- **Tempos** (desde 25/10 00:00 BRT): `t_tot_s/t_tot_min` (entra nos agregados), `t_recv_s/t_recv_min` (dr/hr) e `t_pub_s/t_pub_min` (aux/BU publicados; −1 = nunca). Mais o `hash` do diretório do BU.
- **Metadados:** cenário, semente, alvo, calibração, lag, `order` e doadoras de curva.

## Injeção de falhas

| Flag | Efeito |
|---|---|
| `--lag SPEC` | atraso aux/BU por UF/região. O default reproduz o 1T: NE 2–20 min, SP 35 min–4 h, demais 10–60 min |
| `--freeze-br HH:MM-HH:MM` | `br-c0001-…-u` **e** `br-…-ab` param no tick do início da janela, como no incidente de 19:14 a 20:04 do 1T. UFs, municípios e zonas seguem atualizando |
| `--error-rate P` | fração P de requisições com 429 (`Retry-After: 2`) / 500 / 502 / 503 (XML S3 `SlowDown`) / 504 |
| `--burst-403 HH:MM-HH:MM` | bloqueio total: 403 HTML "Access Denied" do Akamai na janela (hora simulada) |
| `--burst CODE@HH:MM-HH:MM[@RATE]` | rajada de qualquer código, repetível. Ex.: `--burst 429@19:30-19:35@0.7` |
| `--sticky-404 S` | **404 "gruda" por S segundos reais** (cache de 404 do CDN; default 60; 0 desliga). Quem sondou o aux antes da publicação continua vendo 404 até expirar |
| `--edge-ttl S` | cache de edge também para 200 (S segundos reais, default 0); o `max-age` cai como no real |
| `--latency-ms M` | latência extra exponencial de média M |
| `--cs-mode none\|progressive` | testa as duas estratégias do coletor: `none` força o fallback por zonas + sondagem de aux |

## Verificação (`uv run python -m mock.verify`)

Sobe o servidor (speed 60, `--cs-mode progressive`, `--freeze-br 19:14-20:04`) e consulta como um coletor de 16:55 a 03:05 simulados. Compara tudo com a verdade:

- **Agregados BR, 28 UFs, ab (BR e UF), municípios e zonas sorteados:** cada versão bate com a soma das seções da verdade no tick que o próprio arquivo indica (`dg/hg`). As 28 UFs somadas batem com o BR quando ele não muda durante a leitura, e os municípios do `ab` somados batem com a UF.
- **Final:** BR a 100% com `and=f`, `tf=s` e vencedor `Eleito`. Varredura de **todos** os 5.757 municípios e 6.292 zonas.
- **aux:** 404 antes de `t_pub` e 200 depois; `dg/hg` = publicação, `dr/hr` = recebimento, `hash` correto. Também o 404 grudado e a volta a 200 após o TTL.
- **BUs sorteados**, decodificados pelo **decodificador da pesquisa** (`research/scripts/decode_bu.py`), contra a verdade: identificação, eleição 6258, pleito 3221, aptos, TTE, comparecimento, 13, 22, brancos, nulos e um único cargo.
- **HTTP:** 304 (If-None-Match e If-Modified-Since), gzip e CORS.
- **cs progressivo:** `da/ha` = hora de publicação do aux.
- **Congelamento do BR:** o `hg` fica fixo enquanto as UFs andam.
- **Falhas,** num segundo servidor: `--error-rate 0.1`, `--burst-403` e `--burst 503@…@0.5`, contados por minuto simulado.

Relatório em `mock/out/verify_report_<cenario>.json` e log do servidor em `mock/out/verify_server_*.log`. `--order` testa outra ordem de chegada.

**Resultado da última execução completa** (`swing2022`, 16:55 → 03:05 simulados a speed 60; `mock/out/verify_full.log`): **116.862 checagens, 0 falhas**.

| Checagem | Resultado |
|---|---|
| BR | 0 → 499.248 seções (100%); 14.616 leituras de UF e 462 confirmações de "soma das UFs = BR" |
| ab | 522 `ab` de UF |
| municípios e zonas | 2.088 municípios + 2.088 zonas sorteados; na varredura final, **5.757 municípios e 6.292 zonas com 0 divergências** |
| aux | 3.313 com 404 antes de `t_pub` e 9.573 com 200 depois, com `dr/hr` e `dg/hg` corretos |
| BUs | **681 decodificados pelo decodificador da pesquisa e iguais à verdade**: 600 sorteados nas 28 UFs (`votacaoUE`), mais as 50 RED (`votacaoRED`) e as 31 SA (`busa`, `tipoBU=SA`) |
| 304 | 244 com If-None-Match e 104 com If-Modified-Since |
| 404 grudado | 8 aux sondados antes da publicação continuam 404 por 50 s reais depois do `t_pub`; com 62 s reais viram 200 |
| congelamento | `br` fica em `hg 19:13:13` de 19:16 a 20:04 enquanto as UFs andam |
| falhas | `--error-rate 0.1` → 9–11% de erros por minuto; `--burst-403` → 100% de 403; `--burst 503@…@0.5` → 55% |
| lag observado (min) | NE med 6,8 (p10 2,9 / p90 15,8); SP med 82 (41 / 181); demais med 23,5 (12 / 49) |
| BR final | Lula 60.766.390 × Flávio 58.970.341, `and=f`, `tf=s`, 13 `Eleito` / 22 `Não eleito` |

Uma execução parcial com `--order nordeste-first` (17:00–18:40) também passou: 15.018 checagens, 0 falhas.

Testes de formato feitos à parte:
- O serializador (`mock/tsejson.py`) reproduz **byte a byte** os 80 JSON reais de `research/samples` e `data/raw/cs_2t`.
- Os arquivos zerados do mock (br-u, sp-u, pe25313-u, br-ab, pe-ab, pe-cs) saem byte a byte iguais aos reais.
- O roundtrip asn1tools do BU real do 1T é byte a byte.

Carga: com 32 clientes concorrentes na mesma máquina, um processo serve ~6.300 req/s (aux + BU + u.json).

## Diferenças conhecidas em relação ao TSE real

- **Assinaturas:** o JWS tem cabeçalho real (`kid`, EdDSA) e assinatura falsa, que não valida com a chave do bundle. Os hashes e assinaturas dentro do BU são pseudoaleatórios.
- **Infraestrutura:** só HTTP/1.1 (sem HTTP/2/h3). O cabeçalho `Server: Python/aiohttp` aparece, e faltam `akamai-grn`, `alt-svc` e HSTS. `Date`, `Last-Modified` e `Expires` seguem o relógio simulado. O `max-age` é fixo em 60, salvo com `--edge-ttl`; no real ele decresce. Só há cache de edge (stale) quando ativado; por padrão, apenas o 404 gruda.
- **Geração:** os agregados viram todos juntos por tick (60 s simulados). No real, cada arquivo é regerado de forma assíncrona e o `idg` é global e sequencial; aqui o `idg` é monotônico no tempo, mas inventado.
- **Campos com semântica inferida:**
  - `si/sni/sa/sna`: `si` = totalizadas instaladas, `sa` = `si`, `sna` = 0.
  - Percentuais: `pc` e `pa` sobre `esi`, conferido com o 1T real; regra 0,01/99,99.
  - `tf=s` por abrangência completa.
  - `st` dos candidatos vazio até o final nacional.
  - `dvt:"Válido"`, `esae`/`mnae` e reordenação de `seq` aparecem só depois de começar a apuração.
  - O valor `p` de `and` vem do mapeamento do bundle (`{n:0, p:1, f:2}`).
- **aux:**
  - Sempre um único hash, com `st` = `Totalizada`/`Totalizado`. Não há reenvio, troca de urna ou estado "recebido".
  - Lista `log/bu/vota/rdv` como o real, mas **só o `bu` (ou `busa`) é servido**; os outros dão 404.
  - O `busa` das 31 seções de Sistema de Apuração é inferido, sem amostra real.
- **BU:**
  - Só a eleição 6258/Presidente. Em AM, RN, DF, ES, AC e TO o BU real do 2T também trará governador (6260).
  - `ordemImpressao=1`.
  - `aptosTTE` é aproximado (aptos 2T − eleitores vinculados à seção e agregadas).
  - `detalhamentoComparecimento` (biometria) é sintético.
  - Horários no fuso local da UF (AC −2; AM, RR, RO, MT e MS −1); no exterior, BRT.
  - Os votáveis com 0 voto são omitidos, como no real.
- **Seções não instaladas:** as 41 do 1T (exterior) continuam não instaladas. Entram em `st` (e `sni`) no horário do 1T, mas nunca têm aux/BU.
- **cs:**
  - O modo `progressive` é hipótese; o comportamento real do `cs` na noite não foi comprovado.
  - No modo `none` o arquivo é o real, estático.
  - As seções agregadas (`nsp`) nunca ganham `da/ha`.
- **Não servidos:** `-e.json` (eleitos), fotos, `qbu`, arquivos do 1T (6257/3220) e os `log/rdv/vota`.

## Arquivos

- `prepare.py`: base do 1T por seção (DuckDB nos CSVs brutos) e swing 2022 por município.
- `scenario.py`: cenários, calibração, tempos e lags, e a verdade em parquet.
- `state.py`: relógio, agregados incrementais por tick e construção de u/ab/cs/aux/BU com cache.
- `server.py`: aiohttp, roteamento, HTTP (304, gzip, CORS), falhas e controle.
- `bu.py` + `bu-2026.asn1` (cópia de `research/scripts/`): BU sintético a partir de `templates/template-1t-bu.dat` (Recife z1 s31, 1T).
- `tsejson.py`: serializador no formato exato do TSE e percentuais.
- `verify.py`: verificação ponta a ponta.
- `templates/`: arquivos zerados reais do 2T (br/uf/mu), `mun-e006258-cm.json`, `ele-c.json`, corpo do 404 e BU modelo.

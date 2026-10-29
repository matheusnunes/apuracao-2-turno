# site/ — a tela da sala de guerra (2º turno 2026)

Dono: agente front. É uma página estática, servida do Mac em localhost e aberta numa TV, sem ninguém no mouse (Modo TV).

Ela **renderiza**:
- `manifest.json` / `agg.<dv>.json` / `sections.<dv>.bin`, escritos pelo coletor;
- `analytics.json`, escrito pelo analytics.

A única conta feita no navegador é a **agregação espacial do mapa** (contracts §6.5). Termômetro, ritmo, projeção, faixa, fases e `can_call` vêm prontos do `analytics.json`.

## Rodar

```bash
# 1. uma vez (e de novo se data/base/ mudar, p.ex. depois de `etl.build_base --refresh-locais`): ~2 s
uv run python site/tools/export_base.py

# 2. servidor estático (porta 8000; --lan para escutar em 0.0.0.0)
uv run python site/serve.py

# 3. abrir na TV (tela cheia, F11 / ⌃⌘F):
#    http://127.0.0.1:8000/            tela com mapa (lê data/live/)
#    http://127.0.0.1:8000/numeros.html  reserva leve, só números (sem mapa e sem sections.bin)
```

No domingo, o coletor (`uv run python -m collector run`) e o analytics (`uv run python -m analytics.run --live-dir site/data/live --interval 20`) escrevem em `site/data/live/`. A tela só lê.

### Ensaio sem coletor (fixture própria)

```bash
# apuração falsa progressiva a partir da base real + analytics REAL apontado para a mesma pasta
uv run python site/tools/fake_live.py --out-dir site/data/live-fake --speed 20 --with-analytics
#   abrir http://127.0.0.1:8000/?live=data/live-fake/
# um retrato fixo:            --at 18:30
# coletor parado (banner):    --stall-at 18:00 --stall-secs 150
# TSE parado (banner):        --tse-freeze-at 18:20 --tse-freeze-secs 400
# BR congelado (banner):      --br-frozen-at 18:40
# arquivo sumido (404):       --drop-files
# cenários:                   --scenario virada|empate|flavio
```

`?live=<pasta>` escolhe a pasta ao vivo (padrão `data/live/`). Com isso o ensaio do mock pode usar outra pasta sem atrapalhar a do coletor.

### Verificação

```bash
# Chrome headless via CDP: erros de console, heap após GC a cada 30 s, screenshots 1920×1080
uv run python site/tools/cdp_check.py --url 'http://127.0.0.1:8000/?live=data/live-fake/#tv=1' \
    --minutes 10 --shots 150,600 --out /tmp/cdp
# prévia 1920×1080 num navegador comum (iframe escalado):
#   http://127.0.0.1:8000/tools/tv1080.html?src=../%3Flive%3Ddata/live-fake/
```

`window.__tela` expõe o estado (S, B, FEED, PLACE, CELL, map), e `window.__telaErrors` guarda os últimos erros capturados.

## Arquivos

| Arquivo | O que é |
|---|---|
| `index.html` | casca + carregador de bibliotecas: cópia local em `vendor/` primeiro, CDN fixo como reserva |
| `numeros.html` | página de reserva só com números (placar, % apurado, mix, termômetro, ritmo, projeção, selos) |
| `js/app.js` | mapa (MapLibre + deck.gl), painéis, Modo TV, busca, ajustes, hash/recarga |
| `js/mapagg.js` | valor 2T efetivo por seção → locais → células H3; modos de cor; rampas |
| `js/live.js` | polling do manifest e downloads versionados; nunca lança, guarda o último estado bom |
| `js/health.js` | selos "Coletor OK há Xs" / "TSE: última totalização HH:MM (há N min)" e faixas de alerta |
| `js/base.js` | carrega `data/base/` (binários "struct of arrays" + JSON) |
| `js/fmt.js` | formatação pt-BR, cores OKLab, utilidades |
| `js/exterior.js` | país e coordenada das 186 cidades do exterior (tabela manual; o TSE não publica) |
| `js/prorrog.js` | dados dos logs de urna (`data/prorrog/`), carregados sob demanda; somas por município e UF |
| `js/prcharts.js` | gráficos da prorrogação: curva por minuto 1T × 2T, régua contra a diferença final, urnas encerrando × decisão |
| `js/historia.js` | a narrativa pós-eleição em cartões (5 capítulos), montada a partir de `data/prorrog/historia.json` e `resumo.json` |
| `js/urnalog.js` | visor do log de uma urna (votos acumulados 1T × 2T, código de barras, linhas filtráveis) |
| `css/style.css` | tokens claro/escuro e layout TV/desktop/celular (base do protótipo + acréscimos) |
| `serve.py` | servidor estático: `no-store` + ETag em `data/live*`, gzip p/ JSON, MIME de `.bin`/`.topojson` |
| `tools/export_base.py` | parquet do ETL → `data/base/` (formatos abaixo) |
| `tools/fake_live.py` | fixture ao vivo no formato do contrato (manifest/agg/sections) + analytics real |
| `tools/cdp_check.py`, `tools/tv1080.html` | verificação (headless e prévia 1080p) |
| `vendor/` | maplibre-gl 4.7.1, h3-js 4.1.0, deck.gl 9.0.38 (e 9.1.14 para teste), baixados dos CDNs fixos |

### Base exportada (`data/base/`, little-endian; layout em `meta.json`)

- `secoes.bin`: N = 499.248 seções, alinhadas ao `idx`. Colunas:
  - `place` (i32; −1 se a seção não tem coordenada própria, isto é, centroide ou exterior);
  - u16: `a1, c1, l1, o1, x1, bn1, a2 (aptos2_cad), secao, zone, mun`;
  - u8: `uf`.
- `locais.bin`: os 93.297 locais com `coord_src=='local'`. Colunas:
  - `lat, lng` (f32);
  - `sec_start, nsec, local_id` (i32);
  - **`cell_r3..cell_r9`** (i32): índice da célula em `h3/r{res}.json`, pré-calculado em Python. O navegador nunca roda h3 sobre seções.
  - `mun, zone` (u16), `uf` (u8).
- `locais_secs.bin`: as seções de cada local, contíguas (CSR).
- `h3/r{3..9}.json`: `{cells, uf, lat, lng}` de cada resolução. São 639 / 3.214 / 12.775 / 31.914 / 48.658 / 68.109 / 84.155 células, carregadas sob demanda.
- `municipios.json`, `zonas.json`: colunas. As zonas são município×zona, com chave `"{cd_mun}-{zona:04d}"` e seções contíguas.
- `mun_poly.json`, `ufs.json`: malha IBGE simplificada.
- `locais_meta/{uf}.json`: nome, endereço, bairro, nº do local e zona, carregados no clique/hover.
- `meta.json`: contagens, sha256 do `secoes_index.csv` (o front confere com `manifest.index`) e a base 1T nacional (reserva se o `analytics.json` faltar).

As seções com centroide do município (2.150) e as do exterior (1.351) ficam fora de pontos e H3. Elas entram nos números de zona e município, que vêm do agg e do analytics.

O exterior tem vista própria (foco "Exterior", busca por país ou cidade, e uma vista no Modo TV). Cada cidade é um círculo no mapa-múndi, com a cor do modo atual e área proporcional aos votos válidos. A tabela por país abre as cidades no clique. A comparação com o 1º turno no exterior é exata, pelo agregado oficial de cada cidade já toda apurada. O swing por zona do analytics não serve lá, porque não descreve cada cidade.

## Depois da apuração: história, prorrogação e log da urna

```bash
uv run python -m etl.logs_urna --rate 60 --concurrency 32   # ~499 mil logs (~87 GB brutos em data/logs/raw), ~2,3 h, retomável
uv run python -m etl.prorrogacao                            # site/data/prorrog/{secoes.bin,resumo.json}
uv run python -m etl.historia                               # site/data/prorrog/historia.json
```

- **História no Modo TV.** Com a fase `final`, o Modo TV deixa as vistas de acompanhamento e conta a história:
  1. a recuperação entre os turnos;
  2. a noite da apuração;
  3. o modelo que previu cedo;
  4. o resultado;
  5. a prorrogação.

  Cada cartão traz um número-herói, uma frase e, às vezes, um gráfico. O "palco" esmaece o mapa por trás. O cartão comanda o modo de cor, a camada e o foco, e as "batidas" trocam a cor no meio dele (1º → 2º turno).
  - Navegação: ◀ ❚❚ ▶ e os chips de capítulo; no teclado, ← →, espaço e 0–5.
  - `#tv=1&tvi=N` abre no cartão N.
  - As frases só montam texto: os números vêm de `historia.json` e `resumo.json`.
- **Prorrogação.** Modos de cor "Votos após 17h" do 2º e do 1º turno, na mesma escala (por 10 mil votos, hora de Brasília). O log do 2º turno traz o 1º turno da mesma urna. O exterior fica sem cor (hora local).
  - Nesses modos, o painel direito vira a régua, a curva, os testes e os fatos.
  - A conclusão segue uma regra: só aparece com ≥ 95% dos votos conferidos nos logs, e a variante sai do pior caso, urna a urna.
  - Fatos de imprensa ficam em `etl/fatos_prorrogacao.json`, sempre com a fonte.
- **Log da urna.** Abre pelo botão "ver log" na tabela de seções do local, pela busca ("zona 12 seção 40 SP") ou por `#log=<idx>`.
  - O `serve.py` serve `/logs/{uf}/{mun}/{zona}-{secao}.txt` descompactado. Se a urna ainda não foi baixada, busca no TSE.
  - Títulos de eleitor de mesários ficam mascarados na tela; o download é o arquivo original.

## Como a tela usa os dados

- **Polling** a cada 10 s:
  - `manifest.json` sempre;
  - `agg.<dv>.json` e `sections.<dv>.bin` só quando o `data_versions` respectivo muda;
  - `analytics.json` a cada ciclo, com `If-None-Match` (304 se não mudou).
- Se `manifest.index` (`n_rows`/`sha256`/`n_cols`) divergir da base, o `sections.bin` é ignorado, com banner vermelho.
- **Valor 2T efetivo por seção**, refeito no máximo a cada 30 s:
  - com BU, vale o BU;
  - sem BU, aplica o `Z` da zona (`obs`/`est`): `p_k2 = max(0, k1/A1 + d_k/100)`, renormaliza e multiplica por `aptos2_cad`;
  - zona `shrunk` fica sem valor e sem cor;
  - zona ou município com `pct ≥ 99,95` e `Z.raw` presente usa o swing bruto `raw` (sem encolhimento); abaixo disso, o `Z` encolhido.
- **Observado × estimado:**
  - cor cheia = BU;
  - cor lavada = estimado pela zona;
  - opacidade proporcional à parte apurada.
- **Municípios:**
  - "Margem 2T" e "% apurado" vêm do `agg.mun`;
  - os modos comparativos aplicam `analytics.mun` (o mesmo `Z`) à base 1T do município.
- **UFs:** usam `analytics.thermometer.uf[*].best`.
- **"Projeção · votos que faltam":** usa `projection.uf − agg.uf`.
- **Fases:**
  - `pre`: só base 1T + ritmo necessário;
  - `early`: rótulo "sinal inicial · não é tendência";
  - "tendência" só em `trend`.
- **Vencedor:** aparece só com `status.can_call` e `status.call`. A faixa aparece sempre junto da projeção.
- **Mix do apurado:**
  - a frase é `status.mix_caption`, sob o placar bruto;
  - as barras por região vêm de `status.mix_regiao`.
  - Nenhum texto fixo sobre a ordem de chegada; toda frase do Modo TV é montada com dados atuais.
- **Frescor:**
  - âmbar a partir de 60 s sem manifest novo; vermelho e banner a partir de 120 s;
  - TSE: âmbar a partir de 3 min sem mudança, vermelho e banner a partir de 5 min, só durante a contagem;
  - banners também para `br_frozen`, índice divergente, analytics atrasado, servidor local fora do ar e pausa da pista de BU.

## Robustez (TV a noite toda)

- Arrays tipados alocados uma vez. Os `data` das camadas do deck são estáveis; só as cores mudam (`updateTriggers`), com transição de 700 ms.
- Recarga completa a cada ~45 min, só se o servidor responder. Antes de recarregar, o estado vai para a hash: `#tv=1&mode=…&layer=…&f=uf:sp&tvi=3&z=…&c=…`. Ela também é atualizada a cada interação, e um F5 manual também restaura.
- Arquivo ausente/parcial, JSON inválido ou HTTP 404 nunca derrubam a tela: o último estado bom continua.
- Perda do contexto WebGL → recarga.
- Mapa base sem rede → fundo liso após 12 s. As linhas de UF e os rótulos de cidades são desenhados pelo deck com dados locais.
- Vendor local primeiro: a recarga de 45 min não depende de CDN.

## Verificado em 24/10 (~22h)

| Verificação | Como | Resultado |
|---|---|---|
| Erros de console | `cdp_check.py` (Chrome headless, 1920×1080) + `window.__telaErrors` no Chrome real | 0 erros e 0 exceções em todas as rodadas |
| Memória | 10 min de apuração falsa (17h→24h a 25×), Modo TV girando, heap medido após GC a cada 30 s | 37–52 MB, tendência −0,6 MB/min |
| Layout | coluna esquerda e direita sem rolagem em 1920×1080 | ok, inclusive com chamada de vencedor e foco no Nordeste |
| Fases | `pre` → `early` → `trend` → `final` | o roteiro do Modo TV é refeito a cada troca de fase |
| Recarga e hash | recarga forçada | volta na mesma vista do Modo TV |
| Saída real do coletor | snapshot contra o mock | `source=mock`, índice com sha conferido, 570 BUs |
| Mapa | três escalas | municípios no zoom 4; hexágonos res 7/8 no zoom 9–10,7; pontos + res 9 no zoom 12 |
| Desempenho (Mac, Chrome) | por atualização de dados (no máximo 1 a cada 30 s) | efetivo por seção 10–40 ms quente; recolorir ~30 ms |

## Limitações conhecidas

- deck.gl 9.0.38 sobre MapLibre 4.7.1 em modo sobreposto, como no protótipo:
  - o modo instanciado do `H3HexagonLayer` não desenha nessa combinação, então usamos `highPrecision: true` (polígonos);
  - os rótulos de cidade do mapa base foram trocados por um `TextLayer` próprio, por cima do preenchimento;
  - `?deck=9.1&interleaved=1` carrega deck 9.1.14 intercalado, para teste.
- O modo "Projeção · votos que faltam" é por UF: o analytics não publica projeção por município.

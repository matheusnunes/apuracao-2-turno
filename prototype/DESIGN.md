# Protótipo — tela de apuração do 2º turno (Lula × Flávio Bolsonaro)

Spike descartável com **dados sintéticos** para fechar o desenho da tela. O contrato vinculante entre as peças continua sendo
`docs/contracts.md`; este documento descreve o que a tela espera **em cima** dele (§8) e o que precisa ser acrescentado (§8.4).

## 0. Como abrir

```bash
cd prototype && python3 -m http.server 8765 --bind 127.0.0.1
# abrir http://127.0.0.1:8765/  (TV: tela cheia 1920×1080, F11)
```

- Sem build. As bibliotecas vêm de CDN com versões fixas: `maplibre-gl@4.7.1`, `deck.gl@9.0.38`, `h3-js@4.1.0` e `d3-delaunay@6.0.4`. O mapa base é o Carto dark-matter/positron, sem chave.
- O mundo sintético é gerado no navegador em cerca de 1,3 s:
  - 65,8 mil locais, 418,7 mil seções, 7,9 mil municípios e 8,2 mil zonas (município × zona); São Paulo tem 61 zonas.
  - Calibrado no 1T real: Lula 45,15% × Flávio 46,98%, déficit de 2,17 mi votos e 158,6 mi aptos.
- O cenário padrão é "virada de Lula". Em ⚙ Ajustes também há "empate técnico" e "Flávio mantém".
- Controles da simulação:
  - A apuração anda a 1 min simulado/s, com um "snapshot" a cada 2 s.
  - ❚❚ pausa. Deixe pausado por mais de 60 s para ver o alerta de dados atrasados (o de dados velhos aparece com mais de 120 s).
  - O slider leva a qualquer horário entre 17:00 e 24:00.
- `?interleaved=1` liga o modo intercalado do deck (ver §7).
- `window.__apur` expõe o estado para depuração.

Arquivos: `index.html` (casca), `style.css` (tokens claro/escuro, layout TV/desktop/celular), `sim.js` (mundo sintético, simulação, agregação e projeção; roda em Node), `app.js` (mapa, painéis, Modo TV).

## 1. Premissas que moldaram o desenho

1. **Tempo real vem das zonas, não dos boletins.**
   - Os agregados oficiais por zona (cerca de 6,3 mil chaves `cd_mun-zona`) atualizam em cerca de 1 min.
   - O boletim (BU) por seção atrasa: no 1T foram de 2 a 20 min em PE e de 35 min a 4 h em SP.
   - Por isso a tela nunca espera BU para responder "está funcionando?".
2. **O placar bruto engana.** Sul e Sudeste apuram primeiro, e Lula perde lá. A tela leva o olhar para comparações *casadas*: as mesmas seções no 1º e no 2º turno.
3. **A participação de Lula nos válidos sobe mecanicamente no 2T**, porque os outros candidatos saem. Nunca a mostramos sozinha como sucesso. Os indicadores de campanha são:
   - Δ margem;
   - captura do eleitorado em disputa;
   - Δ abstenção;
   - Δ brancos+nulos.
4. **Sala com TV e ninguém no mouse.** O Modo TV vem ligado em telas largas; o controle manual continua disponível.

## 2. Layout

### TV 1920×1080 (padrão, tema escuro, `html{font-size: .86vw}` ≈ 16,5 px)

```
┌ barra: título · SIMULAÇÃO · relógio | placar oficial bruto (com aviso Sul/Sudeste) | 3 barras de cobertura | frescor | Modo TV ▶ ❚❚ velocidade ⟷ ⚙ ┐
├──────────── 34rem ────────────┬──────────────── mapa ────────────────┬──────── 27rem ────────┤
│ TERMÔMETRO DA CAMPANHA · BR   │ [busca] [UF] [Auto|UF|Mun|Hex|Locais] │ EM FOCO (Brasil/região│
│  +2,2 votos de margem/100     │ ┌ legenda TV: "Brasil · Lula está no  │  /UF/município/hex/   │
│  Δabst · ΔBN · ΔLula · ΔFlávio│ │ ritmo?" + frase + barra de tempo    │  local): mini-termôm.,│
│  captura do eleitorado        │                                       │  captura, decomposição│
│ RITMO NECESSÁRIO × OBSERVADO  │            mapa deck.gl               │ POR REGIÃO / UFs (✓/✗)│
│  régua: necessário +1,4 | obs │                       ┌ Cor do mapa ┐ │ DESTAQUES (rankings)  │
│ PROJEÇÃO + VIRADA             │                       │ legenda     │ │  ou CARTÃO DO LOCAL   │
│  Lula 50,9% ± faixa · gráfico │ [badge camada/res]    └─────────────┘ │  (tabela por seção)   │
└───────────────────────────────┴───────────────────────────────────────┴───────────────────────┘
```

- A coluna esquerda é fixa: termômetro, ritmo, projeção e frescor ficam sempre visíveis, como pedido para o Modo TV.
- A direita acompanha a "história" da vez.
- Os números-chave têm de 2,6 a 2,0 rem (43–33 px) para leitura do sofá; o texto secundário fica em ≥ 0,78 rem.

### Desktop menor e celular

- **Até 1600 px:** a barra se compacta.
- **Até 1100 px:**
  - uma coluna só: barra, mapa (64vh) e depois os painéis;
  - o Modo TV começa desligado;
  - a legenda perde as notas;
  - as tabelas viram grade 2×2.
- Testado em 390×844.

## 3. Cores — escolhidas pelo trabalho que fazem, validadas com o script do skill `dataviz`

| Papel | Escuro | Claro | Por quê |
|---|---|---|---|
| Candidato A = Lula | `#3987e5` | `#2a78d6` | É a referência (hubpolitico usa azul p/ Lula, laranja p/ Flávio) e não é a cor do PT. |
| Candidato B = Flávio | `#d9731f` | `#d9731f` | Par frio/quente. Validação: ΔE CVD 27,0, visão normal 31,3, contraste ≥ 3:1 nos dois temas. |
| Abstenção caiu ↔ subiu | `#1f9c8e` ↔ `#b5179e` | idem | Divergente **sem** cores de candidato, para "abstenção caiu" não ser lida como "bom para Lula". ΔE CVD 10,6. (Teal × rosa `#d55181` falhou: ΔE 3,2.) |
| Neutro (meio da divergente) | `#4a4d55` | `#d6d5cf` | Cinza = "empate / nada". |
| Status do frescor | verde / âmbar / vermelho | | Sempre com texto ("Atualizado há 4 s", "Atrasado", "Dados velhos"). |

- Nomes, números e cores dos candidatos ficam em ⚙ Ajustes (salvos em localStorage).
  - Preset "violeta × âmbar" para quem preferir fugir do azul.
  - As rampas são regeneradas por interpolação OKLab.
- **Divergentes:**
  - 5 classes por braço mais a faixa neutra, em degraus, não contínuas: dá leitura estável na TV e transições sem cintilar.
  - Cada braço vai do neutro à cor do candidato. No tema claro, o último degrau escurece.
- **Sequenciais:** 6 classes, do neutro até o matiz.
- **Observado × estimado:**
  - Cor cheia = observado na urna (BU).
  - Cor lavada (32% em direção ao neutro, proporcional à fração estimada) = estimado pelo swing da zona.
  - Opacidade = 0,3 + 0,7 × fração de seções com valor (desligável).
  - Sem valor = transparente (hexágono/local some; município fica cinza fraco).
- Texto nunca usa a cor da série; a identidade vem do quadradinho ao lado.

### Modos de cor do mapa (seletor "Cor do mapa", com legenda própria)

| Modo | Valor | Escala | Limiares (por braço) |
|---|---|---|---|
| **Ritmo vs necessário** (padrão TV) | Δ margem/100 aptos − necessário | div. A↔B | 0,4 · 1 · 2 · 3,5 · 6 |
| Margem no 2º turno | (L−O)/válidos (município: placar oficial) | div. A↔B | 2 · 5 · 10 · 20 · 35 p.p. |
| Δ margem vs 1º turno | ((L2−O2)−(L1−O1))/aptos×100 | div. A↔B | 0,75 · 2 · 3,5 · 6 · 9 |
| Δ abstenção | −Δ(abst/aptos) p.p. | div. teal↔magenta | 0,5 · 1,5 · 3 · 5 · 8 |
| Δ brancos+nulos | −Δ(BN/comparecimento) p.p. | div. teal↔magenta | 0,3 · 0,8 · 1,5 · 2,5 · 4 |
| Ganho de votos de A / de B | ΔL/aptos, ΔO/aptos (p.p. do eleitorado) | seq. cor do candidato | 0 · 2 · 4 · 6 · 9 · 12 |
| Abstenção 2T | 1 − comp/aptos | seq. teal | 10 … 35% |
| Projeção · votos que faltam | margem projetada das seções não totalizadas; opacidade = parte que falta | div. A↔B | 2 · 5 · 10 · 20 · 35 |
| % apurado | município: % totalizado; hexágono/local: % com BU | seq. cinza | 0 … 100% |

"Ganho de votos" traz o aviso de que sobe mecanicamente; o padrão é sempre o ritmo.

## 4. Mapa

### Camadas (seletor "Auto | UF | Município | Hexágonos | Locais")

- **Auto:**
  - zoom < 7,6: municípios (agregado oficial — é o padrão da noite);
  - 7,6–11,8: hexágonos H3;
  - ≥ 11,8: locais sobre hexágonos a 45%.
  - Os locais também aparecem sobre os hexágonos a partir do zoom 10,5.
- **Município:** polígonos com borda fina clara, preenchimento semitransparente, como na referência.
  - No protótipo são Voronoi das sedes recortado pelo contorno do Brasil.
  - Na implementação: malha IBGE simplificada (§8.2).
- **UF:** os mesmos polígonos pintados pelo valor da UF, sem bordas internas. Faz o papel do choropleth estadual em zoom de país.
- **Hexágonos:** mosaico com `coverage 0.9`, para dar um respiro de 10% entre células.
- **Locais:** pontos com anel claro, raio ∝ √aptos (2–13 px).

### Zoom → resolução H3 (lado da célula ≈ 4–8 px na tela)

| zoom MapLibre | res | lado médio | leitura |
|---|---|---|---|
| < 4,5 | 3 | 69 km | Brasil inteiro (mancha) |
| 4,5–5,9 | 4 | 26 km | macrorregião |
| 5,9–7,3 | 5 | 9,9 km | estado |
| 7,3–8,7 | 6 | 3,7 km | região metropolitana |
| 8,7–10,1 | 7 | 1,4 km | cidade grande, entre zonas |
| 10,1–11,5 | 8 | 530 m | bairros |
| ≥ 11,5 | 9 | 200 m | rua / local |

- "Detalhe dos hexágonos" em ⚙ desloca a tabela em −1/+1.
- A agregação é toda no navegador: cada local traz `h3_r9`, os pais (res 3–8) são calculados uma vez com `cellToParent`, e a soma local→célula da resolução atual roda a cada snapshot.
- Hoje leva cerca de 5 ms para 66 mil locais e 38 campos.

### Valor 2T de cada seção (espinha dorsal)

- **Observado na urna:** a seção tem BU (`sections.bin` com `has_bu=1`) e usa os números reais.
- **Estimado pela zona:** a seção não tem BU, mas a zona tem algo totalizado.
  - Aplica-se à base 1T da seção o swing **por apto** da zona: ΔL, ΔO, ΔBN e Δcomparecimento.
  - Depois normaliza para L+O+BN = comparecimento estimado.
- **Sem valor:** a zona ainda não tem nada apurado; a seção some do mapa.
- **Swing residual:**
  - É calculado só com a parte totalizada **sem** BU, contra o 1T da parte da zona sem BU.
  - Assim, quando a zona chega a 100%, as seções estimadas somam exatamente o agregado oficial.
  - Isso imita o `zones` do `analytics.json` (`dl, do, dbn, dabst` por 100 aptos), que é o que a implementação deve consumir.
- Hexágonos e locais somam esses valores efetivos.
- Tooltip:
  - "% apurado na zona" é a média das zonas das seções da célula;
  - "% com boletim";
  - a parte observada × estimada.
- Grandes cidades têm muitas zonas (no mock, cada zona tem um centro espacial e inclinação própria). Com isso, o H3 mostra variação dentro da cidade já às 17:30, antes de qualquer BU.

### Interação

| Ação | Resultado |
|---|---|
| Passar o mouse | Tooltip com valores: nome, cobertura, margem, Δ margem, ritmo, Δ abstenção, Δ BN e a base da comparação. |
| Clicar | Foco em município, hexágono ou local; o painel direito "Em foco" é atualizado. |
| Clicar num local | Cartão do local, abaixo. |

Cartão do local:

- **KPIs:** Seções (urnas) · Zona eleitoral · Eleitores aptos · Votos 2T.
- Endereço e bairro, botão "Ir para o local".
- **Tabela por seção:** seção, status, aptos, comparecimento, abstenção 1T→2T, Lula 1T→2T, Flávio 1T→2T, Δ margem.
  - O status é um chip: "aguardando", "apurada" (totalizada sem BU) ou "boletim recebido".
  - Valores estimados aparecem em itálico com "~".

Outros controles:

- **Busca:**
  - "Recife" busca município;
  - "Bahia" ou "BA" busca UF;
  - "zona 12 SP" busca zona;
  - "zona 12 seção 40 SP" busca seção e foca no local.
- **Filtro UF:** enquadra a UF, esmaece o resto (22%) e restringe os rankings.
- **Painel direito:**
  - mini-termômetro do foco, captura e decomposição;
  - tabela por região (ou UFs da região/UF em foco) com ✓/✗ de ritmo;
  - Destaques: Lula mais cresceu, Flávio mais cresceu, abstenção caiu e abstenção subiu, por município ou por hexágono.
- Amostras com menos de 0,5% do eleitorado mostram "aguardando".

## 5. Métricas (o que cada número significa)

Notação: L = Lula, O = oponente (Flávio), X = outros candidatos no 1T, BN = brancos + nulos, C = comparecimento, A = aptos.
Tudo é calculado sobre **as mesmas seções** nos dois turnos.

### Termômetro

Aparece para BR, cada região, cada UF e cada foco, em duas versões:

- **estimado:** todas as seções com valor (observado + estimado pela zona). É o número grande.
- **exato:** zonas 100% totalizadas + seções com BU. Fica na linha abaixo, com a cobertura do eleitorado.

| Indicador | Fórmula |
|---|---|
| **Δ margem** | ((L2 − O2) − (L1 − O1)) / A × 100, em votos de margem por 100 aptos (unidade do analytics). Também em votos e em p.p. dos válidos. |
| Δ abstenção | (abst2 − abst1)/A em p.p. e em nº de eleitores ausentes. |
| Δ brancos+nulos | BN2/C2 − BN1/C1 em p.p. do comparecimento, e em votos. |
| Δ votos de Lula / do oponente | Em votos e em p.p. do eleitorado. |
| **Captura do eleitorado em disputa** (soma 100%) | Lula ΔL, Flávio ΔO, branco/nulo BN2 e "deixou de votar" max(0, −ΔC), sobre o pool. |
| Linha de captura | "Entre os que escolheram um lado: Lula ΔL/(ΔL+ΔO)", comparada com a **captura necessária** = ½ + déficit/(2·D). |

- O pool da captura, por seção, é X1 + BN1 + max(0, ΔC).
- Na captura necessária, D = (ΔL+ΔO) observado, extrapolado ao Brasil pelo comparecimento do 1T.

### Ritmo necessário

- Necessário: déficit do 1T / aptos × 100 = **+1,4 voto de margem a cada 100 eleitores** (2,2 mi votos). É uniforme, aplicado a toda seção.
- Observado: Δ margem do termômetro.
- Régua com marcador do necessário e barra do observado: azul se acima, laranja se abaixo, com ✓/✗ e texto.
- O mapa no modo "Ritmo" mostra Δ margem − necessário por unidade.

### Decomposição de Bennet

É exata, sem resíduo. Com m = (L−O)/C:

- ΔM = Σ ΔC·(m1+m2)/2 → **efeito comparecimento**;
- \+ Σ Δm·(C1+C2)/2 → **efeito preferência**, que inclui a migração dos outros e de brancos/nulos.

## 6. Projeção e virada (modelo simples e explicável)

1. Mede o swing nas unidades **exatas**:
   - λ = logit(L2/(L2+O2)) − logit(L1/(L1+O1)): desloca a preferência de dois candidatos;
   - τ = ln(C2/C1): comparecimento;
   - ρ = (BN2/C2)/(BN1/C1).
2. Hierarquia com encolhimento: município ← UF ← região ← Brasil ← prior (sem mudança).
   - `est = w·bruto + (1−w)·pai`, com w = n/(n+k).
   - k (em seções) = 12 no município, 30 na UF, 40 na região e 30 no Brasil.
   - Com poucas seções, o número fica perto do pai.
3. Cada seção **não totalizada** parte do seu 1T: C2 = min(A, C1·e^τ); BN = C2·(BN1/C1)·ρ; L = (C2−BN)·logit⁻¹(logit(s0)+λ).
4. **Final projetado = agregado oficial totalizado + soma projetada das que faltam.**
5. Faixa de 90%: σ por município = w·√(σ_sys² + σ_sec²/n) + (1−w)·√(σ_pai² + h²).
   - Heterogeneidade h: região 0,10; UF 0,05; município 0,05 (em logit). Para τ: 0,015 / 0,01 / 0,02.
   - É propagada linearmente (d(L−O)/dλ = 2V·s(1−s)) e somada como totalmente correlacionada, o que é conservador.
   - No mock: ±6 p.p. com 5% apurado, ±2 com 60% e ±0,5 com 90%.
6. **Virada:**
   - "ocorreu" quando o sinal do placar oficial cruza zero no histórico (marca hora e %);
   - "projetada" quando o líder projetado ≠ líder atual e a faixa não cruza zero;
   - "provável" quando a faixa cruza zero;
   - "indefinido" no caso restante.
7. O gráfico usa o eixo **% de seções totalizadas** e mostra:
   - o placar oficial (traço forte);
   - a projeção ao longo da noite (traço fino + faixa);
   - o **caminho projetado** até 100% (tracejado, cone), interpolando os votos que faltam como mistura uniforme;
   - o ponto de virada projetado ou ocorrido.
   - Fundo azul/laranja em 9% para "quem está à frente"; crosshair com tooltip.

Na implementação, **tudo isso vem pronto do `analytics.json`**, porque o módulo `analytics/` faz o cálculo em Python. O modelo de lá vai além deste: zona → município → UF×tercil → UF → região → BR, com imputação por posto da ordem de chegada do 1T. Este JS é só um stand-in com a mesma forma de saída (§8.3).

## 7. Desempenho e tempo real

- Por snapshot, no mock: swings de zona + valores efetivos (419 mil seções) + exato + projeção + agregação levam **22–49 ms** em Node; hex + recolorir, menos de 15 ms.
- Na implementação o navegador não projeta; faz só valores efetivos + agregação, cerca de 15 ms para 499 mil seções.
- **Sem cintilar:**
  - os arrays `data` de cada camada são estáveis (todas as células da resolução, inclusive vazias com alfa 0);
  - a cada snapshot só mudam as cores (`updateTriggers`), com `transitions: {getFillColor: 700 ms}`;
  - células novas surgem com fade de alfa 0 → valor.
  - Os painéis são redesenhados no lugar, sem skeleton nem salto de layout.
- **Frescor:**
  - "Atualizado há N s" com ponto que pulsa a cada snapshot, mais o horário TSE dos dados.
  - Âmbar após 60 s ("Atrasado"); vermelho após 120 s ("Dados velhos"), conforme o contrato §5.
- **Memória:** cerca de 30 MB de typed arrays (38 campos × 66 mil locais × 8 B, mais as seções).
- **deck em modo sobreposto**, não intercalado:
  - O `H3HexagonLayer` não desenhou em `interleaved: true` (deck 9.0.38 + MapLibre 4.7.1); os polígonos sim.
  - Com sobreposição, os rótulos do mapa ficam sob o preenchimento semitransparente.
  - Vale testar deck 9.1.x intercalado (`?interleaved=1`) para pôr os rótulos por cima, como na referência.

### Modo TV

- **Ciclo** de cerca de 8 vistas de 24 s, refeito a cada volta:
  1. Brasil · ritmo (municípios);
  2. Brasil · placar oficial por município;
  3. as 2 regiões com mais urnas novas desde a última passada (Δ margem);
  4. onde Lula mais cresceu (voo ao município, hexágonos Δ margem);
  5. maior queda de abstenção (hexágonos Δ abstenção);
  6. acima/abaixo do ritmo (hexágonos);
  7. o que falta apurar (projeção).
- Cada vista troca camada, modo de cor e foco, voa com `fitBounds`/`flyTo` em 3 s e mostra uma legenda grande, com frase de números vivos e barra de tempo.
- **Pausa** em qualquer clique, roda, tecla ou movimento > 6 px do mouse; retoma após 60 s ocioso, com contagem no botão.
- O botão também liga/desliga o modo e "pula" a pausa.

## 8. Contrato de dados que a tela consome

Base: `docs/contracts.md` (vinculante). Os nomes abaixo usam L/O como lá; no código do protótipo, A = L e B = O.

### 8.1 Ao vivo (já no contrato) — o que a tela usa de cada arquivo

| Arquivo | Uso na tela |
|---|---|
| `manifest.json` | Polling a cada 15 s com `?v=version`. `generated_at` alimenta o frescor; `coverage.*` alimenta as barras "totalizadas" e "com boletim"; `health.stale_seconds` vira alerta. |
| `agg.json` → `br` | Placar oficial bruto (`l`, `o`, `st/ts`). |
| `agg.json` → `mun` | Choropleth municipal no modo "Margem 2T" e "% apurado". |
| `agg.json` → `zona` | % apurado por zona (tooltip). Fallback do swing de zona, se o analytics atrasar. |
| `sections.bin` | Int32 N×8 `[has_bu, aptos2, comp2, l2, o2, b2, n2, t_recv_min]`. Valores "observados na urna" e chip de status por seção. |
| `analytics.json` | Termômetro, ritmo, projeção, histórico e **swing por zona** (§8.3). |

### 8.2 Base estática para pontos e H3 (proposta — o agente da tela escreve a partir do parquet do ETL)

Tudo fica em `site/data/base/`, versionado por hash no nome, cache longo e gzip/brotli. Todos os arrays são **little-endian**.

**`secoes_base.bin`** — Uint16 N×9, alinhado ao `idx` de `secoes_index.csv`:

```
[aptos1, comp1, l1, o1, x1, b1, n1, secao, aptos2_cad]
```

- Cerca de 9 MB brutos, cerca de 3 MB com brotli.
- Qualquer contagem > 65535 é erro de dado; o export valida isso.

**`secoes_ref.bin`** — Int32 N×2:

```
[local_idx (−1 se sem coordenada), zona_idx]
```

Cerca de 4 MB brutos, < 1 MB comprimido (sequencial).

**`locais.bin`** — L registros, ordenados por `local_idx`, com dois arrays:

- Float32 L×2 `[lat, lng]`;
- Uint32 L×6 `[h3_r9_lo, h3_r9_hi, mun_idx, zona_idx, sec_start, n_secoes]`.

Observações:

- O H3 vai partido em 2×Uint32 e a tela usa `h3.splitLongToH3Index(lo, hi)`. Nada de uint64 em JSON (perde precisão).
- `sec_start` / `n_secoes` só valem se as seções de cada local forem contíguas numa permutação auxiliar.
- Alternativa mais simples: a tela inverte `local_idx` de `secoes_ref.bin`.
- Tamanho: 95,1 mil locais ≈ 3 MB.

**`locais_meta/{uf}.json`** — carregado sob demanda no clique/busca:

```json
{ "local_idx": ["nome", "endereco", "bairro", "nr_local"] }
```

**`zonas.json`** — índice das cerca de 6,3 mil zonas:

```json
[ { "k": "71072-0001", "uf": "sp", "cd_mun": 71072, "zona": 1, "n": 412,
    "a1": 0, "c1": 0, "l1": 0, "o1": 0, "x1": 0, "bn1": 0, "lat": 0, "lng": 0 } ]
```

Os totais do 1T alimentam o swing residual no fallback e a busca por zona.

**`municipios.json` + `municipios.topo.json`**:

- `municipios.json`: `{cd_mun, cd_mun_ibge, nome, uf, regiao, capital, lat, lng}` + totais 1T.
- `municipios.topo.json`: malha IBGE simplificada (TopoJSON, cerca de 3–5 MB), propriedade `cd_mun`.
- A tela converte para polígonos uma vez.

**Exterior (ZZ):** fica fora de mapa/H3 (sem coordenada). Entra só nos KPIs nacionais e em "Exterior" na tabela por região.

### 8.3 `analytics.json` — campos que a tela lê

Alinhado ao que `analytics/model.py` já emite (`thermometer.{br,regiao,uf}.{exact,estimated,best}`, `pace`, `projection`, `zones`, `mun`):

```jsonc
{
  "generated_at": "2026-10-25T19:02:11-03:00",
  "baseline_1t": { "l": 53879538, "o": 56104503, "margin_votes": -2224965, "aptos": 158745502 },
  "apurado":     { "l": 0, "o": 0, "st": 0, "ts": 499248, "pct_secoes": 50.0, "margin_pp_valid": -10.6,
                   "secoes_com_bu": 0, "zonas_completas": 0, "zonas_com_dados": 0 },
  "thermometer": {
    "br":     { "estimated": M, "exact": M, "best": M, "coverage_aptos_pct": 70.9, "coverage_exact_pct": 23.8 },
    "regiao": { "NE": { "...": "mesma forma" } },
    "uf":     { "sp": { "...": "mesma forma" } }
  },
  "pace": { "delta_req": 1.40, "delta_obs": 2.2, "req_capture": 0.60 },          // votos de margem por 100 aptos
  "projection": { "l_pct_valid": 50.9, "margin_pp_valid": 1.8, "margin_votes": 2150000,
                  "band_pp_valid": [-4.3, 7.9], "p_lula": 0.71,
                  "virada": { "status": "nao|projetada|provavel|incerta|ocorreu", "pct_secoes": 78, "hora": "19:42" } },
  "history": [ { "t": "19:02", "pct_secoes": 50.0, "margin_pp_valid": -10.6, "proj_margin_pp_valid": 1.25,
                 "band_pp_valid": [-1.7, 4.2], "delta_obs": 2.2 } ],
  "zones": { "71072-0001": { "dl": 1.2, "do": 0.4, "dbn": -0.1, "dabst": -0.6, "pct": 65.0, "src": "obs|est|shrunk" } },
  "mun":   { "71072": { "...": "mesma forma" } }
}
```

`M` é `_metrics` do analytics:

- `d_abst_pp`, `d_abst_n`, `d_bn_pp_comp`, `d_bn_n`;
- `d_l`, `d_o`, `d_l_100`, `d_o_100`;
- `d_margin_votes`, `delta` (por 100 aptos), `capture_l`, `pool`, `pool_sources`.

A tela mapeia assim:

| Campo do analytics | Elemento da tela |
|---|---|
| `delta` | número grande do termômetro |
| `d_abst_*` | tile Δ abstenção |
| `d_bn_pp_comp` | Δ brancos+nulos |
| `d_l` / `d_o` | Δ votos |
| `capture_l` | captura entre decididos |
| `pace.delta_req` × `delta_obs` | régua de ritmo |
| `history[]` | gráfico |
| `zones[k]` | valor "estimado pela zona" de cada seção sem BU (por 100 aptos) |

### 8.4 Pedidos / pontos a confirmar com o supervisor

1. **`zones` precisa ser o swing por apto da zona pronto para aplicar à base 1T da seção.** Hoje é o "swing encolhido usado na projeção", que serve. Falta também `pct_bu` por zona, para o tooltip.
   - Se `src = "shrunk"` (zona sem nada apurado), a tela **não** pinta a seção. Pintar só com prior esconderia a falta de dado.
2. **Barra de captura em 4 partes:** precisa de `bn` e `abst_out` no `M`. Hoje só há `capture_l` e `pool_sources`.
   - Proposta: `capture: {l, o, bn, abst}` como frações do pool.
3. **Decomposição (comparecimento × preferência):** pedir `decomp: {turnout, preference}` em votos no `M`. Os insumos são só a base e os 2T por seção/zona; o mock já calcula.
4. `history[]`: um ponto por publicação, podado para 1/min, como no contrato.
5. `aptos totalizados` por zona (`est` no `A` do `agg.json`) é necessário para o swing por apto da parte apurada. Confirmar que o coletor preenche.

## 9. Limitações conhecidas do protótipo

- Polígonos municipais Voronoi; nomes de municípios pequenos, locais e endereços inventados.
- O mapa usa um contorno grosseiro do Brasil (pontos sintéticos podem cair um pouco no mar ou na UF vizinha).
- A projeção e o termômetro em JS são stand-ins do módulo Python.
- Contrastes e paleta validados para as cores-padrão. Se alguém trocar as cores em Ajustes, não há revalidação automática; a sugestão é exibir aviso.
- Não há "tabela acessível" de cada gráfico além das tabelas por região/ranking. O texto de cada vista TV traz os números.

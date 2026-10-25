# Sala de guerra — Apuração do 2º turno presidencial 2026 (design)

Status: RASCUNHO (aguardando pesquisa de fontes) · Autor: Claude (supervisor) · 2026-10-24

## 1. Intenção e porquê

Na noite de 25/10/2026, o usuário e um grupo de amigos vão acompanhar a apuração do 2º turno presidencial numa única TV. A pergunta que querem responder:

> **A campanha do 2º turno do Lula deu certo?** Ele virou votos, reduziu a abstenção e reduziu brancos e nulos? Ele aponta para a vitória em relação ao que se viu no 1º turno?

O complicador é a ordem de chegada. Sul e Sudeste costumam chegar primeiro, e lá o Lula costuma perder. O placar bruto do começo da noite engana. A tela precisa dar um sinal confiável **mesmo com os primeiros dados**, porque compara cada urna apurada com ela mesma no 1º turno.

## 2. Público e uso

- Uso privado: uma TV numa sala, sem ninguém no mouse. Não será publicado na internet. (decisão do usuário)
- A tela roda sozinha no "Modo TV", alternando visões. Interagir pausa o ciclo. (decisão do usuário)
- Tudo roda localmente no Mac do usuário (macOS, Python 3.14, Node 26, DuckDB, uv, 32 GB). (decisão minha, viável porque o uso é privado)

## 3. Objetivos

1. **Termômetro da campanha.** Usa apenas as urnas já apuradas no 2T, comparadas às mesmas urnas no 1T. Mostra Δ abstenção, Δ brancos+nulos, Δ votos do Lula, Δ votos do adversário, Δ margem (em votos e em p.p.) e a captura líquida do "eleitorado em disputa". Recortes: Brasil, região e UF.
2. **Ritmo necessário × ritmo observado.** Quanto o Lula precisa melhorar, em média, sobre o 1T para empatar no país, contra quanto está melhorando nas urnas apuradas.
3. **Projeção de virada.** Margem final projetada, com faixa de incerteza, mais a curva de margem apurada × % apurado com o ponto de cruzamento.
4. **Mapa urna a urna.** Pontos nos locais de votação, cada um com suas seções. Camada H3 cuja resolução acompanha o zoom e que consolida as seções contidas em cada célula. Modos de cor: margem 2T, Δ margem, crescimento de Lula e do adversário, abstenção, Δ abstenção, acima/abaixo do ritmo.
5. **Atualização em tempo real** a partir do TSE. Indicador de frescor e alerta quando os dados param de chegar.

## 4. Não-objetivos

- Outros cargos (governador, senado etc.).
- Hospedagem pública, escala de tráfego, autenticação.
- Previsão estatística sofisticada (modelos bayesianos completos). A projeção é um modelo de *swing* explicável.

## 5. Anti-objetivos (fracasso mesmo se "funcionar")

- Tela que, no começo da noite, sugere "Lula perdendo" só porque o Sudeste chegou primeiro, sem contexto.
- Projeção com falsa certeza: faixa estreita demais, ou "virada garantida" com 5% apurado.
- Texto fixo que pressupõe ordem de apuração ou resultado (ex.: "Sul/Sudeste apuram primeiro"). **Pedido do usuário:** a ordem de amanhã pode ser outra, então toda frase factual da tela vem dos dados do momento: "mix do apurado" por região × peso no eleitorado. O ensaio inclui uma ordem diferente da real.
- Usar "% do Lula nos válidos subiu vs 1T" como sinal de sucesso. Isso sobe mecanicamente com a saída dos outros candidatos.
- Tela congelada sem avisar (dados velhos mostrados como atuais).
- Coletor que cai às 19h sem retomar, ou que leva bloqueio do TSE por excesso de requisições.

## 6. Restrições

- Prazo: pronto e **ensaiado** antes de 25/10, 17h BRT.
- Polidez com o TSE: concorrência limitada, backoff exponencial, respeito a cache (ETag/Last-Modified quando houver).
- Bibliotecas JS via CDN com versão fixa. Sem chaves de API (mapa base Carto ou OpenFreeMap).

## 7. Fontes de dados (verificadas — ver `research/*.md` e `docs/contracts.md`)

- **2T = 13 LULA × 22 FLÁVIO BOLSONARO**. No 1T, Flávio teve 47,03% e Lula 45,16%: Lula começa 2,22 mi de votos atrás. Ritmo necessário ≈ +1,4 voto de margem por 100 aptos. São 499.248 seções principais e 158,7 mi de aptos.
- **Base 1T por seção:** `votacao_secao_2026_BR` (votos) + `detalhe_votacao_secao_2026_BR` (aptos, comparecimento, brancos, nulos, horários). A soma bate voto a voto com o oficial. As seções agregadas são as mesmas nos dois turnos, então a unidade de comparação é a seção principal.
- **Coordenadas:** `eleitorado_local_votacao_2026`, que já traz o 2T. Faltam coordenadas em 0,37% das seções (fallback: centroide do município). O exterior (ZZ) não tem coordenadas.
- **Ao vivo (2T: eleição 6258, pleito 3221):**
  - Agregados oficiais BR/UF/município/**zona** (`*-u.json`) chegam com ~1 min de atraso. São a "verdade" do progresso.
  - O progresso por município vem do `{uf}-ab.json`.
  - O BU por seção vem de `aux.json` + `-bu.dat` (ASN.1 2026), com o decodificador validado. **Os BUs atrasam de minutos a ~4 h (SP no 1T)**. Por isso:
    - (a) o termômetro "exato" usa seções com BU **e zonas 100% totalizadas** (agregado de zona do 2T × soma do 1T da zona). Zonas parciais entram como "estimado";
    - (b) o mapa tem uma camada **coroplética de municípios** alimentada pelos agregados oficiais. Ela é o padrão no início da noite, e H3 e pontos se completam conforme os BUs chegam.
- **Prioridade (decisão do usuário, 24/10 ~21h):** o objetivo é monitorar em tempo real. A **espinha dorsal são os agregados por zona eleitoral** (~6,3 mil zonas, com ~1 min de atraso). O decodificador de BU **continua** (o usuário pediu explicitamente: "vamos com o decodificador") como enriquecimento urna a urna em segundo plano: entra quando chega, nunca atrasa os agregados.
  - Na tela, cada seção tem um valor "efetivo" do 2T: o observado no BU, quando existe; senão, o swing da sua zona aplicado à base 1T da seção ("estimado pela zona").
  - H3 e pontos agregam esses valores e mostram quanto é observado e quanto é estimado.
- CORS aberto e cache de CDN de 60 s. Os `bweb` (dados abertos) só saem dias depois: não servem ao vivo.
- **Cores (minha decisão, seguindo a referência hubpolitico de que o usuário gosta):** Lula azul, Flávio laranja, configuráveis.

## 8. Arquitetura (decisão minha)

```
TSE ──► coletor (Python, local) ──► site/data/live/*  ──► tela estática (navegador, localhost) ──► TV
            ▲                                                ▲
  base 1T (ETL hoje à noite) ──► site/data/base/* ───────────┘
```

1. **ETL da base (`etl/`)**: DuckDB + h3 (Python). Gera, para o front:
   - índice de seções (UF, município, zona, seção → id inteiro sequencial);
   - locais de votação (lat/lng, H3 na resolução mais fina, lista de seções);
   - valores do 1T por seção;
   - nomes de municípios e UFs.

   Formato: o que o contrato de dados do protótipo definir (`prototype/DESIGN.md`). Por ser localhost, o tamanho não é crítico; o tempo de parse é.
2. **Coletor (`collector/`)**:
   - Loop assíncrono. Descobre quais seções foram totalizadas, baixa e decodifica os BUs (só cargo Presidente) e grava o estado de forma durável, para retomar após uma queda.
   - Publica snapshots atômicos (escreve num arquivo temporário e renomeia) com um manifesto: versão, horário, contagens e saúde.
   - Expõe métricas de saúde para a tela: última coleta bem-sucedida, erros, fila pendente.
   - Também coleta os agregados oficiais (BR/UF/município) como referência e plano B.
3. **Tela (`site/`)**: evolução do protótipo. Faz polling do manifesto a cada ~15–30 s e calcula no cliente as agregações (H3 via `cellToParent`), o termômetro, o ritmo e a projeção.
4. **Ensaio (`mock/`)**: um servidor local que imita os endpoints do TSE. Ele serve BUs reais do 1T 2026 na ordem real de chegada (timestamp de recebimento do BU), de forma acelerada. Testa o encanamento ponta a ponta sem martelar o TSE.
5. **Backtest 2022 (opcional, se os dados forem baixáveis a tempo)**: replay do 2T 2022 contra o 1T 2022 na ordem real de chegada. Mede quão cedo e com que erro a projeção acertaria o resultado, e calibra a faixa de incerteza.
6. **Operação**:
   - Um único comando sobe tudo: coletor, servidor estático e `caffeinate` para o Mac não dormir.
   - Reinício automático do coletor.
   - Logs em arquivo.

## 9. Definições das métricas

Notação por seção *s*. Sufixo 1 = 1º turno, 2 = 2º turno. L = Lula, O = adversário, X = demais candidatos do 1T, BN = brancos+nulos, A = aptos, C = comparecimento, Abs = A − C.

- Conservação (aptos iguais entre os turnos): `L2+O2+BN2+Abs2 = L1+O1+X1+BN1+Abs1`.
- **Δ margem em votos**: `ΔM = (L2−O2) − (L1−O1)`.
- **Margem por eleitor apto**: `μ = (L−O)/A`. Usada no ritmo e na projeção porque não depende de comparecimento nem de válidos.
- **Eleitorado em disputa (líquido)**: `P = ΔL + ΔO = X1 + (BN1−BN2) + (Abs1−Abs2)`. A **captura líquida do Lula** é `ΔL / P`, ou seja, "de cada 100 votos que mudaram de lugar, X foram para o Lula". É saldo líquido, não migração individual. Rotular assim.
- **Ritmo necessário**: `δ_req = Σ_todas(O1−L1) / Σ_todas A`, o ganho uniforme de margem por eleitor que leva ao empate nacional. Pode ser negativo: "Lula pode perder até X e ainda vencer".
- **Ritmo observado**: `δ_obs = Σ_apuradas ΔM / Σ_apuradas A`. Também por UF e região.
- **Projeção**: cada seção não apurada recebe `p_k2 = p_k1 + δ̂_k(área)` para k ∈ {L, O, BN, Abs}, em proporção de aptos. Os votos de X do 1T somem, por construção dos δ. O resultado passa por clamp ≥ 0 e é renormalizado para somar 1.
  - δ̂ vem de um encolhimento hierárquico: município → UF → região → Brasil.
  - Fórmula: `δ̂_área = (n·δ_área + k·δ̂_pai)/(n+k)`, com n = aptos apurados na área e k uma constante a calibrar.
  - Final projetado = apurado real + projetado das não apuradas.
- **Faixa de incerteza**: calibrada pelo backtest 2022, se houver. Sem backtest, usar heurística com a dispersão do swing entre municípios dentro da UF, escalada pelos aptos não apurados. Nunca mostrar "virada garantida" com menos de um patamar mínimo de apuração. (patamar: decisão do builder após o backtest)

## 10. Tela

Ver `prototype/` e `prototype/DESIGN.md`. Os destaques são o termômetro, o ritmo e a projeção. Layout 16:9 1920×1080, tema escuro, legível do sofá.

## 11. Fica a cargo do builder

Formatos binários e JSON exatos, a constante de encolhimento, a ordem de polling e a concorrência, a estrutura de pastas interna e o roteiro do Modo TV.

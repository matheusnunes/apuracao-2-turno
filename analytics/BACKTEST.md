# Backtest 2022: projeção e termômetro na noite do 2º turno de 2022

Gerado por `uv run python -m analytics.backtest` em 2026-10-24 21:28 (312 s). Mesmo código do modo ao vivo (`analytics.model.compute`). Os números abaixo saem do script; o texto é meu.

## Montagem

- **Base:** 1T 2022 por seção (472.075 seções principais), no formato de `data/base/secoes.parquet`. L = 13 Lula, O = 22 Bolsonaro.
- **Verdade:** 2T 2022. Margem final **+2,139,645** votos (**+1.805 p.p.** dos válidos). δ final = **-2.59** votos de margem por 100 aptos vs 1T (Lula piorou a margem: −2,59).
- **Noite simulada:** a cada passo (2 min até 21h, depois 10 min), os agregados de **zona** somam as seções com `DT_PRIM_TOT_PARCIAL_HOR_TSE ≤ t`. É o caminho principal. Os BUs entram como enriquecimento, conforme o cenário:
  - `zonas`: só agregados de zona (nenhum BU)
  - `producao`: zonas + BUs esparsos: recebimento + 2–30 min (SP: 35 min–4 h), coletor com fila
  - `bu_pesado`: zonas + BUs com atraso de 35 min–4 h em todas as UFs
  - `bu_imediato`: zonas + BU disponível no instante da totalização (otimista)
  - `ordem_1t`: estresse: votos do 2T 2022 chegando na ordem/horário do 1T 2022 (reescalado), BUs como em `producao`
  - `ordem_1t_zonas`: estresse, pior caso: ordem do 1T e só agregados de zona (nenhum BU)
- **Placar bruto:** cruza em **18:44:48 com 68,32% das seções**, igual ao histórico oficial.

## Parâmetros escolhidos

- Imputação da parte apurada-sem-BU das zonas parciais: **posto suave, ρ = 0.75** (ver tabela D).
- Peso da parte imputada no swing: w_est = 1.
- Encolhimento zona → município → UF×tercil de inclinação 1T → UF → região → BR, com k = 80,000 / 200,000 / 400,000 / 800,000 / 2,000,000 / 800,000 aptos. É 4× a unidade da grade (tabela F).
- τ (DP do efeito de cada nível no swing de margem por apto, estimado no 2T 2022): zona 0.0040, mun 0.0088, UF×tercil 0.0066, UF 0.0047, região 0.0123, BR 0.0259.
- A faixa é montada em três passos:
  1. **Faixa calibrada** = 1.1 × (1,645·σ_modelo + sensibilidade da imputação + viés).
     - A sensibilidade da imputação é |M − M_alt| para ρ = 0.5 e para posto exato.
     - O viés vale 0.4/100 × fração imputada × aptos não apurados.
     - O multiplicador 1.1 dá 90% de cobertura na união dos cenários, inclusive o teste de estresse.
  2. **Faixa publicada** = max(1.5 × calibrada; 0.3/100 × aptos não apurados).
     - O alargamento ×1,5 e o piso foram exigidos pela revisão de arquitetura.
  3. P(Lula) = Φ(M̂/σ), com σ = meia-faixa publicada / 1,645.
- Fases (contrato §6.4):
  - `pre` abaixo de 2% das seções.
  - `trend` (`can_call`) quando a faixa inteira está de um lado, há ≥30% das seções apuradas e toda região (exceto ZZ) tem ≥5% dos aptos apurados.
  - `final` quando a apuração está completa ou matematicamente decidida.

## A. Cenário de produção (zonas + BUs esparsos e atrasados): a noite em checkpoints

| % seções | hora | placar bruto | projeção (p.p.) | projeção (votos) | erro (p.p.) | faixa 90% publicada (p.p.) | dentro? | P(Lula) | fase | δ_obs UF-pond. (verdade −2,59) | virada proj. (% seções) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 2% | 17:19 | -0.33 mi | +2.00 | +2.36 mi | +0.19 | [-3.27, +7.26] | sim | 0.73 | early | -2.29 | 46% |
| 6% | 17:27 | -0.42 mi | +1.81 | +2.14 mi | +0.01 | [-2.24, +5.86] | sim | 0.77 | early | -2.48 | 61% |
| 11% | 17:35 | -0.51 mi | +1.71 | +2.03 mi | -0.09 | [-1.54, +4.97] | sim | 0.81 | early | -2.64 | 80% |
| 20% | 17:49 | -0.73 mi | +1.97 | +2.34 mi | +0.17 | [-0.29, +4.24] | sim | 0.92 | early | -2.49 | 71% |
| 30% | 18:03 | -0.76 mi | +1.80 | +2.14 mi | -0.00 | [+0.06, +3.54] | sim | 0.96 | trend | -2.60 | 71% |
| 41% | 18:17 | -0.61 mi | +1.76 | +2.09 mi | -0.04 | [+0.46, +3.06] | sim | 0.99 | trend | -2.61 | 67% |
| 50% | 18:27 | -0.36 mi | +1.78 | +2.11 mi | -0.03 | [+0.72, +2.84] | sim | 1.00 | trend | -2.60 | 63% |
| 61% | 18:37 | -0.16 mi | +1.79 | +2.12 mi | -0.02 | [+0.99, +2.59] | sim | 1.00 | trend | -2.58 | 66% |
| 71% | 18:47 | +0.09 mi | +1.80 | +2.13 mi | -0.01 | [+1.21, +2.39] | sim | 1.00 | trend | -2.59 | — |
| 80% | 18:57 | +0.56 mi | +1.85 | +2.19 mi | +0.04 | [+1.47, +2.23] | sim | 1.00 | trend | -2.55 | — |
| 91% | 19:11 | +1.17 mi | +1.81 | +2.15 mi | +0.01 | [+1.64, +1.98] | sim | 1.00 | trend | -2.57 | — |
| 95% | 19:25 | +1.49 mi | +1.80 | +2.14 mi | -0.00 | [+1.72, +1.89] | sim | 1.00 | trend | -2.58 | — |

## B. Erro da margem projetada (p.p. dos válidos) por cenário e cobertura da faixa

Os passos usados vão de 2% a 99% das seções. A cobertura é a fração dos passos com a verdade dentro da faixa.

| cenário | 5% | 10% | 20% | 30% | 50% | 70% | 90% | máx \|erro\| | cobertura calibrada | cobertura publicada | cobertura publicada (≥10%) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `producao` | +0.01 | -0.09 | +0.17 | -0.00 | -0.03 | -0.01 | +0.01 | 0.19 | 100% | 100% | 100% |
| `zonas` | +0.17 | +0.00 | +0.13 | -0.10 | -0.12 | -0.09 | -0.02 | 0.28 | 100% | 100% | 100% |
| `bu_pesado` | +0.17 | +0.00 | +0.13 | -0.10 | -0.12 | -0.08 | -0.01 | 0.28 | 100% | 100% | 100% |
| `bu_imediato` | -0.11 | -0.03 | +0.07 | +0.03 | +0.00 | -0.01 | -0.01 | 0.15 | 100% | 100% | 100% |
| `ordem_1t` | -0.56 | -0.48 | -0.36 | -0.35 | -0.23 | -0.14 | -0.11 | 0.62 | 84% | 100% | 100% |
| `ordem_1t_zonas` | -0.69 | -0.63 | -0.52 | -0.51 | -0.33 | -0.19 | -0.13 | 0.69 | 86% | 100% | 100% |
| `(todos, agrupado)` | — | — | — | — | — | — | — | 0.69 | 95% | 100% | 100% |

- **Leitura honesta da cobertura.**
  - **Ordem real de 2022** (producao, zonas, bu_pesado, bu_imediato): o erro pontual ficou bem abaixo da faixa. Cobertura calibrada: 100%, 100%, 100%, 100%. Nessa noite a faixa foi conservadora.
    - Os parâmetros (ρ, k) foram ajustados nesta mesma noite, então esse erro pontual é in-sample.
  - **Teste de estresse** (ordem_1t, ordem_1t_zonas): os mesmos votos do 2T chegando na ordem do 1T. Cobertura calibrada: 84%, 86%.
    - Ali a ordem dentro da zona se preserva 100%, o pior caso para a imputação suave.
    - O multiplicador 1.1 foi escolhido como o quantil de 90% de |erro|/meia-faixa na união dos cenários, numa grade de 3 min. Esse conjunto de calibração inclui o estresse sem BU.
  - **Agrupando todos os passos de todos os cenários:** cobertura calibrada 95% e publicada 100%.
  - A faixa **publicada** (×1,5 e piso) cobre ~100% em todos os cenários. É conservadora de propósito, e P(Lula) sai dessa faixa, logo também é conservadora.
  - Com uma única eleição de referência, "90%" é uma meta de projeto, não uma frequência medida.

## C. Quando a projeção acertou o vencedor e não mudou mais

O placar bruto cruzou (virada) às **18:44:48, com 68,32% das seções**.

| cenário (a partir de 2%) | P(Lula) > 50% estável | P(Lula) ≥ 90% estável | faixa exclui empate (estável) | `can_call` = Lula (estável) | chamadas erradas |
|---|---|---|---|---|---|
| `producao` | 17:19 (2%) | 17:45 (17%) | 18:03 (30%) | 18:03 (30%) | 0 |
| `zonas` | 17:19 (2%) | 18:07 (33%) | 18:23 (46%) | 18:23 (46%) | 0 |
| `bu_pesado` | 17:19 (2%) | 18:07 (33%) | 18:21 (44%) | 18:21 (44%) | 0 |
| `bu_imediato` | 17:19 (2%) | 17:37 (12%) | 17:45 (17%) | 18:03 (30%) | 0 |
| `ordem_1t` | 17:31 (2%) | 18:13 (33%) | 18:19 (40%) | 18:19 (40%) | 0 |
| `ordem_1t_zonas` | 17:31 (2%) | 18:23 (45%) | 18:29 (53%) | 18:29 (53%) | 0 |

## D. Viés da imputação das zonas parciais (termômetro), antes e depois da correção por posto

Cada célula é o viés do δ (votos de margem por 100 aptos, vs 1T) contra a verdade das **mesmas seções**:

- **parte imputada:** a parte apurada sem BU das zonas parciais;
- **melhor estimativa:** exata + imputada, que é o número que a sala vê.

O cenário é `zonas`, só agregados, que é o pior caso. A revisão de arquitetura mediu −0,8 (pró-rata) e +0,3 (posto).

| método | 5% | 10% | 20% | 30% | 50% |
|---|---|---|---|---|---|
| pró-rata (média da zona): parte imputada | -0.45 | -0.81 | -0.92 | -0.97 | -0.79 |
| pró-rata (média da zona): melhor estimativa | -0.43 | -0.76 | -0.84 | -0.84 | -0.61 |
| posto exato (1T, ρ=1): parte imputada | +0.27 | +0.36 | +0.28 | +0.17 | +0.10 |
| posto exato (1T, ρ=1): melhor estimativa | +0.26 | +0.34 | +0.26 | +0.15 | +0.08 |
| posto suave ρ=0.75 (escolhido): parte imputada | +0.22 | +0.10 | -0.01 | -0.13 | -0.13 |
| posto suave ρ=0.75 (escolhido): melhor estimativa | +0.21 | +0.09 | -0.01 | -0.11 | -0.10 |

Com o método escolhido, os outros componentes da melhor estimativa ficaram assim (viés em p.p. dos aptos):

| | 5% | 10% | 20% | 30% | 50% |
|---|---|---|---|---|---|
| Δ abstenção | +0.05 | +0.07 | +0.04 | +0.02 | +0.01 |
| Δ brancos+nulos | +0.01 | +0.01 | +0.01 | -0.00 | -0.00 |
| fração imputada da cobertura | 95% | 94% | 91% | 87% | 77% |

No cenário `producao`, com os mesmos métodos, a melhor estimativa ficou assim:

| método | 5% | 10% | 20% | 30% | 50% |
|---|---|---|---|---|---|
| pró-rata (média da zona) | -0.42 | -0.60 | -0.46 | -0.44 | -0.32 |
| posto exato (1T, ρ=1) | +0.11 | +0.13 | +0.13 | +0.11 | +0.07 |
| posto suave ρ=0.75 (escolhido) | +0.08 | -0.00 | +0.01 | -0.04 | -0.03 |

## E. Virada projetada (onde o placar bruto cruza) vs real: 68,32%, 18:44

A ordem do restante segue o horário do 1T de cada seção, reescalado pelo ritmo observado da UF no 2T.

| agora (% seções) | hora | virada projetada (% seções) | ETA |
|---|---|---|---|
| 2% | 17:19 | 46% | 18:05 |
| 6% | 17:27 | 61% | 18:02 |
| 11% | 17:35 | 80% | 18:16 |
| 20% | 17:49 | 71% | 18:22 |
| 30% | 18:03 | 71% | 18:30 |
| 41% | 18:17 | 67% | 18:33 |
| 50% | 18:27 | 63% | 18:30 |
| 61% | 18:37 | 66% | 18:37 |

Mesmo com margens oráculo, a ordem de chegada erra o ponto de virada em cerca de ±10 p.p. de seções. O ETA sai adiantado de 10 a 40 min. Na tela, mostrar como "por volta de".

## F. Grade de ajuste (erro pontual, sem faixa)

A grade usa os cenários `producao` e `zonas`, com checkpoints de 5% a 90%.

A escala de k multiplica a unidade (zona 20 mil, mun 50 mil, UF×tercil 100 mil, UF 200 mil, região 500 mil, BR 200 mil).

| imputação | ρ | w_est | escala k | RMSE (p.p.) | erro médio | máx \|erro\| |
|---|---|---|---|---|---|---|
| soft | 0.75 | 1 | 4 | 0.094 | -0.006 | 0.168 |
| soft | 0.75 | 1 | 8 | 0.095 | -0.012 | 0.255 |
| soft | 0.8 | 1 | 8 | 0.099 | +0.030 | 0.287 |
| soft | 0.75 | 0.5 | 4 | 0.099 | -0.009 | 0.284 |
| soft | 0.75 | 0.5 | 2 | 0.100 | +0.001 | 0.189 |
| soft | 0.8 | 0.5 | 4 | 0.103 | +0.028 | 0.315 |
| soft | 0.7 | 1 | 4 | 0.105 | -0.050 | 0.215 |
| soft | 0.8 | 1 | 4 | 0.107 | +0.038 | 0.240 |
| soft | 0.7 | 0.5 | 2 | 0.107 | -0.039 | 0.205 |
| soft | 0.7 | 1 | 2 | 0.111 | -0.032 | 0.211 |
| soft | 0.7 | 0.5 | 1 | 0.111 | -0.022 | 0.200 |
| soft | 0.75 | 1 | 2 | 0.112 | +0.013 | 0.226 |
| … melhor pró-rata | — | 0.5 | 16 | 0.612 | -0.457 | 1.148 |
| … melhor posto exato | — | 0.5 | 4 | 0.220 | +0.174 | 0.464 |

## Ressalvas

1. **Uma única noite de referência.** O ρ e o k foram escolhidos nela.
   - A faixa foi alargada de propósito (×1,5 e piso) para absorver isso.
   - Erro pontual máximo: 0.19 p.p. na ordem real (`producao`) e 0.62 p.p. no teste com outra ordem de chegada (`ordem_1t`).
2. **2026 ≠ 2022.** Em 2026 o adversário recebe mais do pool, Lula começa atrás e os "outros" do 1T são outros (Cury, Renan, Caiado).
   - O τ veio de 2022. A heterogeneidade do swing em 2026 pode ser maior, por exemplo em GO com Caiado.
3. **Correlação de ordem dentro da zona.** A imputação suave supõe que ela se repete com ρ ≈ 0,75 (cópula gaussiana, ajustada em 2022).
   - A sensibilidade (ρ = 0,5 e ρ = 1) entra na faixa.
4. **BUs.** Com BUs imediatos (`bu_imediato`), o erro cai bastante. Os BUs são enriquecimento e não são necessários para a projeção.
5. **Virada projetada:** é indicativa (±10 p.p. de seções).


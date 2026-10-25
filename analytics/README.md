# analytics/: termômetro, ritmo, projeção e backtest 2022

Dono: agente analytics. Lê a base do 1T (`data/base/secoes.parquet`) e as saídas do coletor (`site/data/live/manifest.json` → `agg.<dv>.json` + `sections.<dv>.bin`). Publica **`site/data/live/analytics.json`** com escrita atômica (tmp + `os.replace`).

Contrato: `docs/contracts.md` §4 e §6. O §6.4 é o schema mínimo. Aqui só se **acrescentam** campos; nada é renomeado.

## Rodar

```bash
# ao vivo (domingo): recalcula quando data_versions.agg/sections mudam; ~0,5 s por ciclo
uv run python -m analytics.run --live-dir site/data/live --interval 20

# um ciclo só (diagnóstico)
uv run python -m analytics.run --live-dir site/data/live --once

# backtest 2022 → analytics/BACKTEST.md (~15–20 min; --quick só checkpoints)
uv run python -m analytics.bt2022_build     # uma vez: monta analytics/cache/bt2022_secoes.parquet (5 s)
uv run python -m analytics.backtest

# teste ponta a ponta sem coletor/mock (2T sintético a partir do 1T 2026, em pasta separada)
uv run python -m analytics.fake_live --out-dir analytics/cache/fake_live --at 18:30
uv run python -m analytics.run --live-dir analytics/cache/fake_live --once
uv run python -m analytics.fake_live --out-dir analytics/cache/fake_live --play --speed 60 &   # replay
uv run python -m analytics.run --live-dir analytics/cache/fake_live --interval 5
```

Log em `analytics/logs/run.log`. O loop nunca cai: um ciclo com erro é logado e repetido no próximo.

- **Histórico:** é retomado do `analytics.json` existente e só é zerado quando o `source`/`scope` do manifest muda.
  - Se a apuração "voltar" mais de 1 p.p. sem troca de fonte (coletor reiniciado com estado vazio), o ponto não entra no histórico e o histórico é mantido.
  - Para recomeçar um ensaio do zero: `--reset-history`.
- **Antes da primeira publicação do coletor**, sem manifest, já sai um `analytics.json` em fase `pre`, com a base do 1T e o ritmo necessário.

## Módulos

| Arquivo | O que faz |
|---|---|
| `base.py` | Carrega o parquet de seções em arrays numpy, com índices zona, município, UF×tercil, UF e região. Normaliza `t_tot1` (epoch → segundos desde 00:00 BRT do 1T). |
| `model.py` | **Funções puras**: `prepare` (partes comparáveis por zona + imputação), `project` (encolhimento + projeção + faixa), `run_model` (com sensibilidade da imputação), `thermometer`, `virada_projetada`, `compute` (dicionário publicado). |
| `live_io.py` | Manifest → agg/sections versionados → `Live`. |
| `run.py` | Loop ao vivo, histórico e escrita atômica. |
| `sim2022.py`, `backtest.py`, `bt2022_build.py` | Backtest 2022 com o **mesmo** `compute`. |
| `fake_live.py` | Fabrica manifest/agg/sections a partir do 1T 2026, para ensaio. |

## Modelo (resumo; números do backtest em BACKTEST.md)

Unidade de comparação: o par **município-zona** (`"{cd_mun}-{zona:04d}"`), com os agregados oficiais de zona como caminho principal. BUs por seção são enriquecimento.

1. **Partes comparáveis por zona**
   - **Exata:** zona 100% totalizada (agregado 2T × 1T da zona) ou seções com BU (par exato).
   - **Estimada:** numa zona parcial, o apurado sem BU (agregado − BUs) contra uma **base 1T imputada**.
   - A imputação é por **posto suave**:
     - Peso de cada seção sem BU: P(já apurada) = Φ((c − ρ·Φ⁻¹(q))/√(1−ρ²)), com ρ = 0,75.
     - q é o posto da seção na ordem de chegada do 1T dentro da zona.
     - c é escolhido para que o peso total iguale os aptos apurados (`est`) da parte sem BU.
   - Viés no backtest, comparado aos métodos alternativos:

     | Imputação | Viés (por 100 aptos) |
     |---|---|
     | Pró-rata | −0,8 |
     | Posto exato | +0,3 |
     | Posto suave (escolhido) | ±0,1–0,2 |

   - **Melhor estimativa** = exata + estimada. É o número grande do termômetro. A exata fica como conferência.
   - Fallbacks:
     - Quando o agregado do **município** está à frente da soma das suas zonas (zonas faltando ou defasadas), vale o município. A diferença é rateada entre as zonas pelos aptos ainda não apurados de cada uma (`src` interno `mun_split`). Essas zonas só contam como completas se os aptos apurados também fecharem.
     - Sem nenhum agregado, valem só os BUs.
     - Se o agregado estiver defasado em relação aos BUs, valem os BUs.
2. **Swing por apto** (ΔL, ΔO, ΔBN, Δabstenção vs a proporção do 1T, cada turno com seus aptos)
   - É encolhido em hierarquia: zona → município → UF×tercil de inclinação 1T → UF → região → BR.
   - Fórmula: δ̂ = (D + k·δ̂_pai)/(N + k), com k em aptos (80 mil / 200 mil / 400 mil / 800 mil / 2 mi / 800 mil).
3. **Projeção por zona:** apurado oficial mais o restante (`te − est`).
   - O restante usa a composição 1T das seções ainda não apuradas (peso 1 − P(já apurada)) + δ̂ da zona.
   - Clamp ≥ 0 e renormalização: X vai a zero no 2T.
4. **Faixa de 90%**
   - Calibrada = 1,1 × (1,645·σ + sensibilidade + viés).
     - σ vem dos efeitos de nível τ²·k/(N+k)·(restante na área)².
     - A sensibilidade é |M − M_alt|, com a imputação refeita para ρ = 0,5 e para posto exato.
     - O viés vale 0,4/100 × fração imputada × aptos não apurados.
   - Publicada = max(1,5 × calibrada; 0,3/100 × aptos não apurados).
   - P(Lula) = Φ(M̂/σ), com σ = meia-faixa publicada / 1,645.
5. **Fases**
   - `pre`: abaixo de 2% das seções.
   - `trend`: `can_call = true` quando a faixa inteira está de um lado, há ≥30% das seções e cada uma das regiões N, NE, CO, SE e S tem ≥5% dos aptos apurados. ZZ (exterior) fica fora da regra (contrato §6.4).
   - `final`: só com números **oficiais** (agregado BR). Exige ≥99,5% das seções, ou a diferença oficial maior que os aptos oficiais restantes (`te − est`). Também exige que a projeção tenha o mesmo sinal do placar oficial.
   - `call` é sempre o sinal da projeção. `can_call` usa o % oficial de seções e a cobertura oficial das UFs por região.
   - `early`: o resto.
6. **Virada projetada:** onde o placar bruto deve cruzar.
   - O restante é ordenado pelo horário de totalização do 1T de cada seção, reescalado pelo ritmo observado da UF no 2T.
   - É indicativa: ±10 p.p. de seções, ETA adiantado 10–40 min no backtest.

## `analytics.json`: schema (v1)

Convenções (§6.3):
- margem = L − O, positivo = Lula à frente;
- **δ = votos de margem por 100 aptos**;
- percentuais de 0 a 100;
- faixas `[lo, hi]` de 90%;
- taxas com os aptos de cada turno (A1 no 1T, A2 no 2T);
- votos são inteiros; floats arredondados a 4 casas;
- `null` = indisponível.

Campos marcados com † são extras além do mínimo §6.4.

### Topo
| Campo | Tipo | |
|---|---|---|
| `schema_version` † | int | 1 |
| `input_version` | int | `data_versions.agg` do manifest usado |
| `computed_at` | ISO | quando o analytics calculou |
| `inputs` † | obj | `{manifest_version, agg_version, sections_version, generated_at, source, data_time}`; `data_time` = dt/ht do agregado BR (hora da totalização no TSE) |
| `status`, `baseline_1t`, `apurado`, `pace`, `thermometer`, `projection`, `zones`, `mun`, `history` | | abaixo |
| `virada_observada` † | obj\|null | `{t, pct_secoes, leader, before:{t, pct_secoes}}`, última troca de sinal do placar bruto no histórico |
| `model` † | obj | `{params, compute_ms, notes}` |

### `status`
`phase` (`pre|early|trend|final`), `pct_secoes`, `pct_aptos`, `can_call` (bool), `call` (`"L"|"O"|null`).

Extras †:

| Campo | Tipo | |
|---|---|---|
| `reason` | str | texto curto em PT |
| `leader_projected` | `"L"\|"O"` | |
| `band_one_side` | bool | |
| `decided` | bool | matematicamente decidido |
| `min_region_pct_aptos` | float | menor % de aptos apurados entre N, NE, CO, SE e S (ZZ fora) |
| `region_pct_aptos` | `{N, NE, CO, SE, S, ZZ}` | |
| `inconsistent_inputs` | [uf] | UFs cujas zonas/municípios não batem com o agregado oficial da UF. Nessas UFs, zonas, municípios e BUs são ignorados e o oficial da UF é rateado pelas zonas por aptos. A UF entra na lista em três casos: (a) Σ zonas tem ≥5% das seções/aptos da UF a mais que o oficial; (b) com cobertura igual (±2%), a margem por apto difere > 2 p.p.; (c) hora do arquivo de zona > hora do BR + 10 min. Se o excesso passar de 1% dos aptos do país, não há chamada |
| `warnings` | [str] | avisos legíveis (hoje: as inconsistências acima) |
| `mix_regiao` | `{N…ZZ: Mix}` | composição do que já foi apurado (descreve a ordem real de chegada) |
| `mix_uf` | `{ac…zz: Mix}` | idem por UF |
| `mix_caption` | str | frase pt-BR gerada dos números. Ex.: "Apurado até agora está super-representado no Sul (27% do apurado × 15% do eleitorado)" ou "Apurado equilibrado entre as regiões" |

`Mix` = `{pct_apurado, share_apurado, share_eleitorado, sobre_rep}`:
- `pct_apurado`: % das seções da unidade já totalizadas;
- `share_apurado`: fatia da unidade nos aptos já apurados (0–100);
- `share_eleitorado`: fatia nos aptos totais (0–100);
- `sobre_rep` = `share_apurado / share_eleitorado` (`null` sem apurado).

A legenda cita a região (exceto ZZ) com maior distorção em p.p.: super se `sobre_rep` ≥ 1,25 e a diferença ≥ 3 p.p.; sub se ≤ 0,8 e a diferença ≥ 3 p.p.

### `baseline_1t`
`l`, `o`, `margin_votes`, `aptos`.

Extras †: `x`, `bn`, `abst`, `comp`, `l_pct_valid`, `o_pct_valid`.

### `apurado` (placar bruto oficial: agregado BR do TSE)
`l`, `o`, `vb`, `tvn`, `c`, `a`, `te`, `est`, `margin_votes`, `l_pct_valid`, `o_pct_valid`.

Extras †:

| Campo | |
|---|---|
| `st`, `ts` | seções totalizadas / total |
| `pct_secoes`, `pct_aptos` | |
| `margin_pp_valid` | |
| `secoes_com_bu` | |
| `zonas_completas`, `zonas_total`, `zonas_com_dados` | |
| `zonas_sem_agregado` | zonas sem agregado próprio atualizado (sem arquivo de zona, ou defasadas em relação ao município); usam o rateio do município |

### `pace` (δ por 100 aptos)
| Campo | |
|---|---|
| `delta_req` | Σ(O1−L1)/ΣA1 × 100 (≈ +1,40). Ganho uniforme de margem para empatar |
| `delta_rem` | −(M2_apurado + M1_não_apurado)/A2_não_apurado × 100: ritmo ainda necessário no que falta |
| `delta_obs` | **ponderado por UF**: Σ_UF A_UF·δ̂_UF / ΣA, com δ̂_UF encolhido (UF sem dados herda região/BR) |
| `delta_obs_band` | `[lo, hi]` = δ_obs ± meia-faixa da projeção / A × 100 |
| `delta_obs_pooled_best` † / `delta_obs_pooled_exact` † | δ bruto (não reponderado) das partes cobertas; não usar como manchete no começo |
| `delta_proj` † | δ nacional implícito na margem projetada |
| `capture_needed` † | ½ + déficit_1T/(2·P_projetado): fração do pool líquido projetado (ΔL+ΔO) que Lula precisa |
| `capture_projected` † | ΔL/(ΔL+ΔO) projetado (nacional) |
| `uf` † | `{sp: {delta_shrunk, delta_best, coverage_aptos_pct}}` |
| `regiao` † | `{S: {delta_shrunk, delta_best, coverage_aptos_pct}}` |

### `thermometer`
`{br: T, regiao: {N|NE|CO|SE|S|ZZ: T}, uf: {ac…zz: T}}`.

**T** = `{coverage_aptos_pct, exact: M|null, estimated: M|null, best: M|null}` + `coverage_exact_pct` †:
- `best` = exata + estimada, o número que a sala vê;
- `exact` = só zonas completas + BUs, a conferência;
- `estimated` = só a parte imputada;
- `null` = sem dados.

**M** (sobre exatamente a parte coberta, 1T das mesmas seções):

| Campo | Definição |
|---|---|
| `d_abst_pp` | 100·(AB2/A2 − AB1/A1) (negativo = abstenção caiu) |
| `d_abst_n` | AB2 − AB1 (eleitores) |
| `d_bn_pp` | 100·(BN2/A2 − BN1/A1), **p.p. dos aptos** |
| `d_bn_n` | BN2 − BN1 |
| `d_l`, `d_o` | L2 − L1, O2 − O1 (votos) |
| `d_margin_votes` | ΔL − ΔO |
| `delta` | 100·((L2−O2)/A2 − (L1−O1)/A1): δ observado por 100 aptos |
| `capture_l` | ΔL/(ΔL+ΔO), captura líquida entre os que mudaram de lugar. **Só em BR/região e quando \|P\| ≥ max(50 mil; 2% dos aptos cobertos)**; senão `null` (sempre `null` em UF) |
| `capture` † | `{l, o, bn, abst}`, frações do **pool bruto** = X1 + BN1 + max(0, ΔC) (somam 1): l = ΔL, o = ΔO, bn = BN2, abst = max(0, −ΔC). Calculado por zona-parte e somado |
| `decomp` † | `{turnout, preference}` em votos. Bennet com m = (L−O)/C: ΔC·(m1+m2)/2 + Δm·(C1+C2)/2 = `d_margin_votes`, por zona-parte |
| `coverage_aptos_pct` † | aptos cobertos por esta parte / aptos da área |
| `aptos2`, `aptos1` † | |
| `abst1_pct`, `abst2_pct` † | |
| `bn1_pct_comp`, `bn2_pct_comp`, `d_bn_pp_comp` † | BN como % do comparecimento (forma usual na TV) |
| `d_l_100`, `d_o_100` † | ΔL, ΔO por 100 aptos (proporção de cada turno) |
| `d_comp_n` † | C2 − C1 |
| `l1, o1, x1, l2, o2, margin1, margin2` † | |
| `pool` † | **líquido** P = ΔL + ΔO = X1 + (BN1−BN2) + (AB1−AB2) + (A2−A1) |
| `pool_gross` † | denominador de `capture` |
| `pool_sources` † | `{x1, bn_drop, abst_drop, aptos_change}`: decomposição de `pool` |

Nunca usamos "% do Lula nos válidos subiu vs 1T" como sinal: isso sobe mecanicamente com a saída dos outros.

### `projection`
`l`, `o`, `margin_votes`, `margin_pp_valid`, `band_votes [lo, hi]`, `band_pp_valid [lo, hi]`, `p_lula`, `virada_pct_secoes`.

- `p_lula`: limitado a [0,001; 0,999], então nunca vale 0 ou 1; `null` na fase `pre`.
- `virada_pct_secoes`: 0–100, nunca acima de 100. É `null` se não houver virada pela frente, ou se ela só viria no fim (ver `virada_fim`).

Extras †:

| Campo | |
|---|---|
| `l_pct_valid`, `o_pct_valid`, `valid`, `bn`, `abst`, `comp`, `abst_pct` | |
| `sigma_votes` | σ publicada (= meia-faixa/1,645) |
| `sigma_model_votes` | calibrada, antes do ×1,5 |
| `imputed_share` | fração da informação que vem de imputação |
| `remaining_aptos`, `remaining_pct` | |
| `virada_fim` | bool: o líder projetado é outro, mas o cruzamento só viria no fim (≥99% das seções) ou não acontece pela ordem prevista. Nesse caso `virada_pct_secoes` e o ETA são `null` |
| `virada` | `{projected, pct_secoes, eta_s, eta "HH:MM:SS", fim, leader_now, leader_final}` |
| `uf` | `{sp: {l, o, margin_votes, l_pct_valid, margin_pp_valid, pct_aptos}}`: projeção final por UF |
| `mun` | `{"71072": {margin_votes, margin_pp_valid, l_pct_valid}}`: projeção final por município (apurado + restante projetado) |

### `zones` e `mun`
`zones: {"71072-0001": Z}`, `mun: {"71072": Z}`.

**Z** = `{dl, do, dbn, dabst, pct, src, pct_bu}`:

| Campo | |
|---|---|
| `dl`, `do`, `dbn`, `dabst` | swing **por 100 aptos** vs a proporção do 1T (encolhido, o mesmo usado na projeção) |
| `pct` | % dos aptos da zona já apurados (`est/te`) |
| `src` | `obs` (zona completa ou com BU), `est` (só parte imputada) ou `shrunk` (nada apurado: herdado do pai). **A tela não pinta `shrunk`** |
| `pct_bu` † | % dos aptos da zona cobertos por BU decodificado |
| `raw` † | `{dl, do, dbn, dabst}`: swing **observado, sem encolhimento** (por 100 aptos), sobre a parte apurada (exata + imputada); `null` sem dado apurado. Numa zona completa, é o swing exato. A tela usa `raw` quando `pct ≥ 99,95`; a projeção usa os valores encolhidos |

Para estimar uma seção sem BU, a tela usa a base 1T da seção:

```
p_k2 = max(0, k1/A1 + d_k/100)   para k ∈ {L, O, BN, AB}
```

Depois renormaliza para Σ = 1 (os outros do 1T vão a zero) e multiplica pelos aptos 2T. Onde houver BU, usa o BU.

### `history[]`
Um ponto por minuto (o último do minuto) e só com `pct_secoes > 0`. Campos: `t` (ISO, hora da totalização no TSE), `pct_secoes`, `margin_apurado`, `margin_proj`, `band [lo, hi]` (votos), `delta_obs`, `delta_rem`.

Extras †:

| Campo | |
|---|---|
| `pct_aptos` | |
| `margin_apurado_pp`, `margin_proj_pp`, `band_pp` | |
| `p_lula`, `phase`, `delta_obs_band`, `input_version` | |
| `virada: true` | no ponto em que o placar bruto trocou de sinal |

## Ordem de chegada: nada é suposto

Nenhuma parte do modelo ou da faixa supõe que Sul/Sudeste (ou qualquer região) chegam primeiro.

- **Termômetro, δ_obs e projeção** usam só o que já foi apurado, com encolhimento para o pai. O `delta_obs` é reponderado pelo eleitorado de cada UF, justamente para não depender da ordem.
- **Faixa:** cresce com os aptos restantes em áreas sem dados próprios. A calibração usou a ordem real de 2022 e um teste de estresse com outra ordem (`ordem_1t`).
- **Regra de chamada:** exige ≥5% dos aptos apurados em **todas** as regiões.
- **Única suposição de ordem: dentro de cada zona**, a imputação por posto suave usa a ordem de chegada do 1T da própria zona, com correlação parcial ρ = 0,75. A sensibilidade a isso entra na faixa.
- A virada projetada usa a ordem do 1T de cada seção dentro da UF, reescalada pelo ritmo **observado** de cada UF no 2T.
- O `status.mix_*` mostra na tela a composição real do apurado.

## Desempenho e robustez

- **Desempenho:** ~0,4–0,5 s por ciclo com 499 mil seções (três imputações para a sensibilidade). `analytics.json` ≈ 1,1 MB.
- **Entradas:** sempre pelo manifest (nomes versionados). Se `index.n_rows` ≠ base, os BUs são ignorados e o erro vai para o log.
- **Chaves de zona/município desconhecidas:** vão para o log. Aceita `cd_mun` com ou sem zeros à esquerda.

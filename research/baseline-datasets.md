# Baseline estático: 1º turno 2026 por seção + geolocalização (+ backtest 2022)

Levantamento feito em 24/10/2026, entre 20h15 e 21h BRT. Os arquivos foram baixados do CDN do TSE e perfilados com DuckDB 2.0.0. Todos os números abaixo vêm dos arquivos baixados, não de memória.

- Scripts: `research/scripts/00_download.sh` (download), `01_load_profile_db.sql` (carga 2026), `02_load_2022.sql` (carga 2022) e `03_profile_queries.sql` (queries de profiling e reconciliação).
- Metadados do portal (CKAN): `data/raw/ckan_metadata/` (JSON dos pacotes, headers HTTP e tamanhos dos arquivos BU).

---

## 1. Resumo / decisão

| Necessidade | Fonte escolhida | Por quê |
|---|---|---|
| Votos por candidato por seção (Presidente, 1T) | `votacao_secao_2026_BR.zip` | É **nacional** (inclui ZZ), traz só o cargo Presidente e uma linha por seção × votável. Bate 100% com o oficial. |
| Aptos, comparecimento, abstenção, brancos, nulos e **horário de chegada** por seção | `detalhe_votacao_secao_2026.zip` → `detalhe_votacao_secao_2026_BR.csv` | Uma linha por seção principal, inclusive as não instaladas. Tem `DT_RECEBIMENTO_BU_HOR_TSE` e `DT_PRIM_TOT_PARCIAL_HOR_TSE` em 100% das linhas. |
| Coordenadas, local de votação, agregação, aptos do 2T | `eleitorado_local_votacao_2026.zip` → `..._BRASIL.csv` | Já traz **NR_TURNO=1 e NR_TURNO=2** (gerado em 24/10/2026 15:28). Tem lat/long por seção e o mapa agregada→principal. |
| Totais oficiais para conferência | `Relatorio_Resultado_Totalizacao_2026_BR.zip` (PDF) + `detalhe_votacao_munzona_2026_BR.csv` | O relatório oficial do SISTOT e o detalhe por município/zona. |
| Candidatos | `consulta_cand_2026.zip` → `consulta_cand_2026_BR.csv` | Número, nome, partido e situação no turno. |

O **Boletim de Urna (bweb)** não é necessário. Ele tem os mesmos votos, e `DT_BU_RECEBIDO` é idêntico a `DT_RECEBIMENTO_BU_HOR_TSE`: verificado em 21.418/21.418 seções de PE. Como soma 5,08 GB, baixei só PE e ZZ como amostra.

---

## 2. Datasets: URLs, caminhos locais e tamanhos

Base: `https://cdn.tse.jus.br/estatistica/sead/`. Todos os zips foram verificados com `unzip -t`.

### 2026, 1º turno (eleição 6257, "Eleição Geral Federal 2026", 04/10/2026)

| Dataset | URL (relativa à base) | Zip | CSV usado (descompactado) | Local |
|---|---|---|---|---|
| Votação por seção, Presidente, BR | `odsele/votacao_secao/votacao_secao_2026_BR.zip` | 161,6 MB | `votacao_secao_2026_BR.csv` 1,05 GB, 3.800.511 linhas | `data/raw/votacao_secao_2026_BR/` |
| Detalhe da apuração por seção | `odsele/detalhe_votacao_secao/detalhe_votacao_secao_2026.zip` | 176,9 MB | `detalhe_votacao_secao_2026_BR.csv` 176 MB, 499.248 linhas (cargo 1). Também extraído o `_PE.csv`. O `_BRASIL.csv` (900 MB, todos os cargos) ficou no zip. | `data/raw/detalhe_votacao_secao_2026/` |
| Eleitorado por local de votação | `odsele/eleitorado_locais_votacao/eleitorado_local_votacao_2026.zip` | 167,6 MB (Last-Modified 24/10/2026 18:40 GMT, ETag `9fded1c-65e9a6d40e6cc`) | `eleitorado_local_votacao_2026_BRASIL.csv` 427 MB, 1.034.358 linhas (517.179 × 2 turnos) | `data/raw/eleitorado_local_votacao_2026/` |
| Relatório de totalização BR | `odsele/relatorio_resultado_totalizacao/Relatorio_Resultado_Totalizacao_2026_BR.zip` | 287 KB | `6257_BR_BRASIL_2026_T1_PRESIDENTE.pdf` | `data/raw/relatorio_resultado_totalizacao_2026_BR/` |
| Detalhe por município/zona | `odsele/detalhe_votacao_munzona/detalhe_votacao_munzona_2026.zip` | 2,5 MB | `detalhe_votacao_munzona_2026_BR.csv`, 6.292 linhas | `data/raw/detalhe_votacao_munzona_2026/` |
| Candidatos | `odsele/consulta_cand/consulta_cand_2026.zip` | 3,2 MB | `consulta_cand_2026_BR.csv`, 32 linhas | `data/raw/consulta_cand_2026/` |
| Boletim de Urna 1T (amostra) | `eleicoes/eleicoes2026/buweb/bweb_1t_{UF}_051020261403.zip` | PE 205 MB, ZZ 1,0 MB | `bweb_1t_PE_...csv` 1,3 GB e `bweb_1t_ZZ_...csv` 5 MB | `data/raw/bweb_1t_2026/` |

Lista completa do BU 1T 2026: 28 arquivos, 5,08 GB zipados. O padrão de nome é `bweb_1t_{UF}_051020261403.zip` para AC…TO e ZZ, e os tamanhos estão em `ckan_metadata/bweb2026_sizes.txt`. Os maiores são SP 1,40 GB, RJ 536 MB, MG 489 MB, BA 325 MB e RS 271 MB. O `bweb_2t_*` de 2026 ainda não existe (404).

Encontrados e não baixados (não são necessários para o baseline):
- `votacao_secao_2026_{UF}.zip`: todos os cargos por UF (SP tem 767 MB).
- `votacao_candidato_munzona_2026.zip` (456 MB) e `perfil_eleitor_secao_2026_{UF}.zip`.
- `transf_temporaria_secao_2026.zip`.
- **`correspesp/CESP_2t_{UF}_241020261231.zip`**: correspondências esperadas do 2T, publicadas hoje às 12:31. Interessa ao agente de tempo real.
- Não existem para 2026 (404): `Historico_Totalizacao_Presidente_BR_1T_2026.zip` e `secoes_agregadas_2026.zip`.

### Formato (todos os arquivos)
- Encoding **Latin-1** (o leiame diz "Latin 1"), separador `;`, aspas `"`, fim de linha CRLF e cabeçalho na 1ª linha.
- `#NULO`/`#NULO#` indica texto vazio, e `-1` indica número vazio.
- Leitura recomendada no DuckDB: `read_csv(..., delim=';', quote='"', encoding='latin-1', all_varchar=true)`, com cast explícito depois.
- **Lat/long em 2026 usam vírgula decimal** (`"-21,71924788"`). Em 2022 usam ponto.
- **CD_MUNICIPIO:** no `eleitorado_local_votacao` é string de 5 dígitos com zeros à esquerda (`"00019"`). Em `votacao_secao`/`detalhe_votacao_secao` é inteiro sem padding (`10006`). **Fazer o join com `CAST(... AS INT)`.**
- Os horários estão no horário de Brasília. Em `detalhe_*` o formato é `dd/mm/yyyy HH:MM:SS`; no bweb é `yyyy-mm-dd HH:MM:SS`.

---

## 3. Colunas usadas

**votacao_secao_2026_BR.csv** (uma linha por seção × votável; **votáveis com 0 votos não aparecem**, então é preciso preencher zeros ao pivotar):
`NR_TURNO, CD_ELEICAO, SG_UF, CD_MUNICIPIO, NM_MUNICIPIO, NR_ZONA, NR_SECAO, CD_CARGO (=1), NR_VOTAVEL, NM_VOTAVEL, QT_VOTOS, SQ_CANDIDATO, NR_LOCAL_VOTACAO, NM_LOCAL_VOTACAO, DS_LOCAL_VOTACAO_ENDERECO`.
Brancos aparecem como `NR_VOTAVEL=95` e nulos como `NR_VOTAVEL=96`.

**detalhe_votacao_secao_2026_BR.csv** (uma linha por seção principal):
`SG_UF, CD_MUNICIPIO, NR_ZONA, NR_SECAO, QT_APTOS, QT_COMPARECIMENTO, QT_ABSTENCOES, QT_VOTOS_NOMINAIS, QT_VOTOS_BRANCOS, QT_VOTOS_NULOS, QT_VOTOS_LEGENDA, QT_VOTOS_ANULADOS_APU_SEP, NR_LOCAL_VOTACAO, DT_RECEBIMENTO_BU_HOR_TSE, DT_PRIM_TOT_PARCIAL_HOR_TSE, DS_ORIGEM_VOTO, ST_SECAO_INSTALADA, ST_SECAO_ANULADA, CD_MODELO_URNA`.

**eleitorado_local_votacao_2026_BRASIL.csv** (uma linha por seção × turno):
`NR_TURNO, DT_ELEICAO, SG_UF, CD_MUNICIPIO, NM_MUNICIPIO, NR_ZONA, NR_SECAO, CD_TIPO_SECAO_AGREGADA (1=Principal, 2=Agregada), NR_SECAO_PRINCIPAL, NR_LOCAL_VOTACAO, NM_LOCAL_VOTACAO, CD_TIPO_LOCAL/DS_TIPO_LOCAL (Convencional, Voto em trânsito, Preso provisório, Temporário), DS_ENDERECO, NM_BAIRRO, NR_CEP, NR_LATITUDE, NR_LONGITUDE, QT_ELEITOR_SECAO, QT_ELEITOR_ELEICAO_FEDERAL, NR_LOCAL_VOTACAO_ORIGINAL, NM_LOCAL_VOTACAO_ORIGINAL`.
- `QT_ELEITOR_ELEICAO_FEDERAL` é o **apto para Presidente** depois de agregações e TTE. Vale zero nas agregadas e é igual a `QT_APTOS` do resultado em 100% das seções.
- `QT_ELEITOR_SECAO` é o número de eleitores vinculados originalmente à seção.

**bweb (se for usado):** `DT_BU_RECEBIDO, QT_APTOS, QT_COMPARECIMENTO, QT_ABSTENCOES, DS_TIPO_VOTAVEL, NR_VOTAVEL, QT_VOTOS, DS_SECOES_AGREGADAS, DT_ABERTURA, DT_ENCERRAMENTO, DT_EMISSAO_BU, NR_URNA_EFETIVADA, CD_CARGA_*`.

---

## 4. Candidatos 1T 2026 e reconciliação com os totais oficiais

Fonte oficial: o PDF do SISTOT, "Resultado em 05/10/2026 12:51:05", emitido em 07/10/2026. A soma por seção (`votacao_secao_2026_BR`) bate **exatamente** com o oficial, voto a voto.

| Nº | Candidato (urna) | Partido | Votos (soma seções) | Oficial | % válidos | Situação |
|---|---|---|---|---|---|---|
| 22 | Flávio Bolsonaro (Flavio Nantes Bolsonaro) | PL | 56.104.503 | 56.104.503 | 47,03% | **2º turno** |
| 13 | Lula (Luiz Inácio Lula da Silva) | PT (coligação "Brasil Pronto pra Mais") | 53.879.538 | 53.879.538 | 45,16% | **2º turno** |
| 70 | Escritor Augusto Cury | AVANTE | 3.448.569 | 3.448.569 | 2,89% | não eleito |
| 14 | Renan Santos | MISSÃO | 2.675.887 | 2.675.887 | 2,24% | não eleito |
| 55 | Ronaldo Caiado | PSD | 2.605.148 | 2.605.148 | 2,18% | não eleito |
| 30 | Zema | NOVO | 326.488 | 326.488 | 0,27% | não eleito |
| 80 | Samara | UP | 122.911 | 122.911 | 0,10% | não eleito |
| 16 | Hertz Dias | PSTU | 43.103 | 43.103 | 0,04% | não eleito |
| 27 | Clariana Barão | DC | 40.043 | 40.043 | 0,03% | não eleito |
| 21 | Edmilson Costa | PCB | 22.693 | 22.693 | 0,02% | não eleito |
| 35 | Veterinário Wilson Grassi | DEMOCRATA | 16.881 | 16.881 | 0,01% | não eleito |
| 29 | Rui Costa Pimenta | PCO | 15.024 | 15.024 | 0,01% | não eleito |
| 28 | Leonardo Avalanche (substituiu Pablo Marçal) | PRTB | 5.246 | contado como **nulo técnico** | n/a | não entra nos válidos |

Os vices, segundo o PDF, são Alfredo Gaspar de Mendonça Neto (chapa 22) e Geraldo Alckmin (chapa 13). No `consulta_cand`, o 2T tem só 13 e 22.

| Agregado nacional | Soma por seção | Oficial (PDF / munzona) |
|---|---|---|
| Aptos | 158.745.502 | 158.745.502 (ver nota TTE abaixo) |
| Comparecimento | 125.275.835 (= soma de todos os votos do `votacao_secao`) | 125.275.835 (78,92%) |
| Abstenção | 33.469.244 | 33.469.244 (21,08%) |
| Votos válidos | 119.300.788 | 119.300.788 |
| Brancos (95) | 2.300.798 | 2.300.798 |
| Nulos na urna (96) | 3.669.003 | 3.669.003 |
| Nulos técnicos (votos no 28) | 5.246 | 5.246 → total de nulos 3.674.249 |
| Seções | 517.179 = 499.248 principais (499.207 funcionaram + 41 não instaladas) + 17.931 agregadas | idem |

- **Nota TTE (texto do próprio portal):** 39 eleitores em transferência temporária ficaram vinculados também à seção de origem. Os arquivos mostram 158.745.502 aptos, mas o número efetivo é 158.745.463. Os totais oficiais usam 158.745.502.
- **Exterior (ZZ)** no oficial: 2.715 seções (1.310 funcionaram, 41 não instaladas, 1.364 agregadas), 916.534 aptos e 341.766 votantes (37,31%). Bate com a soma das seções ZZ.
- `QT_VOTOS_NOMINAIS` no detalhe por seção soma 119.306.034, que é igual a válidos + 5.246 do nº 28. Portanto o nº 28 conta em "nominais", mas não em válidos.
- Origem dos BUs: 499.126 vieram de urna eletrônica, 50 de RED (recuperador de dados), 31 do Sistema de Apuração (voto em cédula) e 41 são "Totalização" (não instaladas, 0 votantes).
- 499.206 seções têm linhas de voto: são as 499.207 que funcionaram menos a ZZ Ciudad Guayana (z1 s91), que teve 0 comparecimento.

---

## 5. Geocoordenadas: `eleitorado_local_votacao_2026`

- Existe para 2026 e **já contém o 2º turno** (NR_TURNO=2, DT_ELEICAO 25/10/2026), com 517.179 seções em cada turno.
- Coordenadas são **por local de votação**: nenhum local tem mais de uma coordenada ou mais de um nome.

| Métrica (Brasil, sem ZZ) | 1T | 2T |
|---|---|---|
| Seções principais | 497.897 | 497.897 |
| Seções principais **sem coordenada** (lat/long = `-1`) | **1.736 (0,35%)** | **1.827 (0,37%)** |
| Aptos nessas seções | 494.887 (0,31% do eleitorado BR) | 522.508 (0,33%) |
| Municípios com ao menos uma seção sem coordenada | 291 | 307 |
| Municípios 100% sem coordenada | 1 | 1 |
| Todas as seções (com agregadas) sem coordenada | 2.018 | 2.109 |
| Coordenadas lixo (fora do bbox do Brasil) | 8 seções em 3 locais | 8 |
| Coordenadas com valor `0` ou não parseáveis | 0 | 0 |

- Os 8 casos lixo estão em 3 locais: MG Santo Antônio do Jacinto, "E.E. de Catajás" (48,27 / 17,48); PA Ipixuna do Pará, "EMEF Tiradentes" (24,57 / 90,40); e RS São Pedro do Sul, com lat = long = −83,76.
- **Locais de votação:** 95.558 no total, sendo 95.100 no Brasil e 458 no exterior. A chave é UF+município+zona+NR_LOCAL_VOTACAO. Há 93.767 pares de coordenadas distintos no Brasil, porque o mesmo prédio pode servir zonas diferentes.
  - Locais BR sem coordenada: 803 no 1T e 832 no 2T.
  - 288 coordenadas são compartilhadas por mais de um local. Em 74 delas os locais têm 3 ou mais endereços diferentes (377 locais), o que pode indicar fallback de geocodificação. No máximo, 22 locais caem no mesmo ponto.
- **Seções por local** (BR, principais): mínimo 1, mediana 4, média 5,29, p95 14, máximo 49. Contando agregadas, o máximo é 51.
- Por tipo, as seções sem coordenada no 2T são 1.070 "Temporário", 952 "Convencional", 72 "Preso provisório" e 15 "Voto em trânsito".
- UFs com mais seções sem coordenada (1T, principais): PA 413 (1,98%), **AP 179 (9,35%)**, BA 163, SP 162, PI 144, RS 125, MT 109, CE 102.
- **Exterior (ZZ):** 100% das 2.715 seções estão com `-1`, ou seja, sem coordenadas.

---

## 6. Chaves de join e ressalvas

**Chave da seção:** `(SG_UF, CAST(CD_MUNICIPIO AS INT), CAST(NR_ZONA AS INT), CAST(NR_SECAO AS INT))`.
- O `NR_SECAO` só é único dentro da zona, e a mesma zona pode abranger vários municípios. Por isso o município entra na chave.
- No `eleitorado_local_votacao`, a chave é única por turno, então é preciso filtrar `NR_TURNO`.

**Resultados do join medido:**
- `detalhe_votacao_secao` (1T) → `eleitorado_local_votacao` (NR_TURNO=1): **499.248/499.248 casam (100%)**. Todas são seções principais, e `QT_APTOS == QT_ELEITOR_ELEICAO_FEDERAL` em 100% delas.
- `votacao_secao` → `detalhe_votacao_secao`: as 499.206 seções do arquivo de votos estão no detalhe.
- **Não use `NR_LOCAL_VOTACAO` do arquivo de resultado para buscar coordenadas.** Em 421 seções ele difere do local do `eleitorado_local_votacao`. Em 416 delas, o resultado traz o **local original** e o `eleitorado_local` traz o local efetivo, geralmente "Temporário". Busque as coordenadas pela chave da seção e pelo turno.

**Códigos de município:** são **códigos TSE, não IBGE**. Por exemplo, São Paulo é 71072 (IBGE 3550308), Recife é 25313 e São Gonçalo é 58971.
- Para mapa por H3 e por ponto não é preciso IBGE, porque as coordenadas já vêm prontas.
- Só é preciso IBGE se for usar polígonos ou centroides municipais como fallback para seções sem coordenada. Nesse caso será preciso uma tabela de correspondência TSE↔IBGE. Uma candidata é o diretório de municípios da Base dos Dados (`id_municipio_tse`). **Não verifiquei essa fonte.**
- No Brasil há 5.571 "municípios" TSE: os 5.570 do IBGE mais Fernando de Noronha.

**Seções agregadas:**
- São 17.931, idênticas no 1T e no 2T: 0 mudanças de tipo ou de seção principal.
- Elas **não aparecem nos arquivos de resultado**. Os votos dos seus eleitores estão na seção principal (`NR_SECAO_PRINCIPAL`). 16.142 principais têm ao menos uma agregada, com até 10 por principal.
- O resultado **não reporta a seção agregada separadamente**. No BU, a principal lista as agregadas em `DS_SECOES_AGREGADAS`.
- Para o mapa, use o local e a coordenada da **principal**, que é onde a urna está. Em 2.109 agregadas o local listado difere do da principal, e 1.371 têm coordenada diferente.

**O 2T usa as mesmas seções que o 1T?** Sim. O `eleitorado_local_votacao` de 24/10 tem o mesmo conjunto de 517.179 seções e a mesma agregação nos dois turnos. As diferenças são:
- **Local de votação mudou em 394 seções**, e a coordenada em 389: 21 se moveram mais de cerca de 1 km, 122 perderam a coordenada e 31 ganharam. O tipo de local mudou em 220 seções.
- **Aptos (`QT_ELEITOR_ELEICAO_FEDERAL`) mudaram em 55.722 seções principais**, com soma de |Δ| de 74.692, máximo de 84 e p99 de 7, por causa da TTE, que é solicitada por turno. O total nacional fica igual (158.745.502).
  - **Use os aptos do 2T (NR_TURNO=2) como denominador de comparecimento no 2T.**
- As 41 seções não instaladas no 1T (todas no exterior) podem funcionar no 2T, e outras podem deixar de funcionar.

**Exterior:**
- Aparece como `SG_UF='ZZ'`, `NR_ZONA=1`, e `CD_MUNICIPIO` é o código TSE da cidade no exterior (186 cidades; Lisboa é 29955 e Miami é 30112).
- Vota só para Presidente, por isso o `detalhe_votacao_secao_2026_ZZ.csv` vem vazio. As seções ZZ estão no arquivo `_BR`.
- Não tem coordenadas.

---

## 7. Horário de chegada para o replay do 1T (pedido extra a)

- **`detalhe_votacao_secao_2026_BR.csv`** tem duas colunas de horário, ambas no horário de Brasília ("Hora TSE"), com **cobertura de 100%** (499.248 seções, inclusive as 41 não instaladas):
  - `DT_RECEBIMENTO_BU_HOR_TSE`: recebimento do BU, de 04/10 17:08:44 a 05/10 02:58:30.
  - `DT_PRIM_TOT_PARCIAL_HOR_TSE`: entrada na totalização parcial, de 17:08:57 a 05/10 02:58:39.
- No bweb a coluna equivalente é **`DT_BU_RECEBIDO`** (`yyyy-mm-dd HH:MM:SS`), com 100% de cobertura. Em PE ela é idêntica a `DT_RECEBIMENTO_BU_HOR_TSE` em 21.418/21.418 seções. O bweb também tem `DT_ABERTURA`, `DT_ENCERRAMENTO` e `DT_EMISSAO_BU`.
- **Recomendação:** ordenar o replay por `DT_PRIM_TOT_PARCIAL_HOR_TSE`, que é quando a seção entrou no total divulgado. Em 2022 o recebimento chegou a acontecer de madrugada, no exterior, mas a totalização só abre às 17h.
- **Validação com 2022:** reconstruí o 2T 2022 seção a seção com `DT_PRIM_TOT_PARCIAL_HOR_TSE`. A virada sai às 18:44:48, com 68,32% das seções, **exatamente** como no snapshot oficial `Historico_Totalizacao` (último momento com Bolsonaro à frente às 18:44:48; Lula à frente às 18:44:50, também com 68,32%).
- **Curva do 1T 2026** (válidos acumulados por horário):

| Horário | % seções | Flávio | Lula |
|---|---|---|---|
| 17:15 | 2,3% | 49,45% | 41,96% |
| 18:00 | 20,4% | 51,14% | 40,76% |
| 18:30 | 48,2% | 50,19% | 41,65% |
| 19:00 | 72,9% | 49,19% | 42,66% |
| 20:45 | 98,2% | 47,33% | 44,81% |

  - Flávio liderou o tempo todo depois dos primeiros minutos: **não houve virada no 1T 2026**.
  - Há um **platô entre 19:15 e 19:45** (de 84,3% para 85,0% das seções) e a contagem retoma às 20:00. Vale checar se foi uma pausa real na divulgação antes de usar como cenário.

---

## 8. 2022 para o backtest da "virada" (pedido extra b)

O total ficou abaixo de 2 GB, então baixei (em `data/raw/2022_*`):

| Dataset | URL (relativa à base) | Zip | Conteúdo |
|---|---|---|---|
| Detalhe por seção 2022 (**com horários**) | `odsele/detalhe_votacao_secao/detalhe_votacao_secao_2022.zip` | 243,8 MB | `_BR.csv` 331 MB, regerado em 15/02/2024. Tem 1T e 2T, cargo 1, 472.075 seções por turno, e `DT_RECEBIMENTO_BU_HOR_TSE`/`DT_PRIM_TOT_PARCIAL_HOR_TSE` em **100%** das linhas. |
| Votação por seção, Presidente, BR 2022 | `odsele/votacao_secao/votacao_secao_2022_BR.zip` | 271,3 MB | CSV de 1,59 GB, 1T e 2T, 5.380.736 linhas. Todos os campos vêm entre aspas. |
| Eleitorado por local 2022 | `odsele/eleitorado_locais_votacao/eleitorado_local_votacao_2022.zip` | 24,0 MB | 407 MB, regerado em 30/09/2024, NR_TURNO 1 e 2, 496.856 seções por turno. Lat/long com **ponto** decimal. |
| Histórico de totalização Presidente | `eleicoes/eleicoes2022/Historico_Totalizacao_Presidente_BR_{1T,2T}_2022.zip` | 1,6 MB + 0,8 MB | Snapshots oficiais nacionais, quase segundo a segundo: 8.437 no 1T e 5.286 no 2T, com votos acumulados por candidato. |
| Seções agregadas 2022 | `odsele/secoes_agregadas/secoes_agregadas_2022.zip` | 1,2 MB | 24.163 pares agregada→principal. |
| BU 2T 2022 (28 UFs) | `eleicoes/eleicoes2022/buweb/bweb_2t_{UF}_311020221535.zip` | 69,7 MB no total | 1,3 GB descompactado. `DT_BU_RECEBIDO` em 100% das 472.028 seções instaladas. |
| BU 1T 2022 (**não baixado**) | `eleicoes/eleicoes2022/buweb/bweb_1t_{UF}_051020221321.zip` | **1.457,8 MB** no total (SP 404 MB, MG 141 MB, PE 58 MB) | Dispensável, porque o detalhe por seção de 2022 já tem os horários do 1T. |

- **O 2T 2022 casa 1:1 com o 1T 2022?** Sim. São 472.075 seções nos dois turnos, sem nenhuma seção exclusiva de um turno. Os aptos diferem em 49.710 seções (TTE) e o local em 1.384.
- **Coordenadas 2022:** existem, mas a cobertura é bem pior que em 2026. No 1T faltam coordenadas em 25.911 das 472.075 seções com resultado (5,5%); no 2T, em 25.958. O exterior não tem coordenadas.
- **Totais 2022 (soma por seção):**
  - 1T: Lula 57.259.504, Bolsonaro 51.072.345, Tebet 4.915.423, Ciro 3.599.287.
  - 2T: Lula 60.345.999, Bolsonaro 58.206.354, brancos 1.769.678, nulos 3.930.765. Aptos 156.454.011, igual ao `QT_APTOS_TOTAL` do histórico.
- **Viradas 2022 (histórico oficial):**
  - 1T: 20:00:39, com 68,60% das seções.
  - 2T: 18:44:50, com 68,32% das seções. A reconstrução por seção reproduz o mesmo instante.

---

## 9. Riscos e caveats

1. **O `eleitorado_local_votacao_2026` foi regerado hoje (24/10, 15:28)** e pode mudar de novo antes do 2T, por exemplo com locais temporários de última hora. Rebaixe no domingo de manhã e compare com o ETag anotado acima, e faça o diff por seção.
2. **Fallback geográfico.** Cerca de 0,35% das seções do Brasil (cerca de 1,8 mil, 0,33% dos aptos) e 100% do exterior estão sem coordenada. É preciso uma regra para elas: geocodificar endereço ou CEP, usar o centroide do município (o que exige a tabela TSE↔IBGE) ou excluir do mapa. As 8 coordenadas lixo devem ser descartadas com validação de bbox ou de ponto dentro do município.
3. **Agregadas.** O voto existe só na principal. Use sempre a geografia da principal, e não espere linhas de agregadas nos feeds.
4. **Aptos mudam entre turnos** (55.722 seções, por causa da TTE). Comparar comparecimento seção a seção exige o denominador de cada turno.
5. **Nº 28 (PRTB):** os 5.246 votos de Leonardo Avalanche são nulos técnicos. É preciso excluí-los dos válidos ao recalcular percentuais do 1T.
6. **Votáveis com 0 votos são omitidos** em `votacao_secao`. Ao pivotar, preencha com 0 e use `QT_COMPARECIMENTO` do detalhe como total.
7. **Formatos:** Latin-1, vírgula decimal nas coordenadas de 2026 (ponto em 2022), CD_MUNICIPIO com e sem padding, e tudo entre aspas em 2022. Leia tudo como texto e faça cast.
8. **Download:** o CDN do TSE derrubou uma conexão HTTP/2 (`PROTOCOL_ERROR`), e a retomada com `-C` gerou um zip corrompido. Use `--http1.1`, valide com `unzip -t` e, se falhar, baixe de novo do zero.
9. **Platô 19:15–19:45 no 1T 2026:** confirme se foi uma pausa real antes de usar o ritmo de chegada do 1T como modelo para o 2T.
10. **Ordem de chegada:** BUs do exterior podem ser recebidos antes das 17h, mas só entram na totalização às 17h. Use `DT_PRIM_TOT_PARCIAL_HOR_TSE`.

# Endpoints de resultados do TSE em tempo real: 2º turno 2026 (Presidente)

Pesquisa feita em 24/10/2026, entre 20:15 e 20:45 BRT, véspera do 2º turno (domingo 25/10/2026; urnas fecham às 17:00 BRT).
Todas as afirmações marcadas **[V]** foram verificadas com requisições HTTP reais nesta data.
As marcadas **[I]** são inferências: código do app, documentação de terceiros ou dedução.

---

## 0. Resumo executivo

- **[V]** O app oficial `resultados.tse.jus.br/oficial/app/` (Angular/Ionic, versão `26.10.17 (7216371)`) busca arquivos estáticos JSON e binários servidos por Akamai, com origem num bucket S3/Ceph (`tse-resultados-1`). Não existe API dinâmica: tudo é arquivo.
- **[V]** Ciclo `ele2026`. **1º turno**: pleito **3220**, eleição federal **6257**. **2º turno**: pleito **3221**, eleição federal **6258**. Cargo Presidente = **1**. Processo eleitoral (`cdpr`) = 1219.
- **[V]** Os arquivos do 2T já estão no ar, zerados: candidatos, config de seções e acompanhamento. Candidatos: **13 – LULA** (Luiz Inácio Lula da Silva, PT, coligação "BRASIL PRONTO PRA MAIS") e **22 – FLAVIO BOLSONARO** (Flavio Nantes Bolsonaro, PL). Os arquivos por seção (aux e BU) do 2T ainda dão 404, como esperado.
- **[V]** O BU por seção tem o mesmo layout de 2022/2024: `arquivo-urna/{pleito}/dados/{uf}/{mun}/{zona}/{secao}/` + `aux.json` + `{hash}/{arquivo}`. **Os nomes de arquivo mudaram**: agora `-bu.dat`, `-rdv.dat`, `-log.jez`, `-vota.vsc`. Não há `.imgbu` nas amostras.
- **[V]** **O formato ASN.1 do BU mudou em 2026.** A spec 2022 publicada pelo TSE (`bu.asn1`) falha ao decodificar. Escrevi uma spec adaptada (`scripts/bu-2026.asn1`) e um decodificador. Validação: somei os 21 BUs de Quixaba/PE no 1T e o resultado **bate 100%** com o agregado oficial do município: aptos, comparecimento, brancos, nulos e votos de cada candidato.
- **[V]** **Os BUs foram publicados progressivamente na noite do 1T.** O `Last-Modified` dos `.bu.dat` vai de 18:47 a 22:48 BRT de 04/10, e o `aux.json` registra o horário de recebimento. Mas o **atraso entre recebimento e publicação variou de ~2 min a ~4 h**: em PE ficou em 2–20 min; em SP chegou a 35 min–4 h.
- **[V]** CORS aberto: `Access-Control-Allow-Origin` devolve qualquer Origin, inclusive nos `.bu.dat`. Um navegador em outro domínio consegue buscar direto.
- **[V]** Cache na CDN de até ~60 s (`max-age` ≤ 60). A CDN **ignora a query string**, então `?nocache=` não fura o cache. ETag e Last-Modified funcionam (304). Há gzip, sem brotli.
- **[I]** Detectar mudança sem varrer as ~499 mil seções: há uma hierarquia BR → UF (`ab`, com seções totalizadas por município) → município/zona (`u.json`) → `cs.json` por UF, que traz `da`/`ha` por seção quando o aux existe. Detalhes na seção 8.

---

## 1. Configuração e códigos

Arquivo raiz **[V]**: `https://resultados.tse.jus.br/oficial/comum/config/ele-c.json`
(amostra: `samples/ele-c.json`)

| Item | 1º turno | 2º turno |
|---|---|---|
| Data | 04/10/2026 | 25/10/2026 |
| Ciclo (`c`) | `ele2026` | `ele2026` |
| Pleito (`pl[].cd`) | **3220** | **3221** |
| Processo eleitoral (`cdpr`) | 1219 | 1219 |
| Eleição Federal (Presidente) | **6257** (`cdt2`=6258) | **6258** |
| Eleição Estadual (Governador) | 6259 (`cdt2`=6260) | 6260 (só AM, RN, DF, ES, AC, TO) |
| Cargo Presidente | 1 | 1 |

O pleito 3221 do 2T também inclui suplementares e consultas populares (6282–6293). Para Presidente, use 6258.

O `ele-c.json` também traz os templates de diretório (`arq[]`), confirmados **[V]**:
```
ft  : <base>/<ambiente>/<ciclo>/<cd_eleicao>/fotos/<uf>
cm  : <base>/<ambiente>/<ciclo>/<cd_eleicao>/config
e   : <base>/<ambiente>/<ciclo>/<cd_eleicao>/dados/<uf>
cs  : <base>/<ambiente>/<ciclo>/arquivo-urna/<cd_pleito>/config/<uf>
ab  : <base>/<ambiente>/<ciclo>/<cd_eleicao>/dados/<uf>
u   : <base>/<ambiente>/<ciclo>/<cd_eleicao>/dados/<uf>
aux : <base>/<ambiente>/<ciclo>/arquivo-urna/<cd_pleito>/dados/<uf>/<municipio>/<zona>/<secao>
```
`base` = `https://resultados.tse.jus.br/` e `ambiente` = `oficial`. Os dois valores estão fixos no bundle `main.*.js`, módulo 7695, junto de `atualizarCooldownSegundos=10` e de uma chave pública Ed25519 de verificação JWS.

Rota do SPA para o 2T em Recife: `https://resultados.tse.jus.br/oficial/app/index.html#/eleicao/6258/uf/pe/mu/25313/cargo/1/vis/nominal/resultados`

---

## 2. Templates de URL (2026)

Extraídos do chunk `samples/app/9132.*.js` (serviço `obterUnificado`, `obterAcompanhamento*`, `obterConfiguracao*`). Todos testados com HTTP 200, salvo indicação.
Convenções: `{ele6}` = eleição com 6 dígitos (`006258`); `{pl6}` = pleito com 6 dígitos (`003221`); `{uf}` em minúsculas (`br`, `pe`, `zz`…); `{mun5}` = código TSE do município com 5 dígitos; `{z4}` = zona com 4; `{s4}` = seção com 4.

`B = https://resultados.tse.jus.br/oficial/ele2026`

### 2.1 Agregados (progresso e votos)

| O quê | Template | 2T Presidente (exemplo) | Status |
|---|---|---|---|
| Resultado BR | `B/{ele}/dados/br/br-c0001-e{ele6}-u.json` | `B/6258/dados/br/br-c0001-e006258-u.json` | [V] 200, zerado |
| Resultado UF | `B/{ele}/dados/{uf}/{uf}-c0001-e{ele6}-u.json` | `B/6258/dados/sp/sp-c0001-e006258-u.json` | [V] 200 |
| Resultado município | `B/{ele}/dados/{uf}/{uf}{mun5}-c0001-e{ele6}-u.json` | `B/6258/dados/pe/pe25313-c0001-e006258-u.json` | [V] 200 |
| Resultado zona | `B/{ele}/dados/{uf}/{uf}{mun5}-z{z4}-c0001-e{ele6}-u.json` | `…/pe/pe25313-z0001-c0001-e006258-u.json` | [V] 200 no 1T |
| Acompanhamento BR (progresso por UF) | `B/{ele}/dados/br/br-e{ele6}-ab.json` | `B/6258/dados/br/br-e006258-ab.json` | [V] 200 |
| Acompanhamento UF (progresso por município) | `B/{ele}/dados/{uf}/{uf}-e{ele6}-ab.json` | `B/6258/dados/pe/pe-e006258-ab.json` | [V] 200 (inclui `zz`) |
| Municípios e zonas | `B/{ele}/config/mun-e{ele6}-cm.json` | `B/6258/config/mun-e006258-cm.json` | [V] 200 |
| Eleitos | `B/{ele}/dados/{uf}/{uf}-c0001-e{ele6}-e.json` | — | [V] 404 para `br` no 1T (Presidente não tem "eleitos" no 1T) |
| Fotos | `B/{ele}/fotos/{uf}/{sqcand}.jpeg` | — | [I] (template no bundle) |

**JWS [V]:** cada `.json` tem um gêmeo `.jws`: troque a extensão. É um JWS compacto EdDSA (`kid=sNbt9Q_fLS65zE1_ZLNV-XRRwPY`) cujo payload é idêntico ao `.json` (conferido no `br-c0001-e006258-u`). O app de produção busca o **`.jws`** (`assinaturaJwsAtiva=true`) e valida a assinatura. O `.json` puro também é servido e estava idêntico. **[I]** Há um risco pequeno de o `.json` atrasar em relação ao `.jws` no domingo; se divergir, use o `.jws` e decodifique o payload em base64url.

### 2.2 Arquivo de urna (por seção)

| O quê | Template | Status |
|---|---|---|
| Config de seções da UF (lista mun→zona→seção) | `B/arquivo-urna/{pleito}/config/{uf}/{uf}-p{pl6}-cs.json` | [V] 200 para 3220 e 3221 |
| Auxiliar da seção (lista de arquivos por hash) | `B/arquivo-urna/{pleito}/dados/{uf}/{mun5}/{z4}/{s4}/p{pl6}-{uf}-m{mun5}-z{z4}-s{s4}-aux.json` | [V] 200 no 1T; **404 no 2T (ainda)** |
| Arquivo da seção | `B/arquivo-urna/{pleito}/dados/{uf}/{mun5}/{z4}/{s4}/{hash}/{nm}` | [V] 200 no 1T |

Nomes de arquivo vistos no 1T **[V]**: `o{pleito5}{uf}{mun5}{z4}{s4}-bu.dat`, `-rdv.dat`, `-log.jez`, `-vota.vsc`.
Exemplo: `o03220pe2531300010031-bu.dat`. O prefixo `o` vem de "oficial".
O app também reconhece **[I]** `busa` (BU do Sistema de Apuração), `rdvred`, `logsa`, `imgbu`/`imgbusa`, `red` e `sa`, escolhendo pelo campo `tp`.
O `hash` (nome do diretório) é o hex do texto base64 de um hash do conteúdo. Não dá para prever, então **é preciso ler o aux antes de baixar o BU**.

Exemplo real verificado (Recife, zona 1, seção 31, 1T):
```
https://resultados.tse.jus.br/oficial/ele2026/arquivo-urna/3220/dados/pe/25313/0001/0031/p003220-pe-m25313-z0001-s0031-aux.json
https://resultados.tse.jus.br/oficial/ele2026/arquivo-urna/3220/dados/pe/25313/0001/0031/6673784d7661656a5241716d4d4e75517279624b5846557542676f4547642d6568505552746e3338456b773d/o03220pe2531300010031-bu.dat
```
O mesmo para o 2T: `…/arquivo-urna/3221/dados/pe/25313/0001/0031/p003221-pe-m25313-z0001-s0031-aux.json` (hoje 404).

### 2.3 Outro endpoint encontrado no bundle (não utilizável)

`https://qrcodenobu.tse.jus.br/json-bu/oficial/{cdpr}/o{pleito5}{uf}{mun5}-qbu.js` é um índice por município usado na conferência de QR Code.
**[V] Devolve 403 Akamai "Access Denied"** tanto com curl quanto com User-Agent de navegador. Pode ser WAF, ou o caminho pode ser outro. Não conte com ele.

---

## 3. Payloads: significado dos campos

Os números vêm como **strings**, e os percentuais usam vírgula decimal (`"47,03"`). Os campos com sufixo `n` (`pvapn`, `pstn`…) trazem o percentual com mais casas, também com vírgula.

### 3.1 `*-u.json` (resultado "unificado" de uma abrangência)
Amostras: `samples/t1/br-c0001-e006257-u.json`, `samples/t2/br-c0001-e006258-u.json`, `samples/t1/pe25313-c0001-e006257-u.json`, `samples/t1/pe25313-z0001-c0001-e006257-u.json`

Cabeçalho: `ele` eleição, `t` turno, `f` fase (`o` = oficial), `sup` suplementar, `tpabr` (`br`/`uf`/`mu`/`zona`), `cdabr` código da abrangência, `dg`/`hg` data/hora de geração do arquivo, `idg` id de geração (sequencial global, sobe a cada geração; **[I]** serve para detectar mudança), `dt`/`ht` data/hora da última totalização, `dv` divulgar votação (s/n), `tf` totalização final (s/n), `and` andamento (`n` = não iniciada e `f` = finalizada, ambos **[V]**; **[I]** há um terceiro valor para "em andamento").

`carg[0]` = cargo (`cd`=1, `nv` vagas). `agr[]` = agremiações (coligação `tp:"c"` ou partido isolado `tp:"i"`) → `par[]` → `cand[]`:
`n` número, `sqcand` sequencial, `nm`/`nmu` nome civil/de urna, `seq` ordem, `e` eleito s/n, `st` situação ("2º turno", "Não eleito"…), `dvt` destinação do voto ("Válido", "Anulado"…), **`vap` votos apurados**, `pvap` % dos válidos, `vs[]` vice.

`s` = **seções**: `ts` total, **`st` totalizadas**, `pst` % totalizadas, `snt` não totalizadas, `si`/`sni` instaladas/não instaladas, `sa`/`sna` apuradas/não apuradas.
`e` = **eleitorado**: `te` total de aptos, `est` aptos em seções totalizadas, `esnt`, `esi`, `esni`, `esa`, `esna`, **`c` comparecimento**, **`a` abstenção** (`pc` e `pa` em %).
`v` = **votos**: `tv` total, `vv` válidos (`vvc` = válidos para concorrentes), `vnom` nominais, **`vb` brancos**, **`tvn` total de nulos** (`vn` nulos + `vnt` nulos técnicos), `van`/`vansj` anulados e anulados sub judice, `vp` anulados apurados em separado. O mapeamento de nomes vem do modelo no chunk 9132 **[V]**.

Estado do 2T às 20:16 BRT de 24/10 **[V]**: `s.ts=499248`, `s.st=0`, `e.te=158745502`, `vap=0` para os dois candidatos, `dg=24/10/2026 19:19:13`.

### 3.2 `*-ab.json` (acompanhamento)
Amostras: `samples/t1/br-e006257-ab.json`, `samples/t1/pe-e006257-ab.json`, `samples/t2/br-e006258-ab.json`, `samples/t2/pe-e006258-ab.json`, `samples/t2/mg-e006258-ab.json`

- `br-…-ab.json`: `abr[]` com uma entrada por UF, mais ZZ e BR. Cada entrada tem `and`, `dt`/`ht`, `munnr`/`munpt`/`munf` (municípios não recebidos / em andamento / finalizados), `s` e `e` no mesmo formato acima.
- `{uf}-…-ab.json`: `abr[]` com a UF e **uma entrada por município** (`tpabr:"mun"`, `cdabr`=código), cada uma com `s.st` (seções totalizadas), `s.ts`, `e.c`, `e.a` e `dt`/`ht`. **Este é o índice de progresso por município, servido em 28 arquivos** **[V]**.

Tamanhos gz no 2T **[V]**: SP 13 KB (490 KB sem compressão), MG 17 KB, PE ~4 KB.

### 3.3 `mun-e{ele6}-cm.json` (municípios)
`abr[]` por UF: `cd`, `ds`, `mu[]` com `cd` (código TSE), `cdi` (IBGE), `nm`, `c` (capital s/n), `z[]` (zonas).
No 2T **[V]**: 28 abrangências, **5.757 municípios** (186 deles no exterior, ZZ) e **6.292 pares município-zona**.

### 3.4 `{uf}-p{pl6}-cs.json` (config de seções do arquivo-urna)
Amostras: `samples/t1/pe-p003220-cs.json`, `samples/t1/sp-p003220-cs.json`, `samples/t1/zz-p003220-cs.json`, `samples/t2/pe-p003221-cs.json`

`dg`/`hg`/`idg`/`f`/`cdp`, depois `abr[0].mu[].zon[].sec[]`, onde cada seção tem:
- `ns`: número da seção.
- `nsp`: seção **principal**. Esta seção foi **agregada** à `nsp` e não tem BU próprio. Em PE no 1T foram 673 de 22.091.
- `nsa`: lista de seções agregadas a esta.
- **`da`/`ha`: data/hora do arquivo auxiliar da seção.** O app (modelo no chunk 4703: `dataArquivoAuxiliarSecao`, `isDataHoraArquivoAuxiliarSecao`) usa esses campos para decidir se a seção tem dados de urna **[V no código]**.
  - **No 2T, hoje, nenhuma seção tem `da`/`ha`** (só `ns`, `nsp`, `nsa`) **[V]**.
  - No 1T, PE (regerado em 24/10) mostra todas as seções com `06/10 16:59`. SP (`dg=05/10 02:59`) mostra todas entre **22:59 e 23:01 de 04/10**. Ou seja, `da`/`ha` é a hora da **última** regeração do aux, não a primeira publicação **[V]**.

Tamanhos **[V]**: SP 2T 2,1 MB (94 KB gz, sem `da`/`ha`); SP 1T 6,3 MB (459 KB gz, com `da`/`ha`).

### 3.5 `…-aux.json` (por seção)
Amostra: `samples/t1/recife-z0001-s0031/p003220-pe-m25313-z0001-s0031-aux.json`
```json
{ "dg":"24/10/2026","hg":"12:41:59","idg":"3926578","f":"o","st":"Totalizada",
  "hashes":[{ "hash":"6673784d…3d","dr":"04/10/2026","hr":"18:39:46","st":"Totalizado",
     "arq":[{"nm":"o03220pe2531300010031-log.jez","tp":"log"},
            {"nm":"o03220pe2531300010031-bu.dat","tp":"bu"},
            {"nm":"o03220pe2531300010031-vota.vsc","tp":"vota"},
            {"nm":"o03220pe2531300010031-rdv.dat","tp":"rdv"}]}]}
```
`st` = situação da seção. `hashes[]` = conjuntos de arquivos recebidos. Pode haver mais de um, por exemplo em reenvio ou troca de urna: o app mostra um seletor. `dr`/`hr` = **data/hora de recebimento**. `hashes[].st` = situação daquele conjunto. Use o hash com `st == "Totalizado"`. **[I]** É provável que existam também estados como recebido, excluído e sub judice.

### 3.6 BU (`-bu.dat`): ASN.1 BER

`EntidadeEnvelopeGenerico` (cabeçalho, fase, identificação, `tipoEnvelope=1`, `conteudo`) envolve um `EntidadeBoletimUrna`. O envelope não é cifrado (não tem campo `seguranca`).

Mudanças de 2026 em relação ao `bu.asn1` do TSE de 2022/2024 **[V]**, todas confirmadas decodificando arquivos reais:
- `Carga` ganhou `identificadorGeradorMidia { nome, serialCertificadoTPM, serialInstalacao }` (ex.: `ZPE001WKS04`).
- `EntidadeBoletimUrna`:
  - entrou `qtdEleitoresCompareceram` (comparecimento da seção) e `detalhamentoComparecimento [1] { compareceramSemBiometria, habilitadosPorBiometria, habilitadosPorBiografia }`;
  - `resultadosVotacaoPorEleicao` perdeu a tag `[3]`;
  - entrou `historicoCodigosCarga`;
  - saíram `qtdEleitoresLibCodigo`, `qtdEleitoresCompBiometrico` e `chaveAssinaturaVotosVotavel`.
- `ResultadoVotacaoPorEleicao` ganhou `qtdEleitoresAptosSecao`, `qtdEleitoresAptosTTE` (aptos = seção + TTE), `ultimoHashVotosVotavel` e `assinaturaUltimoHashVotosVotavel`.
- `TotalVotosVotavel` trocou `assinatura` por `ordemGeracaoHash` e `hash`.

Fonte dos nomes: classes asn1bean do projeto `github.com/izaquemacielcunha/mapa-eleicao-presidencial-tse-2026`, que já decodifica BUs de 2026 em Java. A ordem e as tags conferem com os bytes.

Campos úteis extraídos pelo decodificador:

| Campo | Significado |
|---|---|
| `municipio`, `zona`, `local`, `secao` | identificação |
| `eleicoes[].idEleicao` | 6257 no 1T federal, 6259 no estadual; **6258 no 2T** |
| `aptos` (`aptosSecao` + `aptosTTE`) | eleitores aptos |
| `comparecimento` | eleitores que votaram no cargo |
| `faltosos` = aptos − comparecimento | abstenção da seção |
| `nominais{numero: votos}`, `brancos`, `nulos`, `legenda` | votos |
| `detalhamentoComparecimento` | biometria / sem biometria / biografia |
| `urna.tipoUrna` (secao, contingencia, …), `tipoArquivo` (votacaoUE, votacaoRED, sa*), `versaoVotacao`, `numeroInternoUrna`, `codigoCarga`, `dataHoraCarga`, `geradorMidia` | urna |
| `dataHoraAbertura`, `dataHoraEncerramento`, `dataHoraEmissao`, `dataGeracao` | horários (formato `YYYYMMDDThhmmss`, hora local da seção) |

O **modelo** da urna (UE2020, UE2022…) **não aparece no BU** **[V]**. **[I]** Ele fica no log (`.jez`, um 7z), que não foi decodificado aqui.

---

## 4. Amostras salvas (`research/samples/`)

| Caminho | Conteúdo |
|---|---|
| `app-index.html`, `app/*.js` (94 arquivos) | SPA e todos os chunks; os templates de URL estão em `app/9132.*.js` |
| `ele-c.json` | config geral (pleitos e eleições) |
| `t1/br-c0001-e006257-u.json` (+`.jws`), `t1/pe-c0001-…`, `t1/pe25313-…`, `t1/pe25313-z0001-…`, `t1/pe23000-…` | agregados do 1T: BR, UF, município, zona |
| `t1/br-e006257-ab.json`, `t1/pe-e006257-ab.json` | acompanhamento 1T |
| `t1/mun-e006257-cm.json`, `t2/mun-e006258-cm.json` | municípios e zonas |
| `t1/{pe,sp,zz}-p003220-cs.json`, `t2/pe-p003221-cs.json`, `t2/sp-p003221-cs.json` | config de seções 1T e 2T |
| `t1/recife-z0001-s0031/` | aux + **bu.dat** + rdv.dat + log.jez + vota.vsc de Recife z1 s31 |
| `t1/mun-pe-23000/` | aux + BU das 21 seções de **Quixaba/PE** (`_index.json` traz recebimento × Last-Modified) |
| `t1/amostra-sp/` | 8 aux de SP sorteados (`hashes.txt`: recebimento por seção) |
| `t2/br-c0001-e006258-u.json` (+`.jws`), `t2/pe25313-…`, `t2/*-ab.json` | 2T zerado (candidatos 13 e 22) |
| `t2/p003221-…-aux.404.xml`, `t1/br-c0001-e006257-e.404.xml`, `qbu/*.403-akamai.html` | corpos de erro (404 S3 / 403 Akamai) |
| `spec/bu.asn1`, `spec/rdv.asn1`, `spec/assinatura.asn1` | spec oficial 2022/2024 do TSE (via `github.com/danarrib/TSEParser`) |
| `*.headers.txt` | cabeçalhos HTTP de cada download |

---

## 5. Decodificador (`research/scripts/`)

- `bu-2026.asn1`: spec adaptada para 2026 (as mudanças estão comentadas com `[2026]`).
- `decode_bu.py`: BU → JSON Lines. Por padrão filtra Presidente; `--cargo todos` traz todos os cargos; `--raw` mostra a estrutura completa.
- `fetch_secoes.py`: baixa aux + BU de todas as seções de um município (no máximo 4 conexões).
- `verificar_municipio.py`: soma os BUs decodificados e compara com o `u.json` oficial.

Uso (dependência `asn1tools` via `uv`, sem instalar nada global):
```bash
cd research/scripts
uv run --with asn1tools -- python -I decode_bu.py ../samples/t1/recife-z0001-s0031/o03220pe2531300010031-bu.dat
# validar um município inteiro
python3 -I fetch_secoes.py --pleito 3220 --uf pe --mun 23000 --out ../samples/t1/mun-pe-23000
uv run --with asn1tools -- python -I decode_bu.py ../samples/t1/mun-pe-23000/*-bu.dat > /tmp/q.jsonl
python3 -I verificar_municipio.py /tmp/q.jsonl ../samples/t1/pe23000-c0001-e006257-u.json
```
Saída real (Recife z1 s31, 1T, Presidente):
`aptos 393 (seção 389 + TTE 4) | comparecimento 307 | faltosos 86 | 13:123 22:157 70:8 55:7 14:6 | brancos 2 | nulos 4`.
Validação em Quixaba/PE (21 BUs): aptos 6216, comparecimento 5151, brancos 37, nulos 110, 13→4270, 22→594, 70→53, 14→43, 55→42, 30→2. **Bate 100% com `pe23000-c0001-e006257-u.json`**.
Desempenho: **~0,7 ms por BU** em Python/asn1tools. Os ~500 mil BUs somam ~6 min de CPU.

Para o 2T, filtre `idEleicao == 6258`. Os números dos candidatos aparecem em `nominais` (`"13"`, `"22"`).
Ainda não testado: um BU `busa` (Sistema de Apuração, `dadosSecaoSA = dadosSA`) e uma urna de contingência. A spec cobre os dois casos, mas não há amostra.

---

## 6. O BU sai durante a apuração?

**Sim, progressivamente, mas com atraso variável.** Evidências [V] do próprio 1T 2026:

- Quixaba/PE (`samples/t1/mun-pe-23000/_index.json`): recebimento às 18:39 → `Last-Modified` do `bu.dat` às **18:47 BRT**. Recebimento às 19:06 → 19:08. Recebimento às 19:07 → 19:28. Recebimento às 20:46 → 20:53.
- Recife z1 s31: recebimento às 18:39:46 → `Last-Modified` às 18:50:15 BRT.
- SP, 8 seções sorteadas: atrasos de **35 min a ~3h50**. Exemplos: recebimento às 18:13 → publicação às 18:48; 18:59 → 22:48; 19:11 → 22:20; 20:28 → 22:00.
- O `cs.json` de SP do 1T (gerado às 02:59 de 05/10) tem `da`/`ha` de **todas** as seções entre 22:59 e 23:01 de 04/10. Isso indica regeração em massa dos aux por volta das 23h em SP.
- Incidente do 1T (Aos Fatos, citando o TSE): das **19:14 às 20:04** a página "Brasil" parou de atualizar por "contenção no processamento" causada por pico de volume. Os resultados por UF continuaram. Na hora, 64,81% dos votos para Presidente já tinham saído.
- Cobertura jornalística do 1T 2026 descreve o BU por seção acessível pelo ícone "urna" ao longo da noite.

**[I]** Conclusão: o agregado (`u.json`) acompanha a totalização com ~1 min de defasagem. O BU por seção chega depois, de minutos a horas. Um painel por seção vai ficar atrás dos números oficiais agregados, sobretudo em SP e nos picos.

---

## 7. Comportamento HTTP

| Aspecto | Achado |
|---|---|
| Infra | Akamai na frente (`akamai-grn`, `cdn-cache-status`), origem S3/Ceph (`x-amz-request-id`, bucket `tse-resultados-1`). HTTP/2, `alt-svc: h3` **[V]** |
| CORS | `Access-Control-Allow-Origin` devolve o `Origin` recebido (testado com `https://example.com`), `Vary: Origin`. O preflight `OPTIONS` responde 200 com `Allow-Methods: GET`, `Max-Age: 600`. Vale também para `.bu.dat` **[V]**. O navegador busca direto, sem proxy. |
| Cache | `Cache-Control: max-age` ≤ 60 s nos JSON (decrescente: é o tempo restante no edge). `max-age≈3400` no `bu.dat`, que é imutável e tem hash no caminho. 404s também ficam em cache ~60 s **[V]** |
| `?nocache=` | O app sempre envia `?nocache=<timestamp>`, mas **a CDN ignora a query**: `Hit from child` com valor aleatório **[V]**. Frescor máximo ≈ 60 s; não adianta forçar. |
| Validação | `ETag` e `Last-Modified` presentes. `If-None-Match` e `If-Modified-Since` → **304** **[V]**. Use sempre: corta muito a banda. |
| Compressão | `gzip` com `Accept-Encoding: gzip`, inclusive no `bu.dat` (16,5 KB → 6,6 KB). **Sem brotli** **[V]** |
| Rate limit | Cabeçalho `x-ratelimit-limit: 5000, 5000;w=1` (5000 req/s), com `remaining` variando entre requisições: parece limite global da origem ou do edge, não por cliente. Nenhum 429 nas ~150 requisições feitas **[V]** |
| WAF | `resultados.tse.jus.br` aceitou curl e Python sem User-Agent de navegador. `qrcodenobu.tse.jus.br` respondeu 403 Akamai. `www.tse.jus.br` (notícias) respondeu 403 ao WebFetch. **[I]** Pode haver regras de bot mais duras no domingo: mande um User-Agent identificável e faça backoff. |
| Erros | 404 = XML S3 `NoSuchKey` (248 B) **[V]** |

---

## 8. Estratégia de polling recomendada

Não existe feed de mudanças nem índice por seção com carimbo de tempo confiável durante a noite. A estratégia abaixo é uma hierarquia de estreitamento que dispensa varrer as 499.248 seções a cada ciclo.

**Nível 0 (progresso nacional): a cada 15–30 s, 2 requisições**
- `6258/dados/br/br-c0001-e006258-u.json`: votos dos candidatos, `s.st`/`pst`, `e.c`/`e.a`.
- `6258/dados/br/br-e006258-ab.json`: `s.st` por UF. Diga qual UF mudou.
- Sempre condicional (ETag). Compare `idg`/`dg hg` para saber se o arquivo mudou.

**Nível 1 (progresso por município): a cada 30–60 s, até 28 requisições**
- `6258/dados/{uf}/{uf}-e006258-ab.json`, só das UFs cujo `st` subiu no nível 0. Total gz ≲ 200 KB por ciclo completo.
- O diff de `s.st` por município dá a lista de municípios com seções novas.

**Nível 2 (descobrir quais seções têm BU)**, duas opções:
- **A [I, preferida se funcionar]:** releia `arquivo-urna/3221/config/{uf}/{uf}-p003221-cs.json` das UFs que mudaram, de forma condicional. As seções que ganharam `da`/`ha` (ou cujo `da`/`ha` mudou) têm aux novo. Custo: até 28 arquivos, ~2–3 MB gz no total quando cheio (SP sozinho ~460 KB gz). Ciclo sugerido de 60–120 s. **Risco:** não ficou provado que o `cs` é regerado ao longo da noite; o de SP no 1T parece ter sido regerado em lote às ~23h. **Teste nos primeiros minutos após 17h** se o `Last-Modified` do `cs` muda e se aparecem `da`/`ha`.
- **B [fallback, V em funcionamento]:** para cada município cujo `st` subiu, consulte as zonas (`{uf}{mun5}-z{z4}-c0001-e006258-u.json`, que traz `s.st` por zona). Depois peça o `aux.json` só das seções **ainda não capturadas** daquela zona. Um 404 significa que ainda não saiu.

**Nível 3 (dados da seção): 2 requisições por seção, uma única vez**
- `aux.json` (~265 B gz) → `hash` + nome do `bu`/`busa` → `.bu.dat` (estimado em ~1–3 KB gz no 2T, que tem só Presidente e às vezes Governador; no 1T eram 6,6 KB gz).
- Reconsulte o aux de uma seção já capturada só se o `da`/`ha` dela mudar no `cs` (sinal de reenvio ou novo hash).

**Volume estimado na noite toda**
- Seções com BU: ~499.248 (`s.ts`, que já exclui as agregadas). Mais ~1.351 no exterior incluídas.
- Requisições por seção: ~1,0 milhão (aux + BU), mais alguns milhares de 404 no fallback B.
- Banda total dos BUs: **[I]** ~0,5–1,5 GB gz.
- Overhead dos índices: ~2 + 28 requisições a cada 30–60 s, mais cs/zonas. Na ordem de **10–50 mil requisições na noite**, ~0,5–3 GB se baixar `cs` cheio a cada minuto. Use 304.
- **Pico:** no 1T, 64,8% dos votos estavam apurados às 19:14, ou seja, ~325 mil seções em ~2h15 ≈ **40 seções/s ≈ 80 req/s** para ficar em tempo real. Com **4 conexões** e ~50 ms de latência, o teto é ~80 req/s: fica no limite. Dois caminhos:
  - aceitar fila e atraso (o próprio BU já sai com atraso de minutos a horas);
  - subir para 8–16 conexões HTTP/2 com backoff em qualquer 429/403/5xx. O cabeçalho `x-ratelimit-limit` sugere uma folga grande, mas a escolha é sua.
- Decodificação: ~0,7 ms/BU, irrelevante.

**Sugestão de produto [I]:** mostre o agregado oficial (BR/UF/município/zona via `u.json`) como "verdade" de progresso e votos. Use os BUs por seção para a granularidade fina e para métricas de abstenção por seção, sinalizando a cobertura (`seções com BU / s.st`).

---

## 9. Riscos e pontos em aberto

1. **Atraso dos BUs** em relação à totalização: de minutos a ~4 h no 1T (SP). A cobertura por seção vai ficar atrás do agregado.
2. **Comportamento do `cs.json` na noite** (se `da`/`ha` aparecem progressivamente) não foi comprovado. Tenha o fallback B pronto e verifique às 17:00–17:15.
3. **Formato do BU 2026 inferido.** A spec adaptada decodificou 22 BUs de PE (urnas de seção, `votacaoUE`); a soma dos 21 de Quixaba bate 100% com o agregado oficial. Não foi testada com `busa`, urna de contingência, RED ou ZZ. Se um tipo falhar, use `--raw` ou um walker BER genérico. O decodificador registra o erro e segue em frente.
4. **`.json` × `.jws`:** o app consome `.jws`. Se no domingo o `.json` deixar de ser atualizado, troque para `.jws` (o payload é o mesmo JSON em base64url, com assinatura Ed25519 verificável pela chave do bundle).
5. **Incidente como o do 1T** (19:14–20:04): o arquivo BR pode congelar enquanto as UFs seguem. Agregue a partir das UFs se `br-*` parar de mudar.
6. **WAF/bot:** o subdomínio `qrcodenobu` já bloqueia. Se `resultados` endurecer, o acesso direto do navegador (CORS aberto) é a alternativa natural.
7. **Seções agregadas (`nsp`/`nsa`) mudam entre turnos.** Use o `cs` do 2T (3221), não o do 1T. Os votos das agregadas estão no BU da principal.
8. **Múltiplos hashes por seção** (reenvio): use o hash com `st == "Totalizado"` e trate a mudança como atualização da seção.
9. **Horários:** `dr`/`hr`, `dg`/`hg` e `Last-Modified` estão em BRT/GMT conforme o caso; os horários dentro do BU estão na hora local da seção (relevante para AM/AC/MT/MS/RR/RO e ZZ).
10. **Ainda não verificado:**
    - o valor de `and` para "em andamento" (o app mapeia, mas não extraí a tabela);
    - o significado exato de `vvc` × `vv` e de `esae`/`mnae`;
    - se o `aux.json` aparece antes do `bu.dat` (estado "recebido" sem BU).

---

## 10. Fontes externas

- Spec ASN.1 oficial 2022/2024 e documentação do TSE replicadas em `github.com/danarrib/TSEParser`, diretório `TSE_Docs/spec`.
- Projeto 2026 com as classes asn1bean do BU novo: `github.com/izaquemacielcunha/mapa-eleicao-presidencial-tse-2026`. O README confirma o mesmo caminho `…/arquivo-urna/3220/config/{uf}/{uf}-p003220-cs.json`.
- Aos Fatos sobre o incidente de divulgação do 1T 2026: https://www.aosfatos.org/noticias/urnas-apuracao-votos-primeiro-turno/
- Nota do TSE sobre os resultados do 1T: https://www.tse.jus.br/comunicacao/noticias/2026/Outubro/resultados-do-1o-turno-das-eleicoes-2026-estao-disponiveis-em-pagina-do-tse (403 via fetch automatizado; citada pela busca)

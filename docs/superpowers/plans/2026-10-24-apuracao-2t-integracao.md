# Sala de guerra 2T — Plano de integração, ensaio e operação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** juntar as peças já em construção (ETL, coletor, mock, analytics, tela) num sistema que roda a noite toda no Mac, ensaiado e com roteiro de operação.

**Architecture:** processos locais independentes que se comunicam por arquivos (`docs/contracts.md`):
- coletor (raia AGG + raia BU) → `site/data/live/{manifest, agg.<dv>.json, sections.<dv>.bin}`;
- analytics → `analytics.json`;
- servidor estático → navegador na TV.

Um supervisor sobe tudo, reinicia o que cair e mantém o Mac acordado.

**Tech Stack:** Python 3.14 + uv (httpx/h2, asn1tools, duckdb, numpy, aiohttp), MapLibre + deck.gl + h3-js.

**Spec:** `docs/superpowers/specs/2026-10-24-apuracao-2t-design.md` + `docs/contracts.md` (§6 prevalece).

## Global Constraints

- Prazo: ensaio completo verde até 25/10 ~12:00; sistema no ar 16:30 BRT; urnas fecham 17:00.
- Prioridade (usuário): **tempo real por zona eleitoral**. BU é enriquecimento ("vamos com o decodificador") e nunca atrasa os agregados.
- Raia AGG ≤ ~20 req/s; raia BU ≤ 10 req/s. Um 403/429 na raia BU pausa só a raia BU, por 10 min.
- Front não calcula métricas (§6.5). Convenções §6.3: margem = L − O; δ por 100 aptos; faixa 90%.
- `can_call` só com a faixa inteira de um lado, ≥30% apurado e todas as regiões ≥5%. Fase `pre` antes de 2%.
- Sem hospedagem pública. Tudo em `127.0.0.1`.

## Review Focus

1. **O TSE congela o arquivo BR** (como das 19:14 às 20:04 no 1T). O BR é reconstruído pelas UFs (`br_frozen`), e o selo "TSE: última totalização" denuncia o atraso. Teste: mock `--freeze-br`.
2. **Coletor cai, Mac dorme ou rede cai.** O supervisor reinicia em ≤5 s, `caffeinate` fica ativo e a retomada usa o SQLite sem rebaixar nada. A tela mostra o banner de dados velhos e volta sozinha. Teste: `kill -9` do coletor, mais 2 min com o Wi-Fi desligado no ensaio.
3. **Bloqueio 403/429.** A raia BU pausa e a raia AGG segue. Teste: mock `--burst-403`.
4. **Sinal enganoso no início da noite** (Sul primeiro, viés das zonas parciais). A imputação por ordem de chegada, a ponderação por UF, o piso da faixa e as regras de fase seguram isso. Teste: backtest 2022 e pontuação do ensaio contra o `truth.parquet` do mock em 5/10/20/30% apurado.
5. **Navegador degrada durante a madrugada.** Recarga a cada 45 min com o estado preservado no hash da URL. Teste: memória estável em 10 min acelerados e `numeros.html` como reserva.

---

### Task 1: Componentes (em andamento com agentes; o supervisor aceita cada um)

Cada componente só é aceito com a prova pedida no brief:

| Componente | Status | Prova de aceite |
|---|---|---|
| ETL | ✅ | totais 1T conferidos exatamente e reconstrução em 7,5 s |
| Coletor | 🔄 | testes do decodificador; integração real com o 1T numa UF pequena (soma dos BUs = agregado oficial); teste de restart; latência da raia AGG medida |
| Mock | 🔄 | 0→100% de seções; soma das seções = agregados; amostra de BUs decodificados = truth; 404 → 200 após o atraso; 304 |
| Analytics | 🔄 | `BACKTEST.md` com viés antes e depois da imputação por ordem; faixa com ~90% de cobertura; ciclo < 2 s |
| Tela | 🔄 | zero erros de console; municípios, H3 e pontos nos zooms certos; Modo TV ciclando; memória estável |

- [ ] **Step 1:** receber o relatório final de cada agente e conferir a prova (rodar o comando citado, não confiar no texto).
- [ ] **Step 2:** registrar desvios de contrato e decidir: aceitar e documentar, ou devolver.

### Task 2: Supervisor `sala.py` (dono: supervisor/Claude)

**Files:**
- Create: `sala.py` (raiz)
- Create: `docs/RUNBOOK.md` (Task 5)

**Interfaces:**
- Consumes:
  - `uv run python -m collector run [--base-url URL] [--no-bu]`
  - `uv run python -m analytics.run --live-dir site/data/live --interval 20`
  - `uv run python site/serve.py`
  - `uv run python -m mock.server ...`
- Produces: `uv run python sala.py --mode live|mock [--scenario S --speed X] [--no-bu]`.

O supervisor sobe os filhos, reinicia com backoff (1, 2, 4… máx. 30 s), roda `caffeinate -dimsu` atrelado ao próprio PID, grava logs em `data/live/logs/<proc>.log` e imprime um resumo de saúde a cada 30 s (lido do manifest e do analytics). Ctrl+C derruba tudo.

- [ ] **Step 1:** escrever e rodar no modo mock. Esperado: os 4 processos sobem e a tela abre em http://127.0.0.1:8000.
- [ ] **Step 2:** `kill -9` no PID do coletor. Esperado: reinício em ≤5 s; a tela mostra o banner de dados velhos e volta sozinha.
- [ ] **Step 3:** Ctrl+C. Esperado: nenhum processo filho sobra (`pgrep -f collector` vazio).

### Task 3: Ensaio ponta a ponta com o mock

- [ ] **Step 1:** rodar `sala.py --mode mock --scenario swing2022 --speed 20`, cobrindo 17:00→22:00 simulados, com:
  - `--freeze-br 19:14-20:04`;
  - `--burst-403` de 5 min na raia BU;
  - `--error-rate 0.02`.
- [ ] **Step 2:** conferir os critérios:
  - atraso dos agregados < 60 s simulados fora das falhas;
  - `br_frozen` detectado;
  - a raia BU pausa no 403 e a AGG segue;
  - nenhum `can_call` antes das regras;
  - margem projetada final dentro da faixa;
  - erro da projeção vs truth em 10/30/50% registrado;
  - a tela sem erros.
- [ ] **Step 3:** cenários `virada`, `flavio` e `empate` a velocidade 60, para ver se a tela "conta a história certa" em cada um.
- [ ] **Step 4:** registrar os resultados em `docs/ENSAIO.md` e corrigir o que falhar, devolvendo ao dono do componente.

### Task 4: Teste de fumaça contra o TSE real (1T)

- [ ] **Step 1:** coletor com `--election 6257 --pleito 3220 --ufs ac,rr`, mais analytics e tela. Esperado: `agg.json` = agregados oficiais do 1T das duas UFs, e a tela renderiza dados reais. Δ ≈ 0 é o esperado, porque o 1T é comparado consigo mesmo.

### Task 5: Roteiro de operação (`docs/RUNBOOK.md`)

- [ ] **Step 1:** escrever a linha do tempo de domingo:
  - **09:00** `etl.build_base --refresh-locais`, depois o export da tela;
  - **10:00** ensaio final;
  - **16:30** `sala.py --mode live`;
  - **17:00–17:15** conferir no `cs.json` se `da`/`ha` aparecem (estratégia A ou B) e se os primeiros agregados chegam;
  - **cada falha conhecida:** o sintoma e a ação. Ex.: o selo do TSE parado → confirmar no app oficial; 403 → hotspot do celular; tela travada → `numeros.html`.
- [ ] **Step 2:** checklist do Mac:
  - na tomada e no cabo de rede;
  - atualizações automáticas desligadas;
  - `pmset` sem sleep;
  - tela da TV sem descanso;
  - Chrome em tela cheia.

### Task 6: Revisão independente final

- [ ] **Step 1:** um revisor novo lê o coletor e o analytics (maior risco) contra o spec e o contracts, procurando bugs que derrubam a noite: exceções não tratadas, loops sem backoff, escrita não atômica, unidades trocadas.
- [ ] **Step 2:** corrigir o que for confirmado e repetir o ensaio da Task 3 em velocidade 60.

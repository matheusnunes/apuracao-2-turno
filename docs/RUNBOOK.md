# Roteiro de domingo — Sala de guerra 2º turno (25/10/2026)

Pasta do projeto: `~/Projects/apuracao-2-turno`. Todos os comandos rodam a partir dela.

## Mac (de manhã)

- [ ] Ligado na tomada; de preferência no **cabo de rede**.
- [ ] Ajustes → Geral → Atualização de Software → **desligar** as atualizações automáticas, para não reiniciar à noite.
- [ ] Bateria/Energia: com o adaptador, **nunca** dormir; tela da TV sem descanso nem economia. O `sala.py` também roda `caffeinate`.
- [ ] Hotspot do celular configurado e testado (rede reserva, que muda o IP).
- [ ] Chrome atualizado. A TV fica espelhada ou como segunda tela.

## Linha do tempo

| Hora | O que fazer | Comando / onde olhar |
|---|---|---|
| 09:00 | Atualizar os locais de votação (o TSE pode ter regerado o arquivo) e reconstruir a base | `uv run python -m etl.build_base --refresh-locais` e depois `uv run python site/tools/export_base.py` |
| 10:00 | **Ensaio final** no simulador, ordem real | `uv run python sala.py --mode mock --scenario swing2022 --speed 20` → abrir http://127.0.0.1:8000 |
| 10:40 | Ensaio com **outra ordem** de chegada | `uv run python sala.py --mode mock --scenario flavio --order nordeste-first --speed 30` |
| 16:30 | **Ligar para valer** (limpa os arquivos do ensaio e arquiva o estado de testes do coletor em `data/live/old/`) | `uv run python sala.py --mode live --clean-live` |
| 16:35 | Abrir a tela em tela cheia na TV | Chrome em http://127.0.0.1:8000, Modo TV ligado, F11 / ⌃⌘F |
| 17:00–17:15 | Primeiros números: conferir os selos de frescor e comparar o placar com o app oficial do TSE | app oficial: https://resultados.tse.jus.br/oficial/app/index.html (eleição 6258, Presidente) |
| 17:05 | Ver se o TSE marca quais urnas têm boletim (estratégia A); senão o coletor segue sozinho na B | `uv run python -m collector cs-check --ufs sp,pe,rr` |
| a noite toda | Acompanhar o terminal do `sala.py`, que imprime a saúde a cada 30 s | linha `[sala HH:MM:SS] …` |

## Como ler a tela (o que é confiável quando)

- **Antes de 2% apurado (fase `pre`):** só aparecem a base do 1º turno e o "ritmo necessário" (+1,4 voto de margem por 100 aptos para o Lula empatar). Não há projeção.
- **Fase `early`:** o termômetro e a projeção aparecem com faixa larga. **A tela não crava vencedor.** O "mix do apurado" diz quais regiões estão super-representadas.
- **Fase `trend` ("tendência"):** só quando a faixa de 90% inteira fica de um lado, com ≥30% apurado e todas as regiões com ≥5%. Em 2022 isso aconteceu às 18:03, 42 min antes da virada no placar bruto.
- **"Ritmo observado × necessário":** se o observado (ponderado por estado) está acima do necessário, Lula está fazendo o suficiente nas urnas já apuradas.
- **Cores lavadas no mapa** = estimado pela zona eleitoral. **Cores fortes** = boletim de urna recebido.

## Se algo der errado

| Sintoma | Provável causa | O que fazer |
|---|---|---|
| Selo "TSE: última totalização" parado há vários minutos, mas o app oficial avança | O arquivo nacional do TSE congelou (aconteceu no 1T, 19:14–20:04) | Nada: o coletor reconstrói o Brasil pelos estados (`br_frozen=True` no terminal) |
| O app oficial também parado | Pausa do próprio TSE | Esperar. A tela mostra o alerta |
| Selo "Coletor OK há…" passa de 2 min | Coletor caiu ou travou | O `sala.py` reinicia sozinho. Se persistir, ver `data/live/logs/collector.log` |
| Erros 403/429 no log | Bloqueio do TSE | Os boletins pausam sozinhos por 10 min. Se os **agregados** também derem 403: trocar para o hotspot do celular. Se ainda assim falhar, reiniciar só com agregados: `uv run python sala.py --mode live --no-bu` |
| Rede caiu | Wi-Fi/roteador | O coletor retoma sozinho quando a rede volta, sem baixar de novo o que já tem |
| Tela travada ou lenta | Navegador | Recarregar (⌘R); ela recarrega sozinha a cada 45 min. Reserva leve: http://127.0.0.1:8000/numeros.html |
| Mac reiniciou, ou o terminal do `sala.py` foi fechado/morto | Atualização, energia, engano | `uv run python sala.py --mode live` (**sem** `--clean-live`). Ele encerra sozinho a execução anterior, seja o supervisor ainda vivo (inclusive um aberto pelo Claude) ou os processos órfãos. O coletor e a análise retomam de onde pararam |

## Encerrar

`Ctrl+C` no terminal do `sala.py` derruba tudo. Os dados ficam em `data/live/` e `site/data/live/`.

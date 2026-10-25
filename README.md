# Sala de guerra · 2º turno presidencial 2026

Monitor local da apuração do 2º turno (Lula × Flávio Bolsonaro). Compara cada zona e cada seção apurada com elas mesmas no 1º turno. Mostra o termômetro da campanha, o ritmo necessário × observado, a projeção com faixa de incerteza e um mapa por município, H3 e local de votação. Uso privado, numa TV.

- Operação no dia: [`docs/RUNBOOK.md`](docs/RUNBOOK.md). Ponto de entrada único: `uv run python sala.py --mode live|mock`.
- Desenho e contratos: [`docs/superpowers/specs/2026-10-24-apuracao-2t-design.md`](docs/superpowers/specs/2026-10-24-apuracao-2t-design.md), [`docs/contracts.md`](docs/contracts.md).
- Partes:
  - `etl/` (base do 1º turno);
  - `collector/` (TSE ao vivo, agregados + boletins de urna);
  - `analytics/` (modelo e backtest 2022);
  - `site/` (tela);
  - `mock/` (TSE simulado para ensaio);
  - `research/` (fontes e decodificador do BU).

Os dados não são versionados. Para gerá-los: `uv run python -m etl.build_base` e depois `uv run python site/tools/export_base.py`.

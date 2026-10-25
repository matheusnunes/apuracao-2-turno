#!/usr/bin/env bash
# Baixa os datasets estáticos (baseline 1T 2026 + backtest 2022) do CDN do TSE.
# Uso: bash research/scripts/00_download.sh   (a partir da raiz do projeto)
# Obs.: use --http1.1; com HTTP/2 o CDN derrubou uma conexão (PROTOCOL_ERROR) e a retomada com -C gerou zip corrompido.
set -euo pipefail
R=data/raw
B=https://cdn.tse.jus.br/estatistica/sead
dl(){ mkdir -p "$1"; curl -sS -L --http1.1 --retry 5 -o "$1/$(basename "$2")" "$2" -w "%{http_code} %{size_download} $2\n"; unzip -tq "$1/$(basename "$2")" >/dev/null; }

# --- 2026, 1º turno ---
dl $R/votacao_secao_2026_BR                 $B/odsele/votacao_secao/votacao_secao_2026_BR.zip
dl $R/detalhe_votacao_secao_2026            $B/odsele/detalhe_votacao_secao/detalhe_votacao_secao_2026.zip
dl $R/eleitorado_local_votacao_2026         $B/odsele/eleitorado_locais_votacao/eleitorado_local_votacao_2026.zip
dl $R/relatorio_resultado_totalizacao_2026_BR $B/odsele/relatorio_resultado_totalizacao/Relatorio_Resultado_Totalizacao_2026_BR.zip
dl $R/detalhe_votacao_munzona_2026          $B/odsele/detalhe_votacao_munzona/detalhe_votacao_munzona_2026.zip
dl $R/consulta_cand_2026                    $B/odsele/consulta_cand/consulta_cand_2026.zip
# Boletim de Urna (opcional; 5,08 GB no total). Baixados apenas PE e ZZ como amostra.
for uf in PE ZZ; do dl $R/bweb_1t_2026 $B/eleicoes/eleicoes2026/buweb/bweb_1t_${uf}_051020261403.zip; done

# --- 2022 (backtest da "virada") ---
dl $R/2022_votacao_secao_BR                 $B/odsele/votacao_secao/votacao_secao_2022_BR.zip
dl $R/2022_detalhe_votacao_secao            $B/odsele/detalhe_votacao_secao/detalhe_votacao_secao_2022.zip
dl $R/2022_eleitorado_local_votacao         $B/odsele/eleitorado_locais_votacao/eleitorado_local_votacao_2022.zip
dl $R/2022_historico_totalizacao_presidente $B/eleicoes/eleicoes2022/Historico_Totalizacao_Presidente_BR_1T_2022.zip
dl $R/2022_historico_totalizacao_presidente $B/eleicoes/eleicoes2022/Historico_Totalizacao_Presidente_BR_2T_2022.zip
dl $R/2022_secoes_agregadas                 $B/odsele/secoes_agregadas/secoes_agregadas_2022.zip
for uf in AC AL AM AP BA CE DF ES GO MA MG MS MT PA PB PE PI PR RJ RN RO RR RS SC SE SP TO ZZ; do
  dl $R/2022_bweb_2t $B/eleicoes/eleicoes2022/buweb/bweb_2t_${uf}_311020221535.zip
done

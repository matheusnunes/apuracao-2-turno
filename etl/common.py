"""Constantes e caminhos compartilhados pelo ETL."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
BASE = ROOT / "data" / "base"
CS_2T_DIR = RAW / "cs_2t"
GEO_RAW_DIR = RAW / "ibge_malhas"
LOCAIS_REFRESH_DIR = RAW / "eleitorado_local_votacao_2026_refresh"

PLEITO_2T = "3221"
ELEICAO_2T = "6258"
CS_URL = "https://resultados.tse.jus.br/oficial/ele2026/arquivo-urna/{pl}/config/{uf}/{uf}-p{pl6}-cs.json"
CM_URL = "https://resultados.tse.jus.br/oficial/ele2026/{ele}/config/mun-e{ele6}-cm.json"

UF_REGIAO = {
    "ac": "N", "am": "N", "ap": "N", "pa": "N", "ro": "N", "rr": "N", "to": "N",
    "al": "NE", "ba": "NE", "ce": "NE", "ma": "NE", "pb": "NE", "pe": "NE",
    "pi": "NE", "rn": "NE", "se": "NE",
    "df": "CO", "go": "CO", "ms": "CO", "mt": "CO",
    "es": "SE", "mg": "SE", "rj": "SE", "sp": "SE",
    "pr": "S", "rs": "S", "sc": "S",
    "zz": "ZZ",
}

# Ordem canônica: UFs minúsculas em ordem alfabética, `zz` por último.
UFS = sorted(u for u in UF_REGIAO if u != "zz") + ["zz"]
UF_ORDER = {u: i for i, u in enumerate(UFS)}

# Candidatos
L_NUM = 13  # LULA
O_NUM = 22  # FLÁVIO BOLSONARO
NULO_TECNICO_NUMS = (28,)  # Leonardo Avalanche (PRTB): votos contados como nulos técnicos
BRANCO_NUM = 95
NULO_NUM = 96

# Totais oficiais do 1T (research/baseline-datasets.md §4)
OFFICIAL_1T = {
    "aptos": 158_745_502,
    "comp": 125_275_835,
    "abst": 33_469_244,
    "validos": 119_300_788,
    "o": 56_104_503,
    "l": 53_879_538,
    "x": 119_300_788 - 56_104_503 - 53_879_538,
    "b": 2_300_798,
    "n": 3_674_249,  # 3.669.003 nulos + 5.246 nulos técnicos (nº 28)
    "secoes_principais": 499_248,
}

# Bounding box generoso do Brasil (inclui ilhas oceânicas)
BR_BBOX = {"lat_min": -34.0, "lat_max": 5.5, "lng_min": -74.5, "lng_max": -28.5}


def section_key(uf: str, cd_mun: int, zona: int, secao: int) -> str:
    return f"{uf}-{cd_mun:05d}-{zona:04d}-{secao:04d}"

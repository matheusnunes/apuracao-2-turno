"""Serialização no estilo exato dos JSON do TSE e formatação de percentuais.

Formato real (byte a byte): começa com "\n", separador `" : "`, objetos aninhados
precedidos de "\n", lista vazia `[  ]`. Ex.:
  \n{ "ele" : "6258", "carg" : [ \n{ "cd" : "1" } ], "s" : \n{ "ts" : "1" } }
"""

from __future__ import annotations

import json

_esc = json.JSONEncoder(ensure_ascii=False).encode


def _enc(v) -> str:
    if isinstance(v, dict):
        if not v:
            return "{  }"
        parts = []
        for k, x in v.items():
            if isinstance(x, dict):
                parts.append(f'"{k}" : \n{_enc(x)}')
            else:
                parts.append(f'"{k}" : {_enc(x)}')
        return "{ " + ", ".join(parts) + " }"
    if isinstance(v, list):
        if not v:
            return "[  ]"
        return "[ " + ", ".join(("\n" + _enc(x)) if isinstance(x, dict) else _enc(x) for x in v) + " ]"
    if isinstance(v, str):
        return _esc(v)
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def dumps(obj) -> bytes:
    return ("\n" + _enc(obj)).encode("utf-8")


# ---------------------------------------------------------------- percentuais

def pct(num: int, den: int) -> str:
    """2 casas, vírgula. Regras observadas nos arquivos reais:
    arredonda, mas nunca mostra 100,00 se < 100 (99,9997 -> "99,99") nem 0,00 se > 0
    (0,00027 -> "0,01")."""
    if den <= 0 or num <= 0:
        return "0,00"
    if num >= den:
        return "100,00"
    x = 100.0 * num / den
    r = round(x, 2)
    if r <= 0:
        r = 0.01
    if r >= 100:
        r = 99.99
    return f"{r:.2f}".replace(".", ",")


def pctn(num: int, den: int, zero_den: str = "0") -> str:
    """Percentual com 9 casas ("47,027772356"); "0" e "100" exatos sem casas."""
    if den <= 0:
        return zero_den
    if num <= 0:
        return "0"
    if num >= den:
        return "100"
    return f"{100.0 * num / den:.9f}".replace(".", ",")


def p3(prefix: str, num: int, den: int, zero_den: str = "0") -> dict:
    """{"p<x>": pct, "p<x>n": pctn}"""
    return {f"p{prefix}": pct(num, den), f"p{prefix}n": pctn(num, den, zero_den)}

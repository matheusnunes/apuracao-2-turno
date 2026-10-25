#!/usr/bin/env python3
"""
Soma os BUs decodificados (JSON Lines do decode_bu.py) e compara com o arquivo
agregado oficial do municipio ({uf}{mun}-c0001-e{ele}-u.json).

Uso:
  uv run --with asn1tools -- python -I decode_bu.py ../samples/t1/mun-pe-23000/*-bu.dat > /tmp/x.jsonl
  python3 -I verificar_municipio.py /tmp/x.jsonl ../samples/t1/pe23000-c0001-e006257-u.json
"""
import collections
import json
import sys


def main(jsonl, ujson):
    tot = collections.Counter()
    n = 0
    for line in open(jsonl, encoding="utf-8"):
        d = json.loads(line)
        n += 1
        for e in d["eleicoes"]:
            for c in e["cargos"]:
                tot["aptos"] += c["aptos"]
                tot["comparecimento"] += c["comparecimento"]
                tot["brancos"] += c["brancos"]
                tot["nulos"] += c["nulos"]
                for k, v in c["nominais"].items():
                    tot["cand_" + k] += v
    u = json.load(open(ujson, encoding="utf-8"))
    oficial = {
        "aptos": int(u["e"]["te"]),
        "comparecimento": int(u["e"]["c"]),
        "brancos": int(u["v"]["vb"]),
        "nulos": int(u["v"]["tvn"]),
    }
    for ag in u["carg"][0]["agr"]:
        for p in ag["par"]:
            for c in p["cand"]:
                oficial["cand_" + c["n"]] = int(c["vap"])
    ok = True
    print(f"BUs somados: {n} | secoes no agregado: {u['s']['ts']} (totalizadas {u['s']['st']})")
    for k in sorted(set(oficial) | set(tot)):
        a, b = tot.get(k, 0), oficial.get(k, 0)
        flag = "OK " if a == b else "DIF"
        ok &= a == b
        print(f"  {flag} {k:16s} BU={a:>8} oficial={b:>8}")
    print("RESULTADO:", "BATE 100%" if ok else "DIVERGE")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))

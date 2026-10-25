#!/usr/bin/env python3
"""
Baixa aux.json + BU (bu.dat / busa.dat) de todas as secoes de UM municipio, a partir do
arquivo de configuracao de secoes (cs) do arquivo-urna. Educado: max 4 conexoes.

Uso:
  python3 -I fetch_secoes.py --pleito 3220 --uf pe --mun 23000 --out ../samples/t1/mun-23000
"""
import argparse
import gzip
import json
import os
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

BASE = "https://resultados.tse.jus.br/oficial/ele2026/arquivo-urna"
UA = "apuracao-2-turno-research/0.1 (+contato: pesquisa)"


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "gzip"})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            data = gzip.decompress(data)
        return data, dict(r.headers)


def secoes_do_municipio(pleito, uf, mun):
    cs, _ = get(f"{BASE}/{pleito}/config/{uf}/{uf}-p{int(pleito):06d}-cs.json")
    cs = json.loads(cs)
    for a in cs["abr"]:
        for m in a["mu"]:
            if m["cd"] == mun:
                for z in m["zon"]:
                    for s in z["sec"]:
                        yield z["cd"], s
                return


def baixar(pleito, uf, mun, zona, sec, out):
    ns = sec["ns"]
    if "nsp" in sec:  # secao agregada a outra (sem BU proprio)
        return {"zona": zona, "secao": ns, "status": f"agregada->{sec['nsp']}"}
    d = f"{BASE}/{pleito}/dados/{uf}/{mun}/{zona}/{ns}"
    auxname = f"p{int(pleito):06d}-{uf}-m{mun}-z{zona}-s{ns}-aux.json"
    try:
        raw, _ = get(f"{d}/{auxname}")
    except urllib.error.HTTPError as e:
        return {"zona": zona, "secao": ns, "status": f"aux HTTP {e.code}"}
    open(os.path.join(out, auxname), "wb").write(raw)
    aux = json.loads(raw)
    res = {"zona": zona, "secao": ns, "status": aux.get("st"), "bu": None}
    for h in aux.get("hashes", []):
        for arq in h.get("arq", []):
            if arq["tp"] in ("bu", "busa"):
                data, hdr = get(f"{d}/{h['hash']}/{arq['nm']}")
                open(os.path.join(out, arq["nm"]), "wb").write(data)
                res.update(bu=arq["nm"], hash_st=h.get("st"), recebido=f"{h.get('dr')} {h.get('hr')}",
                           last_modified=hdr.get("Last-Modified"))
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pleito", required=True)
    ap.add_argument("--uf", required=True)
    ap.add_argument("--mun", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    secs = list(secoes_do_municipio(a.pleito, a.uf.lower(), a.mun))
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=min(a.workers, 4)) as ex:
        res = list(ex.map(lambda zs: baixar(a.pleito, a.uf.lower(), a.mun, zs[0], zs[1], a.out), secs))
    json.dump(res, open(os.path.join(a.out, "_index.json"), "w"), ensure_ascii=False, indent=1)
    print(f"{len(secs)} secoes em {time.time()-t0:.1f}s", file=sys.stderr)
    for r in res:
        print(json.dumps(r, ensure_ascii=False))


if __name__ == "__main__":
    main()

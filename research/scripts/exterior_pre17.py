"""Exterior antes das 17h: baixa os BUs do 2T já recebidos pelo TSE (status "Recebido" ou "Totalizado")
e compara com as mesmas seções no 1T da base.

Uso (rodar de novo só busca o que falta; cache em research/samples/zz-2t/):
    uv run python research/scripts/exterior_pre17.py [--rate 8]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

import duckdb
import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from collector.bu import BUDecodeError, decode_bu  # noqa: E402
from collector.tse import parse_cs  # noqa: E402

BASE = "https://resultados.tse.jus.br/oficial/ele2026/arquivo-urna/3221"
CACHE = ROOT / "research" / "samples" / "zz-2t"
ELEICAO, LULA, FLAVIO = 6258, 13, 22


def pick_hash(aux: dict) -> tuple[dict | None, str | None]:
    """Hash mais recente (Totalizado tem preferência) e o nome do arquivo de BU."""
    hashes = aux.get("hashes") or []
    hashes = sorted(hashes, key=lambda h: (str(h.get("st", "")).lower() == "totalizado", h.get("dr", ""), h.get("hr", "")))
    for h in reversed(hashes):
        for f in h.get("arq") or []:
            if str(f.get("tp", "")).lower() in ("bu", "busa"):
                return h, f["nm"]
    return None, None


async def fetch_section(client: httpx.AsyncClient, gate: "Gate", mun5: str, z4: str, s4: str) -> dict:
    d = f"{BASE}/dados/zz/{mun5}/{z4}/{s4}"
    await gate.wait()
    r = await client.get(f"{d}/p003221-zz-m{mun5}-z{z4}-s{s4}-aux.json")
    if r.status_code in (403, 404):
        return {"mun5": mun5, "z4": z4, "s4": s4, "aux": r.status_code}
    r.raise_for_status()
    aux = r.json()
    h, nm = pick_hash(aux)
    out = {"mun5": mun5, "z4": z4, "s4": s4, "aux": 200, "st": aux.get("st"),
           "states": [x.get("st") for x in aux.get("hashes") or []]}
    if h is None:
        return out
    await gate.wait()
    rb = await client.get(f"{d}/{h['hash']}/{nm}")
    if rb.status_code != 200:
        out["bu_http"] = rb.status_code
        return out
    try:
        bu = decode_bu(rb.content, ELEICAO)
    except BUDecodeError as e:
        out["decode_error"] = str(e)
        return out
    out.update(recebido=f"{h.get('dr')} {h.get('hr')}", hash_st=h.get("st"), tipo=bu.tipo_bu,
               aptos=bu.aptos, comp=bu.comparecimento, l=bu.votos.get(LULA, 0), o=bu.votos.get(FLAVIO, 0),
               bn=bu.brancos + bu.nulos + bu.legenda, outros=sum(v for k, v in bu.votos.items() if k not in (LULA, FLAVIO)))
    return out


class Gate:
    def __init__(self, rate: float):
        self.dt, self.next, self.lock = 1.0 / rate, 0.0, asyncio.Lock()

    async def wait(self) -> None:
        async with self.lock:
            now = time.monotonic()
            self.next = max(self.next, now)
            delay = self.next - now
            self.next += self.dt
        if delay > 0:
            await asyncio.sleep(delay)


async def scan(rate: float) -> list[dict]:
    CACHE.mkdir(parents=True, exist_ok=True)
    done_path = CACHE / "secoes.jsonl"
    done = {}
    if done_path.exists():
        for line in done_path.read_text().splitlines():
            r = json.loads(line)
            if "l" in r:  # só reaproveita quem já tem BU decodificado
                done[(r["mun5"], r["z4"], r["s4"])] = r
    async with httpx.AsyncClient(http2=True, timeout=20, headers={"User-Agent": "apuracao-2t-sala/1.0"}) as client:
        cs = (await client.get(f"{BASE}/config/zz/zz-p003221-cs.json")).json()
        secs = [(m, z, s) for m, z, s, _dh, principal in parse_cs(cs) if principal]
        todo = [k for k in secs if k not in done]
        print(f"exterior: {len(secs)} seções principais; {len(done)} já em cache; buscando {len(todo)}…", flush=True)
        gate, sem = Gate(rate), asyncio.Semaphore(8)
        results = list(done.values())

        async def one(k):
            async with sem:
                for attempt in range(3):
                    try:
                        return await fetch_section(client, gate, *k)
                    except (httpx.HTTPError, ValueError) as e:
                        err = repr(e)[:120]
                        await asyncio.sleep(2 * (attempt + 1))
                return {"mun5": k[0], "z4": k[1], "s4": k[2], "erro": err}

        for i, fut in enumerate(asyncio.as_completed([one(k) for k in todo]), 1):
            results.append(await fut)
            if i % 200 == 0:
                print(f"  {i}/{len(todo)}", flush=True)
    done_path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in results) + "\n")
    return results


def report(results: list[dict]) -> None:
    con = duckdb.connect()
    base = con.sql("""select cd_mun, zona, secao, aptos1, aptos2_cad, comp1, l1, o1, x1, b1 + n1 bn1
                      from read_parquet(?) where uf = 'zz'""", params=[str(ROOT / "data/base/secoes.parquet")]).fetchall()
    nomes = dict(con.sql("select cd_mun, nome from read_parquet(?) where uf = 'zz'",
                         params=[str(ROOT / "data/base/municipios.parquet")]).fetchall())
    b = {(r[0], r[1], r[2]): r[3:] for r in base}
    got = [r for r in results if "l" in r]
    n_aux = sum(1 for r in results if r.get("aux") == 200)
    erros = [r for r in results if "erro" in r or "decode_error" in r or "bu_http" in r]
    print(f"\nseções do exterior na base: {len(b)} · com aux publicado: {n_aux} · BU decodificado: {len(got)} · erros: {len(erros)}")
    T = dict(A=0, A1=0, C1=0, C2=0, L1=0, O1=0, X1=0, BN1=0, L2=0, O2=0, BN2=0)
    falta_base, aptos_dif, cidades = 0, 0, {}
    for r in got:
        k = (int(r["mun5"]), int(r["z4"]), int(r["s4"]))
        if k not in b:
            falta_base += 1
            continue
        a1, a2c, c1, l1, o1, x1, bn1 = b[k]
        aptos_dif += r["aptos"] != a2c
        for kk, v in dict(A=r["aptos"], A1=a1, C1=c1, C2=r["comp"], L1=l1, O1=o1, X1=x1, BN1=bn1,
                          L2=r["l"], O2=r["o"], BN2=r["bn"]).items():
            T[kk] += v
        c = cidades.setdefault(k[0], dict(n=0, A=0, dm=0, L2=0, O2=0))
        c["n"] += 1; c["A"] += r["aptos"]; c["dm"] += (r["l"] - r["o"]) - (l1 - o1); c["L2"] += r["l"]; c["O2"] += r["o"]
    print(f"validação: seções fora da base {falta_base} · aptos do BU ≠ aptos do cadastro 2T: {aptos_dif}")
    if not T["A"]:
        return
    A = T["A"]
    v1, v2 = T["L1"] + T["O1"] + T["X1"], T["L2"] + T["O2"]
    dm = (T["L2"] - T["O2"]) - (T["L1"] - T["O1"])
    print(f"\nmesmas {len(got)} seções ({A:,} aptos, {A / sum(x[1] for x in b.values()) * 100:.0f}% dos aptos do exterior):")
    print(f"  comparecimento {T['C1'] / T['A1'] * 100:.1f}% → {T['C2'] / A * 100:.1f}%  ({T['C2'] - T['C1']:+,} votantes)")
    print(f"  1T válidos: Lula {T['L1'] / v1 * 100:.1f}% · Flávio {T['O1'] / v1 * 100:.1f}% · outros {T['X1'] / v1 * 100:.1f}%")
    print(f"  2T válidos: Lula {T['L2'] / v2 * 100:.1f}% · Flávio {T['O2'] / v2 * 100:.1f}%")
    print(f"  ΔLula {T['L2'] - T['L1']:+,} · ΔFlávio {T['O2'] - T['O1']:+,} · Δbrancos+nulos {T['BN2'] - T['BN1']:+,}")
    print(f"  Δmargem {dm:+,} votos = {dm / A * 100:+.2f} por 100 aptos (necessário no país: +1,40)")
    print("\ncidades (Δmargem por 100 aptos):")
    for cd, c in sorted(cidades.items(), key=lambda kv: -kv[1]["A"])[:25]:
        print(f"  {nomes.get(cd, cd):<22} {c['n']:>3} seç · {c['A']:>6,} aptos · Lula {c['L2'] / max(c['L2'] + c['O2'], 1) * 100:5.1f}% · Δm {c['dm'] / c['A'] * 100:+5.1f}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rate", type=float, default=8, help="requisições por segundo (teto)")
    ap.add_argument("--report-only", action="store_true")
    a = ap.parse_args()
    if a.report_only:
        results = [json.loads(x) for x in (CACHE / "secoes.jsonl").read_text().splitlines()]
    else:
        results = asyncio.run(scan(a.rate))
    report(results)


if __name__ == "__main__":
    main()

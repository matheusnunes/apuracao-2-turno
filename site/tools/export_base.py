"""Exporta a base do 1T (data/base/*.parquet, só leitura) para os arquivos compactos da tela (site/data/base/).

Uso:  uv run python site/tools/export_base.py   (~10 s; idempotente)

Saídas (todas little-endian; colunas "struct of arrays" descritas em meta.json):
  meta.json            contagens, sha256 do índice, layout dos .bin, base 1T nacional, UFs/regiões
  secoes.bin           N=499.248 seções alinhadas ao idx: place i32 (-1 = sem coordenada própria),
                       a1 c1 l1 o1 x1 bn1 a2 secao zone mun (u16)
  locais.bin           P locais com coord_src=='local': lat lng (f32), sec_start nsec local_id (i32),
                       cell_r3..cell_r9 (i32, índice na lista h3/r{res}.json), mun zone (u16), uf (u8)
  locais_secs.bin      i32: seções de cada local, contíguas (CSR: sec_start/nsec)
  h3/r{3..9}.json      {"cells": [...], "uf": [...], "lat": [...], "lng": [...]}: células com algum local
  municipios.json      colunas por município (mun_idx = ordem de aparição no índice)
  zonas.json           colunas por zona município×zona (zone_idx = ordem de aparição; seções contíguas)
  mun_poly.json        polígonos IBGE simplificados: [[mun_idx, [anel, ...]], ...]
  ufs.json             polígonos e metadados das 27 UFs
  locais_meta/{uf}.json  nome/endereço/bairro/nr_local dos locais (sob demanda no clique)
"""

from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path

import duckdb
import h3
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "data" / "base"
OUT = ROOT / "site" / "data" / "base"
RESES = [3, 4, 5, 6, 7, 8, 9]
UF_ORDER = ["ac", "al", "am", "ap", "ba", "ce", "df", "es", "go", "ma", "mg", "ms", "mt", "pa", "pb", "pe",
            "pi", "pr", "rj", "rn", "ro", "rr", "rs", "sc", "se", "sp", "to", "zz"]
REGIONS = {"N": "Norte", "NE": "Nordeste", "CO": "Centro-Oeste", "SE": "Sudeste", "S": "Sul", "ZZ": "Exterior"}
UF_NAMES = {"ac": "Acre", "al": "Alagoas", "am": "Amazonas", "ap": "Amapá", "ba": "Bahia", "ce": "Ceará",
            "df": "Distrito Federal", "es": "Espírito Santo", "go": "Goiás", "ma": "Maranhão", "mg": "Minas Gerais",
            "ms": "Mato Grosso do Sul", "mt": "Mato Grosso", "pa": "Pará", "pb": "Paraíba", "pe": "Pernambuco",
            "pi": "Piauí", "pr": "Paraná", "rj": "Rio de Janeiro", "rn": "Rio Grande do Norte", "ro": "Rondônia",
            "rr": "Roraima", "rs": "Rio Grande do Sul", "sc": "Santa Catarina", "se": "Sergipe", "sp": "São Paulo",
            "to": "Tocantins", "zz": "Exterior"}


def dump_json(path: Path, obj) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode()
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)
    return len(data)


def write_soa(path: Path, cols: list[tuple[str, np.ndarray]]) -> list[dict]:
    """Concatena colunas com alinhamento de 8 bytes; devolve o layout para meta.json."""
    layout, chunks, off = [], [], 0
    for name, arr in cols:
        arr = np.ascontiguousarray(arr)
        b = arr.astype(arr.dtype.newbyteorder("<"), copy=False).tobytes()
        layout.append({"name": name, "dtype": arr.dtype.name, "offset": off, "length": int(arr.shape[0])})
        chunks.append(b)
        off += len(b)
        pad = (-off) % 8
        if pad:
            chunks.append(b"\0" * pad)
            off += pad
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(b"".join(chunks))
    tmp.replace(path)
    return layout


def u16(name: str, a: np.ndarray) -> np.ndarray:
    a = np.asarray(a)
    if a.min() < 0 or a.max() > 65535:
        raise SystemExit(f"ERRO: coluna {name} fora de Uint16 (min {a.min()}, max {a.max()})")
    return a.astype(np.uint16)


def main() -> int:
    t0 = time.time()
    OUT.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    sec = con.sql(f"""select idx, uf, regiao, cd_mun, zona, secao, local_id, coord_src,
                      aptos1, comp1, l1, o1, x1, b1 + n1 as bn1, aptos2_cad
                      from '{BASE / 'secoes.parquet'}' order by idx""").fetchnumpy()
    N = len(sec["idx"])
    assert (sec["idx"] == np.arange(N)).all(), "idx não é 0..N-1 em ordem"
    idx_csv = BASE / "secoes_index.csv"
    sha = hashlib.sha256(idx_csv.read_bytes()).hexdigest()

    uf_of = np.array([str(u) for u in sec["uf"]])
    uf_idx_map = {u: i for i, u in enumerate(UF_ORDER)}
    sec_uf = np.array([uf_idx_map[u] for u in uf_of], dtype=np.uint8)

    # --- municípios: ordem de aparição no índice (contíguos) ---
    cd_mun = sec["cd_mun"].astype(np.int64)
    mun_keys, mun_first, sec_mun = np.unique(cd_mun, return_index=True, return_inverse=True)
    order = np.argsort(mun_first, kind="stable")
    rank = np.empty_like(order)
    rank[order] = np.arange(len(order))
    sec_mun = rank[sec_mun]
    mun_cd = mun_keys[order]
    M = len(mun_cd)

    # --- zonas (município × zona): ordem de aparição, contíguas ---
    zkey = cd_mun * 10000 + sec["zona"].astype(np.int64)
    z_keys, z_first, sec_zone = np.unique(zkey, return_index=True, return_inverse=True)
    zorder = np.argsort(z_first, kind="stable")
    zrank = np.empty_like(zorder)
    zrank[zorder] = np.arange(len(zorder))
    sec_zone = zrank[sec_zone]
    z_keys = z_keys[zorder]
    Z = len(z_keys)
    assert (np.diff(sec_zone) >= 0).all(), "seções de cada zona deveriam ser contíguas"
    z_start = np.searchsorted(sec_zone, np.arange(Z))
    z_n = np.bincount(sec_zone, minlength=Z)

    # --- locais com coordenada própria ---
    loc = con.sql(f"""select local_id, uf, cd_mun, zona, nr_local, nome, endereco, bairro, lat, lng, coord_src, h3_r9
                      from '{BASE / 'locais.parquet'}' order by local_id""").fetchnumpy()
    loc_local = np.array([str(c) == "local" for c in loc["coord_src"]])
    sec_local = np.array([str(c) == "local" for c in sec["coord_src"]])
    lid_sec = sec["local_id"].astype(np.int64)
    used_lids = np.unique(lid_sec[sec_local])
    keep = loc_local & np.isin(loc["local_id"], used_lids)
    P_lid = loc["local_id"][keep].astype(np.int64)
    P = len(P_lid)
    lid_to_p = {int(l): p for p, l in enumerate(P_lid)}
    sec_place = np.full(N, -1, dtype=np.int32)
    for s in np.nonzero(sec_local)[0]:
        sec_place[s] = lid_to_p[int(lid_sec[s])]
    has = sec_place >= 0
    perm = np.nonzero(has)[0]
    perm = perm[np.argsort(sec_place[perm], kind="stable")].astype(np.int32)
    p_n = np.bincount(sec_place[has], minlength=P).astype(np.int32)
    p_start = np.concatenate([[0], np.cumsum(p_n)[:-1]]).astype(np.int32)
    # mun/zona/uf do local = da primeira seção
    first_sec = perm[p_start]
    p_mun = sec_mun[first_sec]
    p_zone = sec_zone[first_sec]
    p_uf = sec_uf[first_sec]
    p_lat = loc["lat"][keep].astype(np.float32)
    p_lng = loc["lng"][keep].astype(np.float32)
    p_h3 = [str(h) for h in loc["h3_r9"][keep]]

    # --- H3 por resolução ---
    cells_meta = {}
    p_cells = {}
    (OUT / "h3").mkdir(exist_ok=True)
    for r in RESES:
        ids = p_h3 if r == 9 else [h3.cell_to_parent(h, r) for h in p_h3]
        lookup: dict[str, int] = {}
        cl, cuf = [], []
        arr = np.empty(P, dtype=np.int32)
        for p, c in enumerate(ids):
            i = lookup.get(c)
            if i is None:
                i = len(cl)
                lookup[c] = i
                cl.append(c)
                cuf.append(int(p_uf[p]))
            arr[p] = i
        lat, lng = zip(*(h3.cell_to_latlng(c) for c in cl))
        size = dump_json(OUT / "h3" / f"r{r}.json", {"res": r, "cells": cl, "uf": cuf,
                                                     "lat": [round(x, 5) for x in lat],
                                                     "lng": [round(x, 5) for x in lng]})
        p_cells[r] = arr
        cells_meta[str(r)] = {"n": len(cl), "bytes": size}

    # --- secoes.bin ---
    sec_cols = [
        ("place", sec_place),
        ("a1", u16("aptos1", sec["aptos1"])), ("c1", u16("comp1", sec["comp1"])),
        ("l1", u16("l1", sec["l1"])), ("o1", u16("o1", sec["o1"])), ("x1", u16("x1", sec["x1"])),
        ("bn1", u16("bn1", sec["bn1"])), ("a2", u16("aptos2_cad", sec["aptos2_cad"])),
        ("secao", u16("secao", sec["secao"])), ("zone", u16("zone", sec_zone)), ("mun", u16("mun", sec_mun)),
        ("uf", sec_uf),
    ]
    sec_layout = write_soa(OUT / "secoes.bin", sec_cols)

    loc_cols = [("lat", p_lat), ("lng", p_lng), ("sec_start", p_start), ("nsec", p_n),
                ("local_id", P_lid.astype(np.int32))]
    loc_cols += [(f"cell_r{r}", p_cells[r]) for r in RESES]
    loc_cols += [("mun", u16("p_mun", p_mun)), ("zone", u16("p_zone", p_zone)), ("uf", p_uf)]
    loc_layout = write_soa(OUT / "locais.bin", loc_cols)
    perm_layout = write_soa(OUT / "locais_secs.bin", [("sec", perm)])

    # --- metadados de locais por UF (sob demanda) ---
    (OUT / "locais_meta").mkdir(exist_ok=True)
    nome, end, bai, nrl = (loc[k][keep] for k in ("nome", "endereco", "bairro", "nr_local"))
    zon = loc["zona"][keep]
    by_uf: dict[str, dict] = {}
    for p in range(P):
        u = UF_ORDER[int(p_uf[p])]
        d = by_uf.setdefault(u, {})
        d[str(p)] = [str(nome[p] or ""), str(end[p] or ""), str(bai[p] or ""), int(nrl[p]), int(zon[p])]
    for u, d in by_uf.items():
        dump_json(OUT / "locais_meta" / f"{u}.json", d)

    # --- municípios ---
    mu = con.sql(f"select * from '{BASE / 'municipios.parquet'}'").fetchnumpy()
    mrow = {int(c): i for i, c in enumerate(mu["cd_mun"])}
    mi = np.array([mrow[int(c)] for c in mun_cd])

    def mcol(k, cast=int):
        out = []
        for v in mu[k][mi]:
            if v is None or (isinstance(v, float) and np.isnan(v)) or v is np.ma.masked:
                out.append(None)
            else:
                out.append(cast(v))
        return out

    # bbox por município a partir dos polígonos (para fitBounds); centroide como fallback
    geo = json.loads((BASE / "municipios_geo.geojson").read_text())
    cd_to_midx = {int(c): i for i, c in enumerate(mun_cd)}
    polys, bbox = [], [None] * M
    for f in geo["features"]:
        c = int(f["properties"]["cd_mun"])
        m = cd_to_midx.get(c)
        if m is None:
            print(f"aviso: polígono sem município no índice: {c}", file=sys.stderr)
            continue
        g = f["geometry"]
        parts = [g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"]
        x0 = y0 = 1e9
        x1 = y1 = -1e9
        for poly in parts:
            rings = [[[round(x, 4), round(y, 4)] for x, y in ring] for ring in poly]
            polys.append([m, rings])
            for x, y in poly[0]:
                x0, x1, y0, y1 = min(x0, x), max(x1, x), min(y0, y), max(y1, y)
        bbox[m] = [round(x0, 4), round(y0, 4), round(x1, 4), round(y1, 4)]
    dump_json(OUT / "mun_poly.json", polys)

    sec_a2 = sec["aptos2_cad"].astype(np.int64)
    mun_a2 = np.bincount(sec_mun, weights=sec_a2, minlength=M).astype(np.int64)
    mun_json = {
        "cd_mun": [int(c) for c in mun_cd], "ibge": mcol("cd_mun_ibge"), "nome": mcol("nome", str),
        "uf": mcol("uf", str), "regiao": mcol("regiao", str), "capital": mcol("capital", bool),
        "lat": mcol("lat", float), "lng": mcol("lng", float), "bbox": bbox,
        "a1": mcol("aptos1"), "c1": mcol("comp1"), "l1": mcol("l1"), "o1": mcol("o1"), "x1": mcol("x1"),
        "bn1": [int(b) + int(n) for b, n in zip(mu["b1"][mi], mu["n1"][mi])], "a2": [int(x) for x in mun_a2],
        "n_secoes": mcol("n_secoes"), "n_zonas": mcol("n_zonas"),
        "sec_start": [int(x) for x in np.searchsorted(sec_mun, np.arange(M))],
    }
    dump_json(OUT / "municipios.json", mun_json)

    # --- zonas ---
    def zsum(col):
        return [int(x) for x in np.bincount(sec_zone, weights=np.asarray(sec[col], dtype=np.int64), minlength=Z)]

    zcd = z_keys // 10000
    zz = z_keys % 10000
    zonas_json = {
        "k": [f"{int(c)}-{int(z):04d}" for c, z in zip(zcd, zz)], "mun": [int(sec_mun[s]) for s in z_start],
        "zona": [int(z) for z in zz], "uf": [int(sec_uf[s]) for s in z_start],
        "sec_start": [int(s) for s in z_start], "n": [int(n) for n in z_n],
        "a1": zsum("aptos1"), "c1": zsum("comp1"), "l1": zsum("l1"), "o1": zsum("o1"), "x1": zsum("x1"),
        "bn1": zsum("bn1"), "a2": zsum("aptos2_cad"),
    }
    zp = con.sql(f"select cd_mun, zona, n_secoes, aptos1 from '{BASE / 'zonas.parquet'}'").fetchnumpy()
    zchk = {(int(c), int(z)): (int(n), int(a)) for c, z, n, a in zip(zp["cd_mun"], zp["zona"], zp["n_secoes"], zp["aptos1"])}
    bad = sum(1 for i, k in enumerate(zip(zcd, zz)) if zchk.get((int(k[0]), int(k[1]))) != (zonas_json["n"][i], zonas_json["a1"][i]))
    if bad or len(zchk) != Z:
        raise SystemExit(f"ERRO: zonas não batem com zonas.parquet ({bad} divergentes, {len(zchk)} × {Z})")
    dump_json(OUT / "zonas.json", zonas_json)

    # --- UFs ---
    ugeo = json.loads((BASE / "ufs_geo.geojson").read_text())
    ufs = []
    sec_a1 = sec["aptos1"].astype(np.int64)
    uf_a2 = np.bincount(sec_uf, weights=sec_a2, minlength=len(UF_ORDER))
    uf_reg = {}
    for u, r in zip(uf_of, sec["regiao"]):
        uf_reg.setdefault(u, str(r))
    feats = {f["properties"]["uf"]: f for f in ugeo["features"]}
    for i, u in enumerate(UF_ORDER):
        f = feats.get(u)
        rings_out, b = [], None
        if f:
            g = f["geometry"]
            parts = [g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"]
            x0 = y0 = 1e9
            x1 = y1 = -1e9
            for poly in parts:
                rings_out.append([[[round(x, 4), round(y, 4)] for x, y in ring] for ring in poly])
                for x, y in poly[0]:
                    x0, x1, y0, y1 = min(x0, x), max(x1, x), min(y0, y), max(y1, y)
            b = [round(x0, 4), round(y0, 4), round(x1, 4), round(y1, 4)]
        ufs.append({"uf": u, "nome": UF_NAMES[u], "regiao": uf_reg.get(u, "ZZ"), "bbox": b,
                    "a2": int(uf_a2[i]), "polys": rings_out})
    dump_json(OUT / "ufs.json", ufs)

    # --- meta ---
    tot = {k: int(np.asarray(sec[k], dtype=np.int64).sum()) for k in ("aptos1", "comp1", "l1", "o1", "x1", "bn1", "aptos2_cad")}
    build_id = hashlib.sha1(f"{sha}-{P}-{Z}-{M}-{time.time()}".encode()).hexdigest()[:12]
    meta = {
        "build_id": build_id,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "index": {"n_rows": N, "n_cols": 8, "sha256": sha},
        "counts": {"secoes": N, "locais": P, "locais_secoes": int(len(perm)), "zonas": Z, "municipios": M,
                   "secoes_sem_coord_local": int(N - len(perm))},
        "files": {"secoes": {"path": "secoes.bin", "n": N, "cols": sec_layout},
                  "locais": {"path": "locais.bin", "n": P, "cols": loc_layout},
                  "locais_secs": {"path": "locais_secs.bin", "n": int(len(perm)), "cols": perm_layout}},
        "h3": {"reses": RESES, "cells": cells_meta},
        "uf_order": UF_ORDER, "uf_names": UF_NAMES, "regions": REGIONS,
        "region_order": ["N", "NE", "CO", "SE", "S", "ZZ"],
        "baseline_1t": {"l": tot["l1"], "o": tot["o1"], "x": tot["x1"], "bn": tot["bn1"], "comp": tot["comp1"],
                        "aptos": tot["aptos1"], "aptos2_cad": tot["aptos2_cad"],
                        "margin_votes": tot["l1"] - tot["o1"],
                        "l_pct_valid": round(100 * tot["l1"] / (tot["l1"] + tot["o1"] + tot["x1"]), 4),
                        "o_pct_valid": round(100 * tot["o1"] / (tot["l1"] + tot["o1"] + tot["x1"]), 4),
                        # mesmo que pace.delta_req do analytics: só usado se o analytics.json faltar
                        "delta_req": round(100 * (tot["o1"] - tot["l1"]) / tot["aptos1"], 4)},
        "br_bounds": [[-74.2, -34.0], [-34.6, 5.4]],
    }
    dump_json(OUT / "meta.json", meta)

    sizes = {p.name: p.stat().st_size for p in sorted(OUT.glob("*")) if p.is_file()}
    print(f"exportado em {time.time() - t0:.1f}s → {OUT}")
    print(f"  seções {N:,} · locais {P:,} ({len(perm):,} seções com coord. própria) · zonas {Z:,} · municípios {M:,}")
    print("  células H3: " + ", ".join(f"r{r}={cells_meta[str(r)]['n']:,}" for r in RESES))
    for k, v in sizes.items():
        print(f"  {k:24s} {v / 1e6:7.2f} MB")
    print(f"  sha256 índice {sha}")
    print(f"  base 1T: L {tot['l1']:,} O {tot['o1']:,} aptos {tot['aptos1']:,} δ_req {meta['baseline_1t']['delta_req']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

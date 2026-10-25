"""Malhas do IBGE: download, centroides (ponto interno), GeoJSON/TopoJSON para o front.

Fonte: API de malhas v3 do IBGE (servicodados.ibge.gov.br/api/v3/malhas).
- `mun_minima.*`       -> municipios_geo.geojson / municipios_geo.topojson (choropleth nacional)
- `mun_intermediaria`  -> municipios_geo_hd.topojson (zoom estadual) e testes de ponto no polígono
- `uf_intermediaria`   -> ufs_geo.geojson
- `mun_metadados.json` -> centroide oficial do IBGE (validado: tem de cair dentro do polígono)
"""

from __future__ import annotations

import os
import subprocess

import numpy as np
import orjson

from .common import BASE, GEO_RAW_DIR, UF_REGIAO

API = "https://servicodados.ibge.gov.br/api/v3/malhas/paises/BR"
DOWNLOADS = {
    "mun_minima.geojson": "formato=application/vnd.geo%2Bjson&qualidade=minima&intrarregiao=municipio",
    "mun_minima.topojson": "formato=application/json&qualidade=minima&intrarregiao=municipio",
    "mun_intermediaria.geojson": "formato=application/vnd.geo%2Bjson&qualidade=intermediaria&intrarregiao=municipio",
    "mun_intermediaria.topojson": "formato=application/json&qualidade=intermediaria&intrarregiao=municipio",
    "uf_intermediaria.geojson": "formato=application/vnd.geo%2Bjson&qualidade=intermediaria&intrarregiao=UF",
}
META = ("mun_metadados.json", f"{API}/metadados?intrarregiao=municipio")

UF_IBGE = {
    11: "ro", 12: "ac", 13: "am", 14: "rr", 15: "pa", 16: "ap", 17: "to",
    21: "ma", 22: "pi", 23: "ce", 24: "rn", 25: "pb", 26: "pe", 27: "al", 28: "se", 29: "ba",
    31: "mg", 32: "es", 33: "rj", 35: "sp", 41: "pr", 42: "sc", 43: "rs",
    50: "ms", 51: "mt", 52: "go", 53: "df",
}
UF_NOME = {
    "ac": "Acre", "al": "Alagoas", "am": "Amazonas", "ap": "Amapá", "ba": "Bahia", "ce": "Ceará",
    "df": "Distrito Federal", "es": "Espírito Santo", "go": "Goiás", "ma": "Maranhão",
    "mg": "Minas Gerais", "ms": "Mato Grosso do Sul", "mt": "Mato Grosso", "pa": "Pará",
    "pb": "Paraíba", "pe": "Pernambuco", "pi": "Piauí", "pr": "Paraná", "rj": "Rio de Janeiro",
    "rn": "Rio Grande do Norte", "ro": "Rondônia", "rr": "Roraima", "rs": "Rio Grande do Sul",
    "sc": "Santa Catarina", "se": "Sergipe", "sp": "São Paulo", "to": "Tocantins",
}


def download(force: bool = False):
    """Baixa as malhas com curl (HTTP/1.1). Só baixa o que falta, salvo --force."""
    GEO_RAW_DIR.mkdir(parents=True, exist_ok=True)
    jobs = [(name, f"{API}?{q}") for name, q in DOWNLOADS.items()] + [META]
    for name, url in jobs:
        dst = GEO_RAW_DIR / name
        if dst.exists() and dst.stat().st_size > 1000 and not force:
            continue
        tmp = dst.with_suffix(dst.suffix + ".tmp")
        subprocess.run(
            ["curl", "-sS", "--http1.1", "--compressed", "--fail", "-m", "300", "-o", str(tmp), url],
            check=True,
        )
        orjson.loads(tmp.read_bytes())  # valida
        os.replace(tmp, dst)
        print(f"  baixado {name} ({dst.stat().st_size:,} B)")


# ---------------------------------------------------------------- geometria

def _polys(geom):
    """Lista de polígonos; cada polígono = lista de anéis (np.ndarray Nx2, lon/lat)."""
    if geom["type"] == "Polygon":
        return [[np.asarray(r, dtype=float) for r in geom["coordinates"]]]
    if geom["type"] == "MultiPolygon":
        return [[np.asarray(r, dtype=float) for r in p] for p in geom["coordinates"]]
    raise ValueError(geom["type"])


def _ring_contains(ring: np.ndarray, x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Ray casting vetorizado: quais pontos (x, y) estão dentro do anel."""
    x1, y1 = ring[:-1, 0], ring[:-1, 1]
    x2, y2 = ring[1:, 0], ring[1:, 1]
    xs = x[:, None]
    ys = y[:, None]
    cond = (y1 > ys) != (y2 > ys)
    with np.errstate(divide="ignore", invalid="ignore"):
        xint = (x2 - x1) * (ys - y1) / (y2 - y1) + x1
    return np.count_nonzero(cond & (xs < xint), axis=1) % 2 == 1


def contains(polys, x, y) -> np.ndarray:
    x = np.atleast_1d(np.asarray(x, dtype=float))
    y = np.atleast_1d(np.asarray(y, dtype=float))
    inside = np.zeros(len(x), dtype=bool)
    for p in polys:
        inn = _ring_contains(p[0], x, y)
        for hole in p[1:]:
            inn &= ~_ring_contains(hole, x, y)
        inside |= inn
    return inside


def _area(ring):
    x, y = ring[:, 0], ring[:, 1]
    return 0.5 * float(np.dot(x[:-1], y[1:]) - np.dot(x[1:], y[:-1]))


def point_on_surface(polys):
    """Ponto garantidamente interno: meio do maior segmento interno numa linha
    horizontal que passa pelo centro do maior polígono (aproximação de PointOnSurface)."""
    p = max(polys, key=lambda pp: abs(_area(pp[0])))
    outer = p[0]
    best = None
    ymin, ymax = outer[:, 1].min(), outer[:, 1].max()
    for frac in (0.5, 0.4, 0.6, 0.3, 0.7, 0.2, 0.8):
        yc = ymin + frac * (ymax - ymin)
        xs = []
        for ring in p:
            x1, y1, x2, y2 = ring[:-1, 0], ring[:-1, 1], ring[1:, 0], ring[1:, 1]
            m = (y1 > yc) != (y2 > yc)
            with np.errstate(divide="ignore", invalid="ignore"):
                xs.extend(((x2 - x1) * (yc - y1) / (y2 - y1) + x1)[m].tolist())
        xs.sort()
        for a, b in zip(xs[0::2], xs[1::2]):
            if best is None or (b - a) > best[0]:
                best = (b - a, (a + b) / 2, yc)
        if best:
            break
    return (best[1], best[2]) if best else (float(outer[:, 0].mean()), float(outer[:, 1].mean()))


class Mesh:
    """Malha municipal intermediária indexada pelo código IBGE."""

    def __init__(self):
        d = orjson.loads((GEO_RAW_DIR / "mun_intermediaria.geojson").read_bytes())
        self.polys = {int(f["properties"]["codarea"]): _polys(f["geometry"]) for f in d["features"]}
        meta = orjson.loads((GEO_RAW_DIR / "mun_metadados.json").read_bytes())
        self.meta = {int(m["id"]): m for m in meta}

    def centroid(self, ibge: int):
        """(lng, lat, origem): centroide do IBGE se estiver dentro do polígono, senão ponto interno."""
        polys = self.polys.get(ibge)
        m = self.meta.get(ibge)
        if polys is None:
            return None
        if m:
            lon, lat = m["centroide"]["longitude"], m["centroide"]["latitude"]
            if contains(polys, [lon], [lat])[0]:
                return lon, lat, "ibge_centroide"
        lon, lat = point_on_surface(polys)
        return round(lon, 5), round(lat, 5), "ibge_ponto_interno"

    def bbox(self, ibge: int):
        polys = self.polys.get(ibge)
        if polys is None:
            return None
        allp = np.vstack([p[0] for p in polys])
        return allp[:, 0].min(), allp[:, 1].min(), allp[:, 0].max(), allp[:, 1].max()


# ---------------------------------------------------------------- saídas do front

def _round_coords(obj, nd):
    if isinstance(obj, list):
        if obj and isinstance(obj[0], (int, float)):
            return [round(v, nd) for v in obj]
        out = [_round_coords(o, nd) for o in obj]
        # remove pontos consecutivos repetidos após arredondar (anéis)
        if out and isinstance(out[0], list) and out[0] and isinstance(out[0][0], (int, float)):
            ded = [out[0]]
            for pt in out[1:]:
                if pt != ded[-1]:
                    ded.append(pt)
            if len(ded) >= 4:
                return ded
        return out
    return obj


def _atomic(path, data: bytes):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def write_front_geo(mun_props: dict[int, dict]):
    """mun_props: cd_mun_ibge -> {cd_mun, cd_mun_ibge, nome, uf, regiao}. Retorna estatísticas."""
    stats = {}
    for src, dst, nd in (("mun_minima.geojson", "municipios_geo.geojson", 4),):
        d = orjson.loads((GEO_RAW_DIR / src).read_bytes())
        feats = []
        for f in d["features"]:
            code = int(f["properties"]["codarea"])
            props = mun_props.get(code)
            if props is None:
                continue
            feats.append({"type": "Feature", "properties": props,
                          "geometry": {"type": f["geometry"]["type"],
                                       "coordinates": _round_coords(f["geometry"]["coordinates"], nd)}})
        out = BASE / dst
        _atomic(out, orjson.dumps({"type": "FeatureCollection", "features": feats}))
        stats[dst] = {"features": len(feats), "bytes": out.stat().st_size}

    # TopoJSON (arcos compartilhados, quantizado pelo IBGE). Objeto: "municipios".
    for src, dst in (("mun_minima.topojson", "municipios_geo.topojson"),
                     ("mun_intermediaria.topojson", "municipios_geo_hd.topojson")):
        t = orjson.loads((GEO_RAW_DIR / src).read_bytes())
        obj_name = next(iter(t["objects"]))
        geoms = []
        for g in t["objects"][obj_name]["geometries"]:
            code = int(g["properties"]["codarea"])
            if code in mun_props:
                g["properties"] = mun_props[code]
                geoms.append(g)
        t["objects"] = {"municipios": {"type": "GeometryCollection", "geometries": geoms}}
        out = BASE / dst
        _atomic(out, orjson.dumps(t))
        stats[dst] = {"features": len(geoms), "bytes": out.stat().st_size, "object": "municipios"}
    stale = BASE / "municipios_geo_hd.geojson"
    if stale.exists():
        stale.unlink()

    d = orjson.loads((GEO_RAW_DIR / "uf_intermediaria.geojson").read_bytes())
    feats = []
    for f in d["features"]:
        cod = int(f["properties"]["codarea"])
        uf = UF_IBGE[cod]
        feats.append({"type": "Feature",
                      "properties": {"uf": uf, "cd_uf_ibge": cod, "nome": UF_NOME[uf], "regiao": UF_REGIAO[uf]},
                      "geometry": {"type": f["geometry"]["type"],
                                   "coordinates": _round_coords(f["geometry"]["coordinates"], 4)}})
    out = BASE / "ufs_geo.geojson"
    _atomic(out, orjson.dumps({"type": "FeatureCollection", "features": feats}))
    stats["ufs_geo.geojson"] = {"features": len(feats), "bytes": out.stat().st_size}
    return stats

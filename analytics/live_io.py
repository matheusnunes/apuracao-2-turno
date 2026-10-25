"""Leitura das saídas do coletor (manifest → agg.<dv>.json + sections.<dv>.bin) e montagem de `Live`."""

from __future__ import annotations

import datetime as dt
import logging
import time
from pathlib import Path

import numpy as np
import orjson

from .base import Base, zone_key
from .model import AGG, IA, Live

log = logging.getLogger("analytics")
BRT = dt.timezone(dt.timedelta(hours=-3))


def read_json(path: Path):
    return orjson.loads(path.read_bytes())


def read_manifest(live_dir: Path) -> dict | None:
    p = live_dir / "manifest.json"
    if not p.exists():
        return None
    try:
        return read_json(p)
    except (OSError, orjson.JSONDecodeError) as e:   # escrita atômica: só se o arquivo sumir no meio
        log.warning("manifest ilegível: %s", e)
        return None


def _agg_row(A: dict) -> np.ndarray:
    return np.array([float(A.get(k, 0) or 0) for k in AGG], dtype=np.float64)


def _parse_zone_key(k: str) -> str | None:
    try:
        a, b = k.split("-")
        return zone_key(int(a), int(b))
    except ValueError:
        return None


def brt_seconds(dmy: str | None, hms: str | None, day0: dt.date | None = None) -> float | None:
    """'dd/mm/yyyy','hh:mm:ss' (BRT) → segundos desde 00:00 BRT de day0 (ou do próprio dia)."""
    if not dmy or not hms:
        return None
    try:
        d = dt.datetime.strptime(f"{dmy} {hms}", "%d/%m/%Y %H:%M:%S")
    except ValueError:
        return None
    ref = dt.datetime.combine(day0 or d.date(), dt.time())
    return (d - ref).total_seconds()


def iso_seconds(iso: str | None, day0: dt.date | None = None) -> float | None:
    if not iso:
        return None
    try:
        t = dt.datetime.fromisoformat(iso)
    except ValueError:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=BRT)
    t = t.astimezone(BRT)
    ref = dt.datetime.combine(day0 or t.date(), dt.time(), tzinfo=BRT)
    return (t - ref).total_seconds()


def build_live(base: Base, agg: dict | None, bu: np.ndarray | None, manifest: dict | None) -> Live:
    Z, M = base.Z, base.M
    zone = np.zeros((Z, len(AGG)))
    zone_has = np.zeros(Z, dtype=bool)
    mun = np.zeros((M, len(AGG)))
    mun_has = np.zeros(M, dtype=bool)
    unknown_z = unknown_m = 0
    br = uf = None
    now_s = None
    zone_t = np.full(Z, np.nan)
    br_t = None
    day0 = None
    if agg and agg.get("br"):
        try:
            day0 = dt.datetime.strptime(agg["br"].get("dt") or "", "%d/%m/%Y").date()
        except ValueError:
            day0 = None
    if agg:
        for k, A in (agg.get("zona") or {}).items():
            zk = _parse_zone_key(k)
            i = base.zone_index.get(zk) if zk else None
            if i is None:
                unknown_z += 1
                continue
            zone[i] = _agg_row(A)
            zone_has[i] = True
            if day0 is not None:
                tz = brt_seconds(A.get("dt"), A.get("ht"), day0)
                if tz is not None:
                    zone_t[i] = tz
        for k, A in (agg.get("mun") or {}).items():
            try:
                i = base.mun_index.get(int(k))
            except ValueError:
                i = None
            if i is None:
                unknown_m += 1
                continue
            mun[i] = _agg_row(A)
            mun_has[i] = True
        if agg.get("br"):
            br = {k: float(agg["br"].get(k, 0) or 0) for k in AGG}
            br["dt"], br["ht"] = agg["br"].get("dt"), agg["br"].get("ht")
            now_s = brt_seconds(br["dt"], br["ht"])
            br_t = now_s
        uf = agg.get("uf")
    if now_s is None and manifest:
        now_s = iso_seconds(manifest.get("generated_at"))
    if unknown_z or unknown_m:
        log.warning("chaves desconhecidas no agg: %d zonas, %d municípios", unknown_z, unknown_m)
    return Live(zone=zone, zone_has=zone_has, mun=mun, mun_has=mun_has, br=br, uf=uf, bu=bu, now_s=now_s,
                meta={"unknown_zones": unknown_z, "unknown_muns": unknown_m},
                zone_t=zone_t if day0 is not None else None, br_t=br_t)


def read_sections(path: Path, n: int) -> np.ndarray | None:
    raw = np.fromfile(path, dtype="<i4")
    if raw.size != n * 8:
        log.warning("sections.bin com %d valores; esperado %d×8. Ignorando BUs.", raw.size, n)
        return None
    arr = raw.reshape(n, 8)
    return arr[:, :7].astype(np.float64)


def load_inputs(base: Base, live_dir: Path, cache: dict, tries: int = 3) -> tuple[Live, dict]:
    """Lê o par consistente (agg, sections) apontado pelo manifest; se a versão sumir no meio (o coletor
    mantém só as 3 últimas), relê o manifest e tenta de novo."""
    for k in range(tries):
        try:
            return _load_inputs(base, live_dir, cache)
        except FileNotFoundError as e:
            log.warning("arquivo versionado sumiu (%s); relendo o manifest (%d/%d)", e, k + 1, tries)
            time.sleep(0.2)
    return _load_inputs(base, live_dir, cache)


def _load_inputs(base: Base, live_dir: Path, cache: dict) -> tuple[Live, dict]:
    """Lê o par consistente (agg, sections) apontado pelo manifest. `cache` evita reler o sections.bin."""
    man = read_manifest(live_dir)
    info = {"manifest_version": None, "agg_version": None, "sections_version": None,
            "generated_at": None, "source": None, "health": None}
    agg = None
    bu = None
    if man:
        info.update(manifest_version=man.get("version"), generated_at=man.get("generated_at"),
                    source=man.get("source"), health=man.get("health"),
                    agg_version=(man.get("data_versions") or {}).get("agg"),
                    sections_version=(man.get("data_versions") or {}).get("sections"),
                    scope=man.get("scope"))
        files = man.get("files") or {}
        idx = man.get("index") or {}
        if idx.get("n_rows") not in (None, base.n):
            log.error("manifest.index.n_rows=%s ≠ base %d: índice divergente, ignorando sections.bin",
                      idx.get("n_rows"), base.n)
            files = {k: v for k, v in files.items() if k != "sections"}
        if files.get("agg"):
            p = live_dir / files["agg"]
            agg = read_json(p)
        if files.get("sections"):
            key = (files["sections"], info["sections_version"])
            if cache.get("sec_key") == key:
                bu = cache.get("sec")
            else:
                p = live_dir / files["sections"]
                if p.exists():
                    bu = read_sections(p, base.n)
                    cache["sec_key"], cache["sec"] = key, bu
    live = build_live(base, agg, bu, man)
    return live, info

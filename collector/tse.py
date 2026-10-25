"""Parsers dos arquivos do TSE (u.json, ab.json, cs.json, aux.json, .jws)."""

from __future__ import annotations

import base64
import datetime as dt

import orjson

from .config import BRT

A_KEYS_INT = ("ts", "st", "te", "est", "c", "a", "vb", "tvn", "l", "o")


def _int(x) -> int:
    if x is None or x == "":
        return 0
    try:
        return int(x)
    except (TypeError, ValueError):
        try:
            return int(str(x).replace(".", "").split(",")[0])
        except ValueError:
            return 0


def loads(body: bytes) -> dict:
    """JSON puro ou JWS compacto (payload em base64url)."""
    b = body.lstrip()
    if b[:1] in (b"{", b"["):
        return orjson.loads(b)
    parts = b.strip().split(b".")
    if len(parts) == 3:
        p = parts[1]
        p += b"=" * (-len(p) % 4)
        return orjson.loads(base64.urlsafe_b64decode(p))
    raise ValueError("corpo não é JSON nem JWS")


def parse_brt(d: str | None, h: str | None) -> dt.datetime | None:
    if not d:
        return None
    try:
        day, mon, year = (int(x) for x in d.split("/"))
        hh, mm, ss = (int(x) for x in (h or "00:00:00").split(":"))
        return dt.datetime(year, mon, day, hh, mm, ss, tzinfo=BRT)
    except (ValueError, TypeError):
        return None


def minutes_since(day0: dt.date, d: str | None, h: str | None) -> int:
    t = parse_brt(d, h)
    if t is None:
        return -1
    base = dt.datetime(day0.year, day0.month, day0.day, tzinfo=BRT)
    return int((t - base).total_seconds() // 60)


def empty_a() -> dict:
    a = {k: 0 for k in A_KEYS_INT}
    a.update(dt="", ht="", **{"and": "n"})
    return a


def parse_u(d: dict, cand_l: int, cand_o: int) -> tuple[dict, dict]:
    """`*-u.json` -> (A do contrato, meta {idg, dg, hg, tpabr, cdabr})."""
    s, e, v = d.get("s") or {}, d.get("e") or {}, d.get("v") or {}
    votes = {}
    for carg in d.get("carg") or []:
        for agr in carg.get("agr") or []:
            for par in agr.get("par") or []:
                for cand in par.get("cand") or []:
                    try:
                        votes[int(cand.get("n"))] = _int(cand.get("vap"))
                    except (TypeError, ValueError):
                        pass
    a = {
        "ts": _int(s.get("ts")), "st": _int(s.get("st")),
        "te": _int(e.get("te")), "est": _int(e.get("est")),
        "c": _int(e.get("c")), "a": _int(e.get("a")),
        "vb": _int(v.get("vb")), "tvn": _int(v.get("tvn")),
        "l": votes.get(cand_l, 0), "o": votes.get(cand_o, 0),
        "dt": d.get("dt") or "", "ht": d.get("ht") or "", "and": d.get("and") or "n",
    }
    meta = {"idg": d.get("idg") or "", "dg": d.get("dg") or "", "hg": d.get("hg") or "",
            "tpabr": d.get("tpabr"), "cdabr": d.get("cdabr")}
    return a, meta


def parse_ab(d: dict) -> tuple[list[dict], dict]:
    """`*-ab.json` -> (entradas, meta). Entrada: tpabr, cdabr, and, dt, ht, ts, st, te, est, c, a."""
    out = []
    for ab in d.get("abr") or []:
        s, e = ab.get("s") or {}, ab.get("e") or {}
        out.append({
            "tpabr": ab.get("tpabr"), "cdabr": str(ab.get("cdabr") or "").lower(),
            "and": ab.get("and") or "n", "dt": ab.get("dt") or "", "ht": ab.get("ht") or "",
            "ts": _int(s.get("ts")), "st": _int(s.get("st")),
            "te": _int(e.get("te")), "est": _int(e.get("est")),
            "c": _int(e.get("c")), "a": _int(e.get("a")),
        })
    meta = {"idg": d.get("idg") or "", "dg": d.get("dg") or "", "hg": d.get("hg") or ""}
    return out, meta


def ab_sig(e: dict) -> tuple:
    return (e.get("st", 0), e.get("c", 0), e.get("dt", ""), e.get("ht", ""))


def parse_cs(d: dict):
    """`{uf}-p{pl6}-cs.json` -> iterável de (mun5, z4, s4, dh|None, principal: bool)."""
    for ab in d.get("abr") or []:
        for mu in ab.get("mu") or []:
            mun5 = f"{int(mu['cd']):05d}"
            for zon in mu.get("zon") or []:
                z4 = f"{int(zon['cd']):04d}"
                for sec in zon.get("sec") or []:
                    ns = sec.get("ns")
                    if ns is None:
                        continue
                    s4 = f"{int(ns):04d}"
                    nsp = sec.get("nsp")
                    principal = nsp is None or int(nsp) == int(ns)
                    da = sec.get("da")
                    dh = f"{da} {sec.get('ha') or ''}".strip() if da else None
                    yield mun5, z4, s4, dh, principal


def parse_aux(d: dict) -> dict:
    """aux.json -> {st, hash, dr, hr, nm, tp, n_hashes} (hash None se nada 'Totalizado')."""
    hashes = d.get("hashes") or []
    best = None
    best_t = None
    for h in hashes:
        if str(h.get("st", "")).strip().lower() != "totalizado":
            continue
        t = parse_brt(h.get("dr"), h.get("hr"))
        if best is None or (t is not None and (best_t is None or t >= best_t)):
            best, best_t = h, t
    out = {"st": d.get("st"), "hash": None, "dr": None, "hr": None, "nm": None, "tp": None,
           "n_hashes": len(hashes), "states": [h.get("st") for h in hashes]}
    if best is None:
        return out
    nm = tp = None
    arqs = best.get("arq") or []
    for want in ("bu", "busa"):
        for f in arqs:
            if str(f.get("tp", "")).lower() == want:
                nm, tp = f.get("nm"), want
                break
        if nm:
            break
    if nm is None:
        for f in arqs:
            n = str(f.get("nm", ""))
            if n.endswith("-bu.dat") or n.endswith("-busa.dat"):
                nm, tp = n, "bu" if n.endswith("-bu.dat") else "busa"
                break
    out.update(hash=best.get("hash"), dr=best.get("dr"), hr=best.get("hr"), nm=nm, tp=tp)
    return out

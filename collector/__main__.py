"""CLI do coletor.

  uv run python -m collector run [opções]     # roda para sempre (reinicia sozinho se cair)
  uv run python -m collector status [opções]  # resumo do estado sqlite + manifest
  uv run python -m collector verify [opções]  # soma dos BUs x agregado oficial (por UF e município)
  uv run python -m collector cs-check --ufs sp,pe   # o cs.json já tem da/ha? (checar às 17:00)
  uv run python -m collector decode ARQ-bu.dat [--eleicao 6257]
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import logging.handlers
import sqlite3
import sys
import time
from collections import Counter, defaultdict

import orjson

from .config import add_run_args, config_from_args

log = logging.getLogger("collector")


def setup_logging(cfg) -> None:
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    fh = logging.handlers.RotatingFileHandler(cfg.log_path, maxBytes=50 * 1024 * 1024, backupCount=5,
                                              encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(fmt)
    root.addHandler(fh)
    root.addHandler(sh)
    root.setLevel(getattr(logging, cfg.log_level.upper(), logging.INFO))
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("hpack").setLevel(logging.WARNING)


def cmd_run(a) -> int:
    from .engine import Engine

    cfg = config_from_args(a)
    setup_logging(cfg)
    crashes = 0
    while True:
        try:
            eng = Engine(cfg)
            asyncio.run(eng.run())
            return 0
        except KeyboardInterrupt:
            return 0
        except Exception:  # noqa: BLE001
            crashes += 1
            log.exception("coletor caiu (%d); reiniciando em 10 s", crashes)
            cfg.reset = False
            time.sleep(10)


def _open_db(cfg):
    if not cfg.db_path.exists():
        raise SystemExit(f"estado não encontrado: {cfg.db_path}")
    return sqlite3.connect(f"file:{cfg.db_path}?mode=ro", uri=True)


def cmd_status(a) -> int:
    from .state import STATUS_NAMES

    cfg = config_from_args(a)
    db = _open_db(cfg)
    print(f"estado: {cfg.db_path}")
    rows = db.execute("SELECT uf, status, has_bu, count(*) FROM sec GROUP BY uf, status, has_bu").fetchall()
    by_uf: dict = defaultdict(Counter)
    for uf, st, hb, n in rows:
        by_uf[uf][STATUS_NAMES.get(st, st)] += n
        by_uf[uf]["_com_bu"] += n if hb else 0
        by_uf[uf]["_total"] += n
    for uf in sorted(by_uf):
        c = by_uf[uf]
        rest = {k: v for k, v in c.items() if not k.startswith("_")}
        print(f"  {uf}: {c['_com_bu']}/{c['_total']} com BU  {rest}")
    fails = db.execute("SELECT key, err FROM sec WHERE status=5 LIMIT 10").fetchall()
    if fails:
        print("falhas de decodificação (até 10):")
        for k, e in fails:
            print(f"  {k}: {e}")
    meta = dict(db.execute("SELECT k, v FROM meta WHERE k IN ('version','dv_agg','dv_sec','tse_last_change','cs_state')"))
    print("meta:", meta)
    mf = cfg.out_dir / "manifest.json"
    if mf.exists():
        m = orjson.loads(mf.read_bytes())
        print("manifest:", orjson.dumps({k: m[k] for k in ("version", "generated_at", "coverage", "data_versions")}).decode())
        print("health:", orjson.dumps(m["health"], option=orjson.OPT_INDENT_2).decode())
    return 0


def cmd_verify(a) -> int:
    """Compara a soma dos BUs decodificados com os agregados oficiais guardados (e com o agg publicado)."""
    cfg = config_from_args(a)
    db = _open_db(cfg)
    aggs: dict = defaultdict(dict)
    for scope, code, data in db.execute("SELECT scope, code, data FROM agg"):
        if data:
            aggs[scope][code] = orjson.loads(data)
    sums: dict = defaultdict(lambda: Counter())
    totals: dict = defaultdict(Counter)
    for uf, mun, hb, aptos, comp, l, o, b, n in db.execute(
            "SELECT uf, mun, has_bu, aptos, comp, l, o, b, n FROM sec"):
        for key in (("uf", uf), ("mun", f"{uf}:{mun}")):
            totals[key]["secoes"] += 1
            if hb:
                c = sums[key]
                c["secoes_bu"] += 1
                c["aptos"] += aptos
                c["c"] += comp
                c["l"] += l
                c["o"] += o
                c["vb"] += b
                c["n"] += n
    ok_all = True
    pub = None
    mf = cfg.out_dir / "manifest.json"
    if mf.exists():
        m = orjson.loads(mf.read_bytes())
        pub = orjson.loads((cfg.out_dir / m["files"]["agg"]).read_bytes())
    print(f"{'escopo':<14}{'seções':>8}{'c/BU':>8}  {'campo':<6}{'Σ BU':>12}{'oficial':>12}  ok")
    keys = sorted(k for k in sums if k[0] == "uf") + sorted(k for k in sums if k[0] == "mun")
    mun_ok = mun_bad = 0
    for key in keys:
        scope, code = key
        A = aggs.get(scope, {}).get(code)
        s = sums[key]
        full = s["secoes_bu"] == totals[key]["secoes"]
        if A is None:
            print(f"{code:<14} sem agregado oficial")
            continue
        fields = [("l", "l"), ("o", "o"), ("vb", "vb"), ("c", "c"), ("aptos", "te")]
        res = []
        for f, fa in fields:
            res.append((f, s[f], A[fa], s[f] == A[fa]))
        good = full and all(r[3] for r in res)
        if scope == "mun":
            mun_ok += good
            mun_bad += not good
            if good and not a.verbose:
                continue
        ok_all &= good if full else True
        for i, (f, v, w, eq) in enumerate(res):
            lab = f"{scope}:{code}" if i == 0 else ""
            print(f"{lab:<14}{totals[key]['secoes'] if i == 0 else '':>8}{s['secoes_bu'] if i == 0 else '':>8}  "
                  f"{f:<6}{v:>12}{w:>12}  {'OK' if eq else ('PARCIAL' if not full else 'DIFERENTE')}")
        if scope == "uf":
            print(f"{'':<30}nulos Σ BU={s['n']} | tvn oficial={A['tvn']} (no 1T tvn inclui nulos técnicos do nº 28)")
        if scope == "uf" and pub is not None:
            P = pub["uf"].get(code)
            same = P == A
            print(f"{'':<30}agg publicado ({mf.parent.name}/{m['files']['agg']}) uf.{code} == oficial guardado: {same}")
            ok_all &= same
    print(f"municípios: {mun_ok} batem 100%, {mun_bad} com diferença ou parciais")
    if pub is not None:
        n_pub_mun = sum(1 for k in pub["mun"])
        print(f"agg publicado: {len(pub['uf'])} UFs, {n_pub_mun} municípios, {len(pub['zona'])} zonas; br.st={pub['br']['st']}")
    print("RESULTADO:", "OK" if ok_all and mun_bad == 0 else "VER DIFERENÇAS")
    return 0 if ok_all and mun_bad == 0 else 1


def cmd_cs_check(a) -> int:
    import httpx

    from . import tse

    cfg = config_from_args(a)
    with httpx.Client(http2=True, headers={"User-Agent": cfg.user_agent, "Accept-Encoding": "gzip"},
                      timeout=60) as c:
        for uf in cfg.ufs:
            url = cfg.url_cs(uf)
            r = c.get(url)
            if r.status_code != 200:
                print(f"{uf}: HTTP {r.status_code}")
                continue
            d = tse.loads(r.content)
            n = dh = 0
            last = ""
            for _, _, _, x, p in tse.parse_cs(d):
                if p:
                    n += 1
                    if x:
                        dh += 1
                        last = max(last, x[6:10] + x[3:5] + x[0:2] + x[10:])
            print(f"{uf}: dg={d.get('dg')} {d.get('hg')} idg={d.get('idg')} Last-Modified={r.headers.get('last-modified')} "
                  f"| {dh}/{n} seções principais com da/ha{(' (mais nova ' + last + ')') if last else ''}")
            time.sleep(0.5)
    return 0


def cmd_decode(a) -> int:
    from .bu import decode_bu

    for f in a.files:
        r = decode_bu(open(f, "rb").read(), a.eleicao)
        print(orjson.dumps(r.__dict__, default=str, option=orjson.OPT_NON_STR_KEYS).decode())
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m collector", description="Coletor ao vivo do TSE (sala de guerra 2T 2026)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn, hlp in (("run", cmd_run, "roda o coletor"), ("status", cmd_status, "resumo do estado"),
                          ("verify", cmd_verify, "Σ BUs x agregado oficial"),
                          ("cs-check", cmd_cs_check, "cs.json tem da/ha?")):
        p = sub.add_parser(name, help=hlp)
        add_run_args(p)
        p.set_defaults(fn=fn)
        if name == "verify":
            p.add_argument("--verbose", action="store_true")
    p = sub.add_parser("decode", help="decodifica BU(s)")
    p.add_argument("files", nargs="+")
    p.add_argument("--eleicao", type=int, default=6258)
    p.set_defaults(fn=cmd_decode)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())

"""Modo ao vivo: lê o manifest do coletor, calcula e publica `analytics.json` (atômico).

Uso:
  uv run python -m analytics.run --live-dir site/data/live --interval 20
  uv run python -m analytics.run --once                      # um ciclo e sai
  uv run python -m analytics.run --base analytics/cache/bt2022_secoes.parquet --live-dir <dir>   # outra base

Só recalcula quando `data_versions.agg` ou `data_versions.sections` mudam (ou na partida). O histórico é
retomado do analytics.json existente e zerado só se a fonte (`source`/`scope`) mudar; se a apuração "voltar"
sem troca de fonte (coletor reiniciado vazio), o ponto fica fora do histórico. `--reset-history` zera à mão.
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import math
import os
import signal
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

import numpy as np
import orjson

from .base import load_base
from .live_io import BRT, load_inputs, read_json
from .model import Params, compute

ROOT = Path(__file__).resolve().parent.parent
log = logging.getLogger("analytics")
SCHEMA_VERSION = 1


def _round(x, nd=4):
    if isinstance(x, float):
        if not math.isfinite(x):
            return None
        return round(x, nd)
    if isinstance(x, dict):
        return {k: _round(v, nd) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_round(v, nd) for v in x]
    if isinstance(x, (np.floating,)):
        return _round(float(x), nd)
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, np.bool_):
        return bool(x)
    return x


def atomic_write(path: Path, data: bytes):
    tmp = path.with_name(f".{path.name}.tmp")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def iso_now() -> str:
    return dt.datetime.now(BRT).isoformat(timespec="seconds")


def hhmmss(sec: float | None) -> str | None:
    if sec is None:
        return None
    s = int(round(sec))
    return f"{s // 3600 % 24:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


class History:
    """Um ponto por minuto (o último do minuto), mais o instante em que o placar bruto virou."""

    def __init__(self, points: list | None = None, ident: dict | None = None):
        self.points = points or []
        self.ident = ident or {}

    def reset_if_needed(self, ident: dict):
        """Zera só quando a fonte (`manifest.source`) ou o escopo mudam."""
        if self.ident and ident != self.ident:
            log.info("histórico zerado: fonte mudou %s → %s", self.ident, ident)
            self.points = []
        self.ident = ident

    def is_drop(self, pct: float) -> bool:
        """Apuração 'voltou' sem troca de fonte (p. ex. coletor reiniciado com estado vazio): não grava o ponto."""
        last = max((p.get("pct_secoes") or 0) for p in self.points) if self.points else 0
        if pct + 1.0 < last:
            log.warning("apuração voltou (%.1f%% < %.1f%%) sem troca de fonte: ponto fora do histórico", pct, last)
            return True
        return False

    def add(self, p: dict):
        key = (p.get("t") or "")[:16]
        if self.points and (self.points[-1].get("t") or "")[:16] == key and not self.points[-1].get("virada"):
            self.points[-1] = p
        else:
            self.points.append(p)
        if len(self.points) > 3000:
            self.points = self.points[-3000:]

    def virada_observada(self, min_pct: float = 2.0) -> dict | None:
        """Última mudança de sinal da margem apurada (ponto anterior e posterior), ignorando o ruído < min_pct."""
        last = None
        prev = None
        for p in self.points:
            m = p.get("margin_apurado")
            if m is None or m == 0 or (p.get("pct_secoes") or 0) < min_pct:
                continue
            if prev is not None and (m > 0) != (prev["margin_apurado"] > 0):
                last = {"t": p["t"], "pct_secoes": p["pct_secoes"], "leader": "L" if m > 0 else "O",
                        "before": {"t": prev["t"], "pct_secoes": prev["pct_secoes"]}}
            prev = p
        return last


def build_output(base, live, info, params, history: History, compute_ms_ref: list, skip_history: bool = False) -> dict:
    t0 = time.perf_counter()
    out = compute(base, live, params, include_zones=True)
    out.pop("_internal", None)
    st = out["status"]
    pr = out["projection"]
    pace = out["pace"]
    ap = out["apurado"]
    t_data = None
    if live.br and live.br.get("dt") and live.br.get("ht"):
        try:
            t_data = dt.datetime.strptime(f"{live.br['dt']} {live.br['ht']}", "%d/%m/%Y %H:%M:%S").replace(
                tzinfo=BRT).isoformat(timespec="seconds")
        except ValueError:
            t_data = None
    t_data = t_data or info.get("generated_at") or iso_now()
    if st["pct_secoes"] > 0 and not skip_history:
        point = {"t": t_data, "pct_secoes": st["pct_secoes"], "pct_aptos": st["pct_aptos"],
                 "margin_apurado": ap["margin_votes"], "margin_proj": pr["margin_votes"],
                 "band": pr["band_votes"], "delta_obs": pace["delta_obs"], "delta_rem": pace["delta_rem"],
                 # extras
                 "margin_apurado_pp": ap["margin_pp_valid"], "margin_proj_pp": pr["margin_pp_valid"],
                 "band_pp": pr["band_pp_valid"], "p_lula": pr["p_lula"], "phase": st["phase"],
                 "delta_obs_band": pace["delta_obs_band"], "input_version": info.get("agg_version")}
        if history.points and st["pct_secoes"] >= params.pre_max_pct:
            prev = history.points[-1].get("margin_apurado") or 0
            if prev and ap["margin_votes"] and (prev > 0) != (ap["margin_votes"] > 0):
                point["virada"] = True
        history.add(_round(point))
    vir = pr["virada"]
    vir["eta"] = hhmmss(vir.get("eta_s"))
    elapsed = (time.perf_counter() - t0) * 1000
    compute_ms_ref.append(elapsed)
    doc = {
        "schema_version": SCHEMA_VERSION,
        "input_version": info.get("agg_version"),
        "computed_at": iso_now(),
        "inputs": {k: info.get(k) for k in ("manifest_version", "agg_version", "sections_version",
                                              "generated_at", "source")} | {"data_time": t_data},
        **out,
        "virada_observada": history.virada_observada(params.pre_max_pct),
        "history": history.points,
        "model": {"params": {k: v for k, v in params.__dict__.items()}, "compute_ms": round(elapsed),
                  "notes": "ver analytics/README.md e analytics/BACKTEST.md"},
    }
    return _round(doc)


def load_history(path: Path) -> History:
    if not path.exists():
        return History()
    try:
        d = read_json(path)
        ident = {"source": (d.get("inputs") or {}).get("source"), "scope": d.get("_scope")}
        return History(d.get("history") or [], ident)
    except Exception as e:  # noqa: BLE001
        log.warning("não consegui retomar o histórico: %s", e)
        return History()


def setup_logging(log_path: Path | None, verbose: bool):
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    log.setLevel(logging.DEBUG if verbose else logging.INFO)
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    log.addHandler(sh)
    if log_path:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        fh = RotatingFileHandler(log_path, maxBytes=20_000_000, backupCount=3)
        fh.setFormatter(fmt)
        log.addHandler(fh)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--live-dir", default=str(ROOT / "site" / "data" / "live"))
    ap.add_argument("--base", default=str(ROOT / "data" / "base" / "secoes.parquet"))
    ap.add_argument("--out", default=None, help="padrão: <live-dir>/analytics.json")
    ap.add_argument("--interval", type=float, default=20.0)
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--force", action="store_true", help="recalcula a cada ciclo mesmo sem mudança de versão")
    ap.add_argument("--log", default=str(ROOT / "analytics" / "logs" / "run.log"))
    ap.add_argument("--reset-history", action="store_true", help="começa o histórico do zero (ensaio recomeçado)")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)
    setup_logging(Path(a.log) if a.log else None, a.verbose)
    live_dir = Path(a.live_dir)
    live_dir.mkdir(parents=True, exist_ok=True)
    out_path = Path(a.out) if a.out else live_dir / "analytics.json"
    t = time.time()
    base, _ = load_base(a.base)
    log.info("base %s: %d seções, %d zonas, %d municípios (%.1fs)", a.base, base.n, base.Z, base.M, time.time() - t)
    params = Params()
    history = History() if a.reset_history else load_history(out_path)
    cache: dict = {}
    last_key = None
    stop = {"flag": False}

    def _stop(*_):
        stop["flag"] = True
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    ms: list = []
    while not stop["flag"]:
        t_cycle = time.time()
        try:
            live, info = load_inputs(base, live_dir, cache)
            key = (info.get("agg_version"), info.get("sections_version"), info.get("source"))
            if a.force or key != last_key or not out_path.exists():
                ident = {"source": info.get("source"), "scope": info.get("scope")}
                pct = 100 * (live.br or {}).get("st", 0) / max((live.br or {}).get("ts", 0) or base.n, 1)
                history.reset_if_needed(ident)
                doc = build_output(base, live, info, params, history, ms, skip_history=history.is_drop(pct))
                doc["_scope"] = info.get("scope")
                atomic_write(out_path, orjson.dumps(doc, option=orjson.OPT_SERIALIZE_NUMPY))
                last_key = key
                s, p = doc["status"], doc["projection"]
                if s.get("inconsistent_inputs"):
                    log.warning("ENTRADAS INCONSISTENTES (zonas/mun × oficial da UF) em %s: usando o oficial da UF",
                                ",".join(s["inconsistent_inputs"]))
                log.info("v%s agg=%s sec=%s %.1f%% seções | fase %s | proj %+.2f p.p. [%+.2f, %+.2f] P(L)=%s | "
                         "δobs %+.2f δreq %+.2f δrem %s | %d ms",
                         info.get("manifest_version"), info.get("agg_version"), info.get("sections_version"),
                         s["pct_secoes"], s["phase"], p["margin_pp_valid"] or 0,
                         *(p["band_pp_valid"] or [0, 0]), p["p_lula"], doc["pace"]["delta_obs"],
                         doc["pace"]["delta_req"], doc["pace"]["delta_rem"], ms[-1])
        except Exception:  # noqa: BLE001  nunca cair: loga e tenta no próximo ciclo
            log.exception("ciclo falhou")
        if a.once:
            break
        dt_ = time.time() - t_cycle
        for _ in range(int(max(1, (a.interval - dt_) * 10))):
            if stop["flag"]:
                break
            time.sleep(0.1)
    log.info("analytics encerrado")


if __name__ == "__main__":
    main()

"""Mede a latência da pista AGG: mudança no "TSE" -> agg.<dv>.json publicado.

Usa o TSE falso local (sem CDN) com as cadências de produção (br 30 s, UF 60 s,
publicação 15 s). No TSE real some-se a idade do cache da CDN (0-60 s).

  uv run python -m collector.tests.latency_bench --changes 8 --out /caminho/scratch
"""

from __future__ import annotations

import argparse
import asyncio
import random
import statistics
import time
from pathlib import Path

import orjson

from collector.config import Config
from collector.engine import Engine
from collector.tests.fake_tse import FakeTSE
from collector.tests.test_engine_fake import D, E, Scenario, published


async def main(n_changes: int, out: Path, seed: int):
    random.seed(seed)
    fake = FakeTSE()
    url = await fake.start()
    sc = Scenario(fake, strip_dh=True)
    sc.setup_static()
    z1 = z2 = 0
    sc.phase(0, 0, 0)
    cfg = Config(base_url=url, eleicao=E, pleito=3220, ufs=["pe"], muns={"23000", "99999"},
                 bu_enabled=False, source="mock", state_dir=out / "state", out_dir=out / "out", reset=True)
    eng = Engine(cfg)
    task = asyncio.create_task(eng.run())
    await asyncio.sleep(20)
    lat_mun, lat_zone = [], []
    n_qx = 0
    for i in range(n_changes):
        await asyncio.sleep(random.uniform(20, 70))
        n_qx = min(21, n_qx + 2)
        if i % 2 == 0:
            z1 = min(10, z1 + 1)
        else:
            z2 = min(10, z2 + 1)
        t0 = time.time()
        sc.phase(n_qx, z1, z2)
        want_m, want_z = n_qx, (z1, z2)
        got_m = got_z = None
        while time.time() - t0 < 180 and (got_m is None or got_z is None):
            try:
                _, agg = published(cfg.out_dir)
                if got_m is None and agg["mun"]["23000"]["st"] == want_m:
                    got_m = time.time() - t0
                if got_z is None and (agg["zona"].get("99999-0001", {}).get("st"),
                                      agg["zona"].get("99999-0002", {}).get("st")) == want_z:
                    got_z = time.time() - t0
            except (FileNotFoundError, KeyError, orjson.JSONDecodeError):
                pass
            await asyncio.sleep(0.2)
        lat_mun.append(got_m)
        lat_zone.append(got_z)
        print(f"mudança {i + 1}: município {got_m:.1f}s | zona {got_z:.1f}s", flush=True)
    m, _ = published(cfg.out_dir)
    eng.stop.set()
    await task
    await fake.stop()
    for name, v in (("município", lat_mun), ("zona", lat_zone)):
        v = [x for x in v if x is not None]
        print(f"{name}: n={len(v)} média={statistics.mean(v):.1f}s mediana={statistics.median(v):.1f}s "
              f"máx={max(v):.1f}s")
    print("health.agg_latency_s:", m["health"]["agg_latency_s"], "agg_lag_s:", m["health"]["agg_lag_s"])


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--changes", type=int, default=8)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args()
    asyncio.run(main(a.changes, a.out, a.seed))

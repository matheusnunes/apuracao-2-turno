"""Coletor ponta a ponta contra um TSE falso (aiohttp) com BUs reais de Quixaba/PE (1T).

Cenário: fase 0 (nada apurado) -> fase 1 (8 seções + município multizona parcial)
-> reinício do coletor -> fase 2 (tudo). Verifica estratégia B (sondagem com 404 e
backoff), pista AGG (município/zona), versões publicadas, soma dos BUs = oficial e
ausência de trabalho duplicado após reinício.
"""

from __future__ import annotations

import asyncio
import copy
import time
from pathlib import Path

import numpy as np
import orjson
import pyarrow.parquet as pq

from collector import tse
from collector.bu import decode_bu
from collector.config import ROOT, Config
from collector.engine import Engine
from collector.tests.fake_tse import FakeTSE

S = ROOT / "research" / "samples" / "t1"
QX = S / "mun-pe-23000"
E, P = 6257, 3220
D = f"ele2026/{E}/dados"


def u_doc(tpabr, cd, ts, st, te, est, c, vb=0, tvn=0, l=0, o=0, idg=1, and_="n"):
    return {"ele": str(E), "t": "1", "f": "o", "tpabr": tpabr, "cdabr": cd, "dg": "04/10/2026",
            "hg": f"18:{idg // 60:02d}:{idg % 60:02d}", "idg": str(1000 + idg), "dt": "04/10/2026", "ht": "18:00:00",
            "and": and_, "s": {"ts": str(ts), "st": str(st)},
            "e": {"te": str(te), "est": str(est), "c": str(c), "a": str(est - c)},
            "v": {"vb": str(vb), "tvn": str(tvn)},
            "carg": [{"cd": "1", "agr": [{"par": [{"cand": [{"n": "13", "vap": str(l)}, {"n": "22", "vap": str(o)}]}]}]}]}


def ab_entry(tpabr, cd, ts, st, te, est, c, ht="18:00:00"):
    return {"tpabr": tpabr, "cdabr": cd, "and": "f" if st == ts else ("n" if st == 0 else "p"),
            "dt": "04/10/2026", "ht": ht, "s": {"ts": str(ts), "st": str(st)},
            "e": {"te": str(te), "est": str(est), "c": str(c), "a": str(est - c)}}


class Scenario:
    def __init__(self, fake: FakeTSE, strip_dh=True):
        self.f = fake
        self.aux = {}
        for p in sorted(QX.glob("*-aux.json")):
            s4 = p.name.split("-s")[1][:4]
            self.aux[s4] = p.read_bytes()
        self.bu = {}
        for s4, body in self.aux.items():
            a = tse.parse_aux(tse.loads(body))
            self.bu[s4] = (a["hash"], a["nm"], (QX / a["nm"]).read_bytes())
        self.dec = {s4: decode_bu(b, E) for s4, (_, _, b) in self.bu.items()}
        # ordem de chegada no 1T (como o mock faria)
        t = pq.read_table(ROOT / "data/base/secoes.parquet", columns=["uf", "cd_mun", "zona", "secao", "t_tot1"]).to_pydict()
        pr = {f"{s:04d}": tt for uf, m, z, s, tt in zip(t["uf"], t["cd_mun"], t["zona"], t["secao"], t["t_tot1"])
              if uf == "pe" and m == 23000}
        self.order = sorted(self.aux, key=lambda s: pr.get(s, 1e18))
        cs = orjson.loads((S / "pe-p003220-cs.json").read_bytes())
        mu = [m for m in cs["abr"][0]["mu"] if m["cd"] == "23000"]
        mu = copy.deepcopy(mu)
        if strip_dh:
            for z in mu[0]["zon"]:
                for s in z["sec"]:
                    s.pop("da", None)
                    s.pop("ha", None)
        cs["abr"][0]["mu"] = mu
        self.cs = cs
        self.idg = 0

    def setup_static(self):
        cm = {"dg": "x", "abr": [{"cd": "pe", "mu": [{"cd": "23000", "z": ["0098"]},
                                                       {"cd": "99999", "z": ["0001", "0002"]}]}]}
        self.f.put(f"ele2026/{E}/config/mun-e00{E}-cm.json", orjson.dumps(cm))
        self.f.put(f"ele2026/arquivo-urna/{P}/config/pe/pe-p00{P}-cs.json", orjson.dumps(self.cs))

    def phase(self, n_qx: int, z1: int, z2: int):
        """n_qx seções de Quixaba publicadas (ordem 1T); zonas do mun 99999 com z1/z2 de 10 totalizadas."""
        self.idg += 1
        pub = self.order[:n_qx]
        for s4 in self.order:
            base = f"ele2026/arquivo-urna/{P}/dados/pe/23000/0098/{s4}"
            h, nm, body = self.bu[s4]
            if s4 in pub:
                self.f.put(f"{base}/p00{P}-pe-m23000-z0098-s{s4}-aux.json", self.aux[s4])
                self.f.put(f"{base}/{h}/{nm}", body)
            else:
                self.f.delete(f"{base}/p00{P}-pe-m23000-z0098-s{s4}-aux.json")
        r = [self.dec[s] for s in pub]
        qx = dict(te=6216, est=sum(x.aptos for x in r), c=sum(x.comparecimento for x in r),
                  vb=sum(x.brancos for x in r), tvn=sum(x.nulos for x in r),
                  l=sum(x.votos.get(13, 0) for x in r), o=sum(x.votos.get(22, 0) for x in r))
        self.qx = qx
        self.f.put(f"{D}/pe/pe23000-c0001-e00{E}-u.json", orjson.dumps(
            u_doc("mu", "23000", 21, n_qx, 6216, qx["est"], qx["c"], qx["vb"], qx["tvn"], qx["l"], qx["o"], self.idg)))
        zs = []
        for zi, st in ((1, z1), (2, z2)):
            zs.append(dict(st=st, c=st * 80, l=st * 40, o=st * 30))
            self.f.put(f"{D}/pe/pe99999-z000{zi}-c0001-e00{E}-u.json", orjson.dumps(
                u_doc("zona", f"000{zi}", 10, st, 1000, st * 100, st * 80, 0, 0, st * 40, st * 30, self.idg)))
        mst = z1 + z2
        self.f.put(f"{D}/pe/pe99999-c0001-e00{E}-u.json", orjson.dumps(
            u_doc("mu", "99999", 20, mst, 2000, mst * 100, mst * 80, 0, 0, mst * 40, mst * 30, self.idg)))
        ufst = n_qx + mst
        ufc = qx["c"] + mst * 80
        self.f.put(f"{D}/pe/pe-c0001-e00{E}-u.json", orjson.dumps(
            u_doc("uf", "pe", 41, ufst, 8216, qx["est"] + mst * 100, ufc, qx["vb"], qx["tvn"],
                  qx["l"] + mst * 40, qx["o"] + mst * 30, self.idg)))
        ab = {"ele": str(E), "abr": [ab_entry("uf", "pe", 41, ufst, 8216, qx["est"] + mst * 100, ufc),
                                     ab_entry("mun", "23000", 21, n_qx, 6216, qx["est"], qx["c"], f"18:{self.idg:02d}:00"),
                                     ab_entry("mun", "99999", 20, mst, 2000, mst * 100, mst * 80, f"18:{self.idg:02d}:00")]}
        self.f.put(f"{D}/pe/pe-e00{E}-ab.json", orjson.dumps(ab))
        self.f.put(f"{D}/br/br-e00{E}-ab.json", orjson.dumps(
            {"ele": str(E), "abr": [ab_entry("uf", "pe", 41, ufst, 8216, qx["est"] + mst * 100, ufc, f"18:{self.idg:02d}:00")]}))
        self.f.put(f"{D}/br/br-c0001-e00{E}-u.json", orjson.dumps(
            u_doc("br", "br", 41, ufst, 8216, qx["est"] + mst * 100, ufc, qx["vb"], qx["tvn"],
                  qx["l"] + mst * 40, qx["o"] + mst * 30, self.idg)))


def make_cfg(url, tmp: Path, strategy="zonas") -> Config:
    return Config(base_url=url, eleicao=E, pleito=P, ufs=["pe"], muns={"23000", "99999"},
                  agg_concurrency=2, bu_concurrency=2, agg_rate=200, bu_rate=200, probe_rate=200,
                  start_rate=100, l0_interval=0.5, uf_interval=1.0, mun_min_interval=0.3, zone_min_interval=0.3,
                  cs_interval=1.0, cs_idle_interval=1.0, publish_interval=0.3, probe_min_interval=0.5,
                  probe_max_interval=1.5, strategy=strategy, source="mock",
                  state_dir=tmp / "state", out_dir=tmp / "out")


def published(out: Path):
    m = orjson.loads((out / "manifest.json").read_bytes())
    agg = orjson.loads((out / m["files"]["agg"]).read_bytes())
    return m, agg


async def wait_for(pred, timeout=30.0):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        try:
            if pred():
                return True
        except (FileNotFoundError, KeyError, orjson.JSONDecodeError):
            pass
        await asyncio.sleep(0.1)
    raise AssertionError("timeout esperando condição")


def test_end_to_end_strategy_b_with_restart(tmp_path):
    async def go():
        fake = FakeTSE()
        url = await fake.start()
        sc = Scenario(fake, strip_dh=True)
        sc.setup_static()
        sc.phase(0, 0, 0)
        cfg = make_cfg(url, tmp_path)
        eng = Engine(cfg)
        task = asyncio.create_task(eng.run())
        out = cfg.out_dir
        # fase 0: publica zero, não sonda nada (st=0)
        await wait_for(lambda: published(out)[1]["mun"]["23000"]["st"] == 0)
        await asyncio.sleep(1.5)
        assert fake.count("-aux.json") == 0
        assert fake.count("pe99999-z") == 0          # zona só é buscada quando o município muda
        # fase 1
        t1 = time.time()
        sc.phase(8, 5, 0)
        await wait_for(lambda: published(out)[1]["zona"].get("99999-0001", {}).get("st") == 5)
        lat1 = time.time() - t1
        await wait_for(lambda: published(out)[1]["mun"]["23000"]["st"] == 8 and eng.n_bu == 8, 30)
        m, agg = published(out)
        assert agg["uf"]["pe"]["st"] == 13 and agg["br"]["st"] == 13
        assert agg["zona"]["23000-0098"] == agg["mun"]["23000"]          # zona única = município
        assert m["health"]["strategy"] == "zonas"
        assert m["index"]["n_rows"] == 499248 and len(m["index"]["sha256"]) == 64
        assert fake.count("pe99999-z0002") >= 1
        # sondou além das 8 publicadas (quota = 8*1.5), recebendo 404s
        await wait_for(lambda: eng.bu.metrics.counts[("aux", "404")] >= 1, 10)
        # sem trabalho duplicado: cada BU baixado 1x
        bu_hits = {s4: fake.count(sc.bu[s4][1]) for s4 in sc.order[:8]}
        assert set(bu_hits.values()) == {1}, bu_hits
        await asyncio.sleep(0.5)
        eng.stop.set()
        await task
        aux_before = {s4: fake.count(f"s{s4}-aux.json") for s4 in sc.order[:8]}

        # reinício: mesmo estado
        eng2 = Engine(cfg)
        assert eng2.dv_agg >= 1
        task2 = asyncio.create_task(eng2.run())
        await asyncio.sleep(1.5)
        assert eng2.n_bu == 8
        sc.phase(21, 10, 10)
        await wait_for(lambda: eng2.n_bu == 21, 40)
        await wait_for(lambda: published(out)[1]["mun"]["23000"]["st"] == 21
                       and published(out)[0]["coverage"]["secoes_com_bu"] == 21, 10)
        m, agg = published(out)
        eng2.stop.set()
        await task2
        await fake.stop()

        # nada refeito para as 8 já capturadas
        assert {s4: fake.count(f"s{s4}-aux.json") for s4 in sc.order[:8]} == aux_before
        assert {s4: fake.count(sc.bu[s4][1]) for s4 in sc.order[:8]} == bu_hits
        assert all(fake.count(sc.bu[s4][1]) == 1 for s4 in sc.order)
        # soma dos BUs = oficial de Quixaba (u.json real do 1T)
        A, _ = tse.parse_u(orjson.loads((S / "pe23000-c0001-e006257-u.json").read_bytes()), 13, 22)
        assert agg["mun"]["23000"]["l"] == A["l"] == 4270 and agg["mun"]["23000"]["o"] == A["o"] == 594
        sec = np.frombuffer((out / m["files"]["sections"]).read_bytes(), dtype="<i4").reshape(-1, 8)
        assert sec.shape == (499248, 8)
        rows = sec[sec[:, 0] == 1]
        assert len(rows) == 21
        assert rows[:, 3].sum() == 4270 and rows[:, 4].sum() == 594
        assert rows[:, 1].sum() == A["te"] and rows[:, 2].sum() == A["c"]
        assert rows[:, 5].sum() == A["vb"] and rows[:, 6].sum() == A["tvn"]
        assert (rows[:, 7] > 18 * 60).all()                  # t_recv_min (day0=04/10)
        assert (sec[sec[:, 0] == 0][:, 7] == -1).all()
        assert agg["zona"]["99999-0002"]["st"] == 10 and agg["mun"]["99999"]["st"] == 20
        # versões: no máx. 3 de cada
        assert len(list(out.glob("agg.*.json"))) <= 3 and len(list(out.glob("sections.*.bin"))) <= 3
        assert m["version"] > m["data_versions"]["agg"] >= 2
        return lat1

    lat = asyncio.run(go())
    print(f"latência mudança->agg publicado (cadências de teste): {lat:.2f}s")


def test_strategy_a_cs_dh(tmp_path):
    """cs.json com da/ha: busca direta (sem 404 de sondagem)."""
    async def go():
        fake = FakeTSE()
        url = await fake.start()
        sc = Scenario(fake, strip_dh=False)
        sc.setup_static()
        sc.phase(21, 10, 10)
        cfg = make_cfg(url, tmp_path, strategy="auto")
        cfg.exit_when_done = True
        cfg.max_runtime = 40
        eng = Engine(cfg)
        await eng.run()
        await fake.stop()
        assert eng.n_bu == 21
        assert eng.bu.metrics.counts.get(("aux", "404"), 0) == 0
        m, agg = published(cfg.out_dir)
        assert m["health"]["strategy"] == "cs"
        assert m["coverage"]["secoes_com_bu"] == 21
    asyncio.run(go())


def test_agg_429_on_one_mun_does_not_block_lane(tmp_path):
    """Uma URL de município presa em 429 (sem Retry-After): a pista AGG não pausa, o BR segue
    sendo consultado, a URL recua sozinha mas continua pendente (agg_lag honesto) e é buscada
    assim que o 429 passa, sem precisar de um novo Δst."""
    async def go():
        fake = FakeTSE()
        url = await fake.start()
        sc = Scenario(fake, strip_dh=True)
        sc.setup_static()
        sc.phase(0, 0, 0)
        bad = f"{D}/pe/pe99999-c0001-e00{E}-u.json"
        fake.status_override[bad] = 429
        cfg = make_cfg(url, tmp_path)
        cfg.bu_enabled = False
        cfg.fail_backoff = 0.2
        eng = Engine(cfg)
        task = asyncio.create_task(eng.run())
        out = cfg.out_dir
        await wait_for(lambda: published(out)[1]["mun"]["23000"]["st"] == 0)
        sc.phase(8, 5, 0)
        br0 = fake.count("br-c0001")
        await wait_for(lambda: published(out)[1]["mun"]["23000"]["st"] == 8
                       and published(out)[1]["zona"].get("99999-0001", {}).get("st") == 5)
        await asyncio.sleep(3.0)
        m = eng.muns[("pe", "99999")]
        hits = fake.count("pe99999-c0001")
        assert 3 <= hits <= 8                                    # recuo exponencial por URL
        assert m in eng.need_muns and m.fetch_fail >= 3          # continua pendente
        assert eng.agg.gate.lane_pauses == 0 and eng.agg.gate.paused_for() == 0
        assert fake.count("br-c0001") - br0 >= 5                 # BR continuou (a cada 0,5 s)
        h = published(out)[0]["health"]
        assert h["lanes"]["agg"]["paused_until"] is None
        assert h["agg_lag_s"] >= 2.5                              # o atraso aparece na métrica
        # o 429 passa: a URL é buscada sem precisar de novo Δst
        del fake.status_override[bad]
        await wait_for(lambda: published(out)[1]["mun"]["99999"]["st"] == 5, 15)
        await wait_for(lambda: published(out)[0]["health"]["agg_lag_s"] == 0.0, 10)
        sc.phase(21, 10, 10)
        await wait_for(lambda: published(out)[1]["mun"]["99999"]["st"] == 20, 15)
        eng.stop.set()
        await task
        await fake.stop()
    asyncio.run(go())


def test_mun_reset_refetches(tmp_path):
    """Estado velho de outra rodada (st do ab despencou): o município é rebuscado, inclusive zonas 'completas'."""
    e = Engine(make_cfg("http://127.0.0.1:9/oficial", tmp_path))
    m = e.get_mun("pe", "99999")
    z1, z2 = e.get_zone("pe", "99999", "0001"), e.get_zone("pe", "99999", "0002")
    full = {"ts": 10, "st": 10, "te": 1000, "est": 1000, "c": 800, "a": 200, "vb": 0, "tvn": 0, "l": 1, "o": 1,
            "dt": "", "ht": "", "and": "f"}
    m.A = dict(full, ts=20, st=20)
    z1.A, z2.A = dict(full), dict(full)
    m.ab = {"tpabr": "mun", "cdabr": "99999", "and": "p", "dt": "", "ht": "", "ts": 20, "st": 2,
            "te": 2000, "est": 0, "c": 150, "a": 0}
    e.on_mun_ab(m)
    assert m in e.need_muns and z1 in e.need_zones and z2 in e.need_zones and m.resetting
    # ab só um pouco atrás do u.json (cache da CDN): nada
    e.need_muns.clear(); e.need_zones.clear(); m.resetting = False
    m.ab = dict(m.ab, st=19, c=1500)
    e.on_mun_ab(m)
    assert m not in e.need_muns

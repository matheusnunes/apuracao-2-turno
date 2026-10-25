"""Portão das pistas: prioridade, taxa, pausa de 403/429 por pista; fila round-robin."""

import asyncio
import time

from collector.model import RRQueue, Sec, Zone
from collector.net import Gate


def test_priority_and_rate():
    async def go():
        g = Gate("t", concurrency=1, max_rate=50, start_rate=50)
        g.start()
        order = []

        async def req(p, tag):
            await g.acquire(p)
            order.append(tag)
            await asyncio.sleep(0.01)
            g.release()

        tasks = [asyncio.create_task(req(6, f"bu{i}")) for i in range(5)]
        await asyncio.sleep(0)
        tasks += [asyncio.create_task(req(0, "br")), asyncio.create_task(req(2, "mun"))]
        t0 = time.monotonic()
        await asyncio.gather(*tasks)
        dt = time.monotonic() - t0
        # o primeiro bu pode ter saído antes; br e mun passam na frente dos demais
        assert order.index("br") <= 1 and order.index("mun") <= 2
        assert dt >= 6 / 50 * 0.5
    asyncio.run(go())


def _clock(monkeypatch):
    import collector.net as net
    T = [0.0]
    monkeypatch.setattr(net.time, "monotonic", lambda: T[0])
    monkeypatch.setattr(net.time, "time", lambda: 1.8e9 + T[0])
    return T


def _simulate(g, T, seconds, rps, p_err, rng, hard_share=0.5, burst=None):
    """Tráfego a `rps` com fração `p_err` de erros aleatórios (metade 429, metade 5xx);
    `burst=(t0, t1)`: 100% 403 nessa janela. Devolve (segundos pausada, menor taxa)."""
    paused = 0.0
    low = g.rate
    i = 0
    dt = 1.0 / rps
    while T[0] < seconds:
        if g.paused_for() > 0:
            paused += dt
        else:
            i += 1
            url = f"u{i}"
            if burst and burst[0] <= T[0] < burst[1]:
                g.on_result("hard", url=url, status=403)
            elif rng.random() < p_err:
                if rng.random() < hard_share:
                    g.on_result("hard", url=url, status=429)
                else:
                    g.on_result("soft", url=url, status=503)
            else:
                g.on_result("ok")
            low = min(low, g.rate)
        T[0] += dt
    return paused, low


def test_random_2pct_errors_never_pause_or_collapse(monkeypatch):
    """Ensaio final: --error-rate 0.02 (429/5xx aleatórios) não pode pausar nem derrubar as pistas."""
    import random
    for name, policy, rate in (("agg", "escalate", 20.0), ("bu", "fixed600", 10.0)):
        T = _clock(monkeypatch)
        g = Gate(name, 6, rate, start_rate=rate, hard_policy=policy)
        paused, low = _simulate(g, T, 3600, rate, 0.02, random.Random(1))
        assert paused == 0 and g.lane_pauses == 0, (name, paused)
        assert low >= 0.6 * rate, (name, low)


def test_bu_burst_403_escalates_then_recovers(monkeypatch):
    import random
    T = _clock(monkeypatch)
    g = Gate("bu", 6, 10.0, start_rate=10.0, hard_policy="fixed600")
    # 2% de ruído + bloqueio total de 403 entre 600 s e 900 s (5 min, como --burst-403)
    paused, _ = _simulate(g, T, 900, 10.0, 0.02, random.Random(2), burst=(600, 900))
    assert g.lane_pauses >= 1 and g.pause_level >= 1
    assert g.pause_steps == (60.0, 120.0, 300.0, 600.0)
    # tempestades seguidas escalonam até 600 s
    levels = []
    for _ in range(5):
        T[0] += g.paused_for() + 0.1
        for k in range(6):
            g.on_result("hard", url=f"x{T[0]}-{k}", status=403)
        levels.append(g.paused_for())
    assert levels == [120.0, 300.0, 600.0, 600.0, 600.0] or levels[-1] == 600.0, levels
    # 10 min sem tempestade: volta ao primeiro degrau
    T[0] += g.paused_for() + 601
    g.on_result("ok")
    for k in range(6):
        g.on_result("hard", url=f"y{k}", status=403)
    assert g.paused_for() == 60.0


def test_bu_isolated_403_does_not_pause(monkeypatch):
    T = _clock(monkeypatch)
    g = Gate("bu", 6, 10.0, hard_policy="fixed600")
    for _ in range(100):
        g.on_result("ok")
    assert not g.on_result("hard", url="a", status=403, retry_after=99999)
    assert g.paused_for() == 0


def test_agg_single_url_never_pauses_lane(monkeypatch):
    """Uma URL de município presa em 403 (re-tentada a cada 15 s, 1 h) não pausa a pista nem derruba a taxa."""
    T = _clock(monkeypatch)
    g = Gate("agg", 6, 20.0, start_rate=15.0, hard_policy="escalate")
    paused = 0.0
    while T[0] < 3600:
        if g.paused_for() > 0:
            paused += 0.5
        else:
            for _ in range(5):
                g.on_success()
            if T[0] % 15 == 0:
                g.on_congestion(hard=True, url="u/pe99999-c0001.json", status=403)
        T[0] += 0.5
    assert paused == 0 and g.lane_pauses == 0 and g.rate > 15


def test_agg_storm_escalates_and_caps(monkeypatch):
    T = _clock(monkeypatch)
    g = Gate("agg", 6, 20.0, start_rate=16.0, hard_policy="escalate")
    for _ in range(50):
        g.on_result("ok")
    assert not g.on_result("hard", url="a", status=429)     # isolado: nada
    assert g.paused_for() == 0 and g.rate >= 16.0
    for k in range(40):                                       # 429 em massa, URLs distintas
        if g.on_result("hard", url=f"b{k}", status=429):
            break
    assert 3 <= k < 20                                        # pausa assim que >= 20% da janela é 429
    assert g.paused_for() == 15.0
    pauses = []
    for _ in range(4):
        T[0] += g.paused_for() + 1
        for k in range(4):
            g.on_result("hard", url=f"z{T[0]}-{k}", status=429)
        pauses.append(g.paused_for())
    assert pauses == [30.0, 60.0, 60.0, 60.0]
    T[0] += 61
    for k in range(4):
        g.on_result("hard", url=f"r{k}", retry_after=3600, status=429)
    assert g.paused_for() == 60.0                             # Retry-After limitado a 60 s
    T[0] += 1000                                              # 5 min quietos: volta a 15 s
    g.on_result("ok")
    for k in range(4):
        g.on_result("hard", url=f"q{k}", status=429)
    assert g.paused_for() == 15.0


def test_br_uf_bypass_pause():
    """Com a pista pausada, br/UF esperam no máximo um teto de pausa; município/zona continuam esperando."""
    async def go():
        g = Gate("agg", 2, 50, start_rate=50, pause_cap=0.3)
        g.start()
        g.paused_until = time.monotonic() + 10
        t0 = time.monotonic()
        mun = asyncio.create_task(g.acquire(2))
        await asyncio.wait_for(g.acquire(0), 2.0)
        assert 0.25 <= time.monotonic() - t0 < 1.0
        g.release()
        await asyncio.sleep(0.5)
        assert not mun.done()
        mun.cancel()
    asyncio.run(go())


def test_rr_queue_breadth():
    q = RRQueue()
    zs = {k: Zone(*k) for k in [("sp", "71072", "0001"), ("sp", "71072", "0002"), ("ac", "01392", "0001")]}
    i = 0
    for k, z in zs.items():
        for n in range(3):
            s = Sec(i, f"{k}-{n}", k[0], k[1], k[2], f"{n:04d}")
            s.zone = z
            q.push(s)
            i += 1
    out = [q.pop() for _ in range(9)]
    assert q.pop() is None
    ufs = [s.uf for s in out[:4]]
    assert ufs.count("ac") == 2 and ufs.count("sp") == 2      # alterna UFs
    sp = [s.zona for s in out if s.uf == "sp"]
    assert sp[:2] == ["0001", "0002"]                          # alterna zonas dentro da UF

"""Lógica da pista AGG sem rede: BR congelado -> soma das UFs, detecção de mudança por (st, c)."""

import time

from collector.config import ALL_UFS, Config
from collector.engine import Engine, a_from_ab


def A(st, ts=100, c=0, l=0, o=0, and_="p"):
    return {"ts": ts, "st": st, "te": ts * 300, "est": st * 300, "c": c, "a": st * 300 - c, "vb": 1, "tvn": 2,
            "l": l, "o": o, "dt": "25/10/2026", "ht": f"19:{st % 60:02d}:00", "and": and_}


def eng(tmp_path, **kw):
    return Engine(Config(state_dir=tmp_path / "s", out_dir=tmp_path / "o", source="mock", **kw))


def test_br_frozen_rebuilds_from_ufs(tmp_path):
    e = eng(tmp_path)
    for i, uf in enumerate(ALL_UFS):
        e.ufs[uf].A = A(10, l=5, o=4)
    e.br_A = A(200, ts=2800)
    # UFs à frente mas o BR acabou de mudar (caches de CDN independentes): publica o mais adiantado,
    # sem acusar congelamento
    e.br_content_at = time.time() - 30
    agg, info = e.build_agg()
    assert not info["br_frozen"] and info["br_source"] == "uf_sum"
    assert agg["br"]["st"] == 280 and agg["br"]["l"] == 5 * 28 and agg["br"]["ts"] == 2800
    # BR sem mudar (idg/dg/hg) há > 120 s e UFs à frente: congelado
    e.br_content_at = time.time() - 121
    agg, info = e.build_agg()
    assert info["br_frozen"] and info["br_source"] == "uf_sum"
    e.br_A = A(300, ts=2800)
    agg, info = e.build_agg()
    assert not info["br_frozen"] and agg["br"]["st"] == 300 and info["br_source"] == "br"


def test_zone_share_and_aging_prevent_starvation(tmp_path):
    e = eng(tmp_path, ufs=["sp", "pe"])
    now = time.time()
    # 40 municípios pequenos com Δst grande e recém-mudados (score alto)
    for i in range(40):
        m = e.get_mun("pe", f"{20000 + i:05d}")
        e.get_zone("pe", m.mun5, "0001")
        m.ab = {"tpabr": "mun", "cdabr": m.mun5, "and": "p", "dt": "", "ht": "", "ts": 100, "st": 80,
                "te": 30000, "est": 0, "c": 1, "a": 0}
        m.need_since = now
        e.need_muns.add(m)
    # capital multizona: município já buscado (score baixo), 20 zonas pendentes
    cap = e.get_mun("sp", "71072")
    zs = [e.get_zone("sp", "71072", f"{i:04d}") for i in range(1, 21)]
    cap.ab = {"tpabr": "mun", "cdabr": "71072", "and": "p", "dt": "", "ht": "", "ts": 2000, "st": 1000,
              "te": 600000, "est": 0, "c": 1, "a": 0}
    cap.A = A(1000, ts=2000, c=1)
    for z in zs:
        z.need_since = now
        e.need_zones.add(z)
    batch = e.select_fetches(10)
    assert len(batch) == 10
    assert sum(1 for k, _ in batch if k == 1) >= 4          # fatia reservada às zonas
    # envelhecimento: zona pendente há 5 min passa à frente de município recém-mudado
    e.cfg.zone_share = 0.0
    zs[0].need_since = now - 300
    batch = e.select_fetches(3)
    assert (1, zs[0]) in batch


def test_subset_never_rebuilds_br(tmp_path):
    e = eng(tmp_path, ufs=["rr"])
    e.ufs["rr"].A = A(50)
    e.br_A = A(10)
    agg, info = e.build_agg()
    assert not info["br_frozen"] and agg["br"]["st"] == 10


def test_mun_change_detection_by_st_c(tmp_path):
    e = eng(tmp_path, ufs=["pe"])
    m = e.get_mun("pe", "23000")
    e.get_zone("pe", "23000", "0098")
    ab = {"tpabr": "mun", "cdabr": "23000", "and": "n", "dt": "", "ht": "", "ts": 21, "st": 0, "te": 6216,
          "est": 0, "c": 0, "a": 0}
    m.ab = ab
    e.on_mun_ab(m)
    assert m not in e.need_muns and m.out_A == a_from_ab(ab)          # zero: sai do ab, sem buscar
    m.ab = dict(ab, st=3, c=900, dt="25/10/2026", ht="17:20:00")
    e.on_mun_ab(m)
    assert m in e.need_muns and m.need_since
    m.A = A(3, ts=21, c=900)
    m.A["dt"], m.A["ht"] = "25/10/2026", "17:25:00"                     # dt/ht do u.json != do ab: ignorar
    e._clear_need(m)
    e.on_mun_ab(m)
    assert m not in e.need_muns


def test_multizone_marks_incomplete_zones(tmp_path):
    e = eng(tmp_path, ufs=["sp"])
    m = e.get_mun("sp", "71072")
    z1, z2 = e.get_zone("sp", "71072", "0001"), e.get_zone("sp", "71072", "0002")
    z1.A = A(100, ts=100)                                               # zona completa
    m.ab = {"tpabr": "mun", "cdabr": "71072", "and": "p", "dt": "", "ht": "", "ts": 200, "st": 150,
            "te": 60000, "est": 0, "c": 10, "a": 0}
    e.on_mun_ab(m)
    assert z2 in e.need_zones and z1 not in e.need_zones
    agg, _ = e.build_agg()
    assert "71072-0001" in agg["zona"] and "71072-0002" not in agg["zona"]

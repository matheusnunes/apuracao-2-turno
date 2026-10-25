"""Decodificador de BU e parsers dos arquivos do TSE, contra as amostras reais da pesquisa."""

from pathlib import Path

import orjson

from collector import tse
from collector.bu import BUDecodeError, decode_bu

S = Path(__file__).resolve().parents[2] / "research" / "samples"


def test_recife_z1_s31_1t():
    r = decode_bu((S / "t1/recife-z0001-s0031/o03220pe2531300010031-bu.dat").read_bytes(), 6257)
    assert (r.municipio, r.zona, r.secao) == (25313, 1, 31)
    assert r.aptos == 393 and r.aptos_secao == 389 and r.aptos_tte == 4
    assert r.comparecimento == 307
    assert r.votos[13] == 123 and r.votos[22] == 157
    assert r.brancos == 2 and r.nulos == 4


def test_wrong_eleicao_is_reported_not_crash():
    data = (S / "t1/recife-z0001-s0031/o03220pe2531300010031-bu.dat").read_bytes()
    try:
        decode_bu(data, 6258)
    except BUDecodeError as e:
        assert e.kind == "sem_eleicao"
    else:
        raise AssertionError("deveria falhar")
    try:
        decode_bu(b"\x00\x01garbage", 6258)
    except BUDecodeError as e:
        assert e.kind in ("envelope", "boletim")
    else:
        raise AssertionError("deveria falhar")


def test_quixaba_sum_matches_official():
    tot = dict(aptos=0, c=0, l=0, o=0, b=0, n=0)
    files = sorted((S / "t1/mun-pe-23000").glob("*-bu.dat"))
    assert len(files) == 21
    for f in files:
        r = decode_bu(f.read_bytes(), 6257)
        tot["aptos"] += r.aptos
        tot["c"] += r.comparecimento
        tot["l"] += r.votos.get(13, 0)
        tot["o"] += r.votos.get(22, 0)
        tot["b"] += r.brancos
        tot["n"] += r.nulos
    A, _ = tse.parse_u(orjson.loads((S / "t1/pe23000-c0001-e006257-u.json").read_bytes()), 13, 22)
    assert (tot["aptos"], tot["c"], tot["l"], tot["o"], tot["b"], tot["n"]) == (A["te"], A["c"], A["l"], A["o"], A["vb"], A["tvn"])
    assert (A["l"], A["o"]) == (4270, 594)


def test_parse_u_and_jws_equal():
    j = (S / "t2/br-c0001-e006258-u.json").read_bytes()
    w = (S / "t2/br-c0001-e006258-u.jws").read_bytes()
    A1, m1 = tse.parse_u(tse.loads(j), 13, 22)
    A2, m2 = tse.parse_u(tse.loads(w), 13, 22)
    assert A1 == A2 and m1 == m2
    assert A1["ts"] == 499248 and A1["te"] == 158745502 and A1["st"] == 0
    assert set(A1) == {"ts", "st", "te", "est", "c", "a", "vb", "tvn", "l", "o", "dt", "ht", "and"}
    A, _ = tse.parse_u(tse.loads((S / "t1/br-c0001-e006257-u.json").read_bytes()), 13, 22)
    assert (A["l"], A["o"]) == (53879538, 56104503)


def test_parse_ab():
    e, _ = tse.parse_ab(tse.loads((S / "t1/pe-e006257-ab.json").read_bytes()))
    q = [x for x in e if x["cdabr"] == "23000"][0]
    assert q["tpabr"] == "mun" and q["st"] == 21 and q["c"] == 5151 and q["te"] == 6216
    e, _ = tse.parse_ab(tse.loads((S / "t2/br-e006258-ab.json").read_bytes()))
    assert len([x for x in e if x["tpabr"] == "uf"]) == 28


def test_parse_cs_main_sections_match_index_rule():
    rows = list(tse.parse_cs(tse.loads((S / "t2/pe-p003221-cs.json").read_bytes())))
    assert len(rows) == 22091
    main = [r for r in rows if r[4]]
    assert len(main) == 21418          # = PE em data/base/secoes_index.csv
    assert all(r[3] is None for r in rows)   # 2T hoje: sem da/ha
    rows1 = list(tse.parse_cs(tse.loads((S / "t1/pe-p003220-cs.json").read_bytes())))
    assert all(r[3] for r in rows1 if r[4])  # 1T: todas com da/ha


def test_parse_aux_and_time():
    aux = tse.parse_aux(tse.loads((S / "t1/recife-z0001-s0031/p003220-pe-m25313-z0001-s0031-aux.json").read_bytes()))
    assert aux["nm"] == "o03220pe2531300010031-bu.dat" and aux["tp"] == "bu"
    assert aux["hash"].startswith("6673784d") and (aux["dr"], aux["hr"]) == ("04/10/2026", "18:39:46")
    import datetime as dt
    assert tse.minutes_since(dt.date(2026, 10, 4), aux["dr"], aux["hr"]) == 18 * 60 + 39
    assert tse.minutes_since(dt.date(2026, 10, 25), "25/10/2026", "17:05:59") == 17 * 60 + 5
    assert tse.minutes_since(dt.date(2026, 10, 25), None, None) == -1
    none = tse.parse_aux({"hashes": [{"hash": "x", "st": "Recebido", "arq": []}]})
    assert none["hash"] is None
    two = tse.parse_aux({"hashes": [
        {"hash": "a", "st": "Totalizado", "dr": "25/10/2026", "hr": "17:10:00", "arq": [{"nm": "a-bu.dat", "tp": "bu"}]},
        {"hash": "b", "st": "Totalizado", "dr": "25/10/2026", "hr": "18:10:00", "arq": [{"nm": "b-busa.dat", "tp": "busa"}]}]})
    assert two["hash"] == "b" and two["tp"] == "busa"


def test_parse_aux_2026_recebido_com_bu():
    # 2T de 2026: o aux marca "Recebido" (topo "Recebida") e o bu.dat já está publicado e decodifica
    rec = tse.parse_aux({"st": "Recebida", "hashes": [{"hash": "h1", "st": "Recebido", "dr": "25/10/2026", "hr": "19:31:59",
        "arq": [{"nm": "o03221to9559100030021-rdv.dat"}, {"nm": "o03221to9559100030021-bu.dat"}]}]})
    assert rec["hash"] == "h1" and rec["nm"] == "o03221to9559100030021-bu.dat" and rec["tp"] == "bu"
    # no mesmo horário, Totalizado ganha; um Recebido mais novo (reenvio) ganha do Totalizado antigo
    tie = tse.parse_aux({"hashes": [
        {"hash": "r", "st": "Recebido", "dr": "25/10/2026", "hr": "19:00:00", "arq": [{"nm": "r-bu.dat"}]},
        {"hash": "t", "st": "Totalizado", "dr": "25/10/2026", "hr": "19:00:00", "arq": [{"nm": "t-bu.dat"}]}]})
    assert tie["hash"] == "t"
    newer = tse.parse_aux({"hashes": [
        {"hash": "t", "st": "Totalizado", "dr": "25/10/2026", "hr": "19:00:00", "arq": [{"nm": "t-bu.dat"}]},
        {"hash": "r", "st": "Recebido", "dr": "25/10/2026", "hr": "19:20:00", "arq": [{"nm": "r-bu.dat"}]}]})
    assert newer["hash"] == "r"
    # outros estados não valem
    assert tse.parse_aux({"hashes": [{"hash": "x", "st": "Excluído", "arq": [{"nm": "x-bu.dat"}]}]})["hash"] is None

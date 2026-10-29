"""Logs de urna (etl/logs_urna.py) + BU do 2T + base do 1T -> dados da vista "Prorrogação" da tela.

Saídas em site/data/prorrog/:
- `secoes.bin`: colunas alinhadas ao `idx` da base (499.248 seções), little-endian, layout em `resumo.json` → `cols`;
- `resumo.json`: curvas por minuto (1T × 2T, hora de Brasília), cenários contra a margem, totais por UF,
  "speedrun" e cobertura.

Hora de Brasília: a urna registra hora local. O fuso de cada seção sai da abertura ("Urna pronta para receber
votos", às 8h de Brasília em todo o país). A diferença, arredondada em horas, é tomada como a moda do município.
No exterior a votação segue a hora local (8h–17h). Lá não há conversão, e a seção fica marcada.

"Tardio" = voto computado às 17:00:00 ou depois (hora de Brasília). Quem estava na fila às 17h vota de qualquer
jeito, então o total de tardios é um TETO para os eleitores que só votaram por causa da prorrogação.

    uv run python -m etl.prorrogacao
"""

from __future__ import annotations

import collections
import json
import sqlite3
import time
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq

from etl.common import UF_REGIAO
from etl.logs_urna import OUT as LOGS_DB, STATE, unpack

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "site/data/prorrog"
H17 = 17 * 3600
MIN0, MIN1 = 5 * 60, 21 * 60          # janela das curvas por minuto (Brasília)

COLS = [  # (nome, dtype, descrição)
    ("flags", "uint8", "bit0 log do 2T; bit1 1T na mesma urna/seção; bit2 exterior (hora local)"),
    ("n2", "uint16", "votos computados no log, 2T"),
    ("t2", "uint16", "votos do 2T a partir das 17:00 (Brasília)"),
    ("t2b", "uint16", "votos do 2T a partir das 17:10"),
    ("t2c", "uint16", "votos do 2T a partir das 18:00"),
    ("n1", "uint16", "votos computados no log, 1T (mesma urna)"),
    ("t1", "uint16", "votos do 1T a partir das 17:00"),
    ("t1c", "uint16", "votos do 1T a partir das 18:00"),
    ("e2", "uint16", "encerramento do 2T, minutos desde 0h de Brasília (0 = sem registro)"),
    ("e1", "uint16", "encerramento do 1T, idem"),
    ("u2", "uint16", "último voto do 2T, minutos desde 0h (0 = sem voto)"),
    ("u1", "uint16", "último voto do 1T, idem"),
    ("f2", "uint8", "voto mais rápido do 2T, segundos (255 = 255+ ou sem dado)"),
]


def mode(xs):
    return collections.Counter(xs).most_common(1)[0][0] if xs else None


def main():
    t0 = time.time()
    base = pq.read_table(ROOT / "data/base/secoes.parquet",
                         columns=["idx", "uf", "cd_mun", "zona", "secao", "l1", "o1", "comp1", "aptos1"]).to_pydict()
    N = len(base["idx"])
    assert base["idx"] == list(range(N))
    key_idx = {f"{u}-{m:05d}-{z:04d}-{s:04d}": i
               for i, (u, m, z, s) in enumerate(zip(base["uf"], base["cd_mun"], base["zona"], base["secao"]))}
    uf_of = np.array(base["uf"])
    mun_of = np.array(base["cd_mun"], dtype=np.int32)

    # BU do 2T por seção (estado do coletor)
    bu = {"l": np.zeros(N, np.int64), "o": np.zeros(N, np.int64), "c": np.zeros(N, np.int64)}
    has_bu = np.zeros(N, bool)
    st = sqlite3.connect(f"file:{STATE}?mode=ro", uri=True)
    for key, l, o, c in st.execute("SELECT key, l, o, comp FROM sec WHERE has_bu=1"):
        i = key_idx.get(key)
        if i is not None:
            bu["l"][i], bu["o"][i], bu["c"][i], has_bu[i] = l or 0, o or 0, c or 0, True

    db = sqlite3.connect(f"file:{LOGS_DB}?mode=ro", uri=True)
    rows = db.execute("SELECT key, t1_open, t2_open, t1_sel, t2_sel, t1_id, t1_v, t2_v, t2_d, t1_allp, t2_allp, t1_d "
                      "FROM log WHERE status='ok'").fetchall()
    status = collections.Counter(s for (s,) in db.execute("SELECT status FROM log"))

    # 1) fuso por seção -> moda do município
    off_sec, off_mun = {}, collections.defaultdict(list)
    for key, o1, o2, *_r in rows:
        o = o2 if o2 is not None else o1
        if o is None or key.startswith("zz"):
            continue
        h = round((o - 8 * 3600) / 3600)
        if -3 <= h <= 1:
            off_sec[key] = h
            off_mun[key[:8]].append(h)
    off_mun = {k: mode(v) for k, v in off_mun.items()}

    C = {name: np.zeros(N, dtype) for name, dtype, _ in COLS}
    C["f2"][:] = 255
    curve = {t: np.zeros(MIN1 - MIN0, np.int64) for t in (1, 2)}
    curve_uf = {t: collections.defaultdict(lambda: np.zeros((MIN1 - MIN0) // 5, np.int64)) for t in (1, 2)}
    dur_hist = {t: np.zeros(601, np.int64) for t in (1, 2)}
    close_hist = {t: np.zeros(MIN1 - MIN0, np.int64) for t in (1, 2)}   # urnas encerrando por minuto (Brasília)
    allp = collections.Counter()
    fastest = []           # (dur, key, hh:mm:ss)
    last_votes = []        # (t_brt, key)
    mism = n1_ok = 0
    zz_sec = 0
    t2_1705 = t2_19 = 0
    n2_vs_bu = collections.Counter()

    for key, o1, o2, s1, s2, id1, v1, v2, d2, a1, a2, d1 in rows:
        i = key_idx.get(key)
        if i is None:
            continue
        zz = key.startswith("zz")
        off = 0 if zz else off_mun.get(key[:8], off_sec.get(key, 0))
        shift = -off * 3600       # local -> Brasília
        f = 4 if zz else 0
        V2 = unpack(v2, np.uint32).astype(np.int64) + shift
        D2 = unpack(d2, np.uint16)
        if len(V2):
            f |= 1
            C["n2"][i] = min(len(V2), 65535)
            C["t2"][i] = int((V2 >= H17).sum())
            C["t2b"][i] = int((V2 >= H17 + 600).sum())
            C["t2c"][i] = int((V2 >= 18 * 3600).sum())
            t2_1705 += int(((V2 >= H17) & (V2 < H17 + 300)).sum())
            t2_19 += int((V2 >= 19 * 3600).sum())
            C["u2"][i] = int(V2[-1] // 60)
            if not zz:
                m = V2 // 60 - MIN0
                m = m[(m >= 0) & (m < MIN1 - MIN0)]
                curve[2] += np.bincount(m, minlength=MIN1 - MIN0)
                curve_uf[2][key[:2]] += np.bincount(m // 5, minlength=(MIN1 - MIN0) // 5)
                last_votes.append((int(V2[-1]), key))
            ok = D2[D2 < 65535]
            if len(ok):
                fmin = int(ok.min())
                C["f2"][i] = min(fmin, 255)
                dur_hist[2] += np.bincount(np.minimum(ok, 600), minlength=601)
                j = int(np.argmin(np.where(D2 < 65535, D2, 65535)))
                fastest.append((fmin, key, int(V2[j])))
            if has_bu[i]:
                n2_vs_bu["igual" if len(V2) == bu["c"][i] else "diferente"] += 1
        if s2 is not None:
            C["e2"][i] = (s2 + shift) // 60
            if not zz and 0 <= C["e2"][i] - MIN0 < MIN1 - MIN0:
                close_hist[2][C["e2"][i] - MIN0] += 1
        if a2 is not None and not zz:
            allp[("2T", a2)] += 1
        # 1T: só quando a urna do 2T estava nesta mesma seção no 1T
        uf, mun, z, s = key.split("-")
        same = id1 == f"{mun}-{z}-{s}"
        if v1 and id1 and not same:
            mism += 1
        if v1 and same:
            V1 = unpack(v1, np.uint32).astype(np.int64) + shift
            f |= 2
            n1_ok += 1
            C["n1"][i] = min(len(V1), 65535)
            C["t1"][i] = int((V1 >= H17).sum())
            C["t1c"][i] = int((V1 >= 18 * 3600).sum())
            C["u1"][i] = int(V1[-1] // 60)
            if s1 is not None:
                C["e1"][i] = (s1 + shift) // 60
                if not zz and 0 <= C["e1"][i] - MIN0 < MIN1 - MIN0:
                    close_hist[1][C["e1"][i] - MIN0] += 1
            D1 = unpack(d1, np.uint16)
            D1 = D1[D1 < 65535]
            if len(D1):
                dur_hist[1] += np.bincount(np.minimum(D1, 600), minlength=601)
            if not zz:
                m = V1 // 60 - MIN0
                m = m[(m >= 0) & (m < MIN1 - MIN0)]
                curve[1] += np.bincount(m, minlength=MIN1 - MIN0)
                curve_uf[1][key[:2]] += np.bincount(m // 5, minlength=(MIN1 - MIN0) // 5)
            if a1 is not None and not zz:
                allp[("1T", a1)] += 1
        C["flags"][i] = f
        zz_sec += zz

    # 2) cenários contra a margem (Brasil + exterior, com o BU oficial de cada seção)
    br = ~np.char.equal(uf_of, "zz")
    L, O, CO = bu["l"], bu["o"], bu["c"]
    late = C["t2"].astype(np.int64)
    lw = has_bu & (C["flags"] & 1 > 0)
    margin = int(L.sum() - O.sum())
    late_tot = int(late[lw].sum())
    worst_rm = int(np.minimum(late, L)[lw].sum())            # tira de Lula todo voto tardio da urna
    share = np.divide(L - O, CO, out=np.zeros(N), where=CO > 0)
    prop = float((late * share)[lw].sum())                    # tardios seguem a proporção da urna
    lshare = np.divide(L, L + O, out=np.full(N, np.nan), where=(L + O) > 0)
    with_late = lw & (late > 0)
    t1_same = (C["flags"] & 2) > 0
    late18 = C["t2c"].astype(np.int64)
    t1l = C["t1"].astype(np.int64)
    worst18 = int(np.minimum(late18, L)[lw].sum())
    # cobertura: votos (comparecimento do BU) nas urnas com log ÷ comparecimento total; folga para as que faltam
    c_total = int(CO.sum())
    c_log = int(CO[lw].sum())
    taxa_uf = {}
    for uf in sorted(set(base["uf"])):
        m = (uf_of == uf) & lw
        if CO[m].sum() > 0:
            taxa_uf[uf] = 1e4 * float(late[m].sum()) / float(CO[m].sum())
    sem_log = int(CO[has_bu & ~lw].sum())
    extrap = round(sem_log * max(taxa_uf.values(), default=0) / 1e4)
    vv = lw & ((L + O) > 0)
    pond = 100 * float((late * np.divide(L, L + O, out=np.zeros(N), where=(L + O) > 0))[vv].sum()) / max(1, int(late[vv].sum()))
    win_l, win_o = lw & (L > O), lw & (O > L)
    top3 = sorted((u for u in taxa_uf if late[(uf_of == u) & lw].sum() >= 100), key=lambda u: -taxa_uf[u])[:3]
    cen = {
        "margem": margin,
        "tardios": late_tot,
        "tardios_17h10": int(C["t2b"][lw].sum()),
        "tardios_18h": int(C["t2c"][lw].sum()),
        "tardios_19h": t2_19,
        "tardios_17h_17h05": t2_1705,
        "secoes_com_log": int(lw.sum()),
        "pct_cobertura_logs": round(100 * c_log / max(1, c_total), 3),
        "pct_votos_apos_17h": round(100 * late_tot / max(1, int(C["n2"][lw].sum())), 4),
        "margem_sobre_tardios": round(margin / late_tot, 1) if late_tot else None,
        "pct_tardios_sobre_margem": round(100 * late_tot / margin, 3) if margin else None,
        "pct_lula_tardio_para_virar": round(50 + 50 * margin / late_tot, 1) if late_tot else None,
        "pior_caso_18h": {"tirados_de_lula": worst18, "margem": margin - worst18},
        "tardios_extrapolados_sem_log": extrap,
        "margem_pior_caso_conservador": margin - worst_rm - extrap,
        "pct_lula_ponderado_tardio": round(pond, 2),
        "taxa_tardia_urnas_lula": round(1e4 * float(late[win_l].sum()) / max(1, float(CO[win_l].sum())), 2),
        "taxa_tardia_urnas_flavio": round(1e4 * float(late[win_o].sum()) / max(1, float(CO[win_o].sum())), 2),
        "taxa_tardia_uf": {u: round(v, 2) for u, v in taxa_uf.items()},
        "top3_uf_taxa_tardia": top3,
        "pct_tardios_top3_uf": round(100 * float(sum(late[(uf_of == u) & lw].sum() for u in top3)) / max(1, late_tot), 1),
        "hora_ultimo_voto": None,
        "pior_caso_remove": {"tirados_de_lula": worst_rm, "margem": margin - worst_rm},
        "pior_caso_troca": {"trocados": worst_rm, "margem": margin - 2 * worst_rm},
        "proporcional": {"efeito_liquido_lula": round(prop), "margem": round(margin - prop)},
        "secoes_com_tardio": int(with_late.sum()),
        "lula_pct_validos_secoes_com_tardio": round(100 * float(L[with_late].sum() / max(1, (L + O)[with_late].sum())), 2),
        "lula_pct_validos_demais": round(100 * float(L[lw & ~with_late].sum() / max(1, (L + O)[lw & ~with_late].sum())), 2),
        "corr_tardio_x_lula": round(float(np.corrcoef(late[lw & (CO > 0)] / CO[lw & (CO > 0)],
                                                      lshare[lw & (CO > 0)])[0, 1]), 4)
        if (lw & (CO > 0)).sum() > 2 else None,
        "votos_log_2t": int(C["n2"][lw].sum()),
        "comparecimento_bu": int(CO[lw].sum()),
        "secoes_abertas_apos_17h10": int(((C["e2"] >= 17 * 60 + 10) & br).sum()),
        "secoes_abertas_apos_18h": int(((C["e2"] >= 18 * 60) & br).sum()),
        "mesma_urna_1t": {
            "secoes": int(t1_same.sum()),
            "urnas_mais_tardios_2t_que_1t": int((t1_same & (late > t1l)).sum()),
            "tardios_acima_do_1t": int(np.maximum(0, late - t1l)[t1_same].sum()),
            "razao_1t_2t": round(float(t1l[t1_same].sum()) / max(1, int(late[t1_same].sum())), 1),
            "pct_tardios_1t": round(100 * float(t1l[t1_same].sum()) / max(1, int(C["n1"][t1_same].sum())), 3),
            "votos_1t": int(C["n1"][t1_same].sum()), "tardios_1t": int(C["t1"][t1_same].sum()),
            "tardios_1t_18h": int(C["t1c"][t1_same].sum()),
            "votos_2t": int(C["n2"][t1_same].sum()), "tardios_2t": int(C["t2"][t1_same].sum()),
            "tardios_2t_18h": int(C["t2c"][t1_same].sum()),
        },
    }

    # 3) por UF
    por_uf = {}
    for uf in sorted(set(base["uf"])):
        m = (uf_of == uf)
        ml = m & lw
        por_uf[uf] = {
            "secoes": int(m.sum()), "com_log": int((m & ((C["flags"] & 1) > 0)).sum()),
            "votos": int(C["n2"][m].sum()), "tardios": int(C["t2"][m].sum()), "tardios_18h": int(C["t2c"][m].sum()),
            "abertas_17h10": int((m & (C["e2"] >= 17 * 60 + 10)).sum()),
            "votos_1t": int(C["n1"][m & t1_same].sum()), "tardios_1t": int(C["t1"][m & t1_same].sum()),
            "margem": int((L - O)[ml].sum()), "pior_caso": int(np.minimum(late, L)[ml].sum()),
            "fuso": mode([off_mun[k] for k in off_mun if k.startswith(uf)]) if uf != "zz" else None,
            "regiao": UF_REGIAO.get(uf),
        }

    # 4) speedrun e curiosidades
    fastest.sort()
    fmin = fastest[0][0] if fastest else None
    sec_hms = lambda s: f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"  # noqa: E731
    last_votes.sort(reverse=True)
    cen["hora_ultimo_voto"] = sec_hms(last_votes[0][0]) if last_votes else None
    resumo = {
        "gerado_em": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "n": N, "cols": [], "status_logs": dict(status),
        "cobertura": {"secoes_log_ok": len(rows), "secoes_com_bu": int(has_bu.sum()), "exterior": zz_sec,
                      "mesma_urna_1t": n1_ok, "urna_de_outra_secao_no_1t": mism,
                      "votos_log_x_comparecimento_bu": dict(n2_vs_bu)},
        "cenarios": cen,
        "curvas": {"min0": MIN0, "passo_min": 1, "t1": curve[1].tolist(), "t2": curve[2].tolist()},
        "encerramentos": {"min0": MIN0, "passo_min": 1, "t1": close_hist[1].tolist(), "t2": close_hist[2].tolist()},
        "curvas_uf": {"min0": MIN0, "passo_min": 5,
                      "t1": {k: v.tolist() for k, v in sorted(curve_uf[1].items())},
                      "t2": {k: v.tolist() for k, v in sorted(curve_uf[2].items())}},
        "por_uf": por_uf,
        "todos_votaram": {f"{t}_{'sim' if a else 'nao'}": n for (t, a), n in sorted(allp.items())},
        "speedrun": {
            "min_s": fmin,
            "secoes_no_min": [{"key": k, "hora": sec_hms(t), "idx": key_idx[k]} for d, k, t in fastest if d == fmin][:50],
            "n_secoes_no_min": sum(1 for d, *_ in fastest if d == fmin),
            "hist_s": dur_hist[2][:121].tolist(), "acima_120s": int(dur_hist[2][121:].sum()),
            "mediana_s": int(np.searchsorted(np.cumsum(dur_hist[2]), dur_hist[2].sum() / 2)),
            "mediana_1t_s": int(np.searchsorted(np.cumsum(dur_hist[1]), dur_hist[1].sum() / 2)) if dur_hist[1].sum() else None,
            "min_1t_s": int(np.nonzero(dur_hist[1])[0][0]) if dur_hist[1].sum() else None,
        },
        "ultimos_votos": [{"key": k, "hora": sec_hms(t), "idx": key_idx[k]} for t, k in last_votes[:20]],
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    off = 0
    with open(OUT_DIR / "secoes.bin", "wb") as fh:
        for name, dtype, desc in COLS:
            a = C[name].astype("<" + np.dtype(dtype).str[1:])
            resumo["cols"].append({"name": name, "dtype": dtype, "offset": off, "length": N, "desc": desc})
            fh.write(a.tobytes())
            off += a.nbytes
            pad = (-off) % 4
            fh.write(b"\0" * pad)
            off += pad
    fatos = ROOT / "etl/fatos_prorrogacao.json"
    if fatos.exists():
        resumo["fatos"] = json.loads(fatos.read_text())
    (OUT_DIR / "resumo.json").write_text(json.dumps(resumo, ensure_ascii=False, separators=(",", ":")))
    print(json.dumps({"cobertura": resumo["cobertura"], "cenarios": cen, "speedrun_min": fmin,
                      "n_fmin": resumo["speedrun"]["n_secoes_no_min"], "mediana": resumo["speedrun"]["mediana_s"],
                      "ultimo": resumo["ultimos_votos"][:3], "todos": resumo["todos_votaram"]},
                     ensure_ascii=False, indent=1))
    print(f"ok em {time.time() - t0:.0f} s")


if __name__ == "__main__":
    main()

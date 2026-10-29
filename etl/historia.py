"""Números da narrativa pós-eleição (capítulos 1–4: recuperação, noite, modelo, resultado) -> site/data/prorrog/historia.json.

Tudo sai dos arquivos finais da apuração (analytics.json, agg, sections.bin, base); a tela só monta as frases.
O capítulo 5 (prorrogação) vem de etl/prorrogacao.py (resumo.json). Referência das definições: o roteiro da narrativa.

    uv run python -m etl.historia
"""
import json
from datetime import datetime
from pathlib import Path
import numpy as np

R = str(Path(__file__).resolve().parents[1])
LIVE = f'{R}/site/data/live'
an = json.load(open(f'{LIVE}/analytics.json'))
man = json.load(open(f'{LIVE}/manifest.json'))
agg = json.load(open(f"{LIVE}/{man['files']['agg']}"))
mu = json.load(open(f'{R}/site/data/base/municipios.json'))
meta = json.load(open(f'{R}/site/data/base/meta.json'))
V = {}

# ---------------- Cap. 1: recuperação ----------------
b1, ap, M = an['baseline_1t'], an['apurado'], an['thermometer']['br']['best']
V['m1'] = b1['margin_votes']                        # -2.224.965
V['m2'] = ap['margin_votes']                        # +4.430.191
V['d_margin'] = M['d_margin_votes']                 # 6.655.156
V['d_l'], V['d_o'] = M['d_l'], M['d_o']
V['x1'] = M['x1']
V['lula_ganhou_mais_que_outros_1t'] = M['d_l'] > M['x1']
V['pool'] = M['pool']                               # ΔL+ΔO
ps = M['pool_sources']
V['pool_src'] = ps
V['pool_src_pct'] = {k: round(100 * v / M['pool'], 1) for k, v in ps.items()}
V['capture_l_pct'] = round(100 * M['capture_l'], 1)               # 73,6
V['capture_needed_pct'] = round(100 * an['pace']['capture_needed'], 1)  # 57,9
V['d_comp_n'] = M['d_comp_n']; V['d_abst_n'] = M['d_abst_n']
V['abst1_pct'], V['abst2_pct'] = M['abst1_pct'], M['abst2_pct']
V['d_bn_n'] = M['d_bn_n']; V['bn1_pct_comp'], V['bn2_pct_comp'] = M['bn1_pct_comp'], M['bn2_pct_comp']
V['bn1'], V['bn2'] = b1['bn'], an['projection']['bn']
V['decomp'] = M['decomp']                           # turnout ≈ 0; preference ≈ d_margin
V['delta_req'], V['delta_obs'] = an['pace']['delta_req'], M['delta']
TU = an['thermometer']['uf']
ufs = [u for u in TU if u != 'zz']
V['ufs_melhoraram'] = sum(1 for u in ufs if TU[u]['best']['delta'] > 0)
V['ufs_total'] = len(ufs)
V['zz_melhorou'] = TU['zz']['best']['delta'] > 0
V['ufs_abst_subiu'] = sorted(u for u in ufs if TU[u]['best']['d_abst_pp'] > 0)
top_delta = sorted(ufs, key=lambda u: -TU[u]['best']['delta'])[:3]
V['top3_uf_delta100'] = [(u, round(TU[u]['best']['delta'], 2)) for u in top_delta]
top_votes = sorted(ufs, key=lambda u: -TU[u]['best']['d_margin_votes'])[:3]
V['top3_uf_dmargem_votos'] = [(u, TU[u]['best']['d_margin_votes']) for u in top_votes]
V['top3_uf_dmargem_votos_pct_virada'] = round(100 * sum(TU[u]['best']['d_margin_votes'] for u in top_votes) / M['d_margin_votes'], 1)
# UFs: virada de lado (sinal da margem 1T × 2T oficial)
uf1 = {}
for i, u in enumerate(mu['uf']):
    d = uf1.setdefault(u, [0, 0]); d[0] += mu['l1'][i]; d[1] += mu['o1'][i]
flip_uf = []
for u, g in agg['uf'].items():
    m1, m2 = uf1[u][0] - uf1[u][1], g['l'] - g['o']
    if (m1 > 0) != (m2 > 0) and m1 != 0 and m2 != 0: flip_uf.append((u, m1, m2))
V['ufs_viraram'] = flip_uf
# municípios
rows = []
for i, cd in enumerate(mu['cd_mun']):
    g = agg['mun'].get(str(cd))
    if not g: continue
    a1, a2 = mu['a1'][i], g['te']
    m1, m2 = mu['l1'][i] - mu['o1'][i], g['l'] - g['o']
    rows.append(dict(cd=cd, nome=mu['nome'][i], uf=mu['uf'][i], a2=a2, m1=m1, m2=m2, dm=m2 - m1,
                     d100=100 * (m2 / a2 - m1 / a1) if a1 and a2 else 0.0))
br_rows = [r for r in rows if r['uf'] != 'zz']
A = sum(r['a2'] for r in br_rows)
mel = [r for r in br_rows if r['d100'] > 0]
V['mun_total_br'] = len(br_rows)
V['mun_melhorou'] = len(mel)
V['mun_melhorou_pct_aptos'] = round(100 * sum(r['a2'] for r in mel) / A, 1)
V['mun_viraram_para_lula'] = sum(1 for r in rows if r['m1'] < 0 < r['m2'])
V['mun_viraram_para_flavio'] = sum(1 for r in rows if r['m2'] < 0 < r['m1'])
V['mun_viraram_para_lula_br'] = sum(1 for r in br_rows if r['m1'] < 0 < r['m2'])
V['mun_viraram_para_flavio_br'] = sum(1 for r in br_rows if r['m2'] < 0 < r['m1'])
maior_virada = max((r for r in br_rows if r['m1'] < 0 < r['m2']), key=lambda r: r['a2'])
V['maior_mun_que_virou'] = (maior_virada['nome'], maior_virada['uf'], maior_virada['m1'], maior_virada['m2'], maior_virada['a2'])
top10 = sorted(rows, key=lambda r: -r['dm'])[:10]
V['top10_mun_dmargem_pct_virada'] = round(100 * sum(r['dm'] for r in top10) / M['d_margin_votes'], 1)
V['top3_mun_dmargem'] = [(r['nome'], r['uf'], r['dm']) for r in top10[:3]]
V['mun_venceu_2t'] = {'L': sum(1 for r in br_rows if r['m2'] > 0), 'O': sum(1 for r in br_rows if r['m2'] < 0), 'empate': sum(1 for r in br_rows if r['m2'] == 0)}
V['mun_venceu_1t'] = {'L': sum(1 for r in br_rows if r['m1'] > 0), 'O': sum(1 for r in br_rows if r['m1'] < 0), 'empate': sum(1 for r in br_rows if r['m1'] == 0)}

# ---------------- Cap. 2: a noite (placar bruto) ----------------
H = an['history']
tt = lambda h: datetime.fromisoformat(h['t'])
hm = lambda h: h['t'][11:16]
# pausa da divulgação: maior intervalo entre pontos consecutivos com o ponto anterior abaixo de 5% das seções
gaps = [(tt(H[i]) - tt(H[i - 1]), i) for i in range(1, len(H)) if H[i - 1]['pct_secoes'] < 5]
g, gi = max(gaps)
V['maior_pausa'] = {'de': H[gi - 1]['t'], 'ate': H[gi]['t'], 'min': round(g.total_seconds() / 60), 'pct_secoes_antes': H[gi - 1]['pct_secoes']}
V['primeiro_ponto'] = {k: H[0][k] for k in ('t', 'pct_secoes', 'margin_apurado', 'margin_proj')}
# primeira vez que Flávio lidera o bruto
fl = next(h for h in H if h['margin_apurado'] < 0)
V['flavio_passa'] = {'t': fl['t'], 'pct_secoes': fl['pct_secoes'], 'margin_apurado': fl['margin_apurado']}
# maior vantagem de Lula antes disso
pre = [h for h in H if tt(h) < tt(fl)]
mx = max(pre, key=lambda h: h['margin_apurado'])
V['lula_max_antes'] = {'t': mx['t'], 'pct_secoes': mx['pct_secoes'], 'margin_apurado': mx['margin_apurado']}
mn = min(H, key=lambda h: h['margin_apurado'])
V['flavio_max'] = {'t': mn['t'], 'pct_secoes': mn['pct_secoes'], 'margin_apurado': mn['margin_apurado']}
V['virada_observada'] = an['virada_observada']
# trocas de sinal no bruto ao longo da noite
sg = [1 if h['margin_apurado'] > 0 else -1 for h in H]
V['trocas_de_lider_bruto'] = sum(1 for i in range(1, len(sg)) if sg[i] != sg[i - 1])
V['ultimo_ponto'] = {k: H[-1][k] for k in ('t', 'pct_secoes', 'margin_apurado')}
V['data_time'] = an['inputs']['data_time']
# ordem de chegada: % de Lula nos válidos por horário de recebimento do BU (sections.bin, t_recv_min)
a = np.fromfile(f"{LIVE}/{man['files']['sections']}", dtype='<i4').reshape(-1, 8)
has, apt, comp, l2, o2, bb, nn, trc = a.T
bins = [(19 * 60, 19 * 60 + 30), (19 * 60 + 30, 20 * 60), (20 * 60, 20 * 60 + 30), (20 * 60 + 30, 21 * 60), (21 * 60, 22 * 60)]
V['lula_share_por_chegada'] = []
for lo, hi in bins:
    s = (has == 1) & (trc >= lo) & (trc < hi)
    L, O = int(l2[s].sum()), int(o2[s].sum())
    V['lula_share_por_chegada'].append({'janela': f'{lo//60:02d}:{lo%60:02d}-{hi//60:02d}:{hi%60:02d}', 'bus': int(s.sum()), 'pct_lula_validos': round(100 * L / (L + O), 1)})
# critério: janelas de 30 min com ≥5% dos BUs; menor e maior % de Lula
jan = []
for lo in range(19 * 60, 23 * 60, 30):
    s = (has == 1) & (trc >= lo) & (trc < lo + 30)
    L, O = int(l2[s].sum()), int(o2[s].sum())
    if s.sum() >= 0.05 * has.sum(): jan.append({'janela': f'{lo//60:02d}:{lo%60:02d}', 'pct_bus': round(100 * s.sum() / has.sum(), 1), 'pct_lula_validos': round(100 * L / (L + O), 1)})
V['janelas_chegada_5pct'] = jan
V['janela_min_lula'] = min(jan, key=lambda j: j['pct_lula_validos']); V['janela_max_lula'] = max(jan, key=lambda j: j['pct_lula_validos'])
s = (has == 1) & (trc >= 0) & (trc < 19 * 60)
V['bus_recebidos_antes_19h'] = {'n': int(s.sum()), 'pct': round(100 * s.sum() / has.sum(), 2)}

# ---------------- Cap. 3: o modelo já previa ----------------
FINAL = an['apurado']['margin_votes']
first_pos_idx = next(i for i, h in enumerate(H) if all(x['margin_proj'] > 0 for x in H[i:]))
fp = H[first_pos_idx]
V['proj_sempre_positiva_desde'] = {'t': fp['t'], 'pct_secoes': fp['pct_secoes'], 'margin_proj': fp['margin_proj'], 'band': fp['band'], 'phase': fp['phase']}
post = H[first_pos_idx:]
V['proj_min_max_desde'] = {'min': min(h['margin_proj'] for h in post), 'max': max(h['margin_proj'] for h in post)}
r1 = next(h for h in H if h['pct_secoes'] >= 1.5)   # primeiro ponto com ≥1,5% das seções
V['proj_1_5pct'] = {'t': r1['t'], 'pct_secoes': r1['pct_secoes'], 'margin_proj': r1['margin_proj'], 'band': r1['band'], 'erro': FINAL - r1['margin_proj']}
def first(cond):
    h = next((h for h in H if cond(h)), None)
    return None if h is None else {'t': h['t'], 'pct_secoes': h['pct_secoes'], 'p_lula': h.get('p_lula'), 'margin_proj': h['margin_proj'], 'margin_apurado': h['margin_apurado'], 'band': h['band'], 'phase': h['phase']}
V['p_primeira'] = first(lambda h: h.get('p_lula') is not None)
V['p90'] = first(lambda h: (h.get('p_lula') or 0) >= 0.90)
V['p95'] = first(lambda h: (h.get('p_lula') or 0) >= 0.95)
V['p99'] = first(lambda h: (h.get('p_lula') or 0) >= 0.99)
V['faixa_acima_zero'] = first(lambda h: h['band'][0] > 0)
V['chamada_tela'] = first(lambda h: h.get('phase') in ('trend', 'final'))   # can_call: ≥30% seções + ≥5% aptos por região
post15 = [h for h in H if h['pct_secoes'] >= r1['pct_secoes']]
V['proj_min_max_desde_1_5pct'] = {'min': min(h['margin_proj'] for h in post15), 'max': max(h['margin_proj'] for h in post15)}
i95 = H.index(next(h for h in H if (h.get('p_lula') or 0) >= 0.95))
V['estavel_apos_p95'] = {'p_min': min(h['p_lula'] for h in H[i95:]), 'faixa_lo_min': min(h['band'][0] for h in H[i95:])}
V['faixa_continha_final'] = {'pontos': len(H), 'contem': sum(1 for h in H if h['band'][0] <= FINAL <= h['band'][1])}
V['erro_max_desde_1_5pct'] = max(abs(FINAL - h['margin_proj']) for h in H if tt(h) >= tt(r1))
V['minutos_chamada_ate_virada'] = round((datetime.fromisoformat(V['virada_observada']['t']) - datetime.fromisoformat(V['chamada_tela']['t'])).total_seconds() / 60)
V['minutos_pausa_divulgacao'] = V['maior_pausa']['min']
V['minutos_1_5pct_ate_virada'] = round((datetime.fromisoformat(V['virada_observada']['t']) - datetime.fromisoformat(r1['t'])).total_seconds() / 60)

# ---------------- Cap. 4: resultado final ----------------
P = an['projection']
V['final'] = {k: ap[k] for k in ('l', 'o', 'l_pct_valid', 'o_pct_valid', 'margin_votes', 'margin_pp_valid', 'c', 'a', 'te', 'vb', 'tvn')}
V['final']['abst_pct'] = P['abst_pct']; V['final']['bn'] = P['bn']
V['final']['comp_pct'] = round(100 * ap['c'] / ap['te'], 2)
won = {'L': sorted(u for u in ufs if agg['uf'][u]['l'] > agg['uf'][u]['o']), 'O': sorted(u for u in ufs if agg['uf'][u]['o'] > agg['uf'][u]['l'])}
V['ufs_vencidas'] = {k: len(v) for k, v in won.items()}; V['ufs_vencidas_lista'] = won
V['zz_vencedor'] = 'L' if agg['uf']['zz']['l'] > agg['uf']['zz']['o'] else 'O'
pp = {u: P['uf'][u]['margin_pp_valid'] for u in ufs}
ua = min(ufs, key=lambda u: abs(pp[u]))
V['uf_mais_apertada'] = (ua, P['uf'][ua]['margin_votes'], pp[ua])
out = Path(R) / 'site/data/prorrog/historia.json'
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(V, ensure_ascii=False, default=str))
print(f'ok: {out} ({len(V)} chaves)')

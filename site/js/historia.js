// historia.js — a narrativa pós-eleição em cartões: recuperação 1T→2T, a noite, o modelo, o resultado e a prorrogação.
// Os números vêm de data/prorrog/historia.json (etl/historia.py) e resumo.json (etl/prorrogacao.py); aqui só se
// montam as frases. Cada cartão diz o que o mapa mostra (modo de cor, camada, foco) e pode trazer um gráfico.
import { el, isNum, fInt, fN, fPct, nf1, cssVar } from './fmt.js';
import { curveChart, reguaBars, relogioChart, pctEncerradas } from './prcharts.js';

export const CHAPTERS = ['A virada', 'A recuperação', 'A noite da apuração', 'O modelo já previa', 'O resultado', 'A prorrogação'];

let HV = null, loadingH = null;
export function historiaData() { return HV; }
export function loadHistoria() {
  if (HV) return Promise.resolve(HV);
  if (loadingH) return loadingH;
  loadingH = fetch(`data/prorrog/historia.json?t=${Date.now()}`).then((r) => { if (!r.ok) throw new Error(`HTTP ${r.status} em historia.json`); return r.json(); })
    .then((j) => { HV = j; return j; });
  loadingH.catch(() => { loadingH = null; });
  return loadingH;
}

const hm = (iso) => (iso ? iso.slice(11, 16).replace(':', 'h') : '—');
const mi2 = (v) => `${new Intl.NumberFormat('pt-BR', { maximumFractionDigits: 2, minimumFractionDigits: 2 }).format(Math.abs(v) / 1e6)} mi`;
const sg = (v) => (v > 0 ? '+' : v < 0 ? '−' : '');

/** barra empilhada simples (partes somam 100) */
function stack(parts) {
  const w = el('div', 'st-stack'), bar = el('div', 'st-bar'), lg = el('div', 'st-lg');
  for (const p of parts) {
    const i = el('i'); i.style.width = `${p.pct}%`; i.style.background = p.c; bar.append(i);
    const s = el('span'); const d = el('i'); d.style.background = p.c; s.append(d, `${p.t} `, el('b', null, fPct(p.pct, 0))); lg.append(s);
  }
  w.append(bar, lg); return w;
}

/** série da noite: contagem bruta × projeção (com a faixa), eixo x em % das seções (escala raiz, para o começo aparecer) */
function nightChart(H, final, marks = [], { w = 560, h = 190, showProj = true } = {}) {
  const pts = H.filter((p) => isNum(p.pct_secoes) && isNum(p.margin_apurado)).slice().sort((a, b) => a.pct_secoes - b.pct_secoes);
  const ns = 'http://www.w3.org/2000/svg', svg = document.createElementNS(ns, 'svg');
  svg.setAttribute('viewBox', `0 0 ${w} ${h}`); svg.setAttribute('width', '100%'); svg.setAttribute('role', 'img');
  svg.setAttribute('aria-label', 'Margem na contagem bruta e na projeção ao longo da apuração');
  const add = (tag, a, txt) => { const e = document.createElementNS(ns, tag); for (const k in a) e.setAttribute(k, a[k]); if (txt != null) e.textContent = txt; svg.append(e); return e; };
  const m = { l: 44, r: 12, t: 14, b: 24 }, iw = w - m.l - m.r, ih = h - m.t - m.b;
  let lo = -1e6, hi = 1e6;
  for (const p of pts) { lo = Math.min(lo, p.margin_apurado); hi = Math.max(hi, p.margin_apurado); if (showProj && isNum(p.margin_proj)) { lo = Math.min(lo, p.margin_proj); hi = Math.max(hi, p.margin_proj); } }
  if (showProj) for (const p of pts) if (p.pct_secoes >= 1 && Array.isArray(p.band) && p.band.every(isNum)) { lo = Math.min(lo, Math.max(-5e6, p.band[0])); hi = Math.max(hi, Math.min(13e6, p.band[1])); } // a faixa precisa aparecer cruzando o zero
  hi = Math.max(hi, final) * 1.08; lo *= 1.15;
  const X = (pct) => m.l + Math.sqrt(Math.max(0, Math.min(100, pct)) / 100) * iw;
  const Y = (v) => m.t + (1 - (Math.max(lo, Math.min(hi, v)) - lo) / (hi - lo)) * ih;
  const ink = cssVar('--ink'), ink2 = cssVar('--ink-2'), muted = cssVar('--muted'), grid = cssVar('--grid'), cA = cssVar('--A'), cB = cssVar('--B');
  add('rect', { x: m.l, y: Y(hi), width: iw, height: Y(0) - Y(hi), fill: cA, opacity: 0.07 });
  add('rect', { x: m.l, y: Y(0), width: iw, height: Y(lo) - Y(0), fill: cB, opacity: 0.07 });
  for (const v of [0.5, 2, 10, 25, 50, 100]) { add('line', { x1: X(v), x2: X(v), y1: m.t, y2: m.t + ih, stroke: grid, 'stroke-width': 1 }); add('text', { x: X(v), y: h - 7, 'text-anchor': v === 100 ? 'end' : 'middle', 'font-size': 10.5, fill: muted }, `${nf1.format(v).replace(',0', '')}%`); }
  add('text', { x: m.l + iw, y: h - 18, 'text-anchor': 'end', 'font-size': 9.5, fill: muted }, 'seções apuradas');
  for (let v = Math.ceil(lo / 2e6) * 2e6; v <= hi; v += 2e6) { add('line', { x1: m.l, x2: m.l + iw, y1: Y(v), y2: Y(v), stroke: v === 0 ? cssVar('--axis') : grid, 'stroke-width': v === 0 ? 1.5 : 0.5 }); add('text', { x: m.l - 5, y: Y(v) + 4, 'text-anchor': 'end', 'font-size': 10, fill: muted }, `${sg(v)}${Math.abs(v / 1e6)} mi`); }
  if (showProj) {
    const hb = pts.filter((p) => Array.isArray(p.band) && p.band.every(isNum));
    if (hb.length > 1) add('polygon', { points: hb.map((p) => `${X(p.pct_secoes)},${Y(p.band[1])}`).concat(hb.slice().reverse().map((p) => `${X(p.pct_secoes)},${Y(p.band[0])}`)).join(' '), fill: ink, opacity: 0.1 });
    const hp = pts.filter((p) => isNum(p.margin_proj));
    if (hp.length > 1) add('polyline', { points: hp.map((p) => `${X(p.pct_secoes)},${Y(p.margin_proj)}`).join(' '), fill: 'none', stroke: ink2, 'stroke-width': 1.6, 'stroke-dasharray': '5 3' });
  }
  add('line', { x1: m.l, x2: m.l + iw, y1: Y(final), y2: Y(final), stroke: cA, 'stroke-width': 1, 'stroke-dasharray': '2 3' });
  add('text', { x: m.l + 4, y: Y(final) - 4, 'font-size': 10, fill: cA }, `final ${sg(final)}${mi2(final)}`);
  add('polyline', { points: pts.map((p) => `${X(p.pct_secoes)},${Y(p.margin_apurado)}`).join(' '), fill: 'none', stroke: ink, 'stroke-width': 2.4, 'stroke-linejoin': 'round' });
  for (const mk of marks) {
    if (!isNum(mk.pct) || !isNum(mk.v)) continue;
    add('circle', { cx: X(mk.pct), cy: Y(mk.v), r: 5.5, fill: cssVar('--panel'), stroke: ink, 'stroke-width': 2.5 });
    if (mk.t) add('text', { x: X(mk.pct) + (mk.left ? -9 : 9), y: Y(mk.v) + (mk.dy ?? -8), 'text-anchor': mk.left ? 'end' : 'start', 'font-size': 11, 'font-weight': 700, fill: ink }, mk.t);
  }
  const lg = el('div', 'ul-legend');
  const swatch = (c, t, cls) => { const i = el('i'); i.style.background = c; if (cls) i.className = cls; lg.append(i, el('span', null, t)); };
  swatch(ink, 'contagem bruta', 'ln'); if (showProj) { swatch(ink2, 'projeção do modelo', 'ln'); swatch(ink, 'faixa de 90%'); lg.lastElementChild.previousElementSibling.style.opacity = 0.25; }
  const box = el('div', 'pr-chart'); box.append(svg, lg); return box;
}

/**
 * Cartões da narrativa. ctx: {V (historia.json), PR (resumo.json da prorrogação ou null), an (analytics), A, Bn (nomes curtos),
 * ufName, munByCd(cd) -> idx | undefined}. Cada cartão: {ch, key, title, hero, heroSub, text, viz?, map: {mode, layer, focus}, dur, foot?}
 */
export function buildStory(ctx) {
  const { V, PR, an, A, Bn, ufName } = ctx, cards = [];
  const H = an?.history || [], fin = V.m2;
  const add = (c) => cards.push({ dur: 13, ...c });
  const br = { type: 'br' };

  // --- abertura ---
  add({ ch: 0, key: 'virada', title: `${A} virou o jogo`, hero: `${sg(V.m1)}${mi2(V.m1)} → ${sg(V.m2)}${mi2(V.m2)}`, heroSub: `margem de ${A} sobre ${Bn}`,
    text: `${A} saiu do 1º turno ${mi2(V.m1)} de votos atrás. Terminou o 2º turno ${mi2(V.m2)} à frente.`, map: { mode: 'margem1', layer: 'auto', focus: br }, beats: [{ at: 0.45, mode: 'margem' }], dur: 12 });

  // --- 1 · recuperação ---
  const b1 = an?.baseline_1t || {};
  add({ ch: 1, key: 'partida', title: 'O ponto de partida', hero: fInt(Math.abs(V.m1)), heroSub: `votos de vantagem de ${Bn} no 1º turno`,
    text: `${A} ${fPct(b1.l_pct_valid, 2)} × ${fPct(b1.o_pct_valid, 2)} ${Bn} nos válidos. Os outros candidatos somaram ${fN(V.x1)} votos.`, map: { mode: 'margem1', layer: 'auto', focus: br } });
  add({ ch: 1, key: 'tamanho', title: 'O tamanho da virada', hero: `${mi2(V.d_margin)}`, heroSub: `de margem na direção de ${A}`,
    text: `${nf1.format(V.delta_obs)} votos de margem a cada 100 eleitores. Para empatar bastavam ${nf1.format(V.delta_req)}: ${nf1.format(V.delta_obs / V.delta_req)}× o necessário.`, map: { mode: 'dmargem', layer: 'auto', focus: br } });
  add({ ch: 1, key: 'cresceu', title: 'Quem cresceu', hero: `+${mi2(V.d_l)}`, heroSub: `votos a mais para ${A} · ${Bn}: +${mi2(V.d_o)}`,
    text: `${V.lula_ganhou_mais_que_outros_1t ? `O ganho de ${A} supera todos os votos dos outros candidatos no 1º turno (${mi2(V.x1)}). ` : ''}É uma conta de saldo, não o rastro de cada eleitor. Mapa: ganho de ${A}, depois o de ${Bn}, na mesma escala.`, map: { mode: 'ganhoA', layer: 'auto', focus: br }, beats: [{ at: 0.5, mode: 'ganhoB' }], dur: 15 });
  const ps = V.pool_src_pct || {};
  add({ ch: 1, key: 'saldo', title: 'De onde saiu o saldo', hero: mi2(V.pool), heroSub: 'votos a mais para os dois finalistas, somados',
    text: 'Dois terços equivalem aos votos dos outros candidatos; o resto, à queda da abstenção e dos brancos e nulos.',
    viz: () => stack([{ t: 'outros candidatos do 1º turno', pct: ps.x1 ?? 0, c: cssVar('--ink-2') }, { t: 'abstenção caiu', pct: ps.abst_drop ?? 0, c: cssVar('--teal') }, { t: 'brancos e nulos caíram', pct: ps.bn_drop ?? 0, c: cssVar('--muted') }]),
    map: { mode: 'dmargem', layer: 'auto', focus: br } });
  add({ ch: 1, key: 'comparecimento', title: 'Mais gente foi votar', hero: `+${mi2(V.d_comp_n)}`, heroSub: 'eleitores a mais nas urnas',
    text: `A abstenção caiu de ${fPct(V.abst1_pct)} para ${fPct(V.abst2_pct)}.${V.ufs_abst_subiu?.length ? ` Subiu em ${V.ufs_abst_subiu.length} UFs: ${V.ufs_abst_subiu.map((u) => u.toUpperCase()).join(', ')}.` : ''}`, map: { mode: 'dabst', layer: 'auto', focus: br } });
  add({ ch: 1, key: 'captura', title: 'Para quem foi', hero: fPct(V.capture_l_pct, 1), heroSub: `dos votos a mais ficaram com ${A}`,
    text: `De cada 4 votos a mais para os finalistas, quase 3 foram para ${A}. Para virar, ele precisava de ${fPct(V.capture_needed_pct, 1)}.`, map: { mode: 'dmargem', layer: 'hex', focus: br } });
  add({ ch: 1, key: 'espalhada', title: 'Em todo o país', hero: `${V.ufs_melhoraram} de ${V.ufs_total}`, heroSub: `UFs em que a margem de ${A} melhorou`,
    text: `E em ${fInt(V.mun_melhorou)} de ${fInt(V.mun_total_br)} municípios (${fPct(100 * V.mun_melhorou / V.mun_total_br, 0)}), onde estão ${fPct(V.mun_melhorou_pct_aptos, 0)} dos eleitores.`, map: { mode: 'dmargem', layer: 'muni', focus: br } });
  const t3 = V.top3_uf_dmargem_votos || [];
  if (t3.length === 3) add({ ch: 1, key: 'top3uf', title: 'Onde mais andou, em votos', hero: fPct(V.top3_uf_dmargem_votos_pct_virada, 0), heroSub: `da virada veio de ${t3.map(([u]) => u.toUpperCase()).join(', ')}`,
    text: `${ufName(t3[0][0])}: ${sg(t3[0][1])}${fInt(t3[0][1])} votos de margem para ${A}. ${ufName(t3[1][0])}: ${sg(t3[1][1])}${fInt(t3[1][1])}. ${ufName(t3[2][0])}: ${sg(t3[2][1])}${fInt(t3[2][1])}.`, map: { mode: 'dmargem', layer: 'auto', focus: { type: 'uf', id: t3[0][0] } } });
  const fu = V.ufs_viraram || [], mv = V.maior_mun_que_virou;
  add({ ch: 1, key: 'viraram', title: 'Quem trocou de lado', hero: fInt(V.mun_viraram_para_lula_br), heroSub: `municípios passaram de ${Bn} para ${A} · ${V.mun_viraram_para_flavio_br} no sentido contrário`,
    text: `${fu.length === 1 ? `${ufName(fu[0][0])} foi a única UF a trocar de lado: de ${Bn} por ${fN(-fu[0][1])} para ${A} por ${fN(fu[0][2])}.` : fu.length ? `${fu.length} UFs trocaram de lado.` : 'Nenhuma UF trocou de lado.'}${mv ? ` O maior município que virou: ${mv[0].replace(/\b\w/g, (c) => c.toUpperCase()).replace(/\B\w+/g, (s) => s.toLowerCase())} (${String(mv[1]).toUpperCase()}).` : ''}`,
    map: { mode: 'margem', layer: 'muni', focus: fu.length === 1 ? { type: 'uf', id: fu[0][0] } : br } });

  // --- 2 · a noite ---
  const P0 = V.maior_pausa || {}, fp = V.flavio_passa, fm = V.flavio_max, vo = V.virada_observada;
  const night = (marks) => () => nightChart(H, fin, marks, { showProj: false });
  add({ ch: 2, key: 'pausa', title: 'Começou e parou', hero: `${Math.floor(P0.min / 60)}h${String(P0.min % 60).padStart(2, '0')}`, heroSub: 'sem números novos do TSE',
    text: `Às ${hm(P0.de)}, com ${fPct(P0.pct_secoes_antes, 2)} das seções, a divulgação parou por causa da prorrogação. Só voltou às ${hm(P0.ate)}.`,
    viz: night([{ pct: P0.pct_secoes_antes, v: H.find((p) => p.t === P0.de)?.margin_apurado, t: hm(P0.de) }]), map: { mode: 'margem', layer: 'auto', focus: br } });
  if (fp) add({ ch: 2, key: 'flavio-frente', title: `${Bn} passa à frente na contagem`, hero: hm(fp.t), heroSub: `com ${fPct(fp.pct_secoes, 1)} das seções`,
    text: `Antes disso, a maior vantagem de ${A} na contagem bruta foi de ${fInt(V.lula_max_antes?.margin_apurado)} votos, às ${hm(V.lula_max_antes?.t)}.`,
    viz: night([{ pct: fp.pct_secoes, v: fp.margin_apurado, t: hm(fp.t), dy: 16 }]), map: { mode: 'margem', layer: 'auto', focus: br } });
  if (fm) add({ ch: 2, key: 'auge', title: `O auge de ${Bn}`, hero: fInt(-fm.margin_apurado), heroSub: `votos à frente às ${hm(fm.t)}, com ${fPct(fm.pct_secoes, 1)} apurado`,
    text: 'A contagem bruta depende da ordem de chegada dos boletins: o que chega primeiro não é uma amostra do país.',
    viz: night([{ pct: fm.pct_secoes, v: fm.margin_apurado, t: hm(fm.t), dy: 18 }]), map: { mode: 'margem', layer: 'auto', focus: br } });
  if (vo) add({ ch: 2, key: 'virada-bruta', title: 'A virada na contagem', hero: hm(vo.t), heroSub: `com ${fPct(vo.pct_secoes, 1)} das seções`,
    text: `${A} passou à frente na contagem bruta e não saiu mais. O número final saiu às ${hm(V.data_time)}.`,
    viz: night([{ pct: vo.pct_secoes, v: 0, t: hm(vo.t), dy: -10 }]), map: { mode: 'margem', layer: 'auto', focus: br } });

  // --- 3 · o modelo ---
  const sp = V.proj_sempre_positiva_desde, p15 = V.proj_1_5pct, fz = V.faixa_acima_zero, ct = V.chamada_tela;
  const model = (marks) => () => nightChart(H, fin, marks, { showProj: true });
  if (sp) add({ ch: 3, key: 'modelo-038', title: `Com ${fPct(sp.pct_secoes, 2)} das seções`, hero: `${sg(sp.margin_proj)}${mi2(sp.margin_proj)}`, heroSub: `projeção do modelo para ${A}, às ${hm(sp.t)}`,
    text: `Dali em diante, a margem projetada nunca mais ficou negativa. A faixa de 90% ainda era larga: de ${sg(sp.band[0])}${mi2(sp.band[0])} a ${sg(sp.band[1])}${mi2(sp.band[1])}.`,
    viz: model([{ pct: sp.pct_secoes, v: sp.margin_proj, t: hm(sp.t) }]), map: { mode: 'margem', layer: 'auto', focus: br } });
  if (p15) add({ ch: 3, key: 'modelo-15', title: `Com ${fPct(p15.pct_secoes, 1)} das seções`, hero: `${sg(p15.margin_proj)}${mi2(p15.margin_proj)}`, heroSub: `projeção às ${hm(p15.t)} · o final foi ${sg(fin)}${mi2(fin)}`,
    text: `Errou o número final por ${fInt(Math.abs(p15.erro))} votos, ${V.minutos_1_5pct_ate_virada} minutos antes de ${A} passar à frente na contagem. A faixa ainda cruzava o zero.`,
    viz: model([{ pct: p15.pct_secoes, v: p15.margin_proj, t: hm(p15.t) }]), map: { mode: 'margem', layer: 'auto', focus: br } });
  if (fz) add({ ch: 3, key: 'modelo-certeza', title: 'Quando virou certeza', hero: fPct(fz.pct_secoes, 1), heroSub: `das seções: a faixa de 90% fica toda acima de zero (${hm(fz.t)})`,
    text: `Probabilidade de vitória de ${A}: ${[V.p_primeira, V.p90, V.p95, V.p99].filter(Boolean).map((p) => `${fPct(100 * p.p_lula, 0)} com ${fPct(p.pct_secoes, 1)}`).join(' · ')}. Depois disso, nunca abaixo de ${fPct(100 * (V.estavel_apos_p95?.p_min ?? 0.95), 0)}.`,
    viz: model([{ pct: fz.pct_secoes, v: fz.band?.[0], t: `faixa > 0 · ${hm(fz.t)}`, dy: 16 }]), map: { mode: 'margem', layer: 'auto', focus: br } });
  if (ct && vo) add({ ch: 3, key: 'modelo-chamada', title: 'A tela cravou antes da virada', hero: hm(ct.t), heroSub: `${A} vencedor na tela · ${V.minutos_chamada_ate_virada} min antes da virada na contagem`,
    text: `Na hora, ${ct.margin_apurado < 0 ? `${Bn} ainda liderava a contagem por ${fInt(-ct.margin_apurado)} votos` : `${A} liderava a contagem por ${fInt(ct.margin_apurado)}`}. A regra exige a faixa toda de um lado e ≥ 30% das seções, com ≥ 5% dos aptos em cada região.`,
    viz: model([{ pct: ct.pct_secoes, v: ct.margin_apurado, t: `chamada ${hm(ct.t)}`, dy: 18 }, { pct: vo.pct_secoes, v: 0, t: `virada ${hm(vo.t)}`, dy: -10 }]), map: { mode: 'margem', layer: 'auto', focus: br } });

  // --- 4 · o resultado ---
  const F = V.final || {};
  add({ ch: 4, key: 'venceu', title: `${A} venceu`, hero: fPct(F.l_pct_valid, 2), heroSub: `dos votos válidos · ${Bn} ${fPct(F.o_pct_valid, 2)}`,
    text: `${fInt(F.l)} votos × ${fInt(F.o)}. Diferença de ${fInt(F.margin_votes)} votos (${nf1.format(F.margin_pp_valid)} pontos dos válidos).`, map: { mode: 'margem', layer: 'auto', focus: br } });
  add({ ch: 4, key: 'ufs', title: 'Menos estados, mais votos', hero: `${V.ufs_vencidas?.L} × ${V.ufs_vencidas?.O}`, heroSub: `UFs vencidas por ${A} × ${Bn}${V.zz_vencedor === 'L' ? ` · ${A} venceu também no exterior` : ''}`,
    text: `Votaram ${fPct(F.comp_pct, 1)} dos aptos; abstenção de ${fPct(F.abst_pct, 2)}. Venceu quem somou mais votos.`, map: { mode: 'margem', layer: 'muni', focus: br } });

  // --- 5 · a prorrogação ---
  if (!PR) { add({ ch: 5, key: 'pr-sem', title: 'A prorrogação', hero: '…', heroSub: 'logs de urna ainda não processados', text: 'Rode etl/logs_urna.py e etl/prorrogacao.py.', map: { mode: 'tardio2', layer: 'auto', focus: br } }); return cards; }
  const c = PR.cenarios, u1 = c.mesma_urna_1t || {}, fx = PR.fatos || {}, cov = c.pct_cobertura_logs;
  const parcial = cov < 99.5 ? ` (logs de ${fPct(cov, 1)} dos votos até agora)` : '';
  const tl = fx.linha_do_tempo || [];
  add({ ch: 5, key: 'pr-fato', title: 'O que aconteceu', hero: '17h → 19h', heroSub: 'votação estendida por decisão do STF, só onde a urna estava aberta',
    text: tl.length ? tl.map((e) => `${e.hora}: ${e.texto.replace(/\.$/, '')}`).slice(0, 4).join('. ') + '.' : 'A votação foi estendida até as 19h.',
    foot: tl.length ? `Fontes: ${[...new Set(tl.map((e) => e.fonte))].join('; ')} (imprensa; a confirmar).` : '', map: { mode: 'margem', layer: 'auto', focus: br }, dur: 16 });
  const e03 = pctEncerradas(PR, 2, 17 * 60 + 4), e03b = pctEncerradas(PR, 1, 17 * 60 + 4);
  if (isNum(e03)) add({ ch: 5, key: 'pr-relogio', title: 'Quando a decisão saiu', hero: fPct(e03, 0), heroSub: 'das urnas do 2º turno já tinham encerrado até 17h03',
    text: `A prorrogação só valia onde a urna seguia aberta, e quase todas fechavam no minuto das 17h. No 1º turno, que não teve prorrogação, eram ${fPct(e03b, 0)} até 17h03: a fila era maior.`,
    viz: () => relogioChart(PR), map: { mode: 'abertas', layer: 'hex', focus: br }, dur: 16 });
  add({ ch: 5, key: 'pr-regua', title: 'A régua', hero: fInt(c.margem), heroSub: 'votos de diferença: é isso que a prorrogação teria de explicar',
    text: 'O log de cada urna registra a hora de cada voto. Dá para contar quantos votaram depois das 17h; não dá para saber em quem.', map: { mode: 'tardio2', layer: 'auto', focus: br } });
  add({ ch: 5, key: 'pr-tardios', title: 'Quantos votaram depois das 17h', hero: fInt(c.tardios), heroSub: `${fPct(c.pct_votos_apos_17h, 3)} dos votos${parcial}`,
    text: `${fPct(100 * c.tardios_17h_17h05 / Math.max(1, c.tardios), 0)} deles entre 17h00 e 17h05: a fila que já estava lá. Depois das 18h, ${fInt(c.tardios_18h)}; o último voto foi às ${c.hora_ultimo_voto?.slice(0, 5).replace(':', 'h')}.`,
    foot: fx.tse ? `Conferência: o TSE informou ${fInt(fx.tse.votos_apos_17h)} (${fx.tse.fonte}; a confirmar).` : '', map: { mode: 'tardio2', layer: 'hex', focus: br } });
  add({ ch: 5, key: 'pr-1t', title: 'O mesmo relógio no 1º turno', hero: fInt(u1.tardios_1t), heroSub: 'votaram depois das 17h no 1º turno, nas mesmas urnas, e lá não houve prorrogação',
    text: `É a regra de sempre: quem está na fila às 17h vota. No 2º turno, com a prorrogação, foram ${fInt(u1.tardios_2t)} nessas urnas, ${nf1.format(u1.razao_1t_2t)}× menos. No 1º turno há mais cargos: ${isNum(PR.speedrun?.mediana_1t_s) ? `o voto típico levou ${PR.speedrun.mediana_1t_s} s, contra ${PR.speedrun.mediana_s} s no 2º turno, e` : 'cada voto demora mais e'} a fila cresce.`,
    viz: () => curveChart(PR, { w: 560, h: 180, t0: 15 * 60 + 30, t1: 19 * 60 + 30, step: 2 }), map: { mode: 'tardio1', layer: 'hex', focus: br }, beats: [{ at: 0.55, mode: 'tardio2' }], dur: 18 });
  add({ ch: 5, key: 'pr-teto', title: 'Teste 1: o teto', hero: `${fInt(c.margem_sobre_tardios)}×`, heroSub: 'a diferença final é esse múltiplo de todos os votos dados depois das 17h',
    text: 'É um teto: conta todo voto depois das 17h, inclusive a fila que votaria de qualquer jeito.', viz: () => reguaBars(PR, { aName: A }), map: { mode: 'tardio2', layer: 'auto', focus: br }, dur: 15 });
  add({ ch: 5, key: 'pr-pior', title: 'Teste 2: o pior caso, urna a urna', hero: `${sg(c.pior_caso_remove.margem)}${fInt(Math.abs(c.pior_caso_remove.margem))}`, heroSub: `margem de ${A} tirando dele todo voto dado depois das 17h`,
    text: `Mesmo trocando esses votos para ${Bn}, ${A} venceria por ${fInt(c.pior_caso_troca.margem)}. Para virar, ${A} precisaria ter tido ${c.pct_lula_tardio_para_virar > 100 ? 'mais de 100% (impossível)' : fPct(c.pct_lula_tardio_para_virar, 1)} dos votos tardios.`, map: { mode: 'tardio2', layer: 'auto', focus: br } });
  const ef = c.proporcional.efeito_liquido_lula;
  add({ ch: 5, key: 'pr-padrao', title: 'Teste 3: no padrão de cada urna', hero: `${fInt(Math.abs(ef))}`, heroSub: `votos de margem para ${ef >= 0 ? A : Bn}, se os tardios votaram como o resto da urna`,
    text: `Nas urnas com voto tardio, ${A} teve ${fPct(c.pct_lula_ponderado_tardio, 1)} dos válidos (no país, ${fPct(F.l_pct_valid, 1)}). Sem esses votos, ${A} venceria por ${fInt(c.proporcional.margem)}.`, map: { mode: 'tardio2', layer: 'hex', focus: br } });
  let concl;
  if (!(cov >= 95)) concl = { title: 'Conclusão: ainda conferindo as urnas', hero: fPct(cov, 0), heroSub: 'dos votos do país já tiveram o horário conferido no log da urna',
    text: `Os logs das urnas ainda estão sendo baixados. Os números acima são de uma amostra aleatória; a conclusão só é afirmada quando passar de 95% dos votos.` };
  else if (c.margem_pior_caso_conservador > 0) concl = { title: 'O que os dados dizem', hero: fPct(c.pct_tardios_sobre_margem, 2), heroSub: 'é o peso de todos os votos depois das 17h na diferença final', text: `Nem no pior caso a prorrogação mudaria o vencedor: os votos depois das 17h não decidiram a eleição.${fx.tse ? ` Bate com a avaliação do TSE (“${fx.tse.avaliacao}”). Os números são nossos, dos logs.` : ''}` };
  else if (c.proporcional.margem > 0) concl = { title: 'O teto não descarta', hero: fInt(c.proporcional.margem), heroSub: `margem de ${A} no padrão de cada urna`, text: 'No pior caso, os votos tardios superam a diferença. Um efeito decisivo só existiria se esses votos fossem muito diferentes do resto da mesma urna.' };
  else concl = { title: 'Os números não confirmam', hero: fInt(Math.abs(ef)), heroSub: 'votos de efeito, acima da diferença final', text: 'No padrão de cada urna, os votos depois das 17h somam mais que a diferença final: os dados não confirmam a avaliação do TSE.' };
  add({ ch: 5, key: 'pr-conclusao', ...concl, foot: 'Voto é secreto: o log mostra quando cada pessoa votou, não em quem. Os testes 2 e 3 são hipóteses.', map: { mode: 'tardio2', layer: 'auto', focus: br }, dur: 16 });
  const spd = PR.speedrun;
  if (spd && isNum(spd.min_s)) add({ ch: 5, key: 'pr-bastidor', title: 'Bastidor: quanto dura um voto', hero: `${spd.mediana_s} s`, heroSub: 'tempo típico entre liberar a urna e confirmar o voto (mediana, 2º turno)',
    text: `No 1º turno, com cinco cargos, a mediana foi ${spd.mediana_1t_s ?? '—'} s. O voto mais rápido do 2º turno: ${spd.min_s} s, em ${fInt(spd.n_secoes_no_min)} urnas. Clique numa seção (ou busque “zona 12 seção 40 SP”) para abrir o log da urna.`, map: { mode: 'tardio2', layer: 'auto', focus: br }, dur: 12 });
  if (cov < 99.5) for (const c of cards) if (c.ch === 5 && !['pr-fato', 'pr-conclusao'].includes(c.key)) // amostra: números ainda vão mudar um pouco
    c.foot = `${c.foot ? c.foot + ' ' : ''}Amostra parcial: ${fPct(cov, 0)} dos votos conferidos nos logs até agora.`;
  return cards;
}

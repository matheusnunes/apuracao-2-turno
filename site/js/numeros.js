// numeros.js — página só de números (reserva leve, sem mapa e sem sections.bin).
import { $, el, isNum, fPP, fM, fPct, fN, fInt, U100, hhmm, parseIso, loadPrefs, applyCandColors, keyEl, safe } from './fmt.js';
import { LiveFeed, liveBaseFromUrl } from './live.js';
import { freshness, renderBadges, renderBanners } from './health.js';

const PREF = loadPrefs();
const cand = PREF.cand;
const short = (k) => cand[k].short || cand[k].name.split(' ')[0];
const REG = ['N', 'NE', 'CO', 'SE', 'S', 'ZZ'];
const REGN = { N: 'Norte', NE: 'Nordeste', CO: 'Centro-Oeste', SE: 'Sudeste', S: 'Sul', ZZ: 'Exterior' };
let FEED, META = null;
const bootAt = Date.now();

function big(label, value, sub, key) {
  const d = el('div', 'nbox'); const l = el('div', 'l'); if (key) l.append(keyEl(key)); l.append(label);
  d.append(l, el('div', 'v', value), el('div', 's', sub || '')); return d;
}
function render() {
  const A = FEED.an, br = FEED.agg?.br, st = A?.status, ph = st?.phase || 'none';
  const cmp = ['early', 'trend', 'final'].includes(ph);
  const base = A?.baseline_1t || META?.baseline_1t || {};
  const req = A?.pace?.delta_req ?? base.delta_req;
  // placar
  const P = $('#n-placar'); P.replaceChildren();
  const l = br?.l || 0, o = br?.o || 0, v = l + o, pct = br?.ts ? 100 * (br.st || 0) / br.ts : 0;
  P.append(big(`${short('A')} (placar bruto)`, v ? fPct(100 * l / v, 2) : '—', `${fN(l)} votos`, 'A'), big(`${short('B')} (placar bruto)`, v ? fPct(100 * o / v, 2) : '—', `${fN(o)} votos`, 'B'),
    big('Seções totalizadas', fPct(pct, 1), br ? `${fInt(br.st)} de ${fInt(br.ts)}` : 'aguardando'),
    big('Com boletim', fPct(FEED.manifest?.coverage?.secoes_total ? 100 * (FEED.manifest.coverage.secoes_com_bu || 0) / FEED.manifest.coverage.secoes_total : 0, 1), 'urna a urna'));
  $('#n-mix').textContent = st?.mix_caption || '';
  // fase
  const ptxt = { none: 'Aguardando o analytics', pre: 'Pré-apuração · menos de 2% das seções (só base do 1º turno e ritmo necessário)', early: 'Sinal inicial · ainda não é tendência', trend: 'Tendência', final: 'Final' }[ph] || ph;
  $('#n-phase').textContent = ptxt + (st?.reason ? ` · ${st.reason}` : '');
  const call = $('#n-call'); call.replaceChildren();
  if (st?.can_call && (st.call === 'L' || st.call === 'O')) { const k = st.call === 'L' ? 'A' : 'B'; call.append(keyEl(k), `Projeção: ${cand[k].name} vence`); call.hidden = false; } else call.hidden = true;
  // termômetro + ritmo + projeção
  const G = $('#n-grid'); G.replaceChildren();
  const M = A?.thermometer?.br?.best;
  G.append(big('1º turno (base)', `${fPct(base.l_pct_valid, 2)} × ${fPct(base.o_pct_valid, 2)}`, `${short('A')} × ${short('B')} · diferença ${fN(Math.abs(base.margin_votes))} votos`));
  G.append(big(`Ritmo necessário (${U100})`, fM(req, 2), `ganho de margem de ${short('A')} sobre o 1º turno para empatar`));
  if (cmp && M) {
    const p = A.pace || {}, b = Array.isArray(p.delta_obs_band) ? p.delta_obs_band : null;
    G.append(big(`Ritmo observado (${U100})`, fM(p.delta_obs, 2), b ? `cada estado pesa o seu eleitorado · faixa ${fM(b[0], 2)} a ${fM(b[1], 2)} · ${isNum(req) && b ? (b[0] > req ? '✓ acima do necessário' : b[1] < req ? '✗ abaixo do necessário' : '≈ inclui o necessário') : ''}` : ''));
    if (ph !== 'final' && isNum(p.delta_rem) && Math.abs(p.delta_rem) <= 50) G.append(big('Necessário no que falta', fM(p.delta_rem, 2), U100));
    G.append(big('Termômetro · Δ margem', fM(M.delta, 2), `soma simples do apurado · ${fN(M.d_margin_votes, true)} votos · cobre ${fPct(A.thermometer.br.coverage_aptos_pct, 0)} do eleitorado`));
    G.append(big('Δ abstenção', fPP(M.d_abst_pp), `${fN(M.d_abst_n, true)} eleitores`));
    G.append(big('Δ brancos + nulos', fPP(isNum(M.d_bn_pp_comp) ? M.d_bn_pp_comp : M.d_bn_pp), `${fN(M.d_bn_n, true)} votos`));
    G.append(big(`Δ votos ${short('A')} / ${short('B')}`, `${fN(M.d_l, true)} / ${fN(M.d_o, true)}`, isNum(M.capture_l) ? `saldo líquido: ${short('A')} ${fPct(100 * M.capture_l, 0)}` : ''));
    const pj = A.projection || {}, bp = Array.isArray(pj.band_pp_valid) ? pj.band_pp_valid : null, bv = Array.isArray(pj.band_votes) ? pj.band_votes : null;
    G.append(big(ph === 'trend' ? 'Tendência (% válidos)' : 'Projeção (% válidos)', `${fPct(pj.l_pct_valid)} × ${fPct(pj.o_pct_valid)}`, `margem ${fPP(pj.margin_pp_valid, 2)} · faixa ${bp ? `${fPP(bp[0], 2)} a ${fPP(bp[1], 2)}` : '—'}`));
    G.append(big('Margem projetada (votos)', fN(pj.margin_votes, true), bv ? `faixa ${fN(bv[0], true)} a ${fN(bv[1], true)}` : ''));
  } else {
    G.append(big('Termômetro, ritmo observado e projeção', '—', A ? 'começam com 2% das seções apuradas' : 'aguardando analytics.json'));
  }
  // tabela por região
  const T = $('#n-reg'); T.replaceChildren();
  const h = el('tr'); for (const c of ['Região', 'Apurado', 'Fatia apurado × eleitorado', 'Δ margem /100', 'Δ abst. p.p.', 'Ritmo']) h.append(el('th', null, c)); T.append(h);
  for (const r of REG) {
    const x = st?.mix_regiao?.[r], Mr = A?.thermometer?.regiao?.[r]?.best, ok = cmp && Mr && isNum(Mr.delta);
    const tr = el('tr');
    tr.append(el('td', null, REGN[r]), el('td', null, fPct(x?.pct_apurado ?? st?.region_pct_aptos?.[r], 0)), el('td', null, x ? `${fPct(x.share_apurado, 0)} × ${fPct(x.share_eleitorado, 0)}` : '—'),
      el('td', null, ok ? fM(Mr.delta) : '—'), el('td', null, ok ? fPP(Mr.d_abst_pp).replace(' p.p.', '') : '—'), el('td', null, ok && isNum(req) ? (Mr.delta >= req ? '✓ ' : '✗ ') + fM(Mr.delta - req) : 'aguardando'));
    T.append(tr);
  }
  const vo = A?.virada_observada;
  $('#n-note').textContent = vo?.t ? `Placar bruto trocou de líder às ${hhmm(parseIso(vo.t))} (${fPct(vo.pct_secoes, 0)} das seções).` : '';
}
function tickFresh() { const fr = freshness(FEED); renderBadges($('#fresh'), fr, el); renderBanners($('#banners'), fr, el); $('#clock').textContent = `${hhmm(new Date())} BRT`; }

async function boot() {
  applyCandColors(cand);
  document.documentElement.dataset.theme = PREF.theme;
  try { const r = await fetch(`data/base/meta.json?t=${Date.now()}`); if (r.ok) META = await r.json(); } catch { META = null; }
  FEED = new LiveFeed({ base: liveBaseFromUrl(), wantSections: false, nRows: META?.index?.n_rows || 0, sha: META?.index?.sha256 || null, interval: 10000,
    onUpdate: () => { safe('números', render); safe('frescor', tickFresh); } });
  FEED.start();
  setInterval(() => safe('frescor', tickFresh), 1000);
  setInterval(async () => { // recarga periódica, só se o servidor responder
    if (Date.now() - bootAt < 45 * 60000) return;
    try { const r = await fetch(`numeros.html?t=${Date.now()}`, { method: 'HEAD', cache: 'no-store' }); if (r.ok) location.reload(); } catch { /* tenta de novo */ }
  }, 60000);
  render();
}
boot().catch((e) => console.error(e));

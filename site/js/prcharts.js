// prcharts.js — gráficos da prorrogação (dados de data/prorrog/resumo.json, ver prorrog.js):
// curva nacional de votos por minuto (1º × 2º turno, mesmas urnas) e a régua "votos tardios × diferença final".
import { el, isNum, fInt, fN, fPct, cssVar } from './fmt.js';

const teal = () => cssVar('--teal');
const hm = (min) => `${Math.floor(min / 60)}h${min % 60 ? String(min % 60).padStart(2, '0') : ''}`;

/** votos por minuto no país, 1º turno (linha âmbar) × 2º turno (área), de t0 a t1 (minutos desde 0h, Brasília) */
export function curveChart(R, { w = 520, h = 190, t0 = 15 * 60, t1 = 19 * 60 + 30, step = 1 } = {}) {
  const C = R.curvas, m0 = C.min0;
  const series = (a) => { const out = []; for (let t = t0; t < t1; t += step) { let s = 0; for (let k = 0; k < step; k++) s += a[t + k - m0] || 0; out.push(s / step); } return out; };
  const s1 = series(C.t1), s2 = series(C.t2), n = s1.length;
  const ymax = Math.max(1, ...s1, ...s2) * 1.08;
  const m = { l: 46, r: 10, t: 16, b: 22 }, iw = w - m.l - m.r, ih = h - m.t - m.b;
  const X = (t) => m.l + ((t - t0) / (t1 - t0)) * iw, Y = (v) => m.t + ih - (v / ymax) * ih;
  const ns = 'http://www.w3.org/2000/svg', svg = document.createElementNS(ns, 'svg');
  svg.setAttribute('viewBox', `0 0 ${w} ${h}`); svg.setAttribute('width', '100%'); svg.setAttribute('role', 'img');
  svg.setAttribute('aria-label', 'Votos por minuto no país, 1º e 2º turno, hora de Brasília');
  const add = (tag, a, txt) => { const e = document.createElementNS(ns, tag); for (const k in a) e.setAttribute(k, a[k]); if (txt != null) e.textContent = txt; svg.append(e); return e; };
  const ink = cssVar('--ink'), ink2 = cssVar('--ink-2'), muted = cssVar('--muted'), grid = cssVar('--grid');
  add('rect', { x: X(17 * 60), y: m.t, width: X(Math.min(t1, 19 * 60)) - X(17 * 60), height: ih, fill: teal(), opacity: 0.1 });
  for (let t = Math.ceil(t0 / 30) * 30; t <= t1; t += 30) {
    add('line', { x1: X(t), x2: X(t), y1: m.t, y2: m.t + ih, stroke: grid, 'stroke-width': t % 60 ? 0.5 : 1 });
    if (t % 60 === 0) add('text', { x: X(t), y: h - 6, 'text-anchor': 'middle', 'font-size': 11, fill: muted }, hm(t));
  }
  const yt = [0, ymax / 2 / 1.08, ymax / 1.08];
  for (const v of yt) add('text', { x: m.l - 6, y: Y(v) + 4, 'text-anchor': 'end', 'font-size': 10, fill: muted }, fN(v));
  const pts = (s) => s.map((v, i) => `${X(t0 + (i + 0.5) * step)},${Y(v)}`);
  add('polygon', { points: [`${X(t0)},${Y(0)}`, ...pts(s2), `${X(t1)},${Y(0)}`].join(' '), fill: ink2, opacity: 0.35 });
  add('polyline', { points: pts(s2).join(' '), fill: 'none', stroke: ink, 'stroke-width': 2 });
  add('polyline', { points: pts(s1).join(' '), fill: 'none', stroke: teal(), 'stroke-width': 2.5, 'stroke-linejoin': 'round' });
  add('line', { x1: X(17 * 60), x2: X(17 * 60), y1: m.t - 6, y2: m.t + ih, stroke: ink, 'stroke-width': 1.5 });
  if (t1 > 19 * 60) add('line', { x1: X(19 * 60), x2: X(19 * 60), y1: m.t - 6, y2: m.t + ih, stroke: ink, 'stroke-width': 1.5, 'stroke-dasharray': '4 3' });
  add('text', { x: (X(17 * 60) + X(Math.min(t1, 19 * 60))) / 2, y: m.t + 2, 'text-anchor': 'middle', 'font-size': 11, 'font-weight': 700, fill: ink }, '2º turno: prorrogação (onde a urna seguia aberta)');
  add('text', { x: m.l + 4, y: m.t + 12, 'font-size': 10.5, fill: muted }, step === 1 ? 'votos por minuto' : `votos por minuto (média de ${step} min)`);
  const lg = el('div', 'ul-legend');
  const sw = (c, t, line) => { const i = el('i'); i.style.background = c; if (line) i.className = 'ln'; lg.append(i, el('span', null, t)); };
  sw(ink2, '2º turno (com prorrogação)'); sw(teal(), '1º turno, mesmas urnas: não houve prorrogação; depois das 17h é a fila', true);
  const box = el('div', 'pr-chart'); box.append(svg, lg);
  return box;
}

/** três barras na mesma escala: diferença final × votos depois das 17h no 2º turno × no 1º turno (mesmas urnas) */
export function reguaBars(R, { aName = 'Lula' } = {}) {
  const c = R.cenarios, M = c.margem, t2 = c.tardios, t1 = c.mesma_urna_1t?.tardios_1t;
  const max = Math.max(M, t2, t1 || 0);
  const wrap = el('div', 'pr-regua');
  const bar = (lab, v, cls, note) => {
    const r = el('div', 'pr-row'), l = el('div', 'pr-lab'); l.append(el('span', null, lab), el('b', null, fInt(v)));
    const tr = el('div', 'pr-track'), f = el('i', cls); f.style.width = `${Math.max(0.35, (100 * v) / max)}%`; tr.append(f);
    r.append(l, tr); if (note) r.append(el('div', 'pr-note', note)); wrap.append(r);
  };
  bar(`Diferença final (${aName} à frente)`, M, 'm');
  bar('Votos depois das 17h · 2º turno', t2, 't2', isNum(c.pct_tardios_sobre_margem) ? `${fPct(c.pct_tardios_sobre_margem, 2)} da diferença` : '');
  if (isNum(t1)) bar('Depois das 17h · 1º turno, mesmas urnas, sem prorrogação', t1, 't1', 'fila normal de fim de votação');
  return wrap;
}

/** urnas encerrando minuto a minuto perto das 17h (2º turno em barras, 1º turno tracejado) e a janela da decisão */
export function relogioChart(R, { w = 560, h = 170, t0 = 16 * 60 + 58, t1 = 17 * 60 + 31, dec = [17 * 60 + 1, 17 * 60 + 3] } = {}) {
  const E = R.encerramentos; if (!E) return el('div');
  const m0 = E.min0, n = t1 - t0, b2 = [], b1 = [];
  for (let t = t0; t < t1; t++) { b2.push(E.t2[t - m0] || 0); b1.push(E.t1[t - m0] || 0); }
  const ymax = Math.max(1, ...b2, ...b1) * 1.1;
  const m = { l: 46, r: 10, t: 18, b: 22 }, iw = w - m.l - m.r, ih = h - m.t - m.b, bw = iw / n;
  const X = (t) => m.l + ((t - t0) / n) * iw, Y = (v) => m.t + ih - (v / ymax) * ih;
  const ns = 'http://www.w3.org/2000/svg', svg = document.createElementNS(ns, 'svg');
  svg.setAttribute('viewBox', `0 0 ${w} ${h}`); svg.setAttribute('width', '100%'); svg.setAttribute('role', 'img');
  svg.setAttribute('aria-label', 'Urnas encerradas por minuto perto das 17h, 1º e 2º turno');
  const add = (tag, a, txt) => { const e = document.createElementNS(ns, tag); for (const k in a) e.setAttribute(k, a[k]); if (txt != null) e.textContent = txt; svg.append(e); return e; };
  const ink = cssVar('--ink'), ink2 = cssVar('--ink-2'), muted = cssVar('--muted'), grid = cssVar('--grid');
  add('rect', { x: X(dec[0]), y: m.t - 6, width: X(dec[1] + 1) - X(dec[0]), height: ih + 6, fill: teal(), opacity: 0.25 });
  add('text', { x: X(dec[1] + 1) + 4, y: m.t + 4, 'font-size': 10.5, 'font-weight': 700, fill: ink }, 'decisão do STF (≈17h01–17h03, imprensa)');
  for (let t = t0; t <= t1; t++) if (t % 5 === 0) { add('line', { x1: X(t), x2: X(t), y1: m.t, y2: m.t + ih, stroke: grid, 'stroke-width': 0.6 }); add('text', { x: X(t), y: h - 6, 'text-anchor': 'middle', 'font-size': 10, fill: muted }, `${Math.floor(t / 60)}h${String(t % 60).padStart(2, '0')}`); }
  b2.forEach((v, i) => { if (v) add('rect', { x: m.l + i * bw + 1, y: Y(v), width: bw - 2, height: m.t + ih - Y(v), fill: ink2, opacity: 0.8 }); });
  add('polyline', { points: b1.map((v, i) => `${m.l + (i + 0.5) * bw},${Y(v)}`).join(' '), fill: 'none', stroke: teal(), 'stroke-width': 2, 'stroke-dasharray': '5 3' });
  for (const v of [0, ymax / 1.1]) add('text', { x: m.l - 6, y: Y(v) + 4, 'text-anchor': 'end', 'font-size': 10, fill: muted }, fN(v));
  add('text', { x: m.l + iw, y: m.t + 18, 'text-anchor': 'end', 'font-size': 10.5, fill: muted }, 'urnas encerradas por minuto');
  const lg = el('div', 'ul-legend');
  const sw = (c, t, line) => { const i = el('i'); i.style.background = c; if (line) i.className = 'ln'; lg.append(i, el('span', null, t)); };
  sw(ink2, '2º turno'); sw(teal(), '1º turno (sem prorrogação)', true);
  const box = el('div', 'pr-chart'); box.append(svg, lg); return box;
}

/** % das urnas do turno (com log, no Brasil) encerradas antes do minuto `ate` (Brasília) */
export function pctEncerradas(R, t, ate) {
  const a = R.encerramentos?.[t === 1 ? 't1' : 't2']; if (!a) return null;
  let s = 0, tot = 0; a.forEach((v, i) => { tot += v; if (R.encerramentos.min0 + i < ate) s += v; });
  return tot ? 100 * s / tot : null;
}

// app.js — protótipo da tela de apuração do 2º turno (dados sintéticos).
// Bibliotecas globais (UMD): maplibregl, deck, h3, d3 (d3-delaunay).
import {
  generateWorld, simulate2T, Sim, metrics, required, F, STRIDE, UFS, UF_LIST, REGIONS, REGION_ORDER,
  SCENARIOS, OUTLINE, inBrazil, NIGHT_END_MIN,
} from './sim.js';

const $ = (s) => document.querySelector(s);
const el = (tag, cls, txt) => { const e = document.createElement(tag); if (cls) e.className = cls; if (txt != null) e.textContent = txt; return e; };

// ---------------- Estado ----------------
const LS = (() => { try { return JSON.parse(localStorage.getItem('apur2t') || '{}'); } catch { return {}; } })();
const saveLS = () => { try { localStorage.setItem('apur2t', JSON.stringify({ cand: S.cand, theme: S.theme, fade: S.fade })); } catch { /* sem storage */ } };
const PRESETS = {
  hub: { dark: ['#3987e5', '#d9731f'], light: ['#2a78d6', '#d9731f'] },
  neutral: { dark: ['#8a7cf7', '#c98500'], light: ['#6a5ae0', '#b27600'] },
};
const S = {
  cand: LS.cand || { A: { name: 'Lula', num: '13', color: '#3987e5' }, B: { name: 'Flávio Bolsonaro', num: '22', color: '#d9731f' } },
  theme: LS.theme || 'dark', fade: LS.fade ?? true,
  scenario: 'virada', speed: 1, playing: true, minute: 0,
  mode: 'ritmo', layer: 'auto', resOff: 0, ufFilter: '',
  focus: { type: 'br' }, rankTab: 'lula', rankBy: 'muni',
  tv: { on: window.innerWidth >= 1100, idx: -1, viewStart: 0, pausedUntil: 0, views: [], lastSeen: {} },
  history: [], lastTickReal: Date.now(), dataMinute: 0,
};
const short = (k) => { const n = S.cand[k].name; return n.split(' ')[0] === 'Flávio' ? 'Flávio' : n.split(' ')[0]; };

// ---------------- Formatação pt-BR ----------------
const nf0 = new Intl.NumberFormat('pt-BR', { maximumFractionDigits: 0 });
const nf1 = new Intl.NumberFormat('pt-BR', { minimumFractionDigits: 1, maximumFractionDigits: 1 });
const nf2 = new Intl.NumberFormat('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const sign = (v) => (v > 0 ? '+' : v < 0 ? '−' : '±');
const fPP = (v, d = 1) => (v == null || !isFinite(v) ? '—' : sign(Math.abs(v) < 0.05 && d === 1 ? 0 : v) + (d === 2 ? nf2 : nf1).format(Math.abs(v)) + ' p.p.');
const fM = (v) => (v == null || !isFinite(v) ? '—' : sign(Math.abs(v) < 0.05 ? 0 : v) + nf1.format(Math.abs(v))); // votos de margem por 100 aptos
const U100 = 'por 100 aptos';
const fPct = (v, d = 1) => (v == null || !isFinite(v) ? '—' : (d === 0 ? nf0 : nf1).format(v) + '%');
function fN(v, signed = false) {
  if (v == null || !isFinite(v)) return '—';
  const a = Math.abs(v), s = signed ? sign(v) : v < 0 ? '−' : '';
  if (a >= 1e6) return s + nf2.format(a / 1e6) + ' mi';
  if (a >= 1e4) return s + nf1.format(a / 1e3) + ' mil';
  return s + nf0.format(a);
}
const hhmm = (min) => { const t = 17 * 60 + Math.floor(min); return String(Math.floor(t / 60) % 24).padStart(2, '0') + ':' + String(t % 60).padStart(2, '0'); };

// ---------------- Cor (OKLab) ----------------
const hex2rgb = (h) => { h = h.replace('#', ''); return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16)); };
const s2l = (c) => { c /= 255; return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4; };
const l2s = (c) => { const v = c <= 0.0031308 ? 12.92 * c : 1.055 * c ** (1 / 2.4) - 0.055; return Math.round(Math.max(0, Math.min(1, v)) * 255); };
function rgb2lab([r, g, b]) {
  r = s2l(r); g = s2l(g); b = s2l(b);
  const l = Math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b), m = Math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b), s = Math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b);
  return [0.2104542553 * l + 0.793617785 * m - 0.0040720468 * s, 1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s, 0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s];
}
function lab2rgb([L, a, b]) {
  const l = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3, m = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3, s = (L - 0.0894841775 * a - 1.291485548 * b) ** 3;
  return [l2s(4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s), l2s(-1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s), l2s(-0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s)];
}
const mix = (h1, h2, t) => { const a = rgb2lab(hex2rgb(h1)), b = rgb2lab(hex2rgb(h2)); return lab2rgb(a.map((v, i) => v + (b[i] - v) * t)); };
const rgbCss = (c, a = 1) => `rgba(${c[0]},${c[1]},${c[2]},${a})`;
const cssVar = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();

// ---------------- Modos de cor do mapa ----------------
// kind div: 5 classes por braço + neutro; seq: 6 classes. src: off = placar oficial; cmp = unidades comparáveis; proj; cov.
const MODES = [
  { key: 'ritmo', label: 'Ritmo vs necessário', kind: 'div', th: [0.4, 1, 2, 3.5, 6], pos: 'A', neg: 'B', src: 'cmp',
    title: () => `Δ margem sobre o 1º turno − necessário (${fM(REQ.req100)} ${U100})`, sub: () => `Acima: ${short('A')} no ritmo de vencer. Abaixo: fora do ritmo. Mesmas seções do 1º turno.`,
    ends: () => ['abaixo do ritmo', 'acima do ritmo'] },
  { key: 'margem', label: 'Margem no 2º turno', kind: 'div', th: [2, 5, 10, 20, 35], pos: 'A', neg: 'B', src: 'off',
    title: () => 'Margem no 2º turno (% dos válidos)', sub: () => 'Placar bruto das seções apuradas — não compara com o 1º turno.', ends: () => [`${short('B')} à frente`, `${short('A')} à frente`] },
  { key: 'dmargem', label: 'Δ margem vs 1º turno', kind: 'div', th: [0.75, 2, 3.5, 6, 9], pos: 'A', neg: 'B', src: 'cmp',
    title: () => `Δ margem ${short('A')} − ${short('B')} vs 1º turno (votos ${U100})`, sub: () => 'Mesmas seções nos dois turnos (observado na urna ou estimado pela zona).', ends: () => [`melhorou p/ ${short('B')}`, `melhorou p/ ${short('A')}`] },
  { key: 'dabst', label: 'Δ abstenção vs 1º turno', kind: 'div', th: [0.5, 1.5, 3, 5, 8], pos: 'teal', neg: 'mag', src: 'cmp',
    title: () => 'Δ abstenção vs 1º turno (p.p. do eleitorado)', sub: () => 'Verde-azulado: mais gente votou. Magenta: mais gente faltou.', ends: () => ['abstenção subiu', 'abstenção caiu'] },
  { key: 'dbn', label: 'Δ brancos + nulos', kind: 'div', th: [0.3, 0.8, 1.5, 2.5, 4], pos: 'teal', neg: 'mag', src: 'cmp',
    title: () => 'Δ brancos + nulos vs 1º turno (p.p. do comparecimento)', sub: () => 'Verde-azulado: menos brancos/nulos.', ends: () => ['subiu', 'caiu'] },
  { key: 'ganhoA', label: 'Ganho de votos de A', kind: 'seq', th: [0, 2, 4, 6, 9, 12], hue: 'A', src: 'cmp',
    title: () => `Votos ganhos por ${short('A')} sobre o 1º turno`, sub: () => 'p.p. do eleitorado apto. Atenção: sobe mecanicamente com a saída dos outros candidatos.', ends: () => ['0', '12+ p.p.'] },
  { key: 'ganhoB', label: 'Ganho de votos de B', kind: 'seq', th: [0, 2, 4, 6, 9, 12], hue: 'B', src: 'cmp',
    title: () => `Votos ganhos por ${short('B')} sobre o 1º turno`, sub: () => 'p.p. do eleitorado apto. Sobe mecanicamente com a saída dos outros candidatos.', ends: () => ['0', '12+ p.p.'] },
  { key: 'abst2', label: 'Abstenção no 2º turno', kind: 'seq', th: [10, 15, 20, 25, 30, 35], hue: 'teal', src: 'cmp',
    title: () => 'Abstenção no 2º turno', sub: () => '% do eleitorado apto que não votou.', ends: () => ['10%', '35%+'] },
  { key: 'proj', label: 'Projeção · votos que faltam', kind: 'div', th: [2, 5, 10, 20, 35], pos: 'A', neg: 'B', src: 'proj',
    title: () => 'Projeção das seções ainda não totalizadas', sub: () => 'Margem projetada do que falta; mais opaco = mais votos por apurar.', ends: () => [`favorece ${short('B')}`, `favorece ${short('A')}`] },
  { key: 'cob', label: '% apurado', kind: 'seq', th: [0, 20, 40, 60, 80, 99.5], hue: 'gray', src: 'cov',
    title: () => 'Cobertura da apuração', sub: () => 'Município/UF: % totalizado (oficial). Hexágono/local: % com boletim.', ends: () => ['0%', '100%'] },
];
const MODE = Object.fromEntries(MODES.map((m) => [m.key, m]));
let RAMPS = {};
function buildRamps() {
  const dark = S.theme === 'dark';
  const neutral = dark ? '#4a4d55' : '#d6d5cf';
  const hue = { A: S.cand.A.color, B: S.cand.B.color, teal: '#1f9c8e', mag: '#b5179e', gray: dark ? '#f2f1ee' : '#2b2b2b' };
  const steps = [0.3, 0.47, 0.64, 0.82, 1.0];
  const arm = (h) => steps.map((t, i) => (i === 4 && !dark ? mix(h, '#000000', 0.12) : mix(neutral, h, t)));
  RAMPS = { neutral: hex2rgb(neutral), noData: dark ? [120, 124, 132] : [190, 189, 184] };
  for (const m of MODES) {
    if (m.kind === 'div') RAMPS[m.key] = { pos: arm(hue[m.pos]), neg: arm(hue[m.neg]), mid: hex2rgb(neutral) };
    else RAMPS[m.key] = { seq: [0.16, 0.32, 0.5, 0.68, 0.84, 1].map((t) => mix(neutral, hue[m.hue], t)), below: dark ? [70, 72, 78] : [225, 224, 219] };
  }
  document.documentElement.style.setProperty('--A', S.cand.A.color);
  document.documentElement.style.setProperty('--B', S.cand.B.color);
}
function classColor(mode, v) {
  const R = RAMPS[mode.key];
  if (mode.kind === 'div') {
    const a = Math.abs(v); let k = 0;
    while (k < mode.th.length && a >= mode.th[k]) k++;
    return k === 0 ? R.mid : (v > 0 ? R.pos : R.neg)[k - 1];
  }
  if (v < mode.th[0]) return R.below;
  let k = 0;
  while (k < mode.th.length - 1 && v >= mode.th[k + 1]) k++;
  return R.seq[k];
}

// ---------------- Mundo sintético ----------------
let W, T, SIM, REQ, H = {}, MUNI_POLY = [], CELLS_RES = 6, BR_BOUNDS = [[-74, -34], [-34.5, 5.5]];
const RESES = [3, 4, 5, 6, 7, 8, 9];
const BAIRROS = ['Centro', 'Vila Nova', 'Jardim América', 'Boa Vista', 'São José', 'Santa Cruz', 'Industrial', 'Alto da Glória', 'Bela Vista', 'Liberdade', 'Primavera', 'Cidade Nova', 'Vila Rica', 'Água Verde', 'Planalto', 'Novo Horizonte', 'Morada do Sol', 'Esplanada'];
const RUAS = ['Rua das Flores', 'Av. Brasil', 'Rua Sete de Setembro', 'Rua XV de Novembro', 'Av. Getúlio Vargas', 'Rua Tiradentes', 'Rua São Paulo', 'Av. Independência', 'Rua Santos Dumont', 'Rua Rio Branco'];
const placeBairro = (p) => BAIRROS[(p * 2654435761 >>> 0) % BAIRROS.length];
const placeAddr = (p) => `${RUAS[(p * 40503 >>> 0) % RUAS.length]}, ${10 + ((p * 7919) % 1990)}`;

function buildH3() {
  const P = W.P, c9 = new Array(P);
  for (let p = 0; p < P; p++) c9[p] = h3.latLngToCell(W.pLat[p], W.pLng[p], 9);
  for (const r of RESES) {
    const map = new Map(), placeCell = new Int32Array(P), data = [];
    for (let p = 0; p < P; p++) {
      const id = r === 9 ? c9[p] : h3.cellToParent(c9[p], r);
      let i = map.get(id);
      if (i === undefined) { i = data.length; map.set(id, i); data.push({ h: id, i, uf: W.muniUf[W.pMuni[p]] }); }
      placeCell[p] = i;
    }
    H[r] = { data, map, placeCell, agg: new Float64Array(data.length * STRIDE), colors: new Uint8Array(data.length * 4) };
  }
}
function clipConvex(subject, clip) { // Sutherland–Hodgman: sujeito côncavo (contorno do Brasil) recortado por célula de Voronoi convexa
  let area = 0;
  for (let i = 0; i < clip.length - 1; i++) area += clip[i][0] * clip[i + 1][1] - clip[i + 1][0] * clip[i][1];
  const sg = area > 0 ? 1 : -1;
  let out = subject;
  for (let i = 0; i < clip.length - 1 && out.length; i++) {
    const a = clip[i], b = clip[i + 1], inp = out; out = [];
    const ins = (p) => sg * ((b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])) >= 0;
    const X = (p, q) => {
      const d = (p[0] - q[0]) * (a[1] - b[1]) - (p[1] - q[1]) * (a[0] - b[0]);
      const t = ((p[0] - a[0]) * (a[1] - b[1]) - (p[1] - a[1]) * (a[0] - b[0])) / d;
      return [p[0] + t * (q[0] - p[0]), p[1] + t * (q[1] - p[1])];
    };
    for (let j = 0; j < inp.length; j++) {
      const cur = inp[j], prev = inp[(j + inp.length - 1) % inp.length], ci = ins(cur), pi = ins(prev);
      if (ci) { if (!pi) out.push(X(prev, cur)); out.push(cur); } else if (pi) out.push(X(prev, cur));
    }
  }
  return out;
}
function buildMuniPolys() { // polígonos sintéticos (Voronoi das sedes ∩ contorno). Na implementação: malha IBGE.
  const del = d3.Delaunay.from(W.munis, (m) => m.lng, (m) => m.lat);
  const vor = del.voronoi([-75, -35, -33, 6]);
  MUNI_POLY = [];
  W.munis.forEach((m, i) => {
    const cell = vor.cellPolygon(i);
    if (!cell) return;
    const allIn = cell.every(([x, y]) => inBrazil(x, y));
    const poly = allIn ? cell : clipConvex(OUTLINE, cell);
    if (poly.length >= 3) MUNI_POLY.push({ i, poly });
  });
}

// ---------------- Analytics (mock no formato do analytics.json — ver DESIGN.md §8) ----------------
let AN = null;
function thermo(m) {
  return {
    cobertura_aptos: m.covElect, secoes_comparaveis: m.nCnt, totalizado: m.covTot, com_bu: m.covBU, observado: m.obsShare,
    d_margem_pp: m.dMargin, d_margem_100: m.dM100, d_margem_votos: m.dMarginV, d_abst_pp: m.dAbstPP, d_abst_eleitores: m.dAbstV,
    d_bn_pp: m.dBnPP, d_bn_votos: m.dBnV, d_l_votos: m.dA, d_o_votos: m.dB, d_l_pp_aptos: m.dAPP, d_o_pp_aptos: m.dBPP,
    captura: { l: m.capA, o: m.capB, bn: m.capBN, abst: m.capAbst, l_entre_decididos: m.capDecidedA },
    decomp: { comparecimento: m.decT, preferencia: m.decP },
    ritmo_100: m.dM100 == null ? null : m.dM100 - REQ.req100,
    placar: { l_pct: m.oShareA, margem_pp: m.oM, l: m.oA2, o: m.oB2 },
    projecao: { l_pct: m.projA, margem_pp: m.projM, margem_votos: m.projMV, faixa_pp: m.bandPP, faixa_votos: m.bandVotes },
  };
}
function buildAnalytics() {
  const br = metrics(SIM.br);
  REQ = required(br);
  const pair = (eff, ex, i) => ({ estimado: thermo(metrics(eff, i)), exato: thermo(metrics(ex, i)) });
  const regiao = {}, uf = {};
  REGION_ORDER.forEach((r, i) => { regiao[r] = pair(SIM.region, SIM.regionX, i); });
  UF_LIST.forEach((u, i) => { uf[u.toLowerCase()] = pair(SIM.uf, SIM.ufX, i); });
  const tbx = thermo(metrics(SIM.brX));
  const tb = thermo(br);
  // virada
  const hist = S.history;
  let occurred = null;
  for (let i = 1; i < hist.length; i++) if (Math.sign(hist[i].margem_apurada_pp) !== Math.sign(hist[i - 1].margem_apurada_pp) && hist[i - 1].margem_apurada_pp !== 0 && hist[i].pct_tot > 2) occurred = hist[i];
  const lead = Math.sign(br.oM || 0), plead = Math.sign(br.projM || 0);
  let virada = 'nao';
  if (occurred && br.covTot < 1) virada = 'ocorreu';
  else if (br.covTot < 1 && lead !== 0 && plead !== lead) virada = Math.abs(br.projM) > br.bandPP ? 'projetada' : 'provavel';
  else if (br.covTot < 1 && Math.abs(br.projM) <= br.bandPP) virada = 'incerta';
  AN = {
    generated_at: new Date().toISOString(), source: 'mock', minute: S.minute,
    thermometer: { br: { estimado: tb, exato: tbx }, regiao, uf },
    delta_req: REQ.req100, delta_obs: br.dM100, delta_obs_exato: tbx.d_margem_100, delta_req_pp_validos: REQ.reqPP, req_votos: REQ.gapVotes, req_captura: REQ.reqCapture,
    projection: { ...tb.projecao, virada, virada_em: occurred ? { min: occurred.min, pct_tot: occurred.pct_tot } : null, leader_now: lead, leader_proj: plead },
    history: S.history,
    _br: br,
  };
}
function pushHistory() {
  const b = metrics(SIM.br);
  if (!b.nTot) return;
  const last = S.history[S.history.length - 1];
  const pt = { min: S.minute, pct_tot: b.covTot * 100, margem_apurada_pp: b.oM, margem_proj_pp: b.projM, faixa_pp: b.bandPP, delta_obs: b.dM100 };
  if (last && last.min === S.minute) S.history[S.history.length - 1] = pt; else S.history.push(pt);
}

// ---------------- Simulação (substitui o polling de manifest.json) ----------------
function runTo(min, withHistory = true) {
  min = Math.max(0, Math.min(NIGHT_END_MIN, min));
  if (min < S.minute) { S.history = S.history.filter((h) => h.min <= min); SIM.reset(); SIM.minute = -1; }
  if (withHistory && min - S.minute > 6) { // preenche a história em passos de 5 min (para o gráfico)
    for (let t = Math.ceil((S.minute + 1) / 5) * 5; t < min; t += 5) { SIM.advanceToMinute(t); SIM.step(); S.minute = t; pushHistory(); }
  }
  SIM.advanceToMinute(min); SIM.step(); S.minute = min;
  if (withHistory) pushHistory();
  buildAnalytics();
  S.lastTickReal = Date.now(); S.dataMinute = min;
}

// ---------------- Mapa ----------------
let map, overlay, firstSymbol;
// H3HexagonLayer não desenhou em modo intercalado (deck 9.0 + MapLibre 4.7) no teste; usamos o overlay por cima do mapa.
// Rótulos ficam sob o preenchimento semitransparente. ?interleaved=1 reativa para testes.
const INTERLEAVED = new URLSearchParams(location.search).has('interleaved');
const STYLE = { dark: 'https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json', light: 'https://basemaps.cartocdn.com/gl/positron-gl-style/style.json' };
function initMap() {
  map = new maplibregl.Map({ container: 'map', style: STYLE[S.theme], bounds: BR_BOUNDS, fitBoundsOptions: { padding: 30 }, attributionControl: { compact: true }, dragRotate: false, pitchWithRotate: false });
  overlay = new deck.MapboxOverlay({ interleaved: INTERLEAVED, layers: [], getCursor: ({ isHovering }) => (isHovering ? 'pointer' : 'grab') });
  map.addControl(overlay);
  map.on('style.load', () => { firstSymbol = map.getStyle().layers.find((l) => l.type === 'symbol')?.id; updateLayers(); });
  map.on('zoomend', () => { onZoom(); });
  map.on('moveend', () => { renderBadge(); });
}
function resForZoom(z) { const t = [4.5, 5.9, 7.3, 8.7, 10.1, 11.5]; let r = 3; for (const x of t) if (z >= x) r++; return Math.max(3, Math.min(9, r + S.resOff)); }
function effLayer() {
  if (S.layer !== 'auto') return S.layer;
  const z = map ? map.getZoom() : 4;
  return z < 7.6 ? 'muni' : z < 11.8 ? 'hex' : 'locais';
}
function onZoom() {
  const r = resForZoom(map.getZoom());
  if (r !== CELLS_RES) { CELLS_RES = r; aggregateCells(); recolor(); }
  updateLayers(); renderBadge(); renderLegend();
}
function aggregateCells() {
  const C = H[CELLS_RES], a = C.agg, pc = C.placeCell, pl = SIM.place;
  a.fill(0);
  for (let p = 0; p < W.P; p++) { const o = p * STRIDE, co = pc[p] * STRIDE; for (let f = 0; f < STRIDE; f++) a[co + f] += pl[o + f]; }
}

// valor do modo para uma unidade (array agregado, índice, nível)
function unitVal(mode, arr, i, level) {
  const o = i * STRIDE, g = (f) => arr[o + f];
  const nSec = g(F.nSec);
  if (!nSec) return null;
  const big = level === 'muni' || level === 'uf';
  if (mode.src === 'cov') return { v: (big ? g(F.nTot) : g(F.nBU)) / nSec * 100, cov: 1, obs: null };
  if (mode.src === 'proj') {
    const pA = g(F.pA), pB = g(F.pB);
    if (pA + pB <= 0) return null;
    return { v: (pA - pB) / (pA + pB) * 100, cov: 1 - g(F.nTot) / nSec };
  }
  if (mode.src === 'off' && big) {
    const a = g(F.oA2), b = g(F.oB2);
    if (!g(F.nTot) || a + b <= 0) return null;
    return { v: (a - b) / (a + b) * 100, cov: g(F.nTot) / nSec };
  }
  const n = g(F.nCnt), A2 = g(F.A2), B2 = g(F.B2), cA = g(F.c_A1), cB = g(F.c_B1), cO = g(F.c_O1), cAp = g(F.c_aptos);
  if (!n || A2 + B2 <= 0 || cA + cB + cO <= 0 || !cAp) return null;
  const cov = n / nSec, obs = g(F.nBU) / n;
  const m2 = (A2 - B2) / (A2 + B2) * 100, m1 = (cA - cB) / (cA + cB + cO) * 100;
  let v;
  switch (mode.key) {
    case 'margem': v = m2; break;
    case 'ritmo': v = ((A2 - B2) - (cA - cB)) / cAp * 100 - REQ.req100; break;
    case 'dmargem': v = ((A2 - B2) - (cA - cB)) / cAp * 100; break;
    case 'dabst': v = (g(F.comp2) - g(F.c_comp1)) / cAp * 100; break; // positivo = abstenção caiu
    case 'dbn': v = -((g(F.BN2) / g(F.comp2)) - (g(F.c_BN1) / g(F.c_comp1))) * 100; break;
    case 'ganhoA': v = (A2 - cA) / cAp * 100; break;
    case 'ganhoB': v = (B2 - cB) / cAp * 100; break;
    case 'abst2': v = (1 - g(F.comp2) / cAp) * 100; break;
    default: v = 0;
  }
  return isFinite(v) ? { v, cov, obs } : null;
}
function rgbaFor(mode, uv, level, ufIdx) {
  const dim = S.ufFilter && ufIdx !== W.ufIndex[S.ufFilter] ? 0.22 : 1;
  if (!uv) {
    if (level === 'muni' || level === 'uf') return [...RAMPS.noData, Math.round(38 * dim)];
    if (level === 'place') return [225, 232, 245, Math.round(110 * dim)];
    return [0, 0, 0, 0];
  }
  let c = classColor(mode, uv.v);
  // estimado pela zona = cor lavada (mistura com o neutro); observado na urna = cor cheia
  if (uv.obs != null && (level === 'cell' || level === 'place')) { const k2 = 0.32 * (1 - uv.obs), N = RAMPS.neutral; c = [c[0] + (N[0] - c[0]) * k2, c[1] + (N[1] - c[1]) * k2, c[2] + (N[2] - c[2]) * k2].map(Math.round); }
  const base = level === 'place' ? 240 : level === 'cell' ? 205 : 195;
  let k = 1;
  if (mode.src === 'proj') k = uv.cov <= 0 ? 0.1 : 0.25 + 0.75 * uv.cov;
  else if (S.fade && mode.src !== 'cov') k = 0.3 + 0.7 * uv.cov;
  return [c[0], c[1], c[2], Math.round(base * k * dim)];
}
let COLORS = { muni: null, uf: null, cell: null, place: null }, colorVersion = 0;
function recolor() {
  const mode = MODE[S.mode];
  const M = W.munis.length;
  COLORS.muni = new Uint8Array(M * 4);
  for (let m = 0; m < M; m++) COLORS.muni.set(rgbaFor(mode, unitVal(mode, SIM.muni, m, 'muni'), 'muni', W.muniUf[m]), m * 4);
  const ufc = UF_LIST.map((u, i) => rgbaFor(mode, unitVal(mode, SIM.uf, i, 'uf'), 'uf', i));
  COLORS.uf = new Uint8Array(M * 4);
  for (let m = 0; m < M; m++) COLORS.uf.set(ufc[W.muniUf[m]], m * 4);
  const C = H[CELLS_RES];
  for (let c = 0; c < C.data.length; c++) C.colors.set(rgbaFor(mode, unitVal(mode, C.agg, c, 'cell'), 'cell', C.data[c].uf), c * 4);
  COLORS.place = new Uint8Array(W.P * 4);
  for (let p = 0; p < W.P; p++) COLORS.place.set(rgbaFor(mode, unitVal(mode, SIM.place, p, 'place'), 'place', W.muniUf[W.pMuni[p]]), p * 4);
  colorVersion++;
  updateLayers();
}
const pick4 = (arr, i) => [arr[i * 4], arr[i * 4 + 1], arr[i * 4 + 2], arr[i * 4 + 3]];
function updateLayers() {
  if (!overlay || !COLORS.muni) return;
  const L = effLayer(), dark = S.theme === 'dark';
  const beforeId = INTERLEAVED ? firstSymbol : undefined;
  const TR = { getFillColor: { duration: 700 } };
  const layers = [];
  if (L === 'muni' || L === 'uf') {
    const src = L === 'uf' ? COLORS.uf : COLORS.muni;
    layers.push(new deck.PolygonLayer({
      id: 'muni', data: MUNI_POLY, getPolygon: (d) => d.poly, beforeId, pickable: true, stroked: true, filled: true,
      getFillColor: (d) => pick4(src, d.i), getLineColor: dark ? [255, 255, 255, L === 'uf' ? 10 : 34] : [0, 0, 0, L === 'uf' ? 10 : 30],
      lineWidthUnits: 'pixels', getLineWidth: 0.6, updateTriggers: { getFillColor: [colorVersion, L] }, transitions: TR,
    }));
  } else {
    const C = H[CELLS_RES], k = L === 'locais' ? 0.45 : 1;
    layers.push(new deck.H3HexagonLayer({
      id: 'hex', data: C.data, getHexagon: (d) => d.h, beforeId, pickable: L === 'hex', filled: true, stroked: false, extruded: false,
      coverage: 0.9, highPrecision: 'auto', getFillColor: (d) => { const c = pick4(C.colors, d.i); c[3] = Math.round(c[3] * k); return c; },
      updateTriggers: { getFillColor: [colorVersion, CELLS_RES, L] }, transitions: TR,
    }));
    if (L === 'locais' || map.getZoom() >= 10.5) {
      layers.push(new deck.ScatterplotLayer({
        id: 'places', data: { length: W.P }, beforeId, pickable: true, radiusUnits: 'meters',
        getPosition: (_, { index }) => [W.pLng[index], W.pLat[index]],
        getRadius: (_, { index }) => 22 * Math.sqrt(SIM.place[index * STRIDE + F.aptos] / 100), radiusMinPixels: 2, radiusMaxPixels: 13,
        stroked: true, lineWidthUnits: 'pixels', getLineWidth: 1, getLineColor: dark ? [255, 255, 255, 150] : [20, 20, 20, 120],
        getFillColor: (_, { index }) => pick4(COLORS.place, index), updateTriggers: { getFillColor: colorVersion }, transitions: TR,
      }));
    }
  }
  // destaque da seleção
  const f = S.focus;
  if (f.type === 'muni') {
    const mp = MUNI_POLY.find((d) => d.i === f.id);
    if (mp) layers.push(new deck.PolygonLayer({ id: 'sel-muni', data: [mp], getPolygon: (d) => d.poly, filled: false, stroked: true, getLineColor: dark ? [255, 255, 255, 230] : [0, 0, 0, 220], lineWidthUnits: 'pixels', getLineWidth: 2.5 }));
  } else if (f.type === 'cell') {
    layers.push(new deck.H3HexagonLayer({ id: 'sel-cell', data: [{ h: f.h }], getHexagon: (d) => d.h, filled: false, stroked: true, extruded: false, getLineColor: dark ? [255, 255, 255, 240] : [0, 0, 0, 220], lineWidthUnits: 'pixels', getLineWidth: 2.5 }));
  } else if (f.type === 'place') {
    layers.push(new deck.ScatterplotLayer({ id: 'sel-place', data: [f.id], getPosition: (p) => [W.pLng[p], W.pLat[p]], radiusUnits: 'pixels', getRadius: 14, filled: false, stroked: true, lineWidthUnits: 'pixels', getLineWidth: 2.5, getLineColor: dark ? [255, 255, 255, 240] : [0, 0, 0, 220] }));
  }
  overlay.setProps({ layers, onHover, onClick });
}
function renderBadge() {
  if (!map) return;
  const L = effLayer(), names = { muni: 'Municípios (agregado oficial)', uf: 'UFs (agregado oficial)', hex: `Hexágonos H3 · res ${CELLS_RES}`, locais: `Locais de votação + H3 res ${CELLS_RES}` };
  const edge = { 3: '69 km', 4: '26 km', 5: '9,9 km', 6: '3,7 km', 7: '1,4 km', 8: '530 m', 9: '200 m' };
  $('#resBadge').textContent = `${names[L]}${L === 'hex' || L === 'locais' ? ' · lado ≈ ' + edge[CELLS_RES] : ''} · zoom ${nf1.format(map.getZoom())}`;
}

// ---------------- Hover / clique ----------------
function onHover(info) {
  const tt = $('#tooltip');
  if (!info.layer || info.index < 0 || !['muni', 'hex', 'places'].includes(info.layer.id)) { tt.hidden = true; return; }
  tt.replaceChildren();
  let m, title, sub;
  if (info.layer.id === 'muni') {
    const i = info.object.i, mu = W.munis[i];
    m = metrics(SIM.muni, i); title = `${mu.name} (${mu.uf})`;
    sub = `Totalizado ${fPct(m.covTot * 100, 0)} (oficial) · com boletim ${fPct(m.covBU * 100, 0)} · ${mu.zonas.length} zona(s)`;
  } else if (info.layer.id === 'hex') {
    m = metrics(H[CELLS_RES].agg, info.object.i); title = `Hexágono H3 · res ${CELLS_RES} · ${nf0.format(m.nSec)} seções`;
    sub = `% apurado nas zonas: ${fPct(m.zoneTot * 100, 0)} · com boletim: ${fPct(m.covBU * 100, 0)} · valores: ${fPct(m.covBU * 100, 0)} observado na urna, ${fPct(m.nEst / m.nSec * 100, 0)} estimado pela zona`;
  } else {
    const p = info.index, mu = W.munis[W.pMuni[p]];
    m = metrics(SIM.place, p); title = W.pName[p];
    sub = `${placeBairro(p)} · ${mu.name} (${mu.uf}) · Zona ${W.pZona[p]} · ${W.pSecCount[p]} seções · % apurado na zona: ${fPct(m.zoneTot * 100, 0)} · com boletim: ${m.nBU} de ${m.nSec}`;
  }
  tt.append(el('div', 't', title), el('div', 'lg-sub', sub));
  const row = (k, v) => { const r = el('div', 'r'); r.append(el('span', null, k), el('b', null, v)); tt.append(r); };
  if (info.layer.id === 'muni') row('Placar oficial', m.oM == null ? '—' : `${m.oM >= 0 ? short('A') : short('B')} ${fPP(Math.abs(m.oM))}`);
  if (m.has) {
    if (info.layer.id !== 'muni') row('Margem 2T', `${m.m2 >= 0 ? short('A') : short('B')} ${fPP(Math.abs(m.m2))}`);
    row('Δ margem vs 1T', `${fM(m.dM100)} ${U100} (${fN(m.dMarginV, true)} votos)`);
    row('Ritmo vs necessário', `${fM(m.dM100 - REQ.req100)} ${U100}`);
    row('Δ abstenção', `${fPP(m.dAbstPP)} (${fN(m.dAbstV, true)} ausentes)`);
    row('Δ brancos+nulos', fPP(m.dBnPP));
    row('Base da comparação', m.obsShare >= 0.999 ? 'observado na urna' : m.obsShare <= 0.001 ? 'estimado pela zona' : `${fPct(m.obsShare * 100, 0)} observado · resto estimado pela zona`);
  } else row('Comparação com o 1T', 'zona ainda sem apuração');
  if (m.remM != null && m.covTot < 1) row('Projeção do que falta', `${m.remM >= 0 ? short('A') : short('B')} ${fPP(Math.abs(m.remM))}`);
  tt.hidden = false;
  const box = $('#mapwrap').getBoundingClientRect();
  const x = Math.min(info.x + 14, box.width - tt.offsetWidth - 8), y = Math.min(info.y + 14, box.height - tt.offsetHeight - 8);
  tt.style.left = x + 'px'; tt.style.top = y + 'px';
}
function onClick(info) {
  if (!info.layer || info.index < 0) return;
  if (info.layer.id === 'muni') setFocus({ type: 'muni', id: info.object.i });
  else if (info.layer.id === 'hex') setFocus({ type: 'cell', res: CELLS_RES, h: info.object.h, idx: info.object.i });
  else if (info.layer.id === 'places') setFocus({ type: 'place', id: info.index });
}
function setFocus(f, fly = false) {
  S.focus = f;
  if (fly) flyToFocus(f);
  updateLayers(); renderRight();
}
function boundsOf(filter) {
  let x0 = 180, y0 = 90, x1 = -180, y1 = -90;
  W.munis.forEach((m, i) => { if (filter(m, i)) { x0 = Math.min(x0, m.lng); x1 = Math.max(x1, m.lng); y0 = Math.min(y0, m.lat); y1 = Math.max(y1, m.lat); } });
  return [[x0 - 0.4, y0 - 0.4], [x1 + 0.4, y1 + 0.4]];
}
function flyToFocus(f, duration = 2600) {
  const pad = { top: 90, bottom: 60, left: 40, right: 40 };
  if (f.type === 'br') map.fitBounds(BR_BOUNDS, { padding: pad, duration });
  else if (f.type === 'region') map.fitBounds(boundsOf((m) => m.region === f.id), { padding: pad, duration });
  else if (f.type === 'uf') map.fitBounds(boundsOf((m) => m.uf === f.id), { padding: pad, duration });
  else if (f.type === 'muni') { const m = W.munis[f.id]; map.flyTo({ center: [m.lng, m.lat], zoom: m.kind === 'municipio' ? 10.2 : Math.max(9.2, 11.4 - Math.log10(m.pop) * 0.9), duration }); }
  else if (f.type === 'place') map.flyTo({ center: [W.pLng[f.id], W.pLat[f.id]], zoom: 14, duration });
  else if (f.type === 'cell') { const [lat, lng] = h3.cellToLatLng(f.h); map.flyTo({ center: [lng, lat], zoom: Math.max(map.getZoom(), 8), duration }); }
}
function focusMetrics(f) {
  switch (f.type) {
    case 'region': return { m: metrics(SIM.region, REGION_ORDER.indexOf(f.id)), name: REGIONS[f.id], kind: 'Região' };
    case 'uf': return { m: metrics(SIM.uf, W.ufIndex[f.id]), name: UFS[f.id][6], kind: 'UF' };
    case 'muni': { const mu = W.munis[f.id]; return { m: metrics(SIM.muni, f.id), name: `${mu.name} (${mu.uf})`, kind: mu.kind === 'capital' ? 'Capital' : 'Município' }; }
    case 'cell': {
      const R = H[f.res], idx = R.map.get(f.h), tmp = new Float64Array(STRIDE);
      for (let p = 0; p < W.P; p++) if (R.placeCell[p] === idx) { const o = p * STRIDE; for (let k = 0; k < STRIDE; k++) tmp[k] += SIM.place[o + k]; }
      return { m: metrics(tmp), name: `Hexágono res ${f.res}`, kind: 'H3 · ' + f.h };
    }
    case 'place': { const mu = W.munis[W.pMuni[f.id]]; return { m: metrics(SIM.place, f.id), name: W.pName[f.id], kind: `${placeBairro(f.id)} · ${mu.name} (${mu.uf})` }; }
    default: return { m: AN._br, name: 'Brasil', kind: 'País' };
  }
}

// ---------------- Painéis ----------------
function keyEl(k) { const s = el('span', 'key'); s.style.background = `var(--${k})`; return s; }
function tile(label, value, subtxt, key) {
  const t = el('div', 'tile'); const l = el('div', 'l'); if (key) l.append(keyEl(key)); l.append(label);
  t.append(l, el('div', 'v', value), el('div', 's', subtxt)); return t;
}
function captureBlock(th, compact = false) {
  const c = th.captura, frag = el('div');
  if (c.l == null) { frag.append(el('div', 'capnote', 'Captura do eleitorado em disputa: aguardando as primeiras zonas.')); return frag; }
  const parts = [['A', Math.max(0, c.l), short('A')], ['B', Math.max(0, c.o), short('B')], ['n', Math.max(0, c.bn), 'Branco/nulo'], ['x', Math.max(0, c.abst), 'Deixou de votar']];
  const tot = parts.reduce((s, p) => s + p[1], 0) || 1;
  const bar = el('div', 'capbar'), lab = el('div', 'caplab');
  const col = { A: 'var(--A)', B: 'var(--B)', n: 'var(--muted)', x: 'var(--grid)' };
  for (const [k, v, n] of parts) {
    const i = el('i'); i.style.width = (v / tot * 100) + '%'; i.style.background = col[k]; bar.append(i);
    const s = el('span'); const kk = el('span', 'key'); kk.style.background = col[k]; s.append(kk, `${n} ${fPct(v / tot * 100, 0)}`); lab.append(s);
  }
  if (!compact) frag.append(el('div', 'kicker', 'Captura do eleitorado em disputa'));
  frag.append(bar, lab);
  if (c.l_entre_decididos != null) {
    const note = el('div', 'capnote');
    note.append('Entre os que escolheram um lado: ', el('b', null, `${short('A')} ${fPct(c.l_entre_decididos * 100, 0)}`), ` × ${fPct((1 - c.l_entre_decididos) * 100, 0)} ${short('B')}`);
    if (AN.req_captura != null && !compact) note.append(` — ${short('A')} precisa de `, el('b', null, fPct(AN.req_captura * 100, 0)));
    frag.append(note);
  }
  return frag;
}
function renderLeft() {
  const L = $('#left'); L.replaceChildren();
  const tb = AN.thermometer.br.estimado, tx = AN.thermometer.br.exato, br = AN._br;
  // 1. Termômetro
  const c1 = el('div', 'card');
  c1.append(el('h2', null, 'Termômetro da campanha · Brasil'));
  c1.append(el('div', 'sub', `2º turno vs as mesmas seções no 1º turno, pelo swing de cada zona eleitoral · cobre ${fPct(tb.cobertura_aptos * 100)} do eleitorado (${fPct(tb.observado * 100, 0)} observado na urna)`));
  const hero = el('div', 'hero');
  hero.append(el('span', 'v', fM(tb.d_margem_100)), el('span', 'vs', tb.d_margem_100 == null ? 'aguardando as primeiras zonas' : `votos de margem ${short('A')} − ${short('B')} a cada 100 eleitores, sobre o 1º turno · ${fN(tb.d_margem_votos, true)} votos`));
  c1.append(hero);
  const ex = el('div', 'lg-note');
  ex.append(`Exato (zonas 100% totalizadas + boletins): ${fM(tx.d_margem_100)} ${U100} em ${fPct(tx.cobertura_aptos * 100)} do eleitorado · em p.p. dos válidos: ${fPP(tb.d_margem_pp)}.`);
  c1.append(ex);
  const tiles = el('div', 'tiles');
  tiles.append(
    tile('Δ abstenção', fPP(tb.d_abst_pp), tb.d_abst_eleitores == null ? '—' : `${fN(tb.d_abst_eleitores, true)} eleitores ausentes`),
    tile('Δ brancos + nulos', fPP(tb.d_bn_pp), tb.d_bn_votos == null ? '—' : `${fN(tb.d_bn_votos, true)} votos`),
    tile(`Δ votos ${short('A')}`, fN(tb.d_l_votos, true), `${fPP(tb.d_l_pp_aptos)} do eleitorado`, 'A'),
    tile(`Δ votos ${short('B')}`, fN(tb.d_o_votos, true), `${fPP(tb.d_o_pp_aptos)} do eleitorado`, 'B'),
  );
  tiles.classList.add('four');
  c1.append(tiles);
  const cap = captureBlock(tb); cap.style.marginTop = '.6rem'; c1.append(cap);
  L.append(c1);

  // 2. Ritmo necessário vs observado
  const c2 = el('div', 'card');
  c2.append(el('h2', null, 'Ritmo necessário vs observado'));
  const req = AN.delta_req, obs = AN.delta_obs;
  const s2 = el('div', 'sentence');
  s2.append(`${S.cand.A.name} precisa ganhar `, el('b', null, `${fM(req)} voto de margem a cada 100 eleitores`), ` sobre o 1º turno (≈ ${fN(AN.req_votos)} votos no total); nas zonas apuradas está ganhando `, el('b', null, fM(obs)), '.');
  c2.append(s2);
  const lo = Math.min(-1, obs ?? 0, req) - 1, hi = Math.max(req, obs ?? 0, 1) + 1.5, X = (v) => ((v - lo) / (hi - lo)) * 100;
  const g = el('div', 'gauge'); g.append(el('div', 'axis'));
  const z = el('div', 'zero'); z.style.left = X(0) + '%'; g.append(z);
  if (obs != null) {
    const b = el('div', 'bar'); const a = Math.min(X(0), X(obs)), w = Math.abs(X(obs) - X(0));
    b.style.left = a + '%'; b.style.width = w + '%'; b.style.background = obs >= req ? 'var(--A)' : 'var(--B)'; g.append(b);
    const ol = el('div', 'obslab', `observado ${fM(obs)}`); ol.style.left = X(obs) + '%'; g.append(ol);
  }
  const r = el('div', 'req'); r.style.left = `calc(${X(req)}% - 1px)`; g.append(r);
  const rl = el('div', 'reqlab', `necessário ${fM(req)}`); rl.style.left = X(req) + '%'; g.append(rl);
  c2.append(g);
  if (obs != null) {
    const st = el('span', 'status');
    st.append(obs >= req ? '✓ acima do ritmo' : '✗ abaixo do ritmo', el('span', 'kicker', ` · ${fM(obs - req)} ${U100}`));
    c2.append(st);
  }
  L.append(c2);

  // 3. Projeção e virada
  const c3 = el('div', 'card'), pj = AN.projection;
  c3.append(el('h2', null, 'Projeção para 100% e virada'));
  c3.append(el('div', 'sub', 'Oficial já totalizado + modelo nas seções que faltam (swing município → UF → região → Brasil, com encolhimento). Faixa de 90%.'));
  const ph = el('div', 'proj-head');
  const left = el('div');
  left.append(el('div', 'kicker', 'Projeção final'));
  const pn = el('div', 'proj-num'); pn.append(keyEl(pj.l_pct >= 50 ? 'A' : 'B'), `${pj.l_pct >= 50 ? short('A') : short('B')} ${fPct(Math.max(pj.l_pct, 100 - pj.l_pct))}`);
  left.append(pn, el('div', 'sub', `margem ${fPP(pj.margem_pp)} ± ${nf1.format(pj.faixa_pp)} · ${fN(pj.margem_votos, true)} votos`));
  const chip = el('span', 'status');
  const lead = pj.leader_now > 0 ? short('A') : short('B'), plead = pj.leader_proj > 0 ? short('A') : short('B');
  const vtxt = { ocorreu: `↺ Virada às ${hhmm(pj.virada_em?.min ?? 0)} (${fPct(pj.virada_em?.pct_tot ?? 0, 0)})`, projetada: `↗ Virada de ${plead} projetada`, provavel: `↗ Virada de ${plead} provável · faixa cruza o empate`, incerta: '≈ Indefinido · faixa cruza o empate', nao: br.covTot >= 1 ? `■ Final: ${lead} vence` : `→ ${lead} segue à frente` };
  chip.textContent = vtxt[pj.virada];
  ph.append(left, chip);
  c3.append(ph);
  const ch = el('div', 'chart'); ch.id = 'chart'; c3.append(ch);
  const lg = el('div', 'lgd');
  lg.innerHTML = '<span><span class="ln"></span>Placar oficial</span><span><span class="ln thin"></span>Projeção ao longo da noite</span><span><span class="band"></span>Faixa 90%</span><span><span class="ln dash"></span>Caminho projetado</span>';
  c3.append(lg);
  L.append(c3);
  renderChart(ch);
}

function renderChart(box) {
  const hist = AN.history, br = AN._br;
  const w = box.clientWidth || 480, h = Math.round(parseFloat(getComputedStyle(document.documentElement).fontSize) * (window.innerWidth > 1100 ? 11.5 : 14));
  const m = { l: 40, r: 12, t: 10, b: 26 };
  const iw = w - m.l - m.r, ih = h - m.t - m.b;
  let ymin = -4, ymax = 4;
  for (const p of hist) { ymin = Math.min(ymin, p.margem_apurada_pp, p.margem_proj_pp - p.faixa_pp); ymax = Math.max(ymax, p.margem_apurada_pp, p.margem_proj_pp + p.faixa_pp); }
  ymin = Math.max(ymin, -30); ymax = Math.min(ymax, 30);
  const pad = (ymax - ymin) * 0.08; ymin -= pad; ymax += pad;
  const X = (v) => m.l + (v / 100) * iw, Y = (v) => m.t + (1 - (Math.max(ymin, Math.min(ymax, v)) - ymin) / (ymax - ymin)) * ih;
  const ns = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(ns, 'svg'); svg.setAttribute('viewBox', `0 0 ${w} ${h}`); svg.setAttribute('height', h);
  svg.setAttribute('role', 'img'); svg.setAttribute('aria-label', 'Margem por % de seções totalizadas');
  const add = (tag, attrs, txt) => { const e = document.createElementNS(ns, tag); for (const k in attrs) e.setAttribute(k, attrs[k]); if (txt != null) e.textContent = txt; svg.append(e); return e; };
  const A = cssVar('--A'), B = cssVar('--B'), ink = cssVar('--ink'), ink2 = cssVar('--ink-2'), muted = cssVar('--muted'), grid = cssVar('--grid'), axis = cssVar('--axis');
  const wash = +cssVar('--wash') || 0.08;
  add('rect', { x: m.l, y: Y(ymax), width: iw, height: Y(0) - Y(ymax), fill: A, opacity: wash });
  add('rect', { x: m.l, y: Y(0), width: iw, height: Y(ymin) - Y(0), fill: B, opacity: wash });
  const step = (ymax - ymin) > 30 ? 10 : (ymax - ymin) > 12 ? 5 : 2;
  for (let v = Math.ceil(ymin / step) * step; v <= ymax; v += step) {
    add('line', { x1: m.l, x2: m.l + iw, y1: Y(v), y2: Y(v), stroke: v === 0 ? axis : grid, 'stroke-width': 1 });
    add('text', { x: m.l - 6, y: Y(v) + 4, 'text-anchor': 'end', 'font-size': 11, fill: muted, class: 'tab' }, (v > 0 ? '+' : v < 0 ? '−' : '') + Math.abs(v));
  }
  for (const v of [0, 25, 50, 75, 100]) add('text', { x: X(v), y: h - 8, 'text-anchor': v === 0 ? 'start' : v === 100 ? 'end' : 'middle', 'font-size': 11, fill: muted }, v + '%');
  add('text', { x: m.l + 6, y: Y(ymax) + 14, 'font-size': 11, fill: ink2 }, `${short('A')} à frente`);
  add('text', { x: m.l + 6, y: Y(ymin) - 6, 'font-size': 11, fill: ink2 }, `${short('B')} à frente`);
  add('text', { x: m.l + iw, y: h - 8 - 13, 'text-anchor': 'end', 'font-size': 10, fill: muted }, 'seções totalizadas');
  if (hist.length) {
    const band = hist.map((p) => `${X(p.pct_tot)},${Y(p.margem_proj_pp + p.faixa_pp)}`).concat(hist.slice().reverse().map((p) => `${X(p.pct_tot)},${Y(p.margem_proj_pp - p.faixa_pp)}`));
    add('polygon', { points: band.join(' '), fill: ink, opacity: 0.12 });
    add('polyline', { points: hist.map((p) => `${X(p.pct_tot)},${Y(p.margem_proj_pp)}`).join(' '), fill: 'none', stroke: ink2, 'stroke-width': 1.5, 'stroke-linejoin': 'round' });
    add('polyline', { points: hist.map((p) => `${X(p.pct_tot)},${Y(p.margem_apurada_pp)}`).join(' '), fill: 'none', stroke: ink, 'stroke-width': 2.5, 'stroke-linejoin': 'round', 'stroke-linecap': 'round' });
    // caminho projetado até 100% (mistura uniforme do que falta) + cone
    const last = hist[hist.length - 1];
    if (last.pct_tot < 99.9) {
      const oA = br.oA2, oB = br.oB2, pA = br.pA, pB = br.pB, pts = [], up = [], dn = [];
      let cross = null, prev = null;
      for (let k = 0; k <= 20; k++) {
        const f = k / 20, a = oA + f * pA, b = oB + f * pB, mm = (a - b) / (a + b) * 100, x = last.pct_tot + f * (100 - last.pct_tot);
        pts.push(`${X(x)},${Y(mm)}`); up.push(`${X(x)},${Y(mm + br.bandPP * f)}`); dn.push(`${X(x)},${Y(mm - br.bandPP * f)}`);
        if (prev != null && Math.sign(prev) !== Math.sign(mm) && cross == null) cross = x;
        prev = mm;
      }
      add('polygon', { points: up.concat(dn.reverse()).join(' '), fill: br.projM >= 0 ? A : B, opacity: 0.18 });
      add('polyline', { points: pts.join(' '), fill: 'none', stroke: ink, 'stroke-width': 2, 'stroke-dasharray': '5 4' });
      if (cross != null) {
        add('circle', { cx: X(cross), cy: Y(0), r: 5, fill: cssVar('--panel'), stroke: ink, 'stroke-width': 2 });
        add('text', { x: X(cross), y: Y(0) - 9, 'text-anchor': cross > 80 ? 'end' : 'middle', 'font-size': 11, 'font-weight': 700, fill: ink }, `virada projetada ~${nf0.format(cross)}%`);
      }
      add('circle', { cx: X(last.pct_tot), cy: Y(last.margem_apurada_pp), r: 4.5, fill: ink, stroke: cssVar('--panel'), 'stroke-width': 2 });
    }
    if (AN.projection.virada_em) {
      const v = AN.projection.virada_em;
      add('circle', { cx: X(v.pct_tot), cy: Y(0), r: 5, fill: ink, stroke: cssVar('--panel'), 'stroke-width': 2 });
      add('text', { x: X(v.pct_tot) - 6, y: Y(0) + 16, 'text-anchor': 'end', 'font-size': 11, 'font-weight': 700, fill: ink }, `virada ${hhmm(v.min)}`);
    }
  }
  // crosshair
  const ov = add('rect', { x: m.l, y: m.t, width: iw, height: ih, fill: 'transparent' });
  const cl = add('line', { y1: m.t, y2: m.t + ih, stroke: muted, 'stroke-width': 1, visibility: 'hidden' });
  const tip = el('div', 'ctip'); tip.hidden = true;
  ov.addEventListener('pointermove', (e) => {
    if (!hist.length) return;
    const r = svg.getBoundingClientRect(), px = ((e.clientX - r.left) / r.width) * w, pct = ((px - m.l) / iw) * 100;
    let best = hist[0]; for (const p of hist) if (Math.abs(p.pct_tot - pct) < Math.abs(best.pct_tot - pct)) best = p;
    cl.setAttribute('x1', X(best.pct_tot)); cl.setAttribute('x2', X(best.pct_tot)); cl.setAttribute('visibility', 'visible');
    tip.replaceChildren(el('div', null, `${hhmm(best.min)} · ${fPct(best.pct_tot)} totalizado`));
    const r1 = el('div'); r1.append(el('b', null, fPP(best.margem_apurada_pp)), ' placar oficial'); tip.append(r1);
    const r2 = el('div'); r2.append(el('b', null, `${fPP(best.margem_proj_pp)} ± ${nf1.format(best.faixa_pp)}`), ' projeção'); tip.append(r2);
    if (best.delta_obs != null) { const r3 = el('div'); r3.append(el('b', null, fM(best.delta_obs)), ` Δ margem vs 1T ${U100}`); tip.append(r3); }
    tip.hidden = false; tip.style.left = Math.min((X(best.pct_tot) / w) * r.width + 10, r.width - 190) + 'px'; tip.style.top = '6px';
  });
  ov.addEventListener('pointerleave', () => { cl.setAttribute('visibility', 'hidden'); tip.hidden = true; });
  box.replaceChildren(svg, tip);
}

function decompBlock(th) {
  const d = th.decomp, wrap = el('div');
  wrap.append(el('div', 'kicker', 'De onde veio a Δ margem (votos)'));
  const g = el('div', 'decomp'); g.style.marginTop = '.3rem';
  const mx = Math.max(Math.abs(d.comparecimento), Math.abs(d.preferencia), 1);
  for (const [lab, v] of [['Efeito comparecimento', d.comparecimento], ['Efeito preferência', d.preferencia]]) {
    const trk = el('div', 'trk'); trk.append(el('div', 'z'));
    const bar = el('i'); const wpc = Math.abs(v) / mx * 50;
    bar.style.left = (v >= 0 ? 50 : 50 - wpc) + '%'; bar.style.width = wpc + '%'; bar.style.background = v >= 0 ? 'var(--A)' : 'var(--B)'; trk.append(bar);
    g.append(el('span', null, lab), trk, el('span', 'val tab', fN(v, true)));
  }
  wrap.append(g);
  wrap.append(el('div', 'lg-note', `Comparecimento: mudança no nº de votantes × inclinação local. Preferência: mudança na margem por votante (inclui brancos/nulos). Soma exata = Δ margem.`));
  return wrap;
}
function rowsFor(f) {
  // tabela por região (Brasil) ou UFs da região ou UFs de uma UF selecionada
  if (f.type === 'region') return UFS_OF(f.id).map((u) => ({ key: u, name: u, th: AN.thermometer.uf[u.toLowerCase()].estimado, focus: { type: 'uf', id: u } }));
  if (f.type === 'uf') return UFS_OF(UFS[f.id][0]).map((u) => ({ key: u, name: u, th: AN.thermometer.uf[u.toLowerCase()].estimado, focus: { type: 'uf', id: u } }));
  return REGION_ORDER.map((r) => ({ key: r, name: REGIONS[r], th: AN.thermometer.regiao[r].estimado, focus: { type: 'region', id: r } }));
}
const UFS_OF = (r) => UF_LIST.filter((u) => UFS[u][0] === r);
function renderRight() {
  const R = $('#right'); R.replaceChildren();
  const f = S.focus, { m, name, kind } = focusMetrics(f);
  const th = thermo(m);
  const c = el('div', 'card');
  const head = el('div', 'focus-head'), hl = el('div');
  hl.append(el('div', 'kicker', 'Em foco · ' + kind), el('div', 'name', name));
  head.append(hl);
  if (f.type !== 'br') { const b = el('button', 'linkbtn', '× Brasil'); b.onclick = () => { userAct(); setFocus({ type: 'br' }, true); }; head.append(b); }
  c.append(head);
  c.append(el('div', 'sub', `Totalizado ${fPct(m.covTot * 100)} (oficial) · com boletim ${fPct(m.covBU * 100)} · com valor 2T ${fPct(m.cov * 100)} das seções (${fPct(m.obsShare * 100, 0)} observado, resto estimado pela zona)`));
  const mini = el('div', 'mini');
  mini.append(tile(`Δ margem (${U100})`, fM(th.d_margem_100), fN(th.d_margem_votos, true) + ' votos'), tile('Δ abstenção', fPP(th.d_abst_pp), th.d_abst_eleitores == null ? '—' : fN(th.d_abst_eleitores, true) + ' ausentes'),
    tile('Δ brancos+nulos', fPP(th.d_bn_pp), fN(th.d_bn_votos, true)), tile('Ritmo', th.ritmo_100 == null ? '—' : (th.ritmo_100 >= 0 ? '✓ ' : '✗ ') + fM(th.ritmo_100), `vs necessário (${U100})`));
  c.append(mini);
  if (f.type === 'place' || f.type === 'cell') {
    if (m.has) c.append(el('div', 'sub', `2º turno (observado + estimado pela zona): ${short('A')} ${fPct(m.shareA2)} × ${fPct(100 - m.shareA2)} ${short('B')}`));
  } else if (m.oM != null) {
    const pl = el('div', 'sub'); pl.append(`Placar oficial: ${short('A')} ${fPct(m.oShareA)} × ${fPct(100 - m.oShareA)} ${short('B')}`);
    if (m.covTot < 1 && m.projA != null) pl.append(` · projeção ${short('A')} ${fPct(m.projA)} (± ${nf1.format(m.bandPP / 2)})`);
    c.append(pl);
  }
  if (th.captura.l != null) c.append(captureBlock(th, true));
  if (m.has) { const d = decompBlock(th); d.style.marginTop = '.6rem'; c.append(d); }
  R.append(c);

  if (f.type === 'place') { R.append(placeCard(f.id)); return; }
  // tabela por região / UF
  const c2 = el('div', 'card');
  c2.append(el('h2', null, f.type === 'region' || f.type === 'uf' ? `UFs · ${REGIONS[f.type === 'region' ? f.id : UFS[f.id][0]]}` : 'Por região'));
  c2.append(el('div', 'sub', 'Mesmas unidades do 1º turno. ✓ = acima do ritmo necessário.'));
  const tbl = el('table', 'tbl'), thead = el('tr');
  for (const h of ['', 'Totaliz.', 'Com valor', 'Δ margem /100', 'Δ abst. p.p.', 'Ritmo /100']) thead.append(el('th', null, h));
  tbl.append(thead);
  for (const row of rowsFor(f)) {
    const t = row.th, tr = el('tr', 'click' + ((f.type === row.focus.type && f.id === row.focus.id) || (S.tv.on && S.tv.cur?.focus?.id === row.key) ? ' sel' : ''));
    const thin = t.cobertura_aptos < 0.005; // amostra pequena demais: não mostra número
    const sp = (v) => (v == null || thin ? '—' : fPP(v).replace(' p.p.', ''));
    const fm = (v) => (thin ? '—' : fM(v));
    tr.append(el('td', null, row.name), el('td', null, fPct(t.totalizado * 100, 0)), el('td', null, fPct(t.cobertura_aptos * 100, 0)), el('td', null, fm(t.d_margem_100)), el('td', null, sp(t.d_abst_pp)),
      el('td', null, t.ritmo_100 == null || thin ? 'aguardando' : (t.ritmo_100 >= 0 ? '✓ ' : '✗ ') + fM(t.ritmo_100)));
    tr.onclick = () => { userAct(); setFocus(row.focus, true); };
    tbl.append(tr);
  }
  c2.append(tbl); R.append(c2);
  R.append(rankCard());
}
const RANKS = {
  lula: { label: () => `${short('A')} mais cresceu`, val: (m) => m.dMarginV, fmt: (m) => `${fN(m.dMarginV, true)} · ${fM(m.dM100)}/100` },
  flavio: { label: () => `${short('B')} mais cresceu`, val: (m) => -m.dMarginV, fmt: (m) => `${fN(-m.dMarginV, true)} · ${fM(-m.dM100)}/100` },
  abstcaiu: { label: () => 'Abstenção caiu', val: (m) => -m.dAbstV, fmt: (m) => `${fN(m.dAbstV, true)} ausentes · ${fPP(m.dAbstPP)}` },
  abstsubiu: { label: () => 'Abstenção subiu', val: (m) => m.dAbstV, fmt: (m) => `${fN(m.dAbstV, true)} ausentes · ${fPP(m.dAbstPP)}` },
};
function rankList(key, by, n = 8) {
  const out = [], R = RANKS[key];
  if (by === 'muni') {
    for (let i = 0; i < W.munis.length; i++) {
      if (S.ufFilter && W.munis[i].uf !== S.ufFilter) continue;
      const m = metrics(SIM.muni, i); if (!m.has || m.nCnt < 20) continue;
      out.push({ v: R.val(m), m, name: `${W.munis[i].name} (${W.munis[i].uf})`, focus: { type: 'muni', id: i } });
    }
  } else {
    const C = H[CELLS_RES];
    for (let c = 0; c < C.data.length; c++) {
      if (S.ufFilter && C.data[c].uf !== W.ufIndex[S.ufFilter]) continue;
      const m = metrics(C.agg, c); if (!m.has || m.nCnt < 8) continue;
      const mu = W.munis[W.pMuni[C.placeCell.indexOf(c)]];
      out.push({ v: R.val(m), m, name: `H3 ${C.data[c].h.slice(0, 9)}… · ${mu?.name ?? ''}`, focus: { type: 'cell', res: CELLS_RES, h: C.data[c].h, idx: c } });
    }
  }
  out.sort((a, b) => b.v - a.v);
  return out.slice(0, n);
}
function rankCard() {
  const c = el('div', 'card');
  c.append(el('h2', null, 'Destaques'));
  const tabs = el('div', 'tabs');
  for (const k of Object.keys(RANKS)) { const b = el('button', S.rankTab === k ? 'on' : '', RANKS[k].label()); b.onclick = () => { userAct(); S.rankTab = k; renderRight(); }; tabs.append(b); }
  const by = el('div', 'tabs');
  for (const [k, l] of [['muni', 'por município'], ['cell', `por hexágono (res ${CELLS_RES})`]]) { const b = el('button', S.rankBy === k ? 'on' : '', l); b.onclick = () => { userAct(); S.rankBy = k; renderRight(); }; by.append(b); }
  c.append(tabs, by);
  const list = rankList(S.rankTab, S.rankBy);
  const tbl = el('table', 'tbl'); const h = el('tr'); h.append(el('th', null, ''), el('th', null, 'votos · /100 aptos'), el('th', null, 'com valor')); tbl.append(h);
  for (const r of list) {
    const tr = el('tr', 'click'); tr.append(el('td', null, r.name), el('td', null, RANKS[S.rankTab].fmt(r.m)), el('td', null, fPct(r.m.cov * 100, 0)));
    tr.onclick = () => { userAct(); setFocus(r.focus, true); }; tbl.append(tr);
  }
  if (!list.length) c.append(el('div', 'sub', 'Aguardando zonas suficientes.'));
  else c.append(tbl);
  return c;
}
function placeCard(p) {
  const c = el('div', 'card'), mu = W.munis[W.pMuni[p]];
  c.append(el('div', 'kicker', `${placeBairro(p)} · ${mu.name} (${mu.uf})`), el('h2', null, W.pName[p]));
  const go = el('button', 'linkbtn', '→ Ir para o local'); go.onclick = () => { userAct(); flyToFocus({ type: 'place', id: p }); }; c.append(go);
  const st = W.pSecStart[p], n = W.pSecCount[p];
  let aptos = 0, comp2 = 0; for (let k = 0; k < n; k++) { aptos += W.sAptos[st + k]; if (SIM.state[st + k] === 2) comp2 += T.comp2[st + k]; }
  const kp = el('div', 'kpis');
  kp.append(tile('Seções', `${n} urnas`, ''), tile('Zona eleitoral', String(W.pZona[p]), ''), tile('Eleitores aptos', nf0.format(aptos), ''), tile('Votos 2T (com BU)', nf0.format(comp2), ''));
  c.append(kp, el('div', 'sub', `Endereço: ${placeAddr(p)} · ${placeBairro(p)}, ${mu.name} – ${mu.uf}`));
  const wrap = el('div', 'sec-scroll'), tbl = el('table', 'tbl'), h = el('tr');
  for (const t of ['Seção', 'Status', 'Aptos', 'Comp. 2T', 'Abst. 1T→2T', `${short('A')} 1T→2T`, `${short('B')} 1T→2T`, 'Δ margem']) h.append(el('th', null, t));
  tbl.append(h);
  for (let k = 0; k < n; k++) {
    const s = st + k, state = SIM.state[s], tr = el('tr');
    const chip = el('span', 'schip' + (state === 2 ? ' bu' : state === 1 ? ' tot' : ''), state === 2 ? 'boletim recebido' : state === 1 ? 'apurada' : 'aguardando');
    const td = el('td'); td.append(chip);
    const ap = W.sAptos[s], c1 = W.sComp1[s], a1 = W.sA1[s], b1 = W.sB1[s], v1 = a1 + b1 + W.sO1[s];
    const ab1 = (1 - c1 / ap) * 100;
    tr.append(el('td', null, String(W.sNum[s]).padStart(4, '0')), td, el('td', null, nf0.format(ap)));
    if (state === 2) {
      const c2 = T.comp2[s], a2 = T.A2[s], b2 = T.B2[s];
      const dm = ((a2 - b2) / (a2 + b2) - (a1 - b1) / v1) * 100;
      tr.append(el('td', null, nf0.format(c2)), el('td', null, `${nf1.format(ab1)}→${nf1.format((1 - c2 / ap) * 100)}%`), el('td', null, `${a1}→${a2}`), el('td', null, `${b1}→${b2}`), el('td', null, fPP(dm)));
    } else if (SIM.zSwing[W.sZone[s] * 5]) {
      const q = W.sZone[s] * 5, sw = SIM.zSwing, bn1 = W.sBR1[s] + W.sNU1[s];
      const c2 = Math.min(ap, Math.max(0, c1 + ap * sw[q + 4]));
      let a2 = Math.max(0, a1 + ap * sw[q + 1]), b2 = Math.max(0, b1 + ap * sw[q + 2]); const n2 = Math.max(0, bn1 + ap * sw[q + 3]);
      const k2 = c2 / (a2 + b2 + n2 || 1); a2 = Math.round(a2 * k2); b2 = Math.round(b2 * k2);
      const dm = ((a2 - b2) / (a2 + b2) - (a1 - b1) / v1) * 100;
      const it = (t) => { const e = el('td', null, t); e.style.fontStyle = 'italic'; e.style.color = 'var(--muted)'; return e; };
      tr.append(it('~' + nf0.format(Math.round(c2))), it(`${nf1.format(ab1)}→~${nf1.format((1 - c2 / ap) * 100)}%`), it(`${a1}→~${a2}`), it(`${b1}→~${b2}`), it(fPP(dm)));
    } else {
      tr.append(el('td', null, '—'), el('td', null, `${nf1.format(ab1)}→…`), el('td', null, `${a1}→…`), el('td', null, `${b1}→…`), el('td', null, '—'));
    }
    tbl.append(tr);
  }
  wrap.append(tbl); c.append(wrap);
  c.append(el('div', 'lg-note', '“boletim recebido” = observado na urna. “apurada” = já totalizada no agregado oficial, boletim ainda não chegou. Valores em itálico com ~ = estimados pelo swing da zona sobre o 1º turno da seção.'));
  return c;
}

// ---------------- Barra superior ----------------
function renderTop() {
  const br = AN._br;
  $('#clock').textContent = `Dom 25/10 · ${hhmm(S.minute)} BRT · cenário: ${SCENARIOS[S.scenario].label}`;
  const pl = $('#placar'); pl.replaceChildren();
  const row = el('div', 'row');
  const a = el('span'); a.append(keyEl('A'), `${short('A')} ${fPct(br.oShareA)}`);
  const b = el('span'); b.append(`${fPct(br.oShareA == null ? null : 100 - br.oShareA)} ${short('B')}`, keyEl('B'));
  row.append(a, b);
  const duo = el('div', 'duo'), ia = el('i'), ib = el('i');
  ia.style.width = (br.oShareA ?? 50) + '%'; ia.style.background = 'var(--A)'; ib.style.flex = '1'; ib.style.background = 'var(--B)'; duo.append(ia, ib);
  pl.append(row, duo, el('div', 'lab', `placar oficial bruto · ${fN(br.oA2)} × ${fN(br.oB2)} votos · Sul/Sudeste apuram primeiro`));
  const pr = $('#progress'); pr.replaceChildren();
  const bar = (label, v, cls) => { const d = el('div', 'pbar'), t = el('div', 'track'), f = el('div', 'fill ' + (cls || '')); f.style.width = (v * 100) + '%'; t.append(f); d.append(el('span', null, label), t, el('b', 'tab', fPct(v * 100))); return d; };
  pr.append(bar('Seções totalizadas (oficial)', br.covTot), bar('Com boletim (urna a urna)', br.covBU, 'bu'), bar('Com valor no mapa (zonas)', br.cov, 'bu'));
  renderFresh(true);
}
function renderFresh(pulse = false) {
  const fr = $('#fresh'), age = Math.round((Date.now() - S.lastTickReal) / 1000);
  fr.className = 'fresh' + (age > 120 ? ' crit' : age > 60 ? ' warn' : '');
  fr.replaceChildren();
  const st = el('span', 'st'), d = el('span', 'dot' + (pulse ? ' pulse' : ''));
  st.append(d, age > 120 ? `Dados velhos · há ${age} s` : age > 60 ? `Atrasado · há ${age} s` : `Atualizado há ${age} s`);
  fr.append(st, el('span', null, `TSE ${hhmm(S.dataMinute)} · ${S.playing ? 'ao vivo (simulado)' : 'simulação pausada'}`));
}
function renderLegend() {
  const mode = MODE[S.mode], b = $('#legendBody'); b.replaceChildren();
  b.append(el('div', 'lg-title', mode.title()), el('div', 'lg-sub', mode.sub()));
  const bar = el('div', 'lg-bar'), R = RAMPS[mode.key];
  const sw = (c) => { const i = el('i'); i.style.background = rgbCss(c); bar.append(i); };
  const ticks = el('div', 'lg-ticks');
  if (mode.kind === 'div') {
    [...R.neg].reverse().forEach(sw); sw(R.mid); R.pos.forEach(sw);
    [...mode.th].reverse().forEach((t) => ticks.append(el('span', null, nf1.format(t).replace(',0', ''))));
    ticks.append(el('span', null, '0'));
    mode.th.forEach((t) => ticks.append(el('span', null, nf1.format(t).replace(',0', ''))));
  } else {
    R.seq.forEach(sw);
    mode.th.forEach((t) => ticks.append(el('span', null, nf1.format(t).replace(',0', ''))));
  }
  const ends = el('div', 'lg-ends'), [e0, e1] = mode.ends();
  ends.append(el('span', null, '◀ ' + e0), el('span', null, e1 + ' ▶'));
  b.append(bar, ticks, ends);
  const L = effLayer();
  b.append(el('div', 'lg-note', `${L === 'muni' || L === 'uf' ? 'Municípios: agregados oficiais por zona (atualizam em ~1 min). ' : 'Cor cheia = observado na urna (boletim); cor lavada = estimado pelo swing da zona. '}${S.fade && mode.src !== 'cov' ? 'Mais transparente = menor parte com valor. ' : ''}Sem cor = zona ainda sem apuração.`));
}

// ---------------- Modo TV ----------------
const TV_SECONDS = 24;
function buildTvViews() {
  const v = [];
  v.push({ key: 'br-ritmo', layer: 'muni', mode: 'ritmo', focus: { type: 'br' }, title: () => `Brasil · ${short('A')} está no ritmo?`,
    text: () => `${short('A')} precisa ganhar <b>${fM(AN.delta_req)}</b> voto de margem a cada 100 eleitores sobre o 1º turno; nas zonas já apuradas ganha <b>${fM(AN.delta_obs)}</b>.` });
  v.push({ key: 'br-margem', layer: 'muni', mode: 'margem', focus: { type: 'br' }, title: () => 'Brasil · placar oficial por município',
    text: () => `Com ${fPct(AN._br.covTot * 100, 0)} das seções: <b>${short('A')} ${fPct(AN._br.oShareA)}</b> × ${fPct(100 - (AN._br.oShareA ?? 50))} ${short('B')}. Sul e Sudeste apuram primeiro.` });
  // regiões com mais urnas novas desde a última passada
  const fresh = REGION_ORDER.map((r, i) => { const n = SIM.region[i * STRIDE + F.nTot]; const d = n - (S.tv.lastSeen[r] ?? 0); return { r, d, n }; }).filter((x) => x.n > 0).sort((a, b) => b.d - a.d).slice(0, 2);
  for (const { r } of fresh) {
    v.push({ key: 'reg-' + r, layer: 'muni', mode: 'dmargem', focus: { type: 'region', id: r }, title: () => `${REGIONS[r]} · Δ margem vs 1º turno`, onShow: () => { S.tv.lastSeen[r] = SIM.region[REGION_ORDER.indexOf(r) * STRIDE + F.nTot]; },
      text: () => { const t = AN.thermometer.regiao[r].estimado; if (t.cobertura_aptos < 0.005) return `${fPct(t.totalizado * 100, 0)} totalizado · ainda sem zonas suficientes para comparar com o 1º turno.`;
        return `${fPct(t.totalizado * 100, 0)} totalizado · nas mesmas seções do 1º turno, margem de ${short('A')} <b>${fM(t.d_margem_100)}</b> ${U100} (${t.ritmo_100 >= 0 ? 'acima' : 'abaixo'} do ritmo) · abstenção <b>${fPP(t.d_abst_pp)}</b>`; } });
  }
  const top = rankList('lula', 'muni', 1)[0];
  if (top) v.push({ key: 'lula-cresceu', layer: 'hex', mode: 'dmargem', focus: top.focus, title: () => `Onde ${short('A')} mais cresceu`,
    text: () => { const m = metrics(SIM.muni, top.focus.id); return `<b>${top.name}</b>: margem ${fN(m.dMarginV, true)} votos melhor que no 1º turno (${fM(m.dM100)} ${U100}), ${fPct(m.cov * 100, 0)} das seções com valor.`; } });
  const ab = rankList('abstcaiu', 'muni', 1)[0];
  if (ab) v.push({ key: 'abst', layer: 'hex', mode: 'dabst', focus: ab.focus, title: () => 'Maior queda de abstenção',
    text: () => { const m = metrics(SIM.muni, ab.focus.id); return `<b>${ab.name}</b>: ${fN(-m.dAbstV)} eleitores a mais votaram do que no 1º turno (abstenção ${fPP(m.dAbstPP)}).`; } });
  v.push({ key: 'br-ritmo-hex', layer: 'hex', mode: 'ritmo', focus: { type: 'br' }, title: () => 'Acima / abaixo do ritmo necessário · boletins',
    text: () => `Cada hexágono compara as mesmas seções nos dois turnos. ${fPct(AN._br.covBU * 100, 0)} das seções já têm boletim.` });
  if (AN._br.covTot < 1) v.push({ key: 'proj', layer: 'muni', mode: 'proj', focus: { type: 'br' }, title: () => 'O que falta apurar',
    text: () => { const p = AN.projection; return `Faltam ${fPct((1 - AN._br.covTot) * 100, 0)} das seções. Projeção: <b>${p.l_pct >= 50 ? short('A') : short('B')} ${fPct(Math.max(p.l_pct, 100 - p.l_pct))}</b> (margem ${fPP(p.margem_pp)} ± ${nf1.format(p.faixa_pp)}).`; } });
  return v;
}
function tvNext() {
  if (!S.tv.views.length || S.tv.idx >= S.tv.views.length - 1) { S.tv.views = buildTvViews(); S.tv.idx = -1; }
  S.tv.idx++;
  const v = S.tv.views[S.tv.idx];
  S.tv.cur = v; S.tv.viewStart = Date.now();
  v.onShow?.();
  S.mode = v.mode; S.layer = v.layer; $('#selMode').value = v.mode; syncSeg();
  S.focus = v.focus;
  flyToFocus(v.focus, 3000);
  recolor(); renderLegend(); renderRight(); renderTvCap(); renderBadge();
}
function renderTvCap() {
  const cap = $('#tvcap');
  if (!S.tv.on || !S.tv.cur) { cap.hidden = true; return; }
  cap.hidden = false; cap.replaceChildren();
  const paused = Date.now() < S.tv.pausedUntil;
  const k = el('div', 'k'); k.append(el('span', null, `Modo TV · ${S.tv.idx + 1}/${S.tv.views.length}`), el('span', null, paused ? `pausado · retoma em ${Math.ceil((S.tv.pausedUntil - Date.now()) / 1000)} s` : ''));
  const p = el('div', 'p'); p.innerHTML = S.tv.cur.text(); // texto montado só com números e nomes gerados aqui (sem dados externos)
  const tm = el('div', 'tm'), bar = el('i'); bar.id = 'tvbar'; tm.append(bar);
  cap.append(k, el('div', 'h', S.tv.cur.title()), p, tm);
}
function userAct() {
  if (!S.tv.on) return;
  S.tv.pausedUntil = Date.now() + 60000;
  $('#btnTv').classList.add('paused');
}
function tvLoop() {
  const btn = $('#btnTv');
  if (!S.tv.on) { btn.className = 'chip tv'; btn.textContent = '▶ Modo TV'; return; }
  const now = Date.now(), paused = now < S.tv.pausedUntil;
  btn.className = 'chip tv on' + (paused ? ' paused' : '');
  btn.textContent = paused ? `❚❚ TV · ${Math.ceil((S.tv.pausedUntil - now) / 1000)} s` : '▶ Modo TV';
  if (paused) { S.tv.viewStart = now - 1000 * TV_SECONDS * 0.0; const cap = $('#tvcap .k span:last-child'); if (cap) cap.textContent = `pausado · retoma em ${Math.ceil((S.tv.pausedUntil - now) / 1000)} s`; return; }
  const el_ = $('#tvbar');
  const frac = (now - S.tv.viewStart) / (TV_SECONDS * 1000);
  if (el_) el_.style.width = Math.min(100, frac * 100) + '%';
  if (frac >= 1 || !S.tv.cur) tvNext();
}

// ---------------- Controles ----------------
function syncSeg() { document.querySelectorAll('#segLayer button').forEach((b) => b.classList.toggle('on', b.dataset.v === S.layer)); }
function wireControls() {
  const sel = $('#selMode');
  for (const m of MODES) { const o = el('option', null, m.key === 'ganhoA' ? `Ganho de votos de ${short('A')}` : m.key === 'ganhoB' ? `Ganho de votos de ${short('B')}` : m.label); o.value = m.key; sel.append(o); }
  sel.value = S.mode;
  sel.onchange = () => { userAct(); S.mode = sel.value; recolor(); renderLegend(); };
  document.querySelectorAll('#segLayer button').forEach((b) => (b.onclick = () => { userAct(); S.layer = b.dataset.v; syncSeg(); updateLayers(); renderBadge(); renderLegend(); }));
  const uf = $('#selUf');
  for (const u of UF_LIST) { const o = el('option', null, `${u} · ${UFS[u][6]}`); o.value = u; uf.append(o); }
  uf.onchange = () => { userAct(); S.ufFilter = uf.value; recolor(); setFocus(uf.value ? { type: 'uf', id: uf.value } : { type: 'br' }, true); };
  $('#btnPlay').onclick = () => { S.playing = !S.playing; $('#btnPlay').textContent = S.playing ? '❚❚' : '▶'; renderFresh(); };
  $('#selSpeed').onchange = (e) => { S.speed = +e.target.value; };
  const scrub = $('#scrub');
  scrub.oninput = () => { $('#clock').textContent = `Dom 25/10 · ${hhmm(+scrub.value)} BRT`; };
  scrub.onchange = () => { userAct(); runTo(+scrub.value); refreshAll(); };
  $('#btnTv').onclick = () => {
    if (S.tv.on && Date.now() < S.tv.pausedUntil) { S.tv.pausedUntil = 0; tvNext(); return; }
    S.tv.on = !S.tv.on; S.tv.pausedUntil = 0;
    if (S.tv.on) tvNext(); else { S.tv.cur = null; renderTvCap(); }
    tvLoop();
  };
  // pausa o Modo TV em qualquer interação; retoma após 60 s ocioso
  let lastMove = null;
  window.addEventListener('pointerdown', (e) => { if (!e.target.closest('#btnTv')) userAct(); }, true);
  window.addEventListener('wheel', userAct, { passive: true, capture: true });
  window.addEventListener('keydown', userAct, true);
  window.addEventListener('pointermove', (e) => { if (lastMove && Math.hypot(e.clientX - lastMove[0], e.clientY - lastMove[1]) > 6) userAct(); lastMove = [e.clientX, e.clientY]; }, true);
  // busca
  const inp = $('#search'), res = $('#searchRes');
  const norm = (s) => s.normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();
  inp.oninput = () => {
    const q = norm(inp.value.trim()); res.replaceChildren();
    if (q.length < 2) { res.hidden = true; return; }
    const items = [];
    const zm = q.match(/\b(?:z|zona)\s*(\d+)/), sm = q.match(/\b(?:s|secao)\s*(\d+)/), um = q.match(/\b([a-z]{2})\b/);
    const ufq = um && UFS[um[1].toUpperCase()] ? um[1].toUpperCase() : null;
    if (zm) {
      const z = +zm[1];
      for (let p = 0; p < W.P && items.length < 8; p++) {
        if (W.pZona[p] !== z) continue;
        const mu = W.munis[W.pMuni[p]]; if (ufq && mu.uf !== ufq) continue;
        if (sm) {
          const sn = +sm[1];
          for (let k = 0; k < W.pSecCount[p]; k++) if (W.sNum[W.pSecStart[p] + k] === sn) items.push({ t: `Seção ${sn} · Zona ${z} · ${mu.uf}`, s: `${W.pName[p]} · ${mu.name}`, f: { type: 'place', id: p } });
        } else if (!items.some((it) => it.zkey === mu.uf + z)) items.push({ zkey: mu.uf + z, t: `Zona ${z} · ${mu.uf}`, s: `${mu.name} · ex.: ${W.pName[p]}`, f: { type: 'muni', id: W.pMuni[p] } });
      }
    }
    if (!zm) {
      for (const u of UF_LIST) if (norm(UFS[u][6]).startsWith(q) || norm(u) === q) items.push({ t: `${UFS[u][6]} (${u})`, s: 'Unidade da federação', f: { type: 'uf', id: u } });
      for (let i = 0; i < W.munis.length && items.length < 10; i++) if (norm(W.munis[i].name).startsWith(q)) items.push({ t: `${W.munis[i].name} (${W.munis[i].uf})`, s: W.munis[i].kind === 'municipio' ? 'Município (sintético)' : 'Município', f: { type: 'muni', id: i } });
    }
    if (!items.length) items.push({ t: 'Nada encontrado', s: 'Tente “Recife”, “zona 12 SP” ou “zona 12 seção 40 SP”', f: null });
    for (const it of items.slice(0, 10)) {
      const b = el('button'); b.append(el('div', null, it.t), el('small', null, it.s));
      b.onclick = () => { if (it.f) { userAct(); setFocus(it.f, true); } res.hidden = true; inp.value = ''; };
      res.append(b);
    }
    res.hidden = false;
  };
  inp.onblur = () => setTimeout(() => (res.hidden = true), 200);
  // ajustes
  const dlg = $('#dlgSettings');
  $('#btnSettings').onclick = () => {
    $('#cAname').value = S.cand.A.name; $('#cAnum').value = S.cand.A.num; $('#cAcol').value = S.cand.A.color;
    $('#cBname').value = S.cand.B.name; $('#cBnum').value = S.cand.B.num; $('#cBcol').value = S.cand.B.color;
    $('#selScenario').value = S.scenario; $('#chkFade').checked = S.fade; $('#selTheme').value = S.theme; $('#selResOff').value = String(S.resOff);
    dlg.showModal();
  };
  const scSel = $('#selScenario');
  for (const [k, v] of Object.entries(SCENARIOS)) { const o = el('option', null, v.label); o.value = k; scSel.append(o); }
  document.querySelectorAll('.presets button').forEach((b) => (b.onclick = () => { const p = PRESETS[b.dataset.p][S.theme]; $('#cAcol').value = p[0]; $('#cBcol').value = p[1]; }));
  dlg.addEventListener('close', () => {
    S.cand.A = { name: $('#cAname').value || 'Candidato A', num: $('#cAnum').value, color: $('#cAcol').value };
    S.cand.B = { name: $('#cBname').value || 'Candidato B', num: $('#cBnum').value, color: $('#cBcol').value };
    S.fade = $('#chkFade').checked; S.resOff = +$('#selResOff').value;
    const th = $('#selTheme').value;
    if (th !== S.theme) { S.theme = th; document.documentElement.dataset.theme = th; map.setStyle(STYLE[th]); }
    const sc = scSel.value;
    if (sc !== S.scenario) { S.scenario = sc; T = simulate2T(W, sc); SIM = new Sim(W, T); S.history = []; S.minute = 0; runTo(+$('#scrub').value); }
    saveLS(); buildRamps();
    [...$('#selMode').options].forEach((o) => { if (o.value === 'ganhoA') o.textContent = `Ganho de votos de ${short('A')}`; if (o.value === 'ganhoB') o.textContent = `Ganho de votos de ${short('B')}`; });
    CELLS_RES = resForZoom(map.getZoom()); aggregateCells();
    refreshAll();
  });
  window.addEventListener('resize', () => renderLeft());
}

function refreshAll(pulse = true) {
  aggregateCells(); recolor();
  renderTop(); renderLeft(); renderRight(); renderLegend(); renderTvCap(); renderBadge();
  $('#scrub').value = String(Math.round(S.minute));
  if (!pulse) renderFresh(false);
}

// ---------------- Boot ----------------
async function boot() {
  await new Promise((r) => setTimeout(r, 30));
  const t0 = performance.now();
  W = generateWorld(2026);
  T = simulate2T(W, S.scenario);
  SIM = new Sim(W, T);
  const t1 = performance.now();
  buildH3();
  const t2 = performance.now();
  buildMuniPolys();
  const t3 = performance.now();
  console.info(`[apuração] mundo ${W.P} locais / ${W.S} seções / ${W.munis.length} municípios em ${(t1 - t0).toFixed(0)} ms · H3 ${(t2 - t1).toFixed(0)} ms · Voronoi ${(t3 - t2).toFixed(0)} ms`);
  document.documentElement.dataset.theme = S.theme;
  buildRamps();
  wireControls();
  runTo(8); // começa com as primeiras urnas (17:08)
  initMap();
  CELLS_RES = resForZoom(map.getZoom());
  refreshAll();
  $('#loading').remove();
  map.once('idle', () => { if (S.tv.on) tvNext(); });
  // laço de simulação: um "snapshot" a cada 2 s reais
  setInterval(() => {
    if (S.playing && S.minute < NIGHT_END_MIN) {
      const t = performance.now();
      runTo(S.minute + 2 * S.speed);
      refreshAll();
      const dt = performance.now() - t;
      if (dt > 400) console.warn('[apuração] tick lento', dt.toFixed(0), 'ms');
    }
  }, 2000);
  setInterval(() => { renderFresh(); tvLoop(); }, 1000);
  window.__apur = { S, get W() { return W; }, get SIM() { return SIM; }, get AN() { return AN; }, map: () => map };
}
boot().catch((e) => { console.error(e); const l = $('#loading'); if (l) l.textContent = 'Erro ao iniciar: ' + e.message; });

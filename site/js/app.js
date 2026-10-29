// app.js — tela da sala de guerra (2º turno 2026). Renderiza manifest/agg/analytics; o único cálculo local é a
// agregação espacial do mapa (mapagg.js). Bibliotecas globais (UMD): maplibregl, deck, h3.
import {
  $, el, isNum, nf0, nf1, fPP, fM, fPct, fN, fInt, U100, fAge, fProb, hhmm, parseIso, parseBrt, loadPrefs, savePrefs, PRESETS,
  rgbCss, cssVar, applyCandColors, keyEl, safe, logErr, fimVotacao, fimTxt,
} from './fmt.js';
import { LiveFeed, liveBaseFromUrl } from './live.js';
import { freshness, renderBadges, renderBanners } from './health.js';
import { loadMeta, loadBase, loadH3, loadPlaceMeta } from './base.js';
import * as MA from './mapagg.js';
import { PAISES, CIDADES } from './exterior.js';
import { loadProrrog, prorrog, lateShare } from './prorrog.js';
import { openUrnaLog } from './urnalog.js';
import { curveChart, reguaBars } from './prcharts.js';
import { buildStory, loadHistoria, historiaData, CHAPTERS } from './historia.js';

const { F, K, MODES, MODE } = MA;
const HEAVY_MIN_MS = 30000;        // recomputação/recoloração por dado novo: no máximo a cada 30 s
const RELOAD_MS = 45 * 60 * 1000;  // recarga completa periódica (restaura o Modo TV pela hash)
const TV_SECONDS = 24;
const REG_ORDER = ['N', 'NE', 'CO', 'SE', 'S', 'ZZ'];

// ---------------- estado ----------------
const PREF = loadPrefs();
const S = {
  cand: PREF.cand, theme: PREF.theme, fade: PREF.fade, resOff: PREF.resOff,
  mode: 'ritmo', layer: 'auto', ufFilter: '', focus: { type: 'br' }, rankTab: 'lula', rankBy: 'muni', extIso: '', logSec: -1,
  tv: { on: window.innerWidth >= 1100, idx: -1, viewStart: 0, pausedUntil: 0, views: [], cur: null, lastSeen: {} },
  bootAt: Date.now(), lastUserAt: 0,
};
const short = (k) => S.cand[k].short || S.cand[k].name.split(' ')[0];
let B, FEED, ZA, PLACE, RAMPS, map, overlay;
const CELL = {};                 // res → Float64Array
let CELLS_RES = 4, COLORS = { muni: null, uf: null, cell: null, place: null }, colorVersion = 0;
let MUNV = null;                 // cache por município do último recolor (tooltip/rankings)
let lastHeavyAt = 0, heavyTimer = null, mapDirty = { zones: true, places: true };
let labelData = [];
const TMP3 = new Float64Array(3);
let PLACES_DATA = null;          // objeto estável: um novo {length} a cada atualização faria o deck recalcular tudo
let EXT = null;                  // exterior: {list (maior → menor), byMun, bounds}; ver initExterior()
window.__tela = { S, get B() { return B; }, get FEED() { return FEED; }, get PLACE() { return PLACE; }, get CELL() { return CELL; }, map: () => map, reloadNow: () => scheduleReload(0), bootAt: Date.now() };

const an = () => FEED?.an || null;
const agg = () => FEED?.agg || null;
const phase = () => an()?.status?.phase || 'none';
const reqDelta = () => (isNum(an()?.pace?.delta_req) ? an().pace.delta_req : B?.meta?.baseline_1t?.delta_req);
const comparing = () => ['early', 'trend', 'final'].includes(phase());

// ---------------- texto ----------------
const LOWER = new Set(['de', 'da', 'do', 'das', 'dos', 'e', 'd']);
function titleCase(s) {
  if (!s) return '';
  return String(s).toLowerCase().split(/(\s+|-)/).map((w, i) => (LOWER.has(w) && i > 0 ? w : w.charAt(0).toUpperCase() + w.slice(1))).join('').replace(/D'(\w)/g, (m, c) => "D'" + c.toUpperCase());
}
const munName = (m) => { const e = EXT?.byMun.get(m); return e ? `${e.nome} (${PAISES[e.iso]})` : `${titleCase(B.mun.nome[m])} (${String(B.mun.uf[m]).toUpperCase()})`; };
const isExt = (m) => B.mun.uf[m] === 'zz';
/** foco no exterior: a região ZZ inteira ou uma cidade de lá */
const extFocus = () => (S.focus.type === 'region' && S.focus.id === 'ZZ') || (S.focus.type === 'muni' && isExt(S.focus.id));
const ufName = (u) => B.meta.uf_names[u] || u.toUpperCase();
const regName = (r) => B.meta.regions[r] || r;
/** fragmento com partes em negrito: rich(['texto ', {b:'forte'}, '.']) — sem innerHTML */
function rich(parts) { const f = document.createDocumentFragment(); for (const p of parts) f.append(typeof p === 'string' ? p : el('b', null, p.b)); return f; }

// ---------------- cores ----------------
function buildRamps() { RAMPS = MA.buildRamps(S.cand, S.theme); applyCandColors(S.cand); }

// ---------------- dados do mapa ----------------
function base1Mun(m) { const M = B.mun; return { a1: M.a1[m], c1: M.c1[m], l1: M.l1[m], o1: M.o1[m], bn1: M.bn1[m], x1: M.x1[m], a2: M.a2[m] }; }
function munZ(m) { return an()?.mun?.[String(B.mun.cd_mun[m])] || null; }
function munAgg(m) { return agg()?.mun?.[String(B.mun.cd_mun[m])] || null; }
function ufT(u) { return an()?.thermometer?.uf?.[u] || null; }
function ufRemProj(u) {
  const pr = an()?.projection?.uf?.[u], A = agg()?.uf?.[u];
  if (!pr || !isNum(pr.l) || !isNum(pr.o)) return null;
  const rl = pr.l - (A?.l || 0), ro = pr.o - (A?.o || 0);
  return rl + ro > 0 ? { m: 100 * (rl - ro) / (rl + ro), rem: rl + ro } : null;
}

// ---------------- exterior ----------------
// Cada cidade com seção no exterior vira um círculo no mapa-múndi (exterior.js dá país e coordenada).
function initExterior() {
  const list = [], miss = [];
  for (let m = 0; m < B.M; m++) {
    if (!isExt(m)) continue;
    const g = CIDADES[B.mun.nome[m]];
    if (!g) { miss.push(B.mun.nome[m]); continue; }
    list.push({ i: m, p: [g[2], g[1]], iso: g[0], nome: g[3] || titleCase(B.mun.nome[m]) });
  }
  if (miss.length) logErr(`exterior sem coordenada: ${miss.join(', ')}`);
  list.sort((a, b) => B.mun.a2[b.i] - B.mun.a2[a.i]); // maiores desenhados primeiro, menores por cima
  const ext = (k, f) => list.reduce((v, d) => f(v, d.p[k]), list.length ? list[0].p[k] : 0);
  EXT = { list, byMun: new Map(list.map((d) => [d.i, d])), bounds: list.length ? [[ext(0, Math.min), ext(1, Math.min)], [ext(0, Math.max), ext(1, Math.max)]] : null };
}
/** comparação com o 1º turno de uma cidade do exterior: exata, pelo agregado oficial (só com a cidade toda apurada) */
function extCmp(m) { return MA.cmpExact(base1Mun(m), munAgg(m)); }
/** raio (px, antes do fator de zoom): área proporcional aos votos válidos; sem apuração, pelo eleitorado */
function extRadius(m) {
  const off = MA.official(munAgg(m)), v = off.has ? off.l + off.o : 0.4 * B.mun.a2[m];
  return Math.max(2.5, 0.8 * Math.sqrt(v / 100));
}
const extZoomK = (z) => Math.max(1, Math.min(1.8, 2 ** ((z - 2) * 0.45)));
/** países do exterior, do maior para o menor em votos válidos */
function extCountries() {
  const by = new Map();
  for (const d of EXT?.list || []) {
    let r = by.get(d.iso);
    if (!r) by.set(d.iso, (r = { iso: d.iso, nome: PAISES[d.iso], cities: [], l: 0, o: 0, a2: 0, st: 0, ts: 0, dmv: 0, a2c: 0 }));
    const A = munAgg(d.i), c = extCmp(d.i);
    r.cities.push(d); r.l += A?.l || 0; r.o += A?.o || 0; r.st += A?.st || 0; r.ts += A?.ts || 0; r.a2 += B.mun.a2[d.i];
    if (c.has) { r.dmv += c.dMarginV; r.a2c += B.mun.a2[d.i]; }
  }
  return [...by.values()].map((r) => ({ ...r, v: r.l + r.o, lshare: r.l + r.o > 0 ? 100 * r.l / (r.l + r.o) : null, pct: r.ts ? 100 * r.st / r.ts : 0,
    delta: r.a2c > 0 ? 100 * r.dmv / r.a2c : null })).sort((a, b) => b.v - a.v || b.a2 - a.a2);
}
function flyToCountry(iso, duration = 2200) {
  const pts = (EXT?.list || []).filter((d) => d.iso === iso).map((d) => d.p);
  if (!map || !pts.length) return;
  if (pts.length === 1) { map.flyTo({ center: pts[0], zoom: 5, duration }); return; }
  const bb = unionBbox(pts.map((p) => [p[0], p[1], p[0], p[1]]));
  map.fitBounds(bb, { padding: { top: 140, bottom: 80, left: 80, right: 80 }, duration, maxZoom: 5.5 });
}

/** recomputa valores efetivos por local e por célula; recolore. data=true → respeita o intervalo mínimo */
function heavyUpdate(dataDriven) {
  const now = Date.now();
  if (dataDriven && now - lastHeavyAt < HEAVY_MIN_MS) {
    if (!heavyTimer) heavyTimer = setTimeout(() => { heavyTimer = null; heavyUpdate(false); }, HEAVY_MIN_MS - (now - lastHeavyAt));
    return;
  }
  if (heavyTimer) { clearTimeout(heavyTimer); heavyTimer = null; }
  lastHeavyAt = now;
  const t0 = performance.now();
  safe('zonas', () => { if (mapDirty.zones) { MA.updateZones(B, ZA, an()?.zones); mapDirty.zones = false; } });
  safe('locais', () => { MA.computePlaces(B, FEED?.sec, ZA, PLACE, prorrog()?.S || null); mapDirty.places = false; });
  safe('células', () => { if (CELL[CELLS_RES] && B.h3[CELLS_RES]) MA.aggregateCells(B, CELLS_RES, PLACE, CELL[CELLS_RES]); });
  const t1 = performance.now();
  recolor();
  const t2 = performance.now();
  if (t2 - t0 > 250) console.warn(`[tela] atualização do mapa lenta: efetivo ${(t1 - t0).toFixed(0)} ms · cores ${(t2 - t1).toFixed(0)} ms`);
}

async function ensureRes(res) {
  if (!B.h3[res]) await loadH3(B, res);
  if (!CELL[res]) { CELL[res] = new Float64Array(B.h3[res].n * K); MA.aggregateCells(B, res, PLACE, CELL[res]); }
  if (!COLORS['cell' + res]) COLORS['cell' + res] = new Uint8Array(B.h3[res].n * 4);
}

function dimFor(ufIdx) { return S.ufFilter && ufIdx !== B.ufIdx[S.ufFilter] ? 0.22 : 1; }

/** dados dos logs de urna: carrega na primeira vez que um modo/vista precisa deles e redesenha */
let prPromise = null;
function ensurePr() {
  if (prorrog()) return Promise.resolve(prorrog());
  prPromise ||= loadProrrog(B).then((P) => { mapDirty.places = true; heavyUpdate(false); safe('direita', renderRight); safe('legenda', renderLegend); return P; })
    .catch((e) => { prPromise = null; logErr('prorrogação', e); throw e; });
  return prPromise;
}
function munValue(mode, m) {
  if (mode.src === 'pr') { const P = prorrog(); return { v: P && !isExt(m) ? lateShare(P.mun, m, mode.key === 'tardio1' ? 1 : 2) : null, cov: 1 }; } // exterior: hora local, fica sem cor
  if (mode.src === 'base') {
    const M = B.mun, v = M.l1[m] + M.o1[m] + M.x1[m];
    return { v: v > 0 ? 100 * (M.l1[m] - M.o1[m]) / v : null, cov: 1 };
  }
  const A = munAgg(m), off = MA.official(A);
  if (mode.src === 'cov') return { v: off.pct > 0 ? off.pct : null, cov: 1 }; // 0% = sem cor, distinto de 'pouco apurado'
  if (mode.src === 'proj') { const u = B.mun.uf[m], rp = ufRemProj(u); return { v: rp ? rp.m : null, cov: A ? 1 - off.pct / 100 : 1 }; }
  if (mode.src === 'off') return { v: off.has ? off.m : null, cov: off.pct / 100 };
  const c = isExt(m) ? extCmp(m) : MA.cmpFromZ(base1Mun(m), munZ(m));
  return { v: MA.modeValue(mode, c, null, reqDelta()), cov: c.cov, c };
}
function ufValue(mode, u) {
  if (mode.src === 'pr') { const P = prorrog(); return { v: P && u !== 'zz' ? lateShare(P.uf, B.ufIdx[u], mode.key === 'tardio1' ? 1 : 2) : null, cov: 1 }; }
  const A = agg()?.uf?.[u], off = MA.official(A);
  if (mode.src === 'base') return { v: null, cov: 1 }; // recolor() pinta a UF no 1T pela soma dos municípios
  if (mode.src === 'cov') return { v: off.pct > 0 ? off.pct : null, cov: 1 }; // 0% = sem cor, distinto de 'pouco apurado'
  if (mode.src === 'proj') { const rp = ufRemProj(u); return { v: rp ? rp.m : null, cov: A ? 1 - off.pct / 100 : 1 }; }
  if (mode.src === 'off') return { v: off.has ? off.m : null, cov: off.pct / 100 };
  const c = MA.cmpFromT(ufT(u));
  return { v: MA.modeValue(mode, c, null, reqDelta()), cov: c.cov };
}

function recolor() {
  if (!B || !RAMPS) return;
  const mode = MODE[S.mode];
  if (mode.src === 'pr') ensurePr().catch(() => {});
  const M = B.M;
  if (!COLORS.muni) COLORS.muni = new Uint8Array(M * 4);
  MUNV = MUNV || new Array(M);
  for (let m = 0; m < M; m++) {
    const r = safe('muni', () => munValue(mode, m)) || { v: null, cov: 0 };
    MUNV[m] = r;
    MA.writeColor(COLORS.muni, m, RAMPS, mode, r.v, { level: 'muni', cov: r.cov, dim: dimFor(B.munUf[m]), fade: S.fade });
  }
  const U = B.ufOrder.length;
  if (!COLORS.uf) COLORS.uf = new Uint8Array(U * 4);
  for (let i = 0; i < U; i++) {
    const r = safe('uf', () => (mode.src === 'base' ? null : ufValue(mode, B.ufOrder[i]))) || { v: null, cov: 0 };
    MA.writeColor(COLORS.uf, i, RAMPS, mode, r.v, { level: 'uf', cov: r.cov, dim: dimFor(i), fade: S.fade });
  }
  if (mode.src === 'base') { // UF no 1T: usa a soma dos municípios (base)
    const acc = new Float64Array(U * 3);
    for (let m = 0; m < M; m++) { const u = B.munUf[m]; acc[u * 3] += B.mun.l1[m]; acc[u * 3 + 1] += B.mun.o1[m]; acc[u * 3 + 2] += B.mun.x1[m]; }
    for (let i = 0; i < U; i++) { const v = acc[i * 3] + acc[i * 3 + 1] + acc[i * 3 + 2]; MA.writeColor(COLORS.uf, i, RAMPS, mode, v > 0 ? 100 * (acc[i * 3] - acc[i * 3 + 1]) / v : null, { level: 'uf', dim: dimFor(i), fade: false }); }
  }
  const H = B.h3[CELLS_RES], CA = CELL[CELLS_RES], CC = COLORS['cell' + CELLS_RES];
  const tmp = TMP3, req = reqDelta(), key = mode.key, opt = { level: 'cell', cov: 1, obs: null, dim: 1, fade: S.fade };
  const cellOk = mode.src !== 'proj' && mode.src !== 'base';
  if (H && CA && CC) {
    for (let c = 0; c < H.n; c++) {
      if (cellOk) MA.aggModeValue(key, CA, c * K, req, tmp); else tmp[0] = NaN;
      opt.cov = tmp[1]; opt.obs = tmp[2]; opt.dim = dimFor(H.uf[c]);
      MA.writeColor(CC, c, RAMPS, mode, tmp[0], opt);
    }
  }
  if (!COLORS.place) COLORS.place = new Uint8Array(B.P * 4);
  const LU = B.L.uf;
  opt.level = 'place';
  for (let p = 0; p < B.P; p++) {
    if (cellOk) MA.aggModeValue(key, PLACE, p * K, req, tmp); else tmp[0] = NaN;
    if (!isFinite(tmp[0])) { const o = p * 4, dim = dimFor(LU[p]); COLORS.place[o] = 225; COLORS.place[o + 1] = 232; COLORS.place[o + 2] = 245; COLORS.place[o + 3] = Math.round(70 * dim); continue; }
    opt.cov = tmp[1]; opt.obs = tmp[2]; opt.dim = dimFor(LU[p]);
    MA.writeColor(COLORS.place, p, RAMPS, mode, tmp[0], opt);
  }
  colorVersion++;
  updateLayers();
}

// ---------------- mapa ----------------
const STYLE = { dark: 'https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json', light: 'https://basemaps.cartocdn.com/gl/positron-gl-style/style.json' };
const BLANK = (dark) => ({ version: 8, sources: {}, layers: [{ id: 'bg', type: 'background', paint: { 'background-color': dark ? '#0b0c10' : '#f2f2ef' } }] });
const INTERLEAVED = new URLSearchParams(location.search).has('interleaved');
function initMap(view) {
  const bb = B.meta.br_bounds;
  let styleOk = false;
  map = new maplibregl.Map({
    container: 'map', style: STYLE[S.theme], attributionControl: { compact: true }, dragRotate: false, pitchWithRotate: false,
    ...(view ? { center: view.c, zoom: view.z } : { bounds: bb, fitBoundsOptions: { padding: 30 } }),
  });
  overlay = new deck.MapboxOverlay({ interleaved: INTERLEAVED, layers: [], getCursor: ({ isHovering }) => (isHovering ? 'pointer' : 'grab') });
  map.addControl(overlay);
  map.on('style.load', () => {
    styleOk = true;
    // nomes de cidades/UFs ficariam duplicados (os do mapa base ficam sob o preenchimento; os nossos, por cima)
    safe('rótulos base', () => { for (const l of map.getStyle().layers || []) if (l.type === 'symbol' && /^place_(villages|town|state|city|capital)/.test(l.id)) map.setLayoutProperty(l.id, 'visibility', 'none'); });
    updateLayers();
  });
  map.on('error', (e) => { if (!styleOk) console.warn('[tela] mapa base:', e?.error?.message || e); });
  setTimeout(() => { if (!styleOk) { console.warn('[tela] estilo do mapa base não carregou; usando fundo liso'); map.setStyle(BLANK(S.theme === 'dark')); } }, 12000);
  map.on('zoomend', () => safe('zoom', onZoom));
  map.on('moveend', () => { safe('badge', renderBadge); updateLayers(); writeHash(); }); // updateLayers: rótulos da nova vista
  map.getCanvas().addEventListener('webglcontextlost', () => { console.error('[tela] contexto WebGL perdido; recarregando'); scheduleReload(4000); });
}
// Até res 6 o hexágono fica com ~15–20 px na tela; dali em diante a resolução sobe mais rápido que o zoom,
// para o hexágono encolher ao aproximar (~8 px na entrada da res 9, a mais fina da base, no zoom 10,6).
function resForZoom(z) { const t = [4.5, 5.9, 7.3, 8.3, 9.3, 10.6]; let r = 3; for (const x of t) if (z >= x) r++; return Math.max(3, Math.min(9, r + S.resOff)); }
function effLayer() {
  const z = map ? map.getZoom() : 4;
  let L = S.layer === 'auto' ? (z < 7.6 ? 'muni' : z < 11.8 ? 'hex' : 'locais') : S.layer;
  if ((MODE[S.mode].src === 'proj' || MODE[S.mode].src === 'base') && (L === 'hex' || L === 'locais')) L = 'muni';
  return L;
}
async function onZoom() {
  const r = resForZoom(map.getZoom());
  if (r !== CELLS_RES) {
    CELLS_RES = r;
    try { await ensureRes(r); } catch (e) { console.error('[tela] H3', e); }
    if (CELLS_RES === r) { MA.aggregateCells(B, r, PLACE, CELL[r]); recolor(); }
  } else updateLayers();
  renderBadge(); renderLegend();
}
const pick4 = (arr, i) => [arr[i * 4], arr[i * 4 + 1], arr[i * 4 + 2], arr[i * 4 + 3]];
/** Rótulos de cidades sem colisão: candidatos por zoom, ordem gulosa por eleitorado (capitais com peso),
 *  descarta o rótulo cuja caixa na tela cruza uma já colocada. Recalculado por vista (zoom/centro). */
let labelKey = '', labelCand = null;
function labelsFor(z) {
  if (!map) return labelData;
  const ext = extFocus() && !!EXT?.list.length;
  const c = map.getCenter(), key = `${z.toFixed(2)}|${c.lng.toFixed(3)}|${c.lat.toFixed(3)}|${map.getContainer().clientWidth}x${map.getContainer().clientHeight}${ext ? '|ext' : ''}`;
  if (key === labelKey) return labelData;
  labelKey = key;
  if (!labelCand) { // todos os municípios com coordenada (exceto exterior), em ordem de prioridade
    labelCand = [];
    for (let m = 0; m < B.M; m++) {
      if (!isNum(B.mun.lng[m]) || B.mun.uf[m] === 'zz') continue;
      labelCand.push({ m, t: titleCase(B.mun.nome[m]), p: [B.mun.lng[m], B.mun.lat[m]], cap: !!B.mun.capital[m], a2: B.mun.a2[m], w: B.mun.a2[m] * (B.mun.capital[m] ? 3 : 1) });
    }
    labelCand.sort((a, b) => b.w - a.w);
  }
  // exterior: só as cidades de lá, com o rótulo acima do círculo
  const cands = ext ? EXT.list.map((d) => ({ m: d.i, t: d.nome, p: d.p, cap: false, a2: B.mun.a2[d.i] })) : labelCand;
  const minA2 = ext ? (z < 2.3 ? 14000 : z < 3.3 ? 5000 : z < 4.5 ? 1500 : 0) : z < 5.2 ? Infinity : z < 6.8 ? 250000 : z < 8.5 ? 60000 : z < 10 ? 12000 : 0;
  const zk = extZoomK(z);
  const W = map.getContainer().clientWidth, H = map.getContainer().clientHeight, M = 40;
  const boxes = [], out = [];
  for (const d of cands) {
    if (!d.cap && d.a2 < minA2) continue;
    const q = map.project(d.p);
    if (q.x < -M || q.y < -M || q.x > W + M || q.y > H + M) continue;
    if (ext) d.dy = -(extRadius(d.m) * zk + 8);
    const size = d.cap ? 13 : 11.5, hw = (d.t.length * size * 0.3) + 4, hh = size * 0.6 + 3, cy = q.y + (d.dy || 0);
    const x0 = q.x - hw, x1 = q.x + hw, y0 = cy - hh, y1 = cy + hh;
    let hit = false;
    for (let i = 0; i < boxes.length; i += 4) if (x0 < boxes[i + 2] && x1 > boxes[i] && y0 < boxes[i + 3] && y1 > boxes[i + 1]) { hit = true; break; }
    if (hit) continue;
    boxes.push(x0, y0, x1, y1); out.push(d);
    if (out.length >= 160) break;
  }
  labelData = out;
  return out;
}
function updateLayers() {
  if (!overlay || !COLORS.muni || !map) return;
  safe('camadas', () => {
    const L = effLayer(), dark = S.theme === 'dark', z = map.getZoom();
    const beforeId = undefined;
    const TR = { getFillColor: { duration: 700 } };
    const layers = [];
    if (L === 'muni' || L === 'uf') {
      if (L === 'uf') {
        layers.push(new deck.PolygonLayer({ id: 'uf', data: B.ufPolys, getPolygon: (d) => d.p, beforeId, pickable: true, stroked: false, filled: true,
          getFillColor: (d) => pick4(COLORS.uf, d.i), updateTriggers: { getFillColor: [colorVersion] }, transitions: TR }));
      } else {
        layers.push(new deck.PolygonLayer({ id: 'muni', data: B.munPolys, getPolygon: (d) => d.p, beforeId, pickable: true, stroked: true, filled: true,
          getFillColor: (d) => pick4(COLORS.muni, d.i), getLineColor: dark ? [255, 255, 255, 30] : [0, 0, 0, 28], lineWidthUnits: 'pixels', getLineWidth: 0.5,
          updateTriggers: { getFillColor: [colorVersion] }, transitions: TR }));
      }
    } else {
      const H = B.h3[CELLS_RES], CC = COLORS['cell' + CELLS_RES];
      if (H && CC) {
        const k = L === 'locais' ? 0.45 : 1;
        layers.push(new deck.H3HexagonLayer({ id: 'hex', data: H.data, getHexagon: (d) => d.h, beforeId, pickable: L === 'hex', filled: true, stroked: false, extruded: false,
          coverage: 0.9, highPrecision: true, /* o modo instanciado ('auto') não desenha no deck 9.0.38 sobreposto ao MapLibre */ getFillColor: (d) => { const c = pick4(CC, d.i); c[3] = Math.round(c[3] * k); return c; },
          updateTriggers: { getFillColor: [colorVersion, CELLS_RES, L] }, transitions: TR }));
      }
      if (L === 'locais' || z >= 11.2) { // antes disso os pontos cobririam os hexágonos de res 9
        layers.push(new deck.ScatterplotLayer({ id: 'places', data: PLACES_DATA, beforeId, pickable: true, radiusUnits: 'meters',
          getPosition: (_, { index }) => [B.L.lng[index], B.L.lat[index]],
          getRadius: (_, { index }) => 22 * Math.sqrt(B.placeAptos[index] / 100), radiusMinPixels: 2, radiusMaxPixels: 13,
          stroked: true, lineWidthUnits: 'pixels', getLineWidth: 1, getLineColor: dark ? [255, 255, 255, 150] : [20, 20, 20, 120],
          getFillColor: (_, { index }) => pick4(COLORS.place, index), updateTriggers: { getFillColor: colorVersion }, transitions: TR }));
      }
    }
    layers.push(new deck.PathLayer({ id: 'uf-lines', data: B.ufLines, getPath: (d) => d.path, getColor: dark ? [255, 255, 255, 70] : [0, 0, 0, 60], widthUnits: 'pixels', getWidth: 1, pickable: false }));
    const zk = extZoomK(z);
    if (EXT?.list.length) { // exterior: um círculo por cidade, cor do modo atual (mesmas cores dos municípios)
      layers.push(new deck.ScatterplotLayer({ id: 'ext', data: EXT.list, pickable: true, radiusUnits: 'pixels',
        getPosition: (d) => d.p, getRadius: (d) => extRadius(d.i) * zk,
        stroked: true, lineWidthUnits: 'pixels', getLineWidth: 1, getLineColor: dark ? [255, 255, 255, 140] : [20, 20, 20, 120],
        getFillColor: (d) => { const c = pick4(COLORS.muni, d.i); c[3] = Math.min(255, Math.round(c[3] * 1.2)); return c; },
        updateTriggers: { getFillColor: [colorVersion], getRadius: [colorVersion, zk.toFixed(2)] }, transitions: TR }));
    }
    const f = S.focus, selC = dark ? [255, 255, 255, 235] : [0, 0, 0, 220];
    if (f.type === 'muni' && EXT?.byMun.has(f.id)) {
      const d = EXT.byMun.get(f.id);
      layers.push(new deck.ScatterplotLayer({ id: 'sel-ext', data: [d], getPosition: (x) => x.p, radiusUnits: 'pixels', getRadius: extRadius(d.i) * zk + 4,
        filled: false, stroked: true, lineWidthUnits: 'pixels', getLineWidth: 2.5, getLineColor: selC }));
    } else if (f.type === 'muni') {
      const parts = B.munPolys.filter((d) => d.i === f.id);
      if (parts.length) layers.push(new deck.PolygonLayer({ id: 'sel-muni', data: parts, getPolygon: (d) => d.p, filled: false, stroked: true, getLineColor: selC, lineWidthUnits: 'pixels', getLineWidth: 2.5 }));
    } else if (f.type === 'uf') {
      const i = B.ufIdx[f.id], parts = B.ufPolys.filter((d) => d.i === i);
      if (parts.length) layers.push(new deck.PolygonLayer({ id: 'sel-uf', data: parts, getPolygon: (d) => d.p, filled: false, stroked: true, getLineColor: selC, lineWidthUnits: 'pixels', getLineWidth: 2 }));
    } else if (f.type === 'cell') {
      layers.push(new deck.H3HexagonLayer({ id: 'sel-cell', data: [{ h: f.h }], getHexagon: (d) => d.h, highPrecision: true, filled: false, stroked: true, extruded: false, getLineColor: selC, lineWidthUnits: 'pixels', getLineWidth: 2.5 }));
    } else if (f.type === 'place') {
      layers.push(new deck.ScatterplotLayer({ id: 'sel-place', data: [f.id], getPosition: (p) => [B.L.lng[p], B.L.lat[p]], radiusUnits: 'pixels', getRadius: 14, filled: false, stroked: true, lineWidthUnits: 'pixels', getLineWidth: 2.5, getLineColor: selC }));
    }
    const lab = labelsFor(z);
    if (lab.length) {
      layers.push(new deck.TextLayer({ id: 'labels', data: lab, getText: (d) => d.t, getPosition: (d) => d.p, characterSet: 'auto', sizeUnits: 'pixels',
        getSize: (d) => (d.cap ? 13 : 11.5), getColor: dark ? [240, 240, 236, 235] : [20, 20, 20, 230], fontFamily: 'system-ui, -apple-system, Segoe UI, Roboto, sans-serif',
        fontWeight: 600, fontSettings: { sdf: true }, outlineWidth: 3, outlineColor: dark ? [11, 12, 16, 230] : [252, 252, 251, 230], pickable: false,
        getTextAnchor: 'middle', getAlignmentBaseline: 'center', getPixelOffset: (d) => [0, d.dy || 0], updateTriggers: { getColor: S.theme } }));
    }
    overlay.setProps({ layers, onHover: (i) => safe('hover', () => onHover(i)), onClick: (i) => safe('click', () => onClick(i)) });
  });
}
function renderBadge() {
  if (!map) return;
  const L = effLayer(), names = { muni: 'Municípios (agregado oficial + swing do analytics)', uf: 'UFs (analytics / agregado oficial)', hex: `Hexágonos H3 · res ${CELLS_RES}`, locais: `Locais de votação + H3 res ${CELLS_RES}` };
  const edge = { 3: '69 km', 4: '26 km', 5: '9,9 km', 6: '3,7 km', 7: '1,4 km', 8: '530 m', 9: '200 m' };
  $('#resBadge').textContent = `${names[L]}${L === 'hex' || L === 'locais' ? ' · lado ≈ ' + edge[CELLS_RES] : ''} · zoom ${nf1.format(map.getZoom())}`;
}

// ---------------- hover / clique ----------------
function placeTitle(p, cb) {
  const u = B.ufOrder[B.L.uf[p]];
  loadPlaceMeta(B, u).then((d) => { const r = d[String(p)]; if (r) cb(r); }).catch(() => {});
}
function onHover(info) {
  const tt = $('#tooltip');
  if (!info || !info.layer || info.index < 0 || !['muni', 'uf', 'hex', 'places', 'ext'].includes(info.layer.id)) { tt.hidden = true; return; }
  tt.replaceChildren();
  const row = (k, v) => { const r = el('div', 'r'); r.append(el('span', null, k), el('b', null, v)); tt.append(r); };
  if (MODE[S.mode].src === 'pr' && prorrog()) { prHover(info, tt, row); tt.hidden = false; placeTip(tt, info); return; }
  const req = reqDelta();
  let c = null, title = '', sub = '';
  if (info.layer.id === 'ext') {
    const m = info.object.i, off = MA.official(munAgg(m)), n = B.mun.n_secoes[m];
    tt.append(el('div', 't', munName(m)), el('div', 'lg-sub', `Totalizado ${fPct(off.pct, 0)} (oficial) · ${n} ${n === 1 ? 'seção' : 'seções'} · ${fInt(B.mun.a2[m])} eleitores`));
    row('Placar oficial', off.has ? `${short('A')} ${fPct(off.lshare)} × ${fPct(100 - off.lshare)} ${short('B')}` : '—');
    if (off.has) row('Votos', `${fInt(off.l)} × ${fInt(off.o)}`);
    c = extCmp(m);
    if (!c.has) { row('Comparação com o 1T', 'aguardando a cidade toda'); tt.hidden = false; placeTip(tt, info); return; }
    row('Abstenção 2T', fPct(c.abst2));
  } else if (info.layer.id === 'muni') {
    const m = info.object.i, A = munAgg(m), off = MA.official(A), z = munZ(m);
    title = munName(m);
    sub = `Totalizado ${fPct(off.pct, 0)} (oficial)${isNum(z?.pct_bu) ? ` · com boletim ${fPct(z.pct_bu, 0)}` : ''} · ${B.mun.n_zonas[m]} zona(s)`;
    tt.append(el('div', 't', title), el('div', 'lg-sub', sub));
    row('Placar oficial', off.has ? `${off.m >= 0 ? short('A') : short('B')} ${fPP(Math.abs(off.m)).replace('+', '')}` : '—');
    c = MA.cmpFromZ(base1Mun(m), z);
    if (z?.src === 'shrunk' || !c.has) { row('Comparação com o 1T', 'aguardando apuração'); tt.hidden = false; placeTip(tt, info); return; }
    row('Base da comparação', z.src === 'obs' ? 'zonas completas / boletins' : 'parte apurada (estimada pelo analytics)');
  } else if (info.layer.id === 'uf') {
    const u = B.ufOrder[info.object.i], A = agg()?.uf?.[u], off = MA.official(A);
    tt.append(el('div', 't', ufName(u)), el('div', 'lg-sub', `Totalizado ${fPct(off.pct, 0)} (oficial)`));
    row('Placar oficial', off.has ? `${off.m >= 0 ? short('A') : short('B')} ${fPP(Math.abs(off.m)).replace('+', '')}` : '—');
    c = MA.cmpFromT(ufT(u));
    if (!c.has) { row('Comparação com o 1T', 'aguardando apuração'); tt.hidden = false; placeTip(tt, info); return; }
  } else if (info.layer.id === 'hex') {
    c = MA.cmpFromAgg(CELL[CELLS_RES], info.object.i * K);
    tt.append(el('div', 't', `Hexágono H3 · res ${CELLS_RES} · ${fInt(c.nSec)} seções`),
      el('div', 'lg-sub', `% apurado nas zonas: ${fPct(c.zPct, 0)} · com boletim: ${fPct(100 * c.buShare, 0)} · com valor: ${fPct(100 * c.cov, 0)}`));
    if (!c.has) { row('Comparação com o 1T', 'zona ainda sem apuração'); tt.hidden = false; placeTip(tt, info); return; }
    row('Margem 2T', isNum(c.m2) ? `${c.m2 >= 0 ? short('A') : short('B')} ${fPP(Math.abs(c.m2)).replace('+', '')}` : '—');
    row('Base da comparação', c.obs >= 0.999 ? 'observado na urna' : c.obs <= 0.001 ? 'estimado pela zona' : `${fPct(c.obs * 100, 0)} observado · resto estimado pela zona`);
  } else {
    const p = info.index, m = B.L.mun[p];
    c = MA.cmpFromAgg(PLACE, p * K);
    const t = el('div', 't', `Local de votação · ${munName(m)}`);
    tt.append(t, el('div', 'lg-sub', `Zona ${B.zon.zona[B.L.zone[p]]} · ${B.L.nsec[p]} seções · % apurado na zona ${fPct(c.zPct, 0)} · com boletim ${fInt(c.nBU)} de ${fInt(c.nSec)}`));
    placeTitle(p, (r) => { if (!tt.hidden) t.textContent = titleCase(r[0]); });
    if (!c.has) { row('Comparação com o 1T', 'zona ainda sem apuração'); tt.hidden = false; placeTip(tt, info); return; }
    row('Margem 2T', isNum(c.m2) ? `${c.m2 >= 0 ? short('A') : short('B')} ${fPP(Math.abs(c.m2)).replace('+', '')}` : '—');
    row('Base da comparação', c.obs >= 0.999 ? 'observado na urna' : c.obs <= 0.001 ? 'estimado pela zona' : `${fPct(c.obs * 100, 0)} observado · resto estimado pela zona`);
  }
  row('Δ margem vs 1T', `${fM(c.delta)} ${U100}${isNum(c.dMarginV) ? ` (${fN(c.dMarginV, true)} votos)` : ''}`);
  if (isNum(req)) row('Ritmo vs necessário', `${fM(c.delta - req)} ${U100}`);
  row('Δ abstenção', `${fPP(c.dAbst)}${isNum(c.dAbstV) ? ` (${fN(c.dAbstV, true)} ausentes)` : ''}`);
  row('Δ brancos+nulos', `${fPP(c.dBnComp)} do comparecimento`);
  tt.hidden = false; placeTip(tt, info);
}
/** hover nos modos de votos após 17h: 2º turno × 1º turno na mesma urna */
function prHover(info, tt, row) {
  const P = prorrog(), id = info.layer.id;
  let t = '', n2 = 0, t2 = 0, n1 = 0, t1 = 0, c2 = null, a2 = null, ns = null;
  const fromAcc = (a, o) => { n2 = a[o + 18]; t2 = a[o + 19]; n1 = a[o + 20]; t1 = a[o + 21]; ns = a[o]; };
  if (id === 'muni' || id === 'ext') { const m = info.object.i, A = P.mun; t = munName(m); n2 = A.n2[m]; t2 = A.t2[m]; n1 = A.n1[m]; t1 = A.t1[m]; c2 = A.c2[m]; a2 = A.a2[m]; ns = A.s[m]; }
  else if (id === 'uf') { const i = info.object.i, A = P.uf; t = ufName(B.ufOrder[i]); n2 = A.n2[i]; t2 = A.t2[i]; n1 = A.n1[i]; t1 = A.t1[i]; c2 = A.c2[i]; a2 = A.a2[i]; ns = A.s[i]; }
  else if (id === 'hex') { t = `Hexágono H3 · res ${CELLS_RES}`; fromAcc(CELL[CELLS_RES], info.object.i * K); }
  else { const p = info.index; t = `Local de votação · ${munName(B.L.mun[p])}`; fromAcc(PLACE, p * K); const te = el('div', 't', t); tt.append(te); placeTitle(p, (r) => { if (!tt.hidden) te.textContent = titleCase(r[0]); }); t = ''; }
  if (t) tt.append(el('div', 't', t));
  tt.append(el('div', 'lg-sub', `${isNum(ns) ? fInt(ns) + ' seções com log · ' : ''}hora de Brasília${id === 'ext' ? ' (exterior: hora local)' : ''}`));
  const p10 = (t, n) => `${nf1.format(1e4 * t / n).replace(',0', '')} por 10 mil`;
  row('2º turno · depois das 17h', n2 > 0 ? `${fInt(t2)} de ${fInt(n2)} votos (${p10(t2, n2)})` : '—');
  if (isNum(c2)) row('2º turno · depois das 18h', fInt(c2));
  if (isNum(a2)) row('Seções abertas após 17h10', fInt(a2));
  row('1º turno · depois das 17h, mesma urna', n1 > 0 ? `${fInt(t1)} de ${fInt(n1)} votos (${p10(t1, n1)})` : '—');
}
function placeTip(tt, info) {
  const box = $('#mapwrap').getBoundingClientRect();
  const x = Math.min(info.x + 14, box.width - tt.offsetWidth - 8), y = Math.min(info.y + 14, box.height - tt.offsetHeight - 8);
  tt.style.left = Math.max(4, x) + 'px'; tt.style.top = Math.max(4, y) + 'px';
}
function onClick(info) {
  if (!info || !info.layer || info.index < 0) return;
  if (info.layer.id === 'muni' || info.layer.id === 'ext') setFocus({ type: 'muni', id: info.object.i });
  else if (info.layer.id === 'uf') setFocus({ type: 'uf', id: B.ufOrder[info.object.i] });
  else if (info.layer.id === 'hex') setFocus({ type: 'cell', res: CELLS_RES, h: info.object.h, idx: info.object.i });
  else if (info.layer.id === 'places') setFocus({ type: 'place', id: info.index });
}
function setFocus(f, fly = false) {
  S.focus = f;
  if (fly) flyToFocus(f);
  updateLayers(); safe('direita', renderRight); safe('legenda', renderLegend); writeHash();
}
function unionBbox(list) {
  let x0 = 180, y0 = 90, x1 = -180, y1 = -90, ok = false;
  for (const b of list) { if (!b) continue; ok = true; x0 = Math.min(x0, b[0]); y0 = Math.min(y0, b[1]); x1 = Math.max(x1, b[2]); y1 = Math.max(y1, b[3]); }
  return ok ? [[x0, y0], [x1, y1]] : null;
}
function flyToFocus(f, duration = 2600) {
  if (!map) return;
  const pad = { top: 110, bottom: 60, left: 40, right: 40 };
  const cap = $('#tvcap');
  if (cap && !cap.hidden && cap.classList.contains('story') && !cap.classList.contains('palco')) pad.top = Math.min(map.getContainer().clientHeight * 0.55, cap.offsetTop + cap.offsetHeight + 12);
  const fit = (bb) => bb && map.fitBounds(bb, { padding: pad, duration, maxZoom: 11.2 });
  if (f.type === 'br') fit(B.meta.br_bounds);
  else if (f.type === 'region' && f.id === 'ZZ') { if (EXT?.bounds) map.fitBounds(EXT.bounds, { padding: { top: 150, bottom: 50, left: 30, right: 30 }, duration }); }
  else if (f.type === 'muni' && EXT?.byMun.has(f.id)) map.flyTo({ center: EXT.byMun.get(f.id).p, zoom: Math.max(4.5, Math.min(map.getZoom(), 6)), duration });
  else if (f.type === 'region') fit(unionBbox(B.ufs.filter((u) => u.regiao === f.id).map((u) => u.bbox)));
  else if (f.type === 'uf') fit(unionBbox([B.ufs[B.ufIdx[f.id]]?.bbox]));
  else if (f.type === 'muni') { const bb = B.mun.bbox[f.id]; if (bb) fit([[bb[0], bb[1]], [bb[2], bb[3]]]); else if (isNum(B.mun.lng[f.id])) map.flyTo({ center: [B.mun.lng[f.id], B.mun.lat[f.id]], zoom: 9, duration }); }
  else if (f.type === 'place') map.flyTo({ center: [B.L.lng[f.id], B.L.lat[f.id]], zoom: 14, duration });
  else if (f.type === 'cell') { const H = B.h3[f.res], i = H?.index.get(f.h); if (i != null) map.flyTo({ center: [H.lng[i], H.lat[i]], zoom: Math.max(map.getZoom(), 8), duration }); }
}

// ---------------- painéis ----------------
function tile(label, value, subtxt, key) {
  const t = el('div', 'tile'); const l = el('div', 'l'); if (key) l.append(keyEl(key)); l.append(label);
  t.append(l, el('div', 'v', value), el('div', 's', subtxt ?? '')); return t;
}
function phaseChip() {
  const st = an()?.status, ph = phase();
  const txt = { none: 'aguardando analytics', pre: 'pré-apuração (<2%)', early: 'sinal inicial · não é tendência', trend: 'tendência', final: 'final' }[ph] || ph;
  const c = el('span', 'phase ph-' + ph, txt); c.title = st?.reason || ''; return c;
}
function captureBlock(M, compact = false) {
  const frag = el('div');
  const c = M?.capture;
  if (c && [c.l, c.o, c.bn, c.abst].every(isNum)) {
    const parts = [['A', c.l, short('A')], ['B', c.o, short('B')], ['n', c.bn, 'Branco/nulo'], ['x', c.abst, 'Deixou de votar']];
    const tot = parts.reduce((s, p) => s + Math.max(0, p[1]), 0) || 1;
    const bar = el('div', 'capbar'), lab = el('div', 'caplab');
    const col = { A: 'var(--A)', B: 'var(--B)', n: 'var(--muted)', x: 'var(--grid)' };
    for (const [k, v, n] of parts) {
      const i = el('i'); i.style.width = (Math.max(0, v) / tot * 100) + '%'; i.style.background = col[k]; bar.append(i);
      const s = el('span'); const kk = el('span', 'key'); kk.style.background = col[k]; s.append(kk, `${n} ${fPct(v * 100, 0)}`); lab.append(s);
    }
    // sem o título a barra parece o placar (ex.: "Lula 48%" num estado em que ele perde): o título vai sempre
    const kk = el('div', 'kicker', `Votos em disputa${isNum(M.pool_gross) ? ` (${fN(M.pool_gross)})` : ''}: para onde foram`);
    if (!compact) kk.dataset.drop = '5';
    const why = el('div', 'lg-note', 'Não é o placar. Em disputa = votos que no 1º turno foram para outros candidatos ou branco/nulo, mais quem passou a votar, nas zonas já apuradas.');
    why.dataset.drop = '5';
    frag.append(kk, bar, lab, why);
  }
  if (isNum(M?.capture_l)) {
    const note = el('div', 'capnote');
    note.append('Saldo líquido entre os que mudaram de lugar: ', el('b', null, `${short('A')} ${fPct(M.capture_l * 100, 0)}`), ` × ${fPct((1 - M.capture_l) * 100, 0)} ${short('B')}`);
    const need = an()?.pace?.capture_needed;
    if (isNum(need) && !compact) note.append(` · ${short('A')} precisa de `, el('b', null, fPct(need * 100, 0)));
    frag.append(note);
  }
  return frag;
}
/** antes do fim da votação (BRT): texto "faltam X"; senão null */
function untilFim() {
  const now = Date.now(), t = fimVotacao(now).getTime();
  return now < t ? fAge((t - now) / 1000) : null;
}
/** Referência discreta (some a partir de 2%): não pode competir com o placar do 2º turno no topo. */
function baselineCard() {
  const b = an()?.baseline_1t || B.meta.baseline_1t, bm = B.meta.baseline_1t;
  const c = el('div', 'card base1t');
  c.append(el('div', 'kicker', 'Ponto de partida · 1º turno'));
  const lp = b.l_pct_valid ?? bm.l_pct_valid, op = b.o_pct_valid ?? bm.o_pct_valid;
  const duo = el('div', 'duo'), ia = el('i'), ib = el('i');
  ia.style.width = lp + '%'; ia.style.background = 'var(--A)'; ib.style.width = op + '%'; ib.style.background = 'var(--B)'; duo.append(ia, el('i', 'gap'), ib);
  const row = el('div', 'base-row');
  const a = el('span'); a.append(keyEl('A'), `${short('A')} ${fPct(lp, 2)}`);
  const o = el('span'); o.append(`${fPct(op, 2)} ${short('B')}`, keyEl('B'));
  row.append(a, o);
  c.append(row, duo);
  c.append(el('div', 'sub', `${fInt(b.l)} × ${fInt(b.o)} votos · diferença de ${fN(Math.abs(b.margin_votes))} votos · ${fN(b.aptos)} aptos`));
  const u = untilFim();
  if (u) c.append(el('div', 'waiting', agg()?.br?.st > 0 ? `Votação até as ${fimTxt()}: o TSE retoma a divulgação em ${u}.` : `Votação até as ${fimTxt()}: resultados começam em ${u}.`));
  return c;
}
let lastLeftAt = 0;
function renderLeft() { renderLeftInner(); fitLeft(); }
function renderLeftInner() {
  lastLeftAt = Date.now();
  const L = $('#left'); L.replaceChildren();
  const A = an(), ph = phase(), req = reqDelta(), pace = A?.pace || {};
  if (!comparing()) L.append(baselineCard());
  // 1. termômetro
  const c1 = el('div', 'card');
  const h1 = el('div', 'card-head'); h1.append(el('h2', null, 'Termômetro da campanha · Brasil'), phaseChip()); c1.append(h1);
  const T = A?.thermometer?.br, M = T?.best;
  if (!comparing() || !M) {
    const pct = A?.status?.pct_secoes ?? MA.official(agg()?.br).pct;
    c1.append(el('div', 'sub', 'Compara cada zona/seção apurada no 2º turno com ela mesma no 1º turno.'));
    c1.append(el('div', 'waiting', !A ? 'Aguardando analytics.json.' : `O termômetro começa com 2% das seções apuradas (agora: ${fPct(pct, 1)}).`));
  } else {
    c1.append(el('div', 'sub', `Soma simples do que já foi apurado, nas mesmas zonas e seções do 1º turno · cobre ${fPct(T.coverage_aptos_pct)} do eleitorado`));
    const hero = el('div', 'hero');
    hero.append(el('span', 'v', fM(M.delta)), el('span', 'vs', `votos de margem ${short('A')} − ${short('B')} a cada 100 eleitores, sobre o 1º turno · ${fN(M.d_margin_votes, true)} votos`));
    c1.append(hero);
    const X = T.exact;
    const ex = el('div', 'lg-note', X && isNum(X.delta) ? `Exato (zonas completas + boletins): ${fM(X.delta)} em ${fPct(T.coverage_exact_pct ?? X.coverage_aptos_pct)} do eleitorado.` : 'Exato (zonas completas + boletins): aguardando.');
    ex.dataset.drop = '4'; c1.append(ex);
    const tiles = el('div', 'tiles four');
    tiles.append(
      tile('Δ abstenção', fPP(M.d_abst_pp), isNum(M.d_abst_n) ? `${fN(M.d_abst_n, true)} eleitores` : '—'),
      tile('Δ brancos+nulos', fPP(isNum(M.d_bn_pp_comp) ? M.d_bn_pp_comp : M.d_bn_pp), isNum(M.d_bn_n) ? `${fN(M.d_bn_n, true)} votos` : '—'),
      tile(`Δ votos ${short('A')}`, fN(M.d_l, true), isNum(M.d_l_100) ? `${fPP(M.d_l_100)} aptos` : '', 'A'),
      tile(`Δ votos ${short('B')}`, fN(M.d_o, true), isNum(M.d_o_100) ? `${fPP(M.d_o_100)} aptos` : '', 'B'),
    );
    c1.append(tiles);
    const cap = captureBlock(M); cap.style.marginTop = '.6rem'; c1.append(cap);
  }
  L.append(c1);

  // 2. ritmo
  const c2 = el('div', 'card');
  c2.append(el('h2', null, 'Ritmo necessário vs observado'));
  const obs = comparing() ? pace.delta_obs : null, band = Array.isArray(pace.delta_obs_band) ? pace.delta_obs_band : null;
  // no fim da noite o denominador (aptos que faltam) some e o δ_rem explode: não informa nada
  const rem = comparing() && ph !== 'final' && isNum(pace.delta_rem) && Math.abs(pace.delta_rem) <= 50 ? pace.delta_rem : null;
  const gap = -(A?.baseline_1t?.margin_votes ?? B.meta.baseline_1t.margin_votes);
  const s2 = el('div', 'sentence');
  s2.append(rich([`${S.cand.A.name} precisa ganhar `, { b: `${fM(req, 2)} voto${Math.abs(req) >= 2 ? 's' : ''} de margem a cada 100 eleitores` }, ` sobre o 1º turno (${gap >= 0 ? 'déficit' : 'vantagem'} de ${fN(Math.abs(gap))} votos no 1º turno).`]));
  if (isNum(obs)) s2.append(rich([' Nas seções já apuradas, com cada estado pesando o seu eleitorado: ', { b: fM(obs, 2) }, band && band.every(isNum) ? ` (faixa ${fM(band[0], 2)} a ${fM(band[1], 2)})` : '', '.']));
  else s2.append(' O ritmo observado aparece a partir de 2% das seções apuradas.');
  if (isNum(rem)) s2.append(rich([' No que falta apurar, precisaria de ', { b: fM(rem, 2) }, '.']));
  c2.append(s2);
  const vals = [0, req, obs, ...(band || []), isNum(rem) && Math.abs(rem) < 12 ? rem : null].filter(isNum);
  const lo = Math.min(-1, ...vals) - 0.8, hi = Math.max(1, ...vals) + 0.8, X = (v) => ((Math.max(lo, Math.min(hi, v)) - lo) / (hi - lo)) * 100;
  const g = el('div', 'gauge'); g.append(el('div', 'axis'));
  const z = el('div', 'zero'); z.style.left = X(0) + '%'; g.append(z);
  if (isNum(obs)) {
    if (band && band.every(isNum)) { const bb = el('div', 'band'); bb.style.left = X(band[0]) + '%'; bb.style.width = Math.max(0.6, X(band[1]) - X(band[0])) + '%'; g.append(bb); }
    const b = el('div', 'bar'); const a = Math.min(X(0), X(obs)), w = Math.abs(X(obs) - X(0));
    b.style.left = a + '%'; b.style.width = w + '%'; b.style.background = obs >= req ? 'var(--A)' : 'var(--B)'; g.append(b);
    const ol = el('div', 'obslab', `observado ${fM(obs)}`); ol.style.left = X(obs) + '%'; g.append(ol);
  }
  if (isNum(rem)) { const rr = el('div', 'rem' + (Math.abs(rem) >= 12 ? ' off' : '')); rr.style.left = `calc(${X(rem)}% - 1px)`; rr.title = `necessário no que falta: ${fM(rem, 2)}`; g.append(rr);
    if (Math.abs(X(rem) - X(req)) > 14) {
      const xr = X(rem), rl2 = el('div', 'remlab', `no que falta ${fM(rem)}${Math.abs(rem) >= 12 ? ' (fora da escala)' : ''}`);
      rl2.style.left = xr + '%'; rl2.style.transform = xr < 18 ? 'none' : xr > 82 ? 'translateX(-100%)' : 'translateX(-50%)';
      if (isNum(obs) && Math.abs(xr - X(obs)) < 24) rl2.classList.add('up'); // longe do observado: embaixo, sem cruzar o "necessário"
      g.append(rl2);
    } }
  if (isNum(req)) { const r = el('div', 'req'); r.style.left = `calc(${X(req)}% - 1px)`; g.append(r); const rl = el('div', 'reqlab', `necessário ${fM(req)}`); rl.style.left = X(req) + '%'; g.append(rl); }
  c2.append(g);
  if (isNum(obs) && isNum(req)) {
    const st = el('span', 'status');
    const above = band && band.every(isNum) ? band[0] > req : obs > req, below = band && band.every(isNum) ? band[1] < req : obs < req;
    st.append(above ? '✓ acima do ritmo necessário' : below ? '✗ abaixo do ritmo necessário' : '≈ faixa inclui o ritmo necessário', el('span', 'kicker', ` · ${fM(obs - req)} ${U100}`));
    c2.append(st);
  }
  L.append(c2);

  // 3. projeção
  const c3 = el('div', 'card'), pj = A?.projection, st = A?.status;
  const h3h = el('div', 'card-head'); h3h.append(el('h2', null, 'Projeção para 100% das seções')); c3.append(h3h);
  if (!comparing() || !pj || !isNum(pj.margin_votes)) {
    c3.append(el('div', 'waiting', !A ? 'Aguardando analytics.json.' : 'A projeção começa com 2% das seções apuradas.'));
    L.append(c3); return;
  }
  if (ph === 'early') { const d = el('div', 'sub', 'Oficial já totalizado + modelo do analytics nas seções que faltam. Faixa de 90%.'); d.dataset.drop = '2'; c3.append(d); }
  if (st?.can_call && (st.call === 'L' || st.call === 'O')) {
    const k = st.call === 'L' ? 'A' : 'B';
    const call = el('div', 'call'); call.append(keyEl(k), `Projeção: ${S.cand[k].name} vence`); c3.append(call);
  }
  const ph2 = el('div', 'proj-head');
  const left = el('div');
  left.append(el('div', 'kicker', ph === 'trend' ? 'Tendência · % dos válidos' : ph === 'final' ? 'Final · % dos válidos' : 'Projeção · % dos válidos'));
  const pn = el('div', 'proj-num');
  pn.append(keyEl('A'), `${short('A')} ${fPct(pj.l_pct_valid)}`, el('span', 'x', ' × '), `${fPct(pj.o_pct_valid ?? (isNum(pj.l_pct_valid) ? 100 - pj.l_pct_valid : null))} ${short('B')}`, keyEl('B'));
  left.append(pn);
  const bp = Array.isArray(pj.band_pp_valid) ? pj.band_pp_valid : null, bv = Array.isArray(pj.band_votes) ? pj.band_votes : null;
  left.append(el('div', 'bandline', `margem ${fPP(pj.margin_pp_valid, 2)} · faixa 90%: ${bp ? `${fPP(bp[0], 2)} a ${fPP(bp[1], 2)}` : '—'}`));
  left.append(el('div', 'sub', `${fN(pj.margin_votes, true)} votos (faixa ${bv ? `${fN(bv[0], true)} a ${fN(bv[1], true)}` : '—'})${(ph === 'trend' || ph === 'final') && isNum(pj.p_lula) ? ` · chance no modelo: ${short('A')} ${fProb(pj.p_lula)}` : ''}`));
  ph2.append(left);
  const chip = el('span', 'status');
  if (st?.can_call) chip.textContent = st.reason || 'faixa inteira de um lado';
  else chip.textContent = st?.reason ? `Sem vencedor projetado · ${st.reason}` : 'Sem vencedor projetado';
  ph2.append(chip);
  c3.append(ph2);
  const vo = A.virada_observada;
  if (vo?.t) { const vn = el('div', 'lg-note', `Placar bruto trocou de líder às ${hhmm(parseIso(vo.t))} (${fPct(vo.pct_secoes, 0)} das seções): agora à frente ${vo.leader === 'L' ? short('A') : short('B')}.`); vn.dataset.drop = '6'; c3.append(vn); }
  if (st?.can_call && pj.virada?.projected && isNum(pj.virada_pct_secoes)) {
    const late = pj.virada_pct_secoes >= 99; // ETA no fim (ou além de 100%): sem horário
    const lead = short(pj.virada.leader_final === 'L' ? 'A' : 'B');
    const ind = el('div', 'virnote', late ? `Virada prevista: ${lead} só deve passar à frente no placar bruto no fim da apuração.`
      : `Virada prevista: ${lead} deve passar à frente no placar bruto perto de ${fPct(pj.virada_pct_secoes, 0)} das seções${pj.virada.eta ? ` (≈ ${pj.virada.eta.slice(0, 5)})` : ''}; incerteza de ±10 p.p.`);
    ind.dataset.drop = '7'; c3.append(ind); // a última coisa a sair quando falta espaço: é o que se espera ver na TV
  }
  const ch = el('div', 'chart'); ch.id = 'chart'; c3.append(ch);
  const lg = el('div', 'lgd'); lg.dataset.drop = '3';
  lg.innerHTML = '<span><span class="ln"></span>Placar oficial apurado</span><span><span class="ln thin"></span>Projeção ao longo da noite</span><span><span class="band"></span>Faixa 90%</span><span><span class="ln dash"></span>Até a projeção final</span>';
  c3.append(lg);
  L.append(c3);
  renderChart(ch);
}

// ---------- TV sem rolagem: as colunas cedem espaço (gráfico encolhe, linhas opcionais somem por prioridade) ----------
const narrow = () => window.innerWidth <= 1100;
const overOf = (C) => C.scrollHeight - C.clientHeight;
function contentBottom(C) { const last = C.lastElementChild; if (!last) return 0; const pb = parseFloat(getComputedStyle(C).paddingBottom) || 0; return last.getBoundingClientRect().bottom - C.getBoundingClientRect().top + C.scrollTop + pb; }
function dropByPriority(C) {
  const drops = [...C.querySelectorAll('[data-drop]')].sort((a, b) => a.dataset.drop - b.dataset.drop);
  for (const e of drops) { if (overOf(C) <= 0) break; e.classList.add('dropped'); }
}
function fitLeft() {
  const L = $('#left'); if (!L || narrow()) return;
  L.querySelectorAll('.dropped').forEach((e) => e.classList.remove('dropped'));
  const ch = L.querySelector('#chart');
  const h0 = ch ? renderChart(ch) : 0;
  let h = h0;
  if (overOf(L) > 0 && ch) h = renderChart(ch, Math.max(96, h - overOf(L) - 2));
  dropByPriority(L);
  if (overOf(L) > 0 && ch) h = renderChart(ch, Math.max(56, h - overOf(L) - 2));
  if (ch && h < h0) { const slack = L.clientHeight - contentBottom(L) - 2; if (slack > 4) renderChart(ch, Math.min(h0, h + slack)); }
}
function fitRight() {
  const R = $('#right'); if (!R || narrow()) return;
  R.querySelectorAll('.dropped').forEach((e) => e.classList.remove('dropped'));
  if (overOf(R) <= 0) return;
  dropByPriority(R);
  for (const r of [...R.querySelectorAll('table.rank tr.click')].reverse()) { if (overOf(R) <= 0) break; r.classList.add('dropped'); }
  if (overOf(R) > 0) R.querySelector('.card.rank')?.classList.add('dropped');
}
let fitRaf = 0;
function scheduleFit() { if (fitRaf) return; fitRaf = requestAnimationFrame(() => { fitRaf = 0; safe('ajuste esquerda', fitLeft); safe('ajuste direita', fitRight); }); }

function renderChart(box, hForce) {
  const A = an(), hist = (A?.history || []).filter((p) => isNum(p.pct_secoes) && isNum(p.margin_apurado));
  const pj = A?.projection;
  const w = box.clientWidth || 480, h = hForce || Math.round(parseFloat(getComputedStyle(document.documentElement).fontSize) * (window.innerWidth > 1100 ? 8.5 : 14));
  const m = { l: 46, r: 12, t: 18, b: 26 };
  const iw = w - m.l - m.r, ih = h - m.t - m.b;
  const toM = (v) => v / 1e6;
  let ymin = -1, ymax = 1;
  const upd = (v) => { if (isNum(v)) { ymin = Math.min(ymin, toM(v)); ymax = Math.max(ymax, toM(v)); } };
  // escala pelas margens e pela faixa ATUAL; faixas antigas (largas no começo da noite) são cortadas na borda
  for (const p of hist) { upd(p.margin_apurado); upd(p.margin_proj); }
  if (pj) { upd(pj.margin_votes); if (Array.isArray(pj.band_votes)) { upd(pj.band_votes[0]); upd(pj.band_votes[1]); } }
  const pad = (ymax - ymin) * 0.1; ymin -= pad; ymax += pad;
  const X = (v) => m.l + (Math.max(0, Math.min(100, v)) / 100) * iw, Y = (v) => m.t + (1 - (Math.max(ymin, Math.min(ymax, toM(v))) - ymin) / (ymax - ymin)) * ih;
  const Ym = (mv) => m.t + (1 - (mv - ymin) / (ymax - ymin)) * ih;
  const ns = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(ns, 'svg'); svg.setAttribute('viewBox', `0 0 ${w} ${h}`); svg.setAttribute('height', h);
  svg.setAttribute('role', 'img'); svg.setAttribute('aria-label', 'Margem em votos por % de seções totalizadas');
  const add = (tag, attrs, txt) => { const e = document.createElementNS(ns, tag); for (const k in attrs) e.setAttribute(k, attrs[k]); if (txt != null) e.textContent = txt; svg.append(e); return e; };
  const cA = cssVar('--A'), cB = cssVar('--B'), ink = cssVar('--ink'), ink2 = cssVar('--ink-2'), muted = cssVar('--muted'), grid = cssVar('--grid'), axis = cssVar('--axis');
  const wash = +cssVar('--wash') || 0.08;
  add('rect', { x: m.l, y: Ym(ymax), width: iw, height: Math.max(0, Ym(0) - Ym(ymax)), fill: cA, opacity: wash });
  add('rect', { x: m.l, y: Ym(0), width: iw, height: Math.max(0, Ym(ymin) - Ym(0)), fill: cB, opacity: wash });
  const span = ymax - ymin, step = span > 16 ? 5 : span > 8 ? 2 : span > 4 ? 1 : 0.5;
  for (let v = Math.ceil(ymin / step) * step; v <= ymax; v += step) {
    add('line', { x1: m.l, x2: m.l + iw, y1: Ym(v), y2: Ym(v), stroke: Math.abs(v) < 1e-9 ? axis : grid, 'stroke-width': 1 });
    add('text', { x: m.l - 6, y: Ym(v) + 4, 'text-anchor': 'end', 'font-size': 11, fill: muted }, (v > 0 ? '+' : v < 0 ? '−' : '') + nf1.format(Math.abs(v)).replace(',0', ''));
  }
  for (const v of [0, 25, 50, 75, 100]) add('text', { x: X(v), y: h - 8, 'text-anchor': v === 0 ? 'start' : v === 100 ? 'end' : 'middle', 'font-size': 11, fill: muted }, v + '%');
  add('text', { x: m.l, y: m.t - 6, 'font-size': 11, fill: ink2 }, `↑ ${short('A')} à frente · margem em mi votos`); // acima da área do gráfico
  const lateLabels = [[`↓ ${short('B')} à frente`, m.l + 6, Ym(ymin) - 6, 'start', 11, ink2], ['seções totalizadas', m.l + iw - 2, h - 8 - 13, 'end', 10, muted]];
  const hb = hist.filter((p) => Array.isArray(p.band) && p.band.every(isNum));
  if (hb.length > 1) add('polygon', { points: hb.map((p) => `${X(p.pct_secoes)},${Y(p.band[1])}`).concat(hb.slice().reverse().map((p) => `${X(p.pct_secoes)},${Y(p.band[0])}`)).join(' '), fill: ink, opacity: 0.12 });
  const hp = hist.filter((p) => isNum(p.margin_proj));
  if (hp.length > 1) add('polyline', { points: hp.map((p) => `${X(p.pct_secoes)},${Y(p.margin_proj)}`).join(' '), fill: 'none', stroke: ink2, 'stroke-width': 1.5, 'stroke-linejoin': 'round' });
  if (hist.length > 1) add('polyline', { points: hist.map((p) => `${X(p.pct_secoes)},${Y(p.margin_apurado)}`).join(' '), fill: 'none', stroke: ink, 'stroke-width': 2.5, 'stroke-linejoin': 'round', 'stroke-linecap': 'round' });
  const last = hist[hist.length - 1];
  if (last && pj && isNum(pj.margin_votes) && last.pct_secoes < 99.9) {
    const bv = Array.isArray(pj.band_votes) && pj.band_votes.every(isNum) ? pj.band_votes : null;
    if (bv) add('polygon', { points: `${X(last.pct_secoes)},${Y(last.margin_apurado)} ${X(100)},${Y(bv[1])} ${X(100)},${Y(bv[0])}`, fill: pj.margin_votes >= 0 ? cA : cB, opacity: 0.18 });
    add('line', { x1: X(last.pct_secoes), y1: Y(last.margin_apurado), x2: X(100), y2: Y(pj.margin_votes), stroke: ink, 'stroke-width': 2, 'stroke-dasharray': '5 4' });
    add('circle', { cx: X(100), cy: Y(pj.margin_votes), r: 4, fill: ink });
  }
  if (last) add('circle', { cx: X(last.pct_secoes), cy: Y(last.margin_apurado), r: 4.5, fill: ink, stroke: cssVar('--panel'), 'stroke-width': 2 });
  const vo = A?.virada_observada;
  const wLab = (t, fs) => t.length * fs * 0.56 + 6;
  let voSpan = null; // rótulos acima da linha do zero, à direita do ponto (abaixo dela fica "↓ B à frente")
  if (vo && isNum(vo.pct_secoes)) {
    add('circle', { cx: X(vo.pct_secoes), cy: Ym(0), r: 5, fill: ink, stroke: cssVar('--panel'), 'stroke-width': 2 });
    const t = `virada ${hhmm(parseIso(vo.t))}`, x = X(vo.pct_secoes) + 8;
    lateLabels.push([t, x, Ym(0) - 8, 'start', 11, ink, 700]); voSpan = [x, x + wLab(t, 11)];
  }
  const pv = pj?.virada?.projected ? pj.virada_pct_secoes : null;
  if (last && isNum(pv) && pv > last.pct_secoes) {
    add('circle', { cx: X(pv), cy: Ym(0), r: 5, fill: cssVar('--panel'), stroke: ink, 'stroke-width': 2 });
    const t = `virada prevista ≈ ${fPct(pv, 0)}`, wt = wLab(t, 11), xl = X(pv) - 8, xr = X(pv) + 8;
    if (xl - wt > m.l && !(voSpan && xl - wt < voSpan[1] + 4)) lateLabels.push([t, xl, Ym(0) - 8, 'end', 11, ink, 700]);
    else if (xr + wt < m.l + iw) lateLabels.push([t, xr, Ym(0) - 8, 'start', 11, ink, 700]);
  }
  // rótulos internos por cima das curvas, com fundo (não somem atrás da linha)
  for (const [t, x, y, anchor, fs, col, wt] of lateLabels) {
    const wEst = t.length * fs * 0.56 + 6, x0 = anchor === 'end' ? x - wEst + 3 : x - 3;
    add('rect', { x: x0, y: y - fs, width: wEst, height: fs + 4, rx: 3, fill: cssVar('--panel'), opacity: 0.85 });
    add('text', { x, y, 'text-anchor': anchor, 'font-size': fs, 'font-weight': wt || 400, fill: col }, t);
  }
  // crosshair
  const ov = add('rect', { x: m.l, y: m.t, width: iw, height: ih, fill: 'transparent' });
  const cl = add('line', { y1: m.t, y2: m.t + ih, stroke: muted, 'stroke-width': 1, visibility: 'hidden' });
  const tip = el('div', 'ctip'); tip.hidden = true;
  ov.addEventListener('pointermove', (e) => {
    if (!hist.length) return;
    const r = svg.getBoundingClientRect(), px = ((e.clientX - r.left) / r.width) * w, pct = ((px - m.l) / iw) * 100;
    let best = hist[0]; for (const p of hist) if (Math.abs(p.pct_secoes - pct) < Math.abs(best.pct_secoes - pct)) best = p;
    cl.setAttribute('x1', X(best.pct_secoes)); cl.setAttribute('x2', X(best.pct_secoes)); cl.setAttribute('visibility', 'visible');
    tip.replaceChildren(el('div', null, `${hhmm(parseIso(best.t))} · ${fPct(best.pct_secoes)} totalizado`));
    const r1 = el('div'); r1.append(el('b', null, fN(best.margin_apurado, true)), ' placar oficial'); tip.append(r1);
    if (isNum(best.margin_proj)) { const r2 = el('div'); r2.append(el('b', null, fN(best.margin_proj, true)), Array.isArray(best.band) ? ` projeção (${fN(best.band[0], true)} a ${fN(best.band[1], true)})` : ' projeção'); tip.append(r2); }
    if (isNum(best.delta_obs)) { const r3 = el('div'); r3.append(el('b', null, fM(best.delta_obs)), ` Δ margem vs 1T ${U100}`); tip.append(r3); }
    tip.hidden = false; tip.style.left = Math.min((X(best.pct_secoes) / w) * r.width + 10, r.width - 220) + 'px'; tip.style.top = '6px';
  });
  ov.addEventListener('pointerleave', () => { cl.setAttribute('visibility', 'hidden'); tip.hidden = true; });
  box.replaceChildren(svg, tip);
  return h;
}

function decompBlock(d) {
  const wrap = el('div');
  if (!d || !isNum(d.turnout) || !isNum(d.preference)) return wrap;
  wrap.append(el('div', 'kicker', 'De onde veio a Δ margem (votos)'));
  const g = el('div', 'decomp'); g.style.marginTop = '.3rem';
  const mx = Math.max(Math.abs(d.turnout), Math.abs(d.preference), 1);
  for (const [lab, v] of [['Efeito comparecimento', d.turnout], ['Efeito preferência', d.preference]]) {
    const trk = el('div', 'trk'); trk.append(el('div', 'z'));
    const bar = el('i'); const wpc = Math.abs(v) / mx * 50;
    bar.style.left = (v >= 0 ? 50 : 50 - wpc) + '%'; bar.style.width = wpc + '%'; bar.style.background = v >= 0 ? 'var(--A)' : 'var(--B)'; trk.append(bar);
    g.append(el('span', null, lab), trk, el('span', 'val tab', fN(v, true)));
  }
  wrap.append(g);
  const dn = el('div', 'lg-note', 'Comparecimento: nº de votantes × inclinação local. Preferência: margem por votante. Soma = Δ margem.'); dn.dataset.drop = '1'; wrap.append(dn);
  return wrap;
}
/** dados do foco: {name, kind, c (cmp), off, pct, pctBu, M (analytics M p/ BR/região/UF), note} */
function focusData(f) {
  const A = an();
  switch (f.type) {
    case 'region': {
      const T = A?.thermometer?.regiao?.[f.id], mx = A?.status?.mix_regiao?.[f.id];
      let l = 0, o = 0; for (const u of B.ufOrder) if (B.ufReg[B.ufIdx[u]] === f.id) { const a = agg()?.uf?.[u]; l += a?.l || 0; o += a?.o || 0; }
      return { name: regName(f.id), kind: f.id === 'ZZ' ? 'Fora do Brasil' : 'Região', c: MA.cmpFromT(T), M: T?.best, off: { has: l + o > 0, l, o, m: l + o > 0 ? 100 * (l - o) / (l + o) : null, lshare: l + o > 0 ? 100 * l / (l + o) : null }, pct: mx?.pct_apurado ?? A?.status?.region_pct_aptos?.[f.id], pctLabel: 'dos aptos apurados' };
    }
    case 'uf': {
      const T = ufT(f.id), a = agg()?.uf?.[f.id], off = MA.official(a), pr = A?.projection?.uf?.[f.id];
      return { name: ufName(f.id), kind: 'UF', c: MA.cmpFromT(T), M: T?.best, off, pct: off.pct, pctLabel: 'das seções totalizadas (oficial)', proj: pr };
    }
    case 'muni': {
      if (isExt(f.id)) {
        const off = MA.official(munAgg(f.id));
        return { name: munName(f.id), kind: 'Cidade no exterior', c: extCmp(f.id), off, pct: off.pct, pctLabel: 'das seções totalizadas (oficial)', src: 'obs' };
      }
      const z = munZ(f.id), off = MA.official(munAgg(f.id));
      return { name: munName(f.id), kind: B.mun.capital[f.id] ? 'Capital' : 'Município', c: MA.cmpFromZ(base1Mun(f.id), z), off, pct: off.pct, pctLabel: 'das seções totalizadas (oficial)', pctBu: z?.pct_bu, src: z?.src };
    }
    case 'cell': {
      const arr = CELL[f.res]; const c = arr && f.idx != null ? MA.cmpFromAgg(arr, f.idx * K) : { has: false };
      return { name: `Hexágono H3 res ${f.res}`, kind: 'Hexágono', c, pct: c.zPct, pctLabel: 'apurado nas zonas (média)', pctBu: 100 * (c.buShare || 0), local: true };
    }
    case 'place': {
      const c = MA.cmpFromAgg(PLACE, f.id * K);
      return { name: 'Local de votação', kind: munName(B.L.mun[f.id]), c, pct: c.zPct, pctLabel: 'apurado na zona', pctBu: 100 * (c.buShare || 0), local: true };
    }
    default: {
      const T = A?.thermometer?.br, off = MA.official(agg()?.br);
      return { name: 'Brasil', kind: 'País', c: MA.cmpFromT(T), M: T?.best, off, pct: off.pct, pctLabel: 'das seções totalizadas (oficial)' };
    }
  }
}
function renderRight() { renderRightInner(); safe('ajuste direita', fitRight); }
function renderRightInner() {
  const R = $('#right'); R.replaceChildren();
  if (MODE[S.mode].src === 'pr') { R.append(prCard()); return; }
  const f = S.focus, d = focusData(f), c = d.c || { has: false }, req = reqDelta();
  const card = el('div', 'card');
  const head = el('div', 'focus-head'), hl = el('div');
  const nameEl = el('div', 'name', d.name);
  hl.append(el('div', 'kicker', 'Em foco · ' + d.kind), nameEl);
  head.append(hl);
  if (f.type === 'place') placeTitle(f.id, (r) => { nameEl.textContent = titleCase(r[0]); });
  if (f.type !== 'br') { const b = el('button', 'linkbtn', '× Brasil'); b.onclick = () => { userAct(); setFocus({ type: 'br' }, true); }; head.append(b); }
  card.append(head);
  const subs = [`${fPct(d.pct)} ${d.pctLabel}`];
  if (isNum(d.pctBu)) subs.push(`com boletim ${fPct(d.pctBu, 0)}`);
  if (d.local && c.nSec) subs.push(`com valor 2T ${fPct(100 * c.cov, 0)} das seções (${fPct(100 * (c.obs || 0), 0)} observado na urna, resto estimado pela zona)`);
  if (d.src === 'est') subs.push('comparação estimada (zona parcial)');
  card.append(el('div', 'sub', subs.join(' · ')));
  const comp = comparing() && c.has && d.src !== 'shrunk';
  const mini = el('div', 'mini');
  mini.append(
    tile(`Δ margem (${U100})`, comp ? fM(c.delta) : '—', comp && isNum(c.dMarginV) ? `${fN(c.dMarginV, true)} votos` : 'aguardando apuração'),
    tile('Δ abstenção', comp ? fPP(c.dAbst) : '—', comp && isNum(c.dAbstV) ? `${fN(c.dAbstV, true)} ausentes` : ''),
    tile('Δ brancos+nulos', comp ? fPP(c.dBnComp) : '—', comp ? '% do comparecimento' : ''),
    tile('Ritmo', comp && isNum(req) ? (c.delta >= req ? '✓ ' : '✗ ') + fM(c.delta - req) : '—', `soma simples vs necessário (${fM(req)})`),
  );
  card.append(mini);
  if (d.off?.has && !d.local) {
    const pl = el('div', 'sub'); pl.append(`Placar oficial: ${short('A')} ${fPct(d.off.lshare)} × ${fPct(100 - d.off.lshare)} ${short('B')} (${fN(d.off.l)} × ${fN(d.off.o)})`);
    if (d.proj && isNum(d.proj.l_pct_valid) && comparing()) pl.append(` · projeção ${short('A')} ${fPct(d.proj.l_pct_valid)}`);
    card.append(pl);
  } else if (d.local && c.has) card.append(el('div', 'sub', `2º turno (observado + estimado pela zona): ${short('A')} ${fPct(c.l2share)} × ${fPct(100 - c.l2share)} ${short('B')}`));
  if (comp && d.M) { if (f.type !== 'br') card.append(captureBlock(d.M, true)); const dd = decompBlock(d.M.decomp); dd.style.marginTop = '.5rem'; card.append(dd); }
  R.append(card);
  if (f.type === 'place') { R.append(placeCard(f.id)); return; }
  if (extFocus() && EXT?.list.length) { R.append(extCard(f)); return; }
  R.append(tableCard(f));
  if (f.type !== 'region' && f.type !== 'uf') R.append(rankCard()); // região/UF: a tabela de UFs ocupa o espaço (TV sem rolagem)
}
/** exterior: resumo e tabela por país; clicar num país voa até ele e abre as cidades */
function extCard(f) {
  const card = el('div', 'card'), cs = extCountries(), o = MA.official(agg()?.uf?.zz);
  const selIso = f.type === 'muni' ? EXT.byMun.get(f.id)?.iso : S.extIso;
  let wa = 0, wb = 0;
  for (const d of EXT.list) { const x = MA.official(munAgg(d.i)); if (x.has) { if (x.l > x.o) wa++; else if (x.o > x.l) wb++; } }
  card.append(el('h2', null, 'Exterior · por país'));
  card.append(el('div', 'sub', `${EXT.list.length} cidades em ${cs.length} países${o.has ? ` · ${fN(o.l + o.o)} votos válidos · ${short('A')} à frente em ${wa}, ${short('B')} em ${wb}` : ''}`));
  const shareCell = (ls) => {
    const td = el('td');
    if (!isNum(ls)) { td.textContent = '—'; return td; }
    const bar = el('span', 'xbar'), a = el('i'), b = el('i');
    a.style.width = ls + '%'; a.style.background = 'var(--A)'; b.style.flex = '1'; b.style.background = 'var(--B)';
    bar.append(a, b); td.append(bar, fPct(ls)); return td;
  };
  const tbl = el('table', 'tbl ext'), h = el('tr');
  for (const t of ['', 'Votos', `${short('A')} %`, 'Δ /100']) h.append(el('th', null, t));
  tbl.append(h);
  let selRow = null;
  for (const r of cs) {
    const open = r.iso === selIso;
    const tr = el('tr', 'click' + (open ? ' sel' : ''));
    tr.append(el('td', 'nm', `${open ? '▾' : '▸'} ${r.nome}${r.cities.length > 1 ? ` (${r.cities.length})` : ''}`), el('td', null, r.v ? fN(r.v) : '—'), shareCell(r.lshare), el('td', null, isNum(r.delta) ? fM(r.delta) : '—'));
    tr.onclick = () => {
      userAct(); S.extIso = open ? '' : r.iso;
      if (S.focus.type !== 'region') setFocus({ type: 'region', id: 'ZZ' }); else renderRight();
      if (!open) flyToCountry(r.iso);
    };
    tbl.append(tr);
    if (open) {
      selRow = tr;
      for (const d of [...r.cities].sort((x, y) => (munAgg(y.i)?.c || 0) - (munAgg(x.i)?.c || 0))) {
        const x = MA.official(munAgg(d.i)), cc = extCmp(d.i);
        const ct = el('tr', 'click city' + (f.type === 'muni' && f.id === d.i ? ' sel' : ''));
        ct.append(el('td', 'nm', d.nome), el('td', null, x.has ? fN(x.l + x.o) : '—'), shareCell(x.lshare), el('td', null, cc.has ? fM(cc.delta) : '—'));
        ct.onclick = (e) => { e.stopPropagation(); userAct(); setFocus({ type: 'muni', id: d.i }, true); };
        tbl.append(ct);
      }
    }
  }
  const wrap = el('div', 'sec-scroll'); wrap.append(tbl); card.append(wrap);
  if (selRow) requestAnimationFrame(() => { const top = selRow.offsetTop - wrap.offsetTop; if (top < wrap.scrollTop || top > wrap.scrollTop + wrap.clientHeight - 40) wrap.scrollTop = Math.max(0, top - 30); });
  const n = el('div', 'lg-note', 'Votos válidos no 2º turno. Δ margem: mesmas cidades no 1º turno, votos de margem por 100 eleitores aptos (só cidades totalmente apuradas).'); n.dataset.drop = '2';
  card.append(n);
  return card;
}
function tableCard(f) {
  const A = an(), req = reqDelta();
  const c2 = el('div', 'card');
  let rows, title;
  const ufRow = (u) => ({ key: u, name: u.toUpperCase(), T: A?.thermometer?.uf?.[u], pct: A?.status?.mix_uf?.[u]?.pct_apurado ?? MA.official(agg()?.uf?.[u]).pct, focus: { type: 'uf', id: u } });
  if (f.type === 'region' || f.type === 'uf') {
    const r = f.type === 'region' ? f.id : B.ufReg[B.ufIdx[f.id]];
    rows = B.ufOrder.filter((u) => B.ufReg[B.ufIdx[u]] === r).map(ufRow);
    title = `UFs · ${regName(r)}`;
  } else {
    rows = REG_ORDER.map((r) => ({ key: r, name: regName(r), T: A?.thermometer?.regiao?.[r], pct: A?.status?.mix_regiao?.[r]?.pct_apurado ?? A?.status?.region_pct_aptos?.[r], focus: { type: 'region', id: r } }));
    title = 'Por região';
  }
  c2.append(el('h2', null, title));
  const ts = el('div', 'sub', `✓ = acima do ritmo necessário (${fM(req)} ${U100}), nas mesmas zonas do 1º turno.`); ts.dataset.drop = '2'; c2.append(ts);
  const tbl = el('table', 'tbl'), thead = el('tr');
  for (const h of ['', 'Apurado', 'Δ margem /100', 'Δ abst. p.p.', 'Ritmo /100']) thead.append(el('th', null, h));
  tbl.append(thead);
  for (const row of rows) {
    const M = row.T?.best, thin = !M || !comparing() || !isNum(M.delta) || (row.T.coverage_aptos_pct ?? 0) < 0.5;
    const tr = el('tr', 'click' + (f.type === row.focus.type && f.id === row.focus.id ? ' sel' : ''));
    tr.append(el('td', null, row.name), el('td', null, fPct(row.pct, 0)), el('td', null, thin ? '—' : fM(M.delta)), el('td', null, thin ? '—' : fPP(M.d_abst_pp).replace(' p.p.', '')),
      el('td', null, thin || !isNum(req) ? 'aguardando' : (M.delta >= req ? '✓ ' : '✗ ') + fM(M.delta - req)));
    tr.onclick = () => { userAct(); setFocus(row.focus, true); };
    tbl.append(tr);
  }
  c2.append(tbl);
  return c2;
}
const RANKS = {
  lula: { label: () => `${short('A')} mais cresceu`, val: (c) => c.dMarginV, fmt: (c) => `${fN(c.dMarginV, true)} · ${fM(c.delta)}/100` },
  flavio: { label: () => `${short('B')} mais cresceu`, val: (c) => -c.dMarginV, fmt: (c) => `${fN(-c.dMarginV, true)} · ${fM(-c.delta)}/100` },
  abstcaiu: { label: () => 'Abstenção caiu', val: (c) => -c.dAbstV, fmt: (c) => `${fN(c.dAbstV, true)} ausentes · ${fPP(c.dAbst)}` },
  abstsubiu: { label: () => 'Abstenção subiu', val: (c) => c.dAbstV, fmt: (c) => `${fN(c.dAbstV, true)} ausentes · ${fPP(c.dAbst)}` },
};
function rankList(key, by, n = 8) {
  const out = [], R = RANKS[key];
  if (!comparing()) return out;
  if (by === 'muni') {
    const mz = an()?.mun; if (!mz) return out;
    for (let m = 0; m < B.M; m++) {
      if (B.mun.uf[m] === 'zz' || (S.ufFilter && B.mun.uf[m] !== S.ufFilter)) continue;
      const z = mz[String(B.mun.cd_mun[m])];
      if (!z || (z.src !== 'obs' && z.src !== 'est') || !(z.pct >= 20) || B.mun.a2[m] < 3000) continue;
      const c = MA.cmpFromZ(base1Mun(m), z); if (!c.has) continue;
      const v = R.val(c); if (!isNum(v)) continue;
      out.push({ v, c, name: munName(m), focus: { type: 'muni', id: m } });
    }
  } else {
    const H = B.h3[CELLS_RES], CA = CELL[CELLS_RES]; if (!H || !CA) return out;
    const ufF = S.ufFilter ? B.ufIdx[S.ufFilter] : -1;
    for (let c = 0; c < H.n; c++) {
      if (ufF >= 0 && H.uf[c] !== ufF) continue;
      const v0 = MA.cmpFromAgg(CA, c * K); if (!v0.has || v0.nCnt < 8) continue;
      const v = R.val(v0); if (!isNum(v)) continue;
      out.push({ v, c: v0, cell: c, focus: { type: 'cell', res: CELLS_RES, h: H.cells[c], idx: c } });
    }
  }
  out.sort((a, b) => b.v - a.v);
  const top = out.slice(0, n);
  if (by !== 'muni') { // nome do hexágono: município mais próximo do centro (só para os poucos exibidos)
    const H = B.h3[CELLS_RES];
    for (const r of top) {
      let best = -1, bd = Infinity; const la = H.lat[r.cell], ln = H.lng[r.cell];
      for (let m = 0; m < B.M; m++) { if (!isNum(B.mun.lat[m])) continue; const dd = (B.mun.lat[m] - la) ** 2 + (B.mun.lng[m] - ln) ** 2; if (dd < bd) { bd = dd; best = m; } }
      r.name = `Hexágono perto de ${best >= 0 ? munName(best) : '—'}`;
    }
  }
  return top;
}
function rankCard() {
  const c = el('div', 'card rank');
  c.append(el('h2', null, 'Destaques'));
  const tabs = el('div', 'tabs');
  for (const k of Object.keys(RANKS)) { const b = el('button', S.rankTab === k ? 'on' : '', RANKS[k].label()); b.onclick = () => { userAct(); S.rankTab = k; renderRight(); }; tabs.append(b); }
  const by = el('div', 'tabs');
  for (const [k, l] of [['muni', 'por município'], ['cell', `por hexágono (res ${CELLS_RES})`]]) { const b = el('button', S.rankBy === k ? 'on' : '', l); b.onclick = () => { userAct(); S.rankBy = k; renderRight(); }; by.append(b); }
  c.append(tabs, by);
  const list = rankList(S.rankTab, S.rankBy, 5);
  if (!list.length) { c.append(el('div', 'sub', comparing() ? 'Aguardando municípios com ≥20% apurado.' : 'Os destaques começam com 2% das seções apuradas.')); return c; }
  const tbl = el('table', 'tbl rank'); const h = el('tr'); h.append(el('th', null, ''), el('th', null, 'votos · /100 aptos')); tbl.append(h);
  for (const r of list) {
    const tr = el('tr', 'click'); tr.append(el('td', 'nm', r.name), el('td', null, RANKS[S.rankTab].fmt(r.c)));
    tr.onclick = () => { userAct(); setFocus(r.focus, true); }; tbl.append(tr);
  }
  c.append(tbl);
  return c;
}
function placeCard(p) {
  const c = el('div', 'card'), m = B.L.mun[p], z = B.L.zone[p];
  const kick = el('div', 'kicker', `${munName(m)} · Zona ${B.zon.zona[z]}`), h = el('h2', null, `Local de votação`), addr = el('div', 'sub', '');
  c.append(kick, h);
  placeTitle(p, (r) => { h.textContent = titleCase(r[0]); addr.textContent = `${titleCase(r[1])}${r[2] ? ' · ' + titleCase(r[2]) : ''} · local nº ${r[3]}`; });
  const go = el('button', 'linkbtn', '→ Ir para o local'); go.onclick = () => { userAct(); flyToFocus({ type: 'place', id: p }); }; c.append(go);
  const st = B.L.sec_start[p], n = B.L.nsec[p], sec = FEED?.sec, S2 = B.S;
  let aptos = 0, comp2 = 0, nbu = 0;
  for (let k = 0; k < n; k++) { const s = B.perm[st + k]; aptos += S2.a2[s]; if (sec && sec[s * 8] === 1) { comp2 += sec[s * 8 + 2]; nbu++; } }
  const kp = el('div', 'kpis');
  kp.append(tile('Seções', `${n} urnas`, ''), tile('Zona eleitoral', String(B.zon.zona[z]), ''), tile('Eleitores aptos', fInt(aptos), ''), tile('Votos 2T (com BU)', nbu ? fInt(comp2) : '—', `${nbu} de ${n} boletins`));
  c.append(kp, addr);
  const wrap = el('div', 'sec-scroll'), tbl = el('table', 'tbl'), hr = el('tr');
  for (const t of ['Seção', 'Status', 'Aptos', 'Comp. 2T', 'Abst. 1T→2T', `${short('A')} 1T→2T`, `${short('B')} 1T→2T`, 'Δ margem/100', 'Log']) hr.append(el('th', null, t));
  tbl.append(hr);
  const zs = ZA.state[z], zp = ZA.pct[z];
  const idxs = []; for (let k = 0; k < n; k++) idxs.push(B.perm[st + k]);
  idxs.sort((a, b) => S2.secao[a] - S2.secao[b]);
  for (const s of idxs) {
    const tr = el('tr'), a1 = S2.a1[s], c1 = S2.c1[s], l1 = S2.l1[s], o1 = S2.o1[s], a2 = S2.a2[s];
    const bu = sec && sec[s * 8] === 1;
    const chip = el('span', 'schip' + (bu ? ' bu' : zp >= 99.95 ? ' tot' : ''), bu ? 'boletim recebido' : zp >= 99.95 ? 'apurada' : zp > 0 ? `zona ${fPct(zp, 0)}` : 'aguardando');
    const td = el('td'); td.append(chip);
    const ab1 = a1 ? (1 - c1 / a1) * 100 : null;
    tr.append(el('td', null, String(S2.secao[s]).padStart(4, '0')), td, el('td', null, fInt(a2)));
    if (bu) {
      const A2 = sec[s * 8 + 1] || a2, C2 = sec[s * 8 + 2], L2 = sec[s * 8 + 3], O2 = sec[s * 8 + 4];
      const dm = 100 * ((L2 - O2) / A2 - (l1 - o1) / a1);
      tr.append(el('td', null, fInt(C2)), el('td', null, `${nf1.format(ab1)}→${nf1.format((1 - C2 / A2) * 100)}%`), el('td', null, `${l1}→${L2}`), el('td', null, `${o1}→${O2}`), el('td', null, fM(dm)));
    } else if ((zs === 1 || zs === 2) && a1 > 0) {
      const pl = Math.max(0, l1 / a1 + ZA.dl[z] / 100), po = Math.max(0, o1 / a1 + ZA.do[z] / 100), pb = Math.max(0, S2.bn1[s] / a1 + ZA.dbn[z] / 100), pa = Math.max(0, (a1 - c1) / a1 + ZA.dabst[z] / 100);
      const sum = pl + po + pb + pa || 1, L2 = pl / sum * a2, O2 = po / sum * a2, C2 = (pl + po + pb) / sum * a2;
      const dm = 100 * ((L2 - O2) / a2 - (l1 - o1) / a1);
      const it = (t) => { const e = el('td', 'est', t); return e; };
      tr.append(it('~' + fInt(Math.round(C2))), it(`${nf1.format(ab1)}→~${nf1.format(pa / sum * 100)}%`), it(`${l1}→~${Math.round(L2)}`), it(`${o1}→~${Math.round(O2)}`), it(fM(dm)));
    } else tr.append(el('td', null, '—'), el('td', null, `${isNum(ab1) ? nf1.format(ab1) : '—'}→…`), el('td', null, `${l1}→…`), el('td', null, `${o1}→…`), el('td', null, '—'));
    const lb = el('button', 'logbtn', 'ver log'); lb.title = 'Log da urna: horário de cada voto, abertura e encerramento (1º e 2º turno)';
    lb.onclick = () => { userAct(); openSecLog(s, p); };
    const tdl = el('td'); tdl.append(lb); tr.append(tdl);
    tbl.append(tr);
  }
  wrap.append(tbl); c.append(wrap);
  c.append(el('div', 'lg-note', '“boletim recebido” = observado na urna. “apurada”/“zona N%” = zona já totalizada (toda ou em parte) no agregado oficial, boletim ainda não chegou. Em itálico com ~ = estimado pelo swing da zona sobre o 1º turno da seção.'));
  return c;
}

/** a regra do roteiro (cap. 5): sem conclusão abaixo de 95% de cobertura; senão, a variante sai do pior caso */
function prConclusao(c) {
  if (!(c.pct_cobertura_logs >= 95)) return { k: 'parcial', t: `Logs ainda em processamento: ${fPct(c.pct_cobertura_logs, 1)} dos votos conferidos. Sem conclusão até 95%.` };
  if (c.margem_pior_caso_conservador > 0) return { k: 'A', t: `Nem no pior caso: tirando de ${short('A')} todo voto dado depois das 17h, ${short('A')} venceria por ${fInt(c.pior_caso_remove.margem)}. Os votos depois das 17h são ${fPct(c.pct_tardios_sobre_margem, 2)} da diferença; não decidiram a eleição.` };
  if (c.proporcional.margem > 0) return { k: 'B1', t: `No pior caso, os votos tardios superam a diferença. No padrão de cada urna, ${short('A')} venceria por ${fInt(c.proporcional.margem)}; para virar, precisaria de ${fPct(c.pct_lula_tardio_para_virar, 1)} desses votos.` };
  return { k: 'B2', t: 'No padrão de cada urna, os votos dados depois das 17h somam mais que a diferença final: os dados não confirmam a avaliação do TSE.' };
}
/** painel direito nos modos "Votos após 17h": régua, curva 1T × 2T, testes e fatos */
function prCard() {
  const card = el('div', 'card pr-card'), P = prorrog();
  card.append(el('div', 'kicker', 'Prorrogação · logs de urna'), el('h2', null, 'A prorrogação mudou o resultado?'));
  if (!P) { card.append(el('div', 'sub', 'Carregando os logs de urna…')); return card; }
  const R = P.R, c = R.cenarios, u1 = c.mesma_urna_1t || {};
  card.append(el('div', 'sub', `Hora de cada voto, de ${fInt(c.secoes_com_log)} urnas (${fPct(c.pct_cobertura_logs, 1)} dos votos). Voto é secreto: o log diz quando, não em quem.`));
  const f = S.focus, A = f.type === 'muni' ? P.mun : f.type === 'uf' ? P.uf : null, i = f.type === 'muni' ? f.id : f.type === 'uf' ? B.ufIdx[f.id] : -1;
  if (A && i >= 0) {
    const nm = f.type === 'muni' ? munName(f.id) : ufName(f.id), fb = el('div', 'pr-focus');
    fb.append(el('div', 'kicker', `Em foco · ${nm}`));
    const r = el('div', 'sub'); r.append(`2º turno: `, el('b', null, `${fInt(A.t2[i])} votos depois das 17h`), ` de ${fInt(A.n2[i])} (${fPct(A.n2[i] ? 100 * A.t2[i] / A.n2[i] : 0, 2)}) · ${fInt(A.a2[i])} seções abertas após 17h10 · 1º turno: ${fInt(A.t1[i])} (${fPct(A.n1[i] ? 100 * A.t1[i] / A.n1[i] : 0, 1)})`);
    fb.append(r); card.append(fb);
  }
  card.append(reguaBars(R, { aName: short('A') }));
  const ch = curveChart(R, { w: 520, h: 170, t0: 15 * 60 + 30, t1: 19 * 60 + 30, step: 2 }); ch.style.marginTop = '.5rem'; card.append(ch);
  const tb = el('table', 'tbl pr-tests'), row = (k, v, sub) => { const tr = el('tr'), td = el('td'); td.append(el('div', null, k)); if (sub) td.append(el('div', 'lg-note', sub)); tr.append(td, el('td', null, v)); tb.append(tr); };
  row('Votos depois das 17h', `${fInt(c.tardios)} (${fPct(c.pct_votos_apos_17h, 3)})`, `${fPct(100 * c.tardios_17h_17h05 / Math.max(1, c.tardios), 0)} entre 17h00 e 17h05 (fila) · depois das 18h: ${fInt(c.tardios_18h)}`);
  row('No 1º turno, mesmas urnas', `${fInt(u1.tardios_1t)} (${fPct(u1.pct_tardios_1t, 1)})`, `${nf1.format(u1.razao_1t_2t)}× o do 2º turno, sem prorrogação`);
  row(`Pior caso: tirar de ${short('A')} todo voto tardio`, `${short('A')} +${fInt(c.pior_caso_remove.margem)}`, 'urna a urna, até o total de votos dele na urna');
  row('No padrão de cada urna', `${c.proporcional.efeito_liquido_lula >= 0 ? short('A') : short('B')} +${fInt(Math.abs(c.proporcional.efeito_liquido_lula))}`, `efeito líquido dos votos tardios; sem eles, ${short('A')} +${fInt(c.proporcional.margem)}`);
  if (isNum(c.pct_lula_tardio_para_virar)) row('Para virar o resultado', c.pct_lula_tardio_para_virar > 100 ? 'impossível' : fPct(c.pct_lula_tardio_para_virar, 1), `fatia dos votos tardios que ${short('A')} precisaria ter tido`);
  card.append(tb);
  const cc = prConclusao(c), cn = el('div', `pr-concl k-${cc.k}`, cc.t); card.append(cn);
  const fx = R.fatos;
  if (fx?.tse) {
    const t = el('div', 'lg-note'); t.dataset.drop = '3';
    t.append(`Conferência: o TSE informou ${fInt(fx.tse.votos_apos_17h)} votos depois das 17h e ${fInt(fx.tse.secoes_abertas_apos_17h10)} seções abertas após 17h10; para o presidente do TSE, “${fx.tse.avaliacao}”. Fonte: ${fx.tse.fonte}${fx.tse.confirmado ? '' : ' (a confirmar)'}.`);
    card.append(t);
  }
  if (fx?.linha_do_tempo?.length) {
    const tl = el('div', 'pr-tl'); tl.dataset.drop = '2';
    tl.append(el('div', 'kicker', 'O que aconteceu em 25/10'));
    for (const e of fx.linha_do_tempo) { const r = el('div', 'r'); r.append(el('b', null, e.hora), el('span', null, e.texto)); tl.append(r); }
    tl.append(el('div', 'lg-note', `Fontes: ${[...new Set(fx.linha_do_tempo.map((e) => e.fonte))].join('; ')} (imprensa; a confirmar).`));
    card.append(tl);
  }
  return card;
}
/** abre o log da urna da seção s (índice da base); p = local, para o título */
function openSecLog(s, p = B.S.place[s]) {
  const m = B.S.mun[s], z = B.S.zone[s], u = B.mun.uf[m], cd = B.mun.cd_mun[m], zn = B.zon.zona[z], sn = B.S.secao[s];
  const info = { uf: u, cdMun: cd, zona: zn, secao: sn, isExt: u === 'zz', title: `Seção ${sn} · Zona ${zn} · ${munName(m)}`, sub: '',
    onClose: () => { S.logSec = -1; writeHash(); } };
  S.logSec = s; writeHash();
  openUrnaLog(info);
  if (p >= 0) placeTitle(p, (r) => { const e = document.querySelector('.ul-head .sub'); if (e) e.textContent = `${titleCase(r[0])} · ${titleCase(r[1])}`; });
}

// ---------------- barra superior ----------------
function renderTop() {
  const br = agg()?.br, off = MA.official(br), A = an();
  const pl = $('#placar'); pl.replaceChildren();
  const row = el('div', 'row');
  const a = el('span'); a.append(keyEl('A'), `${short('A')} ${off.has ? fPct(off.lshare) : '—'}`);
  const b = el('span'); b.append(`${off.has ? fPct(100 - off.lshare) : '—'} ${short('B')}`, keyEl('B'));
  row.append(a, b);
  const duo = el('div', 'duo'), ia = el('i'), ib = el('i');
  ia.style.width = (off.has ? off.lshare : 50) + '%'; ia.style.background = off.has ? 'var(--A)' : 'var(--grid)'; ib.style.flex = '1'; ib.style.background = off.has ? 'var(--B)' : 'var(--grid)'; duo.append(ia, ib);
  pl.append(row, duo, el('div', 'lab', off.has ? `placar oficial bruto · ${fN(off.l)} × ${fN(off.o)} votos · ${fPct(off.pct)} das seções` : 'placar oficial bruto · nenhuma seção totalizada'));
  const cap = A?.status?.mix_caption;
  if (cap) pl.append(el('div', 'mixcap', cap));
  // progresso + mix por região
  const pr = $('#progress'); pr.replaceChildren();
  const bar = (label, v, cls, title) => { const d = el('div', 'pbar'), t = el('div', 'track'), f = el('div', 'fill ' + (cls || '')); f.style.width = (Math.max(0, Math.min(1, v || 0)) * 100) + '%'; t.append(f); d.append(el('span', null, label), t, el('b', 'tab', fPct((v || 0) * 100))); if (title) d.title = title; return d; };
  const cov = FEED?.manifest?.coverage || {};
  pr.append(bar('Seções totalizadas (oficial)', off.ts ? off.st / off.ts : 0), bar('Com boletim (urna a urna)', cov.secoes_total ? (cov.secoes_com_bu || 0) / cov.secoes_total : 0, 'bu'));
  const mixr = A?.status?.mix_regiao;
  if (mixr && REG_ORDER.some((r) => (mixr[r]?.pct_apurado || 0) > 0)) {
    const grid = el('div', 'mixgrid');
    for (const r of REG_ORDER) {
      const x = mixr[r]; if (!x) continue;
      const cell = el('div', 'mixc');
      const over = isNum(x.sobre_rep) ? (x.sobre_rep >= 1.25 ? ' over' : x.sobre_rep <= 0.8 ? ' under' : '') : '';
      cell.className = 'mixc' + over;
      cell.title = `${regName(r)}: ${fPct(x.pct_apurado)} apurado · ${fPct(x.share_apurado, 0)} do apurado × ${fPct(x.share_eleitorado, 0)} do eleitorado`;
      const t = el('div', 'mt'); t.append(el('b', null, r), el('span', null, fPct(x.pct_apurado, 0)));
      const bars = el('div', 'mb'), b1 = el('i', 'ap'), b2 = el('i', 'el');
      const mx = Math.max(x.share_apurado || 0, x.share_eleitorado || 0, 1);
      b1.style.width = (100 * (x.share_apurado || 0) / mx) + '%'; b2.style.width = (100 * (x.share_eleitorado || 0) / mx) + '%';
      bars.append(b1, b2);
      cell.append(t, bars, el('div', 'ms', `${fPct(x.share_apurado, 0)} × ${fPct(x.share_eleitorado, 0)}`));
      grid.append(cell);
    }
    grid.title = 'Mix do apurado por região: % apurado · fatia do apurado × fatia do eleitorado (barra clara = apurado, escura = eleitorado)';
    pr.append(grid);
  }
}
function renderClock() { $('#clock').textContent = `${new Date().toLocaleDateString('pt-BR', { timeZone: 'America/Sao_Paulo', weekday: 'short', day: '2-digit', month: '2-digit' })} · ${hhmm(new Date())} BRT`; }
function renderFresh() {
  const fr = freshness(FEED);
  renderBadges($('#fresh'), fr, el);
  renderBanners($('#banners'), fr, el);
}
function legendTexts(mode) {
  const req = reqDelta();
  return {
    ritmo: [`Δ margem sobre o 1º turno − necessário (${fM(req)} ${U100})`, `Acima: ${short('A')} melhora mais que o necessário para empatar. Mesmas zonas/seções do 1º turno.`, ['abaixo do ritmo', 'acima do ritmo']],
    margem: ['Margem no 2º turno (% dos válidos)', 'Placar bruto das seções apuradas — não compara com o 1º turno.', [`${short('B')} à frente`, `${short('A')} à frente`]],
    dmargem: [`Δ margem ${short('A')} − ${short('B')} vs 1º turno (votos ${U100})`, 'Mesmas seções nos dois turnos (observado na urna ou estimado pela zona).', [`melhorou p/ ${short('B')}`, `melhorou p/ ${short('A')}`]],
    dabst: ['Δ abstenção vs 1º turno (p.p. do eleitorado)', 'Verde-azulado: mais gente votou. Magenta: mais gente faltou.', ['abstenção subiu', 'abstenção caiu']],
    dbn: ['Δ brancos + nulos vs 1º turno (p.p. do comparecimento)', 'Verde-azulado: menos brancos/nulos.', ['subiu', 'caiu']],
    ganhoA: [`Votos ganhos por ${short('A')} sobre o 1º turno`, 'p.p. do eleitorado apto. Sobe mecanicamente com a saída dos outros candidatos.', ['0', '12+ p.p.']],
    ganhoB: [`Votos ganhos por ${short('B')} sobre o 1º turno`, 'p.p. do eleitorado apto. Sobe mecanicamente com a saída dos outros candidatos.', ['0', '12+ p.p.']],
    abst2: ['Abstenção no 2º turno', '% do eleitorado apto que não votou.', ['10%', '35%+']],
    proj: ['Projeção do que falta apurar (por UF)', 'Margem projetada pelo analytics nos votos que faltam; mais opaco = mais seções por apurar.', [`favorece ${short('B')}`, `favorece ${short('A')}`]],
    cob: ['Cobertura da apuração', 'Município/UF: % totalizado (oficial). Hexágono/local: % com boletim. Sem cor = nada apurado ainda.', ['>0%', '100%']],
    margem1: ['Margem no 1º turno (% dos válidos)', `Base de comparação: ${short('A')} − ${short('B')} no 1º turno.`, [`${short('B')} à frente`, `${short('A')} à frente`]],
    tardio2: ['Votos depois das 17h · 2º turno (por 10 mil votos)', 'Logs de urna: hora em que cada voto foi computado (Brasília). Mesma escala do 1º turno.', ['nenhum', '1.000+']],
    tardio1: ['Votos depois das 17h · 1º turno (por 10 mil votos)', 'Mesma urna no 1º turno, sem prorrogação: quem estava na fila às 17h votou depois. Mesma escala do 2º turno.', ['nenhum', '1.000+']],
  }[mode.key];
}
function renderLegend() {
  const mode = MODE[S.mode], b = $('#legendBody'); b.replaceChildren();
  const [title, sub, ends] = legendTexts(mode);
  b.append(el('div', 'lg-title', title), el('div', 'lg-sub', sub));
  const bar = el('div', 'lg-bar'), R = RAMPS[mode.key];
  const sw = (c) => { const i = el('i'); i.style.background = rgbCss(c); bar.append(i); };
  const ticks = el('div', 'lg-ticks');
  const ft = (t) => nf1.format(t).replace(',0', '');
  if (mode.kind === 'div') {
    [...R.neg].reverse().forEach(sw); sw(R.mid); R.pos.forEach(sw);
    [...mode.th].reverse().forEach((t) => ticks.append(el('span', null, ft(t)))); ticks.append(el('span', null, '0')); mode.th.forEach((t) => ticks.append(el('span', null, ft(t))));
  } else { R.seq.forEach(sw); mode.th.forEach((t) => ticks.append(el('span', null, ft(t)))); }
  const e = el('div', 'lg-ends'); e.append(el('span', null, '◀ ' + ends[0]), el('span', null, ends[1] + ' ▶'));
  b.append(bar, ticks, e);
  const L = effLayer();
  if (mode.src === 'pr') {
    const c = prorrog()?.R?.cenarios;
    b.append(el('div', 'lg-note', c ? `Fonte: logs de urna do TSE (o log do 2º turno traz o 1º turno da mesma urna) · cobertura ${fPct(c.pct_cobertura_logs, 1)} dos votos${c.pct_cobertura_logs < 99.5 ? ' · parcial' : ''}. Cinza = nenhum voto depois das 17h; sem cor = sem log. Exterior fica de fora (hora local).` : 'Carregando os logs de urna…'));
    return;
  }
  b.append(el('div', 'lg-note', `${EXT?.list.length && extFocus() ? 'Exterior: cada círculo é uma cidade com seção eleitoral; área proporcional aos votos válidos. ' : ''}${L === 'muni' ? 'Municípios: agregado oficial e swing do analytics. ' : L === 'uf' ? 'UFs: termômetro do analytics. ' : 'Cor cheia = observado na urna (boletim); cor lavada = estimado pelo swing da zona. '}${S.fade && mode.src !== 'cov' && mode.src !== 'base' ? 'Mais transparente = menor parte apurada. ' : ''}Sem cor = ainda sem apuração.`));
}

// ---------------- Modo TV ----------------
function newSince(r) { const x = an()?.status?.mix_regiao?.[r]; const v = x?.pct_apurado ?? 0; return v - (S.tv.lastSeen[r] ?? 0); }
/** frase com ponto final único (aceita texto com ou sem ponto; vazio → '') */
const sent = (t) => { t = String(t ?? '').trim().replace(/[\s.;,·]+$/, ''); return t ? t + '. ' : ''; };
/** junta partes de texto rico e remove espaço sobrando no fim */
function tidy(parts) { const out = parts.filter((p) => p !== '' && p != null); const last = out[out.length - 1]; if (typeof last === 'string') out[out.length - 1] = last.replace(/\s+$/, ''); return out; }
const votoS = (v) => (isNum(v) && Math.abs(v) >= 1.995 ? 'votos' : 'voto');
// Toda frase do Modo TV sai de dados atuais; cláusulas condicionais só aparecem quando valem (fase/dado presente).
/** apuração encerrada: o Modo TV passa a contar a história (historia.js) em vez das vistas de acompanhamento */
let storyLoading = false;
function storyViews() {
  const V = historiaData(), P = prorrog();
  if (!V || !P) {
    if (!storyLoading) {
      storyLoading = true;
      Promise.allSettled([loadHistoria(), ensurePr()]).then((r) => {
        storyLoading = false;
        r.forEach((x) => { if (x.status === 'rejected') logErr('história', x.reason); });
        if (historiaData() && S.tv.on) { S.tv.views = []; S.tv.idx = -1; safe('tv', tvNext); }
      });
    }
    return null;
  }
  const cards = buildStory({ V, PR: P.R, an: an(), A: short('A'), Bn: short('B'), ufName });
  return cards.map((c, i) => ({ key: 'h-' + c.key, layer: c.map.layer, mode: c.map.mode, focus: c.map.focus, story: c, n: i, dur: c.dur,
    beats: (c.beats || []).map((b) => ({ ...b })), title: () => c.title, text: () => [c.text] }));
}
function buildTvViews() {
  if (phase() === 'final') { const sv = safe('história', storyViews); if (sv?.length) return sv; }
  const v = [], A = an(), req = reqDelta();
  if (!comparing()) {
    const b = A?.baseline_1t || B.meta.baseline_1t;
    v.push({ key: 'base1t', layer: 'auto', mode: 'margem1', focus: { type: 'br' }, title: () => 'Ponto de partida · 1º turno por município',
      text: () => { const u = untilFim(); return tidy([`No 1º turno: ${short('A')} `, { b: fN(b.l) }, ' × ', { b: fN(b.o) }, ` ${short('B')}. Para empatar, ${short('A')} precisa ganhar `, { b: `${fM(req, 2)} ${votoS(req)} de margem ${U100}` }, ' sobre o 1º turno. ', u ? `Resultados a partir das ${fimTxt()} (em ${u}).` : '']); } });
    if ((agg()?.br?.st || 0) > 0) v.push({ key: 'cob', layer: 'auto', mode: 'cob', focus: { type: 'br' }, title: () => 'O que já foi totalizado',
      text: () => { const o = MA.official(agg()?.br), mc = an()?.status?.mix_caption;
        return tidy([{ b: fPct(o.pct) }, ' das seções totalizadas. ', sent(mc), phase() === 'pre' ? 'A comparação com o 1º turno começa com 2% das seções.' : '']); } });
    return v;
  }
  v.push({ key: 'br-ritmo', layer: 'auto', mode: 'ritmo', focus: { type: 'br' }, title: () => `Brasil · ${short('A')} está no ritmo necessário?`,
    text: () => { const p = an()?.pace || {}, rq = isNum(p.delta_req) ? p.delta_req : req, bd = Array.isArray(p.delta_obs_band) && p.delta_obs_band.every(isNum) ? p.delta_obs_band : null;
      const head = [`Para empatar, ${short('A')} ${phase() === 'final' ? 'precisava' : 'precisa'} ganhar `, { b: `${fM(rq, 2)} ${votoS(rq)} de margem ${U100}` }, ' sobre o 1º turno'];
      if (!isNum(p.delta_obs)) return tidy([...head, '. O ritmo observado ainda não está disponível.']);
      return tidy([...head, '; nas seções apuradas, com cada estado pesando o seu eleitorado: ', { b: fM(p.delta_obs, 2) }, bd ? ` (faixa ${fM(bd[0], 2)} a ${fM(bd[1], 2)})` : '', '.']); } });
  v.push({ key: 'br-margem', layer: 'auto', mode: 'margem', focus: { type: 'br' }, title: () => 'Brasil · placar oficial por município',
    text: () => { const o = MA.official(agg()?.br), mc = an()?.status?.mix_caption;
      if (!o.has) return tidy(['Nenhuma seção totalizada no agregado oficial ainda.']);
      return tidy(['Com ', { b: fPct(o.pct, 0) }, ' das seções: ', { b: `${short('A')} ${fPct(o.lshare)}` }, ` × ${fPct(100 - o.lshare)} ${short('B')}. `, sent(mc)]); } });
  const regs = REG_ORDER.filter((r) => r !== 'ZZ' && (an()?.thermometer?.regiao?.[r]?.coverage_aptos_pct ?? 0) >= 0.5).map((r) => ({ r, d: newSince(r) })).sort((a, b) => b.d - a.d).slice(0, 2);
  for (const { r } of regs) {
    v.push({ key: 'reg-' + r, layer: 'auto', mode: 'dmargem', focus: { type: 'region', id: r }, title: () => `${regName(r)} · Δ margem vs 1º turno`,
      onShow: () => { S.tv.lastSeen[r] = an()?.status?.mix_regiao?.[r]?.pct_apurado ?? 0; },
      text: () => { const T = an()?.thermometer?.regiao?.[r], M = T?.best, pa = an()?.status?.mix_regiao?.[r]?.pct_apurado ?? T?.coverage_aptos_pct;
        const lead = isNum(pa) ? `${fPct(pa, 0)} apurado · ` : '';
        if (!M || !isNum(M.delta)) return tidy([`${lead}ainda sem zonas suficientes para comparar com o 1º turno.`]);
        return tidy([`${lead}nas mesmas zonas do 1º turno, margem de ${short('A')} `, { b: `${fM(M.delta)} ${U100}` },
          isNum(M.d_margin_votes) ? ` (${fN(M.d_margin_votes, true)} votos` : ' (', isNum(req) ? `${isNum(M.d_margin_votes) ? '; ' : ''}${M.delta >= req ? 'acima' : 'abaixo'} do necessário)` : ')',
          isNum(M.d_abst_pp) ? ' · abstenção ' : '', isNum(M.d_abst_pp) ? { b: fPP(M.d_abst_pp) } : '']); } });
  }
  if (MA.official(agg()?.uf?.zz).has && EXT?.list.length) v.push({ key: 'exterior', layer: 'auto', mode: 'margem', focus: { type: 'region', id: 'ZZ' }, title: () => 'Exterior · placar por cidade',
    onShow: () => { S.extIso = ''; },
    text: () => { const o = MA.official(agg()?.uf?.zz); let wa = 0, wb = 0;
      for (const d of EXT.list) { const x = MA.official(munAgg(d.i)); if (x.has) { if (x.l > x.o) wa++; else if (x.o > x.l) wb++; } }
      const top = extCountries().filter((r) => isNum(r.lshare)).slice(0, 3).map((r) => `${r.nome} (${r.lshare >= 50 ? short('A') : short('B')} ${fPct(Math.max(r.lshare, 100 - r.lshare), 0)})`);
      return tidy([`${short('A')} `, { b: fPct(o.lshare) }, ` × ${fPct(100 - o.lshare)} ${short('B')} entre `, { b: `${fN(o.l + o.o)} votos válidos` },
        ` em ${EXT.list.length} cidades. ${short('A')} à frente em ${wa}, ${short('B')} em ${wb}. `, top.length ? `Mais votos: ${top.join(', ')}.` : '']); } });
  const munLine = (f, body) => { const z = munZ(f.id), c = MA.cmpFromZ(base1Mun(f.id), z);
    if (!c.has) return tidy([{ b: munName(f.id) }, ': comparação com o 1º turno indisponível no momento.']);
    return tidy([{ b: munName(f.id) }, ': ', ...body(c), isNum(z?.pct) ? `, com ${fPct(z.pct, 0)} apurado.` : '.']); };
  const top = rankList('lula', 'muni', 1)[0];
  if (top && top.v > 0) v.push({ key: 'lula-cresceu', layer: 'auto', mode: 'dmargem', focus: top.focus, title: () => `Onde ${short('A')} mais ganhou margem`,
    text: () => munLine(top.focus, (c) => [`${short('A')} ganhou `, { b: `${fN(c.dMarginV)} votos de margem` }, ` em relação ao 1º turno (${fM(c.delta)} ${U100})`]) });
  const topB = rankList('flavio', 'muni', 1)[0];
  if (topB && topB.v > 0) v.push({ key: 'flavio-cresceu', layer: 'auto', mode: 'dmargem', focus: topB.focus, title: () => `Onde ${short('B')} mais ganhou margem`,
    text: () => munLine(topB.focus, (c) => [`${short('B')} ganhou `, { b: `${fN(-c.dMarginV)} votos de margem` }, ` em relação ao 1º turno (${fM(-c.delta)} ${U100})`]) });
  const ab = rankList('abstcaiu', 'muni', 1)[0];
  if (ab && ab.v > 0) v.push({ key: 'abst', layer: 'auto', mode: 'dabst', focus: ab.focus, title: () => 'Maior queda de abstenção',
    text: () => munLine(ab.focus, (c) => [{ b: `${fN(-c.dAbstV)} eleitores a mais` }, ` votaram do que no 1º turno (abstenção ${fPP(c.dAbst)})`]) });
  v.push({ key: 'br-ritmo-hex', layer: 'hex', mode: 'ritmo', focus: { type: 'br' }, title: () => 'Acima / abaixo do ritmo necessário · hexágonos',
    text: () => { const cv = FEED?.manifest?.coverage || {}, pb = cv.secoes_total ? 100 * (cv.secoes_com_bu || 0) / cv.secoes_total : null;
      return tidy(['Cada hexágono compara as mesmas seções nos dois turnos. ', isNum(pb) ? { b: fPct(pb, 0) } : '', isNum(pb) ? (pb >= 99.95 ? ' das seções têm boletim.' : ' das seções já têm boletim; as demais usam o swing da zona.') : 'As seções sem boletim usam o swing da zona.']); } });
  if (phase() !== 'final') v.push({ key: 'proj', layer: 'auto', mode: 'proj', focus: { type: 'br' }, title: () => 'O que falta apurar',
    text: () => { const p = an()?.projection || {}, st = an()?.status || {}, ph = phase();
      const bp = Array.isArray(p.band_pp_valid) && p.band_pp_valid.every(isNum) ? p.band_pp_valid : null;
      const rem = isNum(p.remaining_pct) ? p.remaining_pct : isNum(st.pct_aptos) ? 100 - st.pct_aptos : null;
      const parts = [isNum(rem) ? 'Faltam ' : '', isNum(rem) ? { b: fPct(rem, 0) } : '', isNum(rem) ? ' dos aptos. ' : ''];
      if (isNum(p.l_pct_valid)) parts.push(`${ph === 'trend' ? 'Tendência' : 'Projeção'}: ${short('A')} `, { b: fPct(p.l_pct_valid) }, ` × ${fPct(p.o_pct_valid ?? 100 - p.l_pct_valid)} ${short('B')}`,
        bp ? ` (margem ${fPP(p.margin_pp_valid)}; faixa ${fPP(bp[0])} a ${fPP(bp[1])}). ` : '. ');
      if (st.can_call && (st.call === 'L' || st.call === 'O')) parts.push(`${S.cand[st.call === 'L' ? 'A' : 'B'].name} vence na projeção.`);
      else parts.push(st.reason ? sent(`Sem vencedor projetado: ${st.reason}`) : 'Sem vencedor projetado.');
      return tidy(parts); } });
  return v;
}
function tvNext() {
  if (!S.tv.views.length || S.tv.idx >= S.tv.views.length - 1) { S.tv.views = buildTvViews(); S.tv.idx = -1; }
  if (!S.tv.views.length) return;
  S.tv.idx++;
  const v = S.tv.views[S.tv.idx];
  S.tv.cur = v; S.tv.viewStart = Date.now();
  v.beats?.forEach((b) => { b.done = false; });
  safe('tv onShow', () => v.onShow?.());
  S.mode = v.mode; S.layer = v.layer; $('#selMode').value = v.mode; syncSeg();
  S.focus = v.focus;
  renderTvCap(); // antes do voo: o cartão da história entra no enquadramento (flyToFocus desconta a altura dele)
  flyToFocus(v.focus, 3000);
  recolor(); renderLegend(); safe('direita', renderRight); renderBadge(); writeHash();
}
/** teclado da história: ← → navegam, espaço pausa, 0–5 pulam para o capítulo (só com a história na tela) */
function storyKey(e) {
  if (!S.tv.on || !S.tv.cur?.story || e.target.closest?.('input, select, textarea, dialog') || document.querySelector('.ul-bg')) return false;
  if (e.key === 'ArrowRight') tvGo(S.tv.idx + 1);
  else if (e.key === 'ArrowLeft') tvGo(S.tv.idx - 1);
  else if (e.key === ' ') { if (Date.now() < S.tv.pausedUntil) S.tv.pausedUntil = 0; else S.tv.pausedUntil = Date.now() + 10 * 60000; renderTvCap(); }
  else if (/^[0-5]$/.test(e.key)) { const i = S.tv.views.findIndex((v) => v.story?.ch === +e.key); if (i >= 0) tvGo(i); else return false; }
  else return false;
  e.preventDefault();
  return true;
}
/** vai para a vista i do Modo TV (navegação manual da história: ◀ ▶, capítulos, teclado) */
function tvGo(i) {
  if (!S.tv.on || !S.tv.views.length) return;
  S.tv.idx = Math.max(-1, Math.min(S.tv.views.length - 1, i) - 1);
  S.tv.pausedUntil = 0; $('#btnTv').classList.remove('paused');
  tvNext();
}
function renderStoryCap(cap, c) {
  cap.classList.add('story'); if (c.viz) cap.classList.add('palco'); // palco: cartão com gráfico, o mapa fica atrás, esmaecido
  const paused = Date.now() < S.tv.pausedUntil, views = S.tv.views;
  const nav = el('div', 'st-nav');
  CHAPTERS.forEach((nm, k) => {
    const first = views.findIndex((v) => v.story?.ch === k); if (first < 0) return;
    const b = el('button', 'st-ch' + (c.ch === k ? ' on' : ''), k === 0 ? '●' : `${k} · ${nm}`); b.title = nm;
    b.onclick = () => tvGo(first); nav.append(b);
  });
  const ctr = el('div', 'st-ctr');
  const bp = el('button', 'st-btn', '◀'); bp.title = 'anterior (←)'; bp.onclick = () => tvGo(S.tv.idx - 1);
  const bz = el('button', 'st-btn', paused ? '▶' : '❚❚'); bz.title = paused ? 'continuar (espaço)' : 'pausar (espaço)';
  bz.onclick = () => { if (Date.now() < S.tv.pausedUntil) { S.tv.pausedUntil = 0; $('#btnTv').classList.remove('paused'); } else S.tv.pausedUntil = Date.now() + 10 * 60000; renderTvCap(); };
  const bn = el('button', 'st-btn', '▶'); bn.title = 'próximo (→)'; bn.onclick = () => tvGo(S.tv.idx + 1);
  ctr.append(bp, bz, bn, el('span', 'st-n', `${S.tv.idx + 1}/${views.length}${paused ? ' · pausado' : ''}`));
  const top = el('div', 'st-top'); top.append(nav, ctr);
  const hero = el('div', 'st-hero'); hero.append(el('div', 'st-big', c.hero || ''), el('div', 'st-hsub', c.heroSub || ''));
  cap.append(top, el('div', 'k', CHAPTERS[c.ch] || ''), el('div', 'h', c.title), hero, el('div', 'p', c.text || ''));
  if (c.viz) { const vz = el('div', 'st-viz'); const node = safe('história gráfico', c.viz); if (node) vz.append(node); cap.append(vz); }
  if (c.foot) cap.append(el('div', 'st-foot', c.foot));
  const tm = el('div', 'tm'), bar = el('i'); bar.id = 'tvbar'; tm.append(bar); cap.append(tm);
}
function renderTvCap() {
  const cap = $('#tvcap');
  if (!S.tv.on || !S.tv.cur) { cap.hidden = true; $('#scrim')?.classList.remove('on'); return; }
  cap.hidden = false; cap.replaceChildren(); cap.classList.remove('story', 'palco');
  $('#scrim')?.classList.toggle('on', !!S.tv.cur.story?.viz);
  if (S.tv.cur.story) { renderStoryCap(cap, S.tv.cur.story); return; }
  const paused = Date.now() < S.tv.pausedUntil;
  const k = el('div', 'k'); k.append(el('span', null, `Modo TV · ${S.tv.idx + 1}/${S.tv.views.length}`), el('span', null, paused ? `pausado · retoma em ${Math.ceil((S.tv.pausedUntil - Date.now()) / 1000)} s` : ''));
  const p = el('div', 'p'); p.append(rich(safe('tv texto', () => S.tv.cur.text()) || ['—']));
  const tm = el('div', 'tm'), bar = el('i'); bar.id = 'tvbar'; tm.append(bar);
  cap.append(k, el('div', 'h', S.tv.cur.title()), p, tm);
}
function userAct() {
  S.lastUserAt = Date.now();
  if (!S.tv.on) return;
  S.tv.pausedUntil = Date.now() + 60000;
  $('#btnTv').classList.add('paused');
}
function tvLoop() {
  const btn = $('#btnTv');
  if (!S.tv.ready) return; // só depois do boot (restauração da hash)
  if (!S.tv.on) { btn.className = 'chip tv'; btn.textContent = '▶ Modo TV'; return; }
  const now = Date.now(), paused = now < S.tv.pausedUntil;
  btn.className = 'chip tv on' + (paused ? ' paused' : '');
  btn.textContent = paused ? `❚❚ TV · ${Math.ceil((S.tv.pausedUntil - now) / 1000)} s` : '▶ Modo TV';
  if (paused) { S.tv.viewStart = now; const c = $('#tvcap .k span:last-child'); if (c) c.textContent = `pausado · retoma em ${Math.ceil((S.tv.pausedUntil - now) / 1000)} s`; return; }
  const b = $('#tvbar');
  const frac = (now - S.tv.viewStart) / ((S.tv.cur?.dur || TV_SECONDS) * 1000);
  if (b) b.style.width = Math.min(100, frac * 100) + '%';
  for (const bt of S.tv.cur?.beats || []) { // "batida": troca a cor no meio do cartão, sem voo (ex.: 1º → 2º turno na mesma moldura)
    if (bt.done || frac < bt.at) continue;
    bt.done = true; S.mode = bt.mode; $('#selMode').value = bt.mode;
    recolor(); renderLegend(); safe('direita', renderRight); writeHash();
  }
  if (frac >= 1 || !S.tv.cur) tvNext();
}

// ---------------- hash (estado sobrevive à recarga) ----------------
let hashTimer = null;
function writeHash() {
  if (hashTimer) return;
  hashTimer = setTimeout(() => {
    hashTimer = null;
    const p = new URLSearchParams();
    p.set('tv', S.tv.on ? '1' : '0'); p.set('mode', S.mode); p.set('layer', S.layer);
    if (S.ufFilter) p.set('uf', S.ufFilter);
    const f = S.focus;
    if (f.type === 'region' || f.type === 'uf' || f.type === 'muni' || f.type === 'place') p.set('f', `${f.type}:${f.id}`);
    if (S.tv.on) { p.set('tvi', String(Math.max(0, S.tv.idx))); }
    if (S.logSec >= 0) p.set('log', String(S.logSec));
    if (map) { const c = map.getCenter(); p.set('z', map.getZoom().toFixed(2)); p.set('c', `${c.lng.toFixed(4)},${c.lat.toFixed(4)}`); }
    history.replaceState(null, '', `${location.pathname}${location.search}#${p.toString()}`);
  }, 400);
}
function readHash() {
  const p = new URLSearchParams(location.hash.slice(1));
  if (!p.toString()) return null;
  const r = {};
  if (p.has('tv')) S.tv.on = p.get('tv') === '1';
  if (p.get('mode') && MODE[p.get('mode')]) S.mode = p.get('mode');
  if (['auto', 'uf', 'muni', 'hex', 'locais'].includes(p.get('layer'))) S.layer = p.get('layer');
  if (p.get('uf') && B.ufIdx[p.get('uf')] != null) S.ufFilter = p.get('uf');
  const f = p.get('f');
  if (f) {
    const [t, id] = f.split(':');
    if (t === 'region' && B.meta.regions[id]) S.focus = { type: t, id };
    else if (t === 'uf' && B.ufIdx[id] != null) S.focus = { type: t, id };
    else if ((t === 'muni' && +id < B.M) || (t === 'place' && +id < B.P)) S.focus = { type: t, id: +id };
  }
  if (p.has('tvi')) r.tvi = Math.max(0, +p.get('tvi') || 0);
  if (p.has('log') && +p.get('log') >= 0 && +p.get('log') < B.N) r.log = +p.get('log');
  if (p.get('c') && p.get('z')) { const [lng, lat] = p.get('c').split(',').map(Number); const z = Number(p.get('z')); if ([lng, lat, z].every(isFinite)) r.view = { c: [lng, lat], z }; }
  return r;
}

// ---------------- recarga periódica ----------------
let reloading = false;
async function serverHealthy() {
  try {
    const [a, b] = await Promise.all([fetch(`index.html?t=${Date.now()}`, { method: 'HEAD', cache: 'no-store' }), fetch(`data/base/meta.json?t=${Date.now()}`, { method: 'HEAD', cache: 'no-store' })]);
    return a.ok && b.ok;
  } catch { return false; }
}
async function scheduleReload(delay = 0) {
  if (reloading) return;
  reloading = true;
  await new Promise((r) => setTimeout(r, delay));
  if (!(await serverHealthy())) { console.warn('[tela] servidor local indisponível; adiando recarga'); reloading = false; return; }
  writeHash(); clearTimeout(hashTimer); hashTimer = null;
  const p = new URLSearchParams(location.hash.slice(1)); p.set('tvi', String(Math.max(0, S.tv.idx))); p.set('tv', S.tv.on ? '1' : '0');
  if (map) { const c = map.getCenter(); p.set('z', map.getZoom().toFixed(2)); p.set('c', `${c.lng.toFixed(4)},${c.lat.toFixed(4)}`); }
  history.replaceState(null, '', `${location.pathname}${location.search}#${p.toString()}`);
  location.reload();
}
function reloadWatch() {
  if (Date.now() - S.bootAt > RELOAD_MS && Date.now() - S.lastUserAt > 90000) scheduleReload(0);
}

// ---------------- controles ----------------
function syncSeg() { document.querySelectorAll('#segLayer button').forEach((b) => b.classList.toggle('on', b.dataset.v === S.layer)); }
function wireControls() {
  const sel = $('#selMode');
  for (const m of MODES) { const o = el('option', null, m.key === 'ganhoA' ? `Ganho de votos de ${short('A')}` : m.key === 'ganhoB' ? `Ganho de votos de ${short('B')}` : m.label); o.value = m.key; sel.append(o); }
  sel.value = S.mode;
  sel.onchange = () => { userAct(); S.mode = sel.value; recolor(); renderLegend(); renderBadge(); writeHash(); };
  document.querySelectorAll('#segLayer button').forEach((b) => (b.onclick = () => { userAct(); S.layer = b.dataset.v; syncSeg(); updateLayers(); renderBadge(); renderLegend(); writeHash(); }));
  syncSeg();
  const uf = $('#selUf');
  for (const u of B.ufOrder) { if (u === 'zz') continue; const o = el('option', null, `${u.toUpperCase()} · ${ufName(u)}`); o.value = u; uf.append(o); }
  uf.value = S.ufFilter;
  uf.onchange = () => { userAct(); S.ufFilter = uf.value; recolor(); setFocus(uf.value ? { type: 'uf', id: uf.value } : { type: 'br' }, true); };
  $('#btnTv').onclick = () => {
    if (S.tv.on && Date.now() < S.tv.pausedUntil) { S.tv.pausedUntil = 0; tvNext(); return; }
    S.tv.on = !S.tv.on; S.tv.pausedUntil = 0;
    if (S.tv.on) tvNext(); else { S.tv.cur = null; renderTvCap(); }
    tvLoop(); writeHash();
  };
  let lastMove = null;
  window.addEventListener('pointerdown', (e) => { if (!e.target.closest('#btnTv, #tvcap.story .st-top')) userAct(); }, true);
  window.addEventListener('wheel', userAct, { passive: true, capture: true });
  window.addEventListener('keydown', (e) => { if (!storyKey(e)) userAct(); }, true);
  window.addEventListener('pointermove', (e) => { if (lastMove && Math.hypot(e.clientX - lastMove[0], e.clientY - lastMove[1]) > 6) userAct(); lastMove = [e.clientX, e.clientY]; }, true);
  wireSearch();
  const dlg = $('#dlgSettings');
  $('#btnSettings').onclick = () => {
    $('#cAname').value = S.cand.A.name; $('#cAnum').value = S.cand.A.num; $('#cAcol').value = S.cand.A.color;
    $('#cBname').value = S.cand.B.name; $('#cBnum').value = S.cand.B.num; $('#cBcol').value = S.cand.B.color;
    $('#chkFade').checked = S.fade; $('#selTheme').value = S.theme; $('#selResOff').value = String(S.resOff);
    $('#liveInfo').textContent = `Pasta ao vivo: ${FEED.base} · base ${B.meta.build_id} (${B.meta.generated_at})`;
    dlg.showModal();
  };
  document.querySelectorAll('.presets button').forEach((b) => (b.onclick = () => { const p = PRESETS[b.dataset.p][S.theme]; $('#cAcol').value = p[0]; $('#cBcol').value = p[1]; }));
  dlg.addEventListener('close', () => {
    const nm = (v, d) => (v || '').trim() || d;
    S.cand.A = { name: nm($('#cAname').value, 'Candidato A'), short: nm($('#cAname').value, 'A').split(' ')[0], num: $('#cAnum').value, color: $('#cAcol').value };
    S.cand.B = { name: nm($('#cBname').value, 'Candidato B'), short: nm($('#cBname').value, 'B').split(' ')[0], num: $('#cBnum').value, color: $('#cBcol').value };
    S.fade = $('#chkFade').checked; S.resOff = +$('#selResOff').value;
    const th = $('#selTheme').value;
    if (th !== S.theme) { S.theme = th; document.documentElement.dataset.theme = th; map.setStyle(STYLE[th]); labelKey = ''; }
    savePrefs(S); buildRamps();
    [...$('#selMode').options].forEach((o) => { if (o.value === 'ganhoA') o.textContent = `Ganho de votos de ${short('A')}`; if (o.value === 'ganhoB') o.textContent = `Ganho de votos de ${short('B')}`; });
    onZoom().then(() => { recolor(); renderAll(); });
  });
  window.addEventListener('resize', () => safe('esquerda', renderLeft));
}
function wireSearch() {
  const inp = $('#search'), res = $('#searchRes');
  const norm = (s) => String(s).normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();
  const munNorm = B.mun.nome.map(norm);
  inp.oninput = () => safe('busca', () => {
    const q = norm(inp.value.trim()); res.replaceChildren();
    if (q.length < 2) { res.hidden = true; return; }
    const items = [];
    const zm = q.match(/\b(?:z|zona)\s*(\d+)/), sm = q.match(/\b(?:s|secao|seção)\s*(\d+)/), um = q.match(/\b([a-z]{2})\b(?!.*\b[a-z]{2}\b)/);
    const ufq = um && B.ufIdx[um[1]] != null && um[1] !== 'zz' ? um[1] : null;
    if (zm) {
      const z = +zm[1];
      for (let i = 0; i < B.Z && items.length < 10; i++) {
        if (B.zon.zona[i] !== z) continue;
        const u = B.ufOrder[B.zon.uf[i]]; if (ufq && u !== ufq) continue;
        const m = B.zon.mun[i];
        if (sm) {
          const sn = +sm[1], st = B.zon.sec_start[i];
          for (let k = 0; k < B.zon.n[i]; k++) {
            const s = st + k;
            if (B.S.secao[s] === sn) { const p = B.S.place[s]; items.push({ t: `Seção ${sn} · Zona ${z} · ${u.toUpperCase()}`, s: `${munName(m)}${p < 0 ? ' · sem coordenada própria' : ''} · abre o log da urna`, f: p >= 0 ? { type: 'place', id: p } : { type: 'muni', id: m }, sec: s }); }
          }
        } else items.push({ t: `Zona ${z} · ${munName(m)}`, s: `${B.zon.n[i]} seções · ${fN(B.zon.a2[i])} aptos`, f: { type: 'muni', id: m } });
      }
    } else {
      for (const u of B.ufOrder) if (u !== 'zz' && (norm(ufName(u)).startsWith(q) || u === q)) items.push({ t: `${ufName(u)} (${u.toUpperCase()})`, s: 'Unidade da federação', f: { type: 'uf', id: u } });
      for (const r of REG_ORDER) if (norm(regName(r)).startsWith(q)) items.push({ t: regName(r), s: 'Região', f: { type: 'region', id: r } });
      for (const [iso, nm] of Object.entries(PAISES)) if (norm(nm).startsWith(q) && EXT?.list.some((d) => d.iso === iso)) items.push({ t: nm, s: 'País · eleitores no exterior', f: { type: 'region', id: 'ZZ' }, iso });
      const qq = ufq ? q.replace(new RegExp(`\\s*\\b${ufq}\\b\\s*`), '').trim() : q;
      for (let i = 0; i < B.M && items.length < 12; i++) if ((munNorm[i].startsWith(qq) || (EXT?.byMun.has(i) && norm(EXT.byMun.get(i).nome).startsWith(qq))) && qq.length >= 2 && (!ufq || B.mun.uf[i] === ufq)) items.push({ t: munName(i), s: isExt(i) ? 'Cidade no exterior' : B.mun.capital[i] ? 'Capital' : 'Município', f: { type: 'muni', id: i } });
    }
    if (!items.length) items.push({ t: 'Nada encontrado', s: 'Tente “Recife”, “zona 12 SP” ou “zona 12 seção 40 SP”', f: null });
    for (const it of items.slice(0, 12)) {
      const b = el('button'); b.append(el('div', null, it.t), el('small', null, it.s));
      b.onclick = () => { if (it.f) { userAct(); if (it.iso) { S.extIso = it.iso; setFocus(it.f); flyToCountry(it.iso); } else setFocus(it.f, true); if (it.sec != null) openSecLog(it.sec); } res.hidden = true; inp.value = ''; };
      res.append(b);
    }
    res.hidden = false;
  });
  inp.onblur = () => setTimeout(() => (res.hidden = true), 200);
}

function renderAll() {
  safe('topo', renderTop); safe('esquerda', renderLeft); safe('direita', renderRight); safe('legenda', renderLegend);
  safe('tv', renderTvCap); safe('badge', renderBadge); safe('frescor', renderFresh);
}

// ---------------- dados novos ----------------
let lastPhase = 'none'; // o roteiro inicial do Modo TV é montado antes do 1º analytics (fase 'none')
function onLive(ch) {
  if (ch.analytics) mapDirty.zones = true;
  const ph = phase();
  if (ph !== lastPhase) { // mudou de fase: o roteiro do Modo TV muda (pré → comparação → final)
    lastPhase = ph;
    if (S.tv.on && S.tv.cur) { S.tv.views = []; S.tv.idx = -1; if (Date.now() >= S.tv.pausedUntil) safe('tv', tvNext); }
    if (MODE[S.mode]?.src === 'base' && !S.tv.on && ph !== 'pre' && ph !== 'none') { S.mode = 'ritmo'; $('#selMode').value = S.mode; }
  }
  if (ch.analytics || ch.sections) heavyUpdate(true);
  else if (ch.agg) { // municípios/UF usam agg diretamente: recolorir também respeita o intervalo
    heavyUpdate(true);
  }
  if (ch.analytics || ch.agg || ch.manifest) { safe('topo', renderTop); }
  if (ch.analytics || ch.agg || ch.sections) { safe('esquerda', renderLeft); if (Date.now() - S.lastUserAt > 3000) safe('direita', renderRight); safe('tv', renderTvCap); }
  safe('frescor', renderFresh);
}

// ---------------- boot ----------------
async function boot() {
  const lo = $('#loading');
  const meta = await loadMeta();
  lo.textContent = `Carregando base: ${fInt(meta.counts.secoes)} seções, ${fInt(meta.counts.locais)} locais…`;
  B = await loadBase(meta);
  initExterior();
  ZA = MA.makeZones(B.Z);
  PLACE = new Float64Array(B.P * K);
  PLACES_DATA = { length: B.P };
  document.documentElement.dataset.theme = S.theme;
  buildRamps();
  const restored = readHash();
  wireControls();
  let gotFirst; const firstData = new Promise((r) => { gotFirst = r; });
  FEED = new LiveFeed({ base: liveBaseFromUrl(), wantSections: true, nRows: B.N, sha: meta.index.sha256, interval: 10000, onUpdate: (ch) => { onLive(ch); gotFirst(); } });
  FEED.start();
  initMap(restored?.view);
  CELLS_RES = resForZoom(restored?.view?.z ?? 4);
  await ensureRes(CELLS_RES).catch((e) => console.error('[tela] H3', e));
  recolor(); renderAll();
  lo.remove();
  map.once('idle', async () => {
    await Promise.race([firstData, new Promise((r) => setTimeout(r, 8000))]); // roteiro do Modo TV depende da fase
    for (let k = 0; k < 40 && !an(); k++) await new Promise((r) => setTimeout(r, 200)); // a fase vem do analytics
    if (phase() === 'final') await Promise.race([Promise.allSettled([loadHistoria(), ensurePr()]), new Promise((r) => setTimeout(r, 8000))]); // a história é o roteiro do Modo TV
    lastPhase = phase(); S.tv.ready = true;
    if (S.tv.on) { // retoma o ciclo na vista em que estava antes da recarga
      if (restored?.tvi != null) { S.tv.views = buildTvViews(); S.tv.idx = Math.min(restored.tvi, S.tv.views.length) - 1; }
      tvNext();
    } else if (restored?.view == null && S.focus.type !== 'br') flyToFocus(S.focus, 0);
    if (restored?.log != null) safe('log da urna', () => openSecLog(restored.log));
  });
  setInterval(() => {
    safe('frescor', renderFresh); safe('tv', tvLoop); safe('relógio', renderClock);
    if (!comparing() && Date.now() - lastLeftAt > 30000) safe('esquerda', renderLeft); // contagem regressiva até o fim da votação
  }, 1000);
  setInterval(reloadWatch, 60000);
  // banners aparecem/somem, janela muda: as colunas se reajustam (nada de rolagem na TV)
  if (window.ResizeObserver) { const ro = new ResizeObserver(() => scheduleFit()); ro.observe($('#left')); ro.observe($('#right')); }
  renderClock();
}
window.addEventListener('error', (e) => { logErr(`erro: ${e.message}`); console.error('[tela] erro não tratado:', e.message); });
window.addEventListener('unhandledrejection', (e) => { logErr(`promessa: ${e.reason?.message || e.reason}`); console.error('[tela] promessa rejeitada:', e.reason); });
boot().catch((e) => {
  console.error(e);
  const l = $('#loading'); if (l) l.textContent = 'Erro ao iniciar: ' + e.message + ' — nova tentativa em 30 s';
  setTimeout(() => scheduleReload(0), 30000);
});

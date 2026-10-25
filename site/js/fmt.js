// fmt.js — formatação pt-BR, cores (OKLab) e utilidades de DOM, compartilhadas por index.html e numeros.html.

export const $ = (s, r = document) => r.querySelector(s);
export const el = (tag, cls, txt) => { const e = document.createElement(tag); if (cls) e.className = cls; if (txt != null) e.textContent = txt; return e; };
export const isNum = (v) => typeof v === 'number' && isFinite(v);

const nf0 = new Intl.NumberFormat('pt-BR', { maximumFractionDigits: 0 });
const nf1 = new Intl.NumberFormat('pt-BR', { minimumFractionDigits: 1, maximumFractionDigits: 1 });
const nf2 = new Intl.NumberFormat('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
export { nf0, nf1, nf2 };
export const sign = (v) => (v > 0 ? '+' : v < 0 ? '−' : '±');
/** pontos percentuais com sinal */
export const fPP = (v, d = 1) => (!isNum(v) ? '—' : sign(Math.abs(v) < (d === 2 ? 0.005 : 0.05) ? 0 : v) + (d === 2 ? nf2 : nf1).format(Math.abs(v)) + ' p.p.');
/** δ: votos de margem por 100 aptos, com sinal */
export const fM = (v, d = 1) => (!isNum(v) ? '—' : sign(Math.abs(v) < (d === 2 ? 0.005 : 0.05) ? 0 : v) + (d === 2 ? nf2 : nf1).format(Math.abs(v)));
export const fPct = (v, d = 1) => (!isNum(v) ? '—' : (d === 0 ? nf0 : d === 2 ? nf2 : nf1).format(v) + '%');
export const U100 = 'por 100 aptos';
/** probabilidade 0–1 como %: nunca imprime 0% nem 100% ("<1%" / ">99%") */
export const fProb = (p) => (!isNum(p) ? '—' : p * 100 >= 99.5 ? '>99%' : p * 100 < 0.5 ? '<1%' : nf0.format(p * 100) + '%');
export function fN(v, signed = false) {
  if (!isNum(v)) return '—';
  const a = Math.abs(v), s = signed ? sign(Math.round(v) === 0 ? 0 : v) : v < 0 ? '−' : '';
  if (a >= 1e6) return s + nf2.format(a / 1e6) + ' mi';
  if (a >= 1e4) return s + nf1.format(a / 1e3) + ' mil';
  return s + nf0.format(a);
}
export const fInt = (v) => (!isNum(v) ? '—' : nf0.format(v));

// ---------- tempo ----------
/** 'dd/mm/yyyy hh:mm:ss' (BRT) → Date */
export function parseBrt(s) {
  if (!s || typeof s !== 'string') return null;
  const m = s.match(/(\d{2})\/(\d{2})\/(\d{4})\s+(\d{2}):(\d{2})(?::(\d{2}))?/);
  if (!m) return null;
  const d = new Date(`${m[3]}-${m[2]}-${m[1]}T${m[4]}:${m[5]}:${m[6] || '00'}-03:00`);
  return isNaN(d) ? null : d;
}
export function parseIso(s) { if (!s) return null; const d = new Date(s); return isNaN(d) ? null : d; }
const brtFmt = new Intl.DateTimeFormat('pt-BR', { timeZone: 'America/Sao_Paulo', hour: '2-digit', minute: '2-digit', hour12: false });
const brtFmtS = new Intl.DateTimeFormat('pt-BR', { timeZone: 'America/Sao_Paulo', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
export const hhmm = (d) => (d ? brtFmt.format(d) : '—');
export const hhmmss = (d) => (d ? brtFmtS.format(d) : '—');
export function fAge(sec) {
  if (!isNum(sec)) return '—';
  sec = Math.max(0, Math.round(sec));
  if (sec < 120) return `${sec} s`;
  if (sec < 5400) return `${Math.round(sec / 60)} min`;
  return `${nf1.format(sec / 3600)} h`;
}
/** Fim da votação (BRT). O STF prorrogou o 2º turno de 2026 até as 19h em todo o país; o TSE só divulga depois. */
export const FIM_VOTACAO = '19:00';
export const fimTxt = () => FIM_VOTACAO.replace(/:00$/, 'h').replace(':', 'h');
export function fimVotacao(now = Date.now()) {
  const day = new Date(now).toLocaleDateString('en-CA', { timeZone: 'America/Sao_Paulo' });
  return new Date(`${day}T${FIM_VOTACAO}:00-03:00`);
}

// ---------- candidatos ----------
export const PRESETS = {
  partido: { dark: ['#e5484d', '#3987e5'], light: ['#d13438', '#2a78d6'] },
  hub: { dark: ['#3987e5', '#d9731f'], light: ['#2a78d6', '#d9731f'] },
  neutral: { dark: ['#8a7cf7', '#c98500'], light: ['#6a5ae0', '#b27600'] },
};
export function loadPrefs() {
  let p = {};
  try { p = JSON.parse(localStorage.getItem('apur2t-site') || '{}') || {}; } catch { p = {}; }
  // Cores salvas com o padrão antigo (azul × laranja) migram para o padrão atual (vermelho × azul).
  if (p.cand?.A?.color === '#3987e5' && p.cand?.B?.color === '#d9731f') delete p.cand;
  return {
    cand: p.cand || { A: { name: 'Lula', short: 'Lula', num: '13', color: '#e5484d' }, B: { name: 'Flávio Bolsonaro', short: 'Flávio', num: '22', color: '#3987e5' } },
    theme: p.theme || 'dark', fade: p.fade ?? true, resOff: p.resOff ?? 0,
  };
}
export function savePrefs(p) { try { localStorage.setItem('apur2t-site', JSON.stringify({ cand: p.cand, theme: p.theme, fade: p.fade, resOff: p.resOff })); } catch { /* sem storage */ } }

// ---------- cor (OKLab) ----------
export const hex2rgb = (h) => { h = String(h).replace('#', ''); return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16) || 0); };
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
export const mix = (h1, h2, t) => { const a = rgb2lab(hex2rgb(h1)), b = rgb2lab(hex2rgb(h2)); return lab2rgb(a.map((v, i) => v + (b[i] - v) * t)); };
export const rgbCss = (c, a = 1) => `rgba(${c[0]},${c[1]},${c[2]},${a})`;
export const cssVar = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();

export function applyCandColors(cand) {
  document.documentElement.style.setProperty('--A', cand.A.color);
  document.documentElement.style.setProperty('--B', cand.B.color);
}
export function keyEl(k) { const s = el('span', 'key'); s.style.background = `var(--${k})`; return s; }

/** executa fn e nunca deixa uma exceção derrubar o laço chamador */
export function safe(label, fn) {
  try { return fn(); } catch (e) { logErr(`${label}: ${e?.message || e}`); console.error(`[tela] ${label}:`, e); return undefined; }
}
/** registro de erros para diagnóstico (window.__telaErrors), limitado */
export function logErr(msg) {
  const a = (window.__telaErrors = window.__telaErrors || []);
  a.push(`${new Date().toISOString().slice(11, 19)} ${msg}`);
  if (a.length > 50) a.splice(0, a.length - 50);
}

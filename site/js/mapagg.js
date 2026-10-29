// mapagg.js — agregação espacial do mapa (a única conta que o front faz; contracts §6.5).
// Valor 2T efetivo de cada seção: BU (sections.bin, has_bu=1) ou, sem BU, o swing Z da zona (analytics.zones, src obs|est)
// aplicado à base 1T da seção: p_k2 = max(0, k1/A1 + d_k/100), renormalizado, × A2 (aptos2_cad). Zona `shrunk` = sem valor.
import { mix, hex2rgb, isNum } from './fmt.js';

// campos dos acumuladores (Float64, K por unidade)
// pN2..pT1: logs de urna (prorrog.js), votos no log e votos a partir das 17h, 2º turno e 1º turno na mesma urna
export const F = { nSec: 0, nCnt: 1, nBU: 2, nEst: 3, aAll: 4, cA1: 5, cA2: 6, cC1: 7, cL1: 8, cO1: 9, cBN1: 10, C2: 11, L2: 12, O2: 13, BN2: 14, zPct: 15, cX1: 16, nHasBU: 17, pN2: 18, pT2: 19, pN1: 20, pT1: 21 };
export const K = 22;
const SRC = { obs: 1, est: 2, shrunk: 3 };

/** Zona/município 100% apurado: se o analytics publicar o swing bruto (`raw: {dl, do, dbn, dabst}`), ele vale
 *  (o Z padrão é encolhido para o pai mesmo com a zona completa). Sem `raw`, usa o Z como está. */
export function pickZ(z) {
  const r = z?.raw;
  if (r && z.pct >= 99.95 && [r.dl, r.do, r.dbn, r.dabst].every(isNum)) return { ...z, dl: r.dl, do: r.do, dbn: r.dbn, dabst: r.dabst };
  return z;
}

export function makeZones(Z) {
  return { state: new Uint8Array(Z), dl: new Float32Array(Z), do: new Float32Array(Z), dbn: new Float32Array(Z), dabst: new Float32Array(Z), pct: new Float32Array(Z), pctBu: new Float32Array(Z), version: 0, unknown: 0 };
}
/** copia analytics.zones para arrays por zone_idx; chaves desconhecidas são contadas e ignoradas */
export function updateZones(B, ZA, zones) {
  ZA.state.fill(0); ZA.pct.fill(0); ZA.pctBu.fill(0);
  let unknown = 0;
  if (zones && typeof zones === 'object') {
    for (const k in zones) {
      let i = B.zoneByKey.get(k);
      if (i === undefined) { const m = /^0*(\d+)-0*(\d+)$/.exec(k); if (m) i = B.zoneByKey.get(`${+m[1]}-${String(+m[2]).padStart(4, '0')}`); }
      if (i === undefined) { unknown++; continue; }
      let z = zones[k]; if (!z) continue;
      z = pickZ(z);
      const st = SRC[z.src] || 0;
      const ok = isNum(z.dl) && isNum(z.do) && isNum(z.dbn) && isNum(z.dabst);
      ZA.state[i] = st && (ok || st === 3) ? st : 0;
      ZA.dl[i] = ok ? z.dl : 0; ZA.do[i] = ok ? z.do : 0; ZA.dbn[i] = ok ? z.dbn : 0; ZA.dabst[i] = ok ? z.dabst : 0;
      ZA.pct[i] = isNum(z.pct) ? z.pct : 0; ZA.pctBu[i] = isNum(z.pct_bu) ? z.pct_bu : 0;
    }
  }
  ZA.unknown = unknown; ZA.version++;
}

/** soma por local (Float64Array P×K reaproveitado). sec = Int32Array N×8 ou null; pr = colunas de prorrog.js ou null. */
export function computePlaces(B, sec, ZA, out, pr = null) {
  out.fill(0);
  const S = B.S, N = B.N, place = S.place, zone = S.zone, a1v = S.a1, c1v = S.c1, l1v = S.l1, o1v = S.o1, x1v = S.x1, bn1v = S.bn1, a2v = S.a2;
  const st = ZA.state, dlv = ZA.dl, dov = ZA.do, dbnv = ZA.dbn, dabv = ZA.dabst, pctv = ZA.pct;
  const hasSec = !!sec && sec.length === N * 8;
  for (let s = 0; s < N; s++) {
    const p = place[s]; if (p < 0) continue;
    const o = p * K, z = zone[s];
    out[o] += 1; out[o + 4] += a2v[s]; out[o + 15] += pctv[z];
    if (pr) { const fl = pr.flags[s]; if (fl & 1) { out[o + 18] += pr.n2[s]; out[o + 19] += pr.t2[s]; } if (fl & 2) { out[o + 20] += pr.n1[s]; out[o + 21] += pr.t1[s]; } }
    const a1 = a1v[s], c1 = c1v[s], l1 = l1v[s], o1 = o1v[s], bn1 = bn1v[s];
    let A2, C2, L2, O2, BN2;
    const q = s * 8;
    if (hasSec && sec[q] === 1) {
      A2 = sec[q + 1] > 0 ? sec[q + 1] : a2v[s]; C2 = sec[q + 2]; L2 = sec[q + 3]; O2 = sec[q + 4]; BN2 = sec[q + 5] + sec[q + 6];
      out[o + 2] += 1; out[o + 17] += 1;
    } else {
      const zs = st[z];
      if ((zs !== 1 && zs !== 2) || a1 <= 0) continue;
      const pl = Math.max(0, l1 / a1 + dlv[z] / 100), po = Math.max(0, o1 / a1 + dov[z] / 100);
      const pb = Math.max(0, bn1 / a1 + dbnv[z] / 100), pa = Math.max(0, (a1 - c1) / a1 + dabv[z] / 100);
      const sum = pl + po + pb + pa; if (sum <= 0) continue;
      A2 = a2v[s]; const k = A2 / sum;
      L2 = pl * k; O2 = po * k; BN2 = pb * k; C2 = L2 + O2 + BN2;
      out[o + 3] += 1;
    }
    out[o + 1] += 1; out[o + 5] += a1; out[o + 6] += A2; out[o + 7] += c1; out[o + 8] += l1; out[o + 9] += o1; out[o + 10] += bn1;
    out[o + 11] += C2; out[o + 12] += L2; out[o + 13] += O2; out[o + 14] += BN2; out[o + 16] += x1v[s];
  }
}

/** locais → células da resolução (Float64Array nCells×K reaproveitado) */
export function aggregateCells(B, res, places, out) {
  out.fill(0);
  const cell = B.L[`cell_r${res}`];
  for (let p = 0; p < B.P; p++) {
    const o = p * K, c = cell[p] * K;
    for (let f = 0; f < K; f++) out[c + f] += places[o + f];
  }
}

/** soma um conjunto de seções avulsas (cartão do local, foco em célula) */
export function sumUnit(arr, idxs) { const t = new Float64Array(K); for (const i of idxs) { const o = i * K; for (let f = 0; f < K; f++) t[f] += arr[o + f]; } return t; }

// ---------- valores de uma unidade agregada ----------
/** valores comparáveis (mesmas seções nos dois turnos) de um acumulador K */
export function cmpFromAgg(a, o = 0) {
  const n = a[o + 1], cA1 = a[o + 5], cA2 = a[o + 6];
  const nSec = a[o];
  const r = { nSec, nCnt: n, nBU: a[o + 2], nEst: a[o + 3], aAll: a[o + 4], zPct: nSec ? a[o + 15] / nSec : 0, cov: nSec ? n / nSec : 0, buShare: nSec ? a[o + 2] / nSec : 0, has: false };
  if (!n || cA1 <= 0 || cA2 <= 0) return r;
  const L1 = a[o + 8], O1 = a[o + 9], C1 = a[o + 7], BN1 = a[o + 10], L2 = a[o + 12], O2 = a[o + 13], C2 = a[o + 11], BN2 = a[o + 14];
  r.has = true; r.obs = a[o + 2] / n;
  r.delta = 100 * ((L2 - O2) / cA2 - (L1 - O1) / cA1);
  r.dMarginV = (L2 - O2) - (L1 - O1);
  r.dAbst = 100 * ((cA2 - C2) / cA2 - (cA1 - C1) / cA1); // + = abstenção subiu
  r.dAbstV = (cA2 - C2) - (cA1 - C1);
  r.dBnComp = C2 > 0 && C1 > 0 ? 100 * (BN2 / C2 - BN1 / C1) : null;
  r.gA = 100 * (L2 / cA2 - L1 / cA1); r.gB = 100 * (O2 / cA2 - O1 / cA1);
  r.dL = L2 - L1; r.dO = O2 - O1;
  r.abst2 = 100 * (1 - C2 / cA2); r.abst1 = 100 * (1 - C1 / cA1);
  r.m2 = L2 + O2 > 0 ? 100 * (L2 - O2) / (L2 + O2) : null;
  r.l2share = L2 + O2 > 0 ? 100 * L2 / (L2 + O2) : null;
  r.L2 = L2; r.O2 = O2; r.C2 = C2; r.A2 = cA2; r.L1 = L1; r.O1 = O1; r.C1 = C1; r.A1 = cA1;
  return r;
}

/** caminho rápido (sem alocar) para colorir células/locais: escreve [valor, cobertura, fração observada] em out */
export function aggModeValue(key, a, o, req, out) {
  const nSec = a[o], n = a[o + 1];
  out[1] = nSec ? n / nSec : 0; out[2] = n ? a[o + 2] / n : NaN; out[0] = NaN;
  if (key === 'tardio2' || key === 'tardio1') { const t2 = key === 'tardio2', nv = a[o + (t2 ? 18 : 20)]; out[0] = nv > 0 ? 1e4 * a[o + (t2 ? 19 : 21)] / nv : NaN; out[1] = 1; out[2] = NaN; return; } // por 10 mil votos
  if (key === 'cob') { const v = nSec ? 100 * a[o + 2] / nSec : 0; out[0] = v > 0 ? v : NaN; out[1] = 1; out[2] = NaN; return; } // 0% = sem cor
  const cA1 = a[o + 5], cA2 = a[o + 6];
  if (!n || cA1 <= 0 || cA2 <= 0) return;
  const L1 = a[o + 8], O1 = a[o + 9], C1 = a[o + 7], BN1 = a[o + 10], C2 = a[o + 11], L2 = a[o + 12], O2 = a[o + 13], BN2 = a[o + 14];
  switch (key) {
    case 'ritmo': if (isFinite(req)) out[0] = 100 * ((L2 - O2) / cA2 - (L1 - O1) / cA1) - req; break;
    case 'dmargem': out[0] = 100 * ((L2 - O2) / cA2 - (L1 - O1) / cA1); break;
    case 'dabst': out[0] = -100 * ((cA2 - C2) / cA2 - (cA1 - C1) / cA1); break;
    case 'dbn': if (C2 > 0 && C1 > 0) out[0] = -100 * (BN2 / C2 - BN1 / C1); break;
    case 'ganhoA': out[0] = 100 * (L2 / cA2 - L1 / cA1); break;
    case 'ganhoB': out[0] = 100 * (O2 / cA2 - O1 / cA1); break;
    case 'abst2': out[0] = 100 * (1 - C2 / cA2); break;
    case 'margem': if (L2 + O2 > 0) out[0] = 100 * (L2 - O2) / (L2 + O2); break;
    default: break;
  }
}

/** município: Z do analytics aplicado ao 1T do município (mesma regra das seções) */
export function cmpFromZ(base1, z) {
  z = z ? pickZ(z) : z;
  const r = { has: false, cov: isNum(z?.pct) ? z.pct / 100 : 0, src: z?.src || null, pctBu: z?.pct_bu ?? null };
  if (!z || (z.src !== 'obs' && z.src !== 'est')) return r;
  const { a1, c1, l1, o1, bn1, a2 } = base1;
  if (!(a1 > 0) || ![z.dl, z.do, z.dbn, z.dabst].every(isNum)) return r;
  const pl = Math.max(0, l1 / a1 + z.dl / 100), po = Math.max(0, o1 / a1 + z.do / 100), pb = Math.max(0, bn1 / a1 + z.dbn / 100), pa = Math.max(0, (a1 - c1) / a1 + z.dabst / 100);
  const sum = pl + po + pb + pa; if (sum <= 0) return r;
  const L2r = pl / sum, O2r = po / sum, BN2r = pb / sum, AB2r = pa / sum, C2r = 1 - AB2r;
  const L1r = l1 / a1, O1r = o1 / a1, C1r = c1 / a1, BN1r = bn1 / a1, A2 = a2 || a1;
  r.has = true; r.obs = isNum(z.pct_bu) && z.pct > 0 ? Math.min(1, z.pct_bu / z.pct) : null;
  r.delta = 100 * ((L2r - O2r) - (L1r - O1r));
  r.dMarginV = (L2r - O2r) * A2 - (l1 - o1);
  r.dAbst = 100 * (AB2r - (1 - C1r)); r.dAbstV = AB2r * A2 - (a1 - c1);
  r.dBnComp = C2r > 0 && C1r > 0 ? 100 * (BN2r / C2r - BN1r / C1r) : null;
  r.gA = 100 * (L2r - L1r); r.gB = 100 * (O2r - O1r);
  r.dL = L2r * A2 - l1; r.dO = O2r * A2 - o1;
  r.abst2 = 100 * AB2r; r.abst1 = 100 * (1 - C1r);
  r.m2 = L2r + O2r > 0 ? 100 * (L2r - O2r) / (L2r + O2r) : null;
  return r;
}

/** comparação exata com o 1º turno a partir do agregado oficial completo (A: {l,o,c,a,vb,tvn} do TSE).
 *  Usada no exterior: lá o swing por zona do analytics não descreve cada cidade. */
export function cmpExact(base1, A) {
  const r = { has: false, cov: A?.ts ? (A.st || 0) / A.ts : 0, src: 'obs', pctBu: null };
  const { a1, c1, l1, o1, bn1 } = base1, A2 = (A?.c || 0) + (A?.a || 0);
  if (!A || !(A.ts > 0) || A.st < A.ts || !(a1 > 0) || !(A2 > 0)) return r;
  const L2r = (A.l || 0) / A2, O2r = (A.o || 0) / A2, BN2r = ((A.vb || 0) + (A.tvn || 0)) / A2, AB2r = (A.a || 0) / A2, C2r = 1 - AB2r;
  const L1r = l1 / a1, O1r = o1 / a1, C1r = c1 / a1, BN1r = bn1 / a1;
  r.has = true; r.cov = 1; r.obs = 1;
  r.delta = 100 * ((L2r - O2r) - (L1r - O1r));
  r.dMarginV = (A.l || 0) - (A.o || 0) - (l1 - o1);
  r.dAbst = 100 * (AB2r - (1 - C1r)); r.dAbstV = (A.a || 0) - (a1 - c1);
  r.dBnComp = C2r > 0 && C1r > 0 ? 100 * (BN2r / C2r - BN1r / C1r) : null;
  r.gA = 100 * (L2r - L1r); r.gB = 100 * (O2r - O1r);
  r.dL = (A.l || 0) - l1; r.dO = (A.o || 0) - o1;
  r.abst2 = 100 * AB2r; r.abst1 = 100 * (1 - C1r);
  r.m2 = L2r + O2r > 0 ? 100 * (L2r - O2r) / (L2r + O2r) : null;
  return r;
}

/** UF/região/BR: termômetro do analytics (T.best; exato como conferência) */
export function cmpFromT(T) {
  const M = T?.best || T?.estimated || null;
  const r = { has: false, cov: isNum(T?.coverage_aptos_pct) ? T.coverage_aptos_pct / 100 : 0, T };
  if (!M || !isNum(M.delta)) return r;
  r.has = true; r.delta = M.delta; r.dMarginV = M.d_margin_votes;
  r.dAbst = M.d_abst_pp; r.dAbstV = M.d_abst_n;
  r.dBnComp = isNum(M.d_bn_pp_comp) ? M.d_bn_pp_comp : M.d_bn_pp;
  r.gA = M.d_l_100 ?? null; r.gB = M.d_o_100 ?? null; r.dL = M.d_l; r.dO = M.d_o;
  r.abst2 = M.abst2_pct ?? null; r.abst1 = M.abst1_pct ?? null;
  return r;
}

/** placar oficial (agg A) */
export function official(A) {
  if (!A) return { has: false };
  const l = A.l || 0, o = A.o || 0, v = l + o;
  return { has: v > 0 && (A.st || 0) > 0, l, o, m: v > 0 ? 100 * (l - o) / v : null, lshare: v > 0 ? 100 * l / v : null, pct: A.ts ? 100 * (A.st || 0) / A.ts : 0,
    abst: (A.c || 0) + (A.a || 0) > 0 ? 100 * (A.a || 0) / ((A.c || 0) + (A.a || 0)) : null, st: A.st || 0, ts: A.ts || 0, est: A.est || 0, te: A.te || 0 };
}

// ---------- modos de cor ----------
export const MODES = [
  { key: 'ritmo', label: 'Ritmo vs necessário', kind: 'div', th: [0.4, 1, 2, 3.5, 6], pos: 'A', neg: 'B', src: 'cmp' },
  { key: 'margem', label: 'Margem no 2º turno', kind: 'div', th: [2, 5, 10, 20, 35], pos: 'A', neg: 'B', src: 'off' },
  { key: 'dmargem', label: 'Δ margem vs 1º turno', kind: 'div', th: [0.75, 2, 3.5, 6, 9], pos: 'A', neg: 'B', src: 'cmp' },
  { key: 'dabst', label: 'Δ abstenção vs 1º turno', kind: 'div', th: [0.5, 1.5, 3, 5, 8], pos: 'teal', neg: 'mag', src: 'cmp' },
  { key: 'dbn', label: 'Δ brancos + nulos', kind: 'div', th: [0.3, 0.8, 1.5, 2.5, 4], pos: 'teal', neg: 'mag', src: 'cmp' },
  { key: 'ganhoA', label: 'Ganho de votos de A', kind: 'seq', th: [0, 2, 4, 6, 9, 12], hue: 'A', src: 'cmp' },
  { key: 'ganhoB', label: 'Ganho de votos de B', kind: 'seq', th: [0, 2, 4, 6, 9, 12], hue: 'B', src: 'cmp' },
  { key: 'abst2', label: 'Abstenção no 2º turno', kind: 'seq', th: [10, 15, 20, 25, 30, 35], hue: 'teal', src: 'cmp' },
  { key: 'proj', label: 'Projeção · votos que faltam', kind: 'div', th: [2, 5, 10, 20, 35], pos: 'A', neg: 'B', src: 'proj' },
  { key: 'cob', label: '% apurado', kind: 'seq', th: [0, 5, 20, 50, 80, 99.5], hue: 'gray', src: 'cov' },
  { key: 'margem1', label: 'Margem no 1º turno (base)', kind: 'div', th: [2, 5, 10, 20, 35], pos: 'A', neg: 'B', src: 'base' },
  // logs de urna: votos computados a partir das 17h (Brasília) por 10 mil votos. Mesma escala nos dois turnos, para comparar.
  { key: 'tardio2', label: 'Votos após 17h · 2º turno', kind: 'seq', th: [1, 50, 100, 200, 500, 1000], hue: 'teal', src: 'pr' },
  { key: 'tardio1', label: 'Votos após 17h · 1º turno', kind: 'seq', th: [1, 50, 100, 200, 500, 1000], hue: 'teal', src: 'pr' },
];
export const MODE = Object.fromEntries(MODES.map((m) => [m.key, m]));

/** valor do modo a partir de um objeto cmp (+ oficial) */
export function modeValue(mode, c, off, req) {
  switch (mode.key) {
    case 'ritmo': return c?.has && isNum(req) ? c.delta - req : null;
    case 'dmargem': return c?.has ? c.delta : null;
    case 'dabst': return c?.has && isNum(c.dAbst) ? -c.dAbst : null; // + = abstenção caiu
    case 'dbn': return c?.has && isNum(c.dBnComp) ? -c.dBnComp : null; // + = BN caiu
    case 'ganhoA': return c?.has && isNum(c.gA) ? c.gA : null;
    case 'ganhoB': return c?.has && isNum(c.gB) ? c.gB : null;
    case 'abst2': return c?.has && isNum(c.abst2) ? c.abst2 : null;
    case 'margem': return off?.has ? off.m : c?.has ? c.m2 : null;
    default: return null;
  }
}

export function buildRamps(cand, theme) {
  const dark = theme === 'dark';
  const neutral = dark ? '#4a4d55' : '#d6d5cf';
  const hue = { A: cand.A.color, B: cand.B.color, teal: '#1f9c8e', mag: '#b5179e', gray: dark ? '#f2f1ee' : '#2b2b2b' };
  const steps = [0.3, 0.47, 0.64, 0.82, 1.0];
  const arm = (h) => steps.map((t, i) => (i === 4 && !dark ? mix(h, '#000000', 0.12) : mix(neutral, h, t)));
  const R = { neutral: hex2rgb(neutral), noData: dark ? [120, 124, 132] : [190, 189, 184] };
  for (const m of MODES) {
    if (m.kind === 'div') R[m.key] = { pos: arm(hue[m.pos]), neg: arm(hue[m.neg]), mid: hex2rgb(neutral) };
    else R[m.key] = { seq: [0.16, 0.32, 0.5, 0.68, 0.84, 1].map((t) => mix(neutral, hue[m.hue], t)), below: dark ? [70, 72, 78] : [225, 224, 219] };
  }
  return R;
}
export function classColor(R, mode, v) {
  const r = R[mode.key];
  if (mode.kind === 'div') {
    const a = Math.abs(v); let k = 0;
    while (k < mode.th.length && a >= mode.th[k]) k++;
    return k === 0 ? r.mid : (v > 0 ? r.pos : r.neg)[k - 1];
  }
  if (v < mode.th[0]) return r.below;
  let k = 0;
  while (k < mode.th.length - 1 && v >= mode.th[k + 1]) k++;
  return r.seq[k];
}
/** escreve RGBA em out[i*4..] */
export function writeColor(out, i, R, mode, v, { level, cov = 1, obs = null, dim = 1, fade = true }) {
  const o = i * 4;
  if (!isNum(v)) {
    if (level === 'muni' || level === 'uf') { const c = R.noData; out[o] = c[0]; out[o + 1] = c[1]; out[o + 2] = c[2]; out[o + 3] = Math.round(34 * dim); }
    else { out[o] = 0; out[o + 1] = 0; out[o + 2] = 0; out[o + 3] = 0; }
    return;
  }
  let c = classColor(R, mode, v);
  if (obs != null && isFinite(obs) && (level === 'cell' || level === 'place')) { // estimado pela zona = cor lavada; observado na urna = cheia
    const k2 = 0.32 * (1 - obs), N = R.neutral;
    c = [c[0] + (N[0] - c[0]) * k2, c[1] + (N[1] - c[1]) * k2, c[2] + (N[2] - c[2]) * k2];
  }
  const base = level === 'place' ? 240 : level === 'cell' ? 205 : 195;
  let k = 1;
  if (mode.src === 'proj') k = cov <= 0 ? 0.08 : 0.25 + 0.75 * cov;
  else if (fade && mode.src !== 'cov' && mode.src !== 'base' && mode.src !== 'pr') k = 0.3 + 0.7 * Math.max(0, Math.min(1, cov));
  out[o] = Math.round(c[0]); out[o + 1] = Math.round(c[1]); out[o + 2] = Math.round(c[2]); out[o + 3] = Math.round(base * k * dim);
}

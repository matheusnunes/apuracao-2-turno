// base.js — carrega a base exportada por site/tools/export_base.py (site/data/base/).
const BASE = 'data/base/';

async function getJson(path, v) {
  const r = await fetch(`${BASE}${path}?v=${v}`);
  if (!r.ok) throw new Error(`HTTP ${r.status} em ${path}`);
  return r.json();
}
async function getBin(path, v) {
  const r = await fetch(`${BASE}${path}?v=${v}`);
  if (!r.ok) throw new Error(`HTTP ${r.status} em ${path}`);
  return r.arrayBuffer();
}
const CTOR = { int32: Int32Array, uint16: Uint16Array, uint8: Uint8Array, float32: Float32Array, float64: Float64Array, int16: Int16Array, uint32: Uint32Array };
function soa(buf, spec) {
  const o = {};
  for (const c of spec.cols) o[c.name] = new CTOR[c.dtype](buf, c.offset, c.length);
  return o;
}

export async function loadMeta() {
  const r = await fetch(`${BASE}meta.json?t=${Date.now()}`);
  if (!r.ok) throw new Error(`base não exportada (HTTP ${r.status} em ${BASE}meta.json). Rode: uv run python site/tools/export_base.py`);
  return r.json();
}

/** Base completa da tela (mapa). */
export async function loadBase(meta) {
  if (new Uint8Array(new Uint32Array([1]).buffer)[0] !== 1) throw new Error('plataforma big-endian não suportada');
  const v = meta.build_id;
  const F = meta.files;
  const [sb, lb, pb, mun, zon, polys, ufs] = await Promise.all([
    getBin(F.secoes.path, v), getBin(F.locais.path, v), getBin(F.locais_secs.path, v),
    getJson('municipios.json', v), getJson('zonas.json', v), getJson('mun_poly.json', v), getJson('ufs.json', v),
  ]);
  const B = { meta, v, N: F.secoes.n, P: F.locais.n, S: soa(sb, F.secoes), L: soa(lb, F.locais), perm: soa(pb, F.locais_secs).sec, mun, zon, ufs };
  B.M = mun.cd_mun.length; B.Z = zon.k.length;
  B.ufOrder = meta.uf_order; B.ufIdx = Object.fromEntries(meta.uf_order.map((u, i) => [u, i]));
  B.munByCd = new Map(mun.cd_mun.map((c, i) => [String(c), i]));
  B.zoneByKey = new Map(zon.k.map((k, i) => [k, i]));
  B.munUf = Int16Array.from(mun.uf, (u) => B.ufIdx[u] ?? -1);
  B.ufReg = meta.uf_order.map((u) => ufs.find((x) => x.uf === u)?.regiao || 'ZZ');
  // polígonos municipais: um objeto por parte (multipolígonos viram várias entradas com o mesmo i)
  B.munPolys = polys.map(([i, rings]) => ({ i, p: rings }));
  B.ufPolys = [];
  ufs.forEach((u, i) => { for (const rings of u.polys || []) B.ufPolys.push({ i: B.ufIdx[u.uf] ?? i, p: rings }); });
  B.ufLines = [];
  for (const d of B.ufPolys) B.ufLines.push({ path: d.p[0] });
  // aptos por local (raio dos pontos) e nome curto por município
  B.placeAptos = new Float32Array(B.P);
  const S = B.S;
  for (let s = 0; s < B.N; s++) { const p = S.place[s]; if (p >= 0) B.placeAptos[p] += S.a2[s]; }
  B.h3 = {};
  return B;
}

/** lista de células de uma resolução (sob demanda, cacheada) */
export async function loadH3(B, res) {
  if (B.h3[res]) return B.h3[res];
  const j = await getJson(`h3/r${res}.json`, B.v);
  const data = j.cells.map((h, i) => ({ h, i }));
  const ent = { res, cells: j.cells, uf: Int16Array.from(j.uf), lat: Float32Array.from(j.lat), lng: Float32Array.from(j.lng), data, n: j.cells.length };
  ent.index = new Map(j.cells.map((h, i) => [h, i]));
  B.h3[res] = ent;
  return ent;
}

const metaCache = {};
/** nome/endereço dos locais de uma UF: {place_idx: [nome, endereco, bairro, nr_local, zona]} */
export async function loadPlaceMeta(B, uf) {
  if (metaCache[uf]) return metaCache[uf];
  try { metaCache[uf] = await getJson(`locais_meta/${uf}.json`, B.v); } catch { metaCache[uf] = {}; }
  return metaCache[uf];
}

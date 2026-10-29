// prorrog.js — dados da vista "Prorrogação" (etl/prorrogacao.py → data/prorrog/): votos por horário a partir dos
// logs de urna, 1º × 2º turno na mesma urna. Carregados sob demanda (só quando alguém abre a vista).
const DIR = 'data/prorrog/';
const CTOR = { uint16: Uint16Array, uint8: Uint8Array, int32: Int32Array, uint32: Uint32Array };

let PR = null, loading = null;

/** {R: resumo.json, S: colunas por seção, mun/uf: somas} ou null se os dados não existem */
export function prorrog() { return PR; }

export function loadProrrog(B) {
  if (PR) return Promise.resolve(PR);
  if (loading) return loading;
  loading = (async () => {
    const r = await fetch(`${DIR}resumo.json?t=${Date.now()}`);
    if (!r.ok) throw new Error(`HTTP ${r.status} em ${DIR}resumo.json`);
    const R = await r.json();
    const b = await fetch(`${DIR}secoes.bin?v=${encodeURIComponent(R.gerado_em)}`);
    if (!b.ok) throw new Error(`HTTP ${b.status} em ${DIR}secoes.bin`);
    const buf = await b.arrayBuffer();
    if (R.n !== B.N) throw new Error(`prorrog: ${R.n} seções, base tem ${B.N}`);
    const S = {};
    for (const c of R.cols) S[c.name] = new CTOR[c.dtype](buf, c.offset, c.length);
    // somas por município e UF (n = votos no log, t = a partir das 17h, c = a partir das 18h, a = seções abertas após 17h10)
    const M = B.M, U = B.ufOrder.length, sm = B.S.mun;
    const mk = (n) => ({ n2: new Float64Array(n), t2: new Float64Array(n), c2: new Float64Array(n), n1: new Float64Array(n), t1: new Float64Array(n), c1: new Float64Array(n), a2: new Float64Array(n), s: new Float64Array(n) });
    const mun = mk(M), uf = mk(U);
    for (let s = 0; s < B.N; s++) {
      const m = sm[s], u = B.munUf[m];
      const same = (S.flags[s] & 2) !== 0, has = (S.flags[s] & 1) !== 0;
      const a2 = S.e2[s] >= 17 * 60 + 10 ? 1 : 0;
      for (const [A, i] of [[mun, m], [uf, u]]) {
        if (i < 0) continue;
        if (has) { A.n2[i] += S.n2[s]; A.t2[i] += S.t2[s]; A.c2[i] += S.t2c[s]; A.s[i] += 1; A.a2[i] += a2; }
        if (same) { A.n1[i] += S.n1[s]; A.t1[i] += S.t1[s]; A.c1[i] += S.t1c[s]; }
      }
    }
    PR = { R, S, mun, uf };
    return PR;
  })();
  loading.catch(() => { loading = null; });
  return loading;
}

/** votos a partir das 17h por 10 mil votos (turno 1 ou 2) num acumulador {n1,t1,n2,t2}[i] */
export function lateShare(A, i, t) {
  const n = t === 1 ? A.n1[i] : A.n2[i];
  return n > 0 ? 1e4 * (t === 1 ? A.t1[i] : A.t2[i]) / n : null;
}

/** texto do log da urna da seção (servido por serve.py, que descompacta o .jez) */
export async function fetchUrnaLog(uf, cdMun, zona, secao) {
  const p = `logs/${uf}/${String(cdMun).padStart(5, '0')}/${String(zona).padStart(4, '0')}-${String(secao).padStart(4, '0')}.txt`;
  const r = await fetch(p);
  if (!r.ok) throw new Error(r.status === 404 ? 'log desta urna não disponível' : `HTTP ${r.status}`);
  return r.text();
}

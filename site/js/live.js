// live.js — polling dos arquivos ao vivo (contracts §4 e §6). Nunca lança: guarda o último estado bom.
//   manifest.json a cada ~10 s; agg.<dv>.json e sections.<dv>.bin só quando data_versions mudam;
//   analytics.json a cada ciclo (com If-None-Match, o servidor responde 304 se não mudou).

const T_SMALL = 12000, T_BIG = 45000;

async function fetchWithTimeout(url, opts = {}, ms = T_SMALL) {
  const ac = new AbortController();
  const t = setTimeout(() => ac.abort(), ms);
  try { return await fetch(url, { ...opts, signal: ac.signal, cache: 'no-store' }); } finally { clearTimeout(t); }
}

export class LiveFeed {
  /**
   * @param {object} o
   * @param {string} o.base         pasta dos arquivos ao vivo (padrão data/live/; ?live= sobrescreve)
   * @param {boolean} o.wantSections baixar sections.bin (a página só de números não baixa)
   * @param {number} o.nRows        linhas do índice da base exportada
   * @param {string} o.sha          sha256 do secoes_index.csv da base exportada
   * @param {(changed:object, feed:LiveFeed)=>void} o.onUpdate
   */
  constructor({ base = 'data/live/', wantSections = true, nRows = 0, sha = null, interval = 10000, onUpdate = () => {} }) {
    this.base = base.endsWith('/') ? base : base + '/';
    this.wantSections = wantSections; this.nRows = nRows; this.sha = sha; this.interval = interval; this.onUpdate = onUpdate;
    this.manifest = null; this.agg = null; this.an = null; this.sec = null;
    this.dv = { agg: null, sections: null }; this.anEtag = null; this.anKey = null;
    this.indexOk = null; this.indexMsg = '';
    this.okAt = { manifest: 0, agg: 0, sections: 0, analytics: 0 }; // relógio local do último sucesso
    this.err = { manifest: null, agg: null, sections: null, analytics: null };
    this.lastTickAt = 0; this.busy = false; this.timer = null; this.ticks = 0;
  }
  start() { this.tick(); this.timer = setInterval(() => this.tick(), this.interval); }
  stop() { clearInterval(this.timer); }

  async tick() {
    if (this.busy) return;
    this.busy = true; this.ticks++;
    const changed = { manifest: false, agg: false, sections: false, analytics: false };
    try {
      await this._manifest(changed);
      if (this.manifest) {
        await this._agg(changed);
        if (this.wantSections) await this._sections(changed);
      }
      await this._analytics(changed);
    } catch (e) {
      console.error('[live] ciclo falhou', e);
    } finally {
      this.busy = false; this.lastTickAt = Date.now();
      try { this.onUpdate(changed, this); } catch (e) { console.error('[live] onUpdate', e); }
    }
  }

  async _manifest(changed) {
    try {
      const r = await fetchWithTimeout(`${this.base}manifest.json?t=${Date.now()}`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const m = await r.json();
      if (!m || typeof m !== 'object' || m.version == null) throw new Error('manifest sem version');
      changed.manifest = !this.manifest || m.version !== this.manifest.version || m.generated_at !== this.manifest.generated_at;
      this.manifest = m; this.okAt.manifest = Date.now(); this.err.manifest = null;
      const ix = m.index || {};
      if (ix.n_rows != null && this.nRows && ix.n_rows !== this.nRows) { this.indexOk = false; this.indexMsg = `índice do coletor tem ${ix.n_rows} linhas; base tem ${this.nRows}`; }
      else if (ix.sha256 && this.sha && ix.sha256 !== this.sha) { this.indexOk = false; this.indexMsg = 'sha256 do índice do coletor difere da base exportada'; }
      else if (ix.n_cols != null && ix.n_cols !== 8) { this.indexOk = false; this.indexMsg = `sections.bin com ${ix.n_cols} colunas (esperado 8)`; }
      else { this.indexOk = ix.n_rows != null || ix.sha256 ? true : null; this.indexMsg = ''; }
    } catch (e) { this.err.manifest = String(e.message || e); }
  }

  async _agg(changed) {
    const m = this.manifest, dv = m.data_versions?.agg ?? m.version, name = m.files?.agg || 'agg.json';
    if (this.agg && dv === this.dv.agg) return;
    try {
      const r = await fetchWithTimeout(`${this.base}${name}?v=${dv}`, {}, T_BIG);
      if (!r.ok) throw new Error(`HTTP ${r.status} em ${name}`);
      const a = await r.json();
      if (!a || typeof a !== 'object' || !a.br) throw new Error(`${name} sem br`);
      this.agg = a; this.dv.agg = dv; this.okAt.agg = Date.now(); this.err.agg = null; changed.agg = true;
    } catch (e) { this.err.agg = String(e.message || e); }
  }

  async _sections(changed) {
    const m = this.manifest, dv = m.data_versions?.sections ?? m.version, name = m.files?.sections || 'sections.bin';
    if (this.indexOk === false) { this.err.sections = 'ignorado: ' + this.indexMsg; return; }
    if (this.sec && dv === this.dv.sections) return;
    try {
      const r = await fetchWithTimeout(`${this.base}${name}?v=${dv}`, {}, T_BIG);
      if (!r.ok) throw new Error(`HTTP ${r.status} em ${name}`);
      const buf = await r.arrayBuffer();
      const want = this.nRows * 8 * 4;
      if (this.nRows && buf.byteLength !== want) throw new Error(`${name} tem ${buf.byteLength} bytes (esperado ${want})`);
      const view = new Int32Array(buf);
      if (!this.sec || this.sec.length !== view.length) this.sec = new Int32Array(view.length); // alocado uma vez
      this.sec.set(view);
      this.dv.sections = dv; this.okAt.sections = Date.now(); this.err.sections = null; changed.sections = true;
    } catch (e) { this.err.sections = String(e.message || e); }
  }

  async _analytics(changed) {
    const name = this.manifest?.files?.analytics || 'analytics.json';
    try {
      const headers = this.anEtag && this.an ? { 'If-None-Match': this.anEtag } : {};
      const r = await fetchWithTimeout(`${this.base}${name}`, { headers }, T_BIG);
      if (r.status === 304) { this.okAt.analytics = Date.now(); this.err.analytics = null; return; }
      if (!r.ok) throw new Error(`HTTP ${r.status} em ${name}`);
      const a = await r.json();
      if (!a || typeof a !== 'object' || !a.status) throw new Error(`${name} sem status`);
      this.anEtag = r.headers.get('ETag');
      const key = `${a.computed_at}|${a.input_version}`;
      if (key !== this.anKey) { this.an = a; this.anKey = key; changed.analytics = true; }
      this.okAt.analytics = Date.now(); this.err.analytics = null;
    } catch (e) { this.err.analytics = String(e.message || e); }
  }
}

/** pasta ao vivo: ?live=data/live-fake/ (só caminhos relativos simples) */
export function liveBaseFromUrl() {
  const q = new URLSearchParams(location.search).get('live');
  if (q && /^[\w./-]+$/.test(q) && !q.includes('..') && !q.startsWith('/')) return q.endsWith('/') ? q : q + '/';
  return 'data/live/';
}

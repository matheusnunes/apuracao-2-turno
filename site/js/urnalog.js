// urnalog.js — visor do log de uma urna (o .jez do TSE, descompactado pelo serve.py). O log do 2º turno traz o histórico
// da urna desde a carga, incluindo o dia do 1º turno quando a urna é a mesma: dá para comparar o fim da votação nos dois.
import { el, isNum, fInt, fPct } from './fmt.js';
import { fetchUrnaLog } from './prorrog.js';

const DAYS = { '04/10/2026': 1, '25/10/2026': 2 };
const VOTO = 'O voto do eleitor foi computado', HAB = 'Eleitor foi habilitado';
const KEY_EVENTS = [
  ['Urna pronta para receber votos', 'abertura'],
  ['Operador selecionou: Encerrar votação', 'encerrar'],
  ['Todas as pessoas presentes já votaram?', 'todos votaram?'],
  ['Procedimento de encerramento confirmado', 'encerrada'],
  ['Inicio do Encerramento', 'boletim'],
];
const hms = (x) => `${String(Math.floor(x / 3600)).padStart(2, '0')}:${String(Math.floor(x / 60) % 60).padStart(2, '0')}:${String(x % 60).padStart(2, '0')}`;
const esc = (s) => s.replace(/[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]));
const mask = (m) => (m.includes('/') ? m : m.replace(/\d{9,}/g, (d) => `${d.slice(0, 2)}••••${d.slice(-2)}`));

const newState = () => ({ open: null, sel: null, close: null, allp: null, votes: [], durs: [], hab: null, off: 0, id: {} });
const sameId = (id, want) => !!(id.M && want) && [id.M, id.Z, id.S].map(Number).join('-') === want.split('-').map(Number).join('-');
/** texto do log -> linhas e resumo por turno (horas em Brasília; exterior fica na hora local).
 *  Urna trocada no dia: o texto traz um bloco por arquivo ("### arquivo: …"). O 2º turno soma as urnas desta seção;
 *  o 1º turno só vale da urna que estava nesta mesma seção (want = "mun-zona-secao"). */
export function parseUrnaLog(text, isExt, want = null) {
  const lines = [], blocks = [[newState(), newState()]];
  let B = blocks[0];
  for (const raw of text.split('\n')) {
    if (raw.startsWith('### arquivo:')) { B = [newState(), newState()]; blocks.push(B); lines.push({ d: '', t: '', sec: 0, lvl: '', app: '', msg: raw.slice(4).trim(), turno: 0, k: 'arquivo', blk: blocks.length - 1 }); continue; }
    if (raw.length < 20) continue;
    const p = raw.split('\t');
    const d = raw.slice(0, 10), tt = DAYS[d] || 0;
    const sec = +raw.slice(11, 13) * 3600 + +raw.slice(14, 16) * 60 + +raw.slice(17, 19);
    // títulos de eleitor de mesários (9+ dígitos) ficam mascarados na tela; o download é o arquivo original
    const L = { d, t: raw.slice(11, 19), sec, lvl: p[1] || '', app: p[3] || '', msg: mask(p[4] || ''), turno: tt, k: '', blk: blocks.length - 1 };
    lines.push(L);
    if (!tt) continue;
    const s = B[tt - 1];
    if (/^(Município|Zona Eleitoral|Seção Eleitoral): /.test(p[4] || '')) { const [k, v] = p[4].split(': '); s.id[k[0]] = v.trim(); }
    if (L.msg === VOTO) { L.k = 'voto'; s.votes.push(sec); s.durs.push(s.hab != null && sec >= s.hab ? sec - s.hab : null); s.hab = null; }
    else if (L.msg === HAB) s.hab = sec;
    else {
      for (const [m, k] of KEY_EVENTS) if (L.msg.startsWith(m)) { L.k = k; break; }
      if (L.k === 'abertura' && s.open == null) s.open = sec;
      if (L.k === 'encerrar') s.sel = sec;
      if (L.k === 'encerrada') s.close = sec;
      if (L.k === 'todos votaram?') s.allp = /SIM\s*$/.test(L.msg);
    }
  }
  const T = {}, use = { 1: new Set(), 2: new Set() };
  for (const t of [1, 2]) {
    const ok = blocks.map((b, i) => [b[t - 1], i]).filter(([s]) => s.votes.length || s.open != null)
      .filter(([s]) => (t === 2 ? !s.id.M || !want || sameId(s.id, want) : !want || sameId(s.id, want)));
    if (t === 1 && !want && ok.length > 1) ok.length = 1;
    const s = newState();
    for (const [b, i] of ok) {
      use[t].add(i);
      b.votes.forEach((x, k) => { s.votes.push(x); s.durs.push(b.durs[k]); });
      if (b.open != null && (s.open == null || b.open < s.open)) s.open = b.open;
      if (b.sel != null && (s.sel == null || b.sel > s.sel)) s.sel = b.sel;
      if (b.close != null && (s.close == null || b.close > s.close)) { s.close = b.close; s.allp = b.allp; }
    }
    const ord = s.votes.map((x, k) => k).sort((a, b) => s.votes[a] - s.votes[b]);
    s.votes = ord.map((k) => s.votes[k]); s.durs = ord.map((k) => s.durs[k]);
    s.otherSec = t === 1 && !ok.length && blocks.some((b) => b[0].votes.length);
    s.nUrnas = ok.length;
    T[t] = s;
  }
  for (const L of lines) if (L.turno && !use[L.turno].has(L.blk)) L.excl = true;
  for (const t of [1, 2]) {
    const s = T[t];
    // relógio da urna = hora local; abertura às 8h de Brasília em todo o país (exterior: hora local, sem conversão)
    s.off = !isExt && s.open != null ? Math.max(-3, Math.min(1, Math.round((s.open - 8 * 3600) / 3600))) : 0;
    const sh = -s.off * 3600;
    s.vb = s.votes.map((x) => x + sh);
    s.late = s.vb.filter((x) => x >= 17 * 3600).length;
    s.late18 = s.vb.filter((x) => x >= 18 * 3600).length;
    const ds = s.durs.filter(isNum).sort((a, b) => a - b);
    s.fast = ds.length ? ds[0] : null; s.med = ds.length ? ds[Math.floor(ds.length / 2)] : null;
  }
  for (const L of lines) if (L.turno) L.brt = L.sec - T[L.turno].off * 3600;
  return { lines, T, nArq: blocks.length - (blocks[0][0].votes.length || blocks[0][1].votes.length || blocks[0][0].open != null || blocks[0][1].open != null ? 0 : 1) };
}

/** linha do tempo da urna: votos acumulados (1º turno tracejado × 2º turno) e um "código de barras" com um traço por voto,
 *  os de depois das 17h em teal; ▲ abertura, ■ encerramento. Zoom: dia todo ou 16h30–17h30. */
function chart(T, zoom = 'dia', w = 720, h = 210) {
  const ns = 'http://www.w3.org/2000/svg', svg = document.createElementNS(ns, 'svg');
  svg.setAttribute('viewBox', `0 0 ${w} ${h}`); svg.setAttribute('width', '100%'); svg.setAttribute('role', 'img');
  svg.setAttribute('aria-label', 'Votos acumulados na urna, 1º e 2º turno, hora de Brasília');
  const add = (tag, a, txt) => { const e = document.createElementNS(ns, tag); for (const k in a) e.setAttribute(k, a[k]); if (txt != null) e.textContent = txt; svg.append(e); return e; };
  const [t0, t1] = zoom === '17h' ? [16.5 * 3600, 17.5 * 3600] : zoom === 'tarde' ? [16.75 * 3600, 19.5 * 3600] : [7.5 * 3600, 19.5 * 3600];
  const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
  const ink = css('--ink'), ink2 = css('--ink-2'), muted = css('--muted'), grid = css('--grid'), teal = css('--teal');
  const m = { l: 40, r: 64, t: 16, b: 58 }, iw = w - m.l - m.r, ih = h - m.t - m.b;
  const X = (sec) => m.l + ((Math.max(t0, Math.min(t1, sec)) - t0) / (t1 - t0)) * iw;
  const cnt = (vb) => vb.filter((x) => x < t0).length;
  const ymax = Math.max(4, ...[1, 2].map((t) => T[t].vb.filter((x) => x <= t1).length)), y0 = zoom === 'dia' ? 0 : Math.min(...[1, 2].map((t) => cnt(T[t].vb)));
  const Y = (v) => m.t + ih - ((v - y0) / Math.max(1, ymax - y0)) * ih;
  add('rect', { x: X(17 * 3600), y: m.t, width: X(19 * 3600) - X(17 * 3600), height: ih + 44, fill: teal, opacity: 0.08 });
  const step = zoom === 'dia' ? 3600 : 600;
  for (let t = Math.ceil(t0 / step) * step; t <= t1; t += step) {
    add('line', { x1: X(t), x2: X(t), y1: m.t, y2: m.t + ih, stroke: grid, 'stroke-width': 1 });
    add('text', { x: X(t), y: h - 4, 'text-anchor': 'middle', 'font-size': 10, fill: muted }, `${Math.floor(t / 3600)}h${t % 3600 ? String((t % 3600) / 60).padStart(2, '0') : ''}`);
  }
  for (const [hh, dash] of [[17, ''], [19, '4 3']]) if (hh * 3600 >= t0 && hh * 3600 <= t1) add('line', { x1: X(hh * 3600), x2: X(hh * 3600), y1: m.t - 4, y2: m.t + ih + 44, stroke: ink, 'stroke-width': 1.3, 'stroke-dasharray': dash });
  add('text', { x: X(17 * 3600) + 4, y: m.t + 2, 'font-size': 10, 'font-weight': 700, fill: ink }, 'só no 2º turno: prorrogação');
  add('text', { x: m.l - 6, y: Y(ymax) + 4, 'text-anchor': 'end', 'font-size': 10, fill: muted }, String(ymax));
  add('text', { x: m.l - 6, y: Y(y0) + 4, 'text-anchor': 'end', 'font-size': 10, fill: muted }, String(y0));
  for (const [t, col, wd, dash, lab] of [[1, ink2, 1.6, '5 3', '1º turno'], [2, ink, 2.5, '', '2º turno']]) {
    const vb = T[t].vb; if (!vb.length) continue;
    let n = cnt(vb); const pts = [`${X(t0)},${Y(n)}`];
    for (const x of vb) { if (x < t0 || x > t1) continue; pts.push(`${X(x)},${Y(n)}`); n++; pts.push(`${X(x)},${Y(n)}`); }
    const tEnd = Math.min(t1, vb[vb.length - 1]); pts.push(`${X(tEnd)},${Y(n)}`);
    add('polyline', { points: pts.join(' '), fill: 'none', stroke: col, 'stroke-width': wd, 'stroke-dasharray': dash, 'stroke-linejoin': 'round' });
    add('text', { x: X(tEnd) + 5, y: Y(n) + 4, 'font-size': 10.5, 'font-weight': 700, fill: col }, `${lab} · ${n}`);
  }
  // código de barras: uma faixa por turno
  [[2, m.t + ih + 10], [1, m.t + ih + 28]].forEach(([t, y]) => {
    const s = T[t];
    add('text', { x: m.l - 6, y: y + 9, 'text-anchor': 'end', 'font-size': 9.5, fill: muted }, t === 2 ? '2T' : '1T');
    const hl = (x) => t === 2 && x >= 17 * 3600; // no 1º turno não houve prorrogação: voto depois das 17h ali é fila
    for (const x of s.vb) if (x >= t0 && x <= t1) add('line', { x1: X(x), x2: X(x), y1: y, y2: y + 12, stroke: hl(x) ? teal : ink2, 'stroke-width': 1, opacity: hl(x) ? 1 : 0.55 });
    const sh = -s.off * 3600;
    if (s.open != null && s.open + sh >= t0) add('text', { x: X(s.open + sh), y: y + 10, 'text-anchor': 'middle', 'font-size': 10, fill: ink }, '▲');
    if (s.close != null && s.close + sh <= t1) add('text', { x: X(s.close + sh), y: y + 10, 'text-anchor': 'middle', 'font-size': 10, fill: ink }, '■');
  });
  return svg;
}
function chartBox(T, zoom0) {
  const box = el('div', 'ul-chart'), seg = el('div', 'seg ul-zoom'), holder = el('div');
  const draw = (z) => { holder.replaceChildren(chart(T, z)); seg.querySelectorAll('button').forEach((b) => b.classList.toggle('on', b.dataset.z === z)); };
  for (const [z, t] of [['dia', 'dia todo'], ['17h', '16h30–17h30'], ['tarde', '16h45–19h30']]) { const b = el('button', null, t); b.dataset.z = z; b.onclick = () => draw(z); seg.append(b); }
  const lg = el('div', 'ul-legend'); lg.append(el('span', null, 'votos acumulados · traços = um voto cada (2º turno depois das 17h em verde-azulado; no 1º turno não houve prorrogação, voto depois das 17h é fila) · ▲ abertura · ■ encerramento · hora de Brasília'));
  box.append(seg, holder, lg); draw(zoom0);
  return box;
}

function statBlock(t, s) {
  const b = el('div', 'ul-stat');
  b.append(el('div', 'kicker', t === 2 ? '2º turno · 25/10' : '1º turno · 04/10'));
  if (!s.votes.length) { b.append(el('div', 'sub', t === 1 ? (s.otherSec ? 'no 1º turno esta urna estava em outra seção: fora da comparação' : 'sem registro do 1º turno neste log (outra urna no 1º turno)') : 'sem votos no log')); return b; }
  const row = (k, v) => { const r = el('div', 'r'); r.append(el('span', null, k), el('b', null, v)); b.append(r); };
  const f = (x) => (x == null ? '—' : hms(x - s.off * 3600));
  row('Votos no log', fInt(s.votes.length) + (s.nUrnas > 1 ? ` (${s.nUrnas} urnas)` : ''));
  row('Abertura · encerramento', `${f(s.open)} · ${f(s.sel)}`);
  row('Último voto', hms(s.vb[s.vb.length - 1]));
  row('Depois das 17h', `${fInt(s.late)} (${fPct(100 * s.late / s.votes.length, 1)})`);
  if (s.late18) row('Depois das 18h', fInt(s.late18));
  if (s.allp != null) row('“Todos os presentes votaram?”', s.allp ? 'sim' : 'não');
  if (s.fast != null) row('Voto mais rápido · mediana', `${s.fast} s · ${s.med} s`);
  return b;
}

let open = null;
/** abre o visor. info: {uf, cdMun, zona, secao, title, sub, isExt} */
export async function openUrnaLog(info) {
  closeUrnaLog();
  const bg = el('div', 'ul-bg'), box = el('div', 'ul-box');
  bg.append(box); document.body.append(bg);
  const onKey = (e) => { if (e.key === 'Escape') closeUrnaLog(); };
  document.addEventListener('keydown', onKey);
  open = { bg, onKey, onClose: info.onClose };
  bg.addEventListener('click', (e) => { if (e.target === bg) closeUrnaLog(); });
  const head = el('div', 'ul-head'), hl = el('div');
  hl.append(el('div', 'kicker', 'Log da urna'), el('div', 'name', info.title), el('div', 'sub', info.sub || ''));
  const x = el('button', 'icon-btn', '×'); x.title = 'Fechar (Esc)'; x.onclick = closeUrnaLog;
  head.append(hl, x); box.append(head);
  const body = el('div', 'ul-body', 'Carregando o log…'); box.append(body);
  let parsed;
  try { parsed = parseUrnaLog(await fetchUrnaLog(info.uf, info.cdMun, info.zona, info.secao), info.isExt, `${info.cdMun}-${info.zona}-${info.secao}`); }
  catch (e) { body.textContent = `Não consegui abrir o log: ${e.message}`; return; }
  if (!open || open.bg !== bg) return;
  const { lines, T } = parsed;
  body.replaceChildren();
  const offTxt = T[2].off ? ` · relógio da urna em hora local (${T[2].off > 0 ? '+' : '−'}${Math.abs(T[2].off)} h de Brasília); resumo e gráfico convertidos para Brasília` : info.isExt ? ' · exterior: hora local do país' : '';
  body.append(el('div', 'sub', `${fInt(lines.length)} linhas, da carga da urna ao fim do 2º turno${offTxt}.${parsed.nArq > 1 ? ` Urna trocada no dia: ${parsed.nArq} arquivos de log (separados abaixo); linhas de outra seção ficam esmaecidas.` : ''}`));
  const top = el('div', 'ul-top'); top.append(chartBox(T, info.zoom || 'dia'), statBlock(2, T[2]), statBlock(1, T[1])); body.append(top);
  // filtros
  const bar = el('div', 'ul-bar'); const st = { turno: 2, vota: true, q: '' };
  const seg = el('div', 'seg');
  for (const [v, t] of [[2, '2º turno'], [1, '1º turno'], [0, 'Tudo']]) { const b = el('button', null, t); b.dataset.v = v; b.onclick = () => { st.turno = v; draw(); }; seg.append(b); }
  const cb = el('label', 'ul-cb'), ci = el('input'); ci.type = 'checkbox'; ci.checked = true; ci.onchange = () => { st.vota = ci.checked; draw(); }; cb.append(ci, ' só a votação (app VOTA)');
  const q = el('input', 'ul-q'); q.placeholder = 'filtrar texto (ex.: encerr, voto, biometria)'; q.oninput = () => { st.q = q.value.trim().toLowerCase(); draw(); };
  const go17 = el('button', 'chip', '↓ 17h'); go17.title = 'Ir para o primeiro registro a partir das 17h (Brasília)';
  const dl = el('a', 'linkbtn', 'baixar original (.txt)'); dl.title = 'Arquivo original do TSE: contém os títulos de eleitor dos mesários, mascarados na tela'; dl.href = `logs/${info.uf}/${String(info.cdMun).padStart(5, '0')}/${String(info.zona).padStart(4, '0')}-${String(info.secao).padStart(4, '0')}.txt`; dl.download = `log-${info.uf}-${info.cdMun}-${info.zona}-${info.secao}.txt`;
  bar.append(seg, cb, q, go17, dl); body.append(bar);
  const list = el('div', 'ul-lines'); body.append(list);
  function draw() {
    seg.querySelectorAll('button').forEach((b) => b.classList.toggle('on', +b.dataset.v === st.turno));
    const out = [];
    for (let i = 0; i < lines.length; i++) {
      const L = lines[i];
      if (L.k === 'arquivo') { out.push(`<div class="arq"><span class="ms">${esc(L.msg)}</span></div>`); continue; }
      if (st.turno && L.turno !== st.turno) continue;
      if (st.vota && L.app !== 'VOTA') continue;
      if (st.q && !L.msg.toLowerCase().includes(st.q)) continue;
      const late = L.turno && L.brt >= 17 * 3600;
      const cls = `${L.k === 'voto' ? 'v' : L.k ? 'k' : ''}${late && L.turno === 2 ? ' late' : ''}${L.excl ? ' excl' : ''}`;
      out.push(`<div class="${cls}" data-i="${i}"><span class="tm">${L.d.slice(0, 5)} ${L.t}</span><span class="ap">${esc(L.app)}</span><span class="ms">${esc(L.msg)}${L.k && L.k !== 'voto' ? ` <em>${L.k}</em>` : ''}</span></div>`);
      if (out.length >= 20000) break;
    }
    list.innerHTML = out.length ? out.join('') : '<div class="sub">nenhuma linha com esses filtros</div>';
  }
  go17.onclick = () => { const r = list.querySelector('.late'); if (r) r.scrollIntoView({ block: 'center' }); else go17.textContent = 'nada após 17h'; };
  draw();
}

export function closeUrnaLog() {
  if (!open) return;
  document.removeEventListener('keydown', open.onKey);
  open.bg.remove();
  const cb = open.onClose; open = null;
  cb?.();
}

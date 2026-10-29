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

/** texto do log -> linhas e resumo por turno (horas em Brasília; exterior fica na hora local) */
export function parseUrnaLog(text, isExt) {
  const lines = [], T = {};
  for (const t of [1, 2]) T[t] = { open: null, sel: null, close: null, allp: null, votes: [], durs: [], hab: null, off: 0 };
  for (const raw of text.split('\n')) {
    if (raw.length < 20) continue;
    const p = raw.split('\t');
    const d = raw.slice(0, 10), tt = DAYS[d] || 0;
    const sec = +raw.slice(11, 13) * 3600 + +raw.slice(14, 16) * 60 + +raw.slice(17, 19);
    const L = { d, t: raw.slice(11, 19), sec, lvl: p[1] || '', app: p[3] || '', msg: p[4] || '', turno: tt, k: '' };
    lines.push(L);
    if (!tt) continue;
    const s = T[tt];
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
  return { lines, T };
}

function chart(T, w = 720, h = 170) {
  const ns = 'http://www.w3.org/2000/svg', svg = document.createElementNS(ns, 'svg');
  svg.setAttribute('viewBox', `0 0 ${w} ${h}`); svg.setAttribute('width', '100%'); svg.setAttribute('role', 'img');
  svg.setAttribute('aria-label', 'Votos a cada 10 minutos, 1º e 2º turno, hora de Brasília');
  const add = (tag, a, txt) => { const e = document.createElementNS(ns, tag); for (const k in a) e.setAttribute(k, a[k]); if (txt != null) e.textContent = txt; svg.append(e); return e; };
  const t0 = 7 * 3600, t1 = 19.5 * 3600, step = 600, nb = (t1 - t0) / step;
  const bins = (arr) => { const b = new Array(nb).fill(0); for (const x of arr) { const i = Math.floor((x - t0) / step); if (i >= 0 && i < nb) b[i]++; } return b; };
  const b1 = bins(T[1].vb), b2 = bins(T[2].vb), ymax = Math.max(4, ...b1, ...b2);
  const m = { l: 34, r: 10, t: 14, b: 22 }, iw = w - m.l - m.r, ih = h - m.t - m.b;
  const X = (sec) => m.l + ((sec - t0) / (t1 - t0)) * iw, Y = (v) => m.t + ih - (v / ymax) * ih, bw = iw / nb;
  const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
  const amber = document.documentElement.dataset.theme === 'light' ? '#c47a00' : '#f2a516';
  add('rect', { x: X(17 * 3600), y: m.t, width: X(19 * 3600) - X(17 * 3600), height: ih, fill: amber, opacity: 0.1 });
  for (let hh = 8; hh <= 19; hh++) {
    add('line', { x1: X(hh * 3600), x2: X(hh * 3600), y1: m.t, y2: m.t + ih, stroke: css('--grid'), 'stroke-width': hh === 17 || hh === 19 ? 0 : 1 });
    add('text', { x: X(hh * 3600), y: h - 6, 'text-anchor': 'middle', 'font-size': 10, fill: css('--muted') }, `${hh}h`);
  }
  b2.forEach((v, i) => { if (v) add('rect', { x: m.l + i * bw + 1, y: Y(v), width: bw - 2, height: m.t + ih - Y(v), fill: css('--ink-2'), opacity: 0.85 }); });
  add('polyline', { points: b1.map((v, i) => `${m.l + (i + 0.5) * bw},${Y(v)}`).join(' '), fill: 'none', stroke: amber, 'stroke-width': 2, 'stroke-linejoin': 'round' });
  for (const [hh, dash] of [[17, ''], [19, '4 3']]) add('line', { x1: X(hh * 3600), x2: X(hh * 3600), y1: m.t - 4, y2: m.t + ih, stroke: css('--ink'), 'stroke-width': 1.5, 'stroke-dasharray': dash });
  add('text', { x: (X(17 * 3600) + X(19 * 3600)) / 2, y: m.t + 4, 'text-anchor': 'middle', 'font-size': 10, 'font-weight': 700, fill: css('--ink') }, 'prorrogação');
  add('text', { x: m.l - 6, y: Y(ymax) + 4, 'text-anchor': 'end', 'font-size': 10, fill: css('--muted') }, String(ymax));
  add('text', { x: m.l - 6, y: Y(0) + 4, 'text-anchor': 'end', 'font-size': 10, fill: css('--muted') }, '0');
  const lg = el('div', 'ul-legend');
  const sw = (c, t, line) => { const i = el('i'); i.style.background = c; if (line) i.className = 'ln'; lg.append(i, el('span', null, t)); };
  sw(css('--ink-2'), '2º turno (barras, votos a cada 10 min)'); sw(amber, '1º turno, mesma urna (linha)', true);
  const box = el('div', 'ul-chart'); box.append(svg, lg);
  return box;
}

function statBlock(t, s) {
  const b = el('div', 'ul-stat');
  b.append(el('div', 'kicker', t === 2 ? '2º turno · 25/10' : '1º turno · 04/10'));
  if (!s.votes.length) { b.append(el('div', 'sub', t === 1 ? 'sem registro do 1º turno neste log (outra urna no 1º turno)' : 'sem votos no log')); return b; }
  const row = (k, v) => { const r = el('div', 'r'); r.append(el('span', null, k), el('b', null, v)); b.append(r); };
  const f = (x) => (x == null ? '—' : hms(x - s.off * 3600));
  row('Votos no log', fInt(s.votes.length));
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
  try { parsed = parseUrnaLog(await fetchUrnaLog(info.uf, info.cdMun, info.zona, info.secao), info.isExt); }
  catch (e) { body.textContent = `Não consegui abrir o log: ${e.message}`; return; }
  if (!open || open.bg !== bg) return;
  const { lines, T } = parsed;
  body.replaceChildren();
  const offTxt = T[2].off ? ` · relógio da urna em hora local (${T[2].off > 0 ? '+' : '−'}${Math.abs(T[2].off)} h de Brasília); resumo e gráfico convertidos para Brasília` : info.isExt ? ' · exterior: hora local do país' : '';
  body.append(el('div', 'sub', `${fInt(lines.length)} linhas, da carga da urna ao fim do 2º turno${offTxt}.`));
  const top = el('div', 'ul-top'); top.append(chart(T), statBlock(2, T[2]), statBlock(1, T[1])); body.append(top);
  // filtros
  const bar = el('div', 'ul-bar'); const st = { turno: 2, vota: true, q: '' };
  const seg = el('div', 'seg');
  for (const [v, t] of [[2, '2º turno'], [1, '1º turno'], [0, 'Tudo']]) { const b = el('button', null, t); b.dataset.v = v; b.onclick = () => { st.turno = v; draw(); }; seg.append(b); }
  const cb = el('label', 'ul-cb'), ci = el('input'); ci.type = 'checkbox'; ci.checked = true; ci.onchange = () => { st.vota = ci.checked; draw(); }; cb.append(ci, ' só a votação (app VOTA)');
  const q = el('input', 'ul-q'); q.placeholder = 'filtrar texto (ex.: encerr, voto, biometria)'; q.oninput = () => { st.q = q.value.trim().toLowerCase(); draw(); };
  const go17 = el('button', 'chip', '↓ 17h'); go17.title = 'Ir para o primeiro registro a partir das 17h (Brasília)';
  const dl = el('a', 'linkbtn', 'baixar .txt'); dl.href = `logs/${info.uf}/${String(info.cdMun).padStart(5, '0')}/${String(info.zona).padStart(4, '0')}-${String(info.secao).padStart(4, '0')}.txt`; dl.download = `log-${info.uf}-${info.cdMun}-${info.zona}-${info.secao}.txt`;
  bar.append(seg, cb, q, go17, dl); body.append(bar);
  const list = el('div', 'ul-lines'); body.append(list);
  function draw() {
    seg.querySelectorAll('button').forEach((b) => b.classList.toggle('on', +b.dataset.v === st.turno));
    const out = [];
    for (let i = 0; i < lines.length; i++) {
      const L = lines[i];
      if (st.turno && L.turno !== st.turno) continue;
      if (st.vota && L.app !== 'VOTA') continue;
      if (st.q && !L.msg.toLowerCase().includes(st.q)) continue;
      const late = L.turno && L.brt >= 17 * 3600;
      const cls = `${L.k === 'voto' ? 'v' : L.k ? 'k' : ''}${late ? ' late' : ''}`;
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

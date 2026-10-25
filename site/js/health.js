// health.js — selos de frescor e faixas de alerta, a partir do manifest/analytics (contracts §5 e §6.2).
import { parseIso, parseBrt, hhmm, fAge, isNum, fimVotacao, fimTxt } from './fmt.js';

/** @returns {{collector:{level,text,title}, tse:{level,text,title}, banners:{level,text}[], counting:boolean, source:string}} */
export function freshness(feed, now = Date.now()) {
  const m = feed.manifest, h = m?.health || {}, an = feed.an, br = feed.agg?.br;
  const banners = [];
  const out = { banners, counting: false, source: m?.source || null };

  // --- coletor ---
  const gen = parseIso(m?.generated_at);
  const age = gen ? (now - gen.getTime()) / 1000 : null;
  if (!m) {
    out.collector = { level: feed.err.manifest ? 'crit' : 'warn', text: 'Coletor: sem manifest', title: feed.err.manifest || 'aguardando o primeiro manifest.json' };
    if (feed.err.manifest && feed.ticks > 1) banners.push({ level: 'crit', text: `Sem dados do coletor (${feed.base}manifest.json: ${feed.err.manifest}). A tela mostra só a base do 1º turno.` });
  } else {
    const lvl = age > 120 ? 'crit' : age > 60 ? 'warn' : 'ok';
    out.collector = { level: lvl, text: lvl === 'ok' ? `Coletor OK há ${fAge(age)}` : lvl === 'warn' ? `Coletor atrasado há ${fAge(age)}` : `Coletor parado há ${fAge(age)}`,
      title: `manifest v${m.version} gerado ${gen ? gen.toLocaleTimeString('pt-BR') : '?'} · erros 5 min: ${h.errors_5min ?? '—'} · fila BU: ${h.bu_queue ?? '—'}` };
    if (age > 120) banners.push({ level: 'crit', text: `Dados velhos: o coletor não publica há ${fAge(age)}. Os números abaixo podem estar desatualizados.` });
    if (feed.err.manifest && feed.okAt.manifest && now - feed.okAt.manifest > 30000)
      banners.push({ level: 'crit', text: `Sem resposta do servidor local há ${fAge((now - feed.okAt.manifest) / 1000)} (${feed.err.manifest}).` });
  }

  // --- TSE ---
  const stamp = parseBrt(h.tse_br_dg_hg) || parseBrt(m?.tse ? `${m.tse.br_dg} ${m.tse.br_hg}` : null) || parseBrt(br ? `${br.dt} ${br.ht}` : null);
  const lastChange = parseIso(h.tse_last_change);
  const ref = lastChange || stamp;
  const tAge = ref ? (now - ref.getTime()) / 1000 : null;
  const st = br?.st ?? 0, ts = br?.ts ?? 0;
  const phase = an?.status?.phase;
  out.counting = st > 0 && (ts ? st < ts : true) && phase !== 'final';
  const close = fimVotacao(now).getTime(), voting = now < close;
  if (!m) out.tse = { level: 'muted', text: 'TSE: —', title: '' };
  else if (!st) {
    out.tse = { level: 'muted', text: voting ? `TSE: apuração começa às ${fimTxt()}` : 'TSE: aguardando a 1ª totalização', title: stamp ? `arquivo BR de ${hhmm(stamp)}` : '' };
  } else if (voting) {
    // votação ainda aberta: o TSE segura a divulgação e o parcial fica parado; não é falha
    out.tse = { level: 'muted', text: `TSE: divulgação suspensa até as ${fimTxt()}`, title: `última totalização ${stamp ? hhmm(stamp) : '—'}` };
    banners.push({ level: 'info', text: `Votação prorrogada até as ${fimTxt()}: o TSE suspendeu a divulgação até lá. O parcial (último carimbo ${stamp ? hhmm(stamp) : '—'}) fica parado.` });
  } else {
    // a pausa conta a partir do fim da votação, não do último carimbo antes dele
    const pAge = ref ? (now - Math.max(ref.getTime(), close)) / 1000 : null;
    const lvl = !out.counting ? 'ok' : !isNum(pAge) ? 'warn' : pAge > 300 ? 'crit' : pAge > 180 ? 'warn' : 'ok';
    out.tse = { level: lvl, text: `TSE: última totalização ${stamp ? hhmm(stamp) : '—'}${isNum(tAge) ? ` (há ${fAge(tAge)})` : ''}`,
      title: `carimbo BR ${h.tse_br_dg_hg || '—'} · última mudança vista pelo coletor ${lastChange ? lastChange.toLocaleTimeString('pt-BR') : '—'}${isNum(h.agg_lag_s) ? ` · atraso de zonas ${Math.round(h.agg_lag_s)} s` : ''}` };
    if (out.counting && isNum(pAge) && pAge > 300) banners.push({ level: 'warn', text: ref.getTime() < close
      ? `O TSE ainda não retomou a divulgação (votação encerrada há ${fAge(pAge)}; último carimbo ${stamp ? hhmm(stamp) : '—'}).`
      : `O TSE não publica novas totalizações há ${fAge(pAge)} (último carimbo ${stamp ? hhmm(stamp) : '—'}).` });
  }
  if (h.br_frozen) banners.push({ level: 'warn', text: 'Arquivo nacional do TSE parado: o placar do Brasil está sendo reconstruído pela soma das UFs.' });
  if (feed.indexOk === false) banners.push({ level: 'crit', text: `Índice de seções divergente (${feed.indexMsg}). Boletins por seção ignorados; o mapa usa só os swings das zonas.` });
  if (h.lanes?.bu?.paused_until) { const p = parseIso(h.lanes.bu.paused_until); if (p && p.getTime() > now) banners.push({ level: 'info', text: `Coleta de boletins pausada pelo TSE até ${hhmm(p)} (os agregados seguem normais).` }); }

  // --- analytics ---
  if (m && !an && feed.err.analytics && feed.ticks > 2) banners.push({ level: 'warn', text: `analytics.json indisponível (${feed.err.analytics}). Termômetro, ritmo e projeção aguardam.` });
  if (an && m) {
    const ca = parseIso(an.computed_at), anAge = ca ? (now - ca.getTime()) / 1000 : null;
    const dvAgg = m.data_versions?.agg;
    if (isNum(anAge) && anAge > 180 && isNum(dvAgg) && isNum(an.input_version) && an.input_version < dvAgg)
      banners.push({ level: 'warn', text: `Analytics atrasado: calculado há ${fAge(anAge)} sobre a versão ${an.input_version} dos agregados (atual: ${dvAgg}).` });
  }
  if (feed.err.agg && feed.agg && m && feed.okAt.agg && now - feed.okAt.agg > 90000) banners.push({ level: 'warn', text: `Não consegui baixar ${m.files?.agg}: ${feed.err.agg}. Mostrando a versão anterior.` });
  return out;
}

export function renderBadges(root, fr, el) {
  root.replaceChildren();
  for (const k of ['collector', 'tse']) {
    const b = fr[k]; if (!b) continue;
    const s = el('span', `badge lvl-${b.level}`); s.title = b.title || '';
    s.append(el('span', 'dot'), b.text);
    root.append(s);
  }
  if (fr.source && fr.source !== 'live') { const s = el('span', 'badge lvl-info', `ENSAIO · fonte: ${fr.source}`); root.append(s); }
}

export function renderBanners(root, fr, el) {
  root.replaceChildren();
  for (const b of fr.banners) root.append(el('div', `banner lvl-${b.level}`, b.text));
  root.hidden = !fr.banners.length;
}

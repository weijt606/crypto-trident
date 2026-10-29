"use strict";
const $ = s => document.querySelector(s);
const fmtTs = ts => new Date(ts).toLocaleString(undefined,{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'});
const fmtN = (v,d=2) => v==null || Number.isNaN(Number(v)) ? '—' : Number(v).toLocaleString('en-US',{minimumFractionDigits:d,maximumFractionDigits:d});
const cls = v => v==null ? 'muted' : v>=0 ? 'up' : 'down';
const sign = (v,d=2) => v==null ? '—' : (v>=0?'+':'') + fmtN(v,d);
const pctS = v => v==null ? '—' : (v>=0?'+':'') + (v*100).toFixed(2) + '%';
const ESC = {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'};
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ESC[c]);
function ago(ts) {
  if (!ts) return '—';
  const s = Math.max(0, (Date.now()-ts)/1000);
  if (s < 90) return Math.round(s)+'s ago';
  if (s < 5400) return Math.round(s/60)+'m ago';
  if (s < 172800) return Math.round(s/3600)+'h ago';
  return Math.round(s/86400)+'d ago';
}
const TF_MS = {'5m':3e5,'15m':9e5,'1h':36e5,'4h':1.44e7,'1d':8.64e7};
function tilNextBar(tf) {
  const ms = TF_MS[tf]; if (!ms) return '—';
  const left = (Math.floor(Date.now()/ms)+1)*ms - Date.now();
  const h = Math.floor(left/3.6e6), m = Math.round((left%3.6e6)/6e4);
  return h>0 ? `in ${h}h ${m}m` : `in ${m}m`;
}
const ls = { get:k=>{try{return localStorage.getItem(k)}catch(e){return null}}, set:(k,v)=>{try{localStorage.setItem(k,v)}catch(e){}} };

// Deep links: ?view=paper&panel=market&chart=pnl
const QS = new URLSearchParams(location.search);
let state = { bot:null, symbol:null, tf:'1h',
  view: (QS.get('view') || ls.get('tdview')) === 'paper' ? 'paper' : 'live', showRetired:false,
  chart: QS.get('chart') || ls.get('tdchart') || 'equity', panel: QS.get('panel') || ls.get('tdpanel') || 'fills' };
let cache = { bots:[], events:[], alerts:[], universe:[], benchmark:'BTC/USDT', liveSum:null };
function inView(p) { return state.view==='live' ? p.stage==='live' : p.stage!=='live'; }

const charts = {};
const mkChart = id => charts[id] ||= echarts.init(document.getElementById(id), null, {renderer:'canvas'});
const GRID = {left:52, right:16, top:28, bottom:28};
const AXIS = {axisLine:{lineStyle:{color:'#2d3b52'}}, axisLabel:{color:'#7a879c'}, splitLine:{lineStyle:{color:'#1f2a3b'}}};
const TIP = {backgroundColor:'#1a2535', borderColor:'#2d3b52', textStyle:{color:'#d8e1ee'}};
const ACCENT = '#3fb6d9';

async function api(path, params={}) {
  try {
    const q = new URLSearchParams(params).toString();
    const r = await fetch(path + (q ? '?'+q : ''));
    return await r.json();
  } catch (e) { return {error:'network: '+e.message}; }
}

// ================= view / chart tabs / panel tabs =================
function switchView(v) {
  if (state.view === v) return;
  state.view = v; ls.set('tdview', v);
  state.bot = null;
  applyView(); refreshAll();
}
function applyView() {
  document.body.className = 'view-' + state.view;
  $('#tabLive').classList.toggle('active', state.view==='live');
  $('#tabPaper').classList.toggle('active', state.view==='paper');
  if (!['equity','pnl'].includes(state.chart)) state.chart = 'equity';
  if (!document.getElementById('p-'+state.panel)) state.panel = 'fills';
  setChart(state.chart, true); setPanel(state.panel, true);
}
function setChart(c, quiet) {
  state.chart = c; ls.set('tdchart', c);
  document.querySelectorAll('#chartSeg button').forEach(b => b.classList.toggle('active', b.dataset.c===c));
  $('#equityChart').hidden = c!=='equity';
  $('#dailyPnlChart').hidden = c!=='pnl';
  if (!quiet) renderChartPane();
  Object.values(charts).forEach(ch => ch.resize());
}
function setPanel(p, quiet) {
  state.panel = p; ls.set('tdpanel', p);
  document.querySelectorAll('#ptabs button').forEach(b => b.classList.toggle('active', b.dataset.p===p));
  document.querySelectorAll('.pane').forEach(el => el.classList.toggle('active', el.id==='p-'+p));
  if (!quiet) renderPanel();
  setTimeout(() => Object.values(charts).forEach(ch => ch.resize()), 0);
}
document.querySelectorAll('#ptabs button').forEach(b => b.addEventListener('click', () => setPanel(b.dataset.p)));
// Event wiring (no inline handlers: the CSP forbids them)
$('#tabLive').addEventListener('click', () => switchView('live'));
$('#tabPaper').addEventListener('click', () => switchView('paper'));
document.querySelectorAll('#chartSeg button').forEach(b => b.addEventListener('click', () => setChart(b.dataset.c)));
$('#fillFilter').addEventListener('change', () => renderTables());


// ================= status pill + attention banner =================
function renderStatus() {
  const P = cache.bots;
  const live = P.filter(p => p.stage==='live');
  const paper = P.filter(p => p.stage==='paper');
  const liveOn = live.filter(p => p.alive===true).length;
  const paperOn = paper.filter(p => p.alive===true).length;
  $('#tabLiveCount').textContent = live.length ? `${liveOn}/${live.length}` : '';
  $('#tabPaperCount').textContent = paper.length ? `${paperOn}/${paper.length}` : '';
  const pill = $('#statusPill');
  if (state.view === 'live') {
    if (!live.length) { pill.innerHTML = `<span class="dot"></span><span class="muted">No live bots</span>`; return; }
    const ok = liveOn === live.length;
    pill.innerHTML = `<span class="dot ${ok?'on':'off'}"></span>Live executors <b class="${ok?'':'down'}">${liveOn}/${live.length}</b><span class="muted">online</span>`;
  } else {
    if (!paper.length) { pill.innerHTML = `<span class="dot"></span><span class="muted">No paper bots</span>`; return; }
    pill.innerHTML = `<span class="dot ${paperOn===paper.length?'on':'warn'}"></span>Paper <b>${paperOn}/${paper.length}</b><span class="muted">online</span>`;
  }
}
function renderAttn() {
  // Only conditions that hold right now: a live bot whose heartbeat stopped,
  // severe events from the last 24h, critical alerts from the last 6h.
  const rows = [];
  for (const p of cache.bots.filter(p => p.stage==='live'))
    if (p.alive === false) rows.push({t:`Live bot <b>${esc(p.name)}</b> is offline (last heartbeat ${ago(p.heartbeat_ts)})`, ts:p.heartbeat_ts});
  const liveNames = new Set(cache.bots.filter(p => p.stage==='live').map(p => p.name));
  const seen = new Set();
  for (const e of cache.events.slice(-30).reverse()) {
    if (!liveNames.has(e.bot)) continue;
    const d = e.detail || '';
    const severe = /kill|halt|liquidat|circuit|auth_fail|flatten_incomplete/i.test(e.kind + ' ' + d) || e.kind==='risk';
    if (!severe || Date.now() - e.ts > 24*3600e3) continue;
    const key = e.kind + e.bot; if (seen.has(key)) continue; seen.add(key);
    rows.push({t:`<b>${esc(e.bot)}</b> ${esc(e.kind)}: ${esc(d.slice(0,160))}`, ts:e.ts});
  }
  for (const a of cache.alerts.filter(a => a.level==='critical' && Date.now()-a.ts < 6*3600e3).slice(0,2))
    rows.push({t:esc(a.text), ts:a.ts});
  const box = $('#attn');
  box.classList.toggle('show', rows.length > 0);
  box.innerHTML = rows.slice(0,4).map(r => `<div class="row"><span class="dot"></span><span>${r.t}</span><span class="t">${r.ts?fmtTs(r.ts):''}</span></div>`).join('');
}

// ================= bot chips + side card =================
function hb(p) {
  if (p.alive === true) return {c:'on', t:'running · heartbeat ' + ago(p.heartbeat_ts)};
  if (p.alive === false) return {c:'off', t:'stopped · heartbeat ' + ago(p.heartbeat_ts)};
  return {c:'', t:'not running'};
}
async function renderBots() {
  const [o, ev] = await Promise.all([api('/api/overview'), api('/api/events')]);
  if (!o.bots) return;
  cache.bots = o.bots;
  cache.universe = o.universe || [];
  cache.benchmark = o.benchmark || cache.universe[0];
  if (!state.symbol || !cache.universe.includes(state.symbol)) state.symbol = cache.benchmark;
  if (ev.events) cache.events = ev.events;
  syncFillFilter(o.bots);
  const inv = o.bots.filter(inView);
  const isMain = p => p.stage==='live' || p.stage==='paper';
  const rest = inv.filter(p => !isMain(p));
  const shown = state.showRetired ? inv : inv.filter(isMain);
  if (!state.bot || !shown.some(p => p.name===state.bot))
    state.bot = ((shown.find(p => p.equity!=null) || shown[0]) || {}).name || null;
  const chips = shown.map(p => {
    const h = hb(p);
    return `<div class="chip ${p.name===state.bot?'active':''} ${p.stage==='retired'?'retired':''}" data-bot="${esc(p.name)}">
      <span class="dot ${h.c}"></span><span class="n">${esc(p.name)}</span>
      ${p.equity!=null && p.pnl_pct!=null ? `<span class="p ${cls(p.pnl_pct)}">${sign(p.pnl_pct)}%</span>` : '<span class="p muted">—</span>'}</div>`;
  }).join('') + (rest.length
    ? `<div class="chip ghost" id="toggleRest">${state.showRetired?'Hide':'Show'} research/retired (${rest.length})</div>` : '');
  const box = $('#botChips');
  box.innerHTML = shown.length > 1 || rest.length ? chips : '';
  box.style.display = shown.length > 1 || rest.length ? '' : 'none';
  box.querySelectorAll('.chip[data-bot]').forEach(el => el.onclick = () => selectBot(el.dataset.bot));
  const t = $('#toggleRest'); if (t) t.onclick = () => { state.showRetired = !state.showRetired; renderBots(); };
  renderBotInfo();
  renderStatus();
}
function selectBot(name) {
  state.bot = name;
  const p = cache.bots.find(x => x.name===name);
  if (p && p.symbol && cache.universe.includes(p.symbol)) state.symbol = p.symbol;
  renderBots(); renderChartPane();
  if (state.panel==='market') { renderSymbolBar(); renderKline(); }
}
function renderBotInfo() {
  const p = cache.bots.find(x => x.name===state.bot);
  const box = $('#botInfo');
  if (!p) { box.innerHTML = `<div class="empty">${state.view==='live'?'No live bots (stage: live)':'No paper or research bots configured'}</div>`; return; }
  const h = hb(p);
  const stateLine = p.equity==null
    ? `<span class="muted">${p.stage==='live'?'Waiting for the first settled bar':'No ledger data yet'}</span>`
    : p.holding ? `<span>In position · value <b class="num">${fmtN(p.position_value)}</b></span>`
                : `<span class="warn">Flat · waiting for an entry signal</span>`;
  box.innerHTML = `
    <div class="bot-name"><span class="dot ${h.c}"></span>${esc(p.name)}<span class="badge ${esc(p.stage)}">${esc(p.stage)}</span></div>
    <div class="bot-sub">${esc(p.strategy)} · ${esc(p.symbol)} ${esc(p.timeframe)} · ${h.t}</div>
    <div class="bot-state">${stateLine}</div>
    ${p.equity!=null ? `<div class="kvs">
      <div class="kv"><span class="k">Equity</span><span class="v">${fmtN(p.equity)}</span></div>
      <div class="kv"><span class="k">Total PnL</span><span class="v ${cls(p.pnl)}">${sign(p.pnl)}${p.pnl_pct!=null?` (${sign(p.pnl_pct)}%)`:''}</span></div>
      <div class="kv"><span class="k">Max drawdown</span><span class="v">${fmtN(p.drawdown_pct)}%</span></div>
      <div class="kv"><span class="k">Position / cash</span><span class="v">${fmtN(p.position_value)} / ${fmtN(p.cash)}</span></div>
      <div class="kv"><span class="k">Fills / fees</span><span class="v">${p.n_fills ?? 0} / ${fmtN(p.fees)}</span></div>
      <div class="kv"><span class="k">Realized PnL</span><span class="v ${cls(p.realized_pnl)}">${sign(p.realized_pnl)}</span></div>
      <div class="kv"><span class="k">Next ${esc(p.timeframe)} close</span><span class="v">${tilNextBar(p.timeframe)}</span></div>
      <div class="kv"><span class="k">Ledger</span><span class="v muted">${esc(p.ledger)}</span></div>
    </div>` : ''}`;
}

// ================= live KPI strip =================
async function renderLiveSummary() {
  if (state.view !== 'live') return;
  const d = await api('/api/live_summary');
  if (!d || !d.available) {
    ['#kEq','#kDay','#kWeek','#kDd','#kNot','#kReal'].forEach(id => { $(id).textContent = '—'; $(id).className = 'v'; });
    $('#kEqS').textContent = 'No live ledger data'; $('#kDdS').textContent = ''; $('#kNotS').textContent = ''; $('#kRealS').textContent = '';
    return;
  }
  cache.liveSum = d;
  $('#kEq').innerHTML = `${fmtN(d.total)}<small>USDT</small>`;
  $('#kEqS').textContent = `${d.n_bots} live bot${d.n_bots===1?'':'s'}`;
  $('#kDay').className = 'v ' + cls(d.day_chg); $('#kDay').textContent = pctS(d.day_chg);
  $('#kWeek').className = 'v ' + cls(d.week_chg); $('#kWeek').textContent = pctS(d.week_chg);
  const dd = d.drawdown_pct ?? 0, lim = d.max_drawdown_limit != null ? d.max_drawdown_limit*100 : null;
  $('#kDd').className = 'v ' + (lim && dd >= lim*0.8 ? 'down' : lim && dd >= lim*0.5 ? 'warn' : '');
  $('#kDd').textContent = fmtN(dd) + '%';
  $('#kDdS').textContent = lim != null ? `halt at ${fmtN(lim,0)}%` : '';
  $('#kNot').textContent = fmtN(d.notional);
  $('#kNotS').textContent = `${d.n_holding}/${d.n_bots} bots in position`;
  $('#kReal').className = 'v ' + cls(d.realized); $('#kReal').textContent = sign(d.realized);
  $('#kRealS').textContent = `fees ${fmtN(d.fees)}`;
}

// ================= charts =================
function renderChartPane() {
  if (state.chart==='pnl') renderDailyPnl(); else renderEquity();
}
async function renderEquity() {
  if (!state.bot) { mkChart('equityChart').clear(); $('#chartMeta').textContent=''; $('#chartSub').textContent=''; return; }
  const d = await api('/api/equity', {bot: state.bot});
  if (d.error || !d.series) return;
  const bn = d.benchmark || cache.benchmark;
  $('#chartMeta').textContent = `${state.bot} vs ${bn} buy & hold · rebased to 100 · capital moves excluded`;
  if (!d.series.length) { mkChart('equityChart').clear(); $('#chartSub').textContent='No equity data yet'; return; }
  const last = d.series[d.series.length-1];
  $('#chartSub').innerHTML = `Now <b>${fmtN(last.equity,1)}</b> · ${esc(bn)} <b>${fmtN(last.bench,1)}</b>`;
  mkChart('equityChart').setOption({
    color:[ACCENT,'#9ba8bb'], grid:GRID, legend:{textStyle:{color:'#9ba8bb'}, top:0, right:0, itemHeight:8},
    tooltip:{trigger:'axis', ...TIP},
    xAxis:{type:'category', data:d.series.map(p=>fmtTs(p.ts)), ...AXIS},
    yAxis:{type:'value', scale:true, ...AXIS},
    series:[
      {name:state.bot, type:'line', symbol:'none', data:d.series.map(p=>p.equity), lineStyle:{color:ACCENT,width:2},
       areaStyle:{color:new echarts.graphic.LinearGradient(0,0,0,1,[{offset:0,color:'rgba(63,182,217,.14)'},{offset:1,color:'rgba(63,182,217,0)'}])}},
      {name:`${bn} buy & hold`, type:'line', symbol:'none', data:d.series.map(p=>p.bench), lineStyle:{color:'#9ba8bb',width:1.3,type:'dashed'}},
    ]}, true);
}
async function renderDailyPnl() {
  if (!state.bot) { mkChart('dailyPnlChart').clear(); return; }
  const d = await api('/api/daily_pnl', {bot: state.bot});
  if (d.error || !d.series) return;
  $('#chartMeta').textContent = `${state.bot} · daily PnL (USDT) · capital moves excluded`;
  if (!d.series.length) { mkChart('dailyPnlChart').clear(); $('#chartSub').textContent='No data yet'; return; }
  const s = d.stats;
  $('#chartSub').innerHTML = `${s.days} days · up <b>${s.win_days}</b> / down <b>${s.loss_days}</b> (win rate <b>${s.win_rate}%</b>)`
    + ` · best <b class="up">${sign(s.best)}</b> · worst <b class="down">${fmtN(s.worst)}</b> · total <b class="${cls(s.sum_pnl)}">${sign(s.sum_pnl)}</b>`;
  const marks = d.series.filter(p=>p.capital).map(p=>({xAxis:p.date,
    label:{formatter:`capital ${p.capital>0?'+':''}${p.capital}`, color:'#7a879c', fontSize:10}}));
  mkChart('dailyPnlChart').setOption({
    grid:GRID,
    tooltip:{trigger:'axis', ...TIP, formatter: ps => {
      const p = d.series.find(x=>x.date===ps[0].axisValue); if (!p) return '';
      const L = [p.date, `PnL ${sign(p.pnl,4)}`];
      if (p.realized) L.push(`Realized ${sign(p.realized,4)}`);
      if (p.equity != null) L.push(`Close equity ${p.equity.toFixed(2)}`);
      if (p.capital) L.push(`Capital move ${p.capital>0?'+':''}${p.capital} (not PnL)`);
      return L.join('<br/>'); }},
    xAxis:{type:'category', data:d.series.map(p=>p.date), ...AXIS},
    yAxis:{type:'value', scale:true, ...AXIS},
    series:[{type:'bar', data:d.series.map(p=>p.pnl), barMaxWidth:12,
      itemStyle:{color: pt => pt.value >= 0 ? '#3ec99a' : '#ef5f6b', borderRadius:[2,2,0,0]},
      markLine: marks.length ? {silent:true, symbol:'none', lineStyle:{color:'#7a879c',type:'dashed',width:1}, data:marks} : undefined}]
  }, true);
}

// ================= panels =================
function renderPanel() {
  const p = state.panel;
  if (p==='fills' || p==='events') renderTables();
  else if (p==='alerts') renderAlerts();
  else if (p==='market') { renderMarkets(); renderSymbolBar(); renderKline(); }
  else if (p==='sent') renderSentiment();
  else if (p==='strategy') renderStrategy();
}
function syncFillFilter(bots) {
  const sel = $('#fillFilter'); if (!sel) return;
  const names = bots.filter(inView).map(p => p.name);
  const opts = ['__all__', ...names];
  const sig = state.view + opts.join(',');
  if (sel.dataset.sig === sig) return;
  sel.dataset.sig = sig;
  const cur = opts.includes(sel.value) ? sel.value : '__all__';
  sel.innerHTML = opts.map(n => `<option value="${esc(n)}" ${n===cur?'selected':''}>${n==='__all__'?'All bots':esc(n)}</option>`).join('');
}
const KIND_CLS = k => /fail|kill|halt|liq|reject|error/i.test(k) ? 'bad' : /risk|skip|warn|stop/i.test(k) ? 'mid' : /restore|ok|fill|start/i.test(k) ? 'good' : '';
async function renderTables() {
  const fv = ($('#fillFilter')||{}).value || '__all__';
  const scope = state.view;
  const fp = fv==='__all__' ? {scope} : {bot:fv};
  const [f, e] = await Promise.all([api('/api/fills', fp), api('/api/events', {scope})]);
  if (!f.fills || !e.events) return;
  const isBuy = s => String(s).toLowerCase().includes('buy');
  $('#fillsTable tbody').innerHTML = f.fills.slice().reverse().map(x => `
    <tr><td>${fmtTs(x.ts)}</td><td class="t">${esc(x.bot)}</td><td class="t">${esc(x.symbol)}</td>
    <td class="t ${isBuy(x.side)?'up':'down'}">${isBuy(x.side)?'Buy':'Sell'}</td>
    <td>${fmtN(x.qty,4)}</td><td>${fmtN(x.price,4)}</td><td>${fmtN(x.fee,4)}</td>
    <td class="${x.realized_pnl?cls(x.realized_pnl):'muted'}">${x.realized_pnl?sign(x.realized_pnl):'—'}</td></tr>`).join('')
    || '<tr><td colspan="8" class="t muted">No fills yet</td></tr>';
  $('#eventsTable tbody').innerHTML = e.events.slice().reverse().map(x => `
    <tr><td>${fmtTs(x.ts)}</td><td class="t">${esc(x.bot)}</td><td class="t"><span class="kind ${KIND_CLS(x.kind)}">${esc(x.kind)}</span></td>
    <td class="wrap">${esc(x.detail)}</td></tr>`).join('')
    || '<tr><td colspan="4" class="t muted">No events</td></tr>';
}
async function renderAlerts() {
  const {alerts} = await api('/api/alerts');
  if (!alerts) return;
  cache.alerts = alerts;
  const day = alerts.filter(a => Date.now()-a.ts < 24*3600e3).length;
  $('#alCount').textContent = day ? day : '';
  if (state.panel !== 'alerts') return;
  const color = l => l==='critical' ? 'var(--down)' : l==='warning' ? 'var(--warn)' : 'var(--muted)';
  $('#alertsBox').innerHTML = alerts.map(a => `<div class="al"><span class="dot" style="background:${color(a.level)}"></span>
    <span class="tx">${esc(a.text)}${a.count>1?` <span class="x">×${a.count}</span>`:''}</span>
    <span class="tm">${fmtTs(a.ts)}</span></div>`).join('') || '<div class="empty">No alerts</div>';
}

async function renderMarkets() {
  const {tickers} = await api('/api/markets');
  if (!tickers) return;
  $('#marketCards').innerHTML = tickers.length ? tickers.map(t => `<div class="tk"><div class="s">${esc(t.symbol)}</div>
    <div class="p">${fmtN(t.price, t.price>100?2:4)}</div>
    <div class="c ${cls(t.change_pct)}">${t.change_pct==null?'—':sign(t.change_pct)+'%'} <span class="muted">24h · H ${fmtN(t.high, t.high>100?0:4)} L ${fmtN(t.low, t.low>100?0:4)}</span></div></div>`).join('')
    : '<div class="empty">Tickers unavailable (exchange unreachable)</div>';
}
function renderSymbolBar() {
  $('#symbolBar').innerHTML = cache.universe.map(s => `<button class="${s===state.symbol?'active':''}" data-s="${esc(s)}">${esc(s.split('/')[0])}</button>`).join('');
  $('#tfBar').innerHTML = ['5m','15m','1h','4h','1d'].map(t => `<button class="${t===state.tf?'active':''}" data-tf="${t}">${t}</button>`).join('');
  $('#symbolBar').querySelectorAll('button').forEach(b => b.onclick = () => { state.symbol = b.dataset.s; renderKline(); renderSymbolBar(); });
  $('#tfBar').querySelectorAll('button').forEach(b => b.onclick = () => { state.tf = b.dataset.tf; renderKline(); renderSymbolBar(); });
  $('#klineMeta').textContent = `Scroll to zoom · drag to pan · markers = fills of ${state.bot ?? '—'}`;
}
async function renderKline() {
  if (state.panel !== 'market' || !state.symbol) return;
  const d = await api('/api/klines', {symbol:state.symbol, tf:state.tf, bot:state.bot ?? ''});
  if (d.error) { mkChart('klineChart').clear(); $('#klineMeta').textContent = d.error; return; }
  const ts = d.bars.map(b=>fmtTs(b[0]));
  const ohlc = d.bars.map(b=>[b[1],b[2],b[3],b[4]]);
  const vol = d.bars.map(b=>({value:b[5], itemStyle:{color: b[2]>=b[1] ? 'rgba(62,201,154,.45)' : 'rgba(239,95,107,.45)'}}));
  const tsIndex = Object.fromEntries(d.bars.map((b,i)=>[b[0],i]));
  const markers = d.markers.map(m => {
    let i = tsIndex[m.ts];
    if (i === undefined) { i = d.bars.findIndex(b=>b[0]>=m.ts); if (i<0) return null; }
    const buy = String(m.side).toLowerCase().includes('buy');
    return {coord:[i, m.price], value:buy?'B':'S', itemStyle:{color:buy?'#3ec99a':'#ef5f6b'},
      label:{color:'#0b1320', fontWeight:'bold'}, symbol:buy?'arrow':'pin', symbolSize:14};
  }).filter(Boolean);
  mkChart('klineChart').setOption({
    grid:[{left:52,right:16,top:12,height:290},{left:52,right:16,top:318,height:56}],
    tooltip:{trigger:'axis', axisPointer:{type:'cross'}, ...TIP},
    xAxis:[{type:'category', data:ts, ...AXIS},{type:'category', gridIndex:1, data:ts, show:false}],
    yAxis:[{type:'value', scale:true, ...AXIS},{type:'value', gridIndex:1, show:false}],
    dataZoom:[{type:'inside', xAxisIndex:[0,1], start:55, end:100},
      {type:'slider', xAxisIndex:[0,1], bottom:4, height:18, borderColor:'#2d3b52', backgroundColor:'#151e2c',
       fillerColor:'rgba(63,182,217,.1)', handleStyle:{color:ACCENT}, textStyle:{color:'#7a879c'}}],
    series:[{name:state.symbol, type:'candlestick', data:ohlc,
        itemStyle:{color:'#3ec99a', color0:'#ef5f6b', borderColor:'#3ec99a', borderColor0:'#ef5f6b'}, markPoint:{data:markers}},
      {name:'Vol', type:'bar', xAxisIndex:1, yAxisIndex:1, data:vol}]
  }, true);
}

const regimeMap = {extreme_fear:'extreme fear', extreme_greed:'extreme greed', neutral:'neutral'};
async function renderSentiment() {
  const s = await api('/api/sentiment');
  const ok = s && s.available && s.series;
  $('#sentEmpty').hidden = !!ok;
  $('#sentBody').hidden = !ok;
  if (!ok) return;
  const el = $('#sentScore');
  const cut = s.cutoffs || {fear:-60, greed:60};
  el.textContent = s.score; el.className = s.score<=cut.fear ? 'down' : s.score>=cut.greed ? 'up' : '';
  $('#sentMeta').textContent = `${regimeMap[s.regime]||s.regime} · z=${s.z} · ${s.n_sources} sources · divergence ${s.divergence}`;
  $('#sentSources').innerHTML = (s.sources||[]).map(x => {
    const share = Math.round((x.share||0)*100);
    const raw = x.value!=null ? ` · now <b>${esc(fmtN(x.value,0))}</b>` : '';
    const zv = !x.enabled ? '<span class="tag">disabled</span>'
      : x.active ? `<span class="${x.z>=0?'up':'down'}">${(x.z>=0?'+':'')+x.z.toFixed(2)}</span>` : '<span class="tag">warming up</span>';
    return `<div class="srcbar"${x.enabled?'':' style="opacity:.55"'}>
    <span class="wbar"><i style="width:${share}%"></i></span>
    <span class="nm"><b>${esc(x.label)}</b>${x.breaking?'<span class="tag y">event</span>':''}<span class="d">${esc(x.desc)}${raw} · Source: ${esc(x.source)}${x.enabled?' · weight '+share+'%':''}${x.shift?' · shift '+x.shift+'d':''}</span></span>
    <span class="zv">${zv}</span></div>`; }).join('');
  const c = s.config || {};
  $('#sentNote').innerHTML = `Each enabled source is converted to a ${esc(c.z_window)}-day rolling z-score (clipped at ±3), blended by weight,
    smoothed with a ${esc(c.ema_span)}-day EMA and squashed to −100…+100 with tanh. Regime cutoffs: ${esc(cut.fear)} / +${esc(cut.greed)}.
    Sources need at least 30 days of history before they activate. Every value carries an availability timestamp to prevent lookahead.
    Sources, weights and cutoffs are set in the optional <b>sentiment</b> block of config/system.yaml.
    <b>Monitoring only:</b> a sentiment input should not drive trading until it passes the IC validation gate
    (|IC| ≥ 0.03 with monotonic quantiles).`;
  mkChart('sentChart').setOption({
    grid:{left:0,right:0,top:6,bottom:0}, xAxis:{type:'category',show:false,data:s.series.map(p=>p.ts)},
    yAxis:{show:false,min:-100,max:100},
    series:[{type:'line', data:s.series.map(p=>p.score), symbol:'none', lineStyle:{color:ACCENT,width:1.5},
      areaStyle:{color:'rgba(63,182,217,.08)'}, markLine:{silent:true,symbol:'none',label:{show:false},lineStyle:{color:'#2d3b52'},data:[{yAxis:0}]}}]
  }, true);
}

let pnlCharts = {};
function fmtVal(v) {
  if (v == null || v === '') return '—';
  if (typeof v === 'number' && v > 1e12 && v < 1e13) return fmtTs(v);   // millisecond timestamp
  if (Array.isArray(v)) return v.length ? v.map(fmtVal).join(' · ') : 'none';
  if (typeof v === 'object') { const e = Object.entries(v); return e.length ? e.map(([k,x]) => `${k} ${typeof x==='object'?JSON.stringify(x):fmtVal(x)}`).join(' · ') : 'none'; }
  return String(v);
}
async function renderStrategy() {
  const s = await api('/api/strategy');
  if (!s.bots) return;
  const shown = s.bots.filter(p => inView(p) && (state.showRetired || p.stage!=='retired'));
  Object.values(pnlCharts).forEach(c => { try { c.dispose(); } catch(e){} });
  pnlCharts = {};
  $('#strategyCards').innerHTML = shown.map((p, idx) => {
    const live = p.live;
    const stateRows = live && live.state ? Object.entries(live.state).map(([k,v]) =>
      `<div class="kv"><span class="k">${esc(k)}</span><span class="v" style="white-space:normal">${esc(fmtVal(v))}</span></div>`).join('')
      : '<div class="empty">No state snapshot yet (waiting for the next bar)</div>';
    const trigs = live && live.triggers && live.triggers.length ? live.triggers.map(t =>
      `<div class="trig"><b class="${String(t.condition).startsWith('BUY')?'up':'down'}">${esc(t.condition)}</b> — ${esc(t.note)}</div>`).join('') : '';
    const params = Object.entries(p.params||{}).map(([k,v])=>`${k}=${typeof v==='object'?JSON.stringify(v):v}`).join(' · ');
    const pnl = p.pnl || {points:[], last:0, last_pct:0};
    const has = pnl.points && pnl.points.length > 1;
    return `<div class="strat-card">
      <div class="hd"><span class="nm">${esc(p.name)}</span><span class="badge ${esc(p.stage)}">${esc(p.stage)}</span>
        <span class="muted" style="font-size:11.5px">${esc(p.symbol)} · ${esc(p.timeframe)}</span>
        <span class="r ${cls(pnl.last)}">${sign(pnl.last||0)} <span style="font-size:11px;opacity:.8">(${(pnl.last_pct||0)>=0?'+':''}${pnl.last_pct||0}%)</span></span></div>
      <div id="spark-${idx}" class="sp">${has?'':'<div class="empty" style="line-height:52px">No PnL data yet</div>'}</div>
      <div class="kv"><span class="k">Params</span><span class="v" style="font-size:11.5px;white-space:normal">${esc(params) || '—'}</span></div>
      ${stateRows}${trigs}
      ${live ? `<div class="kv"><span class="k">Snapshot</span><span class="v muted">${fmtTs(live.ts)}</span></div>` : ''}
      ${p.rules ? `<span class="rules-t">▸ Strategy logic</span><div class="rules">${esc(p.rules)}</div>` : ''}
    </div>`;
  }).join('') || '<div class="empty">No bots in this view</div>';
  document.querySelectorAll('#strategyCards .rules-t').forEach(t => t.onclick = () => {
    const r = t.nextElementSibling; r.classList.toggle('open');
    t.textContent = (r.classList.contains('open') ? '▾ ' : '▸ ') + 'Strategy logic';
  });
  if (state.panel === 'strategy') shown.forEach((p, idx) => {
    const pnl = p.pnl; if (!pnl || !pnl.points || pnl.points.length < 2) return;
    const el = document.getElementById('spark-'+idx); if (!el) return;
    const c = echarts.init(el, null, {renderer:'canvas'}); pnlCharts[idx] = c;
    const up = (pnl.last||0) >= 0, color = up ? '#3ec99a' : '#ef5f6b';
    c.setOption({grid:{left:2,right:2,top:4,bottom:2}, xAxis:{type:'time',show:false}, yAxis:{type:'value',show:false,scale:true},
      tooltip:{trigger:'axis', confine:true, ...TIP, formatter:a=>`${fmtTs(a[0].value[0])}<br/>PnL ${sign(a[0].value[1])} USDT`},
      series:[{type:'line', showSymbol:false, data:pnl.points.map(x=>[x.ts,x.pnl]), lineStyle:{color,width:1.5},
        areaStyle:{color: up?'rgba(62,201,154,.1)':'rgba(239,95,107,.1)'},
        markLine:{silent:true,symbol:'none',label:{show:false},lineStyle:{color:'#35435c',type:'dashed',width:1},data:[{yAxis:0}]}}]});
  });
  const f = s.factors || {};
  $('#factorsBox').innerHTML =
    `<div class="kv"><span class="k">Symbol whitelist</span><span class="v" style="white-space:normal">${(f.universe||[]).map(u=>esc(u.split('/')[0])).join(' / ') || '—'}</span></div>`
    + (f.sentiment ? `<div class="kv"><span class="k">Composite sentiment</span><span class="v">${esc(f.sentiment.score)} (${esc(regimeMap[f.sentiment.regime]||f.sentiment.regime)}, ${esc(f.sentiment.n_sources)} sources), monitoring only</span></div>` : '')
    + Object.entries(f.risk||{}).map(([k,v])=>`<div class="kv"><span class="k">Risk · ${esc(k)}</span><span class="v">${esc(v)}</span></div>`).join('')
    + `<div class="kv"><span class="k">Cost assumptions</span><span class="v">${esc(f.fees)}</span></div>`;
}

// ================= refresh loop =================
async function refreshAll() {
  await renderBots();
  await Promise.all([renderLiveSummary(), renderAlerts()]);
  renderStatus(); renderAttn();
  renderChartPane(); renderPanel();
  $('#updatedAt').textContent = 'Updated ' + new Date().toLocaleTimeString(undefined, {hour:'2-digit', minute:'2-digit'});
}
const vis = () => document.visibilityState === 'visible';
applyView();
refreshAll();
setInterval(() => { if (vis()) refreshAll(); }, 60_000);
setInterval(() => { if (vis()) renderKline(); }, 20_000);
document.addEventListener('visibilitychange', () => { if (vis()) refreshAll(); });
window.addEventListener('resize', () => Object.values(charts).forEach(c=>c.resize()));

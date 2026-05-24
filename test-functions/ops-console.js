const crypto = require("crypto");

const METRICS = {
  cpu: Array.from({ length: 60 }, () => 15 + Math.random() * 30),
  memory: Array.from({ length: 60 }, () => 40 + Math.random() * 35),
  requests: Array.from({ length: 60 }, () => Math.floor(Math.random() * 200)),
  latency: Array.from({ length: 60 }, () => 5 + Math.random() * 60),
};

const TICKET_DB = new Map();
const LOG_BUFFER = [];
let LOG_ID = 0;

function pushLog(level, component, message) {
  const entry = { id: ++LOG_ID, level, component, message, ts: Date.now() };
  LOG_BUFFER.push(entry);
  if (LOG_BUFFER.length > 500) LOG_BUFFER.shift();
}

function tickMetrics() {
  for (const key of ["cpu", "memory", "requests", "latency"]) {
    const arr = METRICS[key];
    arr.shift();
    const last = arr[arr.length - 1];
    const noise = (Math.random() - 0.5) * (key === "cpu" ? 8 : key === "memory" ? 6 : key === "requests" ? 40 : 12);
    arr.push(Math.max(0, Math.min(100, last + noise)));
  }
}
setInterval(tickMetrics, 2000);
tickMetrics();

function HTML() {
  return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Grid Operations Console</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@300;400;600;700&family=Orbitron:wght@400;600;700;900&display=swap');

  :root {
    --bg: #0a0a0f;
    --surface: #111118;
    --surface2: #161622;
    --border: #1e1e30;
    --text: #c8c8d4;
    --text-dim: #6b6b80;
    --accent: #00f0ff;
    --accent2: #7b61ff;
    --danger: #ff3b6e;
    --warning: #f0a030;
    --success: #00e676;
    --glow-cyan: 0 0 12px rgba(0, 240, 255, 0.3);
    --glow-purple: 0 0 12px rgba(123, 97, 255, 0.3);
    --radius: 8px;
    --font-mono: 'JetBrains Mono', 'Cascadia Code', monospace;
    --font-display: 'Orbitron', sans-serif;
  }

  * { margin: 0; padding: 0; box-sizing: border-box; }

  body {
    background: var(--bg);
    color: var(--text);
    font-family: var(--font-mono);
    font-size: 12px;
    min-height: 100vh;
    overflow-x: hidden;
  }

  .scanlines {
    position: fixed; inset: 0; pointer-events: none; z-index: 9999;
    background: repeating-linear-gradient(0deg, transparent, transparent 2px, rgba(0,0,0,0.08) 2px, rgba(0,0,0,0.08) 4px);
  }

  .header {
    display: flex; align-items: center; justify-content: space-between;
    padding: 12px 20px; background: var(--surface);
    border-bottom: 1px solid var(--border);
    position: sticky; top: 0; z-index: 100;
  }
  .header-left { display: flex; align-items: center; gap: 16px; }
  .logo {
    font-family: var(--font-display); font-weight: 900; font-size: 20px;
    background: linear-gradient(135deg, var(--accent), var(--accent2));
    -webkit-background-clip: text; -webkit-text-fill-color: transparent;
    letter-spacing: 2px;
  }
  .badge {
    display: inline-flex; align-items: center; gap: 5px;
    padding: 3px 10px; border-radius: 12px; font-size: 10px; font-weight: 600;
  }
  .badge-live { background: rgba(0,230,118,0.12); color: var(--success); border: 1px solid rgba(0,230,118,0.3); }
  .badge-live::before { content: ''; width: 6px; height: 6px; background: var(--success); border-radius: 50%; animation: pulse-dot 1.5s infinite; }
  @keyframes pulse-dot { 0%,100%{opacity:1} 50%{opacity:0.3} }

  .header-right { display: flex; align-items: center; gap: 20px; }
  .clock { font-family: var(--font-display); font-size: 22px; font-weight: 700; color: var(--accent); text-shadow: var(--glow-cyan); }
  .node-info { color: var(--text-dim); font-size: 10px; text-align: right; }
  .node-info span { color: var(--accent2); }

  .layout { display: grid; grid-template-columns: 1fr 340px; gap: 12px; padding: 12px 20px; min-height: calc(100vh - 64px); }
  .main-panel { display: flex; flex-direction: column; gap: 12px; }
  .side-panel { display: flex; flex-direction: column; gap: 12px; }

  .card {
    background: var(--surface); border: 1px solid var(--border);
    border-radius: var(--radius); overflow: hidden; transition: border-color 0.3s;
  }
  .card:hover { border-color: rgba(123,97,255,0.3); }
  .card-header {
    display: flex; align-items: center; justify-content: space-between;
    padding: 10px 14px; background: var(--surface2);
    border-bottom: 1px solid var(--border); font-size: 11px; font-weight: 600;
    text-transform: uppercase; letter-spacing: 1px; color: var(--text-dim);
  }
  .card-header .icon { margin-right: 8px; }
  .card-body { padding: 12px 14px; }

  .metrics-row { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; }
  .metric-gauge { text-align: center; }
  .metric-gauge canvas { display: block; margin: 0 auto 6px; }
  .metric-label { font-size: 10px; color: var(--text-dim); text-transform: uppercase; letter-spacing: 1px; }
  .metric-value { font-family: var(--font-display); font-size: 18px; font-weight: 700; margin-top: 2px; }

  .chart-container { height: 180px; position: relative; }
  .chart-container canvas { width: 100%; height: 100%; }

  .log-entry {
    font-size: 10px; padding: 4px 0; border-bottom: 1px solid rgba(255,255,255,0.03);
    display: flex; gap: 8px; align-items: flex-start;
  }
  .log-ts { color: var(--text-dim); min-width: 70px; }
  .log-level { font-weight: 700; min-width: 50px; text-transform: uppercase; }
  .log-INFO .log-level { color: var(--success); }
  .log-WARN .log-level { color: var(--warning); }
  .log-ERROR .log-level { color: var(--danger); }
  .log-comp { color: var(--accent2); min-width: 100px; }
  .log-msg { color: var(--text); word-break: break-all; }

  .terminal {
    background: #08080e; padding: 12px; border-radius: var(--radius);
    border: 1px solid var(--border); height: 100%; display: flex; flex-direction: column;
  }
  .term-header { font-size: 10px; color: var(--text-dim); margin-bottom: 8px; display: flex; gap: 6px; }
  .term-dot { width: 8px; height: 8px; border-radius: 50%; }
  .term-dot.r { background: var(--danger); }
  .term-dot.y { background: var(--warning); }
  .term-dot.g { background: var(--success); }
  .term-output { flex: 1; overflow-y: auto; font-size: 11px; line-height: 1.6; padding: 4px 0; }
  .term-line { white-space: pre-wrap; word-break: break-all; }
  .term-prompt { display: flex; align-items: center; gap: 6px; margin-top: 8px; }
  .term-prompt span { color: var(--accent); }
  .term-prompt input {
    flex: 1; background: transparent; border: none; color: var(--text);
    font-family: var(--font-mono); font-size: 11px; outline: none;
  }

  .service-row {
    display: flex; align-items: center; gap: 10px; padding: 6px 0;
    border-bottom: 1px solid rgba(255,255,255,0.03); font-size: 11px;
  }
  .svc-status { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
  .svc-online { background: var(--success); box-shadow: 0 0 6px rgba(0,230,118,0.5); }
  .svc-degraded { background: var(--warning); box-shadow: 0 0 6px rgba(240,160,48,0.5); }
  .svc-offline { background: var(--danger); }
  .svc-name { flex: 1; }
  .svc-meta { color: var(--text-dim); font-size: 10px; }

  .ticket-row {
    padding: 8px 10px; border: 1px solid var(--border); border-radius: 6px;
    margin-bottom: 6px; font-size: 10px; cursor: pointer; transition: all 0.2s;
  }
  .ticket-row:hover { border-color: var(--accent2); background: var(--surface2); }
  .ticket-id { color: var(--accent); font-weight: 600; }
  .ticket-status { display: inline-block; padding: 2px 6px; border-radius: 4px; font-size: 9px; font-weight: 600; text-transform: uppercase; }
  .ticket-status.open { background: rgba(0,240,255,0.12); color: var(--accent); }
  .ticket-status.closed { background: rgba(0,230,118,0.12); color: var(--success); }

  .btn {
    background: transparent; border: 1px solid var(--border); color: var(--text);
    padding: 6px 14px; border-radius: 6px; font-family: var(--font-mono);
    font-size: 11px; cursor: pointer; transition: all 0.2s;
  }
  .btn:hover { border-color: var(--accent2); color: var(--accent2); box-shadow: var(--glow-purple); }
  .btn-accent { border-color: var(--accent); color: var(--accent); }
  .btn-accent:hover { box-shadow: var(--glow-cyan); background: rgba(0,240,255,0.06); }

  .glitch-text {
    animation: glitch 3s infinite;
  }
  @keyframes glitch {
    0%,90%,100% { transform: none; opacity: 1; }
    92% { transform: skewX(-2deg) translate(1px, -1px); opacity: 0.8; }
    94% { transform: skewX(1deg) translate(-1px, 1px); opacity: 0.9; }
    96% { transform: none; }
  }

  .toast {
    position: fixed; top: 80px; right: 20px; z-index: 9998;
    padding: 10px 18px; border-radius: 8px; font-size: 11px;
    animation: slideIn 0.3s ease; transition: opacity 0.3s;
  }
  .toast-ok { background: rgba(0,230,118,0.15); border: 1px solid rgba(0,230,118,0.4); color: var(--success); }
  .toast-err { background: rgba(255,59,110,0.15); border: 1px solid rgba(255,59,110,0.4); color: var(--danger); }
  @keyframes slideIn { from { transform: translateX(100%); opacity: 0; } to { transform: none; opacity: 1; } }

  @media (max-width: 900px) {
    .layout { grid-template-columns: 1fr; }
    .metrics-row { grid-template-columns: repeat(2, 1fr); }
  }

  ::-webkit-scrollbar { width: 4px; }
  ::-webkit-scrollbar-track { background: transparent; }
  ::-webkit-scrollbar-thumb { background: var(--border); border-radius: 4px; }
</style>
</head>
<body>
<div class="scanlines"></div>

<div class="header">
  <div class="header-left">
    <div class="logo glitch-text">● GRID</div>
    <div class="badge badge-live">LIVE</div>
    <span style="color:var(--text-dim);font-size:10px">v4.1.0 &middot; node-01</span>
  </div>
  <div class="header-right">
    <div class="node-info">CLUSTER <span>SJC-1</span> &middot; UPTIME <span id="uptime">--</span></div>
    <div class="clock" id="clock">--:--:--</div>
  </div>
</div>

<div class="layout">
  <div class="main-panel">
    <!-- Metrics Row -->
    <div class="card">
      <div class="card-header"><span>● REAL-TIME METRICS</span><span style="color:var(--text-dim);font-size:10px">POLLING 2S</span></div>
      <div class="card-body">
        <div class="metrics-row">
          <div class="metric-gauge">
            <canvas id="gauge-cpu" width="100" height="100"></canvas>
            <div class="metric-label">CPU</div>
            <div class="metric-value" id="val-cpu">--%</div>
          </div>
          <div class="metric-gauge">
            <canvas id="gauge-mem" width="100" height="100"></canvas>
            <div class="metric-label">MEMORY</div>
            <div class="metric-value" id="val-mem">--%</div>
          </div>
          <div class="metric-gauge">
            <canvas id="gauge-req" width="100" height="100"></canvas>
            <div class="metric-label">REQ/S</div>
            <div class="metric-value" id="val-req">--</div>
          </div>
          <div class="metric-gauge">
            <canvas id="gauge-lat" width="100" height="100"></canvas>
            <div class="metric-label">P99 LAT</div>
            <div class="metric-value" id="val-lat">--ms</div>
          </div>
        </div>
      </div>
    </div>

    <!-- CPU/Memory Chart -->
    <div class="card">
      <div class="card-header"><span>📈 CPU / MEMORY HISTORY</span></div>
      <div class="card-body">
        <div class="chart-container"><canvas id="chart-cpu-mem"></canvas></div>
      </div>
    </div>

    <!-- System Log -->
    <div class="card" style="flex:1">
      <div class="card-header">
        <span>📋 SYSTEM LOG</span>
        <button class="btn" onclick="clearLogs()" style="padding:2px 10px;font-size:10px">CLEAR</button>
      </div>
      <div class="card-body" id="log-container" style="max-height:280px;overflow-y:auto">
        <div style="color:var(--text-dim);text-align:center;padding:40px">Loading logs…</div>
      </div>
    </div>
  </div>

  <div class="side-panel">
    <!-- Terminal -->
    <div class="terminal" style="flex:1">
      <div class="term-header">
        <div class="term-dot r"></div><div class="term-dot y"></div><div class="term-dot g"></div>
        <span style="margin-left:6px">ops@grid ~ bash</span>
      </div>
      <div class="term-output" id="term-output">
        <div class="term-line" style="color:var(--accent)">Grid Operations Console v4.1.0</div>
        <div class="term-line" style="color:var(--text-dim)">Type 'help' for available commands.</div>
        <div class="term-line">&nbsp;</div>
      </div>
      <div class="term-prompt">
        <span>➜</span><input id="term-input" placeholder="type a command..." autofocus autocomplete="off">
      </div>
    </div>

    <!-- Services -->
    <div class="card">
      <div class="card-header"><span>🔗 SERVICES</span></div>
      <div class="card-body" id="services-list" style="max-height:160px;overflow-y:auto">
        <div class="service-row"><div class="svc-status svc-online"></div><div class="svc-name">api-gateway</div><div class="svc-meta">1.2ms</div></div>
        <div class="service-row"><div class="svc-status svc-online"></div><div class="svc-name">task-runner</div><div class="svc-meta">3.8ms</div></div>
        <div class="service-row"><div class="svc-status svc-online"></div><div class="svc-name">postgres-01</div><div class="svc-meta">0.4ms</div></div>
        <div class="service-row"><div class="svc-status svc-online"></div><div class="svc-name">redis-cache</div><div class="svc-meta">0.2ms</div></div>
        <div class="service-row"><div class="svc-status svc-degraded"></div><div class="svc-name">rabbitmq</div><div class="svc-meta">15ms</div></div>
        <div class="service-row"><div class="svc-status svc-online"></div><div class="svc-name">caddy-proxy</div><div class="svc-meta">0.8ms</div></div>
      </div>
    </div>

    <!-- Tickets -->
    <div class="card">
      <div class="card-header">
        <span>🎫 TICKETS</span>
        <button class="btn btn-accent" onclick="newTicket()" style="padding:2px 10px;font-size:10px">+ NEW</button>
      </div>
      <div class="card-body" id="tickets-list" style="max-height:180px;overflow-y:auto">
        <div style="color:var(--text-dim);text-align:center;padding:20px">No tickets</div>
      </div>
    </div>
  </div>
</div>

<div id="toast-area"></div>

<script>
(function() {
  // ── CLOCK ──
  const $c = document.getElementById('clock');
  const $u = document.getElementById('uptime');
  const startTime = Date.now();
  function tickClock() {
    const d = new Date();
    $c.textContent = d.toTimeString().slice(0,8);
    const sec = Math.floor((Date.now() - startTime) / 1000);
    const h = Math.floor(sec/3600), m = Math.floor((sec%3600)/60), s = sec%60;
    $u.textContent = String(h).padStart(2,'0')+':'+String(m).padStart(2,'0')+':'+String(s).padStart(2,'0');
  }
  tickClock(); setInterval(tickClock, 1000);

  // ── GAUGE DRAWING ──
  function drawGauge(canvasId, value, maxVal, colorFn) {
    const c = document.getElementById(canvasId);
    if (!c) return;
    const ctx = c.getContext('2d');
    const w = c.width, h = c.height, cx = w/2, cy = h/2, r = 40, lw = 8;
    ctx.clearRect(0, 0, w, h);
    // bg arc
    ctx.beginPath();
    ctx.arc(cx, cy, r, 0.75*Math.PI, 2.25*Math.PI);
    ctx.strokeStyle = 'rgba(255,255,255,0.06)';
    ctx.lineWidth = lw; ctx.stroke();
    // value arc
    const pct = Math.min(1, Math.max(0, value / maxVal));
    const startAngle = 0.75 * Math.PI;
    const endAngle = startAngle + pct * 1.5 * Math.PI;
    const grad = ctx.createLinearGradient(0, 0, w, 0);
    const c1 = colorFn(pct);
    grad.addColorStop(0, c1.start);
    grad.addColorStop(1, c1.end);
    ctx.beginPath();
    ctx.arc(cx, cy, r, startAngle, endAngle);
    ctx.strokeStyle = grad;
    ctx.lineWidth = lw; ctx.lineCap = 'round';
    ctx.stroke();
    // center value
    ctx.fillStyle = c1.end;
    ctx.font = 'bold 15px Orbitron'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
    ctx.fillText(Math.round(value), cx, cy);
  }

  function gaugeColors(pct) {
    if (pct < 0.5) return { start: '#00e676', end: '#00f0ff' };
    if (pct < 0.8) return { start: '#f0a030', end: '#f0a030' };
    return { start: '#ff3b6e', end: '#ff3b6e' };
  }

  function updateGauges(data) {
    drawGauge('gauge-cpu', data.cpu, 100, gaugeColors);
    drawGauge('gauge-mem', data.memory, 100, gaugeColors);
    const reqPct = Math.min(1, data.requests / 200);
    drawGauge('gauge-req', data.requests, 200, p => p<0.5?{start:'#00e676',end:'#7b61ff'}:p<0.8?{start:'#7b61ff',end:'#f0a030'}:{start:'#f0a030',end:'#ff3b6e'});
    drawGauge('gauge-lat', data.latency, 100, p => p<0.3?{start:'#00e676',end:'#00f0ff'}:p<0.7?{start:'#f0a030',end:'#f0a030'}:{start:'#ff3b6e',end:'#ff3b6e'});
    document.getElementById('val-cpu').textContent = Math.round(data.cpu)+'%';
    document.getElementById('val-mem').textContent = Math.round(data.memory)+'%';
    document.getElementById('val-req').textContent = Math.round(data.requests)+'/s';
    document.getElementById('val-lat').textContent = Math.round(data.latency)+'ms';
  }

  // ── CHART ──
  let chartHistory = { cpu: [], memory: [] };
  function drawChart(data) {
    const c = document.getElementById('chart-cpu-mem');
    if (!c) return;
    const container = c.parentElement;
    c.width = container.clientWidth;
    c.height = container.clientHeight;
    const ctx = c.getContext('2d');
    const w = c.width, h = c.height, pad = { top: 20, right: 20, bottom: 20, left: 40 };
    const pw = w - pad.left - pad.right, ph = h - pad.top - pad.bottom;
    ctx.clearRect(0, 0, w, h);
    // grid
    ctx.strokeStyle = 'rgba(255,255,255,0.04)';
    ctx.lineWidth = 0.5;
    for (let i = 0; i <= 4; i++) {
      const y = pad.top + (ph / 4) * i;
      ctx.beginPath(); ctx.moveTo(pad.left, y); ctx.lineTo(pad.left+pw, y); ctx.stroke();
      ctx.fillStyle = '#6b6b80';
      ctx.font = '9px "JetBrains Mono"'; ctx.textAlign = 'right';
      ctx.fillText(Math.round(100 - 25*i)+'%', pad.left - 8, y + 3);
    }
    // line
    function drawLine(vals, color, glow) {
      ctx.strokeStyle = color;
      ctx.lineWidth = 2;
      ctx.shadowColor = glow;
      ctx.shadowBlur = 6;
      ctx.beginPath();
      const n = vals.length;
      for (let i = 0; i < n; i++) {
        const x = pad.left + (pw / (n - 1)) * i;
        const y = pad.top + ph - (vals[i] / 100) * ph;
        if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      }
      ctx.stroke();
      ctx.shadowBlur = 0;
      // fill
      ctx.lineTo(pad.left + pw, pad.top + ph);
      ctx.lineTo(pad.left, pad.top + ph);
      ctx.closePath();
      const grad = ctx.createLinearGradient(0, pad.top, 0, pad.top + ph);
      grad.addColorStop(0, color.replace(')', ',0.15)').replace('rgb', 'rgba'));
      grad.addColorStop(1, 'transparent');
      ctx.fillStyle = grad;
      ctx.fill();
    }
    drawLine(data.cpu, 'rgb(0, 240, 255)', 'rgba(0,240,255,0.5)');
    drawLine(data.memory, 'rgb(123, 97, 255)', 'rgba(123,97,255,0.5)');
    // legend
    ctx.font = '10px "JetBrains Mono"';
    ctx.fillStyle = '#00f0ff'; ctx.fillText('CPU', pad.left, 12);
    ctx.fillStyle = '#7b61ff'; ctx.fillText('MEM', pad.left + 48, 12);
  }

  // ── LOGS ──
  function renderLogs(logs) {
    const el = document.getElementById('log-container');
    if (!logs || !logs.length) { el.innerHTML = '<div style="color:var(--text-dim);text-align:center;padding:40px">No logs</div>'; return; }
    el.innerHTML = logs.slice(-50).reverse().map(l => 
      '<div class="log-entry log-'+l.level+'"><span class="log-ts">'+new Date(l.ts).toLocaleTimeString()+'</span><span class="log-level">'+l.level+'</span><span class="log-comp">'+l.component+'</span><span class="log-msg">'+l.message+'</span></div>'
    ).join('');
    el.scrollTop = 0;
  }

  // ── TICKETS ──
  function renderTickets(tickets) {
    const el = document.getElementById('tickets-list');
    if (!tickets || !tickets.length) { el.innerHTML = '<div style="color:var(--text-dim);text-align:center;padding:20px">No tickets</div>'; return; }
    el.innerHTML = tickets.map(t => 
      '<div class="ticket-row" onclick="toggleTicket(\''+t.id+'\')">'+
        '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:2px">'+
          '<span class="ticket-id">#'+t.id.substring(0,8)+'</span>'+
          '<span class="ticket-status '+t.status+'">'+t.status+'</span>'+
        '</div>'+
        '<div style="color:var(--text)">'+t.title+'</div>'+
        '<div style="color:var(--text-dim);font-size:9px;margin-top:2px">'+t.author+' &middot; '+new Date(t.ts).toLocaleString()+'</div>'+
      '</div>'
    ).join('');
  }

  // ── API ──
  async function api(path, method, body) {
    const opts = { method: method||'GET', headers: {} };
    if (body) {
      opts.headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(body);
    }
    const res = await fetch('/api'+path, opts);
    return res.json();
  }

  function toast(msg, ok) {
    const area = document.getElementById('toast-area');
    const el = document.createElement('div');
    el.className = 'toast '+(ok?'toast-ok':'toast-err');
    el.textContent = msg;
    area.appendChild(el);
    setTimeout(() => { el.style.opacity='0'; setTimeout(()=>el.remove(),300); }, 2500);
  }

  // ── TERMINAL ──
  const termOut = document.getElementById('term-output');
  const termIn = document.getElementById('term-input');
  const commands = {
    help() { return 'Commands: help, status, services, tickets, logs, deploy, ping, whoami, clear, theme, easter'; },
    status() { return 'All systems nominal. Cluster SJC-1 | v4.1.0 | Node count: 6'; },
    whoami() { return 'root@grid-node-01  ::  SJC-1 primary'; },
    services() { return 'api-gateway (1.2ms) | task-runner (3.8ms) | postgres-01 (0.4ms) | redis-cache (0.2ms) | rabbitmq (15ms) | caddy-proxy (0.8ms)'; },
    ping() { return 'PING grid.smsly.cloud (10.0.1.24): 56 data bytes\n64 bytes from 10.0.1.24: icmp_seq=0 ttl=64 time=0.312 ms\n64 bytes from 10.0.1.24: icmp_seq=1 ttl=64 time=0.298 ms\n64 bytes from 10.0.1.24: icmp_seq=2 ttl=64 time=0.287 ms'; },
    deploy() { return '🚀 Triggered deploy pipeline...\n📦 Building image... smsly/func-ops-console:latest\n✅ Build complete (2.1s)\n🚢 Deploying to node-01... OK\n🔗 Live at: https://grid.smsly.cloud'; },
    clear() { termOut.innerHTML = ''; return ''; },
    async tickets() { const r = await api('/tickets'); return r.map(t=>'#'+t.id.substring(0,8)+' ['+t.status+'] '+t.title).join('\n'); },
    async logs() { const r = await api('/logs'); return r.slice(-10).map(l=>'['+l.level+'] '+l.component+': '+l.message).join('\n'); },
    easter() { return '🐉 You found the dragon. Grid runs on coffee and quantum entanglement.'; },
    theme() { document.body.style.background = '#0a0a0f'; return 'Theme reset.'; },
  };

  termIn.addEventListener('keydown', async e => {
    if (e.key !== 'Enter') return;
    const cmd = termIn.value.trim();
    termIn.value = '';
    if (!cmd) return;
    termOut.innerHTML += '<div class="term-line"><span style="color:var(--accent)">➜</span> <span style="color:var(--text)">'+cmd+'</span></div>';
    let output = '';
    const fn = commands[cmd.split(' ')[0].toLowerCase()];
    if (fn) {
      output = await fn();
    } else {
      output = 'Command not found: '+cmd+'. Type help for available commands.';
    }
    if (output) {
      termOut.innerHTML += '<div class="term-line" style="color:var(--text-dim)">'+output.replace(/\n/g,'<br>')+'</div>';
    }
    termOut.scrollTop = termOut.scrollHeight;
  });

  // ── NEW TICKET ──
  window.newTicket = async function() {
    const title = prompt('Ticket title:');
    if (!title) return;
    const r = await api('/tickets', 'POST', { title: title, author: 'ops-console' });
    if (r.id) { toast('Ticket #'+r.id.substring(0,8)+' created', true); refreshAll(); }
  };

  window.toggleTicket = async function(id) {
    const r = await api('/tickets/'+id, 'PATCH', {});
    refreshAll();
  };

  window.clearLogs = async function() {
    await api('/logs/clear', 'POST');
    refreshAll();
  };

  // ── POLL LOOP ──
  async function refreshAll() {
    try {
      const [metrics, logs, tickets] = await Promise.all([
        api('/metrics'), api('/logs'), api('/tickets')
      ]);
      updateGauges(metrics);
      drawChart(metrics);
      renderLogs(logs);
      renderTickets(tickets);
    } catch (e) { /* silent */ }
  }
  refreshAll();
  setInterval(refreshAll, 2000);

  // Handle resize
  window.addEventListener('resize', async () => {
    try { const m = await api('/metrics'); drawChart(m); } catch(e){}
  });

  // Keyboard shortcut for terminal focus
  document.addEventListener('keydown', e => {
    if (e.ctrlKey && e.key === 'k') { e.preventDefault(); termIn.focus(); }
  });
})();
</script>
</body>
</html>`;
}

function parsePath(path) {
  return path.replace(/^\/+|\/+$/g, "").split("/").filter(Boolean);
}

function json(res, status, data) {
  res.status(status);
  res.setHeader("Content-Type", "application/json");
  const headers = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, PATCH, DELETE, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
  };
  for (const [k, v] of Object.entries(headers)) {
    res.setHeader(k, v);
  }
  res.send(JSON.stringify(data));
}

exports.handler = async (req, res) => {
  const segments = parsePath(req.path);
  const root = segments[0];
  const id = segments[1];

  if (req.method === "OPTIONS") {
    return json(res, 204, "");
  }

  if (req.method === "HEAD") {
    res.status(200);
    res.send("");
    return;
  }

  if (root === "api") {
    const resource = id;
    const subId = segments[2];

    switch (resource) {
      case "metrics": {
        if (req.method === "GET") {
          const cpu = METRICS.cpu[METRICS.cpu.length - 1];
          const memory = METRICS.memory[METRICS.memory.length - 1];
          const requests = METRICS.requests[METRICS.requests.length - 1];
          const latency = METRICS.latency[METRICS.latency.length - 1];
          return json(res, 200, {
            cpu, memory, requests, latency,
            cpu_history: METRICS.cpu,
            memory_history: METRICS.memory,
            requests_history: METRICS.requests,
            latency_history: METRICS.latency,
            uptime: process.uptime(),
            node_version: process.version,
            memory_usage: process.memoryUsage(),
          });
        }
        break;
      }

      case "logs": {
        if (req.method === "GET") {
          const limit = req.query && req.query.limit ? parseInt(req.query.limit, 10) : 100;
          const level = (req.query && req.query.level) || null;
          let logs = LOG_BUFFER.slice();
          if (level) logs = logs.filter(l => l.level === level.toUpperCase());
          return json(res, 200, logs.slice(-Math.min(limit, 500)));
        }
        break;
      }

      case "logs/clear":
        if (req.method === "POST") {
          LOG_BUFFER.length = 0;
          return json(res, 200, { cleared: true });
        }
        break;

      case "tickets": {
        if (req.method === "GET") {
          return json(res, 200, [...TICKET_DB.values()].sort((a, b) => b.ts - a.ts));
        }
        if (req.method === "POST" && !subId) {
          const body = typeof req.body === "string" ? JSON.parse(req.body) : req.body;
          const ticket = {
            id: crypto.randomUUID(),
            title: (body && body.title) || "Untitled",
            status: "open",
            author: (body && body.author) || "anonymous",
            ts: Date.now(),
          };
          TICKET_DB.set(ticket.id, ticket);
          pushLog("INFO", "ticketing", `Ticket ${ticket.id.substring(0, 8)} created: ${ticket.title}`);
          return json(res, 201, ticket);
        }
        if (req.method === "PATCH" && subId) {
          const ticket = TICKET_DB.get(subId);
          if (!ticket) return json(res, 404, { error: "Ticket not found" });
          ticket.status = ticket.status === "open" ? "closed" : "open";
          TICKET_DB.set(subId, ticket);
          pushLog("INFO", "ticketing", `Ticket ${subId.substring(0, 8)} ${ticket.status}`);
          return json(res, 200, ticket);
        }
        break;
      }
    }
    return json(res, 404, { error: "API route not found", resource });
  }

  const html = HTML();
  res.status(200);
  res.setHeader("Content-Type", "text/html; charset=utf-8");
  res.setHeader("Cache-Control", "no-cache");
  res.send(html);
};

"""
RELATIONAL GRAPH — the Deep Field live node network.

Served at /graph3d, polling /api/feed every 1 s. Renders the whole radar as
a force-stable node graph where every edge is a REAL data dependency and its
thickness/pulse is the LIVE weight computed by the FCI fusion + ensemble:

    MARKET ─→ ARMA ─→┐             SENTIMENT ─→ LAYA ─→ WCI ─┐
    MARKET ─→ SYMREG ─┼→ ENSEMBLE ←─ CONFORMAL                ├─→ FCI
    MARKET ─→ HAWKES ─┘                                       │
    MARKET ─→ VOL ─→ FCI      MARKET ─→ VALUATION (P/E)       │
                                   └──────────→ FCI ──────────┘

Node size = live magnitude, node color = live sign (green/red semantic), edge
particles flow at speeds proportional to the current weight. This is the
"relational updation" view: change the sentiment, the models or the vol
regime and you SEE the impulse propagate into FCI.
"""

from __future__ import annotations

import config as C

_HEAD = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>PSX RADAR — RELATIONAL GRAPH — __ASSET__</title>
<!-- build __NONCE__ -->
<script type="importmap">
{"imports": {"three": "https://unpkg.com/three@0.160.0/build/three.module.js"}}
</script>
<link rel="preconnect" href="https://fonts.googleapis.com"/>
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin/>
<link href="https://fonts.googleapis.com/css2?family=Rajdhani:wght@500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet"/>
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  html,body{width:100%;height:100%;background:#000;overflow:hidden;
    font-family:'Rajdhani','Segoe UI',system-ui,sans-serif;color:#ffffff}
  #gl{position:fixed;inset:0;width:100%;height:100%;display:block}
  .ov{position:fixed;z-index:5;pointer-events:none}
  .k{font-size:10px;font-weight:600;letter-spacing:.22em;color:rgba(255,255,255,.78);
    text-transform:uppercase}
  .chip{display:inline-block;border:1px solid rgba(255,255,255,.28);color:#ffffff;
    font-family:'JetBrains Mono',monospace;font-size:9px;letter-spacing:.14em;
    padding:3px 8px;margin-left:6px;text-transform:uppercase}
  .chip.warn{border-color:#8a6a1c;color:#ffc247}
  .chip.off{border-color:#8a2733;color:#ff5d6c}
  #hudTop{top:0;left:0;right:0;display:flex;justify-content:space-between;
    align-items:flex-start;padding:12px 18px 0}
  #ttl{font-size:14px;font-weight:700;letter-spacing:.18em;display:flex;
    align-items:center;gap:9px;color:#ffffff}
  #liveDot{width:7px;height:7px;border-radius:50%;background:#ffffff;display:inline-block;
    animation:pulse 1.6s ease-in-out infinite}
  @keyframes pulse{0%,100%{opacity:1;box-shadow:0 0 6px rgba(255,255,255,.9)}
    50%{opacity:.25;box-shadow:none}}
  #subTtl{font-family:'JetBrains Mono',monospace;font-size:9px;letter-spacing:.18em;
    color:rgba(255,255,255,.72);margin-top:5px}
  #railL{top:64px;left:18px;width:246px;
    background:linear-gradient(180deg,rgba(0,0,0,.72),rgba(0,0,0,.32) 70%,transparent);
    padding:10px 12px 12px}
  #fci{font-size:42px;font-weight:700;line-height:1.05;margin-top:3px;color:#ffffff}
  #fci.pos{color:#4dffa0} #fci.neg{color:#ff5d6c} #fci.zro{color:#ffffff}
  #railL .sub{font-family:'JetBrains Mono',monospace;font-size:8.5px;letter-spacing:.06em;
    color:rgba(255,255,255,.62);margin-top:3px}
  .frow{display:flex;justify-content:space-between;width:100%;
    font-family:'JetBrains Mono',monospace;font-size:9.5px;letter-spacing:.06em;
    margin-top:4px;color:rgba(255,255,255,.78)}
  .frow b{color:#ffffff;font-weight:500}
  #railR{top:64px;right:18px;width:264px;text-align:left;
    background:linear-gradient(180deg,rgba(0,0,0,.72),rgba(0,0,0,.32) 70%,transparent);
    padding:10px 12px 12px}
  .nrow{display:grid;grid-template-columns:14px 92px 1fr;gap:8px;align-items:center;
    font-family:'JetBrains Mono',monospace;font-size:9px;padding:3.5px 0;
    border-top:1px solid rgba(255,255,255,.08);color:rgba(255,255,255,.8);
    letter-spacing:.04em}
  .nrow b{color:#ffffff;font-weight:500;text-align:right}
  .dot{width:8px;height:8px;border-radius:50%;display:inline-block}
  #legend{bottom:10px;left:22px;display:flex;gap:16px;font-size:9.5px;font-weight:600;
    letter-spacing:.14em;color:rgba(255,255,255,.78);text-transform:uppercase}
  #legend .sw{display:inline-block;width:18px;height:2px;vertical-align:middle;
    margin-right:5px}
  #standby{position:fixed;top:10px;left:50%;transform:translateX(-50%);
    z-index:9;pointer-events:none;opacity:0;transition:opacity .35s}
  #standby.on{opacity:1}
  #standby div{border:1px solid #8a6a1c;color:#ffc247;font-family:'JetBrains Mono',monospace;
    font-size:10px;letter-spacing:.22em;padding:6px 16px;background:rgba(0,0,0,.82)}
</style>
</head>
<body class="__LAYOUTCLS__">
<canvas id="gl"></canvas>

<div class="ov" id="hudTop">
  <div>
    <div id="ttl"><span id="liveDot"></span>RELATIONAL GRAPH — <span id="assetTtl">__ASSET__</span></div>
    <div id="subTtl">LIVE SIGNAL PROPAGATION · TICK <span id="tickN">0</span> · <span id="srcLine">LINKING…</span></div>
  </div>
  <div><span class="chip" id="modeChip">—</span><span class="chip warn" id="linkChip">LINK…</span></div>
</div>

<div class="ov" id="railL">
  <div class="k">Fused Conviction Index</div>
  <div id="fci" class="zro">+0.000</div>
  <div class="sub">FCI = (w<sub>s</sub>·WCI + w<sub>m</sub>·MODEL) · ALIGN · (1−CASCADE) · VOL_DAMP</div>
  <div class="frow"><span>chatter WCI (CS)</span><b id="fCS">—</b></div>
  <div class="frow"><span>model signal</span><b id="fMS">—</b></div>
  <div class="frow"><span>alignment</span><b id="fAL">—</b></div>
  <div class="frow"><span>cascade penalty</span><b id="fCP">—</b></div>
  <div class="frow"><span>vol damping</span><b id="fVD">—</b></div>
  <div class="frow"><span>exp 5d return</span><b id="fE5">—</b></div>
</div>

<div class="ov" id="railR">
  <div class="k" style="margin-bottom:5px">Live nodes</div>
  <div id="nrows"></div>
</div>

<div class="ov" id="legend">
  <span><i class="sw" style="background:#4dffa0"></i>bull / inflow</span>
  <span><i class="sw" style="background:#ff5d6c"></i>bear / penalty</span>
  <span><i class="sw" style="background:#ffc247"></i>damping</span>
  <span><i class="sw" style="background:#9aa3af"></i>neutral link</span>
</div>

<div id="standby"><div>RECONNECTING TO PIPELINE · <span id="retryN">0</span> RETRIES</div></div>
"""

_JS = r"""
<script type="module">
import * as THREE from 'three';

const API = '__API__';
const ASSET = '__ASSET__';
const GREEN = 0x4dffa0, RED = 0xff5d6c, AMBER = 0xffc247, GRAY = 0x9aa3af, WHITE = 0xffffff;
let retries = 0;

/* ── renderer / scene ─────────────────────────────────────────────────── */
const $ = id => document.getElementById(id);
const canvas = $('gl');
const renderer = new THREE.WebGLRenderer({canvas, antialias:true, alpha:true});
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.5));
renderer.setClearColor(0x000000, 1);
const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(45, innerWidth/innerHeight, .1, 200);
camera.position.set(0, 0, 26);

/* ── graph definition: fixed radial layout, live weights ──────────────── */
// v3.3 OVERLAP FIX: the layout is compacted (x×0.90, y×0.72) and the camera
// pulled back (z=26) so every node + its value sprite stays inside the
// frustum with margin — edge-node labels can no longer collide with the
// HUD rails or clip at the top.
// ring radii: FCI center; inner = fused inputs; outer = sources/models
const NODES = {
  FCI:       {x:0,     y:0,   r:.9,  label:'FCI'},
  WCI:       {x:-4.14, y:1.87, r:.55, label:'WCI'},
  SENTIMENT: {x:-8.46, y:3.31, r:.45, label:'SENTIMENT'},
  LAYA:      {x:-6.84, y:4.46, r:.45, label:'LAYA'},
  MARKET:    {x:6.84,  y:3.31, r:.6,  label:'MARKET'},
  ARMA:      {x:3.1,   y:4.8,  r:.4,  label:'ARMA'},
  SYMREG:    {x:8.7,   y:6.3,  r:.4,  label:'SYMREG'},
  HAWKES:    {x:11.4,  y:2.1,  r:.4,  label:'HAWKES'},
  CONFORMAL: {x:5.9,   y:7.3,  r:.4,  label:'CONFORMAL'},
  ENSEMBLE:  {x:5.22,  y:1.58, r:.62, label:'ENSEMBLE'},
  VOL:       {x:9.6,   y:5.1,  r:.4,  label:'VOL'},
  VALUATION: {x:10.62, y:-.29, r:.4,  label:'VALUATION'},
};
const EDGES = [
  ['SENTIMENT','LAYA','flow'], ['LAYA','WCI','conv'],
  ['MARKET','ARMA','data'], ['MARKET','SYMREG','data'], ['MARKET','HAWKES','data'],
  ['MARKET','VOL','data'], ['MARKET','VALUATION','data'],
  ['ARMA','ENSEMBLE','w'], ['SYMREG','ENSEMBLE','w'], ['CONFORMAL','ENSEMBLE','band'],
  ['WCI','FCI','ws'], ['ENSEMBLE','FCI','wm'], ['HAWKES','FCI','cascade'],
  ['VOL','FCI','damp'], ['VALUATION','FCI','ctx'],
];

/* ── build static actors ──────────────────────────────────────────────── */
function makeLabel(text, sizePx, color, w){
  const c = document.createElement('canvas'); c.width = 256; c.height = 64;
  const x = c.getContext('2d');
  x.font = '700 32px Rajdhani, system-ui, sans-serif';
  x.fillStyle = color; x.textAlign = 'center'; x.textBaseline = 'middle';
  x.fillText(text, 128, 32);
  const tx = new THREE.CanvasTexture(c); tx.minFilter = THREE.LinearFilter;
  const sp = new THREE.Sprite(new THREE.SpriteMaterial({map:tx, transparent:true, depthWrite:false}));
  sp.scale.set(w || 3.1, (w || 3.1) / 4, 1);
  return sp;
}

const nodeObjs = {}, valueObjs = {};
for(const [id, n] of Object.entries(NODES)){
  const core = new THREE.Mesh(
    new THREE.SphereGeometry(n.r, 24, 24),
    new THREE.MeshBasicMaterial({color:GRAY, transparent:true, opacity:.92}));
  const halo = new THREE.Mesh(
    new THREE.SphereGeometry(n.r * 1.55, 24, 24),
    new THREE.MeshBasicMaterial({color:GRAY, transparent:true, opacity:.16}));
  core.position.set(n.x, n.y, 0); halo.position.set(n.x, n.y, 0);
  scene.add(core); scene.add(halo);
  const lb = makeLabel(n.label, 30, '#ffffff');
  lb.position.set(n.x, n.y - n.r - .62, 0); scene.add(lb);
  const val = makeLabel('—', 26, '#ffffff', 2.5);
  val.position.set(n.x, n.y + n.r + .62, 0); scene.add(val);
  nodeObjs[id] = {core, halo, lb, base:n.r};
  valueObjs[id] = val;
}

// edges: line + traveling particles; rebuilt when weights change materially
const edgeObjs = {};
for(const [a, b, kind] of EDGES){
  const A = NODES[a], B = NODES[b];
  const geo = new THREE.BufferGeometry().setFromPoints(
    [new THREE.Vector3(A.x, A.y, 0), new THREE.Vector3(B.x, B.y, 0)]);
  const mat = new THREE.LineBasicMaterial({color:GRAY, transparent:true, opacity:.16});
  const line = new THREE.Line(geo, mat);
  scene.add(line);
  const parts = [];
  for(let i=0;i<3;i++){
    const p = new THREE.Mesh(new THREE.SphereGeometry(.075, 8, 8),
      new THREE.MeshBasicMaterial({color:GRAY, transparent:true, opacity:.85}));
    p.position.set(A.x, A.y, 0); p.userData.t = i / 3;
    scene.add(p); parts.push(p);
  }
  edgeObjs[a + '>' + b] = {line, parts, a, b, kind, weight:.05, color:GRAY, speed:.06};
}

/* ── live weight mapping (mirrors engine.analytics.fuse_conviction) ───── */
function edgeWeight(kind, d){
  const f = d.fusion || {}, models = d.models || {};
  const e = models.ensemble || {}, w = e.weights || {};
  const cs = Math.abs(d.cs ?? 0);
  const ms = Math.abs(f.m_sig ?? 0);
  const hk = models.hawkes || {};
  switch(kind){
    case 'flow':    return Math.min(1, .25 + (d.detections ?? 0) / 90);
    case 'conv':    return Math.min(1, .18 + cs);
    case 'data':    return models.refits ? .55 : .12;
    case 'w':       return Math.min(1, .1 + (w.arma ?? 0) * 2.4);
    case 'band':    return Math.min(1, .15 + ((models.conformal || {}).coverage ?? 0));
    case 'ws':      return Math.min(1, .1 + (f.w_sentiment ?? .55) * cs * 1.6);
    case 'wm':      return Math.min(1, .1 + (f.w_models ?? .45) * ms * 1.6);
    case 'cascade': return Math.min(1, .12 + (f.cascade_pen ?? 0) * 2.2);
    case 'damp':    return Math.min(1, .12 + ((f.vol_damp ?? 1) < 1 ? .85 : .06));
    case 'ctx':     return .14;
    default:        return .1;
  }
}
function edgeColor(kind, d){
  const f = d.fusion || {};
  const cs = d.cs ?? 0, ms = f.m_sig ?? 0;
  switch(kind){
    case 'conv':    return cs >= 0 ? GREEN : RED;
    case 'ws':      return cs >= 0 ? GREEN : RED;
    case 'wm':      return ms >= 0 ? GREEN : RED;
    case 'cascade': return (f.cascade_pen ?? 0) > 0.02 ? RED : GRAY;
    case 'damp':    return (f.vol_damp ?? 1) < 1 ? AMBER : GRAY;
    case 'flow':    return (d.nodes || []).length && (d.nodes[0].side === 'bearish') ? RED : GREEN;
    default:        return GRAY;
  }
}

function applyFeed(d){
  const f = d.fusion || {}, models = d.models || {};
  // node colors/sizes
  const cs = d.cs ?? 0;
  const ms = f.m_sig ?? 0;
  const hk = models.hawkes || {};
  const vol = models.vol || {};
  const paint = (id, val, mag, base) => {
    const o = nodeObjs[id];
    const col = val > 0.001 ? GREEN : val < -0.001 ? RED : GRAY;
    o.core.material.color.setHex(col);
    o.halo.material.color.setHex(col);
    const s = base * (0.75 + Math.min(Math.abs(mag), 1.4));
    o.core.scale.setScalar(s / base);
    o.halo.scale.setScalar(s / base);
  };
  paint('FCI', d.fci ?? 0, Math.abs(d.fci ?? 0) * 2.2, NODES.FCI.r);
  paint('WCI', cs, Math.abs(cs) * 2, NODES.WCI.r);
  paint('ENSEMBLE', ms, Math.abs(ms) * 1.6, NODES.ENSEMBLE.r);
  paint('SENTIMENT', cs, Math.abs(cs) * 1.6, NODES.SENTIMENT.r);
  paint('LAYA', 0, 0, NODES.LAYA.r);
  const mkt = d.market || {};
  paint('MARKET', mkt.change_pct ?? 0, Math.abs(mkt.change_pct ?? 0) / 3, NODES.MARKET.r);
  paint('ARMA', (models.arma ? models.arma.path_pct : [0])[0] ?? 0,
        Math.abs((models.arma && models.arma.path_pct.length) ? models.arma.path_pct[models.arma.path_pct.length-1] : 0) / 2, NODES.ARMA.r);
  paint('SYMREG', ms, Math.abs(ms), NODES.SYMREG.r);
  paint('HAWKES', 0, (hk.branching ?? 0), NODES.HAWKES.r);
  paint('CONFORMAL', 0, (models.conformal ? models.conformal.coverage : 0), NODES.CONFORMAL.r);
  paint('VOL', 0, (vol.pctl ?? .5), NODES.VOL.r);
  paint('VALUATION', 0, 0, NODES.VALUATION.r);

  // node value labels
  const setV = (id, txt, colr) => {
    const sp = valueObjs[id];
    if(sp.userData.txt !== txt){
      sp.material.map.dispose();
      sp.material.map = makeLabel(txt, 26, colr, 2.5).material.map;
      sp.userData.txt = txt;
    }
  };
  setV('FCI', (d.fci>=0?'+':'') + (d.fci??0).toFixed(3), d.fci>0?'#4dffa0':d.fci<0?'#ff5d6c':'#ffffff');
  setV('WCI', (cs>=0?'+':'') + cs.toFixed(2), cs>0?'#4dffa0':cs<0?'#ff5d6c':'#ffffff');
  setV('SENTIMENT', (d.detections ?? 0) + ' DET', '#ffffff');
  setV('LAYA', String(d.engine || '—').toUpperCase(), d.engine === 'laya' ? '#4dffa0' : '#ffc247');
  setV('MARKET', mkt.price != null ? (mkt.price > 5000 ? mkt.price.toFixed(0) : mkt.price.toFixed(1)) : '—', '#ffffff');
  setV('ARMA', models.arma ? (100*(models.arma.path_pct[models.arma.path_pct.length-1] ?? 0)).toFixed(1)+'%' : '—', '#ffffff');
  setV('SYMREG', (ms>=0?'+':'') + ms.toFixed(2), '#ffffff');
  setV('HAWKES', 'B ' + (hk.branching ?? 0).toFixed(2), (hk.branching ?? 0) > .75 ? '#ff5d6c' : '#ffffff');
  setV('CONFORMAL', Math.round((models.conformal ? models.conformal.coverage : 0)*100) + '%', '#ffffff');
  setV('VOL', vol.cc != null ? (100*vol.cc).toFixed(0)+'%' : '—', '#ffffff');
  setV('VALUATION', models.pe != null ? 'P/E ' + models.pe.toFixed(1) : '—', '#ffffff');
  setV('ENSEMBLE', ((models.ensemble ? models.ensemble.expected_5d_pct : 0)>=0?'+':'') +
    (models.ensemble ? models.ensemble.expected_5d_pct : 0).toFixed(1) + '%/5d',
    (models.ensemble ? models.ensemble.expected_5d_pct : 0) >= 0 ? '#4dffa0' : '#ff5d6c');

  // edges
  for(const [a, b, kind] of EDGES){
    const E = edgeObjs[a + '>' + b];
    const wgt = edgeWeight(kind, d);
    E.weight += (wgt - E.weight) * .25;
    const col = edgeColor(kind, d);
    E.color = col;
    E.line.material.color.setHex(col);
    E.line.material.opacity = .1 + E.weight * .5;
    E.speed = .05 + E.weight * .5;
    E.parts.forEach(p => p.material.color.setHex(col));
  }

  // HUD
  const fe = $('fci'), v = d.fci ?? 0;
  fe.textContent = (v>=0?'+':'') + v.toFixed(3);
  fe.className = v > .001 ? 'pos' : v < -.001 ? 'neg' : 'zro';
  $('fCS').textContent = (cs>=0?'+':'') + cs.toFixed(3);
  $('fMS').textContent = (ms>=0?'+':'') + ms.toFixed(3);
  $('fAL').textContent = (f.align ?? 0).toFixed(3);
  $('fCP').textContent = (f.cascade_pen ?? 0).toFixed(3);
  $('fVD').textContent = '×' + (f.vol_damp ?? 1).toFixed(2);
  $('fE5').textContent = ((f.expected_5d_pct ?? 0)>=0?'+':'') + (f.expected_5d_pct ?? 0).toFixed(2) + '%';
  $('tickN').textContent = d.tick ?? 0;
  $('modeChip').textContent = models.backend ? String(models.backend).toUpperCase() : 'COLD';
  $('srcLine').textContent = (d.market ? 'MKT ' + String(d.market.source || '—').toUpperCase() : 'MKT —') +
    ' · REFITS ' + (models.refits ?? 0);

  // node table
  const rows = [
    ['SENTIMENT', (d.detections ?? 0) + ' det · cs ' + cs.toFixed(2), cs],
    ['LAYA', String(d.engine || '—') + ' · ' + (d.laya_latency_ms ?? 0).toFixed(0) + 'ms', null],
    ['MARKET', (mkt.source || '—').toUpperCase() + (mkt.closed ? ' · CLOSED' : '') +
      ' · ' + ((mkt.change_pct ?? 0) >= 0 ? '+' : '') + (mkt.change_pct ?? 0).toFixed(2) + '%', mkt.change_pct],
    ['ARMA', models.arma ? 'φ ' + models.arma.phi + ' · θ ' + models.arma.theta : '—', null],
    ['SYMREG', models.symreg ? String(models.symreg.expr).slice(0, 34) : '—', ms],
    ['HAWKES', 'λ ' + (hk.lambda_now ?? 0).toFixed(2) + '/h · br ' + (hk.branching ?? 0).toFixed(2), null],
    ['CONFORMAL', 'q̂ ' + (100 * ((models.conformal || {}).q_hat ?? 0)).toFixed(1) + '% · cov ' +
      Math.round(((models.conformal || {}).coverage ?? 0) * 100) + '%', null],
    ['ENSEMBLE', ((models.ensemble || {}).expected_5d_pct ?? 0).toFixed(2) + '%/5d · ' +
      (models.backend || 'layats-4'), (models.ensemble || {}).expected_5d_pct],
    ['VOL', vol.cc != null ? (100 * vol.cc).toFixed(1) + '% ann · ' + (vol.regime || '—') : '—', null],
    ['VALUATION', models.pe != null ? 'P/E ' + models.pe.toFixed(1) : '—', null],
    ['WCI', 'conv ' + cs.toFixed(3), cs],
    ['FCI', (v>=0?'+':'') + v.toFixed(3), v],
  ];
  const box = $('nrows'); box.textContent = '';
  for(const [nm, val, sgn] of rows){
    const r = document.createElement('div'); r.className = 'nrow';
    const dot = document.createElement('span');
    const dEl = document.createElement('i'); dEl.className = 'dot';
    dEl.style.background = sgn == null ? '#4a4f57' : sgn > 0 ? '#00ff88' : sgn < 0 ? '#ff3b4e' : '#4a4f57';
    dot.appendChild(dEl);
    const n1 = document.createElement('span'); n1.textContent = nm;
    const b = document.createElement('b'); b.textContent = val;
    r.appendChild(dot); r.appendChild(n1); r.appendChild(b);
    box.appendChild(r);
  }
}

/* ── poll ─────────────────────────────────────────────────────────────── */
async function poll(){
  const t0 = performance.now();
  let ok = false;
  try{
    const r = await fetch(API + '/api/feed?asset=' + encodeURIComponent(ASSET) +
      '&t=' + Date.now(), {cache:'no-store'});
    const d = await r.json();
    retries = 0;
    $('standby').classList.remove('on');
    applyFeed(d);
    ok = true;
  }catch(e){
    ok = false; retries++;
    $('retryN').textContent = retries;
    $('standby').classList.add('on');
  }
  const el = $('linkChip');
  el.textContent = ok ? ((performance.now() - t0).toFixed(0) + 'MS') : 'LINK LOST';
  el.className = 'chip' + (ok ? '' : ' off');
}
poll();
setInterval(poll, 1000);

/* ── render loop: particle flow + gentle float ────────────────────────── */
const clock = new THREE.Clock();
function frame(){
  requestAnimationFrame(frame);
  const dt = Math.min(clock.getDelta(), .05);
  const t = clock.elapsedTime;
  for(const id in nodeObjs){
    const o = nodeObjs[id], n = NODES[id];
    o.core.position.y = n.y + Math.sin(t * 1.1 + n.x * .7) * .14;
    o.halo.position.y = o.core.position.y;
    o.lb.position.y = o.core.position.y - n.r - .62;
    const vs = valueObjs[id];
    vs.position.y = o.core.position.y + n.r + .62;
    o.halo.scale.setScalar(1 + Math.sin(t * 2 + n.x) * .05);
  }
  for(const key in edgeObjs){
    const E = edgeObjs[key];
    const A = NODES[E.a], B = NODES[E.b];
    for(const p of E.parts){
      p.userData.t = (p.userData.t + dt * E.speed) % 1;
      const k = p.userData.t;
      p.position.set(A.x + (B.x - A.x) * k,
        A.y + Math.sin(t * 1.1 + A.x * .7) * .14 +
        (B.y + Math.sin(t * 1.1 + B.x * .7) * .14 - A.y - Math.sin(t * 1.1 + A.x * .7) * .14) * k, 0);
      p.material.opacity = .25 + E.weight * .75;
      const sc = .6 + E.weight * .9;
      p.scale.setScalar(sc);
    }
  }
  renderer.render(scene, camera);
}
function resize(){
  renderer.setSize(innerWidth, innerHeight, false);
  camera.aspect = innerWidth/innerHeight;
  camera.updateProjectionMatrix();
}
addEventListener('resize', resize); resize();
frame();
</script>
</body>
</html>
"""


def build_graph_html(asset: str, api_base: str | None = None, nonce: int = 0,
                     layout: str = "full") -> str:
    base = (api_base if api_base is not None
            else f"http://localhost:{C.API_PORT}").rstrip("/")
    doc = _HEAD + _JS
    return (doc.replace("__LAYOUTCLS__", "full")
               .replace("__ASSET__", asset)
               .replace("__API__", base)
               .replace("__NONCE__", str(nonce)))


if __name__ == "__main__":
    import sys
    out = build_graph_html(sys.argv[1] if len(sys.argv) > 1 else "KSE-100")
    with open("graph_preview.html", "w", encoding="utf-8") as fh:
        fh.write(out)
    print(f"wrote graph_preview.html ({len(out)} bytes)")

"""
FORECAST DECK — the Deep Field forecast surface.

One self-contained Three.js document (same iframe pattern as the Sentiment
Sphere, served by the feed API at /surface3d, polling /api/feed every 1 s):

  • trailing price ribbon — the last ~90 closes as a glowing 3D line
  • ensemble forecast fan — P10→P90 translucent band widening with horizon,
    tinted green/red by the predicted direction
  • model pins — ARMA / SYMREG / ENSEMBLE expected moves at each horizon as
    vertical bars whose height = predicted % and color = sign
  • right-rail model cards — live ARMA params, symbolic expression, ensemble
    weights, Hawkes intensity, conformal coverage, vol metrics, P/E
  • left rail — price, change, source chip (PSX / YAHOO / SIM · CLOSED),
    FCI vs WCI conviction pair

Monochrome + semantic green/red, pure black, no emojis — same design contract
as the sphere. v3.3: Rajdhani/JetBrains Mono PURE-WHITE typography,
layout="half" compaction for the side-by-side grid, slim reconnect banner.
"""

from __future__ import annotations

import config as C

_HEAD = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>PSX RADAR — FORECAST DECK — __ASSET__</title>
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
  #railL{top:64px;left:18px;width:270px;
    background:linear-gradient(180deg,rgba(0,0,0,.66),rgba(0,0,0,.28) 70%,transparent);
    padding:10px 12px 12px}
  #px{font-size:40px;font-weight:700;letter-spacing:.02em;line-height:1.05;
    margin-top:4px;color:#ffffff}
  #chg{font-family:'JetBrains Mono',monospace;font-size:11.5px;letter-spacing:.08em;
    margin-top:2px}
  #chg.up{color:#4dffa0} #chg.dn{color:#ff5d6c} #chg.fl{color:#ffffff}
  #fci{font-size:32px;font-weight:700;margin-top:2px;color:#ffffff}
  #fci.pos{color:#4dffa0} #fci.neg{color:#ff5d6c} #fci.zro{color:#ffffff}
  .pair{display:flex;gap:16px;margin-top:9px}
  .pair .cell .k{margin-bottom:2px}
  .pair .cell .v{font-family:'JetBrains Mono',monospace;font-size:14px;font-weight:500;
    color:#ffffff}
  #railR{top:64px;right:18px;width:250px;text-align:right;
    background:linear-gradient(180deg,rgba(0,0,0,.66),rgba(0,0,0,.28) 70%,transparent);
    padding:10px 12px 12px}
  .card{border:1px solid rgba(255,255,255,.12);background:rgba(0,0,0,.55);padding:7px 9px;
    margin-bottom:7px;text-align:left}
  .card .hd{display:flex;justify-content:space-between;align-items:baseline}
  .card .nm{font-size:10px;font-weight:600;letter-spacing:.18em;color:#ffffff}
  .card .bd{font-family:'JetBrains Mono',monospace;font-size:9px;color:rgba(255,255,255,.78);
    margin-top:4px;line-height:1.5;letter-spacing:.02em;word-break:break-all}
  .card .bd b{color:#ffffff;font-weight:500}
  .wbar{height:4px;background:rgba(255,255,255,.08);border:1px solid rgba(255,255,255,.14);
    margin-top:3px}
  .wbar i{display:block;height:100%;background:#4dffa0}
  #hudBot{bottom:0;left:0;right:0;padding:10px 20px 14px;
    background:linear-gradient(to top, rgba(0,0,0,.92) 65%, rgba(0,0,0,0))}
  #hgrid{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-top:6px}
  .hcell{border-top:1px solid rgba(255,255,255,.16);padding-top:5px}
  .hcell .hv{font-family:'JetBrains Mono',monospace;font-size:12px;margin-top:2px;
    color:#ffffff}
  .hcell .hv .p10,.hcell .hv .p90{color:rgba(255,255,255,.55);font-size:9px}
  .pos{color:#4dffa0}.neg{color:#ff5d6c}.mut{color:rgba(255,255,255,.72)}
  #standby{position:fixed;top:10px;left:50%;transform:translateX(-50%);
    z-index:9;pointer-events:none;opacity:0;transition:opacity .35s}
  #standby.on{opacity:1}
  #standby div{border:1px solid #8a6a1c;color:#ffc247;font-family:'JetBrains Mono',monospace;
    font-size:10px;letter-spacing:.22em;padding:6px 16px;background:rgba(0,0,0,.82)}

  /* ── HALF layout: side-by-side grid compaction ── */
  body.half #railL{width:212px;top:60px}
  body.half #px{font-size:30px}
  body.half #fci{font-size:24px}
  body.half .pair .cell .v{font-size:12px}
  body.half #railR{width:196px;top:60px}
  body.half .card .bd{font-size:8.5px;line-height:1.45}
  body.half .card{padding:6px 7px;margin-bottom:6px}
  body.half #ttl{font-size:12px}
  body.half #hgrid{gap:8px}
  body.half .hcell .hv{font-size:10.5px}
</style>
</head>
<body class="__LAYOUTCLS__">
<canvas id="gl"></canvas>

<div class="ov" id="hudTop">
  <div>
    <div id="ttl"><span id="liveDot"></span>FORECAST DECK — <span id="assetTtl">__ASSET__</span></div>
    <div id="subTtl">DEEP FIELD MODEL ZOO · TICK <span id="tickN">0</span> · <span id="srcLine">LINKING…</span></div>
  </div>
  <div>
    <span class="chip" id="mktChip">LINK…</span><span class="chip" id="closedChip" style="display:none">CLOSED</span><span class="chip warn" id="linkChip">LINK…</span>
  </div>
</div>

<div class="ov" id="railL">
  <div class="k">Last Price</div>
  <div id="px">—</div>
  <div id="chg" class="fl">—</div>
  <div class="k" style="margin-top:18px">Fused Conviction (FCI)</div>
  <div id="fci" class="zro">+0.000</div>
  <div class="pair">
    <div class="cell"><div class="k">WCI · chatter</div><div class="v" id="csV">—</div></div>
    <div class="cell"><div class="k">Model signal</div><div class="v" id="msigV">—</div></div>
    <div class="cell"><div class="k">Align</div><div class="v" id="alV">—</div></div>
  </div>
  <div class="pair" style="margin-top:14px">
    <div class="cell"><div class="k">Vol · ann.</div><div class="v" id="volV">—</div></div>
    <div class="cell"><div class="k">Regime</div><div class="v" id="regV">—</div></div>
    <div class="cell"><div class="k">P/E</div><div class="v" id="peV">—</div></div>
  </div>
</div>

<div class="ov" id="railR">
  <div class="card"><div class="hd"><span class="nm">ENSEMBLE</span><span class="chip" id="ensChip" style="margin:0">—</span></div>
    <div class="bd" id="ensBd">—</div>
    <div class="wbar"><i id="wEwma" style="width:25%"></i></div>
    <div class="wbar"><i id="wHolt" style="width:25%"></i></div>
    <div class="wbar"><i id="wArma" style="width:25%"></i></div>
    <div class="wbar"><i id="wSym" style="width:25%"></i></div></div>
  <div class="card"><div class="hd"><span class="nm">ARMA(1,1)</span></div><div class="bd" id="armaBd">—</div></div>
  <div class="card"><div class="hd"><span class="nm">SYMREG · PySR-style</span></div><div class="bd" id="symBd">—</div></div>
  <div class="card"><div class="hd"><span class="nm">HAWKES · tick-style</span></div><div class="bd" id="hkBd">—</div></div>
  <div class="card"><div class="hd"><span class="nm">CONFORMAL · MAPIE-style</span></div><div class="bd" id="cfBd">—</div></div>
</div>

<div class="ov" id="hudBot">
  <div class="k">Ensemble horizon fan · predicted % move · P10 / P50 / P90</div>
  <div id="hgrid">
    <div class="hcell"><div class="k">1D</div><div class="hv" id="h1">—</div></div>
    <div class="hcell"><div class="k">3D</div><div class="hv" id="h3">—</div></div>
    <div class="hcell"><div class="k">5D</div><div class="hv" id="h5">—</div></div>
    <div class="hcell"><div class="k">10D</div><div class="hv" id="h10">—</div></div>
  </div>
</div>

<div id="standby"><div>RECONNECTING TO PIPELINE · <span id="retryN">0</span> RETRIES</div></div>
"""

_JS = r"""
<script type="module">
import * as THREE from 'three';

const API = '__API__';
const ASSET = '__ASSET__';
const LAYOUT = '__LAYOUT__';                 // 'half' (grid) | 'full' (standalone)
const SC = LAYOUT === 'half' ? 0.62 : 1.0;   // horizontal scene scale in the grid cell
let retries = 0;
const $ = id => document.getElementById(id);

const canvas = $('gl');
const renderer = new THREE.WebGLRenderer({canvas, antialias:true, alpha:true});
renderer.setPixelRatio(Math.min(window.devicePixelRatio, LAYOUT==='half' ? 1.4 : 1.6));
renderer.setClearColor(0x000000, 1);
const scene = new THREE.Scene();
scene.fog = new THREE.Fog(0x000000, 26, 60);
const camera = new THREE.PerspectiveCamera(42, innerWidth/innerHeight, .1, 200);
camera.position.set(0, 6.2, 17);
camera.lookAt(0, 0, 0);

const grid = new THREE.GridHelper(46, 30, 0x141619, 0x0d0f12);
grid.position.y = -6; scene.add(grid);

/* ── scene groups ─────────────────────────────────────────────────────── */
const ribbonGroup = new THREE.Group(); scene.add(ribbonGroup);
const fanGroup = new THREE.Group();    scene.add(fanGroup);
const pinGroup = new THREE.Group();    scene.add(pinGroup);

/* ── state ────────────────────────────────────────────────────────────── */
let lastFeed = null, linkOk = false, lastPoll = 0;
let fanRange = {min:-3, max:3};

/* ── helpers ──────────────────────────────────────────────────────────── */
function disposeGroup(g){
  g.traverse(o => { if(o.geometry) o.geometry.dispose();
    if(o.material){ if(o.material.map) o.material.map.dispose(); o.material.dispose(); }});
  while(g.children.length) g.remove(g.children[0]);
}
function label(text, color, size){
  const c = document.createElement('canvas'); c.width = 256; c.height = 64;
  const x = c.getContext('2d');
  x.font = '700 36px Rajdhani, system-ui, sans-serif';
  x.fillStyle = color; x.textAlign = 'center';
  x.fillText(text, 128, 42);
  const tx = new THREE.CanvasTexture(c);
  tx.minFilter = THREE.LinearFilter;
  const sp = new THREE.Sprite(new THREE.SpriteMaterial({map:tx, transparent:true,
    depthWrite:false}));
  sp.scale.set(size, size/4, 1);
  return sp;
}

/* ── ribbon + fan rebuild (every poll; tiny geometries) ───────────────── */
function rebuild(trail, horizons, armaPct){
  disposeGroup(ribbonGroup); disposeGroup(fanGroup); disposeGroup(pinGroup);
  if(!trail || trail.length < 8) return;

  // vertical scale: window = trail ± max forecast extent
  let tmin = Math.min(...trail), tmax = Math.max(...trail);
  let ext = 1.5;
  if(horizons){
    for(const h of Object.values(horizons)){
      if(h.p10 != null) ext = Math.max(ext, Math.abs(h.p10), Math.abs(h.p90));
    }
  }
  const pad = (tmax - tmin) * .25 + ext * .55;
  const lo = tmin - pad, hi = tmax + pad;
  fanRange = {min:lo, max:hi};
  const span = Math.max(hi - lo, 1e-6);
  const N = trail.length;
  const X0 = -11 * SC, X1 = 11 * SC;          // scene x extent (SC compacts the grid cell)
  const yOf = p => -5.5 + 11 * (p - lo) / span;
  const xOfTrail = i => X0 + (X1 * .62) * i / (N - 1);
  const xOfH = hDays => X0 + (X1 * .62) + 1.0 + (X1 * .34) * (Math.log(hDays)/Math.log(10));

  // ── ribbon (trail) — line + endpoint dot
  const pts = [];
  for(let i=0;i<N;i++) pts.push(new THREE.Vector3(xOfTrail(i), yOf(trail[i]), 0));
  const line = new THREE.Line(
    new THREE.BufferGeometry().setFromPoints(pts),
    new THREE.LineBasicMaterial({color:0xeaeaea, transparent:true, opacity:.85}));
  ribbonGroup.add(line);
  const glow = new THREE.Mesh(new THREE.SphereGeometry(.14, 12, 12),
    new THREE.MeshBasicMaterial({color:0xeaeaea}));
  glow.position.copy(pts[pts.length-1]); ribbonGroup.add(glow);

  // ── forecast fan (ensemble P10–P90 band) + P50 line
  if(horizons){
    const hs = Object.keys(horizons).map(Number).sort((a,b)=>a-b);
    const y0 = trail[trail.length-1];
    const Xs = [xOfTrail(N-1), ...hs.map(xOfH)];
    const up = [], dn = [];
    const p50pts = [];
    for(let k=0;k<hs.length;k++){
      const h = horizons[hs[k]];
      const p50 = h.p50 ?? 0, p10 = h.p10 ?? 0, p90 = h.p90 ?? 0;
      const price = y0 * (1 + p50/100), p10v = y0*(1+p10/100), p90v = y0*(1+p90/100);
      up.push(new THREE.Vector3(Xs[k+1], yOf(p90v), 0));
      dn.push(new THREE.Vector3(Xs[k+1], yOf(p10v), 0));
      p50pts.push(new THREE.Vector3(Xs[k+1], yOf(price), 0));
    }
    // band as triangle strip
    // v3.3 FIX: p50 was referenced out of block scope here (const inside the
    // loop body) → ReferenceError on EVERY poll with horizons → the poll's
    // catch flipped linkOk off → the deck showed LINK LOST / STANDBY forever
    // while the HUD still updated. Use the last horizon's p50 explicitly.
    const verts = [];
    const lastP50 = ((horizons[hs[hs.length - 1]] || {}).p50 ?? 0);
    const col = (lastP50 >= 0) ? [0.0, 1.0, .53] : [1.0, .23, .31];
    for(let k=0;k<hs.length;k++){
      if(k===0){
        verts.push(Xs[0], yOf(y0), 0,  Xs[0], yOf(y0), 0);
      }
      verts.push(up[k].x, up[k].y, up[k].z,  dn[k].x, dn[k].y, dn[k].z);
    }
    const idx = [];
    const M = hs.length + 1;                     // columns (start + each horizon)
    for(let c=0;c<M-1;c++){
      const a = c*2, b = c*2+1, c2 = (c+1)*2, d = (c+1)*2+1;
      idx.push(a, b, c2,  b, d, c2);
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.Float32BufferAttribute(verts, 3));
    g.setIndex(idx);
    const fan = new THREE.Mesh(g, new THREE.MeshBasicMaterial({
      color:new THREE.Color(col[0],col[1],col[2]), transparent:true, opacity:.13,
      side:THREE.DoubleSide, depthWrite:false}));
    fanGroup.add(fan);
    if(p50pts.length > 1){
      p50pts.unshift(new THREE.Vector3(Xs[0], yOf(y0), 0));
      const p50l = new THREE.Line(
        new THREE.BufferGeometry().setFromPoints(p50pts),
        new THREE.LineBasicMaterial({color:p50pts[p50pts.length-1].y >= yOf(y0) ? 0x00ff88 : 0xff3b4e,
          transparent:true, opacity:.9}));
      fanGroup.add(p50l);
    }
    // horizon labels
    for(let k=0;k<hs.length;k++){
      const lb = label(hs[k]+'d', '#565b62', 1.15);
      lb.position.set(Xs[k+1], yOf(fanRange.min) - .55, 0);
      fanGroup.add(lb);
    }
    // ── model pins at h=5: ARMA / SYMREG / ENS
    const pinCol = v => v >= 0 ? 0x00ff88 : 0xff3b4e;
    function pin(x, pct, color, name){
      const hgt = Math.max(Math.min(Math.abs(pct) / ext * 4.4, 4.6), .05);
      const geo = new THREE.CylinderGeometry(.055, .055, hgt, 8);
      const m = new THREE.Mesh(geo, new THREE.MeshBasicMaterial({color, transparent:true, opacity:.92}));
      const yBase = yOf(trail[trail.length-1]);
      m.position.set(x, yBase + (pct>=0 ? hgt/2 : -hgt/2), 0);
      pinGroup.add(m);
      const lb = label(name, '#7a7f87', 1.5);
      lb.position.set(x, yBase + (pct>=0 ? hgt + .42 : -hgt - .42), 0);
      pinGroup.add(lb);
    }
    const xPin = h => xOfH(h) ;
    if(armaPct && armaPct.length) pin(xPin(10)+.9, armaPct[armaPct.length-1], pinCol(armaPct[armaPct.length-1]), 'ARMA·10d');
    if(horizons[5]) pin(xPin(5), horizons[5].p50 ?? 0, pinCol(horizons[5].p50 ?? 0), 'ENS·5d');
    else if(horizons[3]) pin(xPin(3), horizons[3].p50 ?? 0, pinCol(horizons[3].p50 ?? 0), 'ENS·3d');
  }
}

/* ── HUD updates (textContent-only) ───────────────────────────────────── */
function fmt(n, d=2){ return (n==null || !isFinite(n)) ? '—' : Number(n).toFixed(d); }
function signCls(v){ return v > 0 ? 'pos' : v < 0 ? 'neg' : ''; }

function updateHUD(d){
  const m = d.market || {}, models = d.models || {};
  $('px').textContent = m.price != null ? fmt(m.price, m.price > 5000 ? 0 : 2) : '—';
  const cp = m.change_pct;
  const chg = $('chg');
  chg.textContent = cp != null ? (cp>=0?'+':'') + fmt(cp) + '%  ·  ' + String(m.source||'—').toUpperCase() : '—';
  chg.className = cp == null ? 'fl' : (cp >= 0 ? 'up' : 'dn');
  const f = d.fusion || {}, fciV = d.fci ?? 0;
  const fe = $('fci');
  fe.textContent = (fciV>=0?'+':'') + fmt(fciV, 3);
  fe.className = fciV > 0.001 ? 'pos' : fciV < -0.001 ? 'neg' : 'zro';
  $('csV').textContent = fmt(d.cs, 3);
  $('msigV').textContent = fmt(f.m_sig, 3);
  $('alV').textContent = fmt(f.align, 2);
  const vol = models.vol || {};
  $('volV').textContent = vol.cc != null ? (100*vol.cc).toFixed(1)+'%' : '—';
  $('regV').textContent = vol.regime || '—';
  $('peV').textContent = models.pe != null ? fmt(models.pe, 1) : '—';

  // source chips
  const src = (m.source || '—').toUpperCase();
  const mc = $('mktChip'); mc.textContent = src;
  mc.className = 'chip' + (src === 'SIM' ? ' warn' : '');
  $('closedChip').style.display = m.closed ? '' : 'none';
  $('tickN').textContent = d.tick ?? 0;
  $('srcLine').textContent = 'REFITS ' + (models.refits ?? 0) + ' · FIT ' + fmt(models.fit_ms,0) + 'MS' +
     (models.error ? ' · ERR ' + models.error.slice(0,26) : '');

  // model cards
  const e = models.ensemble;
  const ensChip = $('ensChip');
  if(e){
    ensChip.textContent = (e.backend || 'layats-4').toUpperCase();
    const w = e.weights || {};
    $('wEwma').style.width = Math.max(2, (w.ewma??0)*100) + '%';
    $('wHolt').style.width = Math.max(2, (w.holt??0)*100) + '%';
    $('wArma').style.width = Math.max(2, (w.arma??0)*100) + '%';
    $('wSym').style.width  = Math.max(2, (w.symreg??0)*100) + '%';
    $('ensBd').innerHTML = 'exp <b>' + fmt(e.expected_5d_pct) + '%</b> / 5d · weights ' +
      ['ewma','holt','arma','symreg'].map(k => k+' <b>' + fmt((w[k]??0)*100,0) + '%</b>').join(' · ');
  } else { ensChip.textContent = 'COLD'; $('ensBd').textContent = 'warming up…'; }
  const a = models.arma;
  $('armaBd').innerHTML = a ? 'φ <b>'+fmt(a.phi,2)+'</b> · θ <b>'+fmt(a.theta,2)+
    '</b> · σ<sub>d</sub> <b>'+(100*(a.sigma_d??0)).toFixed(2)+'%</b>' : '—';
  const s = models.symreg;
  $('symBd').innerHTML = s ? '<b>'+ (s.expr||'—') +'</b><br>complexity '+s.complexity+
    ' · mse '+(s.mse!=null? s.mse.toExponential(1) : '—') : '—';
  const hk = models.hawkes;
  $('hkBd').innerHTML = hk ? 'λ(t) <b>'+fmt(hk.lambda_now,3)+'</b>/h · μ '+fmt(hk.mu,3)+
    ' · branching <b class="'+((hk.branching??0)>0.75?'neg':'mut')+'">'+fmt(hk.branching,2)+
    '</b> · events '+hk.events : '—';
  const cf = models.conformal;
  $('cfBd').innerHTML = cf ? 'q̂ <b>'+(100*(cf.q_hat??0)).toFixed(2)+'%</b> · α '+cf.alpha+
    ' · coverage <b>'+fmt(cf.coverage*100,0)+'%</b> (n '+cf.coverage_n+')' : '—';

  // horizon table
  const hs = e && e.horizons ? e.horizons : null;
  for(const [id, key] of [['h1','1'],['h3','3'],['h5','5'],['h10','10']]){
    const el = $(id);
    if(hs && hs[key]){
      const h = hs[key];
      const cls = h.p50 > 0 ? 'pos' : h.p50 < 0 ? 'neg' : 'mut';
      el.innerHTML = '<span class="'+cls+'"><b>'+(h.p50>=0?'+':'')+fmt(h.p50)+'%</b></span>'+
        '<br><span class="p10">'+fmt(h.p10)+' · '+fmt(h.p90)+'</span>';
    } else el.textContent = '—';
  }
}

/* ── poll loop (1s, same-origin) ──────────────────────────────────────── */
async function poll(){
  const t0 = performance.now();
  let pollErr = '';
  try{
    const r = await fetch(API + '/api/feed?asset=' + encodeURIComponent(ASSET) +
      '&t=' + Date.now(), {cache:'no-store'});
    const d = await r.json();
    if(!linkOk) retries = 0;
    linkOk = true;
    lastFeed = d;
    const models = d.models || {};
    updateHUD(d);
    rebuild(models.trail, models.ensemble ? models.ensemble.horizons : null,
            models.arma ? models.arma.path_pct : null);
  }catch(err){
    linkOk = false; retries++;
    $('retryN').textContent = retries;
    pollErr = String(err && err.message || err).slice(0, 24);
  }
  const dt = performance.now() - t0;
  $('linkChip').style.display = '';
  $('linkChip').textContent = linkOk ? (dt.toFixed(0)+'MS')
                                     : (pollErr ? 'ERR ' + pollErr : 'LINK LOST');
  $('linkChip').className = 'chip' + (linkOk ? '' : ' off');
  $('standby').classList.toggle('on', !linkOk);
}

/* ── pointer parallax + render loop ───────────────────────────────────── */
let mx = 0, my = 0;
addEventListener('pointermove', e => {
  mx = (e.clientX/innerWidth - .5) * 2;
  my = (e.clientY/innerHeight - .5) * 2;
});
function resize(){
  renderer.setSize(innerWidth, innerHeight, false);
  camera.aspect = innerWidth/innerHeight;
  camera.updateProjectionMatrix();
}
addEventListener('resize', resize); resize();

const clock = new THREE.Clock();
function frame(){
  requestAnimationFrame(frame);
  const dt = Math.min(clock.getDelta(), .05);
  camera.position.x += (mx*1.6 - camera.position.x) * .04;
  camera.position.y += (6.2 - my*2.2 - camera.position.y) * .04;
  camera.lookAt(1.5, 0, 0);
  pinGroup.children.forEach((o,i) => { if(o.isSprite) o.position.y += Math.sin(clock.elapsedTime*2 + i)*.0016; });
  renderer.render(scene, camera);
}
frame();
poll();
setInterval(poll, 1000);
</script>
</body>
</html>
"""


def build_forecast_html(asset: str, api_base: str | None = None, nonce: int = 0,
                        layout: str = "full") -> str:
    base = (api_base if api_base is not None
            else f"http://localhost:{C.API_PORT}").rstrip("/")
    lay = "half" if layout == "half" else "full"
    doc = _HEAD + _JS
    return (doc.replace("__LAYOUTCLS__", lay)
               .replace("__LAYOUT__", lay)
               .replace("__ASSET__", asset)
               .replace("__API__", base)
               .replace("__NONCE__", str(nonce)))


if __name__ == "__main__":
    import sys
    out = build_forecast_html(sys.argv[1] if len(sys.argv) > 1 else "KSE-100")
    with open("forecast_preview.html", "w", encoding="utf-8") as fh:
        fh.write(out)
    print(f"wrote forecast_preview.html ({len(out)} bytes)")

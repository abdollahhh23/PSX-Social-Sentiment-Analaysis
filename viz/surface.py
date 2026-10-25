"""
The Monitor Surface — Three.js "Sentiment Sphere" + HUD, as one self-contained
HTML document rendered inside the Streamlit shell via a persistent iframe.

Design contract (user-locked, v3.3)
-----------------------------------
• Pure black void; dense point-cloud sphere (radar aesthetic); elliptical
  orbital rings; a slow green sweep — the reference sci-fi radar look.
• Every pipeline tick emits scored posts as particles that STREAK IN from the
  screen edge and DOCK onto the sphere:
      green node = bullish · red node = bearish · dim gray = neutral
  Node size / glow = WCI conviction weight (institutional posts arrive big).
• Spatial ratio encoding — bullish posts dock on the north hemisphere band,
  bearish on the south: the sphere visibly TILTS with market sentiment.
• The canvas polls /api/feed every 1 second client-side; the iframe never
  re-mounts during idle, so the animation is 60fps-continuous while data
  streams in.
• TOP SIGNALS · HIGHEST CURRENT WCI lives on the LEFT rail, directly beneath
  the Net Conviction Index — every scored signal appears there in real time,
  classified green (bullish) / red (bearish) / gray (neutral).
• Typography v3.3: Rajdhani (display) + JetBrains Mono (data), PURE WHITE
  text on black — green/red reserved strictly for bull/bear semantics.
• layout="half" compacts every rail for the side-by-side grid on the main
  page (sphere left, forecast deck right); "full" is the standalone view.
• Link loss is a slim top banner, NOT a fullscreen takeover — the sphere and
  the last-good data stay visible while the poll retries.
"""

from __future__ import annotations

import config as C

# ─────────────────────────────────────────────────────────────────────────────
# Head + CSS + DOM skeleton
# ─────────────────────────────────────────────────────────────────────────────

_HEAD_CSS = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>PSX RADAR — __ASSET__</title>
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
  .k{font-family:'Rajdhani',sans-serif;font-weight:600;font-size:10px;
    letter-spacing:.22em;color:rgba(255,255,255,.78);text-transform:uppercase}
  .mono{font-family:'JetBrains Mono',monospace}
  .chip{display:inline-block;border:1px solid rgba(255,255,255,.28);color:#ffffff;
    font-family:'JetBrains Mono',monospace;font-size:9px;letter-spacing:.14em;
    padding:3px 8px;margin-left:6px;text-transform:uppercase}
  .chip.warn{border-color:#8a6a1c;color:#ffc247}
  .chip.off{border-color:#8a2733;color:#ff5d6c}
  .chip.bull{border-color:#0e5c38;color:#4dffa0}
  .chip.bear{border-color:#8a2733;color:#ff8b96}

  /* ── top HUD ── */
  #hudTop{top:0;left:0;right:0;display:flex;justify-content:space-between;
    align-items:flex-start;padding:12px 18px 0}
  #ttl{font-size:14px;font-weight:700;letter-spacing:.18em;display:flex;
    align-items:center;gap:9px;color:#ffffff}
  #liveDot{width:7px;height:7px;border-radius:50%;background:#ffffff;
    display:inline-block;animation:pulse 1.6s ease-in-out infinite}
  @keyframes pulse{0%,100%{opacity:1;box-shadow:0 0 6px rgba(255,255,255,.9)}
    50%{opacity:.25;box-shadow:none}}
  #subTtl{font-family:'JetBrains Mono',monospace;font-size:9px;letter-spacing:.2em;
    color:rgba(255,255,255,.72);margin-top:5px}

  /* ── left rail — conviction + TOP SIGNALS ── */
  #railL{top:64px;left:18px;width:300px;
    background:linear-gradient(180deg,rgba(0,0,0,.66),rgba(0,0,0,.28) 70%,transparent);
    padding:10px 12px 14px}
  #cs{font-size:50px;font-weight:700;letter-spacing:.02em;line-height:1.02;
    margin-top:3px;color:#ffffff}
  #cs.pos{color:#4dffa0} #cs.neg{color:#ff5d6c} #cs.zro{color:#ffffff}
  #mom{font-family:'JetBrains Mono',monospace;font-size:9.5px;letter-spacing:.1em;
    color:rgba(255,255,255,.72);margin-top:2px}
  .bar{display:flex;height:8px;background:rgba(255,255,255,.07);
    border:1px solid rgba(255,255,255,.14);margin-top:8px;width:100%}
  #bullBar{background:#4dffa0;height:100%;width:50%;
    transition:width .9s cubic-bezier(.2,.8,.2,1)}
  #bearBar{background:#ff5d6c;height:100%;width:50%;
    transition:width .9s cubic-bezier(.2,.8,.2,1)}
  .leg{display:flex;justify-content:space-between;width:100%;margin-top:5px;
    font-family:'JetBrains Mono',monospace;font-size:9.5px;letter-spacing:.1em;
    color:rgba(255,255,255,.85)}
  .leg .g{color:#4dffa0} .leg .r{color:#ff5d6c} .leg .n{color:rgba(255,255,255,.62)}
  #spark{margin-top:8px;border:1px solid rgba(255,255,255,.12);
    background:rgba(0,0,0,.45);width:100%}

  /* ── TOP SIGNALS — beneath the Net Conviction Index (user-locked spot) ── */
  #topSig{margin-top:16px}
  #topSig .hd{display:flex;justify-content:space-between;align-items:baseline;
    gap:8px;border-bottom:1px solid rgba(255,255,255,.16);padding-bottom:4px}
  #topSig .hd .n{white-space:nowrap}
  #topSig .hd .n{font-family:'JetBrains Mono',monospace;font-size:8.5px;
    color:rgba(255,255,255,.62);letter-spacing:.12em}
  .row{display:grid;grid-template-columns:44px 11px 96px 52px 1fr;gap:7px;
    font-family:'JetBrains Mono',monospace;font-size:9.5px;
    color:rgba(255,255,255,.82);padding:4.5px 0;
    border-bottom:1px solid rgba(255,255,255,.06);align-items:baseline}
  .row .t{color:rgba(255,255,255,.55);letter-spacing:.04em}
  .row .a{color:#ffffff;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;
    font-weight:500}
  .row .w{color:#ffffff;text-align:right;font-weight:600}
  .row .w.pos{color:#4dffa0} .row .w.neg{color:#ff5d6c}
  .row .x{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;
    color:rgba(255,255,255,.66)}
  .dot{width:7px;height:7px;border-radius:50%;display:inline-block;
    transform:translateY(.5px)}
  .dot.bullish,.dot.bull{background:#4dffa0;box-shadow:0 0 5px rgba(77,255,160,.7)}
  .dot.bearish,.dot.bear{background:#ff5d6c;box-shadow:0 0 5px rgba(255,93,108,.7)}
  .dot.neutral{background:rgba(255,255,255,.35)}

  /* ── right rail ── */
  #railR{top:64px;right:18px;width:210px;text-align:right;
    background:linear-gradient(180deg,rgba(0,0,0,.66),rgba(0,0,0,.28) 70%,transparent);
    padding:10px 12px 14px}
  #railR .v{font-family:'JetBrains Mono',monospace;font-size:15px;font-weight:500;
    letter-spacing:.04em;margin-top:2px;color:#ffffff}
  #railR .v.stale{color:#ffc247}
  #railR .blk{margin-bottom:10px}

  /* ── slim reconnect banner (never a fullscreen takeover) ── */
  #standby{position:fixed;top:10px;left:50%;transform:translateX(-50%);
    z-index:9;pointer-events:none;opacity:0;transition:opacity .35s}
  #standby.on{opacity:1}
  #standby div{border:1px solid #8a6a1c;color:#ffc247;
    font-family:'JetBrains Mono',monospace;font-size:10px;letter-spacing:.22em;
    padding:6px 16px;background:rgba(0,0,0,.82)}

  /* ── HALF layout: side-by-side grid compaction ── */
  body.half #railL{width:238px;top:60px}
  body.half #cs{font-size:38px}
  body.half .row{grid-template-columns:40px 10px 76px 46px 1fr;font-size:9px;gap:6px}
  body.half #railR{width:172px;top:60px}
  body.half #railR .v{font-size:13px}
  body.half #railR .blk{margin-bottom:7px}
  body.half #ttl{font-size:12px}
</style>
</head>
<body class="__LAYOUTCLS__">
<canvas id="gl"></canvas>

<div class="ov" id="hudTop">
  <div>
    <div id="ttl"><span id="liveDot"></span>SENTIMENT SPHERE — <span id="assetTtl">__ASSET__</span></div>
    <div id="subTtl">AUTO TRACK · TICK <span id="tickN">0</span> · <span id="srcLine">LINKING…</span></div>
  </div>
  <div>
    <span class="chip" id="engineChip">STANDBY</span><span class="chip" id="demoChip" style="display:none">SYNTH FEED</span><span class="chip" id="quietChip" style="display:none">QUIET LEDGER · POLLING</span><span class="chip" id="bfChip" style="display:none">BACKFILL</span><span class="chip" id="histChip" style="display:none">30D —</span><span class="chip" id="staleChip" style="display:none">STALE SNAP</span><span class="chip warn" id="linkChip">LINK…</span>
  </div>
</div>

<div class="ov" id="railL">
  <div class="k">Net Conviction Index</div>
  <div id="cs" class="zro">+0.000</div>
  <div id="mom" class="mono">MOMENTUM ±0.000 / 15M EMA</div>
  <div class="k" style="margin-top:14px">Sentiment Ratio · Bull : Bear</div>
  <div class="bar"><div id="bullBar"></div><div id="bearBar"></div></div>
  <div class="leg"><span class="g" id="bullPct">BULL —</span><span class="n" id="neutN">N —</span><span class="r" id="bearPct">BEAR —</span></div>
  <div class="k" style="margin-top:12px">WCI Trend · 4 min window</div>
  <canvas id="spark" width="276" height="46"></canvas>
  <div id="topSig">
    <div class="hd"><span class="k">Top Signals · Highest Current WCI</span>
      <span class="n" id="sigN">LIVE</span></div>
    <div id="rows"></div>
  </div>
</div>

<div class="ov" id="railR">
  <div class="blk"><div class="k">Sweep Active</div><div class="v" id="det">0 DETECTIONS</div></div>
  <div class="blk"><div class="k">Last New Signal</div><div class="v" id="ln">—</div></div>
  <div class="blk"><div class="k">Ingest Pulse</div><div class="v" id="ir">0 /min</div></div>
  <div class="blk"><div class="k">Laya Latency</div><div class="v" id="lat">— ms</div></div>
  <div class="blk"><div class="k">Spam / Bot Filtered</div><div class="v" id="spam">— %</div></div>
  <div class="blk"><div class="k">Pipeline Uptime</div><div class="v" id="up">—</div></div>
  <div class="blk"><div class="k">Inference Queue</div><div class="v" id="q">0</div></div>
  <div class="blk"><div class="k">Backfill Archive</div><div class="v" id="bfd">—</div></div>
</div>

<div id="standby"><div>RECONNECTING TO PIPELINE · <span id="retryN">0</span> RETRIES</div></div>
"""

# ─────────────────────────────────────────────────────────────────────────────
# JS part A — scene, docking system, flight system
# ─────────────────────────────────────────────────────────────────────────────

_JS_SCENE = r"""
<script type="module">
import * as THREE from 'three';

const API = '__API__';
const ASSET = '__ASSET__';
const LAYOUT = '__LAYOUT__';                 // 'half' (grid) | 'full' (standalone)
const COL = {bull:0x4dffa0, bear:0xff5d6c, neutral:0x9aa3af};

/* ── renderer / scene / camera ─────────────────────────────────────────── */
const canvas = document.getElementById('gl');
const renderer = new THREE.WebGLRenderer({canvas, antialias:true, alpha:true});
renderer.setPixelRatio(Math.min(window.devicePixelRatio, LAYOUT==='half' ? 1.4 : 1.6));
renderer.setClearColor(0x000000, 1);
const scene = new THREE.Scene();
scene.fog = new THREE.FogExp2(0x000000, 0.045);
const camera = new THREE.PerspectiveCamera(48, innerWidth/innerHeight, .1, 100);
camera.position.set(0, .12, LAYOUT==='half' ? 6.5 : 5.4);   // pull back in the grid cell

/* ── fibonacci point-cloud sphere (the radar body) ─────────────────────── */
function fibPoints(n, R){
  const pts=[], ga=Math.PI*(3-Math.sqrt(5));
  for(let i=0;i<n;i++){
    const y=1-2*i/(n-1), r=Math.sqrt(Math.max(0,1-y*y)), th=ga*i;
    pts.push(new THREE.Vector3(Math.cos(th)*r*R, y*R, Math.sin(th)*r*R));
  }
  return pts;
}
const sphereGroup = new THREE.Group(); scene.add(sphereGroup);
const baseGeo = new THREE.BufferGeometry().setFromPoints(fibPoints(2600, 1.65));
sphereGroup.add(new THREE.Points(baseGeo,
  new THREE.PointsMaterial({size:.013, color:0x565d68, transparent:true, opacity:.8,
    sizeAttenuation:true, depthWrite:false})));
const haloGeo = new THREE.BufferGeometry().setFromPoints(fibPoints(420, 1.68));
sphereGroup.add(new THREE.Points(haloGeo,
  new THREE.PointsMaterial({size:.022, color:0x8b95a3, transparent:true, opacity:.85,
    sizeAttenuation:true, depthWrite:false})));

/* ── elliptical orbital rings ──────────────────────────────────────────── */
function ringLine(radius, color, opacity){
  const pts=[];
  for(let i=0;i<=180;i++){ const a=i/180*Math.PI*2;
    pts.push(new THREE.Vector3(Math.cos(a)*radius, Math.sin(a)*radius, 0)); }
  return new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts),
    new THREE.LineBasicMaterial({color, transparent:true, opacity, depthWrite:false}));
}
const ringsGroup = new THREE.Group();
const r1g = new THREE.Group(); const ring1 = ringLine(2.30, 0x3a4049, .55);
ring1.rotation.set(1.12, .18, 0); r1g.add(ring1);
const r2g = new THREE.Group(); const ring2 = ringLine(2.78, COL.bull, .10);
ring2.rotation.set(1.35, 0, -.42); r2g.add(ring2);
const r3g = new THREE.Group(); const ring3 = ringLine(3.24, 0x3a4049, .38);
ring3.rotation.set(1.02, -.3, .1); r3g.add(ring3);
ringsGroup.add(r1g, r2g, r3g); scene.add(ringsGroup);

/* ── radar sweep ───────────────────────────────────────────────────────── */
const sweepGroup = new THREE.Group();
sweepGroup.add(new THREE.Line(
  new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(0,0,0), new THREE.Vector3(2.95,0,0)]),
  new THREE.LineBasicMaterial({color:COL.bull, transparent:true, opacity:.30, depthWrite:false})));
sweepGroup.add(new THREE.Line(
  new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(0,0,0), new THREE.Vector3(-2.15,0,0)]),
  new THREE.LineBasicMaterial({color:COL.bull, transparent:true, opacity:.09, depthWrite:false})));
sweepGroup.rotation.x = .55;
scene.add(sweepGroup);

/* ── docking slots: bull → north band, bear → south band ──────────────── */
const CAP = {bull:220, bear:220, neutral:90};     // v3.3: higher-volume sphere
function slotsFor(side){
  const all = fibPoints(1100, 1.74);
  if(side==='bull') return all.filter(p=>p.y>0.26).slice(0, CAP.bull);
  if(side==='bear') return all.filter(p=>p.y<-0.26).slice(0, CAP.bear);
  return all.filter(p=>Math.abs(p.y)<=0.26).slice(0, CAP.neutral);
}
const SLOT = {bull:slotsFor('bull'), bear:slotsFor('bear'), neutral:slotsFor('neutral')};

function makeCloud(side, color, size, opacity){
  const n = SLOT[side].length;
  const pos = new Float32Array(n*3);
  for(let i=0;i<n;i++) pos[i*3+1] = -999;
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.BufferAttribute(pos, 3));
  sphereGroup.add(new THREE.Points(g, new THREE.PointsMaterial({size, color,
    transparent:true, opacity, sizeAttenuation:true, depthWrite:false})));
  return {geo:g, pos, free:Array.from({length:n}, (_,i)=>i)};
}
const cloud = {
  bull:    makeCloud('bull',    COL.bull,    .055, .95),
  bear:    makeCloud('bear',    COL.bear,    .055, .95),
  neutral: makeCloud('neutral', COL.neutral, .030, .55),
};

/* ── glow sprites for high-conviction nodes ────────────────────────────── */
function glowTexture(){
  const cv=document.createElement('canvas'); cv.width=cv.height=64;
  const ctx=cv.getContext('2d');
  const g=ctx.createRadialGradient(32,32,0,32,32,32);
  g.addColorStop(0,'rgba(255,255,255,1)'); g.addColorStop(.35,'rgba(255,255,255,.45)');
  g.addColorStop(1,'rgba(255,255,255,0)');
  ctx.fillStyle=g; ctx.fillRect(0,0,64,64);
  return new THREE.CanvasTexture(cv);
}
const glowTex = glowTexture();
const glowPool = [];
function acquireGlow(hex){
  let g = glowPool.find(x=>!x.used);
  if(!g){
    if(glowPool.length >= 140) return null;
    const s = new THREE.Sprite(new THREE.SpriteMaterial({map:glowTex, color:hex,
      transparent:true, opacity:0, blending:THREE.AdditiveBlending, depthWrite:false}));
    sphereGroup.add(s); g = {sprite:s, used:false}; glowPool.push(g);
  }
  g.used = true; g.sprite.material.color.setHex(hex);
  g.sprite.material.opacity = .85; g.sprite.visible = true;
  return g;
}
function releaseGlow(g){ g.used=false; g.sprite.visible=false; }

/* ── flight system: streak in from screen edge → dock ──────────────────── */
const flights = [];
const dockedQueue = {bull:[], bear:[], neutral:[]};
const seenIds = new Set();
const backlog = [];

function startFlight(node){
  const side = node.side==='bullish' ? 'bull' : node.side==='bearish' ? 'bear' : 'neutral';
  const cl = cloud[side];
  if(cl.free.length === 0){                      // FIFO eviction of the oldest dock
    const old = dockedQueue[side].shift();
    if(old){
      cl.pos[old.slot*3]=0; cl.pos[old.slot*3+1]=-999; cl.pos[old.slot*3+2]=0;
      cl.free.push(old.slot);
      if(old.glow) releaseGlow(old.glow);
    }
  }
  const slot = cl.free.shift();
  if(slot === undefined) return;
  const target = SLOT[side][slot].clone();

  const dir = new THREE.Vector3(Math.random()*2-1, Math.random()*2-1, Math.random()*2-1).normalize();
  const start = dir.multiplyScalar(6 + Math.random()*3);

  const N = 16, arr = new Float32Array(N*3);
  for(let i=0;i<N;i++){ arr[i*3]=start.x; arr[i*3+1]=start.y; arr[i*3+2]=start.z; }
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.BufferAttribute(arr, 3));
  const line = new THREE.Line(g, new THREE.LineBasicMaterial({color:COL[side],
    transparent:true, opacity:.4, depthWrite:false}));
  scene.add(line);

  flights.push({side, slot, target, start, node, line, attr:g.getAttribute('position'),
    t:0, dur:1.15 + Math.random()*0.6});
}

function dock(f){
  const cl = cloud[f.side];
  cl.pos[f.slot*3]=f.target.x; cl.pos[f.slot*3+1]=f.target.y; cl.pos[f.slot*3+2]=f.target.z;
  cl.geo.attributes.position.needsUpdate = true;
  const rec = {slot:f.slot, glow:null};
  if(f.node.w > 0.42){
    const g = acquireGlow(COL[f.side]);
    if(g){ g.sprite.position.copy(f.target);
      const s = .14 + .42*Math.min(f.node.w, 1.6);
      g.sprite.scale.set(s, s, 1); rec.glow = g; }
  }
  dockedQueue[f.side].push(rec);
}

function pushNode(node){
  if(seenIds.has(node.id)) return;
  seenIds.add(node.id);
  if(seenIds.size > 4000) seenIds.clear();
  backlog.push(node);
  if(backlog.length > 320) backlog.shift();
}
"""

_JS_RUNTIME = r"""
/* ── 1-second polling of the pipeline feed ─────────────────────────────── */
const $ = id => document.getElementById(id);
let linkOK = false, retries = 0;

async function poll(){
  try{
    const r = await fetch(API + '/api/feed?asset=' + encodeURIComponent(ASSET) + '&_=' + Date.now(),
                          {cache:'no-store'});
    if(!r.ok) throw new Error('http '+r.status);
    const d = await r.json();
    if(!d.ok) throw new Error('payload');
    if(!linkOK) retries = 0;
    linkOK = true; apply(d);
  }catch(e){ linkOK = false; retries++; setLink(); }
}
setInterval(poll, 1000); poll();

function setLink(){
  const chip = $('linkChip');
  chip.textContent = linkOK ? 'LINK OK' : 'LINK LOST';
  chip.className = 'chip' + (linkOK ? '' : ' warn');
  $('standby').classList.toggle('on', !linkOK);
  if(!linkOK) $('retryN').textContent = retries;
}

/* ── HUD apply ─────────────────────────────────────────────────────────── */
function apply(d){
  // conviction number
  const csEl = $('cs');
  const v = d.cs;
  csEl.textContent = (v>=0?'+':'') + v.toFixed(3);
  csEl.className = v > 0.001 ? 'pos' : v < -0.001 ? 'neg' : 'zro';
  const mom = d.momentum || 0;
  $('mom').textContent = 'MOMENTUM ' + (mom>=0?'+':'') + mom.toFixed(3) + ' / 15M EMA';

  // ratio bar (neutral share stays dark gap)
  $('bullBar').style.width = Math.max(d.bull_pct, 0) + '%';
  $('bearBar').style.width = Math.max(d.bear_pct, 0) + '%';
  $('bullPct').textContent = 'BULL ' + d.bull_pct.toFixed(1) + '% · ' + d.bull_n;
  $('bearPct').textContent = 'BEAR ' + d.bear_pct.toFixed(1) + '% · ' + d.bear_n;
  $('neutN').textContent = 'N ' + d.neutral_n;

  // right rail
  $('det').textContent = d.detections + ' DETECTIONS';
  const age = d.last_new_age_s;
  const lnEl = $('ln');
  if (age == null) { lnEl.textContent = '—'; lnEl.className = 'v'; }
  else {
    lnEl.textContent = age < 90 ? Math.round(age) + 's ago'
                     : age < 3600 ? (age/60).toFixed(1) + 'm ago'
                     : (age/3600).toFixed(1) + 'h ago';
    lnEl.className = 'v' + (age > 600 ? ' stale' : '');
  }
  $('ir').textContent = (d.ingest_per_min ?? 0) + ' /min';
  $('lat').textContent = d.laya_latency_ms > 0 ? d.laya_latency_ms.toFixed(1) + ' ms' : '— ms';
  $('spam').textContent = d.spam_pct.toFixed(1) + ' %';
  $('q').textContent = d.queue_depth;
  const bfa = d.backfill || {};
  $('bfd').textContent = (bfa.enabled === false) ? 'OFF'
      : ((bfa.depth_days ?? 0) >= 0.1 ? (bfa.depth_days).toFixed(1) + 'D · ' + (bfa.chunks ?? 0) + ' CH' : 'LIVE WINDOW');
  const up = Math.max(d.uptime_s, 0);
  $('up').textContent = String(Math.floor(up/60)).padStart(2,'0') + ':' +
                        String(Math.floor(up%60)).padStart(2,'0');

  // chips
  const eng = $('engineChip');
  eng.textContent = d.engine === 'laya' ? 'LAYA ENGINE' : 'FALLBACK HEURISTIC';
  eng.className = 'chip' + (d.engine === 'laya' ? '' : ' warn');
  $('demoChip').style.display = d.demo ? 'inline-block' : 'none';
  $('staleChip').style.display = d.stale ? 'inline-block' : 'none';
  // bidirectional crawl — BACKFILL chip shows the walker state; it disappears
  // while the live sweep is producing (LIVE is the normal state)
  const bf = d.backfill || {};
  const bfChip = $('bfChip');
  const bfMode = bf.mode || 'LIVE';
  if (bf.enabled === false || bfMode === 'LIVE') {
    bfChip.style.display = 'none';
  } else {
    bfChip.style.display = 'inline-block';
    const dd = (bf.depth_days ?? 0).toFixed(1);
    if (bfMode === 'BACKFILL') { bfChip.textContent = 'BACKFILL ⟵ ' + dd + 'D'; bfChip.className = 'chip'; }
    else if (bfMode === 'PAUSED') { bfChip.textContent = 'WALK PAUSED · LIVE FIRST'; bfChip.className = 'chip warn'; }
    else if (bfMode === 'ARCHIVE_FULL') { bfChip.textContent = 'ARCHIVE FULL · ' + dd + 'D'; bfChip.className = 'chip'; }
    else { bfChip.textContent = 'CAUGHT UP · ' + dd + 'D'; bfChip.className = 'chip'; }
  }
  // 30-day daily-bias history computed from the backfilled archive
  const h = d.history || {};
  const histChip = $('histChip');
  if ((h.posts ?? 0) > 0 && (h.days ?? 0) >= 2) {
    histChip.style.display = 'inline-block';
    const skew = h.bull_pct - 50;
    histChip.textContent = h.days + 'D BIAS ' + (skew >= 0 ? '+' : '') + skew.toFixed(0) + '% · ' + h.posts + ' POSTS';
    histChip.className = 'chip' + (Math.abs(skew) < 5 ? '' : (skew > 0 ? ' bull' : ' bear'));
  } else {
    histChip.style.display = 'none';
  }
  // quiet ledger: nothing NEW scored for >5 min → say so loudly instead of
  // silently freezing (weekends / closed market / deduped re-polls)
  const quiet = age != null && age > 300;
  $('quietChip').style.display = quiet ? 'inline-block' : 'none';
  $('tickN').textContent = d.tick + ' · ' + (d.tick_ms ?? 0) + 'ms';
  const s = d.sources || {};
  const st = k => (s[k] || '—').toUpperCase();
  $('srcLine').textContent = 'GN ' + st('google_news') + ' · BN ' + st('bing_news') +
    ' · WR ' + st('wires') + ' · RD ' + st('reddit') + ' · X ' + st('x');
  setLink();

  // node stream → flight backlog
  (d.nodes || []).forEach(pushNode);

  // trend + TOP SIGNALS (left rail, realtime red/green classification)
  drawSpark(d.trend || []);
  drawRows(d.top && d.top.length ? d.top : (d.nodes || []).slice(0, 7));
}

/* ── sparkline (pure canvas 2D) ────────────────────────────────────────── */
function drawSpark(t){
  const cv = $('spark'), ctx = cv.getContext('2d');
  const W = cv.width, H = cv.height;
  ctx.clearRect(0, 0, W, H);
  ctx.strokeStyle = 'rgba(255,255,255,.14)'; ctx.lineWidth = 1;
  ctx.beginPath(); ctx.moveTo(0, H/2); ctx.lineTo(W, H/2); ctx.stroke();
  if(!t || t.length < 2) return;
  const last = t[t.length-1] || 0;
  const col = last >= 0 ? '#4dffa0' : '#ff5d6c';
  ctx.strokeStyle = col; ctx.lineWidth = 1.4; ctx.beginPath();
  const n = t.length, step = W/(n-1);
  for(let i=0;i<n;i++){
    const y = H/2 - t[i]*(H/2-5), x = i*step;
    i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
  }
  ctx.stroke();
  ctx.fillStyle = col;
  ctx.beginPath(); ctx.arc(W-2, H/2 - last*(H/2-5), 2.3, 0, 7); ctx.fill();
}

/* ── TOP SIGNALS rows (textContent-only DOM → XSS-safe) ────────────────── */
function drawRows(list){
  const box = $('rows'); box.textContent = '';
  const max = LAYOUT === 'half' ? 6 : 8;
  list.slice(0, max).forEach(nd => {
    const row = document.createElement('div'); row.className = 'row';
    const t = document.createElement('span'); t.className = 't';
    t.textContent = new Date(nd.ts*1000).toTimeString().slice(0,5);
    const dot = document.createElement('span');
    const dEl = document.createElement('span');
    dEl.className = 'dot ' + (nd.side || 'neutral');
    dot.appendChild(dEl);
    const a = document.createElement('span'); a.className = 'a'; a.textContent = nd.author || '—';
    const w = document.createElement('span');
    w.className = 'w ' + ((nd.w || 0) > 0 ? 'pos' : (nd.w || 0) < 0 ? 'neg' : '');
    w.textContent = (nd.w >= 0 ? '+' : '') + (nd.w || 0).toFixed(3);
    const x = document.createElement('span'); x.className = 'x'; x.textContent = nd.text || '';
    [t, dot, a, w, x].forEach(el => row.appendChild(el));
    box.appendChild(row);
  });
  const n = list.length;
  $('sigN').textContent = linkOK ? (n + ' · LIVE') : '—';
}

/* ── pointer parallax ──────────────────────────────────────────────────── */
let mx = 0, my = 0;
addEventListener('pointermove', e => {
  mx = (e.clientX/innerWidth - .5) * 2;
  my = (e.clientY/innerHeight - .5) * 2;
});

/* ── resize ────────────────────────────────────────────────────────────── */
function resize(){
  renderer.setSize(innerWidth, innerHeight, false);
  camera.aspect = innerWidth/innerHeight;
  camera.updateProjectionMatrix();
}
addEventListener('resize', resize); resize();

/* ── render loop ───────────────────────────────────────────────────────── */
const clock = new THREE.Clock();
function frame(){
  requestAnimationFrame(frame);
  const dt = Math.min(clock.getDelta(), .05);

  sphereGroup.rotation.y += dt * .05;
  ringsGroup.rotation.y -= dt * .022;
  r2g.rotation.y += dt * .05;             // green ring counter-orbits
  sweepGroup.rotation.y += dt * .85;

  // parallax
  camera.position.x += (mx*.35 - camera.position.x) * .04;
  camera.position.y += (.12 - my*.28 - camera.position.y) * .04;
  camera.lookAt(0, 0, 0);

  // spawn up to 8 queued nodes per frame (v3.3: higher ingest volume)
  let pump = 0;
  while(backlog.length && pump < 8){ startFlight(backlog.shift()); pump++; }

  // flights
  for(let i=flights.length-1; i>=0; i--){
    const f = flights[i];
    f.t += dt / f.dur;
    const e = 1 - Math.pow(1 - Math.min(f.t, 1), 3);      // ease-out cubic
    const cx = f.start.x + (f.target.x - f.start.x)*e;
    const cy = f.start.y + (f.target.y - f.start.y)*e;
    const cz = f.start.z + (f.target.z - f.start.z)*e;
    const arr = f.attr.array;
    for(let j=0; j<arr.length/3 - 1; j++){
      arr[j*3] = arr[(j+1)*3]; arr[j*3+1] = arr[(j+1)*3+1]; arr[j*3+2] = arr[(j+1)*3+2];
    }
    const H = arr.length/3 - 1;
    arr[H*3] = cx; arr[H*3+1] = cy; arr[H*3+2] = cz;
    f.attr.needsUpdate = true;
    if(f.t >= 1){
      dock(f);
      scene.remove(f.line); f.line.geometry.dispose(); f.line.material.dispose();
      flights.splice(i, 1);
    }
  }

  renderer.render(scene, camera);
}
frame();
</script>
</body>
</html>
"""


# ─────────────────────────────────────────────────────────────────────────────
# Assembly
# ─────────────────────────────────────────────────────────────────────────────

def build_surface_html(asset: str,
                       api_base: str | None = None,
                       nonce: int = 0,
                       layout: str = "full") -> str:
    """Compose the full monitor-surface document for one asset context.

    `nonce` changes the document content so Streamlit remounts the iframe on
    an explicit REFRESH, while idle reruns (identical content) keep the canvas
    alive and polling. `layout="half"` compacts the HUD for the side-by-side
    grid (sphere left, forecast deck right); `"full"` is the standalone view.
    """
    # empty-string base is INTENTIONAL for same-origin /surface serving —
    # only None falls back to the localhost default.
    base = (api_base if api_base is not None else f"http://localhost:{C.API_PORT}").rstrip("/")
    lay = "half" if layout == "half" else "full"
    doc = _HEAD_CSS + _JS_SCENE + _JS_RUNTIME
    return (doc
            .replace("__LAYOUTCLS__", lay)
            .replace("__LAYOUT__", lay)
            .replace("__ASSET__", asset)
            .replace("__API__", base)
            .replace("__NONCE__", str(nonce)))


if __name__ == "__main__":     # local preview: python -m viz.surface
    import sys
    lay = sys.argv[2] if len(sys.argv) > 2 else "full"
    out = build_surface_html(sys.argv[1] if len(sys.argv) > 1 else "KSE-100", layout=lay)
    path = "surface_preview.html"
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(out)
    print(f"wrote {path} ({len(out)} bytes)")

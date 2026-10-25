"""
PIPELINE HERO — the ingestion & classification headline block.

The user's framing (v3.4): this is a DATA INGESTION AND CLASSIFICATION
project — so the pipeline itself is the strongest, most visible thing on
the page; the sphere / deck / graph exist for positioning and visibility.

Modeled on the madewithjev build-hero pattern ("724 competitor ads,
broken down · ~40 s · ~$0.09"): one giant true counter + one dense
telemetry line + the classifier's live output bar. Embedded in Streamlit
via st.components.v1.html, polling /api/health + /api/feed itself every
second — so the numbers GENUINELY tick without a Streamlit rerun
(Streamlit can't self-refresh; the iframes can).

XSS rule (project-wide): all dynamic values are written with textContent
into pre-built spans. No innerHTML with data, ever.
"""

from __future__ import annotations


def build_hero_html(asset: str, api_base: str) -> str:
    """Self-contained hero document. `api_base` must be ABSOLUTE
    (e.g. http://localhost:8001) — this iframe lives on the Streamlit
    origin, not on the feed API's, so relative URLs would 404."""
    asset = (asset or "KSE-100").upper()
    base = (api_base or "").rstrip("/")
    return _HTML.replace("__ASSET__", asset).replace("__API__", base)


_HTML = r"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8"/>
<link rel="preconnect" href="https://fonts.googleapis.com"/>
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin/>
<link href="https://fonts.googleapis.com/css2?family=Rajdhani:wght@500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet"/>
<style>
  * {margin:0; padding:0; box-sizing:border-box;}
  html, body {background:#000000; color:#ffffff; overflow:hidden;
    font-family:'Rajdhani','Segoe UI',system-ui,sans-serif;}
  .wrap {padding:14px 2px 10px;}

  /* kicker — names the discipline */
  .kick {display:flex; align-items:center; gap:8px;
    font-family:'JetBrains Mono',monospace; font-size:9.5px;
    letter-spacing:.34em; color:rgba(255,255,255,.52);}
  .kick .pulse {width:7px; height:7px; border-radius:50%; background:#4dffa0;
    box-shadow:0 0 6px rgba(77,255,160,.8); animation:pp 1.6s ease-in-out infinite;}
  .kick .pulse.down {background:#ffc247; box-shadow:0 0 6px rgba(255,194,71,.7);
    animation:none;}
  @keyframes pp {0%,100%{opacity:1} 50%{opacity:.35}}
  .kick .chip {border:1px solid rgba(255,255,255,.28); padding:1px 7px;
    letter-spacing:.22em; color:rgba(255,255,255,.72);}
  .kick .chip.synth {border-color:#8a6a1c; color:#ffc247;}

  /* the headline — one giant true counter + sentence */
  .head {display:flex; align-items:baseline; gap:14px; margin-top:7px;
    border-bottom:1px solid rgba(255,255,255,.14); padding-bottom:12px;}
  .head .big {font-size:58px; font-weight:700; line-height:.92;
    letter-spacing:.01em; color:#ffffff; font-variant-numeric:tabular-nums;
    white-space:nowrap;}
  .head .say {font-size:29px; font-weight:600; letter-spacing:.015em;
    color:#ffffff; line-height:1.04; padding-bottom:2px;}
  .head .say em {font-style:normal; color:rgba(255,255,255,.55);}

  /* telemetry line */
  .tele {display:flex; flex-wrap:wrap; gap:4px 22px; margin-top:9px;
    font-family:'JetBrains Mono',monospace; font-size:10.5px;
    letter-spacing:.13em; color:rgba(255,255,255,.5); white-space:nowrap;}
  .tele b {font-weight:600; color:rgba(255,255,255,.94);}

  /* classifier output bar */
  .cls {display:flex; align-items:center; gap:16px; margin-top:12px;}
  .cls .lab {font-family:'JetBrains Mono',monospace; font-size:9px;
    letter-spacing:.26em; color:rgba(255,255,255,.5); white-space:nowrap;}
  .cls .track {flex:1; height:6px; display:flex; background:rgba(255,255,255,.07);}
  .cls .track i {display:block; height:100%; transition:width .5s ease;}
  #segBull {background:#4dffa0; box-shadow:0 0 8px rgba(77,255,160,.45);}
  #segNeut {background:rgba(255,255,255,.30);}
  #segBear {background:#ff5d6c; box-shadow:0 0 8px rgba(255,93,108,.45);}
  .leg {display:flex; gap:18px; margin-top:7px;
    font-family:'JetBrains Mono',monospace; font-size:10px; letter-spacing:.12em;
    color:rgba(255,255,255,.62); white-space:nowrap; align-items:baseline;}
  .leg b {color:#ffffff; font-weight:600;}
  .leg .g {color:#4dffa0;} .leg .r {color:#ff5d6c;}
  .leg .right {margin-left:auto; color:rgba(255,255,255,.42); letter-spacing:.16em;}
  .leg .right b {color:rgba(255,255,255,.88); font-weight:500;}

  .wrap.down {opacity:.55;}
</style>
</head>
<body>
<div class="wrap" id="wrap">
  <div class="kick">
    <span class="pulse" id="pulse"></span>
    <span>DATA INGESTION &amp; CLASSIFICATION · LIVE</span>
    <span class="chip" id="synthChip" style="display:none">SYNTH FEED</span>
    <span class="chip" id="linkChip">LINKING…</span>
  </div>

  <div class="head">
    <span class="big" id="nScored">0</span>
    <span class="say">posts, ingested &amp; classified <em id="nRate"></em></span>
  </div>

  <div class="tele">
    <span>ACROSS <b id="vWires">—</b> LIVE WIRES</span>
    <span><b id="vBots">0</b> BOTS &amp; SPAM FILTERED</span>
    <span><b id="vDups">0</b> DUPLICATES DROPPED</span>
    <span><b id="vDepth">0.0D</b> ARCHIVE WALKED</span>
    <span><b id="vLat">—</b> / VERDICT</span>
    <span><b id="vQueue">0</b> IN QUEUE</span>
  </div>

  <div class="cls">
    <span class="lab" id="clsLab">CLASSIFIER OUTPUT — __ASSET__</span>
    <div class="track">
      <i id="segBull" style="width:33.4%"></i><i id="segNeut" style="width:33.3%"></i><i id="segBear" style="width:33.3%"></i>
    </div>
  </div>
  <div class="leg">
    <span><span class="g">■</span> BULLISH <b id="sBull">—</b></span>
    <span><span style="color:rgba(255,255,255,.45)">■</span> NEUTRAL <b id="sNeut">—</b></span>
    <span><span class="r">■</span> BEARISH <b id="sBear">—</b></span>
    <span class="right">TICK <b id="vTick">0</b> · UPTIME <b id="vUp">—</b></span>
  </div>
</div>

<script>
(function () {
  'use strict';
  var API = '__API__';
  var ASSET = '__ASSET__';
  var $ = function (id) { return document.getElementById(id); };
  var fmt = function (n) { return (n || 0).toLocaleString('en-US'); };
  var lastHealth = null, lastFeed = null, linked = false;

  function uptime(s) {
    if (!s && s !== 0) return '—';
    s = Math.max(0, Math.floor(s));
    var h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
    if (h > 0) return h + 'H ' + m + 'M';
    if (m > 0) return m + 'M ' + (s % 60) + 'S';
    return s + 'S';
  }
  function setTxt(id, v) { var el = $(id); if (el && el.textContent !== v) el.textContent = v; }

  function render() {
    var h = lastHealth, f = lastFeed;
    var wrap = $('wrap');
    if (linked) { wrap.classList.remove('down'); } else { wrap.classList.add('down'); }
    setTxt('linkChip', linked ? 'LINK OK' : 'LINKING…');

    if (h) {
      setTxt('nScored', fmt(h.posts_scored));
      var dropped = h.dropped || {};
      var bots = (dropped.bot || 0) + (dropped.spam || 0);
      setTxt('vBots', fmt(bots));
      setTxt('vDups', fmt(dropped.dup || 0));
      var bf = h.backfill || {};
      setTxt('vDepth', (bf.depth_days || 0).toFixed(1) + 'D');
      var laya = h.laya || {};
      setTxt('vLat', laya.latency_ema_ms != null
        ? laya.latency_ema_ms.toFixed(0) + 'MS' : '—');
      setTxt('vTick', fmt(h.tick));
      setTxt('vUp', uptime(h.uptime_s));
      var synth = $('synthChip');
      synth.style.display = h.demo ? 'inline-block' : 'none';
    }
    if (f) {
      setTxt('nRate', '(' + fmt(f.ingest_per_min) + '/min)');
      var srcs = f.sources || {};
      var names = Object.keys(srcs), ok = 0;
      for (var i = 0; i < names.length; i++) {
        if (srcs[names[i]] === 'ok') ok++;
      }
      setTxt('vWires', ok + '/' + names.length);
      setTxt('vQueue', fmt(f.queue_depth));
      var bn = f.bull_n || 0, rn = f.bear_n || 0, nn = f.neutral_n || 0;
      var tot = bn + rn + nn;
      if (tot > 0) {
        var bp = 100 * bn / tot, rp = 100 * rn / tot, np = 100 * nn / tot;
        $('segBull').style.width = bp.toFixed(1) + '%';
        $('segNeut').style.width = np.toFixed(1) + '%';
        $('segBear').style.width = rp.toFixed(1) + '%';
        setTxt('sBull', bp.toFixed(0) + '% · ' + fmt(bn));
        setTxt('sNeut', np.toFixed(0) + '% · ' + fmt(nn));
        setTxt('sBear', rp.toFixed(0) + '% · ' + fmt(rn));
      } else {
        $('segBull').style.width = '33.4%';
        $('segNeut').style.width = '33.3%';
        $('segBear').style.width = '33.3%';
        setTxt('sBull', '—'); setTxt('sNeut', '—'); setTxt('sBear', '—');
      }
      setTxt('clsLab', 'CLASSIFIER OUTPUT — ' + ASSET);
    }
  }

  async function poll() {
    var got = false;
    try {
      var r = await fetch(API + '/api/health', {cache: 'no-store'});
      if (r.ok) { lastHealth = await r.json(); got = true; }
    } catch (e) { /* keep last */ }
    try {
      var r2 = await fetch(API + '/api/feed?asset=' + encodeURIComponent(ASSET),
                           {cache: 'no-store'});
      if (r2.ok) { lastFeed = await r2.json(); got = true; }
    } catch (e) { /* keep last */ }
    linked = got;
    render();
  }

  poll();
  setInterval(poll, 1000);
})();
</script>
</body>
</html>
"""

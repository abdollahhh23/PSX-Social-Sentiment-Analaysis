"""
PSX RADAR — dashboard shell (Streamlit).

v3.4 PAGE HIERARCHY (user-locked)
    1. PIPELINE HERO — ingestion & classification counters (the product)
    2. surfaces — positioning & visibility:
    ┌──────────────────────┬──────────────────────┐
    │  SENTIMENT SPHERE    │  FORECAST DECK       │
    ├──────────────────────┴──────────────────────┤
    │  RELATIONAL GRAPH (full width)              │
    └─────────────────────────────────────────────┘
All three live surfaces render AT ONCE — no view switching. Streamlit owns
the NAV BAR + status strips; each surface is a persistent iframe that polls
the pipeline API every second client-side.

    [ ◆ PSX RADAR ]  [KSE-100] [KSE-30] [KSE-50] [KMI-30] [ALL-SHR]  [🔍 SEARCH] [⟳ REFRESH]

AUTO-BOOT (LINK-TO-PIPELINE fix): if the feed API doesn't answer, the
dashboard SPAWNS `python run_worker.py` itself (once, guarded by a marker
file) — no more dead dashboard because a terminal wasn't opened. Set
RADAR_AUTOBOOT=0 to disable, or run `--demo` yourself for synthetic chatter.

Design: pure black (#000), Rajdhani (display) + JetBrains Mono (data),
PURE WHITE text; green/red reserved strictly for bullish/bearish semantics.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import quote_plus

import httpx
import streamlit as st

import config as C

# ─────────────────────────────────────────────────────────────────────────────
# Page + session defaults
# ─────────────────────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="PSX RADAR — Institutional Sentiment",
    page_icon="",
    layout="wide",
    initial_sidebar_state="collapsed",
    menu_items=None,
)

if "active_asset" not in st.session_state:
    st.session_state.active_asset = C.HEADER_TABS[0]
if "nonce" not in st.session_state:
    st.session_state.nonce = 0
if "search_err" not in st.session_state:
    st.session_state.search_err = None
if "tab_sel" not in st.session_state:
    st.session_state.tab_sel = C.HEADER_TABS[0]
if "autoboot_attempted" not in st.session_state:
    st.session_state.autoboot_attempted = False

API_BASE = os.getenv("RADAR_PUBLIC_API_BASE", f"http://localhost:{C.API_PORT}")

# ─────────────────────────────────────────────────────────────────────────────
# Pure-black CSS — kill every trace of Streamlit chrome; restyle widgets into
# a seamless terminal nav. v3.3: Rajdhani + JetBrains Mono, PURE WHITE.
# ─────────────────────────────────────────────────────────────────────────────

st.markdown(
    """
    <style>
      /* ── kill chrome ── */
      #MainMenu, footer, header[data-testid="stHeader"] {visibility:hidden; height:0;}
      .stApp > header {display:none;}
      .block-container {padding:.6rem 1rem 0; max-width:1780px;}
      .stApp {background:#000000;}
      html, body, [data-testid="stAppViewContainer"], .stApp {
        background:#000000; color:#ffffff;
        font-family:'Rajdhani','Segoe UI',system-ui,sans-serif;
      }

      /* ── brand ── */
      .psx-brand {display:flex; flex-direction:column; justify-content:center; height:100%;}
      .psx-brand .b1 {font-size:16px; font-weight:700; letter-spacing:.2em; color:#ffffff;
        white-space:nowrap;}
      .psx-brand .b1 em {font-style:normal; color:#4dffa0;}
      .psx-brand .b2 {font-family:'JetBrains Mono',monospace; font-size:8.5px;
        letter-spacing:.28em; color:rgba(255,255,255,.66); margin-top:2px; white-space:nowrap;}

      /* ── index tabs (radio restyled as terminal nav pills) ── */
      div[role="radiogroup"] {gap:.4rem !important; align-items:center;}
      label[data-baseweb="radio"] {margin:0; padding:6px 2px 7px; cursor:pointer;}
      label[data-baseweb="radio"] > div:first-child {display:none;}  /* hide the dot */
      label[data-baseweb="radio"] div {background:transparent !important; border:none;}
      label[data-baseweb="radio"] p {
        font-family:'Rajdhani',sans-serif !important; font-weight:600;
        font-size:13.5px !important; letter-spacing:.12em; color:rgba(255,255,255,.62);
        border-bottom:1px solid transparent; padding-bottom:4px; margin:0;
        transition:color .15s ease, border-color .15s ease;}
      label[data-baseweb="radio"] p:hover {color:#ffffff;}
      label[data-baseweb="radio"]:has(input:checked) p {
        color:#ffffff; border-bottom:1px solid #4dffa0;}
      div[role="radiogroup"] > div {background:transparent;}

      /* ── ticker search (BEFORE refresh — user-locked order) ── */
      [data-testid="stTextInput"] input {
        background:#000000; border:1px solid rgba(255,255,255,.22) !important; border-radius:0;
        color:#ffffff; font-family:'JetBrains Mono',monospace;
        font-size:12px; letter-spacing:.08em; padding:7px 12px; height:38px;}
      [data-testid="stTextInput"] input::placeholder {color:rgba(255,255,255,.4);
        letter-spacing:.1em;}
      [data-testid="stTextInput"] input:focus {border-color:#4dffa0 !important;
        box-shadow:none !important; outline:none;}
      [data-testid="stTextInput"] label, [data-testid="stTextInput"] label p {display:none;}

      /* ── refresh button ── */
      [data-testid="stButton"] button {
        background:#000000; border:1px solid rgba(255,255,255,.26); border-radius:0;
        color:#ffffff; font-family:'Rajdhani',sans-serif; font-weight:600; font-size:12.5px;
        letter-spacing:.16em; padding:8px 14px; height:38px;
        transition:border-color .15s ease;}
      [data-testid="stButton"] button:hover {border-color:#4dffa0; color:#ffffff;}
      [data-testid="stButton"] button:focus {box-shadow:none !important; outline:none;}
      [data-testid="stButton"] button p {font-family:inherit !important;}

      /* ── status strip + inline error ── */
      .psx-status {display:flex; gap:20px; align-items:center; flex-wrap:wrap;
        font-family:'JetBrains Mono',monospace; font-size:10px;
        letter-spacing:.14em; color:rgba(255,255,255,.72); padding:6px 2px 2px;
        border-bottom:1px solid rgba(255,255,255,.1); margin-bottom:2px;}
      .psx-status b {font-weight:600; color:#ffffff;}
      .psx-status .ok {color:#ffffff;} .psx-status .warn {color:#ffc247;}
      .psx-err {color:#ff5d6c; font-family:'JetBrains Mono',monospace; font-size:11px;
        letter-spacing:.12em; padding:4px 2px 0;}
      .psx-note {font-family:'JetBrains Mono',monospace; font-size:10px;
        letter-spacing:.12em; color:#ffc247; padding:5px 2px;}
      iframe {border:none;}
      .panel-gap {height:6px;}
    </style>
    <link rel="preconnect" href="https://fonts.googleapis.com"/>
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin/>
    <link href="https://fonts.googleapis.com/css2?family=Rajdhani:wght@500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet"/>
    """,
    unsafe_allow_html=True,
)


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline probes + AUTO-BOOT
# ─────────────────────────────────────────────────────────────────────────────

@st.cache_data(ttl=2.0)
def probe_pipeline(_nonce: int) -> dict | None:
    try:
        r = httpx.get(f"{API_BASE}/api/health", timeout=httpx.Timeout(0.8, connect=0.5))
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return None


@st.cache_data(ttl=2.0)
def probe_feed(_nonce: int, asset: str) -> dict | None:
    """Live market + model snapshot for the metrics strip (2s cache)."""
    try:
        r = httpx.get(f"{API_BASE}/api/feed", params={"asset": asset},
                      timeout=httpx.Timeout(0.9, connect=0.5))
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return None


def post_watch(symbol: str) -> None:
    """Hot-add a searched ticker to the running pipeline (fire-and-forget)."""
    try:
        httpx.post(f"{API_BASE}/api/watch", json={"asset": symbol},
                   timeout=httpx.Timeout(0.8, connect=0.5))
    except Exception:
        pass


_AUTOBOOT_LOCK = Path(__file__).resolve().parent / "daemon" / ".autoboot"


def _probe_now() -> bool:
    try:
        r = httpx.get(f"{API_BASE}/api/health", timeout=httpx.Timeout(0.7, connect=0.4))
        return r.status_code == 200
    except Exception:
        return False


def ensure_pipeline() -> str:
    """LINK-TO-PIPELINE fix, part 2: the dashboard boots its own daemon.

    Returns one of: "online" | "spawned" | "wait" | "disabled" | "failed".
    Guarded by a 90s marker file so a dozen browser tabs can't stampede.
    """
    if _probe_now():
        return "online"
    if os.getenv("RADAR_AUTOBOOT", "1") != "1":
        return "disabled"
    if st.session_state.autoboot_attempted:
        return "wait"                       # already spawned this session — give it time
    now = time.time()
    try:
        if _AUTOBOOT_LOCK.exists() and now - _AUTOBOOT_LOCK.stat().st_mtime < 90:
            st.session_state.autoboot_attempted = True
            return "wait"
        _AUTOBOOT_LOCK.touch()
    except Exception:
        pass
    root = Path(__file__).resolve().parent
    cmd = [sys.executable, "-u", "run_worker.py"]
    if os.getenv("RADAR_AUTOBOOT_DEMO", "0") == "1":
        cmd.append("--demo")
    kwargs: dict = {}
    if os.name == "nt":
        kwargs["creationflags"] = 0x08000000 | 0x00000200   # CREATE_NO_WINDOW | NEW_GROUP
    else:
        kwargs["start_new_session"] = True
    try:
        log = open(root / "daemon" / "autoboot.log", "ab")
        subprocess.Popen(cmd, cwd=str(root), stdout=log,
                         stderr=subprocess.STDOUT, **kwargs)
        st.session_state.autoboot_attempted = True
        time.sleep(2.5)                     # grace — uvicorn binds fast
        return "online" if _probe_now() else "spawned"
    except Exception:
        return "failed"


# ─────────────────────────────────────────────────────────────────────────────
# NAV BAR — brand | 5 index tabs | ticker search | refresh   (locked order)
# ─────────────────────────────────────────────────────────────────────────────

c_brand, c_tabs, c_search, c_refresh = st.columns(
    [1.55, 5.4, 2.05, 0.95], vertical_alignment="center")

with c_brand:
    st.markdown(
        '<div class="psx-brand"><div class="b1">◆ PSX<em>RADAR</em></div>'
        '<div class="b2">INSTITUTIONAL SENTIMENT ENGINE</div></div>',
        unsafe_allow_html=True,
    )

with c_tabs:
    tab = st.radio(
        "INDEX",
        C.HEADER_TABS,
        horizontal=True,
        label_visibility="collapsed",
        key="tab_sel",
    )
    if tab != st.session_state.active_asset:
        st.session_state.active_asset = tab
        st.session_state.nonce += 1
        st.session_state.search_err = None

with c_search:
    query = st.text_input(
        "TICKER",
        key="q",
        placeholder="ENTER TICKER — E.G. OGDC ⏎",
        label_visibility="collapsed",
    )

with c_refresh:
    refresh = st.button("REFRESH ⟳", use_container_width=True)
    if refresh:
        st.session_state.nonce += 1

# search handling — committed value present on this rerun
if query:
    sym = C.resolve_symbol(query)
    if sym is None:
        st.session_state.search_err = (
            f"UNRECOGNIZED SYMBOL '{query.strip().upper()}' — NOT IN PSX REGISTRY")
    else:
        if sym != st.session_state.active_asset:
            post_watch(sym)                       # pipeline picks it up next tick
            st.session_state.active_asset = sym
            st.session_state.nonce += 1
        st.session_state.search_err = None
    del st.session_state["q"]                     # clear the input either way
    st.rerun()

if st.session_state.search_err:
    st.markdown(f'<div class="psx-err">✕ {st.session_state.search_err}</div>',
                unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────────────────────
# Status strip (with AUTO-BOOT — the dashboard no longer depends on a terminal)
# ─────────────────────────────────────────────────────────────────────────────

health = probe_pipeline(st.session_state.nonce)
boot_state = None
if health is None:
    boot_state = ensure_pipeline()
    if boot_state == "online":
        health = probe_pipeline(1)                # daemon just came up — re-read

if health:
    laya = health.get("laya", {})
    laya_txt = f"LAYA {laya.get('breaker', '—').upper()} · {laya.get('latency_ema_ms', 0):.0f}MS" \
        if laya else "LAYA —"
    if health.get("demo"):
        laya_txt += " · SYNTH FEED"
    bf = health.get("backfill", {})
    if bf.get("enabled", False):
        bf_mode = str(bf.get("mode", "LIVE"))
        bf_depth = bf.get("depth_days", 0.0)
        bf_txt = f"BACKFILL {bf_depth:.1f}D · {bf.get('chunks', 0)} CHUNKS" \
            if bf_mode in ("BACKFILL", "ARCHIVE_FULL", "PAUSED", "CAUGHT_UP") \
            else "CRAWL LIVE ⟶"
        if bf_mode == "ARCHIVE_FULL":
            bf_txt += " (FULL)"
    else:
        bf_txt = "BACKFILL OFF"
    boot_tag = ""
    if boot_state in ("spawned", "online") and boot_state == "spawned":
        boot_tag = '<span style="color:#ffc247">AUTO-BOOTED ↻ LINKING</span>'
    st.markdown(
        f'<div class="psx-status">'
        f'<span><b class="ok">PIPELINE ONLINE</b></span>{boot_tag}'
        f'<span>TICK {health.get("tick", 0)}</span>'
        f'<span>{laya_txt}</span>'
        f'<span>{bf_txt}</span>'
        f'<span>WATCHLIST {len(health.get("watchlist", []))}</span>'
        f'<span style="margin-left:auto;color:rgba(255,255,255,.45)">FULL COUNTERS IN THE PIPELINE BLOCK · DAEMON AUTO-BOOTS WITH THIS PAGE</span>'
        f'</div>',
        unsafe_allow_html=True,
    )
else:
    note = {
        "spawned": "DAEMON AUTO-BOOT STARTED — the three surfaces below will link within seconds (surfaces poll on their own; no restart needed).",
        "wait": "DAEMON WARMING UP — auto-boot already fired; linking momentarily…",
        "disabled": "PIPELINE OFFLINE — auto-boot disabled (RADAR_AUTOBOOT=0): start it with python run_worker.py",
        "failed": "PIPELINE OFFLINE — auto-boot failed; start it with python run_worker.py (see daemon/autoboot.log)",
    }.get(boot_state or "", "PIPELINE OFFLINE — starting daemon…")
    st.markdown(
        f'<div class="psx-status"><span><b class="warn">PIPELINE LINKING</b></span>'
        f'<span>{note}</span></div>',
        unsafe_allow_html=True,
    )

# ─────────────────────────────────────────────────────────────────────────────
# PIPELINE HERO (v3.4) — ingestion & classification is the product, so it gets
# the biggest, most visible block on the page. Served by the API at /hero and
# embedded as a plain <iframe> (same pattern as the three surfaces): it polls
# /api/health + /api/feed every second CLIENT-SIDE, so the giant counters tick
# in real time even though Streamlit itself only reruns on interaction.
# ─────────────────────────────────────────────────────────────────────────────

_asset = quote_plus(st.session_state.active_asset)
_nonce = st.session_state.nonce
_common = f"asset={_asset}&nonce={_nonce}"

st.markdown(
    f'<iframe src="{API_BASE}/hero?{_common}" '
    'width="100%" height="212" frameborder="0" '
    'style="border:none; display:block;"></iframe>',
    unsafe_allow_html=True,
)

# ─────────────────────────────────────────────────────────────────────────
# Deep Field market strip — live price / vol / P/E / FCI (v3)
# ─────────────────────────────────────────────────────────────────────────

feed = probe_feed(st.session_state.nonce, st.session_state.active_asset)
if feed:
    mkt = feed.get("market") or {}
    models = feed.get("models") or {}
    fusion = feed.get("fusion") or {}
    vol = (models.get("vol") or {})
    src = str(mkt.get("source") or "—").upper()
    price = mkt.get("price")
    chg = mkt.get("change_pct")
    fci_v = feed.get("fci", 0.0)
    pe_v = models.get("pe")
    vol_cc = vol.get("cc")
    hk = (models.get("hawkes") or {})

    def _fmt(x, d=2, pct=False):
        if x is None:
            return "—"
        return f"{x:+.{d}f}%" if pct else f"{x:.{d}f}"

    chg_cls = "ok" if (chg or 0) >= 0 else "warn"
    src_label = "PSX LIVE" if src == "PSX" else ("YAHOO" if src == "YAHOO" else "SIM FEED")
    if mkt.get("closed"):
        src_label += " · CLOSED"
    st.markdown(
        f'<div class="psx-status">'
        f'<span><b class="ok">{src_label}</b></span>'
        f'<span>LAST <b class="ok">{_fmt(price, 0 if (price or 0) > 5000 else 2)}</b></span>'
        f'<span>CHG <b class="{chg_cls}">{_fmt(chg, 2, True)}</b></span>'
        f'<span>VOL.ANN <b class="ok">{_fmt(100 * vol_cc if vol_cc is not None else None, 1)}%</b></span>'
        f'<span>P/E <b class="ok">{_fmt(pe_v, 1)}</b></span>'
        f'<span>λ<sub>HAWKES</sub> <b class="ok">{_fmt(hk.get("lambda_now"), 2)}</b></span>'
        f'<span>FCI <b class="ok">{fci_v:+.3f}</b> · WCI <b class="ok">{(feed.get("cs") or 0):+.3f}</b></span>'
        f'<span style="margin-left:auto;color:rgba(255,255,255,.45)">MODELS {(models.get("backend") or "layats-4").upper()}'
        f' · REFITS {models.get("refits", 0)}</span>'
        f'</div>',
        unsafe_allow_html=True,
    )

# ─────────────────────────────────────────────────────────────────────────────
# SINGLE-PAGE GRID — all three surfaces at once (user-locked v3.3):
#   row 1: SENTIMENT SPHERE (left) | FORECAST DECK (right)
#   row 2: RELATIONAL GRAPH (full width)
# Each surface is a persistent iframe polling /api/feed every second
# client-side; identical src across idle reruns keeps the WebGL canvases
# alive, while an asset switch or REFRESH (nonce bump) remounts them.
# (_asset / _nonce / _common are built just above, at the HERO block.)
# ─────────────────────────────────────────────────────────────────────────────

_top_l, _top_r = st.columns([1.0, 1.15], gap="small")
with _top_l:
    st.markdown(
        f'<iframe src="{API_BASE}/surface?{_common}&layout=half" '
        'width="100%" height="660" frameborder="0" '
        'style="border:none; display:block;"></iframe>',
        unsafe_allow_html=True,
    )
with _top_r:
    st.markdown(
        f'<iframe src="{API_BASE}/surface3d?{_common}&layout=half" '
        'width="100%" height="660" frameborder="0" '
        'style="border:none; display:block;"></iframe>',
        unsafe_allow_html=True,
    )

st.markdown('<div class="panel-gap"></div>', unsafe_allow_html=True)

st.markdown(
    f'<iframe src="{API_BASE}/graph3d?{_common}" '
    'width="100%" height="640" frameborder="0" '
    'style="border:none; display:block;"></iframe>',
    unsafe_allow_html=True,
)

st.markdown(
    '<div style="font-family:\'JetBrains Mono\',monospace;font-size:9px;'
    'letter-spacing:.16em;color:rgba(255,255,255,.5);padding:6px 2px 10px;">'
    'PSX RADAR · WCI = P(direction) × relevance^γ × λ(age) · '
    'FCI = (w<sub>s</sub>·WCI + w<sub>m</sub>·MODEL) · align · (1−cascade) · vol_damp · '
    'ALS-gated pre-inference · green = bullish · red = bearish · white = everything else</div>',
    unsafe_allow_html=True,
)

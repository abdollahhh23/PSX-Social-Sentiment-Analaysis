"""
Feed API — the read-only window into the running radar.

Served by the SAME process as the worker daemon (see run_worker.py), reading
the shared PipelineState singleton. Endpoints are polled by the Three.js
monitor surface every second.

    GET  /api/feed?asset=KSE-100   → full live snapshot (score, ratio, nodes…)
    GET  /api/health               → daemon + engine status
    GET  /api/assets               → header indices + searchable registry
    POST /api/watch {"asset":…}    → hot-add a searched ticker to the pipeline

RESILIENCE CONTRACT (v3.3): /api/feed NEVER returns 5xx. A snapshot bug
falls back to the last-good payload (flagged "stale": true) — a transient
error must never flap the surfaces' LINK chip or drop the WebGL canvases.
"""

from __future__ import annotations

import time

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

import config as C
from daemon.store import STATE
from viz.surface import build_surface_html
from viz.forecast3d import build_forecast_html
from viz.graph3d import build_graph_html
from viz.pipeline_hero import build_hero_html

app = FastAPI(title="PSX Radar Feed API", version="1.0.0", docs_url=None, redoc_url=None)

# Streamlit iframes fetch from here — srcdoc iframes send a null origin.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class WatchRequest(BaseModel):
    asset: str


# Last-good snapshot cache — a buggy/exception-ing snapshot degrades to the
# previous payload (stale-flagged) instead of a 500 that would tear down the
# surfaces' link state. (root-cause fix for the recurring LINK TO PIPELINE LOST)
_LAST_GOOD: dict[str, tuple[float, dict]] = {}


@app.get("/api/feed")
def feed(asset: str = "KSE-100"):
    symbol = (asset or "").strip().upper()
    laya_health = WORKER_REF["laya"].health() if WORKER_REF["laya"] else {}
    try:
        snap = STATE.snapshot(symbol, laya_health)
        snap["ok"] = True
        _LAST_GOOD[symbol] = (time.time(), snap)
        return snap
    except Exception as e:                       # never 500 — degrade, flag, keep link alive
        hit = _LAST_GOOD.get(symbol)
        if hit:
            snap = dict(hit[1])
            snap["stale"] = True
            snap["snap_err"] = f"{e.__class__.__name__}"
            snap["ok"] = True
            return snap
        return {"ok": True, "asset": symbol, "stale": True, "snap_err": f"{e.__class__.__name__}",
                "cs": 0.0, "bull_pct": 50.0, "bear_pct": 50.0, "bull_n": 0, "bear_n": 0,
                "neutral_n": 0, "detections": 0, "momentum": 0.0, "spam_pct": 0.0,
                "engine": "fallback", "laya_latency_ms": 0.0, "tick": STATE.tick_count,
                "tick_ms": 0.0, "uptime_s": 0.0, "posts_ingested": STATE.posts_ingested,
                "posts_scored": STATE.posts_scored, "last_new_age_s": None,
                "ingest_per_min": 0, "demo": STATE.demo_mode, "trend": [],
                "nodes": [], "top": [], "sources": {}, "queue_depth": 0,
                "watchlist": list(STATE.watchlist.keys()), "backfill": {"enabled": False},
                "history": {}, "market": {}, "models": {}, "fci": 0.0,
                "fusion": {}, "market_status": {}}


@app.get("/api/health")
def health():
    try:
        laya = WORKER_REF["laya"].health() if WORKER_REF["laya"] else {}
        return {
            "ok": True,
            "daemon": "online",
            "tick": STATE.tick_count,
            "uptime_s": round(time_uptime(), 1),
            "tick_ms": round(STATE.last_tick_ms, 1),
            "demo": STATE.demo_mode,
            "watchlist": list(STATE.watchlist.keys()),
            "posts_ingested": STATE.posts_ingested,
            "posts_scored": STATE.posts_scored,
            "dropped": {"spam": STATE.dropped_spam, "bot": STATE.dropped_bot, "dup": STATE.dropped_dup},
            "laya": laya,
            "backfill": {k: v for k, v in STATE.backfill_stats.items() if k != "assets"},
            "backfill_assets": STATE.backfill_stats.get("assets", {}),
        }
    except Exception as e:                       # health is a heartbeat — it must always answer
        return {"ok": True, "daemon": "degraded", "tick": STATE.tick_count,
                "error": f"{e.__class__.__name__}"}


@app.get("/surface", response_class=HTMLResponse)
def surface(asset: str = "KSE-100", nonce: int = 0, layout: str = "full"):
    """Serve the Three.js monitor document itself.

    The dashboard (app.py) embeds this URL in a plain <iframe> instead of
    inlining a giant HTML blob through Streamlit — no st.components.v1.html
    deprecation warnings, and the canvas polls /api/feed SAME-ORIGIN.
    An empty API base makes every surface fetch relative to this server.
    `layout=half` compacts the HUD rails for the side-by-side grid.
    """
    symbol = (asset or "").strip().upper() or "KSE-100"
    return HTMLResponse(build_surface_html(symbol, "", int(nonce), layout=layout))


@app.get("/surface3d", response_class=HTMLResponse)
def surface3d(asset: str = "KSE-100", nonce: int = 0, layout: str = "full"):
    """Deep Field FORECAST DECK — forecast fan + model cards (v3)."""
    symbol = (asset or "").strip().upper() or "KSE-100"
    return HTMLResponse(build_forecast_html(symbol, "", int(nonce), layout=layout))


@app.get("/graph3d", response_class=HTMLResponse)
def graph3d(asset: str = "KSE-100", nonce: int = 0, layout: str = "full"):
    """Deep Field RELATIONAL GRAPH — live node network (v3)."""
    symbol = (asset or "").strip().upper() or "KSE-100"
    return HTMLResponse(build_graph_html(symbol, "", int(nonce), layout=layout))


@app.get("/hero", response_class=HTMLResponse)
def hero(asset: str = "KSE-100"):
    """PIPELINE HERO — the ingestion & classification headline block (v3.4).

    Served same-origin like the surfaces, so the hero polls /api/health +
    /api/feed with RELATIVE URLs; the dashboard embeds it as a plain
    markdown <iframe> (no st.components API, no deprecation warnings)."""
    symbol = (asset or "").strip().upper() or "KSE-100"
    return HTMLResponse(build_hero_html(symbol, ""))


@app.get("/api/assets")
def assets():
    return {"ok": True, **C.assets_document()}


@app.post("/api/watch")
def watch(req: WatchRequest):
    """Hot-add a searched ticker: the worker fetches it on the NEXT tick."""
    resolved = C.resolve_symbol(req.asset)
    if resolved is None:
        return {"ok": False, "error": "unrecognized-symbol",
                "hint": "Not in the PSX registry — check the ticker spelling."}
    meta = STATE.ensure_asset(resolved)
    if meta is None:
        return {"ok": False, "error": "unregistered-symbol"}
    return {"ok": True, "asset": resolved, "watching": list(STATE.watchlist.keys())}


# The worker registers itself here at boot so /api/feed can expose Laya telemetry.
WORKER_REF: dict = {"laya": None}


def register_worker(worker) -> None:
    WORKER_REF["laya"] = worker.laya


def time_uptime() -> float:
    import time as _t
    return _t.time() - STATE.boot_ts

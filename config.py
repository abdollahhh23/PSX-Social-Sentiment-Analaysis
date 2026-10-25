"""
PSX Institutional Sentiment & Alternative Data Radar
=====================================================
Global configuration — single source of truth for every downstream module.

Sections
--------
1. Laya System 1 decision engine (self-hosted, ~33 ms non-autoregressive backbone)
2. Continuous pipeline (1-second tick, staggered ingestion, inference budget)
3. Asset universes: top-5 PSX indices (header tabs) + searchable company registry
4. Author Legitimacy Score (ALS) bot / Sybil / coordinated-network filtering
5. Weighted Conviction Index (WCI) quant model
6. Source adapters
7. Theme (pure black / semantic green-red)

Everything that could ever need tuning lives here. No magic numbers downstream.
"""

from __future__ import annotations

import os

# ─────────────────────────────────────────────────────────────────────────────
# 1. LAYA SYSTEM 1 — self-hosted decision engine
#    Expected local latency ≈ 33 ms (non-autoregressive ModernBERT-large class).
#    Engine-down is definitive: fail fast on ConnectionError, reserve retries
#    for timeouts / transient HTTP errors, then fall back to the local
#    heuristic scorer so the pipeline NEVER stalls.
# ─────────────────────────────────────────────────────────────────────────────
LAYA_ENDPOINT: str = os.getenv("LAYA_ENDPOINT", "http://localhost:8000/v1/systemone")
LAYA_HEALTH_URL: str = os.getenv("LAYA_HEALTH_URL", "http://localhost:8000/health")
LAYA_TIMEOUT_S: float = float(os.getenv("LAYA_TIMEOUT_S", "0.45"))
LAYA_MAX_RETRIES: int = 2            # only for timeouts / 5xx — never for conn-refused
LAYA_TEXT_TRUNC: int = 600           # chars of post text shipped to the engine

# Circuit breaker (per worker process)
CB_FAILURE_THRESHOLD: int = 5        # consecutive failures → OPEN
CB_COOLDOWN_S: float = 60.0          # OPEN → HALF_OPEN probe after this
CB_SUCCESS_RESET: int = 3            # consecutive successes in HALF_OPEN → CLOSED

# Offline heuristic fallback (kept honest: telemetry always labels the source)
FALLBACK_ENABLED: bool = True

# ─────────────────────────────────────────────────────────────────────────────
# 2. CONTINUOUS PIPELINE
#    The daemon ticks every second. Per tick:
#      a. ingest ONE asset from the rotation (staggered — respects rate limits)
#      b. drain the inference queue up to the budget
#      c. recompute decay-adjusted aggregates for EVERY watched asset
#    => scores / ratios / sphere move every single second even when sources
#       are only polled on their own polite intervals.
# ─────────────────────────────────────────────────────────────────────────────
TICK_INTERVAL_S: float = 1.0
INFERENCE_BUDGET_PER_TICK: int = 60  # max Laya calls per tick (drained in PARALLEL waves)
INFERENCE_CONCURRENCY: int = int(os.getenv("INFERENCE_CONCURRENCY", "8"))
#   60 posts ÷ 8 concurrent × ~85 ms engine RTT ≈ 0.65 s worst-case drain.
MAX_POSTS_PER_FETCH: int = 60   # high-volume ingestion — parse caps, not rate caps
FETCH_TIMEOUT_S: float = float(os.getenv("FETCH_TIMEOUT_S", "5.0"))
FETCH_CONNECT_TIMEOUT_S: float = float(os.getenv("FETCH_CONNECT_TIMEOUT_S", "2.5"))
MAX_CONCURRENT_FETCHES: int = int(os.getenv("MAX_CONCURRENT_FETCHES", "32"))
POST_TTL_HOURS: int = 48             # hard prune regardless of half-life
TREND_WINDOW_TICKS: int = 240        # conviction trend series length fed to the UI
TREND_TAU_S: float = 900.0           # momentum EMA time-constant (15 min)
SQLITE_PATH: str = os.getenv("PSX_RADAR_DB", "daemon/psx_radar.db")
SQLITE_ENABLED: bool = True

# Feed API (served by the same process as the worker)
API_HOST: str = os.getenv("RADAR_API_HOST", "0.0.0.0")
API_PORT: int = int(os.getenv("RADAR_API_PORT", "8001"))

# ─────────────────────────────────────────────────────────────────────────────
# 3. ASSET UNIVERSES
#    HEADER TABS  → top-5 PSX indices only (user requirement).
#    COMPANY HITS → via the nav search bar (validated against the registry);
#                   a search hot-adds the ticker to the live watchlist.
# ─────────────────────────────────────────────────────────────────────────────

INDEX_UNIVERSE: dict[str, dict] = {
    "KSE-100": {
        "name": "KSE 100 Index",
        "kind": "index",
        "queries": [
            "PSX KSE-100 index",
            "Pakistan Stock Exchange KSE100 benchmark",
            "KSE-100 index points close",
            # market-wide volume boosters — more of the chatter stream flows
            # through every index poll (all queries fire in parallel)
            "Pakistan Stock Exchange",
            "PSX stocks market",
            "Karachi stock exchange",
        ],
        "half_life_min": 360,         # session-scale decay — daily news cadence
    },
    "KSE-30": {
        "name": "KSE 30 Index",
        "kind": "index",
        "queries": ["KSE-30 index Pakistan", "KSE30 PSX", "Pakistan stock exchange today",
                     "Pakistan Stock Exchange", "PSX shares rally"],
        "half_life_min": 360,
    },
    "KSE-50": {
        "name": "KSE 50 Index",
        "kind": "index",
        "queries": ["KSE-50 index Pakistan", "KSE50 PSX", "PSX benchmark index",
                     "Pakistan Stock Exchange", "PSX equities today"],
        "half_life_min": 360,
    },
    "KMI-30": {
        "name": "KMI 30 Meezan Index",
        "kind": "index",
        "queries": ["KMI-30 index Meezan", "KMI30 Shariah PSX",
                     "Pakistan Shariah stocks", "PSX KMI-30"],
        "half_life_min": 480,
    },
    "ALL-SHR": {
        "name": "KSE All-Share Index",
        "kind": "index",
        "queries": ["KSE all share index", "PSX all share market capitalization",
                     "Pakistan equities market", "Pakistan Stock Exchange",
                     "PSX trading volume today"],
        "half_life_min": 480,
    },
}

# Header tab order (user-locked)
HEADER_TABS: list[str] = list(INDEX_UNIVERSE.keys())

# Searchable company registry — indices are NOT here; these are individual
# tickers reachable through the nav search bar. The original six (OGDC, HUBC,
# SYS, FFC, LUCK …) live here as first-class entries. Half-lives are longer
# than the indices': corporate news cycles run slower than market chatter.
COMPANY_REGISTRY: dict[str, dict] = {
    "OGDC":  {"name": "Oil & Gas Development Co", "sector": "Energy — E&P",
              "queries": ["OGDC stock", "OGDC Pakistan shares", "Oil and Gas Development PSX"],
              "half_life_min": 720},
    "PPL":   {"name": "Pakistan Petroleum Ltd", "sector": "Energy — E&P",
              "queries": ["PPL stock Pakistan", "Pakistan Petroleum PSX"],
              "half_life_min": 720},
    "POL":   {"name": "Pakistan Oilfields Ltd", "sector": "Energy — E&P",
              "queries": ["POL stock Pakistan", "Pakistan Oilfields PSX"],
              "half_life_min": 720},
    "MARI":  {"name": "Mari Petroleum", "sector": "Energy — E&P",
              "queries": ["MARI stock Pakistan", "Mari Petroleum PSX"],
              "half_life_min": 720},
    "PSO":   {"name": "Pakistan State Oil", "sector": "Energy — OMC",
              "queries": ["PSO stock Pakistan", "Pakistan State Oil PSX"],
              "half_life_min": 720},
    "HUBC":  {"name": "Hub Power Co", "sector": "Power",
              "queries": ["HUBC stock", "Hub Power PSX"],
              "half_life_min": 720},
    "KAPCO": {"name": "Kot Addu Power Co", "sector": "Power",
              "queries": ["KAPCO stock", "Kot Addu Power PSX"],
              "half_life_min": 720},
    "NCPL":  {"name": "Nishat Chunian Power", "sector": "Power",
              "queries": ["NCPL stock", "Nishat Chunian Power PSX"],
              "half_life_min": 720},
    "SYS":   {"name": "Systems Ltd", "sector": "Technology",
              "queries": ["SYS stock Systems Ltd", "Systems Ltd PSX"],
              "half_life_min": 600},
    "NETSOL": {"name": "NETSOL Technologies", "sector": "Technology",
              "queries": ["NETSOL stock", "NETSOL Technologies PSX"],
              "half_life_min": 600},
    "TPL":   {"name": "TPL Corp", "sector": "Technology",
              "queries": ["TPL Corp stock", "TPL Corp PSX"],
              "half_life_min": 600},
    "AVN":   {"name": "Avanceon", "sector": "Technology",
              "queries": ["AVN stock", "Avanceon Pakistan PSX"],
              "half_life_min": 600},
    "FFC":   {"name": "Fauji Fertilizer Co", "sector": "Fertilizer",
              "queries": ["FFC stock", "Fauji Fertilizer PSX"],
              "half_life_min": 720},
    "EFERT": {"name": "Engro Fertilizers", "sector": "Fertilizer",
              "queries": ["EFERT stock", "Engro Fertilizers PSX"],
              "half_life_min": 720},
    "ENGRO": {"name": "Engro Corp", "sector": "Conglomerate",
              "queries": ["ENGRO stock", "Engro Corp PSX"],
              "half_life_min": 720},
    "LUCK":  {"name": "Lucky Cement", "sector": "Cement",
              "queries": ["LUCK stock Lucky Cement", "Lucky Cement PSX"],
              "half_life_min": 720},
    "DGKC":  {"name": "D.G. Khan Cement", "sector": "Cement",
              "queries": ["DGKC stock", "DG Khan Cement PSX"],
              "half_life_min": 720},
    "MLCF":  {"name": "Maple Leaf Cement", "sector": "Cement",
              "queries": ["MLCF stock", "Maple Leaf Cement PSX"],
              "half_life_min": 720},
    "MCB":   {"name": "MCB Bank", "sector": "Banking",
              "queries": ["MCB stock", "MCB Bank Pakistan PSX"],
              "half_life_min": 720},
    "UBL":   {"name": "United Bank Ltd", "sector": "Banking",
              "queries": ["UBL stock", "United Bank Pakistan PSX"],
              "half_life_min": 720},
    "BAHL":  {"name": "Bank AL Habib", "sector": "Banking",
              "queries": ["BAHL stock", "Bank AL Habib PSX"],
              "half_life_min": 720},
    "HBL":   {"name": "HBL Bank", "sector": "Banking",
              "queries": ["HBL stock", "HBL Pakistan PSX"],
              "half_life_min": 720},
}

# Alias index for the search bar: lowercase alias → canonical ticker.
SYMBOL_INDEX: dict[str, str] = {}
for _tk, _meta in COMPANY_REGISTRY.items():
    SYMBOL_INDEX[_tk.lower()] = _tk
    SYMBOL_INDEX[_meta["name"].lower()] = _tk
for _tk, _meta in INDEX_UNIVERSE.items():
    SYMBOL_INDEX[_tk.lower()] = _tk
    SYMBOL_INDEX[_meta["name"].lower()] = _tk


def resolve_symbol(query: str) -> str | None:
    """Resolve a free-text nav search to a canonical asset symbol (or None)."""
    q = (query or "").strip().lower()
    if not q:
        return None
    if q in SYMBOL_INDEX:
        return SYMBOL_INDEX[q]
    # tolerant: strip $ / .PSX suffixes, e.g. "$ogdc" or "OGDC.PSX"
    stripped = q.lstrip("$").removesuffix(".psx").strip()
    return SYMBOL_INDEX.get(stripped)


# ─────────────────────────────────────────────────────────────────────────────
# 4. AUTHOR LEGITIMACY SCORE (ALS)
#    Computed BEFORE any inference — bots / Sybil rings never touch Laya.
#    Weighted over the dimensions actually available per source; missing
#    dimensions default to a neutral 0.5 and weights are renormalized.
# ─────────────────────────────────────────────────────────────────────────────
ALS_THRESHOLD: float = 0.35         # below → quarantined, no inference spent
ALS_W_ACCOUNT_AGE: float = 0.30
ALS_W_HISTORY: float = 0.25         # author tenure / repeat sightings
ALS_W_ENGAGEMENT: float = 0.20      # authentic engagement vs hollow spikes
ALS_W_PURITY: float = 0.25          # 1 − spam-lexicon risk

# Coordinated / Sybil ring detection
FUZZY_JACCARD_THRESHOLD: float = 0.82   # 4-gram Jaccard → near-duplicate
COORD_CLUSTER_MIN_AUTHORS: int = 3      # ≥3 authors pushing same text in window
COORD_WINDOW_S: float = 600.0           # 10-minute coordination window
COORD_ALS_PENALTY: float = 1.0          # exact penalty subtracted (kill switch)

# News-domain reputation (ALS base for editorial sources — no "author" exists)
DOMAIN_REPUTATION: dict[str, float] = {
    "reuters.com": 0.95, "bloomberg.com": 0.95, "ft.com": 0.93,
    "businessrecorder.com": 0.88, "profit.pakistantoday.com.pk": 0.82,
    "thenews.com.pk": 0.80, "dawn.com": 0.85, "tribune.com.pk": 0.72,
    "mettisglobal.news": 0.74, "brecorder.com": 0.85,
    "dunyanews.tv": 0.68, "samaa.tv": 0.65, "geo.tv": 0.60,
    "arynews.tv": 0.62, "thefinancedaily.com": 0.70, "arabnews.pk": 0.72,
    "nation.com.pk": 0.66,
}
DOMAIN_REPUTATION_DEFAULT: float = 0.50

# ─────────────────────────────────────────────────────────────────────────────
# 5. WCI — WEIGHTED CONVICTION INDEX
#    WCI(post) = P(direction) × W(institutional_relevance) × λ(age)
#      W(r)    = r ^ WCI_GAMMA          (meme noise suppressed super-linearly)
#      λ(age)  = 2 ^ (−age / H_eff)     H_eff = half_life × URGENCY_MULT
#    Net conviction  CS = (B − S) / (B + S + ε) ∈ [−1, +1]
#    Sentiment ratio      = B : S over directional posts (neutral excluded)
# ─────────────────────────────────────────────────────────────────────────────
WCI_GAMMA: float = 1.35
WCI_FLOOR_RELEVANCE: float = 0.15   # below → treated as pure noise (w=0)
URGENCY_HALF_LIFE_MULT: dict[str, float] = {"low": 0.75, "moderate": 1.0, "critical": 1.6}

# ─────────────────────────────────────────────────────────────────────────────
# 6. SOURCE ADAPTERS — polite, incremental, rate-limit aware
# ─────────────────────────────────────────────────────────────────────────────
USER_AGENT: str = "PSX-Sentiment-Radar/1.0 (market-research; contact: desk@psxradar.local)"

# Poll intervals are deliberately AGGRESSIVE now: every (source, asset) fetch
# fires with ALL of its queries/sub-feeds in parallel and the hygiene gate
# absorbs repeats for free, so tighter cadence costs almost nothing and the
# ledger fills seconds after a story breaks instead of minutes.
SOURCE_MIN_INTERVAL_S: dict[str, float] = {
    "google_news": float(os.getenv("GN_INTERVAL_S", "40.0")),    # RSS poll per asset
    "bing_news":   float(os.getenv("BING_INTERVAL_S", "50.0")),
    "wires":       float(os.getenv("WIRE_INTERVAL_S", "65.0")),
    "reddit":      float(os.getenv("REDDIT_INTERVAL_S", "30.0")),  # public JSON endpoints
    "x":           60.0,    # only if X_BEARER_TOKEN is present
    "demo":        0.0,     # synthetic generator may fire every tick
}
REDDIT_SUBREDDITS: list[str] = ["StocksPakistan", "pakstocks"]
REDDIT_LIMIT: int = int(os.getenv("REDDIT_LIMIT", "50"))
X_BEARER_TOKEN: str | None = os.getenv("X_BEARER_TOKEN")

# ─────────────────────────────────────────────────────────────────────────────
# 6b. BACKFILL — bidirectional crawl ("keep going backwards")
#     Forward is ALWAYS the priority: every live poll still fetches the newest
#     page first. Only when a (source, asset) pair has parsed EVERYTHING the
#     current timeframe offers (BACKFILL_CATCHUP_POLLS consecutive polls where
#     every returned item was a duplicate) does the walker step BACKWARDS —
#     older pages / date-windowed queries — and it resumes from its saved
#     cursor after every forward interruption, so depth is never re-walked.
#     Backfilled posts pass the SAME hygiene gate + Laya + ledger; posts older
#     than POST_TTL_HOURS decay out of the live WCI (honest math) but stay in
#     SQLite, where the 30-day daily-bias history is computed from.
# ─────────────────────────────────────────────────────────────────────────────
BACKFILL_ENABLED: bool = os.getenv("BACKFILL", "1") == "1"
BACKFILL_CATCHUP_POLLS: int = int(os.getenv("BACKFILL_CATCHUP_POLLS", "2"))
#   consecutive zero-fresh forward polls before a pair is "caught up"
BACKFILL_INTERVAL_S: float = float(os.getenv("BACKFILL_INTERVAL_S", "10.0"))
#   one backfill sweep cadence; each sweep pulls ≤ BACKFILL_CHUNKS_PER_CYCLE chunks
BACKFILL_CHUNKS_PER_CYCLE: int = int(os.getenv("BACKFILL_CHUNKS_PER_CYCLE", "2"))
BACKFILL_MAX_DEPTH_DAYS: int = int(os.getenv("BACKFILL_MAX_DEPTH_DAYS", "30"))
#   how far back the walk may go before the pair is marked ARCHIVE FULL
BACKFILL_WINDOW_DAYS: dict[str, int] = {
    "google_news": 3,     # after:/before: date-window per backward chunk
    "bing_news": 2,       # offset pages ≈10 items; 2 pages ≈ one news cycle
}
BACKFILL_EMPTY_STREAK: int = 2
#   consecutive empty backward chunks before a pair is declared exhausted
#   (prevents burning requests past the archive end)
FRESH_HEARTBEAT_WINDOW_S: float = 900.0
#   only posts CREATED within this window advance "Last New Signal" — deep
#   backfill work shows in the BACKFILL chip + Ingest Pulse instead

# Keyless Pakistani business wires — market-wide editorial chatter. Index
# assets take the whole feed; company assets only headlines that mention the
# ticker/company. A feed that 403s (e.g. Dawn against datacenter IPs) simply
# degrades to zero posts — never breaks the tick.
WIRE_FEEDS: list[tuple[str, str]] = [
    ("brecorder.com", "https://www.brecorder.com/feeds/business"),
    ("tribune.com.pk", "https://tribune.com.pk/feed/business"),
    ("dawn.com", "https://www.dawn.com/feeds/business"),
    # v3.3 volume wires — verified live feeds (a feed that 403s on any given
    # network simply degrades to zero posts; never breaks the tick)
    ("thenews.com.pk", "https://www.thenews.com.pk/rss/1/1"),
    ("geo.tv", "https://www.geo.tv/rss/1/3"),
    ("nation.com.pk", "https://www.nation.com.pk/rss/business"),
]

# ─────────────────────────────────────────────────────────────────────────────
# 7. THEME — pure black; green/red are SEMANTIC ONLY (bull / bear)
# ─────────────────────────────────────────────────────────────────────────────
THEME: dict[str, str] = {
    "bg":       "#000000",   # real black — not #0e1117 gray
    "fg":       "#eaeaea",
    "muted":    "#7a7f87",
    "faint":    "#3a3f46",
    "hairline": "#1a1c1f",
    "grid":     "#141619",
    "green":    "#00ff88",   # bullish ONLY
    "red":      "#ff3b4e",   # bearish ONLY
    "amber":    "#ffb020",   # telemetry warnings only
}

DEMO_MODE_POSTS_PER_TICK: tuple[int, int] = (2, 5)   # --demo synthetic burst bounds


# ─────────────────────────────────────────────────────────────────────────────
# 8. MARKET DATA LAYER — real-time PSX Data Portal + free-API fallbacks
#    Primary: dps.psx.com.pk (official PSX Data Portal, keyless):
#      POST /historical   {month, year, symbol} → HTML OHLCV table (NOT JSON)
#      GET  /symbols      → all listed tickers (JSON)
#      POST /data/symbol-position · /til-portal/get-symbol-data → live quote
#    KNOWN GOTCHA (documented by the psx-dps project): the DPS zone publishes
#    a single rotating A record and SOME nodes 404 every data route while the
#    website still serves 200. We therefore probe candidate nodes, pin a
#    healthy one, and re-probe on failure — a data-route 404 is never trusted.
#    Fallback chain: PSX DPS → Yahoo Finance chart (^KSE) → seeded simulator
#    (simulator output is ALWAYS labeled "sim" — never passed off as live).
# ─────────────────────────────────────────────────────────────────────────────
MARKET_ENABLED: bool = os.getenv("MARKET_DATA", "1") == "1"
DPS_BASE: str = os.getenv("DPS_BASE", "https://dps.psx.com.pk")
DPS_HISTORICAL_PATH: str = os.getenv("DPS_HISTORICAL_PATH", "/historical")
DPS_SYMBOLS_PATH: str = os.getenv("DPS_SYMBOLS_PATH", "/symbols")
# live-quote routes tried in order; the first that yields a price is pinned
DPS_QUOTE_CANDIDATES: list[str] = [
    "/data/symbol-position",
    "/til-portal/get-symbol-data",
]
DPS_MIN_INTERVAL_S: float = float(os.getenv("DPS_MIN_INTERVAL_S", "1.0"))  # ≤1 rps etiquette
DPS_TIMEOUT_S: float = float(os.getenv("DPS_TIMEOUT_S", "6.0"))
QUOTE_TTL_S: float = float(os.getenv("QUOTE_TTL_S", "10.0"))
MARKET_POLL_S: float = float(os.getenv("MARKET_POLL_S", "3.0"))
HISTORY_MONTHS: int = int(os.getenv("HISTORY_MONTHS", "14"))    # ~280 trading days
HISTORY_FETCH_WORKERS: int = int(os.getenv("HISTORY_FETCH_WORKERS", "4"))
MARKET_CACHE_PATH: str = os.getenv("PSX_MARKET_CACHE", "daemon/psx_market_cache.json")
MARKET_TZ_OFFSET_H: int = 5                                      # PKT = UTC+5
MARKET_OPEN_H: int = 9
MARKET_OPEN_M: int = 30
MARKET_CLOSE_H: int = 15
MARKET_CLOSE_M: int = 30
# Yahoo chart API fallback (free, keyless). Only symbols mapped here are tried.
YAHOO_SYMBOLS: dict[str, str] = {"KSE-100": "%5EKSE"}
# Simulator cadence (labeled SIM in the UI; keeps the Deep Field panels alive
# offline / outside market hours so the models always have something to chew)
SIM_STEP_S: float = float(os.getenv("SIM_STEP_S", "2.0"))
SIM_VOL_ANN: float = float(os.getenv("SIM_VOL_ANN", "0.17"))     # PSX-like annualized vol
SIM_DRIFT_ANN: float = float(os.getenv("SIM_DRIFT_ANN", "0.10"))

# ─────────────────────────────────────────────────────────────────────────────
# 9. MODEL ZOO — Deep Field forecast engines (PURE-STDLIB ports, no numpy/julia)
#      syreg     — PySR-style symbolic regression (genetic pareto front,
#                  complexity-penalized hall-of-fame, time-boxed search)
#      arma      — ARMA(1,1) conditional least squares (joint φ/θ grid)
#      hawkes    — tick-style univariate self-exciting point process
#                  (exponential kernel, profile MLE over a 2-D refined grid)
#      conformal — MAPIE-style split-conformal prediction bands + coverage
#      ens       — TimesFM-style adapter: if the real `timesfm` + torch stack
#                  is importable it is used verbatim; otherwise the built-in
#                  LayaTS ensemble (EWMA · Holt-damped · ARMA · SYMREG) with
#                  softmax inverse-MSE weights carries the forecast.
#    Refits are TIME-BOXED and round-robined across the watchlist so the 1 s
#    sentiment tick is never blocked by model fitting.
# ─────────────────────────────────────────────────────────────────────────────
HISTORY_WINDOW: int = int(os.getenv("HISTORY_WINDOW", "260"))   # closes per model
MODEL_REFIT_S: float = float(os.getenv("MODEL_REFIT_S", "5.0")) # per-asset refit cadence
MODEL_HORIZONS: list[int] = [1, 3, 5, 10]                       # trading days
SYMREG_POPULATION: int = int(os.getenv("SYMREG_POPULATION", "48"))
SYMREG_GENERATIONS: int = int(os.getenv("SYMREG_GENERATIONS", "24"))
SYMREG_TIME_BUDGET_S: float = float(os.getenv("SYMREG_TIME_BUDGET_S", "0.30"))
SYMREG_PARSIMONY: float = float(os.getenv("SYMREG_PARSIMONY", "0.004"))
SYMREG_MAX_DEPTH: int = 5
HAWKES_EVENT_WINDOW_H: int = 6          # lookback for directional-burst events
HAWKES_JUMP_SIGMA: float = 2.5          # |return| > n·σ counts as a jump event
CONFORMAL_ALPHA: float = float(os.getenv("CONFORMAL_ALPHA", "0.20"))  # 80% bands
CONFORMAL_WINDOW: int = 120             # calibration residual window
ENSEMBLE_TAU: float = 0.004             # softmax temperature over component MSE
VOL_WINDOW: int = 20                    # close-to-close vol window (trading days)
VOL_PCTL_WINDOW: int = 250              # trailing window for vol regime percentile
TRAIL_POINTS: int = 90                  # closes shipped to the forecast 3D panel

# ─────────────────────────────────────────────────────────────────────────────
# 10. FUSION — FCI (Fused Conviction Index) — "better weighted conviction"
#     FCI blends the chatter-driven WCI conviction with the model zoo's
#     quantitative directional signal, then applies two regime guards:
#       · cascade penalty  — when Hawkes' branching ratio says chatter is
#         self-exciting (contagion), conviction is dampened, not amplified
#       · vol dampening    — at extreme volatility percentiles conviction
#         shrinks (models and sentiment both get unreliable there)
#     Alignment: sentiment and models agreeing REINFORCES the score.
# ─────────────────────────────────────────────────────────────────────────────
FCI_W_SENTIMENT: float = float(os.getenv("FCI_W_SENTIMENT", "0.55"))
FCI_W_MODELS: float = float(os.getenv("FCI_W_MODELS", "0.45"))
FCI_CASCADE_PENALTY: float = float(os.getenv("FCI_CASCADE_PENALTY", "0.40"))
FCI_BRANCHING_RISK: float = float(os.getenv("FCI_BRANCHING_RISK", "0.75"))
FCI_VOL_EXTREME_PCTL: float = float(os.getenv("FCI_VOL_EXTREME_PCTL", "0.92"))
FCI_VOL_DAMP: float = float(os.getenv("FCI_VOL_DAMP", "0.65"))

# ─────────────────────────────────────────────────────────────────────────────
# 11. FUNDAMENTALS — STATIC, EDITABLE. Annual-report EPS (PKR, diluted) for
#     live P/E = price / EPS; trailing P/E for indices is PSX-published.
#     Values below are editable approximations for offline/demo continuity —
#     update them at each reporting season (clearly labeled STATIC in the UI).
# ─────────────────────────────────────────────────────────────────────────────
FUNDAMENTALS: dict[str, dict] = {
    # indices — PSX-published trailing P/E (static); base_price seeds the sim
    "KSE-100": {"pe_static": 8.6,  "base_price": 160500.0},
    "KSE-30":  {"pe_static": 8.1,  "base_price": 51800.0},
    "KSE-50":  {"pe_static": 8.4,  "base_price": 47200.0},
    "KMI-30":  {"pe_static": 9.2,  "base_price": 95400.0},
    "ALL-SHR": {"pe_static": 9.0,  "base_price": 108200.0},
    # companies — EPS feeds live P/E; base_price seeds the simulator
    "OGDC":  {"eps": 33.5,  "base_price": 205.0},
    "PPL":   {"eps": 17.2,  "base_price": 178.0},
    "POL":   {"eps": 88.0,  "base_price": 512.0},
    "MARI":  {"eps": 61.0,  "base_price": 640.0},
    "PSO":   {"eps": 24.0,  "base_price": 176.0},
    "HUBC":  {"eps": 22.5,  "base_price": 152.0},
    "KAPCO": {"eps": 6.4,   "base_price": 33.0},
    "NCPL":  {"eps": 4.9,   "base_price": 22.0},
    "SYS":   {"eps": 24.0,  "base_price": 168.0},
    "NETSOL":{"eps": 11.5,  "base_price": 110.0},
    "TPL":   {"eps": 2.1,   "base_price": 38.0},
    "AVN":   {"eps": 2.6,   "base_price": 31.0},
    "FFC":   {"eps": 20.8,  "base_price": 128.0},
    "EFERT": {"eps": 15.6,  "base_price": 82.0},
    "ENGRO": {"eps": 40.5,  "base_price": 288.0},
    "LUCK":  {"eps": 82.0,  "base_price": 645.0},
    "DGKC":  {"eps": 21.0,  "base_price": 108.0},
    "MLCF":  {"eps": 12.0,  "base_price": 82.0},
    "MCB":   {"eps": 48.5,  "base_price": 285.0},
    "UBL":   {"eps": 58.0,  "base_price": 338.0},
    "BAHL":  {"eps": 10.5,  "base_price": 62.0},
    "HBL":   {"eps": 27.0,  "base_price": 155.0},
}
SIM_DEFAULT_BASE: float = 100.0       # simulator seed for unmapped symbols


def assets_document() -> dict:
    """One-line inventory used by /api/assets and boot banners."""
    return {
        "header_indices": {k: v["name"] for k, v in INDEX_UNIVERSE.items()},
        "searchable_companies": {k: v["name"] for k, v in COMPANY_REGISTRY.items()},
    }

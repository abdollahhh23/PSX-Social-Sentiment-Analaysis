# ◆ PSX RADAR — Institutional Sentiment & Alternative Data Radar

Everything in one repo: the radar pipeline **and** its Laya System 1 decision
engine, wired for end-to-end local setup with Docker Desktop + two CMD
scripts.

Production-grade Python pipeline that ingests social + news chatter around
Pakistan Stock Exchange (PSX) assets, filters bot/Sybil noise **before**
inference, scores every surviving post through the self-hosted **Laya System
1** engine (Docker container, ~2 ms in heuristic mode), fuses it into a
decay-weighted **Weighted Conviction Index (WCI)** — and streams the result,
every single second, into a pure-black Three.js **Sentiment Sphere**.

**v3 "Deep Field"** adds the quantitative half of the desk:

* **Real-time market data** — the official PSX Data Portal (`dps.psx.com.pk`,
  keyless: live quotes + EOD OHLCV history) with node probe/pin failover,
  Yahoo Finance chart fallback, and a clearly-labeled seeded simulator so the
  desk never goes dark offline.
* **A model zoo of 5 forecast engines** — PySR-style symbolic regression,
  tick-style Hawkes process, ARMA(1,1), MAPIE-style conformal bands and a
  TimesFM-style ensemble — all pure-stdlib ports, all refitting live.
* **Volatility (CC · Parkinson · Garman-Klass), live P/E, and the FCI** — a
  fused conviction index that blends chatter conviction with the models'
  signed edge, guarded by Hawkes cascade risk and the vol regime.
* **Two realtime Deep Field surfaces** — a FORECAST DECK (price ribbon + P10/
  P50/P90 fan + model pins) and a RELATIONAL GRAPH whose nodes are the actual
  pipeline components and whose edges pulse with live weights.

```
                    ┌── Docker container :8000 ──┐
                    │  laya/  LAYA SYSTEM 1      │
                    │  POST /v1/systemone        │
                    └───────────▲────────────────┘
                                │ 3 typed questions (~2 ms)
scrape ──▶ ALS hygiene gate ──▶ Laya client ──▶ WCI fusion ──▶ Sentiment Sphere
 (1s stagger)  (bots never        (radar          (λ decays      (nodes streak in
                reach the engine)  worker :8001)   every tick)    green/red, 60fps)
                                                                    │
                    ┌───────────────────────────────────────────────┤
                    │ DEEP FIELD (v3)                               │
PSX DPS ──▶ market  │  quotes + EOD history + vol + P/E             │
layers  │  loop     │            │                                  │
        ▼           │            ▼                                  │
     model zoo ── FUSION (FCI) ──▶ FORECAST DECK  +  RELATIONAL GRAPH
     (ARMA · SYMREG · HAWKES · CONFORMAL · ENSEMBLE)     (live node network)
                                                                    │
                                                    Streamlit dashboard :8501
                                                    (viewer — view switcher:
                                                     SPHERE · FORECAST · GRAPH)
```

## Repo layout

```
psx_sentiment_radar/
├── setup.cmd              ← ONE-TIME: pip deps + build/start Laya container
├── START_ALL.cmd          ← DAILY: engine check + worker window + dashboard window
├── test_engine.cmd        ← health + sample score against Laya
├── stop_all.cmd           ← stops the Laya container
├── docker-compose.yml     ← optional alternative to the .cmd scripts
├── requirements.txt       ← radar deps (streamlit, httpx, fastapi, uvicorn)
├── config.py              ← universes, Laya endpoint, ALS/WCI params, theme,
│                            market data, model zoo, FCI weights, fundamentals
├── app.py                 ← pure-black Streamlit shell (nav + view switcher
│                            + Deep Field metrics strip + persistent iframe)
├── run_worker.py          ← starts the 1s worker + feed API in ONE process
├── run_app.py             ← starts the Streamlit dashboard
├── scraper/               ← Google News RSS · Bing News RSS · PK business wires (BR/Tribune/Dawn) · Reddit · X (env-gated) · demo synth + ALS hygiene gate — ALL fetched in parallel
├── engine/                ← laya_client (typed protocol, breaker, fallback)
│                            + analytics (WCI ledger + FCI fusion)
├── daemon/                ← 1s tick worker (parallel ingest + parallel
│                            inference) + market loop + model-refit loop
│                            + state store (SQLite persistence)
├── data/                  ← DEEP FIELD market layer: PSX Data Portal client
│                            (node probe/pin failover) · Yahoo fallback ·
│                            seeded simulator · EOD HTML parser · disk cache
├── models/                ← DEEP FIELD zoo (pure stdlib): arma.py ·
│                            symbolic.py (PySR-style) · hawkes.py (tick-style)
│                            · conformal.py (MAPIE-style) · ensemble.py
│                            (TimesFM adapter + LayaTS ensemble) · zoo.py
├── api/                   ← feed API: /api/feed /api/health /api/assets
│                            /api/watch + /surface + /surface3d + /graph3d
├── viz/                   ← Three.js surfaces: sentiment sphere · forecast
│                            deck · relational graph
├── scripts/               ← test_deep_field.sh (E2E) + QA helpers
└── laya/                  ← LAYA SYSTEM 1 ENGINE (zero-dependency, stdlib only)
    ├── app.py             ←    HTTP surface: GET /health · POST /v1/systemone
    ├── engine.py          ←    decision core: heuristic lexicon (+ optional FinBERT mode)
    ├── Dockerfile         ←    ~60 MB image, NO pip, NO network at build
    ├── Dockerfile.finbert ←    optional transformer mode (~2.5 GB, opt-in)
    └── payload.json       ←    sample radar-shaped request for testing
```

## End-to-end quickstart (Windows CMD)

Prerequisites: **Docker Desktop running**, **Python 3.10+** on PATH, internet
for the first `pip install` only.

```cmd
cd C:\path\to\psx_sentiment_radar
setup.cmd          REM one-time: deps + Laya container (builds offline)
START_ALL.cmd      REM every day after that
```

`START_ALL.cmd` checks the Laya container (starts it or builds it if
missing), opens the **worker** window (1s pipeline + feed API on :8001) and
the **dashboard** window (Streamlit on :8501). Open
**http://localhost:8501** — the HUD should read `LAYA ENGINE` with live
latency telemetry.

> **After updating the code** (engine changes included), rebuild the engine
> once so the container matches the repo: `docker compose up -d --build laya`
> — it takes seconds because the image has zero pip steps.

**Offline/no-chatter testing:** `START_ALL.cmd --demo` injects clearly-labeled
synthetic PSX chatter (`SYNTH FEED` chip) so the full pipeline can be
exercised without live sources.

**Manual alternative** (if you prefer typing it out):

```cmd
docker build -t laya-engine:latest laya
docker run -d --name laya --restart unless-stopped -p 8000:8000 laya-engine:latest

pip install -r requirements.txt
python run_worker.py          REM terminal 1
python run_app.py             REM terminal 2
```

**Compose alternative** for the engine: `docker compose up -d`.

## What runs where

| Piece | Where | Port | Notes |
|---|---|---|---|
| Laya System 1 engine | Docker container `laya` | **8000** | `POST /v1/systemone`, `GET /health`; zero external calls, stateless |
| Radar worker + feed API | `run_worker.py` window | **8001** | the always-on 1s tick: ingest → hygiene → inference → decay/aggregate |
| Dashboard | `run_app.py` window | **8501** | pure viewer — reads the feed API, never computes |

## The nav bar (user-locked layout)

```
[ ◆ PSX RADAR ]  [KSE-100] [KSE-30] [KSE-50] [KMI-30] [ALL-SHR]  [SEARCH] [⟳]
                  └────── top-5 indices ONLY ──────┘   ↑ before refresh
```

* **Tabs** — the five PSX indices. Nothing else.
* **Search** — registered company tickers (`OGDC`, `HUBC`, `SYS`, `FFC`,
  `LUCK`, `MCB`, …). A valid symbol **hot-adds the asset to the running
  pipeline**; it is scored on the next tick and the sphere re-targets.
  Invalid symbols get a clean inline error.
* **Refresh** — forces a fresh snapshot + health probe.

## Ingestion performance (v2 — parallel everything)

The pipeline was re-architected for throughput after live testing showed a
trickle (≈69 posts in the first 160 ticks). What changed:

* **Parallel sources** — every due (source × asset) pair fires concurrently
each tick (`asyncio.gather` + a shared 24-thread fetch pool); one rotation
slot now costs ≈ max(single request), not the sum.
* **Parallel queries** — Google News fires ALL of an asset's search queries on
one poll (the old one-query-per-poll cursor needed ~270 s to cover one asset);
Bing News does the same; Reddit pulls both subreddits concurrently.
* **More keyless sources** — Google News RSS + **Bing News RSS** + Pakistani
business **wires** (Business Recorder, Tribune, Dawn — deduped across the
rotation with a content cache) + Reddit public JSON + optional X cashtag.
* **Parallel inference** — the Laya queue drains in 8-wide waves
(`INFERENCE_CONCURRENCY`), so even at ~85 ms/container RTT a full 60-post
budget clears in < 0.7 s and the 1 s tick never stalls.
* **Decay tuned to the news cycle** — half-lives are session-scale (indices
6–8 h, companies 10–12 h) and the ledger prunes only at the 48 h TTL; the old
6×H_eff prune was silently emptying the book of every headline older than a
few hours, pinning WCI at 0.000 and the ratio at 50:50.
* **Lexicon v2 (laya 1.1.0)** — real market-report vocabulary (gains, rises,
closes higher, extends losses, selloff, institutional buying…) so live wire
headlines stop being mis-classified neutral.

Measured cold-start on live sources: **≈ 120 posts ingested + scored within
the first 15 seconds** (vs 69 posts in 160 ticks before), Laya breaker closed,
EMA latency 9–23 ms.

## Bidirectional crawl (v3.2 — "keep going backwards")

The live sweep only ever fetches page 1 of *now*. On closed-market days every
source drains within minutes, every re-poll returns identical items, the
hygiene gate drops them all — and the dashboard freezes (the classic "stuck
at 58 detections"). v3.2 turns the crawl **bidirectional**:

```
   LIVE SWEEP ⟶ newest page always first
        │
        ├── fresh items found ──► parse forward · PAUSE any walk
        │                          (the newer timeframe is parsed FIRST)
        ▼
   2 quiet polls in a row (everything a duplicate) = CAUGHT UP
        │
        ▼
   BACKFILL ⟵ walk BACKWARDS through the archive, one chunk per sweep:
        Google News  after:/before: date windows (3-day, queries rotate)
        Bing News    date-sorted offset pages (first=1,11,21…)
        Reddit       fullname cursor (after=t3_…) — strictly older posts
        X cashtag    next_token pages (~7-day endpoint window)
        wires        fixed RSS snapshot — no keyless archive → ARCHIVE FULL
        │
        └── forward finds new data ──► walk PAUSES, resumes at the SAME
                                       cursor once forward is quiet again
```

* **Forward priority is absolute** — backfill never delays a live poll; the
  walker runs on its own 10 s loop (`BACKFILL_INTERVAL_S`), ≤ 2 chunks per
  sweep, fully decoupled from the 1 s tick.
* **Resumable depth** — cursors (window/offset/token) persist across pauses,
  so depth accumulates to `BACKFILL_MAX_DEPTH_DAYS` (30 default) and is never
  re-walked. Window walkers (Google News) don't treat empty windows as
  archive-end (weekends are quiet); offset walkers (Bing/Reddit/X) do.
* **Honest scoring** — backfilled chunks pass the SAME dedupe → ring → spam →
  ALS gate and the SAME Laya inference as live data. Posts older than the 48 h
  ledger TTL decay out of the live WCI (the λ math is honest) but persist in
  SQLite, where the **N-day daily-bias history** is computed
  (`history_bias()`): per-day bull/bear counts → the `NN-D BIAS ±x% · N POSTS`
  HUD chip. That's the "more complete picture" the walk feeds.
* **Heartbeat honesty** — only posts *created* inside the live window advance
  `LAST NEW SIGNAL`; deep archive work shows in the BACKFILL chip, the
  `BACKFILL ARCHIVE` rail block (depth + chunks) and the Ingest Pulse instead.
* **HUD chips** — `BACKFILL ⟵ 12.6D` (walking) · `WALK PAUSED · LIVE FIRST`
  (amber, forward producing) · `ARCHIVE FULL · 30D` · `CAUGHT UP · nD`;
  hidden while the live sweep is producing. Status strip mirrors the state.

Config: `BACKFILL*` in `config.py` §6b (all env-tunable; `BACKFILL=0` disables).
Tests: `scripts/test_backfill.py` (offline, 37 checks) ·
`scripts/test_backfill_live.sh` (boots the worker, waits for catch-up, asserts
the walk engages on real sources).

## Why the numbers move every second

Sources are polled on their own intervals (Reddit ≈ 30 s, Google News ≈ 45 s,
Bing ≈ 60 s, wires ≈ 75 s per asset — all env-tunable), staggered across the
rotation — but **decay, aggregation, momentum and the trend series are
recomputed every tick for every watched asset**, because
`λ = 2^(−age / H_eff)` is evaluated *at read time*. The sphere's flow, the
conviction score, the bull/bear ratio and the sparkline therefore tick every
second even between source polls.

## The Sentiment Sphere

* Dense point-cloud globe + elliptical orbital rings + rotating sweep on a
  true-black void.
* Every scored post **streaks in and docks onto the sphere**:
  `green = bullish`, `red = bearish`, `dim gray = neutral`; node size/glow =
  WCI conviction weight.
* **Spatial ratio:** bullish posts dock on the northern hemisphere band,
  bearish on the southern — the sphere visibly tilts with the market.
* HUD: `LIVE SIGNAL FEED — <ASSET>`, net conviction, bull/bear ratio bar, WCI
  trend, detections, Laya latency, spam-filtered %, uptime, top signals strip.
* The canvas polls `/api/feed` **client-side every 1 s**; the iframe is never
  re-mounted while idle, so the animation stays 60 fps-continuous.
* The sphere document is served by the feed API itself (`GET /surface` on
  :8001) and embedded as a plain `<iframe>` — no deprecated Streamlit
  components, no deprecation warnings, and polls are same-origin.

## The quant model

```
WCI(post, t) = P(direction) · W(relevance) · λ(age)

  P(direction)   Laya's probability for the chosen stance (signed: bull +, bear −)
  W(r) = r^γ     γ = 1.35 — meme noise suppressed super-linearly (floor 0.15)
  λ(age)         2^(−age / H_eff),  H_eff = asset half-life × urgency multiplier
                 (low ×0.75 · moderate ×1.0 · critical ×1.6)

Net conviction   CS = (B − S) / (B + S + ε) ∈ [−1, +1]
Sentiment ratio  B : S over directional posts (neutral excluded, still counted)
```

**ALS (Author Legitimacy Score)** — weighted across the dimensions each
source provides (account age, follower base, author history, engagement
authenticity, domain reputation, 1 − spam-risk), renormalized over available
dims. Below `ALS_THRESHOLD = 0.35` a post is quarantined **before** any
inference is spent. Coordinated rings (≥3 authors pushing near-identical text
inside a 10-minute window) are dropped outright.

## The Laya engine (laya/)

Answers the radar's three typed questions per post:

| id | type | returns |
|---|---|---|
| `directional_bias` | choice | `bullish` \| `bearish` \| `neutral` + confidence |
| `institutional_relevance` | noul | P(institutional insight) in [0,1] — analysis vs meme hype |
| `urgency_score` | score | `low` \| `moderate` \| `critical` |

**Modes:**

| mode | how | speed |
|---|---|---|
| **heuristic** (default) | deterministic finance lexicon + discourse rules: ~250 weighted PSX-flavored bull/bear stems (market-report verbs, circular debt, SCRA, rupee devaluation, IMF slippage, flows, earnings beats/misses…), negation flipping ("not bullish"), rumor dampeners, boosters, meme/spam markers, shouting penalty | < 1 ms/post |
| **finbert** (opt-in) | `docker build -f laya/Dockerfile.finbert` + `LAYA_MODE=finbert`; HuggingFace `ProsusAI/finbert` head for direction, heuristic aux scores | 100–300 ms/post |

Zero dependencies, zero external calls, deterministic — same text in, same
verdict out. Swap in your own fine-tuned ModernBERT checkpoint by editing
`_finbert_pipeline()` in `laya/engine.py`; the HTTP contract stays identical.

## The Deep Field layer (v3) — real market data, real models, fused conviction

### Market data (data/market_client.py)

| Priority | Source | What it gives | Notes |
|---|---|---|---|
| 1 | **PSX Data Portal** `dps.psx.com.pk` | live quotes (`/data/symbol-position`, `/til-portal/get-symbol-data`), EOD OHLCV history (`POST /historical`, HTML table), ticker list | keyless, official. The DPS zone **rotates A records and some nodes 404 every data route** — the client enumerates all IPs, probes, pins a healthy node (TLS/SNI still validated against the real hostname), and re-probes on failure. Machine-wide ≤1 req/s, quotes cached 10 s, EOD history disk-cached |
| 2 | **Yahoo Finance chart API** | quotes + 1y daily for `^KSE` (KSE-100) | free, keyless; per-symbol mapping in `C.YAHOO_SYMBOLS` |
| 3 | **Seeded simulator** | regime-switching GBM anchored to editable base prices | ALWAYS labeled `sim` in the HUD — it exists so the desk never goes dark offline / outside market hours / from datacenter IPs (PSX and Yahoo both throttle cloud ranges). On your residential connection the real portal answers |

The worker runs a dedicated **market loop** (`MARKET_POLL_S`, default 3 s —
never inside the 1 s sentiment tick) refreshing rotation + hot assets, and a
**model loop** refitting one asset per cycle (`MODEL_REFIT_S`, default 5 s,
time-boxed, threaded). First run pulls ~14 months of EOD bars per asset
(4 workers, ~1 req/s — then it's disk-cached forever).

### The model zoo (models/ — pure stdlib, no numpy/torch needed)

| Engine | Port of | What it does here | Output |
|---|---|---|---|
| **SYMREG** | PySR (astroautomata) | genetic symbolic regression over lag/momentum/vol features, complexity-penalized pareto hall-of-fame, time-boxed 0.3 s | an **interpretable expression** (rendered verbatim in the deck) + 1-step return forecast |
| **ARMA(1,1)** | classic statsmodels/lost-stats ARMA | conditional least squares over a (φ, θ) grid | φ/θ/σ + iterated 10-day return path |
| **HAWKES** | tick (X-DataInitiative) | self-exciting point process (exponential kernel) over sentiment bursts + price jumps, profile MLE | λ(t) intensity, **branching ratio** (contagion), expected gap |
| **CONFORMAL** | MAPIE (sklearn-contrib) | split-conformal calibration on ARMA residuals → distribution-free bands | q̂, P10/P90 per horizon, live **coverage %** |
| **ENSEMBLE** | TimesFM (google-research) adapter | if the real `timesfm` + torch stack is installed it decodes the forecast; otherwise the built-in **LayaTS-4** (EWMA · Holt-damped · ARMA · SYMREG) with softmax inverse-MSE weights | P10/P50/P90 fan at 1/3/5/10-day horizons |

Everything refits live in a background thread. The zoo is deliberately
dependency-free so it runs on the same slow-network machines as the rest of
the radar; the honest TimesFM path is opt-in and labeled by backend
(`backend: layats-4` vs `timesfm`) everywhere it appears.

### Volatility & valuation

* **Close-to-close** (20d, annualized), **Parkinson** high-low and
  **Garman-Klass** estimators, plus a trailing **vol percentile** with regime
  labels (`normal` / `extreme` / `comatose`).
* **P/E** — live `price / EPS` from the editable `FUNDAMENTALS` registry
  (config §11); indices use PSX-published trailing P/E. Labeled STATIC in the
  UI — update EPS at each reporting season.

### FCI — Fused Conviction Index (the "better weighted conviction")

```
m_sig   = tanh(expected_5d_return / 5d_vol)          models' signed edge, vol-scaled
align   = 0.5 + 0.5·tanh(4·CS·m_sig)                 chatter ⊗ models agreement
cascade = Hawkes branching > 0.75 → penalty ≤ 0.40    contagion DAMPS conviction
vol_damp= extreme vol percentile → ×0.65              both signals unreliable there

FCI = clamp((0.55·CS + 0.45·m_sig) · align · (1−cascade) · vol_damp)
```

Read-time computed every tick, with the full breakdown (`cs`, `m_sig`,
`align`, `cascade_pen`, `vol_damp`, `branching`, `vol_pctl`) exposed in
`/api/feed → fusion` — which is exactly what the Relational Graph animates.

### The two Deep Field surfaces

* **FORECAST DECK** (`/surface3d`) — trailing price ribbon, ensemble P10→P90
  fan widening with horizon, ARMA/ENS pins per horizon, model cards (ARMA
  φ/θ/σ, symbolic expression, weight bars, Hawkes λ, conformal coverage,
  vol + P/E) and the 1/3/5/10-day fan table.
* **RELATIONAL GRAPH** (`/graph3d`) — every pipeline component as a node
  (SENTIMENT, LAYA, MARKET, ARMA, SYMREG, HAWKES, CONFORMAL, ENSEMBLE, VOL,
  VALUATION, WCI, FCI) with edges that ARE the real data dependencies;
  thickness/pulse = live weight, color = sign, particles flow at speeds
  proportional to the current weights. Watch an impulse propagate from
  chatter or a price jump through the models into FCI.

## Single-page grid + AUTO-BOOT (v3.3)

No more view switching — **all three surfaces render at once** on one page:

```
┌──────────────────────┬──────────────────────┐
│  SENTIMENT SPHERE    │  FORECAST DECK       │   ← side by side (compact rails)
├──────────────────────┴──────────────────────┤
│  RELATIONAL GRAPH (full width)              │   ← beneath
└─────────────────────────────────────────────┘
```

* **TOP SIGNALS · HIGHEST CURRENT WCI** moved onto the sphere's LEFT rail,
  directly beneath the Net Conviction Index — every scored signal appears in
  real time, classified **green = bullish / red = bearish / gray = neutral**, with
  author, signed WCI and headline. This is the desk's live blotter.
* **DAEMON AUTO-BOOT** — if the feed API doesn't answer, the dashboard
  spawns `python run_worker.py` ITSELF (once, marker-file guarded, log at
  `daemon/autoboot.log`). No dead dashboard because a terminal wasn't
  opened. `RADAR_AUTOBOOT=0` disables; `RADAR_AUTOBOOT_DEMO=1` boots the
  synth feed instead.
* **LINK resilience (3 layers)** — (1) auto-boot above, (2) `/api/feed` can
  NEVER 500 again: a failing snapshot degrades to the last-good payload
  (flagged `STALE SNAP` on the sphere), (3) link loss is now a slim
  "RECONNECTING · n RETRIES" banner — the scenes and last data stay
  visible while the poll retries.
* **Forecast-fan bug fixed** — an out-of-scope variable in the deck's fan
  builder threw on every poll since v3: the P10→P90 fan + pins never drew
  and the deck showed a phantom STANDBY. Fan now renders (green/red by sign).
* **Typography** — Rajdhani (display) + JetBrains Mono (data), PURE WHITE
  on black; green/red stay strictly semantic (bull/bear), amber = telemetry.
* **Higher ingestion volume** — `MAX_POSTS_PER_FETCH` 30→60, fetch pool
  24→32, tighter poll intervals, market-wide query boosters on every index,
  and 3 more keyless wires (The News, Geo Business, The Nation) → the sphere
  receives a visibly denser node stream (`nodes` cap 80→120, dock caps 440).
* **Graph overlap fix** — node layout compacted + camera pulled back so
  value sprites can no longer collide with the HUD rails; slimmer value
  sprites; rail scrims.

## The pipeline is the hero (v3.4)

This is a **data ingestion and classification** project — so the pipeline
itself now owns the biggest, most visible block on the page, right beneath
the nav. Modeled on the build-hero pattern ("724 competitor ads, broken
down · ~40 s · ~$0.09"):

```
● DATA INGESTION & CLASSIFICATION · LIVE
12,431 posts, ingested & classified (37/min)
ACROSS 9/9 LIVE WIRES · 1,073 BOTS & SPAM FILTERED · 214 DUPLICATES DROPPED
· 18.4D ARCHIVE WALKED · 2MS / VERDICT · 0 IN QUEUE
CLASSIFIER OUTPUT — KSE-100  [█████████░░░░░░░░░░░░░░░░]
■ BULLISH 61% · 412   ■ NEUTRAL 27% · 183   ■ BEARISH 12% · 81
```

* **One giant true counter** — `posts_scored` ticks up live, client-side,
  every second (`viz/pipeline_hero.py` iframe polling `/api/health` +
  `/api/feed` — no Streamlit rerun needed).
* **Telemetry line** — live wires up / total, bots & spam filtered,
  duplicates dropped, archive depth walked by the bidirectional crawl,
  per-verdict engine latency, inference-queue depth.
* **Classifier output bar** — the active asset's live bull/neutral/bear
  split as a full-width bar (green/white/red) with counts — the classifier's
  actual output as a first-class visual.
* The sphere / deck / graph stay exactly where they are — they're the
  positioning-and-visibility layer beneath the hero.
* Label pass: no surface advertises its rendering technique anymore
  ("FORECAST DECK 3D" → "FORECAST DECK" — you can see it's 3D).

## Configuration

| Var | Default | Purpose |
|---|---|---|
| `LAYA_ENDPOINT` | `http://localhost:8000/v1/systemone` | Engine score endpoint |
| `LAYA_HEALTH_URL` | `http://localhost:8000/health` | Health probe |
| `LAYA_TIMEOUT_S` | `0.45` | Radar→engine HTTP timeout (fail-fast philosophy) |
| `LAYA_MODE` (container env) | `heuristic` | `finbert` for transformer mode |
| `X_BEARER_TOKEN` | *(none → X adapter off)* | Enables the $cashtag source |
| `RADAR_API_PORT` | `8001` | Feed API port |
| `RADAR_PUBLIC_API_BASE` | `http://localhost:8001` | Override if dashboard runs on another host |
| `PSX_RADAR_DB` | `daemon/psx_radar.db` | SQLite persistence (48 h rehydration on restart) |
| `GN_INTERVAL_S` / `BING_INTERVAL_S` / `WIRE_INTERVAL_S` / `REDDIT_INTERVAL_S` | 40 / 50 / 65 / 30 | Per-asset poll interval per source |
| `FETCH_TIMEOUT_S` / `FETCH_CONNECT_TIMEOUT_S` | 5.0 / 2.5 | Per-request scrape timeouts |
| `MAX_CONCURRENT_FETCHES` | 32 | Shared scrape thread-pool width |
| `INFERENCE_CONCURRENCY` | 8 | Parallel Laya calls per drain wave |
| `REDDIT_LIMIT` | 50 | Posts per subreddit request |
| `MARKET_DATA` | `1` | `0` disables the Deep Field market layer |
| `DPS_BASE` | `https://dps.psx.com.pk` | PSX Data Portal base URL |
| `DPS_MIN_INTERVAL_S` | `1.0` | Machine-wide PSX request spacing (be polite) |
| `MARKET_POLL_S` / `QUOTE_TTL_S` | `3.0` / `10.0` | Quote loop cadence / per-symbol quote cache |
| `HISTORY_MONTHS` | `14` | EOD months pulled per asset on first refit |
| `PSX_MARKET_CACHE` | `daemon/psx_market_cache.json` | EOD history disk cache (restart-safe) |
| `MODEL_REFIT_S` | `5.0` | Per-asset model refit cadence (round-robin, threaded) |
| `MODEL_HORIZONS` | `[1,3,5,10]` | Forecast horizons (trading days) |
| `SYMREG_POPULATION` / `_GENERATIONS` / `_TIME_BUDGET_S` | 48 / 24 / 0.30 | Symbolic-regression search budget |
| `CONFORMAL_ALPHA` | `0.20` | 80% P10/P90 bands |
| `FCI_W_SENTIMENT` / `FCI_W_MODELS` | `0.55` / `0.45` | FCI blend weights |
| `FCI_BRANCHING_RISK` / `FCI_CASCADE_PENALTY` | `0.75` / `0.40` | Hawkes contagion guard |
| `FCI_VOL_EXTREME_PCTL` / `FCI_VOL_DAMP` | `0.92` / `0.65` | Vol-regime conviction damping |
| `SIM_STEP_S` / `SIM_VOL_ANN` | `2.0` / `0.17` | Simulator tick / annualized vol |
| `BACKFILL` | `1` | Bidirectional crawl master switch (`0` = forward-only) |
| `BACKFILL_CATCHUP_POLLS` | `2` | Quiet forward polls before a pair walks backwards |
| `BACKFILL_INTERVAL_S` | `10.0` | Backward sweep cadence (decoupled from the 1 s tick) |
| `BACKFILL_CHUNKS_PER_CYCLE` | `2` | Max backward chunks per sweep (politeness cap) |
| `BACKFILL_MAX_DEPTH_DAYS` | `30` | How far back the walk may go (ARCHIVE FULL at cap) |
| `RADAR_AUTOBOOT` | `1` | Dashboard spawns the daemon when the feed API is down (`0` = off) |
| `RADAR_AUTOBOOT_DEMO` | `0` | `1` = auto-boot runs the synth feed instead of live sources |
| `RADAR_UI_PORT` | `8501` | Streamlit dashboard port |
| `RADAR_PUBLIC_API_BASE` | `http://localhost:8001` | Base the dashboard iframes/probes use |

## Troubleshooting

* **"Detections stopped rising / dashboard looks frozen"** — check the
  **TICK** counter in the sphere's top-left subtitle and the new
  **LAST NEW SIGNAL** / **INGEST PULSE** blocks on the right rail:
  * TICK advancing every second = the pipeline is alive.
  * `LAST NEW SIGNAL — Xm ago` growing + amber **QUIET LEDGER · POLLING**
    chip = alive but nothing new survived dedupe. This is normal on
    **weekends and after 15:30 PKT**: PSX is closed, wires publish slowly,
    and every re-poll returns headlines you already ingested, so the hygiene
    gate drops them and detections legitimately freeze. Conviction also
    stays pinned (e.g. −1.000 at 100% bear) because with no new posts the
    decay divides out of the ratio — the ledger is honest, just quiet.
  * It self-heals the moment a fresh headline breaks (Google News also runs
    a 3-day catch-up probe each poll to catch late-indexed weekend items).
  * Since v3.2 the pipeline no longer just idles when caught up — it walks
    BACKWARDS into the archive (`BACKFILL ⟵ nD` chip, `BACKFILL ARCHIVE`
    rail block). Ingest Pulse keeps moving during the walk; LAST NEW SIGNAL
    stays honest (only live-window posts advance it).
  * TICK **not** advancing / RECONNECTING banner = the worker or the link is
    actually down — since v3.3 the dashboard AUTO-BOOTS the daemon itself
    (watch `daemon/autoboot.log`); if it still fails, check
    `localhost:8001/api/health` manually.
* **"LINK TO PIPELINE LOST" / STANDBY on the forecast deck** — historical
  root cause was a JS bug (fixed in v3.3): the deck now surfaces any poll
  exception directly in its right chip (`ERR …`). If you ever see it again,
  that chip names the exact error — report it with that text.
* **Font looks wrong / fallback squares** — the surfaces load Rajdhani +
  JetBrains Mono from Google Fonts; fully offline they fall back to system
  fonts (all data still renders, just less pretty).
* **The deck's fan was missing / STANDBY while numbers updated** — fixed in
  v3.3 (out-of-scope `p50` in the fan builder threw every poll). Restart the
  worker so the fixed HTML is served.
* **`LAYA ENGINE` never lights up** — the radar's circuit breaker self-heals:
  if the engine was down at worker boot, it re-probes on cooldown. Run
  `test_engine.cmd`; if health fails, check `docker logs laya`.
* **Slow pip installs / timeouts** — `setup.cmd` and `START_ALL.cmd` already
  use `--timeout 120 --retries 10`. The Laya image build itself needs **no**
  pip and no PyPI access at all (base image is pulled once by Docker).
* **Port already in use** — stop whatever holds :8000/:8001/:8501, or change
  via the env vars above (container port mapping `-p 8000:8000` must keep
  8000 on the host side unless you also set `LAYA_ENDPOINT`).
* **`docker` / `python` not recognized** — start Docker Desktop; install
  Python with "Add to PATH" checked.
* **Restarting the worker** — up to 48 h of scored posts rehydrate from
  SQLite; the dashboard doesn't blank on restart.
* **Reddit shows `idle`** — Reddit 403s datacenter/cloud IPs. From a normal
  residential connection it works; the adapter degrades to zero posts without
  affecting the rest of the pipeline (same for any wire that blocks you).
* The demo generator is **clearly labeled** (`SYNTH FEED` chip + `demo: true`
  in `/api/health`) — it exists to exercise the pipeline offline, never to
  fake live data.
* **Market chip shows `SIM`** — PSX Data Portal and Yahoo are both
  unreachable from your network (cloud IPs, firewall, or the portal is down).
  On a residential connection in Pakistan the PSX portal answers directly;
  the client probes nodes automatically. Models keep running on simulator
  history in the meantime — the label stays honest.
* **`MARKET` chip stuck / quotes frozen** — outside 09:30–15:30 PKT
  (Mon–Fri) live quotes freeze by design (`CLOSED` chip); the simulator
  keeps the panels alive. Delete `daemon/psx_market_cache.json` to force a
  history re-fetch.
* **First model refit is slow** — the very first refit per asset downloads
  ~14 months of EOD bars through the ≤1 req/s limiter (~15 s on PSX, instant
  from disk cache afterwards). Subsequent refits are 60–300 ms.

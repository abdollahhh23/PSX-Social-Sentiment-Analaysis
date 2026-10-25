"""
The always-on radar worker — a 1-second tick pipeline.

Per tick
--------
1. PARALLEL INGESTION — every due (source × asset) pair fires CONCURRENTLY:
   the rotation asset plus any user-searched "hot" assets are gathered in one
   await, and inside each adapter every query / subreddit / wire feed is also
   a parallel HTTP request. One rotation slot now costs ≈ max(single request),
   not the sum — this is what makes the 1 s cadence actually keep up.
2. HYGIENE — exact dedupe → fuzzy near-dup → coordinated Sybil rings →
   spam lexicon → ALS gate. Dropped accounting feeds spam-filtered %.
3. INFERENCE — drain the queue in PARALLEL WAVES (INFERENCE_CONCURRENCY at a
   time) through Laya (or the labeled fallback), within the per-tick budget.
   Survivors become ScoredPosts in the ledger.
4. DECAY + AGGREGATE — every watched asset's conviction is recomputed
   (λ decays on read), momentum EMA advances, the trend ring is appended.
   ⇒ Numbers genuinely move every second, even between source polls.

Run alongside the FastAPI feed server inside `run_worker.py`.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

import config as C
from daemon.store import STATE
from engine.analytics import ScoredPost
from engine.laya_client import LayaClient, probe_health
from data.market_client import MarketClient, Quote
from scraper.backfill import BackfillController
from scraper.social_feed import FeedHub, RawPost, build_sources


@dataclass
class QueuedPost:
    """RawPost stamped with its asset context for the inference queue."""
    asset: str
    sector: str
    half_life_min: float
    post: RawPost


class RadarWorker:
    def __init__(self, demo: bool = False) -> None:
        self.demo = demo
        self.hub = FeedHub()
        self.laya = LayaClient()
        self.sources = build_sources(demo=demo)
        self.market = MarketClient()          # Deep Field data layer
        self.bfc = BackfillController()       # bidirectional crawl control
        self.pending: asyncio.Queue[QueuedPost] = asyncio.Queue(maxsize=2000)
        self._rotation_idx = 0
        self._model_rr = 0
        self._last_decay_ts = time.time()

        STATE.demo_mode = demo
        self.laya_alive, _ = probe_health()

    # ──────────────────────────────────────────────────────────────────── tick
    async def tick(self) -> None:
        t0 = time.perf_counter()

        # 1 ── parallel ingestion: rotation asset + hot assets, ALL AT ONCE
        rotation = STATE.rotation()
        if rotation:
            asset = rotation[self._rotation_idx % len(rotation)]
            self._rotation_idx += 1
            targets = [asset]
            for hot_asset in [a for a in rotation if STATE.is_hot(a)][:2]:
                if hot_asset != asset:
                    targets.append(hot_asset)
            await asyncio.gather(*(self._ingest(a) for a in targets))

        # 2+3 ── drain inference queue within budget
        await self._drain_inference()

        # 4 ── decay + aggregate every watched asset; advance trend rings
        now = time.time()
        dt = max(now - self._last_decay_ts, 0.001)
        self._last_decay_ts = now
        for symbol in list(STATE.watchlist.keys()):
            led = STATE.ledger_for(symbol)
            led.prune(now)
            agg = led.aggregate(now)
            if agg is not None:
                led.advance_momentum(agg["cs"], dt)
                STATE.trend_for(symbol).append(agg["cs"])

        STATE.tick_count += 1
        STATE.queue_depth = self.pending.qsize()
        STATE.last_tick_ms = (time.perf_counter() - t0) * 1000.0
        if STATE.tick_count % 30 == 0:
            STATE.persist_flush()
        if STATE.tick_count % 60 == 0:
            self.laya_alive, _ = await asyncio.to_thread(probe_health)

    # ─────────────────────────────────────────────────────────────── ingestion
    async def _ingest(self, asset: str) -> None:
        meta = STATE.ensure_asset(asset)
        if meta is None:
            return
        due_srcs = [src for src in self.sources if src.due(asset)]
        if not due_srcs:
            return
        # every live source fetches CONCURRENTLY (each adapter further
        # parallelizes its own queries/sub-feeds on the shared fetch pool)
        results = await asyncio.gather(
            *(asyncio.to_thread(src.fetch, asset, meta) for src in due_srcs),
            return_exceptions=True,
        )
        for src, raw in zip(due_srcs, results):
            if isinstance(raw, BaseException):
                raw = []
            STATE.source_status[src.name] = "ok" if raw else "idle"
            if not raw:
                self.bfc.record_forward(src.name, asset, 0)   # quiet poll
                continue
            self._absorb(raw, asset, meta, src.name)          # + forward signal
            if raw and STATE.is_hot(asset):
                STATE.cool(asset)                               # priority fetch served

    # ─────────────────────────────────────────────────────────── absorb gate
    def _absorb(self, posts: list[RawPost], asset: str, meta: dict,
                source_name: str, backfill: bool = False) -> int:
        """Shared hygiene → inference-queue path for LIVE and BACKFILLED chunks
        alike: exact dedupe → fuzzy near-dup → rings → spam → ALS gate.

        Also feeds the bidirectional controller: `fresh_n` counts everything
        that was NOT an exact/fuzzy duplicate (spam/bot/low-ALS items are new
        too — the timeframe is producing). For backfilled chunks the signal is
        not recorded (the forward cursor must not learn from history)."""
        rep = self.hub.ingest(posts)                        # hygiene gate
        fresh_n = rep.total - rep.dup_exact - rep.dup_fuzzy
        if not backfill:
            self.bfc.record_forward(source_name, asset, fresh_n)
        STATE.posts_ingested += len(rep.kept)
        STATE.dropped_dup += rep.dup_exact + rep.dup_fuzzy
        STATE.dropped_bot += rep.coordinated
        STATE.dropped_spam += rep.spam
        for p in rep.kept:
            try:
                self.pending.put_nowait(QueuedPost(
                    asset=asset,
                    sector=meta.get("sector", "Index"),
                    half_life_min=float(meta.get("half_life_min", 240)),
                    post=p,
                ))
            except asyncio.QueueFull:
                break                                        # budget guard, never block
        return len(rep.kept)

    # ────────────────────────────────────────────────────── backfill (walk)
    async def _backfill_loop(self) -> None:
        """The backward crawl — a SEPARATE loop on its own cadence, never inside
        the 1 s tick. Caught-up pairs walk into the archive (older pages /
        date windows / cursors); any forward freshness pauses them instantly."""
        print(f"[backfill] walk-back loop up · cadence {C.BACKFILL_INTERVAL_S}s · "
              f"depth cap {C.BACKFILL_MAX_DEPTH_DAYS}d · catchup after "
              f"{C.BACKFILL_CATCHUP_POLLS} quiet polls")
        while True:
            try:
                if C.BACKFILL_ENABLED:
                    await self._backfill_step()
            except Exception as e:               # a backfill error must never kill the daemon
                print(f"[backfill] loop error: {e.__class__.__name__}: {e}")
            STATE.backfill_stats = self.bfc.summary()
            await asyncio.sleep(C.BACKFILL_INTERVAL_S)

    async def _backfill_step(self) -> None:
        keys = [(s.name, a) for s in self.sources for a in list(STATE.watchlist.keys())]
        targets = self.bfc.next_targets(keys)
        if not targets:
            return
        results = await asyncio.gather(
            *(self._backfill_one(c.source, c.asset) for c in targets),
            return_exceptions=True)
        for c, r in zip(targets, results):
            if isinstance(r, BaseException):
                print(f"[backfill] {c.source}:{c.asset} chunk error: "
                      f"{r.__class__.__name__}: {r}")

    async def _backfill_one(self, source_name: str, asset: str) -> None:
        src = next((s for s in self.sources if s.name == source_name), None)
        if src is None:
            return
        cur = self.bfc.cursor(source_name, asset)
        if cur.exhausted or cur.paused or not cur.caught_up:
            return                    # forward priority re-checked at fetch time
        meta = STATE.ensure_asset(asset)
        if meta is None:
            return
        posts = await asyncio.to_thread(src.fetch_backfill, asset, meta, cur)
        if posts:
            kept = self._absorb(posts, asset, meta, source_name, backfill=True)
            print(f"[backfill] {source_name}:{asset} chunk #{cur.chunks} "
                  f"+{len(posts)} raw · {kept} kept · depth {cur.depth_days:.1f}d"
                  + (" · ARCHIVE FULL" if cur.exhausted else ""))

    # ────────────────────────────────────────────────────────────── inference
    async def _drain_inference(self) -> None:
        budget = C.INFERENCE_BUDGET_PER_TICK
        conc = max(C.INFERENCE_CONCURRENCY, 1)
        while budget > 0 and not self.pending.empty():
            batch: list[QueuedPost] = []
            for _ in range(min(conc, budget)):
                try:
                    batch.append(self.pending.get_nowait())
                except asyncio.QueueEmpty:
                    break
            if not batch:
                break
            budget -= len(batch)
            # parallel wave — N Laya calls at once; keeps the 1 s tick intact
            # even when the engine RTT is ~85 ms through Docker
            verdicts = await asyncio.gather(
                *(self._score_one(q) for q in batch), return_exceptions=True)
            for q, v in zip(batch, verdicts):
                if isinstance(v, BaseException) or v is None:
                    continue
                STATE.add_scored(v)
                # material conviction → Hawkes burst event (feed the
                # self-exciting point process behind the cascade guard)
                if v.direction != "neutral" and v.wci_abs(time.time()) > 0.18:
                    STATE.zoo.get(v.asset).record_burst(time.time())

    async def _score_one(self, q: QueuedPost):
        p = q.post
        age_s = max(time.time() - p.created_ts, 0.0)
        als = self.hub.author_legitimacy_score(p, 0.0)
        verdict = await asyncio.to_thread(
            self.laya.score,
            p.text, p.url, q.asset, q.sector,
            als, age_s, p.source,
        )
        return ScoredPost(
            id=p.id,
            asset=q.asset,
            source=p.source,
            author=p.author,
            text=p.text,
            url=p.url,
            created_ts=p.created_ts,
            scored_ts=time.time(),
            als=als,
            direction=verdict.direction,
            dir_confidence=verdict.dir_confidence,
            relevance=verdict.relevance,
            urgency=verdict.urgency,
            half_life_min=q.half_life_min,
            engine=verdict.source,
        )

    # ──────────────────────────────────────────────────── Deep Field loops
    def _apply_quote(self, asset: str, q: Quote) -> None:
        """Push a fresh quote into the shared state + the asset's model book."""
        am = STATE.zoo.get(asset)
        am.update_quote(q)
        STATE.market_meta[asset] = {
            "price": round(q.price, 4),
            "prev_close": round(q.prev_close, 4) if q.prev_close else None,
            "open": round(q.open, 4) if q.open else None,
            "high": round(q.high, 4) if q.high else None,
            "low": round(q.low, 4) if q.low else None,
            "volume": q.volume,
            "change": round(q.change, 4) if q.change is not None else None,
            "change_pct": round(q.change_pct, 3) if q.change_pct is not None else None,
            "source": q.source,
            "closed": q.closed,
            "ts": q.ts,
        }

    async def _market_loop(self) -> None:
        """Deep Field quotes: rotation + hot assets, refreshed on their own
        cadence (MARKET_POLL_S), never inside the 1 s sentiment tick."""
        print(f"[deep-field] market loop up · poll {C.MARKET_POLL_S}s "
              f"· dps@{C.DPS_BASE}")
        while True:
            try:
                assets = list(STATE.watchlist.keys())
                hot = [a for a in assets if STATE.is_hot(a)]
                ordered = hot + [a for a in assets if a not in hot]
                for asset in ordered[:8]:          # bounded per cycle
                    q = await asyncio.to_thread(self.market.refresh, asset)
                    self._apply_quote(asset, q)
                h = self.market.health()
                src = "psx" if h.get("dps_ok", 0) > h.get("sim_ticks", 0) else \
                    ("yahoo" if h.get("yahoo_ok", 0) > 0 else "sim")
                STATE.market_status = {**h, "source": src}
            except Exception as e:                 # market data must never kill the daemon
                print(f"[deep-field] market loop error: {e.__class__.__name__}: {e}")
            await asyncio.sleep(C.MARKET_POLL_S)

    async def _model_loop(self) -> None:
        """Round-robin model refits — ONE asset per cycle, in a thread,
        time-boxed inside. The 1 s tick never waits on a model fit."""
        print(f"[deep-field] model loop up · refit every {C.MODEL_REFIT_S}s "
              f"· horizons {C.MODEL_HORIZONS}")
        while True:
            try:
                assets = list(STATE.watchlist.keys())
                if assets:
                    asset = assets[self._model_rr % len(assets)]
                    self._model_rr += 1
                    am = STATE.zoo.get(asset)
                    await asyncio.to_thread(am.refit, self.market)
            except Exception as e:                 # a model fit must never kill the daemon
                print(f"[deep-field] model loop error: {e.__class__.__name__}: {e}")
            await asyncio.sleep(C.MODEL_REFIT_S)

    # ───────────────────────────────────────────────────────────────  loop
    async def _tick_loop(self) -> None:
        next_tick = time.monotonic()
        while True:
            try:
                await self.tick()
            except Exception as e:                    # a tick must never kill the daemon
                print(f"[radar-worker] tick error: {e.__class__.__name__}: {e}")
            next_tick += C.TICK_INTERVAL_S
            delay = next_tick - time.monotonic()
            if delay < -1.0:                          # fell badly behind → resync
                next_tick = time.monotonic()
                delay = 0.0
            await asyncio.sleep(max(delay, 0.0))

    async def run(self) -> None:
        print(f"[radar-worker] boot demo={self.demo} "
              f"sources={[s.name for s in self.sources]} laya_alive={self.laya_alive}")
        await asyncio.gather(self._tick_loop(), self._market_loop(),
                             self._model_loop(), self._backfill_loop())

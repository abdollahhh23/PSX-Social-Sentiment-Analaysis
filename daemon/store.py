"""
Radar state store — the shared brain between the 1-second pipeline daemon and
the feed API. Single instance per process (module-level `STATE`).

Contents
--------
• per-asset ConvictionLedgers (scored posts, on-read decay aggregation)
• watchlist — the 5 header indices by default; searched tickers hot-add
• per-asset conviction trend rings (fed to the sphere sparkline)
• hygiene telemetry counters (spam / bot / dup accounting)
• source health registry
• optional SQLite persistence (restart resilience across 48h window)
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from collections import defaultdict, deque

import config as C
from engine.analytics import ConvictionLedger, ScoredPost, fuse_conviction
from models.zoo import Zoo


class PipelineState:
    """Thread-safe process-wide radar state."""

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.boot_ts = time.time()
        self.demo_mode = False

        # watchlist: asset symbol → meta dict (ordered; header indices first)
        self.watchlist: dict[str, dict] = {
            sym: dict(meta) for sym, meta in C.INDEX_UNIVERSE.items()
        }
        self._hot: set[str] = set()          # user-searched → fetched on next tick

        self.ledgers: dict[str, ConvictionLedger] = {}
        self.trends: dict[str, deque] = defaultdict(lambda: deque(maxlen=C.TREND_WINDOW_TICKS))

        # Deep Field: model zoo + live market metadata (per symbol)
        self.zoo = Zoo()
        self.market_meta: dict[str, dict] = {}   # symbol → quote snapshot
        self.market_status: dict = {"node": "probing…", "source": "init",
                                    "dps_ok": 0, "dps_fail": 0, "yahoo_ok": 0,
                                    "sim_ticks": 0}

        # telemetry
        self.tick_count = 0
        self.posts_ingested = 0
        self.posts_scored = 0
        self.dropped_spam = 0
        self.dropped_bot = 0
        self.dropped_dup = 0
        self.queue_depth = 0
        self.source_status: dict[str, str] = {"google_news": "init", "reddit": "init", "x": "init"}
        self.last_tick_ms: float = 0.0

        # bidirectional crawl — the worker's BackfillController publishes its
        # summary here every backfill sweep (worker writes, API reads)
        self.backfill_stats: dict = {"enabled": C.BACKFILL_ENABLED, "mode": "LIVE",
                                     "depth_days": 0.0, "chunks": 0, "posts": 0,
                                     "walking": 0, "paused": 0, "exhausted": 0,
                                     "assets": {}}
        self._hist_cache: dict[tuple, tuple[float, dict]] = {}   # (asset, days) → (ts, bias)

        # liveness heartbeat — lets the HUD prove the pipeline is ALIVE even
        # during quiet hours (weekends / closed market), when detections
        # legitimately freeze because dedupe rejects already-seen headlines.
        self.last_new_post_ts: float | None = None   # last ledger insertion
        self._scored_ring: deque[float] = deque(maxlen=2000)  # scored timestamps (10 min window)

        # sqlite
        self._db: sqlite3.Connection | None = None
        if C.SQLITE_ENABLED:
            self._init_sqlite()

    # ─────────────────────────────────────────────────────────────── watchlist
    def ensure_asset(self, symbol: str) -> dict | None:
        """Return meta for a known asset; register it (hot) if searchable."""
        with self.lock:
            if symbol in self.watchlist:
                return self.watchlist[symbol]
            if symbol in C.COMPANY_REGISTRY:
                meta = dict(C.COMPANY_REGISTRY[symbol])
                meta["kind"] = "company"
                self.watchlist[symbol] = meta
                self._hot.add(symbol)
                return meta
            if symbol in C.INDEX_UNIVERSE:
                meta = dict(C.INDEX_UNIVERSE[symbol])
                self.watchlist[symbol] = meta
                self._hot.add(symbol)
                return meta
        return None

    def is_hot(self, symbol: str) -> bool:
        with self.lock:
            return symbol in self._hot

    def cool(self, symbol: str) -> None:
        with self.lock:
            self._hot.discard(symbol)

    def rotation(self) -> list[str]:
        """Assets to rotate through — hot (user-searched) assets go first."""
        with self.lock:
            hot = [a for a in self.watchlist if a in self._hot]
            rest = [a for a in self.watchlist if a not in self._hot]
            return hot + rest

    # ───────────────────────────────────────────────────────────────── ledger
    def ledger_for(self, symbol: str) -> ConvictionLedger:
        with self.lock:
            if symbol not in self.ledgers:
                self.ledgers[symbol] = ConvictionLedger(symbol)
            return self.ledgers[symbol]

    def add_scored(self, post: ScoredPost) -> None:
        with self.lock:
            self.ledger_for(post.asset).add(post)
            self.posts_scored += 1
            now = time.time()
            # freshness-gated heartbeat: only posts CREATED inside the live
            # window advance "Last New Signal" — deep backfill scoring shows
            # in the BACKFILL chip + Ingest Pulse instead, so a busy archive
            # walk can never disguise a quiet live timeframe.
            if post.created_ts >= now - C.FRESH_HEARTBEAT_WINDOW_S:
                self.last_new_post_ts = now
            self._scored_ring.append(now)
            self._persist_post(post)

    def ingest_per_min(self) -> int:
        """Posts scored in the trailing 60 s — the pipeline's pulse."""
        cutoff = time.time() - 60.0
        ring = self._scored_ring
        n = 0
        for ts in reversed(ring):
            if ts < cutoff:
                break
            n += 1
        return n

    # ───────────────────────────────────────────────────────── history bias
    def history_bias(self, symbol: str | None = None,
                     days: int | None = None) -> dict:
        """Daily directional bias across the BACKFILLED archive — the 'fuller
        picture' the backward walk feeds. Computed from the persisted
        scored_posts book (SQLite), cached 60 s because the HUD polls 1×/s.

        Returns {days_covered, posts, bull, bear, neutral, bull_pct,
                 first_ts, daily:[{date,bull,bear,n} …oldest→newest]}.
        Zeros when SQLite is off — history is never faked."""
        days = days or C.BACKFILL_MAX_DEPTH_DAYS
        key = (symbol, days)
        now = time.time()
        hit = self._hist_cache.get(key)
        if hit and now - hit[0] < 60.0:
            return hit[1]

        out = {"days": 0, "posts": 0, "bull": 0, "bear": 0, "neutral": 0,
               "bull_pct": 50.0, "first_ts": None, "daily": []}
        if self._db is None:
            self._hist_cache[key] = (now, out)
            return out
        try:
            cutoff = now - days * 86400.0
            if symbol:
                cur = self._db.execute(
                    "SELECT CAST(created_ts/86400 AS INTEGER) d, direction, COUNT(*) "
                    "FROM scored_posts WHERE created_ts > ? AND asset = ? "
                    "GROUP BY d, direction", (cutoff, symbol))
            else:
                cur = self._db.execute(
                    "SELECT CAST(created_ts/86400 AS INTEGER) d, direction, COUNT(*) "
                    "FROM scored_posts WHERE created_ts > ? "
                    "GROUP BY d, direction", (cutoff,))
            rows = cur.fetchall()
        except Exception:
            self._hist_cache[key] = (now, out)
            return out

        per_day: dict[int, dict] = {}
        for d, direction, n in rows:
            slot = per_day.setdefault(int(d), {"bull": 0, "bear": 0, "neutral": 0})
            if direction == "bullish":
                slot["bull"] += n; out["bull"] += n; out["posts"] += n
            elif direction == "bearish":
                slot["bear"] += n; out["bear"] += n; out["posts"] += n
            else:
                slot["neutral"] += n; out["neutral"] += n; out["posts"] += n
        for d in sorted(per_day):
            slot = per_day[d]
            out["daily"].append({
                "date": time.strftime("%Y-%m-%d", time.gmtime(d * 86400.0)),
                **slot, "n": slot["bull"] + slot["bear"] + slot["neutral"],
            })
        out["days"] = len(per_day)
        denom = out["bull"] + out["bear"]
        out["bull_pct"] = round(100.0 * out["bull"] / denom, 1) if denom else 50.0
        if per_day:
            out["first_ts"] = min(per_day) * 86400.0
        out["daily"] = out["daily"][-days:]
        self._hist_cache[key] = (now, out)
        return out

    def trend_for(self, symbol: str) -> deque:
        with self.lock:
            return self.trends[symbol]

    # ─────────────────────────────────────────────────────────────── snapshot
    def snapshot(self, symbol: str, laya_health: dict) -> dict:
        """The /api/feed payload for one asset — everything the surface needs."""
        now = time.time()
        with self.lock:
            led = self.ledgers.get(symbol)
            agg = led.aggregate(now) if led else None
            nodes = led.nodes(now, limit=120) if led else []   # v3.3: high-volume sphere
            top = led.top_signals(now, limit=8) if led else []
            trend = list(self.trends.get(symbol, ()))

            total_dropped = self.dropped_spam + self.dropped_bot + self.dropped_dup
            spam_pct = round(100.0 * (self.dropped_spam + self.dropped_bot) / total_dropped, 1) \
                if total_dropped else 0.0

            # ── Deep Field: market + models + fused conviction ──────────────
            market = dict(self.market_meta.get(symbol, {}))
            zoo_snap = self.zoo.get(symbol).snapshot() if symbol in \
                self.zoo.all() else None
            fusion = fuse_conviction(agg["cs"] if agg else 0.0, zoo_snap, market)
            trend = list(self.trends.get(symbol, ()))

            return {
                "ok": True,
                "asset": symbol,
                "ts": now,
                "cs": agg["cs"] if agg else 0.0,
                "bull_pct": agg["bull_pct"] if agg else 50.0,
                "bear_pct": agg["bear_pct"] if agg else 50.0,
                "bull_n": agg["bull_n"] if agg else 0,
                "bear_n": agg["bear_n"] if agg else 0,
                "neutral_n": agg["neutral_n"] if agg else 0,
                "detections": agg["detections"] if agg else 0,
                "momentum": agg["momentum"] if agg else 0.0,
                "spam_pct": spam_pct,
                "engine": "laya" if laya_health.get("live_calls", 0) > 0 and
                          laya_health.get("breaker") == "closed" else
                          ("laya" if laya_health.get("breaker") == "closed" and
                           laya_health.get("fallback_calls", 0) == 0 else "fallback"),
                "laya_latency_ms": laya_health.get("latency_ema_ms", 0.0),
                "uptime_s": round(now - self.boot_ts, 1),
                "tick": self.tick_count,
                "tick_ms": round(self.last_tick_ms, 1),
                "posts_ingested": self.posts_ingested,
                "posts_scored": self.posts_scored,
                "last_new_age_s": (round(now - self.last_new_post_ts, 1)
                                   if self.last_new_post_ts is not None else None),
                "ingest_per_min": self.ingest_per_min(),
                "demo": self.demo_mode,
                "trend": trend[-C.TREND_WINDOW_TICKS:],
                "nodes": nodes,
                "top": top,
                "sources": dict(self.source_status),
                "queue_depth": self.queue_depth,
                "watchlist": list(self.watchlist.keys()),
                # ── bidirectional crawl blocks ─────────────────────────────
                "backfill": {**self.backfill_stats.get("assets", {}).get(symbol,
                             {"mode": "LIVE", "depth_days": 0.0, "chunks": 0,
                              "posts": 0, "walking": 0, "paused": 0,
                              "exhausted": 0}),
                             "enabled": self.backfill_stats.get("enabled", False)},
                "history": self.history_bias(symbol),
                # ── Deep Field blocks ────────────────────────────────────────
                "market": market,
                "models": zoo_snap,
                "fci": fusion["fci"],
                "fusion": fusion,
                "market_status": dict(self.market_status),
            }

    # ───────────────────────────────────────────────────────────────── sqlite
    def _init_sqlite(self) -> None:
        try:
            path = C.SQLITE_PATH
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            self._db = sqlite3.connect(path, check_same_thread=False)
            self._db.execute("""
                CREATE TABLE IF NOT EXISTS scored_posts (
                    id TEXT PRIMARY KEY, asset TEXT, source TEXT, author TEXT,
                    text TEXT, url TEXT, created_ts REAL, scored_ts REAL,
                    als REAL, direction TEXT, dir_confidence REAL,
                    relevance REAL, urgency TEXT, half_life_min REAL, engine TEXT
                )
            """)
            self._db.commit()
            self._load_recent()
        except Exception:
            self._db = None          # persistence is best-effort, never fatal

    def _persist_post(self, p: ScoredPost) -> None:
        if self._db is None:
            return
        try:
            self._db.execute(
                "INSERT OR REPLACE INTO scored_posts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (p.id, p.asset, p.source, p.author, p.text[:400], p.url,
                 p.created_ts, p.scored_ts, p.als, p.direction, p.dir_confidence,
                 p.relevance, p.urgency, p.half_life_min, p.engine))
            if self.posts_scored % 25 == 0:
                self._db.commit()
        except Exception:
            pass

    def _load_recent(self) -> None:
        """Rehydrate the last 48h of scored posts so a daemon restart doesn't
        blank the dashboard."""
        if self._db is None:
            return
        cutoff = time.time() - C.POST_TTL_HOURS * 3600
        try:
            cur = self._db.execute(
                "SELECT id,asset,source,author,text,url,created_ts,scored_ts,als,"
                "direction,dir_confidence,relevance,urgency,half_life_min,engine "
                "FROM scored_posts WHERE created_ts > ? ORDER BY created_ts ASC", (cutoff,))
            rows = cur.fetchall()
        except Exception:
            return
        for row in rows:
            p = ScoredPost(
                id=row[0], asset=row[1], source=row[2], author=row[3], text=row[4],
                url=row[5], created_ts=row[6], scored_ts=row[7], als=row[8],
                direction=row[9], dir_confidence=row[10], relevance=row[11],
                urgency=row[12], half_life_min=row[13], engine=row[14])
            meta = self.watchlist.get(p.asset)
            if meta is None and p.asset in C.COMPANY_REGISTRY:
                meta = dict(C.COMPANY_REGISTRY[p.asset]); meta["kind"] = "company"
                self.watchlist[p.asset] = meta
            self.ledger_for(p.asset).add(p)
            self.posts_scored += 1

    def persist_flush(self) -> None:
        if self._db is not None:
            try:
                self._db.commit()
            except Exception:
                pass


# Module-level singleton — worker and API share this exact instance.
STATE = PipelineState()

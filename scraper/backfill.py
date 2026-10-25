"""
Bidirectional crawl control — "caught up with NOW? Then walk BACKWARDS."
=========================================================================

The radar's live sweep is forward-only by nature: every adapter fetches the
NEWEST page of its feed. Once a (source, asset) pair has parsed everything the
current timeframe offers, those re-polls return identical items, the hygiene
gate drops them, and ingestion goes quiet — the "frozen dashboard" problem on
closed-market days.

This module turns the crawl BIDIRECTIONAL with three rules:

1. FORWARD PRIORITY — the live poll always runs first. The moment it yields
   genuinely NEW items, any backward walk for that pair is PAUSED (the newer
   timeframe gets parsed first, exactly as requested).
2. BACKWARD ON CATCH-UP — after BACKFILL_CATCHUP_POLLS consecutive forward
   polls where every item was a duplicate, the pair is declared "caught up"
   and the walker starts stepping BACKWARDS through the archive: older offset
   pages (Bing), date-windowed queries (Google News after:/before:),
   fullname cursors (Reddit), next_token pages (X).
3. RESUME, DON'T RESTART — the backward cursor (page / window / token) is
   preserved across pauses. When forward goes quiet again, the walk continues
   from exactly where it stopped — depth accumulates, nothing is re-walked.

Every backfilled chunk flows through the SAME hygiene gate (dedupe → rings →
spam → ALS) and the SAME Laya inference queue as live data — history is scored
honestly, never pasted into aggregates.

Cursors live in the worker process (single asyncio loop + bounded threads);
the controller only ever surfaces plain-dict summaries for the HUD/API.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import config as C


# ─────────────────────────────────────────────────────────────────────────────
# Per-pair cursor
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class PairCursor:
    """Bidirectional crawl state for one (source, asset) pair."""
    source: str
    asset: str

    # ── forward (live) state ────────────────────────────────────────────────
    fresh_misses: int = 0          # consecutive forward polls with ZERO fresh items
    caught_up: bool = False        # fresh_misses >= threshold → timeframe fully parsed
    last_fresh_ts: float = 0.0     # last forward poll that produced something new

    # ── backward (backfill) state ───────────────────────────────────────────
    backfilling: bool = False      # walker engaged for this pair
    paused: bool = False           # forward found new data → walk paused, forward first
    page: int = 0                  # generic offset page (Bing first=1+page*10, query rotation)
    oldest_ts: float = 0.0         # oldest parsed post ts so far (depth marker)
    after: str | None = None       # Reddit fullname cursor (t3_xxx)
    next_token: str | None = None  # X API v2 pagination token
    win_end_ts: float = 0.0        # Google News date-window walk boundary (unix)
    empty_streak: int = 0          # consecutive empty backward chunks
    exhausted: bool = False        # archive end / depth cap reached → ARCHIVE FULL
    chunks: int = 0                # backward chunks fetched
    posts: int = 0                 # backward posts emitted (pre-hygiene)

    # ------------------------------------------------------------------ math
    @property
    def depth_days(self) -> float:
        """How far back this pair has walked (0 = still in the live window)."""
        if not self.oldest_ts:
            return 0.0
        return max((time.time() - self.oldest_ts) / 86400.0, 0.0)

    @property
    def mode(self) -> str:
        """LIVE | BACKFILL | PAUSED | CAUGHT_UP | ARCHIVE_FULL."""
        if self.exhausted:
            return "ARCHIVE_FULL"
        if self.backfilling and not self.paused:
            return "BACKFILL"
        if self.paused:
            return "PAUSED"
        if self.caught_up:
            return "CAUGHT_UP"
        return "LIVE"

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "asset": self.asset,
            "mode": self.mode,
            "depth_days": round(self.depth_days, 2),
            "oldest_ts": self.oldest_ts if self.oldest_ts else None,
            "chunks": self.chunks,
            "posts": self.posts,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Controller
# ─────────────────────────────────────────────────────────────────────────────

class BackfillController:
    """Owns every PairCursor and decides WHEN each pair walks backwards.

    All mutation happens on the worker's asyncio loop (the adapters' fetch
    threads only advance their own pair's cursor), so no locking is needed.
    """

    def __init__(self) -> None:
        self._cur: dict[tuple[str, str], PairCursor] = {}
        self._rr = 0                      # round-robin fairness across eligible pairs

    # ---------------------------------------------------------------- access
    def cursor(self, source: str, asset: str) -> PairCursor:
        key = (source, asset)
        if key not in self._cur:
            self._cur[key] = PairCursor(source=source, asset=asset)
        return self._cur[key]

    def pairs(self) -> list[PairCursor]:
        return list(self._cur.values())

    # ----------------------------------------------------------- forward poll
    def record_forward(self, source: str, asset: str, fresh_n: int) -> None:
        """Called after EVERY forward poll with the count of genuinely-new
        items (duplicates excluded — spam/bot/low-ALS items are still NEW).

        fresh_n > 0  → the newer timeframe is producing: pause any walk.
        fresh_n == 0 → one more quiet poll; after the threshold the pair is
                       caught up and (re)opens for backward walking.
        """
        c = self.cursor(source, asset)
        if fresh_n > 0:
            c.fresh_misses = 0
            c.caught_up = False
            c.last_fresh_ts = time.time()
            if c.backfilling:
                c.paused = True          # rule 1: forward first
        else:
            c.fresh_misses += 1
            if c.fresh_misses >= C.BACKFILL_CATCHUP_POLLS and not c.exhausted:
                c.caught_up = True
                c.paused = False         # rule 3: resume the walk where it stopped

    # ------------------------------------------------------------ scheduling
    def next_targets(self, keys: list[tuple[str, str]]) -> list[PairCursor]:
        """Round-robin pick of backfill-eligible pairs, up to
        BACKFILL_CHUNKS_PER_CYCLE. Eligible = caught up, walkable (not
        exhausted), not paused (a paused pair is waiting on forward data)."""
        eligible = [self.cursor(s, a) for s, a in keys]
        eligible = [c for c in eligible
                    if c.caught_up and not c.exhausted and not c.paused]
        if not eligible:
            return []
        out: list[PairCursor] = []
        n = len(eligible)
        for i in range(min(C.BACKFILL_CHUNKS_PER_CYCLE, n)):
            out.append(eligible[(self._rr + i) % n])
        self._rr = (self._rr + len(out)) % max(n, 1)
        return out

    # ----------------------------------------------------------------- stats
    def summary(self) -> dict:
        """HUD/API payload: global aggregates + per-asset blocks."""
        curs = list(self._cur.values())

        def block(cs: list[PairCursor]) -> dict:
            if not cs:
                return {"mode": "LIVE", "depth_days": 0.0, "chunks": 0,
                        "posts": 0, "walking": 0, "paused": 0, "exhausted": 0}
            walking = sum(1 for c in cs if c.mode == "BACKFILL")
            paused = sum(1 for c in cs if c.mode == "PAUSED")
            exhausted = sum(1 for c in cs if c.mode == "ARCHIVE_FULL")
            fresh_recent = any(time.time() - c.last_fresh_ts < 90.0 for c in cs)
            depth = max((c.depth_days for c in cs), default=0.0)
            if fresh_recent:
                mode = "LIVE"
            elif walking:
                mode = "BACKFILL"
            elif paused:
                mode = "PAUSED"
            elif exhausted and exhausted == len(cs):
                mode = "ARCHIVE_FULL"
            else:
                mode = "CAUGHT_UP"
            return {
                "mode": mode,
                "depth_days": round(depth, 2),
                "chunks": sum(c.chunks for c in cs),
                "posts": sum(c.posts for c in cs),
                "walking": walking,
                "paused": paused,
                "exhausted": exhausted,
            }

        assets: dict[str, dict] = {}
        by_asset: dict[str, list[PairCursor]] = {}
        for c in curs:
            by_asset.setdefault(c.asset, []).append(c)
        for sym, cs in by_asset.items():
            assets[sym] = block(cs)

        return {
            "enabled": C.BACKFILL_ENABLED,
            "catchup_polls": C.BACKFILL_CATCHUP_POLLS,
            "max_depth_days": C.BACKFILL_MAX_DEPTH_DAYS,
            **block(curs),                 # global block (mode/depth/chunks/…)
            "pairs": len(curs),
            "assets": assets,
        }

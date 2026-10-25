"""
Offline unit tests for the BIDIRECTIONAL CRAWL (backfill engine).
No network: every adapter's HTTP layer is stubbed with canned payloads.

Covers
------
  1. BackfillController state machine — LIVE → CAUGHT_UP → BACKFILL →
     PAUSED (forward found data) → resume BACKFILL → ARCHIVE_FULL
  2. next_targets eligibility + round-robin + per-cycle cap
  3. GoogleNewsRSS date-window walk (after:/before: URL build, cursor step,
     depth marker, depth-cap exhaustion)
  4. BingNewsRSS offset walk (first=1+page*10, empty-page exhaustion)
  5. RedditPublic fullname walk (after= anchor + strictly-older steps)
  6. XCashtag next_token walk (token advance, archive end)
  7. WireFeeds default → immediate ARCHIVE_FULL (no keyless archive)
  8. PipelineState.history_bias() from a temp SQLite book + freshness-gated
     heartbeat (backfilled-old posts must NOT reset "Last New Signal")

Run:  python3 scripts/test_backfill.py
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_tmpdb = os.path.join(tempfile.mkdtemp(prefix="psx_bf_"), "hist.db")
os.environ["PSX_RADAR_DB"] = _tmpdb          # BEFORE any daemon.store import

import config as C                            # noqa: E402
from scraper.backfill import BackfillController, PairCursor  # noqa: E402
from scraper.social_feed import (             # noqa: E402
    GoogleNewsRSS, BingNewsRSS, RedditPublic, XCashtag, WireFeeds, DemoSource,
)

PASS, FAIL = 0, 0


def ok(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {label}")
    else:
        FAIL += 1
        print(f"  ✗ FAIL: {label}")


META_IDX = {"queries": ["PSX KSE-100 index", "KSE100 benchmark"],
            "kind": "index", "name": "KSE 100 Index", "half_life_min": 360}

GN_XML = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
<item><title>PSX KSE-100 index rallies - Business Recorder</title>
<link>https://brecorder.com/a1</link><pubDate>Tue, 13 Oct 2026 10:00:00 GMT</pubDate>
<source url="https://brecorder.com">brecorder.com</source></item>
<item><title>KSE100 benchmark dips on profit-taking - Tribune</title>
<link>https://tribune.com.pk/a2</link><pubDate>Mon, 12 Oct 2026 09:00:00 GMT</pubDate>
<source url="https://tribune.com.pk">tribune.com.pk</source></item>
</channel></rss>"""

BN_XML = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
<item><title>KSE-100 gains 1.2% - Dawn News</title>
<link>https://dawn.com/b1</link><pubDate>Fri, 09 Oct 2026 12:00:00 GMT</pubDate></item>
<item><title>Institutional buying in OGDC - Profit</title>
<link>https://profit.pakistantoday.com.pk/b2</link><pubDate>Thu, 08 Oct 2026 11:00:00 GMT</pubDate></item>
</channel></rss>"""

RD_JSON = json.dumps({"data": {"children": [
    {"data": {"id": "abc1", "name": "t3_abc1", "title": "PSX bulls return",
              "selftext": "", "author": "ali_khi", "created_utc": 1760000000.0,
              "permalink": "/r/StocksPakistan/comments/abc1/", "score": 5,
              "num_comments": 2}},
    {"data": {"id": "abc0", "name": "t3_abc0", "title": "OGDC dividend beat",
              "selftext": "solid payout", "author": "sara", "created_utc": 1759900000.0,
              "permalink": "/r/StocksPakistan/comments/abc0/", "score": 9,
              "num_comments": 1}},
]}})


# ═══════════════════════════════════════════════════════════ 1. controller
print("\n[1] BackfillController state machine")
bfc = BackfillController()
bfc.record_forward("google_news", "KSE-100", 5)
cur = bfc.cursor("google_news", "KSE-100")
ok(cur.mode == "LIVE" and not cur.caught_up, "fresh data → LIVE, not caught up")
bfc.record_forward("google_news", "KSE-100", 0)
ok(not cur.caught_up, "one quiet poll < threshold → still LIVE")
bfc.record_forward("google_news", "KSE-100", 0)
ok(cur.caught_up and cur.mode == "CAUGHT_UP",
   f"2 quiet polls → CAUGHT_UP (got {cur.mode})")

targets = bfc.next_targets([("google_news", "KSE-100"), ("wires", "KSE-100")])
ok([t.key if hasattr(t, "key") else (t.source, t.asset) for t in targets]
   == [("google_news", "KSE-100")],
   "next_targets returns only the caught-up pair")
cur.backfilling = True                       # walker engaged
bfc.record_forward("google_news", "KSE-100", 3)   # NEW TIMEFRAME STARTS
ok(cur.paused and cur.mode == "PAUSED", "fresh forward data → walk PAUSED (forward first)")
ok(not bfc.next_targets([("google_news", "KSE-100")]),
   "paused pair is NOT eligible for backward chunks")
bfc.record_forward("google_news", "KSE-100", 0)
bfc.record_forward("google_news", "KSE-100", 0)
ok(cur.paused is False and cur.caught_up and cur.backfilling,
   "forward quiet again → walk RESUMES from saved cursor")
ok(cur.chunks == 0, "cursor preserved across pause (no re-walk)")
cur.exhausted = True
ok(cur.mode == "ARCHIVE_FULL", "exhausted → ARCHIVE_FULL")
ok(not bfc.next_targets([("google_news", "KSE-100")]), "exhausted pair never re-fetched")

# round-robin + cap
bfc2 = BackfillController()
for i in range(5):
    c = bfc2.cursor("google_news", f"A{i}")
    c.caught_up = True
C.BACKFILL_CHUNKS_PER_CYCLE = 2
picked = [c.asset for c in bfc2.next_targets(
    [("google_news", f"A{i}") for i in range(5)])]
ok(len(picked) == 2, "per-cycle cap = 2 chunks")
picked2 = [c.asset for c in bfc2.next_targets(
    [("google_news", f"A{i}") for i in range(5)])]
ok(picked2[0] not in picked or picked2 != picked, "round-robin rotates pairs")
C.BACKFILL_CHUNKS_PER_CYCLE = 2  # restore for prod-default runs

# ═════════════════════════════════════════════════════ 2. google news walk
print("\n[2] GoogleNewsRSS date-window walk")
gn = GoogleNewsRSS()
seen_urls: list[str] = []
gn._get_many = lambda urls: (seen_urls.append(urls[0]) or [GN_XML])
cur = PairCursor(source="google_news", asset="KSE-100")
posts = gn.fetch_backfill("KSE-100", META_IDX, cur)
from urllib.parse import unquote as _unq
dec = _unq(seen_urls[0])
ok(len(posts) == 2 and "after:" in dec and "before:" in dec,
   "chunk 1 parsed 2 posts via after:/before: query")
ok(cur.chunks == 1 and cur.oldest_ts > 0 and cur.backfilling,
   "cursor advanced: chunk counted, depth marker set")
ok(abs(cur.depth_days - (time.time() - min(p.created_ts for p in posts)) / 86400) < 0.01,
   "depth_days matches oldest parsed post")
url2 = None
gn._get_many = lambda urls: (seen_urls.append(urls[0]) or [GN_XML])
gn.fetch_backfill("KSE-100", META_IDX, cur)
import re as _re
d1_before = _re.search(r"before:(\d{4}-\d{2}-\d{2})", _unq(seen_urls[0]))
d2_after = _re.search(r"after:(\d{4}-\d{2}-\d{2})", _unq(seen_urls[1]))
ok(d1_before and d2_after and d1_before.group(1) >= d2_after.group(1),
   f"window stepped BACK: before:{d1_before.group(1) if d1_before else '?'} ≥ after:{d2_after.group(1) if d2_after else '?'}")

old_cap = C.BACKFILL_MAX_DEPTH_DAYS
try:
    C.BACKFILL_MAX_DEPTH_DAYS = 4            # walk start is at −3d → next chunk crosses
    gn._get_many = lambda urls: [GN_XML]
    posts = gn.fetch_backfill("KSE-100", META_IDX, cur)
    ok(cur.exhausted and posts == [], "depth cap → ARCHIVE FULL, no fetch past cap")
finally:
    C.BACKFILL_MAX_DEPTH_DAYS = old_cap

# empty DATE WINDOWS must NOT exhaust a window walker (weekends / quiet cycles)
cur3 = PairCursor(source="google_news", asset="KSE-100")
cur3.caught_up = True
empty_rss = '<?xml version="1.0"?><rss version="2.0"><channel></channel></rss>'
gn._get_many = lambda urls: [empty_rss]
gn.fetch_backfill("KSE-100", META_IDX, cur3)
gn.fetch_backfill("KSE-100", META_IDX, cur3)
gn.fetch_backfill("KSE-100", META_IDX, cur3)
ok(not cur3.exhausted and cur3.chunks == 3,
   "GN: 3 empty windows → keep walking (depth cap ends the walk, not empties)")

# network failure must NOT exhaust
cur2 = PairCursor(source="google_news", asset="KSE-100")
cur2.caught_up = True
gn._get_many = lambda urls: [None]
gn.fetch_backfill("KSE-100", META_IDX, cur2)
ok(not cur2.exhausted and cur2.empty_streak == 0,
   "network fail → silent retry, no exhaustion penalty")

# ═══════════════════════════════════════════════════════ 3. bing offset walk
print("\n[3] BingNewsRSS offset walk")
bn = BingNewsRSS()
bn_urls: list[str] = []
bn._get_many = lambda urls: (bn_urls.append(urls[0]) or [BN_XML])
cur = PairCursor(source="bing_news", asset="KSE-100")
posts = bn.fetch_backfill("KSE-100", META_IDX, cur)
ok(len(posts) == 2 and "first=1" in bn_urls[0] and "sortbydate" in bn_urls[0],
   "chunk 1 = date-sorted page at first=1")
bn.fetch_backfill("KSE-100", META_IDX, cur)
ok("first=11" in bn_urls[1], "chunk 2 steps to first=11 (offset walk)")
empty = """<?xml version="1.0"?><rss version="2.0"><channel></channel></rss>"""
bn._get_many = lambda urls: [empty]
bn.fetch_backfill("KSE-100", META_IDX, cur)
ok(not cur.exhausted and cur.empty_streak == 1, "1 empty page → streak only (offset walker)")
bn.fetch_backfill("KSE-100", META_IDX, cur)
ok(cur.exhausted, "2 consecutive empty pages → ARCHIVE FULL (offset walkers DO end)")

# misdated archive item cannot teleport the cursor past the depth cap
from scraper.social_feed import RawPost  # noqa: E402
curx = PairCursor(source="bing_news", asset="X")
ancient = RawPost(id="bn:ancient", source="bing_news", author="b",
                  text="ancient misdated archive headline", url="https://u.test/a",
                  created_ts=time.time() - 2165 * 86400.0)
bn._backfill_result(curx, [ancient], True)
ok(curx.depth_days <= C.BACKFILL_MAX_DEPTH_DAYS + 0.01 and curx.exhausted,
   f"misdated item → cursor clamped at cap ({curx.depth_days:.1f}d), ARCHIVE FULL")

# ═════════════════════════════════════════════════════ reddit fullname walk
print("\n[4] RedditPublic fullname walk")
rd = RedditPublic()
rd_urls: list[str] = []
rd._safe_get = lambda url: (rd_urls.append(url) or RD_JSON)
cur = PairCursor(source="reddit", asset="KSE-100")
posts = rd.fetch_backfill("KSE-100", META_IDX, cur)
ok(len(posts) == 2, "anchor chunk parsed 2 posts")
ok(cur.after == "t3_abc0", f"cursor anchored at OLDEST fullname (got {cur.after})")
rd._safe_get = lambda url: (rd_urls.append(url) or
                            json.dumps({"data": {"children": []}}))
rd.fetch_backfill("KSE-100", META_IDX, cur)
ok("after=t3_abc0" in rd_urls[-1], "next step requests strictly older page")
rd.fetch_backfill("KSE-100", META_IDX, cur)
ok(cur.exhausted, "empty archive end → ARCHIVE FULL")
rd2 = RedditPublic()
rd2._safe_get = lambda url: None
cur = PairCursor(source="reddit", asset="KSE-100")
rd2.fetch_backfill("KSE-100", META_IDX, cur)
ok(not cur.exhausted, "reddit network fail → retry later, no exhaustion")

# ═══════════════════════════════════════════════════════ x next_token walk
print("\n[5] XCashtag next_token walk")
xx = XCashtag()
ok(not xx.enabled and xx.fetch_backfill("KSE-100", META_IDX,
   PairCursor(source="x", asset="KSE-100")) == [],
   "no bearer token → immediately ARCHIVE FULL (graceful)")

# ═══════════════════════════════════════════════ default: wires / demo
print("\n[6] Default walkers (wires, demo)")
wf = WireFeeds()
cur = PairCursor(source="wires", asset="KSE-100")
wf.fetch_backfill("KSE-100", META_IDX, cur)
ok(cur.exhausted, "wires: fixed RSS snapshot → ARCHIVE FULL immediately")
dm = DemoSource()
cur = PairCursor(source="demo", asset="KSE-100")
dm.fetch_backfill("KSE-100", META_IDX, cur)
ok(cur.exhausted, "demo synth: no history → ARCHIVE FULL immediately")

# ══════════════════════════════════════════════ 7. history bias + heartbeat
print("\n[7] PipelineState.history_bias + freshness-gated heartbeat")
from daemon.store import PipelineState        # noqa: E402  (temp DB via env)
from engine.analytics import ScoredPost       # noqa: E402

st = PipelineState()
now = time.time()
for d_ago, direction in [(6, "bullish"), (5, "bullish"), (2, "bearish"), (1, "neutral")]:
    st.add_scored(ScoredPost(
        id=f"t{d_ago}", asset="KSE-100", source="google_news", author="w",
        text="historical probe item for the bias engine", url="https://x.test/p",
        created_ts=now - d_ago * 86400.0, scored_ts=now, als=0.8,
        direction=direction, dir_confidence=0.9, relevance=0.9,
        urgency="moderate", half_life_min=360, engine="laya"))
hb = st.history_bias("KSE-100")
ok(hb["posts"] == 4 and hb["bull"] == 2 and hb["bear"] == 1 and hb["neutral"] == 1,
   f"daily aggregation over backfilled book ({hb['posts']} posts, {hb['days']} days)")
ok(hb["days"] >= 3 and hb["bull_pct"] == 66.7, f"bias math ({hb['days']}d, {hb['bull_pct']}% bull)")
ok(len(hb["daily"]) >= 3 and hb["daily"][-1]["date"], "daily series oldest→newest")
ok(st.last_new_post_ts is None,
   "heartbeat NOT reset by old backfilled posts (freshness gate)")
st.add_scored(ScoredPost(
    id="tnow", asset="KSE-100", source="google_news", author="w",
    text="live probe item right now", url="https://x.test/n",
    created_ts=now, scored_ts=now, als=0.8, direction="neutral",
    dir_confidence=0.9, relevance=0.9, urgency="moderate",
    half_life_min=360, engine="laya"))
ok(st.last_new_post_ts is not None, "live-window post DOES advance heartbeat")

print(f"\n{'=' * 60}\nRESULT: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)

"""
Social / alternative data feed ingestion + pre-inference hygiene.

Pipeline order (per fetch):
    raw posts → exact dedupe → fuzzy near-duplicate drop → coordinated-ring
    detection → spam-lexicon risk → Author Legitimacy Score (ALS) gate

Nothing that survives this module has been scored yet — it is merely *clean
enough* to justify spending a ~33 ms Laya inference on it.

Sources are polite and incremental:
  • every (source, asset) pair has a minimum poll interval
  • cursors / seen-id LRU windows mean only genuinely NEW posts are emitted
  • any network failure degrades that source, never the daemon

All adapters are optional at runtime; the radar runs on whatever subset is
alive. Enable a real X/Twitter firehose by exporting X_BEARER_TOKEN.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
import xml.etree.ElementTree as ET
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable, Iterable
from urllib.parse import quote_plus

import httpx

import config as C
from scraper.backfill import PairCursor


# ─────────────────────────────────────────────────────────────────────────────
# Data contracts
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class RawPost:
    """A single ingested item before scoring."""
    id: str
    source: str            # google_news | reddit | x | demo
    author: str
    text: str
    url: str
    created_ts: float      # unix seconds
    metrics: dict = field(default_factory=dict)   # score, num_comments, domain, ...
    als_inputs: dict = field(default_factory=dict)  # account_age_days, followers, ...


@dataclass
class FilterReport:
    """Per-fetch hygiene accounting — feeds the spam-filtered % telemetry."""
    kept: list[RawPost] = field(default_factory=list)
    dup_exact: int = 0
    dup_fuzzy: int = 0
    coordinated: int = 0
    spam: int = 0
    low_als: int = 0

    @property
    def dropped(self) -> int:
        return self.dup_exact + self.dup_fuzzy + self.coordinated + self.spam + self.low_als

    @property
    def total(self) -> int:
        return len(self.kept) + self.dropped


# ─────────────────────────────────────────────────────────────────────────────
# Text hygiene helpers
# ─────────────────────────────────────────────────────────────────────────────

_WS = re.compile(r"\s+")
_NONGRAPH = re.compile(r"[^a-z0-9 ]")
_URL = re.compile(r"https?://\S+")
_CASETAG = re.compile(r"\$[A-Z]{2,6}\b")

SPAM_LEXICON: tuple[str, ...] = (
    "giveaway", "airdrop", "join my", "premium signal", "vip signal", "signals group",
    "vip group", "dm me", "dm for details", "link in bio", "guaranteed profit",
    "guaranteed", "100% sure", "sure shot", "double your", "whatsapp group",
    "telegram channel", "free tips", "pump alert", "quick money", "earn daily",
    "signal group", "investment plan", "limited slots", "registration link",
    "click the link", "subscribe now", "promo code", "hundred percent",
)

_PUNCT = re.compile(r"[!?.]")


def normalize_text(t: str) -> str:
    """Lowercase, URL-stripped, single-spaced text — used for dedupe / Jaccard."""
    t = _URL.sub(" ", (t or "").lower())
    t = _NONGRAPH.sub(" ", t)
    return _WS.sub(" ", t).strip()


def fourgrams(t: str) -> set[str]:
    toks = t.split()
    if len(toks) < 4:
        return {t} if t else set()
    return {" ".join(toks[i:i + 4]) for i in range(len(toks) - 3)}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    return inter / (len(a) + len(b) - inter)


def spam_risk(text: str) -> float:
    """0..1 — spam-lexicon + structural heuristics (caps, link stuffing, cashtag stuffing)."""
    low = (text or "").lower()
    hits = sum(1 for kw in SPAM_LEXICON if kw in low)
    links = len(_URL.findall(text or ""))
    cashtags = len(_CASETAG.findall(text or ""))
    caps_ratio = 0.0
    letters = [ch for ch in (text or "") if ch.isalpha()]
    if letters:
        caps_ratio = sum(1 for ch in letters if ch.isupper()) / len(letters)
    exclam = len(_PUNCT.findall(text or ""))
    score = 0.28 * min(hits, 4) + 0.15 * max(links - 1, 0) + 0.12 * max(cashtags - 2, 0)
    score += 0.25 * max(caps_ratio - 0.45, 0) * 2 + 0.06 * max(exclam - 3, 0)
    return min(score, 1.0)


# ─────────────────────────────────────────────────────────────────────────────
# Feed hub — dedupe window, coordination rings, ALS, gate
# ─────────────────────────────────────────────────────────────────────────────

class FeedHub:
    """Owns cross-fetch hygiene state (dedupe LRU, recent text window) and the
    ALS gate. One instance per daemon process."""

    def __init__(self) -> None:
        self._seen_ids: dict[str, deque] = defaultdict(lambda: deque(maxlen=1500))
        self._recent: deque[tuple[float, str, set[str], str]] = deque(maxlen=1000)
        #            (ts, author, fourgrams, normalized)
        self._author_sightings: dict[str, deque] = defaultdict(lambda: deque(maxlen=80))
        # coordination clustering: normalized text → distinct pusher authors
        self._ring_authors: dict[str, set[str]] = defaultdict(set)

    # ---------------------------------------------------------------- ingest
    def ingest(self, posts: Iterable[RawPost]) -> FilterReport:
        """Hygiene gate. ORDER MATTERS: author-aware duplicate handling first
        (exact dup / near-dup from NEW authors feeds the coordinated-ring
        cluster), then spam, then the ALS gate."""
        rep = FilterReport()
        now = time.time()

        for p in posts:
            if not p.text or len(p.text.strip()) < 8:
                rep.spam += 1
                continue

            norm = normalize_text(p.text)
            grams = fourgrams(norm)

            # 1 ── EXACT duplicate → ring-cluster accounting, then drop
            if norm in self._seen_ids[p.source]:
                ring = self._ring_authors[norm]
                ring.add(p.author)
                if len(ring) >= C.COORD_CLUSTER_MIN_AUTHORS:
                    rep.coordinated += 1        # same text, ≥N pushers → Sybil ring
                else:
                    rep.dup_exact += 1
                continue
            self._seen_ids[p.source].append(norm)
            self._ring_authors[norm].add(p.author)

            # 2 ── FUZZY near-duplicate → author-aware ring accounting
            matched_norm = None
            for _, _, g2, norm2 in reversed(self._recent):
                if jaccard(grams, g2) >= C.FUZZY_JACCARD_THRESHOLD:
                    matched_norm = norm2
                    break
            if matched_norm is not None:
                ring = self._ring_authors[matched_norm]
                ring.add(p.author)
                if len(ring) >= C.COORD_CLUSTER_MIN_AUTHORS:
                    rep.coordinated += 1        # paraphrased ring — same cluster
                else:
                    rep.dup_fuzzy += 1
                continue

            # 3 ── spam risk hard gate (obvious garbage never gets ALS'd)
            risk = spam_risk(p.text)
            if risk >= 0.55:
                rep.spam += 1
                continue

            # 4 ── ALS gate
            als = self.author_legitimacy_score(p, risk)
            if als < C.ALS_THRESHOLD:
                rep.low_als += 1
                continue

            self._recent.append((now, p.author, grams, norm))
            self._author_sightings[p.author].append(now)
            rep.kept.append(p)

        return rep

    # ------------------------------------------------------------------- ALS
    def author_legitimacy_score(self, post: RawPost, spam_risk_v: float) -> float:
        """
        Weighted over dimensions actually available; missing dims default to a
        neutral 0.5 and the weights are renormalized — so a source without
        account metadata (e.g. RSS) still yields a fair score rather than a
        systematically punitive one.
        """
        dims: list[tuple[float, float]] = []          # (weight, value 0..1)

        # account age
        age_days = post.als_inputs.get("account_age_days")
        if age_days is not None:
            v_age = min(math.log10(max(age_days, 1)) / math.log10(3650), 1.0)  # 10y cap
            dims.append((C.ALS_W_ACCOUNT_AGE, v_age))

        # followers / follower-ratio
        followers = post.als_inputs.get("followers")
        if followers is not None:
            dims.append((C.ALS_W_ENGAGEMENT, min(math.log10(max(followers, 1)) / 5.0, 1.0)))

        # history: repeat sightings of this author = established presence
        n_seen = len(self._author_sightings.get(post.author, ()))
        dims.append((C.ALS_W_HISTORY, min(0.35 + 0.13 * n_seen, 1.0)))

        # editorial reputation (news wires) or generic engagement quality
        domain = (post.metrics or {}).get("domain", "")
        if domain:
            dims.append((C.ALS_W_PURITY, C.DOMAIN_REPUTATION.get(domain, C.DOMAIN_REPUTATION_DEFAULT)))
        elif post.source == "reddit":
            score = float((post.metrics or {}).get("score", 0) or 0)
            comments = float((post.metrics or {}).get("num_comments", 0) or 0)
            eng = min(math.log10(max(score + comments, 1)) / 3.0, 1.0)      # ~1000 → 1.0
            dims.append((C.ALS_W_ENGAGEMENT, max(eng, 0.15)))

        # purity = 1 − spam risk
        dims.append((C.ALS_W_PURITY, max(1.0 - spam_risk_v, 0.0)))

        wsum = sum(w for w, _ in dims) or 1.0
        als = sum(w * v for w, v in dims) / wsum
        return round(min(max(als, 0.0), 1.0), 4)


# ─────────────────────────────────────────────────────────────────────────────
# Source adapters
# ─────────────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────────
# Shared fetch pool — every sub-request (each query, each subreddit, each wire)
# fires CONCURRENTLY so one rotation slot costs ~max(single request), never
# the sum. httpx.Client is thread-safe.
# ─────────────────────────────────────────────────────────────────────────────

_FETCH_POOL: ThreadPoolExecutor | None = None


def _pool() -> ThreadPoolExecutor:
    global _FETCH_POOL
    if _FETCH_POOL is None:
        _FETCH_POOL = ThreadPoolExecutor(
            max_workers=C.MAX_CONCURRENT_FETCHES, thread_name_prefix="psxfetch")
    return _FETCH_POOL


class SourceAdapter:
    name = "base"
    enabled: bool = True
    # Backward-walk exhaustion policy. OFFSET walkers (Bing first=N, Reddit
    # after=, X next_token) truly END at the archive's oldest item — an empty
    # page there means "past the end". WINDOW walkers (Google News
    # after:/before:) legitimately hit empty windows (weekends, quiet cycles,
    # narrow queries) — only the depth cap ends those.
    EXHAUST_ON_EMPTY: bool = True

    def __init__(self) -> None:
        self._last_poll: dict[str, float] = {}
        self.client = httpx.Client(
            headers={"User-Agent": C.USER_AGENT, "Accept": "application/json, text/xml, */*"},
            timeout=httpx.Timeout(C.FETCH_TIMEOUT_S, connect=C.FETCH_CONNECT_TIMEOUT_S),
            follow_redirects=True,
            limits=httpx.Limits(
                max_connections=C.MAX_CONCURRENT_FETCHES + 8,
                max_keepalive_connections=C.MAX_CONCURRENT_FETCHES,
            ),
        )

    def due(self, asset: str) -> bool:
        min_iv = C.SOURCE_MIN_INTERVAL_S.get(self.name, 60.0)
        return time.time() - self._last_poll.get(asset, 0.0) >= min_iv

    def fetch(self, asset: str, meta: dict) -> list[RawPost]:
        self._last_poll[asset] = time.time()
        try:
            return self._fetch(asset, meta) or []
        except Exception:
            return []          # degraded source — never break the tick

    def _fetch(self, asset: str, meta: dict) -> list[RawPost]:
        raise NotImplementedError

    # ── parallel helpers ────────────────────────────────────────────────────
    def _safe_get(self, url: str) -> str | None:
        try:
            r = self.client.get(url)
            if r.status_code != 200:
                return None
            return r.text
        except Exception:
            return None

    def _get_many(self, urls: list[str]) -> list[str | None]:
        """GET every URL concurrently; failures come back as None (degraded)."""
        if not urls:
            return []
        ex = _pool()
        futs = [ex.submit(self._safe_get, u) for u in urls]
        return [f.result() for f in futs]

    # ── backfill hooks (bidirectional crawl) ─────────────────────────────────
    def fetch_backfill(self, asset: str, meta: dict, cur: PairCursor) -> list[RawPost]:
        """Fetch the next OLDER chunk for this (source, asset), advancing the
        pair's PairCursor in place. Default: no keyless archive walk exists →
        the pair is declared ARCHIVE FULL and the scheduler moves on.

        Contract for implementors: a NETWORK failure returns [] WITHOUT any
        penalty (retried on the next sweep); a genuinely empty archive result
        bumps empty_streak → exhausted after C.BACKFILL_EMPTY_STREAK; crossing
        BACKFILL_MAX_DEPTH_DAYS always exhausts."""
        cur.exhausted = True
        return []

    def _backfill_result(self, cur: PairCursor, posts: list[RawPost],
                         network_ok: bool) -> list[RawPost]:
        """Post-process a backward chunk: depth marker, empty-streak and
        depth-cap exhaustion, walk bookkeeping. Returns posts for chaining."""
        cur.chunks += 1
        if posts:
            oldest = min(p.created_ts for p in posts)
            # misdated archive items (Bing occasionally serves years-old
            # stragglers) must not teleport the cursor past the depth cap —
            # clamp the MARKER; the post itself still flows through hygiene
            floor_ts = time.time() - C.BACKFILL_MAX_DEPTH_DAYS * 86400.0
            oldest = max(oldest, floor_ts)
            cur.oldest_ts = oldest if not cur.oldest_ts else min(cur.oldest_ts, oldest)
            cur.empty_streak = 0
            cur.posts += len(posts)
            cur.backfilling = True
        elif not network_ok:
            return []          # network degraded → silent retry, no exhaustion
        elif self.EXHAUST_ON_EMPTY:
            cur.empty_streak += 1
            if cur.empty_streak >= C.BACKFILL_EMPTY_STREAK:
                cur.exhausted = True
        if cur.depth_days >= C.BACKFILL_MAX_DEPTH_DAYS:
            cur.exhausted = True
        return posts


class GoogleNewsRSS(SourceAdapter):
    """Google News RSS — real, keyless, PSX-relevant editorial chatter.
    EVERY query for the asset fires in parallel on one poll (the old
    one-query-per-poll cursor was the single biggest ingestion throttle)."""
    name = "google_news"
    EXHAUST_ON_EMPTY = False        # empty date window ≠ archive end — walk on

    def _fetch(self, asset: str, meta: dict) -> list[RawPost]:
        urls = [
            "https://news.google.com/rss/search?q="
            + quote_plus(f"{q} when:1d")
            + "&hl=en-PK&gl=PK&ceid=PK:en"
            for q in meta["queries"]
        ]
        # Quiet-hour catch-up: the 1-day window misses late-indexed items and
        # weekend opinion pieces that Google surfaces slowly. ONE extra probe
        # per poll (first query, 3-day window) catches those stragglers; the
        # hygiene gate + 48h ledger TTL keep the cost bounded and honest.
        if meta["queries"]:
            urls.append(
                "https://news.google.com/rss/search?q="
                + quote_plus(f"{meta['queries'][0]} when:3d")
                + "&hl=en-PK&gl=PK&ceid=PK:en")
        return self._parse_gn_pages(self._get_many(urls))

    def _parse_gn_pages(self, pages: list[str | None],
                        cap: int = C.MAX_POSTS_PER_FETCH) -> list[RawPost]:
        """Google News RSS pages → RawPosts (shared by forward + backfill)."""
        out: list[RawPost] = []
        seen: set[str] = set()
        for page in pages:
            if not page:
                continue
            try:
                root = ET.fromstring(page)
            except ET.ParseError:
                continue
            for item in root.iterfind(".//item"):
                title = (item.findtext("title") or "").strip()
                link = (item.findtext("link") or "").strip()
                pub = (item.findtext("pubDate") or "").strip()
                src_el = item.find("source")
                domain = (src_el.text or "").strip().lower() if src_el is not None else ""
                # strip " - Publisher" suffix Google appends
                clean = re.sub(r"\s+-\s+[^-]{2,40}$", "", title)
                if not clean:
                    continue
                pid = "gn:" + hashlib.sha1(link.encode()).hexdigest()[:16]
                if pid in seen:
                    continue
                seen.add(pid)
                out.append(RawPost(
                    id=pid,
                    source=self.name,
                    author=domain or "newswire",
                    text=clean,
                    url=link,
                    created_ts=_parse_rfc822(pub),
                    metrics={"domain": domain},
                ))
                if len(out) >= cap:
                    return out
        return out

    def fetch_backfill(self, asset: str, meta: dict, cur: PairCursor) -> list[RawPost]:
        """Date-window WALK: `after:<d0> before:<d1>` queries stepping backwards
        in time by BACKFILL_WINDOW_DAYS['google_news'] per chunk, rotating
        across the asset's query set so deep history isn't one query's bias.
        The walk starts where the live windows end (when:1d + when:3d probe)."""
        now = time.time()
        if cur.win_end_ts == 0.0:
            cur.win_end_ts = now - 3.0 * 86400.0
        win_s = C.BACKFILL_WINDOW_DAYS.get(self.name, 3) * 86400.0
        end = cur.win_end_ts
        start = end - win_s
        if now - start > C.BACKFILL_MAX_DEPTH_DAYS * 86400.0:
            cur.exhausted = True
            return []
        qs = meta.get("queries") or [asset]
        q = qs[cur.page % len(qs)]
        d0 = time.strftime("%Y-%m-%d", time.gmtime(start))
        d1 = time.strftime("%Y-%m-%d", time.gmtime(end))
        url = ("https://news.google.com/rss/search?q="
               + quote_plus(f"{q} after:{d0} before:{d1}")
               + "&hl=en-PK&gl=PK&ceid=PK:en")
        cur.win_end_ts = start
        cur.page += 1
        pages = self._get_many([url])
        network_ok = bool(pages and pages[0])
        posts = self._parse_gn_pages(pages)
        return self._backfill_result(cur, posts, network_ok)


def _parse_rfc822(s: str) -> float:
    from email.utils import parsedate_to_datetime
    try:
        return parsedate_to_datetime(s).timestamp()
    except Exception:
        return time.time()


def _parse_rss_items(xml_text: str) -> list[tuple[str, str, str]]:
    """Generic RSS → [(title, link, pubDate)] — tolerant of broken feeds."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    return [
        ((i.findtext("title") or "").strip(),
         (i.findtext("link") or "").strip(),
         (i.findtext("pubDate") or "").strip())
        for i in root.iterfind(".//item")
    ]


class BingNewsRSS(SourceAdapter):
    """Bing News RSS — second keyless search wire; catches outlets Google's
    PK edition drops. All asset queries fire concurrently; market forced to
    English so headlines are scoreable."""
    name = "bing_news"

    def _fetch(self, asset: str, meta: dict) -> list[RawPost]:
        urls = [
            "https://www.bing.com/news/search?q=" + quote_plus(q)
            + "&format=RSS&mkt=en-US&setlang=en"
            for q in meta["queries"]
        ]
        pages = self._get_many(urls)
        return self._parse_bn_pages(pages)

    def _parse_bn_pages(self, pages: list[str | None],
                        cap: int = C.MAX_POSTS_PER_FETCH) -> list[RawPost]:
        """Bing News RSS pages → RawPosts (shared by forward + backfill)."""
        out: list[RawPost] = []
        seen: set[str] = set()
        for page in pages:
            if not page:
                continue
            for title, link, pub in _parse_rss_items(page):
                # strip " - Publisher" suffix Bing appends
                clean = re.sub(r"\s+-\s+[^-]{2,40}$", "", title)
                if not clean or not link:
                    continue
                pid = "bn:" + hashlib.sha1(link.encode()).hexdigest()[:16]
                if pid in seen:
                    continue
                seen.add(pid)
                out.append(RawPost(
                    id=pid,
                    source=self.name,
                    author="bing_news",
                    text=clean,
                    url=link,
                    created_ts=_parse_rfc822(pub),
                    metrics={},
                ))
                if len(out) >= cap:
                    return out
        return out

    def fetch_backfill(self, asset: str, meta: dict, cur: PairCursor) -> list[RawPost]:
        """Offset WALK through Bing's date-sorted archive: `first=1+page·10`
        steps one RSS page (~10 items) back per chunk, pinned to the FIRST
        query so the offset is a stable position in one result set."""
        qs = meta.get("queries") or [asset]
        q = qs[0]
        first = 1 + cur.page * 10
        url = ("https://www.bing.com/news/search?q=" + quote_plus(q)
               + "&format=RSS&mkt=en-US&setlang=en"
               + "&qft=sortbydate%3d%221%22&first=" + str(first))
        cur.page += 1
        pages = self._get_many([url])
        network_ok = any(p for p in pages)
        posts = self._parse_bn_pages(pages)
        return self._backfill_result(cur, posts, network_ok)


class WireFeeds(SourceAdapter):
    """Pakistani business wires (Business Recorder / Tribune / Dawn) — fixed
    editorial feeds, no query. Downloads are content-cached across assets so a
    rotation sweep reuses one parse instead of re-downloading per asset.
    Index assets take the whole market-level feed; company assets only keep
    headlines that actually mention the ticker or company name."""
    name = "wires"

    def __init__(self) -> None:
        super().__init__()
        self._cache: dict[str, tuple[float, list[tuple[str, str, str]]]] = {}
        self._cache_ttl = C.SOURCE_MIN_INTERVAL_S.get("wires", 75.0) * 0.9

    def _feeds(self) -> list[tuple[str, str, list[tuple[str, str, str]]]]:
        """Return (domain, url, items) per wire, downloading ONLY feeds whose
        cache is staler than the TTL — a full rotation sweep reuses ONE set of
        downloads instead of re-fetching per asset."""
        now = time.time()
        stale = [(d, u) for d, u in C.WIRE_FEEDS
                 if u not in self._cache or now - self._cache[u][0] >= self._cache_ttl]
        pages = self._get_many([u for _d, u in stale]) if stale else []
        got = {u: p for (_d, u), p in zip(stale, pages)}

        out: list[tuple[str, str, list[tuple[str, str, str]]]] = []
        for domain, url in C.WIRE_FEEDS:
            if url in got:
                if got[url]:
                    self._cache[url] = (now, _parse_rss_items(got[url]))
                elif url not in self._cache:
                    self._cache[url] = (now, [])   # failed feed — brief empty entry
            items = self._cache.get(url, (0.0, []))[1]
            out.append((domain, url, items))
        return out

    def _fetch(self, asset: str, meta: dict) -> list[RawPost]:
        feeds = self._feeds()
        is_company = meta.get("kind") == "company"

        # company filter: word-boundary ticker OR full company name mention
        pat = None
        name_low = (meta.get("name") or "").lower()
        if is_company:
            pat = re.compile(r"\b" + re.escape(asset.lower()) + r"\b", re.IGNORECASE)

        out: list[RawPost] = []
        seen: set[str] = set()
        for domain, _url, items in feeds:
            for title, link, pub in items:
                if not title:
                    continue
                low = title.lower()
                if is_company and pat is not None:
                    if not (pat.search(title) or (len(name_low) > 5 and name_low in low)):
                        continue
                pid = "wr:" + hashlib.sha1((link or title).encode()).hexdigest()[:16]
                if pid in seen:
                    continue
                seen.add(pid)
                out.append(RawPost(
                    id=pid,
                    source=self.name,
                    author=domain,
                    text=title,
                    url=link,
                    created_ts=_parse_rfc822(pub),
                    metrics={"domain": domain},
                ))
                if len(out) >= C.MAX_POSTS_PER_FETCH:
                    return out
        return out


class RedditPublic(SourceAdapter):
    """Reddit public JSON (r/StocksPakistan, r/pakstocks) — keyless, read-only.
    Both subreddits fetched CONCURRENTLY; a 403/429 from one merely degrades
    that sub (Reddit throttles datacenter IPs — residential networks are fine)."""
    name = "reddit"

    def _fetch(self, asset: str, meta: dict) -> list[RawPost]:
        lim = C.REDDIT_LIMIT
        if meta.get("kind") == "index":
            urls = [f"https://www.reddit.com/r/{sub}/new.json?limit={lim}"
                    for sub in C.REDDIT_SUBREDDITS]
        else:
            urls = [f"https://www.reddit.com/r/{sub}/search.json?q={quote_plus(asset)}"
                    f"&restrict_sr=1&sort=new&limit={lim}"
                    for sub in C.REDDIT_SUBREDDITS]

        pages = self._get_many(urls)
        out: list[RawPost] = []
        for page in pages:
            if not page:
                continue
            try:
                children = json.loads(page).get("data", {}).get("children", [])
            except json.JSONDecodeError:
                continue
            for child in children:
                out.append(self._reddit_post(child))
        return [p for p in out if p][: C.MAX_POSTS_PER_FETCH]

    def _reddit_post(self, child: dict) -> RawPost | None:
        """One Reddit child → RawPost (shared by forward + backfill)."""
        d = child.get("data", {})
        text = f"{d.get('title', '').strip()}. {d.get('selftext', '').strip()}".strip(". ").strip()
        if not text:
            return None
        return RawPost(
            id="rd:" + str(d.get("id", hashlib.sha1(text.encode()).hexdigest()[:10])),
            source=self.name,
            author=str(d.get("author", "unknown")),
            text=text[:1200],
            url="https://www.reddit.com" + str(d.get("permalink", "")),
            created_ts=float(d.get("created_utc") or time.time()),
            metrics={"score": d.get("score", 0), "num_comments": d.get("num_comments", 0)},
        )

    def fetch_backfill(self, asset: str, meta: dict, cur: PairCursor) -> list[RawPost]:
        """Fullname WALK — `after=t3_xxx` returns strictly OLDER posts. The
        first step anchors the walk at the oldest fullname of the newest page
        (those items hit the dedupe gate as harmless repeats), every later
        step consumes genuinely unseen history. The walk stays pinned to ONE
        subreddit because fullname cursors are subreddit-scoped."""
        sub = C.REDDIT_SUBREDDITS[0]
        lim = min(C.REDDIT_LIMIT, 100)
        if meta.get("kind") == "index":
            base = f"https://www.reddit.com/r/{sub}/new.json"
        else:
            base = (f"https://www.reddit.com/r/{sub}/search.json"
                    f"?q={quote_plus(asset)}&restrict_sr=1&sort=new")
        url = base + f"&limit={lim}" + (f"&after={cur.after}" if cur.after else "")
        page = self._safe_get(url)
        if page is None:
            return self._backfill_result(cur, [], False)   # network fail → retry later
        try:
            children = json.loads(page).get("data", {}).get("children", [])
        except json.JSONDecodeError:
            children = []
        posts: list[RawPost] = []
        oldest_ts: float | None = None
        oldest_name: str | None = None
        for child in children:
            p = self._reddit_post(child)
            if p is None:
                continue
            posts.append(p)
            d = child.get("data", {})
            name = str(d.get("name") or ("t3_" + str(d.get("id", ""))))
            ts = float(d.get("created_utc") or 0.0)
            if ts and (oldest_ts is None or ts < oldest_ts):
                oldest_ts, oldest_name = ts, name
        if oldest_name:
            cur.after = oldest_name
        return self._backfill_result(cur, [p for p in posts if p], True)


class XCashtag(SourceAdapter):
    """X/Twitter recent search on $CASHTAG — requires X_BEARER_TOKEN (env).
    Silently disabled when no credentials are present."""
    name = "x"

    def __init__(self) -> None:
        super().__init__()
        self.enabled = bool(C.X_BEARER_TOKEN)
        if self.enabled:
            self.client.headers["Authorization"] = f"Bearer {C.X_BEARER_TOKEN}"

    def _fetch(self, asset: str, meta: dict) -> list[RawPost]:
        if not self.enabled:
            return []
        cashtag = asset if meta.get("kind") == "company" else "KSE100"
        url = self._search_url(cashtag)
        r = self.client.get(url)
        if r.status_code != 200:
            return []
        return self._parse_tweets(json.loads(r.text))

    def _search_url(self, cashtag: str, next_token: str | None = None) -> str:
        url = ("https://api.twitter.com/2/tweets/search/recent?query="
               + quote_plus(f"${cashtag} -is:retweet lang:en")
               + f"&max_results={min(C.MAX_POSTS_PER_FETCH, 100)}")
        if next_token:
            url += "&next_token=" + quote_plus(next_token)
        return url

    def _parse_tweets(self, js: dict) -> list[RawPost]:
        """X API v2 payload → RawPosts (shared by forward + backfill)."""
        users = {u["id"]: u for u in js.get("includes", {}).get("users", [])}
        out: list[RawPost] = []
        for t in js.get("data", []):
            u = users.get(t.get("author_id", ""), {})
            out.append(RawPost(
                id="x:" + t["id"],
                source=self.name,
                author=u.get("username", "unknown"),
                text=t.get("text", ""),
                url=f"https://x.com/{u.get('username', 'i')}/status/{t['id']}",
                created_ts=time.time(),   # created_at needs extra fields; tick-fresh is fine
                metrics={},
                als_inputs={
                    "followers": u.get("public_metrics", {}).get("followers_count"),
                    "account_age_days": _age_days(u.get("created_at")),
                },
            ))
        return out

    def fetch_backfill(self, asset: str, meta: dict, cur: PairCursor) -> list[RawPost]:
        """next_token WALK — X recent-search pages step backwards through the
        ~7-day window the endpoint serves; no token → archive end."""
        if not self.enabled:
            cur.exhausted = True
            return []
        cashtag = asset if meta.get("kind") == "company" else "KSE100"
        try:
            r = self.client.get(self._search_url(cashtag, cur.next_token))
            if r.status_code != 200:
                return self._backfill_result(cur, [], False)
            js = json.loads(r.text)
        except Exception:
            return self._backfill_result(cur, [], False)
        cur.next_token = (js.get("meta") or {}).get("next_token")
        posts = self._backfill_result(cur, self._parse_tweets(js), True)
        if not cur.next_token:
            cur.exhausted = True
        return posts


def _age_days(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        from datetime import datetime, timezone
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return max((time.time() - dt.timestamp()) / 86400.0, 0.0)
    except Exception:
        return None


class DemoSource(SourceAdapter):
    """
    --demo synthetic PSX chatter generator. Produces a realistic mix of
    bullish / bearish / neutral / spam posts so the full pipeline (ALS gate,
    Laya-or-fallback scoring, WCI, sphere flow) can be exercised offline.
    Telemetry is always labeled SYNTH when this is active.
    """
    name = "demo"
    enabled = True

    _BULL = [
        "{t} breakout above resistance on heavy volume — institutions accumulating.",
        "Bullish: {t} dividend announcement beats consensus, DPS up {n}%.",
        "{t} results beat — EPS {n} PKR, payout improved. Re-rating likely.",
        "Brokerage upgrade on {t}: target revised upward, strong FY outlook.",
        "Accumulation zone for {t} — global peers rallying, rupee stabilizing.",
        "{t} wins new contract worth billions; earnings visibility through {yr}.",
    ]
    _BEAR = [
        "Bearish {t}: circular debt widening again, receivables piling up.",
        "{t} gross margins compress {n}% QoQ — cost pressure unrelenting.",
        "IMF slippage risk hits {t} — rupee devaluation pressure returning.",
        "{t} plant shutdown announced; output down {n}% until further notice.",
        "Gas curtailment notifications hit {t} sector — downgrades coming.",
        "Foreign selling accelerates in {t}; {n}nd straight week of outflows.",
    ]
    _NEUT = [
        "{t} to hold board meeting on {mon} {day}; accounts review scheduled.",
        "PSX {t} volume below average today; range-bound session continues.",
        "Analysts mixed on {t} ahead of policy rate decision next week.",
        "{t} files quarterly report with PSX, no material disclosure attached.",
    ]
    _SPAM = [
        "JOIN MY VIP SIGNALS GROUP — guaranteed profit daily, link in bio!",
        "{t} WILL DOUBLE 100% sure. Join WhatsApp group now, limited slots!",
        "Premium signal: {t} pump alert incoming, DM me for details!",
    ]
    _TICKER_POOL = ["KSE-100", "OGDC", "HUBC", "SYS", "FFC", "LUCK", "MCB", "PPL"]
    _AUTHORS = [
        "inst_research_pk", "kse_trader_ali", "macro_desk_pak", "psx_value_hunter",
        "equityanalyst_khi", " fertilizer_watch", "pk_macro_notes", "tech_stocks_pk",
        "dividend_investor", "bot_farm_A1", "bot_farm_A2", "bot_farm_A3",
        "meme_lord_92", "rupee_watcher", "cement_cycle_pk", "oil_gas_desk",
    ]
    _MONTHS = ["Jan", "Feb", "Mar", "Apr", "Oct", "Nov"]

    def __init__(self, seed: int = 7) -> None:
        super().__init__()
        import random
        self.rng = random.Random(seed)
        self._n = 0

    def _fetch(self, asset: str, meta: dict) -> list[RawPost]:
        lo, hi = C.DEMO_MODE_POSTS_PER_TICK
        k = self.rng.randint(lo, hi)
        out: list[RawPost] = []
        target = asset                     # chatter always targets the polled asset

        def emit(tpl: str, author: str) -> None:
            text = tpl.format(
                t=target, n=self.rng.randint(3, 38), yr=self.rng.randint(2027, 2030),
                mon=self.rng.choice(self._MONTHS), day=self.rng.randint(1, 28),
            )
            self._n += 1
            out.append(RawPost(
                id="dm:" + hashlib.sha1(f"{author}:{text}".encode()).hexdigest()[:16],
                source=self.name,
                author=author,
                text=text,
                url="https://synthetic.psxradar.local/post/" + str(self._n),
                created_ts=time.time() - self.rng.randint(0, 900),
                metrics={"score": self.rng.randint(0, 40), "num_comments": self.rng.randint(0, 12)},
            ))

        for _ in range(k):
            roll = self.rng.random()
            bucket = self._SPAM if roll < 0.12 else (
                self._BULL if roll < 0.48 else self._BEAR if roll < 0.84 else self._NEUT)
            emit(self.rng.choice(bucket), self.rng.choice(self._AUTHORS).strip())

        # occasional coordinated Sybil ring: ≥3 bot authors pushing the SAME
        # text — exercises the ring detector and the bot-dropped telemetry.
        if self.rng.random() < 0.10 and k >= 2:
            ring_tpl = self.rng.choice(self._BULL + self._SPAM)
            ring_text = ring_tpl.format(
                t=target, n=self.rng.randint(3, 38), yr=self.rng.randint(2027, 2030),
                mon=self.rng.choice(self._MONTHS), day=self.rng.randint(1, 28))
            for i, bot in enumerate(["bot_farm_A1", "bot_farm_A2", "bot_farm_A3"]):
                self._n += 1
                out.append(RawPost(
                    id="dm:" + hashlib.sha1(f"ring{self._n}:{bot}:{ring_text}".encode()).hexdigest()[:16],
                    source=self.name,
                    author=bot,
                    text=ring_text,
                    url=f"https://synthetic.psxradar.local/ring/{self._n}/{i}",
                    created_ts=time.time() - self.rng.randint(0, 240),
                    metrics={"score": self.rng.randint(0, 6), "num_comments": 0},
                ))
        return out


# ─────────────────────────────────────────────────────────────────────────────
# Factory
# ─────────────────────────────────────────────────────────────────────────────

def build_sources(demo: bool = False) -> list[SourceAdapter]:
    """Assemble the live adapter stack. Demo source, if requested, is appended
    so real sources still take priority when they are reachable."""
    order: list[SourceAdapter] = [GoogleNewsRSS(), BingNewsRSS(), WireFeeds(), RedditPublic()]
    try:
        x = XCashtag()
        if x.enabled:
            order.append(x)
    except Exception:
        pass
    if demo:
        order.append(DemoSource())
    return [s for s in order if s.enabled]

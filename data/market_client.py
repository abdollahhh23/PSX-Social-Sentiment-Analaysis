"""
Market data layer — real-time PSX quotes + EOD history for the Deep Field
model zoo, with a graceful multi-stage fallback chain.

Source priority
===============
1. **PSX Data Portal** (`dps.psx.com.pk`, keyless, official):
     POST /historical                     → EOD OHLCV as an HTML table
     GET  /symbols                        → ticker list (also used as probe)
     POST /data/symbol-position           → live quote (JSON, shape varies)
     POST /til-portal/get-symbol-data     → live quote (JSON, legacy shape)
   ⚠ The DPS zone publishes a single rotating A record; some edge nodes 404
   every data route while the website itself serves 200. Strategy (learned
   from the psx-dps project): resolve ALL A records, probe each with the
   cheap /symbols route, pin a healthy node by IP (SNI + Host header keep TLS
   valid), and re-probe automatically when a data route misbehaves. A 404 on
   a data route is NEVER treated as "symbol missing".
2. **Yahoo Finance chart API** (free, keyless) — only for symbols mapped in
   `C.YAHOO_SYMBOLS` (e.g. ^KSE for KSE-100).
3. **Seeded simulator** — regime-switching GBM anchored to editable base
   prices. ALWAYS labeled `sim` in telemetry; keeps the model zoo and the 3D
   panels alive offline / outside market hours / from datacenter IPs (PSX and
   Yahoo both block or throttle cloud ranges aggressively).

Etiquette
=========
A machine-wide rate limiter caps DPS at ~1 request/second, quotes are cached
per symbol for QUOTE_TTL_S, and EOD history is cached to disk so restarts
never refetch months of bars on a slow connection.
"""

from __future__ import annotations

import html.parser
import http.client
import json
import math
import os
import random
import re
import socket
import ssl
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from urllib.parse import urlparse

import config as C

_NUM = re.compile(r"[,\s]")
_MAX_READ = 1_500_000        # cap response reads (EOD HTML tables can be long)


def _num(v) -> float | None:
    """Tolerant numeric parse: '1,234.50' → 1234.5."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v) if math.isfinite(float(v)) else None
    try:
        s = _NUM.sub("", str(v))
        return float(s) if s else None
    except (TypeError, ValueError):
        return None


@dataclass
class Quote:
    symbol: str
    price: float
    prev_close: float | None = None
    open: float | None = None
    high: float | None = None
    low: float | None = None
    volume: float | None = None
    ts: float = field(default_factory=time.time)
    source: str = "sim"          # psx | yahoo | sim
    closed: bool = False         # market closed → price frozen

    @property
    def change(self) -> float | None:
        if self.prev_close:
            return self.price - self.prev_close
        return None

    @property
    def change_pct(self) -> float | None:
        if self.prev_close:
            return 100.0 * (self.price - self.prev_close) / self.prev_close
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Machine-wide DPS rate limiter
# ─────────────────────────────────────────────────────────────────────────────

class _RateLimiter:
    def __init__(self, min_interval_s: float) -> None:
        self.min = max(min_interval_s, 0.0)
        self._next_ok = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            delay = self._next_ok - now
            if delay > 0:
                time.sleep(min(delay, C.DPS_TIMEOUT_S))
                now = time.monotonic()
            self._next_ok = max(now, self._next_ok) + self.min


# ─────────────────────────────────────────────────────────────────────────────
# DPS node pool — probe / pin / re-probe (the psx-dps lesson)
# ─────────────────────────────────────────────────────────────────────────────

class _DPSNodePool:
    """Enumerate A records for DPS_HOST, keep the ones that genuinely serve
    data routes, pin the healthiest. Falls back to hostname-direct if DNS
    enumeration or probing fails."""

    HOST = None                     # set from C.DPS_BASE at init
    PROBE_PATH = None

    def __init__(self) -> None:
        u = urlparse(C.DPS_BASE)
        self.host = u.hostname or "dps.psx.com.pk"
        self.port = 443
        self.candidates: list[str | None] = [None]     # None = default DNS path
        self.pinned: str | None = None                 # pinned node IP
        self.last_probe: float = 0.0
        self.reprobe_every: float = 900.0              # re-check every 15 min
        self.unreachable_until: float = 0.0            # fast-fail after a dead cycle
        self.unreachable_backoff: float = 300.0        # 5 min before re-dialing
        self._lock = threading.Lock()

    # ── TLS-validated request, optionally PINNED to one IP ─────────────────
    # The psx-dps trick: dial the candidate IP directly, but wrap TLS with
    # server_hostname = real host so SNI + cert verification stay valid, and
    # send the real hostname in the Host header for vhost routing.
    def _request(self, ip: str | None, method: str, path: str,
                 body: str | None = None, headers: dict | None = None,
                 timeout: float | None = None) -> tuple[int, str]:
        to = timeout or C.DPS_TIMEOUT_S
        raw = socket.create_connection((ip or self.host, self.port), timeout=to)
        raw.settimeout(to)
        try:
            ctx = ssl.create_default_context()
            sock = ctx.wrap_socket(raw, server_hostname=self.host)
            conn = http.client.HTTPConnection(self.host, self.port, timeout=to)
            conn.sock = sock                    # pre-connected → auto_open skipped
            hdrs = {"Host": self.host, "Accept": "*/*",
                    "User-Agent": C.USER_AGENT,
                    "Referer": f"https://{self.host}/",
                    "X-Requested-With": "XMLHttpRequest"}
            if body is not None:
                hdrs["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"
            if headers:
                hdrs.update(headers)
            conn.request(method, path, body=body, headers=hdrs)
            resp = conn.getresponse()
            payload = resp.read(_MAX_READ)
            return resp.status, payload.decode("utf-8", "replace")
        finally:
            try:
                conn.close()
            except Exception:
                pass
            try:
                raw.close()
            except Exception:
                pass

    # ── candidate discovery ─────────────────────────────────────────────────
    def _refresh_candidates(self) -> None:
        try:
            infos = socket.getaddrinfo(self.host, 443, proto=socket.IPPROTO_TCP)
            ips = sorted({i[4][0] for i in infos})
        except Exception:
            ips = []
        with self._lock:
            # hostname-direct first, then each resolved A record
            self.candidates = [None] + ips[:6]
            self.pinned = None
            self.last_probe = time.time()

    def probe(self, limiter: _RateLimiter, force: bool = False) -> bool:
        """Find and pin a node whose DATA routes actually work.

        After a fully dead probe cycle the pool fast-fails for
        UNREACHABLE_BACKOFF seconds — offline / firewalled callers must not
        pay a multi-second dialing tax on every request."""
        now = time.monotonic()
        if now < self.unreachable_until:
            return False
        with self._lock:
            fresh = (time.time() - self.last_probe) < self.reprobe_every
            if self.pinned and fresh and not force:
                return True
        self._refresh_candidates()
        tried = 0
        for ip in list(self.candidates):
            if tried >= 4:                             # bounded probe cost
                break
            tried += 1
            limiter.wait()
            try:
                status, body = self._request(ip, "GET", C.DPS_SYMBOLS_PATH,
                                             timeout=1.5)
                if status == 200 and body and len(body) > 2:
                    with self._lock:
                        self.pinned = ip
                    self.unreachable_until = 0.0
                    return True
            except Exception:
                continue
        with self._lock:
            self.pinned = None
        self.unreachable_until = time.monotonic() + self.unreachable_backoff
        return False

    def call(self, method: str, path: str, body: str | None = None,
             limiter: _RateLimiter | None = None) -> tuple[int, str] | None:
        """One data request through the pinned node; on 404/broken pipe mark
        the node bad and re-probe once before giving up (returns None)."""
        limiter = limiter or _RateLimiter(0)
        for attempt in range(2):
            if not self.probe(limiter, force=(attempt > 0 or self.pinned is None)):
                return None
            with self._lock:
                ip = self.pinned
            try:
                status, text = self._request(ip, method, path, body=body)
                if status == 200:
                    return status, text
                # 404-on-data-route = bad node (the psx-dps finding) → re-probe
                if status in (404, 502, 503):
                    with self._lock:
                        self.pinned = None
                    continue
                return status, text
            except Exception:
                with self._lock:
                    self.pinned = None
                continue
        return None


# ─────────────────────────────────────────────────────────────────────────────
# EOD HTML table parser (stdlib — the /historical route returns HTML, not JSON)
# ─────────────────────────────────────────────────────────────────────────────

class _TableParser(html.parser.HTMLParser):
    """Pull (row-of-cells) lists out of every <tr> in the first <table>.

    PSX renders the /historical table with <th> header cells mixed into the
    same table as the data rows (thead not guaranteed), so ALL rows are
    captured and the header row is identified by content later.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self._table = 0
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._table += 1
        elif tag == "tr" and self._table:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag):
        if tag == "table" and self._table:
            self._table -= 1
        elif tag == "tr" and self._row is not None:
            if any(c.strip() for c in self._row):
                self.rows.append(self._row)
            self._row = None
        elif tag in ("td", "th") and self._cell is not None:
            self._row.append("".join(self._cell).strip())
            self._cell = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def parse_eod_html(page: str) -> list[dict]:
    """HTML table → [{'date','open','high','low','close','volume'}, …] (asc)."""
    p = _TableParser()
    try:
        p.feed(page)
    except Exception:
        return []
    if len(p.rows) < 2:
        return []

    # header row = first row containing a date-ish cell; else first row
    def looks_like_header(row: list[str]) -> bool:
        low = [c.strip().lower() for c in row]
        return any(c in ("date", "time", "dt") or "date" in c for c in low)

    h_idx = 0
    for i, row in enumerate(p.rows[:3]):
        if looks_like_header(row):
            h_idx = i
            break
    head = [h.lower() for h in p.rows[h_idx]]
    body_rows = p.rows[h_idx + 1:]
    if not body_rows:
        return []

    def col(*names: str) -> int | None:
        for n in names:
            for i, h in enumerate(head):
                if n in h:
                    return i
        return None

    i_date = col("date", "time", "dt")
    i_open, i_high = col("open"), col("high")
    i_low, i_close = col("low"), col("close", "rate")
    i_vol = col("volume", "vol")
    if i_date is None or i_close is None:
        return []
    bars: list[dict] = []
    for row in body_rows:
        if len(row) <= max(x for x in (i_date, i_close) if x is not None):
            continue
        close = _num(row[i_close])
        if close is None or close <= 0:
            continue
        d = row[i_date]
        # PSX formats vary: 'Sep 01, 2025' / '01-09-2025' — keep raw + sortable
        bars.append({
            "date": d,
            "open": _num(row[i_open]) if i_open is not None else None,
            "high": _num(row[i_high]) if i_high is not None else None,
            "low": _num(row[i_low]) if i_low is not None else None,
            "close": close,
            "volume": _num(row[i_vol]) if i_vol is not None else None,
        })
    return bars


# ─────────────────────────────────────────────────────────────────────────────
# Simulator — clearly-labeled synthetic market (regime-switching GBM)
# ─────────────────────────────────────────────────────────────────────────────

class SimMarket:
    """Deterministic per-symbol synthetic price engine.

    Regimes: low-vol drift / high-vol chop / trend burst, switching via a
    Markov chain, seeded from the symbol hash so every restart reproduces the
    same history (models see a stable world offline). If a LIVE quote has ever
    been seen for the symbol the sim re-anchors to it (no fabrication on top
    of real data — sim only fills the gaps).
    """

    _dt = 1.0 / (252.0 * 6.5 * 3600.0 / C.SIM_STEP_S)   # step fraction of a year

    def __init__(self) -> None:
        self._state: dict[str, dict] = {}
        self._lock = threading.Lock()

    def _seed(self, symbol: str) -> random.Random:
        return random.Random(hash(("psx-sim", symbol)) & 0xFFFFFFFF)

    def _init_state(self, symbol: str) -> dict:
        rng = self._seed(symbol)
        # anchor the intraday book to where the daily series actually ended,
        # plus a realistic overnight gap — no fake −48% phantom moves
        prev = self.history(symbol)[-1]
        day_open = round(prev * (1.0 + rng.uniform(-0.012, 0.012)), 2)
        price = day_open
        return {
            "price": price, "anchor": price, "regime": 0,
            "regime_left": rng.randint(400, 2500), "rng": rng,
            "last_step": time.time(), "day_open": day_open,
        }

    def history(self, symbol: str, n: int = C.HISTORY_WINDOW) -> list[float]:
        """Seed daily EOD closes (regime-switching GBM) — identical across
        restarts for the same symbol."""
        rng = self._seed(symbol)
        base = C.FUNDAMENTALS.get(symbol, {}).get("base_price", C.SIM_DEFAULT_BASE)
        vol = C.SIM_VOL_ANN / math.sqrt(252.0)
        closes, price = [], base
        regime, left = 0, rng.randint(10, 40)
        kappa = 0.015                      # mild pull toward base → plausible range
        for _ in range(n):
            if left <= 0:
                regime = rng.choice((0, 0, 1, 2))
                left = rng.randint(10, 40)
            left -= 1
            if regime == 0:                       # calm up-drift
                mu, sig = 0.0004, vol
            elif regime == 1:                     # high-vol chop
                mu, sig = 0.0, vol * 2.1
            else:                                 # trend burst
                mu, sig = rng.choice((-0.0022, 0.0024)), vol * 1.4
            reversion = kappa * math.log(base / price)
            price *= math.exp(mu + reversion + sig * rng.gauss(0, 1))
            closes.append(round(price, 2))
        return closes

    def step(self, symbol: str, now: float | None = None) -> tuple[float, float]:
        """Advance the intraday sim one tick → (price, day_open)."""
        with self._lock:
            st = self._state.setdefault(symbol, self._init_state(symbol))
            now = now or time.time()
            # clamp: one step minimum, 10 minutes of catch-up maximum —
            # guards against clock jumps and first-call super-delta
            dt = min(max(now - st["last_step"], C.SIM_STEP_S), 600.0)
            st["last_step"] = now
            if st["regime_left"] <= 0:
                st["regime"] = st["rng"].choice((0, 0, 1, 2))
                st["regime_left"] = st["rng"].randint(400, 2500)
            st["regime_left"] -= 1
            vol = {"0": 0.55, "1": 1.9, "2": 1.15}[str(st["regime"])] \
                * C.SIM_VOL_ANN * math.sqrt(self._dt * max(dt / C.SIM_STEP_S, 0.25))
            drift = {"0": 0.10, "1": -0.02, "2": 0.45}[str(st["regime"])] \
                * self._dt * max(dt / C.SIM_STEP_S, 0.25)
            st["price"] *= math.exp(drift + vol * st["rng"].gauss(0, 1))
            return round(st["price"], 2), round(st["day_open"], 2)

    def reanchor(self, symbol: str, price: float) -> None:
        """Sync the sim to a LIVE quote — sim only fills gaps around real data."""
        with self._lock:
            st = self._state.setdefault(symbol, self._init_state(symbol))
            st["price"] = st["anchor"] = price


# ─────────────────────────────────────────────────────────────────────────────
# The client
# ─────────────────────────────────────────────────────────────────────────────

def market_open(now: float | None = None) -> bool:
    """PSX regular session 09:30–15:30 PKT, Mon–Fri (approx — holidays ignored)."""
    now = now or time.time()
    lt = time.gmtime(now + C.MARKET_TZ_OFFSET_H * 3600)
    if lt.tm_wday >= 5:
        return False
    mins = lt.tm_hour * 60 + lt.tm_min
    return (C.MARKET_OPEN_H * 60 + C.MARKET_OPEN_M) <= mins <= (C.MARKET_CLOSE_H * 60 + C.MARKET_CLOSE_M)


class MarketClient:
    """One instance per worker. Thread-safe, never raises on fetch paths."""

    def __init__(self) -> None:
        self.pool = _DPSNodePool()
        self.limiter = _RateLimiter(C.DPS_MIN_INTERVAL_S)
        self.sim = SimMarket()
        self._quotes: dict[str, Quote] = {}
        self._hist_cache: dict[str, list[float]] = {}
        self._disk_loaded = False
        self._lock = threading.Lock()
        self.stats = {"dps_ok": 0, "dps_fail": 0, "yahoo_ok": 0, "sim_ticks": 0,
                      "node": None, "probe_count": 0}
        self._load_disk_cache()

    # ─────────────────────────────────────────────────────────────── quote ──
    def refresh(self, symbol: str, force: bool = False) -> Quote:
        """Refresh + return the current quote for one symbol (cached TTL)."""
        with self._lock:
            cached = self._quotes.get(symbol)
            if cached and not force and (time.time() - cached.ts) < C.QUOTE_TTL_S:
                return cached

        q = self._quote_dps(symbol)
        if q is None:
            q = self._quote_yahoo(symbol)
        live_seen = q is not None

        is_open = market_open()
        if live_seen:
            if q.source == "psx":
                self.stats["dps_ok"] += 1
            else:
                self.stats["yahoo_ok"] += 1
            self.sim.reanchor(symbol, q.price)
            q.closed = not is_open
        else:
            self.stats["dps_fail"] += 1
            price, open_ = self.sim.step(symbol)
            prev = self.sim.history(symbol)[-1]
            q = Quote(symbol=symbol, price=price, prev_close=prev, open=open_,
                      high=max(price, open_), low=min(price, open_),
                      ts=time.time(), source="sim", closed=False)
            self.stats["sim_ticks"] += 1

        with self._lock:
            self._quotes[symbol] = q
        return q

    def quote(self, symbol: str) -> Quote:
        with self._lock:
            cached = self._quotes.get(symbol)
        if cached:
            return cached
        return self.refresh(symbol, force=True)

    # ── DPS live quote (JSON shapes vary — walk the payload) ────────────────
    def _quote_dps(self, symbol: str) -> Quote | None:
        body = f"symbol={symbol}"
        for path in C.DPS_QUOTE_CANDIDATES:
            res = self.pool.call("POST", path, body=body, limiter=self.limiter)
            if not res:
                continue
            status, text = res
            if status != 200 or not text:
                continue
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                continue
            q = _quote_from_json(symbol, data)
            if q:
                q.source = "psx"
                return q
        return None

    # ── Yahoo chart fallback (index symbols only) ───────────────────────────
    def _quote_yahoo(self, symbol: str) -> Quote | None:
        ysym = C.YAHOO_SYMBOLS.get(symbol)
        if not ysym:
            return None
        try:
            status, text = _plain_https_get(
                "query1.finance.yahoo.com",
                f"/v8/finance/chart/{ysym}?range=1d&interval=5m",
                timeout=4.0)
        except Exception:
            return None
        if status != 200 or not text:
            return None
        try:
            j = json.loads(text)
            meta = j["chart"]["result"][0]["meta"]
            price = _num(meta.get("regularMarketPrice"))
            prev = _num(meta.get("chartPreviousClose")) or _num(meta.get("previousClose"))
            if price:
                return Quote(symbol=symbol, price=price, prev_close=prev,
                             ts=time.time(), source="yahoo")
        except Exception:
            return None
        return None

    # ───────────────────────────────────────────────────────────── history ──
    def history(self, symbol: str, months: int | None = None) -> list[float]:
        """EOD closes, ascending. Disk-cached; DPS → sim fallback. Never None."""
        months = months or C.HISTORY_MONTHS
        with self._lock:
            if symbol in self._hist_cache:
                return self._hist_cache[symbol]
        bars = self._fetch_dps_history(symbol, months)
        src = "psx"
        if not bars:
            bars = self._fetch_yahoo_history(symbol)
            src = "yahoo"
        if bars:
            closes = [b["close"] for b in bars][-C.HISTORY_WINDOW:]
            self._save_disk_cache(symbol, {"source": src, "bars": bars})
        else:
            closes = self.sim.history(symbol)[-C.HISTORY_WINDOW:]
            src = "sim"
        closes = closes[-C.HISTORY_WINDOW:]
        with self._lock:
            self._hist_cache[symbol] = closes
        return closes

    def _fetch_dps_history(self, symbol: str, months: int) -> list[dict]:
        if not C.MARKET_ENABLED:
            return []
        today = time.gmtime(time.time() + C.MARKET_TZ_OFFSET_H * 3600)
        chunks: list[tuple[int, int]] = []
        y, m = today.tm_year, today.tm_mon
        for _ in range(months):
            chunks.append((y, m))
            m -= 1
            if m == 0:
                y, m = y - 1, 12
        # most recent first — stop early once we have enough bars
        bars: list[dict] = []
        seen_dates: set[str] = set()

        def pull(ym: tuple[int, int]) -> list[dict]:
            yy, mm = ym
            body = f"month={mm}&year={yy}&symbol={symbol}"
            res = self.pool.call("POST", C.DPS_HISTORICAL_PATH, body=body,
                                 limiter=self.limiter)
            if not res or res[0] != 200:
                return []
            return parse_eod_html(res[1])

        with ThreadPoolExecutor(max_workers=min(C.HISTORY_FETCH_WORKERS, len(chunks))) as pool:
            for out in pool.map(pull, chunks):
                for b in out:
                    if b["date"] not in seen_dates:
                        seen_dates.add(b["date"])
                        bars.append(b)
                if len(bars) >= C.HISTORY_WINDOW + 30:
                    break
        return sorted(bars, key=lambda b: b["date"])

    def _fetch_yahoo_history(self, symbol: str) -> list[dict]:
        ysym = C.YAHOO_SYMBOLS.get(symbol)
        if not ysym:
            return []
        try:
            status, text = _plain_https_get(
                "query1.finance.yahoo.com",
                f"/v8/finance/chart/{ysym}?range=1y&interval=1d",
                timeout=6.0)
            if status != 200 or not text:
                return []
            j = json.loads(text)
            r = j["chart"]["result"][0]
            ts = r.get("timestamp", [])
            closes = (r.get("indicators", {}).get("quote", [{}])[0]
                      .get("close", []))
            bars = []
            for t, c in zip(ts, closes):
                cv = _num(c)
                if cv:
                    bars.append({"date": str(t), "close": cv})
            return bars
        except Exception:
            return []

    # ────────────────────────────────────────────────────────── disk cache ──
    def _load_disk_cache(self) -> None:
        if self._disk_loaded:
            return
        self._disk_loaded = True
        try:
            with open(C.MARKET_CACHE_PATH, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            for sym, entry in data.items():
                bars = entry.get("bars") or []
                if bars:
                    self._hist_cache[sym] = [b["close"] for b in bars][-C.HISTORY_WINDOW:]
        except Exception:
            pass

    def _save_disk_cache(self, symbol: str, entry: dict) -> None:
        try:
            data: dict = {}
            if os.path.exists(C.MARKET_CACHE_PATH):
                with open(C.MARKET_CACHE_PATH, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
            data[symbol] = entry
            os.makedirs(os.path.dirname(C.MARKET_CACHE_PATH) or ".", exist_ok=True)
            tmp = C.MARKET_CACHE_PATH + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(data, fh)
            os.replace(tmp, C.MARKET_CACHE_PATH)
        except Exception:
            pass

    # ─────────────────────────────────────────────────────────── telemetry ──
    def health(self) -> dict:
        return {
            "node": self.pool.pinned or "probing…",
            "dps_ok": self.stats["dps_ok"],
            "dps_fail": self.stats["dps_fail"],
            "yahoo_ok": self.stats["yahoo_ok"],
            "sim_ticks": self.stats["sim_ticks"],
        }


# ─────────────────────────────────────────────────────────────────────────────
# Plain HTTPS GET for non-DPS hosts (Yahoo) — normal DNS, SNI = target host
# ─────────────────────────────────────────────────────────────────────────────

def _plain_https_get(host: str, path: str, timeout: float = 5.0,
                     port: int = 443) -> tuple[int, str]:
    raw = socket.create_connection((host, port), timeout=timeout)
    raw.settimeout(timeout)
    try:
        ctx = ssl.create_default_context()
        sock = ctx.wrap_socket(raw, server_hostname=host)
        conn = http.client.HTTPConnection(host, port, timeout=timeout)
        conn.sock = sock
        try:
            conn.request("GET", path, headers={
                "Host": host, "Accept": "*/*",
                "User-Agent": C.USER_AGENT})
            resp = conn.getresponse()
            return resp.status, resp.read(_MAX_READ).decode("utf-8", "replace")
        finally:
            try:
                conn.close()
            except Exception:
                pass
    finally:
        try:
            raw.close()
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# Tolerant DPS quote-JSON extraction — shapes drift between DPS routes/versions
# ─────────────────────────────────────────────────────────────────────────────

def _quote_from_json(symbol: str, data) -> Quote | None:
    """Walk an arbitrary DPS JSON blob looking for a price-ish dict."""
    best: dict = {}

    def walk(node) -> None:
        if isinstance(node, dict):
            score = 0
            for k, v in node.items():
                lk = str(k).lower()
                if any(t in lk for t in ("close", "price", "ldcp", "rate")) and \
                        _num(v) is not None and _num(v) > 0:
                    score += 2 if ("close" in lk or "price" in lk) else 1
            if score > best.get("_score", 0):
                cand = dict(node)
                cand["_score"] = score
                best.clear()
                best.update(cand)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(data)
    if not best:
        return None

    def g(*names) -> float | None:
        for n in names:
            for k, v in best.items():
                if str(k).lower() == n:
                    x = _num(v)
                    if x is not None:
                        return x
        return None

    price = g("close", "currentclose", "price", "rate", "ltp")
    prev = g("ldcp", "prevclose", "previousclose")
    if price is None:
        # some shapes only carry change + ldcp
        chg = g("change")
        if prev is not None and chg is not None:
            price = prev + chg
    if price is None or price <= 0:
        return None
    if prev is None:
        prev = price - (g("change") or 0.0) or None
    return Quote(symbol=symbol, price=price, prev_close=prev,
                 open=g("open"), high=g("high"), low=g("low"),
                 volume=g("volume", "vol"), ts=time.time())

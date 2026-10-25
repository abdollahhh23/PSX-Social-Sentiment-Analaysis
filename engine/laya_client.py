"""
Laya System 1 decision-engine client — the radar's ~33 ms scoring core.

Protocol (self-hosted, POST /v1/systemone)
------------------------------------------
The engine receives a structured payload:

    {
      "engine": "system-1",
      "state":  { domain / asset / temporal / author context },
      "questions": [
        {"id": "directional_bias",        "type": "choice",
         "options": ["bullish", "bearish", "neutral"],  "prompt": …},
        {"id": "institutional_relevance", "type": "noul",
         "prompt": "credible analytical insight vs meme hype → P(0..1)"},
        {"id": "urgency_score",           "type": "score",
         "scale": ["low", "moderate", "critical"],      "prompt": …}
      ],
      "input": {"text": …, "url": …}
    }

and answers each typed question. The parser is deliberately tolerant of
shape drift (different answer wrappers) but strict about semantics:
direction ∈ {bullish, bearish, neutral}, relevance ∈ [0,1], urgency ∈
{low, moderate, critical}.

Resilience contract
-------------------
• ConnectionError → fail FAST (engine-down is definitive — no retry storm).
• Timeout / 5xx   → up to LAYA_MAX_RETRIES quick retries.
• Circuit breaker → after N consecutive failures the client goes OPEN for a
  cooldown, then HALF_OPEN probes with a single call.
• Every failure path lands on the deterministic local heuristic scorer so
  the pipeline never stalls — and telemetry always labels the result source
  ("laya" vs "fallback"), which the dashboard surfaces honestly.
"""

from __future__ import annotations

import math
import re
import time
from dataclasses import dataclass, field

import httpx

import config as C


# ─────────────────────────────────────────────────────────────────────────────
# Result contract
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Verdict:
    """Normalized scoring verdict for one post."""
    direction: str            # bullish | bearish | neutral
    dir_confidence: float     # 0..1 — engine's probability for that direction
    relevance: float          # 0..1 — institutional relevance probability
    urgency: str              # low | moderate | critical
    latency_ms: float
    source: str               # "laya" | "fallback"
    fallback_reason: str = ""


@dataclass
class CircuitBreaker:
    failure_threshold: int = C.CB_FAILURE_THRESHOLD
    cooldown_s: float = C.CB_COOLDOWN_S
    success_reset: int = C.CB_SUCCESS_RESET

    state: str = "closed"                     # closed | open | half_open
    failures: int = 0
    successes: int = 0
    opened_at: float = 0.0

    def allow(self) -> bool:
        if self.state == "closed":
            return True
        if self.state == "open":
            if time.monotonic() - self.opened_at >= self.cooldown_s:
                self.state = "half_open"
                self.successes = 0
                return True
            return False
        return True                            # half_open → single probe allowed

    def record_failure(self) -> None:
        if self.state == "half_open":
            self.state = "open"
            self.opened_at = time.monotonic()
            return
        self.failures += 1
        if self.failures >= self.failure_threshold:
            self.state = "open"
            self.opened_at = time.monotonic()

    def record_success(self) -> None:
        self.failures = 0
        if self.state == "half_open":
            self.successes += 1
            if self.successes >= self.success_reset:
                self.state = "closed"


# ─────────────────────────────────────────────────────────────────────────────
# Offline heuristic fallback — deterministic finance lexicon scorer
# ─────────────────────────────────────────────────────────────────────────────

_BULL_TERMS = (
    "breakout", "accumulat", "upgrade", "outperform", "buy call", "beats consensus",
    "dividend", "payout", "dps", "earnings beat", "eps growth", "re-rating", "rerate",
    "record profit", "margin expansion", "new contract", "award", "expansion",
    "surge", "rally", "oversold bounce", "target raised", "strong outlook",
    "institutions buying", "foreign buying", "rupee stabiliz", "rupee stabilis",
    "imf approval", "policy rate cut", "recovery", "undervalued", "value pickup",
    "book building", "buyback", "divestment gains", "capacity utilization",
    "sales up", "profit up", "revenue growth", "all-time high", "green close",
)
_BEAR_TERMS = (
    "breakdown", "crash", "plunge", "default", "downgrade", "underperform",
    "sell call", "misses estimates", "profit warning", "circular debt",
    "receivables", "rupee devalu", "rupee depreciat", "gas curtailment",
    "plant shutdown", "margin compress", "margins compress", "margin pressure",
    "cost pressure", "foreign selling", "foreign outflow",
    "imf slippage", "rate hike", "tax hike", "super tax", "raid", "nab notice",
    "inquiry", "penalty", "fine imposed", "resignation", "board dispute",
    "delay in", "audit qualified", "going concern", "liquidity crunch",
    "overvalued", "profit taking", "red close", "halt", "lower lock",
)
_URGENT_TERMS = (
    "breaking", "crash", "default", "halt", "suspend", "raid", "emergency",
    "imf slippage", "resignation", " downgrade", " margin call",
)
_FUNDAMENTAL_HINTS = (
    "eps", "p/e", "payout", "dividend", "revenue", "margin", "capex", "ebitda",
    "quarterly", "annual", "results", "balance sheet", "debt", "cash flow",
    "guidance", "target price", "multiples", "book value", "roe", "%",
    "volume", "resistance", "support", "breakout", "accumulat", "institutions",
    "brokerage", "upgrade", "outlook", "forecast", "valuation", "yields",
)


def _lexicon_score(text: str) -> tuple[str, float]:
    low = text.lower()
    bull = sum(low.count(t) for t in _BULL_TERMS)
    bear = sum(low.count(t) for t in _BEAR_TERMS)
    total = bull + bear
    if total == 0:
        return "neutral", 0.50
    x = (bull - bear) / max(total, 3.0)
    direction = "bullish" if x > 0.08 else "bearish" if x < -0.08 else "neutral"
    confidence = 0.5 + 0.5 * math.tanh(1.2 * abs(x))
    return direction, round(min(max(confidence, 0.05), 0.92), 4)


def _lexicon_relevance(text: str) -> float:
    low = text.lower()
    hints = sum(1 for h in _FUNDAMENTAL_HINTS if h in low)
    nums = len(re.findall(r"\d+(?:\.\d+)?%?", text))
    base = 0.30 + 0.12 * min(hints, 4) + 0.05 * min(nums, 5)
    return round(min(base, 0.90), 4)


def _lexicon_urgency(text: str) -> str:
    low = text.lower()
    hits = sum(1 for t in _URGENT_TERMS if t in low)
    if hits >= 2:
        return "critical"
    if hits == 1:
        return "moderate"
    return "low"


# ─────────────────────────────────────────────────────────────────────────────
# Client
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class LayaClient:
    endpoint: str = C.LAYA_ENDPOINT
    timeout_s: float = C.LAYA_TIMEOUT_S
    max_retries: int = C.LAYA_MAX_RETRIES

    breaker: CircuitBreaker = field(default_factory=CircuitBreaker)
    lat_ema_ms: float = 0.0
    live_calls: int = 0
    fallback_calls: int = 0
    consecutive_failures: int = 0
    last_error: str = ""

    _http: httpx.Client | None = None

    # ---------------------------------------------------------------- setup
    def __post_init__(self) -> None:
        self._http = httpx.Client(timeout=httpx.Timeout(self.timeout_s, connect=0.8))

    # ------------------------------------------------------------- payload
    @staticmethod
    def build_payload(text: str, url: str, asset: str, sector: str,
                      als: float, post_age_s: float, source: str) -> dict:
        """Structured JSON payload — state + the three typed decision questions."""
        return {
            "engine": "system-1",
            "state": {
                "domain": "psx_equities",
                "asset": {"symbol": asset, "market": "PSX", "sector": sector},
                "temporal": {"post_age_s": round(post_age_s, 1),
                             "captured_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")},
                "author": {"als": round(als, 3), "source": source},
            },
            "questions": [
                {
                    "id": "directional_bias",
                    "type": "choice",
                    "options": ["bullish", "bearish", "neutral"],
                    "prompt": ("Classify the directional bias of this post toward the "
                               "asset strictly as one of: bullish, bearish, neutral."),
                },
                {
                    "id": "institutional_relevance",
                    "type": "noul",
                    "prompt": ("Does this post provide credible analytical insight an "
                               "institutional desk would act on — as opposed to meme "
                               "hype or spam? Return the probability in [0,1]."),
                },
                {
                    "id": "urgency_score",
                    "type": "score",
                    "scale": ["low", "moderate", "critical"],
                    "prompt": ("Rate the information urgency for an institutional desk "
                               "as one of: low, moderate, critical."),
                },
            ],
            "input": {
                "text": (text or "")[: C.LAYA_TEXT_TRUNC],
                "url": url or "",
            },
        }

    # -------------------------------------------------------------- scoring
    def score(self, text: str, url: str, asset: str, sector: str,
              als: float, post_age_s: float, source: str) -> Verdict:
        """Score one post. Live engine first (if breaker allows); fallback always
        available. NEVER raises."""
        if self.breaker.allow():
            try:
                v = self._score_live(text, url, asset, sector, als, post_age_s, source)
                self.breaker.record_success()
                self.consecutive_failures = 0
                self.live_calls += 1
                self.lat_ema_ms = (0.25 * v.latency_ms + 0.75 * self.lat_ema_ms) \
                    if self.lat_ema_ms else v.latency_ms
                return v
            except httpx.ConnectError as e:
                # engine-down is definitive → fail fast, no retry storm
                self.last_error = f"connect: {e.__class__.__name__}"
                self.breaker.record_failure()
                self.consecutive_failures += 1
            except (httpx.TimeoutException, httpx.HTTPStatusError) as e:
                self.last_error = f"{e.__class__.__name__}"
                self.consecutive_failures += 1
                if self.consecutive_failures <= self.max_retries:
                    return self._score_fallback(text, reason=self.last_error)
                self.breaker.record_failure()
            except Exception as e:                       # protocol drift, parse errors
                self.last_error = f"protocol: {e.__class__.__name__}"
                self.consecutive_failures += 1
                self.breaker.record_failure()
        else:
            self.last_error = "circuit-open"
        return self._score_fallback(text, reason=self.last_error)

    def _score_live(self, text: str, url: str, asset: str, sector: str,
                    als: float, post_age_s: float, source: str) -> Verdict:
        payload = self.build_payload(text, url, asset, sector, als, post_age_s, source)
        t0 = time.perf_counter()
        r = self._http.post(self.endpoint, json=payload)
        latency_ms = (time.perf_counter() - t0) * 1000.0
        r.raise_for_status()
        data = r.json()
        answers = _extract_answers(data)
        return Verdict(
            direction=_norm_direction(answers.get("directional_bias", "neutral")),
            dir_confidence=_clip01(float(answers.get("directional_bias_confidence", 0.6) or 0.6)),
            relevance=_clip01(float(answers.get("institutional_relevance", 0.5) or 0.5)),
            urgency=_norm_urgency(answers.get("urgency_score", "low")),
            latency_ms=round(latency_ms, 2),
            source="laya",
        )

    def _score_fallback(self, text: str, reason: str) -> Verdict:
        self.fallback_calls += 1
        d, conf = _lexicon_score(text)
        return Verdict(
            direction=d, dir_confidence=conf,
            relevance=_lexicon_relevance(text), urgency=_lexicon_urgency(text),
            latency_ms=0.0, source="fallback", fallback_reason=reason,
        )

    # --------------------------------------------------------------- health
    def health(self) -> dict:
        return {
            "endpoint": self.endpoint,
            "breaker": self.breaker.state,
            "latency_ema_ms": round(self.lat_ema_ms, 2),
            "live_calls": self.live_calls,
            "fallback_calls": self.fallback_calls,
            "last_error": self.last_error,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Tolerant answer parsing
# ─────────────────────────────────────────────────────────────────────────────

def _extract_answers(data: dict) -> dict:
    """Normalize the many shapes a decision engine might answer in into a flat
    {question_id: value} map. Tolerant, never raises."""
    out: dict = {}
    if not isinstance(data, dict):
        return out

    if isinstance(data.get("answers"), dict):
        raw = data["answers"]
        for k, v in raw.items():
            if isinstance(v, dict):
                val = v.get("value", v.get("choice", v.get("score", v.get("probability"))))
                if isinstance(val, dict):
                    val = val.get("value")
                out[k] = val
                for ck in ("confidence", "probability", "p"):
                    if ck in v:
                        out[k + "_confidence"] = v[ck]
            else:
                out[k] = v
        return out

    # flat shape: {"directional_bias": "bullish", ...}
    for k, v in data.items():
        if isinstance(v, (str, int, float)):
            out[k] = v

    # list-of-answers shape
    if isinstance(data.get("results"), list):
        for item in data["results"]:
            if isinstance(item, dict) and "id" in item:
                out[item["id"]] = item.get("value", item.get("answer"))
    return out


def _norm_direction(v) -> str:
    s = str(v or "").strip().lower()
    if "bull" in s:
        return "bullish"
    if "bear" in s:
        return "bearish"
    return "neutral"


def _norm_urgency(v) -> str:
    s = str(v or "").strip().lower()
    if "crit" in s or s in {"3", "high"}:
        return "critical"
    if "mod" in s or s in {"2", "medium"}:
        return "moderate"
    return "low"


def _clip01(x: float) -> float:
    try:
        x = float(x)
    except (TypeError, ValueError):
        return 0.5
    return min(max(x, 0.0), 1.0)


def probe_health() -> tuple[bool, float]:
    """(alive, latency_ms) — quick health probe against the engine."""
    t0 = time.perf_counter()
    try:
        with httpx.Client(timeout=httpx.Timeout(0.8, connect=0.5)) as c:
            r = c.get(C.LAYA_HEALTH_URL)
            alive = r.status_code < 500
    except Exception:
        alive = False
    return alive, (time.perf_counter() - t0) * 1000.0

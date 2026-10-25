"""
WCI — Weighted Conviction Index analytics.

Model (user-locked spec)
========================
    WCI(post, t) = P(direction) × W(institutional_relevance) × λ(age)

    P(direction)  — Laya's probability for the chosen direction; signed by
                    stance: bullish → +, bearish → −, neutral → 0.
    W(r)          = r ^ γ   (γ = 1.35 — meme noise is suppressed super-linearly;
                              r below the floor is treated as pure noise, W = 0)
    λ(age)        = 2 ^ (−age / H_eff)
                    H_eff = asset half-life × urgency multiplier
                    (critical intel decays slower — it *matters* longer)

Aggregation
===========
    B = Σ WCI over bullish posts, S = Σ WCI over bearish posts
    Net conviction CS = (B − S) / (B + S + ε) ∈ [−1, +1]
    Sentiment ratio   = B : S  (neutral posts excluded from the ratio but
                                still counted as detections)

λ is recomputed at every snapshot read — so the entire book of posts decays
every tick without any mutation cost, and the dashboard genuinely moves every
second even when no new data arrives.
"""

from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass, field, asdict

import config as C


# ─────────────────────────────────────────────────────────────────────────────
# Scored post contract
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ScoredPost:
    """A post that survived hygiene AND carries a verdict."""
    id: str
    asset: str
    source: str
    author: str
    text: str
    url: str
    created_ts: float
    scored_ts: float
    als: float
    direction: str            # bullish | bearish | neutral
    dir_confidence: float
    relevance: float
    urgency: str              # low | moderate | critical
    half_life_min: float
    engine: str               # laya | fallback

    _SDIR = {"bullish": 1.0, "bearish": -1.0, "neutral": 0.0}

    # ------------------------------------------------------------------ math
    def relevance_weight(self) -> float:
        if self.relevance < C.WCI_FLOOR_RELEVANCE:
            return 0.0
        return self.relevance ** C.WCI_GAMMA

    def effective_half_life_min(self) -> float:
        return self.half_life_min * C.URGENCY_HALF_LIFE_MULT.get(self.urgency, 1.0)

    def wci(self, now: float | None = None) -> float:
        """Signed, decay-adjusted conviction contribution at time `now`."""
        now = now if now is not None else time.time()
        age_s = max(now - self.created_ts, 0.0)
        h_eff_min = max(self.effective_half_life_min(), 1.0)
        lam = 2.0 ** (-age_s / (h_eff_min * 60.0))
        return self._SDIR[self.direction] * self.dir_confidence * self.relevance_weight() * lam

    def wci_abs(self, now: float | None = None) -> float:
        return abs(self.wci(now))

    # --------------------------------------------------------------- export
    def node_payload(self, now: float | None = None) -> dict:
        """Compact payload for the sphere node stream.

        `w` is the node's DISPLAY weight — magnitude of conviction
        (confidence × relevance-weight × λ) regardless of stance, so neutral
        posts still size/dock proportionally; direction is carried by `side`.
        """
        now = now if now is not None else time.time()
        age_s = max(now - self.created_ts, 0.0)
        h_eff_min = max(self.effective_half_life_min(), 1.0)
        lam = 2.0 ** (-age_s / (h_eff_min * 60.0))
        w = self.dir_confidence * self.relevance_weight() * lam
        return {
            "id": self.id,
            "side": self.direction,                      # bullish | bearish | neutral
            "w": round(w, 5),                            # node size/brightness driver
            "author": self.author,
            "text": self.text[:160],
            "ts": self.created_ts,
            "engine": self.engine,
            "als": round(self.als, 3),
            "url": self.url,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Per-asset ledger
# ─────────────────────────────────────────────────────────────────────────────

class ConvictionLedger:
    """Bounded, per-asset book of scored posts with on-read decay aggregation."""

    def __init__(self, asset: str, capacity: int = 1200) -> None:
        self.asset = asset
        self._posts: deque[ScoredPost] = deque(maxlen=capacity)
        self.cs_ema: float = 0.0            # momentum EMA (τ = TREND_TAU_S)
        self._ema_initialized: bool = False

    # ----------------------------------------------------------------- write
    def add(self, post: ScoredPost) -> None:
        self._posts.append(post)

    def prune(self, now: float) -> None:
        """Drop posts past the hard TTL only.

        λ-decay already drives old posts' *influence* to ~0 — pruning them
        earlier (the old 6×H_eff rule) emptied the ledger whenever real feeds
        delivered headlines older than a few hours, flattening WCI to 0 and
        the ratio to 50:50. Memory is bounded by TTL + deque capacity instead.
        """
        keep = deque(maxlen=self._posts.maxlen)
        ttl_s = C.POST_TTL_HOURS * 3600
        for p in self._posts:
            if now - p.created_ts > ttl_s:
                continue
            keep.append(p)
        self._posts = keep

    # ------------------------------------------------------------------ read
    def aggregate(self, now: float | None = None) -> dict | None:
        """Decay-adjusted aggregate snapshot. λ recomputed per read → the score
        moves every tick even with zero new posts."""
        now = now if now is not None else time.time()
        if not self._posts:
            return None

        b_sum = s_sum = 0.0
        bull_n = bear_n = neutral_n = 0
        for p in self._posts:
            w = p.wci(now)
            if p.direction == "bullish":
                b_sum += w
                bull_n += 1
            elif p.direction == "bearish":
                s_sum += -w
                bear_n += 1
            else:
                neutral_n += 1

        denom = b_sum + s_sum
        eps = 1e-9
        cs = (b_sum - s_sum) / (denom + eps) if denom > eps else 0.0
        cs = max(min(cs, 1.0), -1.0)

        # momentum EMA — advanced by the caller once per tick
        return {
            "asset": self.asset,
            "cs": round(cs, 4),
            "bull_sum": round(b_sum, 4),
            "bear_sum": round(s_sum, 4),
            "bull_pct": round(100.0 * b_sum / denom, 1) if denom > eps else 50.0,
            "bear_pct": round(100.0 * s_sum / denom, 1) if denom > eps else 50.0,
            "bull_n": bull_n, "bear_n": bear_n, "neutral_n": neutral_n,
            "detections": len(self._posts),
            "momentum": round(cs - self.cs_ema, 4),
        }

    def advance_momentum(self, cs: float, dt_s: float) -> None:
        """First-order low-pass toward the current conviction (τ = TREND_TAU_S)."""
        tau = max(C.TREND_TAU_S, 1.0)
        alpha = dt_s / (tau + dt_s)
        self.cs_ema = self.cs_ema * (1 - alpha) + cs * alpha

    def nodes(self, now: float, limit: int = 80) -> list[dict]:
        """Latest scored posts for the sphere's node stream (newest first)."""
        out = [p.node_payload(now) for p in list(self._posts)[-limit:]]
        out.reverse()
        return out

    def top_signals(self, now: float, limit: int = 6) -> list[dict]:
        """Highest current-WCI posts — what an institutional desk sees first."""
        ranked = sorted(self._posts, key=lambda p: p.wci_abs(now), reverse=True)[:limit]
        ranked.sort(key=lambda p: p.scored_ts, reverse=True)
        return [p.node_payload(now) for p in ranked]

    @property
    def size(self) -> int:
        return len(self._posts)


# ─────────────────────────────────────────────────────────────────────────────
# FCI — Fused Conviction Index (v3 "Deep Field")
#
# The WCI answers "what does the CHATTER think?". The FCI answers "what
# should a desk with both chatter AND quantitative models believe right now?":
#
#     m_sig   = tanh(expected 5-day model return / (5-day vol))  — models' signed
#               edge in units the vol regime makes comparable
#     align   = 0.5 + 0.5·tanh(4·CS·m_sig)   — agreement amplifier: chatter and
#               models pointing the SAME way reinforce, opposite ways dilute
#     cascade = Hawkes branching ratio > risk threshold → chatter is
#               self-exciting (contagion), conviction is DAMPENED not amplified
#     vol_damp  = extreme realized-vol percentile → both signals unreliable,
#               conviction shrinks
#
#     FCI = clamp( (w_s·CS + w_m·m_sig) · align · (1 − cascade_pen) · vol_damp )
#
# The breakdown is returned for the relational-graph panel: every node weight
# the 3D graph renders comes from this dict.
# ─────────────────────────────────────────────────────────────────────────────

def fuse_conviction(cs: float, models: dict | None, market: dict | None) -> dict:
    """Read-time fusion of sentiment + model zoo → FCI + node weights."""
    cs = max(min(cs or 0.0, 1.0), -1.0)

    m_sig = 0.0
    exp5 = 0.0
    vol5 = 0.0
    models_present = False
    if models:
        ens = models.get("ensemble") or {}
        exp5 = float(ens.get("expected_5d_pct") or 0.0)
        vol = models.get("vol") or {}
        vol_cc = float(vol.get("cc") or 0.0)
        vol5 = 100.0 * vol_cc * math.sqrt(5.0 / 252.0)   # annualized → 5-day
        if vol5 < 0.5:
            vol5 = 0.5
        m_sig = math.tanh(exp5 / vol5)
        models_present = bool(ens)

    # alignment ∈ [0,1]: 1 → both signals agree in sign, 0.5 → one is ~0
    align = 0.5 + 0.5 * math.tanh(4.0 * cs * m_sig)

    # cascade guard (Hawkes contagion)
    branching = 0.0
    cascade_pen = 0.0
    if models:
        hk = models.get("hawkes") or {}
        branching = float(hk.get("branching") or 0.0)
        if branching > C.FCI_BRANCHING_RISK:
            cascade_pen = min(
                (branching - C.FCI_BRANCHING_RISK) /
                max(1.0 - C.FCI_BRANCHING_RISK, 1e-6) * C.FCI_CASCADE_PENALTY,
                C.FCI_CASCADE_PENALTY)

    # vol regime dampening
    vol_pctl = 0.5
    vol_damp = 1.0
    if models:
        vol_pctl = float((models.get("vol") or {}).get("pctl", 0.5) or 0.5)
        if vol_pctl >= C.FCI_VOL_EXTREME_PCTL or vol_pctl <= 0.04:
            vol_damp = C.FCI_VOL_DAMP

    raw = (C.FCI_W_SENTIMENT * cs + C.FCI_W_MODELS * m_sig) * align
    fci = raw * (1.0 - cascade_pen) * vol_damp
    fci = max(min(fci, 1.0), -1.0)

    return {
        "fci": round(fci, 4),
        "raw": round(raw, 4),
        "cs": round(cs, 4),
        "m_sig": round(m_sig, 4),
        "expected_5d_pct": round(exp5, 3),
        "vol5_pct": round(vol5, 3),
        "align": round(align, 4),
        "cascade_pen": round(cascade_pen, 4),
        "branching": round(branching, 3),
        "vol_pctl": round(vol_pctl, 3),
        "vol_damp": round(vol_damp, 3),
        "w_sentiment": C.FCI_W_SENTIMENT,
        "w_models": C.FCI_W_MODELS,
        "models_present": models_present,
    }

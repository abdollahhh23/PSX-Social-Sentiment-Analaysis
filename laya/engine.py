"""
Laya System 1 — the decision core behind POST /v1/systemone.

Modes (env LAYA_MODE):
  heuristic  (default)  deterministic finance lexicon + discourse rules.
                        Sub-millisecond, zero external dependencies, no
                        network calls. Same text in → same verdict out.
  finbert               HuggingFace ProsusAI/finbert transformer head for the
                        directional question; relevance/urgency stay on the
                        heuristic core. Requires torch + transformers
                        (see Dockerfile.finbert). Falls back to heuristic
                        automatically if the model cannot load.

The three typed questions answered (matches the radar's client contract):
  directional_bias         choice   bullish | bearish | neutral  (+ confidence)
  institutional_relevance  noul     P(institutional insight) in [0,1]
  urgency_score            score    low | moderate | critical
"""

from __future__ import annotations

import math
import os
import re
import time

VERSION = "1.1.0"   # lexicon v2 — tuned on real PSX wire/market-report vocabulary
MODE = os.getenv("LAYA_MODE", "heuristic").strip().lower()

# ─────────────────────────────────────────────────────────────────────────────
# Lexicons — lowercase stems, matched on word boundaries.
# Weights are roughly "how hard does this phrase push the needle".
# ─────────────────────────────────────────────────────────────────────────────

_BULL = {
    # momentum / price action
    "breakout": 1.4, "breaks out": 1.4, "broke out": 1.2, "rally": 1.1, "rallies": 1.1,
    "surge": 1.2, "surged": 1.2, "rebound": 0.9, "rebounds": 0.9, "oversold bounce": 1.0,
    "upper lock": 0.9, "hit upper circuit": 1.0, "all-time high": 1.2, "all time high": 1.2,
    "green close": 0.8, "uptrend": 0.9, "higher highs": 0.9,
    # market-report verbs (v2 — the bread and butter of real PSX coverage)
    "gain": 0.8, "gained": 0.8, "rise": 0.8, "rose": 0.8, "climb": 0.8,
    "jump": 0.9, "improve": 0.7, "improved": 0.7, "up ": 0.6,
    "points higher": 0.8, "points up": 0.7,
    "closes higher": 0.9, "closed higher": 0.9, "ends higher": 0.9,
    "settles higher": 0.9, "settle higher": 0.9, "higher close": 0.8,
    "record high": 1.1, "fresh high": 0.9, "highest since": 0.7, "highest level": 0.7,
    "snaps losing streak": 0.9, "winning streak": 0.8, "extends gains": 0.9,
    "picks up": 0.6, "picked up": 0.6,
    # flows / positioning
    "institutional buying": 1.1, "buying interest": 0.9, "buying momentum": 0.9,
    "value buying": 0.8, "buying spree": 0.9, "net buying": 0.9, "smart money": 0.7,
    "inflow": 0.8, "positive close": 0.8, "bullish": 0.9, "bull run": 0.9,
    "risk-on": 0.7, "upbeat": 0.7, "optimism": 0.6,
    # analyst / institutional actions
    "upgrade": 1.2, "upgraded": 1.2, "outperform": 1.1, "overweight": 1.0,
    "buy call": 1.3, "buy rating": 1.1, "buy recommendation": 1.2,
    "target raised": 1.3, "target price raised": 1.3, "re-rating": 1.1, "rerate": 1.1,
    "institutions buying": 1.2, "foreign buying": 1.2, "accumulat": 1.0,
    "book building": 0.7, "buyback": 1.0, "buy back": 1.0,
    # fundamentals
    "beats consensus": 1.5, "beat estimates": 1.4, "beats estimates": 1.4,
    "beats forecast": 1.3, "beats expectations": 1.3, "tops estimates": 1.3,
    "above estimates": 1.0, "strong results": 1.1, "record sales": 1.1,
    "record earnings": 1.2, "earnings beat": 1.4, "eps growth": 1.1, "eps up": 1.0,
    "record profit": 1.5, "profit grows": 1.0, "profit surges": 1.2,
    "sales grow": 0.9, "sales rise": 1.0, "sales up": 1.0,
    "margin expansion": 1.3, "margins expand": 1.3, "new contract": 1.1, "contract win": 1.2,
    "award": 0.8, "expansion": 0.7, "capex plan": 0.6, "capacity utilization": 0.7,
    "dividend": 0.7, "payout": 0.6, "dps": 0.5, "bonus issue": 0.9, "bonus share": 0.9,
    "announces dividend": 1.0, "declares dividend": 1.0, "dividend yield": 0.8,
    "dividend increase": 0.9, "raises payout": 0.9, "increases payout": 0.9,
    "profit up": 1.1, "revenue growth": 1.0, "revenue up": 1.0,
    "net profit": 0.5, "pat up": 1.0, "order book": 0.9, "demand pick": 0.8,
    "turnaround": 1.1, "deleverag": 0.9, "debt reduction": 0.9, "undervalued": 0.9,
    "deep value": 0.9, "value pickup": 0.9, "strong outlook": 1.2, "guidance raised": 1.3,
    # macro / PK-specific
    "rupee stabiliz": 0.9, "rupee stabilis": 0.9, "rupee appreciat": 1.0,
    "imf approval": 1.1, "imf tranche": 1.0, "imf review passed": 1.2,
    "policy rate cut": 1.2, "rate cut": 1.1, "rates cut": 1.1,
    "cuts policy rate": 1.2, "lowers rates": 1.0,
    "scra approved": 0.9, "recovery": 0.8, "recovery in": 0.7,
    "gets approval": 0.7, "receives approval": 0.7,
    "gas discovery": 1.1, "oil discovery": 1.1, "mou signed": 0.8,
    "agreement signed": 0.8, "divestment gains": 0.8, "refinanced at lower": 0.8,
}

_BEAR = {
    # momentum / price action
    "breakdown": 1.3, "crash": 1.4, "crashes": 1.4, "plunge": 1.3, "plunges": 1.3,
    "sink": 1.0, "sinks": 1.0, "slump": 1.1, "tumble": 1.1, "free fall": 1.3,
    "lower lock": 1.0, "hit lower circuit": 1.1, "red close": 0.8, "halt": 0.9,
    "halted": 0.9, "trading halted": 1.1, "profit taking": 0.8, "downtrend": 0.9,
    # market-report verbs (v2)
    "fall": 0.9, "fell": 0.9, "drop": 0.8, "dropped": 0.8, "decline": 0.8,
    "slip": 0.8, "slide": 0.8, "weaken": 0.8, "down ": 0.6,
    "points lower": 0.9, "points down": 0.8,
    "closes lower": 0.9, "closed lower": 0.9, "ends lower": 0.9,
    "settles lower": 0.9, "settle lower": 0.9, "lower close": 0.8,
    "fresh low": 0.9, "record low": 1.0, "lowest since": 0.7, "new low": 0.8,
    "extends losses": 1.0, "losing streak": 0.8,
    "bleeds": 0.9, "bloodbath": 1.4, "meltdown": 1.3,
    # flows / positioning
    "selling pressure": 1.0, "selloff": 1.2, "sell-off": 1.2, "profit booking": 0.8,
    "net selling": 0.9, "panic selling": 1.3, "selling spree": 0.9,
    "outflow": 0.8, "bearish": 0.9, "bear run": 0.9, "risk-off": 0.8,
    # analyst / institutional actions
    "downgrade": 1.2, "downgraded": 1.2, "underperform": 1.1, "sell call": 1.3,
    "sell rating": 1.1, "target cut": 1.2, "target price cut": 1.2,
    "foreign selling": 1.2, "foreign outflow": 1.1,
    # fundamentals
    "misses estimates": 1.4, "missed estimates": 1.4, "misses consensus": 1.4,
    "misses forecast": 1.3, "misses expectations": 1.3, "below estimates": 1.1,
    "below consensus": 1.2, "weak results": 1.0, "disappointing results": 1.2,
    "earnings miss": 1.3, "profit warning": 1.4, "net loss": 1.1, "loss widens": 1.2,
    "posts loss": 1.2, "declares loss": 1.2, "swings to loss": 1.3,
    "sales fall": 1.0, "profit falls": 1.1, "profit down": 1.1, "revenue down": 1.0,
    "cuts forecast": 1.2, "lowers guidance": 1.2, "cuts payout": 0.9,
    "skips dividend": 1.0, "no dividend": 0.8, "withholds dividend": 1.0,
    "loss making": 1.0, "margin compress": 1.3, "margins compress": 1.3,
    "margin pressure": 1.1, "margin squeeze": 1.2, "cost pressure": 1.0,
    "impairment": 0.9, "write-off": 1.0, "write off": 1.0, "layoff": 0.9,
    "plant shutdown": 1.1, "shutdown": 0.8, "delayed": 0.6, "delay in": 0.7,
    "audit qualified": 1.1, "qualified opinion": 1.0, "going concern": 1.4,
    "liquidity crunch": 1.3, "cash crunch": 1.2, "overvalued": 0.9,
    "stretched valuation": 0.9, "circular debt": 1.1, "receivable": 0.6,
    "shrink": 0.7,
    # governance / legal
    "default": 1.5, "defaults": 1.4, "defaulted": 1.4, "payment default": 1.4,
    "bond default": 1.4, "penalty": 0.9, "fined": 0.8, "fine imposed": 0.9,
    "raid": 1.0, "nab notice": 1.1, "fbr notice": 0.9, "secp notice": 1.0,
    "show-cause": 0.9, "inquiry": 0.7, "investigation": 0.8,
    "arrest": 1.0, "crackdown": 1.0,
    "resignation": 1.0, "resigns": 0.9, "cfo resign": 1.2, "ceo resign": 1.2,
    "board dispute": 1.0, "governance concern": 0.9,
    # macro / PK-specific
    "rupee devalu": 1.2, "rupee depreciat": 1.1, "devaluation": 1.1,
    "imf slippage": 1.3, "imf review failed": 1.3, "rate hike": 1.2,
    "rates hiked": 1.2, "policy rate hike": 1.3, "tax hike": 1.1, "super tax": 1.0,
    "new taxes": 0.9, "gas curtailment": 1.0, "gas shortage": 0.9,
    "currency crisis": 1.2, "import restrictions": 0.8, "lc backlog": 1.0,
}

_URGENT = (
    "breaking", "just in", "alert", "urgent", "flash", "crash", "default",
    "halt", "trading halted", "suspend", "suspended", "raid", "emergency",
    "imf slippage", "resignation", " downgrade", "margin call", "lower circuit",
    "secp notice", "show-cause", "going concern",
    "plunge", "plunges", "bloodbath", "panic", "meltdown", "arrest",
)

_HINTS = (
    "eps", "p/e", "pe ratio", "payout", "dividend", "revenue", "margin", "capex",
    "ebitda", "quarterly", "annual", "result", "results", "balance sheet", "debt",
    "cash flow", "guidance", "target price", "multiples", "book value", "roe",
    "roa", "%", "volume", "volumes", "resistance", "support", "breakout",
    "accumulat", "institutions", "brokerage", "outlook", "forecast", "valuation",
    "yield", "yields", "psx", "kse", "dps", "kibor", "t-bill", "yoy", "qoq",
    "q1", "q2", "q3", "q4", "half yearly", "earnings", "financials", "growth",
    "index", "benchmark", "turnover", "session", "intraday", "close", "circuit",
    "points", "streak", "inflow", "outflow",
)

_INSIGHT_VERBS = (
    "announced", "reported", "disclosed", "filed", "approved", "signed",
    "released", "briefing", "notified", "submitted",
)

_MEME = (
    "moon", "rocket", "pump", "pumping", "dump", "yolo", "hot tip", "sure shot",
    "guaranteed profit", "guaranteed return", "jackpot", "double your money",
    "insider tip", "free tips", "signal group", "whatsapp group", "join now",
    "hurry", "limited offer", "100% sure",
)

_NEGATORS = {
    "not", "no", "never", "isnt", "isn't", "arent", "aren't", "wont", "won't",
    "doesnt", "doesn't", "dont", "don't", "without", "unlikely", "denied",
    "denies", "refuted", "refutes", "dismissed", "despite",
}
_BOOSTERS = {
    "very", "strongly", "sharply", "massively", "significantly", "record",
    "massive", "huge", "major", "confirm", "confirmed", "official", "officially",
}
_DAMPENERS = {
    "maybe", "might", "possibly", "rumor", "rumour", "rumours", "speculation",
    "speculative", "unconfirmed", "hearsay", "allegedly", "reportedly",
}

_TERM_RE_CACHE: dict[str, re.Pattern] = {}


def _term_re(term: str) -> re.Pattern:
    pat = _TERM_RE_CACHE.get(term)
    if pat is None:
        pat = re.compile(r"(?<![a-z])" + re.escape(term) + r"[a-z]*")
        _TERM_RE_CACHE[term] = pat
    return pat


def _scan(low: str) -> list[tuple[str, float, str]]:
    """Find stance terms with negation/booster/dampener context multipliers.

    Returns [(term, signed_weight, stance)] — negation flips the sign and
    dampens, boosters amplify, rumor-language dampens. Max 3 hits per term.
    """
    hits: list[tuple[str, float, str]] = []
    for stance, table in (("bull", _BULL), ("bear", _BEAR)):
        for term, weight in table.items():
            count = 0
            for m in _term_re(term).finditer(low):
                if count >= 3:
                    break
                count += 1
                window = low[max(0, m.start() - 30):m.start()].split()[-3:]
                mult = 1.0
                if any(t in _NEGATORS for t in window):
                    mult *= -0.80
                if any(t in _BOOSTERS for t in window):
                    mult *= 1.35
                if any(t in _DAMPENERS for t in window):
                    mult *= 0.60
                hits.append((term, weight * mult, stance))
    return hits


def _direction(hits: list[tuple[str, float, str]]) -> tuple[str, float, float]:
    net = sum(w for _, w, s in hits if s == "bull") - sum(w for _, w, s in hits if s == "bear")
    norm = math.tanh(net / 2.4)
    if norm > 0.10:
        return "bullish", round(min(0.52 + 0.46 * abs(norm), 0.96), 4), net
    if norm < -0.10:
        return "bearish", round(min(0.52 + 0.46 * abs(norm), 0.96), 4), net
    return "neutral", 0.50, net


def _relevance(low: str, clean: str) -> float:
    hints = sum(1 for h in _HINTS if h in low)
    nums = len(re.findall(r"\d+(?:\.\d+)?%?", clean))
    verbs = sum(1 for v in _INSIGHT_VERBS if v in low)
    meme = sum(1 for m in _MEME if m in low)
    letters = [c for c in clean if c.isalpha()]
    caps_ratio = sum(1 for c in letters if c.isupper()) / max(len(letters), 1)
    r = (0.28 + 0.11 * min(hints, 4) + 0.05 * min(nums, 5)
         + 0.06 * min(verbs, 3) - 0.09 * min(meme, 3))
    if caps_ratio > 0.30:                       # SHOUTING → less institutional
        r -= 0.08
    return round(min(max(r, 0.04), 0.95), 4)


def _urgency(low: str) -> tuple[str, float]:
    hits = [t for t in _URGENT if t in low]
    if len(hits) >= 2:
        return "critical", round(min(0.55 + 0.12 * len(hits), 0.92), 4)
    if len(hits) == 1:
        return "moderate", 0.60
    return "low", 0.50


# ─────────────────────────────────────────────────────────────────────────────
# Optional FinBERT head (LAYA_MODE=finbert)
# ─────────────────────────────────────────────────────────────────────────────

_FINBERT = None
_FINBERT_TRIED = False


def _finbert_pipeline():
    global _FINBERT, _FINBERT_TRIED
    if _FINBERT_TRIED:
        return _FINBERT
    _FINBERT_TRIED = True
    try:
        from transformers import pipeline as hf_pipeline
        _FINBERT = hf_pipeline("text-classification", model="ProsusAI/finbert", top_k=None)
        print("[laya] FinBERT loaded — transformer mode active")
    except Exception as e:                                    # noqa: BLE001
        print(f"[laya] FinBERT unavailable ({e.__class__.__name__}) — staying heuristic")
        _FINBERT = None
    return _FINBERT


def _finbert_score(text: str) -> tuple[str, float] | None:
    p = _finbert_pipeline()
    if p is None:
        return None
    try:
        res = p((text or "")[:1500])
        flat = res[0] if res and isinstance(res[0], list) else res
        scores = {d["label"].lower(): float(d["score"]) for d in flat}
        pos, neg, neu = (scores.get("positive", 0.0),
                         scores.get("negative", 0.0),
                         scores.get("neutral", 0.0))
        if neu >= pos and neu >= neg:
            return ("neutral", round(neu, 4))
        if pos >= neg:
            return ("bullish", round(pos, 4))
        return ("bearish", round(neg, 4))
    except Exception:                                         # noqa: BLE001
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def active_mode() -> str:
    if MODE == "finbert" and _FINBERT is not None:
        return "finbert"
    return "heuristic"


def score(text: str) -> dict:
    """Score one post → the three typed answers + rationale. Never raises."""
    t0 = time.perf_counter()
    clean = " ".join((text or "").split())
    low = clean.lower()

    hits = _scan(low) if low else []
    direction, conf, net = _direction(hits)
    relevance = _relevance(low, clean)
    urgency, urgency_conf = _urgency(low)

    if MODE == "finbert" and clean:
        fb = _finbert_score(clean)
        if fb is not None:
            direction, conf = fb

    matched = [t for t, w, s in hits if abs(w) > 0][:8]
    return {
        "direction": direction,
        "confidence": conf,
        "relevance": relevance,
        "urgency": urgency,
        "urgency_conf": urgency_conf,
        "net": round(net, 3),
        "matched": matched,
        "latency_ms": round((time.perf_counter() - t0) * 1000, 2),
    }


if __name__ == "__main__":                    # quick self-test: python engine.py
    for t in (
        "OGDC announces record profit, margin expansion and a fat dividend payout",
        "HUBC faces circular debt, margin pressure and continued foreign selling",
        "TO THE MOON boys!! guaranteed profit, join my whatsapp group NOW",
        "E&P volumes look steady at current levels",
    ):
        print(score(t))

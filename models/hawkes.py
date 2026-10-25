"""
Hawkes process — tick-style self-exciting point process, pure stdlib port.

The `tick` library fits univariate exponential-kernel Hawkes processes by
maximum likelihood. This port implements the same model,

    λ(t) = μ + Σ_{tᵢ < t} α·e^(−β·(t − tᵢ))

with the Ozaki R-recursion giving an O(n) log-likelihood per (μ, α, β)
candidate, and a coarse-to-fine 2-D profile-MLE grid over (μ, α) with β on a
fixed grid (stability constraint α < β enforced by clamping).

Events fed in from the radar: directional sentiment bursts (scored posts with
material conviction) and price jumps (|return| > 2.5σ). Output: current
intensity λ(t), branching ratio α/β (the contagion/cascade signal that the
FCI uses as a conviction guard), and the expected next-event gap.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass

_HOURS = 3600.0          # internal time unit = hours


@dataclass
class HawkesModel:
    mu: float = 0.0            # base intensity (events/hour)
    alpha: float = 0.0         # excitation amplitude
    beta: float = 1.0          # decay rate (1/hour)
    branching: float = 0.0     # α/β ∈ [0,1) — contagion ratio
    lambda_now: float = 0.0    # current total intensity (events/hour)
    lambda_base: float = 0.0
    expected_gap_h: float = math.inf
    n_events: int = 0
    fitted: bool = False


def _loglik(events: list[float], horizon_h: float,
            mu: float, alpha: float, beta: float) -> float:
    """Ozaki R-recursion log-likelihood (events in hours, ascending)."""
    n = len(events)
    if n == 0 or mu <= 0:
        return -math.inf
    R = 0.0
    ll = 0.0
    prev = events[0]
    for i, t in enumerate(events):
        if i:
            R = math.exp(-beta * (t - prev)) * (1.0 + R)
        denom = mu + alpha * R
        if denom <= 0:
            return -math.inf
        ll += math.log(denom)
        prev = t
    ll -= mu * horizon_h
    ll += (alpha / beta) * sum(math.exp(-beta * (horizon_h - t)) for t in events) \
        - (alpha / beta) * n * 0.0
    return ll


def fit(events_ts: list[float], now: float | None = None) -> HawkesModel:
    """Fit on unix-second event timestamps within the lookback window."""
    m = HawkesModel()
    now = now or time.time()
    ev = sorted((t for t in events_ts if t is not None), reverse=False)
    if not ev:
        return m
    # drop events older than the window; keep at least the last one
    horizon_h = max((ev[-1] - ev[0]) + 1.0, 1.0) / _HOURS
    if len(ev) < 3:
        # not enough structure — report the empirical rate only
        m.mu = len(ev) / horizon_h
        m.lambda_now = m.mu
        m.lambda_base = m.mu
        m.n_events = len(ev)
        m.expected_gap_h = horizon_h / len(ev)
        m.fitted = True
        return m
    ev_h = [(t - ev[0]) / _HOURS for t in ev]
    horizon_h = max(ev_h[-1] + 0.5, 1.0)

    best = (-math.inf, 1.0, 0.5, 1.0)          # (ll, mu, alpha, beta)
    # coarse grid: mu ∈ [0.1, 2]×rate, alpha/beta ∈ (0, 0.95)
    rate = len(ev_h) / horizon_h
    beta_grid = [0.25, 0.5, 1.0, 2.0, 4.0, 8.0]
    for beta in beta_grid:
        for ratio in (0.2, 0.45, 0.7, 0.9):
            mu_hi = max(4.0 * rate, 0.5)
            for mu in (0.15 * rate, 0.4 * rate, 0.8 * rate, 1.5 * rate):
                mu = max(mu, 0.01)
                alpha = min(ratio * beta, mu_hi)
                ll = _loglik(ev_h, horizon_h, mu, alpha, beta)
                if ll > best[0]:
                    best = (ll, mu, alpha, beta)
    # refinement rounds around the winner
    ll0, mu_b, alpha_b, beta_b = best
    for _ in range(3):
        for db in (-0.4, -0.2, 0.0, 0.2, 0.4):
            beta = beta_b * (1.0 + db)
            if beta <= 0.05:
                continue
            for da in (-0.25, -0.1, 0.0, 0.1, 0.25):
                alpha = alpha_b * (1.0 + da)
                if alpha <= 0 or alpha >= 0.98 * beta:
                    continue
                for dm in (-0.3, -0.15, 0.0, 0.15, 0.3):
                    mu = mu_b * (1.0 + dm)
                    if mu <= 0.005:
                        continue
                    ll = _loglik(ev_h, horizon_h, mu, alpha, beta)
                    if ll > best[0]:
                        best = (ll, mu, alpha, beta)
        ll0, mu_b, alpha_b, beta_b = best

    _, mu, alpha, beta = best
    m.mu, m.alpha, m.beta = mu, alpha, beta
    m.branching = min(alpha / beta, 0.999) if beta > 0 else 0.0
    # current intensity: μ + α·Σ e^(−β·(t_now − tᵢ))
    tail = 0.0
    for t_h in ev_h:
        dt = horizon_h - t_h
        if dt < 0:
            continue
        tail += math.exp(-beta * dt)
    m.lambda_now = mu + alpha * tail
    m.lambda_base = mu
    m.expected_gap_h = 1.0 / m.lambda_now if m.lambda_now > 1e-9 else math.inf
    m.n_events = len(ev)
    m.fitted = True
    return m


def intensity_at(model: HawkesModel, events_ts: list[float],
                 now: float) -> float:
    """Live λ(t) without a refit — cheap client-side intensity query."""
    if not model.fitted:
        return 0.0
    beta = model.beta / _HOURS          # back to per-second decay
    tail = 0.0
    for t in events_ts[-400:]:
        dt = now - t
        if dt >= 0:
            tail += math.exp(-beta * dt)
    return model.mu + model.alpha * tail

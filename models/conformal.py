"""
Split-conformal prediction bands — MAPIE-style, pure stdlib port.

MAPIE (scikit-learn-contrib) wraps any estimator with distribution-free
prediction intervals via conformal prediction. The split (inductive) variant
used here is the lightest and the one MAPIE exposes as `SplitConformalRegressor`
behavior:

  1. hold out the last K one-step-ahead residuals of a base forecaster as the
     calibration set
  2. nonconformity score sᵢ = |residualᵢ|
  3. q̂ = the ⌈(n+1)(1−α)⌉/n empirical quantile of the scores
  4. interval = pred ± q̂·√h   (√h widens the band across horizons, the
     standard random-walk scaling)

Coverage — the empirical fraction of recent points whose realized value fell
inside the band — is tracked on a rolling window and surfaced in the UI, so
the band can be trusted or doubted at a glance.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field

import config as C


@dataclass
class Band:
    p10: float
    p50: float
    p90: float


class Conformal:
    """Rolling split-conformal calibrator over one forecaster's residuals."""

    def __init__(self, alpha: float = C.CONFORMAL_ALPHA,
                 window: int = C.CONFORMAL_WINDOW) -> None:
        self.alpha = alpha
        self.window = window
        self._scores: deque[float] = deque(maxlen=window)
        self._inside: deque[bool] = deque(maxlen=120)
        self.q_hat: float = 0.0
        self.fitted: bool = False

    # ── calibration ─────────────────────────────────────────────────────────
    def calibrate(self, residuals: list[float]) -> float:
        """Recompute q̂ from the trailing residual window (call on refit)."""
        scores = sorted(abs(r) for r in residuals[-self.window:] if
                        isinstance(r, (int, float)) and math.isfinite(r))
        n = len(scores)
        if n < 8:
            self.q_hat = 0.0
            self.fitted = False
            return 0.0
        rank = min(n, max(1, math.ceil((n + 1) * (1.0 - self.alpha)))) - 1
        self.q_hat = scores[rank]
        self.fitted = True
        return self.q_hat

    # ── band construction ───────────────────────────────────────────────────
    def band(self, pred: float, horizon: int) -> Band:
        spread = self.q_hat * math.sqrt(max(horizon, 1))
        return Band(p10=pred - spread, p50=pred, p90=pred + spread)

    # ── coverage tracking ───────────────────────────────────────────────────
    def observe(self, pred: float, realized: float) -> None:
        if not self.fitted:
            return
        self._inside.append(abs(realized - pred) <= self.q_hat)

    @property
    def coverage(self) -> float:
        if not self._inside:
            return 0.0
        return sum(1 for ok in self._inside if ok) / len(self._inside)

    @property
    def coverage_n(self) -> int:
        return len(self._inside)

    # ── export ──────────────────────────────────────────────────────────────
    def snapshot(self) -> dict:
        return {
            "q_hat": round(self.q_hat, 6),
            "alpha": self.alpha,
            "coverage": round(self.coverage, 3),
            "coverage_n": self.coverage_n,
            "fitted": self.fitted,
        }

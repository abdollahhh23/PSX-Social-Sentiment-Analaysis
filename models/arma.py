"""
ARMA(1,1) — conditional least squares, pure stdlib.

    y_t = c + φ·y_{t−1} + ε_t + θ·ε_{t−1}

Estimated by the textbook conditional-SSE grid: for every (φ, θ) pair the
recursion ε_t = y_t − c − φ·y_{t−1} − θ·ε_{t−1} is rolled forward with ε₀ = 0
and c fixed at the mean-adjusted optimum for that φ (closed form for a fixed
φ, θ pair). The joint grid is small (21 φ × 39 θ) and each pass is O(n), so a
fit costs a few milliseconds on a 260-point series — comfortably inside the
1 s tick's shadow. Forecast: iterated one-step recursion with ε fixed at its
unconditional expectation (0) beyond the sample.

Port note: this mirrors the ARMA path of classic statsmodels/lost-stats
ARMA workflows (conditional least squares variant) with zero dependencies.
"""

from __future__ import annotations

import math

from dataclasses import dataclass, field


@dataclass
class ArmaModel:
    c: float = 0.0
    phi: float = 0.0
    theta: float = 0.0
    sigma: float = 0.0
    sse: float = math.inf
    residuals: list[float] = field(default_factory=list)
    fitted: bool = False


def _cond_sse(y: list[float], c: float, phi: float, theta: float) -> tuple[float, list[float]]:
    eps = 0.0
    sse = 0.0
    residuals: list[float] = []
    for t in range(1, len(y)):
        e = y[t] - c - phi * y[t - 1] - theta * eps
        residuals.append(e)
        sse += e * e
        eps = e
    return sse, residuals


def fit(y: list[float]) -> ArmaModel:
    """Fit ARMA(1,1) on a return series (y = log returns). Falls back to a
    pure-AR(1) optimum if the MA grid finds nothing better than θ=0."""
    m = ArmaModel()
    n = len(y)
    if n < 12:
        return m
    mean_y = sum(y) / n
    best = (math.inf, 0.0, 0.0, 0.0, [])
    phi_grid = [round(-0.90 + 0.09 * i, 3) for i in range(21)]
    theta_grid = [round(-0.95 + 0.05 * i, 3) for i in range(39)]
    for phi in phi_grid:
        # for fixed φ the conditional mean c that nulls the intercept is the
        # OLS solution on the constant — closed form:
        num = den = 0.0
        for t in range(1, n):
            num += y[t] - phi * y[t - 1]
            den += 1.0
        c0 = num / den if den else 0.0
        for theta in theta_grid:
            sse, res = _cond_sse(y, c0, phi, theta)
            if sse < best[0]:
                best = (sse, c0, phi, theta, res)
    sse, c, phi, theta, residuals = best
    if sse == math.inf:
        return m
    m.c, m.phi, m.theta, m.sse = c, phi, theta, sse
    m.residuals = residuals
    m.sigma = math.sqrt(sse / max(n - 1, 1))
    m.fitted = True
    return m


def forecast_logret(model: ArmaModel, y: list[float], horizon: int) -> list[float]:
    """Iterated h-step forecast of log returns (ε → 0 beyond the sample)."""
    if not model.fitted or not y:
        return [0.0] * max(horizon, 0)
    eps = model.residuals[-1] if model.residuals else 0.0
    last = y[-1]
    out: list[float] = []
    for _ in range(horizon):
        nxt = model.c + model.phi * last + model.theta * eps
        out.append(nxt)
        last = nxt
        eps = 0.0                       # E[ε future] = 0
    return out


def forecast_path(last_price: float, logrets: list[float]) -> list[float]:
    """Cumulative price path from forecast log returns."""
    price = last_price
    path = []
    for r in logrets:
        price *= math.exp(r)
        path.append(round(price, 4))
    return path

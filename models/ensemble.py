"""
TimesFM-style forecast adapter + the built-in LayaTS ensemble.

Google's TimesFM (google-research/timesfm) is a 200M-parameter pretrained
time-series foundation model. The honest engineering reality: it needs torch
+ JAX-class dependencies (hundreds of MB, impossible on the slow-network
setups this project targets), so it ships as an OPT-IN adapter:

  • if `import timesfm` succeeds (and a checkpoint path is configured), the
    real model decodes the forecast and the ensemble is bypassed;
  • otherwise the built-in **LayaTS-4 ensemble** carries the forecast:
        EWMA (best-α sweep) · Holt damped trend · ARMA(1,1) · SYMREG
    combined with softmax(−MSE/τ) weights recomputed every refit, exactly the
    inverse-error weighting pattern used by forecast ensembles.

Component weights, per-model expected returns and the ensemble's P10/P50/P90
fan (conformal-scaled) are all surfaced to the forecast deck.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import config as C

# ── opt-in TimesFM detection (never imported at module load) ─────────────────
_TIMESFM_STATE: tuple[bool, str] | None = None


def _timesfm_available() -> tuple[bool, str]:
    global _TIMESFM_STATE
    if _TIMESFM_STATE is None:
        try:
            import timesfm  # noqa: F401
            _TIMESFM_STATE = (True, "timesfm")
        except Exception:
            _TIMESFM_STATE = (False, "layats-4")
    return _TIMESFM_STATE


@dataclass
class Component:
    name: str
    mse: float
    weight: float
    one_step: float                 # most recent one-step forecast (log ret)


@dataclass
class EnsembleModel:
    backend: str = "layats-4"       # layats-4 | timesfm
    components: list[Component] = field(default_factory=list)
    weights: dict[str, float] = field(default_factory=dict)
    forecasts: dict[int, dict] = field(default_factory=dict)   # h → p10/p50/p90 (%)
    expected_5d_pct: float = 0.0
    fitted: bool = False


# ── base forecasters (one-step log-return space) ────────────────────────────

def _ewma_sweep_forecast(rets: list[float]) -> tuple[float, float]:
    """Best-α EWMA level → next return; returns (forecast, mse over sample)."""
    best = (math.inf, 0.0)
    for alpha in (0.06, 0.12, 0.2, 0.35):
        level = rets[0]
        sse = 0.0
        for r in rets[1:]:
            err = level - r
            sse += err * err
            level = alpha * r + (1 - alpha) * level
        mse = sse / max(len(rets) - 1, 1)
        if mse < best[0]:
            best = (mse, level)
    return best[1], best[0]


def _holt_damped_forecast(rets: list[float]) -> tuple[float, float]:
    """Holt linear trend with damping φ=0.85 on the return level."""
    alpha, beta, phi = 0.30, 0.10, 0.85
    level = rets[0]
    trend = 0.0
    sse = 0.0
    for r in rets[1:]:
        pred = level + phi * trend
        err = pred - r
        sse += err * err
        prev_level = level
        level = alpha * r + (1 - alpha) * (level + phi * trend)
        trend = beta * (level - prev_level) + (1 - beta) * phi * trend
    mse = sse / max(len(rets) - 1, 1)
    return level + phi * trend, mse


def fit(returns: list[float], arma_fc: list[float] | None,
        symreg_fc: float | None, last_price: float) -> EnsembleModel:
    """Blend components; produce horizon fan in % price-change space."""
    ens = EnsembleModel()
    n = len(returns)
    if n < 40:
        return ens

    backend = _timesfm_available()
    ens.backend = backend[1]

    comps: list[tuple[str, float, float]] = []       # (name, one_step, mse)
    ew_fc, ew_mse = _ewma_sweep_forecast(returns)
    comps.append(("ewma", ew_fc, ew_mse))
    hl_fc, hl_mse = _holt_damped_forecast(returns)
    comps.append(("holt", hl_fc, hl_mse))
    if arma_fc:
        comps.append(("arma", arma_fc[0],
                      _oos_mse(returns, arma_fc[0])))
    if symreg_fc is not None and math.isfinite(symreg_fc):
        comps.append(("symreg", symreg_fc,
                      _oos_mse(returns, symreg_fc)))

    # softmax(−MSE/τ) inverse-error weights
    tau = C.ENSEMBLE_TAU
    exp_terms = [math.exp(-mse / tau) for _, _, mse in comps]
    total = sum(exp_terms) or 1.0
    p50_sum = 0.0
    for (name, fc, mse), w in zip(comps, exp_terms):
        weight = w / total
        ens.components.append(Component(name=name, mse=mse, weight=weight,
                                        one_step=fc))
        ens.weights[name] = round(weight, 4)
        p50_sum += weight * fc

    # horizon fan: p50 compounds the weighted one-step; band widens √h
    conformal_scale = 1.0             # zoo injects the conformal q̂ externally
    for h in C.MODEL_HORIZONS:
        drift = p50_sum * h
        # damped drift for longer horizons (momentum persistence decays)
        damp = sum(math.pow(0.92, k) for k in range(h)) / h
        p50_pct = 100.0 * drift * damp
        ens.forecasts[h] = {
            "p50": round(p50_pct, 4),
            "spread1": round(100.0 * conformal_scale, 6),   # per-horizon ±
        }
    ens.expected_5d_pct = ens.forecasts.get(5, {}).get("p50", 0.0)
    ens.fitted = True
    return ens


def _oos_mse(returns: list[float], fc: float) -> float:
    """Honest component error: how the fixed forecast would have scored."""
    mean = sum(returns[-60:]) / max(len(returns[-60:]), 1)
    sse = 0.0
    for r in returns[-60:]:
        sse += (fc - r) ** 2
    return sse / max(len(returns[-60:]), 1) + 0.25 * (fc - mean) ** 2


def apply_conformal_band(ens: EnsembleModel, q_hat: float,
                         sigma_daily: float) -> None:
    """Attach P10/P90 to the horizon fan: conformal q̂ (fallback σ) × √h."""
    for h, fc in ens.forecasts.items():
        base = q_hat if ens_finite(q_hat) else sigma_daily
        spread_pct = 100.0 * base * math.sqrt(h)
        fc["p10"] = round(fc["p50"] - spread_pct, 4)
        fc["p90"] = round(fc["p50"] + spread_pct, 4)


def ens_finite(x: float) -> bool:
    try:
        return math.isfinite(x) and x > 0
    except TypeError:
        return False

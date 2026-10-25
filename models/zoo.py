"""
Model zoo orchestrator — one AssetModels per watched symbol.

Per asset it owns:
  • the rolling close series (EOD history + live intraday anchors)
  • the ARMA(1,1), symbolic-regression, Hawkes, conformal and ensemble fits
  • volatility metrics: close-to-close (annualized), Parkinson high-low,
    Garman-Klass, realized intraday, and the trailing percentile regime
  • live P/E from the static EPS registry (config §11)
  • refit telemetry: fit latency, refit count, last-fit timestamp

Refits are called from the worker's model loop (one asset per cycle, in a
thread, time-boxed) and NEVER inside the 1 s sentiment tick.
"""

from __future__ import annotations

import math
import threading
import time
from collections import deque

import config as C
from data.market_client import MarketClient, Quote
from models import arma as arma_mod
from models import ensemble as ens_mod
from models import hawkes as hawkes_mod
from models import symbolic as sym_mod
from models.conformal import Conformal


# ─────────────────────────────────────────────────────────────────────────────
# Volatility metrics (pure stdlib)
# ─────────────────────────────────────────────────────────────────────────────

def log_returns(closes: list[float]) -> list[float]:
    return [math.log(closes[i] / closes[i - 1])
            for i in range(1, len(closes)) if closes[i - 1] > 0 and closes[i] > 0]


def vol_metrics(closes: list[float], highs: list[float] | None = None,
                lows: list[float] | None = None) -> dict:
    """CC/Parkinson/Garman-Klass annualized vols + trailing percentile."""
    rets = log_returns(closes)
    out = {"cc": 0.0, "parkinson": 0.0, "gk": 0.0, "pctl": 0.5, "regime": "normal"}
    w = min(C.VOL_WINDOW, len(rets))
    if w < 5:
        return out
    recent = rets[-w:]
    mean = sum(recent) / w
    out["cc"] = math.sqrt(sum((r - mean) ** 2 for r in recent) / (w - 1)) * math.sqrt(252.0)

    if highs and lows and len(highs) == len(closes):
        w2 = min(C.VOL_WINDOW, len(closes) - 1)
        if w2 >= 5:
            pk = gk = 0.0
            for i in range(len(closes) - w2, len(closes)):
                h, l, o, c = highs[i], lows[i], closes[i - 1], closes[i]
                if l <= 0 or o <= 0 or c <= 0:
                    continue
                hl = math.log(h / l)
                pk += 0.361 * hl * hl
                gk += 0.5 * hl * hl - (2 * math.log(2) - 1) * math.log(c / o)
            out["parkinson"] = math.sqrt(pk / w2) * math.sqrt(252.0)
            gkv = gk / w2
            out["gk"] = math.sqrt(gkv) * math.sqrt(252.0) if gkv > 0 else out["cc"]

    # trailing percentile of rolling CC vol (regime detector)
    if len(rets) >= 30:
        rolls: list[float] = []
        for i in range(C.VOL_WINDOW, len(rets) + 1):
            seg = rets[i - C.VOL_WINDOW:i]
            m = sum(seg) / len(seg)
            rolls.append(math.sqrt(sum((r - m) ** 2 for r in seg) / len(seg)))
        cur = rolls[-1]
        if rolls:
            below = sum(1 for v in rolls if v <= cur)
            out["pctl"] = round(below / len(rolls), 3)
    if out["pctl"] >= C.FCI_VOL_EXTREME_PCTL:
        out["regime"] = "extreme"
    elif out["pctl"] <= 0.08:
        out["regime"] = "comatose"
    return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in out.items()}


# ─────────────────────────────────────────────────────────────────────────────
# Per-asset state
# ─────────────────────────────────────────────────────────────────────────────

class AssetModels:
    def __init__(self, asset: str) -> None:
        self.asset = asset
        self.lock = threading.Lock()
        self.closes: deque[float] = deque(maxlen=C.HISTORY_WINDOW + 40)
        self.highs: deque[float] = deque(maxlen=C.HISTORY_WINDOW + 40)
        self.lows: deque[float] = deque(maxlen=C.HISTORY_WINDOW + 40)
        self.burst_events: deque[float] = deque(maxlen=600)   # unix ts
        self.arma = arma_mod.ArmaModel()
        self.symreg = sym_mod.SyRegModel()
        self.hawkes = hawkes_mod.HawkesModel()
        self.conformal = Conformal()
        self.ensemble = ens_mod.EnsembleModel()
        self.vols: dict = {}
        self.pe: float | None = None
        self.last_quote_ts: float = 0.0
        self.refits = 0
        self.last_fit_ms = 0.0
        self.last_fit_ts = 0.0
        self.last_error = ""
        self._residuals: deque[float] = deque(maxlen=C.CONFORMAL_WINDOW + 40)

    # ── data in ─────────────────────────────────────────────────────────────
    def load_history(self, closes: list[float]) -> None:
        with self.lock:
            self.closes.clear()
            for c in closes:
                self.closes.append(float(c))
            if not self.vols:
                self.vols = vol_metrics(list(self.closes))

    def update_quote(self, q: Quote) -> None:
        """Live intraday anchor: track today's close as the rolling last."""
        with self.lock:
            if q.price <= 0:
                return
            if self.closes:
                self.closes[-1] = q.price        # today's bar moves with the tape
            self.last_quote_ts = q.ts

    def record_burst(self, ts: float) -> None:
        with self.lock:
            self.burst_events.append(ts)

    def price(self) -> float:
        with self.lock:
            return self.closes[-1] if self.closes else 0.0

    # ── refit ───────────────────────────────────────────────────────────────
    def refit(self, market: MarketClient) -> dict:
        """Full model cycle on the current series. Time-boxed by SYMREG budget;
        everything else is milliseconds. Returns the fresh snapshot."""
        t0 = time.perf_counter()
        try:
            if len(self.closes) < 40:
                hist = market.history(self.asset)
                if hist:
                    self.load_history(hist)
            with self.lock:
                closes = list(self.closes)
            if len(closes) < 40:
                return self.snapshot()

            rets = log_returns(closes)
            # ── ARMA(1,1)
            self.arma = arma_mod.fit(rets)
            arma_fc = arma_mod.forecast_logret(self.arma, rets, max(C.MODEL_HORIZONS))
            # ── symbolic regression (time-boxed inside)
            X, y = sym_mod.build_features(closes)
            self.symreg = sym_mod.fit(X, y, self.asset)
            feats = X[-1] if X else [0.0] * 6
            sym_fc = sym_mod.predict(self.symreg, feats)
            # ── conformal calibration — use the final ARMA fit's recursive
            #    one-step-ahead residuals (the conditional forecast errors) as
            #    the calibration window, plus the symreg residual. Refitting
            #    prefixes for pseudo-OOS residuals costs 7+s and is NOT worth
            #    it; coverage tracking below exposes any miscalibration live.
            calib: list[float] = list(self.arma.residuals[-C.CONFORMAL_WINDOW:])
            if sym_fc is not None and rets:
                calib.append(rets[-1] - sym_fc)
            if calib:
                self.conformal.calibrate(calib)
            # ── ensemble
            self.ensemble = ens_mod.fit(rets, arma_fc, sym_fc, closes[-1])
            ens_mod.apply_conformal_band(
                self.ensemble, self.conformal.q_hat, self.arma.sigma or 0.01)
            # ── Hawkes on sentiment bursts + price jumps
            sigma_d = self.arma.sigma or 0.01
            cutoff = time.time() - C.HAWKES_EVENT_WINDOW_H * 3600
            events = [t for t in self.burst_events if t >= cutoff]
            for i in range(max(1, len(rets) - 400), len(rets)):
                if abs(rets[i]) > C.HAWKES_JUMP_SIGMA * max(sigma_d, 1e-6):
                    # approximate event ts by spacing days backwards from now
                    events.append(time.time() - (len(rets) - i) * 86400.0)
            self.hawkes = hawkes_mod.fit(sorted(set(events)), time.time())
            # ── vol + valuation
            with self.lock:
                hs = list(self.highs) if len(self.highs) == len(self.closes) else None
                ls = list(self.lows) if len(self.lows) == len(self.closes) else None
            self.vols = vol_metrics(closes, hs, ls)
            fund = C.FUNDAMENTALS.get(self.asset, {})
            if fund.get("eps") and closes[-1] > 0:
                self.pe = round(closes[-1] / fund["eps"], 2)
            elif fund.get("pe_static"):
                self.pe = fund["pe_static"]

            self.refits += 1
            self.last_fit_ts = time.time()
            self.last_error = ""
        except Exception as e:                    # a model failure never kills the daemon
            self.last_error = f"{e.__class__.__name__}: {e}"
        self.last_fit_ms = round((time.perf_counter() - t0) * 1000.0, 1)
        return self.snapshot()

    # ── export ──────────────────────────────────────────────────────────────
    def snapshot(self, trail: bool = True) -> dict:
        """Compact /api/feed block for the forecast panels."""
        with self.lock:
            closes = list(self.closes)
        last = closes[-1] if closes else 0.0
        out: dict = {
            "asset": self.asset,
            "price": round(last, 2),
            "backend": self.ensemble.backend,
            "refits": self.refits,
            "fit_ms": self.last_fit_ms,
            "fit_ts": self.last_fit_ts,
            "error": self.last_error,
            "vol": self.vols or {},
            "pe": self.pe,
            "fitted": bool(self.ensemble.fitted),
        }
        if trail:
            out["trail"] = [round(c, 2) for c in closes[-C.TRAIL_POINTS:]]
        if self.ensemble.fitted:
            out["ensemble"] = {
                "backend": self.ensemble.backend,
                "weights": self.ensemble.weights,
                "expected_5d_pct": round(self.ensemble.expected_5d_pct, 3),
                "horizons": {
                    str(h): {
                        "p10": fc.get("p10"),
                        "p50": fc["p50"],
                        "p90": fc.get("p90"),
                    } for h, fc in self.ensemble.forecasts.items()
                },
            }
        else:
            out["ensemble"] = None
        if self.arma.fitted:
            rets = log_returns(closes)
            path = arma_mod.forecast_path(last, arma_mod.forecast_logret(
                self.arma, rets, max(C.MODEL_HORIZONS)))
            out["arma"] = {
                "phi": round(self.arma.phi, 3), "theta": round(self.arma.theta, 3),
                "sigma_d": round(self.arma.sigma, 5),
                "path_pct": [round(100.0 * (p / last - 1.0), 3) if last else 0.0
                             for p in path],
            }
        else:
            out["arma"] = None
        if self.symreg.fitted:
            out["symreg"] = {
                "expr": self.symreg.expr_str,
                "complexity": self.symreg.complexity,
                "mse": round(self.symreg.mse, 8) if math.isfinite(self.symreg.mse) else None,
                "hof": self.symreg.hall_of_fame,
            }
        else:
            out["symreg"] = None
        out["hawkes"] = {
            "lambda_now": round(self.hawkes.lambda_now, 4) if self.hawkes.fitted else None,
            "mu": round(self.hawkes.mu, 4) if self.hawkes.fitted else None,
            "branching": round(self.hawkes.branching, 3) if self.hawkes.fitted else None,
            "events": self.hawkes.n_events,
        } if self.hawkes.fitted else None
        out["conformal"] = self.conformal.snapshot()
        return out


# ─────────────────────────────────────────────────────────────────────────────
# Registry (owned by PipelineState)
# ─────────────────────────────────────────────────────────────────────────────

class Zoo:
    """symbol → AssetModels map with creation on demand."""

    def __init__(self) -> None:
        self._models: dict[str, AssetModels] = {}
        self._lock = threading.Lock()

    def get(self, asset: str) -> AssetModels:
        with self._lock:
            if asset not in self._models:
                self._models[asset] = AssetModels(asset)
            return self._models[asset]

    def all(self) -> dict[str, AssetModels]:
        with self._lock:
            return dict(self._models)

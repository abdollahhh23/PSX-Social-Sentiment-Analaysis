"""
Symbolic regression — PySR-style genetic search, pure stdlib port.

PySR (astroautomata/PySR) evolves symbolic expression trees with a
complexity-penalized pareto front ("hall of fame"). This port keeps the
essentials that make PySR useful on a 260-point return series:

  • expression trees over {+, −, ×, protected ÷, tanh, const, lag-features}
  • fitness = one-step-ahead return MSE × (1 + PARSIMONY × complexity)
  • a per-complexity hall of fame (pareto front), reported to the UI
  • tournament selection, subtree crossover, point/subtree/hoist mutation
  • deterministic per-asset RNG, TIME-BOXED search (config §9)

Features (x0…x5): lag-1/2/3 returns, 3-day momentum, 5-day realized vol,
tanh-scaled trend. Target: next-day log return. The winning expression is
both a forecast AND an interpretable hypothesis — the UI shows it verbatim.
"""

from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass, field

import config as C

# ── protected operators (no NaN/Inf ever leaves an evaluation) ───────────────

def _pdiv(a: float, b: float) -> float:
    if abs(b) < 1e-9:
        return 1.0
    return a / b


_OPS = {
    "add": (2, lambda a, b: a + b),
    "sub": (2, lambda a, b: a - b),
    "mul": (2, lambda a, b: a * b),
    "div": (2, _pdiv),
    "tanh": (1, math.tanh),
    "neg": (1, lambda a: -a),
}
_OPS2 = ("add", "sub", "mul", "div")
_OPS1 = ("tanh", "neg")

_FEATURES = ["r1", "r2", "r3", "mom3", "vol5", "trend"]


def build_features(closes: list[float]) -> tuple[list[list[float]], list[float]]:
    """Feature matrix + one-step-ahead targets from a close series."""
    X: list[list[float]] = []
    y: list[float] = []
    rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))
            if closes[i - 1] > 0]
    n = len(rets)
    for t in range(5, n):
        r1, r2, r3 = rets[t - 1], rets[t - 2], rets[t - 3]
        mom3 = r1 + r2 + r3
        window = rets[max(0, t - 5):t]
        mean = sum(window) / len(window)
        vol5 = math.sqrt(sum((r - mean) ** 2 for r in window) / len(window))
        trend = math.tanh(mom3 * 40.0)
        X.append([r1, r2, r3, mom3, vol5, trend])
        y.append(rets[t])
    return X, y


# ── tree representation: ["var", i] | ["const", v] | ["op", child…] ──────────
# Lists (not tuples) — subtree crossover grafts in place.

def _depth(node) -> int:
    if node[0] in ("var", "const"):
        return 1
    return 1 + max(_depth(c) for c in node[1:])


def _size(node) -> int:
    if node[0] in ("var", "const"):
        return 1
    return 1 + sum(_size(c) for c in node[1:])


def _evaluate(node, x: list[float]) -> float:
    op = node[0]
    if op == "var":
        return x[node[1]]
    if op == "const":
        return node[1]
    fn = _OPS[op][1]
    if op in _OPS2:
        return fn(_evaluate(node[1], x), _evaluate(node[2], x))
    return fn(_evaluate(node[1], x))


def _to_expr(node) -> str:
    op = node[0]
    if op == "var":
        return _FEATURES[node[1]]
    if op == "const":
        return f"{node[1]:.4f}"
    if op in _OPS2:
        return f"({ _to_expr(node[1]) } { {'add':'+','sub':'-','mul':'×','div':'÷'}[op] } { _to_expr(node[2]) })"
    if op == "neg":
        return f"−{_to_expr(node[1])}"
    return f"tanh({_to_expr(node[1])})"


def _rand_tree(rng: random.Random, depth: int) -> list:
    if depth <= 1 or rng.random() < 0.3:
        if rng.random() < 0.72:
            return ["var", rng.randrange(len(_FEATURES))]
        return ["const", round(rng.uniform(-0.01, 0.01), 5)]
    if rng.random() < 0.8:
        op = rng.choice(_OPS2)
        return [op, _rand_tree(rng, depth - 1), _rand_tree(rng, depth - 1)]
    op = rng.choice(_OPS1)
    return [op, _rand_tree(rng, depth - 1)]


def _collect(node, acc: list) -> None:
    acc.append(node)
    if node[0] not in ("var", "const"):
        for c in node[1:]:
            _collect(c, acc)


def _copy(node) -> list:
    if node[0] in ("var", "const"):
        return list(node)
    return [node[0], *[_copy(c) for c in node[1:]]]


def _random_path(rng: random.Random, node) -> list:
    """Path of nodes from root to a random leaf-ish node."""
    path = [node]
    while node[0] not in ("var", "const") and (len(node) > 2 or rng.random() > 0.25):
        node = node[rng.randrange(1, len(node))]
        path.append(node)
    return path


def _crossover(a, b, rng: random.Random):
    """Subtree crossover: graft a random subtree of b into a random slot of a."""
    child = _copy(a)
    pa = _random_path(rng, child)
    donor = _random_path(rng, _copy(b))
    graft = donor[-1]
    if len(pa) > 1:
        parent = pa[-2]
        idx = None
        for i, c in enumerate(parent[1:], start=1):
            if c is pa[-1]:
                idx = i
                break
        if idx is not None:
            parent[idx] = _copy(graft)
    return child


def _mutate(node, rng: random.Random):
    out = _copy(node)
    nodes = []
    _collect(out, nodes)
    victim = rng.choice(nodes)
    if victim[0] in ("var", "const"):
        if rng.random() < 0.4:
            new = _rand_tree(rng, C.SYMREG_MAX_DEPTH - 1)
            return new                                        # subtree swap
        if victim[0] == "const":                              # point tweak
            victim[1] = round(victim[1] * rng.uniform(0.5, 1.5)
                              + rng.uniform(-0.002, 0.002), 6)
            return out
        victim[1] = rng.randrange(len(_FEATURES))             # feature swap
        return out
    # operator shuffle on an internal node
    if victim[0] in _OPS2:
        victim[0] = rng.choice(_OPS2)
        if rng.random() < 0.3:
            victim[1], victim[2] = victim[2], victim[1]
    else:
        victim[0] = rng.choice(_OPS1)
    return out


@dataclass
class SyRegModel:
    expr: list | None = None
    expr_str: str = ""
    complexity: int = 0
    mse: float = math.inf
    hall_of_fame: list[dict] = field(default_factory=list)   # pareto front
    fitted: bool = False


def fit(X: list[list[float]], y: list[float], asset: str) -> SyRegModel:
    """Time-boxed genetic search (config budgets). Deterministic per asset."""
    m = SyRegModel()
    n = len(y)
    if n < 40 or not X:
        return m
    rng = random.Random(hash(("psx-syreg", asset)) & 0xFFFFFFFF)
    t0 = time.perf_counter()

    pop = [_rand_tree(rng, C.SYMREG_MAX_DEPTH) for _ in range(C.SYMREG_POPULATION)]

    def score(tree) -> float:
        sse = 0.0
        for i in range(n):
            try:
                pred = _evaluate(tree, X[i])
            except (OverflowError, ValueError, IndexError):
                return math.inf
            if not math.isfinite(pred):
                return math.inf
            d = pred - y[i]
            sse += d * d
        mse = sse / n
        return mse * (1.0 + C.SYMREG_PARSIMONY * _size(tree))

    scored = [(score(t), t) for t in pop]
    hof: dict[int, tuple] = {}                       # complexity → (score, tree)
    for s, t in scored:
        if s == math.inf:
            continue
        c = _size(t)
        if c not in hof or s < hof[c][0]:
            hof[c] = (s, t)

    gen = 0
    while gen < C.SYMREG_GENERATIONS and \
            (time.perf_counter() - t0) < C.SYMREG_TIME_BUDGET_S:
        gen += 1
        scored.sort(key=lambda p: p[0])
        elite_k = max(2, C.SYMREG_POPULATION // 8)
        nxt = [t for _, t in scored[:elite_k]]
        while len(nxt) < C.SYMREG_POPULATION:
            r = rng.random()
            t1 = min(rng.sample(scored, 3), key=lambda p: p[0])[1]
            if r < 0.62:
                t2 = min(rng.sample(scored, 3), key=lambda p: p[0])[1]
                nxt.append(_crossover(t1, t2, rng))
            elif r < 0.87:
                nxt.append(_mutate(t1, rng))
            else:
                nxt.append(_copy(t1))
        scored = [(s, t) for s, t in ((score(t), t) for t in nxt)]
        for s, t in scored:
            if s == math.inf:
                continue
            c = _size(t)
            if c not in hof or s < hof[c][0]:
                hof[c] = (s, t)

    best_tree, best_s = None, math.inf
    for c, (s, t) in hof.items():
        if s < best_s:
            best_tree, best_s = t, s
    if best_tree is None:
        return m
    m.expr = best_tree
    m.expr_str = _to_expr(best_tree)
    m.complexity = _size(best_tree)
    sse = 0.0
    for i in range(n):
        d = _evaluate(best_tree, X[i]) - y[i]
        sse += d * d
    m.mse = sse / n
    m.fitted = True
    # pareto front for the UI: best tree at each complexity, cheapest first
    fronts = []
    for c in sorted(hof):
        s, t = hof[c]
        fronts.append({
            "complexity": c,
            "expr": _to_expr(t),
            "mse": round(s, 8),
        })
    m.hall_of_fame = fronts[:8]
    return m


def predict(model: SyRegModel, features: list[float]) -> float | None:
    """One-step-ahead predicted log return (None if unfit/degenerate)."""
    if not model.fitted or model.expr is None:
        return None
    try:
        v = _evaluate(model.expr, features)
    except (OverflowError, ValueError, IndexError):
        return None
    return v if math.isfinite(v) else None

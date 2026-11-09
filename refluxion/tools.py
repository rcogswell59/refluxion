"""Model analysis tools: sensitivity analysis, case studies and optimization.

These wrap a converged flowsheet exactly like Aspen's *Model Analysis Tools*:

* :func:`sensitivity` - vary one or two inputs over a grid, tabulate any results (Aspen's S-1 blocks)
* :func:`case_study`  - run named alternatives side by side (great for "design A vs design B")
* :func:`optimize`    - minimize/maximize an objective with constraints (SQP, like Aspen's default)

Inputs are *paths* into block or feed specs, in the flowsheet's unit set: ``"COOL.T"``,
``"FEED.flows.C3"``, ``"SPLIT.fractions.PURGE"``, ``"COL1.heaters.2"``,
``"COL1.specs.reboiler_duty"``. Results are any callable ``f(fs)`` returning a number, e.g.
``lambda fs: fs["VAPOR"].flow("C2")``.
"""

from __future__ import annotations

import itertools
import time
from dataclasses import dataclass, field

import numpy as np


def _grid(spec):
    """``values`` may be a list, or dict(lower=, upper=, points=) / dict(lower=, upper=, incr=)."""
    if isinstance(spec, dict):
        lo, hi = float(spec["lower"]), float(spec["upper"])
        if "incr" in spec:
            n = int(round((hi - lo) / float(spec["incr"]))) + 1
            return list(np.linspace(lo, hi, n))
        return list(np.linspace(lo, hi, int(spec.get("points", 11))))
    return [float(v) for v in spec]


@dataclass
class SensitivityResult:
    name: str
    vary: list            # [(path, label)]
    tabulate: list        # labels
    rows: list            # dicts: vary values + tabulated values + status
    base: dict
    seconds: float = 0.0

    def column(self, label):
        return np.array([r.get(label, np.nan) for r in self.rows], dtype=float)

    def to_rows(self):
        return self.rows

    def __repr__(self):
        return f"<Sensitivity {self.name}: {len(self.rows)} cases, {sum(r['status'] == 'ok' for r in self.rows)} ok>"


def sensitivity(fs, vary: dict, tabulate: dict, name: str = "S-1", restore: bool = True, rerun_base: bool = True,
                verbose: bool = False) -> SensitivityResult:
    """Run a sensitivity analysis.

    ``vary``: ``{path: values}`` (1 or 2 entries; 2 gives a full grid - the first is the x-axis,
    the second the parametric variable). Values: list or dict(lower=, upper=, points=|incr=).
    ``tabulate``: ``{label: f(fs)}``.
    """
    t0 = time.perf_counter()
    snap = fs.snapshot()
    paths = list(vary)
    grids = [_grid(vary[p]) for p in paths]
    base = {p: fs.get_path(p) for p in paths}
    rows = []
    for combo in itertools.product(*grids):
        row = {p: v for p, v in zip(paths, combo)}
        try:
            for p, v in zip(paths, combo):
                fs.set_path(p, float(v))
            fs.run()
            for label, fn in tabulate.items():
                row[label] = float(fn(fs))
            row["status"] = "ok"
        except Exception as exc:
            for label in tabulate:
                row[label] = float("nan")
            row["status"] = f"error: {str(exc)[:120]}"
        rows.append(row)
        if verbose:
            print(row)
    if restore:
        fs.restore(snap)
        if rerun_base:
            try:
                fs.run()
            except Exception:
                pass
    return SensitivityResult(name, [(p, p) for p in paths], list(tabulate), rows, base, time.perf_counter() - t0)


@dataclass
class CaseStudyResult:
    name: str
    cases: list
    tabulate: list
    rows: list

    def __repr__(self):
        return f"<CaseStudy {self.name}: {len(self.rows)} cases>"


def case_study(fs, cases: dict, tabulate: dict, name: str = "Cases", restore: bool = True) -> CaseStudyResult:
    """``cases = {"Base": {}, "Cooler side exchangers": {"COL1.heaters.2": -5e5, ...}}``."""
    snap = fs.snapshot()
    rows = []
    for label, changes in cases.items():
        fs.restore(snap)
        row = {"case": label, "changes": dict(changes)}
        try:
            for p, v in changes.items():
                fs.set_path(p, v)
            fs.run()
            for t, fn in tabulate.items():
                row[t] = float(fn(fs))
            row["status"] = "ok"
        except Exception as exc:
            for t in tabulate:
                row[t] = float("nan")
            row["status"] = f"error: {str(exc)[:120]}"
        rows.append(row)
    if restore:
        fs.restore(snap)
        try:
            fs.run()
        except Exception:
            pass
    return CaseStudyResult(name, list(cases), list(tabulate), rows)


@dataclass
class Constraint:
    label: str
    fn: object
    kind: str        # "<=", ">=", "=="
    value: float
    tol: float = 0.0


@dataclass
class OptimizationResult:
    name: str
    success: bool
    message: str
    x: dict
    objective: float
    constraints: dict
    history: list = field(default_factory=list)
    evaluations: int = 0
    seconds: float = 0.0
    sense: str = "min"

    def __repr__(self):
        return f"<Optimization {self.name}: {'ok' if self.success else 'FAILED'} obj={self.objective:.6g} x={self.x}>"


def optimize(fs, objective, vary: dict, constraints=(), sense: str = "min", name: str = "O-1",
             maxiter: int = 60, tol: float = 1e-6, eps: float = 1e-3, verbose: bool = False) -> OptimizationResult:
    """Optimize with SLSQP (sequential quadratic programming, Aspen's default).

    ``vary = {path: (lower, upper)}`` (start = current value); ``constraints`` is a list of
    ``(label, f(fs), "<=" | ">=" | "==", value)`` tuples. Variables are scaled to [0, 1];
    ``eps`` is the finite-difference step in scaled units. The flowsheet is left at the optimum.
    """
    from scipy.optimize import minimize

    t0 = time.perf_counter()
    paths = list(vary)
    lo = np.array([float(vary[p][0]) for p in paths])
    hi = np.array([float(vary[p][1]) for p in paths])
    x0 = np.array([float(fs.get_path(p)) if fs.get_path(p) is not None else 0.5 * (a + b) for p, a, b in zip(paths, lo, hi)])
    u0 = np.clip((x0 - lo) / (hi - lo), 0, 1)
    cons = [Constraint(*c) if not isinstance(c, Constraint) else c for c in constraints]
    cache = {}
    history = []
    sign = 1.0 if sense == "min" else -1.0

    def evaluate(u):
        key = tuple(np.round(u, 12))
        if key in cache:
            return cache[key]
        x = lo + np.clip(u, 0, 1) * (hi - lo)
        for p, v in zip(paths, x):
            fs.set_path(p, float(v))
        try:
            fs.run()
            obj = float(objective(fs))
            cv = [float(c.fn(fs)) for c in cons]
            ok = True
        except Exception as exc:
            obj, cv, ok = float("nan"), [float("nan")] * len(cons), False
            if verbose:
                print("evaluation failed:", exc)
        cache[key] = (obj, cv, ok)
        history.append(dict(x={p: float(v) for p, v in zip(paths, x)}, objective=obj,
                            constraints={c.label: v for c, v in zip(cons, cv)}, ok=ok))
        if verbose:
            print(history[-1])
        return cache[key]

    obj0, cv0, ok0 = evaluate(u0)
    if not ok0:
        raise RuntimeError("the flowsheet does not solve at the starting point")
    oscale = max(abs(obj0), 1e-12)
    cscale = [max(abs(c.value), abs(v) if np.isfinite(v) else 0.0, 1e-12) for c, v in zip(cons, cv0)]

    def f(u):
        obj, _, ok = evaluate(u)
        return sign * obj / oscale if ok else 1e6

    sp_cons = []
    for k, c in enumerate(cons):
        def g(u, k=k, c=c):
            _, cv, ok = evaluate(u)
            v = cv[k] if ok else np.nan
            if not np.isfinite(v):
                return -1e3
            if c.kind == "<=":
                return (c.value - v) / cscale[k]
            if c.kind == ">=":
                return (v - c.value) / cscale[k]
            return (v - c.value) / cscale[k]
        sp_cons.append(dict(type="eq" if c.kind == "==" else "ineq", fun=g))
    res = minimize(f, u0, method="SLSQP", bounds=[(0, 1)] * len(paths), constraints=sp_cons,
                   options=dict(maxiter=maxiter, ftol=tol, eps=eps))
    u_best = np.clip(res.x, 0, 1)
    obj, cv, ok = evaluate(u_best)
    x_best = lo + u_best * (hi - lo)
    for p, v in zip(paths, x_best):
        fs.set_path(p, float(v))
    fs.run()
    feasible = all(
        (c.kind == "<=" and v <= c.value + max(c.tol, 1e-6 * abs(c.value))) or
        (c.kind == ">=" and v >= c.value - max(c.tol, 1e-6 * abs(c.value))) or
        (c.kind == "==" and abs(v - c.value) <= max(c.tol, 1e-4 * abs(c.value)))
        for c, v in zip(cons, cv))
    return OptimizationResult(name, bool(res.success and ok and feasible), str(res.message),
                              {p: float(v) for p, v in zip(paths, x_best)}, obj,
                              {c.label: v for c, v in zip(cons, cv)}, history, len(cache),
                              time.perf_counter() - t0, sense)

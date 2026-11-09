"""Design studies: search a flowsheet's design variables against constraints and an economic objective.

A study is three functions' worth of problem definition plus bookkeeping:

* ``build(**x) -> flowsheet`` builds and solves a design for variable values ``x`` (it may raise)
* ``evaluate(flowsheet) -> dict`` returns metrics (purities, costs, NPV ...)
* ``constraints = [(metric, ">=" | "<=", value)]`` and ``objective = metric`` (``sense`` max or min)

Every evaluation is cached (in memory, and in JSON at ``cache_path`` so a long study can resume). A design that
fails to converge is kept as an infeasible result with its error, never dropped. :meth:`Study.grid` scans a
DOE grid, :meth:`Study.refine` runs a bounded pattern search (integer variables move in whole steps), and
:meth:`Study.parallel` evaluates independent points in worker processes (``build``/``evaluate`` must be
importable module-level functions).
"""

from __future__ import annotations

import itertools
import json
import math
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class Variable:
    name: str
    lower: float
    upper: float
    integer: bool = False
    start: float | None = None      # value used when a point leaves this variable out (default: mid-range)

    @property
    def default(self):
        return self.clip(self.start if self.start is not None else 0.5 * (self.lower + self.upper))

    def clip(self, v):
        v = min(max(v, self.lower), self.upper)
        return int(round(v)) if self.integer else float(v)


@dataclass
class Result:
    x: dict
    metrics: dict = field(default_factory=dict)
    feasible: bool = False
    objective: float | None = None
    error: str | None = None
    violations: dict = field(default_factory=dict)


def _check(metrics, constraints):
    viol = {}
    for key, op, value in constraints:
        v = metrics.get(key)
        if v is None or (isinstance(v, float) and math.isnan(v)):
            viol[key] = "missing"
        elif op == ">=" and v < value:
            viol[key] = v - value
        elif op == "<=" and v > value:
            viol[key] = v - value
    return viol


def _evaluate_point(build, evaluate, constraints, objective, x):
    try:
        metrics = dict(evaluate(build(**x)))
    except Exception as exc:  # a design that does not solve is a result, not a crash
        return Result(dict(x), {}, False, None, f"{type(exc).__name__}: {exc}"[:300])
    viol = _check(metrics, constraints)
    obj = metrics.get(objective)
    return Result(dict(x), metrics, not viol and obj is not None, obj, None, viol)


def _refine_worker(args):
    """One multistart branch in its own process: a fresh Study (no shared cache file) refined from ``start``."""
    build, evaluate, variables, constraints, objective, sense, fixed, digits, start, max_evals, seed_cache = args
    st = Study(build, evaluate, variables, constraints, objective, sense, fixed, None, digits)
    st.cache.update(seed_cache)
    best = st.refine(start, max_evals=max_evals)
    return best, st.cache


class Study:
    def __init__(self, build, evaluate, variables, constraints=(), objective: str = "objective", sense: str = "max",
                 fixed: dict | None = None, cache_path=None, digits: int = 6):
        self.build, self.evaluate = build, evaluate
        self.variables = {v.name: v for v in variables}
        self.constraints = list(constraints)
        self.objective, self.sense = objective, sense
        self.fixed = dict(fixed or {})
        self.digits = digits
        self.cache_path = Path(cache_path) if cache_path else None
        self.cache: dict = {}
        if self.cache_path and self.cache_path.exists():
            for k, v in json.loads(self.cache_path.read_text()).items():
                self.cache[k] = Result(**v)

    # --- bookkeeping ------------------------------------------------------------------------
    def _x(self, x: dict) -> dict:
        full = dict(self.fixed)
        for k, var in self.variables.items():
            if k not in x:
                full[k] = var.default
        for k, v in x.items():
            full[k] = self.variables[k].clip(v) if k in self.variables else v
        return full

    def _key(self, x: dict) -> str:
        return json.dumps({k: (round(v, self.digits) if isinstance(v, float) else v) for k, v in sorted(x.items())})

    def _save(self):
        if self.cache_path:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            self.cache_path.write_text(json.dumps({k: asdict(r) for k, r in self.cache.items()}, default=str))

    def score(self, r: Result) -> float:
        """Larger is better; infeasible results rank below every feasible one."""
        if not r.feasible:
            return -math.inf
        return r.objective if self.sense == "max" else -r.objective

    # --- evaluation -------------------------------------------------------------------------
    def run_point(self, **x) -> Result:
        full = self._x(x)
        key = self._key(full)
        if key not in self.cache:
            self.cache[key] = _evaluate_point(self.build, self.evaluate, self.constraints, self.objective, full)
            self._save()
        return self.cache[key]

    def parallel(self, points, workers: int | None = None) -> list:
        fulls = [self._x(p) for p in points]
        todo = [(i, f) for i, f in enumerate(fulls) if self._key(f) not in self.cache]
        if todo:
            with ProcessPoolExecutor(max_workers=workers) as ex:
                futs = {i: ex.submit(_evaluate_point, self.build, self.evaluate, self.constraints, self.objective, f) for i, f in todo}
                for i, fut in futs.items():
                    self.cache[self._key(fulls[i])] = fut.result()
            self._save()
        return [self.cache[self._key(f)] for f in fulls]

    def grid(self, levels: dict, workers: int | None = None) -> list:
        names = list(levels)
        points = [dict(zip(names, combo)) for combo in itertools.product(*(levels[n] for n in names))]
        if workers and workers > 1:
            return self.parallel(points, workers)
        return [self.run_point(**p) for p in points]

    def best(self, results=None) -> Result:
        results = list(results if results is not None else self.cache.values())
        return max(results, key=self.score)

    def refine(self, start, steps: dict | None = None, min_step: dict | None = None, max_evals: int = 300) -> Result:
        """Bounded pattern search from ``start`` (a Result or a dict of variable values). Continuous steps start at
        10 % of the range and halve until ``min_step`` (default 0.5 % of the range); integer steps start at
        max(1, 10 % of the range) and stop at 1."""
        x = dict(start.x if isinstance(start, Result) else start)
        names = [n for n in self.variables]
        steps = dict(steps or {})
        mins = dict(min_step or {})
        for n in names:
            v = self.variables[n]
            span = v.upper - v.lower
            steps.setdefault(n, max(1, round(0.1 * span)) if v.integer else 0.1 * span)
            mins.setdefault(n, 1 if v.integer else 0.005 * span)
        best = self.run_point(**{n: x[n] for n in names if n in x})
        evals = 0
        while evals < max_evals:
            improved = False
            for n in names:
                for sgn in (+1, -1):
                    trial = dict(best.x)
                    trial[n] = self.variables[n].clip(trial[n] + sgn * steps[n])
                    if trial[n] == best.x[n]:
                        continue
                    r = self.run_point(**{k: trial[k] for k in names})
                    evals += 1
                    if self.score(r) > self.score(best):
                        best, improved = r, True
                        break
            if not improved:
                shrinkable = [n for n in names if steps[n] > mins[n]]
                if not shrinkable:
                    break
                for n in shrinkable:
                    v = self.variables[n]
                    steps[n] = max(mins[n], (steps[n] // 2) if v.integer else steps[n] / 2)
        return best

    def multistart(self, starts, workers: int | None = None, max_evals: int = 300):
        """Refine from several starting points in parallel; merge every evaluation into this study's cache.
        Returns (best Result, [best Result of each start])."""
        args = [(self.build, self.evaluate, list(self.variables.values()), self.constraints, self.objective, self.sense,
                 self.fixed, self.digits, dict(st.x if isinstance(st, Result) else st), max_evals, dict(self.cache)) for st in starts]
        results = []
        with ProcessPoolExecutor(max_workers=workers) as ex:
            for best, cache in ex.map(_refine_worker, args):
                results.append(best)
                self.cache.update(cache)
        self._save()
        return max(results, key=self.score), results


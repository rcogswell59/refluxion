"""Design study framework: cached evaluations, constraints, grid scan, pattern-search refine, failures recorded."""

from __future__ import annotations

import json

import pytest

from refluxion.study import Study, Variable

CALLS = {"n": 0}


def build(n: int, r: float):
    CALLS["n"] += 1
    if r < 0.2:
        raise RuntimeError("did not converge")
    return dict(n=n, r=r)


def evaluate(obj):
    n, r = obj["n"], obj["r"]
    return dict(profit=-(n - 7) ** 2 - 10 * (r - 1.3) ** 2, purity=0.9 + 0.01 * n)


def study(tmp_path=None):
    return Study(build, evaluate, [Variable("n", 2, 20, integer=True), Variable("r", 0.1, 3.0)],
                 constraints=[("purity", ">=", 0.95)], objective="profit", sense="max",
                 cache_path=(tmp_path / "cache.json") if tmp_path else None)


def test_constraint_and_objective():
    s = study()
    good = s.run_point(n=7, r=1.3)
    bad = s.run_point(n=3, r=1.3)
    assert good.feasible and good.objective == pytest.approx(0.0)
    assert not bad.feasible and "purity" in bad.violations


def test_failure_is_recorded_not_raised():
    r = study().run_point(n=7, r=0.1)
    assert not r.feasible and "did not converge" in r.error


def test_grid_then_refine_finds_optimum(tmp_path):
    s = study(tmp_path)
    res = s.grid({"n": [3, 6, 12, 18], "r": [0.5, 1.0, 2.0]})
    assert len(res) == 12
    best = s.best(res)
    final = s.refine(best)
    assert final.x["n"] == 7 and final.x["r"] == pytest.approx(1.3, abs=0.02) and final.feasible


def test_cache_hits_and_persists(tmp_path):
    s = study(tmp_path)
    CALLS["n"] = 0
    s.run_point(n=8, r=1.0)
    s.run_point(n=8, r=1.0)
    assert CALLS["n"] == 1
    data = json.loads((tmp_path / "cache.json").read_text())
    assert len(data) == 1
    s2 = study(tmp_path)
    s2.run_point(n=8, r=1.0)
    assert CALLS["n"] == 1


def test_parallel_matches_serial():
    s = study()
    pts = [dict(n=5, r=1.0), dict(n=9, r=2.0)]
    par = s.parallel(pts, workers=2)
    assert [p.objective for p in par] == pytest.approx([s.run_point(**p).objective for p in pts])


def test_refine_from_a_point_that_leaves_variables_out():
    s = Study(build, evaluate, [Variable("n", 2, 20, integer=True, start=6), Variable("r", 0.1, 3.0, start=1.0)],
              constraints=[("purity", ">=", 0.95)], objective="profit", sense="max")
    first = s.run_point(n=6)                      # r not given: its start value is used
    assert first.x == {"n": 6, "r": 1.0}
    assert s.refine(first).x["n"] == 7


def test_multistart_refines_each_start_and_returns_the_best(tmp_path):
    s = study(tmp_path)
    starts = [dict(n=3, r=0.5), dict(n=18, r=2.5), dict(n=12, r=1.0)]
    best, results = s.multistart(starts, workers=2)
    assert len(results) == 3 and best.x["n"] == 7 and best.x["r"] == pytest.approx(1.3, abs=0.02)
    assert all(s.score(best) >= s.score(r) for r in results)
    assert len(s.cache) > 3                      # the workers' evaluations were merged back

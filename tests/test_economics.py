"""Discounted cash flow: MACRS / SOYD depreciation, NPV, DCF rate of return, payback."""

from __future__ import annotations

import pytest

from refluxion.design.economics import MACRS, cash_flows, irr, npv, payback, soyd


def test_macrs_tables_sum_to_one():
    assert len(MACRS[5]) == 6 and len(MACRS[7]) == 8
    assert sum(MACRS[5]) == pytest.approx(1.0, abs=1e-4) and sum(MACRS[7]) == pytest.approx(1.0, abs=1e-4)
    assert MACRS[7][0] == pytest.approx(0.1429)


def test_soyd():
    assert soyd(5) == pytest.approx([5 / 15, 4 / 15, 3 / 15, 2 / 15, 1 / 15])


def test_npv_and_irr():
    cf = [-100.0, 60.0, 60.0]
    assert npv(cf, 0.10) == pytest.approx(-100 + 60 / 1.1 + 60 / 1.21, rel=1e-12)
    assert irr(cf) == pytest.approx(0.130662, abs=1e-6)
    assert payback(cf) == pytest.approx(1 + 40 / 60)


def test_cash_flow_table_after_tax():
    t = cash_flows(FCI=1000.0, working_capital=100.0, annual_savings=500.0, annual_costs=200.0, years=3, tax=0.28,
                   depreciation="SOYD", dep_years=3, i=0.15)
    y1 = t.rows[1]
    dep1 = 1000.0 * 3 / 6
    assert y1["depreciation"] == pytest.approx(dep1)
    assert y1["tax"] == pytest.approx(0.28 * (500 - 200 - dep1))
    assert y1["cash_flow"] == pytest.approx(500 - 200 - 0.28 * (500 - 200 - dep1))
    assert t.rows[0]["cash_flow"] == pytest.approx(-1100.0)
    assert t.rows[-1]["cash_flow"] == pytest.approx(500 - 200 - 0.28 * (500 - 200 - 1000 / 6) + 100.0)   # WC recovered
    assert t.npv == pytest.approx(npv([r["cash_flow"] for r in t.rows], 0.15))

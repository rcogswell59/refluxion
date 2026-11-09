"""Discounted-cash-flow economics: depreciation, after-tax cash flows, NPV, DCF rate of return, payback.

Conventions (as in Turton Ch. 10 and Peters & Timmerhaus, simplified to a single construction year):

* year 0: fixed capital plus working capital are spent; operation runs in years 1..n
* depreciation on the fixed capital: MACRS (IRS GDS half-year tables, Publication 946) or sum-of-the-years-digits
* taxable income = savings - costs - depreciation; tax = rate x taxable income (a loss earns a credit against
  the rest of the company's income)
* working capital is recovered at the end of year n; no salvage value
"""

from __future__ import annotations

from dataclasses import dataclass, field

MACRS = {
    3: [0.3333, 0.4445, 0.1481, 0.0741],
    5: [0.2000, 0.3200, 0.1920, 0.1152, 0.1152, 0.0576],
    7: [0.1429, 0.2449, 0.1749, 0.1249, 0.0893, 0.0892, 0.0893, 0.0446],
    10: [0.1000, 0.1800, 0.1440, 0.1152, 0.0922, 0.0737, 0.0655, 0.0655, 0.0656, 0.0655, 0.0328],
}


def soyd(n: int) -> list:
    """Sum-of-the-years-digits fractions for an n-year life."""
    s = n * (n + 1) / 2
    return [(n - k) / s for k in range(n)]


def depreciation_schedule(method: str, n_years: int, dep_years: int) -> list:
    m = method.upper()
    if m.startswith("MACRS"):
        frac = MACRS[dep_years]
    elif m == "SOYD":
        frac = soyd(dep_years)
    elif m in ("SL", "STRAIGHT-LINE"):
        frac = [1.0 / dep_years] * dep_years
    else:
        raise ValueError(f"unknown depreciation method {method!r} (MACRS, SOYD, SL)")
    return (list(frac) + [0.0] * n_years)[:n_years]


def npv(cf, i: float) -> float:
    return float(sum(c / (1.0 + i) ** k for k, c in enumerate(cf)))


def irr(cf, lo: float = -0.99, hi: float = 10.0) -> float | None:
    """DCF rate of return: the i with NPV = 0 (None if it does not exist in [lo, hi])."""
    from scipy.optimize import brentq

    f = lambda i: npv(cf, i)  # noqa: E731
    if f(lo) * f(hi) > 0:
        return None
    return float(brentq(f, lo, hi, xtol=1e-12))


def payback(cf) -> float | None:
    """Years until the cumulative (undiscounted) cash flow turns positive, interpolated within the year."""
    cum = 0.0
    for k, c in enumerate(cf):
        prev = cum
        cum += c
        if k > 0 and cum >= 0 > prev:
            return (k - 1) + (-prev / c)
    return None


def annualized_cost(capital: float, annual_opex: float, i: float, n: int) -> float:
    """Equivalent annual cost: capital x A/P(i, n) + operating cost."""
    crf = i * (1 + i) ** n / ((1 + i) ** n - 1)
    return capital * crf + annual_opex


@dataclass
class CashFlowTable:
    rows: list
    npv: float
    irr: float | None
    payback: float | None
    i: float
    basis: str
    inputs: dict = field(default_factory=dict)


def cash_flows(FCI: float, working_capital: float, annual_savings: float, annual_costs: float, *, years: int = 20,
               tax: float = 0.28, depreciation: str = "MACRS", dep_years: int = 7, i: float = 0.15) -> CashFlowTable:
    """After-tax cash-flow table for a project that saves ``annual_savings`` and costs ``annual_costs`` per year ($)."""
    dep = depreciation_schedule(depreciation, years, dep_years)
    rows = [dict(year=0, savings=0.0, costs=0.0, depreciation=0.0, taxable=0.0, tax=0.0,
                 cash_flow=-(FCI + working_capital), discounted=-(FCI + working_capital))]
    cum = rows[0]["cash_flow"]
    rows[0]["cumulative_discounted"] = cum
    for k in range(1, years + 1):
        d = FCI * dep[k - 1]
        taxable = annual_savings - annual_costs - d
        t = tax * taxable
        cf = annual_savings - annual_costs - t + (working_capital if k == years else 0.0)
        disc = cf / (1 + i) ** k
        cum += disc
        rows.append(dict(year=k, savings=annual_savings, costs=annual_costs, depreciation=d, taxable=taxable, tax=t,
                         cash_flow=cf, discounted=disc, cumulative_discounted=cum))
    cf = [r["cash_flow"] for r in rows]
    return CashFlowTable(rows, npv(cf, i), irr(cf), payback(cf), i,
                         f"{depreciation} ({dep_years} yr) depreciation, {tax:.0%} tax, {years}-yr life, i = {i:.0%}, "
                         "capital and working capital in year 0, working capital recovered in the last year",
                         dict(FCI=FCI, working_capital=working_capital, annual_savings=annual_savings, annual_costs=annual_costs))

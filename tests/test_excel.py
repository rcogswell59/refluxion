"""Excel workbook export: live formulas for capital, operating cost and the DCF cash flow (E-6)."""

from __future__ import annotations

import openpyxl
import pytest

from refluxion import Flowsheet, RadFrac
from refluxion.design.plant import PlantBasis, evaluate_plant
from refluxion.report.excel import export_workbook
from refluxion.utilities import Utility

K = 273.15


@pytest.fixture(scope="module")
def evaluated():
    fs = Flowsheet("t", components=[("BZ", "benzene"), ("TOL", "toluene")], method="IDEAL", units="SI-KW")
    fs.feed("F", T=25, P=1.2, flows={"BZ": 500.0, "TOL": 500.0}, basis="mass")
    fs.add(RadFrac("COL", n_stages=20, condenser="total", reboiler="kettle", P_top=1.2, feeds={"F": 10},
                   specs={"reflux_ratio": 2.0, "distillate_mass_rate": 495.0}), inlets=["F"], outlets={"distillate": "D", "bottoms": "B"})
    fs.run()
    b = PlantBasis(cooling=[Utility("CW", "cooling", T_in=30 + K, T_out=40 + K, unit_price=3e-5, basis="kg", cp=4180.0)],
                   heating=[Utility("LPS", "heating", T_in=160 + K, T_out=160 + K, unit_price=0.02, basis="kg", latent=2080e3)],
                   electricity=Utility("E", "electricity", price=20.0), price={"BZ": 1.0, "TOL": 0.8})
    m = evaluate_plant(fs, b, products={"BZ": ("D", "BZ"), "TOL": ("B", "TOL")}, feeds=["F"], keys={"COL": ("BZ", "TOL")})
    return fs, b, m


def test_workbook_has_sheets_and_live_formulas(tmp_path, evaluated):
    fs, b, m = evaluated
    path = export_workbook(tmp_path / "design.xlsx", m, b, fs=fs, streams={"F": "Feed", "D": "Benzene", "B": "Toluene"})
    wb = openpyxl.load_workbook(path)
    assert {"Summary", "Equipment", "Utilities", "Operating cost", "Cash flow", "Streams"} <= set(wb.sheetnames)
    eq = wb["Equipment"]
    formulas = [c.value for row in eq.iter_rows() for c in row if isinstance(c.value, str) and c.value.startswith("=")]
    assert any("SUM(" in f for f in formulas) and any("1.18" in f for f in formulas)
    cf = wb["Cash flow"]
    cells = [c.value for row in cf.iter_rows() for c in row if isinstance(c.value, str)]
    assert any(v.startswith("=IRR(") for v in cells) and any("NPV(" in v for v in cells)
    assert wb["Operating cost"]["B2"].value is not None


def test_check_values_match_python(tmp_path, evaluated):
    fs, b, m = evaluated
    wb = openpyxl.load_workbook(export_workbook(tmp_path / "d.xlsx", m, b, fs=fs))
    summ = {r[0].value: r[1].value for r in wb["Summary"].iter_rows(min_row=2) if r[0].value}
    assert summ["NPV (Refluxion)"] == pytest.approx(m["npv"]) and summ["Grassroots capital (Refluxion)"] == pytest.approx(m["capital"])


def _evaluate_workbook(wb):
    """Tiny evaluator for the formulas export_workbook writes: refs (Sheet!A1, 'Sheet name'!A1, $A$1), + - * / ^,
    SUM, SUMIF, NPV, IRR. Enough to check the workbook reproduces Refluxion's numbers without Excel."""
    import re
    from scipy.optimize import brentq
    cache = {}

    def rng(sheet, a, b):
        ws = wb[sheet]
        return [[val(sheet, c.coordinate) for c in row] for row in ws[a:b]]

    def val(sheet, coord):
        key = (sheet, coord.replace("$", ""))
        if key in cache:
            return cache[key]
        v = wb[sheet][key[1]].value
        if isinstance(v, str) and v.startswith("="):
            v = ev(sheet, v[1:])
        cache[key] = v if v is not None else 0.0
        return cache[key]

    def flat(x):
        return [z for row in x for z in row]

    def ev(sheet, f):
        ref = r"(?:(?:'([^']+)'|([A-Za-z][\w ]*))!)?\$?([A-Z]+)\$?(\d+)"
        def rng_sub(mo):
            sh = mo.group(1) or mo.group(2) or sheet
            return f"R({sh!r},'{mo.group(3)}{mo.group(4)}','{mo.group(7)}{mo.group(8)}')"
        f = re.sub(ref + r":" + ref, rng_sub, f)
        def cell_sub(mo):
            sh = mo.group(1) or mo.group(2) or sheet
            return f"V({sh!r},'{mo.group(3)}{mo.group(4)}')"
        f = re.sub(r"(?<![\w'])" + ref, cell_sub, f)
        f = f.replace("^", "**")
        env = dict(V=val, R=lambda s, a, b: rng(s, a, b), SUM=lambda r: sum(z or 0 for z in flat(r)),
                   SUMIF=lambda crit, c, r: sum((y or 0) for x, y in zip(flat(crit), flat(r)) if x == c),
                   NPV=lambda i, r: sum(c / (1 + i) ** (k + 1) for k, c in enumerate(flat(r))),
                   IRR=lambda r: brentq(lambda i: sum(c / (1 + i) ** k for k, c in enumerate(flat(r))), -0.99, 10))
        return eval(f, env)

    return val


def test_formulas_reproduce_python_results(tmp_path, evaluated):
    fs, b, m = evaluated
    wb = openpyxl.load_workbook(export_workbook(tmp_path / "e.xlsx", m, b, fs=fs))
    val = _evaluate_workbook(wb)
    cf = wb["Cash flow"]
    npv_row = next(r for r in range(1, cf.max_row + 1) if cf[f"A{r}"].value == "NPV")
    assert val("Cash flow", f"B{npv_row}") == pytest.approx(m["npv"], rel=1e-6)
    assert val("Cash flow", f"B{npv_row + 1}") == pytest.approx(m["irr"], rel=1e-6)
    assert val("Operating cost", "B2") == pytest.approx(m["capital"], rel=1e-9)
    assert val("Operating cost", "B8") == pytest.approx(m["opex_per_yr"], rel=1e-9)

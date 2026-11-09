"""Plant-level evaluation from any flowsheet: equipment found by block type, utilities, capital, DCF economics."""

from __future__ import annotations

import pytest

from refluxion import Flowsheet, RadFrac
from refluxion.design.plant import PlantBasis, evaluate_plant
from refluxion.utilities import Utility

K = 273.15
CW = Utility("CW", "cooling", T_in=30 + K, T_out=40 + K, unit_price=0.03e-3, basis="kg", cp=4180.0)
LPS = Utility("LPS", "heating", T_in=160 + K, T_out=160 + K, unit_price=20e-3, basis="kg", latent=2080e3)


def tiny():
    fs = Flowsheet("t", components=[("BZ", "benzene"), ("TOL", "toluene")], method="IDEAL", units="SI-KW")
    fs.feed("F", T=25, P=1.2, flows={"BZ": 500.0, "TOL": 500.0}, basis="mass")
    fs.add(RadFrac("COL", n_stages=20, condenser="total", reboiler="kettle", P_top=1.2, feeds={"F": 10},
                   specs={"reflux_ratio": 2.0, "distillate_mass_rate": 495.0}), inlets=["F"], outlets={"distillate": "D", "bottoms": "B"})
    return fs.run()


def basis():
    return PlantBasis(cooling=[CW], heating=[LPS], electricity=Utility("E", "electricity", price=20.0), dTmin=10.0,
                      U={"condenser": 850.0, "reboiler": 1140.0, "liquid": 850.0}, cepci=800.0, hours=8000.0, tax=0.3,
                      marr=0.1, life=10, macrs_years=7, wc_frac=0.15, wage=60_000.0, price={"BZ": 1.0, "TOL": 0.8})


def test_evaluate_plant_finds_column_equipment_and_utilities():
    m = evaluate_plant(tiny(), basis(), products={"BZ": ("D", "BZ"), "TOL": ("B", "TOL")}, feeds=["F"], keys={"COL": ("BZ", "TOL")})
    items = {r["item"] for r in m["equipment"]}
    assert {"COL tower", "COL trays", "COL condenser", "COL reboiler", "COL reflux drum", "F tank", "D tank", "B tank"} <= items
    assert m["utility_COL condenser"] == "CW" and m["utility_COL reboiler"] == "LPS"
    assert m["value_per_yr"] == pytest.approx((m["product_kg_h"]["BZ"] * 1.0 + m["product_kg_h"]["TOL"] * 0.8) * 8000.0)
    assert m["capital"] > 0 and m["npv"] == pytest.approx(m["cash_flow"].npv)


def test_no_utility_cold_enough_is_an_infeasible_design():
    b = basis()
    b.cooling = [Utility("hot CW", "cooling", T_in=90 + K, T_out=100 + K, unit_price=0.03e-3, basis="kg", cp=4180.0)]
    with pytest.raises(ValueError, match="no cooling utility"):
        evaluate_plant(tiny(), b, products={"BZ": ("D", "BZ")}, feeds=["F"], keys={"COL": ("BZ", "TOL")})


def test_flash_drum_duty_is_priced_and_gets_an_exchanger():
    from refluxion import Flash2
    fs = Flowsheet("f", components=[("BZ", "benzene"), ("TOL", "toluene")], method="IDEAL", units="SI-KW")
    fs.feed("F", T=25, P=1.0, flows={"BZ": 500.0, "TOL": 500.0}, basis="mass")
    fs.add(Flash2("FL", T=95.0, P=1.0), inlets=["F"], outlets=["V", "L"])
    fs.run()
    m = evaluate_plant(fs, basis(), products={"BZ": ("V", "BZ")}, feeds=["F"])
    assert "FL heater" in {r["item"] for r in m["equipment"]}
    row = [r for r in m["utilities"] if r["item"] == "FL heater"][0]
    assert row["utility"] == "LPS" and row["duty_W"] == pytest.approx(fs["FL"].result("duty", units="W"))

"""Packed-column sizing (structured packing, F-factor method) and the cheaper-internals choice in plant evaluation."""

from __future__ import annotations

import math

import pytest

from refluxion import Flowsheet, RadFrac
from refluxion.design.plant import PlantBasis, evaluate_plant
from refluxion.design.sizing import packed_column_diameter, size_packed_column
from refluxion.utilities import Utility

K = 273.15


def test_f_factor_diameter():
    V, rhoV = 0.5, 2.0                                    # kg/s, kg/m3
    D = packed_column_diameter(V, rhoV, F=2.5)
    u = 2.5 / math.sqrt(rhoV)
    assert D == pytest.approx(math.sqrt(4 * (V / rhoV / u) / math.pi), rel=1e-12)


def column():
    fs = Flowsheet("t", components=[("BZ", "benzene"), ("TOL", "toluene")], method="IDEAL", units="SI-KW")
    fs.feed("F", T=25, P=1.2, flows={"BZ": 500.0, "TOL": 500.0}, basis="mass")
    fs.add(RadFrac("COL", n_stages=20, condenser="total", reboiler="kettle", P_top=1.2, feeds={"F": 10},
                   specs={"reflux_ratio": 2.0, "distillate_mass_rate": 495.0}), inlets=["F"], outlets={"distillate": "D", "bottoms": "B"})
    return fs.run()


def test_packed_height_uses_hetp_beds_and_allowances():
    fs = column()
    r = size_packed_column(fs["COL"], F=2.5, hetp=0.48, bed_max=6.0)
    packed = 18 * 0.48
    beds = math.ceil(packed / 6.0)
    assert r["packed_height_m"] == pytest.approx(packed) and r["beds"] == beds
    assert r["height_m"] == pytest.approx(packed + 0.6 * beds + 1.2 + 1.8)
    assert r["diameter_m"] > 0 and "Mellapak" in r["basis"]


def test_plant_picks_cheaper_internals():
    b = PlantBasis(cooling=[Utility("CW", "cooling", T_in=30 + K, T_out=40 + K, unit_price=3e-5, basis="kg", cp=4180.0)],
                   heating=[Utility("LPS", "heating", T_in=160 + K, T_out=160 + K, unit_price=0.02, basis="kg", latent=2080e3)],
                   electricity=Utility("E", "electricity", price=20.0), price={"BZ": 1.0, "TOL": 0.8}, internals="cheaper")
    m = evaluate_plant(column(), b, products={"BZ": ("D", "BZ")}, feeds=["F"], keys={"COL": ("BZ", "TOL")})
    assert m["COL_internals"] in ("trays", "packing")
    items = {r["item"] for r in m["equipment"]}
    assert ("COL packing" in items) == (m["COL_internals"] == "packing")
    b.internals = "trays"
    mt = evaluate_plant(column(), b, products={"BZ": ("D", "BZ")}, feeds=["F"], keys={"COL": ("BZ", "TOL")})
    b.internals = "packing"
    mp = evaluate_plant(column(), b, products={"BZ": ("D", "BZ")}, feeds=["F"], keys={"COL": ("BZ", "TOL")})
    assert m["bare_module"] == pytest.approx(min(mt["bare_module"], mp["bare_module"]), rel=1e-9)

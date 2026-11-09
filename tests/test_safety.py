"""Safety checks: ideal solubility, vacuum vent load, flash points, MAWP table, HAZOP node scaffold."""

from __future__ import annotations

import math

import pytest

from refluxion import Flowsheet, Heater
from refluxion.design.safety import flash_point_check, hazop_nodes, ideal_solubility, mawp_table, vent_load


def test_vacuum_vent_load_hand_calculation():
    fs = Flowsheet("v", components=[("ACN", "acetonitrile")], method="IDEAL", units="SI-KW")
    Ps = float(fs.comps.Psat(263.15)[0])                                 # Pa at -10 C
    n_air = 2.0 / 28.96                                                  # kmol/h of air leaking in
    expect = n_air * Ps / (10e3 - Ps) * 41.05
    assert vent_load(fs, "ACN", P_kPa=10.0, T_vent_C=-10.0, air_leak_kg_h=2.0) == pytest.approx(expect, rel=0.02)


def test_flash_point_check_flags_hot_product():
    fs = Flowsheet("f", components=[("TOL", "toluene")], method="IDEAL", units="SI-KW")
    fs.feed("F", T=110, P=1.0, flows={"TOL": 1.0})
    fs.add(Heater("H", T=45, P=0), inlets=["F"], outlets=["P"])
    fs.run()
    rows = flash_point_check(fs, {"P": 4.4})
    assert rows[0]["ok"] is False and rows[0]["T_C"] == pytest.approx(45.0)


def test_mawp_table_applies_minimum_and_vacuum():
    rows = mawp_table([("COLUMN-1", -0.91, 58.0), ("COLUMN2", 0.0, 138.0), ("PUMP", 2.0, 60.0)])
    c1 = rows[0]
    assert c1["design_P_barg"] == pytest.approx(3.45) and c1["full_vacuum"] is True
    assert rows[2]["design_P_barg"] == pytest.approx(max(2.0 * 1.1, 2.0 + 1.72, 3.45))
    assert rows[1]["design_T_C"] == pytest.approx(138.0 + 28.0)


def test_hazop_nodes_cover_every_block():
    fs = Flowsheet("f", components=[("TOL", "toluene")], method="IDEAL", units="SI-KW")
    fs.feed("F", T=110, P=1.0, flows={"TOL": 1.0})
    fs.add(Heater("H", T=45, P=0), inlets=["F"], outlets=["P"])
    fs.run()
    nodes = hazop_nodes(fs)
    assert [n["node"] for n in nodes] == ["H"] and "No flow" in nodes[0]["deviations"]


def test_fmea_rows_cover_each_equipment_type():
    from refluxion import Pump, RadFrac
    from refluxion.design.safety import fmea_rows
    fs = Flowsheet("f", components=[("BZ", "benzene"), ("TOL", "toluene")], method="IDEAL", units="SI-KW")
    fs.feed("F", T=25, P=1.2, flows={"BZ": 50.0, "TOL": 50.0})
    fs.add(RadFrac("COL", n_stages=10, condenser="total", reboiler="kettle", P_top=1.2, feeds={"F": 5},
                   specs={"reflux_ratio": 2.0, "distillate_rate": 50.0}), inlets=["F"], outlets={"distillate": "D", "bottoms": "B"})
    fs.add(Pump("P1", P=3.0, eff=0.6), inlets=["B"], outlets=["B2"])
    fs.run()
    rows = fmea_rows(fs)
    eq = {r["equipment"] for r in rows}
    assert {"COL", "COL condenser", "COL reboiler", "P1"} <= eq
    for r in rows:
        assert r["failure_mode"] and r["effect"] and r["detection"] and r["S"] is None    # severity etc. left for the team


def test_isd_facts_name_the_four_principles():
    from refluxion.design.safety import isd_facts
    rows = isd_facts(dict(largest_tank_m3=20.0, tank_volumes_m3={"A": 8.0, "W": 20.0}, T_range_C=(20.0, 140.0)),
                     max_P_barg=0.0, min_P_barg=-0.9, flammables=["ACN", "TOL"])
    assert [r["principle"] for r in rows] == ["Minimize", "Substitute", "Moderate", "Simplify"]
    assert "20" in rows[0]["evidence"] and "vacuum" in rows[2]["evidence"].lower()


def test_one_bar_absolute_is_not_vacuum_service():
    rows = mawp_table([("C2", 1.0 - 1.01325, 137.0), ("C1", -0.81, 74.0)])
    assert rows[0]["full_vacuum"] is False and rows[1]["full_vacuum"] is True

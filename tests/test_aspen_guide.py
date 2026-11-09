"""The 'Set it up in Aspen Plus' guide covers the plant-design blocks and specs."""

from __future__ import annotations

from refluxion import Flash2, Flowsheet, RadFrac, UtilityHX
from refluxion.report.aspen import aspen_setup_md
from refluxion.utilities import Utility

CW = Utility("Cooling water", "cooling", T_in=308.15, T_out=318.15, unit_price=3e-5, basis="kg", cp=4180.0)


def test_guide_explains_utility_hx_mass_specs_and_flash_spec():
    fs = Flowsheet("g", components=[("TOL", "toluene"), ("XYL", "p-xylene"), ("C10", "n-decane")], method="IDEAL", units="SI-KW")
    fs.feed("F", T=25, P=1.0, flows={"TOL": 100.0, "XYL": 100.0, "C10": 10.0}, basis="mass")
    fs.add(RadFrac("COL", n_stages=15, condenser="total", reboiler="kettle", P_top=1.0, feeds={"F": 8},
                   specs={"reflux_ratio_mass": 2.0, "distillate_mass_rate": 100.0}), inlets=["F"], outlets={"distillate": "D", "bottoms": "B"})
    fs.add(UtilityHX("COOL", utility=CW, T=45.0, P=0.0, U=850.0), inlets=["D"], outlets=["D2"])
    fs.add(Flash2("FL", P=1.0, liquid_spec=("C10", 0.25)), inlets=["B"], outlets=["V", "L"])
    md = aspen_setup_md(fs)
    assert "Mass" in md and "Distillate rate" in md                           # D-BASIS = MASS
    assert "HeatX" in md and "Cooling water" in md and "Design Spec" in md    # utility side + spec on its flow
    assert "vary the flash temperature" in md.lower() and "C10" in md

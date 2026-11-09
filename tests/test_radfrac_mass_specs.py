"""RadFrac operating specs on a mass basis (Aspen's D-BASIS = MASS, RR-BASIS = MASS, mass purity)."""

from __future__ import annotations

import pytest

from refluxion import Flowsheet, RadFrac

COMPS6 = [("C3", "propane"), ("IC4", "isobutane"), ("NC4", "n-butane"), ("IC5", "isopentane"), ("NC5", "n-pentane"), ("NC6", "n-hexane")]
FEED6 = {"C3": .05, "IC4": .1, "NC4": .3, "IC5": .2, "NC5": .15, "NC6": .2}


def column(specs):
    fs = Flowsheet("mass specs", components=COMPS6, method="PENG-ROB", units="MET")
    fs.feed("FEED", P=4.4, vf=0.0, total=100, fractions=FEED6)
    col = fs.add(RadFrac("COL", n_stages=24, condenser="total", reboiler="kettle", feeds={"FEED": 12}, P_top=4.4, specs=specs),
                 inlets=["FEED"], outlets={"distillate": "OVHD", "bottoms": "BOTTOMS"})
    fs.run()
    return fs, col


def test_distillate_mass_rate_reproduces_molar_case():
    fs, col = column({"reflux_ratio": 1.8, "distillate_to_feed": 0.45})
    D_mass = fs["OVHD"].get("mass_flow")
    split = col.tables["split_fractions"]["distillate"].copy()
    assert col.result("distillate_mass") == pytest.approx(D_mass, rel=1e-9)
    fs2, col2 = column({"reflux_ratio_mass": 1.8, "distillate_mass_rate": D_mass})
    assert fs2["OVHD"].get("mass_flow") == pytest.approx(D_mass, rel=1e-8)
    assert col2.tables["split_fractions"]["distillate"] == pytest.approx(split, abs=1e-6)


def test_mass_purity_spec_is_met():
    fs, col = column({"reflux_ratio": 2.0, ("mass_purity", "NC4", "distillate"): 0.60})
    assert fs["OVHD"].get("mass_frac", "NC4") == pytest.approx(0.60, abs=1e-7)


def test_mass_comp_flow_spec_is_met():
    fs, col = column({"reflux_ratio": 2.0, ("mass_comp_flow", "NC4", "distillate"): 1500.0})
    assert fs["OVHD"].get("mass_flow", "NC4") == pytest.approx(1500.0, rel=1e-7)

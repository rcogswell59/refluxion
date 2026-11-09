"""Benchmarks against Aspen Plus answers for classic Aspen Plus training workshops.

Tolerances are deliberately realistic: Refluxion uses open property data (ChemSep kij, DIPPR/TRC
correlations), so results should be *close* to Aspen, not identical. Each test says what Aspen got.
Run:  .venv/Scripts/python -m pytest -q
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from refluxion import DSTWU, Flash2, Flowsheet, Heater, RadFrac, RStoic
from refluxion.modules import pinch_analysis
from refluxion.tools import optimize, sensitivity


def rel(a, b):
    return abs(a - b) / abs(b)


# ---------------------------------------------------------------------------------------------
# manual chapter 3: benzene flashes, PENG-ROB

def test_benzene_workshop():
    fs = Flowsheet("Benzene", components=[("H2", "hydrogen"), ("CH4", "methane"), ("BENZENE", "benzene"), ("TOLUENE", "toluene")],
                   method="PENG-ROB", units="ENG")
    fs.feed("FEED", T=1000, P=550, flows={"H2": 405, "CH4": 95, "BENZENE": 95, "TOLUENE": 5})
    fs.add(Heater("COOL", T=200, dP=0), inlets=["FEED"], outlets=["COOL-OUT"])
    fs.add(Flash2("FL1", T=100, P=500), inlets=["COOL-OUT"], outlets=["V1", "L1"])
    fs.add(Flash2("FL2", Q=0, P=(1, "atm")), inlets=["L1"], outlets=["V2", "L2"])
    fs.run()
    assert rel(fs["COOL"].result("vf"), 0.869327) < 0.01           # Aspen 0.869327
    assert rel(fs["COOL"].result("duty"), -7.19654e6) < 0.02        # Aspen -7.19654e6 Btu/hr
    assert rel(fs["FL1"].result("vf"), 0.836207) < 0.005            # Aspen 0.836207
    assert rel(fs["FL1"].result("duty"), -930973) < 0.05            # Aspen -930973 Btu/hr
    assert abs(fs["FL2"].result("T") - 99.8298) < 0.5               # Aspen 99.83 F
    assert rel(fs["FL2"].result("vf"), 0.0281058) < 0.25            # Aspen 0.0281 (sensitive to H2 kij)
    for r in fs.balance_check():
        assert abs(r["mass"]) < 1e-9 and abs(r["energy"]) < 1e-6


# ---------------------------------------------------------------------------------------------
# manual chapters 3 & 10: cumene reactor-flash-recycle, RK-SOAVE

def cumene_flowsheet():
    fs = Flowsheet("Cumene", components=[("BENZENE", "benzene"), ("PROPYLEN", "propylene"), ("CUMENE", "cumene")],
                   method="RK-SOAVE", units="ENG")
    fs.feed("FEED", T=220, P=36, flows={"BENZENE": 40, "PROPYLEN": 40})
    fs.add(RStoic("REACTOR", Q=0, P=0, reactions=[dict(stoich={"BENZENE": -1, "PROPYLEN": -1, "CUMENE": 1},
                                                      key="PROPYLEN", conversion=0.9)]),
           inlets=["FEED", "RECYCLE"], outlets=["REAC-OUT"])
    fs.add(Heater("COOL", T=130, dP=0.1), inlets=["REAC-OUT"], outlets=["COOL-OUT"])
    fs.add(Flash2("SEP", P=(1, "atm"), Q=0), inlets=["COOL-OUT"], outlets=["RECYCLE", "PRODUCT"])
    return fs


def test_cumene_recycle():
    fs = cumene_flowsheet().run()
    assert fs.loops[0].converged and fs.loops[0].tears[0].name == "RECYCLE"
    assert abs(fs["PRODUCT"].frac("CUMENE") - 0.90552) < 0.003       # Aspen 0.90552
    assert abs(fs["PRODUCT"].get("T") - 130.054) < 0.5              # Aspen 130.054 F
    assert abs(fs.overall_balance()["rel_error"]) < 1e-6


def test_design_spec():
    fs = cumene_flowsheet()
    fs.design_spec("DS-1", measure=lambda fs: fs["PRODUCT"].frac("CUMENE"), target=0.98, tol=1e-4,
                   vary="COOL.T", lower=100, upper=300)
    fs.run()
    assert abs(fs["PRODUCT"].frac("CUMENE") - 0.98) <= 1.1e-4
    assert 100 < fs["COOL"].spec["T"] < 300


def test_sensitivity_restores_base_case():
    fs = cumene_flowsheet().run()
    base = fs["PRODUCT"].frac("CUMENE")
    res = sensitivity(fs, {"COOL.T": [110, 150]}, {"x": lambda fs: fs["PRODUCT"].frac("CUMENE")})
    assert all(r["status"] == "ok" for r in res.rows)
    assert res.rows[0]["x"] < res.rows[1]["x"]          # hotter flash -> purer liquid product
    assert fs["COOL"].spec["T"] == 130 and abs(fs["PRODUCT"].frac("CUMENE") - base) < 1e-4  # within loop tolerance


# ---------------------------------------------------------------------------------------------
# manual chapter 5: six-component DSTWU and the RadFrac built from it, PENG-ROB

COMPS6 = [("C3", "propane"), ("IC4", "isobutane"), ("NC4", "n-butane"), ("IC5", "isopentane"), ("NC5", "n-pentane"), ("NC6", "n-hexane")]
FEED6 = {"C3": .05, "IC4": .1, "NC4": .3, "IC5": .2, "NC5": .15, "NC6": .2}


def test_dstwu():
    fs = Flowsheet("DSTWU1", components=COMPS6, method="PENG-ROB", units="MET")
    fs.feed("FEED", P=4.4, vf=0.0, total=100, fractions=FEED6)
    b = fs.add(DSTWU("DSTWU", LK="NC4", HK="IC5", rec_LK=0.9908, rec_HK=0.01124, reflux_ratio=1.8, P_cond=4.4, P_reb=4.4),
               inlets=["FEED"], outlets=["OVHD", "BOTTOMS"])
    fs.run()
    assert rel(b.result("Rmin"), 1.3342) < 0.06          # Aspen 1.3342
    assert rel(b.result("Nmin"), 12.9098) < 0.06         # Aspen 12.9098 (Winn)
    assert rel(b.result("N"), 23.9878) < 0.05            # Aspen 23.9878
    assert abs(b.result("feed_stage") - 12.2632) < 1.0   # Aspen 12.2632 (Kirkbride)
    assert abs(b.result("T_D") - 308.2) < 1.0            # Aspen 308.2 K
    assert abs(b.result("T_B") - 367.152) < 1.0          # Aspen 367.152 K
    assert abs(b.result("D_F") - 0.449637) < 1e-3        # Aspen 0.449637
    # Aspen's screenshot duties (labelled cal/sec) match watts: 816145 and -746169
    assert rel(b.result("reboiler_duty", units="W"), 816145) < 0.10
    assert rel(b.result("condenser_duty", units="W"), -746169) < 0.10


@pytest.mark.parametrize("RR, nc4, ic5", [(1.8, 0.988722, 0.015302), (2.0, 0.992518, 0.010188)])
def test_radfrac_distillation(RR, nc4, ic5):
    fs = Flowsheet("RADFRAC", components=COMPS6, method="PENG-ROB", units="MET")
    fs.feed("FEED", P=4.4, vf=0.0, total=100, fractions=FEED6)
    col = fs.add(RadFrac("RADFRAC", n_stages=24, condenser="total", reboiler="kettle", feeds={"FEED": 12}, P_top=4.4,
                         specs={"reflux_ratio": RR, "distillate_to_feed": 0.45}),
                 inlets=["FEED"], outlets={"distillate": "OVHD", "bottoms": "BOTTOMS"})
    fs.run()
    sf = col.tables["split_fractions"]["distillate"]
    assert abs(sf[2] - nc4) < 0.005 and abs(sf[3] - ic5) < 0.005     # Aspen split fractions to OVHD
    assert abs(col.result("distillate") - 45.0) < 1e-6
    assert abs(col.energy_balance_error()) < 1e-8 and abs(col.mass_balance_error()) < 1e-10
    assert col.iterations <= 10


def test_radfrac_recovery_spec():
    """Aspen's 'design spec + vary' inside RadFrac: hit a recovery by freeing the distillate rate."""
    fs = Flowsheet("RADFRAC", components=COMPS6, method="PENG-ROB", units="MET")
    fs.feed("FEED", P=4.4, vf=0.0, total=100, fractions=FEED6)
    col = fs.add(RadFrac("RADFRAC", n_stages=24, condenser="total", reboiler="kettle", feeds={"FEED": 12}, P_top=4.4,
                         specs={"reflux_ratio": 2.0, ("recovery", "NC4", "distillate"): 0.995}),
                 inlets=["FEED"], outlets={"distillate": "OVHD", "bottoms": "BOTTOMS"})
    fs.run()
    assert abs(col.tables["split_fractions"]["distillate"][2] - 0.995) < 1e-6




# ---------------------------------------------------------------------------------------------
# manual chapter 9: DCM steam-stripping optimization with Aspen's own NRTL parameters

def test_dcm_optimization():
    nrtl = {("WATER", "DCM"): dict(a=0.0, b=(2670.95376, "R"), c=0.3), ("DCM", "WATER"): dict(a=0.0, b=(1694.57184, "R"), c=0.3)}
    fs = Flowsheet("DCM", components=[("WATER", "water"), ("DCM", "dichloromethane")], method="NRTL", units="ENG", nrtl=nrtl)
    fs.feed("FEED", T=100, P=24, flows={"WATER": 98600, "DCM": 1400}, basis="mass")
    fs.feed("STEAM1", P=200, vf=1.0, flows={"WATER": 5000}, basis="mass")
    fs.feed("STEAM2", P=200, vf=1.0, flows={"WATER": 13445.6}, basis="mass")
    fs.add(Flash2("TOWER1", P=19.7, Q=0), inlets=["STEAM1", "FEED"], outlets=["TOP1", "BOT1"])
    fs.add(Flash2("TOWER2", P=18.7, Q=0), inlets=["STEAM2", "BOT1"], outlets=["TOP2", "EFFLUENT"])
    fs.run()
    ppm = lambda fs: fs["EFFLUENT"].get("mass_frac", "DCM") * 1e6
    assert abs(fs["STEAM1"].get("T") - 381.8) < 0.5                  # saturated steam at 200 psia
    assert rel(ppm(fs), 150.8) < 0.15                                # Aspen 150.8 ppm at its optimum
    res = optimize(fs, objective=lambda fs: fs["STEAM1"].get("mass_flow") + fs["STEAM2"].get("mass_flow"),
                   vary={"STEAM1.flows.WATER": (1000, 5000), "STEAM2.flows.WATER": (1000, 15000)},
                   constraints=[("DCM ppm", ppm, "<=", 150.0, 2.0)])
    assert res.success
    assert res.x["STEAM1.flows.WATER"] == pytest.approx(5000, rel=1e-3)   # Aspen: at the bound
    assert rel(res.x["STEAM2.flows.WATER"], 13445.6) < 0.08               # Aspen 13445.6 lb/hr


# ---------------------------------------------------------------------------------------------
# other blocks: energy closure and physics sanity

def test_pump_compressor_valve_heatx():
    from refluxion import Compr, HeatX, Pump, Valve

    fs = Flowsheet("misc", components=[("WATER", "water"), ("CH4", "methane"), ("C3", "propane")], method="PENG-ROB", units="SI-KW")
    fs.feed("W", T=25, P=1, flows={"WATER": 100})
    fs.feed("G", T=25, P=1, flows={"CH4": 100})
    fs.feed("HOT", T=150, P=5, flows={"C3": 50})
    fs.add(Pump("P1", P=10, eff=0.7), inlets=["W"], outlets=["W2"])
    fs.add(Compr("K1", P=3, eff=0.72), inlets=["G"], outlets=["G2"])
    fs.add(Valve("V1", P=1.0), inlets=["G2"], outlets=["G3"])
    fs.add(HeatX("E1", hot_T_out=60), inlets={"hot_in": "HOT", "cold_in": "W2"}, outlets={"hot_out": "HOT2", "cold_out": "W3"})
    fs.run()
    # pump: fluid power = v * dP * F
    p = fs["P1"]
    assert p.result("fluid_power", units="kW") == pytest.approx(100 / 3.6 * 18.015e-6 * 9e5 / 1000 * 1000 / 1000, rel=0.05)
    # compressor: ideal-gas check T2s = T1 (P2/P1)^(R/Cp) with Cp ~ 35.7 J/mol-K for methane at 25-100 C
    T2s = fs["K1"].result("T_isentropic", units="K")
    assert 360 < T2s < 385
    assert fs["G3"].get("T", units="K") < fs["G2"].get("T", units="K")      # Joule-Thomson cooling
    assert fs["E1"].result("min_approach") > 0 and np.isfinite(fs["E1"].result("area"))
    for r in fs.balance_check():
        assert abs(r["energy"]) < 1e-6, r


def test_report_files(tmp_path):
    fs = cumene_flowsheet().run()
    rep = fs.report(title="Cumene", problem="Test problem.")
    rep.highlight("x cumene", fs["PRODUCT"].frac("CUMENE"), ">=", 0.9)
    paths = rep.save(tmp_path / "cumene")
    html = open(paths["html"], encoding="utf-8").read()
    md = open(paths["markdown"], encoding="utf-8").read()
    assert "<svg class=\"pfd\"" in html and "unit-select" in html and "Stream results" in html
    assert "![Flowsheet]" in md and "| Temperature (F) |" in md.replace("  ", " ")
    assert (tmp_path / "cumene_assets" / "flowsheet.svg").exists()

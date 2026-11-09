"""Aspen training manual, chapter 9: minimize steam in a two-stage DCM steam stripper (CAD0016), NRTL.

Uses Aspen's own NRTL binary parameters from the manual's input file (in ENG units, so b is in Rankine).
"""

from pathlib import Path

from refluxion import Flash2, Flowsheet
from refluxion.tools import optimize, sensitivity

NRTL = {("WATER", "DCM"): dict(a=0.0, b=(2670.95376, "R"), c=0.3),
        ("DCM", "WATER"): dict(a=0.0, b=(1694.57184, "R"), c=0.3)}

fs = Flowsheet("DCM solvent recovery (CAD0016)", components=[("WATER", "water"), ("DCM", "dichloromethane")],
               method="NRTL", units="ENG", nrtl=NRTL)
fs.feed("FEED", T=100, P=24, flows={"WATER": 98600, "DCM": 1400}, basis="mass")
fs.feed("STEAM1", P=200, vf=1.0, flows={"WATER": 5000}, basis="mass")
fs.feed("STEAM2", P=200, vf=1.0, flows={"WATER": 15000}, basis="mass")
fs.add(Flash2("TOWER1", P=19.7, Q=0), inlets=["STEAM1", "FEED"], outlets=["TOP1", "BOT1"])
fs.add(Flash2("TOWER2", P=18.7, Q=0), inlets=["STEAM2", "BOT1"], outlets=["TOP2", "EFFLUENT"])
fs.run()


def ppm(fs):
    return fs["EFFLUENT"].get("mass_frac", "DCM") * 1e6


sens = sensitivity(fs, {"STEAM2.flows.WATER": dict(lower=8000, upper=18000, points=11)}, {"DCM in effluent": ppm})
res = optimize(fs, objective=lambda fs: fs["STEAM1"].get("mass_flow") + fs["STEAM2"].get("mass_flow"),
               vary={"STEAM1.flows.WATER": (1000, 5000), "STEAM2.flows.WATER": (1000, 15000)},
               constraints=[("DCM in effluent (ppm)", ppm, "<=", 150.0, 2.0)], sense="min")

rep = fs.report(
    title="DCM steam-stripping optimization",
    subtitle="Two adiabatic flash towers heated by live steam - manual chapter 9",
    problem="""98,600 lb/hr water with 1,400 lb/hr dichloromethane (100 F, 24 psia) is stripped with 200 psia saturated
steam in two adiabatic flash towers (19.7 and 18.7 psia). Minimize total steam (STEAM1 + STEAM2) while keeping
DCM in the effluent at or below 150 ppm (mass).""",
    approach="""**NRTL** - water and DCM are strongly nonideal (DCM's infinite-dilution activity coefficient in water is
in the hundreds). Parameters are the ones in Aspen's input file.
**Optimization:** SQP (SLSQP) on scaled variables, exactly the algorithm Aspen uses by default.""")
rep.highlight("Minimum total steam", res.objective, unit="lb/hr", note="Aspen: 5,000 + 13,446 = 18,446 lb/hr")
rep.highlight("STEAM2 at optimum", res.x["STEAM2.flows.WATER"], unit="lb/hr", note="Aspen: 13,445.6 lb/hr")
rep.highlight("DCM in effluent", ppm(fs), "<=", 150.0, unit="ppm")
rep.optimization(res)
rep.sensitivity(sens, title="Sensitivity: second-tower steam", x_label="STEAM2 (lb/hr)",
                targets={"DCM in effluent": 150.0}, y_labels={"DCM in effluent": "DCM in effluent (ppm by mass)"})
print(rep.save(Path(__file__).parent / "reports" / "04_dcm_optimization"))

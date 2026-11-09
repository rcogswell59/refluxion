"""Aspen training manual, chapters 3 and 10: cumene production (CAD0001), RK-SOAVE.

RStoic -> cooler -> flash, with the flash vapor recycled to the reactor. Shows: tear-stream
convergence (Wegstein), a sensitivity analysis, and a design spec that finds the cooler temperature
giving 98 mol% cumene.
"""

from pathlib import Path

from refluxion import Flash2, Flowsheet, Heater, RStoic
from refluxion.tools import sensitivity


def build():
    fs = Flowsheet("Cumene production (CAD0001)",
                   components=[("BENZENE", "benzene"), ("PROPYLEN", "propylene"), ("CUMENE", "cumene")],
                   method="RK-SOAVE", units="ENG")
    fs.feed("FEED", T=220, P=36, flows={"BENZENE": 40, "PROPYLEN": 40})
    fs.add(RStoic("REACTOR", Q=0, P=0, reactions=[dict(stoich={"BENZENE": -1, "PROPYLEN": -1, "CUMENE": 1},
                                                      key="PROPYLEN", conversion=0.9)]),
           inlets=["FEED", "RECYCLE"], outlets=["REAC-OUT"])
    fs.add(Heater("COOL", T=130, dP=0.1), inlets=["REAC-OUT"], outlets=["COOL-OUT"])
    fs.add(Flash2("SEP", P=(1, "atm"), Q=0), inlets=["COOL-OUT"], outlets=["RECYCLE", "PRODUCT"])
    return fs


fs = build().run()
sens = sensitivity(fs, {"COOL.T": dict(lower=90, upper=270, points=10)},
                   {"Cumene mole fraction": lambda fs: fs["PRODUCT"].frac("CUMENE"),
                    "Recycle flow": lambda fs: fs["RECYCLE"].flow()})
base_x = fs["PRODUCT"].frac("CUMENE")

ds = build()
ds.design_spec("DS-1", measure=lambda fs: fs["PRODUCT"].frac("CUMENE"), target=0.98, tol=1e-4,
               vary="COOL.T", lower=100, upper=300)
ds.run()

rep = fs.report(
    title="Cumene production workshop",
    subtitle="Reactor-flash-recycle loop, sensitivity and design spec - Aspen training manual chapters 3 and 10",
    problem="""Benzene and propylene (40 lbmol/hr each, 220 F, 36 psia) react to cumene in an adiabatic RStoic with
90% propylene conversion. The effluent is cooled to 130 F and flashed at 1 atm; the vapor is recycled.

- What is the cumene purity of PRODUCT?
- How does the cooler temperature change it (sensitivity)?
- What cooler temperature gives 98 mol% cumene (design spec)?""",
    approach="""**RK-SOAVE** - nonpolar hydrocarbons at low pressure; an EOS is the natural choice.
The recycle makes a loop, so the solver tears RECYCLE and converges it with Wegstein acceleration.""")
rep.highlight("Cumene in PRODUCT", base_x, note="Aspen: 0.90552")
rep.highlight("Cooler T for 98 mol% cumene", ds["COOL"].spec["T"], unit="F", note="design spec DS-1")
rep.sensitivity(sens, title="Sensitivity: cooler temperature", targets={"Cumene mole fraction": 0.98},
                y_labels={"Recycle flow": "Recycle flow (lbmol/hr)"}, x_label="Cooler outlet temperature (F)")
rep.text("What the numbers say", f"""A hotter flash leaves more benzene and propylene in the vapor, so the liquid product gets purer
but more material recycles. The design spec finds **{ds['COOL'].spec['T']:.1f} F** for 98 mol% cumene
({ds.design_specs[0].result.get('iterations', '?')} flowsheet solves, secant method).""")
print(rep.save(Path(__file__).parent / "reports" / "02_cumene_recycle"))

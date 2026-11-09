"""Aspen training manual, chapter 3: Benzene flowsheet workshop (CAD0002), PENG-ROB.

A hot hydrogen/methane/benzene/toluene stream is cooled, flashed at high pressure, and the liquid
is flashed adiabatically to 1 atm. Aspen's answers are shown on the key-result cards.
"""

from pathlib import Path

from refluxion import Flash2, Flowsheet, Heater

fs = Flowsheet("Benzene flowsheet (CAD0002)",
               components=[("H2", "hydrogen"), ("CH4", "methane"), ("BENZENE", "benzene"), ("TOLUENE", "toluene")],
               method="PENG-ROB", units="ENG")
fs.feed("FEED", T=1000, P=550, flows={"H2": 405, "CH4": 95, "BENZENE": 95, "TOLUENE": 5})
fs.add(Heater("COOL", T=200, dP=0), inlets=["FEED"], outlets=["COOL-OUT"])
fs.add(Flash2("FL1", T=100, P=500), inlets=["COOL-OUT"], outlets=["V1", "L1"])
fs.add(Flash2("FL2", Q=0, P=(1, "atm")), inlets=["L1"], outlets=["V2", "L2"])
fs.run()

rep = fs.report(
    title="Benzene flowsheet workshop",
    subtitle="Cooler, high-pressure flash and adiabatic flash - Aspen training manual chapter 3 (CAD0002.bkp)",
    problem="""Feed at 1000 F and 550 psia: 405 H2, 95 CH4, 95 benzene, 5 toluene (lbmol/hr).
Cool to 200 F (no pressure drop), flash at 100 F and 500 psia, then flash the liquid adiabatically to 1 atm.

1. What is the heat duty of the COOL block?
2. What is the temperature in the second flash FL2?""",
    approach="""**Property method: PENG-ROB.** Light gases (H2, CH4) with hydrocarbons at high pressure - an equation of
state handles both phases and the supercritical gases. Binary kij from ChemSep (Aspen uses its PRKBV databank).""")
rep.highlight("COOL duty", fs["COOL"].results["duty"][1], quantity="duty", note="Aspen: -7,196,540 Btu/hr")
rep.highlight("FL1 vapor fraction", fs["FL1"].result("vf"), note="Aspen: 0.836207")
rep.highlight("FL2 temperature", fs["FL2"].results["T"][1], quantity="temperature", note="Aspen: 99.83 F")
rep.highlight("FL2 vapor fraction", fs["FL2"].result("vf"), note="Aspen: 0.0281058")
print(rep.save(Path(__file__).parent / "reports" / "01_benzene_flashes"))

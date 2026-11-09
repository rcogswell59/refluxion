"""Aspen training manual, chapter 5: shortcut design (DSTWU) then rigorous simulation (RadFrac).

Six light hydrocarbons, PENG-ROB. DSTWU sizes the column (Winn-Underwood-Gilliland), RadFrac checks it
stage by stage: at RR = 1.8 the key recoveries fall short; raising RR to 2.0 fixes them.
"""

from pathlib import Path

from refluxion import DSTWU, Flowsheet, RadFrac
from refluxion.tools import sensitivity

COMPS = [("C3", "propane"), ("IC4", "isobutane"), ("NC4", "n-butane"), ("IC5", "isopentane"), ("NC5", "n-pentane"), ("NC6", "n-hexane")]
FEED = {"C3": .05, "IC4": .1, "NC4": .3, "IC5": .2, "NC5": .15, "NC6": .2}

short = Flowsheet("DSTWU1", components=COMPS, method="PENG-ROB", units="MET")
short.feed("FEED", P=4.4, vf=0.0, total=100, fractions=FEED)
short.add(DSTWU("DSTWU", LK="NC4", HK="IC5", rec_LK=0.9908, rec_HK=0.01124, reflux_ratio=1.8, P_cond=4.4, P_reb=4.4),
          inlets=["FEED"], outlets=["OVHD", "BOTTOMS"])
short.run()
d = short["DSTWU"]

fs = Flowsheet("Six-component column (DSTWU1a)", components=COMPS, method="PENG-ROB", units="MET")
fs.feed("FEED", P=4.4, vf=0.0, total=100, fractions=FEED)
# Aspen's workshop rounds DSTWU's 23.99 stages to 24 and uses feed stage 12 (DSTWU: 12.26)
col = fs.add(RadFrac("COLUMN", n_stages=24, condenser="total", reboiler="kettle", feeds={"FEED": 12}, P_top=4.4,
                     specs={"reflux_ratio": 1.8, "distillate_to_feed": 0.45}),
             inlets=["FEED"], outlets={"distillate": "OVHD", "bottoms": "BOTTOMS"})
fs.run()
sf = col.tables["split_fractions"]["distillate"]
nc4_18, ic5_18 = sf[2], sf[3]
sens = sensitivity(fs, {"COLUMN.specs.reflux_ratio": dict(lower=1.6, upper=2.4, points=9)},
                   {"NC4 recovery to OVHD": lambda fs: fs["COLUMN"].tables["split_fractions"]["distillate"][2],
                    "IC5 to OVHD": lambda fs: fs["COLUMN"].tables["split_fractions"]["distillate"][3],
                    "Reboiler duty": lambda fs: fs["COLUMN"].result("reboiler_duty", units="kW")})

rep = short.report(title="Shortcut design: DSTWU", subtitle="Winn-Underwood-Gilliland with Kirkbride feed location (manual chapter 5)")
rep.highlight("Minimum reflux ratio", d.result("Rmin"), note="Aspen: 1.3342")
rep.highlight("Minimum stages", d.result("Nmin"), note="Aspen: 12.91")
rep.highlight("Actual stages", d.result("N"), note="Aspen: 23.99")
rep.highlight("Feed stage", d.result("feed_stage"), note="Aspen: 12.26")
print(rep.save(Path(__file__).parent / "reports" / "03a_dstwu"))

rep = fs.report(
    title="Rigorous column from the shortcut design",
    subtitle="RadFrac with the DSTWU stages and feed location (manual chapter 5)",
    problem="""Use the DSTWU results as the starting design for RadFrac: 24 stages (condenser = stage 1, reboiler = 24),
feed on stage 12, total condenser, RR = 1.8 and D/F = 0.45.
Targets from DSTWU: n-butane recovery overhead >= 0.9908, isopentane overhead <= 0.01124.
Do the rigorous results meet them? If not, which operating variable do you change?""")
rep.highlight("NC4 recovery overhead (RR 1.8)", nc4_18, ">=", 0.9908, note="Aspen: 0.988722")
rep.highlight("IC5 to overhead (RR 1.8)", ic5_18, "<=", 0.01124, note="Aspen: 0.015302")
rep.sensitivity(sens, title="Sensitivity: reflux ratio", x_label="Reflux ratio",
                targets={"NC4 recovery to OVHD": 0.9908, "IC5 to OVHD": 0.01124},
                y_labels={"Reboiler duty": "Reboiler duty (kW)"})
print(rep.save(Path(__file__).parent / "reports" / "03b_radfrac"))

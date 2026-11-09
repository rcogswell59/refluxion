"""<Problem title>

<One-paragraph summary of the problem and what is asked.>
Run from this folder:  ..\\..\\.venv\\Scripts\\python solve.py
"""

from pathlib import Path

from refluxion import Flash2, Flowsheet, Heater  # noqa: F401  (import the blocks you need)
from refluxion.tools import optimize, sensitivity  # noqa: F401

HERE = Path(__file__).parent

# 1. flowsheet --------------------------------------------------------------------------------
fs = Flowsheet("<name>", components=[("C3", "propane"), ("NC4", "n-butane")], method="PENG-ROB", units="ENG")
fs.feed("FEED", T=100, P=200, flows={"C3": 50, "NC4": 50})
fs.add(Flash2("F1", P=100, Q=0), inlets=["FEED"], outlets=["VAP", "LIQ"])
fs.run()

# 2. analyses (optional) -------------------------------------------------------------------------
# sens = sensitivity(fs, {"F1.P": dict(lower=50, upper=150, points=11)}, {"Vapor fraction": lambda fs: fs["F1"].result("vf")})

# 3. report ------------------------------------------------------------------------------------
rep = fs.report(
    title="<title>",
    subtitle="<short description>",
    problem="""<statement in Markdown: data, then numbered questions>""",
    approach="""**Property method:** <which and why>. **Assumptions:** <list>.""",
)
rep.highlight("<key result>", fs["VAP"].flow(), unit="lbmol/hr")
# rep.sensitivity(sens)
# rep.hand_calc("Hand check", "...")
rep.text("Answers", """1. <answer with numbers, units and the physics behind it>""")
print(rep.save(HERE / "reports" / "solution"))

# Refluxion

**A lightweight, Aspen-style process simulator in Python** - for learning how simulators like Aspen Plus
work inside, and for quickly exploring design problems before (or alongside) doing them in Aspen.

Drop a problem into a Claude Code session in this repo and ask it to solve it: Claude writes a short
script against this toolkit, runs it, checks it, and produces an interactive HTML report plus a
Markdown copy, with the flowsheet, stream table, block results, sensitivity/optimization and a
"set it up in Aspen Plus" guide.

> Results come from open property data (ChemSep, DIPPR/TRC correlations), so they are close to Aspen's,
> not identical. When an assignment asks for Aspen numbers, use Aspen for the final answer.

## What it can do (v0.2)

| Area | Refluxion | Aspen equivalent |
|---|---|---|
| Property methods | PENG-ROB, RK-SOAVE (Boston-Mathias alpha, Graboski-Daubert H2), RK, IDEAL, NRTL, NRTL-RK | same names |
| Components | databank retrieval, plus user-defined components in Aspen's correlation forms (PLXANT, DHVLDP / DIPPR 106, CPIG / DIPPR 107, polynomial) | user-defined / pseudo components |
| Parameters | ChemSep binaries, A_ji / alpha tables, NRTL regression to VLE data or to an Aspen file's stored stage profiles | Data Regression System |
| Flash | TP, PH, PS, P-VF (bubble/dew), T-VF; negative-flash phase detection; PH flash robust for near-azeotropic mixtures | flash specs on every block |
| Blocks | Mixer, FSplit, Sep, Dupl, Heater, Flash2 (incl. a liquid-composition spec), Pump, Compr, Valve, RStoic, HeatX (shortcut + zone analysis), UtilityHX (utility-side exchanger), DSTWU, RadFrac | model palette |
| RadFrac | total / partial-vapor / no condenser, kettle / no reboiler, stage heaters, above/on-stage feeds, pressure drop, Murphree efficiency, recovery / purity specs, mass-basis specs | RadFrac |
| Flowsheet | automatic sequencing, tear streams, Wegstein convergence, design specs, calculators | sequential-modular engine |
| Analysis | sensitivity (1 or 2 variables), case studies, SQP optimization with constraints, Txy/Pxy/xy | Model Analysis Tools |
| Modules | pinch analysis (problem table, composite and grand composite curves) from a flowsheet, column-to-column matches, approach-limited match duties; azeotrope finder | Aspen Energy Analyzer (simplified) |
| Plant design | utilities with real prices, tray and packed column sizing, exchangers/drums/tanks, Turton CAPCOST costing with CEPCI, grassroots capital, Turton COM, MACRS/SOYD DCF (NPV, IRR, payback), safety checks (flash points, solubility, vent load, MAWP, HAZOP nodes, FMEA, ISD) | Aspen Economic Analyzer / spreadsheets |
| Design studies | cached, constrained grid + pattern search with integer variables, parallel and multistart | Model Analysis Tools / optimization |
| Aspen files | `.bkp` reader and editor; drive a local Aspen Plus V15 over COM (open, run, read/set any variable, save .bkp) | - |
| Reports | self-contained interactive HTML (unit switcher incl. the AIChE kg-kmol-C-kJ-kPa-m-hr set, dark mode, hover charts, clickable flowsheet) + Markdown with SVG figures; design sections; Excel workbook with live capital/COM/DCF formulas | reports / result forms |

## Quick start

```powershell
cd C:\Code\School\Projects\refluxion
python -m venv .venv
.venv\Scripts\python -m pip install -e .[dev]
.venv\Scripts\python -m pytest            # unit tests + benchmarks against Aspen answers
.venv\Scripts\python examples\02_cumene_recycle.py   # -> examples\reports\02_cumene_recycle.html
```

```python
from refluxion import Flowsheet, RStoic, Heater, Flash2

fs = Flowsheet("Cumene", components=[("BENZENE", "benzene"), ("PROPYLEN", "propylene"), ("CUMENE", "cumene")],
               method="RK-SOAVE", units="ENG")
fs.feed("FEED", T=220, P=36, flows={"BENZENE": 40, "PROPYLEN": 40})
fs.add(RStoic("REACTOR", Q=0, P=0, reactions=[dict(stoich={"BENZENE": -1, "PROPYLEN": -1, "CUMENE": 1},
                                                  key="PROPYLEN", conversion=0.9)]),
       inlets=["FEED", "RECYCLE"], outlets=["REAC-OUT"])
fs.add(Heater("COOL", T=130, dP=0.1), inlets=["REAC-OUT"], outlets=["COOL-OUT"])
fs.add(Flash2("SEP", P=(1, "atm"), Q=0), inlets=["COOL-OUT"], outlets=["RECYCLE", "PRODUCT"])
fs.run()
print(fs["PRODUCT"].frac("CUMENE"))          # 0.9064 (Aspen: 0.9055)
fs.report(title="Cumene").save("cumene")     # cumene.html + cumene.md + cumene_assets/
```

Inputs use the flowsheet's unit set (`ENG`, `SI`, `MET`, `METCBAR`, `SI-KW`); any value can be a
`(number, "unit")` tuple instead. Pressures follow Aspen: a positive `P` is the outlet pressure,
zero or negative is a pressure drop (or use `dP=`).

## How well it matches Aspen

Benchmarks against Aspen Plus answers for classic Aspen training workshops (all in `tests/`):

| Case | Quantity | Refluxion | Aspen |
|---|---|---|---|
| Benzene flashes (PENG-ROB) | cooler duty | -7.163e6 Btu/hr | -7.197e6 |
| | FL1 vapor fraction | 0.8366 | 0.8362 |
| Cumene recycle (RK-SOAVE) | product cumene mole fraction | 0.9064 | 0.9055 |
| DSTWU, 6 hydrocarbons | R min / N min / N | 1.288 / 12.47 / 23.6 | 1.334 / 12.91 / 23.99 |
| RadFrac, RR = 1.8 | n-butane recovery overhead | 0.9905 | 0.9887 |
| DCM stripping (NRTL) | optimal steam 2 | 12,878 lb/hr | 13,446 |

## Aspen Plus connector (optional, Windows)

If Aspen Plus V15 is installed and its license is reachable, Refluxion can drive it through COM:

```powershell
.venv\Scripts\python -m pip install -e .[dev,aspen]     # adds pywin32
```

```python
from refluxion.io.aspen_com import Aspen

with Aspen() as a:                       # Aspen(visible=True) to watch the GUI
    a.open("my_case.bkp")
    a.run()
    print(a.status())                    # converged? errors/warnings
    print(a.value(r"\Data\Streams\PRODUCT\Output\TEMP_OUT\MIXED"))
    a.save_bkp("my_case_run.bkp")
```

Paths are Aspen's Variable Explorer paths (Customize > Variable Explorer); `a.dump(path, depth=2)` returns a
subtree's paths so you can find new ones. The connector targets V15 (`Apwn.Document.41.0`); for another version pass
`Aspen(progid="Apwn.Document.<n>.0")`.

## Repository layout

```
refluxion/            the package (see docs/ARCHITECTURE.md for how each part maps to Aspen)
  components.py       databank retrieval + fitted correlations (CPIG, PLXANT, DHVLDP, RKTZRA equivalents)
  props/              property methods (EOS, activity, ideal) and flash algorithms
  blocks/             unit operations - one module per model family, registered in a palette
  flowsheet.py        sequencing, tear streams, Wegstein, design specs, calculators
  tools.py            sensitivity, case study, optimization
  modules/pinch.py    heat integration
  utilities.py        utility prices and operating cost
  report/             HTML + Markdown reports, SVG charts, automatic flowsheet drawing, Aspen setup guide
examples/             classic Aspen workshops, each writes a report to examples/reports/
problems/             one folder per problem you solve (git-ignored except _template/)
tests/                benchmark tests
docs/ARCHITECTURE.md  how Refluxion (and Aspen) work inside
```

## Roadmap

Planned modules: three-phase flash and Decanter, kinetic reactors (RCSTR, RPlug), RGibbs, Henry's-law
components for activity models, cryogenic/refrigeration cycles and thermo-fluids (CoolProp), column
sizing (tray/packing hydraulics), costing and economics. Each is a new block or module registered with
the palette - nothing in the core changes.

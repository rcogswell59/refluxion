---
name: solve-problem
description: Solve a chemical process design problem (homework, workshop, project part) with the Refluxion toolkit and produce an interactive HTML + Markdown solution report. Use when the user pastes or attaches a problem statement, PDF page, photo of a handout, or .bkp file and asks to solve, check, model or explore it.
---

# Solve a process problem with Refluxion

Work in `C:\Code\School\Projects\refluxion` with `.venv\Scripts\python`.

## 1. Understand the problem

- Read everything given: text, PDF (PyMuPDF if the Read tool can't render it), images, `.bkp` files
  (plain text; the `? BLOCK`, `? STREAM`, `PROPERTIES MAIN` sections hold every input).
- List the questions explicitly. List the data (components, flows, T, P, specs, targets) with units.
- Note what is missing and the assumption you will make.

## 2. Set up the folder

`problems/<slug>/problem.md` (statement summary + data table + questions) and `solve.py`
(start from `problems/_template/solve.py`). Reuse `examples/` scripts as patterns:
flashes (01), recycle + design spec + sensitivity (02), DSTWU -> RadFrac (03), NRTL + optimization (04). Complex studies: put the flowsheet in `model.py` with a `build(**changes)` function.

## 3. Model it

- Components: `("ID", "name or Aspen alias")`. Method: from the problem/.bkp, else by chemistry.
- Unit set: the problem's (ENG for F/psia/lbmol/hr, SI-KW for C/bar/kmol/hr/kW, MET, METCBAR, SI).
- Blocks and specs mirroring what the Aspen model would use. Columns: specs dict + feeds + pressures.
- For "what happens if" questions: `tools.sensitivity`, `tools.case_study`; for "minimize/maximize":
  `tools.optimize`; for targets: `fs.design_spec` or RadFrac recovery/purity specs.
- Heat integration: `modules.pinch_analysis` (streams can come from `streams_from_flowsheet`).

## 4. Verify before reporting

- `fs.run()` converged; `fs.balance_check()` all tiny; no block warnings (or explain them).
- Sanity: phases, temperatures between feeds' ranges, duty signs, recoveries <= 1, stage profiles smooth.
- Cross-check one number by hand (Fenske/Underwood, a simple energy balance, Raoult bubble point...).
- Compare with any known answer (textbook or manual values, an Aspen run) and state the difference.

## 5. Report

```python
rep = fs.report(title=..., subtitle=..., problem=<markdown>, approach=<method choice + assumptions>)
rep.highlight(label, value, ">=", target, unit=...)      # one card per question/target
rep.sensitivity(res, targets={...}); rep.optimization(res); rep.pinch(res); rep.column("COL")
rep.hand_calc("Hand check: ...", "<worked equations in markdown>")
rep.text("Answers", "1. ...\n2. ...")                     # answer every question + the physics behind it
rep.save("problems/<slug>/reports/<name>")
```

The report automatically adds: flowsheet drawing, stream table, block results, column profiles,
convergence history, balance checks, component data sources and a "Set it up in Aspen Plus" guide.

## 6. Hand back

Short chat summary: answers with numbers and units, the report path, differences from Aspen to expect,
and one or two suggestions for what to explore next. Offer to open the HTML report.

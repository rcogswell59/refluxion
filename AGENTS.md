# Refluxion - notes for Codex

Refluxion is an Aspen-style process simulator in Python. Its main use: **the user drops in a problem and
Codex solves it with this toolkit**, producing an organized HTML + Markdown report, then answers follow-up
questions. It is a learning and checking tool; final numbers for any assignment come from Aspen Plus itself.

## Environment

- Python venv: `.venv\Scripts\python` (Windows). Run scripts from their own folder or with full paths.
- Tests: `.venv\Scripts\python -m pytest` (benchmarks against Aspen answers; keep them passing).
- Component data cache: `~/.refluxion/components.json` (delete to force re-retrieval).
- `.bkp` files are plain text; grep the BLOCK / STREAM / PROPERTIES sections to read inputs.
- Aspen Plus V15 over COM (optional): `refluxion/io/aspen_com.py` (`Aspen()` context manager: open, run,
  status, value/set by Variable Explorer path, dump, save_bkp). Needs `pip install -e .[aspen]` and a
  reachable Aspen license. Smoke test: `.venv\Scripts\python -c "from refluxion.io.aspen_com import Aspen; a=Aspen(); print('ok'); a.close()"`.
- When building or editing a .bkp for the user, open it in Aspen over COM and run it before handing it back.

## Solving a problem (use the `solve-problem` skill)

1. Make `problems/<short-slug>/` with `problem.md` (the statement, data, questions - summarize, keep numbers exact).
2. Write `solve.py` (copy `problems/_template/solve.py`). Build the flowsheet, run it, add analyses.
3. Choose the property method deliberately and say why in the report's approach section
   (hydrocarbons/gases -> PENG-ROB or RK-SOAVE; polar/water mixtures -> NRTL; low-pressure ideal
   mixtures -> IDEAL; if the problem or a `.bkp` names a method, use that one).
4. Run it, then **check**: every block's balances close (report "Checks" section), loops converged,
   results physically sensible (temperatures, phases, signs of duties). If something disagrees with a hand
   estimate, find out why before reporting.
5. Report: `rep = fs.report(title=..., problem=..., approach=...)`, key results with `rep.highlight`
   (with targets where the problem has them), analyses (`rep.sensitivity`, `rep.optimization`,
   `rep.pinch`, `rep.column`), a hand-calculation cross-check with `rep.hand_calc` where one exists,
   and an **answers** section (`rep.text("Answers", ...)`) that answers each question in plain English and
   explains the physics. Save to `problems/<slug>/reports/`.
6. In chat: give the headline answers, the report path, and anything uncertain. Say plainly that numbers
   are close to Aspen's, not identical (open kij and pure-component data).

## Conventions

- Stage numbering and specs follow Aspen (stage 1 = top/condenser, N = bottom/reboiler; `P <= 0` is a drop).
- Paths for sensitivity/design specs/optimization: `"BLOCK.key.sub"` into `block.spec`, e.g.
  `"COL1.heaters.2"`, `"COL1.specs.reboiler_duty"`, `"FEED.flows.C3"` (flowsheet units).
- Never invent data. If a problem lacks a value, state the assumption in the report.
- Adding capability = new block (`blocks/`, decorate with `@register`) or module (`modules/`), plus a test.

## Academic integrity

This is for learning and checking work. For graded deliverables, help the user understand and verify;
don't write their report or memo for them unless they explicitly ask, and follow their course's rules.

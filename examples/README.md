# Examples - classic Aspen Plus workshops, rebuilt in Refluxion

Each script solves a classic Aspen Plus training workshop and writes an
HTML + Markdown report to `reports/`. The key-result cards show Aspen's answers next to Refluxion's.

| Script | Workshop | Shows |
|---|---|---|
| `01_benzene_flashes.py` | Ch. 3, CAD0002 | Heater, Flash2, adiabatic flash, PENG-ROB |
| `02_cumene_recycle.py` | Ch. 3/10, CAD0001 | RStoic, tear stream + Wegstein, sensitivity, design spec |
| `03_dstwu_to_radfrac.py` | Ch. 5, DSTWU1/1a | shortcut design, rigorous column, reflux-ratio sensitivity |
| `04_dcm_optimization.py` | Ch. 9, CAD0016 | NRTL with Aspen's parameters, SQP optimization with a constraint |

Run one: `..\.venv\Scripts\python 02_cumene_recycle.py`, then open `reports\02_cumene_recycle.html`.

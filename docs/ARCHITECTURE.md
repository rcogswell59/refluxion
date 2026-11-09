# How Refluxion works (and what that says about Aspen Plus)

Refluxion is organized the way Aspen Plus is, so reading the code is a tour of how a sequential-modular
process simulator works. Each section names the Aspen concept, then the Refluxion file that implements it.

## 1. Components and the databank - `components.py`

**Aspen:** picking a component on the *Components* form retrieves constants (TC, PC, OMEGA, MW, DHFORM)
and temperature-dependent correlation parameters (CPIG for ideal-gas heat capacity, PLXANT for vapor
pressure, DHVLDP for heat of vaporization, RKTZRA for liquid density) from SQL Server databanks
(APV150 PURE40, NIST-TRC ...).

**Refluxion:** `lookup()` resolves a name, formula, CAS number or Aspen alias (`C4H10-1`) and pulls the
same kinds of data from the open `chemicals`/`thermo` databanks. It picks a *data-based* correlation
first (DIPPR, TRC, VDI, Wagner; estimation methods only as a last resort), then fits it to a fast analytic
form and records the fit error:

| Property | Fitted form | Aspen parameter |
|---|---|---|
| Ideal-gas Cp | 6th-order polynomial in T/1000 (150-1200 K) | CPIG |
| Vapor pressure | ln P = A + B/T + C ln T + D T^2 | PLXANT |
| Heat of vaporization | DIPPR 106: A(1-Tr)^(B + C Tr + D Tr^2 + E Tr^3) | DHVLDP |
| Liquid volume | Rackett with Z_RA fitted to density at 25 C | RKTZRA |

Enthalpy reference is Aspen's: an ideal gas at 25 C has enthalpy equal to its heat of formation. That is
why a reactor's duty automatically includes the heat of reaction.

## 2. Property methods - `props/`

**Aspen:** a *property method* (PENG-ROB, RK-SOAVE, NRTL ...) is a bundle of models and routes. Every
unit operation asks it for K-values, enthalpies, entropies and densities.

**Refluxion:** `PropertyMethod` defines that interface (`lnK`, `h_liq`, `h_vap`, `s_liq`, `s_vap`,
`v_liq`, `v_vap`). Everything is vectorized over a leading "state" axis, so a whole column's stages are
evaluated in one call (about 1.5 ms for 16 stages x 14 components).

- `cubic.py` - PR, SRK and RK in one generic form `P = RT/(V-b) - a/(V^2 + u b V + w b^2)`. Fugacity
  coefficients and enthalpy/entropy departures are closed-form. Above Tc the alpha function switches to
  Boston-Mathias (as Aspen does); SRK uses Graboski-Daubert's alpha for H2.
- `activity.py` - NRTL with Aspen's parameter form (`tau = a + b/T + e ln T + f T`,
  `alpha = c + d(T-273.15)`); liquid enthalpy = ideal gas - heat of vaporization + excess enthalpy.
- `params.py` - binary-parameter retrieval (ChemSep kij and NRTL), user values override. Like Aspen's
  "retrieve parameters" step, it reports which pairs were found and which defaulted.

## 3. Flash - `props/flash.py`

Nearly every block ends with a flash. `tp_flash` uses successive substitution with the Rachford-Rice
equation solved over the *negative-flash* window, which detects single-phase feeds cleanly (beta
outside 0..1). PH and PS flashes bracket the temperature and use Brent's method on h(T) or s(T);
bubble and dew points iterate the incipient-phase composition. Lesson learned while building it:
a liquid with dissolved H2 can have two bubble points at the same pressure, so the starting guess matters.

## 4. Streams and blocks - `stream.py`, `blocks/`

A stream is Aspen's "C + 2 variables": component flows plus two state variables, after a flash.
Blocks are nodes: each reads its inlet streams, applies its model, and writes its outlet streams. They
register themselves in `PALETTE` (the model palette), and inputs are kept exactly as typed (in your unit
set) and converted when the block runs - which is also how the Aspen setup guide can echo them back.

## 5. The flowsheet solver - `flowsheet.py`

This is the heart of a sequential-modular simulator:

1. **Graph.** Blocks are nodes, streams are edges.
2. **Recycle detection.** Tarjan's algorithm finds strongly connected components - each is a recycle loop.
3. **Tearing.** For each loop, choose the smallest set of streams whose removal breaks every cycle
   (preferring streams that go "backward" in the drawing). These are the *tear streams* (`$OLVER01` ...).
4. **Convergence.** Guess the tear streams, run the loop's blocks in order, compare what comes back with
   the guess, and update with **Wegstein** acceleration (q bounded between -5 and 0, Aspen's defaults).
   Converged when every tear variable's error / tolerance < 1 - the same number Aspen's Control Panel prints.
5. **Design specs** wrap the whole flowsheet: vary an input with the secant method (bracketing with
   Brent's method once a sign change appears) until the measured result hits its target.

## 6. RadFrac - `blocks/radfrac.py`

Aspen's RadFrac uses nested loops (outer: thermodynamics; middle: specs; inner: stage balances). Refluxion
solves the same MESH equations **simultaneously** with Newton's method (the Naphtali-Sandholm formulation):

- unknowns per stage: component liquid flows l, component vapor flows v, temperature T
- equations per stage: component balances (M), equilibrium y - Kx = 0 (E), energy balance (H)
- operating specs (reflux ratio, distillate rate, boilup ratio, recoveries, purities ...) replace the
  condenser/reboiler energy balances whose duties are unknown

Thermodynamic derivatives are stage-local, so one batched finite-difference sweep over all stages gives
them; the rest of the Jacobian is assembled analytically. Initialization uses constant molar overflow
plus a few bubble-point sweeps (tridiagonal Thomas solves per component). Newton then converges
quadratically - you can see the residual square each step in the report.

## 7. Model analysis tools - `tools.py`

- **Sensitivity**: set inputs on a grid, re-solve, tabulate - and restore the base case.
- **Optimization**: SLSQP (sequential quadratic programming, Aspen's default) on variables scaled to
  [0, 1], with inequality/equality constraints scaled by their targets.

## 8. Reports - `report/`

Charts and the flowsheet drawing are SVG generated in Python, so the HTML and Markdown show identical
figures. The flowsheet is laid out automatically: tear streams are cut, blocks ranked by longest path,
ordered by barycenter sweeps, streams routed orthogonally with recycles on a lane below. The HTML adds
a unit-set switcher (every number carries its SI value and quantity), dark mode, hover crosshairs,
clickable legends, stream tooltips and copy-to-CSV - with no internet needed.

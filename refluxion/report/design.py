"""Report sections for a whole-plant evaluation (:func:`refluxion.design.plant.evaluate_plant`).

* Equipment summary - every item with size, material, pressure factor, purchased and bare-module cost
* Utilities summary - duty, consumption and cost per item, and totals per utility (competition section H)
* Economics - capital, operating cost, value, NPV, DCF rate of return, payback, and the cash-flow table (section L)
* :func:`stream_table_rows` - the competition's stream table (section J): number, description, T, P, total and
  component mass flows, weight fractions
"""

from __future__ import annotations


def _usd(v):
    return f"${v:,.0f}"


def add_design_sections(rep, m: dict, title_prefix: str = ""):
    t = (title_prefix + " ") if title_prefix else ""
    rows = [[r["item"], f"{r['size']:.4g} {r['unit']}", r["units"], r["material"], f"{r['P_barg']:.3g}", f"{r['Fm']:.3g}",
             f"{r['Fp']:.3g}", _usd(r["purchased"]), _usd(r["bare_module"]), r["warnings"]] for r in m["equipment"]]
    rep.table(f"{t}Equipment summary", ["Item", "Size", "Units", "Material", "P (barg)", "Fm", "Fp", "Purchased (Cp0)", "Bare module",
                                        "Notes"], rows, caption="Turton et al. Appendix A (CAPCOST) at the basis CEPCI; grassroots capital.")
    urows = [[r["item"], r["utility"], f"{r['duty_W'] / 1e3:.4g}", f"{r['flow_kg_h']:.4g}" if r.get("flow_kg_h") else "-",
              _usd(r["cost_per_yr"])] for r in m["utilities"]]
    for name, c in m["utility_costs"].items():
        duty = sum(abs(r["duty_W"]) for r in m["utilities"] if r["utility"] == name) / 1e3
        flow = sum(r["flow_kg_h"] or 0.0 for r in m["utilities"] if r["utility"] == name)
        urows.append([f"**Total {name}**", name, f"{duty:.4g}", f"{flow:.4g}" if flow else "-", _usd(c)])
    rep.table(f"{t}Utilities summary", ["Item", "Utility", "Duty (kW)", "Consumption (kg/h)", "Cost ($/yr)"], urows)
    cf = m["cash_flow"]
    rep.text(f"{t}Economics", "\n".join([
        f"- Grassroots capital (FCI): **{_usd(m['capital'])}** (bare module {_usd(m['bare_module'])})",
        f"- Value of recovered solvents and avoided disposal: {_usd(m['value_per_yr'])}/yr",
        f"- Cost of manufacturing without depreciation: {_usd(m['opex_per_yr'])}/yr (utilities {_usd(m['C_UT'])}, raw materials "
        f"{_usd(m['C_RM'])}, waste {_usd(m['C_WT'])}, labor {_usd(m['C_OL'])} for {m['operators']} operators)",
        f"- NPV at {cf.i:.0%}: **{_usd(m['npv'])}**; DCF rate of return: **{(m['irr'] or float('nan')):.1%}**; payback {m['payback']:.2f} yr",
        f"- Basis: {cf.basis}"]))
    rep.table(f"{t}Cash flow", ["Year", "Value", "Costs", "Depreciation", "Taxable", "Tax", "Cash flow", "Discounted", "Cumulative PV"],
              [[r["year"], _usd(r["savings"]), _usd(r["costs"]), _usd(r["depreciation"]), _usd(r["taxable"]), _usd(r["tax"]),
                _usd(r["cash_flow"]), _usd(r["discounted"]), _usd(r["cumulative_discounted"])] for r in cf.rows], collapsible=True)
    return rep


def stream_table_rows(fs, streams: dict) -> list:
    """``streams = {name: description}`` in PFD order -> rows [number, description, T C, P kPa, total kg/h,
    component kg/h ..., component weight fractions ...]."""
    ids = fs.comps.ids
    rows = []
    for k, (name, desc) in enumerate(streams.items(), 1):
        s = fs[name]
        rows.append([str(k), desc, round(s.get("T", units="C"), 2), round(s.get("P", units="kPa"), 2), round(s.get("mass_flow", units="kg/hr"), 3)]
                    + [round(s.get("mass_flow", c, units="kg/hr"), 4) for c in ids] + [round(s.get("mass_frac", c), 6) for c in ids])
    return rows

"""Excel workbook for a plant evaluation (:func:`refluxion.design.plant.evaluate_plant`) with live formulas.

Sheets: Summary, Equipment (costs, total module and grassroots capital as formulas), Utilities (per item, totals by
utility), Operating cost (COM_d formula), Cash flow (depreciation, tax, after-tax cash flow, NPV and IRR formulas),
Streams. Inputs can be changed in Excel and the capital, cost and return update. Refluxion's own results sit in
the Summary sheet so the formulas can be checked against them.
"""

from __future__ import annotations

from pathlib import Path

from ..design.economics import depreciation_schedule


def _header(ws, cols):
    from openpyxl.styles import Font
    ws.append(cols)
    for c in ws[1]:
        c.font = Font(bold=True)


def export_workbook(path, m: dict, basis, fs=None, streams: dict | None = None, notes: dict | None = None) -> Path:
    from openpyxl import Workbook
    from openpyxl.utils import get_column_letter as L

    path = Path(path)
    wb = Workbook()
    # ------------------------------------------------------------------ Equipment
    eq = wb.active
    eq.title = "Equipment"
    _header(eq, ["Item", "Type", "Size", "Unit", "Material", "P (barg)", "Fm", "Fp", "Purchased Cp0 ($)", "Bare module ($)",
                 "Bare module, base conditions ($)", "Notes"])
    for r in m["equipment"]:
        eq.append([r["item"], r["kind"], r["size"], r["unit"], r["material"], r["P_barg"], r["Fm"], r["Fp"], r["purchased"],
                   r["bare_module"], r.get("bare_module_base", r["bare_module"]), r["warnings"]])
    n = eq.max_row
    eq.append([])
    eq.append(["Sum of bare-module costs", None, None, None, None, None, None, None, f"=SUM(I2:I{n})", f"=SUM(J2:J{n})", f"=SUM(K2:K{n})"])
    s_row = eq.max_row
    eq.append(["Total module cost C_TM = 1.18 x sum C_BM", None, None, None, None, None, None, None, None, f"=1.18*J{s_row}"])
    tm_row = eq.max_row
    eq.append(["Grassroots capital C_GR = C_TM + 0.50 x sum C_BM0", None, None, None, None, None, None, None, None, f"=J{tm_row}+0.5*K{s_row}"])
    gr_row = eq.max_row
    # ------------------------------------------------------------------ Utilities
    ut = wb.create_sheet("Utilities")
    _header(ut, ["Item", "Utility", "Duty (kW)", "Consumption (kg/h)", "Cost ($/yr)"])
    for r in m["utilities"]:
        ut.append([r["item"], r["utility"], r["duty_W"] / 1e3, r.get("flow_kg_h"), r["cost_per_yr"]])
    nu = ut.max_row
    ut.append([])
    for name in m["utility_costs"]:
        ut.append([f"Total {name}", name, f'=SUMIF(B2:B{nu},"{name}",C2:C{nu})', f'=SUMIF(B2:B{nu},"{name}",D2:D{nu})',
                   f'=SUMIF(B2:B{nu},"{name}",E2:E{nu})'])
    ut.append(["All utilities", None, None, None, f"=SUM(E2:E{nu})"])
    ut_total = ut.max_row
    # ------------------------------------------------------------------ Operating cost
    oc = wb.create_sheet("Operating cost")
    oc.append(["Item", "Value ($/yr)", "Basis"])
    oc.append(["Fixed capital FCI", f"=Equipment!J{gr_row}", "grassroots capital"])
    oc.append(["Operating labor C_OL", m["C_OL"], f"{m['operators']} operators x ${basis.wage:,.0f}/yr"])
    oc.append(["Utilities C_UT", f"=Utilities!E{ut_total}", "Utilities sheet"])
    oc.append(["Raw materials C_RM", m["C_RM"], "makeup solvent"])
    oc.append(["Waste treatment C_WT", m["C_WT"], "waste disposal"])
    oc.append(["Value of products and avoided disposal", m["value_per_yr"], "products x price + avoided disposal"])
    oc.append(["Cost of manufacturing COM_d", "=0.18*B2+2.73*B3+1.23*(B4+B5+B6)", "Turton: 0.18 FCI + 2.73 C_OL + 1.23 (C_UT + C_WT + C_RM)"])
    # ------------------------------------------------------------------ Cash flow
    cf = wb.create_sheet("Cash flow")
    years = basis.life
    dep = depreciation_schedule(basis.depreciation, years, basis.macrs_years)
    cf.append(["Input", "Value"])
    cf.append(["Fixed capital FCI", "='Operating cost'!B2"])
    cf.append(["Working capital fraction of FCI", basis.wc_frac])
    cf.append(["Working capital", "=B2*B3"])
    cf.append(["Annual value", "='Operating cost'!B7"])
    cf.append(["Annual cost COM_d", "='Operating cost'!B8"])
    cf.append(["Tax rate", basis.tax])
    cf.append(["Discount rate (MARR)", basis.marr])
    cf.append([f"Depreciation: {basis.depreciation} {basis.macrs_years}-yr", None])
    cf.append([])
    cf.append(["Year", "Value", "Costs", "Depreciation", "Taxable income", "Tax", "After-tax cash flow", "Depreciation fraction", "Discounted"])
    first = cf.max_row + 1
    for k in range(years + 1):
        r = first + k
        if k == 0:
            cf.append([0, 0, 0, 0, 0, 0, "=-(B2+B4)", 0, f"=G{r}"])
        else:
            last = " + B4" if k == years else ""
            cf.append([k, "=$B$5", "=$B$6", f"=$B$2*H{r}", f"=B{r}-C{r}-D{r}", f"=E{r}*$B$7", f"=B{r}-C{r}-F{r}{last.replace(' + B4', '+$B$4')}",
                       dep[k - 1], f"=G{r}/(1+$B$8)^A{r}"])
    lastr = first + years
    cf.append([])
    cf.append(["NPV", f"=G{first}+NPV($B$8,G{first + 1}:G{lastr})"])
    cf.append(["DCF rate of return (IRR)", f"=IRR(G{first}:G{lastr})"])
    # ------------------------------------------------------------------ Streams
    st = wb.create_sheet("Streams")
    if fs is not None:
        from .design import stream_table_rows
        streams = streams or {s.name: s.name for s in fs.streams.values() if s.solved}
        ids = fs.comps.ids
        _header(st, ["No.", "Stream", "T (C)", "P (kPa)", "Total (kg/h)"] + [f"{c} (kg/h)" for c in ids] + [f"w {c}" for c in ids])
        for row in stream_table_rows(fs, streams):
            st.append(row)
    # ------------------------------------------------------------------ Summary
    su = wb.create_sheet("Summary", 0)
    su.append(["Result", "Value", "Note"])
    rows = [("NPV (Refluxion)", m["npv"], "check against Cash flow!NPV"), ("DCF rate of return (Refluxion)", m["irr"], ""),
            ("Grassroots capital (Refluxion)", m["capital"], "check against Equipment"), ("COM_d (Refluxion)", m["opex_per_yr"], ""),
            ("Value per year (Refluxion)", m["value_per_yr"], ""), ("Operators", m["operators"], ""),
            ("Hours per year", basis.hours, ""), ("CEPCI", basis.cepci, "")]
    for k, v in (notes or {}).items():
        rows.append((k, v, "note"))
    for r in rows:
        su.append(list(r))
    for ws in wb.worksheets:
        for i, col in enumerate(ws.columns, 1):
            width = max((len(str(c.value)) for c in col if c.value is not None), default=8)
            ws.column_dimensions[L(i)].width = min(max(10, width + 2), 60)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path

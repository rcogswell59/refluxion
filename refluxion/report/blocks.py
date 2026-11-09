"""Block-specific report content (shared by the HTML and Markdown renderers)."""

from __future__ import annotations

from .charts import line_chart


def block_extras(report, b) -> list:
    """Extra items for a block card: tables and charts (items use the Report item tuples)."""
    from . import Q

    u = report.units
    items = []
    t = b.tables
    sid = f"blk-{b.name}".replace(" ", "-")
    if "split_fractions" in t:
        sf = t["split_fractions"]
        rows = [[c, Q(f, "mole_flow"), Q(d, "mole_flow"), Q(bb, "mole_flow"), float(fd), float(fb)]
                for c, f, d, bb, fd, fb in zip(sf["components"], sf["feed"], sf["d"], sf["b"], sf["distillate"], sf["bottoms"])]
        items.append(("table", dict(columns=["Component", ("Total feed", "mole_flow"), ("Top product", "mole_flow"),
                                             ("Bottoms", "mole_flow"), "Fraction to top", "Fraction to bottoms"],
                                    rows=rows, caption="Component recoveries (split fractions)", title=f"{b.name} split fractions")))
    if "feeds" in t and t["feeds"].get("rows"):
        rows = [[r["stream"], r["stage"], r["convention"], r["vf"]] for r in t["feeds"]["rows"]]
        items.append(("table", dict(columns=["Feed", "Stage", "Convention", "Vapor fraction at stage P"], rows=rows,
                                    caption="Above-stage feeds send their vapor to the stage above.", title=f"{b.name} feeds")))
    if "convergence" in t and len(t["convergence"]["residual"]) > 1:
        r = t["convergence"]["residual"]
        ser = [dict(name="Max scaled residual", x=list(range(len(r))), y=list(r))]
        items.append(("svg", line_chart(ser, x_label="Newton iteration", y_label="max |residual|", log_y=True,
                                        chart_id=f"{sid}-conv", x_integer=True),
                      line_chart(ser, title=f"{b.name} Newton convergence", x_label="Newton iteration", y_label="max |residual|",
                                 log_y=True, standalone=True, x_integer=True),
                      f"{sid}-conv", f"{b.name} Newton convergence",
                      "Quadratic convergence: close to the answer, the residual roughly squares each step."))
    if "reflux_profile" in t:
        rp = t["reflux_profile"]
        ser = [dict(name="Reflux ratio", x=list(rp["stages"]), y=list(rp["reflux_ratio"]))]
        refs = [dict(value=b.results["Rmin"][1], label="R min")] if "Rmin" in b.results else []
        items.append(("svg", line_chart(ser, x_label="Number of stages", y_label="Reflux ratio", chart_id=f"{sid}-rr", ref_lines=refs),
                      line_chart(ser, title=f"{b.name} reflux ratio vs stages", x_label="Number of stages", y_label="Reflux ratio",
                                 standalone=True, ref_lines=refs),
                      f"{sid}-rr", "Reflux ratio vs number of stages (Gilliland)", "The classic trade-off: fewer stages need more reflux."))
    if "volatility" in t:
        v = t["volatility"]
        rows = [[c, float(a), float(kt), float(kb), Q(d, "mole_flow"), Q(bb, "mole_flow")]
                for c, a, kt, kb, d, bb in zip(v["components"], v["alpha"], v["K_top"], v["K_bottom"], v["d"], v["b"])]
        items.append(("table", dict(columns=["Component", "alpha (vs HK)", "K top", "K bottom", ("Distillate", "mole_flow"),
                                             ("Bottoms", "mole_flow")], rows=rows, caption="", title=f"{b.name} volatilities")))
    if "heat_curve" in t:
        hc = t["heat_curve"]
        x = [u.from_si(q, "duty") for q in hc["duty"]]
        sides = (("Process", "T_process"), ("Utility", "T_utility")) if "T_process" in hc else (("Hot side", "T_hot"), ("Cold side", "T_cold"))
        ser = [dict(name=label, x=x, y=[u.from_si(v, "temperature") for v in hc[key]]) for label, key in sides]
        xl = f"Heat transferred ({u.unit('duty')})"
        yl = f"Temperature ({u.unit('temperature')})"
        items.append(("svg", line_chart(ser, x_label=xl, y_label=yl, chart_id=f"{sid}-hc"),
                      line_chart(ser, title=f"{b.name} heat curves", x_label=xl, y_label=yl, standalone=True),
                      f"{sid}-hc", "Heat curves (zone analysis)", "The gap between the curves is the local driving force."))
    if "reactions" in t:
        rows = [[r["reaction"], r["equation"], Q(r["extent"], "mole_flow"), Q(r["dHr"], "molar_enthalpy")]
                for r in t["reactions"]["rows"]]
        items.append(("table", dict(columns=["#", "Reaction", ("Extent", "mole_flow"), ("Heat of reaction at 25 C", "molar_enthalpy")],
                                    rows=rows, caption="Heat of reaction per unit extent, from heats of formation.",
                                    title=f"{b.name} reactions")))
    return items

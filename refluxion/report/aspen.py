"""Generate "how to set this up in Aspen Plus" instructions from a Refluxion flowsheet.

The point: model a problem here, understand it, then rebuild it in Aspen quickly. Each block is
mapped to its Aspen model and the input forms you fill in, using the flowsheet's own unit set.
"""

from __future__ import annotations

from ..components import ASPEN_ALIASES


def _fmt(v):
    if isinstance(v, tuple):
        return f"{v[0]:g} {v[1]}"
    if isinstance(v, float):
        return f"{v:g}"
    return str(v)


def _pressure(spec, u):
    if spec.get("dP") is not None:
        return f"Pressure drop = {_fmt(spec['dP'])} {u.unit('delta_pressure') if not isinstance(spec['dP'], tuple) else ''}".strip()
    P = spec.get("P")
    if P is None:
        return "Pressure = 0 (no pressure drop)"
    if isinstance(P, tuple):
        return f"Pressure = {_fmt(P)}"
    if P <= 0:
        return f"Pressure = {P:g} {u.unit('delta_pressure')} (zero/negative = pressure drop)"
    return f"Pressure = {P:g} {u.unit('pressure')}"


def _second(spec, u):
    for key, label, q in (("T", "Temperature", "temperature"), ("Q", "Heat duty", "duty"), ("vf", "Vapor fraction", None),
                          ("dT", "Temperature change", "delta_temperature")):
        if spec.get(key) is not None:
            v = spec[key]
            unit = "" if q is None or isinstance(v, tuple) else u.unit(q)
            return f"{label} = {_fmt(v)} {unit}".strip()
    return ""


def aspen_setup_md(fs) -> str:
    u = fs.units
    rev = {v: k for k, v in ASPEN_ALIASES.items()}
    lines = ["Rebuild this flowsheet in Aspen Plus V15 with these inputs (save as `.bkp`, and run through the Control Panel).", "",
             "**Properties environment**", "",
             f"- Setup > Specifications: unit set **{u.base}**" + (f" (with overrides {u.overrides})" if u.overrides else "")]
    comps = ", ".join(f"`{c.id}` = {rev.get(c.cas, c.name.upper())}" for c in fs.comps)
    lines.append(f"- Components > Specifications: {comps}")
    lines.append(f"- Methods > Specifications: base method **{fs.pm.name}**")
    if fs.pm.name.startswith("NRTL"):
        lines.append("- Methods > Parameters > Binary Interaction > NRTL-1: check the retrieved pairs; enter any missing ones")
    else:
        lines.append("- Methods > Parameters > Binary Interaction: review the retrieved kij (Aspen's values differ slightly from ChemSep's)")
    lines += ["", "**Simulation environment - streams**", ""]
    for s in fs.feeds:
        sp = s.spec
        state = ", ".join(f"{lab} = {_fmt(sp[k])}{'' if isinstance(sp[k], tuple) else ' ' + u.unit(q)}"
                          for k, lab, q in (("T", "Temperature", "temperature"), ("P", "Pressure", "pressure")) if sp.get(k) is not None)
        if sp.get("vf") is not None:
            state += (", " if state else "") + f"Vapor fraction = {sp['vf']:g}"
        basis = "Mole-Flow" if sp.get("basis", "mole") == "mole" else "Mass-Flow"
        fu = u.unit("mole_flow" if basis == "Mole-Flow" else "mass_flow")
        if sp.get("flows"):
            flows = ", ".join(f"{k} {_fmt(v)}" for k, v in sp["flows"].items())
            lines.append(f"- **{s.name}**: {state}; {basis} ({fu}): {flows}")
        else:
            fr = ", ".join(f"{k} {_fmt(v)}" for k, v in sp["fractions"].items())
            lines.append(f"- **{s.name}**: {state}; total {_fmt(sp['total'])} {fu}; mole fractions: {fr}")
    lines += ["", "**Simulation environment - blocks**", ""]
    for b in fs.blocks.values():
        sp = b.spec
        ins = ", ".join(s.name for s in b.all_inlets())
        outs = ", ".join(f"{p} -> {s.name}" if p != s.name else s.name for p, s in b.outlets.items())
        head = f"- **{b.name}** (Model Palette > {b.category} > **{b.type_name}**; in: {ins}; out: {outs})"
        det = []
        t = b.type_name
        if t in ("Heater", "Flash2"):
            det = [f"Specifications: {_pressure(sp, u)}; {_second(sp, u)}"]
            if sp.get("liquid_spec"):
                comp, w = sp["liquid_spec"]
                T = b.results.get("T")
                Tg = f"{u.from_si(T[1], 'temperature'):.4g} {u.unit('temperature')}" if T else "the bubble point of the feed"
                det = [f"Specifications: {_pressure(sp, u)}; Temperature = {Tg} (initial value)",
                       f"Flowsheeting Options > Design Spec: define the mass fraction of {comp} in {b.outlet('liquid').name}; "
                       f"target {w:g}; vary the flash temperature of {b.name} (Refluxion solves this inside the block)"]
        elif t == "Mixer":
            det = ["Specifications: defaults (outlet at the lowest inlet pressure)" if "P" not in sp else f"Specifications: {_pressure(sp, u)}"]
        elif t == "FSplit":
            det = [f"Specifications: split fraction {k} = {v:g}" for k, v in (sp.get("fractions") or {}).items()] + \
                  [f"Specifications: flow {k} = {_fmt(v)}" for k, v in (sp.get("flows") or {}).items()]
        elif t == "Sep":
            for out, d in (sp.get("splits") or {}).items():
                det.append(f"Specifications > outlet {out}: " + ", ".join(f"{c} split fraction {v:g}" for c, v in d.items()))
        elif t == "Pump":
            det = [f"Specifications: Discharge pressure = {_fmt(sp.get('P'))} {u.unit('pressure')}" if sp.get("P") is not None
                   else f"Specifications: Pressure increase = {_fmt(sp.get('dP'))}",
                   f"Efficiencies: Pump = {sp.get('eff', 1.0):g}, Driver = {sp.get('driver_eff', 1.0):g}"]
        elif t == "Compr":
            det = ["Specifications: Type = Isentropic", f"Discharge pressure = {_fmt(sp.get('P'))} {u.unit('pressure')}"
                   if sp.get("P") is not None else f"Pressure ratio = {sp.get('ratio')}",
                   f"Isentropic efficiency = {sp.get('eff', 0.72):g}, Mechanical = {sp.get('mech_eff', 1.0):g}"]
        elif t == "Valve":
            det = [f"Operation: adiabatic flash for specified outlet pressure; {_pressure(sp, u)}"]
        elif t == "RStoic":
            det = [f"Specifications: {_pressure(sp, u)}; {_second(sp, u)}"]
            for i, r in enumerate(sp.get("reactions", []), 1):
                st = ", ".join(f"{c} {v:+g}" for c, v in r["stoich"].items())
                conv = (f"fractional conversion {r['conversion']:g} of {r['key']}" if "conversion" in r else f"extent {_fmt(r['extent'])}")
                det.append(f"Reactions > reaction {i}: stoichiometry {st}; {conv}"
                           + ("" if not sp.get("series") else " (check 'Reactions occur in series')"))
        elif t == "HeatX":
            spec_keys = [k for k in ("hot_T_out", "cold_T_out", "duty", "hot_vf_out", "cold_vf_out", "min_approach") if sp.get(k) is not None]
            det = ["Specifications: Shortcut, " + str(sp.get("flow", "countercurrent")) +
                   "; exchanger specification " + ", ".join(f"{k} = {_fmt(sp[k])}" for k in spec_keys),
                   f"U methods: constant U = {_fmt(sp.get('U', 850))} (Refluxion default 850 W/m2-K)"]
        elif t == "UtilityHX":
            ut = b.utility
            uflow = b.results.get("utility_flow")
            det = [f"Model Palette > Exchangers > **HeatX** (Shortcut, Design, countercurrent); hot/cold side: process stream "
                   f"{', '.join(s.name for s in b.all_inlets())} against a utility stream of {ut.name}",
                   f"Utility stream: {ut.T_in - 273.15:.4g} C supply" + (f", leaves at {ut.T_out - 273.15:.4g} C" if ut.T_out != ut.T_in else ""),
                   f"Exchanger specification: process outlet {_second(sp, u)}; minimum temperature approach {sp.get('dTmin', 10):g} C; "
                   f"U = {_fmt(sp.get('U'))} {u.unit('u_coefficient')} (U-methods: constant)",
                   "Flowsheeting Options > Design Spec: vary the utility stream's mass flow until its outlet temperature is "
                   f"{ut.T_out - 273.15:.4g} C" + (f" (Refluxion: {u.from_si(uflow[1], 'mass_flow'):.5g} {u.unit('mass_flow')})" if uflow else "")]
        elif t == "DSTWU":
            rr = sp.get("reflux_ratio")
            det = [f"Specifications: {'Reflux ratio = ' + _fmt(rr) if rr is not None else 'Number of stages = ' + _fmt(sp.get('n_stages'))}"
                   + (" (negative = multiple of Rmin)" if isinstance(rr, (int, float)) and rr < 0 else ""),
                   f"Light key {sp['LK']} recovery = {sp['rec_LK']:g}; heavy key {sp['HK']} recovery = {sp['rec_HK']:g}",
                   f"Pressure: condenser = {_fmt(sp.get('P_cond'))}, reboiler = {_fmt(sp.get('P_reb', sp.get('P_cond')))} {u.unit('pressure')}",
                   f"Condenser: {'Total' if str(sp.get('condenser', 'total')).startswith('total') else 'Partial with all vapor distillate'}"]
        elif t == "RadFrac":
            cond = {"total": "Total", "partial": "Partial-Vapor", "partial-vapor": "Partial-Vapor", "none": "None"}.get(str(sp["condenser"]).lower(), sp["condenser"])
            reb = "Kettle" if sp["reboiler"] == "kettle" else "None"
            det.append(f"Configuration: Calculation type Equilibrium; Number of stages = {sp['n_stages']}; Condenser = {cond}; "
                       f"Reboiler = {reb}; Valid phases Vapor-Liquid")
            for k, v in (sp.get("specs") or {}).items():
                if k == "distillate_mass_rate":
                    det.append(f"Configuration > Operating specifications: Distillate rate, basis Mass = {_fmt(v)} {u.unit('mass_flow')}")
                    continue
                if k == "bottoms_mass_rate":
                    det.append(f"Configuration > Operating specifications: Bottoms rate, basis Mass = {_fmt(v)} {u.unit('mass_flow')}")
                    continue
                if k == "reflux_ratio_mass":
                    det.append(f"Configuration > Operating specifications: Reflux ratio, basis Mass = {_fmt(v)}")
                    continue
                name = k if isinstance(k, str) else f"{k[0]} of {k[1]} in {k[2]}"
                unit = u.unit("duty") if "duty" in str(k) else (u.unit("mole_flow") if str(k).endswith("rate") else "")
                det.append(f"Configuration > Operating specifications: {name.replace('_', ' ')} = {_fmt(v)} {unit}".rstrip()
                           + (" (in Aspen: add as a Design Spec + Vary inside the column)" if isinstance(k, tuple) else ""))
            conv = str(sp.get("feed_convention", "above-stage")).capitalize()
            for f, st in sp.get("feeds", {}).items():
                stage, c = st if isinstance(st, tuple) else (st, conv)
                det.append(f"Streams: {f} on stage {stage}, convention {str(c).capitalize()}")
            det.append(f"Pressure: Stage 1 / condenser pressure = {_fmt(sp['P_top'])} {u.unit('pressure')}"
                       + (f"; column pressure drop = {_fmt(sp['dP_column'])} {u.unit('delta_pressure')}" if sp.get("dP_column") is not None else "")
                       + (f"; stage pressure drop = {_fmt(sp['dP_stage'])}" if sp.get("dP_stage") is not None else ""))
            for stage, q in (sp.get("heaters") or {}).items():
                det.append(f"Heaters Coolers: stage {stage} duty = {_fmt(q)} {u.unit('duty')}")
            if sp.get("murphree"):
                det.append(f"Efficiencies: Murphree {sp['murphree']}")
            if sp.get("T_est"):
                det.append("Estimates > Temperature: " + ", ".join(f"stage {k} = {_fmt(v)}" for k, v in sp["T_est"].items()))
            det.append("Convergence (if needed): Basic > max iterations 200; Advanced > damping Mild; flash iterations 100")
        lines.append(head)
        lines += [f"    - {d}" for d in det if d]
    if fs.design_specs:
        lines += ["", "**Flowsheeting Options > Design Spec**", ""]
        for ds in fs.design_specs:
            lines.append(f"- {ds.name}: target {ds.target:g} (tolerance {ds.tol:g}); Vary `{ds.vary}` between {ds.lower:g} and {ds.upper:g}")
    return "\n".join(lines)

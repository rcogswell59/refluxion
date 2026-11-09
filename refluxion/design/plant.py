"""Whole-plant evaluation of a solved flowsheet: size and cost every item, assign and price utilities, add raw
materials, waste and labor, and run the DCF economics.

Equipment is found by block type, so any flowsheet works:

* RadFrac: tower shell + sieve trays (Fair sizing), condenser, reboiler (cheapest feasible utility), reflux drum and
  reflux pump; a column below 0.9 bar gets a vacuum pump and a vent-load estimate
* Pump: centrifugal pump at its brake power; Flash2: vertical drum; Heater: exchanger on the cheapest feasible utility;
  UtilityHX: exchanger at its own area and utility
* storage tanks for every listed feed, product, makeup and waste stream (``tank_hours`` of surge)

Optional heat integration pairs each column condenser with another column's reboiler when the temperatures allow
(greedy, largest exchangeable duty first); the pair becomes one kettle-type exchanger.

Operating cost uses Turton's cost of manufacturing without depreciation, COM_d = 0.18 FCI + 2.73 C_OL +
1.23 (C_UT + C_WT + C_RM), with operators N_OL = 4.5 x (6.29 + 0.23 N_np)^0.5. Capital is grassroots
(:func:`refluxion.design.costing.capital_estimate`).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .costing import capital_estimate, cost_equipment
from .economics import cash_flows
from .safety import vent_load
from .sizing import size_drum, size_packed_column, size_tank, size_tray_column
from ..utilities import Utility, assess, pick_utility


@dataclass
class PlantBasis:
    cooling: list                    # cooling Utility objects
    heating: list                    # heating Utility objects
    electricity: Utility
    dTmin: float = 10.0              # C
    U: dict = field(default_factory=lambda: {"condenser": 850.0, "reboiler": 1140.0, "liquid": 850.0})   # W/m2-K
    cepci: float = 800.0
    hours: float = 8000.0
    tax: float = 0.28
    marr: float = 0.15
    life: int = 20
    depreciation: str = "MACRS"
    macrs_years: int = 7
    wc_frac: float = 0.15            # working capital / FCI
    wage: float = 70_000.0           # $/yr per operator
    price: dict = field(default_factory=dict)        # component -> $/kg (products and makeup)
    material: str = "SS316"
    utility_material: str = "CS/SS316"
    tank_hours: float = 24.0
    reflux_pump_dP_bar: float = 3.0
    pump_eff: float = 0.6
    driver_eff: float = 0.9
    vacuum_air_leak_kg_h: float = 2.0
    vent_condenser_T_C: float = -9.0
    internals: str = "trays"         # "trays", "packing" or "cheaper" (choose per column by bare-module cost)
    assumptions: dict = field(default_factory=dict)


def _barg(P_Pa):
    return P_Pa / 1e5 - 1.01325


def _lmtd(d1, d2):
    if d1 <= 0 or d2 <= 0:
        return float("nan")
    return (d1 - d2) / math.log(d1 / d2) if abs(d1 - d2) > 1e-9 else d1


def _exchanger(name, area, material, cepci, P_barg=0.0):
    kind = "exchanger_double_pipe" if area < 10.0 else "exchanger_floating_head"
    return cost_equipment(kind, max(area, 1e-3), material=material, P_barg=P_barg, name=name, cepci=cepci)


def _tank_item(name, V, material, cepci):
    if V >= 90.0:
        return cost_equipment("tank_fixed_roof", V, material=material, name=name, cepci=cepci)
    D = (4.0 * V / (math.pi * 3.0)) ** (1 / 3)
    return cost_equipment("vessel_horizontal", V, material=material, P_barg=0.0, D_m=D, name=name, cepci=cepci)


def evaluate_plant(fs, basis: PlantBasis, *, products: dict, feeds=(), makeup: dict | None = None, waste: dict | None = None,
                   keys: dict | None = None, heat_integration: bool = False, extra_value_per_hr: float = 0.0,
                   vent_comp: str | None = None, min_tank_m3: dict | None = None) -> dict:
    """Evaluate a solved flowsheet.

    ``products = {label: (stream, component)}`` valued at ``basis.price[component]`` per kg of that component;
    ``makeup = {stream: component}`` bought at ``basis.price``; ``waste = {stream: price_fn(mass_frac) -> $/kg}``;
    ``keys = {column: (LK, HK)}`` for O'Connell efficiencies; ``extra_value_per_hr`` (e.g. avoided disposal);
    ``min_tank_m3 = {stream: volume}`` (e.g. a tank that must hold a full trailer load).
    """
    keys, makeup, waste, min_tank_m3 = dict(keys or {}), dict(makeup or {}), dict(waste or {}), dict(min_tank_m3 or {})
    m: dict = {}
    items, util = [], []
    cond, reb = {}, {}
    n_np = 0
    cep = basis.cepci
    # ---------------------------------------------------------------- columns
    for b in fs.blocks.values():
        if b.type_name != "RadFrac":
            continue
        name = b.name
        p = b.tables["profiles"]
        T, P = p["T"], float(p["P"][0])
        options = {}
        if basis.internals in ("trays", "cheaper"):
            s = size_tray_column(b, keys=keys.get(name))
            D, H = s["diameter_m"], s["height_m"]
            options["trays"] = (s, [cost_equipment("tower", math.pi / 4 * D * D * H, material=basis.material, P_barg=_barg(P), D_m=D,
                                                   name=f"{name} tower", cepci=cep),
                                    cost_equipment("tray_sieve", math.pi / 4 * D * D, material=basis.material, n_trays=s["trays"],
                                                   name=f"{name} trays", cepci=cep)])
        if basis.internals in ("packing", "cheaper"):
            s = size_packed_column(b)
            D, H = s["diameter_m"], s["height_m"]
            options["packing"] = (s, [cost_equipment("tower", math.pi / 4 * D * D * H, material=basis.material, P_barg=_barg(P), D_m=D,
                                                     name=f"{name} tower", cepci=cep),
                                      cost_equipment("packing_loose", math.pi / 4 * D * D * s["packed_height_m"], material=basis.material,
                                                     name=f"{name} packing", cepci=cep)])
        choice = min(options, key=lambda k: sum(i.bare_module_usd for i in options[k][1]))
        s, col_items = options[choice]
        items.extend(col_items)
        m.update({f"{name}_internals": choice, f"{name}_diameter_m": s["diameter_m"], f"{name}_height_m": s["height_m"],
                  f"{name}_trays": s.get("trays", 0), f"{name}_efficiency": s.get("efficiency"), f"{name}_sizing": s["basis"],
                  f"T_top_{name}": float(T[0]) - 273.15, f"T_bottom_{name}": float(T[-1]) - 273.15})
        if len(options) > 1:
            m[f"{name}_internals_cost"] = {k: sum(i.bare_module_usd for i in v[1]) for k, v in options.items()}
        n_np += 1
        if b.cond != "none":
            cond[name] = [b.result("condenser_duty", units="W"), float(T[1]), float(T[0])]
            Lm = float(p["L"][0]) * float(p["x"][0] @ b.comps.MW) / 1000.0
            rho = float(p["x"][0] @ b.comps.MW) / 1000.0 / float(b.pm.v_liq(float(T[0]), P, p["x"][0]))
            dr = size_drum(Lm / rho * 3600.0)
            items.append(cost_equipment("vessel_horizontal", dr["volume_m3"], material=basis.material, P_barg=_barg(P), D_m=dr["D_m"],
                                        name=f"{name} reflux drum", cepci=cep))
            kw = Lm / rho * basis.reflux_pump_dP_bar * 1e5 / basis.pump_eff / 1e3
            items.append(cost_equipment("pump_centrifugal", kw, material=basis.material, name=f"{name} reflux pump", cepci=cep))
            util.append(dict(item=f"{name} reflux pump", utility=basis.electricity, duty_W=kw * 1e3 / basis.driver_eff))
        if b.reb != "none":
            reb[name] = [b.result("reboiler_duty", units="W"), float(T[-2]), float(T[-1])]
        if P < 0.9e5:
            vac_kw = max(1.0, basis.vacuum_air_leak_kg_h / 28.96 / 3600 * 8.314 * 300 * math.log(1.01325e5 / P) / 0.3)
            items.append(cost_equipment("pump_positive_displacement", vac_kw, material=basis.material, name=f"{name} vacuum pump", cepci=cep))
            util.append(dict(item=f"{name} vacuum pump", utility=basis.electricity, duty_W=vac_kw * 1e3))
            if vent_comp:
                m[f"vent_{name}_kg_h"] = vent_load(fs, vent_comp, P_kPa=P / 1e3, T_vent_C=basis.vent_condenser_T_C,
                                                   air_leak_kg_h=basis.vacuum_air_leak_kg_h)
    m["vent_kg_h"] = sum(v for k, v in m.items() if k.startswith("vent_") and k.endswith("_kg_h"))
    # ---------------------------------------------------------------- heat integration (condenser -> another column's reboiler)
    hi_total, pairs = 0.0, []
    if heat_integration:
        cands = []
        for c, (Qc, _, Tc_out) in cond.items():
            for r, (Qr, _, Tr_out) in reb.items():
                if c != r and Tc_out - Tr_out >= basis.dTmin:
                    cands.append((min(-Qc, Qr), c, r, Tc_out - Tr_out))
        used = set()
        for Q, c, r, dT in sorted(cands, reverse=True):
            if ("c", c) in used or ("r", r) in used:
                continue
            Q = min(-cond[c][0], reb[r][0])
            if Q <= 0:
                continue
            items.append(cost_equipment("kettle_reboiler", Q / (basis.U["reboiler"] * dT), material=basis.material,
                                        name=f"{c} condenser / {r} reboiler", cepci=cep))
            n_np += 1
            cond[c][0] += Q
            reb[r][0] -= Q
            used |= {("c", c), ("r", r)}
            pairs.append((c, r, Q / 1e3, dT))
            hi_total += Q
    m["hi_pairs"], m["hi_duty_kW"] = pairs, hi_total / 1e3
    # ---------------------------------------------------------------- condensers and reboilers on utilities
    for name, (Q, T_in, T_out) in cond.items():
        if -Q < 1.0:
            m[f"utility_{name} condenser"] = "none"
            continue
        u = pick_utility(basis.cooling, "cooling", T_hot_in=T_in, T_hot_out=T_out, dTmin=basis.dTmin)
        if u is None:
            raise ValueError(f"{name} condenser at {T_out - 273.15:.1f} C: no cooling utility is cold enough")
        A = -Q / (basis.U["condenser"] * _lmtd(T_in - u.T_out, T_out - u.T_in))
        items.append(_exchanger(f"{name} condenser", A, basis.utility_material, cep))
        n_np += 1
        util.append(dict(item=f"{name} condenser", utility=u, duty_W=Q))
        m[f"utility_{name} condenser"] = u.name
    for name, (Q, T_in, T_out) in reb.items():
        if Q < 1.0:
            m[f"utility_{name} reboiler"] = "none"
            continue
        u = pick_utility(basis.heating, "heating", T_cold_in=T_in, T_cold_out=T_out, dTmin=basis.dTmin)
        if u is None:
            raise ValueError(f"{name} reboiler at {T_out - 273.15:.1f} C: no heating utility is hot enough")
        items.append(cost_equipment("kettle_reboiler", Q / (basis.U["reboiler"] * (u.T_in - T_out)), material=basis.utility_material,
                                    name=f"{name} reboiler", cepci=cep))
        n_np += 1
        util.append(dict(item=f"{name} reboiler", utility=u, duty_W=Q))
        m[f"utility_{name} reboiler"] = u.name
    # ---------------------------------------------------------------- pumps, flash drums, heaters, utility exchangers
    for b in fs.blocks.values():
        if b.type_name == "Pump":
            items.append(cost_equipment("pump_centrifugal", b.result("brake_power", units="kW"), material=basis.material, name=b.name, cepci=cep))
            util.append(dict(item=b.name, utility=basis.electricity, duty_W=b.result("electricity", units="W")))
        elif b.type_name == "Flash2":
            liq = b.outlet("liquid").volume_flow * 3600.0
            vap = b.outlet("vapor").volume_flow * 3600.0
            V = max(size_drum(max(liq, 1e-6), holdup_min=10.0)["volume_m3"], vap / 3600.0 * 5.0)
            D = (4 * V / (math.pi * 3.0)) ** (1 / 3)
            items.append(cost_equipment("vessel_vertical", V, material=basis.material, P_barg=_barg(b.result("P", units="Pa")), D_m=D,
                                        name=f"{b.name} drum", cepci=cep))
            Q = b.result("duty", units="W")
            if abs(Q) > 1.0:                       # a flash at a set temperature needs a heater (or cooler) on its feed
                T_in, T_fl = max(s.T for s in b.all_inlets()), b.result("T", units="K")
                if Q > 0:
                    u = pick_utility(basis.heating, "heating", T_cold_in=T_in, T_cold_out=T_fl, dTmin=basis.dTmin)
                    dT = _lmtd(u.T_in - T_fl, u.T_out - T_in) if u else None
                else:
                    u = pick_utility(basis.cooling, "cooling", T_hot_in=T_in, T_hot_out=T_fl, dTmin=basis.dTmin)
                    dT = _lmtd(T_in - u.T_out, T_fl - u.T_in) if u else None
                if u is None:
                    raise ValueError(f"{b.name}: no utility can serve the flash duty")
                items.append(_exchanger(f"{b.name} heater", abs(Q) / (basis.U["liquid"] * dT), basis.utility_material, cep))
                n_np += 1
                util.append(dict(item=f"{b.name} heater", utility=u, duty_W=Q))
                m[f"utility_{b.name} heater"] = u.name
        elif b.type_name == "Heater" and abs(b.result("duty", units="W")) > 1.0:
            Q = b.result("duty", units="W")
            s_in, s_out = b.all_inlets()[0], b.outlet("out")
            if Q < 0:
                u = pick_utility(basis.cooling, "cooling", T_hot_in=s_in.T, T_hot_out=s_out.T, dTmin=basis.dTmin)
                if u is None:
                    raise ValueError(f"{b.name}: no cooling utility is cold enough")
                dT = _lmtd(s_in.T - u.T_out, s_out.T - u.T_in)
                Uv = basis.U["condenser"]
            else:
                u = pick_utility(basis.heating, "heating", T_cold_in=s_in.T, T_cold_out=s_out.T, dTmin=basis.dTmin)
                if u is None:
                    raise ValueError(f"{b.name}: no heating utility is hot enough")
                dT = _lmtd(u.T_in - s_out.T, u.T_out - s_in.T)
                Uv = basis.U["liquid"]
            items.append(_exchanger(b.name, abs(Q) / (Uv * dT), basis.utility_material, cep))
            n_np += 1
            util.append(dict(item=b.name, utility=u, duty_W=Q))
            m[f"utility_{b.name}"] = u.name
        elif b.type_name == "UtilityHX" and b.result("duty") != 0.0:
            items.append(_exchanger(b.name, b.result("area", units="m2"), basis.utility_material, cep))
            n_np += 1
            util.append(dict(item=b.name, utility=b.utility, duty_W=b.result("duty", units="W")))
    # ---------------------------------------------------------------- storage tanks
    tank_streams = list(feeds) + [s for s, _ in products.values()] + list(makeup) + list(waste)
    vols = {}
    for sname in dict.fromkeys(tank_streams):
        st = fs[sname]
        V = max(size_tank(st.get("mass_flow", units="kg/hr") / st.density, hours=basis.tank_hours)["volume_m3"], min_tank_m3.get(sname, 0.0))
        vols[sname] = V
        items.append(_tank_item(f"{sname} tank", V, basis.material, cep))
    m["tank_volumes_m3"] = vols
    m["largest_tank_m3"] = max(vols.values()) if vols else 0.0
    m["spill_containment_m3"] = 1.5 * m["largest_tank_m3"]
    # ---------------------------------------------------------------- capital and operating cost
    cap = capital_estimate(items)
    FCI = cap["grassroots"]
    costs_by_utility = {}
    for r in util:
        a = assess(r["utility"], r["duty_W"], basis.hours)
        r.update(cost_per_yr=a["cost_per_yr"], flow_kg_h=a["flow_kg_h"], utility=r["utility"].name)
        costs_by_utility[r["utility"]] = costs_by_utility.get(r["utility"], 0.0) + a["cost_per_yr"]
    C_UT = sum(costs_by_utility.values())
    C_RM = sum(fs[s].get("mass_flow", c, units="kg/hr") * basis.price[c] for s, c in makeup.items()) * basis.hours
    C_WT = 0.0
    for sname, fn in waste.items():
        st = fs[sname]
        C_WT += st.get("mass_flow", units="kg/hr") * fn({c: st.get("mass_frac", c) for c in fs.comps.ids}) * basis.hours
    operators = math.ceil(4.5 * math.sqrt(6.29 + 0.23 * n_np))
    C_OL = operators * basis.wage
    COM = 0.18 * FCI + 2.73 * C_OL + 1.23 * (C_UT + C_WT + C_RM)
    prod_kg = {lab: fs[s].get("mass_flow", c, units="kg/hr") for lab, (s, c) in products.items()}
    value = (sum(prod_kg[lab] * basis.price[c] for lab, (s, c) in products.items()) + extra_value_per_hr) * basis.hours
    cf = cash_flows(FCI, basis.wc_frac * FCI, value, COM, years=basis.life, tax=basis.tax, depreciation=basis.depreciation,
                    dep_years=basis.macrs_years, i=basis.marr)
    m.update(capital=FCI, bare_module=cap["bare_module"], equipment=[dict(r) for r in cap["rows"]], utilities=util,
             utility_costs=costs_by_utility, C_UT=C_UT, C_RM=C_RM, C_WT=C_WT, C_OL=C_OL, operators=operators, n_np=n_np,
             opex_per_yr=COM, value_per_yr=value, product_kg_h=prod_kg, npv=cf.npv, irr=cf.irr, payback=cf.payback, cash_flow=cf)
    return m

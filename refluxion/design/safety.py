"""Process-safety checks for preliminary design.

These turn design rules into numbers a reviewer can check, and give HAZOP/FMEA work a structured start.
They do not replace the analyses themselves.

* :func:`flash_point_check` - product/storage streams below their flash point
* :func:`ideal_solubility` - solid solubility (ideal solution): ln x = -dHfus/R (1/T - 1/Tm)
* :func:`vent_load` - VOC carried out of a vacuum system by air leakage, saturated at the vent condenser
* :func:`mawp_table` - design pressure/temperature per vessel (heuristics + a minimum rating), full-vacuum flag
* :func:`hazop_nodes` - one node per block with its operating window and the standard guide-word deviations
"""

from __future__ import annotations

import math

R = 8.314462618

GUIDE_WORDS = ["No flow", "More flow", "Less flow", "Reverse flow", "More pressure", "Less pressure (vacuum)",
               "More temperature", "Less temperature", "More level", "Less level", "Composition change / contamination",
               "Loss of utility (cooling water, steam, coolant, power, instrument air)", "Startup / shutdown", "Maintenance / drain"]


def ideal_solubility(dHfus_J_mol: float, Tm_K: float, T_K: float) -> float:
    """Mole-fraction solubility of a solid in an ideal solution (1 at or above the melting point)."""
    if T_K >= Tm_K:
        return 1.0
    return math.exp(-dHfus_J_mol / R * (1.0 / T_K - 1.0 / Tm_K))


def vent_load(fs, comp: str, *, P_kPa: float, T_vent_C: float, air_leak_kg_h: float, x: float = 1.0) -> float:
    """kg/h of ``comp`` leaving a vacuum system: air leakage saturated with ``comp`` (Raoult, liquid mole fraction ``x``)
    at the vent-condenser temperature and the system pressure."""
    i = fs.comps.idx(comp)
    Ps = float(fs.comps.Psat(T_vent_C + 273.15)[i]) * x
    P = P_kPa * 1e3
    if Ps >= P:
        return float("inf")
    n_air = air_leak_kg_h / 28.96
    return n_air * Ps / (P - Ps) * float(fs.comps.MW[i])


def flash_point_check(fs, limits: dict) -> list:
    """``limits = {stream: flash point C}``; ok when the stream is colder than its flash point."""
    rows = []
    for name, fp in limits.items():
        T = fs[name].get("T", units="C")
        rows.append(dict(stream=name, T_C=T, flash_point_C=fp, ok=bool(T < fp), margin_C=fp - T))
    return rows


def mawp_table(items, min_barg: float = 3.45, margin: float = 0.10, add_bar: float = 1.72, add_T: float = 28.0,
               vacuum_below_barg: float = -0.1) -> list:
    """``items = [(name, max operating P barg, max operating T C)]``. Design P = max(1.1 P, P + 1.72 bar, minimum
    rating); design T = T + 28 C (50 F). A vessel operating below ``vacuum_below_barg`` is also rated for full vacuum."""
    rows = []
    for name, P, T in items:
        dp = max(P * (1 + margin), P + add_bar, min_barg)
        rows.append(dict(item=name, operating_P_barg=P, operating_T_C=T, design_P_barg=dp, design_T_C=T + add_T,
                         full_vacuum=bool(P < vacuum_below_barg), basis=f"max(1.1 P, P + {add_bar} bar, {min_barg} barg); T + {add_T} C"))
    return rows


def hazop_nodes(fs) -> list:
    """One HAZOP node per block: equipment, connected lines, operating window and guide-word deviations to work."""
    out = []
    for b in fs.blocks.values():
        if b.status in ("not run", "error"):
            continue
        ins = [s for s in b.all_inlets() if s.solved]
        outs = [s for s in b.outlets.values() if s is not None and s.solved]
        Ts = [s.get("T", units="C") for s in ins + outs]
        Ps = [s.get("P", units="kPa") for s in ins + outs]
        out.append(dict(node=b.name, equipment=b.type_name, inlets=[s.name for s in ins], outlets=[s.name for s in outs],
                        T_range_C=(min(Ts), max(Ts)) if Ts else None, P_range_kPa=(min(Ps), max(Ps)) if Ps else None,
                        deviations=list(GUIDE_WORDS)))
    return out


# ------------------------------------------------------------------------------------------------ FMEA scaffold
_FAILURE_MODES = {
    "RadFrac": [("Tray/packing flooding", "off-spec products, pressure surge", "column dP, top temperature, product analyzer"),
                ("Loss of vacuum or pressure control", "temperature profile shifts, off-spec products, air ingress", "column pressure"),
                ("Fouling or maldistribution", "loss of separation", "temperature profile, dP trend")],
    "condenser": [("Loss of coolant", "column overpressure, vapor release to relief/vent", "coolant flow, column pressure"),
                  ("Tube leak", "cross-contamination of process and utility", "utility-side analyzer, conductivity")],
    "reboiler": [("Loss of steam", "loss of boil-up, light ends to bottoms, off-spec", "steam flow, bottoms temperature"),
                 ("Steam control valve fails open", "excess boil-up, flooding, overpressure", "column dP, pressure"),
                 ("Tube leak", "steam/condensate contamination with solvent", "condensate analyzer")],
    "Pump": [("Pump trips / fails", "loss of flow, level rises upstream", "flow, upstream level, motor status"),
             ("Seal leak", "flammable/toxic liquid release", "seal pot level, gas detection"),
             ("Cavitation (low NPSH, vacuum suction)", "damage, flow loss", "vibration, discharge pressure")],
    "Flash2": [("High liquid level / carry-over", "liquid in vapor line, heavies into recycle", "level"),
               ("Temperature controller failure", "bottoms off spec (heavies carry-over) or all-vapor", "temperature, waste density")],
    "UtilityHX": [("Loss of coolant", "product stored above its flash point", "outlet temperature alarm"),
                  ("Tube leak", "solvent into coolant system / coolant into product", "analyzer")],
    "Heater": [("Loss of utility", "outlet off target", "outlet temperature")],
    "Mixer": [("Makeup valve fails", "solvent inventory drifts", "flow, level")],
}


def fmea_rows(fs) -> list:
    """FMEA worksheet rows per equipment item (failure mode, effect, detection) with severity, occurrence, detection
    ratings and actions left blank for the team to fill."""
    rows = []

    def add(eq, kind):
        for mode, effect, det in _FAILURE_MODES.get(kind, []):
            rows.append(dict(equipment=eq, failure_mode=mode, effect=effect, detection=det, S=None, O=None, D=None, RPN=None, action=""))

    for b in fs.blocks.values():
        if b.type_name == "RadFrac":
            add(b.name, "RadFrac")
            if b.spec.get("condenser", "total") != "none":
                add(f"{b.name} condenser", "condenser")
            if b.spec.get("reboiler", "kettle") != "none":
                add(f"{b.name} reboiler", "reboiler")
        else:
            add(b.name, b.type_name)
    return rows


def isd_facts(metrics: dict, max_P_barg: float, min_P_barg: float, flammables, substitute_note: str = "",
              simplify_note: str = "") -> list:
    """Evidence for the inherently-safer-design checklist, one row per principle (Kletz: minimize, substitute,
    moderate, simplify), drawn from the design's numbers plus project notes."""
    tanks = metrics.get("tank_volumes_m3", {})
    T0, T1 = metrics.get("T_range_C", (None, None))
    return [
        dict(principle="Minimize", evidence=f"largest storage tank {metrics.get('largest_tank_m3', 0):.1f} m3; total storage "
             f"{sum(tanks.values()):.1f} m3 in {len(tanks)} tanks"),
        dict(principle="Substitute", evidence=f"flammable liquids handled: {', '.join(flammables)}" + (f"; {substitute_note}" if substitute_note else "")),
        dict(principle="Moderate", evidence=f"pressures {min_P_barg:.2f} to {max_P_barg:.2f} barg" +
             (" (vacuum operation: air ingress and vent load)" if min_P_barg < -0.1 else "") +
             (f"; column temperatures {T0:.0f}-{T1:.0f} C" if T0 is not None else "")),
        dict(principle="Simplify", evidence=simplify_note or "see the equipment list"),
    ]

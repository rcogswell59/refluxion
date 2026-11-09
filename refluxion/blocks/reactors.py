"""Reactors.

RStoic  stoichiometric reactor: known fractional conversion (of a key component) or extent for
        each reaction. Energy balance from the outlet flash; because enthalpies are referenced to
        heats of formation, the heat of reaction is included automatically - exactly Aspen's approach.
"""

from __future__ import annotations

import numpy as np

from .base import Block, BlockError, flash_outlet, register


@register
class RStoic(Block):
    """``reactions=[dict(stoich={"C6H6": -1, "C3H6": -1, "CUMENE": 1}, key="C3H6", conversion=0.9)]``

    Each reaction takes either ``key`` + ``conversion`` (fraction of the key component's inlet
    flow) or ``extent`` (mole flow of reaction). ``series=True`` bases each conversion on the flow
    leaving the previous reaction instead of the block inlet (Aspen's "series reactions" box).
    Specs: ``P`` (or ``dP``) plus one of ``T``, ``Q`` (Q=0 is adiabatic), ``vf``.
    """

    type_name = "RStoic"
    category = "Reactors"
    icon = "reactor"

    def run(self):
        flows_in, H_in, P_in, T_in = self.feed_totals()
        flows = flows_in.copy()
        reactions = self.spec.get("reactions", [])
        series = bool(self.spec.get("series", False))
        extents, dHr = [], []
        C = len(flows)
        for r in reactions:
            nu = np.zeros(C)
            for cid, v in r["stoich"].items():
                nu[self.comps.idx(cid)] = float(v)
            basis = flows if series else flows_in
            if "extent" in r:
                xi = self.fs.units.to_si(r["extent"], "mole_flow")
            else:
                k = self.comps.idx(r["key"])
                if nu[k] >= 0:
                    raise BlockError(f"{self.name}: key component {r['key']} must be a reactant")
                xi = float(r["conversion"]) * basis[k] / (-nu[k])
            flows = flows + nu * xi
            extents.append(xi)
            dHr.append(float(np.sum(nu * self.comps.Hf)))
        if np.any(flows < -1e-9 * max(flows_in.sum(), 1e-30)):
            neg = [self.comps.ids[i] for i in np.where(flows < 0)[0]]
            raise BlockError(f"{self.name}: conversions consume more {neg} than is fed")
        flows = np.maximum(flows, 0.0)
        P = self.outlet_pressure(P_in)
        out = self.outlet("out")
        Q = flash_outlet(self, out, flows, P, H_in, T=self.si("T", "temperature"), Q=self.si("Q", "duty"),
                         vf=self.spec.get("vf"), T_in=T_in)
        self.set_result("T", "Outlet temperature", out.T, "temperature")
        self.set_result("P", "Outlet pressure", out.P, "pressure")
        self.set_result("vf", "Outlet vapor fraction", out.vf, "fraction")
        self.set_result("duty", "Heat duty", Q, "duty")
        rows = []
        for i, (r, xi, dh) in enumerate(zip(reactions, extents, dHr), 1):
            eq = " + ".join(f"{-v:g} {c}" for c, v in r["stoich"].items() if v < 0) + " -> " + \
                 " + ".join(f"{v:g} {c}" for c, v in r["stoich"].items() if v > 0)
            self.set_result(f"extent_{i}", f"Reaction {i} extent", xi, "mole_flow")
            self.set_result(f"dHr_{i}", f"Reaction {i} heat of reaction (25 C, per unit extent)", dh, "molar_enthalpy")
            rows.append(dict(reaction=i, equation=eq, extent=xi, dHr=dh))
        self.tables["reactions"] = {"rows": rows}
        self.status = "ok"


@register
class HeatX(Block):
    """Two-stream heat exchanger, shortcut design with zone analysis.

    Ports: ``hot_in``, ``hot_out``, ``cold_in``, ``cold_out``. One exchanger spec:
    ``hot_T_out``, ``cold_T_out``, ``duty``, ``hot_vf_out``, ``cold_vf_out``, or ``min_approach``
    (hot-inlet/cold-outlet approach for countercurrent). Optional ``dP_hot``, ``dP_cold``,
    ``U`` (default 850 W/m2-K, Aspen's shortcut default), ``flow`` = "countercurrent" | "cocurrent",
    ``zones`` (points for the heat curves, default 12).
    """

    type_name = "HeatX"
    category = "Exchangers"
    icon = "heatx"
    inlet_ports = ("hot_in", "cold_in")
    outlet_ports = ("hot_out", "cold_out")

    def run(self):
        from ..props.flash import ph_flash, pvf_flash, tp_flash

        pm = self.pm
        hf, hH, hP, hT = self.feed_totals("hot_in")
        cf, cH, cP, cT = self.feed_totals("cold_in")
        hP_out = hP - (self.si("dP_hot", "delta_pressure") or 0.0)
        cP_out = cP - (self.si("dP_cold", "delta_pressure") or 0.0)
        Fh, Fc = hf.sum(), cf.sum()
        zh, zc = hf / Fh, cf / Fc
        s = self.spec
        if s.get("duty") is not None:
            Q = self.si("duty", "duty")
        elif s.get("hot_T_out") is not None:
            Q = hH - Fh * tp_flash(pm, self.si("hot_T_out", "temperature"), hP_out, zh).h(pm)
        elif s.get("cold_T_out") is not None:
            Q = Fc * tp_flash(pm, self.si("cold_T_out", "temperature"), cP_out, zc).h(pm) - cH
        elif s.get("hot_vf_out") is not None:
            Q = hH - Fh * pvf_flash(pm, hP_out, s["hot_vf_out"], zh).h(pm)
        elif s.get("cold_vf_out") is not None:
            Q = Fc * pvf_flash(pm, cP_out, s["cold_vf_out"], zc).h(pm) - cH
        elif s.get("min_approach") is not None:
            Q = hH - Fh * tp_flash(pm, cT + self.si("min_approach", "delta_temperature"), hP_out, zh).h(pm)
        else:
            raise BlockError(f"{self.name}: give one exchanger spec (hot_T_out, cold_T_out, duty, ...)")
        if Q < 0:
            raise BlockError(f"{self.name}: the spec implies heat flowing from cold to hot side")
        ho, co = self.outlet("hot_out"), self.outlet("cold_out")
        ho.set_state(hf, P=hP_out, H=(hH - Q) / Fh, T0=hT)
        co.set_state(cf, P=cP_out, H=(cH + Q) / Fc, T0=cT)
        # zone analysis: temperatures along the exchanger (heat curves)
        n = int(s.get("zones", 12))
        counter = str(s.get("flow", "countercurrent")).lower().startswith("counter")
        qs = np.linspace(0.0, Q, n + 1)          # heat released by the hot side from its inlet
        Th, Tc, Kh, Kc = [], [], None, None
        for q in qs:
            P_h = hP + (hP_out - hP) * (q / Q if Q else 0)
            rh = ph_flash(pm, P_h, (hH - q) / Fh, zh, T0=Th[-1] if Th else hT)
            Th.append(rh.T)
            q_cold = (Q - q) if counter else q   # cold side absorbed so far at that position
            P_c = cP + (cP_out - cP) * (q_cold / Q if Q else 0)
            rc = ph_flash(pm, P_c, (cH + q_cold) / Fc, zc, T0=Tc[-1] if Tc else cT)
            Tc.append(rc.T)
        Th, Tc = np.array(Th), np.array(Tc)
        dT = Th - Tc
        U = self.si("U", "u_coefficient") or 850.0
        if np.any(dT <= 0):
            self.messages.append("Temperature cross: the hot side is colder than the cold side somewhere "
                                 "in the exchanger - this duty is not feasible.")
            area = float("nan")
        else:
            a, b = dT[:-1], dT[1:]
            lm = np.where(np.abs(a - b) > 1e-9, (a - b) / np.log(np.maximum(a, 1e-12) / np.maximum(b, 1e-12)), a)
            area = float(np.sum(np.diff(qs) / (U * lm)))
        if counter:
            d1, d2 = hT - co.T, ho.T - cT
        else:
            d1, d2 = hT - cT, ho.T - co.T
        lmtd = (d1 - d2) / np.log(d1 / d2) if (d1 > 0 and d2 > 0 and abs(d1 - d2) > 1e-9) else min(d1, d2)
        self.set_result("duty", "Exchanger duty", Q, "duty")
        self.set_result("hot_T_out", "Hot outlet temperature", ho.T, "temperature")
        self.set_result("cold_T_out", "Cold outlet temperature", co.T, "temperature")
        self.set_result("lmtd", "LMTD (terminal temperatures)", lmtd, "delta_temperature")
        self.set_result("min_approach", "Minimum temperature approach", float(dT.min()), "delta_temperature")
        self.set_result("U", "Overall U", U, "u_coefficient")
        self.set_result("UA", "UA (zone analysis)", U * area if np.isfinite(area) else float("nan"), "cp_flow")
        self.set_result("area", "Heat-transfer area (zone analysis)", area, "area")
        self.tables["heat_curve"] = {"duty": qs, "T_hot": Th, "T_cold": Tc}
        self.status = "ok" if not self.messages else "warning"

    def energy_balance_error(self) -> float:
        H_in = sum(s.H for s in self.all_inlets())
        H_out = sum(s.H for s in self.outlets.values())
        return (H_in - H_out) / max(abs(H_in), 1.0)

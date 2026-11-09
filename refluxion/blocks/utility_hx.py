"""UtilityHX - a heater or cooler whose other side is a utility (cooling water, steam, coolant).

Aspen models this as a HeatX with a utility stream and a design spec on the utility flow (e.g. a condenser,
TOLHX1/TOLHX2). Here the utility is a :class:`refluxion.utilities.Utility`: the block solves the process side like
a Heater (P plus one of T, vf, Q, dT), then sizes the utility side:

* utility flow from the duty (latent heat, or cp x the utility's temperature change)
* zone analysis along the process heat curve against the utility's temperature profile (counter-current;
  linear for a sensible utility, flat for condensing steam), giving the minimum approach, area = sum dQ/(U dT_lm)
* a BlockError if the utility cannot do the job with ``dTmin`` (e.g. cooling water asked for 4 C)
* ``skip_if_colder=True``: when the inlet is already past the target, the exchanger is bypassed (zero duty) - for
  cooling trains whose inlet temperature depends on the design
"""

from __future__ import annotations

import numpy as np

from .base import Block, BlockError, flash_outlet, register


@register
class UtilityHX(Block):
    type_name = "UtilityHX"
    category = "Exchangers"
    icon = "heater"
    inlet_ports = ("in",)
    outlet_ports = ("out",)

    def __init__(self, name, *, utility, U=850.0, dTmin=10.0, zones=12, **spec):
        super().__init__(name, U=U, dTmin=dTmin, zones=zones, **spec)
        self.utility = utility

    def run(self):
        from ..props.flash import ph_flash

        flows, H_in, P_in, T_in = self.feed_totals()
        P = self.outlet_pressure(P_in)
        out = self.outlet("out")
        Q = flash_outlet(self, out, flows, P, H_in, T=self.si("T", "temperature"), Q=self.si("Q", "duty"),
                         vf=self.spec.get("vf"), dT=self.si("dT", "delta_temperature"), T_in=T_in)
        u = self.utility
        if self.spec.get("skip_if_colder") and ((u.kind == "cooling" and Q >= 0) or (u.kind == "heating" and Q <= 0)):
            # already past the target: this exchanger is bypassed
            out.set_state(flows, P=P, H=H_in / flows.sum(), T0=T_in)
            for key, label, val, q in (("T", "Outlet temperature", out.T, "temperature"), ("P", "Outlet pressure", out.P, "pressure"),
                                       ("vf", "Outlet vapor fraction", out.vf, "fraction"), ("duty", "Heat duty", 0.0, "duty"),
                                       ("utility_flow", f"{u.name} flow", 0.0, "mass_flow"), ("area", "Heat-transfer area", 0.0, "area"),
                                       ("cost", "Utility cost", 0.0, "money_rate")):
                self.set_result(key, label, val, q)
            self.messages = [m for m in self.messages if not m.startswith("bypassed")] + ["bypassed: inlet already past the target"]
            self.status = "ok"
            return
        cooling = Q < 0
        if (u.kind == "cooling") != cooling:
            raise BlockError(f"{self.name}: {u.name} is a {u.kind} utility but the duty is {Q / 1e3:+.3g} kW")
        dTmin = float(self.spec["dTmin"])
        F = flows.sum()
        z = flows / F
        n = int(self.spec["zones"])
        qs = np.linspace(0.0, abs(Q), n + 1)                  # heat transferred so far, from the process inlet
        Tp = []
        for q in qs:
            h = (H_in - q) / F if cooling else (H_in + q) / F
            Tp.append(ph_flash(self.pm, P_in + (P - P_in) * (q / abs(Q) if Q else 0.0), h, z, T0=Tp[-1] if Tp else T_in).T)
        Tp = np.array(Tp)
        # counter-current: the utility leaves where the process enters
        frac = qs / abs(Q) if Q else qs * 0
        Tu = u.T_out + (u.T_in - u.T_out) * frac
        dT = (Tp - Tu) if cooling else (Tu - Tp)
        if dT.min() < dTmin - 1e-6:
            raise BlockError(f"{self.name}: {u.name} ({u.T_in - 273.15:.1f} -> {u.T_out - 273.15:.1f} C) cannot take the process "
                             f"from {T_in - 273.15:.1f} to {out.T - 273.15:.1f} C with dTmin {dTmin:g} C "
                             f"(closest approach {dT.min():.1f} C)")
        a, b = dT[:-1], dT[1:]
        lm = np.where(np.abs(a - b) > 1e-9, (a - b) / np.log(a / b), a)
        U = self.si("U", "u_coefficient")
        area = float(np.sum(np.diff(qs) / (U * lm)))
        e = u.energy_per_kg
        flow = abs(Q) / e if e else float("nan")          # kg/s
        cost = abs(Q) / 1e9 * u.price_per_GJ               # $/s
        self.set_result("T", "Outlet temperature", out.T, "temperature")
        self.set_result("P", "Outlet pressure", out.P, "pressure")
        self.set_result("vf", "Outlet vapor fraction", out.vf, "fraction")
        self.set_result("duty", "Heat duty", Q, "duty")
        self.set_result("utility_flow", f"{u.name} flow", flow, "mass_flow")
        self.set_result("min_approach", "Minimum temperature approach", float(dT.min()), "delta_temperature")
        self.set_result("lmtd", "Mean temperature difference (Q / UA)", abs(Q) / (U * area) if area > 0 else float("nan"), "delta_temperature")
        self.set_result("U", "Overall U", U, "u_coefficient")
        self.set_result("area", "Heat-transfer area", area, "area")
        self.set_result("cost", "Utility cost", cost, "money_rate")
        self.tables["heat_curve"] = {"duty": qs, "T_process": Tp, "T_utility": Tu}
        self.status = "ok"

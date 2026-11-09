"""Mixers/splitters, heaters and flash drums, pressure changers.

Mixer    adiabatic mixing (outlet P = lowest inlet P, or specified)
FSplit   stream splitter: fractions or flows to each outlet, same state
Sep      component separator: split fraction of each component to each outlet
Dupl     duplicator: copies the inlet to every outlet (for side-by-side comparisons)
Heater   heater/cooler: pressure (or drop) + one of T, Q, vf, dT
Flash2   two-outlet flash drum: same specs as Heater, outlets vapor + liquid
Pump     liquid pump: outlet P (or dP, or ratio), efficiency, driver efficiency
Compr    isentropic compressor/turbine: outlet P (or ratio), isentropic + mechanical efficiency
Valve    adiabatic valve: outlet P or pressure drop
"""

from __future__ import annotations

import numpy as np

from ..components import R
from ..props.flash import ps_flash
from .base import Block, BlockError, flash_outlet, register

G = 9.80665


@register
class Mixer(Block):
    type_name = "Mixer"
    category = "Mixers/Splitters"
    icon = "mixer"
    inlet_ports = ("in",)
    outlet_ports = ("out",)

    def run(self):
        flows, H, P_min, T = self.feed_totals()
        P = self.outlet_pressure(P_min) if ("P" in self.spec or "dP" in self.spec) else P_min
        out = self.outlet("out")
        F = flows.sum()
        out.set_state(flows, P=P, H=H / F if F > 0 else 0.0, T0=T)
        self.set_result("T", "Outlet temperature", out.T, "temperature")
        self.set_result("P", "Outlet pressure", out.P, "pressure")
        self.set_result("vf", "Outlet vapor fraction", out.vf, "fraction")
        self.status = "ok"

    def duty_keys(self):
        return ()


@register
class FSplit(Block):
    """``fractions={"A": 0.3}`` (the last unnamed outlet takes the rest) or ``flows={"A": 10}``."""

    type_name = "FSplit"
    category = "Mixers/Splitters"
    icon = "splitter"
    inlet_ports = ("in",)
    outlet_ports = ()  # outlets are named by their stream names

    def run(self):
        from ..stream import Stream

        flows, H, P, T = self.feed_totals()
        src = self.all_inlets()
        F = flows.sum()
        if len(src) == 1:
            ref = src[0]
        else:  # mix first (adiabatically), then split
            ref = Stream("_mixed")
            ref.fs = self.fs
            ref.set_state(flows, P=P, H=H / F if F > 0 else 0.0, T0=T)
        fr = dict(self.spec.get("fractions") or {})
        fl = dict(self.spec.get("flows") or {})
        names = list(self.outlets)
        fracs = {}
        for name in names:
            if name in fr:
                fracs[name] = float(fr[name])
            elif name in fl:
                fracs[name] = self.fs.units.to_si(fl[name], "mole_flow") / F if F > 0 else 0.0
        rest = [n for n in names if n not in fracs]
        if len(rest) > 1:
            raise BlockError(f"{self.name}: specify fractions/flows for all outlets but one (unspecified: {rest})")
        if rest:
            fracs[rest[0]] = max(0.0, 1.0 - sum(fracs.values()))
        for name in names:
            self.outlets[name].copy_from(ref, fracs[name] * F / ref.F if ref.F > 0 else 0.0)
        for name in names:
            self.set_result(f"frac_{name}", f"Split fraction to {name}", fracs[name], "fraction")
        self.status = "ok"

    def duty_keys(self):
        return ()


@register
class Sep(Block):
    """Component separator. ``splits={"TOP": {"C2": 0.99, "C3": 0.02}}`` gives the fraction of each
    component's inlet flow sent to outlet TOP; the remaining outlet takes the rest. Outlets leave
    at the inlet T and P unless ``T``/``P`` are given (per outlet: ``conditions={"TOP": dict(T=.., P=..)}``)."""

    type_name = "Sep"
    category = "Separators"
    icon = "sep"
    inlet_ports = ("in",)
    outlet_ports = ()

    def run(self):
        flows, H_in, P_in, T_in = self.feed_totals()
        splits = self.spec.get("splits", {})
        default = self.spec.get("default", 0.0)
        names = list(self.outlets)
        given = [n for n in names if n in splits]
        rest = [n for n in names if n not in splits]
        if len(rest) != 1:
            raise BlockError(f"{self.name}: give splits for all outlets but one")
        remaining = flows.copy()
        out_flows = {}
        for n in given:
            f = np.full(len(flows), float(default))
            for cid, v in splits[n].items():
                f[self.comps.idx(cid)] = float(v)
            out_flows[n] = flows * f
            remaining = remaining - out_flows[n]
        out_flows[rest[0]] = np.maximum(remaining, 0.0)
        cond = self.spec.get("conditions", {})
        H_out = 0.0
        for n in names:
            c = cond.get(n, {})
            T = self.fs.units.to_si(c["T"], "temperature") if "T" in c else T_in
            P = self.fs.units.to_si(c["P"], "pressure") if "P" in c else P_in
            self.outlets[n].set_state(out_flows[n], T=T, P=P)
            H_out += self.outlets[n].H
        self.set_result("duty", "Heat duty", H_out - H_in, "duty")
        self.status = "ok"


@register
class Dupl(Block):
    type_name = "Dupl"
    category = "Manipulators"
    icon = "dupl"
    inlet_ports = ("in",)
    outlet_ports = ()

    def run(self):
        src = self.all_inlets()[0]
        for out in self.outlets.values():
            out.copy_from(src, 1.0)
        self.status = "ok"

    def duty_keys(self):
        return ()

    def mass_balance_error(self) -> float:
        return 0.0

    def energy_balance_error(self) -> float:
        return 0.0


@register
class Heater(Block):
    """Heater/cooler. Specs: ``P`` (Aspen convention: <= 0 means pressure drop) or ``dP``, plus one of
    ``T``, ``Q`` (duty), ``vf`` (vapor fraction), ``dT`` (temperature change)."""

    type_name = "Heater"
    category = "Exchangers"
    icon = "heater"
    inlet_ports = ("in",)
    outlet_ports = ("out",)

    def _second_spec(self, T_in):
        return dict(T=self.si("T", "temperature"), Q=self.si("Q", "duty"), vf=self.spec.get("vf"),
                    dT=self.si("dT", "delta_temperature"), T_in=T_in)

    def run(self):
        flows, H_in, P_in, T_in = self.feed_totals()
        P = self.outlet_pressure(P_in)
        out = self.outlet("out")
        Q = flash_outlet(self, out, flows, P, H_in, **self._second_spec(T_in))
        self.set_result("T", "Outlet temperature", out.T, "temperature")
        self.set_result("P", "Outlet pressure", out.P, "pressure")
        self.set_result("vf", "Vapor fraction", out.vf, "fraction")
        self.set_result("duty", "Heat duty", Q, "duty")
        self.status = "ok"


@register
class Flash2(Heater):
    """Two-outlet flash drum (ports ``vapor`` and ``liquid``). Specs as :class:`Heater`, or
    ``liquid_spec=(comp, mass_frac)``: the block finds the temperature that puts that mass fraction of ``comp`` in the liquid."""

    type_name = "Flash2"
    category = "Separators"
    icon = "flash"
    inlet_ports = ("in",)
    outlet_ports = ("vapor", "liquid")

    def _T_for_liquid_spec(self, flows, P):
        """Temperature at which the liquid holds the requested mass fraction of one component
        (``liquid_spec=(comp, mass_frac)``) - a design spec on the flash temperature, solved in the block."""
        from scipy.optimize import brentq

        from ..props.flash import bubble_T, dew_T, tp_flash

        comp, target = self.spec["liquid_spec"]
        i = self.comps.idx(comp)
        MW = self.comps.MW
        z = flows / flows.sum()
        Tb, Td = bubble_T(self.pm, P, z), dew_T(self.pm, P, z)

        def w_liq(T):
            r = tp_flash(self.pm, T, P, z)
            x = r.x if r.phase != "V" else r.y
            return x[i] * MW[i] / float(x @ MW)

        lo, hi = Tb + 1e-6 * Tb, Td - 1e-6 * Td
        w_lo, w_hi = w_liq(lo), w_liq(hi)
        self._spec_met = True
        if not (min(w_lo, w_hi) < target < max(w_lo, w_hi)):
            if self.spec.get("liquid_spec_mode", "strict") == "clamp":
                # inside a recycle loop the spec may become reachable once the loop builds up: go to the nearer end
                self._spec_met = False
                self.messages.append(f"liquid_spec {comp} = {target:g} not reachable this pass "
                                     f"(range {min(w_lo, w_hi):.4g}-{max(w_lo, w_hi):.4g})")
                return lo if abs(w_lo - target) < abs(w_hi - target) else hi
            raise BlockError(f"{self.name}: liquid_spec {comp} mass fraction {target:g} is outside what a flash at "
                             f"{P / 1e5:.4g} bar can give ({min(w_lo, w_hi):.4g} at the bubble point to {max(w_lo, w_hi):.4g} near the dew point)")
        return brentq(lambda T: w_liq(T) - target, lo, hi, xtol=1e-9)

    def run(self):
        self.messages = [m for m in self.messages if not m.startswith("liquid_spec")]
        flows, H_in, P_in, T_in = self.feed_totals()
        P = self.outlet_pressure(P_in)
        vap, liq = self.outlet("vapor"), self.outlet("liquid")
        # flash the mixture into the vapor stream object, then split phases
        second = self._second_spec(T_in)
        if self.spec.get("liquid_spec") is not None:
            second.update(T=self._T_for_liquid_spec(flows, P), Q=None, vf=None, dT=None)
        Q = flash_outlet(self, vap, flows, P, H_in, **second)
        st = vap.state
        F = flows.sum()
        if st.phase == "VL":
            fv = F * st.beta * st.y
            fl = F * (1 - st.beta) * st.x
        elif st.phase == "V":
            fv, fl = flows.copy(), flows * 0.0
        else:
            fv, fl = flows * 0.0, flows.copy()
        # products leave at equilibrium: saturated vapor and saturated liquid
        vap.set_phase(fv, st.T, P, "V", other=st.x)
        liq.set_phase(fl, st.T, P, "L", other=st.y)
        self.set_result("T", "Temperature", st.T, "temperature")
        self.set_result("P", "Pressure", P, "pressure")
        self.set_result("vf", "Vapor fraction (molar)", st.beta, "fraction")
        self.set_result("duty", "Heat duty", Q, "duty")
        if self.spec.get("liquid_spec") is not None:
            self.set_result("spec_met", "Liquid spec met (1 = yes)", 1.0 if self._spec_met else 0.0, "fraction")
        self.status = "ok"


@register
class Valve(Block):
    """Adiabatic valve: ``P`` (outlet, or <= 0 for a drop) or ``dP``."""

    type_name = "Valve"
    category = "Pressure Changers"
    icon = "valve"

    def run(self):
        flows, H_in, P_in, T_in = self.feed_totals()
        P = self.outlet_pressure(P_in)
        out = self.outlet("out")
        F = flows.sum()
        out.set_state(flows, P=P, H=H_in / F, T0=T_in)
        self.set_result("T", "Outlet temperature", out.T, "temperature")
        self.set_result("P", "Outlet pressure", out.P, "pressure")
        self.set_result("dP", "Pressure drop", P_in - out.P, "delta_pressure")
        self.set_result("vf", "Outlet vapor fraction", out.vf, "fraction")
        self.status = "ok"

    def duty_keys(self):
        return ()


@register
class Pump(Block):
    """Pump: ``P`` outlet pressure (or ``dP`` increase, or ``ratio``), ``eff`` pump efficiency
    (default 1.0 - Aspen would use efficiency curves), ``driver_eff`` (default 1.0)."""

    type_name = "Pump"
    category = "Pressure Changers"
    icon = "pump"

    def run(self):
        flows, H_in, P_in, T_in = self.feed_totals()
        src = self.all_inlets()[0]
        if "ratio" in self.spec:
            P = P_in * float(self.spec["ratio"])
        elif "dP" in self.spec:
            P = P_in + self.si("dP", "delta_pressure")
        else:
            P = self.si("P", "pressure")
        eff = float(self.spec.get("eff", 1.0))
        deff = float(self.spec.get("driver_eff", 1.0))
        F = flows.sum()
        x = src.x_liq() if src.state.phase != "V" else src.z
        v = float(self.pm.v_liq(T_in, P_in, x))  # m3/mol
        fluid_power = F * v * (P - P_in)
        brake = fluid_power / eff
        out = self.outlet("out")
        out.set_state(flows, P=P, H=(H_in + brake) / F, T0=T_in)
        rho = src.comps.MW @ x / 1000.0 / v
        self.set_result("P", "Outlet pressure", P, "pressure")
        self.set_result("dP", "Pressure change", P - P_in, "delta_pressure")
        self.set_result("fluid_power", "Fluid power", fluid_power, "power")
        self.set_result("brake_power", "Brake power", brake, "power")
        self.set_result("electricity", "Electricity", brake / deff, "power")
        self.set_result("vol_flow", "Volumetric flow", F * v, "volume_flow")
        self.set_result("head", "Head developed", (P - P_in) / (rho * G), "head")
        self.set_result("T", "Outlet temperature", out.T, "temperature")
        self.set_result("eff", "Pump efficiency", eff, "fraction")
        self.status = "ok"

    def duty_keys(self):
        return ()

    def work_keys(self):
        return ("brake_power",)


@register
class Compr(Block):
    """Isentropic compressor or turbine: ``P`` (or ``ratio``/``dP``), ``eff`` isentropic efficiency
    (default 0.72, Aspen's default), ``mech_eff`` (default 1.0). A lower outlet pressure makes it a
    turbine (work is then negative = produced)."""

    type_name = "Compr"
    category = "Pressure Changers"
    icon = "compressor"

    def run(self):
        flows, H_in, P_in, T_in = self.feed_totals()
        src = self.all_inlets()[0]
        if "ratio" in self.spec:
            P = P_in * float(self.spec["ratio"])
        elif "dP" in self.spec:
            P = P_in + self.si("dP", "delta_pressure")
        else:
            P = self.si("P", "pressure")
        eff = float(self.spec.get("eff", 0.72))
        meff = float(self.spec.get("mech_eff", 1.0))
        F = flows.sum()
        z = flows / F
        s_in = src.s
        h_in = H_in / F
        ideal = ps_flash(self.pm, P, s_in, z, T0=T_in)
        h_s = ideal.h(self.pm)
        if P >= P_in:
            h_out = h_in + (h_s - h_in) / eff
        else:
            h_out = h_in - eff * (h_in - h_s)
        out = self.outlet("out")
        out.set_state(flows, P=P, H=h_out, T0=ideal.T)
        W_ind = F * (h_out - h_in)
        self.set_result("P", "Outlet pressure", P, "pressure")
        self.set_result("ratio", "Pressure ratio", P / P_in, "fraction")
        self.set_result("T", "Outlet temperature", out.T, "temperature")
        self.set_result("T_isentropic", "Isentropic outlet temperature", ideal.T, "temperature")
        self.set_result("W_isentropic", "Isentropic power", F * (h_s - h_in), "power")
        self.set_result("indicated_power", "Indicated power", W_ind, "power")
        self.set_result("brake_power", "Brake power", W_ind / meff if W_ind > 0 else W_ind * meff, "power")
        self.set_result("vf", "Outlet vapor fraction", out.vf, "fraction")
        self.set_result("eff", "Isentropic efficiency", eff, "fraction")
        self.status = "ok"

    def duty_keys(self):
        return ()

    def work_keys(self):
        return ("indicated_power",)

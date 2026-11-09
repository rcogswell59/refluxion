"""Material streams.

A stream holds component molar flows plus a thermodynamic state from a flash - Aspen's
"C + 2" variables (T, P and C component flows) plus everything derived from them.

Attributes such as ``T``, ``P``, ``F``, ``flows`` and ``H`` are SI. For numbers in your chosen
unit set use :meth:`Stream.get`, e.g. ``s.get("T")`` or ``s.get("mole_flow", "C2")``.
"""

from __future__ import annotations

import numpy as np

from .props.flash import FlashResult, ph_flash, pvf_flash, tp_flash, tvf_flash


class Stream:
    def __init__(self, name: str):
        self.name = name
        self.fs = None
        self.flows: np.ndarray | None = None   # mol/s
        self.state: FlashResult | None = None
        self.source = None      # (block, port) or None for a feed
        self.dest = None        # (block, port) or None for a product
        self.spec: dict | None = None  # feed specification (user units)
        self.estimate: dict | None = None  # tear-stream estimate
        self._h = None

    # --- setup -----------------------------------------------------------------------------
    @property
    def pm(self):
        return self.fs.pm

    @property
    def comps(self):
        return self.fs.comps

    @property
    def is_feed(self) -> bool:
        return self.source is None

    @property
    def is_product(self) -> bool:
        return self.dest is None

    @property
    def solved(self) -> bool:
        return self.state is not None

    def set_state(self, flows, T=None, P=None, vf=None, H=None, T0=None):
        """Set flows (mol/s) and flash with two of T (K), P (Pa), vf, H (J/mol)."""
        flows = np.maximum(np.asarray(flows, dtype=float), 0.0)
        self.flows = flows
        Ftot = flows.sum()
        self._h = None
        if Ftot <= 0:
            z = np.full(len(flows), 1.0 / len(flows))
            T = T if T is not None else (T0 or 298.15)
            P = P if P is not None else 101325.0
            self.state = FlashResult(float(T), float(P), 0.0, z, z, z, "L", 0, None)
            self._h = 0.0
            return self
        z = flows / Ftot
        pm = self.fs.pm
        if T is not None and P is not None:
            K0 = self.state.K if (self.state is not None and self.state.K is not None) else None
            self.state = tp_flash(pm, T, P, z, K0=K0)
        elif P is not None and H is not None:
            T0 = T0 if T0 is not None else (self.state.T if self.state is not None else None)
            self.state = ph_flash(pm, P, H, z, T0=T0)
        elif P is not None and vf is not None:
            self.state = pvf_flash(pm, P, vf, z)
        elif T is not None and vf is not None:
            self.state = tvf_flash(pm, T, vf, z)
        else:
            raise ValueError(f"stream {self.name}: need two of T, P, vf, H")
        return self

    def set_phase(self, flows, T, P, phase: str, other=None):
        """Set a single-phase product leaving an equilibrium stage without re-flashing.

        ``phase`` is "V" or "L"; ``other`` is the composition of the phase it was in
        equilibrium with (kept as the incipient-phase composition)."""
        flows = np.maximum(np.asarray(flows, dtype=float), 0.0)
        self.flows = flows
        F = flows.sum()
        z = flows / F if F > 0 else np.full(len(flows), 1.0 / len(flows))
        other = z if other is None else np.asarray(other, dtype=float)
        if phase == "V":
            self.state = FlashResult(float(T), float(P), 1.0, other, z, z, "V", 0, None)
            self._h = float(self.fs.pm.h_vap(T, P, z)) if F > 0 else 0.0
        else:
            self.state = FlashResult(float(T), float(P), 0.0, z, other, z, "L", 0, None)
            self._h = float(self.fs.pm.h_liq(T, P, z)) if F > 0 else 0.0
        return self

    def copy_from(self, other: "Stream", scale: float = 1.0):
        """Same state, flows scaled (used by splitters)."""
        self.flows = other.flows * scale
        self.state = other.state
        self._h = other._h
        return self

    # --- SI properties -----------------------------------------------------------------------
    @property
    def T(self) -> float:
        return self.state.T

    @property
    def P(self) -> float:
        return self.state.P

    @property
    def F(self) -> float:
        return float(self.flows.sum())

    @property
    def z(self) -> np.ndarray:
        F = self.F
        return self.flows / F if F > 0 else self.flows * 0

    @property
    def vf(self) -> float:
        return self.state.beta

    @property
    def phase(self) -> str:
        return {"V": "Vapor", "L": "Liquid", "VL": "Mixed"}[self.state.phase]

    @property
    def h(self) -> float:
        """Molar enthalpy, J/mol."""
        if self._h is None:
            self._h = self.state.h(self.pm) if self.F > 0 else 0.0
        return self._h

    @property
    def H(self) -> float:
        """Enthalpy flow, W."""
        return self.h * self.F

    @property
    def s(self) -> float:
        return self.state.s(self.pm) if self.F > 0 else 0.0

    @property
    def MW(self) -> float:
        return float(np.sum(self.z * self.comps.MW))

    @property
    def mass_flows(self) -> np.ndarray:
        """kg/s per component."""
        return self.flows * self.comps.MW / 1000.0

    @property
    def mass_flow(self) -> float:
        return float(self.mass_flows.sum())

    @property
    def volume_flow(self) -> float:
        """Actual volumetric flow, m3/s."""
        st, pm = self.state, self.pm
        v = 0.0
        if st.beta > 0:
            v += st.beta * float(pm.v_vap(st.T, st.P, st.y if st.phase == "VL" else self.z))
        if st.beta < 1:
            v += (1 - st.beta) * float(pm.v_liq(st.T, st.P, st.x if st.phase == "VL" else self.z))
        return v * self.F

    @property
    def density(self) -> float:
        vf = self.volume_flow
        return self.mass_flow / vf if vf > 0 else float("nan")

    def x_liq(self) -> np.ndarray:
        return self.state.x if self.state.phase == "VL" else self.z

    def y_vap(self) -> np.ndarray:
        return self.state.y if self.state.phase == "VL" else self.z

    # --- user-facing accessors (flowsheet unit set) ------------------------------------------
    def _idx(self, comp):
        return self.comps.idx(comp)

    def get(self, what: str, comp: str | None = None, units: str | None = None):
        """Read a stream variable in the flowsheet's units (or ``units``).

        ``what``: T, P, VF, mole_flow, mass_flow, mole_frac, mass_frac, H (enthalpy flow),
        h (molar enthalpy), volume_flow, density, MW. ``comp`` selects a component.
        """
        u = self.fs.units
        key = what.lower().replace("-", "_")
        table = {
            "t": (self.T, "temperature"), "temperature": (self.T, "temperature"),
            "p": (self.P, "pressure"), "pressure": (self.P, "pressure"),
            "vf": (self.vf, "fraction"), "vapor_fraction": (self.vf, "fraction"),
            "h": (self.h, "molar_enthalpy"), "enthalpy_flow": (self.H, "duty"),
            "volume_flow": (self.volume_flow, "volume_flow"), "density": (self.density, "mass_density"),
            "mw": (self.MW, "fraction"),
        }
        if key in ("mole_flow", "flow", "molar_flow"):
            val = self.flows[self._idx(comp)] if comp else self.F
            q = "mole_flow"
        elif key == "mass_flow":
            val = self.mass_flows[self._idx(comp)] if comp else self.mass_flow
            q = "mass_flow"
        elif key in ("mole_frac", "molefrac", "x"):
            val = self.z[self._idx(comp)]
            q = "fraction"
        elif key in ("mass_frac", "massfrac"):
            m = self.mass_flows
            val = m[self._idx(comp)] / m.sum() if m.sum() > 0 else 0.0
            q = "fraction"
        elif key == "enthalpy" or what == "H":
            val, q = self.H, "duty"
        elif key in table:
            val, q = table[key]
        else:
            raise KeyError(f"unknown stream variable {what!r}")
        if units:
            from .units import from_si
            return from_si(float(val), q, units)
        return float(u.from_si(float(val), q)) if q != "fraction" else float(val)

    def flow(self, comp: str | None = None, units: str | None = None) -> float:
        return self.get("mole_flow", comp, units)

    def frac(self, comp: str) -> float:
        return self.get("mole_frac", comp)

    def summary(self) -> dict:
        """Everything a stream table shows, SI."""
        st = self.state
        return dict(name=self.name, T=self.T, P=self.P, vf=self.vf, phase=self.phase, F=self.F,
                    mass_flow=self.mass_flow, flows=self.flows.copy(), z=self.z.copy(), h=self.h,
                    H=self.H, MW=self.MW, volume_flow=self.volume_flow if self.F > 0 else 0.0,
                    density=self.density if self.F > 0 else float("nan"),
                    source=self.source[0].name if self.source else None,
                    dest=self.dest[0].name if self.dest else None)

    def __repr__(self) -> str:
        if not self.solved:
            return f"<Stream {self.name} (not solved)>"
        u = self.fs.units
        return (f"<Stream {self.name}: {self.get('T'):.4g} {u.unit('temperature')}, {self.get('P'):.4g} "
                f"{u.unit('pressure')}, {self.get('mole_flow'):.5g} {u.unit('mole_flow')}, VF={self.vf:.4f}>")


def mix_streams(streams, pm):
    """Adiabatic mixing helper: total flows (mol/s), total enthalpy (W), min P, max T."""
    flows = sum(s.flows for s in streams)
    H = sum(s.H for s in streams)
    P = min(s.P for s in streams)
    T = max(s.T for s in streams)
    return flows, H, P, T

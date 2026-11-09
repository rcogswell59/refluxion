"""Block base class and the model palette (registry).

Every unit operation is a :class:`Block`. Blocks declare their ports, keep their input specs in
``self.spec`` (in the flowsheet's unit set, exactly as you typed them), convert them to SI when
they run, and publish results as (label, SI value, quantity) rows so reports can show them in any
unit set. Registering a block class adds it to :data:`PALETTE`, just like Aspen's model palette.
"""

from __future__ import annotations

import numpy as np

PALETTE: dict[str, type] = {}


def register(cls):
    PALETTE[cls.type_name] = cls
    return cls


class BlockError(RuntimeError):
    pass


class Block:
    type_name = "Block"
    category = "Other"
    icon = "box"                 # used by the flowsheet drawing
    inlet_ports: tuple = ("in",)
    outlet_ports: tuple = ("out",)
    multi_inlet = True           # can one port take several streams (Aspen allows it for most)

    def __init__(self, name: str, **spec):
        self.name = name
        self.spec: dict = dict(spec)
        self.fs = None
        self.inlets: dict[str, list] = {p: [] for p in self.inlet_ports}
        self.outlets: dict[str, object] = {}
        self.results: dict[str, tuple] = {}   # key -> (label, SI value, quantity)
        self.tables: dict[str, dict] = {}     # extra result tables (profiles etc.)
        self.messages: list[str] = []
        self.status = "not run"

    # --- helpers for subclasses --------------------------------------------------------------
    @property
    def pm(self):
        return self.fs.pm

    @property
    def comps(self):
        return self.fs.comps

    def si(self, key, quantity, default=None):
        """A spec value converted to SI (None if absent)."""
        v = self.spec.get(key, default)
        if v is None:
            return None
        return self.fs.units.to_si(v, quantity)

    def all_inlets(self):
        out = []
        for p in self.inlet_ports:
            out.extend(self.inlets.get(p, []))
        return out

    def feed_totals(self, port=None):
        """Mixed inlet: (flows mol/s, enthalpy W, min pressure Pa, max T)."""
        streams = self.inlets[port] if port else self.all_inlets()
        streams = [s for s in streams if s.solved]
        if not streams:
            raise BlockError(f"{self.name}: no solved inlet streams")
        flows = sum(s.flows for s in streams)
        H = sum(s.H for s in streams)
        live = [s for s in streams if s.F > 0] or streams   # empty recycle guesses don't set P or T
        P = min(s.P for s in live)
        T = max(s.T for s in live)
        return flows, H, P, T

    def outlet(self, port):
        s = self.outlets.get(port)
        if s is None:
            raise BlockError(f"{self.name}: outlet port {port!r} is not connected")
        return s

    def outlet_pressure(self, P_in):
        """Aspen convention: ``P`` > 0 is the outlet pressure, ``P`` <= 0 is a pressure drop.
        ``dP`` (a positive drop) is also accepted."""
        if "dP" in self.spec and self.spec["dP"] is not None:
            return P_in - self.si("dP", "delta_pressure")
        P = self.spec.get("P")
        if P is None:
            return P_in
        if isinstance(P, tuple):
            val, unit = P
            if val <= 0:
                return P_in - self.fs.units.to_si((-val, unit), "delta_pressure") if unit not in ("psig", "barg") else P_in
            return self.fs.units.to_si(P, "pressure")
        if P <= 0:
            return P_in - self.fs.units.to_si(-P, "delta_pressure")
        return self.fs.units.to_si(P, "pressure")

    def set_result(self, key, label, value, quantity):
        self.results[key] = (label, value, quantity)

    def result(self, key, units: str | None = None):
        """A result in the flowsheet's unit set (or ``units``)."""
        label, value, q = self.results[key]
        if value is None:
            return None
        if units:
            from ..units import from_si
            return from_si(value, q, units)
        return self.fs.units.from_si(value, q) if q != "fraction" and q else value

    def energy_balance_error(self) -> float:
        """Relative enthalpy imbalance (in - out + duties) / scale, for sanity checks."""
        H_in = sum(s.H for s in self.all_inlets() if s.solved)
        H_out = sum(s.H for s in self.outlets.values() if s is not None and s.solved)
        Q = sum(v[1] for k, v in self.results.items() if v[2] == "duty" and k in self.duty_keys() and v[1] is not None)
        W = sum(v[1] for k, v in self.results.items() if v[2] == "power" and k in self.work_keys() and v[1] is not None)
        scale = max(abs(H_in), abs(H_out), 1.0)
        return (H_in + Q + W - H_out) / scale

    def mass_balance_error(self) -> float:
        fin = sum(s.mass_flow for s in self.all_inlets() if s.solved)
        fout = sum(s.mass_flow for s in self.outlets.values() if s is not None and s.solved)
        return (fin - fout) / max(fin, 1e-30)

    def duty_keys(self):
        return ("duty",)

    def work_keys(self):
        return ()

    def run(self):  # pragma: no cover - abstract
        raise NotImplementedError

    def describe_spec(self) -> str:
        items = []
        for k, v in self.spec.items():
            if v is None:
                continue
            items.append(f"{k}={v!r}" if isinstance(v, (tuple, str)) else f"{k}={v}")
        return ", ".join(items)

    def __repr__(self) -> str:
        return f"<{self.type_name} {self.name} ({self.status})>"


def flash_outlet(block, stream, flows, P, H_in_total, *, T=None, Q=None, vf=None, dT=None, T_in=None):
    """Aspen-style 'two specs' outlet flash used by Heater/Flash/Sep etc.

    Pressure is always given; the second spec is one of T, Q (duty, W), vf, dT.
    Returns the duty (W).
    """
    F = float(np.sum(flows))
    if dT is not None:
        T = T_in + dT
    if T is not None:
        stream.set_state(flows, T=T, P=P)
        return stream.H - H_in_total
    if vf is not None:
        stream.set_state(flows, P=P, vf=vf)
        return stream.H - H_in_total
    if Q is not None:
        h = (H_in_total + Q) / F if F > 0 else 0.0
        stream.set_state(flows, P=P, H=h, T0=T_in)
        return Q
    raise BlockError(f"{block.name}: needs a second spec (T, Q, vf or dT) besides pressure")

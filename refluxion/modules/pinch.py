"""Pinch analysis (heat integration) - the problem-table algorithm.

This is the "single table" (problem table) method: shift hot streams down and cold streams up by
dTmin/2, cut the temperature range at every shifted supply/target temperature, compute the heat
surplus or deficit of each interval, cascade it from the top, and read off

* QH,min  - the largest heat deficit in the cascade (external heating you cannot avoid)
* QC,min  - what is left at the bottom of the feasible cascade (external cooling)
* the pinch - where the feasible cascade is exactly zero (no heat flows across it)

The same table gives the hot/cold composite curves and the grand composite curve, so no
graphical construction is needed.

Units: temperatures in C, heat-capacity flow rates in kW/K and duties in kW by default (the
units used in class); pass ``units=("F", "Btu/hr-F", "Btu/hr")`` or similar to change them.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..units import from_si, to_si


@dataclass
class PinchStream:
    name: str
    T_supply: float
    T_target: float
    CP: float = 0.0           # heat-capacity flow rate (m*cp); ignored when segments are given
    segments: list | None = None   # [(T_from, T_to, duty)] piecewise-linear heat curve (phase changes, varying cp)

    @property
    def hot(self) -> bool:
        return self.T_supply > self.T_target

    @property
    def duty(self) -> float:
        if self.segments:
            return float(sum(abs(q) for _, _, q in self.segments))
        return abs(self.T_supply - self.T_target) * self.CP

    @classmethod
    def from_duty(cls, name: str, T_in: float, T_out: float, Q: float, min_dT: float = 1.0) -> "PinchStream":
        """A stream from its terminal temperatures and duty (Q < 0: heat released, a hot stream). A (nearly)
        isothermal duty such as a condenser becomes a ``min_dT``-wide segment centred on its temperature."""
        hot = Q < 0
        if abs(T_in - T_out) < min_dT:
            Tm = 0.5 * (T_in + T_out)
            T_in, T_out = (Tm + min_dT / 2, Tm - min_dT / 2) if hot else (Tm - min_dT / 2, Tm + min_dT / 2)
        return cls(name, T_in, T_out, abs(Q) / abs(T_in - T_out), segments=[(T_in, T_out, abs(Q))])

    def pieces(self) -> list:
        """Constant-CP pieces (one per segment)."""
        if not self.segments:
            return [self]
        out = []
        for k, (a, b, q) in enumerate(self.segments):
            if abs(a - b) > 1e-12 and q > 0:
                out.append(PinchStream(f"{self.name}#{k + 1}", a, b, abs(q) / abs(a - b)))
        return out


@dataclass
class PinchResult:
    dTmin: float
    streams: list
    boundaries: np.ndarray        # shifted (interval) temperatures, high -> low
    hot_actual: np.ndarray        # boundary + dTmin/2
    cold_actual: np.ndarray       # boundary - dTmin/2
    available: np.ndarray         # heat released by hot streams in each interval (A)
    required: np.ndarray          # heat absorbed by cold streams in each interval (B)
    net: np.ndarray               # A - B (surplus > 0, deficit < 0)
    cascade_zero: np.ndarray      # cascade starting from 0 at the top
    cascade: np.ndarray           # feasible cascade starting from QH,min
    QHmin: float
    QCmin: float
    pinch_shifted: float | None
    pinch_hot: float | None
    pinch_cold: float | None
    units: tuple = ("C", "kW/K", "kW")
    hot_composite: dict = field(default_factory=dict)
    cold_composite: dict = field(default_factory=dict)
    gcc: dict = field(default_factory=dict)

    def table_rows(self) -> list[dict]:
        rows = []
        n = len(self.boundaries)
        for i in range(n):
            row = dict(boundary=self.boundaries[i], T_hot=self.hot_actual[i], T_cold=self.cold_actual[i],
                       cascade_zero=self.cascade_zero[i], cascade=self.cascade[i])
            if i < n - 1:
                row.update(available=self.available[i], required=self.required[i], net=self.net[i])
            rows.append(row)
        return rows

    def summary(self) -> str:
        Tu, _, Qu = self.units
        s = f"QH,min = {self.QHmin:.6g} {Qu}; QC,min = {self.QCmin:.6g} {Qu}"
        if self.pinch_shifted is not None:
            s += (f"; pinch at {self.pinch_shifted:.6g} {Tu} shifted "
                  f"(hot {self.pinch_hot:.6g} {Tu} / cold {self.pinch_cold:.6g} {Tu})")
        else:
            s += "; threshold problem (no pinch)"
        return s

    def __repr__(self) -> str:
        return f"<PinchResult {self.summary()}>"


def pinch_analysis(streams, dTmin: float, units=("C", "kW/K", "kW")) -> PinchResult:
    """``streams``: list of :class:`PinchStream` (or tuples ``(name, T_supply, T_target, CP)``)."""
    streams = [s if isinstance(s, PinchStream) else PinchStream(*s) for s in streams]
    pieces = [p for s in streams for p in s.pieces()]
    half = dTmin / 2.0
    shifted = []
    for s in pieces:
        d = -half if s.hot else half
        shifted.append((s, s.T_supply + d, s.T_target + d))
    bounds = sorted({round(t, 10) for _, a, b in shifted for t in (a, b)}, reverse=True)
    bounds = np.array(bounds, dtype=float)
    n = len(bounds)
    A = np.zeros(n - 1)
    B = np.zeros(n - 1)
    for i in range(n - 1):
        hi, lo = bounds[i], bounds[i + 1]
        dT = hi - lo
        for s, a, b in shifted:
            top, bot = max(a, b), min(a, b)
            if top >= hi - 1e-9 and bot <= lo + 1e-9:
                if s.hot:
                    A[i] += s.CP * dT
                else:
                    B[i] += s.CP * dT
    net = A - B
    casc0 = np.concatenate([[0.0], np.cumsum(net)])
    QH = max(0.0, -casc0.min())
    casc = casc0 + QH
    QC = casc[-1]
    zero = np.where(np.abs(casc) < 1e-9 * max(1.0, QH + QC))[0]
    zero = [i for i in zero if 0 < i < n - 1] or ([i for i in zero] if QH > 0 and QC > 0 else [])
    if zero and QH > 1e-12 and QC > 1e-12:
        k = zero[0]
        pinch = float(bounds[k])
        p_hot, p_cold = pinch + half, pinch - half
    else:
        pinch = p_hot = p_cold = None
    # composite curves (actual temperatures)
    hot = [s for s in pieces if s.hot]
    cold = [s for s in pieces if not s.hot]
    hc = _composite(hot)
    cc = _composite(cold)
    cc["H"] = cc["H"] + QC                       # shift cold curve right by QC,min
    gcc = dict(T=bounds.copy(), H=casc.copy())
    return PinchResult(dTmin, streams, bounds, bounds + half, bounds - half, A, B, net, casc0, casc, QH, QC,
                       pinch, p_hot, p_cold, tuple(units), hc, cc, gcc)


def _composite(streams):
    if not streams:
        return dict(T=np.array([]), H=np.array([]))
    temps = sorted({t for s in streams for t in (s.T_supply, s.T_target)})
    H = [0.0]
    for lo, hi in zip(temps[:-1], temps[1:]):
        cp = sum(s.CP for s in streams if min(s.T_supply, s.T_target) <= lo + 1e-9 and max(s.T_supply, s.T_target) >= hi - 1e-9)
        H.append(H[-1] + cp * (hi - lo))
    return dict(T=np.array(temps), H=np.array(H))


def streams_from_flowsheet(fs, blocks=None, units=("C", "kW/K", "kW")) -> list[PinchStream]:
    """Turn Heater/cooler blocks of a solved flowsheet into pinch streams (constant CP approximation:
    CP = |duty| / |T_out - T_in|). ``blocks`` defaults to every Heater/Flash2 with a nonzero duty."""
    out = []
    Tu, CPu, Qu = units
    for b in fs.blocks.values():
        if blocks is not None and b.name not in blocks:
            continue
        if b.type_name not in ("Heater", "Flash2") or "duty" not in b.results:
            continue
        Q = b.results["duty"][1]
        if not Q:
            continue
        T_in = max(s.T for s in b.all_inlets())
        T_out = next(iter(b.outlets.values())).T
        if abs(T_out - T_in) < 1e-6:
            continue
        CP_si = abs(Q) / abs(T_out - T_in)
        out.append(PinchStream(b.name, from_si(T_in, "temperature", Tu), from_si(T_out, "temperature", Tu),
                               from_si(CP_si, "cp_flow", CPu)))
    return out


def _segments_from_curve(name, T_C, Q_kW, n_max: int = 8):
    """Segments from a heat curve: temperatures (C) at cumulative duties (kW, from 0)."""
    idx = sorted(set([0, len(T_C) - 1] + [round(k * (len(T_C) - 1) / n_max) for k in range(n_max + 1)]))
    segs = [(T_C[a], T_C[b], abs(Q_kW[b] - Q_kW[a])) for a, b in zip(idx[:-1], idx[1:])]
    segs = [sg for sg in segs if sg[2] > 0]
    if not segs:
        return None
    total = sum(sg[2] for sg in segs)
    hot = T_C[0] > T_C[-1]
    if abs(T_C[0] - T_C[-1]) < 1.0:
        return PinchStream.from_duty(name, T_C[0], T_C[-1], -total if hot else total)
    # merge zero-width pieces into a 1 C band so every piece has a finite CP
    segs = [(a, b, q) if abs(a - b) >= 1e-6 else ((a + 0.5, a - 0.5, q) if hot else (a - 0.5, a + 0.5, q)) for a, b, q in segs]
    return PinchStream(name, T_C[0], T_C[-1], total / abs(T_C[0] - T_C[-1]), segments=segs)


def _heater_curve(b, n=8):
    from ..props.flash import ph_flash
    flows, H_in, P_in, T_in = b.feed_totals()
    Q = b.results["duty"][1]
    F = flows.sum()
    z = flows / F
    out = next(iter(b.outlets.values()))
    qs = np.linspace(0.0, Q, n + 1)
    T = []
    for q in qs:
        T.append(ph_flash(b.pm, P_in + (out.P - P_in) * (q / Q if Q else 0.0), (H_in + q) / F, z, T0=T[-1] if T else T_in).T - 273.15)
    return T, list(np.abs(qs) / 1e3)


def process_streams(fs, include=None) -> list:
    """Hot and cold process streams of a solved flowsheet (C, kW/K, kW): every Heater, Flash2 and UtilityHX duty,
    and each RadFrac condenser (stage 2 vapor -> stage 1 liquid) and reboiler (stage N-1 liquid -> stage N)."""
    out = []
    for b in fs.blocks.values():
        if include is not None and b.name not in include:
            continue
        if b.type_name == "RadFrac" and "profiles" in b.tables:
            p = b.tables["profiles"]
            T = [t - 273.15 for t in p["T"]]
            Qc = b.results.get("condenser_duty", (None, None))[1]
            Qr = b.results.get("reboiler_duty", (None, None))[1]
            if Qc:
                out.append(PinchStream.from_duty(f"{b.name} condenser", T[1], T[0], Qc / 1e3))
            if Qr:
                out.append(PinchStream.from_duty(f"{b.name} reboiler", T[-2], T[-1], Qr / 1e3))
        elif b.type_name == "UtilityHX" and "heat_curve" in b.tables:
            hc = b.tables["heat_curve"]
            s = _segments_from_curve(b.name, [t - 273.15 for t in hc["T_process"]], list(np.asarray(hc["duty"]) / 1e3))
            if s:
                out.append(s)
        elif b.type_name in ("Heater", "Flash2") and b.results.get("duty", (None, 0.0))[1]:
            T, Q = _heater_curve(b)
            s = _segments_from_curve(b.name, T, Q)
            if s:
                out.append(s)
    return out


def column_matches(fs, dTmin: float = 10.0) -> list:
    """Condenser -> reboiler pairs between different columns that are feasible with ``dTmin``:
    [(condenser stream, reboiler stream, duty kW that can be exchanged)]."""
    streams = process_streams(fs)
    conds = [s for s in streams if s.name.endswith(" condenser")]
    rebs = [s for s in streams if s.name.endswith(" reboiler")]
    out = []
    for c in conds:
        for r in rebs:
            if c.name.split(" ")[0] == r.name.split(" ")[0]:
                continue
            if min(c.T_supply, c.T_target) - max(r.T_supply, r.T_target) >= dTmin:
                out.append((c.name, r.name, min(c.duty, r.duty)))
    return out


def _profile(stream: PinchStream, hot: bool):
    """Temperature as a function of duty transferred (from the supply end), piecewise linear."""
    pieces = stream.pieces()
    pieces = sorted(pieces, key=lambda s: -s.T_supply) if hot else sorted(pieces, key=lambda s: s.T_supply)
    q, pts = 0.0, [(0.0, stream.T_supply)]
    for s in pieces:
        q += s.duty
        pts.append((q, s.T_target))
    return pts


def _interp(pts, q):
    for (q0, t0), (q1, t1) in zip(pts[:-1], pts[1:]):
        if q <= q1 + 1e-12:
            return t0 + (t1 - t0) * ((q - q0) / (q1 - q0) if q1 > q0 else 0.0)
    return pts[-1][1]


def match_duty(hot: PinchStream, cold: PinchStream, dTmin: float, tol: float = 1e-9) -> float:
    """Largest duty one counter-current exchanger can move from ``hot`` (cooled from its supply temperature) to
    ``cold`` (heated from its supply temperature) without the local approach falling below ``dTmin``."""
    ph, pc = _profile(hot, True), _profile(cold, False)
    qmax = min(hot.duty, cold.duty)

    def ok(Q):
        # positions: every breakpoint of either profile inside [0, Q]
        qs = {0.0, Q} | {q for q, _ in ph if q < Q} | {Q - q for q, _ in pc if q < Q}
        return all(_interp(ph, q) - _interp(pc, Q - q) >= dTmin - 1e-9 for q in qs if 0.0 <= q <= Q)

    if qmax <= 0 or not ok(0.0):
        return 0.0
    if ok(qmax):
        return qmax
    lo, hi = 0.0, qmax
    while hi - lo > tol * max(qmax, 1.0):
        mid = 0.5 * (lo + hi)
        lo, hi = (mid, hi) if ok(mid) else (lo, mid)
    return lo

"""Property analysis (Aspen's Analysis > Binary / Pure): Txy, Pxy and xy diagrams, K-values.

All functions take a flowsheet (for its components, property method and unit set) and return
plain dicts of arrays in the flowsheet's units, ready for :meth:`Report.chart`.
"""

from __future__ import annotations

import numpy as np

from .props.flash import FlashError, bubble_P, bubble_T, dew_P, dew_T


def _binary_z(fs, c1, c2, x1):
    z = np.zeros(len(fs.comps))
    z[fs.comps.idx(c1)] = x1
    z[fs.comps.idx(c2)] = 1.0 - x1
    return z


def txy(fs, c1: str, c2: str, P, points: int = 41) -> dict:
    """Bubble and dew temperatures vs mole fraction of ``c1`` at pressure ``P`` (flowsheet units)."""
    u = fs.units
    P_si = u.to_si(P, "pressure")
    xs = np.linspace(0.0, 1.0, points)
    Tb, Td, y_b = [], [], []
    Tprev_b = Tprev_d = None
    for x1 in xs:
        z = _binary_z(fs, c1, c2, np.clip(x1, 1e-9, 1 - 1e-9))
        try:
            T, inc, K = _bub(fs, P_si, z, Tprev_b)
            Tprev_b = T
            Tb.append(u.from_si(T, "temperature"))
            y_b.append(inc[fs.comps.idx(c1)])
        except FlashError:
            Tb.append(np.nan)
            y_b.append(np.nan)
        try:
            T = dew_T(fs.pm, P_si, z, T0=Tprev_d)
            Tprev_d = T
            Td.append(u.from_si(T, "temperature"))
        except FlashError:
            Td.append(np.nan)
    return dict(kind="Txy", c1=c1, c2=c2, P=P, x=xs, T_bubble=np.array(Tb), T_dew=np.array(Td), y_at_bubble=np.array(y_b),
                T_unit=u.unit("temperature"), P_unit=u.unit("pressure"))


def _bub(fs, P, z, T0):
    from .props.flash import _sat_T
    return _sat_T(fs.pm, P, z, "bubble", T0)


def pxy(fs, c1: str, c2: str, T, points: int = 41) -> dict:
    u = fs.units
    T_si = u.to_si(T, "temperature")
    xs = np.linspace(0.0, 1.0, points)
    Pb, Pd = [], []
    for x1 in xs:
        z = _binary_z(fs, c1, c2, np.clip(x1, 1e-9, 1 - 1e-9))
        try:
            Pb.append(u.from_si(bubble_P(fs.pm, T_si, z), "pressure"))
        except FlashError:
            Pb.append(np.nan)
        try:
            Pd.append(u.from_si(dew_P(fs.pm, T_si, z), "pressure"))
        except FlashError:
            Pd.append(np.nan)
    return dict(kind="Pxy", c1=c1, c2=c2, T=T, x=xs, P_bubble=np.array(Pb), P_dew=np.array(Pd),
                T_unit=u.unit("temperature"), P_unit=u.unit("pressure"))


def xy(fs, c1: str, c2: str, P, points: int = 41) -> dict:
    d = txy(fs, c1, c2, P, points)
    return dict(kind="xy", c1=c1, c2=c2, P=P, x=d["x"], y=d["y_at_bubble"], P_unit=d["P_unit"])


def azeotrope(fs, c1: str, c2: str, P, points: int = 41) -> dict | None:
    """Binary azeotrope of ``c1``/``c2`` at pressure ``P`` (flowsheet units), or None.

    An azeotrope is an interior extremum of the bubble-point curve (where y = x). Returns
    dict(x = mole fraction of c1, T in flowsheet units, kind = "minimum-boiling" | "maximum-boiling").
    """
    from scipy.optimize import minimize_scalar

    d = txy(fs, c1, c2, P, points)
    x, T = d["x"], d["T_bubble"]
    ok = np.isfinite(T)
    x, T = x[ok], T[ok]
    if len(T) < 3:
        return None
    k_min, k_max = int(np.argmin(T)), int(np.argmax(T))
    u = fs.units
    P_si = u.to_si(P, "pressure")
    for k, sign, kind in ((k_min, 1.0, "minimum-boiling"), (k_max, -1.0, "maximum-boiling")):
        if 0 < k < len(T) - 1:
            def f(xx, sign=sign):
                Tb, *_ = _bub(fs, P_si, _binary_z(fs, c1, c2, float(np.clip(xx, 1e-9, 1 - 1e-9))), None)
                return sign * Tb
            r = minimize_scalar(f, bounds=(x[k - 1], x[k + 1]), method="bounded", options={"xatol": 1e-6})
            return dict(x=float(r.x), T=u.from_si(sign * float(r.fun), "temperature"), kind=kind, P=P,
                        T_unit=u.unit("temperature"))
    return None


def k_values(fs, T, P, z=None) -> dict:
    """K-values of every component at T, P (flowsheet units) for composition z (default equimolar)."""
    from .props.flash import tp_flash

    u = fs.units
    n = len(fs.comps)
    z = np.full(n, 1.0 / n) if z is None else np.asarray(z, float)
    r = tp_flash(fs.pm, u.to_si(T, "temperature"), u.to_si(P, "pressure"), z)
    K = r.K if r.K is not None else np.ones(n)
    return dict(components=list(fs.comps.ids), K=np.asarray(K))

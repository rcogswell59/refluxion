"""Component databank.

Aspen retrieves pure-component parameters (TC, PC, OMEGA, CPIG, PLXANT, DHVLDP, ...) from its
SQL databanks when you pick a component. Refluxion does the same from the open ``chemicals`` /
``thermo`` libraries, then fits the temperature-dependent correlations to fast analytic forms:

* ideal-gas heat capacity:   Cp(T) = a0 + a1*t + ... + a6*t^6,  t = T/1000              [J/mol-K]
* vapor pressure:            ln P = A + B/T + C*ln T + D*T^2                             [Pa]
* heat of vaporization:      dHvap = A * (1 - Tr)^(B + C Tr + D Tr^2 + E Tr^3)  (DIPPR 106)   [J/mol]

Fitted coefficients are cached in ``~/.refluxion/components.json`` so later runs start instantly.
Enthalpies use Aspen's convention: the ideal gas at 298.15 K has enthalpy equal to its heat of
formation, so reactor duties automatically include heats of reaction.
"""

from __future__ import annotations

import json
import warnings
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

from .correlations import (CP_TO_J_PER_MOL_K, MOLAR_ENERGY_TO_J_PER_MOL, dippr107_cp, fit_cp_poly6, plxant,
                           plxant_to_pa, poly_cp)

R = 8.314462618  # J/mol-K
T_REF = 298.15
P_REF = 101325.0

CACHE_VERSION = 7
CACHE_PATH = Path.home() / ".refluxion" / "components.json"

# Aspen's formula-style aliases (the ANAME column in the Components form) -> CAS
ASPEN_ALIASES = {
    "H2": "1333-74-0", "N2": "7727-37-9", "O2": "7782-44-7", "AR": "7440-37-1", "CO": "630-08-0",
    "CO2": "124-38-9", "H2O": "7732-18-5", "H2S": "7783-06-4", "SO2": "7446-09-5", "NH3": "7664-41-7",
    "HCL": "7647-01-0", "CL2": "7782-50-5", "CH4": "74-82-8", "C2H6": "74-84-0", "C2H4": "74-85-1",
    "C3H8": "74-98-6", "C3H6-2": "115-07-1", "C4H10-1": "106-97-8", "C4H10-2": "75-28-5",
    "C5H12-1": "109-66-0", "C5H12-2": "78-78-4", "C6H14-1": "110-54-3", "C7H16-1": "142-82-5",
    "C8H18-1": "111-65-9", "C9H20-1": "111-84-2", "C10H22-1": "124-18-5", "C11H24": "1120-21-4",
    "C12H26": "112-40-3", "C6H6": "71-43-2", "C7H8": "108-88-3", "C8H10-4": "100-41-4",
    "C8H8": "100-42-5", "C9H12-2": "98-82-8", "C6H12-1": "110-82-7", "C2H6O-2": "64-17-5",
    "CH4O": "67-56-1", "C2H4O2-1": "64-19-7", "C4H8O2-3": "141-78-6", "C3H6O-1": "67-64-1",
    "CH2CL2": "75-09-2", "C2H4CL2-1": "107-06-2", "C2H3CL": "75-01-4", "C6H6O": "108-95-2",
    "C3H8O-1": "71-23-8", "C3H8O-2": "67-63-0",
}


@dataclass
class Component:
    """One pure component with everything the property methods need (SI units)."""

    id: str
    name: str
    cas: str
    formula: str
    MW: float
    Tc: float
    Pc: float
    omega: float
    Tb: float
    Vc: float
    Zc: float
    Hf: float          # ideal-gas heat of formation at 298.15 K, J/mol
    S0: float          # ideal-gas absolute entropy at 298.15 K, 1 bar, J/mol-K
    cp: list           # Cp_ig polynomial coefficients a0..a6 in t = T/1000
    cp_range: tuple
    psat: list         # PLXANT [A, B, C, D, E, F], P in Pa, T in K
    psat_range: tuple
    dhvap: list        # DIPPR 106 [A, B, C, D, E]
    sources: dict = field(default_factory=dict)
    Z_ra: float | None = None   # Rackett parameter fitted to liquid-density data (Aspen: RKTZRA)

    # --- analytic ideal-gas properties -------------------------------------------------
    def cp_ig(self, T):
        return np.polynomial.polynomial.polyval(np.asarray(T, dtype=float) / 1000.0, self.cp)

    def h_ig(self, T):
        """Ideal-gas molar enthalpy (J/mol), reference = heat of formation at 298.15 K."""
        return self.Hf + _int_cp(self.cp, T) - _int_cp(self.cp, T_REF)

    def s_ig(self, T, P=P_REF):
        """Ideal-gas pure-component entropy at T, P (J/mol-K)."""
        return self.S0 + _int_cp_over_T(self.cp, T) - _int_cp_over_T(self.cp, T_REF) - R * np.log(np.asarray(P) / 1e5)

    def Psat(self, T):
        """Vapor pressure (Pa), Aspen PLXANT form ln P = A + B/(T+C) + D ln T + E T^F."""
        return np.exp(np.clip(plxant(T, self.psat), -700.0, 700.0))

    def dHvap(self, T):
        A, B, C, D, E = self.dhvap
        Tr = np.minimum(np.asarray(T, dtype=float) / self.Tc, 1.0)
        return A * (1.0 - Tr) ** (B + Tr * (C + Tr * (D + Tr * E)))

    @property
    def Z_RA(self) -> float:
        """Rackett parameter: fitted to density data when available, else the Yamada-Gunn estimate."""
        return self.Z_ra if self.Z_ra else 0.29056 - 0.08775 * self.omega

    def __repr__(self) -> str:
        return f"Component({self.id!r}, {self.name!r}, CAS {self.cas})"


def _int_cp(a, T):
    """Integral of the Cp polynomial from 0 to T (J/mol). ``a`` is (n,) or (n, C)."""
    t = np.asarray(T, dtype=float) / 1000.0
    return 1000.0 * sum(a[k] * t ** (k + 1) / (k + 1) for k in range(len(a)))


def _int_cp_over_T(a, T):
    """Integral of Cp/T dT (up to a constant) (J/mol-K). ``a`` is (n,) or (n, C)."""
    T = np.asarray(T, dtype=float)
    t = T / 1000.0
    return a[0] * np.log(T) + sum(a[k] * t ** k / k for k in range(1, len(a)))


# ---------------------------------------------------------------------------------------
# retrieval + fitting


def _resolve_cas(name: str) -> str:
    from chemicals import CAS_from_any

    key = name.strip()
    if key.upper() in ASPEN_ALIASES:
        return ASPEN_ALIASES[key.upper()]
    return CAS_from_any(key)


def _pick_method(obj, priority: list[str]):
    """Use the first *data-based* correlation available in ``priority`` order; fall back to the
    library's own default (which is also ranked by reliability). Estimation methods (Joback,
    Riedel, Lee-Kesler, ...) are only used when no data-based correlation exists."""
    for m in priority:
        if m in obj.all_methods:
            lo, hi = obj.T_limits.get(m, (None, None))
            if lo is None or hi is None:
                continue
            obj.method = m
            try:
                if obj((lo + hi) / 2) is None:
                    continue
            except Exception:
                continue
            return m, (lo, hi)
    m = obj.method
    return m, obj.T_limits.get(m, (None, None))


def _fit_error(f_fit, f_src, T):
    """Max relative error of a fit over T (ignoring invalid source points)."""
    err = 0.0
    for Ti in T:
        try:
            a, b = float(f_fit(Ti)), float(f_src(Ti))
        except Exception:
            continue
        if b and abs(b) > 1e-12:
            err = max(err, abs(a - b) / abs(b))
    return err


def _fit_cp(cas, MW):
    from thermo import HeatCapacityGas

    obj = HeatCapacityGas(CASRN=cas, MW=MW)
    method, (lo, hi) = _pick_method(obj, ["TRCIG", "POLING_POLY", "HEOS_FIT", "VDI_TABULAR", "POLING_CONST"])
    lo = max(lo or 150.0, 150.0)
    hi = min(hi or 1200.0, 1200.0)
    if hi - lo < 300:  # narrow correlation: allow the library's own extrapolation a little further
        hi = lo + 300
    T = np.linspace(lo, hi, 80)
    cp = np.array([obj(Ti) for Ti in T], dtype=float)
    good = np.isfinite(cp)
    coeffs = np.polynomial.polynomial.polyfit(T[good] / 1000.0, cp[good], 6)
    fit = np.polynomial.polynomial.polyval(T[good] / 1000.0, coeffs)
    err = float(np.max(np.abs(fit - cp[good]) / np.abs(cp[good])))
    return list(map(float, coeffs)), (float(lo), float(hi)), f"{method} (fit error {err:.2%})"


def _fit_psat(cas, Tb, Tc, Pc, omega):
    from thermo import VaporPressure

    obj = VaporPressure(CASRN=cas, Tb=Tb, Tc=Tc, Pc=Pc, omega=omega)
    method, (lo, hi) = _pick_method(obj, ["HEOS_FIT", "WAGNER_MCGARRY", "WAGNER_POLING", "DIPPR_PERRY_8E", "VDI_PPDS",
                                                "ANTOINE_EXTENDED_POLING", "ANTOINE_POLING"])
    lo = lo or 0.45 * Tc
    hi = min(hi or Tc, Tc)
    lo = max(lo, 0.3 * Tc)
    T = np.linspace(lo, hi * 0.999, 60)
    P = np.array([obj(Ti) for Ti in T], dtype=float)
    good = np.isfinite(P) & (P > 0)
    T, lnP = T[good], np.log(P[good])
    # linear least squares in (1, 1/T, lnT, T^2)
    X = np.column_stack([np.ones_like(T), 1 / T, np.log(T), T * T])
    coef, *_ = np.linalg.lstsq(X, lnP, rcond=None)
    err = float(np.max(np.abs(np.exp(X @ coef - lnP) - 1.0)))
    A, B, C, D = map(float, coef)
    return [A, B, 0.0, C, D, 2.0], (float(T.min()), float(T.max())), f"{method} (fit error {err:.2%})"


def _fit_dhvap(cas, Tb, Tc, Pc, omega):
    from scipy.optimize import curve_fit
    from thermo import EnthalpyVaporization

    obj = EnthalpyVaporization(CASRN=cas, Tb=Tb, Tc=Tc, Pc=Pc, omega=omega)
    method, (lo, hi) = _pick_method(obj, ["DIPPR_PERRY_8E", "VDI_PPDS", "HEOS_FIT", "VDI_TABULAR", "ALIBAKHSHI"])
    lo = max(lo or 0.4 * Tc, 0.35 * Tc)
    hi = min(hi or Tc, 0.995 * Tc)
    T = np.linspace(lo, hi, 50)
    H = np.array([obj(Ti) for Ti in T], dtype=float)
    good = np.isfinite(H) & (H > 0)
    T, H = T[good], H[good]
    Tr = T / Tc

    def f(Tr, A, B, C, D, E):
        return A * (1 - Tr) ** (B + Tr * (C + Tr * (D + Tr * E)))

    p0 = [H[0] / (1 - Tr[0]) ** 0.38, 0.38, 0.0, 0.0, 0.0]
    try:
        p, _ = curve_fit(f, Tr, H, p0=p0, maxfev=40000)
    except Exception:  # fall back to a Watson fit anchored at the first point
        p = p0
    core = Tr <= 0.95
    err = float(np.max(np.abs(f(Tr[core], *p) - H[core]) / H[core])) if core.any() else 0.0
    return list(map(float, p)), f"{method} (fit error {err:.2%} below 0.95 Tc)"


def _fit_zra(cas, MW, Tb, Tc, Pc, Vc, Zc, omega):
    """Rackett Z_RA from a data-based liquid molar volume at 25 C (or 0.7 Tc for light gases)."""
    from thermo import VolumeLiquid

    obj = VolumeLiquid(CASRN=cas, MW=MW, Tb=Tb, Tc=Tc, Pc=Pc, Vc=Vc, Zc=Zc, omega=omega)
    method = None
    for m in ("HEOS_FIT", "DIPPR_PERRY_8E", "VDI_PPDS", "VDI_TABULAR", "COSTALD", "HTCOSTALDFIT"):
        if m in obj.all_methods:
            method = m
            break
    if method is None:
        return None, "Yamada-Gunn estimate"
    obj.method = method
    T_ref = min(298.15, 0.7 * Tc)
    try:
        V = obj.T_dependent_property(T_ref)
    except Exception:
        V = None
    if not V:
        return None, "Yamada-Gunn estimate"
    Tr = T_ref / Tc
    zra = (V / (R * Tc / Pc)) ** (1.0 / (1.0 + (1.0 - Tr) ** (2.0 / 7.0)))
    return float(zra), f"{method} at {T_ref:.1f} K"


def _load_cache() -> dict:
    try:
        data = json.loads(CACHE_PATH.read_text())
        if data.get("_version") == CACHE_VERSION:
            return data
    except Exception:
        pass
    return {"_version": CACHE_VERSION}


def _save_cache(cache: dict) -> None:
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_text(json.dumps(cache, indent=1))
    except Exception:
        pass


def lookup(name: str, id: str | None = None, **overrides) -> Component:
    """Build a :class:`Component` from the open databanks.

    ``name`` may be a common name ("n-butane"), a CAS number, a formula, or an Aspen alias
    ("C4H10-1"). ``id`` is the short ID used everywhere else (defaults to ``name``).
    Keyword ``overrides`` replace any retrieved value (e.g. ``Tc=...``) - handy for
    components that are missing or poorly represented in the open data.
    """
    cas = overrides.pop("cas", None) or _resolve_cas(name)
    cache = _load_cache()
    data = cache.get(cas)
    if data is None:
        import chemicals
        from chemicals.identifiers import search_chemical

        meta = search_chemical(cas)
        MW = chemicals.MW(cas)
        Tc, Pc, omega = chemicals.Tc(cas), chemicals.Pc(cas), chemicals.omega(cas)
        Tb = chemicals.Tb(cas) or 0.6 * Tc
        Vc = chemicals.Vc(cas) or (0.2905 - 0.085 * omega) * R * Tc / Pc
        Zc = chemicals.Zc(cas) or Pc * Vc / (R * Tc)
        Hf = chemicals.Hfg(cas)
        S0 = chemicals.S0g(cas)
        sources = {}
        if Hf is None:
            Hf = 0.0
            sources["Hf"] = "missing (set to 0: reaction enthalpies will be wrong)"
        if S0 is None:
            S0 = 0.0
            sources["S0"] = "missing (set to 0)"
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            cp, cp_range, sources["cp"] = _fit_cp(cas, MW)
            psat, psat_range, sources["psat"] = _fit_psat(cas, Tb, Tc, Pc, omega)
            dhvap, sources["dhvap"] = _fit_dhvap(cas, Tb, Tc, Pc, omega)
            Z_ra, sources["liquid_volume"] = _fit_zra(cas, MW, Tb, Tc, Pc, Vc, Zc, omega)
        data = dict(name=meta.common_name, cas=cas, formula=meta.formula, MW=MW, Tc=Tc, Pc=Pc,
                    omega=omega, Tb=Tb, Vc=Vc, Zc=Zc, Hf=Hf, S0=S0, cp=cp, cp_range=cp_range,
                    psat=psat, psat_range=psat_range, dhvap=dhvap, sources=sources, Z_ra=Z_ra)
        cache[cas] = data
        _save_cache(cache)
    data = dict(data)
    data["cp_range"] = tuple(data["cp_range"])
    data["psat_range"] = tuple(data["psat_range"])
    data.update(overrides)
    return Component(id=id or name, **data)


def user_component(id: str, *, name: str, MW: float, Tc: float, Pc: float, omega: float, Tb: float,
                   Vc: float | None = None, psat=None, dhvap=None, cp=None, Hf: float = 0.0, S0: float = 0.0,
                   formula: str = "", cas: str = "", rho_liq: float | None = None, T_range=(200.0, 800.0),
                   base: str | None = None) -> Component:
    """A component from your own data - Aspen's user-defined (pseudo) component.

    SI scalars (K, Pa, m3/mol). Correlations are ``(form, coefficients, unit)`` exactly as written in
    a handbook or problem statement:

    * ``psat  = ("PLXANT", [A, B, C, D, E, F], "kPa")``  ln P = A + B/(T+C) + D ln T + E T^F
    * ``dhvap = ("DIPPR106", [A, B, C, D, E], "kJ/kmol")``
    * ``cp    = ("DIPPR107", [A, B, C, D, E], "kJ/kmol-K")`` or ``("POLY", [A, B, C, D, E], ...)``

    Any correlation left out is taken from the databank entry ``base`` (a name for :func:`lookup`).
    ``rho_liq`` (kg/m3 at 25 C) fits the Rackett parameter; otherwise Yamada-Gunn is used.
    """
    sources = {}
    ref = lookup(base) if base else None
    Vc = Vc if Vc is not None else (ref.Vc if ref else (0.2905 - 0.085 * omega) * R * Tc / Pc)
    Zc = Pc * Vc / (R * Tc)
    if psat is not None:
        form, c, unit = psat
        if form.upper() != "PLXANT":
            raise ValueError(f"{id}: vapor pressure form {form!r} not supported (use PLXANT)")
        psat_c = plxant_to_pa(c, unit)
        sources["psat"] = "user PLXANT (exact)"
    elif ref:
        psat_c, sources["psat"] = list(ref.psat), ref.sources.get("psat", "databank")
    else:
        raise ValueError(f"{id}: give psat or base")
    if dhvap is not None:
        form, c, unit = dhvap
        if form.upper() not in ("DIPPR106", "DHVLDP"):
            raise ValueError(f"{id}: heat of vaporization form {form!r} not supported (use DIPPR106)")
        k = MOLAR_ENERGY_TO_J_PER_MOL[unit]
        c = (list(c) + [0.0] * 5)[:5]
        dhvap_c = [c[0] * k] + list(map(float, c[1:]))
        sources["dhvap"] = "user DIPPR 106 (exact)"
    elif ref:
        dhvap_c, sources["dhvap"] = list(ref.dhvap), ref.sources.get("dhvap", "databank")
    else:
        raise ValueError(f"{id}: give dhvap or base")
    if cp is not None:
        form, c, unit = cp
        k = CP_TO_J_PER_MOL_K[unit]
        if form.upper() in ("DIPPR107", "CPIG107"):
            fn = lambda T: k * dippr107_cp(T, c)  # noqa: E731
        elif form.upper() == "POLY":
            fn = lambda T: k * poly_cp(T, c)  # noqa: E731
        else:
            raise ValueError(f"{id}: Cp form {form!r} not supported (DIPPR107 or POLY)")
        cp_c, err = fit_cp_poly6(fn, *T_range)
        sources["cp"] = f"user {form.upper()} (fit error {err:.2%})"
        cp_range = tuple(map(float, T_range))
    elif ref:
        cp_c, cp_range, sources["cp"] = list(ref.cp), ref.cp_range, ref.sources.get("cp", "databank")
    else:
        raise ValueError(f"{id}: give cp or base")
    Z_ra = None
    if rho_liq:
        V = MW / 1000.0 / rho_liq
        Tr = min(298.15 / Tc, 1.0)
        Z_ra = (V / (R * Tc / Pc)) ** (1.0 / (1.0 + (1.0 - Tr) ** (2.0 / 7.0)))
        sources["liquid_volume"] = f"user density {rho_liq:g} kg/m3 at 25 C"
    elif ref:
        Z_ra, sources["liquid_volume"] = ref.Z_ra, ref.sources.get("liquid_volume", "databank")
    else:
        sources["liquid_volume"] = "Yamada-Gunn estimate"
    return Component(id=id, name=name, cas=cas or (ref.cas if ref else ""), formula=formula or (ref.formula if ref else ""),
                     MW=float(MW), Tc=float(Tc), Pc=float(Pc), omega=float(omega), Tb=float(Tb), Vc=float(Vc), Zc=float(Zc),
                     Hf=float(Hf if ref is None or Hf else ref.Hf), S0=float(S0 if ref is None or S0 else ref.S0),
                     cp=cp_c, cp_range=cp_range, psat=psat_c, psat_range=(0.3 * Tc, Tc), dhvap=dhvap_c,
                     sources=sources, Z_ra=Z_ra)


class ComponentList:
    """Ordered component list with id lookup - the equivalent of Aspen's Components form."""

    def __init__(self, components):
        comps = []
        for c in components:
            if isinstance(c, Component):
                comps.append(c)
            elif isinstance(c, tuple):  # ("C3", "propane") or ("C3", "propane", {overrides})
                cid, name, *rest = c
                comps.append(lookup(name, id=cid, **(rest[0] if rest else {})))
            else:
                comps.append(lookup(c, id=c))
        self.items: list[Component] = comps
        self.ids = [c.id for c in comps]
        self.index = {c.id: i for i, c in enumerate(comps)}
        if len(self.index) != len(comps):
            raise ValueError("duplicate component ids")
        # vectors used by the property methods
        self.MW = np.array([c.MW for c in comps])
        self.Tc = np.array([c.Tc for c in comps])
        self.Pc = np.array([c.Pc for c in comps])
        self.omega = np.array([c.omega for c in comps])
        self.Tb = np.array([c.Tb for c in comps])
        self.Vc = np.array([c.Vc for c in comps])
        self.Zc = np.array([c.Zc for c in comps])
        self.Z_RA = np.array([c.Z_RA for c in comps])
        self.Hf = np.array([c.Hf for c in comps])
        self.S0 = np.array([c.S0 for c in comps])
        self.cp_coeffs = np.array([c.cp for c in comps])        # (C, 7)
        self.psat_coeffs = np.array([c.psat for c in comps])    # (C, 6) PLXANT, Pa
        self.dhvap_coeffs = np.array([c.dhvap for c in comps])  # (C, 5)
        self._h_ref = _int_cp(self.cp_coeffs.T, T_REF)
        self._s_ref = _int_cp_over_T(self.cp_coeffs.T, T_REF)

    def __len__(self):
        return len(self.items)

    def __iter__(self):
        return iter(self.items)

    def __getitem__(self, key):
        if isinstance(key, str):
            return self.items[self.index[key]]
        return self.items[key]

    def idx(self, cid: str) -> int:
        try:
            return self.index[cid]
        except KeyError:
            raise KeyError(f"unknown component {cid!r}; components are {self.ids}") from None

    @property
    def cas(self):
        return [c.cas for c in self.items]

    # --- vectorized ideal-gas properties: T has shape (...), result (..., C)
    def h_ig(self, T):
        T = np.asarray(T, dtype=float)[..., None]
        return self.Hf + _int_cp(self.cp_coeffs.T, T) - self._h_ref

    def cp_ig(self, T):
        t = np.asarray(T, dtype=float)[..., None] / 1000.0
        return sum(self.cp_coeffs[:, k] * t ** k for k in range(self.cp_coeffs.shape[1]))

    def s_ig_pure(self, T):
        """Pure ideal-gas entropy at T and 1 bar, shape (..., C)."""
        T = np.asarray(T, dtype=float)[..., None]
        return self.S0 + _int_cp_over_T(self.cp_coeffs.T, T) - self._s_ref

    def Psat(self, T):
        T = np.asarray(T, dtype=float)[..., None]
        A, B, C, D, E, F = self.psat_coeffs.T
        lnP = A + B / (T + C) + D * np.log(T) + E * T ** F
        return np.exp(np.clip(lnP, -700.0, 700.0))

    def dHvap(self, T):
        T = np.asarray(T, dtype=float)[..., None]
        A, B, C, D, E = self.dhvap_coeffs.T
        Tr = np.minimum(T / self.Tc, 1.0)
        return A * (1.0 - Tr) ** (B + Tr * (C + Tr * (D + Tr * E)))

    def v_liquid_pure(self, T):
        """Rackett saturated-liquid molar volume (m3/mol), shape (..., C)."""
        T = np.asarray(T, dtype=float)[..., None]
        Tr = np.minimum(T / self.Tc, 1.0)
        return R * self.Tc / self.Pc * self.Z_RA ** (1.0 + (1.0 - Tr) ** (2.0 / 7.0))

    def describe(self) -> list[dict]:
        """Rows for a 'Components' table in a report."""
        return [dict(id=c.id, name=c.name, formula=c.formula, cas=c.cas, MW=c.MW, Tc=c.Tc, Pc=c.Pc,
                     omega=c.omega, Tb=c.Tb) for c in self.items]


def clear_cache() -> None:
    if CACHE_PATH.exists():
        CACHE_PATH.unlink()

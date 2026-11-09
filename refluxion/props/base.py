"""Property-method interface.

A property method is Aspen's "collection of models and methods" (PENG-ROB, RK-SOAVE, NRTL,
IDEAL, ...). Every unit operation talks to it through the same small interface:

    lnK(T, P, x, y)      ln of the vapor-liquid equilibrium ratios K_i = y_i / x_i
    h_liq / h_vap        molar enthalpy of a phase (J/mol, heat-of-formation reference)
    s_liq / s_vap        molar entropy of a phase (J/mol-K)
    v_liq / v_vap        molar volume (m3/mol)

Shapes: T and P are scalars or arrays of shape (N,); compositions are (C,) or (N, C). All
methods broadcast so a whole column (N stages) is evaluated in one call.
"""

from __future__ import annotations

import numpy as np

from ..components import R, ComponentList


class PropertyMethod:
    name = "BASE"
    description = ""
    # True when both phases come from one equation (cubic EOS), so a saturation search can collapse onto
    # the trivial root x = y. Gamma-phi methods have no such root: K = 1 there is a real azeotrope.
    has_trivial_root = False

    def __init__(self, comps: ComponentList):
        self.comps = comps
        self.C = len(comps)
        self.notes: list[str] = []  # human-readable notes about parameters (shown in reports)

    # --- to be implemented --------------------------------------------------------------
    def lnK(self, T, P, x, y):
        raise NotImplementedError

    def h_liq(self, T, P, x):
        raise NotImplementedError

    def h_vap(self, T, P, y):
        raise NotImplementedError

    def s_liq(self, T, P, x):
        raise NotImplementedError

    def s_vap(self, T, P, y):
        raise NotImplementedError

    def v_vap(self, T, P, y):
        raise NotImplementedError

    # --- shared helpers -----------------------------------------------------------------
    def v_liq(self, T, P, x):
        """Liquid molar volume from the Rackett equation with ideal mixing (m3/mol)."""
        x = np.asarray(x, dtype=float)
        return np.sum(x * self.comps.v_liquid_pure(T), axis=-1)

    def K_wilson(self, T, P):
        """Wilson's correlation - the standard initial guess for K-values."""
        c = self.comps
        T = np.asarray(T, dtype=float)[..., None]
        P = np.asarray(P, dtype=float)[..., None]
        return c.Pc / P * np.exp(5.373 * (1.0 + c.omega) * (1.0 - c.Tc / T))

    def K_initial(self, T, P):
        return self.K_wilson(T, P)

    def h_ig_mix(self, T, z):
        return np.sum(np.asarray(z) * self.comps.h_ig(T), axis=-1)

    def s_ig_mix(self, T, P, z):
        """Ideal-gas mixture entropy including the mixing term."""
        z = np.asarray(z, dtype=float)
        s_pure = self.comps.s_ig_pure(T)
        P = np.asarray(P, dtype=float)
        with np.errstate(divide="ignore", invalid="ignore"):
            mix = np.where(z > 0, z * np.log(np.where(z > 0, z, 1.0)), 0.0)
        return np.sum(z * s_pure, axis=-1) - R * np.log(P / 1e5) - R * np.sum(mix, axis=-1)

    def cp_liq(self, T, P, x, dT=0.05):
        return (self.h_liq(T + dT, P, x) - self.h_liq(T - dT, P, x)) / (2 * dT)

    def cp_vap(self, T, P, y, dT=0.05):
        return (self.h_vap(T + dT, P, y) - self.h_vap(T - dT, P, y)) / (2 * dT)

    def single_phase_is_vapor(self, T, P, z) -> bool:
        """For a stream that doesn't split: is it vapor-like? (Pseudo-critical rule.)"""
        z = np.asarray(z, dtype=float)
        Tpc = float(np.sum(z * self.comps.Tc))
        return float(T) >= Tpc

    def info(self) -> dict:
        return {"method": self.name, "description": self.description, "notes": list(self.notes)}

    def __repr__(self) -> str:
        return f"<{self.name} property method, {self.C} components>"

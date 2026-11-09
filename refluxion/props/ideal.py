"""IDEAL: Raoult's law with an ideal-gas vapor and ideal liquid.

    K_i = Psat_i(T) / P
    h_vap = sum y_i h_ig,i(T)          h_liq = sum x_i [h_ig,i(T) - dHvap_i(T)]
"""

from __future__ import annotations

import numpy as np

from ..components import R
from .base import PropertyMethod


class IdealMethod(PropertyMethod):
    name = "IDEAL"
    description = "Raoult's law: ideal-gas vapor, ideal-solution liquid"

    def lnK(self, T, P, x, y):
        P = np.asarray(P, dtype=float)[..., None]
        return np.log(self.comps.Psat(T)) - np.log(P)

    def K_initial(self, T, P):
        return np.exp(self.lnK(T, P, None, None))

    def h_vap(self, T, P, y):
        return self.h_ig_mix(T, y)

    def h_liq(self, T, P, x):
        x = np.asarray(x, dtype=float)
        return np.sum(x * (self.comps.h_ig(T) - self.comps.dHvap(T)), axis=-1)

    def s_vap(self, T, P, y):
        return self.s_ig_mix(T, P, y)

    def s_liq(self, T, P, x):
        x = np.asarray(x, dtype=float)
        T_ = np.asarray(T, dtype=float)
        c = self.comps
        # pure liquid: ideal gas at Psat, then condense (-dHvap/T); pressure effect neglected
        psat = c.Psat(T_)
        s_pure = c.s_ig_pure(T_) - R * np.log(psat / 1e5) - c.dHvap(T_) / T_[..., None]
        with np.errstate(divide="ignore", invalid="ignore"):
            mix = np.where(x > 0, x * np.log(np.where(x > 0, x, 1.0)), 0.0)
        return np.sum(x * s_pure, axis=-1) - R * np.sum(mix, axis=-1)

    def v_vap(self, T, P, y):
        return R * np.asarray(T, dtype=float) / np.asarray(P, dtype=float)

    def single_phase_is_vapor(self, T, P, z) -> bool:
        z = np.asarray(z, dtype=float)
        return float(np.sum(z * self.comps.Psat(T))) > float(P)

"""Activity-coefficient (gamma-phi) methods: NRTL and NRTL-RK.

    K_i = gamma_i(T, x) * Psat_i(T) / (phi_i^V * P)

NRTL uses Aspen's parameter form (BPVAL NRTL i j):

    tau_ij = a_ij + b_ij/T + e_ij ln T + f_ij T          alpha_ij = c_ij + d_ij (T - 273.15)
    G_ij = exp(-alpha_ij tau_ij)

Vapor phase: ideal gas (NRTL) or Redlich-Kwong (NRTL-RK). Liquid enthalpy follows Aspen's
activity-coefficient route: h_L = sum x_i (h_ig,i - dHvap_i) + h_E, with the excess enthalpy
h_E = -R T^2 sum x_i d(ln gamma_i)/dT.

Limitation (v1): supercritical components (N2, O2, CH4 ... above Tc) need Henry's law, which is
not implemented yet; their Psat is extrapolated and a warning is recorded.
"""

from __future__ import annotations

import numpy as np

from ..components import R
from .base import PropertyMethod
from .cubic import CubicEOS


class NRTLMethod(PropertyMethod):
    def __init__(self, comps, params: dict, vapor: str = "IG", source_notes=()):
        super().__init__(comps)
        C = self.C
        self.a = np.asarray(params.get("a", np.zeros((C, C))), float)
        self.b = np.asarray(params.get("b", np.zeros((C, C))), float)
        self.c = np.asarray(params.get("c", np.full((C, C), 0.3)), float)
        self.d = np.asarray(params.get("d", np.zeros((C, C))), float)
        self.e = np.asarray(params.get("e", np.zeros((C, C))), float)
        self.f = np.asarray(params.get("f", np.zeros((C, C))), float)
        for arr in (self.a, self.b, self.d, self.e, self.f):
            np.fill_diagonal(arr, 0.0)
        self.vapor = vapor.upper()
        self.vap_eos = CubicEOS(comps, "RK") if self.vapor == "RK" else None
        self.name = "NRTL-RK" if self.vap_eos is not None else "NRTL"
        self.description = ("NRTL activity-coefficient liquid, " +
                            ("Redlich-Kwong vapor" if self.vap_eos is not None else "ideal-gas vapor"))
        self.notes.extend(source_notes)

    # --- activity coefficients ------------------------------------------------------------
    def tau_G(self, T):
        T = np.asarray(T, dtype=float)[..., None, None]
        tau = self.a + self.b / T + self.e * np.log(T) + self.f * T
        alpha = self.c + self.d * (T - 273.15)
        G = np.exp(-alpha * tau)
        return tau, G

    def lngamma(self, T, x):
        x = np.asarray(x, dtype=float)
        tau, G = self.tau_G(T)                      # (..., C, C) indexed [i, j]
        # S_j = sum_k x_k G_kj ; W_j = sum_k x_k tau_kj G_kj
        S = np.einsum("...k,...kj->...j", x, G)
        W = np.einsum("...k,...kj->...j", x, tau * G)
        term1 = W / S                                # sum_j x_j tau_ji G_ji / sum_k x_k G_ki  (index i)
        # term2_i = sum_j x_j G_ij / S_j * (tau_ij - W_j / S_j)
        frac = x[..., None, :] * G / S[..., None, :]
        term2 = np.sum(frac * (tau - (W / S)[..., None, :]), axis=-1)
        return term1 + term2

    def lnphi_vap(self, T, P, y):
        if self.vap_eos is None:
            return 0.0
        return self.vap_eos.lnphi(T, P, y, "V")

    def lnK(self, T, P, x, y):
        P_ = np.asarray(P, dtype=float)[..., None]
        lnK = self.lngamma(T, x) + np.log(self.comps.Psat(T)) - np.log(P_)
        if self.vap_eos is not None and y is not None:
            lnK = lnK - self.lnphi_vap(T, P, y)
        return lnK

    def K_initial(self, T, P):
        P_ = np.asarray(P, dtype=float)[..., None]
        return self.comps.Psat(T) / P_

    # --- enthalpy / entropy -----------------------------------------------------------------
    def h_excess(self, T, x, dT=0.01):
        x = np.asarray(x, dtype=float)
        T = np.asarray(T, dtype=float)
        dlng = (self.lngamma(T + dT, x) - self.lngamma(T - dT, x)) / (2 * dT)
        return -R * T * T * np.sum(x * dlng, axis=-1)

    def h_liq(self, T, P, x):
        x = np.asarray(x, dtype=float)
        c = self.comps
        return np.sum(x * (c.h_ig(T) - c.dHvap(T)), axis=-1) + self.h_excess(T, x)

    def h_vap(self, T, P, y):
        h = self.h_ig_mix(T, y)
        if self.vap_eos is not None:
            h = h + self.vap_eos.h_dep(T, P, y, "V")
        return h

    def s_vap(self, T, P, y):
        s = self.s_ig_mix(T, P, y)
        if self.vap_eos is not None:
            s = s + self.vap_eos.s_dep(T, P, y, "V")
        return s

    def s_liq(self, T, P, x):
        x = np.asarray(x, dtype=float)
        T_ = np.asarray(T, dtype=float)
        c = self.comps
        psat = c.Psat(T_)
        s_pure = c.s_ig_pure(T_) - R * np.log(psat / 1e5) - c.dHvap(T_) / T_[..., None]
        with np.errstate(divide="ignore", invalid="ignore"):
            mix = np.where(x > 0, x * np.log(np.where(x > 0, x, 1.0)), 0.0)
        g_e_over_RT = np.sum(x * self.lngamma(T_, x), axis=-1)
        h_e = self.h_excess(T_, x)
        s_e = (h_e - R * T_ * g_e_over_RT) / T_
        return np.sum(x * s_pure, axis=-1) - R * np.sum(mix, axis=-1) + s_e

    def v_vap(self, T, P, y):
        if self.vap_eos is not None:
            return self.vap_eos.v_vap(T, P, y)
        return R * np.asarray(T, dtype=float) / np.asarray(P, dtype=float)

    def single_phase_is_vapor(self, T, P, z) -> bool:
        z = np.asarray(z, dtype=float)
        # bubble-point pressure at T vs P
        pb = float(np.sum(z * np.exp(self.lngamma(T, z)) * self.comps.Psat(T)))
        return pb < float(P)

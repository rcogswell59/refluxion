"""Cubic equations of state: Peng-Robinson (PENG-ROB), Soave-Redlich-Kwong (RK-SOAVE) and
Redlich-Kwong (RK), in the generic form

    P = RT/(V - b) - a(T) / (V^2 + u*b*V + w*b^2)        PR: u=2, w=-1   SRK/RK: u=1, w=0

with van der Waals one-fluid mixing rules and binary interaction parameters kij:

    a_m = sum_i sum_j x_i x_j sqrt(a_i a_j) (1 - k_ij),     b_m = sum_i x_i b_i

Above the critical temperature the alpha function switches to the Boston-Mathias
extrapolation (what Aspen does), which keeps supercritical gases like H2 and CH4 well behaved.
For RK-SOAVE, hydrogen uses the Graboski-Daubert alpha, as Aspen's RKSOAVE does.

Everything is vectorized over a leading "state" axis so a column's stages are one call.
"""

from __future__ import annotations

import numpy as np

from ..components import R, ComponentList
from .base import PropertyMethod

H2_CAS = "1333-74-0"


def solve_cubic(c2, c1, c0):
    """Real roots of Z^3 + c2 Z^2 + c1 Z + c0 = 0, vectorized.

    Returns (zmin, zmax): the smallest and largest real roots (equal when only one is real).
    """
    c2, c1, c0 = np.broadcast_arrays(np.asarray(c2, float), np.asarray(c1, float), np.asarray(c0, float))
    q = (3 * c1 - c2 * c2) / 9.0
    r = (9 * c2 * c1 - 27 * c0 - 2 * c2 ** 3) / 54.0
    disc = q ** 3 + r * r
    one = disc > 0
    # one real root (Cardano)
    sq = np.sqrt(np.where(one, disc, 0.0))
    S = np.cbrt(r + sq)
    T = np.cbrt(r - sq)
    z_one = S + T - c2 / 3.0
    # three real roots (trigonometric)
    mq = np.where(one, 1.0, -q)
    mq = np.maximum(mq, 1e-300)
    theta = np.arccos(np.clip(r / np.sqrt(mq ** 3), -1.0, 1.0))
    base = 2.0 * np.sqrt(mq)
    z1 = base * np.cos(theta / 3.0) - c2 / 3.0
    z2 = base * np.cos((theta + 2 * np.pi) / 3.0) - c2 / 3.0
    z3 = base * np.cos((theta + 4 * np.pi) / 3.0) - c2 / 3.0
    zmax = np.where(one, z_one, np.maximum(np.maximum(z1, z2), z3))
    zmin = np.where(one, z_one, np.minimum(np.minimum(z1, z2), z3))

    def polish(z):
        for _ in range(2):
            f = ((z + c2) * z + c1) * z + c0
            df = (3 * z + 2 * c2) * z + c1
            z = z - np.where(np.abs(df) > 1e-14, f / np.where(np.abs(df) > 1e-14, df, 1.0), 0.0)
        return z

    return polish(zmin), polish(zmax)


class CubicEOS(PropertyMethod):
    has_trivial_root = True
    KINDS = {
        # name: (Omega_a, Omega_b, u, w)
        "PR": (0.45723553, 0.07779607, 2.0, -1.0),
        "SRK": (0.42748023, 0.08664035, 1.0, 0.0),
        "RK": (0.42748023, 0.08664035, 1.0, 0.0),
    }
    ASPEN_NAMES = {"PR": "PENG-ROB", "SRK": "RK-SOAVE", "RK": "RK"}

    def __init__(self, comps: ComponentList, kind: str = "PR", kij=None, kij_source: str = ""):
        super().__init__(comps)
        kind = kind.upper()
        if kind not in self.KINDS:
            raise ValueError(f"unknown cubic EOS {kind!r}")
        self.kind = kind
        self.name = self.ASPEN_NAMES[kind]
        self.Omega_a, self.Omega_b, self.u, self.w = self.KINDS[kind]
        self.delta = np.sqrt(self.u ** 2 - 4 * self.w)
        c = comps
        self.ac = self.Omega_a * (R * c.Tc) ** 2 / c.Pc   # (C,)
        self.b = self.Omega_b * R * c.Tc / c.Pc            # (C,)
        w = c.omega
        if kind == "PR":
            self.m = 0.37464 + 1.54226 * w - 0.26992 * w * w
            self.description = "Peng-Robinson cubic EOS (1976), Boston-Mathias alpha above Tc"
        elif kind == "SRK":
            self.m = 0.480 + 1.574 * w - 0.176 * w * w
            self.description = "Soave-Redlich-Kwong cubic EOS, Boston-Mathias alpha above Tc, Graboski-Daubert alpha for H2"
        else:
            self.m = np.zeros_like(w)
            self.description = "Redlich-Kwong cubic EOS (alpha = Tr^-0.5)"
        self.is_h2 = np.array([comp.cas == H2_CAS for comp in comps])
        C = len(comps)
        self.kij = np.zeros((C, C)) if kij is None else np.asarray(kij, dtype=float)
        self.kij = 0.5 * (self.kij + self.kij.T)
        np.fill_diagonal(self.kij, 0.0)
        self.one_minus_k = 1.0 - self.kij
        if kij_source:
            self.notes.append(kij_source)

    # --- pure-component attraction parameter and its T derivative ------------------------
    def a_alpha(self, T):
        """Return (a_i, da_i/dT), each shaped (..., C)."""
        c = self.comps
        T = np.asarray(T, dtype=float)[..., None]
        Tr = T / c.Tc
        if self.kind == "RK":
            alpha = Tr ** -0.5
            dalpha = -0.5 * Tr ** -1.5 / c.Tc
        else:
            m = self.m
            sq = np.sqrt(Tr)
            sub = Tr <= 1.0
            a_sub = (1.0 + m * (1.0 - sq)) ** 2
            da_sub = -(1.0 + m * (1.0 - sq)) * m / (sq * c.Tc)
            # Boston-Mathias extrapolation for supercritical components
            d = 1.0 + m / 2.0
            cc = 1.0 - 1.0 / d
            a_sup = np.exp(2.0 * cc * (1.0 - Tr ** d))
            da_sup = a_sup * (-2.0 * cc * d * Tr ** (d - 1.0)) / c.Tc
            alpha = np.where(sub, a_sub, a_sup)
            dalpha = np.where(sub, da_sub, da_sup)
            if self.kind == "SRK" and self.is_h2.any():
                a_h2 = 1.202 * np.exp(-0.30228 * Tr)
                da_h2 = -0.30228 / c.Tc * a_h2
                alpha = np.where(self.is_h2, a_h2, alpha)
                dalpha = np.where(self.is_h2, da_h2, dalpha)
        return self.ac * alpha, self.ac * dalpha

    def mix(self, T, x):
        """Mixture parameters: a_m, b_m, da_m/dT and sum_j x_j a_ij (for fugacities)."""
        x = np.asarray(x, dtype=float)
        ai, dai = self.a_alpha(T)
        si = np.sqrt(ai)
        dsi = dai / (2.0 * si)
        # sum_j x_j (1-k_ij) sqrt(a_j)  -> (..., C)
        xs = np.einsum("...j,ij->...i", x * si, self.one_minus_k)
        sum_xa = si * xs
        am = np.sum(x * sum_xa, axis=-1)
        dam = 2.0 * np.sum(x * dsi * xs, axis=-1)
        bm = np.sum(x * self.b, axis=-1)
        return am, bm, dam, sum_xa

    def Z(self, T, P, x, phase):
        """Compressibility factor of the vapor-like ('V') or liquid-like ('L') root."""
        am, bm, _, _ = self.mix(T, x)
        return self._Z(T, P, am, bm, phase)[0]

    def _Z(self, T, P, am, bm, phase):
        T = np.asarray(T, dtype=float)
        P = np.asarray(P, dtype=float)
        RT = R * T
        A = am * P / (RT * RT)
        B = bm * P / RT
        u, w = self.u, self.w
        c2 = -(1.0 + B - u * B)
        c1 = A + w * B * B - u * B - u * B * B
        c0 = -(A * B + w * B * B + w * B ** 3)
        zmin, zmax = solve_cubic(c2, c1, c0)
        z = zmax if phase == "V" else zmin
        z = np.maximum(z, B * (1.0 + 1e-10))
        return z, A, B

    def _log_term(self, Z, B):
        d = self.delta
        return np.log((2.0 * Z + B * (self.u + d)) / (2.0 * Z + B * (self.u - d)))

    def lnphi(self, T, P, x, phase):
        x = np.asarray(x, dtype=float)
        am, bm, _, sum_xa = self.mix(T, x)
        Z, A, B = self._Z(T, P, am, bm, phase)
        Z_, A_, B_, am_, bm_ = (np.asarray(v)[..., None] for v in (Z, A, B, am, bm))
        bi_b = self.b / bm_
        L = self._log_term(Z_, B_)
        return bi_b * (Z_ - 1.0) - np.log(Z_ - B_) - A_ / (B_ * self.delta) * (2.0 * sum_xa / am_ - bi_b) * L

    def lnK(self, T, P, x, y):
        return self.lnphi(T, P, x, "L") - self.lnphi(T, P, y, "V")

    def h_dep(self, T, P, x, phase):
        am, bm, dam, _ = self.mix(T, x)
        Z, A, B = self._Z(T, P, am, bm, phase)
        T = np.asarray(T, dtype=float)
        return R * T * (Z - 1.0) + (T * dam - am) / (bm * self.delta) * self._log_term(Z, B)

    def s_dep(self, T, P, x, phase):
        am, bm, dam, _ = self.mix(T, x)
        Z, A, B = self._Z(T, P, am, bm, phase)
        return R * np.log(Z - B) + dam / (bm * self.delta) * self._log_term(Z, B)

    def h_liq(self, T, P, x):
        return self.h_ig_mix(T, x) + self.h_dep(T, P, x, "L")

    def h_vap(self, T, P, y):
        return self.h_ig_mix(T, y) + self.h_dep(T, P, y, "V")

    def s_liq(self, T, P, x):
        return self.s_ig_mix(T, P, x) + self.s_dep(T, P, x, "L")

    def s_vap(self, T, P, y):
        return self.s_ig_mix(T, P, y) + self.s_dep(T, P, y, "V")

    def v_vap(self, T, P, y):
        return self.Z(T, P, y, "V") * R * np.asarray(T) / np.asarray(P)

    def v_liq_eos(self, T, P, x):
        return self.Z(T, P, x, "L") * R * np.asarray(T) / np.asarray(P)

    def single_phase_is_vapor(self, T, P, z) -> bool:
        """Decide the phase of a single-phase fluid from the EOS: compare the Gibbs energy of
        the two roots when both exist, otherwise use the reduced volume V/b."""
        z = np.asarray(z, dtype=float)
        am, bm, _, _ = self.mix(T, z)
        zl, _, B = self._Z(T, P, am, bm, "L")
        zv, _, _ = self._Z(T, P, am, bm, "V")
        if abs(float(zv - zl)) > 1e-6:  # two roots: the one with lower fugacity is stable
            glnl = float(np.sum(z * self.lnphi(T, P, z, "L")))
            glnv = float(np.sum(z * self.lnphi(T, P, z, "V")))
            return glnv < glnl
        v_over_b = float(zv / B)
        return v_over_b > 1.75  # dense fluids (V < ~1.75 b) behave as liquids

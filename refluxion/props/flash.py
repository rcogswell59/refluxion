"""Flash calculations - the workhorse under every block.

Aspen runs a flash at the outlet of nearly every unit operation. The same here:

* :func:`tp_flash`   given T, P          -> phase split (successive substitution + Rachford-Rice,
                                             using the "negative flash" so single-phase feeds are
                                             detected cleanly)
* :func:`bubble_T` / :func:`dew_T` / :func:`bubble_P` / :func:`dew_P`
* :func:`pvf_flash`  given P and vapor fraction (bubble point = 0, dew point = 1)
* :func:`tvf_flash`  given T and vapor fraction
* :func:`ph_flash`   given P and enthalpy   (adiabatic flashes, heaters with a duty spec, valves)
* :func:`ps_flash`   given P and entropy    (isentropic compressors and turbines)

All inputs are SI (K, Pa, J/mol).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import brentq


class FlashError(RuntimeError):
    pass


@dataclass
class FlashResult:
    T: float
    P: float
    beta: float            # molar vapor fraction
    x: np.ndarray          # liquid mole fractions (incipient liquid if all vapor)
    y: np.ndarray          # vapor mole fractions (incipient vapor if all liquid)
    z: np.ndarray
    phase: str             # "V", "L" or "VL"
    iterations: int = 0
    K: np.ndarray | None = field(default=None, repr=False)

    def h(self, pm) -> float:
        if self.phase == "V":
            return float(pm.h_vap(self.T, self.P, self.z))
        if self.phase == "L":
            return float(pm.h_liq(self.T, self.P, self.z))
        return float(self.beta * pm.h_vap(self.T, self.P, self.y) + (1 - self.beta) * pm.h_liq(self.T, self.P, self.x))

    def s(self, pm) -> float:
        if self.phase == "V":
            return float(pm.s_vap(self.T, self.P, self.z))
        if self.phase == "L":
            return float(pm.s_liq(self.T, self.P, self.z))
        return float(self.beta * pm.s_vap(self.T, self.P, self.y) + (1 - self.beta) * pm.s_liq(self.T, self.P, self.x))


def _normalize(v):
    v = np.maximum(np.asarray(v, dtype=float), 0.0)
    s = v.sum()
    return v / s if s > 0 else v


def rachford_rice(z, K, beta0=0.5):
    """Solve sum z(K-1)/(1+beta(K-1)) = 0 for beta over the full 'negative flash' window."""
    z = np.asarray(z, float)
    K = np.asarray(K, float)
    m = z > 0
    zk, Kk = z[m], K[m]
    Kmax, Kmin = Kk.max(), Kk.min()
    if Kmax <= 1.0:
        return -np.inf  # all K <= 1: liquid
    if Kmin >= 1.0:
        return np.inf   # all K >= 1: vapor
    lo = 1.0 / (1.0 - Kmax) + 1e-12
    hi = 1.0 / (1.0 - Kmin) - 1e-12
    km1 = Kk - 1.0

    def f(b):
        return np.sum(zk * km1 / (1.0 + b * km1))

    b = min(max(beta0, lo + 1e-9), hi - 1e-9)
    a_lo, a_hi = lo, hi
    for _ in range(100):
        d = 1.0 + b * km1
        fv = np.sum(zk * km1 / d)
        if abs(fv) < 1e-14:
            return b
        if fv > 0:
            a_lo = b
        else:
            a_hi = b
        df = -np.sum(zk * km1 * km1 / (d * d))
        b_new = b - fv / df
        if not (a_lo < b_new < a_hi):
            b_new = 0.5 * (a_lo + a_hi)
        if abs(b_new - b) < 1e-14 * max(1.0, abs(b)):
            return b_new
        b = b_new
    return b


def tp_flash(pm, T, P, z, K0=None, tol=1e-10, maxiter=400) -> FlashResult:
    """Isothermal flash at T (K), P (Pa)."""
    z = _normalize(z)
    T = float(T)
    P = float(P)
    K = np.array(K0, dtype=float) if K0 is not None else np.asarray(pm.K_initial(T, P), dtype=float)
    K = np.clip(K, 1e-30, 1e30)
    beta = 0.5
    x = y = z
    lnK_old = np.log(K)
    for it in range(1, maxiter + 1):
        beta = rachford_rice(z, K, beta0=beta if np.isfinite(beta) else 0.5)
        if beta == -np.inf:     # all K<1: liquid; incipient vapor
            x = z
            y = _normalize(K * z)
            beta_eff = 0.0
        elif beta == np.inf:    # all K>1: vapor; incipient liquid
            y = z
            x = _normalize(z / K)
            beta_eff = 1.0
        else:
            d = 1.0 + beta * (K - 1.0)
            x = _normalize(z / d)
            y = _normalize(K * z / d)
            beta_eff = beta
        lnK = np.asarray(pm.lnK(T, P, x, y), dtype=float)
        lnK = np.clip(lnK, -69.0, 69.0)
        err = np.max(np.abs(lnK - lnK_old)[z > 0]) if np.any(z > 0) else 0.0
        K = np.exp(lnK)
        lnK_old = lnK
        if np.max(np.abs(lnK[z > 0])) < 1e-5:   # trivial solution: single phase
            vap = pm.single_phase_is_vapor(T, P, z)
            return FlashResult(T, P, 1.0 if vap else 0.0, z.copy(), z.copy(), z.copy(), "V" if vap else "L", it, K)
        if err < tol:
            break
    beta = rachford_rice(z, K, beta0=beta_eff if np.isfinite(beta_eff) else 0.5)
    if not np.isfinite(beta) or beta <= 0.0:
        phase = "L" if (beta == -np.inf or beta <= 0.0) else "V"
        if beta == np.inf:
            phase = "V"
    elif beta >= 1.0:
        phase = "V"
    else:
        phase = "VL"
    if phase == "VL":
        d = 1.0 + beta * (K - 1.0)
        x = _normalize(z / d)
        y = _normalize(K * z / d)
        return FlashResult(T, P, float(beta), x, y, z, "VL", it, K)
    if phase == "L":
        return FlashResult(T, P, 0.0, z.copy(), _normalize(K * z), z, "L", it, K)
    return FlashResult(T, P, 1.0, _normalize(z / K), z.copy(), z, "V", it, K)


# ----------------------------------------------------------------------------------------
# saturation points


def _sat_T(pm, P, z, kind, T0=None, maxiter=100):
    """Bubble ('bubble') or dew ('dew') temperature at pressure P."""
    z = _normalize(z)
    P = float(P)
    c = pm.comps
    if T0 is None:
        # Wilson-based estimate by bisection on ln(sum z K) (bubble) / ln(sum z/K) (dew)
        def g(T):
            K = pm.K_wilson(T, P)
            return np.log(np.sum(z * K)) if kind == "bubble" else -np.log(np.sum(z / K))
        lo, hi = 20.0, 2000.0
        try:
            T0 = brentq(g, lo, hi, xtol=1e-3)
        except ValueError:
            T0 = float(np.sum(z * c.Tb))
    T = float(T0)
    K = np.asarray(pm.K_initial(T, P), dtype=float)
    inc = _normalize(K * z) if kind == "bubble" else _normalize(z / K)

    def resid(T, inc):
        if kind == "bubble":
            lnK = pm.lnK(T, P, z, inc)
            K = np.exp(np.clip(lnK, -69, 69))
            s = np.sum(z * K)
            return np.log(s), _normalize(K * z), K
        lnK = pm.lnK(T, P, inc, z)
        K = np.exp(np.clip(lnK, -69, 69))
        s = np.sum(z / K)
        return -np.log(s), _normalize(z / K), K

    for it in range(maxiter):
        # converge the incipient phase composition at this T (a few substitutions)
        for _ in range(3):
            f, inc_new, K = resid(T, inc)
            inc = inc_new
        dT = max(1e-3, 1e-5 * T)
        f2, _, _ = resid(T + dT, inc)
        dfdT = (f2 - f) / dT
        if not np.isfinite(dfdT) or abs(dfdT) < 1e-12:
            raise FlashError(f"{kind} point search stalled at T={T:.2f} K")
        step = -f / dfdT
        step = float(np.clip(step, -30.0, 30.0))
        T = max(T + step, 5.0)
        if abs(step) < 1e-7 * T and abs(f) < 1e-9:
            # K = 1 is the right answer for a pure component; for a mixture it means the trivial root
            if pm.has_trivial_root and (z > 0).sum() > 1 and np.max(np.abs(np.log(np.maximum(K[z > 0], 1e-300)))) < 1e-4:
                raise FlashError(f"{kind} point converged to the trivial solution (near critical?)")
            return T, inc, K
    raise FlashError(f"{kind} temperature did not converge (last T={T:.2f} K)")


def bubble_T(pm, P, z, T0=None):
    return _sat_T(pm, P, z, "bubble", T0)[0]


def dew_T(pm, P, z, T0=None):
    return _sat_T(pm, P, z, "dew", T0)[0]


def _sat_P(pm, T, z, kind, P0=None, maxiter=100):
    z = _normalize(z)
    T = float(T)
    if P0 is None:
        K1 = pm.K_wilson(T, 1e5) * 1e5  # K*P
        P0 = float(np.sum(z * K1)) if kind == "bubble" else float(1.0 / np.sum(z / K1))
    lnP = np.log(max(P0, 1.0))
    K = np.asarray(pm.K_initial(T, np.exp(lnP)), dtype=float)
    inc = _normalize(K * z) if kind == "bubble" else _normalize(z / K)
    for it in range(maxiter):
        P = np.exp(lnP)
        for _ in range(3):
            if kind == "bubble":
                K = np.exp(np.clip(pm.lnK(T, P, z, inc), -69, 69))
                s = np.sum(z * K)
                inc = _normalize(K * z)
            else:
                K = np.exp(np.clip(pm.lnK(T, P, inc, z), -69, 69))
                s = np.sum(z / K)
                inc = _normalize(z / K)
        f = np.log(s) if kind == "bubble" else -np.log(s)
        # for ideal-ish behaviour ln(sum zK) ~ -ln P + const, so d f / d lnP ~ -1
        dl = 1e-4
        P2 = np.exp(lnP + dl)
        if kind == "bubble":
            f2 = np.log(np.sum(z * np.exp(np.clip(pm.lnK(T, P2, z, inc), -69, 69))))
        else:
            f2 = -np.log(np.sum(z / np.exp(np.clip(pm.lnK(T, P2, inc, z), -69, 69))))
        d = (f2 - f) / dl
        if not np.isfinite(d) or abs(d) < 1e-9:
            d = -1.0
        step = float(np.clip(-f / d, -1.0, 1.0))
        lnP += step
        if abs(step) < 1e-9 and abs(f) < 1e-9:
            return float(np.exp(lnP))
    raise FlashError(f"{kind} pressure did not converge at T={T:.2f} K")


def bubble_P(pm, T, z, P0=None):
    return _sat_P(pm, T, z, "bubble", P0)


def dew_P(pm, T, z, P0=None):
    return _sat_P(pm, T, z, "dew", P0)


# ----------------------------------------------------------------------------------------
# flashes on other state variables


def pvf_flash(pm, P, vf, z) -> FlashResult:
    """Flash at pressure P (Pa) and molar vapor fraction vf (0 = bubble point, 1 = dew point)."""
    z = _normalize(z)
    vf = float(vf)
    if vf <= 1e-12:
        T, y, K = _sat_T(pm, P, z, "bubble")
        return FlashResult(T, float(P), 0.0, z.copy(), y, z, "L", 0, K)
    if vf >= 1 - 1e-12:
        T, x, K = _sat_T(pm, P, z, "dew")
        return FlashResult(T, float(P), 1.0, x, z.copy(), z, "V", 0, K)
    Tb = bubble_T(pm, P, z)
    Td = dew_T(pm, P, z, T0=Tb + 20)
    state = {"K": None}

    def g(T):
        r = tp_flash(pm, T, P, z, K0=state["K"])
        state["K"] = r.K
        return r.beta - vf

    T = brentq(g, Tb, Td, xtol=1e-9)
    return tp_flash(pm, T, P, z, K0=state["K"])


def tvf_flash(pm, T, vf, z) -> FlashResult:
    """Flash at temperature T (K) and molar vapor fraction vf."""
    z = _normalize(z)
    vf = float(vf)
    if vf <= 1e-12:
        P = bubble_P(pm, T, z)
        r = tp_flash(pm, T, P * (1 + 1e-9), z)
        return FlashResult(float(T), P, 0.0, z.copy(), r.y, z, "L", 0, r.K)
    if vf >= 1 - 1e-12:
        P = dew_P(pm, T, z)
        r = tp_flash(pm, T, P * (1 - 1e-9), z)
        return FlashResult(float(T), P, 1.0, r.x, z.copy(), z, "V", 0, r.K)
    Pb = bubble_P(pm, T, z)
    Pd = dew_P(pm, T, z)
    P = brentq(lambda P: tp_flash(pm, T, P, z).beta - vf, Pd * (1 + 1e-9), Pb * (1 - 1e-9), xtol=1e-6)
    return tp_flash(pm, T, P, z)


def _state_flash(pm, P, target, z, prop, T0=None, tol=1e-7) -> FlashResult:
    """Find T at fixed P such that the mixture property (h or s) equals ``target``."""
    z = _normalize(z)
    P = float(P)
    cache = {"K": None}

    def g(T):
        r = tp_flash(pm, T, P, z, K0=cache["K"])
        cache["K"] = r.K
        return (r.h(pm) if prop == "h" else r.s(pm)) - target

    T0 = float(T0) if T0 is not None else 350.0
    scale = 1.0 if prop == "h" else 0.01
    g0 = g(T0)
    if abs(g0) < tol * scale * 1e3:
        return tp_flash(pm, T0, P, z, K0=cache["K"])
    # h and s increase with T: step in the right direction until the sign changes
    step = 10.0 if g0 < 0 else -10.0
    a, ga = T0, g0
    for _ in range(80):
        b = max(a + step, 10.0)
        gb = g(b)
        if np.sign(gb) != np.sign(ga):
            break
        a, ga = b, gb
        step *= 1.6
        if b <= 10.0:
            raise FlashError(f"P{prop.upper()} flash: target below the low-temperature limit")
    else:
        raise FlashError(f"P{prop.upper()} flash: could not bracket temperature")
    # Solve inside the bracket with the endpoint values already in hand. Re-evaluating the ends (as brentq does) can
    # flip a sign near a phase boundary, because the warm-started K-values differ from the bracketing pass.
    (lo, glo), (hi, ghi) = sorted([(a, ga), (b, gb)])
    side = 0
    T = 0.5 * (lo + hi)
    for _ in range(200):
        T = hi - ghi * (hi - lo) / (ghi - glo) if ghi != glo else 0.5 * (lo + hi)
        if not lo < T < hi:
            T = 0.5 * (lo + hi)
        gT = g(T)
        if abs(hi - lo) < 1e-8 or abs(gT) < tol * scale:
            break
        if np.sign(gT) == np.sign(glo):
            lo, glo = T, gT
            if side == -1:
                ghi *= 0.5          # Illinois step keeps the far end from stalling
            side = -1
        else:
            hi, ghi = T, gT
            if side == 1:
                glo *= 0.5
            side = 1
    if prop == "h" and abs(gT) > 1.0:
        # The bracket closed on a jump: h(T) skips the two-phase band, which happens for near-azeotropic mixtures
        # whose bubble and dew points are a fraction of a kelvin apart (every K ~ 1, so a TP flash lands on one
        # phase or the other). Solve on vapor fraction between the bubble and dew points instead.
        return _ph_two_phase(pm, P, target, z, T)
    return tp_flash(pm, T, P, z, K0=cache["K"])


def _ph_two_phase(pm, P, H, z, T_near):
    try:
        lo, hi = pvf_flash(pm, P, 1e-9, z), pvf_flash(pm, P, 1.0 - 1e-9, z)
        h_lo, h_hi = lo.h(pm), hi.h(pm)
        if h_lo <= H <= h_hi:
            vf = brentq(lambda v: pvf_flash(pm, P, v, z).h(pm) - H, 1e-9, 1.0 - 1e-9, xtol=1e-12)
            return pvf_flash(pm, P, vf, z)
    except (FlashError, ValueError):
        pass
    # bubble and dew points coincide (an azeotrope): the mixture boils like a pure component at T_near,
    # so h is linear in vapor fraction with x = y = z
    hL = float(pm.h_liq(T_near, P, z))
    hV = float(pm.h_vap(T_near, P, z))
    beta = min(max((H - hL) / (hV - hL), 0.0), 1.0)
    return FlashResult(float(T_near), float(P), beta, z.copy(), z.copy(), z.copy(), "VL" if 0 < beta < 1 else ("V" if beta >= 1 else "L"), 0, None)


def ph_flash(pm, P, H, z, T0=None) -> FlashResult:
    """Flash at P (Pa) and molar enthalpy H (J/mol)."""
    return _state_flash(pm, P, H, z, "h", T0)


def ps_flash(pm, P, S, z, T0=None) -> FlashResult:
    """Flash at P (Pa) and molar entropy S (J/mol-K)."""
    return _state_flash(pm, P, S, z, "s", T0)

"""DSTWU - shortcut distillation design (Winn - Underwood - Gilliland, Kirkbride feed location).

Given light-key / heavy-key recoveries in the distillate and a reflux ratio (or a number of
stages), estimate: minimum stages (Winn, or Fenske), minimum reflux (Underwood), actual stages
(Gilliland), feed stage (Kirkbride), condenser/reboiler duties and product temperatures.

    Winn:        K_i = beta_i * K_HK^phi_i   ->   N_min = ln[(d/b)_LK (b/d)_HK^phi_LK] / ln beta_LK
    Fenske:      N_min = ln[(d/b)_LK (b/d)_HK] / ln alpha_LK,HK
    Underwood:   sum alpha_i z_i / (alpha_i - theta) = 1 - q ;  R_min + 1 = sum alpha_i x_iD / (alpha_i - theta)
    Gilliland:   Y = (N - N_min)/(N + 1) = f(X),  X = (R - R_min)/(R + 1)
    Kirkbride:   N_R/N_S = [ (z_HK/z_LK) (x_LK,B / x_HK,D)^2 (B/D) ]^0.206

Stage counting follows Aspen: stages include the condenser and the reboiler.
Relative volatilities for Underwood are geometric means of the top and bottom values (the
"square root" average).
"""

from __future__ import annotations

import numpy as np
from scipy.optimize import brentq

from ..props.flash import bubble_T, dew_T, pvf_flash, tp_flash
from .base import Block, BlockError, register


def gilliland(X, method="liddle"):
    """Gilliland correlation Y(X)."""
    X = np.asarray(X, dtype=float)
    if method == "eduljee":
        return 0.75 * (1.0 - X ** 0.5668)
    if method == "molokanov":
        return 1.0 - np.exp((1 + 54.4 * X) / (11 + 117.2 * X) * (X - 1) / np.sqrt(X))
    # Liddle (1968) three-piece fit of Gilliland's curve
    return np.where(X < 0.01, 1.0 - 18.5715 * X,
                    np.where(X < 0.9, 0.545827 - 0.591422 * X + 0.002743 / np.maximum(X, 1e-12),
                             0.16595 - 0.16595 * X))


@register
class DSTWU(Block):
    """Specs: ``LK``, ``HK`` (component ids), ``rec_LK``, ``rec_HK`` (fraction of each key's feed that
    goes to the distillate), one of ``reflux_ratio`` (> 0: the value; < -1: that many times R_min)
    or ``n_stages``; ``P_cond``, ``P_reb`` (pressures); ``condenser`` = "total" | "partial-vapor";
    ``nmin_method`` = "winn" (Aspen) | "fenske"; ``gilliland`` = "liddle" | "eduljee" | "molokanov"."""

    type_name = "DSTWU"
    category = "Columns"
    icon = "column"
    inlet_ports = ("feed",)
    outlet_ports = ("distillate", "bottoms")

    def run(self):
        pm = self.pm
        s = self.spec
        u = self.fs.units
        flows, H_in, P_in, T_in = self.feed_totals()
        Ftot = flows.sum()
        z = flows / Ftot
        C = len(flows)
        LK, HK = self.comps.idx(s["LK"]), self.comps.idx(s["HK"])
        rLK, rHK = float(s["rec_LK"]), float(s["rec_HK"])
        Pc = u.to_si(s.get("P_cond", s.get("P")), "pressure")
        Pr = u.to_si(s.get("P_reb", s.get("P_cond", s.get("P"))), "pressure")
        total = str(s.get("condenser", "total")).lower().startswith("total")
        nmin_method = str(s.get("nmin_method", "winn")).lower()
        gmethod = str(s.get("gilliland", "liddle")).lower()
        Pm = 0.5 * (Pc + Pr)
        # --- initial split by volatility
        Tb_feed = bubble_T(pm, Pm, z)
        K_feed = np.asarray(pm.K_wilson(Tb_feed, Pm))
        if K_feed[LK] <= K_feed[HK]:
            raise BlockError(f"{self.name}: light key {s['LK']} is not more volatile than heavy key {s['HK']}")
        d = np.where(K_feed > K_feed[LK], flows * 0.9999, np.where(K_feed < K_feed[HK], flows * 1e-4, flows * 0.5))
        d[LK], d[HK] = rLK * flows[LK], rHK * flows[HK]
        Nmin = None
        for it in range(50):
            b = flows - d
            xD, xB = d / d.sum(), b / b.sum()
            TD = bubble_T(pm, Pc, xD) if total else dew_T(pm, Pc, xD)
            TB = bubble_T(pm, Pr, xB)
            rD = tp_flash(pm, TD, Pc, xD)
            KD = rD.K if rD.K is not None else np.asarray(pm.K_wilson(TD, Pc))
            rB = tp_flash(pm, TB, Pr, xB)
            KB = rB.K if rB.K is not None else np.asarray(pm.K_wilson(TB, Pr))
            # saturated K values of the product phases
            KD = np.exp(pm.lnK(TD, Pc, xD, _norm(KD * xD)) if total else pm.lnK(TD, Pc, _norm(xD / KD), xD))
            KB = np.exp(pm.lnK(TB, Pr, xB, _norm(KB * xB)))
            if nmin_method == "fenske":
                alpha = np.sqrt((KD / KD[HK]) * (KB / KB[HK]))
                Nm = np.log((d[LK] / b[LK]) * (b[HK] / d[HK])) / np.log(alpha[LK])
                ratio = alpha ** Nm * (d[HK] / b[HK])
            else:
                phi = np.log(KD / KB) / np.log(KD[HK] / KB[HK])
                beta = KD / KD[HK] ** phi
                Nm = np.log((d[LK] / b[LK]) * (b[HK] / d[HK]) ** phi[LK]) / np.log(beta[LK])
                ratio = beta ** Nm * (d[HK] / b[HK]) ** phi
            d_new = flows * ratio / (1.0 + ratio)
            d_new[LK], d_new[HK] = rLK * flows[LK], rHK * flows[HK]
            if Nmin is not None and abs(Nm - Nmin) < 1e-8 and np.allclose(d_new, d, rtol=1e-8, atol=1e-14):
                d = d_new
                Nmin = Nm
                break
            d, Nmin = d_new, Nm
        b = flows - d
        D, B = d.sum(), b.sum()
        xD, xB = d / D, b / B
        # --- feed quality at the average column pressure
        h_feed = H_in / Ftot
        try:
            hL = pvf_flash(pm, Pm, 0.0, z).h(pm)
            hV = pvf_flash(pm, Pm, 1.0, z).h(pm)
            q = (hV - h_feed) / (hV - hL)
        except Exception:
            q = 1.0
        # --- Underwood
        alpha = np.sqrt((KD / KD[HK]) * (KB / KB[HK]))

        def und(theta):
            return np.sum(alpha * z / (alpha - theta)) - (1.0 - q)

        a_lo, a_hi = alpha[HK], alpha[LK]
        theta = brentq(und, a_lo * (1 + 1e-9) + 1e-12, a_hi * (1 - 1e-9))
        Rmin = float(np.sum(alpha * xD / (alpha - theta)) - 1.0)
        # --- reflux / stages
        if s.get("reflux_ratio") is not None:
            R = float(s["reflux_ratio"])
            if R < 0:
                if R > -1:
                    raise BlockError(f"{self.name}: a negative reflux ratio means a multiple of R_min and must be < -1")
                R = -R * Rmin
            if R <= Rmin:
                raise BlockError(f"{self.name}: reflux ratio {R:.4g} is at or below the minimum {Rmin:.4g}")
            X = (R - Rmin) / (R + 1)
            Y = float(gilliland(X, gmethod))
            N = (Nmin + Y) / (1 - Y)
        elif s.get("n_stages") is not None:
            N = float(s["n_stages"])
            if N <= Nmin:
                raise BlockError(f"{self.name}: {N} stages is not more than the minimum {Nmin:.3f}")
            Yt = (N - Nmin) / (N + 1)
            X = brentq(lambda X: float(gilliland(X, gmethod)) - Yt, 1e-9, 1 - 1e-9)
            R = (X + Rmin) / (1 - X)
        else:
            raise BlockError(f"{self.name}: give reflux_ratio or n_stages")
        # Aspen counts the condenser and reboiler as stages; Gilliland's N counts theoretical stages
        # including the reboiler. A total condenser is one more "stage" for bookkeeping.
        N_aspen = N
        # --- Kirkbride feed location
        ratio_k = ((z[HK] / z[LK]) * (xB[LK] / xD[HK]) ** 2 * (B / D)) ** 0.206
        N_above = N_aspen * ratio_k / (1 + ratio_k)
        feed_stage = N_above + 1
        # --- products and duties
        dist, bot = self.outlet("distillate"), self.outlet("bottoms")
        if total:
            dist.set_phase(d, TD, Pc, "L", other=_norm(KD * xD))
        else:
            dist.set_phase(d, TD, Pc, "V", other=_norm(xD / KD))
        bot.set_phase(b, TB, Pr, "L", other=_norm(KB * xB))
        Tdew_top = dew_T(pm, Pc, xD, T0=TD) if total else TD
        hV_top = float(pm.h_vap(Tdew_top, Pc, xD))
        hL_top = float(pm.h_liq(TD, Pc, xD)) if total else float(pm.h_liq(TD, Pc, _norm(xD / KD)))
        # condenser: (R+1)D of saturated vapor -> R*D reflux + D product
        if total:
            Qc = (R + 1) * D * (hL_top - hV_top)
        else:
            Qc = R * D * (hL_top - hV_top)
        Qr = dist.H + bot.H - H_in - Qc
        # reflux ratio profile (Aspen's DSTWU "Reflux Ratio Profile" sheet)
        Ns = np.linspace(np.ceil(Nmin + 1), max(np.ceil(Nmin + 1) + 10, 3 * Nmin), 25)
        Rs = []
        for n_ in Ns:
            Yt = (n_ - Nmin) / (n_ + 1)
            try:
                X_ = brentq(lambda X: float(gilliland(X, gmethod)) - Yt, 1e-9, 1 - 1e-9)
                Rs.append((X_ + Rmin) / (1 - X_))
            except ValueError:
                Rs.append(float("nan"))
        self.tables["reflux_profile"] = dict(stages=Ns, reflux_ratio=np.array(Rs))
        self.set_result("Rmin", "Minimum reflux ratio", Rmin, "fraction")
        self.set_result("R", "Actual reflux ratio", R, "fraction")
        self.set_result("Nmin", "Minimum number of stages", float(Nmin), "fraction")
        self.set_result("N", "Number of actual stages", float(N_aspen), "fraction")
        self.set_result("feed_stage", "Feed stage", float(feed_stage), "fraction")
        self.set_result("N_above", "Number of actual stages above feed", float(N_above), "fraction")
        self.set_result("reboiler_duty", "Reboiler heat duty", Qr, "duty")
        self.set_result("condenser_duty", "Condenser heat duty", Qc, "duty")
        self.set_result("T_D", "Distillate temperature", TD, "temperature")
        self.set_result("T_B", "Bottom temperature", TB, "temperature")
        self.set_result("D_F", "Distillate to feed fraction", D / Ftot, "fraction")
        self.set_result("q", "Feed quality q", q, "fraction")
        self.set_result("theta", "Underwood theta", float(theta), "fraction")
        self.tables["volatility"] = dict(components=list(self.comps.ids), K_top=KD, K_bottom=KB, alpha=alpha,
                                         d=d, b=b)
        self.status = "ok"

    def duty_keys(self):
        return ("condenser_duty", "reboiler_duty")


def _norm(v):
    v = np.maximum(np.asarray(v, float), 0)
    return v / v.sum()

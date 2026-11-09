"""RadFrac - rigorous equilibrium-stage columns (distillation, absorbers, strippers, reboiled absorbers).

Formulation (Naphtali-Sandholm "simultaneous correction"): for every stage j the unknowns are the
component liquid flows l_ij, component vapor flows v_ij and the temperature T_j, and the equations
are the MESH equations:

    M  material balance     (1+sL_j) l_j + (1+sV_j) v_j - l_{j-1} - v_{j+1} - f_j = 0
    E  equilibrium          y_j - K_j(T_j, P_j, x_j, y_j) x_j = 0          (x = l/L, y = v/V)
       (Murphree:           y_j - E K_j x_j - (1-E) y_{j+1} = 0)
    S  summation is built in (x and y are normalized flows)
    H  enthalpy balance     Lout h_L + Vout h_V - L_{j-1} h_L,j-1 - V_{j+1} h_V,j+1 - H_F,j - Q_j = 0

All N(2C+1) equations are solved at once with Newton's method. Thermodynamic derivatives are only
needed stage-by-stage, so they are obtained with one batched finite-difference sweep over all stages;
the rest of the Jacobian is assembled analytically. This mirrors what Aspen's RadFrac does with its
nested loops (outer loop: thermodynamics; inner loop: balances; middle loop: specs), but in one step.

Stage numbering follows Aspen: stage 1 is the top (the condenser, if there is one) and stage N is the
bottom (the reboiler, if there is one).

Operating specs (``specs={...}``) - a column with a condenser and a reboiler needs two, one with only a
reboiler needs one, one with neither needs none:

    reflux_ratio, distillate_rate, distillate_to_feed, bottoms_rate, bottoms_to_feed, boilup_ratio,
    reboiler_duty, condenser_duty,
    ("recovery", comp, product) : fraction of the component's total feed leaving in "distillate"/"bottoms"
    ("purity", comp, product)   : mole fraction of the component in that product
    ("comp_flow", comp, product): component flow in that product
    mass basis (Aspen D-BASIS / RR-BASIS = MASS): distillate_mass_rate, bottoms_mass_rate, reflux_ratio_mass,
    ("mass_purity", comp, product), ("mass_comp_flow", comp, product)
"""

from __future__ import annotations

import numpy as np

from ..props.flash import FlashError, bubble_T, dew_T, ph_flash
from .base import Block, BlockError, register

H_SCALE = 1.0e4   # J/mol, for scaling enthalpy balances


def _thomas(a, b, c, d):
    """Solve tridiagonal systems column-wise. a: sub-diagonal (N,C) (a[0] unused), b diag (N,C),
    c super-diagonal (N,C) (c[-1] unused), d rhs (N,C)."""
    N = b.shape[0]
    cp = np.zeros_like(b)
    dp = np.zeros_like(b)
    cp[0] = c[0] / b[0]
    dp[0] = d[0] / b[0]
    for j in range(1, N):
        m = b[j] - a[j] * cp[j - 1]
        cp[j] = c[j] / m if j < N - 1 else 0.0
        dp[j] = (d[j] - a[j] * dp[j - 1]) / m
    x = np.zeros_like(b)
    x[-1] = dp[-1]
    for j in range(N - 2, -1, -1):
        x[j] = dp[j] - cp[j] * x[j + 1]
    return x


@register
class RadFrac(Block):
    type_name = "RadFrac"
    category = "Columns"
    icon = "column"
    inlet_ports = ("feed",)
    outlet_ports = ("distillate", "bottoms")

    def __init__(self, name, **spec):
        spec.setdefault("condenser", "total")
        spec.setdefault("reboiler", "kettle")
        spec.setdefault("heaters", {})
        spec.setdefault("specs", {})
        spec.setdefault("feed_convention", "above-stage")
        super().__init__(name, **spec)
        self.solution = None   # warm start for the next run

    # ------------------------------------------------------------------------------------------
    # setup
    def _setup(self):
        s = self.spec
        u = self.fs.units
        N = int(s["n_stages"])
        C = len(self.comps)
        cond = str(s["condenser"]).lower()
        if cond in ("partial", "partial-v", "partial-vapor"):
            cond = "partial"
        if cond not in ("total", "partial", "none"):
            raise BlockError(f"{self.name}: condenser must be total, partial-vapor or none")
        reb = str(s["reboiler"]).lower()
        if reb not in ("kettle", "none"):
            raise BlockError(f"{self.name}: reboiler must be kettle or none")
        self.N, self.C, self.cond, self.reb = N, C, cond, reb
        # pressures
        P1 = u.to_si(s["P_top"], "pressure")
        if s.get("dP_column") is not None:
            dP = u.to_si(s["dP_column"], "delta_pressure")
            stage_dp = dP / max(N - 1, 1)
        elif s.get("dP_stage") is not None:
            stage_dp = u.to_si(s["dP_stage"], "delta_pressure")
        else:
            stage_dp = 0.0
        P = P1 + stage_dp * np.arange(N)
        if cond != "none" and s.get("dP_condenser") is not None:
            P[1:] += u.to_si(s["dP_condenser"], "delta_pressure")
        self.P = P
        # feeds: split each feed into its vapor and liquid parts at the stage pressure
        feeds = s.get("feeds", {})
        f = np.zeros((N, C))
        HF = np.zeros(N)
        fv = np.zeros((N, C))
        fl = np.zeros((N, C))
        self.feed_info = []
        conv_default = str(s.get("feed_convention", "above-stage")).lower()
        for st in self.inlets.get("feed", []):
            if st.name not in feeds:
                raise BlockError(f"{self.name}: no stage given for feed {st.name} (feeds={{'{st.name}': stage}})")
            val = feeds[st.name]
            stage, conv = (val if isinstance(val, tuple) else (val, conv_default))
            conv = str(conv).lower()
            j = int(stage) - 1
            if not 0 <= j < N:
                raise BlockError(f"{self.name}: feed {st.name} stage {stage} outside 1..{N}")
            if st.F <= 0:
                continue
            z = st.z
            try:
                fr = ph_flash(self.pm, P[j], st.h, z, T0=st.T) if abs(st.P - P[j]) > 1.0 else st.state
            except Exception:
                fr = st.state
            if fr.phase == "VL":
                vpart = st.F * fr.beta * fr.y
                lpart = st.F * (1 - fr.beta) * fr.x
                hv = float(self.pm.h_vap(fr.T, P[j], fr.y))
                hl = float(self.pm.h_liq(fr.T, P[j], fr.x))
            elif fr.phase == "V":
                vpart, lpart = st.flows.copy(), 0 * st.flows
                hv, hl = st.h, 0.0
            else:
                vpart, lpart = 0 * st.flows, st.flows.copy()
                hv, hl = 0.0, st.h
            jv = j - 1 if (conv.startswith("above") and j > 0) else j
            f[j] += lpart
            f[jv] += vpart
            fl[j] += lpart
            fv[jv] += vpart
            HF[j] += lpart.sum() * hl
            HF[jv] += vpart.sum() * hv
            self.feed_info.append(dict(stream=st.name, stage=j + 1, convention=conv, vf=fr.beta))
        if f.sum() <= 0:
            raise BlockError(f"{self.name}: no feed")
        self.f, self.HF, self.fv, self.fl = f, HF, fv, fl
        self.Ftot = f.sum()
        self.Fi = f.sum(axis=0)
        self.Fs = self.Ftot
        # stage duties (known) - heaters are {stage (1-based): duty}
        Q = np.zeros(N)
        for stage, q in (s.get("heaters") or {}).items():
            j = int(stage) - 1
            if not 0 <= j < N:
                raise BlockError(f"{self.name}: heater stage {stage} outside 1..{N}")
            Q[j] += u.to_si(q, "duty")
        self.Qknown = Q
        # Murphree efficiencies
        E = np.ones(N)
        me = s.get("murphree") or {}
        if isinstance(me, (int, float)):
            me = {"all": me}
        if "all" in me:
            E[:] = float(me["all"])
        for k, v in me.items():
            if k != "all":
                E[int(k) - 1] = float(v)
        if cond == "total":
            E[0] = 1.0
        E[N - 1] = E[N - 1] if reb == "none" else 1.0
        self.E = E
        self._setup_specs()

    def _setup_specs(self):
        u = self.fs.units
        raw = dict(self.spec.get("specs") or {})
        N = self.N
        self.RR = None
        self.s1_var = False
        self.Qc_spec = None
        self.Qr_spec = None
        specs = []
        for key, val in raw.items():
            name = key if isinstance(key, str) else key[0]
            if name in ("reflux_ratio", "reflux_ratio_mass") and self.cond == "total":
                # reflux and distillate share one composition with a total condenser, so mass RR = molar RR
                self.RR = float(val)
            elif name == "condenser_duty":
                self.Qc_spec = u.to_si(val, "duty")
            elif name == "reboiler_duty":
                self.Qr_spec = u.to_si(val, "duty")
            else:
                specs.append((key, val))
        if self.cond == "none" and self.Qc_spec is not None:
            raise BlockError(f"{self.name}: no condenser, so no condenser_duty spec")
        if self.reb == "none" and self.Qr_spec is not None:
            raise BlockError(f"{self.name}: no reboiler, so no reboiler_duty spec")
        # which equation rows are free for specs?
        slots = []
        if self.cond == "total":
            if self.RR is None:
                self.s1_var = True
                slots.append("extra")
        elif self.cond == "partial":
            if self.Qc_spec is None:
                slots.append(0)
        if self.reb == "kettle" and self.Qr_spec is None:
            slots.append(N - 1)
        # a condenser-duty spec on a total condenser goes into the extra row
        if self.cond == "total" and self.Qc_spec is not None:
            if "extra" not in slots:
                raise BlockError(f"{self.name}: reflux_ratio and condenser_duty both fix the condenser")
            slots.remove("extra")
            self.extra_is_cond_energy = True
        else:
            self.extra_is_cond_energy = False
        if len(specs) != len(slots):
            need = len(slots) + (0 if self.RR is None or self.cond != "total" else 0)
            raise BlockError(
                f"{self.name}: wrong number of operating specs. This configuration "
                f"(condenser={self.cond}, reboiler={self.reb}) needs {need} more besides "
                f"{'reflux ratio/duties' if (self.RR or self.Qc_spec or self.Qr_spec) else 'none'}; got {[k for k, _ in specs]}")
        self.spec_slots = list(zip(slots, specs))
        # value conversion
        conv = []
        for slot, (key, val) in self.spec_slots:
            if isinstance(key, tuple):
                kind, comp, prod = key
                idx = self.comps.idx(comp)
                if kind in ("recovery", "purity", "mass_purity"):
                    v = float(val)
                elif kind == "mass_comp_flow":
                    v = u.to_si(val, "mass_flow")
                else:
                    v = u.to_si(val, "mole_flow")
                conv.append((slot, kind, idx, prod, v))
            else:
                if key in ("distillate_rate", "bottoms_rate"):
                    v = u.to_si(val, "mole_flow")
                elif key in ("distillate_mass_rate", "bottoms_mass_rate"):
                    v = u.to_si(val, "mass_flow")
                elif key == "stage_temperature":
                    raise BlockError("use ('temperature', stage) for temperature specs")
                else:
                    v = float(val)
                conv.append((slot, key, None, None, v))
        self.spec_conv = conv

    # ------------------------------------------------------------------------------------------
    # variable packing
    def _pack(self, l, v, T, s1):
        x = np.concatenate([np.concatenate([l, v, T[:, None]], axis=1).ravel(), [s1] if self.s1_var else []])
        return x

    def _unpack(self, x):
        N, C = self.N, self.C
        w = 2 * C + 1
        body = x[:N * w].reshape(N, w)
        l = body[:, :C]
        v = body[:, C:2 * C]
        T = body[:, 2 * C]
        s1 = x[-1] if self.s1_var else (1.0 / self.RR if (self.cond == "total" and self.RR) else 0.0)
        return l, v, T, s1

    # ------------------------------------------------------------------------------------------
    # thermodynamics on all stages at once
    def _local(self, l, v, T):
        """Equilibrium residuals e (N,C) and phase enthalpies hL, hV (N,)."""
        pm = self.pm
        L = l.sum(axis=1)
        V = v.sum(axis=1)
        x = l / np.maximum(L, 1e-300)[:, None]
        if self.cond == "total":
            yv = v.copy()
            ysum = np.maximum(v.sum(axis=1), 1e-300)
            y = v / ysum[:, None]
            y[0] = v[0] / max(v[0].sum(), 1e-300)
        else:
            y = v / np.maximum(V, 1e-300)[:, None]
        lnK = pm.lnK(T, self.P, x, y)
        K = np.exp(np.clip(lnK, -69, 69))
        yy = y.copy()
        if self.cond == "total":
            yy[0] = v[0]   # hypothetical vapor in equilibrium with the condenser liquid: not normalized
        e = yy - self.E[:, None] * K * x
        hL = pm.h_liq(T, self.P, x)
        hV = pm.h_vap(T, self.P, y)
        return e, hL, hV, K, x, y

    def _residual(self, xvec, return_parts=False):
        N, C = self.N, self.C
        l, v, T, s1 = self._unpack(xvec)
        e, hL, hV, K, x, y = self._local(l, v, T)
        L = l.sum(axis=1)
        V = v.sum(axis=1)
        sL = np.zeros(N)
        if self.cond == "total":
            sL[0] = s1
        Fs = self.Fs
        # material balances
        M = (1 + sL)[:, None] * l + v - self.f
        if self.cond == "total":
            M[0] -= v[0]           # condenser: no vapor leaves stage 1 (v_0 is hypothetical)
        M[1:] -= l[:-1]
        M[:-1] -= v[1:]
        # Murphree coupling to the vapor entering from below
        Eres = e.copy()
        if np.any(self.E < 1):
            ynext = np.zeros_like(y)
            ynext[:-1] = y[1:]
            Eres[:-1] -= ((1 - self.E) [:, None] * ynext)[:-1]
        # energy balances
        Vout = V.copy()
        if self.cond == "total":
            Vout[0] = 0.0
        Hout = (1 + sL) * L * hL + Vout * hV
        Hin = self.HF.copy()
        Hin[1:] += L[:-1] * hL[:-1]
        Hin[:-1] += V[1:] * hV[1:]
        Hbal = Hout - Hin
        Q = self.Qknown.copy()
        if self.Qc_spec is not None:
            Q[0] += self.Qc_spec
        if self.Qr_spec is not None:
            Q[N - 1] += self.Qr_spec
        S = (Hbal - Q) / (Fs * H_SCALE)
        if self.cond == "total":
            S0_energy = S[0]
            S[0] = v[0].sum() - 1.0          # condenser temperature = bubble point of the reflux
        extra = []
        spec_vals = self._spec_residuals(l, v, T, s1)
        for (slot, *_), r in zip(self.spec_conv, spec_vals):
            if slot == "extra":
                extra.append(r)
            else:
                S[slot] = r
        if self.s1_var:
            if self.extra_is_cond_energy:
                extra = [S0_energy]
            if len(extra) != 1:
                raise BlockError(f"{self.name}: internal spec bookkeeping error")
        R = np.concatenate([np.concatenate([M / Fs, Eres, S[:, None]], axis=1).ravel(), extra])
        if return_parts:
            return R, dict(e=e, hL=hL, hV=hV, K=K, x=x, y=y, L=L, V=V, Hbal=Hbal, sL=sL)
        return R

    def _products(self, l, v, s1):
        if self.cond == "total":
            d = s1 * l[0]
        else:
            d = v[0].copy()
        b = l[-1].copy()
        return d, b

    def _spec_residuals(self, l, v, T, s1):
        d, b = self._products(l, v, s1)
        D, B = d.sum(), b.sum()
        Fs, Ft = self.Fs, self.Ftot
        MW = self.comps.MW / 1000.0                     # kg/mol
        Fm = float(self.Fi @ MW)                        # feed mass flow, kg/s (scale for mass specs)
        out = []
        for slot, kind, idx, prod, val in self.spec_conv:
            if kind == "reflux_ratio":
                r = (l[0].sum() - val * D) / Fs
            elif kind == "distillate_rate":
                r = (D - val) / Fs
            elif kind == "distillate_to_feed":
                r = D / Ft - val
            elif kind == "bottoms_rate":
                r = (B - val) / Fs
            elif kind == "bottoms_to_feed":
                r = B / Ft - val
            elif kind == "distillate_mass_rate":
                r = (float(d @ MW) - val) / Fm
            elif kind == "bottoms_mass_rate":
                r = (float(b @ MW) - val) / Fm
            elif kind == "boilup_ratio":
                r = (v[-1].sum() - val * B) / Fs
            elif kind in ("recovery", "purity", "comp_flow", "mass_purity", "mass_comp_flow"):
                p = d if prod.startswith("dist") else b
                if kind == "recovery":
                    r = p[idx] / max(self.Fi[idx], 1e-300) - val
                elif kind == "purity":
                    r = p[idx] / max(p.sum(), 1e-300) - val
                elif kind == "mass_purity":
                    r = p[idx] * MW[idx] / max(float(p @ MW), 1e-300) - val
                elif kind == "mass_comp_flow":
                    r = (p[idx] * MW[idx] - val) / Fm
                else:
                    r = (p[idx] - val) / Fs
            elif kind == "temperature":
                raise BlockError("temperature specs are not supported yet")
            else:
                raise BlockError(f"{self.name}: unknown spec {kind!r}")
            out.append(r)
        return out

    # ------------------------------------------------------------------------------------------
    # Jacobian: batched finite differences for the stage-local thermodynamics + analytic assembly
    def _jacobian(self, xvec, R0):
        N, C = self.N, self.C
        w = 2 * C + 1
        n = len(xvec)
        l, v, T, s1 = self._unpack(xvec)
        e0, hL0, hV0, K0, x0, y0 = self._local(l, v, T)
        L = l.sum(axis=1)
        V = v.sum(axis=1)
        Fs = self.Fs
        dE = np.zeros((N, C, w))
        dhL = np.zeros((N, w))
        dhV = np.zeros((N, w))
        for k in range(w):
            lp, vp, Tp = l.copy(), v.copy(), T.copy()
            if k < C:
                h = 1e-7 * np.maximum(np.abs(l[:, k]), 1e-6 * np.maximum(L, 1e-12)) + 1e-30
                lp[:, k] += h
            elif k < 2 * C:
                kk = k - C
                h = 1e-7 * np.maximum(np.abs(v[:, kk]), 1e-6 * np.maximum(V, 1e-12)) + 1e-30
                vp[:, kk] += h
            else:
                h = np.full(N, 1e-4)
                Tp = T + h
            e1, hL1, hV1, *_ = self._local(lp, vp, Tp)
            dE[:, :, k] = (e1 - e0) / h[:, None]
            dhL[:, k] = (hL1 - hL0) / h
            dhV[:, k] = (hV1 - hV0) / h
        J = np.zeros((n, n))
        sL = np.zeros(N)
        if self.cond == "total":
            sL[0] = s1
        eye = np.eye(C)
        Vout = V.copy()
        if self.cond == "total":
            Vout[0] = 0.0
        HS = Fs * H_SCALE
        for j in range(N):
            r0 = j * w
            c0 = j * w
            # --- M rows
            J[r0:r0 + C, c0:c0 + C] = (1 + sL[j]) * eye / Fs
            if not (self.cond == "total" and j == 0):
                J[r0:r0 + C, c0 + C:c0 + 2 * C] = eye / Fs
            if j > 0:
                J[r0:r0 + C, (j - 1) * w:(j - 1) * w + C] = -eye / Fs
            if j < N - 1:
                J[r0:r0 + C, (j + 1) * w + C:(j + 1) * w + 2 * C] = -eye / Fs
            # --- E rows
            J[r0 + C:r0 + 2 * C, c0:c0 + w] = dE[j]
            if self.E[j] < 1 and j < N - 1:
                Vn = V[j + 1]
                yn = y0[j + 1]
                J[r0 + C:r0 + 2 * C, (j + 1) * w + C:(j + 1) * w + 2 * C] = -(1 - self.E[j]) * (eye - yn[:, None]) / Vn
            # --- S row (energy balance; replaced below where needed)
            rS = r0 + 2 * C
            row = np.zeros(n)
            row[c0:c0 + C] = (1 + sL[j]) * (hL0[j] + L[j] * dhL[j, :C])
            row[c0 + 2 * C] = (1 + sL[j]) * L[j] * dhL[j, 2 * C] + Vout[j] * dhV[j, 2 * C]
            if Vout[j] > 0 or not (self.cond == "total" and j == 0):
                row[c0 + C:c0 + 2 * C] += hV0[j] + V[j] * dhV[j, C:2 * C] if not (self.cond == "total" and j == 0) else 0.0
            if j > 0:
                cj = (j - 1) * w
                row[cj:cj + C] -= hL0[j - 1] + L[j - 1] * dhL[j - 1, :C]
                row[cj + 2 * C] -= L[j - 1] * dhL[j - 1, 2 * C]
            if j < N - 1:
                cj = (j + 1) * w
                row[cj + C:cj + 2 * C] -= hV0[j + 1] + V[j + 1] * dhV[j + 1, C:2 * C]
                row[cj + 2 * C] -= V[j + 1] * dhV[j + 1, 2 * C]
            if self.cond == "total" and j == 0 and self.s1_var:
                row[n - 1] = L[0] * hL0[0]
            energy_row = row / HS
            if self.cond == "total" and j == 0:
                if self.extra_is_cond_energy:
                    J[n - 1] = energy_row
                J[rS] = 0.0
                J[rS, c0 + C:c0 + 2 * C] = 1.0     # sum v_0 = 1
            else:
                J[rS] = energy_row
            if self.cond == "total" and j == 0 and self.s1_var:
                J[r0:r0 + C, n - 1] = l[0] / Fs   # d M_0 / d s1
        # --- spec rows by finite differences on the (cheap) spec functions
        spec_cols = list(range(0, w)) + list(range((N - 1) * w, N * w)) + ([n - 1] if self.s1_var else [])
        base = np.array(self._spec_residuals(l, v, T, s1))
        rows = []
        for slot, *_ in self.spec_conv:
            rows.append(n - 1 if slot == "extra" else slot * w + 2 * C)
        if rows:
            for r in rows:
                J[r] = 0.0
            for col in spec_cols:
                xp = xvec.copy()
                h = 1e-7 * max(abs(xp[col]), 1e-6 * Fs if col != n - 1 else 1e-6) + 1e-30
                xp[col] += h
                lp, vp, Tp, s1p = self._unpack(xp)
                vals = (np.array(self._spec_residuals(lp, vp, Tp, s1p)) - base) / h
                for r, dv in zip(rows, vals):
                    J[r, col] = dv
        return J

    # ------------------------------------------------------------------------------------------
    # initialization
    def _estimate_split(self):
        """Rough product split used only to start the solver."""
        N, C = self.N, self.C
        pm = self.pm
        Tmid = None
        z = self.Fi / self.Ftot
        Pm = float(self.P.mean())
        try:
            Tb, Td = bubble_T(pm, Pm, z), dew_T(pm, Pm, z)
            Tmid = 0.5 * (Tb + Td)
        except Exception:
            Tmid = float(np.sum(z * self.comps.Tb))
        K = np.asarray(pm.K_wilson(Tmid, Pm))
        # target distillate flow
        D = None
        for slot, kind, idx, prod, val in self.spec_conv:
            if kind == "distillate_rate":
                D = val
            elif kind == "distillate_to_feed":
                D = val * self.Ftot
            elif kind == "bottoms_rate":
                D = self.Ftot - val
            elif kind == "bottoms_to_feed":
                D = self.Ftot * (1 - val)
        Dm = None
        for slot, kind, idx, prod, val in self.spec_conv:
            if kind == "distillate_mass_rate":
                Dm = val
            elif kind == "bottoms_mass_rate":
                Dm = float(self.Fi @ self.comps.MW) / 1000.0 - val
        if D is None and Dm is not None:
            MWk = self.comps.MW / 1000.0
            D, rem = 0.0, Dm
            for i in np.argsort(-K):
                take = min(self.Fi[i], rem / MWk[i])
                D += take
                rem -= take * MWk[i]
                if rem <= 0:
                    break
        if D is None:
            D = float(np.sum(self.Fi[K > 1.0]))
            if D <= 0 or D >= self.Ftot:
                D = 0.5 * self.Ftot
        # fill the distillate with the most volatile components first
        order = np.argsort(-K)
        d = np.zeros(C)
        rem = D
        for i in order:
            take = min(self.Fi[i], rem)
            d[i] = take
            rem -= take
            if rem <= 0:
                break
        # soften the split so no component is exactly zero in either product
        d = np.clip(d, 1e-4 * self.Fi, 0.9999 * self.Fi)
        b = self.Fi - d
        return d, b, K, Tmid

    def _initialize(self):
        N, C = self.N, self.C
        pm = self.pm
        u = self.fs.units
        d, b, Kmid, Tmid = self._estimate_split()
        D, B = d.sum(), b.sum()
        # temperature profile
        test = self.spec.get("T_est") or {}
        if test:
            stages = sorted(int(k) for k in test)
            vals = [u.to_si(test[k] if k in test else test[str(k)], "temperature") for k in stages]
            T = np.interp(np.arange(1, N + 1), stages, vals)
        else:
            try:
                T1 = bubble_T(pm, self.P[0], d / D) if self.cond == "total" else dew_T(pm, self.P[0], d / D)
            except Exception:
                T1 = Tmid - 20
            try:
                TN = bubble_T(pm, self.P[-1], b / B)
            except Exception:
                TN = Tmid + 20
            if TN <= T1:
                TN = T1 + 30
            T = np.linspace(T1, TN, N)
        # flow profile (constant molar overflow)
        RR = self.RR
        if RR is None:
            for slot, kind, *_rest, val in self.spec_conv:
                if kind == "reflux_ratio":
                    RR = val
        L = np.zeros(N)
        V = np.zeros(N)
        FL = self.fl.sum(axis=1)
        FVv = self.fv.sum(axis=1)
        if self.cond != "none":
            RR = RR if RR is not None else 2.0
            L[0] = RR * D + FL[0]
            V[0] = D if self.cond == "partial" else 0.0
            vin = (L[0] + D - FL[0]) if self.cond == "total" else (L[0] + D - FL[0])
            V[1] = max(vin - FVv[0], 1e-3 * self.Ftot) if N > 1 else 0.0
            for j in range(1, N):
                L[j] = L[j - 1] + FL[j]
                if j + 1 < N:
                    V[j + 1] = max(V[j] - FVv[j], 1e-3 * self.Ftot)
            if self.reb == "kettle":
                L[N - 1] = B
                V[N - 1] = max(L[N - 2] - B, 1e-3 * self.Ftot)
        else:
            # no condenser: build vapor up from the reboiler, liquid down from the top feeds
            if self.reb == "kettle":
                Vb = None
                for slot, kind, *_rest, val in self.spec_conv:
                    if kind == "boilup_ratio":
                        Vb = val * B
                if Vb is None and self.Qr_spec is not None:
                    try:
                        lam = float(pm.h_vap(T[-1], self.P[-1], b / B) - pm.h_liq(T[-1], self.P[-1], b / B))
                    except Exception:
                        lam = 3.0e4
                    lam = lam if lam > 5e3 else 3.0e4
                    Vb = self.Qr_spec / lam
                if Vb is None:
                    Vb = 0.5 * self.Ftot
                V[N - 1] = max(Vb, 1e-3 * self.Ftot)
            else:
                V[N - 1] = FVv[N - 1]
            for j in range(N - 2, -1, -1):
                V[j] = V[j + 1] + FVv[j]
            L[0] = FL[0]
            for j in range(1, N):
                L[j] = L[j - 1] + FL[j]
            if self.reb == "kettle":
                L[N - 2] = max(L[N - 2], V[N - 1] + 1e-3 * self.Ftot) if N > 1 else L[N - 2]
                L[N - 1] = max(L[N - 2] - V[N - 1] + FL[N - 1], 1e-3 * self.Ftot)
        L = np.maximum(L, 1e-3 * self.Ftot)
        V = np.maximum(V, 0.0)
        # bubble-point sweeps with fixed flows: tridiagonal component balances + stage bubble points
        K = np.asarray(pm.K_initial(T, self.P)) if True else None
        x = np.tile(self.Fi / self.Ftot, (N, 1))
        y = x.copy()
        s1 = D / max(L[0] - FL[0], 1e-12) if self.cond == "total" else 0.0
        for sweep in range(12):
            Vx = V.copy()
            if self.cond == "total":
                Vx[0] = 0.0
            S = K * (Vx / L)[:, None]              # stripping factors
            b_ = np.ones((N, C)) + S
            if self.cond == "total":
                b_[0] = 1.0 + s1
            a_ = -np.ones((N, C))
            c_ = np.zeros((N, C))
            c_[:-1] = -S[1:]
            lsol = _thomas(a_, b_, c_, self.f)
            lsol = np.maximum(lsol, 1e-30)
            x = lsol / lsol.sum(axis=1, keepdims=True)
            y_new = K * x
            y = y_new / y_new.sum(axis=1, keepdims=True)
            # temperature: two damped Newton steps on ln(sum K x) = 0 per stage
            for _ in range(2):
                K = np.exp(np.clip(pm.lnK(T, self.P, x, y), -69, 69))
                f0 = np.log(np.sum(K * x, axis=1))
                K2 = np.exp(np.clip(pm.lnK(T + 0.5, self.P, x, y), -69, 69))
                f1 = np.log(np.sum(K2 * x, axis=1))
                dfdT = (f1 - f0) / 0.5
                dT = np.where(np.abs(dfdT) > 1e-8, -f0 / np.where(np.abs(dfdT) > 1e-8, dfdT, 1.0), 0.0)
                T = T + np.clip(0.7 * dT, -15.0, 15.0)
                y = K * x
                y = y / y.sum(axis=1, keepdims=True)
            K = np.exp(np.clip(pm.lnK(T, self.P, x, y), -69, 69))
        l = x * L[:, None]
        v = y * V[:, None]
        if self.cond == "total":
            v[0] = K[0] * x[0]      # hypothetical vapor composition (sums to ~1)
        if self.cond == "total" and self.RR is not None:
            s1 = 1.0 / self.RR
        return self._pack(l, np.maximum(v, 1e-30), T, s1)

    # ------------------------------------------------------------------------------------------
    def _newton(self, x, maxit, tol):
        N, C = self.N, self.C
        w = 2 * C + 1
        n = len(x)
        is_T = np.zeros(n, bool)
        is_T[2 * C:N * w:w] = True
        is_flow = ~is_T
        if self.s1_var:
            is_flow[-1] = True
        history = []
        R = self._residual(x)
        norm = np.max(np.abs(R))
        dTmax = float(self.spec.get("max_dT", 15.0))
        for it in range(1, maxit + 1):
            history.append(norm)
            if norm < tol:
                return x, it - 1, history, True
            J = self._jacobian(x, R)
            try:
                dx = np.linalg.solve(J, -R)
            except np.linalg.LinAlgError:
                dx = np.linalg.lstsq(J, -R, rcond=None)[0]
            lam = 1.0
            maxT = np.max(np.abs(dx[is_T])) if is_T.any() else 0.0
            if maxT > dTmax:
                lam = dTmax / maxT
            accepted = False
            for _ in range(8):
                xn = x + lam * dx
                # keep flows positive: Naphtali's exponential damping for would-be negatives
                neg = is_flow & (xn <= 0)
                if np.any(neg):
                    old = np.maximum(x[neg], 1e-30)
                    xn[neg] = old * np.exp(np.clip(lam * dx[neg] / old, -50, 0))
                try:
                    Rn = self._residual(xn)
                    nn = np.max(np.abs(Rn))
                except Exception:
                    nn = np.inf
                if np.isfinite(nn) and (nn < norm * 1.5 or lam < 0.05):
                    accepted = True
                    break
                lam *= 0.5
            if not accepted:
                break
            x, R, norm = xn, Rn, nn
        history.append(norm)
        return x, maxit, history, norm < tol

    def run(self):
        self._setup()
        N, C = self.N, self.C
        tol = float(self.spec.get("tol", 1e-8))
        maxit = int(self.spec.get("max_iter", 60))
        x = None
        if self.solution is not None and len(self.solution) == N * (2 * C + 1) + (1 if self.s1_var else 0) \
                and not self.spec.get("reinit", False):
            x, it, hist, ok = self._newton(self.solution.copy(), maxit, tol)
            if not ok:
                x = None
        if x is None:
            x0 = self._initialize()
            x, it, hist, ok = self._newton(x0, maxit, tol)
        self.history = hist
        self.iterations = it
        if not ok:
            self.status = "error"
            raise BlockError(f"{self.name}: column did not converge (max scaled residual {hist[-1]:.3g} after "
                             f"{it} Newton iterations). Try T_est, different specs, or more iterations.")
        self.solution = x.copy()
        self._post(x)
        self.status = "ok"

    # ------------------------------------------------------------------------------------------
    def _post(self, x):
        N, C = self.N, self.C
        l, v, T, s1 = self._unpack(x)
        R, parts = self._residual(x, return_parts=True)
        L, V, hL, hV, K = parts["L"], parts["V"], parts["hL"], parts["hV"], parts["K"]
        xm, ym = parts["x"], parts["y"]
        Hbal = parts["Hbal"]
        # duties from the stage energy balances: Q_j = Hout - Hin
        Qc = float(Hbal[0]) if self.cond != "none" else None
        Qr = float(Hbal[N - 1] - self.Qknown[N - 1]) if self.reb == "kettle" else None
        if self.cond != "none":
            Qc = float(Hbal[0] - self.Qknown[0])
        d, b = self._products(l, v, s1)
        dist = self.outlet("distillate") if "distillate" in self.outlets else None
        bot = self.outlet("bottoms") if "bottoms" in self.outlets else None
        if dist is not None:
            if self.cond == "total":
                dist.set_phase(d, T[0], self.P[0], "L", other=ym[0])
            else:
                dist.set_phase(d, T[0], self.P[0], "V", other=xm[0])
        if bot is not None:
            bot.set_phase(b, T[-1], self.P[-1], "L", other=ym[-1])
        D, B = d.sum(), b.sum()
        self.set_result("T_top", "Top stage temperature", float(T[0]), "temperature")
        self.set_result("T_bottom", "Bottom stage temperature", float(T[-1]), "temperature")
        if Qc is not None:
            self.set_result("condenser_duty", "Condenser duty", Qc, "duty")
        if Qr is not None:
            self.set_result("reboiler_duty", "Reboiler duty", Qr, "duty")
        for stage, q in sorted((self.spec.get("heaters") or {}).items(), key=lambda kv: int(kv[0])):
            self.set_result(f"heater_{stage}", f"Stage {stage} heater duty", self.fs.units.to_si(q, "duty"), "duty")
        self.set_result("distillate", "Top product rate", D, "mole_flow")
        self.set_result("bottoms", "Bottoms rate", B, "mole_flow")
        MW = self.comps.MW / 1000.0
        self.set_result("distillate_mass", "Top product mass rate", float(d @ MW), "mass_flow")
        self.set_result("bottoms_mass", "Bottoms mass rate", float(b @ MW), "mass_flow")
        if self.cond != "none":
            self.set_result("reflux_ratio", "Reflux ratio (molar)", float(L[0] / D) if D > 0 else float("nan"), "fraction")
            self.set_result("reflux", "Reflux rate", float(L[0]), "mole_flow")
        if self.reb == "kettle":
            self.set_result("boilup_ratio", "Boilup ratio (molar)", float(V[-1] / B) if B > 0 else float("nan"), "fraction")
            self.set_result("boilup", "Boilup rate", float(V[-1]), "mole_flow")
        self.set_result("iterations", "Newton iterations", float(self.iterations), "fraction")
        ids = self.comps.ids
        self.tables["profiles"] = dict(stage=np.arange(1, N + 1), T=T.copy(), P=self.P.copy(),
                                       L=np.where(np.arange(N) == 0, (1 + (s1 if self.cond == "total" else 0)) * L, L),
                                       V=np.where((np.arange(N) == 0) & (self.cond == "total"), 0.0, V),
                                       x=xm.copy(), y=ym.copy(), K=K.copy(), components=list(ids),
                                       Q=np.array([self.Qknown[j] + (Qc if (j == 0 and Qc is not None) else 0.0) +
                                                   (Qr if (j == N - 1 and Qr is not None) else 0.0) for j in range(N)]))
        with np.errstate(divide="ignore", invalid="ignore"):
            self.tables["split_fractions"] = dict(components=list(ids), distillate=np.where(self.Fi > 0, d / self.Fi, 0.0),
                                                  bottoms=np.where(self.Fi > 0, b / self.Fi, 0.0),
                                                  d=d.copy(), b=b.copy(), feed=self.Fi.copy())
        self.tables["feeds"] = {"rows": self.feed_info}
        self.tables["convergence"] = {"residual": np.array(self.history)}

    def duty_keys(self):
        keys = ["condenser_duty", "reboiler_duty"] + [k for k in self.results if k.startswith("heater_")]
        return tuple(keys)

    def energy_balance_error(self) -> float:
        H_in = sum(s.H for s in self.all_inlets())
        H_out = sum(s.H for s in self.outlets.values())
        Q = sum(self.results[k][1] for k in self.duty_keys() if k in self.results and self.results[k][1] is not None)
        return (H_in + Q - H_out) / max(abs(H_in), abs(H_out), 1.0)

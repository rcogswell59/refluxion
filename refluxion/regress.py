"""Binary-parameter regression - a small version of Aspen's Data Regression System (DRS).

Fit NRTL ``b_ij`` (and optionally ``a_ij``) for chosen component pairs so the model's K-values
reproduce measured or reference VLE points (T, P, x, y). Typical uses:

* experimental Txy / Pxy data from a handbook
* calibrating against another simulator: Aspen saves each column's stage T, P, x and y in its
  ``.bkp`` file, so the parameters Aspen used can be recovered without seeing them
  (:func:`points_from_profile` turns a :class:`refluxion.io.aspen_bkp.ColumnProfile` into points)

The objective is the least-squares error in ln K for every component present above ``min_frac``
in both phases, which weights trace components (impurity specs!) as much as the bulk.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass
class VLEPoint:
    T: float              # K
    P: float              # Pa
    x: np.ndarray         # liquid mole fractions (flowsheet component order)
    y: np.ndarray         # vapor mole fractions
    weight: float = 1.0


@dataclass
class FitResult:
    params: dict                     # {(i, j): dict(a=, b=, c=)} in Refluxion's user-NRTL form
    rms: float                       # rms error in ln K over all residuals
    n_points: int
    n_residuals: int
    residuals: np.ndarray = field(repr=False, default=None)
    notes: list = field(default_factory=list)


def points_from_profile(fs, profile, comp_map: dict | None = None, min_frac: float = 0.0) -> list:
    """VLE points from an Aspen column profile. ``comp_map`` maps Aspen ids -> flowsheet ids
    (components not in the map are dropped and the rest renormalized)."""
    comp_map = comp_map or {c: c for c in profile.components if c in fs.comps.index}
    cols = [(profile.components.index(a), fs.comps.idx(b)) for a, b in comp_map.items() if a in profile.components]
    pts = []
    for j in range(len(profile.T_C)):
        x = np.zeros(len(fs.comps))
        y = np.zeros(len(fs.comps))
        for ka, kf in cols:
            x[kf] = max(profile.x[j][ka], 0.0)
            y[kf] = max(profile.y[j][ka], 0.0)
        if x.sum() <= 0 or y.sum() <= 0:
            continue
        pts.append(VLEPoint(T=profile.T_C[j] + 273.15, P=profile.P_bar[j] * 1e5, x=x / x.sum(), y=y / y.sum()))
    return pts


def _apply(pm, idx_pairs, theta, vary):
    k = 0
    for i, j in idx_pairs:
        for key in vary:
            arr = getattr(pm, key)
            arr[i, j] = theta[k]
            arr[j, i] = theta[k + 1]
            k += 2


def fit_nrtl(fs, points, pairs, vary=("b",), min_frac: float = 1e-12, bounds=(-3000.0, 6000.0)) -> FitResult:
    """Fit NRTL parameters of ``pairs`` (both directions) to ``points``; the flowsheet's property
    method is updated in place with the result. Starts from the current values."""
    from scipy.optimize import least_squares

    pm = fs.pm
    if not hasattr(pm, "lngamma"):
        raise TypeError("fit_nrtl needs an NRTL / NRTL-RK flowsheet")
    idx_pairs = [(fs.comps.idx(a), fs.comps.idx(b)) for a, b in pairs]
    theta0 = []
    for i, j in idx_pairs:
        for key in vary:
            arr = getattr(pm, key)
            theta0 += [arr[i, j], arr[j, i]]
    theta0 = np.array(theta0, dtype=float)
    lo = np.full_like(theta0, bounds[0] if "b" in vary else -50.0)
    hi = np.full_like(theta0, bounds[1] if "b" in vary else 50.0)
    if vary != ("b",):  # per-parameter bounds: a in [-50, 50], b in bounds
        per = []
        for _ in idx_pairs:
            for key in vary:
                per += [(-50.0, 50.0)] * 2 if key == "a" else [bounds] * 2
        lo, hi = np.array([p[0] for p in per]), np.array([p[1] for p in per])
    theta0 = np.clip(theta0, lo + 1e-9, hi - 1e-9)
    masks = [(p.x > min_frac) & (p.y > min_frac) for p in points]
    lnK_obs = [np.log(np.where(m, p.y, 1.0) / np.where(m, p.x, 1.0)) for p, m in zip(points, masks)]

    def resid(theta):
        _apply(pm, idx_pairs, theta, vary)
        out = []
        for p, m, obs in zip(points, masks, lnK_obs):
            lnK = pm.lnK(p.T, p.P, p.x, p.y)
            out.append(np.sqrt(p.weight) * (np.asarray(lnK) - obs)[m])
        return np.concatenate(out)

    sol = least_squares(resid, theta0, bounds=(lo, hi), x_scale="jac", xtol=1e-12, ftol=1e-12, gtol=1e-12, max_nfev=2000)
    r = resid(sol.x)
    ids = fs.comps.ids
    params = {}
    for i, j in idx_pairs:
        for a, b in ((i, j), (j, i)):
            params[(ids[a], ids[b])] = {key: float(getattr(pm, key)[a, b]) for key in ("a", "b", "c")}
    note = f"NRTL regression: {len(pairs)} pair(s), {len(points)} points, rms(ln K) = {np.sqrt(np.mean(r * r)):.3g}"
    pm.notes.append(note)
    return FitResult(params, float(np.sqrt(np.mean(r * r))), len(points), len(r), r, [note, str(sol.message)])

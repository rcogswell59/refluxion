"""Equipment sizing for preliminary design and costing.

* Tray columns: Fair's flooding correlation, as fitted by Lygeros and Magoulas (1986) to Fair's chart,
  ``C_sb [m/s] = 0.0105 + 8.127e-4 TS^0.755 exp(-1.463 F_LV^0.842)`` (TS = tray spacing in mm,
  F_LV = (L/V) (rho_V/rho_L)^0.5 on a mass basis), corrected by (sigma/20)^0.2;
  ``U_flood = C_sb (sigma/20)^0.2 sqrt((rho_L - rho_V)/rho_V)``. The column is sized at a fraction of flooding
  (default 80 %) with a downcomer area fraction (default 10 %), at the worst stage.
  NOTE: coefficients written from memory of the published fit; check against Seider et al. or Kister before
  quoting them in a report.
* Tray count: equilibrium stages less condenser and reboiler, divided by the overall efficiency (given, or
  O'Connell's ``E0 = 0.492 (mu_L alpha)^-0.245`` with mu_L in cP and alpha the key relative volatility).
* Height: trays x spacing + 1.2 m above the top tray + 1.8 m sump (common heuristic).
* Exchangers, drums and storage tanks: the usual area, residence-time and surge-volume rules.

Every function returns a dict whose ``basis`` entry says how the number was obtained.
"""

from __future__ import annotations

import math

import numpy as np

_SIGMA_CACHE: dict = {}
_MU_CACHE: dict = {}


def fair_flood_velocity(L_kg_s, V_kg_s, rho_V, rho_L, sigma_mN_m, tray_spacing=0.6096) -> float:
    """Flooding vapor velocity (m/s, based on net area)."""
    FLV = (L_kg_s / V_kg_s) * math.sqrt(rho_V / rho_L)
    C = 0.0105 + 8.127e-4 * (tray_spacing * 1000.0) ** 0.755 * math.exp(-1.463 * FLV ** 0.842)
    return C * (sigma_mN_m / 20.0) ** 0.2 * math.sqrt((rho_L - rho_V) / rho_V)


def tray_column_diameter(V_kg_s, L_kg_s, rho_V, rho_L, sigma, tray_spacing=0.6096, flood=0.8, downcomer_frac=0.1) -> float:
    Uf = fair_flood_velocity(L_kg_s, V_kg_s, rho_V, rho_L, sigma, tray_spacing)
    A_net = V_kg_s / rho_V / (flood * Uf)
    return math.sqrt(4.0 * A_net / (1.0 - downcomer_frac) / math.pi)


def _component_prop(cache, comp, T, kind):
    """Pure-liquid surface tension (N/m) or viscosity (Pa s) from thermo; None if the component is not in the databank."""
    key = (comp.cas, kind)
    if key not in cache:
        obj = None
        if comp.cas:
            try:
                if kind == "sigma":
                    from thermo import SurfaceTension
                    obj = SurfaceTension(CASRN=comp.cas, MW=comp.MW, Tb=comp.Tb, Tc=comp.Tc, Pc=comp.Pc, Vc=comp.Vc, Zc=comp.Zc, omega=comp.omega)
                else:
                    from thermo import ViscosityLiquid
                    obj = ViscosityLiquid(CASRN=comp.cas, MW=comp.MW, Tc=comp.Tc, Pc=comp.Pc, Vc=comp.Vc, omega=comp.omega)
            except Exception:
                obj = None
        cache[key] = obj
    obj = cache[key]
    if obj is None:
        return None
    try:
        v = obj.T_dependent_property(T) if kind == "mu" else obj(T)
    except Exception:
        return None
    return float(v) if v is not None else None


def mixture_surface_tension(comps, T, x) -> float:
    """Mole-fraction average of pure-liquid surface tensions (mN/m); components without data are left out."""
    vals = [(xi, _component_prop(_SIGMA_CACHE, c, T, "sigma")) for c, xi in zip(comps, x)]
    vals = [(xi, s) for xi, s in vals if s is not None]
    w = sum(xi for xi, _ in vals)
    return 1000.0 * sum(xi * s for xi, s in vals) / w if w > 0 else 20.0


def mixture_viscosity(comps, T, x) -> float:
    """ln mu = sum x ln mu_i (cP); components without data are left out."""
    vals = [(xi, _component_prop(_MU_CACHE, c, T, "mu")) for c, xi in zip(comps, x)]
    vals = [(xi, m) for xi, m in vals if m]
    w = sum(xi for xi, _ in vals)
    return 1000.0 * math.exp(sum(xi * math.log(m) for xi, m in vals) / w) if w > 0 else 0.5


def oconnell_efficiency(mu_cP, alpha) -> float:
    return 0.492 * (mu_cP * alpha) ** -0.245


def size_tray_column(col, tray_spacing=0.6096, flood=0.8, efficiency=None, keys=None, downcomer_frac=0.1,
                     top_space=1.2, sump=1.8) -> dict:
    """Size a solved RadFrac as a sieve-tray column. ``efficiency`` overall (fraction) or ``keys=(LK, HK)`` for O'Connell."""
    pm, comps = col.pm, col.comps
    p = col.tables["profiles"]
    MW = comps.MW / 1000.0
    N = len(p["stage"])
    worst = (0.0, None)
    for j in range(1, N - 1):                                  # internal stages (no condenser / reboiler)
        T, P = float(p["T"][j]), float(p["P"][j])
        x, y = p["x"][j], p["y"][j]
        L_mol = float(p["L"][j])
        V_mol = float(p["V"][j + 1]) if j + 1 < N else float(p["V"][j])   # vapor coming up into stage j
        if V_mol <= 0 or L_mol <= 0:
            continue
        y_in = p["y"][j + 1] if j + 1 < N else y
        L, V = L_mol * float(x @ MW), V_mol * float(y_in @ MW)
        rho_L = float(x @ MW) / float(pm.v_liq(T, P, x))
        rho_V = float(y_in @ MW) / float(pm.v_vap(T, P, y_in))
        sigma = mixture_surface_tension(comps, T, x)
        D = tray_column_diameter(V, L, rho_V, rho_L, sigma, tray_spacing, flood, downcomer_frac)
        if D > worst[0]:
            worst = (D, j + 1, dict(L_kg_s=L, V_kg_s=V, rho_L=rho_L, rho_V=rho_V, sigma=sigma))
    if efficiency is None:
        if keys is None:
            efficiency = 0.7
            eff_basis = "assumed overall efficiency 0.70"
        else:
            i, k = comps.idx(keys[0]), comps.idx(keys[1])
            Tm = float(np.mean(p["T"]))
            xm = np.mean(p["x"], axis=0)
            alpha = float(np.mean(p["K"][:, i] / p["K"][:, k]))
            mu = mixture_viscosity(comps, Tm, xm)
            efficiency = min(1.0, oconnell_efficiency(mu, alpha))
            eff_basis = f"O'Connell: mu_L {mu:.3g} cP, alpha {keys[0]}/{keys[1]} {alpha:.3g}"
    else:
        eff_basis = f"given overall efficiency {efficiency:g}"
    trays = math.ceil((N - 2) / efficiency)
    height = trays * tray_spacing + top_space + sump
    return dict(diameter_m=worst[0], flooding_stage=worst[1], conditions=worst[2], n_equilibrium=N, efficiency=efficiency,
                n_actual=trays + 2, trays=trays, tray_spacing_m=tray_spacing, height_m=height, flood=flood,
                basis=f"Fair flooding at {flood:.0%} of flood, {downcomer_frac:.0%} downcomer, worst stage {worst[1]}; "
                      f"{eff_basis}; height = trays x {tray_spacing:g} m + {top_space:g} m + {sump:g} m")


def size_exchanger(Q_W, dT_lm, U, max_area_per_shell=1000.0) -> dict:
    area = abs(Q_W) / (U * dT_lm)
    return dict(area_m2=area, shells=max(1, math.ceil(area / max_area_per_shell)), U=U, dT_lm=dT_lm,
                basis=f"A = Q / (U dT_lm), U = {U:g} W/m2-K")


def size_drum(liquid_m3_h, holdup_min=5.0, L_over_D=3.0, fill=0.5) -> dict:
    """Flash or reflux drum: ``holdup_min`` of liquid with the drum ``fill`` full."""
    V = liquid_m3_h / 60.0 * holdup_min / fill
    D = (4.0 * V / (math.pi * L_over_D)) ** (1.0 / 3.0)
    return dict(volume_m3=V, D_m=D, L_m=L_over_D * D, basis=f"{holdup_min:g} min liquid holdup at {fill:.0%} full, L/D {L_over_D:g}")


def size_tank(liquid_m3_h, hours=24.0, fill=0.85) -> dict:
    """Storage tank for ``hours`` of surge (H-1: at least 24 h)."""
    return dict(volume_m3=liquid_m3_h * hours / fill, basis=f"{hours:g} h surge at {fill:.0%} working fill")


# ------------------------------------------------------------------------------------------------ packed columns
# Sulzer Mellapak 250.Y (Sulzer Chemtech, "Structured Packings for Distillation, Absorption and Reactive Distillation",
# 2010): HETP about 0.42 m at 100 mbar and 0.48 m at 960 mbar for F-factors 1-3 Pa^0.5; about 2 mbar/m at 70-80 % of flood.
MELLAPAK_250Y = dict(name="Mellapak 250.Y", hetp_vacuum=0.42, hetp_atm=0.48, F_design=2.5, dp_mbar_per_m=2.0)


def packed_column_diameter(V_kg_s, rho_V, F=2.5) -> float:
    """Diameter for a vapor load at F-factor ``F`` = u sqrt(rho_V) (Pa^0.5)."""
    u = F / math.sqrt(rho_V)
    return math.sqrt(4.0 * (V_kg_s / rho_V / u) / math.pi)


def size_packed_column(col, F=None, hetp=None, bed_max=6.0, distributor=0.6, top_space=1.2, sump=1.8, packing=MELLAPAK_250Y) -> dict:
    """Size a solved RadFrac as a structured-packing column: diameter from the worst stage's vapor load at the
    design F-factor, packed height = (equilibrium stages - condenser - reboiler) x HETP, beds of at most ``bed_max``."""
    pm, comps = col.pm, col.comps
    p = col.tables["profiles"]
    MW = comps.MW / 1000.0
    N = len(p["stage"])
    F = F or packing["F_design"]
    P_top = float(p["P"][0])
    if hetp is None:
        hetp = packing["hetp_vacuum"] if P_top < 0.5e5 else packing["hetp_atm"]
    worst = (0.0, None)
    for j in range(1, N - 1):
        V_mol = float(p["V"][j + 1]) if j + 1 < N else float(p["V"][j])
        if V_mol <= 0:
            continue
        y = p["y"][j + 1] if j + 1 < N else p["y"][j]
        T, P = float(p["T"][j]), float(p["P"][j])
        V = V_mol * float(y @ MW)
        rho_V = float(y @ MW) / float(pm.v_vap(T, P, y))
        D = packed_column_diameter(V, rho_V, F)
        if D > worst[0]:
            worst = (D, j + 1)
    packed = (N - 2) * hetp
    beds = max(1, math.ceil(packed / bed_max - 1e-9))
    height = packed + distributor * beds + top_space + sump
    return dict(diameter_m=worst[0], worst_stage=worst[1], packed_height_m=packed, beds=beds, hetp_m=hetp, height_m=height, F=F,
                pressure_drop_mbar=packing["dp_mbar_per_m"] * packed,
                basis=f"{packing['name']} at F = {F:g} Pa^0.5, HETP {hetp:g} m, {beds} bed(s), {distributor:g} m per distributor, "
                      f"+ {top_space:g} m top and {sump:g} m sump")

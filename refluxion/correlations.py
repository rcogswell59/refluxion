"""Aspen Plus pure-component correlation forms.

These are the equations behind the parameters you type on Aspen's Methods > Parameters > Pure
Components forms, so handbook or problem-statement data can be entered exactly as given:

* PLXANT  extended Antoine vapor pressure   ln P = A + B/(T + C) + D ln T + E T^F   (T in K)
* DHVLDP  DIPPR 106 heat of vaporization    dHvap = A (1 - Tr)^(B + C Tr + D Tr^2 + E Tr^3)
* CPIG    DIPPR 107 ideal-gas heat capacity Cp = A + B [(C/T)/sinh(C/T)]^2 + D [(E/T)/cosh(E/T)]^2
* POLY    polynomial heat capacity          Cp = A + B T + C T^2 + D T^3 + E T^4

Refluxion stores vapor pressure in PLXANT form ([A, B, C, D, E, F] with P in Pa) and heat of
vaporization in DIPPR 106 form, so both are exact. Ideal-gas Cp is kept as its 6th-order polynomial
in T/1000 (fast to integrate for enthalpy and entropy); other forms are fitted to it and the fit
error is recorded.
"""

from __future__ import annotations

import numpy as np

PRESSURE_TO_PA = {"Pa": 1.0, "kPa": 1e3, "bar": 1e5, "MPa": 1e6, "atm": 101325.0, "mmHg": 133.322368, "psia": 6894.757293168}
MOLAR_ENERGY_TO_J_PER_MOL = {"J/mol": 1.0, "kJ/kmol": 1.0, "J/kmol": 1e-3, "kJ/mol": 1e3, "cal/mol": 4.184}
CP_TO_J_PER_MOL_K = {"J/mol-K": 1.0, "kJ/kmol-K": 1.0, "J/kmol-K": 1e-3, "cal/mol-K": 4.184}


def plxant(T, c):
    """ln P (P in the units the coefficients were made for). ``c = [A, B, C, D, E, F]``."""
    A, B, C, D, E, F = c
    T = np.asarray(T, dtype=float)
    return A + B / (T + C) + D * np.log(T) + E * T ** F


def plxant_to_pa(c, unit: str):
    """Shift A so the coefficients give P in Pa."""
    out = list(map(float, c)) + [0.0] * (6 - len(c))
    out[0] += np.log(PRESSURE_TO_PA[unit])
    return out


def dippr106(T, Tc, c):
    A, B, C, D, E = (list(c) + [0.0] * 5)[:5]
    Tr = np.minimum(np.asarray(T, dtype=float) / Tc, 1.0)
    return A * (1.0 - Tr) ** (B + Tr * (C + Tr * (D + Tr * E)))


def dippr107_cp(T, c):
    A, B, C, D, E = c
    T = np.asarray(T, dtype=float)
    x, y = C / T, E / T
    return A + B * (x / np.sinh(x)) ** 2 + D * (y / np.cosh(y)) ** 2


def poly_cp(T, c):
    T = np.asarray(T, dtype=float)
    return sum(a * T ** k for k, a in enumerate(c))


def fit_cp_poly6(fn, T_lo: float, T_hi: float, n: int = 80):
    """Fit Cp(T) [J/mol-K] to Refluxion's 6th-order polynomial in T/1000. Returns (coeffs, max rel error)."""
    T = np.linspace(T_lo, T_hi, n)
    cp = np.asarray(fn(T), dtype=float)
    coeffs = np.polynomial.polynomial.polyfit(T / 1000.0, cp, 6)
    fit = np.polynomial.polynomial.polyval(T / 1000.0, coeffs)
    return list(map(float, coeffs)), float(np.max(np.abs(fit - cp) / np.abs(cp)))

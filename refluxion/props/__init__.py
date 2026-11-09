"""Property methods. Use :func:`make_method` with an Aspen-style name.

========== ==========================================================
PENG-ROB   Peng-Robinson EOS (hydrocarbons, gases, high pressure)
RK-SOAVE   Soave-Redlich-Kwong EOS (hydrocarbons, gases, refinery)
RK         Redlich-Kwong EOS
IDEAL      Raoult's law + ideal gas
NRTL       NRTL activity model, ideal-gas vapor (polar/nonideal liquids)
NRTL-RK    NRTL activity model, Redlich-Kwong vapor
========== ==========================================================
"""

from __future__ import annotations

from .activity import NRTLMethod
from .base import PropertyMethod
from .cubic import CubicEOS
from .ideal import IdealMethod
from .params import cubic_kij, nrtl_params

ALIASES = {
    "PENG-ROB": "PR", "PR": "PR", "PENGROB": "PR", "PENG-ROBINSON": "PR",
    "RK-SOAVE": "SRK", "SRK": "SRK", "RKSOAVE": "SRK", "SOAVE": "SRK",
    "RK": "RK", "REDLICH-KWONG": "RK",
    "IDEAL": "IDEAL", "RAOULT": "IDEAL",
    "NRTL": "NRTL", "NRTL-IG": "NRTL", "NRTL-RK": "NRTL-RK",
}


def make_method(name: str, comps, kij: dict | None = None, nrtl: dict | None = None) -> PropertyMethod:
    key = ALIASES.get(name.upper().replace("_", "-"))
    if key is None:
        raise ValueError(f"unknown property method {name!r}; choose from {sorted(set(ALIASES))}")
    if key in ("PR", "SRK", "RK"):
        k, notes, _ = cubic_kij(comps, key, kij) if key != "RK" else (None, [], None)
        m = CubicEOS(comps, key, kij=k)
        m.notes.extend(notes)
        return m
    if key == "IDEAL":
        return IdealMethod(comps)
    params, notes = nrtl_params(comps, nrtl)
    return NRTLMethod(comps, params, vapor="RK" if key == "NRTL-RK" else "IG", source_notes=notes)


__all__ = ["make_method", "PropertyMethod", "CubicEOS", "IdealMethod", "NRTLMethod"]

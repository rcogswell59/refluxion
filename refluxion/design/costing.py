"""Equipment cost and capital estimate - the module-factor method (Guthrie / Ulrich) as in Turton et al.

Purchased cost at ambient pressure in carbon steel (CEPCI 397, 2001):
    log10 Cp0 = K1 + K2 log10 A + K3 (log10 A)^2                                   (Turton Eq. A.1)
Pressure factor: log10 Fp = C1 + C2 log10 P + C3 (log10 P)^2 (P in barg, Table A.2); process vessels and towers
use the ASME-based Eq. A.2 (Fp = 1.25 below -0.5 barg).
Bare module cost: C_BM = Cp0 (B1 + B2 Fm Fp) (Table A.4); trays: C_BM = Cp0 N F_BM Fq.
Capital: total module cost C_TM = 1.18 sum C_BM; grassroots C_GR = C_TM + 0.50 sum C_BM0 (base conditions),
plus any explicitly itemized extras (structure, tank farm, fire protection, spill containment).

Coefficients live in :mod:`refluxion.design.cost_data` with their sources. Costs scale to the requested CEPCI.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .cost_data import BARE_MODULE, CEPCI_BASE, CORRELATIONS, MATERIAL_FACTORS, PRESSURE_FACTORS, SOURCE


@dataclass
class CostItem:
    name: str
    kind: str
    size: float
    size_unit: str
    material: str
    P_barg: float
    purchased_usd: float          # Cp at the requested CEPCI, base material/pressure (Cp0)
    bare_module_usd: float        # C_BM at actual material and pressure
    bare_module_base_usd: float   # C_BM0 (CS, ambient) for the grassroots auxiliary-facility allowance
    Fm: float = 1.0
    Fp: float = 1.0
    units: int = 1
    source: str = SOURCE
    cepci: float = CEPCI_BASE
    warnings: list = field(default_factory=list)


def vessel_pressure_factor(P_barg: float, D_m: float) -> float:
    """Turton Eq. A.2 (S = 944 bar, E = 0.9, 1/4 in minimum wall, 1/8 in corrosion allowance)."""
    if P_barg < -0.5:
        return 1.25
    Fp = ((P_barg + 1.0) * D_m / (2.0 * (850.0 - 0.6 * (P_barg + 1.0))) + 0.00315) / 0.0063
    return max(Fp, 1.0)


def tray_quantity_factor(N: int) -> float:
    if N >= 20:
        return 1.0
    L = math.log10(N)
    return 10 ** (0.4771 + 0.08516 * L - 0.3473 * L * L)


def _pressure_factor(kind: str, P_barg: float) -> float:
    for lo, hi, C1, C2, C3 in PRESSURE_FACTORS.get(kind, []):
        if lo <= P_barg < hi:
            if C1 == C2 == C3 == 0:
                return 1.0
            L = math.log10(P_barg)
            return 10 ** (C1 + C2 * L + C3 * L * L)
    return 1.0


def cost_equipment(kind: str, size: float, *, material: str = "SS316", P_barg: float = 0.0, D_m: float | None = None,
                   n_trays: int | None = None, cepci: float = 878.2, name: str = "") -> CostItem:
    """Cost one item. ``size`` in the correlation's capacity unit (see ``cost_data.CORRELATIONS``). Items larger than
    the correlation's maximum are split into equal parallel units; smaller ones are costed at the size given with a
    warning (Turton: extrapolation below the range overestimates little, but flag it)."""
    K1, K2, K3, unit, smin, smax, label = CORRELATIONS[kind]
    warnings = []
    units = 1
    s = float(size)
    if s > smax:
        units = math.ceil(s / smax)
        s = s / units
    if s < smin:
        warnings.append(f"{label}: size {s:.3g} {unit} below the correlation range ({smin:g}-{smax:g}); costed at the minimum size")
        s = smin
    L = math.log10(s)
    Cp0 = 10 ** (K1 + K2 * L + K3 * L * L) * cepci / CEPCI_BASE * units
    Fm = MATERIAL_FACTORS[kind].get(material, None)
    if Fm is None:
        raise KeyError(f"no material factor for {kind} in {material}; known: {sorted(MATERIAL_FACTORS[kind])}")
    if kind in ("vessel_vertical", "vessel_horizontal", "tower"):
        if D_m is None:
            raise ValueError(f"{kind}: give D_m for the vessel pressure factor")
        Fp = vessel_pressure_factor(P_barg, D_m)
    else:
        Fp = _pressure_factor(kind, P_barg)
    if kind == "tray_sieve":
        N = int(n_trays or 1)
        FBM = MATERIAL_FACTORS[kind][material]          # trays: Fig. A.19 gives F_BM directly
        CBM = Cp0 * N * FBM * tray_quantity_factor(N)
        CBM0 = Cp0 * N * MATERIAL_FACTORS[kind]["CS"] * tray_quantity_factor(N)
    else:
        B1, B2 = BARE_MODULE[kind]
        CBM = Cp0 * (B1 + B2 * Fm * Fp)
        CBM0 = Cp0 * (B1 + B2)
    return CostItem(name or label, kind, float(size), unit, material, P_barg, Cp0, CBM, CBM0, Fm, Fp, units,
                    SOURCE, cepci, warnings)


def capital_estimate(items, extras: dict | None = None, contingency_and_fee: float = 0.18, auxiliary: float = 0.50) -> dict:
    """Grassroots capital (Turton Ch. 7): C_TM = 1.18 sum C_BM; C_GR = C_TM + 0.50 sum C_BM0 + extras."""
    extras = dict(extras or {})
    CBM = sum(i.bare_module_usd for i in items)
    CBM0 = sum(i.bare_module_base_usd for i in items)
    CTM = (1.0 + contingency_and_fee) * CBM
    CGR = CTM + auxiliary * CBM0 + sum(extras.values())
    rows = [dict(item=i.name, kind=i.kind, size=i.size, unit=i.size_unit, material=i.material, P_barg=i.P_barg, units=i.units,
                 purchased=i.purchased_usd, bare_module=i.bare_module_usd, bare_module_base=i.bare_module_base_usd, Fm=i.Fm, Fp=i.Fp,
                 warnings="; ".join(i.warnings))
            for i in items]
    return dict(bare_module=CBM, bare_module_base=CBM0, total_module=CTM, grassroots=CGR, extras=extras, rows=rows,
                basis=f"{SOURCE}; C_TM = {1 + contingency_and_fee:.2f} x sum C_BM, C_GR = C_TM + {auxiliary:.2f} x sum C_BM0 + itemized extras")

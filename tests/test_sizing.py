"""Equipment sizing: tray columns (Fair flooding), exchangers, drums, tanks."""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

from refluxion.design.sizing import fair_flood_velocity, size_drum, size_exchanger, size_tank, size_tray_column, tray_column_diameter


def test_fair_flood_velocity_matches_hand_calculation():
    # F_LV = (L/V) sqrt(rho_V/rho_L); C_sb = 0.0105 + 8.127e-4 TS^0.755 exp(-1.463 F_LV^0.842) (TS mm, m/s)
    L, V, rhoV, rhoL, sigma = 2.0, 1.5, 3.0, 800.0, 20.0
    FLV = (L / V) * math.sqrt(rhoV / rhoL)
    C = 0.0105 + 8.127e-4 * 609.6 ** 0.755 * math.exp(-1.463 * FLV ** 0.842)
    Uf = C * (sigma / 20.0) ** 0.2 * math.sqrt((rhoL - rhoV) / rhoV)
    assert fair_flood_velocity(L, V, rhoV, rhoL, sigma, tray_spacing=0.6096) == pytest.approx(Uf, rel=1e-12)


def test_diameter_from_flood_fraction_and_downcomer_area():
    D = tray_column_diameter(V_kg_s=1.5, L_kg_s=2.0, rho_V=3.0, rho_L=800.0, sigma=20.0, flood=0.8, downcomer_frac=0.1)
    Uf = fair_flood_velocity(2.0, 1.5, 3.0, 800.0, 20.0)
    A = 1.5 / 3.0 / (0.8 * Uf) / 0.9
    assert D == pytest.approx(math.sqrt(4 * A / math.pi), rel=1e-12)


def test_tank_and_drum_and_exchanger():
    assert size_tank(7.45 / 24, hours=24, fill=0.85)["volume_m3"] == pytest.approx(7.45 / 0.85, rel=1e-12)
    d = size_drum(liquid_m3_h=1.2, holdup_min=5, L_over_D=3)
    assert d["volume_m3"] == pytest.approx(1.2 / 60 * 5 * 2, rel=1e-12)           # half full at 5 min
    assert d["L_m"] == pytest.approx(3 * d["D_m"]) and math.pi / 4 * d["D_m"] ** 2 * d["L_m"] == pytest.approx(d["volume_m3"])
    x = size_exchanger(Q_W=100e3, dT_lm=20.0, U=850.0)
    assert x["area_m2"] == pytest.approx(100e3 / (850 * 20)) and x["shells"] == 1


def test_liquid_viscosity_is_from_data_not_the_fallback():
    from refluxion.components import ComponentList
    from refluxion.design.sizing import mixture_viscosity
    comps = ComponentList([("TOL", "toluene")])
    mu = mixture_viscosity(comps, 350.0, [1.0])
    assert 0.25 < mu < 0.40 and mu != 0.5            # toluene about 0.32 cP at 77 C

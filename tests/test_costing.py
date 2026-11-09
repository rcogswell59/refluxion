"""Purchased and bare-module costs (Turton et al., Appendix A / CAPCOST, CEPCI 397) and capital estimate."""

from __future__ import annotations

import math

import pytest

from refluxion.design.costing import capital_estimate, cost_equipment, tray_quantity_factor, vessel_pressure_factor


def test_floating_head_carbon_steel_ambient():
    item = cost_equipment("exchanger_floating_head", 100.0, material="CS", P_barg=0.0, cepci=397.0)
    logC = 4.8306 - 0.8509 * 2 + 0.3187 * 4
    assert item.purchased_usd == pytest.approx(10 ** logC, rel=1e-9)
    assert item.bare_module_usd == pytest.approx(10 ** logC * (1.63 + 1.66), rel=1e-9)


def test_escalation_to_oct_2026():
    a = cost_equipment("pump_centrifugal", 5.0, material="CS", cepci=397.0)
    b = cost_equipment("pump_centrifugal", 5.0, material="CS", cepci=878.2)
    assert b.bare_module_usd / a.bare_module_usd == pytest.approx(878.2 / 397.0, rel=1e-12)


def test_stainless_costs_more_and_records_its_source():
    cs = cost_equipment("vessel_vertical", 5.0, material="CS", P_barg=2.45, D_m=1.0)
    ss = cost_equipment("vessel_vertical", 5.0, material="SS316", P_barg=2.45, D_m=1.0)
    assert ss.bare_module_usd > cs.bare_module_usd and "Turton" in ss.source


def test_vessel_pressure_factor():
    assert vessel_pressure_factor(-0.9, 1.0) == 1.25                       # vacuum
    assert vessel_pressure_factor(3.45, 1.0) == 1.0                        # thin wall: minimum thickness governs
    P, D = 20.0, 2.0
    assert vessel_pressure_factor(P, D) == pytest.approx(((P + 1) * D / (2 * (850 - 0.6 * (P + 1))) + 0.00315) / 0.0063)


def test_tray_quantity_factor():
    N = 5
    assert tray_quantity_factor(N) == pytest.approx(10 ** (0.4771 + 0.08516 * math.log10(N) - 0.3473 * math.log10(N) ** 2))
    assert tray_quantity_factor(25) == 1.0


def test_size_outside_correlation_range_is_flagged():
    item = cost_equipment("exchanger_floating_head", 3.0, material="SS316")
    assert item.warnings and "below" in item.warnings[0]


def test_capital_estimate_grassroots():
    items = [cost_equipment("exchanger_floating_head", 50.0, material="SS316"), cost_equipment("pump_centrifugal", 3.0, material="SS316")]
    cap = capital_estimate(items, extras={"Fire protection": 10_000.0})
    CBM = sum(i.bare_module_usd for i in items)
    CBM0 = sum(i.bare_module_base_usd for i in items)
    assert cap["total_module"] == pytest.approx(1.18 * CBM)
    assert cap["grassroots"] == pytest.approx(1.18 * CBM + 0.50 * CBM0 + 10_000.0)

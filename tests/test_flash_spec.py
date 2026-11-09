"""Flash2 with a liquid-composition spec: the block finds the temperature that gives the target mass fraction."""

from __future__ import annotations

import pytest

from refluxion import Flash2, Flowsheet
from refluxion.blocks.base import BlockError


def flash(spec):
    fs = Flowsheet("f", components=[("TOL", "toluene"), ("XYL", "p-xylene"), ("C10", "n-decane")], method="IDEAL", units="SI-KW")
    fs.feed("F", T=100, P=1.0, flows={"TOL": 1.0, "XYL": 8.0, "C10": 1.0})
    fs.add(Flash2("FL", P=1.0, liquid_spec=spec), inlets=["F"], outlets=["V", "L"])
    return fs


def test_liquid_mass_fraction_spec_is_met():
    fs = flash(("C10", 0.25)).run()
    assert fs["L"].get("mass_frac", "C10") == pytest.approx(0.25, abs=1e-7)
    assert 0 < fs["FL"].result("vf") < 1


def test_unreachable_spec_raises():
    with pytest.raises(BlockError, match="liquid_spec"):
        flash(("C10", 0.02)).run()          # below the feed's own decane content: no flash can reach it


def test_clamp_mode_goes_to_nearest_end_and_flags_it():
    fs = Flowsheet("f", components=[("TOL", "toluene"), ("XYL", "p-xylene"), ("C10", "n-decane")], method="IDEAL", units="SI-KW")
    fs.feed("F", T=100, P=1.0, flows={"TOL": 1.0, "XYL": 8.0, "C10": 1.0})
    fs.add(Flash2("FL", P=1.0, liquid_spec=("C10", 0.02), liquid_spec_mode="clamp"), inlets=["F"], outlets=["V", "L"])
    fs.run()
    assert fs["FL"].result("spec_met") == 0.0
    assert fs["L"].get("mass_frac", "C10") > 0.02          # stopped at the bubble-point end

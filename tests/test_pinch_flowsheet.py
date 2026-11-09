"""Heat integration from a flowsheet: segmented streams, column condensers/reboilers, column-to-column matches."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from refluxion.modules.pinch import PinchStream, column_matches, pinch_analysis, process_streams


def test_segmented_stream_gives_same_targets_as_constant_cp():
    base = [("H1", 180, 40, 20), ("H2", 150, 40, 40), ("C1", 60, 180, 30), ("C2", 30, 130, 22)]
    seg = [PinchStream("H1", 180, 40, 20, segments=[(180, 100, 20 * 80), (100, 40, 20 * 60)])] + [PinchStream(*s) for s in base[1:]]
    a, b = pinch_analysis(base, 10), pinch_analysis(seg, 10)
    assert b.QHmin == pytest.approx(a.QHmin) and b.QCmin == pytest.approx(a.QCmin) and b.pinch_hot == pytest.approx(a.pinch_hot)


def test_isothermal_duty_becomes_a_one_degree_segment():
    s = PinchStream.from_duty("COND", 110.0, 110.0, -200.0)        # condenser releases 200 kW at 110 C
    assert s.hot and s.duty == pytest.approx(200.0)
    assert max(t for seg in s.segments for t in seg[:2]) - min(t for seg in s.segments for t in seg[:2]) == pytest.approx(1.0)


def test_match_duty_respects_dtmin_at_both_ends():
    from refluxion.modules.pinch import match_duty
    cond = PinchStream.from_duty("C1 condenser", 36.0, 36.0, -100.0)        # condensing at 36 C, modelled as a 36.5 -> 35.5 C band
    feed = PinchStream("Feed", 25.0, 100.0, 0.13)                            # 9.75 kW to go from 25 to 100 C
    q = match_duty(cond, feed, dTmin=10.0)
    # counter-current: the condenser's inlet (36.5 C) meets the feed outlet, so the feed reaches only 26.5 C
    assert q == pytest.approx(0.13 * (36.5 - 10.0 - 25.0), rel=1e-6)
    hot = PinchStream("Hot", 150.0, 60.0, 1.0)                               # 90 kW
    cold = PinchStream("Cold", 40.0, 120.0, 2.0)                             # 160 kW
    # cold can be heated only to 140 C (limited by its own target 120): hot cooled to 50 C allowed; hot gives at most 90 kW,
    # and counter-current dTmin at the hot outlet end: hot out 60 >= cold in 40 + 10
    assert match_duty(hot, cold, 10.0) == pytest.approx(90.0)
    assert match_duty(PinchStream("H", 50.0, 45.0, 1.0), cold, 10.0) == 0.0

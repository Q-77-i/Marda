"""advance 阶段推进与主动结束门槛单测（SPEC §4.3）。"""

from __future__ import annotations

from app.graph.rules.advance import meets_end_quota, phase_after_answer
from app.graph.state import Phase


def test_十题答五题不可主动结束():
    assert meets_end_quota(5, 10) is False


def test_十题答六题可主动结束():
    assert meets_end_quota(6, 10) is True


def test_十五题九题门槛_浮点边界():
    assert meets_end_quota(9, 15) is True
    assert meets_end_quota(8, 15) is False


def test_五题答三题可主动结束():
    assert meets_end_quota(3, 5) is True


def test_技术题答满进场景题():
    assert phase_after_answer(Phase.TECH_BASE, 10, 10) is Phase.PROJECT


def test_技术题未满留在技术阶段():
    assert phase_after_answer(Phase.TECH_BASE, 9, 10) is Phase.TECH_BASE


def test_场景题完成进反问():
    assert phase_after_answer(Phase.PROJECT, 11, 10) is Phase.CLOSING


def test_反问阶段不变():
    assert phase_after_answer(Phase.CLOSING, 11, 10) is Phase.CLOSING


def test_开场与自我介绍阶段不受影响():
    assert phase_after_answer(Phase.INTRO, 0, 10) is Phase.INTRO
    assert phase_after_answer(Phase.WARMUP, 0, 10) is Phase.WARMUP

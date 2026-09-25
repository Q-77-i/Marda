"""advance 阶段推进与主动结束门槛单测（SPEC §4.3）。"""

from __future__ import annotations

from app.graph.rules.advance import end_quota, meets_end_quota, phase_after_answer
from app.graph.state import Phase


def test_结束门槛题数_与判定同源():
    """门槛题数单独暴露给回放（P1-M4 展示「未达门槛被挽留」的证据），口径必须一致。"""
    assert end_quota(10) == 6
    assert end_quota(15) == 9
    assert end_quota(3) == 2
    for question_count in range(2, 21):
        assert meets_end_quota(end_quota(question_count), question_count) is True
        assert meets_end_quota(end_quota(question_count) - 1, question_count) is False


def test_十题答五题不可主动结束():
    assert meets_end_quota(5, 10) is False


def test_十题答六题可主动结束():
    assert meets_end_quota(6, 10) is True


def test_十五题九题门槛_浮点边界():
    assert meets_end_quota(9, 15) is True
    assert meets_end_quota(8, 15) is False


def test_五题答三题可主动结束():
    assert meets_end_quota(3, 5) is True


def test_技术轮答满进场景题():
    # 轮次语义：10 轮 = 9 技术 + 1 场景，答满 9 轮进场景题
    assert phase_after_answer(Phase.TECH_BASE, 9, 10) is Phase.PROJECT
    assert phase_after_answer(Phase.TECH_BASE, 10, 10) is Phase.PROJECT


def test_技术轮未满留在技术阶段():
    assert phase_after_answer(Phase.TECH_BASE, 8, 10) is Phase.TECH_BASE


def test_场景题完成进反问():
    assert phase_after_answer(Phase.PROJECT, 10, 10) is Phase.CLOSING


def test_反问阶段不变():
    assert phase_after_answer(Phase.CLOSING, 10, 10) is Phase.CLOSING


def test_开场与自我介绍阶段不受影响():
    assert phase_after_answer(Phase.INTRO, 0, 10) is Phase.INTRO
    assert phase_after_answer(Phase.WARMUP, 0, 10) is Phase.WARMUP

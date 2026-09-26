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


def test_项目题答满进技术阶段():
    # P1-M4.6-C 顺序：项目深挖前置。10 轮 = 3 项目 + 7 技术，答满 3 道项目题进技术阶段
    assert phase_after_answer(Phase.PROJECT, 3, 10) is Phase.TECH_BASE
    assert phase_after_answer(Phase.PROJECT, 4, 10) is Phase.TECH_BASE


def test_项目题未答满留在项目阶段():
    assert phase_after_answer(Phase.PROJECT, 2, 10) is Phase.PROJECT


def test_技术轮答满进反问():
    assert phase_after_answer(Phase.TECH_BASE, 10, 10) is Phase.CLOSING
    assert phase_after_answer(Phase.TECH_BASE, 11, 10) is Phase.CLOSING


def test_技术轮未满留在技术阶段():
    assert phase_after_answer(Phase.TECH_BASE, 9, 10) is Phase.TECH_BASE


def test_五题场两道项目题():
    # 5 轮 = 2 项目 + 3 技术
    assert phase_after_answer(Phase.PROJECT, 1, 5) is Phase.PROJECT
    assert phase_after_answer(Phase.PROJECT, 2, 5) is Phase.TECH_BASE
    assert phase_after_answer(Phase.TECH_BASE, 5, 5) is Phase.CLOSING


def test_两题场保底一道技术题():
    # 2 轮 = 1 项目 + 1 技术（N−1 保底）
    assert phase_after_answer(Phase.PROJECT, 1, 2) is Phase.TECH_BASE
    assert phase_after_answer(Phase.TECH_BASE, 2, 2) is Phase.CLOSING


def test_反问阶段不变():
    assert phase_after_answer(Phase.CLOSING, 10, 10) is Phase.CLOSING


def test_开场与自我介绍阶段不受影响():
    assert phase_after_answer(Phase.INTRO, 0, 10) is Phase.INTRO
    assert phase_after_answer(Phase.WARMUP, 0, 10) is Phase.WARMUP

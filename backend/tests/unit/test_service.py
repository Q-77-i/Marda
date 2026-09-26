"""service 层纯函数单测：引擎死活判据（P1-M4.7 后续，前端「重试」的承重墙）。

`engine_stalled` 只看 snapshot 的形状，不需要跑图——用 SimpleNamespace 造两态即可。
（真实图上的两态由 `tests/integration/test_api.py::test_流内失败打上stalled标记` 钉。）
"""

from __future__ import annotations

from types import SimpleNamespace

from app.service import engine_stalled


def _snapshot(next_: tuple, tasks: list[tuple[str, tuple]]) -> SimpleNamespace:
    return SimpleNamespace(
        next=next_,
        tasks=[SimpleNamespace(name=n, interrupts=i) for n, i in tasks],
    )


def test_停在pause中断点不算stalled():
    """正常休息态：next 指向 pause，tasks 带中断载荷（实测 ('pause',)/interrupt=True）。"""
    snap = _snapshot(("pause",), [("pause", ({"type": "ask"},))])
    assert engine_stalled(snap) is False


def test_失败节点无中断载荷算stalled():
    """节点抛异常：checkpoint 停在待执行节点上，没有中断载荷（实测 ('ask',)/interrupt=False）。"""
    assert engine_stalled(_snapshot(("ask",), [("ask", ())])) is True


def test_已结束不算stalled():
    """next 为空 = 跑完了（report → END），不是卡住——否则报告页会被当成故障。"""
    assert engine_stalled(_snapshot((), [])) is False

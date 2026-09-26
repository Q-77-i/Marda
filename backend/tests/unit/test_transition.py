"""衔接语单测（P1-M4.7-D 人味层，SPEC §4.8）：确定性衔接语的分类与文案。

六类黏合点里高频短衔接全部纯代码（模板 + 插槽，零 LLM、可单测、可回放）：
开场过渡 / 项目续题 / 转技术 / 题间同域 / 跨域换方向 / 答错缓冲 / 重连问候。
低频长文（开场白、结束陈词）走 LLM，不在本模块。
"""

from __future__ import annotations

import pytest

from app.graph.rules.transition import (
    TransitionKind,
    buffer_line,
    domain_label,
    estimated_minutes,
    reconnect_line,
    transition_kind,
    transition_line,
)
from app.graph.state import InterviewState, Phase, QuestionRecord, ScoreItem

ROUTING_MARKERS = ("评分官", "报告官", "出题官", "提炼")  # FakeLLM 路由标记词（不得出现在文案里）


def _score(error: bool = False) -> ScoreItem:
    return ScoreItem(
        technical_depth=4, fundamentals=4, project_experience=4,
        communication=4, problem_solving=4, error_flag=error,
    )


def _record(question_type: str = "tech", domain: str = "rag", *, error: bool = False,
            scored: bool = True) -> QuestionRecord:
    record = QuestionRecord(
        text=f"「{domain}」的题干", domain=domain, topic="测试主题", difficulty="L1",
        question_type=question_type,
    )
    if scored:
        record.score = _score(error)
    return record


def _state(*, prev: QuestionRecord | None = None, answered_count: int = 1,
           phase: Phase = Phase.PROJECT, status: str = "running") -> InterviewState:
    state = InterviewState(
        interview_id="iv-1", position="Agent/AI 工程师", question_count=10,
        phase=phase, status=status,
    )
    state.current_question = prev
    state.answered_count = answered_count
    return state


# ---- 开场时长插槽 ----


def test_预计时长按每题三分钟():
    assert estimated_minutes(10) == 30
    assert estimated_minutes(2) == 6
    assert estimated_minutes(15) == 45


# ---- 衔接分类（五类判定）----


def test_首题走开场过渡():
    state = _state(prev=None)
    assert transition_kind(state, _record("scenario", "project")) is TransitionKind.OPEN_PROJECT


def test_项目段续题():
    state = _state(prev=_record("scenario", "project"))
    assert transition_kind(state, _record("scenario", "project")) is TransitionKind.PROJECT_NEXT


def test_项目段转技术段():
    state = _state(prev=_record("scenario", "project"), phase=Phase.TECH_BASE)
    assert transition_kind(state, _record("tech", "rag")) is TransitionKind.TO_TECH


def test_技术段同域续问():
    state = _state(prev=_record("tech", "rag"), phase=Phase.TECH_BASE)
    assert transition_kind(state, _record("tech", "rag")) is TransitionKind.SAME_DOMAIN


def test_技术段跨域换方向():
    state = _state(prev=_record("tech", "rag"), phase=Phase.TECH_BASE)
    assert transition_kind(state, _record("tech", "memory")) is TransitionKind.SWITCH_DOMAIN


# ---- 文案 ----


def test_跨域衔接带新题域标签():
    state = _state(prev=_record("tech", "rag"), phase=Phase.TECH_BASE)
    line = transition_line(state, _record("tech", "memory"))
    assert "Memory" in line


@pytest.mark.parametrize(
    "n,prev_domain,domain,expected",
    [
        (0, "rag", "memory", "好，我们换个方向，聊聊 Memory。"),  # 纯拉丁标签：两侧留空格
        (0, "rag", "planning-reasoning", "好，我们换个方向，聊聊规划与推理范式。"),  # 纯中文：不留
        (1, "memory", "rag", "嗯，接下来换个领域，看看 RAG 方面。"),  # 中文与拉丁文之间也留空格
        (1, "rag", "tool-use", "嗯，接下来换个领域，看看 Tool 与 Function Calling 方面。"),
    ],
)
def test_跨域衔接的中英混排空格(n, prev_domain, domain, expected):
    """域标签插进中文句子时按中文排版留空格（真实链路实测出现「看看RAG方面」挤在一起）。

    n 选定变体：0 → 「聊聊{label}」，1 → 「看看{label}方面」；prev_domain 需与新域不同
    才会判成跨域（同域走 SAME_DOMAIN，不带标签）。
    """
    state = _state(prev=_record("tech", prev_domain), phase=Phase.TECH_BASE, answered_count=n)
    line = transition_line(state, _record("tech", domain))
    assert line.endswith(expected), line


def test_域标签_项目题单列():
    """domain="project" 不在 DOMAIN_LABELS（不参与域统计），衔接语里显示「项目深挖」。"""
    assert domain_label("project") == "项目深挖"
    assert domain_label("rag") == "RAG"


def test_同输入同文案_可回放():
    state = _state(prev=_record("tech", "rag"), phase=Phase.TECH_BASE, answered_count=5)
    first = transition_line(state, _record("tech", "rag"))
    assert transition_line(state, _record("tech", "rag")) == first


def test_变体随轮次确定性轮换():
    lines = {
        transition_line(
            _state(prev=_record("tech", "rag"), phase=Phase.TECH_BASE, answered_count=n),
            _record("tech", "rag"),
        )
        for n in range(4)
    }
    assert len(lines) == 2, "池内两条变体都应被用到（轮换而非随机）"


def test_文案无残留占位符与路由标记词():
    """衔接语不送 LLM，但仍不得含 FakeLLM 路由标记词——组装进 prompt 会串路由。"""
    cases = [
        _state(prev=None),
        _state(prev=_record("scenario", "project")),
        _state(prev=_record("scenario", "project"), phase=Phase.TECH_BASE),
        _state(prev=_record("tech", "rag"), phase=Phase.TECH_BASE),
        _state(prev=_record("tech", "rag"), phase=Phase.TECH_BASE),
    ]
    news = [
        _record("scenario", "project"),
        _record("scenario", "project"),
        _record("tech", "rag"),
        _record("tech", "rag"),
        _record("tech", "memory"),
    ]
    for state, new in zip(cases, news):
        line = transition_line(state, new)
        assert "{" not in line and "}" not in line
        assert not any(marker in line for marker in ROUTING_MARKERS)


# ---- 答错缓冲（独立成条）----


def test_答错缓冲仅在上一题有错时给出():
    assert buffer_line(_state(prev=_record("tech", "rag", error=False))) is None
    assert buffer_line(_state(prev=_record("tech", "rag"))) is None


def test_首题或未评分不缓冲():
    assert buffer_line(_state(prev=None)) is None
    assert buffer_line(_state(prev=_record("tech", "rag", scored=False))) is None


def test_答错缓冲不含方向词():
    """缓冲回应的是上一题，方向交给下一题的衔接语——否则「换个方向」会说两遍。"""
    for n in range(4):
        state = _state(prev=_record("tech", "rag", error=True), answered_count=n)
        line = buffer_line(state)
        assert line and "没关系" in line
        assert "方向" not in line and "领域" not in line


# ---- 重连问候 ----


def test_重连问候重发当前题干():
    prev = _record("tech", "rag")
    line = reconnect_line(_state(prev=prev, phase=Phase.TECH_BASE))
    assert line and prev.text in line and "RAG" in line


@pytest.mark.parametrize("phase", [Phase.INTRO, Phase.WARMUP, Phase.CLOSING, Phase.FINISHED])
def test_非答题阶段不重连(phase):
    """开场/自我介绍/反问阶段没有「刚才的题」可回去，静默恢复。"""
    assert reconnect_line(_state(prev=_record("tech", "rag"), phase=phase)) is None


def test_无当前题不重连():
    assert reconnect_line(_state(prev=None, phase=Phase.TECH_BASE)) is None


def test_已结束场次不重连():
    state = _state(prev=_record("tech", "rag"), phase=Phase.TECH_BASE, status="finished")
    assert reconnect_line(state) is None

"""difficulty 难度自适应单测：连击计数/升降档/封顶保底（SPEC §4.3）。"""

from __future__ import annotations

from app.graph.rules.difficulty import update_difficulty
from app.graph.state import InterviewState, QuestionRecord, ScoreItem


def _state(difficulty: str = "L1", good: int = 0, bad: int = 0) -> InterviewState:
    return InterviewState(
        interview_id="t",
        position="Agent/AI 工程师",
        difficulty=difficulty,
        consecutive_good=good,
        consecutive_bad=bad,
    )


def _answered(state: InterviewState, *dims: int) -> InterviewState:
    """把一道已评分题挂到 state 上，五维分数由 dims 指定（缺省 5 个 4 = 均值 4）。"""
    dims = dims or (4, 4, 4, 4, 4)
    state.current_question = QuestionRecord(
        text="测试题",
        domain="rag",
        topic="测试",
        difficulty=state.difficulty,
        key_points=[],
        score=ScoreItem(
            technical_depth=dims[0],
            fundamentals=dims[1],
            project_experience=dims[2],
            communication=dims[3],
            problem_solving=dims[4],
            comment="",
        ),
    )
    return state


def test_均分达到4记一次好并清差():
    state = _answered(_state(bad=1))
    update_difficulty(state)
    assert state.consecutive_good == 1
    assert state.consecutive_bad == 0
    assert state.difficulty == "L1"


def test_均分不高于2记一次差并清好():
    state = _answered(_state(good=1), 1, 2, 2, 2, 2)
    update_difficulty(state)
    assert state.consecutive_bad == 1
    assert state.consecutive_good == 0


def test_中等均分清零连击():
    state = _answered(_state(good=1, bad=1), 4, 4, 4, 4, 3)  # 均值 3.8
    update_difficulty(state)
    assert state.consecutive_good == 0
    assert state.consecutive_bad == 0


def test_连好两次升一档并清零():
    state = _answered(_state(good=1))
    update_difficulty(state)
    assert state.difficulty == "L2"
    assert state.consecutive_good == 0
    assert state.consecutive_bad == 0


def test_最高难度不溢出():
    state = _answered(_state(difficulty="L3", good=1))
    update_difficulty(state)
    assert state.difficulty == "L3"


def test_连差两次降一档并清零():
    state = _answered(_state(difficulty="L2", bad=1), 1, 1, 1, 1, 1)
    update_difficulty(state)
    assert state.difficulty == "L1"
    assert state.consecutive_good == 0
    assert state.consecutive_bad == 0


def test_最低难度保底():
    state = _answered(_state(difficulty="L1", bad=1), 2, 1, 1, 1, 1)
    update_difficulty(state)
    assert state.difficulty == "L1"


def test_均分恰为4按好计():
    state = _answered(_state())
    update_difficulty(state)
    assert state.consecutive_good == 1


def test_均分恰为2按差计():
    state = _answered(_state(), 2, 2, 2, 2, 2)
    update_difficulty(state)
    assert state.consecutive_bad == 1

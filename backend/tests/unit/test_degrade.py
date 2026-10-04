"""降级链单测（P2-M9）：判据 / 状态与提示成对 / 六个节点的确定性兜底。

注入形态 = 把 `llm.chat` / `llm.chat_json` 换成抛 `LLMError(retryable=True)` 的假函数
（= 上游不可用）。内容类失败（`retryable=False`）**不该**走这里——既有集成用例
（test_api / test_graph_flow 的「模拟抖动」）钉的是那条路。
"""

from __future__ import annotations

import pytest

from app import llm
from app.agents import fallbacks
from app.graph.nodes import ask as ask_node_module
from app.graph.nodes.closing import (
    answer_candidate_node,
    closing_invite_node,
    refuse_end_node,
)
from app.graph.nodes.intro import intro_node
from app.graph.nodes.judge import judge_node
from app.graph.nodes.profile import profile_node
from app.graph.nodes.report import report_node
from app.graph.rules import degrade, stream
from app.graph.rules.follow_up import Decision, Reason, explain_decision
from app.graph.state import InterviewState, Phase, QuestionRecord


@pytest.fixture
def events(monkeypatch):
    """捕获 rules/stream 发出的领域事件（图外 _emit 本是空操作）。"""
    seen: list[dict] = []
    monkeypatch.setattr(stream, "_emit", seen.append)
    return seen


def _fail_llm(monkeypatch, *, retryable: bool = True) -> None:
    async def _chat(*args, **kwargs):
        raise llm.LLMError("上游暂时不可用", retryable=retryable)

    async def _chat_json(*args, **kwargs):
        raise llm.LLMError("上游暂时不可用", retryable=retryable)

    monkeypatch.setattr(llm, "chat", _chat)
    monkeypatch.setattr(llm, "chat_json", _chat_json)


def _record(**overrides) -> QuestionRecord:
    base = dict(text="题干", domain="rag", topic="检索", difficulty="L2", key_points=["要点一"])
    return QuestionRecord(**{**base, **overrides})


# ---------- 判据与标记 ----------


def test_评分缺失时决策直接换题():
    decision, reason = explain_decision(
        None, question_count=5, clarify_used=0, missing_used=0, deepen_used=0,
        remedy_used=0, asked_key_points=[],
    )
    assert (decision, reason) == (Decision.NEXT, Reason.DEGRADED)


def test_内容类失败不降级_原样抛回():
    """JSON 校验失败/配置类错误重试有意义或必须暴露——降级会把它静默吞掉。"""
    with pytest.raises(llm.LLMError):
        degrade.reraise_if_content(llm.LLMError("结构化输出校验失败", retryable=False))

    degrade.reraise_if_content(llm.LLMError("上游超时", retryable=True))  # 不抛 = 可降级


def test_mark去重且状态与提示成对(events):
    state = InterviewState()

    degrade.mark(state, degrade.UNSCORED)
    degrade.mark(state, degrade.UNSCORED)  # 同因重复：不再记、不再提示
    degrade.mark(state, degrade.SCRIPT_FALLBACK)

    assert state.degraded_reasons == [degrade.UNSCORED, degrade.SCRIPT_FALLBACK]
    assert [e["reason"] for e in events] == [degrade.UNSCORED, degrade.SCRIPT_FALLBACK]
    assert all(e["type"] == "degraded" for e in events)


# ---------- 节点兜底 ----------


async def test_开场白降级为固定文案(monkeypatch, events):
    _fail_llm(monkeypatch)
    state = InterviewState(position="Agent 工程师", question_count=5)

    updates = await intro_node(state)

    text = state.chat_history[-1]["content"]
    assert "Agent 工程师" in text and "5 轮问答" in text
    assert "请先做 1 分钟左右的自我介绍" in text  # 固定文案的收句（LLM 版是自由发挥）
    assert state.degraded_reasons == [degrade.SCRIPT_FALLBACK]
    assert updates["degraded_reasons"] == [degrade.SCRIPT_FALLBACK]  # 必须进 checkpoint


async def test_出题降级为内置兜底题(monkeypatch, events):
    _fail_llm(monkeypatch)
    state = InterviewState(question_count=5, phase=Phase.TECH_BASE)

    async def _no_candidates(**_kwargs):
        return []

    monkeypatch.setattr(
        ask_node_module.question_search, "search_questions", _no_candidates
    )
    await ask_node_module.ask_node(state)

    question = state.current_question
    assert question.from_bank is False
    assert question.text in {item["text"] for item in fallbacks._TECH.values()}
    assert question.key_points  # 兜底题也带要点：模型恢复后照常能被评分/追问
    # 两条都降级了：出题（生成题不可用 → 内置题）与文案（口吻改写不可用 → 直发题面）
    assert state.degraded_reasons == [degrade.QUESTION_FALLBACK, degrade.SCRIPT_FALLBACK]


async def test_题库题降级为直发原题面(monkeypatch, events):
    """口吻改写不可用：题面本就不需要 LLM，直接发（衔接语作为前缀）。"""
    _fail_llm(monkeypatch)
    state = InterviewState(question_count=5, phase=Phase.TECH_BASE, difficulty="L2")

    async def _one_candidate(**_kwargs):
        return [{
            "question_id": "q_test", "question": "什么是 RRF 融合？", "domain": "rag",
            "topic": "检索", "difficulty": "L2", "key_points": ["倒数排名"], "follow_ups": [],
        }]

    monkeypatch.setattr(
        ask_node_module.question_search, "search_questions", _one_candidate
    )
    await ask_node_module.ask_node(state)

    assert state.current_question.question_id == "q_test"
    text = state.chat_history[-1]["content"]
    assert text.endswith("什么是 RRF 融合？")  # 原题面在（前缀是代码衔接语）
    assert state.degraded_reasons == [degrade.SCRIPT_FALLBACK]


async def test_内容类失败仍然抛给上层(monkeypatch, events):
    """retryable=False = 重试能治好/要暴露的 bug——保持 error + 用户重试语义。"""
    _fail_llm(monkeypatch, retryable=False)
    state = InterviewState(position="Agent 工程师", question_count=5)

    with pytest.raises(llm.LLMError):
        await intro_node(state)

    assert state.degraded_reasons == []


async def test_评分降级为未评分(monkeypatch, events):
    _fail_llm(monkeypatch)
    question = _record()
    state = InterviewState(current_question=question, user_input="我的回答", question_count=5)

    updates = await judge_node(state)

    assert question.score is None
    assert state.answered_count == 1  # 簿记照常（与正常路径同一套）
    assert state.answered_questions[-1].score is None
    assert updates["answered_questions"][-1].score is None
    trace = state.trace_log[-1]
    assert trace["detail"]["score"] is None and trace["detail"]["coverage"] is None
    assert state.degraded_reasons == [degrade.UNSCORED]


async def test_评分降级后不再追问(monkeypatch, events):
    """判分失败 → 条件边读到 score=None → 换题（不是卡住、不是瞎追问）。"""
    _fail_llm(monkeypatch)
    question = _record()
    state = InterviewState(current_question=question, user_input="我的回答", question_count=5)
    await judge_node(state)

    decision, reason = explain_decision(
        state.current_question.score, question_count=5, clarify_used=0, missing_used=0,
        deepen_used=0, remedy_used=0, asked_key_points=[],
    )
    assert (decision, reason) == (Decision.NEXT, Reason.DEGRADED)


async def test_收尾三节点降级为固定文案(monkeypatch, events):
    _fail_llm(monkeypatch)
    state = InterviewState(current_question=_record(text="谈谈你的项目"))

    await closing_invite_node(state)
    assert "你有什么想问我的吗" in state.chat_history[-1]["content"]

    state.user_input = "团队氛围怎么样？"
    await answer_candidate_node(state)
    assert "团队氛围怎么样？" in state.chat_history[-1]["content"]
    assert state.closing_question_count == 1

    await refuse_end_node(state)
    assert "谈谈你的项目" in state.chat_history[-1]["content"]
    assert state.degraded_reasons == [degrade.SCRIPT_FALLBACK]


async def test_自我介绍提炼降级为跳过(monkeypatch, events):
    _fail_llm(monkeypatch)
    state = InterviewState(user_input="我是应届生，做过 RAG 项目")

    updates = await profile_node(state)

    assert updates.get("candidate_profile") is None  # 不覆盖（保持空）
    assert state.chat_history[-1]["content"] == "我是应届生，做过 RAG 项目"  # 消息照记
    assert state.degraded_reasons == [degrade.PROFILE_SKIPPED]


# ---------- 报告：降级链三段 ----------


async def test_报告模型降级为flash(monkeypatch, events):
    """pro 挂 → flash 顶上：报告内容完整，只记一条「报告模型降级」。"""
    calls: list[str | None] = []

    async def _chat_json(messages, *, schema, **kwargs):
        model = kwargs.get("model")
        calls.append(model)
        if model:  # 只让 v4-pro 那次失败
            raise llm.LLMError("v4-pro 不可用", retryable=True)
        return schema.model_validate({
            "total_comment": "总评", "per_question_comments": [], "study_advice": [],
        })

    monkeypatch.setattr(llm, "chat_json", _chat_json)
    state = InterviewState(
        answered_questions=[_record(score=None), _record(score=None)], answered_count=2
    )

    updates = await report_node(state)

    assert calls and calls[0] is not None and calls[1] is None  # pro → flash
    assert updates["report"]["total_comment"] == "总评"
    assert updates["report"]["degraded"] is True
    assert updates["report"]["degraded_reasons"] == [degrade.REPORT_MODEL_FALLBACK]


async def test_报告文字全失败时输出确定性内容(monkeypatch, events):
    """pro/flash 都不行 → 分数与逐题记录照出（纯代码聚合），文字部分留空。"""
    _fail_llm(monkeypatch)
    state = InterviewState(
        answered_questions=[_record(score=None), _record(score=None)], answered_count=2
    )

    report = (await report_node(state))["report"]
    assert report["degraded"] is True
    assert report["degraded_reasons"] == [degrade.REPORT_FALLBACK]
    assert report["unscored_count"] == 2
    assert report["total_comment"] == "" and report["study_advice"] == []
    assert len(report["per_question_comments"]) == 2  # 题干与作答照出
    # 全未评分 → 不落 0 分（缺数据 ≠ 0 分）
    assert report["scores"] == {} and "overall" not in report


async def test_报告部分未评分时分数照常(monkeypatch, events):
    """中途才降级（前几题有分、后几题没有）→ 分数来自已评题，只标注未评分条数。"""
    from app.graph.state import ScoreItem

    async def _chat_json(messages, *, schema, **kwargs):
        raise llm.LLMError("上游不可用", retryable=True)

    monkeypatch.setattr(llm, "chat_json", _chat_json)
    scored = ScoreItem(
        technical_depth=4, fundamentals=4, project_experience=3, communication=4,
        problem_solving=4, covered_key_points=[], missed_key_points=[], error_flag=False,
        comment="还行",
    )
    state = InterviewState(
        answered_questions=[_record(score=scored), _record(score=None)], answered_count=2
    )

    report = (await report_node(state))["report"]

    assert report["unscored_count"] == 1
    assert report["overall"] is not None and report["overall"] > 0
    assert report["weaknesses"]

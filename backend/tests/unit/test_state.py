"""state 单测（SPEC §4.1）：追问轮回答拼接 + 对话历史完整性。

回答拼接：首答保留 + 固定标记追加，前端按标记分段展示（FR-25）。
对话历史：不截断——它是回放与 SSE 差分的唯一来源。
"""

from __future__ import annotations

from app.graph.state import (
    FOLLOWUP_ANSWER_MARKER,
    InterviewState,
    TraceEvent,
    add_history,
    add_trace,
    merge_answer,
)


def test_首次作答原样返回():
    assert merge_answer(None, "我的首答") == "我的首答"
    assert merge_answer("", "我的首答") == "我的首答"


def test_追问补充保留首答并带固定标记():
    assert merge_answer("我的首答", "补充内容") == f"我的首答\n\n{FOLLOWUP_ANSWER_MARKER}补充内容"


def test_多轮追问依次追加():
    merged = merge_answer(merge_answer("首答", "补充一"), "补充二")

    assert merged == (
        f"首答\n\n{FOLLOWUP_ANSWER_MARKER}补充一\n\n{FOLLOWUP_ANSWER_MARKER}补充二"
    )


def test_对话历史全量保留不截断():
    """长场次也不能截断：回放要完整对话，SSE 差分靠长度比对（截断即漏发面试官文案）。"""
    state = InterviewState()
    for i in range(30):
        add_history(state, "assistant", f"第 {i} 条")

    assert len(state.chat_history) == 30
    assert state.chat_history[0]["content"] == "第 0 条"
    assert state.chat_history[-1]["content"] == "第 29 条"


# ---- trace_log（P1-M4：决策回放唯一数据源，FR-21）----


def test_决策日志追加为可序列化事件():
    state = InterviewState()
    add_trace(state, TraceEvent.ASK, {"domain": "rag", "from_bank": True}, round_no=1)

    assert state.trace_log == [
        {"type": "ask", "round": 1, "detail": {"domain": "rag", "from_bank": True}},
    ]
    # 事件类型落库为字符串（Enum 成员不许漏进来，否则 JSON 序列化会带类名）
    assert isinstance(state.trace_log[0]["type"], str)


def test_决策日志_轮次可空_按序累积():
    state = InterviewState()
    add_trace(state, TraceEvent.END_REFUSED, {"answered_count": 1}, round_no=2)
    add_trace(state, TraceEvent.REPORT, {"answered_count": 2})

    assert [e["round"] for e in state.trace_log] == [2, None]
    assert [e["type"] for e in state.trace_log] == ["end_refused", "report"]


# ---- 图片通道（P2-M6 FR-26）：state 形状与旧 checkpoint 兼容 ----


def test_图片字段默认空_旧形状_dict_可校验():
    """旧 checkpoint 的值里没有新字段 → 校验必须靠默认值补上，不许拒绝（T4 教训重验）。"""
    old_state = {  # 模拟 P2-M6 之前写入的 checkpoint 值（无 current_images）
        "interview_id": "iv1", "position": "Agent/AI 工程师", "question_count": 5,
        "answered_questions": [{
            "question_id": "q_1", "text": "题", "domain": "rag", "topic": "t",
            "difficulty": "L1", "key_points": ["k"], "answer": "答",  # 无 image_ids
        }],
    }

    state = InterviewState.model_validate(old_state)

    assert state.current_images == []
    assert state.answered_questions[0].image_ids == []


def test_add_history_无图不带_image_ids_键():
    """无图场次的 chat_history 条目与接入前逐字一致（键都不多一个）。"""
    state = InterviewState()
    add_history(state, "user", "普通回答")

    assert state.chat_history == [{"role": "user", "content": "普通回答"}]


def test_add_history_带图写入_image_ids():
    state = InterviewState()
    add_history(state, "user", "见截图", image_ids=["a" * 32])

    assert state.chat_history[0]["image_ids"] == ["a" * 32]

"""图组装（SPEC §4.2）：节点/边/单 interrupt 点/route 纯代码分发。

运行时模型：每次用户消息 = 一个 resume super-step——route 分发到对应节点链，
连续执行到下一个 interrupt 暂停；非幂等副作用（计数/写 state）只发生在
interrupt 之后的节点里（SPEC §11 风险 3，resume 不重跑暂停前节点）。
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.graph.nodes import (
    advance_node,
    ask_node,
    closing_invite_node,
    answer_candidate_node,
    followup_node,
    intro_node,
    judge_node,
    profile_node,
    refuse_end_node,
    report_node,
)
from app.graph.nodes.closing import CLOSING_QUESTION_LIMIT
from app.domain import QUESTION_TYPE_BEHAVIORAL
from app.graph.rules.advance import is_end_command, meets_end_quota
from app.graph.rules.follow_up import Decision, decide_follow_up, remedy_used_total
from app.graph.rules.speech import SPEECH_FIELDS
from app.graph.state import InterviewState, Phase


def parse_resume(payload: Any) -> tuple[str, list[str]]:
    """resume 载荷 → (文本, 图片 id 列表)（纯函数，可单测）。

    P2-M6：无图消息仍是裸字符串（与接入前逐字同形），带图消息是
    `{"content": ..., "images": [...]}`——两种形状都要接住。
    """
    if isinstance(payload, dict):
        content = str(payload.get("content") or "")
        images = [item for item in (payload.get("images") or []) if isinstance(item, str)]
        return content, images
    return (str(payload) if payload else ""), []


def parse_speech(payload: Any) -> dict | None:
    """resume 载荷 → 语音原始计数（M16，纯函数，可单测）。

    只接受六个非负整数字段（前端 SpeechCounters 的形状）；缺字段/类型错/前后不一致
    一律返回 None——表达指标是增强，不因一份脏载荷卡住整轮作答。
    """
    if not isinstance(payload, dict):
        return None
    raw = payload.get("speech")
    if not isinstance(raw, dict):
        return None
    out: dict[str, int] = {}
    for key in SPEECH_FIELDS:
        value = raw.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return None
        out[key] = value
    if out["pause_ms"] > out["duration_ms"] or out["pause_count"] * 2000 > out["pause_ms"]:
        return None
    return out


def pause_node(state: InterviewState) -> dict:
    """单 interrupt 点：resume 时拿到用户输入写入 state，供 route 分发。"""
    payload = interrupt({"waiting": "user_input"})
    content, images = parse_resume(payload)
    return {
        "user_input": content,
        "current_images": images,
        # 文字消息会把它覆盖成 None：上一轮的语音指标不能重复计入下一题（M16）
        "current_speech": parse_speech(payload),
    }


def route(state: InterviewState) -> str:
    """入口分发（纯代码）：按 phase + 用户消息分类（SPEC §4.2 图 ROUTE）。"""
    if is_end_command(state.user_input):
        return "report" if meets_end_quota(state.answered_count, state.question_count) else "refuse_end"
    if state.phase is Phase.INTRO:
        return "intro"
    if state.phase is Phase.WARMUP:
        return "profile"
    if state.phase in (Phase.TECH_BASE, Phase.PROJECT, Phase.BEHAVIORAL):
        return "judge"
    if state.phase is Phase.CLOSING:
        return "answer_candidate"
    return "report"  # 防御：FINISHED 等异常状态收尾


def followup_decision(state: InterviewState) -> str:
    """追问决策条件边（纯代码，rules/follow_up.py 已单测）。"""
    question = state.current_question
    decision = decide_follow_up(
        question.score,
        question_count=state.question_count,
        clarify_used=question.clarify_used,
        missing_used=question.missing_used,
        deepen_used=question.deepen_used,
        remedy_used=remedy_used_total(state),
        asked_key_points=question.asked_key_points,
        # 行为面 deepen-only（P1-M11 ①）：与 followup 节点同判据（按题型）
        deepen_only=question.question_type == QUESTION_TYPE_BEHAVIORAL,
    )
    return (
        "followup"
        if decision in (Decision.CLARIFY, Decision.MISSING, Decision.DEEPEN)
        else "advance"
    )


def advance_route(state: InterviewState) -> str:
    """advance 后分发：进反问阶段或继续出题。"""
    return "closing" if state.phase is Phase.CLOSING else "ask"


def after_answer_candidate(state: InterviewState) -> str:
    """反问作答后：达上限（2 个）进报告，否则继续等提问。"""
    return "report" if state.closing_question_count >= CLOSING_QUESTION_LIMIT else "pause"


ROUTE_TARGETS = {
    "intro": "intro",
    "profile": "profile",
    "judge": "judge",
    "answer_candidate": "answer_candidate",
    "report": "report",
    "refuse_end": "refuse_end",
}


def build_graph(checkpointer: Any = None):
    g = StateGraph(InterviewState)
    g.add_node("intro", intro_node)
    g.add_node("profile", profile_node)
    g.add_node("ask", ask_node)
    g.add_node("judge", judge_node)
    g.add_node("followup", followup_node)
    g.add_node("advance", advance_node)
    g.add_node("closing_invite", closing_invite_node)
    g.add_node("answer_candidate", answer_candidate_node)
    g.add_node("refuse_end", refuse_end_node)
    g.add_node("report", report_node)
    g.add_node("pause", pause_node)

    g.add_conditional_edges(START, route, ROUTE_TARGETS)
    g.add_edge("intro", "pause")
    g.add_edge("profile", "ask")
    g.add_edge("ask", "pause")
    g.add_conditional_edges(
        "judge", followup_decision, {"followup": "followup", "advance": "advance"}
    )
    g.add_edge("followup", "pause")
    g.add_conditional_edges("advance", advance_route, {"closing": "closing_invite", "ask": "ask"})
    g.add_edge("closing_invite", "pause")
    g.add_conditional_edges(
        "answer_candidate", after_answer_candidate, {"report": "report", "pause": "pause"}
    )
    g.add_edge("refuse_end", "pause")
    g.add_edge("report", END)
    g.add_conditional_edges("pause", route, ROUTE_TARGETS)
    return g.compile(checkpointer=checkpointer)


def run_config(interview_id: str, question_count: int = 10) -> dict:
    """单场面试的运行配置：thread_id = interview_id；recursion_limit 显式设
    上限并预留余量（SPEC §4.2），T5 捕获 GraphRecursionError 映射 SSE error。"""
    return {
        "configurable": {"thread_id": interview_id},
        "recursion_limit": question_count * 6 + 10,
    }


# checkpoint 序列化器已挪到 app/graph/serde.py（P4-E1：加 zlib 压缩、新旧 type 双读）。
# 这里保留同名转发，调用方（service / 脚本 / 测试）的 import 路径不用改。
from app.graph.serde import make_serde  # noqa: E402  —— 放在文件尾避免循环导入

"""图组装（SPEC §4.2）：节点/边/单 interrupt 点/route 纯代码分发。

运行时模型：每次用户消息 = 一个 resume super-step——route 分发到对应节点链，
连续执行到下一个 interrupt 暂停；非幂等副作用（计数/写 state）只发生在
interrupt 之后的节点里（SPEC §12.3，resume 不重跑暂停前节点）。
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
from app.graph.rules.advance import is_end_command, meets_end_quota
from app.graph.rules.follow_up import Decision, decide_follow_up, remedy_used_total
from app.graph.state import InterviewState, Phase


def pause_node(state: InterviewState) -> dict:
    """单 interrupt 点：resume 时拿到用户输入写入 state，供 route 分发。"""
    user_input = interrupt({"waiting": "user_input"})
    return {"user_input": str(user_input) if user_input else ""}


def route(state: InterviewState) -> str:
    """入口分发（纯代码）：按 phase + 用户消息分类（SPEC §4.2 图 ROUTE）。"""
    if is_end_command(state.user_input):
        return "report" if meets_end_quota(state.answered_count, state.question_count) else "refuse_end"
    if state.phase is Phase.INTRO:
        return "intro"
    if state.phase is Phase.WARMUP:
        return "profile"
    if state.phase in (Phase.TECH_BASE, Phase.PROJECT):
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


def make_serde():
    """checkpoint 序列化器：显式允许 state 模块的 Pydantic 类型
    （Phase/ScoreItem/QuestionRecord/InterviewState），否则 LangGraph 反序列化
    告警且未来版本会阻断。T5 服务层构造 saver 时复用。"""
    from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

    from app.graph.state import InterviewState, Phase, QuestionRecord, ScoreItem

    return JsonPlusSerializer(
        allowed_msgpack_modules=[InterviewState, Phase, QuestionRecord, ScoreItem]
    )

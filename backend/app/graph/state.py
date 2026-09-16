"""面试会话状态定义（SPEC §4.1）。

InterviewState 是 LangGraph 图的状态 schema，也是 checkpointer 的持久化单元。
LangGraph 1.x 的 Pydantic state schema 要求字段可缺省，因此全部给默认值；
创建会话时以完整 state 传入（interview_id / position 等由服务层填）。
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

CHAT_HISTORY_LIMIT = 24  # SPEC §4.1：LLM 上下文保留最近 24 条


class Phase(str, Enum):
    INTRO = "intro"
    WARMUP = "warmup"
    TECH_BASE = "tech_base"
    PROJECT = "project"
    CLOSING = "closing"
    FINISHED = "finished"


class ScoreItem(BaseModel):
    """单题评分（评分节点结构化输出，五维 1-5 整数，SPEC §4.1）。"""

    technical_depth: int = Field(ge=1, le=5)
    fundamentals: int = Field(ge=1, le=5)
    project_experience: int = Field(ge=1, le=5)
    communication: int = Field(ge=1, le=5)
    problem_solving: int = Field(ge=1, le=5)
    covered_key_points: list[str] = []
    missed_key_points: list[str] = []
    error_flag: bool = False
    comment: str = ""

    @property
    def mean(self) -> float:
        """五维等权均值（difficulty 自适应与报告聚合共用，SPEC §4.3/§4.6）。"""
        return (
            self.technical_depth
            + self.fundamentals
            + self.project_experience
            + self.communication
            + self.problem_solving
        ) / 5

    @property
    def coverage(self) -> float:
        """关键点覆盖率；无关键点数据时视为 1.0（不因数据缺失触发追问）。"""
        total = len(self.covered_key_points) + len(self.missed_key_points)
        if total == 0:
            return 1.0
        return len(self.covered_key_points) / total


class QuestionRecord(BaseModel):
    """单题记录（SPEC §4.1）。

    followup_log 为实现补充（SPEC 未列）：评分节点需要追问记录作上下文（SPEC §4.5），
    checkpointer state 是权威，不存即丢。
    """

    question_id: str | None = None
    text: str
    domain: str
    topic: str
    difficulty: str
    key_points: list[str] = []
    follow_up_count: int = 0
    clarify_used: int = 0
    missing_used: int = 0
    followup_log: list[str] = []
    answer: str | None = None
    score: ScoreItem | None = None
    skipped: bool = False
    from_bank: bool = True


class InterviewState(BaseModel):
    """面试会话状态（SPEC §4.1）。position / interview_id 默认空串仅为满足
    Pydantic state schema 的缺省要求，创建会话时必传真实值。

    以下字段为实现补充（SPEC §4.1 未列，实现时发现必需，SPEC 将同步修订）：
    - answered_questions：已答题目记录（报告聚合需逐题 domain/score，state 是权威）；
    - user_input：resume 时的当前用户消息（route 纯代码分发依据）；
    - closing_question_count：反问计数（PRD §4.1 上限 1-2 个）。
    """

    interview_id: str = ""
    position: str = ""
    question_count: int = 10
    phase: Phase = Phase.INTRO
    current_question: QuestionRecord | None = None
    asked_ids: list[str] = []
    difficulty: str = "L1"
    consecutive_good: int = 0
    consecutive_bad: int = 0
    candidate_profile: str = ""
    answered_count: int = 0
    answered_questions: list[QuestionRecord] = []
    user_input: str = ""
    closing_question_count: int = 0
    chat_history: list[dict] = []
    report: dict | None = None
    status: str = "running"  # running / finished


def add_history(state: InterviewState, role: str, content: str) -> None:
    """追加对话历史并截断到 CHAT_HISTORY_LIMIT（原地，节点显式返回该字段）。"""
    state.chat_history.append({"role": role, "content": content})
    if len(state.chat_history) > CHAT_HISTORY_LIMIT:
        del state.chat_history[: len(state.chat_history) - CHAT_HISTORY_LIMIT]

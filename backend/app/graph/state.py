"""面试会话状态定义（SPEC §4.1）。

InterviewState 是 LangGraph 图的状态 schema，也是 checkpointer 的持久化单元。
LangGraph 1.x 的 Pydantic state schema 要求字段可缺省，因此全部给默认值；
创建会话时以完整 state 传入（interview_id / position 等由服务层填）。
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

# 追问轮回答拼接标记（SPEC §4.1）：复盘卡按它分段展示（首答 / 追问补充 N），
# 是给前端的契约（frontend/lib/constants.ts 同值），改文案要同步改前端。
FOLLOWUP_ANSWER_MARKER = "【追问补充】"


class TraceEvent(str, Enum):
    """决策回放事件类型（P1-M4 / FR-21）：语义由后端定义，前端只消费。"""

    ASK = "ask"  # 出题：目标域/难度 → 检索工具输出 → 新题
    JUDGE = "judge"  # 评分：我的回答 → 五维/覆盖率 → 难度状态变化
    FOLLOWUP = "followup"  # 追问决策：决策 + 原因 + 追问文案
    ADVANCE = "advance"  # 换题：换题原因 + 阶段推进
    END_REFUSED = "end_refused"  # 主动结束未达门槛被挽留（PRD §4.5）
    REPORT = "report"  # 收尾：报告生成


def merge_answer(previous: str | None, current: str) -> str:
    """合并同一题的多轮作答（SPEC §4.1）：首答原样，追问补充带标记追加。"""
    if not previous:
        return current
    return f"{previous}\n\n{FOLLOWUP_ANSWER_MARKER}{current}"


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
    follow_ups: list[str] = []  # 题库题深挖追问素材（enrich 产物；生成题为空 → LLM 现场生成）
    follow_up_count: int = 0
    clarify_used: int = 0
    missing_used: int = 0
    deepen_used: int = 0
    asked_key_points: list[str] = []  # 已追问过的 key_points（P1-M4.5-R1：同一漏点只追问一次）
    followup_log: list[str] = []
    answer: str | None = None
    score: ScoreItem | None = None
    skipped: bool = False
    from_bank: bool = True
    question_type: str = "tech"  # 题型（T7a）：tech=技术题；scenario=场景题。均计入问答轮次，默认值兼容旧 checkpoint


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
    question_count: int = 10  # 全场问答轮次（T7a-R1）：组成 = 技术 question_count−1 + 场景 1（domain.SCENARIO_COUNT）
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
    trace_log: list[dict] = []  # 决策回放事件流（P1-M4 / FR-21）：只增不改，见 add_trace


def add_history(state: InterviewState, role: str, content: str) -> None:
    """追加对话历史（原地，节点显式返回该字段）。**不截断**。

    chat_history 是回放（GET /interviews/{id}）与 SSE delta 差分的唯一来源，
    两者都要求完整：服务层用「本次长度 − 上次长度」找新增消息，一旦从头部截断，
    长度差分就算不出新增 → 面试官文案漏发；回放也会丢掉开场。别在这里砍。
    （面试官与 LLM 的记忆来自结构化 state——answered_questions / candidate_profile，
    chat_history 不参与 prompt 组装。）
    """
    state.chat_history.append({"role": role, "content": content})


def add_trace(
    state: InterviewState,
    event_type: TraceEvent,
    detail: dict,
    *,
    round_no: int | None = None,
) -> None:
    """追加决策回放事件（原地，节点显式返回该字段）。只增不改。

    - `round` = 事件所属问答轮次（1 起）；与报告 `number` 同语义（后端定义，前端按它分组）。
      无轮次归属的事件（报告收尾）传 None。
    - trace_log 存的是**决策证据**（工具输出/原因/状态变化），题目与回答的正文在节点里
      本就带出，不做二次快照——回放接口直接把它序列化给前端（服务层 `_plain` 负责归一）。
    - `detail` 必须是纯标量结构（Enum/模型对象不许直接塞，见 SPEC §4.7）。
    """
    state.trace_log.append({"type": event_type.value, "round": round_no, "detail": detail})

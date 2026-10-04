"""面试会话状态定义（SPEC §4.1）。

InterviewState 是 LangGraph 图的状态 schema，也是 checkpointer 的持久化单元。
LangGraph 1.x 的 Pydantic state schema 要求字段可缺省，因此全部给默认值；
创建会话时以完整 state 传入（interview_id / position 等由服务层填）。
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from app.domain import INTERVIEW_BEHAVIORAL, INTERVIEW_TECH

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
    BEHAVIORAL = "behavioral"  # 行为面问答段（P1-M11：行为面场次取代 PROJECT+TECH_BASE）
    CLOSING = "closing"
    FINISHED = "finished"


class BaseScore(BaseModel):
    """评分公共部分（P1-M11）：关键点覆盖、错误标记与点评——技术面与行为面评分共用。

    字段顺序上先于各维得分（子类字段排在后）：schema 注入 prompt 时是文档，无碍解析。
    """

    covered_key_points: list[str] = []
    missed_key_points: list[str] = []
    error_flag: bool = False
    comment: str = ""

    @property
    def coverage(self) -> float:
        """关键点覆盖率；无关键点数据时视为 1.0（不因数据缺失触发追问）。"""
        total = len(self.covered_key_points) + len(self.missed_key_points)
        if total == 0:
            return 1.0
        return len(self.covered_key_points) / total


class ScoreItem(BaseScore):
    """单题评分（技术面，评分节点结构化输出，五维 1-5 整数，SPEC §4.1）。"""

    technical_depth: int = Field(ge=1, le=5)
    fundamentals: int = Field(ge=1, le=5)
    project_experience: int = Field(ge=1, le=5)
    communication: int = Field(ge=1, le=5)
    problem_solving: int = Field(ge=1, le=5)

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


class BehavioralScoreItem(BaseScore):
    """行为面单题评分（P1-M11 FR-22）：五维同刻度 1-5，维度见 aggregate.BEHAVIORAL_DIMS。

    第 3 维与同名技术维（project_experience）刻意对齐：两类型雷达图跨类型对照时语义一致。
    """

    communication: int = Field(ge=1, le=5)
    logic_structure: int = Field(ge=1, le=5)
    project_experience: int = Field(ge=1, le=5)
    values_motivation: int = Field(ge=1, le=5)
    career_stability: int = Field(ge=1, le=5)

    @property
    def mean(self) -> float:
        """五维等权均值（难度自适应沿用同一连击机制，行为面下是死数据、不参与出题）。"""
        return (
            self.communication
            + self.logic_structure
            + self.project_experience
            + self.values_motivation
            + self.career_stability
        ) / 5


def score_schema_for(interview_type: str):
    """评分结构化输出 schema（按会话类型分派；judge 节点与测试共用）。"""
    return BehavioralScoreItem if interview_type == INTERVIEW_BEHAVIORAL else ScoreItem


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
    # 该题回答附带的图（P2-M6 FR-26）：image_id 列表，跨追问轮累积；只存 id 不存路径/字节
    image_ids: list[str] = []
    score: ScoreItem | BehavioralScoreItem | None = None
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
    # 会话类型（P1-M11 FR-22）：tech（默认）/ behavioral。与 position 正交——两种类型
    # 面向同一岗位，只换能力模型与题源。默认值兼容旧 checkpoint（历史场次全是技术面）。
    interview_type: str = INTERVIEW_TECH
    # 场次归属（P1-M7 FR-13）：出题时用它并入该用户的私有题。空串 = 无归属（阶段 1 的历史场次，
    # 那些库里的 checkpoint 没有这个字段，默认值保证它们仍能 resume）——空值即只用公共题库。
    user_id: str = ""
    question_count: int = 10  # 全场问答轮次（P1-M4.6-C）：项目深挖 project_count(N) + 技术 N−project_count(N)（domain.project_count）
    phase: Phase = Phase.INTRO
    current_question: QuestionRecord | None = None
    asked_ids: list[str] = []
    difficulty: str = "L1"
    # 固定难度场次（P1-M6 FR-14）：创建时选定 L1/L2/L3 → 全场锁定该难度、不自适应升降
    difficulty_locked: bool = False
    consecutive_good: int = 0
    consecutive_bad: int = 0
    candidate_profile: str = ""
    answered_count: int = 0
    answered_questions: list[QuestionRecord] = []
    user_input: str = ""
    # 本轮用户消息附带的图（P2-M6）：pause 节点从 resume 载荷写入，评分/追问消费；
    # 下一条消息到达时被整体覆盖，不需要显式清空
    current_images: list[str] = []
    closing_question_count: int = 0
    chat_history: list[dict] = []
    report: dict | None = None
    status: str = "running"  # running / finished
    trace_log: list[dict] = []  # 决策回放事件流（P1-M4 / FR-21）：只增不改，见 add_trace
    # 降级原因（P2-M9，rules/degrade.py 是唯一写入口）：断 LLM 时各节点确定性兜底留下的
    # 痕迹，去重累计。空 = 全程正常；有值 ≠ 面试失败——降级是「照常走完 + 如实标注」，
    # 进报告 payload 供报告页/PDF/能力档案如实交代。默认值兼容旧 checkpoint。
    degraded_reasons: list[str] = []


def add_history(
    state: InterviewState, role: str, content: str, *, image_ids: list[str] | None = None
) -> None:
    """追加对话历史（原地，节点显式返回该字段）。**不截断**。

    chat_history 是回放（GET /interviews/{id}）与 SSE delta 差分的唯一来源，
    两者都要求完整：服务层用「本次长度 − 上次长度」找新增消息，一旦从头部截断，
    长度差分就算不出新增 → 面试官文案漏发；回放也会丢掉开场。别在这里砍。
    （面试官与 LLM 的记忆来自结构化 state——answered_questions / candidate_profile，
    chat_history 不参与 prompt 组装。）

    image_ids（P2-M6）：用户消息附带的图（回放页按它渲染缩略图）。**只在有图时才出现
    这个键**——无图场次的 chat_history 与接入前逐字一致（旧读取侧天然兼容）。
    """
    entry: dict = {"role": role, "content": content}
    if image_ids:
        entry["image_ids"] = list(image_ids)
    state.chat_history.append(entry)


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

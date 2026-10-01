"""知识域与会话类型定义（单一来源）。

解析映射（data/scripts）、出题配额（graph/rules/quota）、报告聚合三处共用，
避免 domain 字符串散落各处。权重口径见 CLAUDE.md「岗位与题库」。
"""

from math import ceil
from typing import Final

# 六大域（阶段 1 主出题域），权重按规划报告 §5.3
DOMAIN_WEIGHTS: Final[dict[str, float]] = {
    "agent-architecture": 0.20,
    "rag": 0.20,
    "planning-reasoning": 0.15,
    "tool-use": 0.15,
    "memory": 0.15,
    "engineering-observability": 0.15,
}

DOMAIN_LABELS: Final[dict[str, str]] = {
    "agent-architecture": "Agent 认知与架构",
    "planning-reasoning": "规划与推理范式",
    "tool-use": "Tool 与 Function Calling",
    "memory": "Memory",
    "rag": "RAG",
    "engineering-observability": "工程化与可观测",
    "algorithms": "手撕算法",
    "behavioral": "行为与项目面",
    "cs-fundamentals": "计算机基础",
}

# 行为面域 id（行为题在 questions.domain 里的取值；也是行为面场次的出题域）
BEHAVIORAL_DOMAIN: Final[str] = "behavioral"

# 引擎可出题的域（P1-M11）：六大技术域 + 行为面。**行为面不属于技术配额**——
# 本集合的语义即「可出题但不属于技术配额」：集合内域都能被出题检索，但只有
# DOMAIN_WEIGHTS 的键参与配额分配；行为面场次按整池抽取（不选域、不分配配额）。
# algorithms 不在此集合：现阶段只存不考（M7 拍板）。
ASKABLE_DOMAINS: Final[frozenset[str]] = frozenset(DOMAIN_WEIGHTS) | {BEHAVIORAL_DOMAIN}

# 可上传/可入私有库的域（P1-M7）：六大技术域 + algorithms（只存不考）。
# **不含行为面**（M11 D7：私有行为题暂不开）；其余域解析入库但置 draft。
ENABLED_DOMAINS: Final[frozenset[str]] = frozenset(DOMAIN_WEIGHTS) | {"algorithms"}

# 会话类型（P1-M11 FR-22）：与技术岗位正交（position 两种类型都是同一个岗位）。
# tech = 技术面（默认，阶段 1 起的全部行为）；behavioral = 行为面/HR 面。
INTERVIEW_TECH: Final[str] = "tech"
INTERVIEW_BEHAVIORAL: Final[str] = "behavioral"
INTERVIEW_TYPES: Final[frozenset[str]] = frozenset({INTERVIEW_TECH, INTERVIEW_BEHAVIORAL})
# 行为面题量上限：题库该域存量小（14 题），15 题场会当场耗尽走 LLM 兜底
BEHAVIORAL_MAX_QUESTIONS: Final[int] = 10

# 行为面题型（question_type）：与域同名的独立题型，计入问答轮次（T7a 口径：
# 新增题型只改后端组成常量，用户侧「N 轮问答」的数字语义不变）
QUESTION_TYPE_BEHAVIORAL: Final[str] = "behavioral"

DIFFICULTIES: Final[frozenset[str]] = frozenset({"L1", "L2", "L3"})

# 题型种类与计数语义（T7a，单一来源）：计入问答轮次的题型参与逐题编号
#（question_type 见 state.QuestionRecord，默认 tech）。
COUNTED_QUESTION_TYPES: Final[frozenset[str]] = frozenset(
    {"tech", "scenario", QUESTION_TYPE_BEHAVIORAL}
)


def project_count(question_count: int) -> int:
    """项目深挖题数量（P1-M4.6-C）：min(3, max(2, ceil(N/3)), N−1)。

    N−1 保底 1 道技术题（N=2 → 1 项目 + 1 技术）；5 题场 2 项目 + 3 技术；
    10/15 题场 3 项目封顶。技术题 = question_count − project_count(question_count)。
    组成规则是引擎事务，不对用户暴露（UI 只讲「N 轮问答」）。
    """
    return min(3, max(2, ceil(question_count / 3)), question_count - 1)

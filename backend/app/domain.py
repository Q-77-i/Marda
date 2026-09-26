"""知识域定义（单一来源）。

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

# 阶段 1 参与出题的域；其余解析入库但置 draft，阶段 2 启用（behavioral / cs-fundamentals）
ENABLED_DOMAINS: Final[frozenset[str]] = frozenset(DOMAIN_WEIGHTS) | {"algorithms"}

DIFFICULTIES: Final[frozenset[str]] = frozenset({"L1", "L2", "L3"})

# 题型种类与计数语义（T7a，单一来源）：计入问答轮次的题型参与逐题编号
#（question_type 见 state.QuestionRecord，默认 tech）。
COUNTED_QUESTION_TYPES: Final[frozenset[str]] = frozenset({"tech", "scenario"})


def project_count(question_count: int) -> int:
    """项目深挖题数量（P1-M4.6-C）：min(3, max(2, ceil(N/3)), N−1)。

    N−1 保底 1 道技术题（N=2 → 1 项目 + 1 技术）；5 题场 2 项目 + 3 技术；
    10/15 题场 3 项目封顶。技术题 = question_count − project_count(question_count)。
    组成规则是引擎事务，不对用户暴露（UI 只讲「N 轮问答」）。
    """
    return min(3, max(2, ceil(question_count / 3)), question_count - 1)

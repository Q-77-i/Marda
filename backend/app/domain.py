"""知识域定义（单一来源）。

解析映射（data/scripts）、出题配额（graph/rules/quota）、报告聚合三处共用，
避免 domain 字符串散落各处。权重口径见 CLAUDE.md「岗位与题库」。
"""

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

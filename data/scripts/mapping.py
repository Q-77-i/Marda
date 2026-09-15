"""md 题库的 topic / round → 结构化字段映射表。

topic 全集以 md 实际出现的 16 个主题为准（SPEC §6.2）；未知 topic 由解析器报错，不静默丢弃。
"""

from typing import Final

# 16 个主题 → 九域。二面/三面存在变体命名（架构与 Agent 设计、评测与 Badcase 定位…），
# 一并收录，避免"同一主题两个名字"导致映射漏网。
TOPIC_TO_DOMAIN: Final[dict[str, str]] = {
    # Agent 认知与架构
    "Agent 认知与架构": "agent-architecture",
    "架构与 Agent 设计": "agent-architecture",
    "Prompt 与上下文工程": "agent-architecture",
    "Multi-Agent": "agent-architecture",
    # 规划与推理范式
    "规划与推理范式": "planning-reasoning",
    # Tool 与 Function Calling
    "Tool 与 Function Calling": "tool-use",
    # Memory
    "Memory": "memory",
    # RAG
    "RAG": "rag",
    # 工程化与可观测
    "工程化与可观测": "engineering-observability",
    "评测与 Badcase": "engineering-observability",
    "评测与 Badcase 定位": "engineering-observability",
    "工程与底层基础": "engineering-observability",
    # 独立域
    "手撕算法": "algorithms",
    "团队与个人规划": "behavioral",
    "计算机基础": "cs-fundamentals",
}

ROUND_TO_DIFFICULTY: Final[dict[str, str]] = {
    "一面": "L1",
    "二面": "L2",
    "三面": "L3",
}

# 手撕算法不按轮次定难度（一面也会出手撕）
DOMAIN_DIFFICULTY_OVERRIDE: Final[dict[str, str]] = {
    "algorithms": "L2",
}

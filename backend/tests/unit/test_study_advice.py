"""study_advice.domain 归一（P2-M2）：LLM 输出 → 合法域 id / 维度 key。

背景：报告官此前自由填域（「状态机与回放架构」这类散文）→ 学习推荐按域 id 配对
配不上 → 建议卡与推荐分组各说各的。修法 = prompt 给合法清单 + 代码宽容归一。

**宽容是硬约束**：归一函数绝不抛错——report 节点的结构化输出校验失败 = LLMError =
整份报告失败，不能让一个建议字段炸掉整场面试（前端 domainLabel 对未知值已有兜底）。
"""

from __future__ import annotations

import pytest

from app.agents.prompts import REPORT_TEMPLATE
from app.domain import DOMAIN_WEIGHTS, INTERVIEW_BEHAVIORAL
from app.graph.rules.aggregate import (
    BEHAVIORAL_DIMENSION_LABELS,
    normalize_advice_domain,
    report_domain_options,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("rag", "rag"),  # 英文 key 原样
        ("  memory  ", "memory"),  # 去空白
        ("工程化与可观测", "engineering-observability"),  # 域中文标签 → id
        ("RAG", "rag"),  # 标签本身是英文词的域
        ("沟通表达", "communication"),  # 维度标签 → key（行为面 LLM 常见填法）
        ("技术深度", "technical_depth"),  # 技术面维度标签也归一（LLM 偶尔按维度给建议）
        ("状态机与回放架构", "状态机与回放架构"),  # 散文 → 保留原文，不炸
        ("", ""),
    ],
)
def test_建议域宽容归一(raw, expected):
    assert normalize_advice_domain(raw) == expected


def test_建议取值清单_技术面六域():
    text = report_domain_options("tech")
    for key in DOMAIN_WEIGHTS:
        assert key in text
    assert "Agent 认知与架构" in text  # key（中文标签）成对给出，避免 LLM 猜


def test_建议取值清单_行为面五维():
    text = report_domain_options(INTERVIEW_BEHAVIORAL)
    for key in BEHAVIORAL_DIMENSION_LABELS:
        assert key in text
    assert "career_stability（职业稳定性）" in text


def test_报告模板带取值清单插槽():
    assert "{domain_options}" in REPORT_TEMPLATE

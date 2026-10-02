"""难度标注脚本的纯逻辑单测（不调 LLM）：prompt 组装与批内序号映射。

**序号映射是防错设计**：不让模型抄 question_id（抄错无法察觉），批内按 1..N 对齐，
覆盖不全立刻报错——这里把这两条钉死。
"""

from __future__ import annotations

import pytest

from annotate_difficulty import BatchResult, LevelItem, build_messages, to_levels


def _batch(n: int) -> list[dict]:
    return [
        {"id": f"q_{index:03d}", "question": f"第 {index} 题的问法？", "domain": "rag"}
        for index in range(1, n + 1)
    ]


def test_prompt_按序号列题不含_question_id():
    messages = build_messages(_batch(3))
    user = messages[1]["content"]
    assert "1. " in user and "3. " in user
    assert "q_001" not in user  # id 不进 prompt：抄错无从察觉，改用序号
    assert "共 3 题" in user


def test_序号映射回_question_id():
    batch = _batch(2)
    result = BatchResult(levels=[LevelItem(n=1, level="L1"), LevelItem(n=2, level="L3")])
    assert to_levels(batch, result) == {"q_001": "L1", "q_002": "L3"}


def test_覆盖不全必须报错():
    batch = _batch(3)
    result = BatchResult(levels=[LevelItem(n=1, level="L2"), LevelItem(n=3, level="L2")])
    with pytest.raises(ValueError, match="覆盖不全"):
        to_levels(batch, result)


def test_档位取值被_schema_限定():
    with pytest.raises(Exception):
        BatchResult.model_validate_json('{"levels": [{"n": 1, "level": "L4"}]}')

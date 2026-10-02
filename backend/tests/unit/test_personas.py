"""三档 persona（P1-M12 会话 2）：模板齐备、**不喂关键点**、档位校验。

「不喂关键点」是硬约束：persona 看到要点就会照着答，弱档不弱、覆盖率失真的对照
也就没法测评分官的区分度了。
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app import llm
from evals import personas
from fake_llm import stream_of


class _CapturingClient:
    """persona 生成走 llm.chat（P2-M4 起恒为流式），替身直接回流式响应。"""

    def __init__(self):
        self.calls: list[dict] = []

    @property
    def chat(self):
        outer = self

        class _Completions:
            async def create(self, **kwargs):
                outer.calls.append(kwargs)
                return stream_of("我的回答")

        return SimpleNamespace(completions=_Completions())


def test_三档模板齐备():
    assert set(personas.PERSONA_TEMPLATES) == set(personas.TIERS)
    assert set(personas.TIERS) == {"weak", "medium", "strong"}


@pytest.mark.asyncio
async def test_生成回答只喂题目不喂关键点(monkeypatch):
    client = _CapturingClient()
    monkeypatch.setattr(llm, "_get_client", lambda: client)
    answer = await personas.generate_tier_answer("什么是 MCP？", "weak")
    assert answer == "我的回答"
    content = client.calls[0]["messages"][0]["content"]
    assert "什么是 MCP？" in content
    assert "关键点" not in content and "key_points" not in content


@pytest.mark.asyncio
async def test_未知档位直接报错():
    with pytest.raises(ValueError, match="未知档位"):
        await personas.generate_tier_answer("题", "genius")


@pytest.mark.asyncio
async def test_档位口吻互不相同(monkeypatch):
    client = _CapturingClient()
    monkeypatch.setattr(llm, "_get_client", lambda: client)
    for tier in personas.TIERS:
        await personas.generate_tier_answer("题", tier)
    prompts = [json.dumps(call["messages"][0]["content"], ensure_ascii=False) for call in client.calls]
    assert len(set(prompts)) == 3

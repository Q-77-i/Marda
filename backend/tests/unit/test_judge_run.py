"""评分运行层（P1-M12 会话 2）：模型→记录转换、失败计数、聚合入口。

真实调用（K 次 × 样本）由 scripts 显式跑；这里只钉住**编排语义**——
尤其「失败不静默」与「error_flag 不得混进维度表」这两条容易悄悄坏掉的地方。
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app import llm
from app.graph.state import ScoreItem

from evals import judge_run


def _score(**over):
    base = {
        "technical_depth": 4, "fundamentals": 3, "project_experience": 3,
        "communication": 4, "problem_solving": 3,
        "covered_key_points": ["k1"], "missed_key_points": ["k2"],
        "error_flag": True, "comment": "点评文字",
    }
    base.update(over)
    return ScoreItem(**base)


def test_评分模型转记录_布尔不得混进维度表():
    """bool 是 int 的子类——不显式排除的话 error_flag 会变成第六个「维度」。"""
    record = judge_run.score_to_record(_score())
    assert set(record["dims"]) == {
        "technical_depth", "fundamentals", "project_experience", "communication", "problem_solving"
    }
    assert record["error_flag"] is True
    assert record["covered"] == ["k1"] and record["missed"] == ["k2"]
    assert "comment" not in record  # 点评文字不进指标


def test_失败拆分不静默():
    runs = {"i1": [{"dims": {}}, {"failure": "boom"}], "i2": [{"dims": {}}]}
    valid, failures = judge_run.split_failures(runs)
    assert len(valid["i1"]) == 1 and valid["i1"][0] == {"dims": {}}
    assert failures == {"i1": 1}


class _FlakyClient:
    """第 N 次调用抛错的 fake（验证「重试一次后仍失败 → 记 failure」）。"""

    def __init__(self, payload: dict, fail_until: int):
        self.payload = payload
        self.fail_until = fail_until
        self.count = 0

    @property
    def chat(self):
        outer = self

        class _Completions:
            async def create(self, **kwargs):
                outer.count += 1
                if outer.count <= outer.fail_until:
                    raise RuntimeError("boom")
                return SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(
                        content=json.dumps(outer.payload, ensure_ascii=False)))]
                )

        return SimpleNamespace(completions=_Completions())


def _item():
    return {
        "id": "i1", "arm": "real", "interview_type": "tech",
        "question": "题", "key_points": ["k1"], "answer": "答",
        "expected": {"dims": {"technical_depth": 3, "fundamentals": 3, "project_experience": 3,
                              "communication": 3, "problem_solving": 3}, "covered_indexes": [], "note": ""},
    }


@pytest.mark.asyncio
async def test_单次失败会重试一次(monkeypatch):
    client = _FlakyClient(
        {"technical_depth": 3, "fundamentals": 3, "project_experience": 3,
         "communication": 3, "problem_solving": 3,
         "covered_key_points": [], "missed_key_points": [], "error_flag": False, "comment": ""},
        fail_until=1,
    )
    monkeypatch.setattr(llm, "_get_client", lambda: client)
    runs = await judge_run.run_item(_item(), runs=1, temperature=0.3)
    assert len(runs) == 1 and "failure" not in runs[0]
    assert client.count == 2  # 第一次失败 + 重试成功


@pytest.mark.asyncio
async def test_重试后仍失败记_failure_不抛(monkeypatch):
    client = _FlakyClient({}, fail_until=99)
    monkeypatch.setattr(llm, "_get_client", lambda: client)
    runs = await judge_run.run_item(_item(), runs=1, temperature=0.3)
    assert len(runs) == 1
    assert "failure" in runs[0] and "boom" in runs[0]["failure"]


@pytest.mark.asyncio
async def test_有效运行不足的样本被排除并亮出来(monkeypatch):
    client = _FlakyClient({}, fail_until=99)
    monkeypatch.setattr(llm, "_get_client", lambda: client)
    report = await judge_run.run_and_aggregate([_item()], runs=2, temperature=0.3)
    assert report["n_items"] == 0
    assert report["dropped_items"] == ["i1"]
    assert report["failures"] == {"i1": 2}
